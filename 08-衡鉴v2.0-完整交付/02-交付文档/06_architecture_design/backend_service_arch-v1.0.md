# 衡鉴 2.0 后端服务架构（backend_service_arch）

> **一句话结论**：后端按「文件上传 / 任务状态管理 / 审核结果查询」三条主线组织服务，所有写操作收敛到 `pipeline.py` 与 `writeback.py`，只读查询直接走 `db.tx()`，依赖方向严格单向（API → 编排 → 解析/审查/回写 → 数据层）。

本文从后端服务视角描述服务划分与依赖方向，配合《系统架构设计文档》。

---

## 1. 三条主线总览

```mermaid
flowchart TB
    subgraph L1["主线一 · 文件上传"]
        U[POST /upload] --> CT[pipeline.create_task]
        P[POST /pull] --> CT
        CT --> DB1[(contracts/documents)]
        U --> AS[run_pipeline_async 后台线程]
    end
    subgraph L2["主线二 · 任务状态管理"]
        AS --> PS[run_parse] --> CO[coordinator]
        PS --> ST[(clauses/documents/contracts)]
        PS --> RV[run_review] --> RE[rule_engine] --> ST2[(findings/reviews/contracts)]
        RT[POST /retry] --> PS
        HB[GET /events] --> EB[(EventBus)]
    end
    subgraph L3["主线三 · 审核结果查询"]
        G1[GET /{id}] --> DB3[(contracts/documents/clauses/reviews/writebacks)]
        G2[GET /{id}/findings] --> DB4[(findings)]
        G3[GET /{id}/review] --> DB5[(reviews)]
        G4[GET /{id}/document] --> CO2[coordinator.normalize_blocks]
        G5[GET /{id}/hitl] --> HA[(hitl_actions)]
    end
```

---

## 2. 主线一：文件上传

| 服务/函数 | 职责 | 调用方 | 依赖 |
| --- | --- | --- | --- |
| `api/contracts.upload` | 校验后缀/大小(413)、落临时文件、调 create_task | 前端 drag&drop | pipeline |
| `api/contracts.pull` | 从审批系统模拟器按样例生成待审任务 | 大盘「拉取」 | pipeline |
| `pipeline.create_task` | 算 SHA256、生成 `code`、构造幂等键 `code\|sha256\|rule_version`、复用车判（同指纹复用任务）、写 `contracts`+`documents`、发 idempotent 事件 | upload/pull | db, writeback, rule_engine |
| `pipeline.run_pipeline_async` | 启 daemon 线程跑全流程；重任务则同步 `run_pipeline` | upload(auto_run) | pipeline |

**写表**：`contracts`(pending)、`documents`(pending)。**关键不变量**：`idem_key` 唯一（条件唯一索引，空串除外）。

---

## 3. 主线二：任务状态管理

状态机（源码 `pipeline.py` 头注释）：`pending → parsing → reviewing → completed`，`parsing/reviewing` 可分流 `blocked`，重试回 `pending`。

| 服务/函数 | 职责 | 依赖 |
| --- | --- | --- |
| `pipeline.run_parse` | 调三解析器 → `coordinator.normalize_blocks/classify/build_clauses/extract_metadata` → `decide_block` | parser_*, coordinator, audit |
| `pipeline.run_review` | `rule_engine.evaluate`（视角 ctx）→ `llm.enrich` 增强 → `writer.build_summary/profile` → 写库 | rule_engine, llm, writer |
| `pipeline.retry` | 清 clauses/findings/reviews + 文档回 pending，保留 writebacks | db, audit |
| `pipeline.EventBus` | 解析/审查各阶段 publish 事件；SSE 增量 | 无 |
| `audit.Step` | 每个阶段写 `audit_logs`（task.create/parse.done/review.done…） | db |

**阻塞代码**（6 种，必须可读可区分）：`DOC_ENCRYPTED` / `OCR_ENGINE_UNAVAILABLE` / `OCR_LOW_CONFIDENCE` / `EMPTY_CONTENT` / `PARSE_FAILED` / `PIPELINE_ERROR`。「引擎缺失」与「图片模糊」分开记录，否则重试无意义。

**写表**：`clauses`/`documents`/`contracts`（解析）、`findings`/`reviews`/`contracts`（审查）、`audit_logs`（全程）。

---

## 4. 主线三：审核结果查询

均为只读（除 HITL/回写/采纳等写入动作），直接 `db.tx()` 取数并映射为展示结构。

| 接口 | 服务 | 读表 |
| --- | --- | --- |
| `GET /{id}` | `_require`+聚合 | contracts/documents/clauses/reviews/writebacks |
| `GET /{id}/document` | 重渲染段落序列 | documents + coordinator |
| `GET /{id}/findings` | `_findings`（按等级排序） | findings |
| `GET /{id}/review` | `_review_out` | reviews + writebacks |
| `GET /{id}/report` | `writer.render_markdown` + `report.export_pdf` | clauses/findings/reviews |
| `GET /{id}/hitl` | `_interrupt_point`+`_last_hitl`+`_available_actions` | contracts/hitl_actions/writebacks |
| `GET /{id}/audit` | `audit.list_logs` | audit_logs |

---

## 5. 依赖方向（强制单向）

```
api/*  →  pipeline  →  {parser_*, coordinator, rule_engine, llm, writer, writeback, report, audit}
api/*  →  {rule_engine, db}
pipeline → db
service 间：coordinator 被 pipeline 调；rule_engine 被 pipeline 调；llm 被 pipeline 调；
           writeback/report/writer/audit 被 pipeline 或 api 调；
           禁止 service 反向依赖 api/*，禁止 pipeline 依赖具体 api 路由。
```

**为什么单向**：API 只做协议转换与编排，业务编排权唯一收口在 `pipeline`，保证状态机不被散落的接口破坏。

---

## 6. 写入动作的并发安全

- 流水线在后台线程跑，`_jobs_lock` 防止同一合同重复起线程。
- 所有写走 `db.tx()`（自动 commit/rollback），WAL + `busy_timeout=8000` 容忍并发。
- 回写幂等：`writeback.idem_key(code, sha256, rule_version)`，重复触发返回既有结果并记 `writeback.skip`。

---

## 相关文档
- `06_architecture_design/系统架构设计文档.md`
- `06_architecture_design/langchain_hitl_arch-v1.0.md`
- `07_data_model/领域数据模型定义.md`
- `08_api_spec/服务边界与接口契约.md`
