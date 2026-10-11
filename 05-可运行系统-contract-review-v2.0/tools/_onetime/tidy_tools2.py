# -*- coding: utf-8 -*-
"""整理第二轮：把已被新工具取代的 v1.0 期脚本也归入 _onetime/，并补全 tools/README。"""
from __future__ import annotations

import io
import os
import shutil

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
TOOLS = os.path.join(ROOT, "tools")
DEST = os.path.join(TOOLS, "_onetime")

SUPERSEDED = [
    ("verify_acceptance.py", "落盘自检结果 —— 已由 run_selftest.py 取代（功能重合）"),
    ("capture_screens.py", "截图采集与渲染校验 —— 已由 verify_render.py 取代（后者探针更严）"),
    ("verify_startup.py", "启动入口验证 —— 已被 audit_frontend.py 覆盖（后者真开页面并量几何）"),
]

log = []
for fn, why in SUPERSEDED:
    src = os.path.join(TOOLS, fn)
    if os.path.exists(src):
        shutil.move(src, os.path.join(DEST, fn))
        log.append("  [MOVE] %s（%s）" % (fn, why))
    else:
        log.append("  [SKIP] %s 不存在" % fn)

# 更新 _onetime/README 的说明表（追加被取代项）
readme_path = os.path.join(DEST, "README.md")
with io.open(readme_path, "r", encoding="utf-8") as f:
    rd = f.read()
add = """
### 追加：被新工具取代的 v1.0 期脚本

| 文件 | 原用途 | 取代者 |
| --- | --- | --- |
| `verify_acceptance.py` | 把验收自检结果落盘 | `tools/run_selftest.py` |
| `capture_screens.py` | 无头截图 + 粗粒度 DOM 校验 | `tools/verify_render.py`（探针更严，且会剥掉脚本源码再断言） |
| `verify_startup.py` | 跑 `run.py` 确认服务能起 | `tools/audit_frontend.py`（真开页面并量几何） |

这三者功能与新工具重合，且写于 v1.0 时期（未考虑 v2.0 的登录态），保留仅作历史参考。
"""
if "被新工具取代的 v1.0 期脚本" not in rd:
    with io.open(readme_path, "w", encoding="utf-8") as f:
        f.write(rd + add)
    log.append("  [WRITE] _onetime/README.md 追加取代关系说明")

# 生成 tools/README.md：把「仍在使用」的脚本逐个登记，避免下次再问「哪个是残留」
KEEP = [
    ("run_all.py", "一键跑齐全部验证（总入口）", "入口"),
    ("check_consistency.py", "改动一致性核查：确认编辑真的落盘（本环境出现过报成功但没改）", "验证"),
    ("check_js.py", "前端脚本语法校验（node --check 抽取内联脚本）", "验证"),
    ("check_conn_noise.py", "客户端早断连不再打 traceback（本地复现 + 断言 stderr 为空）", "验证"),
    ("check_twin_data.py", "孪生看板数据真实性对账（聚合值 vs 逐行统计）", "验证"),
    ("check_assistant.py", "AI 助手专项：意图匹配 / 实时一致性 / 不编造 / 引导可用性", "验证"),
    ("verify_http.py", "HTTP 全链路冒烟（含鉴权与分角色行为）", "验证"),
    ("verify_render.py", "页面渲染与截图校验（探针前剥掉 <script> 源码）", "验证"),
    ("run_selftest.py", "验收自检执行器（结果落盘 verify/）", "验证"),
    ("devcheck.py", "开发期编译 / 导入 / 关键路径自检", "验证"),
    ("bench.py", "性能基准 → verify/bench.{txt,json}", "验证"),
    ("cdp.py", "零依赖 CDP 客户端（手写 WebSocket），前端体检的基础", "体检"),
    ("audit_frontend.py", "前端逐页体检：8 维度 × 6 页（异常/空容器/塌陷/重复 id/模板/溢出/重叠）", "体检"),
    ("audit_interactive.py", "前端交互态体检：弹窗 / 助手面板 / 引导气泡 / 工作台 / 模拟审批", "体检"),
    ("probe_topbar.py", "顶栏几何体检：7 档视口下的行数与每个子元素宽度", "体检"),
    ("reset_data.py", "重置演示数据", "运维"),
    ("test_notify.py", "钉钉通知连通性自测（不需要起本系统）", "运维"),
    ("sync_delivery.py", "把产物同步进交付目录（只增不删、改动先备份）", "交付"),
    ("sync_delivery_after_deploy.py", "上线后回写交付文档并刷新可运行本体", "交付"),
    ("build_samples.py", "示例合同集生成器（构建期，需 python-docx/reportlab/pillow/pypdf）", "构建"),
    ("contracts.py", "示例合同正文文本资源（供 build_samples.py 使用）", "构建"),
    ("stats.py", "统计交付物规模（供文档引用，避免手写数字失真）", "交付"),
    ("debug_pdf.py", "PDF 内部结构调试（定位内容流/字体/CMap 抽取失败）", "排障"),
    ("tidy_tools.py", "整理 tools/ 目录（本文件）", "维护"),
]

rows = "\n".join("| `%s` | %s | %s |" % (f, d, g) for f, d, g in KEEP)
readme = """# tools/ 目录说明

本目录是**开发与验证工具**，不属于运行期依赖（系统本身零第三方依赖）。
一次性变更脚本已归档到 [`_onetime/`](_onetime/README.md)，请勿重跑。

## 常用入口

```bash
python tools/run_all.py                 # 跑齐全部验证（含前端体检，约需数分钟）
python tools/run_all.py http render     # 只跑指定项
python tools/run_all.py front inter     # 只跑前端体检
python tools/run_all.py remote          # 线上实例巡检（公网）
```

## 全部在用的脚本

| 脚本 | 说明 | 分类 |
| --- | --- | --- |
%s

## 三条经验（为什么这些工具长这样）

1. **`check_consistency.py` 是必需的**：本环境的编辑工具出现过「报成功但文件未变」，
   只靠功能测试会带着「以为改了」的假象排查很久。任何一批改动后先跑它。
2. **断言前要剥掉 `<script>` 源码**：`--dump-dom` 输出包含内联脚本，
   探针若用 `class="x"` 这类宽松正则会命中 JS 模板字符串，
   **页面没渲染也会判定通过**（实测踩过）。`verify_render.py` 里的 `body_only()` 就是干这个的。
3. **量几何要用 CDP，不能只看结构**：布局塌陷、元素重叠、横向溢出只有真跑起来才知道。
   `cdp.py` 是零依赖实现，`audit_frontend.py` / `audit_interactive.py` / `probe_topbar.py` 都基于它。

## 交付前建议跑的顺序

```
consistency → js → noise → twin → assistant → selftest → http
→ front → inter → render → bench → devcheck
（线上另跑 remote）
```
""" % rows

with io.open(os.path.join(TOOLS, "README.md"), "w", encoding="utf-8") as f:
    f.write(readme)
log.append("  [WRITE] tools/README.md（登记 %d 个在用脚本）" % len(KEEP))

active = sorted(f for f in os.listdir(TOOLS)
                if f.endswith(".py") and os.path.isfile(os.path.join(TOOLS, f)))
未登记 = [f for f in active if f not in [k for k, _d, _g in KEEP]]
head = ["tools/ 第二轮整理", "=" * 84] + log + ["",
        "顶层在用脚本 %d 个；未登记 %d 个 %s"
        % (len(active), len(未登记), ("：%s" % "、".join(未登记)) if 未登记 else ""),
        "=" * 84]
with io.open(os.path.join(ROOT, "verify", "tools_tidy2.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(head))
print("tidy2 done; unregistered=%d" % len(未登记))
