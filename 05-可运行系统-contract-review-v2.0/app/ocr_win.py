# -*- coding: utf-8 -*-
"""扫描件 / 图片合同 OCR 通道

按平台自动选择识别后端，**两种后端都不引入 Python 第三方包**：

* Windows：调用系统内置 OCR 引擎（Windows.Media.Ocr，WinRT）
* Linux/macOS：调用系统 tesseract 命令行（若已安装；未安装则该能力降级为「解析受阻」）

工程注意点：
* Windows 路径下中间文件统一放到 ASCII 临时目录，避免中文路径在 PowerShell 5.1 下的编码问题；
* Windows OCR 对中文会按单字切分并插入空格，tesseract 也可能出现零星间隔空格，
  统一做 CJK 间隔空格规整，否则条款正则与标题识别会全部失效；
* 置信度为可解释启发式（有效字符占比 × 长度饱和因子），不臆造官方数值。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

ENGINE_WIN = "ocr-win"
ENGINE_TESS = "ocr-tesseract"

_PS_SCRIPT = r"""
param([string]$ImagePath, [string]$OutPath)
$ErrorActionPreference = 'Stop'
$lines = New-Object System.Collections.Generic.List[string]
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]
    function Await($WinRtTask, $ResultType) {
        $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
        $netTask = $asTask.Invoke($null, @($WinRtTask))
        $netTask.Wait(-1) | Out-Null
        $netTask.Result
    }
    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
    [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

    $engine = $null
    foreach ($tag in @('zh-Hans-CN', 'zh-Hans', 'zh-CN')) {
        try {
            $lang = New-Object Windows.Globalization.Language $tag
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
        } catch { $engine = $null }
        if ($null -ne $engine) { break }
    }
    if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
    if ($null -eq $engine) { throw 'NO_OCR_ENGINE' }

    $lines.Add('ENGINE=' + $engine.RecognizerLanguage.LanguageTag)

    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    foreach ($ln in $res.Lines) { $lines.Add('TXT=' + $ln.Text) }
    $stream.Dispose()
} catch {
    $lines.Add('ERROR=' + $_.Exception.Message)
}
[System.IO.File]::WriteAllLines($OutPath, $lines, [System.Text.Encoding]::UTF8)
"""


# ------------------------------------------------------------------ 公共工具
def _work_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "contract_review_ocr")
    os.makedirs(d, exist_ok=True)
    return d


def _script_path() -> str:
    p = os.path.join(_work_dir(), "ocr_win.ps1")
    if not os.path.exists(p):
        with open(p, "w", encoding="ascii", newline="\r\n") as f:
            f.write(_PS_SCRIPT)
    return p


_CJK_RANGE = "[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]"
_GAP_RE = re.compile("(?<=%s) +(?=%s)" % (_CJK_RANGE, _CJK_RANGE))


def normalize_ocr_text(line: str) -> str:
    """规整 OCR 输出。

    Windows OCR 对中文常按单字切分并插入空格（如「智 能 制 造」），
    这里把 CJK 字符之间的空格去掉，恢复正常的行文；
    同时对全角空格与连续空格一并进行压缩。
    """
    if not line:
        return ""
    s = line.replace("\u3000", " ")
    prev = None
    while prev != s:
        prev = s
        s = _GAP_RE.sub("", s)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _confidence(text: str) -> float:
    """OCR 置信度的可解释近似：有效字符占比 × 长度饱和因子。"""
    if not text:
        return 0.0
    body = re.sub(r"\s", "", text)
    if not body:
        return 0.0
    good = sum(1 for ch in body if "\u4e00" <= ch <= "\u9fff" or ch.isalnum()
               or ch in "，。、；：（）《》“”‘’％%￥$-.·/")
    ratio = good / len(body)
    length_factor = min(1.0, len(body) / 200.0)
    return round(ratio * (0.55 + 0.45 * length_factor), 4)


def _new_result(engine: str):
    return {
        "ok": False, "doc_type": "image", "engine": engine,
        "paragraphs": [], "text": "", "page_count": 1,
        "char_count": 0, "error": "", "need_manual": False,
        "ocr_confidence": 0.0,
    }


def _finish(result, texts, engine_tag="", min_chars=30, min_confidence=0.5):
    """把识别到的行文本组装成统一的段落树结果，并按配置阈值判定是否可用。"""
    texts = [t for t in (normalize_ocr_text(x) for x in texts) if t]
    text = "\n".join(texts)
    paras = []
    cursor = 0
    for i, t in enumerate(texts):
        paras.append({
            "index": i, "text": t, "page": 1,
            "is_heading": bool(re.match(
                r"^(第[一二三四五六七八九十百]+[章条节]|[一二三四五六七八九十]+、|\d+[\.、])", t)),
            "char_start": cursor, "char_end": cursor + len(t),
        })
        cursor = cursor + len(t) + 1            # +1 对应拼接时的换行符
    conf = _confidence(text)
    body = len(re.sub(r"\s", "", text))
    min_chars = max(1, int(min_chars))
    min_confidence = float(min_confidence)

    result["paragraphs"] = paras
    result["text"] = text
    result["char_count"] = len(text)
    result["ocr_confidence"] = conf
    if engine_tag:
        result["ocr_engine_tag"] = engine_tag

    too_short = body < min_chars
    low_conf = conf < min_confidence
    result["ok"] = not too_short and not low_conf
    result["need_manual"] = not result["ok"]
    if not result["ok"]:
        reasons = []
        if too_short:
            reasons.append("识别字数 %d 低于阈值 %d" % (body, min_chars))
        if low_conf:
            reasons.append("置信度 %.2f 低于阈值 %.2f" % (conf, min_confidence))
        result["error"] = ("扫描件 OCR 质量不足（%s），建议重新上传更清晰的文件"
                           % "；".join(reasons))
    return result


# ------------------------------------------------------------------ 后端探测
def _tesseract() -> str:
    return shutil.which("tesseract") or ""


def tesseract_langs(exe: str):
    try:
        p = subprocess.run([exe, "--list-langs"], capture_output=True, timeout=40)
        raw = (p.stdout or b"") + (p.stderr or b"")
        out = []
        for line in raw.decode("utf-8", "ignore").splitlines():
            line = line.strip()
            if line and " " not in line and ":" not in line:
                out.append(line)
        return out
    except Exception:                                          # noqa: BLE001
        return []


def available() -> bool:
    return sys.platform.startswith("win") or bool(_tesseract())


def backend_name() -> str:
    if sys.platform.startswith("win"):
        return ENGINE_WIN
    if _tesseract():
        return ENGINE_TESS
    return ""


# ------------------------------------------------------------------ Linux / tesseract
def _ocr_tesseract(path: str, min_chars: int = 30, min_confidence: float = 0.5):
    result = _new_result(ENGINE_TESS)
    exe = _tesseract()
    if not exe:
        result["error"] = "未安装 tesseract，无法识别扫描件"
        result["need_manual"] = True
        return result

    langs = tesseract_langs(exe)
    if "chi_sim" in langs and "eng" in langs:
        lang = "chi_sim+eng"
    elif "chi_sim" in langs:
        lang = "chi_sim"
    elif "eng" in langs:
        lang = "eng"
    else:
        lang = ""
    cmd = [exe, path, "stdout"]
    if lang:
        cmd += ["-l", lang]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=180)
    except Exception as exc:                                   # noqa: BLE001
        result["error"] = "调用 tesseract 失败：%s" % exc
        result["need_manual"] = True
        return result
    if p.returncode != 0 and not p.stdout:
        result["error"] = "tesseract 执行失败：%s" % (
            (p.stderr or b"").decode("utf-8", "ignore")[:200] or "unknown")
        result["need_manual"] = True
        return result
    text = (p.stdout or b"").decode("utf-8", "ignore")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return _finish(result, lines, engine_tag="tesseract:%s" % (lang or "default"),
                   min_chars=min_chars, min_confidence=min_confidence)


# ------------------------------------------------------------------ Windows
def _ocr_windows(path: str, min_chars: int = 30, min_confidence: float = 0.5):
    result = _new_result(ENGINE_WIN)
    wd = _work_dir()
    tag = hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()[:12]
    ext = os.path.splitext(path)[1].lower() or ".png"
    img_copy = os.path.join(wd, "img_%s%s" % (tag, ext))
    out_txt = os.path.join(wd, "out_%s.txt" % tag)
    if os.path.exists(out_txt):
        try:
            os.remove(out_txt)
        except OSError:
            pass
    try:
        if os.path.abspath(path) != os.path.abspath(img_copy):
            shutil.copyfile(path, img_copy)
    except OSError as exc:
        result["error"] = "图片准备失败：%s" % exc
        result["need_manual"] = True
        return result

    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", _script_path(), "-ImagePath", img_copy, "-OutPath", out_txt]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180)
    except Exception as exc:                                   # noqa: BLE001
        result["error"] = "调用系统 OCR 失败：%s" % exc
        result["need_manual"] = True
        return result
    if not os.path.exists(out_txt):
        result["error"] = "系统 OCR 未产出结果（识别器不可用）"
        result["need_manual"] = True
        return result

    with open(out_txt, "r", encoding="utf-8", errors="ignore") as f:
        rows = [ln.rstrip("\n") for ln in f]
    err, engine_tag, texts = "", "", []
    for row in rows:
        row = row.lstrip("\ufeff")
        if row.startswith("ERROR="):
            err = row[6:].strip()
        elif row.startswith("ENGINE="):
            engine_tag = row[7:].strip()
        elif row.startswith("TXT="):
            texts.append(row[4:])
    if err:
        result["error"] = "系统 OCR 报错：%s（语言包或识别器不可用）" % err
        result["need_manual"] = True
        return result
    return _finish(result, texts, engine_tag=engine_tag,
                   min_chars=min_chars, min_confidence=min_confidence)


# ------------------------------------------------------------------ 统一入口
def ocr_image(path: str, min_chars: int = 30, min_confidence: float = 0.5):
    """对图片执行 OCR，返回与其他解析器一致的结果结构。

    阈值由配置传入（review.min_text_chars / review.ocr_min_confidence），
    不满足时判为需人工复核，而不是简单返回空文本。
    """
    if not os.path.exists(path):
        result = _new_result(backend_name() or ENGINE_WIN)
        result["error"] = "图片文件不存在"
        result["need_manual"] = True
        return result
    if sys.platform.startswith("win"):
        return _ocr_windows(path, min_chars, min_confidence)
    if _tesseract():
        return _ocr_tesseract(path, min_chars, min_confidence)
    result = _new_result("-")
    result["error"] = ("当前系统未提供可用的 OCR 引擎（Linux 需安装 tesseract 及中文语言包，"
                       "例如 `yum install -y tesseract tesseract-langpack-chi_sim`），"
                       "请改用 PDF / Word 版本合同，或转人工复核")
    result["need_manual"] = True
    return result
