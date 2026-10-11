# -*- coding: utf-8 -*-
"""衡鉴 · 关键文档 Word 导出

把 docs/ 下的关键 Markdown 转成 .docx，用于提交与评审。
为什么不用 pandoc？—— 本机没有，而且我们只需要一个很小的 Markdown 子集，
自己写一个反而更可控（尤其是中文字体必须显式设置 w:eastAsia，否则 Word 里会变宋体）。

用法：
    python tools/md_to_docx.py                      # 导出内置关键文档清单
    python tools/md_to_docx.py <md路径> <docx路径>   # 导出单个文件
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent.parent   # tools -> backend -> hengjian
DOCS = ROOT / "docs"

CN_FONT = "Microsoft YaHei"
MONO_FONT = "Consolas"

KEY_DOCS = [
    ("06_architecture_design/技术方案.md", "衡鉴2.0-技术方案.docx"),
    ("06_architecture_design/系统架构设计文档.md", "衡鉴2.0-系统架构设计文档.docx"),
    ("08_api_spec/api_spec-v1.0.md", "衡鉴2.0-API接口规范文档.docx"),
    ("07_data_model/领域数据模型定义.md", "衡鉴2.0-领域数据模型定义.docx"),
    ("07_data_model/审核规则体系梳理.md", "衡鉴2.0-审核规则体系梳理.docx"),
    ("03_problem_modeling/业务问题建模文档.md", "衡鉴2.0-业务问题建模文档.docx"),
    ("04_interaction_design/审核流程与状态设计.md", "衡鉴2.0-审核流程与状态设计.docx"),
    ("05_prototype/前端交互设计规范.md", "衡鉴2.0-前端交互设计规范.docx"),
    ("02_competitor_analysis/竞品功能拆解报告.md", "衡鉴2.0-竞品功能拆解报告.docx"),
    ("11_integration/集成联调计划.md", "衡鉴2.0-集成联调计划.docx"),
    ("13_delivery/验收对照表.md", "衡鉴2.0-验收对照表.docx"),
    ("13_delivery/设备手册.md", "衡鉴2.0-设备手册.docx"),
    ("13_delivery/演示脚本.md", "衡鉴2.0-演示脚本.docx"),
    ("12_release_deployment/部署与运维规范.md", "衡鉴2.0-部署与运维规范.docx"),
    ("12_release_deployment/项目环境要求.md", "衡鉴2.0-项目环境要求.docx"),
    ("00_setup/交付文档总览.md", "衡鉴2.0-交付文档总览.docx"),
    ("00_setup/团队分工架构.md", "衡鉴2.0-团队分工架构.docx"),
    ("00_setup/任务清单.md", "衡鉴2.0-任务清单.docx"),
    ("00_setup/原始资产整理.md", "衡鉴2.0-原始资产整理.docx"),
    ("13_delivery/会议纪要-第三次.md", "衡鉴2.0-会议纪要-第三次.docx"),
    ("13_delivery/会议纪要-第四次-项目结项交付.md", "衡鉴2.0-会议纪要-第四次-项目结项交付.docx"),
]

_H = re.compile(r"^(#{1,6})\s+(.*)$")
_HR = re.compile(r"^\s*[-*_]{3,}\s*$")
_UL = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL = re.compile(r"^(\s*)(\d+)[.、)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_SEP = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


# ------------------------------------------------------------------ 行内样式
def _runs(paragraph, text: str, *, size=10.5, color=None, base_bold=False):
    """渲染行内 **粗体** 与 `代码`。"""
    tokens = re.split(r"(\*\*.+?\*\*|`[^`]+`)", text)
    for tk in tokens:
        if not tk:
            continue
        bold, mono, body = base_bold, False, tk
        if tk.startswith("**") and tk.endswith("**") and len(tk) > 4:
            bold, body = True, tk[2:-2]
        elif tk.startswith("`") and tk.endswith("`") and len(tk) > 2:
            mono, body = True, tk[1:-1]
        run = paragraph.add_run(body)
        run.font.size = Pt(size - 0.5 if mono else size)
        run.font.bold = bold
        run.font.name = MONO_FONT if mono else CN_FONT
        if mono:
            run.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
        elif color:
            run.font.color.rgb = color
        # 中文字体必须显式设置 eastAsia，否则 Word 会回退到默认宋体
        rPr = run._element.get_or_add_rPr()
        rf = rPr.find(qn("w:rFonts"))
        if rf is None:
            rf = rPr.makeelement(qn("w:rFonts"), {})
            rPr.append(rf)
        rf.set(qn("w:eastAsia"), MONO_FONT if mono else CN_FONT)
    return paragraph


def _spacing(p, *, before=0, after=4, line=1.35):
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = line


# ------------------------------------------------------------------ 转换
def convert(md_path: Path, docx_path: Path, title: str | None = None) -> Path:
    lines = md_path.read_text(encoding="utf-8").split("\n")
    doc = Document()

    # 全局样式
    style = doc.styles["Normal"]
    style.font.name = CN_FONT
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)

    # 页边距（A4 窄边距，便于容纳表格）
    for sec in doc.sections:
        sec.top_margin = Pt(56)
        sec.bottom_margin = Pt(56)
        sec.left_margin = Pt(60)
        sec.right_margin = Pt(56)

    if title:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _runs(p, title, size=19, base_bold=True)
        _spacing(p, after=10)

    i = 0
    in_code = False
    code_buf: list[str] = []
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        # ---------- 代码块 ----------
        if line.strip().startswith("```"):
            if in_code:
                p = doc.add_paragraph()
                _spacing(p, before=4, after=8, line=1.15)
                run = p.add_run("\n".join(code_buf))
                run.font.name = MONO_FONT
                run.font.size = Pt(8.8)
                rPr = run._element.get_or_add_rPr()
                rf = rPr.makeelement(qn("w:rFonts"), {})
                rf.set(qn("w:eastAsia"), MONO_FONT)
                rPr.append(rf)
                from docx.enum.text import WD_LINE_SPACING
                p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(raw)
            i += 1
            continue

        # ---------- 空行 / 分隔线 ----------
        if not line.strip():
            i += 1
            continue
        if _HR.match(line):
            p = doc.add_paragraph()
            _spacing(p, before=4, after=6)
            run = p.add_run("─" * 46)
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
            i += 1
            continue

        # ---------- 表格 ----------
        if line.strip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].rstrip())
                i += 1
            rows = []
            for b in block:
                if _SEP.match(b):
                    continue
                cells = [c.strip() for c in b.strip().strip("|").split("|")]
                rows.append(cells)
            if rows:
                ncol = max(len(r) for r in rows)
                table = doc.add_table(rows=0, cols=ncol)
                table.style = "Light Grid Accent 1"
                for ri, r in enumerate(rows):
                    cells = table.add_row().cells
                    for ci in range(ncol):
                        txt = r[ci] if ci < len(r) else ""
                        cell = cells[ci]
                        cell.text = ""
                        para = cell.paragraphs[0]
                        _spacing(para, after=0, line=1.2)
                        _runs(para, txt, size=9, base_bold=(ri == 0))
                p = doc.add_paragraph()
                _spacing(p, after=4)
            continue

        # ---------- 标题 ----------
        m = _H.match(line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            p = doc.add_paragraph()
            sizes = {1: 17, 2: 14, 3: 12, 4: 11, 5: 10.5, 6: 10.5}
            _runs(p, text, size=sizes.get(level, 10.5), base_bold=True)
            _spacing(p, before=(12 if level <= 2 else 9), after=5)
            i += 1
            continue

        # ---------- 引用 ----------
        m = _QUOTE.match(line)
        if m:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            _runs(p, m.group(1), size=10)
            _spacing(p, after=4)
            i += 1
            continue

        # ---------- 列表 ----------
        m = _UL.match(line)
        if m:
            indent = len(m.group(1)) // 2
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.left_indent = Pt(16 + indent * 14)
            _runs(p, m.group(2), size=10.5)
            _spacing(p, after=2)
            i += 1
            continue
        m = _OL.match(line)
        if m:
            indent = len(m.group(1)) // 2
            p = doc.add_paragraph(style="List Number")
            p.paragraph_format.left_indent = Pt(16 + indent * 14)
            _runs(p, m.group(3), size=10.5)
            _spacing(p, after=2)
            i += 1
            continue

        # ---------- 普通段落 ----------
        p = doc.add_paragraph()
        _runs(p, line.strip(), size=10.5)
        _spacing(p, after=5)
        i += 1

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(docx_path))
    return docx_path


def main() -> None:
    if len(sys.argv) >= 3:
        src, dst = Path(sys.argv[1]), Path(sys.argv[2])
        path = convert(src, dst)
        print(f"OK -> {path}")
        return

    out_dir = ROOT / "交付文档-Word版"
    ok, fail = [], []
    for rel, name in KEY_DOCS:
        src = DOCS / rel
        if not src.exists():
            fail.append(f"缺失源文件：{rel}")
            continue
        try:
            path = convert(src, out_dir / name, title=name.replace("衡鉴2.0-", "").replace(".docx", ""))
            ok.append(f"{name}  ({path.stat().st_size // 1024} KB)")
        except Exception as exc:  # noqa: BLE001
            fail.append(f"{rel} -> {exc}")

    print(f"导出成功 {len(ok)} 份，失败 {len(fail)} 份 -> {out_dir}")
    for x in ok:
        print("  [ok] " + x)
    for x in fail:
        print("  [!!] " + x)


if __name__ == "__main__":
    main()
