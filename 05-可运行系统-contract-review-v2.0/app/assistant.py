# -*- coding: utf-8 -*-
"""AI 助手（页面宠物）的大脑 —— 零依赖实现

设计口径与项目其它部分保持一致：

  * **能确定回答的，绝不交给模型**。业务事实（任务数、风险等级、规则命中、权限边界、
    系统状态）全部由确定性代码从真实数据里查出来；
  * **匹配不到就明说匹配不到**，并给出「我能回答哪些方向」，**不允许编造**；
  * 大模型只在**已确定的事实之上改写措辞**（可选的 llm.enabled），未配置时用内置模板，
    功能不缺失；
  * 引导步骤里每一步都绑定**真实存在的元素选择器**与**所需能力**，
    自检会逐条断言选择器在页面上真的能选中 —— 避免「引导指向不存在的按钮」。

模块只依赖标准库与项目内模块。
"""
from __future__ import annotations

import re
import time

from . import llm as llm_mod
from . import review as review_mod
from . import store as store_mod

# ==========================================================================
# 一、知识库：流程 / 操作 / 权限 / 安全 / 常见问题
#   tags 里放「用户可能怎么问」的词，匹配时按命中词数与权重打分。
# ==========================================================================
KB = [
    # ---------------- 整体与流程
    {
        "id": "what-is-it",
        "tags": ["这是什么", "干什么", "有什么用", "系统介绍", "能做什么", "介绍", "干嘛的"],
        "q": "这个系统是干什么的？",
        "a": ("这是**衡鉴 V2.1.7**。它插在合同审批流程里做一个自动审查节点：\n"
              "合同附件一进来就自动解析 → 切条款 → 比对公司合规规则 → 生成中文审查意见 → "
              "**写回审批单评论区**。\n\n"
              "审批权始终在人手里 —— 系统只写评论，**没有通过 / 拒绝 / 转交这类改变审批结论的能力**。"),
        "chips": ["审查流程分几步？", "支持哪些格式？", "规则库有多少条？"],
    },
    {
        "id": "pipeline",
        "tags": ["流程", "链路", "分几步", "怎么走", "步骤", "环节", "几个阶段", "流水线"],
        "q": "审查流程分几步？",
        "a": ("七段链路，每段都能在孪生看板上看到实时计数：\n\n"
              "1. **审批接入** —— 从审批平台拉取待审合同\n"
              "2. **文档解析** —— DOCX / PDF / 扫描件三路（扫描件走 OCR）\n"
              "3. **条款抽取** —— 切分条款层级 + 抽取关键元数据\n"
              "4. **规则审查** —— 确定性规则引擎判定风险\n"
              "5. **条款推荐** —— 针对风险给出可落地的示范条款\n"
              "6. **意见回写** —— 把审查意见写进审批单评论区\n"
              "7. **报告产出** —— Markdown / HTML / PDF 三通道导出"),
        "chips": ["为什么能保证不重复处理？", "风险等级怎么定的？", "怎么看实时状态？"],
        "actions": [{"label": "去孪生看板看实时链路", "kind": "goto", "target": "/twin"}],
    },
    {
        "id": "idempotent",
        "tags": ["重复", "幂等", "会不会重复", "重复处理", "拉两次", "重复拉取"],
        "q": "会不会重复处理同一份合同？",
        "a": ("三层幂等，任一层命中都不会重复：\n\n"
              "1. **审批单号** —— 同一张审批单只建一次任务\n"
              "2. **附件指纹** —— 对文档算 SHA256，同一份文件不会被重复解析\n"
              "3. **回写状态** —— 已成功回写的任务再点也只会跳过，不会重复刷屏评论区"),
        "chips": ["重复拉取会怎样？", "怎么看审计记录？"],
    },
    {
        "id": "formats",
        "tags": ["格式", "支持什么文件", "docx", "pdf", "扫描件", "图片", "ocr", "图片能识别吗"],
        "q": "支持哪些合同格式？",
        "a": ("四种情况：\n\n"
              "* **DOCX** —— 标准库 `zipfile` + `xml.etree` 直接解析\n"
              "* **PDF（有文本层）** —— 自研对象扫描 + `/Filter` 链 + ToUnicode CMap\n"
              "* **扫描件 / 图片** —— 走系统 OCR 引擎（Windows 内置 / Linux tesseract）\n"
              "* **加密或无文本层** —— 任务转入「解析受阻」并**记录原因**，等人工处理后一键重试\n\n"
              "整条链路零第三方依赖，装好 Python 就能跑。"),
        "chips": ["解析失败了会怎样？", "OCR 准不准？"],
    },
    {
        "id": "risk-level",
        "tags": ["风险等级", "怎么判定", "高风险", "等级怎么来", "谁判定", "模型判定吗", "ai判定"],
        "q": "风险等级是谁判定的？",
        "a": ("**100% 由确定性规则引擎判定**，大模型不参与。\n\n"
              "规则库按合同类型分四类（采购 / 销售 / 服务 / 劳动），共 41 条，支持五种匹配模式：\n"
              "关键词、正则、**必备条款缺失**（全文范围判断，避免条款标题误报）、"
              "**权责不对称配对**（我方不利条款与对方封顶条款同时命中才算）、数值阈值。\n\n"
              "为什么不用模型判定：法务结论必须能被质疑、追溯、复核，模型自由判断满足不了这个要求。"),
        "chips": ["规则库有多少条？", "模型在这里做什么？", "在哪维护规则？"],
        "actions": [{"label": "去规则库看看", "kind": "goto", "target": "/rules"}],
    },
    {
        "id": "llm-role",
        "tags": ["大模型", "模型", "ai", "llm", "gpt", "模型做什么", "有没有ai"],
        "q": "大模型在这里做什么？",
        "a": ("只做**中文表达润色**，不改结论：\n\n"
              "* 输入是规则引擎已经判定好的命中结果\n"
              "* 输出是把这些结论组织成更自然的中文摘要与评论\n"
              "* **不得新增、删减或改变**任何风险结论、等级、金额、条款编号与法律依据\n\n"
              "工作台右侧有「审查表达模式对照」面板，能并排看到两种模式的差别；"
              "没配 Key 时会**明示当前处于规则模式并说明原因**，不假装有。"),
        "chips": ["风险等级是谁判定的？", "模式对照在哪看？"],
    },
    # ---------------- 操作类
    {
        "id": "how-pull",
        "tags": ["拉取", "怎么拉", "拉待办", "从哪开始", "开始审查", "怎么开始", "第一次"],
        "q": "怎么开始审查合同？",
        "a": ("三种入口，任选：\n\n"
              "1. **拉取待办** —— 大盘右上角「拉取待办并审查」，把审批平台里的待审合同拉进来自动审\n"
              "2. **本地上传** —— 大盘右上角「上传合同」，选 DOCX / PDF / 图片\n"
              "3. **自动流转** —— 打开大盘的「自动流转 · 定时拉取」开关，系统按间隔自己拉（默认关闭）\n\n"
              "「拉取待办」需要**业务经办人或系统管理员**权限。"),
        "chips": ["怎么上传合同？", "自动拉取怎么开？", "审查要多久？"],
        "actions": [{"label": "带我走一遍", "kind": "tour", "target": "/"}],
    },
    {
        "id": "how-upload",
        "tags": ["上传", "怎么上传", "本地文件", "上传合同", "传文件"],
        "q": "怎么上传一份合同？",
        "a": ("大盘右上角 **「上传合同」** → 选文件 → 填合同类型（和标题，可留空取自文件名）→ "
              "点「上传并审查」。\n\n"
              "限制与保护：单文件大小上限、队列长度、待处理堆积、附件存储总量**四道配额**都在服务端强制，"
              "超限会被拒并写审计。文件名会做路径穿越硬化处理。"),
        "chips": ["上传后多久出结果？", "支持什么格式？"],
        "actions": [{"label": "带我走一遍", "kind": "tour", "target": "/"}],
    },
    {
        "id": "how-writeback",
        "tags": ["回写", "写回", "审批意见", "评论", "怎么回写", "写回按钮在哪", "回写按钮"],
        "q": "怎么把审查意见写回审批单？",
        "a": ("在**智能审查工作台**右侧「审批协同与回写」：\n\n"
              "1. 先核对风险项，可按需**采纳 / 调整修改建议**\n"
              "2. 在「法务人工批注」写处理意见（会一并写回）\n"
              "3. 点 **「预览回写内容」** 看一眼要发什么（预览模式不写入）\n"
              "4. 确认后点 **「写回审批单」**\n\n"
              "回写需要**法务审查人**权限。系统只写评论，不改变审批结论；重复写会被幂等跳过。"),
        "chips": ["回写失败怎么办？", "在哪里看评论区？", "我为什么点不了写回？"],
        "actions": [{"label": "带我走一遍", "kind": "tour", "target": "/workbench"}],
    },
    {
        "id": "how-adopt",
        "tags": ["采纳", "修改建议", "示范条款", "建议条款", "怎么改条款", "备选方案"],
        "q": "风险项的建议条款怎么用？",
        "a": ("每条风险都带**可直接落地的示范条款**（不是「建议注意」这种废话）：\n\n"
              "* 卡片里是**可编辑文本框**，可以直接改\n"
              "* 有**备选方案**时，点「用此方案替换」一键换\n"
              "* 改完点 **「采纳建议」** 落库，右侧「建议采纳进度」会跟着走\n\n"
              "采纳需要**法务审查人**权限。"),
        "chips": ["怎么写回审批单？", "规则库在哪维护？"],
        "actions": [{"label": "带我走一遍", "kind": "tour", "target": "/workbench"}],
    },
    {
        "id": "how-goto-detail",
        "tags": ["看详情", "打开合同", "怎么看某份合同", "进工作台", "查合同"],
        "q": "怎么看某一份合同的审查结果？",
        "a": ("在大盘列表里点任意一行的 **「审查」**，或点统计卡下钻后的明细行，"
              "都会进入该合同的**智能审查工作台**。\n\n"
              "工作台左栏是合同正文（按页分页、标了段落序号与条款类型、风险原文高亮），"
              "右栏是风险清单与回写区。"),
        "chips": ["双向定位怎么用？", "怎么导出报告？"],
        "actions": [{"label": "去大盘挑一份", "kind": "goto", "target": "/"}],
    },
    {
        "id": "how-highlight",
        "tags": ["高亮", "定位", "双向", "原文定位", "跳到原文", "锚点"],
        "q": "原文和风险卡片的双向定位怎么用？",
        "a": ("工作台里两边互通：\n\n"
              "* **点风险卡片** → 左边正文自动跳到对应**页码 + 段落**并高亮\n"
              "* **点正文里的高亮** → 右边对应卡片被选中\n\n"
              "锚点不是模糊段落，而是**精确到字符区间**的：解析时就记录了页码、段落索引和起止字符位置，"
              "所以能圈出那一句，而不是把整个条款糊成一片。"),
        "chips": ["怎么导出报告？", "风险等级是谁判定的？"],
        "actions": [{"label": "带我走一遍", "kind": "tour", "target": "/workbench"}],
    },
    {
        "id": "how-report",
        "tags": ["导出", "报告", "下载", "pdf", "markdown", "打印"],
        "q": "怎么导出审查报告？",
        "a": ("工作台右上角有两种：\n\n"
              "* **「导出 Markdown」** —— 文本版，便于归档与二次编辑\n"
              "* **「导出 PDF」** —— 正式排版\n\n"
              "PDF 走三条通道，按可用性依次尝试：**浏览器无头打印 → 纯标准库直出 → 可打印 HTML**。"
              "服务器上没有浏览器时也能出 PDF（走零依赖通道），不会永久降级。"),
        "chips": ["报告里有什么？", "怎么看审计记录？"],
    },
    {
        "id": "how-rules",
        "tags": ["规则", "改规则", "维护规则", "加规则", "规则库", "在哪改规则"],
        "q": "规则库在哪里维护？",
        "a": ("顶栏 **「合规规则与模板」**（只有**系统管理员**能看到这个入口）。\n\n"
              "按合同类型分四类维护，每条规则有：风险等级、条款类型、匹配模式、触发条件、法律依据。"
              "在线编辑后写回配置文件，**自动留 `.bak` 备份**。\n\n"
              "写坏的正则不会静默失效 —— 会被跳过并记入诊断，在工作台上能看到。"),
        "chips": ["规则匹配模式有几种？", "我为什么看不到规则页？"],
        "actions": [{"label": "带我走一遍", "kind": "tour", "target": "/rules"}],
    },
    {
        "id": "how-blocked",
        "tags": ["受阻", "失败", "解析失败", "处理不了", "加密", "重试", "阻塞"],
        "q": "解析受阻的任务怎么处理？",
        "a": ("三种情况会进「解析受阻」，都会**记录具体原因**：文档加密、扫描件严重模糊、正文为空。\n\n"
              "处理方式：在大盘按「解析受阻」筛选 → 打开该任务 → 修正文件或调整参数后点 **「重新审查」**。"
              "需要**系统管理员**权限。\n\n"
              "系统不会硬着头皮出个假结果 —— 拿不到正文就明确报阻塞。"),
        "chips": ["支持什么格式？", "怎么看受阻原因？"],
        "actions": [{"label": "带我走一遍", "kind": "tour", "target": "/"}],
    },
    {
        "id": "how-audit",
        "tags": ["审计", "留痕", "日志", "记录", "追溯", "导出审计"],
        "q": "怎么看审计记录？",
        "a": ("两个地方：\n\n"
              "* **孪生看板的「实时审查日志」** —— 直接读审计表，按秒级时间戳滚动，新条目会高亮\n"
              "* **大盘「运行指标」** —— 含请求量、错误率、慢请求、热点接口\n\n"
              "审计覆盖「拉取 → 下载 → 指纹 → 解析 → 抽条款 → 规则命中 → 摘要 → 回写」全过程，"
              "可一键导出全量 JSON（`/api/export`）。"),
        "chips": ["怎么看实时状态？", "越权会被记录吗？"],
        "actions": [{"label": "去孪生看板看日志", "kind": "goto", "target": "/twin"}],
    },
    {
        "id": "how-queue",
        "tags": ["队列", "并发", "配额", "排队", "卡住", "上限"],
        "q": "审查队列和配额是怎么控制的？",
        "a": ("所有触发审查的入口（手动重审、审批拉取、本地上传、阻塞重试）**统一走队列**，"
              "并发度由服务端钉死。四道配额：\n\n"
              "| 配额 | 说明 |\n| --- | --- |\n"
              "| 单文件大小 | 超限直接拒 |\n| 队列长度 | 满则拒并写审计 |\n"
              "| 待处理堆积 | 防止积压 |\n| 附件存储总量 | 防止磁盘打满 |\n\n"
              "水位在大盘「审查队列与配额」面板实时可见。"),
        "chips": ["审查要多久？", "怎么看实时状态？"],
    },
    # ---------------- 权限类
    {
        "id": "my-role",
        "tags": ["我是谁", "我的权限", "我有什么权限", "我的角色", "能干什么", "权限"],
        "q": "我是谁？我能做什么？",
        "a": None,          # 由实时数据填充（见 live_answers）
        "chips": ["为什么有的按钮是灰的？", "怎么切换角色？"],
    },
    {
        "id": "why-disabled",
        "tags": ["为什么点不了", "按钮灰", "灰的", "点不了", "没权限", "看不到按钮", "不能用"],
        "q": "为什么有些按钮是灰的 / 看不到？",
        "a": None,
        "chips": ["我是谁？我能做什么？", "怎么切换角色？", "越权会怎样？"],
    },
    {
        "id": "switch-role",
        "tags": ["切换角色", "换身份", "换账号", "切身份", "演示切换"],
        "q": "怎么切换角色？",
        "a": ("顶栏右侧有**身份切换下拉**，选一下即刻生效；登录页也有「一键进入」。\n\n"
              "这是为演示准备的（按角色分段走流程），由 `auth.demo_switch` 控制，"
              "**生产化部署应关掉**。\n\n"
              "三个演示账号：\n"
              "| 账号 | 口令 | 角色 |\n| --- | --- | --- |\n"
              "| `legal` | `Legal@2026` | 法务审查人 |\n"
              "| `applicant` | `Apply@2026` | 业务经办人 |\n"
              "| `admin` | `Admin@2026` | 系统管理员 |"),
        "chips": ["我是谁？我能做什么？", "越权会怎样？"],
    },
    {
        "id": "forbidden",
        "tags": ["越权", "403", "权限受限", "被拦", "拒绝访问", "拦截"],
        "q": "越权访问会怎样？",
        "a": ("会被服务端拦住并留痕：\n\n"
              "* 未登录访问页面 → 302 跳登录页；访问接口 → 401\n"
              "* 越权 → **403 权限受限页**，页面上会说明「你是谁、缺哪个权限、这个权限归谁」\n"
              "* 每次拦截都写审计（`tool = auth.guard`），管理员可在大盘查到\n\n"
              "前端只负责按钮显隐，**真正的拦截 100% 在服务端** —— 手工构造请求照样被拦。"),
        "chips": ["我是谁？我能做什么？", "怎么看审计记录？"],
    },
    {
        "id": "login",
        "tags": ["登录", "怎么登录", "账号密码", "进不去", "登录失败", "忘了密码"],
        "q": "怎么登录？",
        "a": ("打开系统会先跳到**登录页**。用账号 + 口令登录，也可以用三个演示身份的「一键进入」。\n\n"
              "口令以 pbkdf2-sha256（12 万次迭代 + 每账号独立盐）存储，**明文不落库**；"
              "会话令牌在服务端，Cookie 是 `HttpOnly`，前端脚本读不到。"),
        "chips": ["怎么切换角色？", "支持哪些格式？"],
    },
    # ---------------- 安全与合规
    {
        "id": "notify-mask",
        "tags": ["通知", "钉钉", "机器人", "脱敏", "外发", "推送"],
        "q": "机器人通知会泄露合同信息吗？",
        "a": ("通知通道是**这个系统唯一把数据送出本机的出口**，所以做了两层脱敏：\n\n"
              "1. **字段级掩码** —— 相对方名称、合同金额、统一社会信用代码、申请人、"
              "合同标题、附件名一律掩码\n"
              "2. **整段兜底扫描** —— 对正文再做一次正则扫描（信用代码 / 金额 / 手机号 / 邮箱），"
              "保证漏调一处字段级掩码也不会明文外发\n\n"
              "另外通知正文**不携带合同正文片段**，`batch_summary` 与 `review_completed` "
              "两类事件默认关闭（信息量低、外发面大）。"),
        "chips": ["怎么看系统状态？", "这个系统有哪些安全设计？"],
    },
    {
        "id": "security",
        "tags": ["安全", "数据安全", "本地", "会不会上传", "隐私", "联网"],
        "q": "数据安全是怎么保证的？",
        "a": ("几个硬约束：\n\n"
              "* **全程可离线运行** —— 零第三方依赖、不联网也能跑完整链路\n"
              "* **唯一外发口是通知通道**，且强制脱敏（见「机器人通知」）\n"
              "* **大模型可选**，不配置就不外发任何内容\n"
              "* 口令哈希存储、会话服务端管理、Cookie `HttpOnly`\n"
              "* 越权拦截写审计\n"
              "* 上传文件名做路径穿越硬化"),
        "chips": ["机器人通知会泄露合同信息吗？", "越权会怎样？"],
    },
    {
        "id": "zero-dep",
        "tags": ["依赖", "装什么", "第三方", "pip", "环境", "部署", "安装"],
        "q": "需要装什么东西才能跑？",
        "a": ("**运行期零第三方依赖。** 只用 Python 标准库，配合系统自带的 OCR 引擎与浏览器。\n\n"
              "* DOCX / PDF 解析 —— 标准库自研\n"
              "* 扫描件 OCR —— Windows 内置 OCR / Linux `tesseract`\n"
              "* PDF 报告 —— 浏览器无头打印，无浏览器时走纯标准库直出\n"
              "* 数据库 —— 标准库 `sqlite3`\n\n"
              "装好 Python 就能跑，不需要联网装包。"),
        "chips": ["怎么部署？", "跑起来要什么版本？"],
    },
]

# 常见问题（登录页等场景直接展示，不依赖接口）
DEFAULT_CHIPS = ["审查流程分几步？", "风险等级是谁判定的？", "怎么开始审查合同？",
                 "我的权限是什么？", "机器人通知会泄露合同信息吗？", "怎么看实时状态？"]


# ==========================================================================
# 二、实时数据问答：能查到的就查，查不到明说
# ==========================================================================
def _fmt_money(v):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return "—"
    if v >= 10000:
        return "¥%s（%.1f 万）" % (format(v, ",.2f"), v / 10000)
    return "¥%s" % format(v, ",.2f")


def _risk_label(lv):
    return {"high": "高风险", "medium": "中风险", "low": "低风险"}.get(lv, "未评级")


def _live_overview(cfg, st, sess):
    s = st.stats()
    rl = st.rollup()
    bs, br, bw = s.get("by_status") or {}, s.get("by_risk") or {}, s.get("by_writeback") or {}
    cases = st.list_cases()
    hi = [c for c in cases if c["risk_level"] == "high"]
    amt = sum((c.get("amount") or 0) for c in hi)
    return ("当前共 **%d 个审查任务**：\n\n"
            "* 已完成 **%d** 个，解析受阻 **%d** 个，处理中 %d 个\n"
            "* 风险分布：高 **%d** / 中 **%d** / 低 %d\n"
            "* 已回写审批单 **%d** 个\n"
            "* 规则累计命中 **%d** 项，其中法务已采纳 %d 项\n"
            "* 高风险合同金额合计 **%s**\n\n"
            "数字来自数据库实时聚合，与大盘统计卡一致。"
            % (s.get("total", 0), bs.get("completed", 0),
               s.get("blocked", 0),
               len([c for c in cases
                    if c["review_status"] in ("pending", "parsing", "reviewing")]),
               br.get("high", 0), br.get("medium", 0), br.get("low", 0),
               bw.get("success", 0), rl["hits"], rl["adopted"], _fmt_money(amt)))


def _live_find_case(st, kw):
    """按关键词找合同（标题 / 相对方 / 单号）。返回 (case, hits) 或 (None, 候选)。"""
    if not kw:
        return None, []
    k = kw.lower()
    cases = st.list_cases()
    exact = [c for c in cases
             if kw in (c.get("title") or "") or kw in (c.get("counterparty") or "")
             or kw in (c.get("case_no") or "")]
    if exact:
        return exact[0], exact
    # 退化到逐字匹配（用户可能只记得几个字）
    chars = [ch for ch in re.sub(r"[\s的合同审查结果情况]", "", kw) if ch]
    if len(chars) >= 2:
        loose = [c for c in cases
                 if sum(1 for ch in chars if ch in (c.get("title") or "")) >= len(chars) * 0.6]
        if loose:
            return loose[0], loose
    return None, []


def _live_case_detail(cfg, st, case):
    rows = st.results(case["id"])
    if not rows:
        return ("《%s》（%s）当前**没有命中风险项**。\n\n"
                "审查状态：%s ｜ 综合风险：%s"
                % (case["title"], case.get("case_no") or "-",
                   case.get("review_status_label") or case.get("review_status"),
                   _risk_label(case.get("risk_level"))), case)
    lines = []
    for i, r in enumerate(rows[:6], 1):
        loc = ("第 %s 页" % r["page_no"]) if r.get("page_no") is not None else "全文"
        lines.append("%d. **%s**（%s · %s）—— %s"
                     % (i, r.get("rule_name"), _risk_label(r.get("risk_level")),
                        loc, (r.get("reason") or "")[:60]))
    more = ("\n\n还有 %d 项，去工作台看完整清单。" % (len(rows) - 6)) if len(rows) > 6 else ""
    return ("《%s》（%s）共命中 **%d 项风险**，综合风险 **%s**：\n\n%s%s"
            % (case["title"], case.get("case_no") or "-", len(rows),
               _risk_label(case.get("risk_level")), "\n".join(lines), more), case)


def _live_rules(cfg, q):
    """规则查询：命中规则名 / 规则编号 / 条款类型。"""
    try:
        types = review_mod.list_rule_files(cfg.root)
    except Exception:                                            # noqa: BLE001
        return None, None
    allr = []
    for t in types:
        rd = review_mod.load_rules(cfg.root, t)
        for r in (rd.get("rules") or []):
            r = dict(r or {})
            r["_type_label"] = rd.get("type_label") or t
            allr.append(r)
    if not allr:
        return None, None
    key = re.sub(r"[\s？?。，,的规则是什么怎么]", "", q)
    hit = [r for r in allr
           if (r.get("id") or "").lower() in q.lower()
           or (r.get("name") and r["name"] in q)
           or (key and len(key) >= 2 and key in (r.get("name") or ""))]
    if not hit:
        return None, None
    r = hit[0]
    return ("规则 **%s（%s）**\n\n"
            "* 适用类型：%s\n* 风险等级：%s\n* 条款类型：%s\n* 匹配方式：%s\n"
            "* 法律 / 合规依据：%s"
            % (r.get("name"), r.get("id"), r.get("_type_label"),
               _risk_label(r.get("risk_level")), r.get("clause_type") or "—",
               r.get("match_mode") or "—", (r.get("legal_basis") or "—")[:120])), r


def _live_system(cfg, st, sess):
    from . import output as output_mod
    from . import queue as queue_mod
    from . import pipeline as pipeline_mod
    from . import scheduler as sch_mod
    q = queue_mod.get_queue(cfg, st).stats()
    sch = sch_mod.get_scheduler(cfg, st).status()
    try:
        # get_notifier 在 pipeline 里（曾经误写成 notify_mod.get_notifier，
        # 异常被吞掉 → 助手把「已配置/已开启」说成「未配置/关闭」）
        nt = pipeline_mod.get_notifier(cfg, st).status()
    except Exception as exc:                                     # noqa: BLE001
        nt = {"_error": str(exc)}
    mi = llm_mod.mode_info(cfg)
    return ("系统当前状态：\n\n"
            "* **版本**：v%s\n"
            "* **大模型**：%s%s\n"
            "* **PDF 通道**：%s\n"
            "* **审查队列**：并发 %s，排队 %s / %s，待处理 %s / %s\n"
            "* **定时拉取**：%s\n"
            "* **机器人通知**：%s（外发脱敏 %s）"
            % (__import__("app", fromlist=["x"]).__version__,
               mi["mode_label"],
               ("" if mi["available"] else "（%s）" % mi["reason"]),
               "浏览器无头打印" if output_mod.find_browser() else "纯标准库直出",
               q.get("workers"), q.get("queued"), q.get("maxsize"),
               q.get("pending_cases"), q.get("max_pending"),
               "运行中，间隔 %ss" % sch.get("current_interval") if sch.get("enabled") else "已关闭",
               ("读取失败（%s）" % nt["_error"]) if nt.get("_error")
               else ("已配置" if nt.get("configured") else "未配置"),
               "未知" if nt.get("_error")
               else ("已开启" if (nt.get("mask") or {}).get("party") else "关闭")))


def _live_my_role(sess):
    from . import auth as auth_mod
    role = (sess or {}).get("role") or ""
    if not role:
        return "当前未登录，所以什么功能都看不到 —— 登录后才有角色与权限。"
    caps = auth_mod.caps_of(role)
    rows = ["* **%s** —— %s" % (auth_mod.CAP_LABEL.get(c, c), c)
            for c in sorted(caps)]
    return ("你是 **%s（%s）**，当前有 %d 项权限：\n\n%s\n\n"
            "前端只是显隐按钮，**真正的拦截在服务端**：没权限的操作手工发请求也会被 403 拦住并写审计。"
            % (auth_mod.ROLE_LABEL.get(role, role), (sess or {}).get("username") or "-",
               len(caps), "\n".join(rows)))


def _live_why_disabled(sess):
    from . import auth as auth_mod
    role = (sess or {}).get("role") or ""
    if not role:
        return "你当前未登录，所有需要身份的功能都不可用。"
    owner = {}
    for cap, roles in auth_mod.CAPS.items():
        for r in roles:
            owner.setdefault(cap, []).append(auth_mod.ROLE_LABEL.get(r, r))
    mine = set(auth_mod.caps_of(role))
    lines = []
    for cap in ("writeback", "edit_rules", "retry_blocked", "run_selftest", "view_metrics"):
        if cap not in mine:
            lines.append("* **%s** —— 归 %s"
                         % (auth_mod.CAP_LABEL.get(cap, cap),
                            "、".join(owner.get(cap, []))))
    extra = ("\n\n".join(lines)) if lines else "* 你当前没有缺失的常见权限。"
    return ("按钮变灰或隐藏，是因为你现在的角色（%s）没有对应权限。以下几项你暂时用不了：\n\n%s\n\n"
            "需要的话可以用顶栏的身份切换（演示用）切到对应角色再操作。"
            % (auth_mod.ROLE_LABEL.get(role, role), extra))


LIVE_INTENTS = [
    (["多少个任务", "有多少", "任务总数", "总体情况", "概况", "统计", "多少份合同", "大盘",
      "高风险的有多", "金额合计", "多少风险", "命中", "多少项", "采纳", "风险项",
      "多少条", "当前情况"], "live_overview", _live_overview),
    (["系统状态", "运行状态", "现在怎么样", "队列水位", "在跑吗", "服务状态", "版本"],
     "live_system", _live_system),
    (["我是谁", "我的权限", "我有什么权限", "我的角色"], "live_role",
     lambda cfg, st, sess: _live_my_role(sess)),
    (["为什么点不了", "按钮灰", "灰的", "点不了", "没权限", "看不到按钮"],
     "live_disabled", lambda cfg, st, sess: _live_why_disabled(sess)),
]


def _live_with_kb(cfg, st, q, sess):
    """答题主流程：先实时数据 → 再知识库 → 都没有则明说。"""
    for kws, _kid, fn in LIVE_INTENTS:
        if any(k.lower() in q.lower() for k in kws):
            try:
                return {"answer": fn(cfg, st, sess), "source": "live",
                        "refs": ["实时数据（数据库聚合）"]}
            except Exception as exc:                             # noqa: BLE001
                return {"answer": "查询实时数据时出错：%s" % exc, "source": "live",
                        "refs": []}

    # 合同个案：出现「XX合同」「XX公司」这类实体时，尝试定位真实案件
    m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,20}?)(合同|协议)", q)
    if m:
        case, _hits = _live_find_case(st, m.group(0))
        if case:
            ans, case = _live_case_detail(cfg, st, case)
            return {"answer": ans, "source": "live",
                    "refs": ["案件 #%s 的审查结果表" % case["id"]],
                    "actions": [{"label": "打开这份合同的工作台", "kind": "goto",
                                 "target": "/workbench?id=%s" % case["id"]}]}

    # 规则查询
    if any(k in q for k in ("规则", "条款要求", "PU-", "SA-", "LA-", "SE-")):
        ans, r = _live_rules(cfg, q)
        if ans:
            return {"answer": ans, "source": "live",
                    "refs": ["config/rules/%s.json" % r["_type_label"]],
                    "actions": [{"label": "去规则库看看", "kind": "goto", "target": "/rules"}]}

    return None


# ==========================================================================
# 三、知识库匹配（关键词打分）
# ==========================================================================
def match_kb(q):
    """按命中标签数与标签长度打分；返回 (entry, score)。"""
    if not q:
        return None, 0
    best, best_score = None, 0
    for e in KB:
        score = 0
        for t in e["tags"]:
            if t.lower() in q.lower():
                # 更长的标签说明更具体，给更高权重
                score += 2 + min(4, len(t) // 2)
        if score > best_score:
            best, best_score = e, score
    return best, best_score


def answer(cfg, st, question, page="/", sess=None, use_llm=True):
    """主入口：返回 {answer, source, refs, actions, chips, matched, used_llm}"""
    q = (question or "").strip()
    if not q:
        return {"answer": "想问点什么？下面这些是我最常被问到的。",
                "source": "kb", "refs": [], "actions": [],
                "chips": DEFAULT_CHIPS, "matched": False, "used_llm": False}

    if len(q) > 300:
        q = q[:300]

    live = _live_with_kb(cfg, st, q, sess)
    if live:
        return {"answer": live["answer"], "source": live["source"],
                "refs": live.get("refs") or [], "actions": live.get("actions") or [],
                "chips": DEFAULT_CHIPS, "matched": True, "used_llm": False}

    entry, score = match_kb(q)
    if entry and score >= 3:
        a = entry["a"]
        if a is None:
            # 这类条目的答案依赖实时数据（角色/权限），走到这里说明没匹配上实时意图
            a = _live_my_role(sess)
        return {"answer": a, "source": "kb", "refs": ["内置知识库：%s" % entry["id"]],
                "actions": entry.get("actions") or [],
                "chips": entry.get("chips") or DEFAULT_CHIPS,
                "matched": True, "used_llm": False}

    # 没匹配到：绝不编造，明确说明并给出可答方向
    tips = ("没匹配到我能确定回答的问题，所以我不猜。\n\n"
            "我能准确回答这几类：\n"
            "* **流程与原理** —— 七段链路、幂等、锚点定位、风险判定口径\n"
            "* **操作指引** —— 怎么拉取 / 上传 / 采纳建议 / 回写 / 导出报告 / 处理受阻\n"
            "* **权限** —— 我是谁、能做什么、为什么某个按钮用不了\n"
            "* **实时数据** —— 现在有多少任务、某份合同有哪些风险、某条规则是什么\n"
            "* **系统状态** —— 版本、队列水位、PDF 通道、通知与脱敏\n\n"
            "也可以直接点下面的问题，或者让我**带你走一遍**。")
    used_llm = False
    if use_llm and llm_mod.available(cfg):
        sys_p = ("你是衡鉴 V2.1.7的使用助手。**只允许改写下面这段已确定的回答的措辞**，"
                 "不得新增系统不具备的功能、不得编造数据或规则。保持 Markdown 要点结构，150 字以内。")
        text, _err = llm_mod.chat(cfg, sys_p, tips)
        if text:
            tips, used_llm = text, True
    return {"answer": tips, "source": "kb-miss", "refs": [],
            "actions": [{"label": "带我走一遍新手引导", "kind": "tour", "target": page}],
            "chips": DEFAULT_CHIPS, "matched": False, "used_llm": used_llm}


# ==========================================================================
# 四、新手引导步骤（按页面 + 按角色能力过滤）
#   每条 step 的 target 必须是页面上**真实存在**的选择器；自检会逐条断言。
# ==========================================================================
TOURS = {
    "/": {
        "title": "从零开始：把合同审出来",
        "steps": [
            {"id": "dash-kpi", "target": "#stats", "advance": "next",
             "title": "先看总体情况",
             "desc": "这里是审查大盘的统计卡：任务总数、高/中风险、审查完成、解析受阻、"
                     "回写成功与高风险金额。**每张卡都可以点开看明细清单。**"},
            {"id": "dash-pull", "target": "#btnPull", "cap": "pull_todo",
             "title": "第 1 步：拉取待办并审查",
             "desc": "点这个按钮，系统会把审批平台里的待审合同拉进来，自动走完"
                     "「解析 → 抽条款 → 规则审查」并给出风险结论。\n\n"
                     "需要**业务经办人**或系统管理员权限。", "advance": "click"},
            {"id": "dash-list", "target": "#tbl", "advance": "next",
             "title": "第 2 步：看审查结果",
             "desc": "每个任务都会带上综合风险等级与回写状态。"
                     "**点任意一行的「审查」就能进入该合同的工作台。**"},
            {"id": "dash-filter", "target": "#fRisk", "advance": "next",
             "title": "第 3 步：按条件筛",
             "desc": "风险等级、审查状态、合同类型都能筛，"
                     "也可以直接在上面搜索框搜合同名称或相对方。"},
            {"id": "dash-upload", "target": "button[onclick=\"openUpload()\"]",
             "cap": "submit_contract", "advance": "next",
             "title": "第 4 步：也可以自己上传",
             "desc": "除了从审批平台拉，也能直接上传 DOCX / PDF / 扫描件。"
                     "上传后会进入审查队列，队列水位在下面能看到。"},
            {"id": "dash-flow", "target": "#swSched", "cap": "manage_scheduler",
             "advance": "next",
             "title": "第 5 步：让它自己跑（可选）",
             "desc": "打开这个开关，系统会**按间隔自动拉取并审查**，不用人手点。"
                     "连续失败会指数退避，成功一次就复位。默认关闭，避免演示数据自己变。"},
            {"id": "dash-drill", "target": "#stats .stat.clickable", "advance": "click",
             "dynamic": True,
             "title": "第 6 步：点统计卡看明细",
             "desc": "随便点一张卡试试 —— 会弹出该口径的**真实明细清单**"
                     "（含金额合计、涉及相对方数、占全部任务比例）。"},
        ],
    },
    "/workbench": {
        "title": "工作台：核对风险并写回审批单",
        "steps": [
            {"id": "wb-doc", "target": "#doc", "advance": "next",
             "title": "左栏：合同正文",
             "desc": "按页分页，每段前面标了段落序号和识别出的条款类型；"
                     "橙色和红色是命中风险的原文，颜色对应风险等级。"},
            {"id": "wb-card", "target": "#tabBody .risk-card", "advance": "click",
             "dynamic": True,
             "title": "点风险卡片 → 跳到原文",
             "desc": "点一张风险卡片，左边正文会自动跳到**对应页码和段落**并高亮。\n"
                     "反过来点正文里的高亮，右边的卡片也会被选中 —— 这就是**双向锚定**。"},
            {"id": "wb-edit", "target": "#tabBody textarea.sugg", "advance": "next",
             "dynamic": True,
             "title": "改示范条款",
             "desc": "每条风险都带**可直接落地的示范条款**，这个框是可以编辑的。"
                     "有备选方案时还能一键替换。"},
            {"id": "wb-adopt", "target": "#tabBody button[data-cap=\"edit_suggestion\"]",
             "cap": "edit_suggestion", "advance": "click", "dynamic": True,
             "title": "点「采纳建议」",
             "desc": "确认条款没问题就点采纳，会写进库；右侧「建议采纳进度」会跟着走。\n\n"
                     "需要**法务审查人**权限。"},
            {"id": "wb-opinion", "target": "#opinion", "cap": "add_opinion", "advance": "next",
             "title": "写一句法务批注",
             "desc": "这里写的内容会**随审查意见一起写进审批单评论区**。"
                     "比如「第二、四条须按示范条款修改后方可用印」。",
             "cap_hint": "法务审查人"},
            {"id": "wb-mode", "target": "#summaryBox", "advance": "next",
             "title": "看模式对照",
             "desc": "右边这块是「审查表达模式对照」：左边规则引擎模式、右边规则+模型润色模式。\n\n"
                     "注意两边**风险结论完全一致** —— 因为风险判定 100% 由确定性规则产出，"
                     "模型只改写措辞。"},
            {"id": "wb-writeback", "target": "#btnWb", "cap": "writeback", "advance": "click",
             "title": "最后一步：写回审批单",
             "desc": "点「写回审批意见」，审查意见就进审批单评论区了。\n\n"
                     "系统**只写评论**，没有通过 / 拒绝 / 转交的能力，审批权始终在人手里；"
                     "重复点会被幂等跳过。需要**法务审查人**权限。"},
        ],
    },
    "/rules": {
        "title": "规则库：合规要求怎么配",
        "steps": [
            {"id": "rules-type", "target": "#curTypeLabel", "advance": "next",
             "cap": "edit_rules",
             "title": "按合同类型分开维护",
             "desc": "采购 / 销售 / 服务 / 劳动四类各一套规则库。切换类型后下面的清单会跟着变。"},
            {"id": "rules-row", "target": "#ruleBody .rule-row", "advance": "next",
             "dynamic": True, "cap": "edit_rules",
             "title": "每条规则有什么",
             "desc": "风险等级、条款类型、匹配方式、触发条件、法律依据。"
                     "匹配方式支持五种：关键词、正则、**必备条款缺失**、"
                     "**权责不对称配对**、数值阈值。"},
            {"id": "rules-edit", "target": "#ruleBody .rule-row button", "advance": "next",
             "dynamic": True, "cap": "edit_rules",
             "title": "在线编辑并保存",
             "desc": "点编辑可以改规则，保存后写回配置文件，**自动留 `.bak` 备份**。\n\n"
                     "写坏的正则不会静默失效，会被跳过并记入诊断。"},
        ],
    },
    "/twin": {
        "title": "孪生看板：实时运行镜像",
        "steps": [
            {"id": "twin-src", "target": "#srcNote", "advance": "next",
             "title": "先说清数据从哪来",
             "desc": "这页**不构造任何演示数据**：任务与金额取自数据库、解析产出取自解析快照、"
                     "命中与采纳取自审查结果表、实时日志直接读审计表。每个数字都能在库里对上。"},
            {"id": "twin-pipe", "target": "#pipe", "advance": "next",
             "title": "七段流水线的真实计数",
             "desc": "从审批接入到报告产出，每段的数值下面都标注了**数据来源表或目录**，方便复核。"},
            {"id": "twin-log", "target": "#logStream", "advance": "next",
             "title": "实时审查日志",
             "desc": "直接读审计表，每 4 秒回读一次，**新条目会整行高亮**。"
                     "覆盖「拉取 → 下载 → 指纹 → 解析 → 抽条款 → 规则命中 → 摘要 → 回写」全过程。"},
            {"id": "twin-gauge", "target": "#gauges", "advance": "next",
             "title": "引擎负载与资源水位",
             "desc": "队列水位、待处理堆积、附件存储、OCR 平均置信度 —— 都是真实读数与真实阈值。"},
        ],
    },
    "/mock": {
        "title": "模拟审批系统：外部平台长什么样",
        "steps": [
            {"id": "mock-list", "target": "#list", "advance": "next",
             "title": "左侧：待审合同单",
             "desc": "这是**模拟审批平台侧**的待办列表。真实场景下合同审批在客户既有平台"
                     "（如飞书审批）里进行，本系统通过 HTTP 拉取待办。"},
            {"id": "mock-detail", "target": "#detail", "advance": "next",
             "title": "右侧：审批单详情与评论区",
             "desc": "重点看**评论区** —— 本系统写回的审查意见就落在这里。"
                     "选中一张有评论的单就能看到。"},
            {"id": "mock-raw", "target": "#btnRaw", "advance": "click",
             "title": "看原始 JSON",
             "desc": "点这里能看到模拟平台**真实的接口返回**（不是前端拼的）。"
                     "这些接口仍以原始 JSON 对外暴露，可被外部直接调用。"},
        ],
    },
    "/login": {
        "title": "先登录进来",
        "steps": [
            {"id": "login-form", "target": "#loginForm", "advance": "next",
             "title": "用账号口令登录",
             "desc": "输入账号与口令登录。也可以直接用右侧三个演示身份的「一键进入」，"
                     "按角色分段演示时很方便。"},
            {"id": "login-roles", "target": "#roleList", "advance": "next",
             "title": "三个演示身份",
             "desc": "法务审查人（可回写）、业务经办人（可送审）、系统管理员（可改规则）。\n\n"
                     "口令哈希存储、Cookie 是 HttpOnly，前端脚本读不到。"},
        ],
    },
}


def tour(page="/", sess=None):
    """返回该页面的引导步骤（按角色能力过滤 + 标注哪些步骤需要切角色）。"""
    from . import auth as auth_mod
    t = TOURS.get(page)
    if not t:
        return {"page": page, "title": "新手引导", "steps": [], "available": False,
                "pages": list(TOURS.keys())}
    role = (sess or {}).get("role") or ""
    caps = auth_mod.caps_of(role) if role else set()
    steps = []
    for i, s in enumerate(t["steps"], 1):
        need = s.get("cap")
        ok = (not need) or (need in caps)
        e = dict(s)
        e["index"] = i
        e["allowed"] = ok
        e["need_cap"] = need or ""
        e["need_label"] = auth_mod.CAP_LABEL.get(need, need) if need else ""
        e["need_roles"] = [auth_mod.ROLE_LABEL.get(r, r)
                           for r in auth_mod.CAPS.get(need, set())] if need else []
        steps.append(e)
    return {
        "page": page, "title": t["title"], "steps": steps, "available": True,
        "role": role, "role_label": auth_mod.ROLE_LABEL.get(role, role),
        "pages": list(TOURS.keys()),
        "note": ("标了「需切换角色」的步骤，当前角色做不了 —— 引导会提示并可一键切换。"
                 "每一步都指向页面上真实存在的元素。"),
    }


def chips():
    return DEFAULT_CHIPS
