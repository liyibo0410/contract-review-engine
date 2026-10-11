# 衡鉴 V2.1.7

面向企业合同审批场景的智能审查系统：读取待审合同与附件 → 抽取正文与关键条款 →
用可配置合规规则识别法律与商业风险 → 生成中文审查意见 → 回写审批单评论区 → 导出审查报告。

**整条链路零第三方依赖**：只用 Python 标准库，配合系统自带的 OCR 引擎与浏览器
即可完成 DOCX / PDF / 扫描件解析、报告 PDF 导出与页面渲染。装好 Python 就能跑。

| | |
| --- | --- |
| 产品名称 | **衡鉴 V2.1.7** · 企业合同智能审查系统 |
| 技术版本 | **2.1.4**（2.0 可运营版 → 界面整改 → AI 助手与引导 → 前端细节修复 → 卡片动效 → 品牌收口） |
| 运行环境 | Windows 10/11 + Python 3.10 以上（开发验证于 3.13）；Linux 用 `tesseract` |
| 第三方依赖 | **无**（构建示例合同集时才需要 `python-docx / reportlab / pillow / pypdf`） |
| 前端 | 原生 HTML / CSS / JS（6 页面 + 悬浮 AI 助手），无框架、无构建步骤，含移动端适配 |
| 存储 | SQLite（标准库 `sqlite3`） |
| 需求闭环 | 需求基线 **2.4 全 10 条**（含 2.4.2 三角色权限） |

> **两套版本口径（决议 D-43）**，不要混用：
> * **品牌名 = 衡鉴 V2.1.7** —— 对外名称，用在界面顶栏/页脚/报告页脚/评论区/文档标题；
>   唯一真源是 `app/__init__.py` 的 `BRAND_*` 常量。
> * **技术版本 = 2.1.4** —— 语义化版本，唯一真源是 `app/__init__.py` 的 `__version__`；
>   `/api/health.version`、`CHANGELOG` 用它。
>
> 再改品牌名只需两步：改 `BRAND_*` 常量 + 重跑 `tools/_onetime/rebrand_hengjian_v202.py`
> （幂等脚本，全站展示位一次改齐）。变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

---

## 一、快速开始

```bash
cd contract-review-v2.0
python run.py
```

启动后打开终端里打印的地址（默认 `http://127.0.0.1:8770`），**会先跳到登录页**。

| 页面 | 地址 | 可访问角色 |
| --- | --- | --- |
| 登录页 | `http://127.0.0.1:8770/login` | 匿名 |
| 合同审查大盘 | `http://127.0.0.1:8770/` | 全部登录角色（统计卡可点击下钻） |
| 智能审查工作台 | `http://127.0.0.1:8770/workbench?id=1` | 全部登录角色 |
| 动态孪生看板 | `http://127.0.0.1:8770/twin` | 全部登录角色（**全真实实时数据**） |
| 合规规则与模板 | `http://127.0.0.1:8770/rules` | **仅系统管理员** |
| 模拟审批系统 | `http://127.0.0.1:8770/mock` | 全部登录角色（美化查看页） |
| 模拟审批原始接口 | `http://127.0.0.1:8770/mock/feishu/api/todo` | 匿名（按外部系统形态暴露） |

**每个页面右下角都有一个悬浮 AI 助手**（点它提问，或点「引导」让它一步一步带操作）。
深链：`?pet=open` 直接开对话、`?pet=tour` 直接开始本页引导。

### 演示账号（需求 2.4.2 三角色）

| 账号 | 口令 | 角色 | 权限边界 |
| --- | --- | --- | --- |
| `legal` | `Legal@2026` | 法务审查人 | 查看详情、核对高亮、采纳/调整建议、补充法务意见、**触发回写** |
| `applicant` | `Apply@2026` | 业务经办人 | 提交审查申请、查看风险点与整改建议、下载报告；**不可回写、不可改规则** |
| `admin` | `Admin@2026` | 系统管理员 | 维护规则与标准条款库、处理阻塞重试、配置调度与对接参数、查看指标 |

口令以 pbkdf2-sha256（12 万次迭代 + 每账号独立盐）存储，**明文不落库**；
会话令牌保存在服务端 `app_sessions` 表，Cookie 为 `HttpOnly` + `SameSite=Lax`，脚本读不到。
演示时可用顶栏的**身份切换**或登录页的**一键进入**按角色分段走流程；
该开关由 `auth.demo_switch` 控制，生产化部署应置 `false`。

常用参数：

```bash
python run.py --port 8780      # 换端口
python run.py --reset          # 清空数据库与模拟审批数据后启动
python run.py --selftest       # 只跑验收自检（52 项），不启动服务
```

### 第一次使用建议按这个顺序点

1. **以业务经办人身份**（登录页点「业务经办人 · 一键进入」）打开大盘 →
   右上角 **「拉取待办并审查」** → 8 个示例合同自动完成解析与审查；
   若要自动化演示，打开大盘的 **「自动流转 · 定时拉取」** 开关（默认关闭）；
2. 切到 **法务审查人** → 列表里点任意合同的 **「审查」** → 进入工作台，
   点击风险卡片会联动跳到正文高亮处；
3. 工作台右侧看 **「审查表达模式对照」** 面板 → 规则引擎模式 vs 规则+模型润色模式；
4. 写一句法务批注 → **「写回审批单」** → **「查看审批评论区」** →
   看到审查意见已进入模拟审批单评论区；
5. 点 **「导出 PDF」** → 服务端直出 PDF 报告（无浏览器环境下走纯标准库通道）；
6. 切到 **系统管理员** → 顶栏 **「合规规则与模板」** 维护规则；
   用 **法务审查人** 访问该页会被拦在 403 权限受限页（截图留证）。

### 一键跑齐全部验证

```bash
python tools/run_all.py                 # 全部
python tools/run_all.py selftest http   # 指定项：consistency|js|selftest|http|render|bench|devcheck
```

产物全部落在 `verify/`：`selftest.txt` / `smoke_http.txt` / `screenshots.txt` /
`bench.txt` / `jscheck.txt` / `consistency.txt`。


---

## 二、目录结构

```
contract-review-1.0/
├─ run.py                     启动入口（参数解析 / 初始化 / 打印访问地址）
├─ selftest.py                验收自检（33 项真实校验，无侵入临时目录执行）
├─ README.md                  本文件：运行说明
├─ PRD.md                     产品需求文档
├─ SPEC.md                    技术规格说明书
│
├─ app/                       后端审查服务（零第三方依赖）
│  ├─ config.py               配置加载与路径解析（含 auth/scheduler/queue 三段）
│  ├─ store.py                SQLite 持久层 + 任务状态机 + 账号与会话表
│  ├─ auth.py                 【v2.0】三角色 RBAC：口令哈希、会话令牌、路由权限表
│  ├─ parsers.py              文档解析引擎（DOCX / PDF / 路由 / 页眉页脚剔除）
│  ├─ ocr_win.py              扫描件 OCR 通道（Windows 内置 OCR ／ Linux tesseract）
│  ├─ review.py               条款切分 + 元数据抽取 + 规则引擎 + 条款推荐
│  ├─ llm.py                  大模型接入（可选，仅用于摘要润色）+ 模式对照信息
│  ├─ output.py               审查意见撰写 + 报告导出（Markdown/HTML/PDF）
│  ├─ pdf_min.py              【v2.0】纯标准库 PDF 直出（无浏览器环境兜底）
│  ├─ scheduler.py            【v2.0】定时拉取调度（指数退避 + 可运行时开关）
│  ├─ queue.py                【v2.0】审查队列与四道配额（线程池 + queue.Queue）
│  ├─ notify.py               机器人通知（钉钉加签 / 通用 Webhook，含外发脱敏）
│  ├─ writeback.py            审批回写模块（幂等 + 预览模式 + 失败落库）
│  ├─ pipeline.py             审查流水线编排
│  └─ server.py               HTTP 服务与路由 + 鉴权闸门
│
├─ mock/feishu.py             模拟审批系统（待办 / 详情 / 评论区 / 故障注入）
├─ ui/                        前端页面
│  ├─ login.html              【v2.0】登录页（三角色一键进入）
│  ├─ denied.html             【v2.0】403 权限受限页（模板，占位符由服务端填充）
│  ├─ index.html              合同审查大盘（含自动流转 / 队列 / 角色权限面板）
│  ├─ workbench.html          智能审查工作台（含模式对照与剔除留痕）
│  ├─ rules.html              合规规则与模板（仅系统管理员）
│  ├─ twin.html               动态孪生平台看板
│  └─ assets/app.css|app.js|fx.css|charts.js
│
├─ config/
│  ├─ app.json                服务、路径、审查阈值、审批对接、鉴权、调度、队列、通知脱敏
│  ├─ samples.json            示例合同集清单（同时是模拟审批待办的种子数据）
│  ├─ clause_types.json       条款类型词典（16 类，用于条款切分与覆盖度）
│  ├─ rules/                  合规规则库：purchase / sales / service / labor
│  └─ clauses/clauses.json    标准示范条款库（多套备选方案）
│
├─ CHANGELOG.md               【v2.0】变更日志与语义化版本规范
├─ samples/                   示例合同集（8 个文件，覆盖多格式与多场景）
├─ docs/                      交付文档、验收对照表、演示脚本、运行截图、参考资产
├─ tools/                     构建与验证工具（示例生成、一键验证、基准、JS 语法、一致性）
├─ verify/                    验证报告输出（自检、冒烟、渲染截图、基准、一致性、JS 校验）
└─ data/                      运行时数据（SQLite、附件、解析缓存、报告）
```

---

## 三、示例合同集

`config/samples.json` 定义了 8 个示例，既用于生成 `samples/` 下的文件，也作为模拟审批系统的待办种子：

| # | 文件 | 格式 | 类别 | 预期结果 |
| --- | --- | --- | --- | --- |
| 01 | 软件采购合同_风险用例 | DOCX | 采购 | 高风险：知识产权归供应商（PU-H-001）、到货即付全款无验收（PU-H-002）→ 建议拒绝 |
| 02 | 服务器设备采购合同_标准用例 | PDF | 采购 | 未命中任何规则 → 通过 |
| 03 | 大额销售合同_违约责任不对等 | DOCX | 销售 | 高风险：客户方滞纳金每日万分之一 vs 我方赔偿无上限（SA-H-001） |
| 04 | 技术服务合同_标准用例 | PDF | 服务 | 未命中任何规则 → 通过 |
| 05 | 劳动合同_风险用例 | DOCX | 劳动 | 高风险：违法约定离职违约金与押金（LA-H-001）、试用期超法定上限（LA-H-002） |
| 06 | 软件采购合同_扫描件 | PNG | 采购 | 走 OCR 通道，命中到货即付全款（PU-H-002）与管辖不利（PU-H-005） |
| 07 | 设备采购合同_无文本层 | PDF | 采购 | 解析受阻：无文本层的图片型 PDF |
| 08 | 保密协议_加密用例 | PDF | 服务 | 解析受阻：AES-128 加密文件 |

> 示例文件由 `python tools/build_samples.py` 生成（该脚本是构建期工具，需要 python-docx /
> reportlab / pillow / pypdf，**运行系统本身不需要这些包**）。示例已随交付物提供，一般无需重新生成。

---

## 四、零依赖是怎么做到的

| 能力 | 常规做法 | 本项目做法 |
| --- | --- | --- |
| DOCX 解析 | python-docx | 标准库 `zipfile` + `xml.etree` 直接解析 OOXML，输出段落树 / 表格 / 页码 / 字符区间 |
| PDF 文本抽取 | pdfplumber / PyMuPDF | 自实现对象扫描 + `/Filter` 链解码（ASCII85/ASCIIHex/Flate）+ **ToUnicode CMap** 映射 |
| 扫描件 OCR | pytesseract / paddleocr | 调用 **Windows 内置 OCR 引擎**（Windows.Media.Ocr，通过 WinRT）；中文识别器本机已就绪 |
| 报告 PDF | reportlab | 调用本机 **Edge / Chrome 无头 `--print-to-pdf`**（系统自带），失败时降级为浏览器打印导出 |
| Web 服务 | FastAPI / Flask | 标准库 `http.server.ThreadingHTTPServer` |
| 持久化 | ORM | 标准库 `sqlite3` |
| 规则配置 | PyYAML | JSON（避免额外依赖；规则模式与语义见 SPEC.md） |

### 可选增强（不影响主流程）

* **大模型润色**：在 `config/app.json` 里把 `llm.enabled` 设为 `true` 并填 `llm.base_url`，
  设置环境变量 `OPENAI_API_KEY`，系统会用 OpenAI 兼容接口润色中文审查摘要。
  **风险判定始终由确定性规则引擎完成，大模型不参与判定，也不触发任何审批动作。**
* **对接真实审批系统**：把 `approval.base_url` 改成真实审批平台的接口地址即可，
  回写模块走的是标准 HTTP，不再使用内置模拟实现。

---

## 五、验证与自检

```bash
python tools/run_all.py                 # 一键跑齐下列全部，产物统一落 verify/
python run.py --selftest                # 验收自检（52 项，命令行输出）
python tools/run_selftest.py            # 同上，报告落盘 verify/selftest.{json,txt}
python tools/verify_http.py             # HTTP 全链路冒烟（88 项，含鉴权与角色行为）
python tools/verify_render.py           # 页面渲染与截图校验（12 项，含 390×844 手机端）
python tools/bench.py                   # 性能基准 → verify/bench.{txt,json}
python tools/check_twin_data.py         # 孪生看板数据真实性对账（逐项与库内比对）
python tools/check_assistant.py         # AI 助手专项：意图/实时数据/不编造/引导可用性
python tools/audit_frontend.py         # 前端逐页体检（CDP 真跑真量：异常/空容器/溢出/重叠）
python tools/audit_interactive.py      # 前端交互态体检（弹窗/助手面板/引导气泡/工作台）
python tools/audit_frontend_deep.py    # 前端深检（对比度/触控目标/可访问性/越界/小字，10 维度 × 3 视口）
python tools/probe_topbar.py           # 顶栏几何体检（7 档视口宽度下的行数与子元素宽度）
python tools/check_conn_noise.py        # 客户端早断连不再打 traceback
python tools/check_consistency.py       # v2.0 改动一致性核查（编辑是否真的落到文件里）
python tools/devcheck.py                # 开发期编译 / 导入 / 关键路径自检
python tools/run_all.py remote          # 线上实例巡检（公网 HTTP，39 项）
python tools/reset_data.py              # 重置数据（演示前清理）
```

也可以在大盘页面点 **「一键自检」**，直接在弹窗里看到逐条结果。

最新一次验证结果：

| 验证项 | v1.0 基线 | **v2.1.4 实测** | 报告 |
| --- | --- | --- | --- |
| 改动一致性核查 | — | **全部在位（缺失 0 项）** | `verify/consistency.txt` |
| 前端脚本语法 | — | **17 个脚本 0 失败** | `verify/jscheck.txt` |
| **前端逐页体检（8 维度 × 6 页）** | — | **0 问题**（脚本异常/空容器/塌陷/重复 id/模板残留/横向溢出/顶栏重叠） | `verify/frontend_audit.txt` |
| **前端交互态体检** | — | **0 问题**（下钻弹窗/助手面板/引导气泡/工作台定位/模拟审批详情） | `verify/interactive_audit.txt` |
| **卡片动效专项** | — | **9 / 9**（入场/常驻/定时刷新不重播/数值变化检测/reduced-motion 降级） | `verify/cfx_probe.txt` |
| 验收自检 | 33 / 33 | **58 / 58 通过** | `verify/selftest.txt` |
| HTTP 全链路冒烟 | 45 / 45 | **95 / 95 通过** | `verify/smoke_http.txt` |
| 页面渲染与截图校验 | 5 / 5 | **15 / 15 通过**（截图 15 张，含手机端 3 张） | `verify/screenshots.txt` |
| **线上实例巡检（公网）** | 11 / 11 | **43 / 43**（v2.1.5 上线后全绿；上线前为 40 / 43，3 项部署漂移已消除） | `verify/remote_inspect.txt` |
| AI 助手专项校验 | — | **26 / 26**（意图匹配 / 实时一致性 / 不编造 / 引导可用性） | `verify/assistant_check.txt` |
| 孪生看板数据真实性 | — | **PASS**（逐项与库内对账，无模拟值） | `verify/twin_data.txt` |
| 客户端早断连日志噪音 | — | **PASS**（stderr 输出 0 字节） | `verify/conn_noise.txt` |
| 运行期第三方依赖 | 0 | **0（未增加）** | 依赖审查 |
| 性能基准 | 仅规则引擎 | **解析 8 例 / 规则 2 例 / 队列并发 / 报告三通道 / HTTP 端到端** | `verify/bench.txt` |
| 需求 2.4 条目闭环 | 8 / 10 | **10 / 10** | `docs/验收对照表.md` |

> 线上巡检 3 项失败**不是代码缺陷**，是本地已领先线上两个补丁版本、且线上 `scheduler.enabled` 仍为 `true`。
> 修正方式是重新部署（`python deploy/deploy_to_server.py`，需 SSH 凭据）；本轮按边界规则未擅自写远端。

`verify/bench.txt` 关键数据（本机实测，非估算）：

| 用例 | 耗时 |
| --- | --- |
| DOCX 解析（37–38KB） | 1–21 ms |
| PDF 文本层解析（178–184KB） | 8–11 ms |
| 扫描件 OCR（243KB PNG） | 约 630–770 ms（走系统 OCR，是链路里最重的一步） |
| 规则引擎（400 段 × 12 条规则） | 约 6 ms |
| 队列并发（6–10 份，workers=2） | 全部返回且综合风险无漂移 |
| 纯标准库 PDF 直出 | 约 2 ms（产物 31KB） |

---

## 六、常见问题

**端口被占用** → `python run.py --port 8780`。

**导出 PDF 没有直接下载，而是弹出了打印预览？**
说明没找到 Edge / Chrome。此时服务端会把排版好的 HTML 返回，浏览器会唤起打印，
在打印对话框里选择「另存为 PDF」即可得到同样的 PDF。装了 Edge 的机器上一般直接下载 PDF。

**扫描件识别失败 / 提示阻断？**
本机需要安装 Windows 中文 OCR 语言包（简体中文系统默认已安装）。
系统会返回 OCR 置信度，正文过少或置信度不足时任务转入 `blocked` 并提示人工复核，
管理员修正文件后可在列表页点「重新审查」。

**我能看到一张加密的示例合同一直处理不了？**
这是刻意设计的受阻用例（`08_保密协议_加密用例.pdf`，口令 `contract2026`），
用来演示「文档加密 → 任务阻塞 → 记录原因 → 人工处理后重试」这条异常流转。

**页面打不开 / 数据不刷新？**
页面通过 `fetch` 调用同源接口。若浏览器装了会拦截本机请求的代理插件，
请把 `127.0.0.1` 加入直连名单（系统内部调用同样显式禁用代理，不受 `http_proxy` 影响）。

**想要一个干净的初始状态** → `python run.py --reset` 或 `python tools/reset_data.py`。

---

## 七、边界与不做的事

* 系统**只生成审查意见并写入评论**，不提供通过 / 拒绝 / 转交 / 撤回等改变审批结论或审批流向的能力，
  审批平台中的最终动作仍由审批人完成。
* 输出的审查意见**仅供审批参考，不构成法律意见**。规则库依据的法律条文需由法务确认后使用。
* 风险判定是**确定性规则**的结论，不是大模型的自由判断；规则的召回率取决于规则库的覆盖程度。
* 本版本为演示与内网部署形态：单机 SQLite、无用户体系与鉴权、审批侧使用模拟实现。
  生产化改造清单见 `SPEC.md` 第 10 节。

---

## 八、部署到 Linux 服务器

系统可直接部署到 Linux（已在 Alibaba Cloud Linux 3 + Python 3.11 上完整验证）。
交付物自带一键部署脚本：

```bash
set CR_SSH_PASS=你的root密码        # Windows（PowerShell: $env:CR_SSH_PASS="..."）
python deploy/deploy_to_server.py
```

**带钉钉通知凭据部署**（推荐做法：凭据注入 systemd 环境变量，**不写进配置文件**，
这样随交付物分发的 `config/app.json` 保持干净）：

```bash
set CR_SSH_PASS=你的root密码
set CR_NOTIFY_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
set CR_NOTIFY_SECRET=SECxxxxxxxx
set CR_NOTIFY_KEYWORD=合同审查            # 机器人安全设置用「自定义关键词」时才需要
python deploy/deploy_to_server.py
```

脚本会自动：把服务器配置的 `notify.enabled` 置为 true、把凭据写进 systemd 的
`Environment=`（脚本打印时不会回显凭据值）、重启服务，并**在服务器上真发一条测试通知**验证链路。

脚本动作：**上传代码（自动跳过运行时数据 `data/` 与验证报告 `verify/`）→ 安装/刷新 systemd
服务 → 重启 → 跑服务端验收自检 → 打印访问地址**。可反复执行，等价于「更新代码 + 重启服务」。

可用环境变量覆盖默认值：`CR_HOST` / `CR_PORT`（默认 8160）/ `CR_REMOTE`（默认
`/opt/contract-review`）/ `CR_SSH_USER` / `CR_PYTHON`（默认 `/usr/bin/python3.11`）。

部署前请确认三件事：

| 依赖 | 说明 | 缺失时的表现 |
| --- | --- | --- |
| **Python 3.10+** | 代码使用了 `dict \| None` 这类类型注解，低版本无法启动 | 启动即报语法错误 |
| **OCR 引擎** | Windows 用系统内置 OCR；Linux 用 `tesseract`（都不需要 pip 包） | 扫描件任务进入 `blocked` 并提示；其余功能正常 |
| **chromium / chrome**（可选） | 用于服务端直出 PDF 报告 | 「导出 PDF」自动降级为返回排版好的 HTML 并唤起浏览器打印，**另存得到的 PDF 完全一致** |

Linux 上安装可选依赖：

```bash
# Alibaba Cloud Linux / RHEL / CentOS
yum install -y tesseract tesseract-langpack-chi_sim          # 扫描件 OCR
yum install -y chromium-headless google-noto-sans-cjk-ttc-fonts   # 服务端直出 PDF（含中文字体）

# Debian / Ubuntu
apt install -y tesseract-ocr tesseract-ocr-chi-sim
apt install -y chromium fonts-noto-cjk
```

部署后的日常操作：

```bash
systemctl status  contract-review      # 查看状态
systemctl restart contract-review      # 重启
tail -f /var/log/contract-review.log   # 查看日志
cd /opt/contract-review && /usr/bin/python3.11 selftest.py   # 在服务器上跑验收自检
```

> 端口需要在云厂商安全组放行；若与服务器上已有服务冲突，用 `CR_PORT` 换一个空闲端口，
> 脚本会自动把内置模拟审批系统的自调用地址对齐到新端口。

---

## 九、机器人通知（钉钉）

审查过程中的关键事件会推送到**钉钉自定义机器人**（也支持任意通用 Webhook）：

| 事件 | 触发时机 | 推送内容 |
| --- | --- | --- |
| `high_risk` | 审查完成且命中高风险 | 合同信息、综合风险与结论、命中的高风险项（含页码） |
| `review_completed` | 任一合同审查完成 | 同上（完整风险统计） |
| `blocked` | 解析失败进入阻塞 | 受阻原因与处理入口 |
| `writeback_failed` | 回写审批评论区失败 | 失败原因与重试入口 |
| `batch_summary` | 一轮拉取/批量审查结束 | 本轮新建数、高风险数、受阻数汇总（不逐条刷屏） |

### 配置

编辑 `config/app.json` 的 `notify` 段：

```json
"notify": {
  "enabled": true,
  "channel": "dingtalk",
  "webhook": "https://oapi.dingtalk.com/robot/send?access_token=你的token",
  "secret": "SECxxxxxxxxxxxxxxxx",        // 机器人安全设置选「加签」时填，SEC 开头
  "webhook_env": "DINGTALK_WEBHOOK",      // 也可改用环境变量传值（优先级更高）
  "secret_env": "DINGTALK_SECRET",
  "keyword": "合同审查",                   // 机器人安全设置选「自定义关键词」时填
  "at_mobiles": [], "at_all": false,
  "min_interval_sec": 0,                   // 同一事件类型最短发送间隔（防刷屏）
  "events": { "high_risk": true, "blocked": true, "writeback_failed": true }
}
```

**推荐用环境变量传密钥**，这样 `config/app.json` 可以随交付物一起分发而不泄露凭据：

```bash
export DINGTALK_WEBHOOK="https://oapi.dingtalk.com/robot/send?access_token=xxx"
export DINGTALK_SECRET="SECxxxxxxxx"
```

取值优先级：**环境变量 > 配置文件**。配置里 `events` 未登记的事件默认不发送
（通知宁可漏发也不要误发，想收哪类消息就显式打开）。

钉钉机器人的两种安全设置都支持：**加签**（`secret`，HMAC-SHA256 签名，签名不匹配会返回
`errcode 310000`）与**自定义关键词**（`keyword`，会前置到消息标题）。

### 先离线确认机器人能通（推荐顺序）

接入系统之前先用自带的自测工具确认机器人配置正确 —— 不需要启动本系统，也不需要连服务器：

```bash
python tools/test_notify.py --webhook "https://oapi.dingtalk.com/robot/send?access_token=xxx" \
                           --secret "SECxxxxxxxx"
# 只校验签名生成、不真发消息：
python tools/test_notify.py --webhook "..." --secret "..." --dry-run
# 不带参数时依次从 环境变量 → config/app.json 读取：
python tools/test_notify.py
```

成功会打印 `✓ 发送成功（钉钉返回 errcode=0）`；失败会**把钉钉的 errcode 翻译成处理建议**
（如 `310000` → 检查加签密钥是否一致或关键词是否出现在内容里）。

### 验证与排障

* 启动后在大盘或工作台点 **「机器人通知」** → 可以看到启用状态、加签配置、凭据来源
  （环境变量 / 配置文件）、发送统计与最近发送记录；
* 点 **「发送测试通知」** 会立刻向群里发一条测试消息，失败时直接显示钉钉返回的 `errcode/errmsg`；
* 或直接调接口：`POST /api/notify/test`、`GET /api/notify/status`、`GET /api/notify/recent`。

> **通知是旁路能力**：发送在后台线程异步进行，任何失败只记录状态与审计，**绝不影响审查主流程**——
> 不会因为群机器人挂了导致合同审不出来。发送失败时可在通知面板看到原因并重试。

### v2.0：外发脱敏与默认收敛（必读）

通知通道是**本项目唯一把数据送出本机的出口**，而合同属最敏感数据。因此 v2.0 起：

* **字段级掩码**：相对方名称、合同金额、统一社会信用代码、申请人姓名、合同标题、
  附件名一律按规则掩码后外发；通知正文**不携带合同正文片段**（`max_evidence_chars: 0`）。
* **整段兜底扫描**：除字段级掩码外，还对整段正文再做一次正则扫描
  （信用代码 / 金额 / 手机号 / 邮箱），保证漏调一处字段级掩码也不会明文外发。
* **默认收敛**：`batch_summary` 与 `review_completed` **默认关闭**（信息量低、外发面大），
  只保留 `high_risk` / `blocked` / `writeback_failed`。

掩码规则在 `config/app.json` 的 `notify.mask` 段，可按需放宽（例如演示时保留相对方名称）：

```json
"notify": {
  "mask": { "party": true, "amount": true, "credit_code": true,
            "case_no": false, "applicant": true, "contract_title": true,
            "max_evidence_chars": 0 }
}
```

发送预览可直接调 `POST /api/notify/test`，返回体里的 `detail` 即最终外发的正文。

---

## 十、v2.0 新增能力速查

### 1. 角色与权限（需求 2.4.2）

| 能力 | 说明 | 法务审查人 | 业务经办人 | 系统管理员 |
| --- | --- | :---: | :---: | :---: |
| `view_cases` / `view_detail` | 查看任务列表与审查详情 | ✓ | ✓ | ✓ |
| `export_report` | 下载审查报告 | ✓ | ✓ | ✓ |
| `submit_contract` | 提交合同审查申请（上传） | — | ✓ | ✓ |
| `pull_todo` | 拉取审批待办 | — | ✓ | ✓ |
| `edit_suggestion` | 采纳 / 调整修改建议 | ✓ | — | — |
| `add_opinion` | 补充法务批注 | ✓ | — | — |
| `writeback` | 回写审批意见 | ✓ | — | — |
| `rerun_review` | 重新执行审查 | ✓ | — | ✓ |
| `retry_blocked` | 阻塞任务重试 | — | — | ✓ |
| `edit_rules` | 维护规则与标准条款库（含 `/rules` 页面） | — | — | ✓ |
| `export_audit` | 导出审计数据 | ✓ | — | ✓ |
| `notify_test` / `fault_inject` / `reset_data` | 通知测试、故障注入、重置演示数据 | — | — | ✓ |
| `run_selftest` / `view_metrics` / `view_users` / `manage_scheduler` | 自检、指标、账号、调度 | — | — | ✓ |

* 权限是 **能力（capability）** 而非角色直连，路由表集中在 `app/auth.py` 的 `ROUTES`；
* **失败关闭**：未登记的非只读接口一律按管理员专属处理 —— 新增接口忘记登记时只会更严，不会更松；
* 未登录访问页面 → 302 跳登录页；访问接口 → 401；越权 → 403 权限受限页并写审计
  （`audit_logs.tool = auth.guard`）；
* 前端只做**显隐与提示**，真正的拦截 100% 在服务端。

### 2. 自动流转（定时拉取）

标准库后台线程按间隔拉取审批待办并自动审查，连续失败按 `backoff_factor` 指数退避并封顶
`max_interval_sec`，成功一次即复位。**默认关闭**（否则演示数据会在无人操作时自己变化），
可在大盘「自动流转 · 定时拉取」面板即时开关，配置落盘 `config/app.json`。

接口：`GET /api/scheduler`、`POST /api/scheduler/config`、`POST /api/scheduler/run`。

### 3. 页眉页脚剔除（解析精度）

三条判定同时满足才剔除，任一不满足一律保留：

1. **跨页重复的边缘短行** —— 归一化后（去空白/分隔符）出现在 ≥ `strip_min_pages` 页的首尾
   `strip_edge_lines` 行内；
2. **典型形态边缘行** —— 命中页码 / 密级 / 文件编号形态（页码逐页不同，只靠重复抓不到，
   必须单列这条通道）；
3. **不在保护名单内** —— 条款标题、含甲方/乙方/金额/价款/编号/签署等实体词的行**永不剔除**。

剔除后**重算段落坐标**，锚点在归一化全文上仍可精确定位；剔除明细逐条写审计，可查可复核。
参数在 `config/app.json` 的 `review.strip_*`，可整体关闭（`strip_repeat_lines: false`）。

### 4. 规则引擎 / 模型润色模式对照

工作台右侧「审查表达模式对照」并排展示两种模式，未配置 LLM Key 时**明示**当前模式与
未启用原因。风险判定 100% 由确定性规则引擎产出，大模型只改写措辞、**不参与判定**，
所以两种模式下的风险等级、命中项与结论完全一致，差别只在中文字面表达。

### 5. 服务端 PDF 直出（零依赖兜底）

报告导出改为三通道，按可用性依次尝试：

1. **浏览器无头打印**（本机有 Edge/Chrome 时，排版最佳）；
2. **纯标准库 PDF 直出**（`app/pdf_min.py`，按字节拼 PDF，中文用 PDF 标准**非嵌入**
   CID 字体 `/STSong-Light` + `/UniGB-UCS2-H`，并写 ToUnicode CMap 保证复制出来是汉字）——
   线上无 chromium 的 Linux 实例由此不再永久降级；
3. **可打印 HTML**（前两者都不可用时的最终降级）。

通道自检：`GET /api/pdf/check`。

### 6. 审查队列与四道配额

`queue.Queue` + 固定工作线程池（`queue.workers`）。所有触发审查的入口
（手动重审、审批拉取、本地上传、阻塞重试）统一经队列，并发度由服务端钉死。

| 配额 | 配置项 | 超限行为 |
| --- | --- | --- |
| 单文件大小 | `review.max_upload_mb` | 拒绝并提示 |
| 队列长度 | `queue.maxsize` | 拒绝并写审计 |
| 待处理堆积 | `queue.max_pending` | 拒绝并写审计 |
| 附件存储总量 | `queue.max_total_mb` | 拒绝并写审计 |

`POST /api/cases/{id}/review` 支持 `{"wait": false}` 异步入队并返回 `task_id`，
默认 `wait=true` 保持同步语义（接口向后兼容）。队列状态：`GET /api/queue`。

### 7. 版本口径（禁止版本失真）

`app/__init__.py` 的 `__version__` 是唯一真源，`/api/health` 必须返回它。
自检里有一条专门断言「交付版本 = 运行版本 = 文档版本」，并检查代码里不存在
硬编码的旧版本号。
