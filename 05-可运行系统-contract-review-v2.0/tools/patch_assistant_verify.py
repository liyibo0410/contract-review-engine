# -*- coding: utf-8 -*-
"""给验证补上 AI 助手的检查：
  * verify_http：3 个助手接口（含鉴权、实时数据一致性、引导步骤）
  * verify_render：宠物是否挂载、对话是否可开、引导是否真的跑起来（含手机端）
"""
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
        log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:58]))
    else:
        log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:58]))


# ================================================================ HTTP 冒烟
patch("tools/verify_http.py",
      """# ---------- 统计卡下钻依赖的回写筛选""",
      '''# ---------- v2.1：AI 助手（页面宠物）
code, _, body = call("POST", "/api/assistant/ask", {"q": "审查流程分几步", "page": "/"})
asst = json.loads(body)["data"] if code == 200 else {}
rec("POST /api/assistant/ask 可回答流程类问题",
    code == 200 and asst.get("matched") and asst.get("source") == "kb"
    and "七段链路" in (asst.get("answer") or ""),
    "来源 %s，%d 字" % (asst.get("source"), len(asst.get("answer") or "")))

code, _, body = call("POST", "/api/assistant/ask", {"q": "现在一共有多少个审查任务", "page": "/"})
asst2 = json.loads(body)["data"] if code == 200 else {}
_st = json.loads(call("GET", "/api/stats")[2])["data"]
rec("助手回答的实时数据与 /api/stats 一致（不造数）",
    code == 200 and asst2.get("source") == "live"
    and ("**%d 个审查任务**" % _st["total"]) in (asst2.get("answer") or ""),
    "任务总数 %s 出现在回答里" % _st["total"])

code, _, body = call("POST", "/api/assistant/ask",
                     {"q": "帮我预测一下明年棉花期货价格", "page": "/"})
asst3 = json.loads(body)["data"] if code == 200 else {}
rec("助手对不知道的问题明确说不知道（不编造）",
    code == 200 and asst3.get("matched") is False and asst3.get("source") == "kb-miss"
    and "没匹配到" in (asst3.get("answer") or ""),
    "来源 %s，未编造内容" % asst3.get("source"))

code, _, body = call("GET", "/api/assistant/tour?page=/")
tr = json.loads(body)["data"] if code == 200 else {}
rec("GET /api/assistant/tour 返回带真实选择器的引导步骤",
    code == 200 and len(tr.get("steps") or []) >= 5
    and all(s.get("target") and s.get("title") for s in (tr.get("steps") or [])),
    "首页 %d 步，全部带目标选择器与标题" % len(tr.get("steps") or []))

code, _, body = call("GET", "/api/assistant/tour?page=/rules")
tr2 = json.loads(body)["data"] if code == 200 else {}
rec("引导步骤按当前角色标注可用性（管理员全可做）",
    code == 200 and tr2.get("available")
    and all(s.get("allowed") for s in (tr2.get("steps") or [])),
    "规则页 %d 步全部 allowed=True" % len(tr2.get("steps") or []))

code, _, body = call("GET", "/api/assistant/kb")
kb = json.loads(body)["data"] if code == 200 else {}
rec("GET /api/assistant/kb 返回知识库规模与常见问题",
    code == 200 and (kb.get("entries") or 0) >= 20 and len(kb.get("chips") or []) >= 4,
    "知识库 %s 条，常见问题 %d 个" % (kb.get("entries"), len(kb.get("chips") or [])))

code, hdrs_e, _ = call("POST", "/api/assistant/ask", {"q": "我是谁"}, no_cookie=True)
rec("未登录访问助手接口被拦（401，不泄露业务数据）", code == 401, "HTTP %s" % code)

# ---------- 统计卡下钻依赖的回写筛选''')

# ================================================================ 渲染探针
patch("tools/verify_render.py",
      '''probe_authed("模拟审批系统美化页渲染（替代裸 JSON）", "/mock", [''',
      '''probe_authed("AI 助手：悬浮宠物挂载 + 对话可开 + 引导可跑", "/?pet=open,tour", [
    ("宠物挂载", r'id="petRoot"'),
    ("宠物按钮", r'id="petBtn"'),
    ("对话面板", r'id="petPanel"'),
    ("输入框", r'id="petInput"'),
    ("快捷提问", r'class="pet-chip"'),
    ("引导聚光灯", r'id="petSpot"'),
    ("引导气泡", r'id="petCoach"'),
    ("引导进度", r'class="pet-prog"'),
    ("引导步骤标题", r'第 1 / \\d+ 步'),
    ("上一步/下一步", r'data-t="next"'),
], shot="09_AI助手与新手引导.png")

probe_authed("AI 助手在孪生看板同样挂载", "/twin?pet=open", [
    ("宠物挂载", r'id="petRoot"'),
    ("对话面板展开", r'pet-panel on'),
    ("欢迎语", r'小亦'),
], shot=None)

probe_authed("模拟审批系统美化页渲染（替代裸 JSON）", "/mock", [''')

# 手机端：补一张宠物与引导的窄屏截图
patch("tools/verify_render.py",
      '''mobile_shot("/twin", "08_手机端_孪生看板.png", [''',
      '''mobile_shot("/?pet=tour", "10_手机端_新手引导.png", [
    ("宠物", r'id="petRoot"'),
    ("引导气泡", r'id="petCoach"'),
], "手机端新手引导可读")

mobile_shot("/twin", "08_手机端_孪生看板.png", [''')

with io.open(os.path.join(ROOT, "verify", "assistant_verify_patch.txt"), "w",
             encoding="utf-8") as f:
    f.write("\n".join(log))
print("assistant verify patch done")
