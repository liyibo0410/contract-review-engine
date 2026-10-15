# 衡鉴 · 合同智能审签系统

> 企业合同全生命周期智能审查平台 —— 规则引擎 × 风险识别 × 审查报告 × 动态孪生

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Production%20Ready-success)
![Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Stdlib%20Only)-orange)
![Version](https://img.shields.io/badge/Version-2.1.7-purple)

</div>

---

## 目录

- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [系统架构](#系统架构)
- [技术栈](#技术栈)
- [快速启动](#快速启动)
- [配置说明](#配置说明)
- [API 文档](#api-文档)
- [项目结构](#项目结构)
- [验收与自检](#验收与自检)
- [功能扩展建议](#功能扩展建议)
- [常见问题](#常见问题)
- [License](#license)

## 项目简介

基于规则引擎与大模型的企业合同智能审查系统，覆盖合同解析、41 条合规规则检查、风险等级评估、审查报告生成与导出等核心能力。

**零第三方依赖**：仅使用 Python 标准库，PDF 报告走系统浏览器无头打印通道，扫描件 OCR 走 Windows 内置 OCR 引擎。

## 核心功能

| 模块 | 能力 |
|------|------|
| **合同审查大盘** | 合同总览、审查进度、风险分布统计 |
| **智能审查工作台** | 逐条规则检查、风险标注、评论协作 |
| **合规规则引擎** | 41 条内置规则、可配置规则库、规则版本管理 |
| **审查报告** | 自动生成 PDF 报告、风险摘要、修改建议 |
| **动态孪生看板** | 实时审查状态可视化、流程追踪 |
| **模拟审批系统** | 飞书/钉钉审批流仿真、评论回写 |
| **智能助手** | 合同条款解读、操作引导、知识库问答 |
| **角色权限** | 多角色登录、会话管理、操作审计 |

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                      前端 UI 层                          │
│  审查大盘 │ 工作台 │ 规则管理 │ 孪生看板 │ 模拟审批      │
└─────────────────────────────────────────────────────────┘
                          │ HTTP/JSON
┌─────────────────────────────────────────────────────────┐
│                    HTTP 服务器层                          │
│              server.py (纯标准库实现)                    │
│         路由分发 │ 静态资源 │ 会话管理 │ gzip            │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│                     业务逻辑层                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ 合同解析 │  │ 规则引擎 │  │ 报告生成 │              │
│  │ parsers  │  │  review  │  │  output  │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ 审批对接 │  │ 通知系统 │  │ 任务调度 │              │
│  │writeback │  │  notify  │  │scheduler │              │
│  └──────────┘  └──────────┘  └──────────┘              │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│                     数据存储层                           │
│              SQLite (data/review.db)                     │
│         合同数据 │ 审查结果 │ 操作日志 │ 规则库          │
└─────────────────────────────────────────────────────────┘
```

## 技术栈

| 层级 | 技术选型 |
|------|---------|
| **后端** | Python 3.12+ · 纯标准库 · SQLite |
| **前端** | 原生 HTML/CSS/JavaScript（零依赖） |
| **设计** | 深色控制台风格 · 玻璃态面板 · 紫色商务配色 |
| **PDF** | 系统浏览器无头打印（Edge/Chrome） |
| **OCR** | Windows 内置 OCR 引擎 |

## 快速启动

### 环境要求

- Python 3.12 或更高版本
- Windows 10/11（PDF 生成与 OCR 依赖系统组件）
- Microsoft Edge 或 Google Chrome（用于 PDF 报告生成）

### 启动步骤

```bash
# 1. 进入项目目录
cd 05-可运行系统-contract-review-v2.0

# 2. 启动服务（推荐使用 run.py）
python run.py

# 或使用 server.py（功能相同）
python server.py
```

服务启动后访问 http://localhost:8770 打开系统。

### 启动参数

```bash
python run.py --port 8780      # 自定义端口
python run.py --reset          # 清空数据库后启动
python run.py --selftest       # 仅运行验收自检
```

### 默认账号

系统内置多个演示账号，可在登录页一键切换：

| 角色 | 权限 |
|------|------|
| 管理员 | 全部功能 |
| 法务专员 | 合同审查、规则管理 |
| 业务经理 | 合同查看、审批流 |
| 访客 | 只读访问 |

## 配置说明

配置文件位于 `config/app.json`，未配置的项使用代码中的默认值。

### 主要配置项

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8770,
    "access_log": false,
    "gzip": true
  },
  "review": {
    "reject_high_count": 2,
    "rectify_total": 1,
    "max_upload_mb": 30,
    "min_text_chars": 30
  },
  "approval": {
    "mode": "mock",
    "base_url": "",
    "write_mode": "live"
  },
  "auth": {
    "enabled": true,
    "demo_switch": true,
    "session_ttl_sec": 28800
  }
}
```

### 配置项说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `server.port` | 8770 | 服务监听端口 |
| `review.reject_high_count` | 2 | 高风险命中数 ≥ 该值则建议拒绝 |
| `review.max_upload_mb` | 30 | 单文件上传上限（MB） |
| `approval.mode` | "mock" | mock=内置模拟；http=对接真实平台 |
| `auth.enabled` | true | 是否启用角色权限 |
| `auth.session_ttl_sec` | 28800 | 会话有效期（秒） |

## API 文档

### 认证相关

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/auth/me` | 获取当前用户信息 |
| POST | `/api/auth/login` | 用户登录 |
| POST | `/api/auth/logout` | 用户登出 |
| POST | `/api/auth/switch` | 切换演示身份 |
| GET | `/api/auth/accounts` | 获取可用账号列表 |

### 合同管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cases` | 获取合同列表 |
| GET | `/api/cases/{id}` | 获取合同详情 |
| POST | `/api/upload` | 上传合同文件 |
| GET | `/api/cases/{id}/report` | 生成审查报告 |
| GET | `/api/results/{id}` | 获取审查结果 |

### 规则引擎

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/rules` | 获取规则列表 |
| GET | `/api/rules/{category}` | 获取分类规则 |

### 系统监控

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/stats` | 统计数据 |
| GET | `/api/metrics` | 性能指标 |
| GET | `/api/audit` | 操作审计日志 |
| GET | `/api/twin` | 孪生看板数据 |

### 智能助手

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/assistant/ask` | 智能问答 |
| GET | `/api/assistant/tour` | 操作引导 |
| GET | `/api/assistant/kb` | 知识库查询 |

### 通知与调度

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/notify/status` | 通知系统状态 |
| GET | `/api/notify/recent` | 最近通知 |
| POST | `/api/notify/test` | 发送测试通知 |
| GET | `/api/scheduler` | 调度器状态 |
| POST | `/api/scheduler/config` | 配置调度任务 |
| POST | `/api/scheduler/run` | 手动触发调度 |

## 项目结构

```
contract-review-engine/
├── 05-可运行系统-contract-review-v2.0/   # 主力开发版本
│   ├── app/                              # 后端应用
│   │   ├── server.py                     # HTTP 服务器
│   │   ├── parsers.py                    # 合同解析（PDF/DOCX/OCR）
│   │   ├── review.py                     # 规则引擎
│   │   ├── output.py                     # 报告生成
│   │   ├── writeback.py                  # 审批对接
│   │   ├── notify.py                     # 通知系统
│   │   ├── scheduler.py                  # 任务调度
│   │   ├── auth.py                       # 认证授权
│   │   ├── assistant.py                  # 智能助手
│   │   ├── store.py                      # 数据存储
│   │   └── config.py                     # 配置加载
│   ├── ui/                               # 前端页面
│   │   ├── index.html                    # 合同审查大盘
│   │   ├── workbench.html                # 智能审查工作台
│   │   ├── rules.html                    # 合规规则维护
│   │   ├── twin.html                     # 动态孪生看板
│   │   ├── mock.html                     # 模拟审批系统
│   │   ├── login.html                    # 登录页
│   │   └── assets/
│   │       ├── app.css                   # 主样式（紫色商务风）
│   │       └── fx.css                    # 动效组件
│   ├── config/                           # 配置文件
│   │   └── app.json
│   ├── data/                             # 数据目录
│   │   ├── review.db                     # SQLite 数据库
│   │   ├── uploads/                      # 上传文件
│   │   └── reports/                      # 生成的报告
│   ├── samples/                          # 示例合同
│   ├── verify/                           # 验收脚本
│   ├── run.py                            # 启动入口
│   └── selftest.py                       # 自检脚本
├── 08-衡鉴v2.0-完整交付/                 # 最终交付版本
│   ├── 01-可运行系统/
│   ├── 02-交付文档/
│   └── 06-验证证据/
├── 00-交付总览.md                        # 项目总览文档
├── 02-技术方案/                          # 技术设计文档
├── 03-演示与验收/                        # 演示材料
└── README.md                             # 本文件
```

## 验收与自检

### 运行完整验收

```bash
cd 05-可运行系统-contract-review-v2.0
python verify/run_all.py
```

### 快速自检

```bash
python run.py --selftest
```

自检项目包括：

- 服务启动与端口监听
- 数据库连接与初始化
- 核心 API 可用性
- 合同解析流程
- 规则引擎执行
- PDF 报告生成
- 审批对接模拟

## 功能扩展建议

1. **大模型深度集成** — 接入 LLM 实现合同条款智能解读与修改建议生成
2. **多人协作审批** — WebSocket 实时同步，支持多部门并行审批流
3. **合同模板库** — 预设常用合同模板，一键生成标准合同
4. **电子签章集成** — 对接 e签宝、法大大等电子签章平台
5. **多语言支持** — 英文合同审查与跨语言条款对比
6. **移动端适配** — 响应式设计与小程序支持

## 常见问题

### Q: 启动时报错端口被占用

修改 `config/app.json` 中的 `server.port`，或使用启动参数：

```bash
python run.py --port 8780
```

### Q: PDF 报告生成失败

确保系统已安装 Microsoft Edge 或 Google Chrome，PDF 生成依赖浏览器无头打印功能。

### Q: OCR 识别不准确

- 确保上传的扫描件清晰
- 调整 `review.ocr_min_confidence` 阈值
- 对于低质量文档，建议手动录入

### Q: 如何对接真实审批系统

1. 修改 `config/app.json`：`approval.mode` 改为 `"http"`
2. 配置 `approval.base_url` 为审批系统 API 地址
3. 实现 `writeback.py` 中的对接接口

## License

MIT

---

<div align="center">

**衡鉴** —— 明察秋毫，鉴往知来

</div>
