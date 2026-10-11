# -*- coding: utf-8 -*-
"""verify_render.py 收尾修正：
  1) 必须先起服务再拉取数据（拉取要走 HTTP 打到模拟审批，服务没起就会 0 条）；
  2) DOM 留证文件名加序号（/rules 被探针了两次，后一次会覆盖前一次）；
  3) 规则页的第二个探针改为页面真实存在的元素。
"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
P = os.path.join(ROOT, "tools", "verify_render.py")
with io.open(P, "r", encoding="utf-8") as f:
    c = f.read()
log = []


def rep(old, new, must=True):
    global c
    if old in c:
        c = c.replace(old, new, 1)
        log.append("ok   " + old.strip().splitlines()[0][:66])
    else:
        log.append(("MISS " if must else "skip ") + old.strip().splitlines()[0][:66])


# ---- 1) 起服务在前，拉取在后
rep("""from app import pipeline                                                    # noqa: E402
pipeline.pull_from_approval(cfg, st, auto_review=True)

httpd = server_mod.serve(cfg, st)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.6)""",
    """from app import pipeline                                                    # noqa: E402

# 顺序很关键：拉取待办是**真的走 HTTP** 打到同端口的模拟审批接口，
# 服务没起来就拉，只会得到 Connection refused 与 0 条任务。
httpd = server_mod.serve(cfg, st)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.7)
PULLED = pipeline.pull_from_approval(cfg, st, auto_review=True)
rec("预热数据：拉取并审查示例待办", bool(PULLED.get("created")),
    "新建 %d 个，完成 %d 个" % (
        len(PULLED.get("created") or []),
        len([c for c in st.list_cases() if c["review_status"] == "completed"])))""")

# ---- 2) DOM 文件名加序号
rep("""log = []
ok_count = [0]""",
    """log = []
ok_count = [0]
SEQ = [0]""")

rep("""        with io.open(os.path.join(ROOT, "verify",
                                 "_dom_%s.html" % re.sub(r"[^\\w]", "_", path)),
                     "w", encoding="utf-8") as f:
            f.write(dom)""",
    """        SEQ[0] += 1
        with io.open(os.path.join(
                ROOT, "verify", "_dom_%02d_%s.html"
                % (SEQ[0], re.sub(r"[^\\w]", "_", path).strip("_"))),
                "w", encoding="utf-8") as f:
            f.write(dom)""")

# ---- 3) 规则页第二个探针换成真实元素
rep("""probe_authed("合规规则与模板渲染（管理员可见）", "/rules", [
    ("规则行", r'class="rule-row"'),
    ("类型切换", r'class="tabs"'),
], shot="03_合规规则与模板.png")""",
    """probe_authed("合规规则与模板渲染（管理员可见）", "/rules", [
    ("规则行", r'class="rule-row"'),
    ("规则面板", r'class="panel"'),
    ("顶栏 logo", r'class="brand-logo"'),
], shot="03_合规规则与模板.png")""")

# ---- 4) 同步修正工作台探针的补充断言
rep("""probe_authed("智能审查工作台渲染（正文高亮 + 风险卡 + 模式对照）", "/workbench?id=1", [
    ("正文段落", r'<p class="doc-para'),
    ("风险高亮", r'<mark class="hl'),
    ("风险卡片", r'id="card-\\d+"'),
    ("模式对照", r'class="mode-col"'),
    ("双栏视窗", r'class="wb"'),
], shot="02_智能审查工作台.png")""",
    """FIRST_CASE = (st.list_cases() or [{}])[-1].get("id") or 1
probe_authed("智能审查工作台渲染（正文高亮 + 风险卡 + 模式对照）",
             "/workbench?id=%d" % FIRST_CASE, [
    ("正文段落", r'<p class="doc-para'),
    ("风险高亮", r'<mark class="hl'),
    ("风险卡片", r'id="card-\\d+"'),
    ("模式对照", r'class="mode-col"'),
    ("剔除留痕或模式说明", r'class="mode-note"'),
    ("双栏视窗", r'class="wb"'),
], shot="02_智能审查工作台.png")""")

with io.open(P, "w", encoding="utf-8") as f:
    f.write(c)
with io.open(os.path.join(ROOT, "verify", "render_patch.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("render patch done")
