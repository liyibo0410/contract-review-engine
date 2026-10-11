# -*- coding: utf-8 -*-
"""v2.1 助手（页面宠物）· 后端接线：3 个接口 + 权限登记。"""
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
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:60]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:60]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ---------------------------------------------------------------- auth：权限登记
patch("app/auth.py", [
    ('''    (r"^/api/twin$", {"GET"}, "auth"),''',
     '''    (r"^/api/twin$", {"GET"}, "auth"),
    # AI 助手（页面宠物）：只读，不改变任何业务状态，所以归在「已登录即可」这一档。
    # 但它能读到真实任务数、风险与规则，所以**不能匿名** —— 未登录一律 401。
    (r"^/api/assistant/ask$", {"POST"}, "auth"),
    (r"^/api/assistant/tour$", {"GET"}, "auth"),
    (r"^/api/assistant/kb$", {"GET"}, "auth"),'''),
])

# ---------------------------------------------------------------- server：接口实现
patch("app/server.py", [
    ('''        # ---------- 定时拉取调度（P1-01）''',
     '''        # ---------- AI 助手（页面宠物）
        # 三个接口都是只读：ask 只回答、tour 只给引导步骤、kb 只给常见问题。
        # 助手不做任何写操作，也不能替代人做审批决定 —— 它在代码层面就没有写权限路径。
        if path == "/api/assistant/ask":
            from . import assistant
            q = (body or {}).get("q") or (body or {}).get("question") or ""
            page = (body or {}).get("page") or "/"
            out = assistant.answer(cfg, st, q, page=page, sess=session)
            out["page"] = page
            st.audit(None, "assistant", "assistant.ask", "ok",
                     "助手问答（来源 %s，命中 %s）：%s"
                     % (out.get("source"), out.get("matched"),
                        (q or "")[:60].replace("\\n", " ")))
            return 0, out

        if path == "/api/assistant/tour":
            from . import assistant
            page = q1("page") or "/"
            return 0, assistant.tour(page, sess)

        if path == "/api/assistant/kb":
            from . import assistant
            return 0, {"chips": assistant.chips(),
                       "entries": len(assistant.KB),
                       "pages": list(assistant.TOURS.keys())}

        # ---------- 定时拉取调度（P1-01）'''),
])

with io.open(os.path.join(ROOT, "verify", "assistant_patch.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(log))
print("assistant patch done")
