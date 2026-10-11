# -*- coding: utf-8 -*-
"""助手后端自检：意图匹配、实时数据一致性、不编造、引导步骤可用性。"""
from __future__ import annotations

import glob
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import assistant, auth as auth_mod, config as cfg_mod   # noqa: E402
from app import pipeline, store as store_mod                    # noqa: E402
from mock import feishu                                         # noqa: E402

tmp = tempfile.mkdtemp(prefix="cr_asst_")
cfg = cfg_mod.Config(ROOT)
cfg.data["paths"] = {
    "data": os.path.join(tmp, "data"), "uploads": os.path.join(tmp, "uploads"),
    "parsed": os.path.join(tmp, "parsed"), "reports": os.path.join(tmp, "reports"),
    "samples": os.path.join(ROOT, "samples"),
}
cfg.data["approval"]["base_url"] = ""
for k in ("data", "uploads", "parsed", "reports"):
    cfg.path(k)
st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)
pipeline.pull_from_approval(cfg, st, auto_review=True)

L = []
OK = [0]
N = [0]


def chk(name, ok, detail=""):
    N[0] += 1
    if ok:
        OK[0] += 1
    L.append("%s %-44s %s" % ("[OK ]" if ok else "[ERR]", name, detail))
    return ok


SESS = {"role": "legal", "username": "legal"}
SESS_A = {"role": "admin", "username": "admin"}

L.append("AI 助手 · 后端自检")
L.append("=" * 96)

# ---------------------------------------------------------------- 1 知识库匹配
CASES = [
    ("审查流程分几步？", "pipeline"),
    ("这个系统是干什么的", "what-is-it"),
    ("怎么把审查意见写回审批单", "how-writeback"),
    ("规则库在哪里维护", "how-rules"),
    ("会不会重复处理同一份合同", "idempotent"),
    ("支持哪些格式的合同", "formats"),
    ("风险等级是谁判定的", "risk-level"),
    ("机器人通知会泄露合同信息吗", "notify-mask"),
    ("按钮为什么是灰的", "live_disabled"),
]
L.append("")
L.append("1) 意图匹配准确性")
bad = []
for q, want in CASES:
    out = assistant.answer(cfg, st, q, sess=SESS, use_llm=False)
    src = out.get("source")
    e, score = assistant.match_kb(q)
    got = e["id"] if (e and score >= 3) else ("live" if src == "live" else "-")
    hit = out.get("matched") and (want == got or (want.startswith("live") and src == "live"))
    if not hit:
        bad.append("%s → 期望 %s 实得 %s" % (q, want, got))
    L.append("   %-26s → %-16s 来源 %-8s 命中 %s"
             % (q[:24], got, src, out.get("matched")))
chk("意图匹配准确（%d 个典型问法）" % len(CASES), not bad,
    "全部命中" if not bad else "；".join(bad))

# ---------------------------------------------------------------- 2 实时数据一致
L.append("")
L.append("2) 实时数据问答与库内数据一致")
s = st.stats()
rl = st.rollup()
out = assistant.answer(cfg, st, "现在一共有多少个审查任务", sess=SESS, use_llm=False)
n_ok = ("**%d 个审查任务**" % s["total"]) in out["answer"]
chk("任务总数取自实时聚合", n_ok and out["source"] == "live",
    "答案含「%d 个审查任务」，来源 %s" % (s["total"], out["source"]))
out = assistant.answer(cfg, st, "有多少高危合同和金额", sess=SESS, use_llm=False)
chk("风险分布与金额取自实时数据",
    ("**%d**" % (s.get("by_risk") or {}).get("high", 0)) in out["answer"]
    and out["source"] == "live",
    "含高风险 %d 与金额合计" % (s.get("by_risk") or {}).get("high", 0))
out = assistant.answer(cfg, st, "规则累计命中了多少项", sess=SESS, use_llm=False)
chk("规则命中/采纳数取自 review_results",
    out["source"] == "live" and ("**%d** 项" % rl["hits"]) in out["answer"],
    "命中 %d / 采纳 %d ｜ 来源 %s" % (rl["hits"], rl["adopted"], out["source"]))

# 具体案件
case = [c for c in st.list_cases() if c["risk_level"] == "high"][0]
rows = st.results(case["id"])
out = assistant.answer(cfg, st, "《%s》有哪些风险" % case["title"], sess=SESS, use_llm=False)
chk("能定位具体合同并列出真实风险项",
    out["source"] == "live" and ("**%d 项风险**" % len(rows)) in out["answer"]
    and rows[0]["rule_name"] in out["answer"],
    "《%s》共 %d 项，答案含首条规则名 %s"
    % (case["title"][:14], len(rows), rows[0]["rule_name"]))
chk("给出可跳转的动作",
    any(a.get("kind") == "goto" and "workbench" in (a.get("target") or "")
        for a in out.get("actions") or []),
    "动作 %s" % [a.get("label") for a in out.get("actions") or []])

# 规则查询
out = assistant.answer(cfg, st, "PU-H-001 这条规则是什么", sess=SESS, use_llm=False)
chk("能按规则编号查到真实规则",
    out["source"] == "live" and "PU-H-001" in out["answer"],
    out["answer"].splitlines()[0][:60] if out["answer"] else "")

# 系统状态
out = assistant.answer(cfg, st, "系统现在运行状态怎么样", sess=SESS, use_llm=False)
chk("系统状态回答含真实通道与队列信息",
    out["source"] == "live" and "队列" in out["answer"] and "PDF 通道" in out["answer"],
    "含队列 / PDF 通道 / 版本")
# 这条是踩坑补上的：助手曾因调用错函数名而把「已配置/已开启」说成「未配置/关闭」，
# 异常被静默吞掉 —— 现在要求它必须能读到真实的通知与脱敏状态。
chk("系统状态里的通知与脱敏读的是真实值（不是异常兜底）",
    "读取失败" not in out["answer"] and "脱敏 已开启" in out["answer"],
    out["answer"].splitlines()[-1][:70] if out["answer"] else "")

# 角色权限
out = assistant.answer(cfg, st, "我是谁 有什么权限", sess=SESS, use_llm=False)
legal_caps = auth_mod.caps_of("legal")
chk("角色权限回答与 auth 定义一致",
    out["source"] == "live" and ("%d 项权限" % len(legal_caps)) in out["answer"]
    and "法务审查人" in out["answer"],
    "法务审查人 %d 项权限" % len(legal_caps))

# ---------------------------------------------------------------- 3 不编造
L.append("")
L.append("3) 匹配不到时不编造")
out = assistant.answer(cfg, st, "帮我预测一下明年的棉花期货价格走势", sess=SESS, use_llm=False)
chk("无关问题明确说匹配不到并给出可答方向",
    out["source"] == "kb-miss" and out["matched"] is False
    and "没匹配到" in out["answer"] and "我能准确回答" in out["answer"],
    "来源 %s，未编造数据" % out["source"])
chk("匹配不到时提供「带我走一遍」入口",
    any(a.get("kind") == "tour" for a in out.get("actions") or []),
    "动作 %s" % [a.get("label") for a in out.get("actions") or []])
out = assistant.answer(cfg, st, "", sess=SESS, use_llm=False)
chk("空问题返回引导而非报错", out["source"] == "kb" and bool(out["chips"]),
    "返回 %d 个常见问题" % len(out["chips"]))

# 未登录
out = assistant.answer(cfg, st, "我是谁", sess=None, use_llm=False)
chk("未登录时不会泄露角色信息", "未登录" in out["answer"], out["answer"][:40])

# ---------------------------------------------------------------- 4 引导步骤
L.append("")
L.append("4) 新手引导步骤定义")


def sel_tokens(sel):
    """把 CSS 选择器拆成可以在源码里搜的「锚点」。

    只做静态存在性检查，所以：
      * 后代/子代组合符按空白与 `>` 拆开，逐段独立检查；
      * `#id` → `id="id"`；`.cls` → `class="cls`；`tag.cls` → `class="cls`；
        `tag[attr="v"]` → `attr="v"`；裸标签名太泛，跳过。
    """
    toks = []
    for part in re.split(r",\s*", sel):
        for seg in re.split(r"\s*>\s*|\s+", part.strip()):
            seg = seg.strip()
            if not seg:
                continue
            m = re.search(r"\[([a-zA-Z-]+)\s*=\s*\"([^\"]*)\"\]", seg)
            if m:
                toks.append('%s="%s"' % (m.group(1), m.group(2)))
                continue
            m = re.search(r"#([\w-]+)", seg)
            if m:
                toks.append('id="%s"' % m.group(1))
                continue
            m = re.search(r"\.([\w-]+)", seg)
            if m:
                toks.append('class="%s' % m.group(1))
                continue
            # 裸标签名（div/button/tr…）不检查：太泛，任何页面都有
    return toks


page_src = {}
for fn in glob.glob(os.path.join(ROOT, "ui", "*.html")):
    with io.open(fn, encoding="utf-8") as f:
        page_src[os.path.basename(fn)] = f.read()
MAP = {"/": "index.html", "/workbench": "workbench.html", "/rules": "rules.html",
       "/twin": "twin.html", "/mock": "mock.html", "/login": "login.html"}

missing = []
step_total = 0
dynamic_total = 0
for page, tour in assistant.TOURS.items():
    src = page_src.get(MAP.get(page, ""), "")
    for s in tour["steps"]:
        step_total += 1
        if s.get("dynamic"):
            dynamic_total += 1
        for needle in sel_tokens(s["target"]):
            if needle not in src:
                missing.append("%s / %s → 找不到 %r" % (page, s["id"], needle))
chk("每条引导步骤的目标锚点都在对应页面里存在（%d 步）" % step_total,
    not missing,
    "全部可定位（其中 %d 步为 JS 渲染后生成，已退化为「生成器仍产出该锚点」检查）"
    % dynamic_total if not missing else "；".join(missing[:3]))

# 选择器必须是 querySelector 能解析的合法 CSS（历史上踩过：写了 #card-\d+ 这种正则形态）
import re as _re2                                                          # noqa: E402
bad_sel = []
for page, tour in assistant.TOURS.items():
    for s in tour["steps"]:
        sel = s["target"]
        if _re2.search(r"\\[dws]|\\d\+|\*\+|[^\\w\\s\\-\\.#\\[\\]\"'=:>,()]", sel):
            bad_sel.append("%s / %s → %r" % (page, s["id"], sel))
chk("引导选择器都是合法 CSS（能被 querySelector 解析）",
    not bad_sel, "全部合法" if not bad_sel else "；".join(bad_sel))

chk("引导步骤都带标题与说明",
    all(s.get("title") and s.get("desc")
        for t in assistant.TOURS.values() for s in t["steps"]),
    "共 %d 步，覆盖 %d 个页面" % (step_total, len(assistant.TOURS)))

t_legal = assistant.tour("/workbench", SESS)
blocked = [s for s in t_legal["steps"] if not s["allowed"]]
chk("引导按角色能力过滤并标注所需角色",
    t_legal["available"] and len(blocked) == 0,
    "法务视角：%d 步全部可做（工作台内所有步骤都在法务能力范围内）" % len(t_legal["steps"]))

t_legal_rules = assistant.tour("/rules", SESS)
blocked_r = [s for s in t_legal_rules["steps"] if not s["allowed"]]
chk("法务在规则页的步骤被标为受限并给出归属角色",
    len(blocked_r) == len(t_legal_rules["steps"])
    and all("系统管理员" in (s["need_roles"] or []) for s in blocked_r),
    "规则页 %d 步全部受限，归属：%s"
    % (len(blocked_r), "、".join(blocked_r[0]["need_roles"]) if blocked_r else "-"))

t_app = assistant.tour("/workbench", {"role": "applicant"})
app_blocked = [s for s in t_app["steps"] if not s["allowed"]]
chk("业务经办人视角的受限步骤更多（权限差异真实生效）",
    len(app_blocked) >= 3,
    "经办人受限 %d 步：%s" % (len(app_blocked), "、".join(s["id"] for s in app_blocked)))

t_admin = assistant.tour("/rules", SESS_A)
chk("管理员在规则页无受限步骤",
    t_admin["available"] and all(s["allowed"] for s in t_admin["steps"]),
    "%d 步全部可做" % len(t_admin["steps"]))

chk("未知页面返回空引导而不是报错",
    assistant.tour("/nope", SESS)["available"] is False,
    "available=False，已给可用页面清单")
chk("引导覆盖全部页面",
    set(assistant.TOURS) >= {"/", "/workbench", "/rules", "/twin", "/mock", "/login"},
    "覆盖 %s" % "、".join(sorted(assistant.TOURS)))

# ---------------------------------------------------------------- 5 零依赖口径
L.append("")
L.append("5) 口径检查")
src = io.open(os.path.join(ROOT, "app", "assistant.py"), encoding="utf-8").read()
chk("助手模块零第三方依赖",
    not re.search(r"^\s*(import|from)\s+(requests|numpy|pandas|openai)\b", src, re.M),
    "仅用标准库 + 项目内模块")
chk("助手不提供任何写操作（不存在改库/回写路径）",
    "update_case" not in src and "writeback.send" not in src and "add_results" not in src,
    "只有查询与引导")
chk("系统提示词约束了模型不得编造",
    "不得新增" in src and "不得编造" in src,
    "llm 润色有明确边界约束")

L.append("")
L.append("=" * 96)
L.append("结果：%d / %d 项通过" % (OK[0], N[0]))
L.append("=" * 96)

out_path = os.path.join(ROOT, "verify", "assistant_check.txt")
with io.open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("assistant check: %d / %d" % (OK[0], N[0]))
sys.exit(0 if OK[0] == N[0] else 1)
