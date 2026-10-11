# -*- coding: utf-8 -*-
"""扫描件 OCR 引擎

设计取舍：
- 唯一外部依赖是 rapidocr-onnxruntime（纯 pip、无系统级依赖，符合"零外部服务"红线）；
- 引擎缺失时不报错崩掉，而是抛 OcrUnavailable，交由协调器把任务置为 blocked 并给出可读原因
  —— "引擎缺失" 与 "图片模糊" 是两种不同的阻塞原因，必须分开记录，否则重试没有意义。
- 行框坐标按 (y, x) 还原阅读顺序，与文本型 PDF / DOCX 共用统一坐标系。
"""
from __future__ import annotations

from pathlib import Path


class OcrUnavailable(Exception):
    """OCR 引擎未安装或初始化失败。"""


_engine = None
_engine_error: str = ""


def _get_engine():
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    if _engine_error:
        raise OcrUnavailable(_engine_error)
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except Exception as exc:  # ImportError / 模型缺失
        _engine_error = (
            "OCR 引擎不可用（未安装 rapidocr-onnxruntime）。"
            "安装命令：uv pip install rapidocr-onnxruntime；"
            f"原始错误：{exc}"
        )
        raise OcrUnavailable(_engine_error) from exc
    try:
        _engine = RapidOCR()
    except Exception as exc:
        _engine_error = f"OCR 引擎初始化失败：{exc}"
        raise OcrUnavailable(_engine_error) from exc
    return _engine


def available() -> bool:
    try:
        _get_engine()
        return True
    except OcrUnavailable:
        return False


def recognize_bytes(data: bytes) -> tuple[list[dict], float]:
    """识别图片字节流，返回 (blocks, 平均置信度)。

    blocks: [{"page":1,"para_index":i,"text":...,"kind":"text","confidence":c}]
    """
    engine = _get_engine()
    result, _elapse = engine(data)
    if not result:
        return [], 0.0

    items = []
    for row in result:
        # RapidOCR 返回 [box(4点), text, score]
        try:
            box, text, score = row[0], row[1], float(row[2])
        except Exception:
            continue
        text = (text or "").strip()
        if not text:
            continue
        try:
            ys = [float(p[1]) for p in box]
            xs = [float(p[0]) for p in box]
            y, x = min(ys), min(xs)
        except Exception:
            y, x = len(items), 0.0
        items.append((y, x, text, score))

    items.sort(key=lambda t: (round(t[0] / 8.0), t[1]))

    blocks: list[dict] = []
    scores: list[float] = []
    for i, (_y, _x, text, score) in enumerate(items):
        blocks.append({
            "page": 1,
            "para_index": i,
            "text": text,
            "kind": "text",
            "confidence": score,
        })
        scores.append(score)

    avg = sum(scores) / len(scores) if scores else 0.0
    return blocks, round(avg, 4)


def recognize_file(path: str | Path) -> tuple[list[dict], float]:
    data = Path(path).read_bytes()
    return recognize_bytes(data)


def engine_name() -> str:
    return "RapidOCR(onnxruntime)"
