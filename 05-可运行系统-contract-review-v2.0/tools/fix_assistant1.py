# -*- coding: utf-8 -*-
"""修正助手的引导选择器与意图关键词（由自检暴露出的真实问题）。

三类修正：
  1) 引导步骤里用了非法 CSS（`#card-\\d+` 这种正则形态，querySelector 会直接抛错），
     改成真实可选的类选择器；
  2) 部分目标是**JS 渲染后**才存在的元素（如风险卡片、规则行），
     静态源码检查找不到 —— 给这类步骤标 `dynamic: true`，
     静态检查退化为「生成器里还产出这个类名吗」，真正的「点得到」交给无头浏览器验证；
  3) 补充实时意图关键词（「命中」「采纳」「多少项」），否则这类问题会掉进知识库答非所问。
"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
P = os.path.join(ROOT, "app", "assistant.py")
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


# ---------- 1) 实时意图关键词
rep('''    (["多少个任务", "有多少", "任务总数", "总体情况", "概况", "统计", "多少份合同", "大盘",
      "高风险的有多", "金额合计", "多少风险"], "live_overview", _live_overview),''',
    '''    (["多少个任务", "有多少", "任务总数", "总体情况", "概况", "统计", "多少份合同", "大盘",
      "高风险的有多", "金额合计", "多少风险", "命中", "多少项", "采纳", "风险项",
      "多少条", "当前情况"], "live_overview", _live_overview),''')

# ---------- 2) 非法 / 不稳的选择器
rep('''            {"id": "wb-card", "target": "#tabBody .risk-card, #card-\\\\d+", "advance": "click",''',
    '''            {"id": "wb-card", "target": "#tabBody .risk-card", "advance": "click",
             "dynamic": True,''')

rep('''            {"id": "wb-edit", "target": "#tabBody textarea.sugg", "advance": "next",''',
    '''            {"id": "wb-edit", "target": "#tabBody textarea.sugg", "advance": "next",
             "dynamic": True,''')

rep('''            {"id": "wb-adopt", "target": "#tabBody button[data-cap=\\"edit_suggestion\\"]",
             "cap": "edit_suggestion", "advance": "click",''',
    '''            {"id": "wb-adopt", "target": "#tabBody button[data-cap=\\"edit_suggestion\\"]",
             "cap": "edit_suggestion", "advance": "click", "dynamic": True,''')

rep('''            {"id": "dash-drill", "target": "#stats .stat.clickable", "advance": "click",''',
    '''            {"id": "dash-drill", "target": "#stats .stat.clickable", "advance": "click",
             "dynamic": True,''')

rep('''            {"id": "rules-row", "target": "#tabBody tr, .rule-row", "advance": "next",''',
    '''            {"id": "rules-row", "target": "#ruleBody .rule-row", "advance": "next",
             "dynamic": True,''')

rep('''            {"id": "rules-edit", "target": "#tabBody button, .rule-row button", "advance": "next",''',
    '''            {"id": "rules-edit", "target": "#ruleBody .rule-row button", "advance": "next",
             "dynamic": True,''')

with io.open(P, "w", encoding="utf-8") as f:
    f.write(c)
with io.open(os.path.join(ROOT, "verify", "assistant_fix1.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("assistant fix1 done")
