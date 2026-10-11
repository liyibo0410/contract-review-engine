# -*- coding: utf-8 -*-
"""修掉渲染探针的一个致命缺陷：DOM 里包含 <script> 源码，
导致探针命中的是 JS 模板字符串（如 '<div class="pipe-node"...'）而不是真实渲染出的元素。
—— 这正是「数字不对但探针全绿」的经典假阳性。

修法：探针前先剥掉 <script>...</script>，只对真实 DOM 结构做断言；
落盘留证仍保留完整 DOM（含脚本），便于人工排查。
"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
P = os.path.join(ROOT, "tools", "verify_render.py")
with io.open(P, "r", encoding="utf-8") as f:
    c = f.read()
log = []


def rep(old, new):
    global c
    if old in c:
        c = c.replace(old, new, 1)
        log.append("ok   " + old.strip().splitlines()[0][:66])
    else:
        log.append("MISS " + old.strip().splitlines()[0][:66])


# 1) 加一个「剥脚本」的工具函数
rep("""def run_browser(url, dump_dom=False, shot_path=None, cookie=None):""",
    """_SCRIPT_RE = re.compile(r"<script[\\s\\S]*?</script>", re.I)


def body_only(dom):
    \"\"\"剥掉 <script> 与内联模板，只留真实 DOM。

    不剥的话探针会命中 JS 源码里的模板字符串（'<div class="pipe-node"...'），
    于是「页面其实没渲染」也会被判定为通过 —— 这是最隐蔽的一类假阳性。
    \"\"\"
    return _SCRIPT_RE.sub("", dom or "")


def run_browser(url, dump_dom=False, shot_path=None, cookie=None):""")

# 2) 两个探针函数都改用 body_only
rep("""def probe(name, path, probes, shot=None, expect_status=200):
    \"\"\"打开页面 → 断言元素个数 → 可选截图。\"\"\"
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    dom = run_browser(url)
    if not dom:""",
    """def probe(name, path, probes, shot=None, expect_status=200):
    \"\"\"打开页面 → 断言元素个数 → 可选截图。\"\"\"
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    dom = body_only(run_browser(url))
    if not dom:""")

rep("""    url = "http://127.0.0.1:%d%s" % (port, path)
    dom = run_browser(url)
    if dom:""",
    """    url = "http://127.0.0.1:%d%s" % (port, path)
    raw_dom = run_browser(url)
    dom = body_only(raw_dom)
    if raw_dom:""")

rep("""        with io.open(os.path.join(
                ROOT, "verify", "_dom_%02d_%s.html"
                % (SEQ[0], re.sub(r"[^\\w]", "_", path).strip("_"))),
                "w", encoding="utf-8") as f:
            f.write(dom)""",
    """        with io.open(os.path.join(
                ROOT, "verify", "_dom_%02d_%s.html"
                % (SEQ[0], re.sub(r"[^\\w]", "_", path).strip("_"))),
                "w", encoding="utf-8") as f:
            f.write(raw_dom)      # 留证保留完整 DOM（含脚本）""")

# 3) 日志条目探针收紧：只数真实 .li 行
rep("""    ("日志条目", r'class="li[^"]*"'),""",
    """    ("日志条目", r'<div class="li[^"]*">'),""")

with io.open(P, "w", encoding="utf-8") as f:
    f.write(c)
with io.open(os.path.join(ROOT, "verify", "render_patch3.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("render patch 3 done")
