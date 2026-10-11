# -*- coding: utf-8 -*-
"""同步探针与一致性检查到新的类名与动效"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def sub(rel, old, new):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    if old in c:
        c = c.replace(old, new)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(c)
        log.append("  ok   %s :: %s" % (rel, old[:60]))
    else:
        log.append("  MISS %s :: %s" % (rel, old[:60]))


# 探针：.gauge → .meter（类名已改）
sub("tools/audit_frontend.py", '"#gauges", ".gauge"', '"#gauges", ".meter"')
sub("tools/audit_frontend.py", '".gauge", "#opsKv"', '".meter", "#opsKv"')
sub("tools/verify_render.py", '("真实仪表", r\'class="gauge \')', '("真实仪表", r\'class="meter \')')
sub("tools/probe_engine_panel.py", "var g=p.querySelectorAll('.gauge')", "var g=p.querySelectorAll('.meter')")
sub("tools/check_consistency.py", '(".gauge.idle .bar i")', '(".meter.idle .bar i")')

# 一致性：补动效系统的断言
p = os.path.join(ROOT, "tools/check_consistency.py")
with io.open(p, "r", encoding="utf-8") as f:
    c = f.read()
ADD = '''    ("ui/assets/fx.css", "卡片动效系统（v2.1.3）"),
    ("ui/assets/fx.css", "@keyframes cfxIn"),
    ("ui/assets/fx.css", "@keyframes cfxBeam"),
    ("ui/assets/fx.css", "@keyframes cfxTick"),
    ("ui/assets/fx.css", ".cfx-lift:hover"),
    ("ui/assets/fx.css", ".cfx-enter, .cfx-tick, .cfx-beam::before { animation: none !important; }"),
    ("ui/assets/app.js", "function cfxEnter(scope)"),
    ("ui/assets/app.js", "function cfxChanged(key, val)"),
    ("ui/assets/app.js", "motion: { enter: cfxEnter, changed: cfxChanged }"),
    ("ui/index.html", "CR.motion.enter(CR.$('#drillBody'))"),
    ("ui/workbench.html", "CR.motion.enter(CR.$('#tabBody'))"),
    ("ui/twin.html", "CR.motion.changed('kpi|'"),
    ("ui/mock.html", "CR.motion.enter(CR.$('#detail'))"),
    ("ui/assets/app.css", "水位框类名是 **.meter** 而不是 .gauge"),
    ("ui/twin.html", 'class="meter '),
    ("ui/twin.html", 'class="meter-row"'),
]
'''
c = c.replace('    ("tools/check_assistant.py", "系统状态里的通知与脱敏读的是真实值"),\n]',
              '    ("tools/check_assistant.py", "系统状态里的通知与脱敏读的是真实值"),\n' + ADD)
with io.open(p, "w", encoding="utf-8") as f:
    f.write(c)
log.append("  ok   tools/check_consistency.py :: 补动效断言")

with io.open(os.path.join(ROOT, "verify", "cfx_probe_patch.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(log))
print("cfx probe patch done")
