# -*- coding: utf-8 -*-
"""更新渲染校验探针：孪生看板（全真实数据版）、模拟审批页、大盘下钻与顶栏新结构。"""
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


# 大盘探针：补「统计卡可下钻」与「顶栏新结构」
rep("""probe_authed("审查大盘渲染（KPI + 自动流转 + 队列 + 权限入口）", "/", [
    ("KPI 容器", r'id="stats"'),
    ("自动流转面板", r'id="swSched"'),
    ("调度指标", r'id="schedKv"'),
    ("队列指标", r'id="queueKv"'),
    ("角色标识", r'id="roleChip"'),
    ("权限入口", r'openAuth\\(\\)'),
    ("顶栏 logo", r'class="brand-logo"'),
], shot="01_审查大盘.png")""",
    """probe_authed("审查大盘渲染（KPI 可下钻 + 自动流转 + 队列 + 顶栏身份）", "/", [
    ("KPI 容器", r'id="stats"'),
    ("统计卡可下钻", r'class="stat [^"]*clickable"'),
    ("下钻数据绑定", r'data-drill='),
    ("下钻弹窗", r'id="mDrill"'),
    ("自动流转面板", r'id="swSched"'),
    ("调度指标", r'id="schedKv"'),
    ("队列指标", r'id="queueKv"'),
    ("角色标识", r'id="roleChip"'),
    ("权限入口", r'openAuth\\(\\)'),
    ("顶栏 logo", r'class="brand-logo"'),
    ("顶栏导航", r'class="nav"'),
], shot="01_审查大盘.png")""")

# 孪生看板探针：换成新结构
rep("""probe_authed("动态孪生看板渲染", "/twin", [
    ("时钟", r'id="clock"'),
    ("流水线", r'id="node8"'),
], shot="04_动态孪生看板.png")""",
    """probe_authed("动态孪生看板渲染（全真实实时数据）", "/twin", [
    ("时钟", r'id="clock"'),
    ("KPI 行", r'id="kpi"'),
    ("流水线容器", r'id="pipe"'),
    ("流水线节点", r'class="pipe-node'),
    ("数据来源标注", r'class="src"'),
    ("真实仪表", r'class="gauge '),
    ("接口响应区", r'id="topPaths"'),
    ("实时日志流", r'id="logStream"'),
    ("日志条目", r'class="li[^"]*"'),
    ("队列调度区", r'id="opsKv"'),
    ("规则库条", r'class="rb"'),
    ("无模拟值声明", r'本页不构造任何演示数据'),
], shot="04_动态孪生看板.png")

probe_authed("模拟审批系统美化页渲染（替代裸 JSON）", "/mock", [
    ("待办列表", r'class="mock-item'),
    ("详情区", r'id="detail"'),
    ("接口清单", r'id="eps"'),
    ("接口条目", r'class="ep"'),
    ("原始 JSON 入口", r'id="rawPanel"'),
    ("评论区区块", r'class="mock-comment'),
], shot="06_模拟审批系统.png")""")

with io.open(P, "w", encoding="utf-8") as f:
    f.write(c)
with io.open(os.path.join(ROOT, "verify", "render_patch2.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("render patch 2 done")
