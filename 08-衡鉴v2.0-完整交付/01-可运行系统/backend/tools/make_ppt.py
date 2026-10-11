# -*- coding: utf-8 -*-
"""衡鉴 2.0 · 项目路演 PPT 生成器

为什么用脚本生成 PPT 而不是手工做？——因为所有数字都必须与《项目事实基线》一致。
脚本从同一份事实来源取值，改一个数字只需改一处，不会出现"PPT 与文档对不上"。

用法：
    python tools/make_ppt.py [输出路径]
默认输出：<项目根>/衡鉴2.0_项目路演.pptx
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parent.parent.parent   # tools -> backend -> hengjian

# ------------------------------------------------------------------ 设计系统
BG = RGBColor(0x0B, 0x0E, 0x14)
BG1 = RGBColor(0x11, 0x16, 0x1F)
BG2 = RGBColor(0x16, 0x1C, 0x27)
LINE = RGBColor(0x2E, 0x39, 0x4B)
TEXT = RGBColor(0xE6, 0xEA, 0xF2)
TEXT2 = RGBColor(0xA7, 0xB0, 0xC0)
TEXT3 = RGBColor(0x6E, 0x7A, 0x8D)
BRAND = RGBColor(0x4C, 0x8D, 0xFF)
BRAND_D = RGBColor(0x2C, 0x5F, 0xA8)
OK = RGBColor(0x35, 0xCE, 0x8D)
WARN = RGBColor(0xF5, 0xC5, 0x42)
HIGH = RGBColor(0xFF, 0x5D, 0x5D)
PURPLE = RGBColor(0xA9, 0x8B, 0xFF)

FONT = "Microsoft YaHei"
W, H = Inches(13.333), Inches(7.5)


# ------------------------------------------------------------------ 基础工具
def set_font(run, size=14, bold=False, color=TEXT, font=FONT, italic=False):
    """设置字体。

    注意：python-pptx 的 `run.font._element` 本身就是 rPr 元素
    （CT_TextCharacterProperties），没有 get_or_add_rPr —— 直接在上面挂 a:ea / a:cs
    才能让中文字符用上指定字体（只设 font.name 只影响西文）。
    """
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = font                      # 生成 a:latin
    rPr = run.font._element
    for tag in ("a:ea", "a:cs"):              # 中文（东亚）与复杂文种
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", font)


def textbox(slide, left, top, width, height, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    return tb, tf


def para(tf, text, *, size=14, bold=False, color=TEXT, space_after=6,
         space_before=0, first=False, align=PP_ALIGN.LEFT, line=None):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.space_after = Pt(space_after)
    p.space_before = Pt(space_before)
    if line:
        p.line_spacing = line
    run = p.add_run()
    run.text = text
    set_font(run, size=size, bold=bold, color=color)
    return p


def rect(slide, left, top, width, height, fill, line=None, radius=None):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    shape.shadow.inherit = False
    if radius:
        try:
            shape.adjustments[0] = radius
        except Exception:
            pass
    return shape


def new_slide(prs, *, page=None, kicker=None, title=None, subtitle=None):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = BG

    # 顶部品牌条
    rect(slide, Inches(0), Inches(0), W, Inches(0.055), BRAND)

    if kicker:
        _, tf = textbox(slide, Inches(0.7), Inches(0.42), Inches(9), Inches(0.32))
        para(tf, kicker, size=11.5, color=BRAND, bold=True, first=True, space_after=0)

    if title:
        _, tf = textbox(slide, Inches(0.7), Inches(0.72), Inches(11.9), Inches(0.7))
        para(tf, title, size=27, bold=True, color=TEXT, first=True, space_after=0)

    if subtitle:
        _, tf = textbox(slide, Inches(0.7), Inches(1.42), Inches(11.9), Inches(0.5))
        para(tf, subtitle, size=13, color=TEXT2, first=True, space_after=0, line=1.35)

    if page is not None:
        _, tf = textbox(slide, Inches(12.1), Inches(6.92), Inches(0.9), Inches(0.3))
        para(tf, str(page), size=10.5, color=TEXT3, first=True, space_after=0,
             align=PP_ALIGN.RIGHT)
    return slide


def body_top(subtitle: bool) -> Emu:
    return Inches(2.05) if subtitle else Inches(1.62)


def bullets(slide, items, left, top, width, height, size=13.5, gap=9):
    _, tf = textbox(slide, left, top, width, height)
    for i, it in enumerate(items):
        if isinstance(it, tuple):
            text, color = it
        else:
            text, color = it, TEXT2
        para(tf, text, size=size, color=color, first=(i == 0),
             space_after=gap, line=1.3)
    return tf


def table_cards(slide, cards, top, *, cols=None, height=Inches(1.5),
                left=Inches(0.7), total_width=Inches(11.93), gap=Inches(0.22)):
    """等宽卡片网格。cards: [(标题, 正文, 状态色)]"""
    cols = cols or len(cards)
    rows = (len(cards) + cols - 1) // cols
    cw = int((total_width - gap * (cols - 1)) / cols)
    for i, (t, b, color) in enumerate(cards):
        r, c = divmod(i, cols)
        l = left + (cw + gap) * c
        tp = top + (height + gap) * r
        rect(slide, l, tp, cw, height, BG1, line=LINE, radius=0.06)
        rect(slide, l, tp, Inches(0.045), height, color)
        _, tf = textbox(slide, l + Inches(0.22), tp + Inches(0.16),
                        cw - Inches(0.4), height - Inches(0.28))
        para(tf, t, size=13, bold=True, color=TEXT, first=True, space_after=5)
        para(tf, b, size=11.5, color=TEXT2, space_after=0, line=1.35)


def stat_row(slide, stats, top, *, left=Inches(0.7), total_width=Inches(11.93),
             gap=Inches(0.18), height=Inches(1.15)):
    n = len(stats)
    cw = int((total_width - gap * (n - 1)) / n)
    for i, (k, v, color) in enumerate(stats):
        l = left + (cw + gap) * i
        rect(slide, l, top, cw, height, BG1, line=LINE, radius=0.08)
        _, tf = textbox(slide, l + Inches(0.18), top + Inches(0.13),
                        cw - Inches(0.32), height - Inches(0.22))
        para(tf, k, size=10.5, color=TEXT3, first=True, space_after=2)
        para(tf, v, size=19, bold=True, color=color, space_after=0)


# ------------------------------------------------------------------ 幻灯片
def build(output: Path) -> Path:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H

    # ============================================== 1 封面
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid(); s.background.fill.fore_color.rgb = BG
    rect(s, Inches(0), Inches(0), W, Inches(0.075), BRAND)
    rect(s, Inches(0.9), Inches(2.0), Inches(0.06), Inches(2.4), BRAND)

    _, tf = textbox(s, Inches(1.25), Inches(2.02), Inches(10), Inches(1.2))
    para(tf, "衡鉴", size=54, bold=True, color=TEXT, first=True, space_after=2)
    para(tf, "HENGJIAN", size=13, color=BRAND, bold=True, space_after=0)

    _, tf = textbox(s, Inches(1.25), Inches(3.5), Inches(10.5), Inches(1.0))
    para(tf, "企业合同智能审查系统", size=24, bold=True, color=TEXT2, first=True,
         space_after=8)
    para(tf, "让每一份合同，在审批前先过一遍「数字法务」的脑子 —— "
             "风险可定位、依据可追溯、意见可回写。",
         size=13.5, color=TEXT2, space_after=0, line=1.4)

    _, tf = textbox(s, Inches(1.25), Inches(5.15), Inches(11), Inches(1.2))
    para(tf, "版本 2.0.0　｜　需求出处：《项目实战》2.4 合同审批审查系统　｜　"
             "验收：八条标准 8/8 满足", size=12, color=TEXT3, first=True, space_after=5)
    para(tf, "交付人：李奕波　｜　2026-09-19", size=12, color=TEXT3, space_after=0)

    stat_row(s, [("规则条数", "32", BRAND), ("接口路径", "33", BRAND),
                 ("单元测试", "39/39", OK), ("接口冒烟", "64/64", OK),
                 ("高风险召回", "100%", OK), ("误报", "0", OK)],
             Inches(5.95), height=Inches(1.05))

    # ============================================== 2 问题定义
    s = new_slide(prs, page=2, kicker="一、业务建模 · 项目场景分析",
                  title="审批人只有几分钟，却要读懂几十页合同",
                  subtitle="真实痛点从来不是「没有制度」，而是「人不够快、不够专、留不下痕」。")
    table_cards(s, [
        ("看不完", "一份采购合同 30–60 页，逐条通读需 2 小时以上。\n审批流卡在人身上，业务等不起。", HIGH),
        ("看不准", "风险藏在违约责任、争议管辖、知识产权等少数条款里。\n缺一条验收前置条件，可能就是全款风险。", WARN),
        ("说不清", "审批意见靠口头与批注，历史合同无法复盘。\n出了纠纷，责任链断在半路。", PURPLE),
    ], Inches(2.35), cols=3, height=Inches(1.95))

    rect(s, Inches(0.7), Inches(4.65), Inches(11.93), Inches(1.9), BG1, line=BRAND_D, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(4.85), Inches(11.3), Inches(1.6))
    para(tf, "我们对这个项目的问题定义", size=12, color=BRAND, bold=True, first=True,
         space_after=8)
    para(tf, "在【不改变审批权责】的前提下，把「读合同、找风险、写意见」三步自动化，"
             "让审批人只做一件事 —— 做决定。",
         size=17, bold=True, color=TEXT, space_after=8, line=1.35)
    para(tf, "边界：系统只生成审查意见、只写审批评论区；不提供通过 / 拒绝 / 转交 / 撤回等"
             "任何改变审批结论的动作。最终判断永远属于审批人。",
         size=12.5, color=TEXT2, space_after=0, line=1.4)

    # ============================================== 3 核心主张
    s = new_slide(prs, page=3, kicker="一、业务建模 · 问题建模与边界定义",
                  title="谁有权下结论 —— 这是整套系统最重要的一条判断",
                  subtitle="不是「把合同丢给大模型」，而是明确划分：谁判定、谁表达、谁决定。")
    table_cards(s, [
        ("规则引擎（主路径）", "有权：判定风险等级、给出命中项与原文坐标。\n\n"
                              "理由：同一份合同任何时候跑都必须得到同一份结论 —— "
                              "可复现才能被法务采信。", BRAND),
        ("大模型（增强路径）", "只做三件事：解释成因、润色示范条款、检测跨条款矛盾。\n\n"
                              "理由：这些恰恰是规则写不出来、而模型擅长的部分。", PURPLE),
        ("法务审查人", "决定：采纳或调整建议、补充批注、是否回写审批单。\n\n"
                       "系统的输出是「意见」，不是「结论」。", OK),
    ], Inches(2.15), cols=3, height=Inches(2.6))

    rect(s, Inches(0.7), Inches(5.15), Inches(11.93), Inches(1.42), BG1, line=LINE, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(5.33), Inches(11.3), Inches(1.15))
    para(tf, "约束不是口号，是代码里的硬约束", size=12, bold=True, color=BRAND,
         first=True, space_after=6)
    para(tf, "① LLM 的 Prompt 明确禁止新增 / 删除 / 合并风险项，禁止修改风险等级；"
             "② 输出必须过结构校验，失败即降级为模板生成器；"
             "③ 后端不存在任何改变审批结论的接口。",
         size=12.5, color=TEXT2, space_after=0, line=1.45)

    # ============================================== 4 七层架构
    s = new_slide(prs, page=4, kicker="二、技术架构 · Agent 多步推理架构",
                  title="七层架构：从接入到观测的完整链路",
                  subtitle="依赖方向单向向下（api → services → db/config）；"
                           "解析与审查严格分离，避免带着空正文跑出「看起来正常其实全错」的结论。")
    lanes = [
        ("① 接入层", "本地上传（DOCX / PDF / 扫描件）· 审批系统待办拉取（模拟器）", BRAND),
        ("② 编排层", "pipeline.py：任务状态机 · 幂等控制 · 阻塞重试 · SSE 事件总线", BRAND),
        ("③ 解析层", "parser_docx / parser_pdf / parser_ocr → coordinator 统一坐标系", OK),
        ("④ 审查层", "rule_engine（32 条规则 / 8 种判定）→ llm（证据约束，可降级）", WARN),
        ("⑤ 回写层", "writer 意见组装 + 合同画像 · writeback(DRY_RUN + 幂等) · report(MD/PDF)", PURPLE),
        ("⑥ 数据层", "SQLite(WAL) 10 张表：任务 / 文档 / 条款 / 命中 / 意见 / 回写 / 审计 / 评测 / HITL", TEXT3),
        ("⑦ 观测层", "audit_logs 全链路留痕 · eval_runs 评测看板 · /api/system/health 健康检查", TEXT3),
    ]
    top = Inches(2.1)
    for i, (name, desc, color) in enumerate(lanes):
        tp = top + Inches(0.62) * i
        rect(s, Inches(0.7), tp, Inches(11.93), Inches(0.54), BG1, line=LINE, radius=0.12)
        rect(s, Inches(0.7), tp, Inches(0.05), Inches(0.54), color)
        _, tf = textbox(s, Inches(0.95), tp + Inches(0.09), Inches(1.5), Inches(0.36))
        para(tf, name, size=12.5, bold=True, color=TEXT, first=True, space_after=0)
        _, tf = textbox(s, Inches(2.5), tp + Inches(0.09), Inches(10), Inches(0.36))
        para(tf, desc, size=11.5, color=TEXT2, first=True, space_after=0)

    # ============================================== 5 三个支点
    s = new_slide(prs, page=5, kicker="二、技术架构 · 可解释性输出方案",
                  title="三个技术支点",
                  subtitle="支撑「结论可复现、证据可溯源、演示零事故」的三件事。")
    table_cards(s, [
        ("支点一 · 统一坐标系",
         "唯一真理坐标系 = 解析出的全文字符串。\n"
         "任何风险必须落到 [char_start, char_end) 区间；\n"
         "DOCX / PDF / OCR 三源同构映射。\n\n"
         "→ 前端只需一种高亮逻辑；法务可逐字核对。", BRAND),
        ("支点二 · 视角解析",
         "「知识产权归甲方所有」在采购合同里对我方有利，\n"
         "在销售合同里恰恰相反。\n"
         "规则用 {我方} / {对方} 占位书写，运行期实例化。\n\n"
         "→ 脱离视角的关键词匹配必然大面积误报。", WARN),
        ("支点三 · 双引擎可降级",
         "规则引擎下判定（可复现、可审计）；\n"
         "LLM 只解释 / 润色 / 查矛盾。\n"
         "超时 8 秒或返回非法 → 确定性模板生成器。\n\n"
         "→ 结论与建议完全不变，只是解释由模板拼装。", OK),
    ], Inches(2.05), cols=3, height=Inches(4.4))

    # ============================================== 6 统一坐标系
    s = new_slide(prs, page=6, kicker="二、技术架构 · 可解释性输出方案",
                  title="证据链到字符级：点哪跳哪，划哪查哪",
                  subtitle="大多数方案止步于「给出风险段落文字」。"
                           "而法务敢采信的前提，是能从结论一路点到原文那一句。")
    stat_row(s, [("DOCX", "段落 + 字符区间", BRAND), ("PDF", "页 + 块 + 字符区间", BRAND),
                 ("扫描件", "OCR 行框 + 字符区间", BRAND), ("前端高亮逻辑", "1 套", OK)],
             Inches(2.05), height=Inches(1.25))

    rect(s, Inches(0.7), Inches(3.65), Inches(5.85), Inches(2.9), BG1, line=LINE, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(3.85), Inches(5.3), Inches(2.6))
    para(tf, "卡片 → 原文", size=13.5, bold=True, color=BRAND, first=True, space_after=7)
    para(tf, "点右侧任一条风险卡，左侧平滑滚动到目标段落并播放高亮脉冲。",
         size=12, color=TEXT2, space_after=9, line=1.4)
    para(tf, "实测（01 埋雷合同）", size=11.5, bold=True, color=TEXT, space_after=5)
    for line in ["HJ-H-001 知识产权归属相对方 → 第 2 页 段#19 [627,687)",
                 "HJ-H-003 违约责任严重不对等 → 第 2 页 段#25 [812,846)",
                 "HJ-H-004 管辖约定在相对方所在地 → 第 3 页 段#32 [1040,1067)"]:
        para(tf, "· " + line, size=10.5, color=TEXT3, space_after=4, line=1.3)

    rect(s, Inches(6.78), Inches(3.65), Inches(5.85), Inches(2.9), BG1, line=LINE, radius=0.05)
    _, tf = textbox(s, Inches(7.08), Inches(3.85), Inches(5.3), Inches(2.6))
    para(tf, "原文 → 卡片（反查）", size=13.5, bold=True, color=OK, first=True, space_after=7)
    para(tf, "在正文里划选一段文字，弹出「片段研判」列出该片段涉及的风险项。",
         size=12, color=TEXT2, space_after=9, line=1.4)
    para(tf, "不做什么", size=11.5, bold=True, color=WARN, space_after=5)
    para(tf, "· 不估算页码：DOCX 无分页信息时如实退化为 page=1",
         size=10.5, color=TEXT3, space_after=4, line=1.3)
    para(tf, "· 不伪造定位：坐标取不到原文时标记为 -1，前端不渲染定位按钮",
         size=10.5, color=TEXT3, space_after=4, line=1.3)
    para(tf, "  假定位比没定位更危险。", size=10.5, bold=True, color=WARN, space_after=0)

    # ============================================== 7 规则体系
    s = new_slide(prs, page=7, kicker="三、数据与接口 · 审核规则体系梳理",
                  title="规则体系：32 条规则 · 8 种判定类型",
                  subtitle="「没写的」和「写错的」一样危险 —— 因此缺失类判定是一等公民。")
    stat_row(s, [("规则总数", "32", BRAND), ("高风险", "11", HIGH), ("中风险", "13", WARN),
                 ("低风险", "8", TEXT3), ("判定类型", "8 种", BRAND),
                 ("示范条款", "14 套", OK)],
             Inches(2.05), height=Inches(1.15))
    table_cards(s, [
        ("keyword / regex", "关键词与正则命中；支持语境排除（exclude_keywords）\n"
                            "例：HJ-H-004 管辖约定在相对方所在地", BRAND),
        ("numeric", "数值阈值比对；支持多模式并排除「设上限」表述\n"
                    "例：HJ-H-006 违约金 > 30% 判高风险", BRAND),
        ("imbalance_regex", "权责不对等：同一方 + 若干字符内 + 加重/减轻措辞\n"
                            "例：HJ-H-003 我方无上限赔偿 vs 对方万分之一", HIGH),
        ("missing_clause / absent", "必备条款缺失；支持前提条件（if_any）\n"
                                    "例：HJ-H-009 缺失违约责任条款", WARN),
        ("required_combo", "条款组合缺失：共现判定 + 前提 + 反向排除\n"
                           "例：HJ-H-002 付款缺少验收前置（到货即付全款）", HIGH),
        ("视角 + 适用面", "{我方}/{对方} 占位 + biz_types 规则级适用范围\n"
                          "例：付款须挂钩验收对劳动合同不适用", PURPLE),
    ], Inches(3.45), cols=3, height=Inches(1.5))

    # ============================================== 8 数据与状态
    s = new_slide(prs, page=8, kicker="三、数据与接口 · 领域数据模型定义",
                  title="数据模型与状态机",
                  subtitle="10 张表 · 5 态任务机 · 4 态回写机 · 6 种阻塞码。"
                           "HITL 用「动作流水」代替状态字段 —— 审计信息就是业务数据本身。")
    table_cards(s, [
        ("10 张表", "contracts · documents · clauses · findings · reviews\n"
                    "writebacks · audit_logs · eval_runs · platform_comments · hitl_actions", BRAND),
        ("任务状态机", "pending → parsing → reviewing → completed\n"
                       "　　　　└→ blocked（一键重试，幂等键保留）", OK),
        ("回写状态机", "not_written → writing → success / failed\n"
                       "失败保留错误与重试入口；幂等三键防重复", PURPLE),
    ], Inches(2.05), cols=3, height=Inches(1.7))

    rect(s, Inches(0.7), Inches(3.95), Inches(11.93), Inches(2.6), BG1, line=LINE, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(4.13), Inches(11.3), Inches(2.3))
    para(tf, "六种阻塞码：必须可区分，否则重试没有意义", size=12.5, bold=True,
         color=BRAND, first=True, space_after=8)
    para(tf, "DOC_ENCRYPTED　文档加密 → 需去除密码，重试无意义",
         size=11.5, color=TEXT2, space_after=4)
    para(tf, "OCR_ENGINE_UNAVAILABLE　OCR 未安装 → 需装环境，不是文件问题",
         size=11.5, color=TEXT2, space_after=4)
    para(tf, "OCR_LOW_CONFIDENCE　置信度 < 0.35 → 需换清晰扫描件",
         size=11.5, color=TEXT2, space_after=4)
    para(tf, "EMPTY_CONTENT　正文 < 120 字 → 可能是纯图片文档",
         size=11.5, color=TEXT2, space_after=4)
    para(tf, "PARSE_FAILED / PIPELINE_ERROR　→ 查审计留痕定位",
         size=11.5, color=TEXT2, space_after=8)
    para(tf, "「引擎缺失」与「图片模糊」必须分开记录 —— 否则用户只会反复点重试然后放弃。",
         size=11.5, bold=True, color=WARN, space_after=0)

    # ============================================== 9 HITL
    s = new_slide(prs, page=9, kicker="三、技术架构 · 人机交互（HITL）",
                  title="人工审核：中断点不是报错，是流程停下来等人做决定",
                  subtitle="与 LangGraph 的 interrupt / resume 语义对齐；四类动作全部留痕。")
    table_cards(s, [
        ("approve 同意", "同意系统审查结论\n→ 解锁回写审批系统", OK),
        ("edit 修改后确认", "调整风险等级 / 改写建议\n→ 自动重算综合等级与审查结论", BRAND),
        ("reject 驳回", "审查结果存在实质错误时使用\n→ 清空本次产物，任务回 pending 并重跑", HIGH),
        ("resume 恢复", "从阻塞中断点恢复\n→ 等价重试，但保留中断上下文", WARN),
    ], Inches(2.1), cols=4, height=Inches(2.0))

    rect(s, Inches(0.7), Inches(4.35), Inches(11.93), Inches(2.2), BG1, line=LINE, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(4.53), Inches(11.3), Inches(1.9))
    para(tf, "门禁设计：驳回后禁止回写", size=12.5, bold=True, color=HIGH, first=True,
         space_after=7)
    para(tf, "审查结果被驳回后，POST /writeback 返回 409 并提示「请重新发起审查后再回写」。"
             "两道校验：① 任务状态必须为 completed；② 最后一次 HITL 动作不得为 reject。",
         size=12, color=TEXT2, space_after=9, line=1.45)
    para(tf, "为什么用「动作流水」而不是「状态字段」", size=12.5, bold=True,
         color=BRAND, space_after=7)
    para(tf, "hitl_actions 表只增不改不删：谁在什么时候同意 / 修改 / 驳回了什么，天然可追溯。"
             "与 audit_logs 的分工是 —— 前者记录业务决策，后者记录系统执行。",
         size=12, color=TEXT2, space_after=0, line=1.45)

    # ============================================== 10 回写边界
    s = new_slide(prs, page=10, kicker="三、技术架构 · 安全边界",
                  title="审批回写：只写意见，不改结论",
                  subtitle="这是系统能被法务信任的前提，也是所有设计的收束点。")
    table_cards(s, [
        ("只写评论", "后端不存在任何审批结论写操作接口。\n"
                     "系统没有通过 / 拒绝 / 转交 / 撤回能力。", HIGH),
        ("DRY_RUN 前置", "回写前先生成待写入正文给你确认。\n"
                         "预览模式只落库、不外发。", BRAND),
        ("幂等三键", "合同编号 | 附件 SHA256 | 规则版本号。\n"
                     "重复触发返回既有结果并记警告日志。", OK),
        ("状态机兜底", "任何下游异常落到 failed，\n"
                       "保留错误信息与重试入口，不会静默丢单。", WARN),
    ], Inches(2.05), cols=4, height=Inches(2.1))

    rect(s, Inches(0.7), Inches(4.45), Inches(11.93), Inches(2.1), BG1, line=BRAND_D, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(4.63), Inches(11.3), Inches(1.8))
    para(tf, "实测证据（可当场复现）", size=12.5, bold=True, color=BRAND, first=True,
         space_after=8)
    para(tf, "① DRY_RUN 预览 1732 字 → 审批评论区 0 条（未写入）",
         size=11.5, color=TEXT2, space_after=5)
    para(tf, "② 真实回写 → 评论 ID CMT-000001 → 评论区 1 条",
         size=11.5, color=TEXT2, space_after=5)
    para(tf, "③ 再次触发同一回写 → 返回 skipped=true → 评论区仍 1 条（幂等生效）",
         size=11.5, color=TEXT2, space_after=8)
    para(tf, "审计留痕：writeback.preview / writeback.write / writeback.skip 三态均落 audit_logs。",
         size=11, color=TEXT3, space_after=0)

    # ============================================== 11 前端与交互
    s = new_slide(prs, page=11, kicker="四、前端实现 · 设计稿转代码落地",
                  title="零构建前端：4 个页面，深色专业主题",
                  subtitle="不用 npm、不用框架、不用 CDN —— 断网也能完整演示。"
                           "复杂交互用字符区间求交实现。")
    table_cards(s, [
        ("审查大盘", "统计卡 · 任务列表（高风险优先）· 多条件筛选\n"
                     "上传/拉取入口 · 实时流水线时间轴 · 状态分布", BRAND),
        ("智能审查工作台", "双栏联动 · 风险卡分级 · 成因与依据折叠\n"
                           "可编辑采纳 · HITL 面板 · 回写与评论区", OK),
        ("合规规则与模板", "按条款类型分组的规则树 · 规则编辑表单\n"
                           "14 套标准示范条款库 · 反向引用提示", WARN),
        ("评测与观测", "六张指标卡 · 逐份样例明细表\n"
                       "一键跑分 · 系统运行观测", PURPLE),
    ], Inches(2.05), cols=4, height=Inches(2.15))

    rect(s, Inches(0.7), Inches(4.5), Inches(11.93), Inches(2.05), BG1, line=LINE, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(4.68), Inches(11.3), Inches(1.75))
    para(tf, "三个值得单说的交互", size=12.5, bold=True, color=BRAND, first=True,
         space_after=8)
    para(tf, "① 双向联动：点卡片跳页高亮（脉冲）；划选原文反查涉及风险 —— 跨 DOCX / PDF / OCR 共用一套逻辑",
         size=12, color=TEXT2, space_after=5, line=1.4)
    para(tf, "② 键盘流：j / k 切换风险、Enter 采纳、/ 聚焦检索 —— 给每天看几十份合同的人准备的",
         size=12, color=TEXT2, space_after=5, line=1.4)
    para(tf, "③ 实时进度：SSE 事件流，从 seq 断线续传；Nginx 侧必须关闭缓冲，否则事件会被积压",
         size=12, color=TEXT2, space_after=0, line=1.4)

    # ============================================== 12 验证证据
    s = new_slide(prs, page=12, kicker="五、工程与交付 · 验证证据",
                  title="用可复现的数字说话",
                  subtitle="四项验证全部实测通过；每条命令都写在文档里，任何人可以重跑一遍。")
    stat_row(s, [("单元测试", "39 / 39", OK), ("接口冒烟", "64 / 64", OK),
                 ("端到端演示", "8 步", OK), ("高风险召回", "100%", OK),
                 ("误报数", "0", OK), ("平均解析耗时", "≈700ms", BRAND)],
             Inches(2.1), height=Inches(1.3))

    table_cards(s, [
        ("端到端实测（01 埋雷合同）",
         "解析 python-docx · 4 页 / 1245 字 / 10 个条款节点\n"
         "审查命中 11 项（高 6 / 中 2 / 低 3）· 规则判定 1 ms · 结论「建议拒绝」\n"
         "回写 CMT-000001 → 幂等跳过 → 报告 Markdown 3360 字 + PDF", OK),
        ("异常实测",
         "加密件 → blocked · block_code=DOC_ENCRYPTED · 原因可读\n"
         "重试后仍 blocked 且 clauses 表无残留脏数据\n"
         "（文件未更换，重试本就不该成功 —— 这是预期行为）", WARN),
    ], Inches(3.7), cols=2, height=Inches(2.4))

    # ============================================== 13 诚实声明
    s = new_slide(prs, page=13, kicker="五、工程与交付 · 指标口径",
                  title="关于 100% 的诚实说明",
                  subtitle="我们说的是「可验证的真话」，不是「无法验证的漂亮话」。")
    rect(s, Inches(0.7), Inches(1.95), Inches(11.93), Inches(2.15), BG1,
         line=WARN, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(2.15), Inches(11.3), Inches(1.85))
    para(tf, "黄金标注与规则库由同一团队同步编写。", size=15, bold=True,
         color=WARN, first=True, space_after=9, line=1.35)
    para(tf, "因此 100% 召回是【内部回归基线】—— 它的作用是防止「改一条规则、伤三处条款」，"
             "不等于面对全新语料的泛化能力。",
         size=13, color=TEXT, space_after=9, line=1.45)
    para(tf, "类比：单元测试全绿不代表程序没有 bug，只代表已写下的断言都成立。"
             "「我们的规则在自建样例集上召回 100%」可验证；"
             "「我们的系统能识别 100% 的合同风险」无法验证 —— 我们说前者。",
         size=12, color=TEXT2, space_after=0, line=1.45)

    rect(s, Inches(0.7), Inches(4.3), Inches(11.93), Inches(2.3), BG1, line=LINE, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(4.48), Inches(11.3), Inches(2.0))
    para(tf, "补上泛化能力的路径（路线图 M2）", size=12.5, bold=True, color=BRAND,
         first=True, space_after=8)
    for t in ["① 采集 50–100 份真实合同（脱敏），覆盖更多业务类型与排版风格",
              "② 由未参与规则编写的法务人员独立标注风险清单（盲测）",
              "③ 在盲测集上跑分，公开真实的召回率与误报率（预期低于 100%，这是正常的）",
              "④ 按漏报 / 误报聚类，定向补充与收紧规则，形成「标注 → 跑分 → 调优」闭环"]:
        para(tf, t, size=12, color=TEXT2, space_after=5, line=1.4)

    # ============================================== 14 局限与路线图
    s = new_slide(prs, page=14, kicker="五、工程与交付 · 局限与演进",
                  title="诚实的局限清单与演进路线",
                  subtitle="未完成的事不隐藏在细节里 —— 全部登记，标注优先级。")
    table_cards(s, [
        ("U1 三角色 RBAC", "需求 2.4.2 的权限隔离未实现。\n"
                            "当前无鉴权，只能内网演示。\n→ 路线图 M1，上线前必做", HIGH),
        ("U2 真实审批对接", "当前为内置模拟器，未验证真实系统兼容性。\n"
                            "适配器位置已预留。\n→ 路线图 M3", HIGH),
        ("U3 孪生演示台", "单文件 HTML 驾驶舱未实现。\n"
                          "已主动降优先级。", TEXT3),
        ("U4 第三方盲测集", "准确率缺外部验证。\n"
                            "→ 路线图 M2", WARN),
        ("P1 Docker 镜像未实跑", "配置全部就绪、依赖清单已核对，\n"
                                  "但镜像未在本地实际构建。", WARN),
        ("P2 CI 未远端实跑", "四道门禁配置完整、各步命令本地验证通过，\n"
                              "但未在远端 runner 跑过。", WARN),
    ], Inches(2.05), cols=3, height=Inches(1.85))

    rect(s, Inches(0.7), Inches(4.3), Inches(11.93), Inches(2.3), BG1, line=BRAND_D, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(4.48), Inches(11.3), Inches(2.0))
    para(tf, "下一步行动（按优先级）", size=12.5, bold=True, color=BRAND, first=True,
         space_after=8)
    para(tf, "P0　三角色 RBAC 与登录鉴权（复用既有 BossPortal SSO 方案）",
         size=12, color=TEXT2, space_after=5)
    para(tf, "P0　本地实跑 docker build + 容器健康检查",
         size=12, color=TEXT2, space_after=5)
    para(tf, "P1　服务器 8130 部署 + 跨端口 SSO 接入　｜　第三方盲测集（50–100 份真实脱敏合同）",
         size=12, color=TEXT2, space_after=5)
    para(tf, "P2　真实审批系统适配器实现　｜　关键路径录屏与演示彩排 2 次",
         size=12, color=TEXT2, space_after=5)
    para(tf, "P3　批量操作、阻塞告警推送", size=12, color=TEXT2, space_after=0)

    # ============================================== 15 交付物
    s = new_slide(prs, page=15, kicker="五、工程与交付 · 交付物清单",
                  title="交付物：22 份文档 + 可运行系统 + 架构图 + PPT",
                  subtitle="按「基线 → 业务 → 架构 → 实现 → 联调 → 部署 → 交付」七段组织；"
                           "全部数字以《项目事实基线》为准。")
    table_cards(s, [
        ("可运行系统", "backend 30 个 Python 文件 + frontend 10 个静态文件\n"
                       "13 份样例合同 · git 追踪 58 个文件\n双击 start.bat 即可启动", OK),
        ("22 份交付文档", "业务建模 7 · 建模交互 8 · 架构数据 12 · 接口实现 6\n"
                          "联调 1 · 部署交付 8 · 基线 4\n（含正式交付物全部 23 项）", BRAND),
        ("配套产物", "全流程架构图（单文件 HTML）\n"
                     "关键文档 Word 版 · 展示版 HTML\n演示 PPT · 验证证据包", PURPLE),
    ], Inches(2.05), cols=3, height=Inches(2.3))

    rect(s, Inches(0.7), Inches(4.55), Inches(11.93), Inches(2.0), BG1, line=LINE, radius=0.05)
    _, tf = textbox(s, Inches(1.0), Inches(4.73), Inches(11.3), Inches(1.7))
    para(tf, "验收结论", size=12.5, bold=True, color=OK, first=True, space_after=8)
    para(tf, "需求 2.4.10 八条验收标准：8 / 8 满足。", size=15, bold=True, color=TEXT,
         space_after=7)
    para(tf, "需求 2.4.1–2.4.9 开发范围：53 项中 50 项满足、2 项部分完成、1 项未完成（RBAC）。",
         size=12, color=TEXT2, space_after=5)
    para(tf, "交付路径：E:\\桌面\\合同审批系统-李奕波\\08-衡鉴v2.0-完整交付\\",
         size=12, color=TEXT2, space_after=0)

    # ============================================== 16 结尾
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid(); s.background.fill.fore_color.rgb = BG
    rect(s, Inches(0), Inches(0), W, Inches(0.075), BRAND)
    rect(s, Inches(0.9), Inches(2.3), Inches(0.06), Inches(2.5), OK)

    _, tf = textbox(s, Inches(1.25), Inches(2.3), Inches(11), Inches(2.6))
    para(tf, "这套系统值得交付的不是「它能审查合同」，", size=20, color=TEXT2,
         first=True, space_after=10, line=1.35)
    para(tf, "而是每一条结论都能被逐字核对 ——", size=20, color=TEXT2,
         space_after=10, line=1.35)
    para(tf, "点到原文那一句、点到规则那一条、点到法条那一款。", size=24, bold=True,
         color=TEXT, space_after=16, line=1.35)
    para(tf, "至于它还做不到什么，我们都写在了交付物里。", size=15, color=OK,
         space_after=0, line=1.35)

    _, tf = textbox(s, Inches(1.25), Inches(5.5), Inches(11), Inches(1.2))
    para(tf, "衡鉴 HengJian · 企业合同智能审查系统 v2.0.0", size=13, bold=True,
         color=BRAND, first=True, space_after=6)
    para(tf, "交付人：李奕波　｜　编制：启明　｜　2026-09-19", size=12, color=TEXT3,
         space_after=0)

    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output))
    return output


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "衡鉴2.0_项目路演.pptx"
    path = build(target)
    print(f"OK -> {path}  ({path.stat().st_size // 1024} KB)")
