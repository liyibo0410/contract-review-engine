# CLAUDE.md — 团队协作与工程约定

> 本文件是**团队协作系统**：边界、命令、禁区、约定都写在这里，
> 让每一个新加入的协作者（人或 AI）在 5 分钟内知道"能做什么、不能做什么、怎么做"。

---

## 1. 三个目录的职责边界

| 目录 | 职责 | 禁止 |
| --- | --- | --- |
| `backend/` | FastAPI 服务、解析引擎、规则引擎、LLM 层、回写、报告、评测 | 禁止在这里放任何前端资源文件 |
| `frontend/` | 零构建前端（原生 HTML/CSS/JS），由后端挂载为静态目录 | 禁止引入 npm/构建工具/CDN 外链；禁止硬编码接口地址（一律用相对路径 `/api/...`） |
| `docs/` | 全部交付文档，按阶段编号分层存放 | 禁止把运行产物（数据库、报告、截图）提交到 docs 根目录，放 `docs/assets/` |

## 2. 环境与命令

```bash
# 环境变量：统一走 .env（后端所有可调参数都在 app/config.py 集中管理）
cp .env.example .env

# 依赖：统一使用 uv 管理，不要用 pip 直接装到全局
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -e .
uv pip install --python .venv/Scripts/python.exe rapidocr-onnxruntime   # 可选

# 启动（开发）
cd backend && uv run uvicorn app.main:app --reload --port 8130
# 启动（一键）
start.bat

# 测试 / 评测 / 演示
cd backend
python -m pytest -q                     # 单元测试（39 项）
python -m app.eval.run_eval             # 黄金样例集回归跑分
python demo.py                          # 端到端演示脚本
python tools/smoke_api.py               # 全接口冒烟
python tools/gen_samples.py             # 重新生成样例合同与黄金标注
```

## 3. 阶段产出与质量门

每个阶段都必须有**明确的输出文件**，不允许"做完了但没留下东西"：

| 阶段 | 输出目录 | 质量门 |
| --- | --- | --- |
| 业务调研 | `docs/01_business_research/` | 场景目标 + 审核对象 + 反方风险清单 |
| 竞品分析 | `docs/02_competitor_analysis/` | 流程拆解 + 结果呈现拆解 + 差异化结论 |
| 问题建模 | `docs/03_problem_modeling/` | 核心问题 + 目标用户 + MVP 场景（收口文档，禁止发散） |
| 交互设计 | `docs/04_interaction_design/` | 上传/处理/人工审批三段链路 + 状态定义 |
| 原型与边界 | `docs/05_prototype/`、`docs/06_architecture_design/` | 页面清单 + 前后端职责边界 |
| 架构设计 | `docs/06_architecture_design/` | 模块边界 + 数据流 + 接口契约先行 |
| 数据模型 | `docs/07_data_model/` | 每张表的字段含义 + 关系 + 前端必展示字段 |
| 接口规范 | `docs/08_api_spec/` | 与 `app/schemas.py` 逐字段一致 |
| 前后端实现 | `docs/09_frontend_plan/`、`docs/10_backend_plan/` | 未实现的接口必须标注「未开发」，禁止伪造功能 |
| 联调 | `docs/11_integration/` | 联调顺序 + 每步验收标准 |
| 发布部署 | `docs/12_release_deployment/` | 环境变量 + 启动顺序 + 异常排查 |
| 交付 | `docs/13_delivery/` | 验收对照表逐条打勾 |

**质量门硬约束**：任何进入 `main` 的改动，必须通过 `test` 与 `eval` 两道门。
规则库改动导致高风险召回率下降或出现误报，CI 直接失败。

## 4. 工程红线（改不得）

1. **只写意见，不改结论** —— 后端不得出现任何审批结论写操作接口（通过/拒绝/转交/撤回）。
2. **规则主判定，模型只增强** —— 风险等级只能由 `rule_engine.py` 产出；
   `llm.py` 不允许新增或修改风险项，其输出必须过结构校验，失败即降级。
3. **坐标唯一真理** —— 一切风险必须可溯源到 `clauses` 表的字符区间；
   禁止绕过 `coordinator` 自行拼接文本定位。
4. **幂等三键** —— 合同编号 + 附件 SHA256 + 规则版本号；回写前一律先 DRY_RUN 检查。
5. **审计留痕** —— 每个链路步骤必须写 `audit_logs`，缺一条视为未完成。
6. **演示零事故** —— 每个外部依赖（LLM / OCR / 网络）都必须有降级路径并有对应测试。
7. **零外部服务** —— 默认不引入 MySQL/Redis/消息队列；确需引入必须先在 docs 中论证。

## 5. 编码约定

- 中文注释解释**为什么**，英文标识符解释**是什么**。
- 单个模块不超过 600 行；超过就拆，拆不动说明职责没理顺。
- 所有对外函数写类型标注（`from __future__ import annotations`）。
- 异常处理必须携带可读原因：阻塞态要能区分"引擎缺失"与"图片模糊"。
- 写 `.ps1` 脚本一律纯 ASCII（Windows PowerShell 5.1 无 BOM 的 UTF-8 会乱码致解析失败）。
- Python 脚本运行加 `-u`；需要 UTF-8 输出时设 `PYTHONIOENCODING=utf-8`，
  **不要**用 `sys.stdout.reconfigure`。

## 6. 高风险操作禁区

以下操作必须先红色警告 → 列出受影响路径 → 等待明确确认，**没有例外**：

- 删除 / 覆盖 `backend/samples/` 下的样例合同
- 修改 `rules_v1.yaml` 中的规则 ID（会被黄金标注引用）
- 执行 `db.reset_db()` 或删除 `backend/data/`
- 任何 `git push --force`、批量重命名

## 7. 复杂任务先 Plan 再实施

超过 3 个文件改动、或涉及规则库/数据模型变更的任务，
必须先输出计划（改哪些文件、影响面、验证方式）并获得确认再动手。

## 8. 已知环境坑（本机实测）

| 现象 | 原因 | 对策 |
| --- | --- | --- |
| `pytest` 报编码错乱 | PowerShell 控制台编码与 UTF-8 冲突 | 设 `PYTHONIOENCODING=utf-8`，输出重定向用 `| Out-File -Encoding utf8` |
| 从 shell 启动的服务秒退 | 沙箱进程随命令结束被回收 | "启动 + 测试"必须在同一次调用内完成；长期驻留让用户手动启动 |
| 直连 PyPI 超时/TLS EOF | 网络受限 | 使用镜像：`uv pip install --index-url https://mirrors.aliyun.com/pypi/simple/` |
| `uv venv` 下载解释器失败 | 需要访问 GitHub | `uv venv --python <本机已有 python 路径>` |
| 中文 URL 参数丢失 | 命令行拼接编码问题 | 参数走 UTF-8 文件（`--json-file`）传递 |
