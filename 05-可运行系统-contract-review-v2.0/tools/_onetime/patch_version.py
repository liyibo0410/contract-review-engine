# -*- coding: utf-8 -*-
"""消除版本号在工具脚本里的硬编码（承接 D-28：唯一真源是 app/__init__.py）。

顺带把「版本号是否正确」的断言从写死 2.0.0 改成与运行版本比对 ——
否则每次发版都要手动改一堆脚本，迟早漏一个。
"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    for old, new in pairs:
        if old in c:
            c = c.replace(old, new, 1)
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:58]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:58]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ---------------------------------------------------------------- verify_http
patch("tools/verify_http.py", [
    ("""rec("健康检查版本号与交付版本一致（禁止版本失真）",
    h.get("version") == "2.0.0", "health.version=%s" % h.get("version"))""",
     """import app as _app                                                       # noqa: E402
rec("健康检查版本号与交付版本一致（禁止版本失真）",
    h.get("version") == _app.__version__,
    "health.version=%s ｜ app.__version__=%s" % (h.get("version"), _app.__version__))"""),
])

# ---------------------------------------------------------------- 远程巡检
patch("deploy/verify_remote_v2.py", [
    ('''rec("版本号与交付版本一致（2.0.0，禁止版本失真）",
    d.get("version") == "2.0.0", "version=%s" % d.get("version"))''',
     '''sys.path.insert(0, ROOT)
import app as _app                                                       # noqa: E402
rec("版本号与交付版本一致（禁止版本失真）",
    d.get("version") == _app.__version__,
    "线上 version=%s ｜ 交付版本=%s" % (d.get("version"), _app.__version__))'''),
    ('log.append("合同智能审查系统 v2.0.0 · 线上实例巡检")',
     'log.append("合同智能审查系统 v%s · 线上实例巡检" % _app.__version__)'),
])

# ---------------------------------------------------------------- 自检执行器
patch("tools/run_selftest.py", [
    ('lines = ["=" * 92,\n         "  合同智能审查系统 v2.0.0 · 验收自检（selftest）",',
     'import app as _app                                                       # noqa: E402\nlines = ["=" * 92,\n         "  合同智能审查系统 v%s · 验收自检（selftest）" % _app.__version__,'),
])

# ---------------------------------------------------------------- 基准
patch("tools/bench.py", [
    ('lines.append("合同智能审查系统 v2.0.0 · 性能基准（bench）")',
     'import app as _app                                                       # noqa: E402\nlines.append("合同智能审查系统 v%s · 性能基准（bench）" % _app.__version__)'),
])

# ---------------------------------------------------------------- 交付/上线脚本
patch("deploy/deploy_v2.py", [
    ('L("合同智能审查系统 v2.0.0 上线（目标 %s:%s，目录 %s）" % (HOST, PORT, REMOTE))',
     'L("合同智能审查系统 v2.0.1 上线（目标 %s:%s，目录 %s）" % (HOST, PORT, REMOTE))'),
])
patch("tools/run_all.py", [
    ('    log_path = os.path.join(OUT, "run_%s.txt" % key)',
     '    log_path = os.path.join(OUT, "run_%s.txt" % key)'),
])

with io.open(os.path.join(ROOT, "verify", "version_patch.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(log))
print("version patch done")
