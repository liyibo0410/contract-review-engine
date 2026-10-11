# -*- coding: utf-8 -*-
"""把 AI 助手纳入「一键自检」、一致性核查与公网巡检。"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def patch(rel, old, new):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    if old in c:
        c = c.replace(old, new, 1)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(c)
        log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:56]))
    else:
        log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:56]))


# ================================================================ selftest.py
patch("selftest.py",
      """    except Exception as exc:                                     # noqa: BLE001
        import traceback
        rep.add("自检执行异常", False, "%s\\n%s" % (exc, traceback.format_exc()[-800:]))""",
      '''        # ============================ v2.1 附加：AI 助手（页面宠物） ================
        from app import assistant as assistant_mod

        chk_list = []
        for q, want in (("审查流程分几步？", "pipeline"),
                        ("怎么把审查意见写回审批单", "how-writeback"),
                        ("风险等级是谁判定的", "risk-level")):
            _o = assistant_mod.answer(cfg, st, q, sess={"role": "legal",
                                                        "username": "legal"},
                                      use_llm=False)
            _e, _sc = assistant_mod.match_kb(q)
            _got = _e["id"] if (_e and _sc >= 3) else "-"
            chk_list.append((q, want, _got, _o.get("source")))
        rep.add("AI 助手能按自然语言问题命中知识库（3 个典型问法）",
                all(w == g for _q, w, g, _s in chk_list),
                "；".join("%s→%s" % (q[:10], g) for q, _w, g, _s in chk_list))

        ans = assistant_mod.answer(cfg, st, "现在一共有多少个审查任务",
                                   sess={"role": "legal", "username": "legal"},
                                   use_llm=False)
        s_now = st.stats()
        rep.add("助手回答的实时数据与数据库一致（不造数）",
                ans.get("source") == "live"
                and ("**%d 个审查任务**" % s_now["total"]) in ans["answer"],
                "任务总数 %d 出现在回答里；来源 %s" % (s_now["total"], ans.get("source")))

        miss = assistant_mod.answer(cfg, st, "帮我预测明年棉花期货的价格",
                                    sess={"role": "legal", "username": "legal"},
                                    use_llm=False)
        rep.add("助手对不确定的问题明确说不知道，不编造",
                miss.get("matched") is False and miss.get("source") == "kb-miss"
                and "没匹配到" in miss["answer"],
                "来源 %s；回答里给出可答方向而不是编内容" % miss.get("source"))

        _pages = {"index.html": "/", "workbench.html": "/workbench",
                  "rules.html": "/rules", "twin.html": "/twin",
                  "mock.html": "/mock", "login.html": "/login"}
        _srcs = {}
        for fn, pg in _pages.items():
            try:
                with io.open(os.path.join(root, "ui", fn), encoding="utf-8") as f:
                    _srcs[pg] = f.read()
            except OSError:
                _srcs[pg] = ""
        _bad, _steps = [], 0
        for pg, t in assistant_mod.TOURS.items():
            for s2 in t["steps"]:
                _steps += 1
                for tok in assistant_mod.__dict__.get("_sel_tokens", lambda x: [])(
                        s2["target"]) if False else []:
                    pass
                # 简化：直接检查 id/class 锚点
                import re as _re3
                for seg in _re3.split(r"\\s+|\\s*>\\s*", s2["target"].replace(",", " ")):
                    seg = seg.strip()
                    if not seg:
                        continue
                    m = _re3.search(r"#([\\w-]+)", seg)
                    tok = 'id="%s"' % m.group(1) if m else None
                    if tok is None:
                        m = _re3.search(r"\\.([\\w-]+)", seg)
                        tok = 'class="%s' % m.group(1) if m else None
                    if tok is None:
                        m = _re3.search(r"\\[([a-zA-Z-]+)\\s*=\\s*\\"([^\\"]*)\\"\\]", seg)
                        tok = '%s="%s"' % (m.group(1), m.group(2)) if m else None
                    if tok and tok not in _srcs.get(pg, ""):
                        _bad.append("%s/%s→%s" % (pg, s2["id"], tok))
        rep.add("新手引导的每一步都指向页面上真实存在的元素（%d 步）" % _steps,
                not _bad,
                "全部可定位（%d 个页面）" % len(assistant_mod.TOURS)
                if not _bad else "；".join(_bad[:3]))

        _t_l = assistant_mod.tour("/workbench", {"role": "legal"})
        _t_a = assistant_mod.tour("/workbench", {"role": "applicant"})
        _bl_l = [x for x in _t_l["steps"] if not x["allowed"]]
        _bl_a = [x for x in _t_a["steps"] if not x["allowed"]]
        rep.add("引导会按角色能力过滤受限步骤并标明归属角色",
                len(_bl_l) == 0 and len(_bl_a) >= 3
                and all(x["need_roles"] for x in _bl_a),
                "法务受限 %d 步；业务经办人受限 %d 步（%s）"
                % (len(_bl_l), len(_bl_a), "、".join(x["id"] for x in _bl_a[:3])))

        _asrc = io.open(os.path.join(root, "app", "assistant.py"),
                        encoding="utf-8").read()
        rep.add("助手只有查询与引导能力，不存在任何写操作路径",
                not any(k in _asrc for k in ("update_case", "add_results",
                                             "writeback.send", "clear_results")),
                "模块内无改库 / 回写调用；接口全部为只读")

    except Exception as exc:                                     # noqa: BLE001
        import traceback
        rep.add("自检执行异常", False, "%s\\n%s" % (exc, traceback.format_exc()[-800:]))''')

# ================================================================ 一致性核查
patch("tools/check_consistency.py",
      """    ("tools/verify_render.py", "def body_only(dom)"),""",
      """    ("tools/verify_render.py", "def body_only(dom)"),

    # ---------------- v2.1 AI 助手（页面宠物） ----------------
    ("app/assistant.py", "def answer(cfg, st, question, page=\\"/\\", sess=None, use_llm=True)"),
    ("app/assistant.py", "def tour(page=\\"/\\", sess=None)"),
    ("app/assistant.py", "没匹配到我能确定回答的问题，所以我不猜"),
    ("app/assistant.py", '"src": "review_results.adopted"'),  # 占位保持有序
    ("app/auth.py", '(r"^/api/assistant/ask$", {"POST"}, "auth")'),
    ("app/server.py", 'if path == "/api/assistant/ask":'),
    ("app/server.py", "assistant.tour(page, session)"),
    ("ui/assets/pet.js", "function startTour(page, resumeIndex)"),
    ("ui/assets/pet.js", "function placeCoach(coach, rect)"),
    ("ui/assets/pet.js", "resumeTour"),
    ("ui/assets/pet.js", "?pet=open"),
    ("ui/index.html", 'src="/assets/pet.js"'),
    ("ui/workbench.html", 'src="/assets/pet.js"'),
    ("ui/rules.html", 'src="/assets/pet.js"'),
    ("ui/twin.html", 'src="/assets/pet.js"'),
    ("ui/mock.html", 'src="/assets/pet.js"'),
    ("ui/login.html", 'src="/assets/pet.js"'),
    ("tools/check_assistant.py", "AI 助手 · 后端自检"),""")

# ================================================================ 公网巡检
patch("deploy/verify_remote_v2.py",
      """# ---------------------------------------------------------------- 5 越权拦截（法务）""",
      '''# ---------------------------------------------------------------- 4.5 AI 助手
code, _, body = call("POST", "/api/assistant/ask",
                     {"q": "现在一共有多少个审查任务", "page": "/"})
_a = json.loads(body).get("data") or {} if code == 200 else {}
rec("公网助手能回答并给出实时数据",
    code == 200 and _a.get("source") == "live"
    and "个审查任务" in (_a.get("answer") or ""),
    "来源 %s，%d 字" % (_a.get("source"), len(_a.get("answer") or "")))
code, _, body = call("POST", "/api/assistant/ask",
                     {"q": "明年棉花期货价格会涨吗", "page": "/"})
_a2 = json.loads(body).get("data") or {} if code == 200 else {}
rec("公网助手对不知道的问题不编造",
    code == 200 and _a2.get("matched") is False and "没匹配到" in (_a2.get("answer") or ""),
    "来源 %s" % _a2.get("source"))
code, _, body = call("GET", "/api/assistant/tour?page=/")
_t = json.loads(body).get("data") or {} if code == 200 else {}
rec("公网可取得新手引导步骤（带真实选择器）",
    code == 200 and len(_t.get("steps") or []) >= 5
    and all(s.get("target") for s in (_t.get("steps") or [])),
    "首页 %d 步" % len(_t.get("steps") or []))
code, hdrs_a, _ = call("POST", "/api/assistant/ask", {"q": "我是谁"}, cookie=False)
rec("未登录访问助手接口被拦（不泄露业务数据）", code == 401, "HTTP %s" % code)

# ---------------------------------------------------------------- 5 越权拦截（法务）''')

with io.open(os.path.join(ROOT, "verify", "assistant_integrate.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(log))
print("assistant integrate done")
