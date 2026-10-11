# -*- coding: utf-8 -*-
"""收尾整理：把一次性变更脚本归入 tools/_onetime/，并写一份说明。

为什么"移"而不是"删"：
  * 这些脚本记录了本次改造**每一步是怎么改的**（包括踩过的坑），是可追溯的变更史料；
  * 它们已经执行完毕，字符串上下文已变，再次执行会失败 —— 所以需要明确的
    "不要重跑"提示，而不是散落在 tools/ 里让人误以为是常用工具。

移动是可逆的、有日志的；本脚本不做任何删除。
"""
from __future__ import annotations

import io
import os
import shutil

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
TOOLS = os.path.join(ROOT, "tools")
DEST = os.path.join(TOOLS, "_onetime")
os.makedirs(DEST, exist_ok=True)

# 一次性脚本（按用途归类）
ONETIME = [
    # 早期 UI 收尾补丁
    "patch_ui.py", "patch_http.py", "patch_render.py", "patch_render2.py",
    "patch_render3.py", "patch_render4.py",
    # v2.0.1 界面整改
    "patch_v201_backend.py", "patch_v201_front1.py",
    # v2.1 助手
    "patch_assistant_backend.py", "patch_assistant_verify.py",
    "patch_assistant_integrate.py", "patch_pet_mount.py", "fix_assistant1.py",
    # v2.1.1 前端细节
    "fix_front1.py", "fix_front2.py",
    # 版本号去硬编码
    "patch_version.py",
    # 一次性诊断探针（已被 audit_* / probe_topbar 取代）
    "probe_http_login.py", "probe_twin.py",
]

log = []
moved = 0
for fn in ONETIME:
    src = os.path.join(TOOLS, fn)
    if os.path.exists(src):
        shutil.move(src, os.path.join(DEST, fn))
        log.append("  [MOVE] tools/%s → tools/_onetime/%s" % (fn, fn))
        moved += 1
    else:
        log.append("  [SKIP] tools/%s 不存在（可能已移动）" % fn)

# 把「仍在使用」的脚本留在 tools/ 顶层，这里列清楚
KEEP = [
    ("run_all.py", "一键跑齐全部验证（入口）"),
    ("check_consistency.py", "改动一致性核查：确认编辑真的落盘"),
    ("check_js.py", "前端脚本语法校验（node --check）"),
    ("check_conn_noise.py", "客户端早断连不再打 traceback"),
    ("check_twin_data.py", "孪生看板数据真实性对账"),
    ("check_assistant.py", "AI 助手专项校验（意图/数据/不编造/引导）"),
    ("cdp.py", "零依赖 CDP 客户端（供前端体检用）"),
    ("audit_frontend.py", "前端逐页体检（8 维度 × 6 页）"),
    ("audit_interactive.py", "前端交互态体检"),
    ("probe_topbar.py", "顶栏几何体检（7 档视口）"),
    ("verify_http.py", "HTTP 全链路冒烟"),
    ("verify_render.py", "页面渲染与截图校验"),
    ("run_selftest.py", "验收自检执行器"),
    ("devcheck.py", "开发期编译/导入/关键路径自检"),
    ("bench.py", "性能基准"),
    ("reset_data.py", "重置演示数据"),
    ("test_notify.py", "钉钉通知连通性自测"),
    ("make_samples.py", "生成示例合同集"),
    ("export_docs.py", "导出交付文档"),
]

readme = """# tools/_onetime · 一次性变更脚本（存档，不要重跑）

本目录存放**已经执行完毕的一次性脚本**。它们记录了本次改造每一步是怎么改的，
以及当时踩过的坑，属于可追溯的变更史料。

> ⚠️ **不要重新执行**：这些脚本按「改前字符串 → 改后字符串」做替换，
> 而目标文件早已变过，重跑会全部 `MISS` 或产生错误结果。
> 需要了解改动内容，请看 `CHANGELOG.md` 与 `docs/v2.1.1-前端细节修复说明.md`。

## 为什么保留而不是删除

1. **可追溯**：审计时能回答「某个字符串是被哪一步改掉的」；
2. **可复现**：同类批改可以照抄写法（尤其是「先备份再改」的骨架）；
3. **有教训**：这批脚本的存在本身说明了一个问题 ——
   在某个环境里编辑工具出现过「报成功但文件没变」，
   所以才需要 `tools/check_consistency.py` 这种逐项断言来兜底。

## 目录内容

| 文件 | 当时的用途 |
| --- | --- |
| `patch_ui.py` | 早期界面收尾（挂载角色栏、给按钮加 data-cap） |
| `patch_http.py` / `patch_render*.py` | 修验证脚本自身的缺陷（重定向跟随、探针命中脚本源码、手机端截图） |
| `patch_v201_backend.py` / `patch_v201_front1.py` | v2.0.1：新增 /api/twin、统计卡下钻、顶栏精简 |
| `patch_assistant_*.py` / `patch_pet_mount.py` / `fix_assistant1.py` | v2.1：助手接口接线、引导选择器修正、挂载到 6 个页面 |
| `fix_front1.py` / `fix_front2.py` | v2.1.1：顶栏换行回归、统计卡提示位置、助手不再主动弹窗 |
| `patch_version.py` | 消除各脚本里硬编码的版本号（改为从 app.__version__ 读） |
| `probe_http_login.py` / `probe_twin.py` | 一次性诊断探针（已被 audit_* 与 probe_topbar 取代） |
"""

with io.open(os.path.join(DEST, "README.md"), "w", encoding="utf-8") as f:
    f.write(readme)
log.append("  [WRITE] tools/_onetime/README.md")

active = sorted(f for f in os.listdir(TOOLS)
                if f.endswith(".py") and os.path.isfile(os.path.join(TOOLS, f)))
head = ["tools/ 目录整理结果", "=" * 84,
        "移入 _onetime/：%d 个" % moved, ""]
head += log
head += ["", "-" * 84, "仍在使用（%d 个）：" % len(active)]
missing_doc = [f for f in active if f not in [k for k, _ in KEEP]]
for f in active:
    desc = dict(KEEP).get(f, "（未登记说明）")
    head.append("  %-24s %s" % (f, desc))
if missing_doc:
    head += ["", "提示：以下脚本未登记说明，建议补进 KEEP 清单：%s" % "、".join(missing_doc)]
head += ["", "=" * 84,
         "安全声明：本脚本只做移动与新增说明文件，未删除任何内容。"]

with io.open(os.path.join(ROOT, "verify", "tools_tidy.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(head))
print("tools tidy done: moved %d" % moved)
