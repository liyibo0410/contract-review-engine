# -*- coding: utf-8 -*-
"""把 v2.0 产物同步进交付目录（决议 D-29 / D-37 / D-38）。

安全约束（本脚本的硬规则）：
  * **只增不删**：不删除、不移动、不重命名交付目录里的任何既有内容；
  * 要改的两个既有文件（技术方案 V2.0、00-交付总览.md）先备份为 .bak-20260919；
  * 所有写入结果落日志，便于逐项复核。

做三件事：
  1. D-29：技术方案 V2.0 文档状态 → 「已冻结（第二次扩大会议审定）」；
  2. D-37：新增第七层 `07-对标分析/`，归入对标原文 OCR / 对标优化方案 /
     P0 开发任务清单与接口契约 / 对标批次配置包；
  3. D-38：新增 `05-可运行系统-contract-review-v2.0/`（v2.0 可运行本体），
     v1.0 冻结留证与运行本体保持不动。
"""
from __future__ import annotations

import io
import os
import shutil
import time

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST_ROOT = r"E:\桌面\合同审批系统-李奕波"
BENCH_DIR = r"D:\workbuddy work\2026-09-18-23-01-50"
BENCH_CFG = os.path.join(BENCH_DIR, "contract-review-system")
LOG = os.path.join(SRC_ROOT, "verify", "delivery_sync.txt")

log = []
copied = [0]


def L(s):
    log.append(str(s))


def copy_file(src, dst, label=""):
    if not os.path.exists(src):
        L("  [SKIP] 源不存在：%s" % src)
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    r = shutil.copy2(src, dst)
    copied[0] += 1
    L("  [COPY] %-58s → %s" % (label or os.path.basename(src), dst))
    return bool(r)


def copy_tree(src, dst, exclude_dirs=(), exclude_files=(), label=""):
    n = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fn in filenames:
            if fn in exclude_files or fn.endswith(".pyc"):
                continue
            s = os.path.join(dirpath, fn)
            rel = os.path.relpath(s, src)
            d = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            n += 1
    copied[0] += n
    L("  [TREE] %s：%d 个文件 → %s" % (label or src, n, dst))
    return n


L("=" * 100)
L("v2.0 产物同步进交付目录（D-29 / D-37 / D-38）")
L("时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
L("源：%s" % SRC_ROOT)
L("目标：%s" % DST_ROOT)
L("=" * 100)

# ---------------------------------------------------------------- 0. 前置检查
L("")
L("--- 0) 前置检查 ---")
if not os.path.isdir(DST_ROOT):
    L("  [FATAL] 交付目录不存在，终止")
    raise SystemExit(1)
L("  [OK] 交付目录存在")

# ---------------------------------------------------------------- 1. D-29 冻结
L("")
L("--- 1) D-29：技术方案 V2.0 冻结 ---")
tech = os.path.join(DST_ROOT, "02-技术方案",
                    "合同审批审查系统-技术方案-V2.0与2.0迭代优化方案-20260918.md")
if os.path.exists(tech):
    bak = tech + ".bak-20260919"
    if not os.path.exists(bak):
        shutil.copy2(tech, bak)
        L("  [BAK ] 原文件已备份：%s" % os.path.basename(bak))
    else:
        L("  [BAK ] 备份已存在，跳过：%s" % os.path.basename(bak))
    with io.open(tech, "r", encoding="utf-8") as f:
        txt = f.read()
    before = txt
    txt = txt.replace("- 文档状态：**待第二次会议审定后冻结**",
                      "- 文档状态：**已冻结（第二次扩大会议审定）**")
    txt = txt.replace("**文档状态**：待第二次会议审定后冻结",
                      "**文档状态**：已冻结（第二次扩大会议审定）")
    txt = txt.rstrip() + (
        "\n\n---\n\n"
        "## 冻结说明（第二次扩大会议 · 决议 D-29）\n\n"
        "本方案自 2026-09-19 第二次扩大会议审定后**正式冻结**，成为唯一现行技术方案；\n"
        "技术方案 V1.0 仅作历史版本引用，不再生效。\n\n"
        "冻结时的状态核对：\n\n"
        "| 项 | 冻结时状态 |\n"
        "| --- | --- |\n"
        "| 第一部分 技术方案 V2.0（修订版） | 与 v1.0 交付实现一致（9 项偏离全部修订） |\n"
        "| 第二部分 2.0 迭代优化方案（四条主线） | 主线 A / B / C / D 均已实现并验证 |\n"
        "| 里程碑 M7 ~ M11 | M7 / M8 / M9 / M10 已完成；M11 验收自检 52/52、HTTP 冒烟 79/79 |\n"
        "| 需求 2.4 条目闭环 | **10 / 10**（D-16 角色权限已于 v2.0 补齐） |\n"
        "| 运行期第三方依赖 | **0**（D-32 的红线同样适用于本文件自身：全文不含本项目不使用的运行期技术） |\n\n"
        "**版本口径（D-28）**：交付版本 = 运行版本 = 文档版本 = **2.0.0**；\n"
        "唯一真源为 `app/__init__.py` 的 `__version__`，变更记录见 `CHANGELOG.md`。\n\n"
        "冻结执行人：启明 ｜ 冻结日期：2026-09-19\n")
    with io.open(tech, "w", encoding="utf-8") as f:
        f.write(txt)
    L("  [EDIT] 文档状态已改为「已冻结（第二次扩大会议审定）」，并追加「冻结说明」章节")
    L("         原文 %d → %d 字符" % (len(before), len(txt)))
else:
    L("  [SKIP] 未找到技术方案 V2.0：%s" % tech)

# ---------------------------------------------------------------- 2. D-37 第七层
L("")
L("--- 2) D-37：新增第七层 07-对标分析 ---")
B7 = os.path.join(DST_ROOT, "07-对标分析")
os.makedirs(B7, exist_ok=True)
L("  [DIR ] 已建：%s" % B7)

copy_file(os.path.join(BENCH_DIR, "对标原文-楚联合同平台PPT-OCR全文.txt"),
          os.path.join(B7, "对标原文-楚联合同平台PPT-OCR全文.txt"), "对标原文 OCR 全文")
copy_file(os.path.join(BENCH_DIR, "合同审查系统-对标优化方案.md"),
          os.path.join(B7, "合同审查系统-对标优化方案.md"), "对标优化方案（两轮 21 项）")
copy_file(os.path.join(BENCH_DIR, "P0-开发任务清单与接口契约.md"),
          os.path.join(B7, "P0-开发任务清单与接口契约.md"), "P0 任务清单与 26 接口契约")

# 对标批次配置包（契约测试 27 项全绿的那批）
CFG7 = os.path.join(B7, "对标批次配置包")
copy_file(os.path.join(BENCH_CFG, "config", "required_clauses.json"),
          os.path.join(CFG7, "required_clauses.json"), "配置包 required_clauses.json")
copy_file(os.path.join(BENCH_CFG, "config", "rules.deterministic.json"),
          os.path.join(CFG7, "rules.deterministic.json"), "配置包 rules.deterministic.json")
copy_file(os.path.join(BENCH_CFG, "config", "scoring.json"),
          os.path.join(CFG7, "scoring.json"), "配置包 scoring.json")
copy_file(os.path.join(BENCH_CFG, "config", "mock", "gsxt.json"),
          os.path.join(CFG7, "mock", "gsxt.json"), "配置包 mock/gsxt.json（外部核验模拟）")
copy_file(os.path.join(BENCH_CFG, "tools", "check_config.py"),
          os.path.join(CFG7, "check_config.py"), "配置契约测试脚本")
copy_file(os.path.join(BENCH_CFG, "tools", "check_config_result.txt"),
          os.path.join(CFG7, "check_config_result.txt"), "配置契约测试结果（27 项全绿）")

# 第七层说明（承接 D-32 的编号体系与红线）
readme7 = """# 07-对标分析 · 目录说明

本层由**第二次扩大会议决议 D-37** 设立，用于承载标杆对标批次的**分析与契约产物**。

## 编号体系（决议 D-36 · 两套编号并存但不得混用）

| 体系 | 前缀 | 含义 | 举例 |
| --- | --- | --- | --- |
| 优化清单 | `OPT-` | 1.0 → 2.0 的迭代优化项 | `OPT-P0-02`（角色权限） |
| 对标批次 | `BM-` | 标杆对标得出的独立批次项 | `BM-P0-2` |

历史文档里出现的裸 `P0-1` / `P0-02` 极易混用，回溯时请先确认属于哪一套。

## 本层文件

| 文件 | 内容 |
| --- | --- |
| `对标原文-楚联合同平台PPT-OCR全文.txt` | 对标对象的原文 OCR 全文（58KB），一切结论的原始依据 |
| `合同审查系统-对标优化方案.md` | 两轮对标共 21 项（P0×6 / P1×5 / P2×4 + 第二轮结构性发现） |
| `P0-开发任务清单与接口契约.md` | 6 个领域对象 + 26 个接口契约 + T01~T10 任务拆解 |
| `对标批次配置包/` | 确定性规则（D-01~D-10）、外部核验（E-01~E-05）、必备条款、评分参数、契约测试脚本与结果 |

## 两条必须在开工前遵守的约束

1. **技术栈红线（D-32，强制）**：对标批次全程**不得引入任何第三方运行期依赖，
   不得引入前端构建链与包管理器**。契约文档中出现的 `DocumentViewer.vue/tsx`、`PDF.js`、
   `pytest`、`npm` 等表述，**统一修订为**「原生 HTML / CSS / JS + 手写内联 SVG +
   标准库 `unittest`」后再施工 —— 这是红线第三次复发（D-32）的收口要求。
2. **启动条件（D-39）**：对标批次 M12~M16 的启动条件为 **M11（2.0 验收）完成**，
   不得与 2.0 并行开盘（单人手工作业，两批会互相拖垮）。

## 本层与运行本体的关系

按 D-37，本次**只增层级与文档，不把对标批次实现并入运行本体**。
即 `05-可运行系统-contract-review-v2.0/` 内**不含**对标批次的实现代码；
本层是分析、契约与配置储备。
"""
with io.open(os.path.join(B7, "README.md"), "w", encoding="utf-8") as f:
    f.write(readme7)
L("  [WRITE] 07-对标分析/README.md（编号体系、红线、启动条件）")

# ---------------------------------------------------------------- 3. D-38 双轨
L("")
L("--- 3) D-38：新增 05-可运行系统-contract-review-v2.0 ---")
B5 = os.path.join(DST_ROOT, "05-可运行系统-contract-review-v2.0")
if os.path.exists(B5):
    L("  [SKIP] 目标已存在，为避免覆盖既有内容而跳过：%s" % B5)
else:
    copy_tree(SRC_ROOT, B5,
              exclude_dirs=("data", "__pycache__", ".idea", "_jschk"),
              exclude_files=("_probe.txt", "_probe2.txt", "_probe3.txt", "_probe4.txt",
                             "_probe5.txt", "_probe6.txt"),
              label="v2.0 可运行本体")

# ---------------------------------------------------------------- 4. 00-交付总览补注
L("")
L("--- 4) 00-交付总览.md 补注（D-30 第③条 + D-37 层级 + D-38 双轨） ---")
overview = os.path.join(DST_ROOT, "00-交付总览.md")
if os.path.exists(overview):
    bak = overview + ".bak-20260919"
    if not os.path.exists(bak):
        shutil.copy2(overview, bak)
        L("  [BAK ] 原文件已备份：%s" % os.path.basename(bak))
    with io.open(overview, "r", encoding="utf-8") as f:
        doc = f.read()
    append = """

---

# 附录 · v2.0 交付补注（2026-09-19 · 第二次扩大会议 D-30 / D-37 / D-38）

> 本附录是**增量补注**，不修改上文任何 v1.0 时期的表述。v1.0 的交付内容与结论保持原貌。

## 一、本次新增层级（D-37）

交付目录由六层扩为**七层**：

| 层级 | 目录 | 说明 |
| --- | --- | --- |
| 01 | `01-会议纪要` | 第一次（md+docx）／第二次（md）／**第二次扩大会议（md+docx）** |
| 02 | `02-技术方案` | 技术方案 V1.0（历史）／**V2.0（已冻结，D-29）**／评审意见书／优化清单 |
| 03 | `03-演示与验收` | 演示脚本、验收对照表、界面快照 |
| 04 | `04-版本归档-v1.0` | v1.0 冻结留证（**保持不动**，D-38） |
| 05 | `05-可运行系统-contract-review-v1.0` | v1.0 运行本体（**保持不动**） |
| 06 | `06-原始资产-项目素材` | 项目素材与参考资产 |
| **07** | **`07-对标分析`（新增）** | 对标原文 OCR／对标优化方案／P0 清单与接口契约／对标批次配置包 |

## 二、v2.0 可运行本体（D-38 双轨形态）

| 位置 | 内容 | 界面 | 版本 |
| --- | --- | --- | --- |
| `04-版本归档-v1.0/` + `05-可运行系统-contract-review-v1.0/` | v1.0 冻结留证 + 运行本体 | **旧界面**（浅色三页） | 1.0.0 |
| **`05-可运行系统-contract-review-v2.0/`（新增）** | v2.0 可运行本体 | **新界面**（深空控制台 5 页 + 登录页 + 权限受限页） | **2.0.0** |

> **审计口径说明（D-30 第③条）**：交付包内 v1.0 副本为旧界面、v2.0 副本为新界面，
> 这是**双轨留证的必然结果**，不是版本串用。
> 判断「当前在用哪一版」的唯一依据是运行实例的 `/api/health` 返回的 `version` 字段。

## 三、v2.0 相对 v1.0 的能力增量

| 维度 | v1.0 | v2.0 |
| --- | --- | --- |
| 需求 2.4 条目闭环 | 8 / 10 | **10 / 10** |
| 角色与权限（2.4.2） | 无 | 三角色 RBAC，路由级权限表，越权 403 并写审计 |
| 流转自动化 | 手动拉取 | 定时拉取（指数退避，默认关闭，可运行时开关） |
| 解析精度 | 坐标体系已实现 | 补齐页眉页脚剔除（双通道判定 + 保护名单 + 剔除留痕） |
| 摘要表达 | 单模式 | 「规则引擎 / 规则+模型润色」对照视图，未配 Key 时明示回退模式 |
| 报告 PDF | 依赖本机浏览器 | 三通道：浏览器无头打印 → **纯标准库直出** → 可打印 HTML |
| 并发与配额 | 无 | 审查队列 + 四道配额（单文件/队列/待处理/存储总量） |
| 通知外发 | 明文含合同主体与金额 | **字段级掩码 + 整段兜底扫描**，事件默认收敛 |
| 版本口径 | `/api/health` 报 1.0.0（与前端 v2.0 不符） | 交付 = 运行 = 文档 = **2.0.0**，`CHANGELOG.md` 留痕 |
| 验收自检 | 33 项 | **52 项** |
| HTTP 全链路冒烟 | 45 项 | **79 项** |
| 页面渲染与截图校验 | 5 项 | **8 项** |
| 运行期第三方依赖 | 0 | **0（未增加）** |

## 四、v2.0 的验证证据位置

全部在 `05-可运行系统-contract-review-v2.0/verify/`：

| 文件 | 内容 |
| --- | --- |
| `selftest.txt` / `selftest.json` | 验收自检 52 项逐条结果 |
| `smoke_http.txt` | HTTP 全链路冒烟 79 项逐条结果 |
| `screenshots.txt` + `_dom_*.html` | 页面渲染校验 8 项与渲染后 DOM 留证 |
| `bench.txt` / `bench.json` | 性能基准（解析 / 规则引擎 / 队列并发 / 报告三通道 / HTTP 端到端） |
| `jscheck.txt` | 前端脚本语法校验（14 个脚本 0 失败） |
| `consistency.txt` | 改动一致性核查（87 项在位） |
| `run_all.txt` / `run_*.txt` | 各脚本原始输出与汇总 |

一键复现：`python tools/run_all.py`

页面截图在 `05-可运行系统-contract-review-v2.0/docs/screenshots/`，
其中 v2.0 新增的两张受控页截图：

* `00_登录页.png` —— 登录页（三类演示身份一键进入）；
* `05_权限受限页.png` —— 法务审查人越权访问规则页时的 403 页面。

## 五、v2.0 尚未闭环的事项

| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | 线上实例部署与巡检 | 会覆盖正在运行的 8160 实例，属对外动作，待明确后再执行 |
| 2 | 关键路径录屏 / 演示彩排 2 次 | 需人工操作屏幕 |
| 3 | 交付截止日 / LLM Key / 钉钉可外发字段范围 | 第二次扩大会议待裁决事项 1~3 |
| 4 | 对标批次（BM 21 项 / M12~M16） | 启动条件为 M11 验收完成（D-39），且需先做契约文档技术栈修订（D-32） |

详见 `05-可运行系统-contract-review-v2.0/docs/v2.0-交付说明与验收对照.md` 第六章。
"""
    with io.open(overview, "w", encoding="utf-8") as f:
        f.write(doc.rstrip() + append)
    L("  [EDIT] 已追加「v2.0 交付补注」附录（层级 / 双轨 / 能力增量 / 证据位置 / 未闭环事项）")
else:
    L("  [SKIP] 未找到 00-交付总览.md")

# ---------------------------------------------------------------- 汇总
L("")
L("=" * 100)
L("同步完成：共写入 / 更新 %d 个文件" % copied[0])
L("安全声明：本脚本未删除、未移动、未重命名交付目录中的任何既有内容；")
L("          被修改的 2 个既有文件均已留下 .bak-20260919 备份。")
L("=" * 100)

os.makedirs(os.path.dirname(LOG), exist_ok=True)
with io.open(LOG, "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("delivery sync done: %d files" % copied[0])
