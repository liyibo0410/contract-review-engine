# -*- coding: utf-8 -*-
"""两处收尾：
  1) verify_http 的 /api/selftest 检查改为防御式 —— 先看首字节是不是 JSON，
     失败时把响应体片段打进报告（原来是直接 json.loads，偶发非 JSON 就整个脚本崩掉，
     后面所有检查都不再执行 —— 一次抖动丢掉半份报告不值得）；
  2) verify_render 的正则改为「类名后面可以跟别的类」——
     动效系统会给卡片追加 .cfx/.cfx-lift/.cfx-beam，原来要求 class 属性正好以
     某个类结尾的正则（如 class="mode-col"）就匹配不上了。
"""
import io
import os
import re

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    for old, new in pairs:
        if old in c:
            c = c.replace(old, new, 1)
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:50]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:50]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ---------------- verify_http：自检检查防御式 + 带诊断
patch("tools/verify_http.py", [
    ('''code, _, body = call("GET", "/api/selftest")
sl = json.loads(body)["data"]
rec("GET /api/selftest 页面一键自检",
    code == 200 and sl["passed"],
    "%d / %d 项通过，%d ms" % (sl["passed_count"], sl["total"], sl["elapsed_ms"]))''',
     '''# 防御式解析：这一步要跑整份自检（约 8s），偶发非 JSON 响应不该让整个冒烟脚本崩掉
code, _, body = call("GET", "/api/selftest", timeout=180)
sl, why = None, ""
if body[:1] != b"{":
    why = "响应体非 JSON：%r" % body[:140]
else:
    try:
        sl = json.loads(body)["data"]
    except Exception as exc:                                         # noqa: BLE001
        why = "解析失败：%s" % exc
rec("GET /api/selftest 页面一键自检",
    bool(sl and sl.get("passed")),
    ("%d / %d 项通过，%d ms" % (sl["passed_count"], sl["total"], sl["elapsed_ms"]))
    if sl else ("HTTP %s ｜ %s" % (code, why or "无数据")))'''),
])

# ---------------- verify_render：类名后面允许跟其它类
p = os.path.join(ROOT, "tools/verify_render.py")
with io.open(p, "r", encoding="utf-8") as f:
    src = f.read()

# 把 class="X" 形式统一放宽为 class="X[^"]*"（X 为纯类名，不含正则元字符）
RELAX = [
    ('class="role-item"', 'class="role-item'),
    ('class="stat [^"]*clickable"', 'class="stat [^"]*clickable'),
    ('class="mode-col"', 'class="mode-col'),
    ('class="mode-note"', 'class="mode-note'),
    ('class="wb"', 'class="wb'),
    ('class="rule-row"', 'class="rule-row'),
    ('class="src"', 'class="src'),
    ('class="rb"', 'class="rb'),
    ('class="ep"', 'class="ep'),
    ('class="nav"', 'class="nav'),
    ('class="brand-logo"', 'class="brand-logo'),
    ('class="pet-chip"', 'class="pet-chip'),
    ('class="pet-prog"', 'class="pet-prog'),
]
n = 0
for old, new in RELAX:
    if old in src:
        src = src.replace(old, new)
        n += 1
    else:
        log.append("  MISS verify_render :: %s" % old)
with io.open(p, "w", encoding="utf-8") as f:
    f.write(src)
log.append("  ok   tools/verify_render.py :: 放宽 %d 处类名正则（允许类名后跟其它类）" % n)

# ---------------- 一致性核查补两条
patch("tools/check_consistency.py", [
    ('    ("ui/assets/fx.css", "卡片动效系统（v2.1.3）"),',
     '    ("ui/assets/fx.css", "卡片动效系统（v2.1.3）"),\n'
     '    ("ui/assets/fx.css", "@keyframes cfxSweep"),\n'
     '    ("ui/assets/app.js", "function cfxDecorate(el)"),\n'
     '    ("tools/probe_cfx.py", "卡片动效专项探针"),'),
])

with io.open(os.path.join(ROOT, "verify", "cfx_final.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("cfx final patch done")
