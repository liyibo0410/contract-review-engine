# 合同智能审查系统 v1.0

面向企业合同审批场景的智能审查系统：读取待审合同与附件 → 抽取正文与关键条款 →
用可配置合规规则识别法律与商业风险 → 生成中文审查意见 → 回写审批单评论区 → 导出审查报告。

**整条链路零第三方依赖**：只用 Python 标准库，配合 Windows 自带的 OCR 引擎与 Edge 浏览器
即可完成 DOCX / PDF / 扫描件解析、报告 PDF 导出与页面渲染。装好 Python 就能跑。

| | |
| --- | --- |
| 版本 | 1.0.0 |
| 运行环境 | Windows 10/11 + Python 3.10 以上（开发验证于 3.13） |
| 第三方依赖 | **无**（构建示例合同集时才需要 `python-docx / reportlab / pillow / pypdf`） |
| 前端 | 原生 HTML / CSS / JS 三页面，无框架、无构建步骤 |
| 存储 | SQLite（标准库 `sqlite3`） |

---

## 一、快速开始

```bash
cd contract-review-1.0
python run.py
```

启动后打开终端里打印的地址（默认 `http://127.0.0.1:8770`）：

| 页面 | 地址 | 说明 |
| --- | --- | --- |
| 合同审查大盘 | `http://127.0.0.1:8770/` | 待审列表、风险统计、筛选、上传入口、一键自检 |
| 智能审查工作台 | `http://127.0.0.1:8770/workbench?id=1` | 双栏视窗 + 风险卡片 + 双向高亮 + 回写区 |
| 合规规则与模板 | `http://127.0.0.1:8770/rules` | 按合同类型维护规则、触发条件与标准条款库 |
| 模拟审批系统 | `http://127.0.0.1:8770/mock/feishu/api/todo` | 模拟飞书的待办与评论接口（JSON） |

常用参数：

```bash
python run.py --port 8780      # 换端口
python run.py --reset          # 清空数据库与模拟审批数据后启动
python run.py --selftest       # 只跑验收自检，不启动服务
```

### 第一次使用建议按这个顺序点

1. 打开大盘，点右上角 **「拉取待办并审查」** → 8 个示例合同自动完成解析与审查；
2. 列表里点任意合同的 **「审查」** → 进入工作台，点击风险卡片会联动跳到正文高亮处；
3. 在工作台右侧 **「审批协同与回写」** 写一句法务批注 → **「写回审批单」**；
4. 点 **「查看审批评论区」** → 看到审查意见已进入模拟审批单评论区；
5. 点 **「导出 PDF」** → 得到正式排版的审查意见报告。

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
│  ├─ config.py               配置加载与路径解析
│  ├─ store.py                SQLite 持久层 + 任务状态机
│  ├─ parsers.py              文档解析引擎（DOCX / PDF / 路由）
│  ├─ ocr_win.py              扫描件 OCR 通道（Windows 内置 OCR）
│  ├─ review.py               条款切分 + 元数据抽取 + 规则引擎 + 条款推荐
│  ├─ llm.py                  大模型接入（可选，仅用于摘要润色）
│  ├─ output.py               审查意见撰写 + 报告导出（Markdown/HTML/PDF）
│  ├─ writeback.py            审批回写模块（幂等 + 预览模式 + 失败落库）
│  ├─ pipeline.py             审查流水线编排
│  └─ server.py               HTTP 服务与路由
│
├─ mock/feishu.py             模拟审批系统（待办 / 详情 / 评论区 / 故障注入）
├─ ui/                        前端三页面
│  ├─ index.html              合同审查大盘
│  ├─ workbench.html          智能审查工作台
│  ├─ rules.html              合规规则与模板
│  └─ assets/app.css|app.js
│
├─ config/
│  ├─ app.json                服务、路径、审查阈值、审批对接、大模型开关
│  ├─ samples.json            示例合同集清单（同时是模拟审批待办的种子数据）
│  ├─ clause_types.json       条款类型词典（16 类，用于条款切分与覆盖度）
│  ├─ rules/                  合规规则库：purchase / sales / service / labor
│  └─ clauses/clauses.json    标准示范条款库（多套备选方案）
│
├─ samples/                   示例合同集（8 个文件，覆盖多格式与多场景）
├─ docs/                      交付文档、验收对照表、演示脚本、运行截图、参考资产
├─ tools/                     构建与验证工具（示例生成、数据重置、验证脚本、截图）
├─ verify/                    验证报告输出（验收自检、HTTP 冒烟、截图采集结果）
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
python run.py --selftest                      # 33 项验收自检（命令行）
python tools/verify_acceptance.py             # 同上，报告落盘 verify/selftest.{json,txt}
python tools/reset_data.py                    # 重置数据（演示前清理）
python tools/verify_http.py                   # HTTP 全链路冒烟测试（45 项）
python tools/verify_startup.py                # 以真实方式启动 run.py 并验证三个页面可用
python tools/capture_screens.py               # 采集页面截图并校验渲染
```

也可以在大盘页面点 **「一键自检」**，直接在弹窗里看到逐条结果。

最新一次验证结果：

| 验证项 | 结果 |
| --- | --- |
| 验收自检 | **33 / 33 通过**（`verify/selftest.txt`） |
| HTTP 全链路冒烟 | **45 / 45 通过**（`verify/smoke_http.txt`） |
| 启动入口验证 | **通过**（`verify/startup.txt`，`python run.py` 真实启动并访问三个页面） |
| 页面截图与渲染校验 | **5 / 5 通过**（`verify/screenshots.txt`，截图见 `docs/screenshots/`） |
| 线上实例巡检 | **11 / 11 通过**（`verify/remote_inspect.txt`，`python deploy/verify_remote.py`） |

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
  "keyword": "合同审查",                   // 机器人安全设置选「自定义关键词」时填
  "at_mobiles": [], "at_all": false,
  "min_interval_sec": 0,                   // 同一事件类型最短发送间隔（防刷屏）
  "events": { "high_risk": true, "blocked": true, "writeback_failed": true }
}
```

钉钉机器人的两种安全设置都支持：**加签**（`secret`，用 HMAC-SHA256 签名，签名不匹配会返回
`errcode 310000`）与**自定义关键词**（`keyword`，会前置到消息标题）。

### 验证与排障

* 启动后在大盘或工作台点 **「机器人通知」** → 可以看到启用状态、加签配置、发送统计与最近发送记录；
* 点 **「发送测试通知」** 会立刻向群里发一条测试消息，失败时直接显示钉钉返回的 `errcode/errmsg`；
* 或直接调接口：`POST /api/notify/test`、`GET /api/notify/status`、`GET /api/notify/recent`。

> **通知是旁路能力**：发送在后台线程异步进行，任何失败只记录状态与审计，**绝不影响审查主流程**——
> 不会因为群机器人挂了导致合同审不出来。发送失败时可在通知面板看到原因并重试。
