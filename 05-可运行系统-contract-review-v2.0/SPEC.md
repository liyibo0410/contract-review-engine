# 衡鉴 V2.1.7 · 技术规格说明书（SPEC）

| 项目 | 内容 |
| --- | --- |
| 版本 | v1.0 |
| 运行环境 | Windows 10/11，Python 3.10+（开发验证 3.13.12） |
| 第三方依赖 | **无**（运行期仅标准库 + 系统自带组件） |
| 存储 | SQLite（`data/review.db`）+ 文件系统（附件 / 解析缓存 / 报告） |
| 关联文档 | `PRD.md`（产品需求）、`README.md`（运行说明）、`docs/验收对照表.md` |

---

## 1. 分层架构

对齐项目架构图（`docs/reference/合同审批助手项目.drawio`）的八层职责划分：

| 层 | 职责 | 实现 |
| --- | --- | --- |
| 审批平台接入层 | 读取待办、审批详情、合同附件，屏蔽不同审批系统接口差异 | `app/writeback.py`（HTTP 调用，`approval.base_url` 可切换）；`mock/feishu.py` 提供同形态的模拟实现 |
| 任务编排层 | 定时拉取待办、建任务、分配审查与回写任务、按审批单做幂等控制 | `app/pipeline.py`；`GET /api/pull` 触发 |
| 工具服务层 | 受控的待办查询、详情读取、附件下载、合同解析、风险审查、结果保存、评论回写 | `app/pipeline.py` + `app/review.py` + `app/writeback.py` |
| 文档解析层 | 处理 DOCX / PDF / 图片 / 扫描件，输出正文、页码、段落位置、表格内容、OCR 置信度 | `app/parsers.py` + `app/ocr_win.py` |
| 风险审查层 | 按可配置规则识别条款缺失、高风险关键词与异常表述 | `app/review.py` + `config/rules/*.json` |
| 结果回写层 | 把风险摘要、命中规则与证据片段组织成中文审查意见，只写入审批评论区 | `app/output.py` + `app/writeback.py` |
| 审计数据层 | 保存审批单、合同文档、审查结果与工具调用日志 | `app/store.py`（4 张主表 + 1 张回写日志表） |
| 运维观测层 | 自检、运行报告、审计导出、处理看板 | `selftest.py`、`GET /api/export`、`GET /api/stats`、大盘页面 |

---

## 2. 运行时与依赖边界

* **运行期零依赖**：`http.server` / `sqlite3` / `zipfile` / `xml.etree` / `zlib` / `base64` /
  `urllib` / `subprocess` 全部来自标准库。
* **系统组件依赖**（非 Python 包）：
  * 扫描件 OCR：Windows 用系统内置 `Windows.Media.Ocr`（需 `zh-Hans-CN` 识别器）；
    Linux/macOS 用系统 `tesseract` 命令行（需 `chi_sim` 中文语言包）→ 扫描件解析；
  * Edge / Chrome（`--headless --print-to-pdf`，Windows 与 Linux 均可）→ 服务端 PDF 报告生成；
  * 两者缺失时均**自动降级**：OCR 缺失 → 任务 `blocked` 并提示；浏览器缺失 → 返回可打印 HTML。
* **构建期依赖**（仅生成示例合同时使用）：`python-docx`、`reportlab`、`pillow`、`pypdf`。
  示例文件已随交付物提供，正常运行不需要安装这些包，也不涉及虚拟环境。

---

## 3. 模块设计

| 模块 | 关键职责 | 关键实现点 |
| --- | --- | --- |
| `app/config.py` | 配置加载、深合并默认值、路径解析 | 相对路径统一基于项目根解析为绝对路径并自动建目录 |
| `app/store.py` | 5 张表的读写、状态枚举、审计写入、统计 | 单连接 + `RLock`；`RISK_WEIGHT` 用于取最高风险 |
| `app/parsers.py` | 格式探测与统一段落树输出 | 文件头优先于扩展名；段落树带 `page` / `char_start` / `char_end` |
| `app/ocr_win.py` | 扫描件 OCR（跨平台双后端） | 按平台自动选择：Windows 走 PowerShell 调用 WinRT OCR；Linux 走 tesseract CLI。图片拷贝到 ASCII 临时目录规避中文路径编码问题；结果做 CJK 间隔空格规整；置信度为可解释启发式 |
| `app/review.py` | 条款切分、元数据抽取、规则引擎、条款推荐、结论判定 | 见第 5、6 节 |
| `app/llm.py` | 可选的中文摘要润色 | 显式禁用代理的 urllib；失败自动回退规则模板，绝不阻断主流程 |
| `app/notify.py` | 关键事件推送（钉钉机器人 / 通用 Webhook） | 后台线程异步投递、HMAC-SHA256 加签、按事件类型防抖、失败只记状态不影响主流程；凭据支持环境变量优先（`notify.webhook_env` / `secret_env`），Webhook 展示时脱敏 |
| `app/output.py` | 审查意见撰写、Markdown/HTML/PDF 报告 | 打印优化 CSS；`@page A4`；风险等级配色 |
| `app/writeback.py` | 回写与幂等 | 只写评论；预览模式不落审批侧；失败落库可重试 |
| `app/pipeline.py` | 流水线编排与详情聚合 | 幂等键 = 审批单号 + 文件 SHA256 |
| `app/server.py` | HTTP 路由、静态资源、mock 转发 | `ThreadingHTTPServer`，HTTP/1.1 + 显式 `Content-Length`；统一 `{code,data,msg}` 响应 |
| `mock/feishu.py` | 模拟审批系统 | 待办 / 详情 / 评论区 / 故障注入 / 重置；既是 HTTP 接口也是可同进程调用 |

---

## 4. 数据模型

```sql
contract_cases      -- 审查任务（1 个审批单 = 1 条）
  id, case_no, title, applicant, send_dept, business_type,
  counterparty, credit_code, amount, currency, perform_period, effective_condition,
  source(approval|local), approval_instance, document_id, doc_sha256,
  review_status, writeback_status, risk_level, conclusion, blocked_reason,
  lawyer_opinion, created_at, updated_at
  UNIQUE(case_no)；索引：doc_sha256 / approval_instance（幂等去重）

contract_documents  -- 合同文档与解析结果元信息
  id, case_id, filename, doc_type(docx|pdf|image), parse_engine, parse_status,
  parse_error, page_count, para_count, char_count, ocr_confidence, need_manual,
  sha256, file_path, text_path, created_at

review_results      -- 风险命中项（证据链）
  id, case_id, rule_id, rule_name, risk_level, clause_type, match_mode,
  evidence, page_no, para_index, char_start, char_end,
  reason, legal_basis, suggestion, suggestion_final, adopted, lawyer_note, created_at

writeback_logs      -- 回写记录
  id, case_id, mode(dry_run|live), channel, status, payload, comment_id, error, created_at

audit_logs          -- 全链路审计
  id, case_id, step, tool, status, detail, created_at
```

**段落树（解析产物，落盘在 `data/parsed/case_{id}.json`）**

```json
{ "index": 12, "text": "…原文…", "page": 2, "is_heading": false,
  "char_start": 486, "char_end": 612, "clause_code": "payment", "clause_label": "付款进度" }
```

`char_start/char_end` 是该段落在全文扁平文本中的区间；风险命中的绝对坐标
= `段落.char_start + 段内匹配偏移`，因此前端可以直接在段落文本里切片渲染高亮。

---

## 5. 解析引擎实现细节

### 5.1 格式探测

按文件头优先：`%PDF` → PDF；`PK\x03\x04` 且含 `word/document.xml` → DOCX；
`\x89PNG` / `\xFF\xD8` / `BM` → 图片；`\xD0\xCF\x11\xE0` → 旧版 `.doc`（提示另存为 .docx）；
其余按扩展名兜底，未知格式直接返回 `blocked` 原因。

### 5.2 DOCX

标准库 `zipfile` 读取 `word/document.xml`，用 `xml.etree` 按 `w` 命名空间遍历 `body`：
`w:p` 收敛 `w:t` / `w:tab` / `w:br`（`type=page` 时页码 +1）/ `w:lastRenderedPageBreak`；
`w:tbl` 按行输出单元格文本并登记为表格；
标题判定依据 `w:pStyle`（Heading / 标题）或文本形态（`第X条` / `一、` / `1.`）。

### 5.3 PDF（自实现抽取器）

1. **对象扫描**：正则定位所有 `N G obj`，取到下一个 `endobj`；不依赖 xref，兼容交叉引用表与流两种写法。
2. **页面顺序**：从 `/Catalog → /Pages → /Kids` 递归遍历；失败时退化为按对象号排序。
3. **流解码**：解析 `/Filter` 链并**按顺序**依次应用
   `ASCII85Decode`（去掉尾部 `~>`）/ `ASCIIHexDecode` / `FlateDecode`（尝试 wbits 15/-15/47 与 `\x78` 偏移兜底）；
   图像类滤镜（DCT/JPX/CCITT/JBIG2）不尝试解码，直接判定无文本层。
4. **字体与编码映射**：解析 `/Resources → /Font`（资源名允许 `+ . - _` 等子集前缀字符），
   对每个字体读取 `/ToUnicode` CMap 并解析 `begincodespacerange` / `beginbfchar` / `beginbfrange`，
   得到 `code → Unicode` 映射与代码字节宽度（`/Type0` 为 2 字节，否则 1 字节）。
5. **文本抽取**：在内容流中识别 `/Fx Tf` 切换当前字体、`(…)Tj` / `<…>Tj` / `[...]TJ` / `'` / `"` 输出文本、
   `T*` / `TD` / `Td` 作为换行；用当前字体的 CMap 映射代码为文本。
6. **降级**：抽取字符数 < 30 视为无有效文本层 → `ok=False`，原因写明「可能为扫描件或图片型 PDF」，
   任务转入 `blocked` 并提示上传扫描件图片（走 OCR 通道）。

### 5.4 文字匹配的鲁棒性

* 每段落匹配时先按原文匹配；失败则在**去间隔空格文本**上重试（PDF 抽取常出现「逐字空格」），
  并通过 `idx_map` 把命中位置无损映射回原文坐标，保证高亮锚点始终对应原文。
* 该机制同样覆盖 Windows OCR 输出（`app/ocr_win.py` 会先把 CJK 字符间的空格规整掉）。

---

## 6. 规则模型

一条规则 = 编号 + 名称 + 风险等级 + 条款类型 + 匹配模式 + 触发条件 + 成因 + 依据 + 默认建议。

### 6.1 五种匹配模式

| 模式 | 语义 | 触发条件 |
| --- | --- | --- |
| `keyword` | 关键词包含 | 命中任一关键词子串 |
| `regex` | 正则匹配 | 命中任一条正则（在原文或去空格文本上） |
| `absence` | **必备条款缺失** | 所有「应当出现的正向表达式」在**全文范围内**均不出现（因此不会因为条款标题段落而误报） |
| `pair` | **权责不对称配对** | `patterns`（对我方不利）与 `patterns_alt`（对方免责/封顶）**同时**命中，证据合并展示两侧原文 |
| `numeric` | **数值阈值** | 正则捕获数值并与 `numeric_threshold` 按 `numeric_op`（gt/gte/lt/lte/eq）比较 |

### 6.2 证据与定位

每条命中都会回带：

* `evidence`：命中所在的**完整句子**（按 `。；：\n` 切分边界），`pair` 模式拼接两侧证据；
* `page_no` / `para_index` / `char_start` / `char_end`：可定位坐标；
* `absence` 模式无定位锚点，返回 `null` 并在界面标注「全文未检出（无定位锚点）」。

### 6.3 阈值与结论（`config/app.json`）

```json
"review": { "reject_high_count": 2, "rectify_total": 1,
            "block_on_parse_fail": true, "min_text_chars": 30,
            "ocr_min_confidence": 0.5, "max_upload_mb": 30,
            "max_paragraphs_expose": 2000 }
```

**这些阈值全部在代码中真实生效**（不做「配置写了但没人读」的摆设）：`min_text_chars` /
`ocr_min_confidence` 由 `pipeline` 传给解析层并参与「是否有有效文本」的判定；
`block_on_parse_fail=false` 时解析质量不足也继续降级审查；`max_upload_mb` 由服务端强制；
`max_paragraphs_expose` 限制详情接口返回的段落数。

配置在加载时按 `_SCHEMA` 逐项校验类型与取值范围，非法值回退默认并在 `/api/health` 的
`config_warnings` 中暴露，避免带着坏配置启动。

### 6.4 现行规则库规模

| 合同类型 | 立场 | 规则数 | 高风险 | 中风险 | 低风险 |
| --- | --- | --- | --- | --- | --- |
| purchase 采购合同 | 本方为需方 | 12 | 5 | 5 | 2 |
| sales 销售合同 | 本方为供方 | 11 | 5 | 4 | 2 |
| service 服务合同 | 本方为委托方 | 9 | 4 | 4 | 1 |
| labor 劳动合同 | 本方为用人单位 | 9 | 4 | 4 | 1 |

规则可在线维护并写回配置文件（原文件自动备份 `.bak`），也可直接编辑 `config/rules/*.json`。

---

## 7. 接口清单

统一响应：`{"code":0,"data":{…},"msg":""}`，`code != 0` 时 `msg` 为可读错误。

### 7.1 审查系统接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 版本、路径、审批对接、PDF 通道、规则类型、示例清单 |
| GET | `/api/stats` | 大盘统计（状态/风险/回写分布、受阻数、高风险金额） |
| GET | `/api/cases` | 任务列表，支持 `kw` / `risk` / `status` / `type` 筛选 |
| POST | `/api/pull` | 拉取审批待办建任务，`{"auto_review":true}` 一并执行审查；返回新建与跳过明细 |
| POST | `/api/upload` | 本地上传，`{filename, content(base64), meta, auto_review}`；按 SHA256 去重 |
| GET | `/api/samples` | 示例合同集清单与预期结果 |
| GET | `/api/cases/{id}` | 工作台详情（合同、文档、风险清单、摘要、条款、段落树、元数据、审计、回写记录、审批评论） |
| POST | `/api/cases/{id}/review` | 重新执行审查流水线 |
| POST | `/api/cases/{id}/retry` | 阻塞任务重试（回到 `pending` 重新解析） |
| POST | `/api/cases/{id}/opinion` | 保存法务批注 |
| POST | `/api/cases/{id}/writeback` | 回写审查意见，`{"mode":"dry_run"｜"live"}`；返回评论标识与回写正文 |
| GET | `/api/cases/{id}/report` | 报告导出，`format=md｜html｜pdf`，`download=1` 附件下载，`print=1` 自动唤起打印 |
| PATCH | `/api/results/{id}` | 法务复核：`suggestion_final` / `adopted` / `lawyer_note` |
| GET | `/api/rules?type=` | 规则库、条款类型词典、示范条款库与统计 |
| POST | `/api/rules/{type}` | 保存规则库（写回配置文件并备份 `.bak`） |
| GET | `/api/audit` | 审计日志，支持 `case_id` / `limit` |
| GET | `/api/export` | 全量导出 JSON（案件/文档/结果/审计/回写） |
| GET | `/api/selftest` | 执行验收自检并返回逐条结果 |
| GET | `/api/config` | 前端可见配置（上传上限、回写模式、通知开关等，**不含密钥**） |
| GET | `/api/metrics` | 运行指标：请求数、错误率、平均/峰值耗时、热点接口、慢请求、响应总量 |
| GET | `/api/notify/status` | 通知配置状态、发送统计与最近发送记录 |
| GET | `/api/notify/recent` | 最近发送记录（`limit` 可调） |
| POST | `/api/notify/test` | 同步发送一条测试通知，直接返回钉钉 `errcode`；未配置时返回 400 并给出配置指引 |
| POST | `/api/mock/fault` | 注入回写接口故障率（演示异常与重试） |
| POST | `/api/mock/reset` | 重置模拟审批数据，`full=1` 同时清空审查数据库 |

### 7.2 模拟审批系统接口（真实平台形态）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/mock/feishu/api/todo` | 待办审批列表 |
| GET | `/mock/feishu/api/instances` | 全部审批实例 |
| GET | `/mock/feishu/api/instances/{id}` | 审批实例详情（含附件、申请人、金额） |
| GET | `/mock/feishu/api/instances/{id}/comments` | 评论区 |
| POST | `/mock/feishu/api/comments` | **写评论（回写目标接口）** |
| POST | `/mock/feishu/api/_config` | 设置故障注入率 |
| POST | `/mock/feishu/api/_reset` | 重置模拟数据 |

---

## 8. 报告生成通道

```
报告数据 → build_report_md / build_report_html
                        │
                        ├─ format=md   → 直接下载 .md
                        ├─ format=html → 打印优化页面（在线预览）
                        └─ format=pdf  → 落 HTML → Edge/Chrome --headless --print-to-pdf
                                            ├─ 成功 → application/pdf 附件下载
                                            └─ 失败 → 返回 HTML 并自动唤起打印（另存为 PDF）
```

PDF 页面使用 A4（`@page size:A4; margin:14mm 10mm`）、正文 13px / 行高 1.75、
风险卡片 `break-inside: avoid`、`print-color-adjust: exact` 保证风险配色不被打印裁剪。

---

## 9. 安全边界、幂等与可观测

### 9.1 安全边界（硬约束）

* `writeback.py` **只实现评论写入**，不存在通过 / 拒绝 / 转交 / 撤回等改变审批结论或流向的接口；
* 审查意见文本固定包含边界声明：仅供审批参考、不构成法律意见、最终结论由审批人作出；
* 大模型只参与摘要表达，不参与风险判定，也不触发任何审批动作。

### 9.2 幂等控制

| 层级 | 幂等键 | 行为 |
| --- | --- | --- |
| 建任务 | 审批单号 `approval_instance` | 已建任务的审批单跳过 |
| 解析 | 文件 SHA256 `doc_sha256` | 指纹重复复用既有审查结果（阻塞态除外） |
| 回写 | 任务 `writeback_status` | `success` 状态下重复回写直接跳过并记审计 |

### 9.3 可观测

* `audit_logs` 覆盖 拉取 → 下载附件 → 计算指纹 → 解析（引擎/页数/段落/字符/OCR 置信度）→
  元数据 → 条款切分 → 规则审查（规则版本与命中编号）→ 摘要生成方式 → 回写（模式/通道/评论标识）
  → 通知（事件类型与投递结果）；
* 工作台「审计日志」页签按时间倒序展示；`GET /api/export` 一键导出全量 JSON；
* 审计记录支持从评论结果反查原始审批、附件、解析内容、命中规则全过程；
* 运行指标（`GET /api/metrics` + 大盘「运行指标」面板）：请求数、错误率、平均/峰值耗时、
  热点接口排序、慢请求（≥1s）列表、响应总量；`server.access_log` 打开后另写
  `data/access.log`。

---

## 10. 性能与健壮性设计要点

这些是本版本在做代码审查时逐项确认并落地的工程约束，不是「顺手加的」：

| 项 | 做法 | 解决的问题 |
| --- | --- | --- |
| 规则引擎匹配开销 | 每个段落的「去空格文本」在一次 `run_rules` 内只计算一次；正则预编译后复用 | 原实现按「规则 × 段落 × 表达式」重复计算，长合同下是主要 CPU 开销（400 段合同单次 6 ms） |
| 响应压缩 | `gzip` 压缩 ≥1KB 的文本类响应，仅对声明 `Accept-Encoding: gzip` 的客户端启用，并设 `Vary` | 详情接口含整篇正文，未压缩时动辄数百 KB（实测详情响应压到 8 KB 级） |
| 解析结果读取 | 按文件 mtime 失效的内存缓存；落盘用「临时文件 + `os.replace`」原子替换 | 详情页每次都要读解析 JSON；避免并发下读到半个文件 |
| SQLite | 开 `journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=5000`；补齐 case_id / risk_level / 状态组合索引 | 单机多线程下读写互不阻塞，避免 `database is locked` |
| 数据更新 | `update_case` / `update_result` 走**字段白名单**，未登记字段忽略并记审计 | 防止误传字段导致 SQL 拼接异常或注入 |
| 上传入口 | 服务端强制大小上限（`review.max_upload_mb`）；文件名做路径穿越硬化（只取 basename、剔除隐藏名、限制长度） | 前端限制可被绕过；`../` 类文件名可能写穿上传目录 |
| 连接管理 | Handler 设 `timeout=30`、`disable_nagle_algorithm=True` | HTTP/1.1 keep-alive 下半开连接会长期占用工作线程 |
| 规则质量 | 非法正则不再静默失效：跳过该规则并写入 `rule_diag`，随详情接口暴露、工作台告警、规则页保存前校验 | 规则支持在线编辑，写错一条会让该规则悄悄不生效 |
| 通知投递 | 后台线程 + 有界队列（满则丢弃并记审计）、按事件类型防抖、失败只记状态 | 通知不能阻断审查；批量审查不能刷屏 |
| 段落暴露上限 | `review.max_paragraphs_expose` 截断超长文档的段落返回，并标记 `paragraphs_truncated` | 异常超长文档不会把浏览器打爆（审查与锚点不受影响） |

---

## 11. 已知限制与生产化改造清单

| 限制（v1.0 现状） | 生产化建议 |
| --- | --- |
| 单机 SQLite，单连接加锁 | 换 PostgreSQL / MySQL，按 `approval_instance` 建唯一索引 |
| 无登录与权限隔离 | 接入企业 SSO，按 `法务审查人 / 业务经办人 / 系统管理员` 做 RBAC 与数据行级权限 |
| 拉取为手动触发 | 加定时调度（Cron / APScheduler），配合失败退避重试 |
| 审批侧为模拟实现 | 替换 `approval.base_url` 为真实接口，并补充签名鉴权与限流 |
| PDF 依赖本机浏览器 | 部署环境统一安装 Edge，或改为独立的文档转换服务 |
| OCR 依赖 Windows | 跨平台部署时接入独立 OCR 服务（保留现有 `blocked` 降级路径） |
| 加密 PDF 直接阻塞 | 接入企业密钥服务或人工解密流程（当前已提供明确的人工处理提示） |
| 规则库需法务确认 | 建立规则评审与版本发布流程，规则变更留痕并回放历史合同 |
| 无性能压测 | 大附件/批量并发场景补充解析队列与资源配额 |
