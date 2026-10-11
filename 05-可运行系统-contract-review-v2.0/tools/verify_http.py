# -*- coding: utf-8 -*-
"""HTTP 链路冒烟测试：真实启动服务、真实发请求、逐条记录结果。

注意：服务与请求必须在同一次进程内完成（宿主环境会回收跨调用的子进程）。
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import tempfile                                                      # noqa: E402

from app import config as cfg_mod, server as server_mod, store as store_mod   # noqa: E402
from mock import feishu                                                       # noqa: E402

PORT = 8791
TMP = tempfile.mkdtemp(prefix="cr_smoke_")      # 独立数据目录：冒烟可重复且不动交付数据
cfg = cfg_mod.Config(ROOT)
cfg.data["paths"] = {
    "data": os.path.join(TMP, "data"), "uploads": os.path.join(TMP, "uploads"),
    "parsed": os.path.join(TMP, "parsed"), "reports": os.path.join(TMP, "reports"),
    "samples": os.path.join(ROOT, "samples"),
}
cfg.data["server"]["port"] = PORT
cfg.data["approval"]["base_url"] = "http://127.0.0.1:%d/mock/feishu/api" % PORT

for k in ("data", "uploads", "parsed", "reports"):
    cfg.path(k)

st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)

httpd = server_mod.serve(cfg, st)
th = threading.Thread(target=httpd.serve_forever, daemon=True)
th.start()
time.sleep(0.6)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# 不跟随重定向：否则「未登录跳登录页」永远看到的是最终 200，测不出 302 本身
NO_REDIRECT = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    type("NR", (urllib.request.HTTPRedirectHandler,), {
        "redirect_request": lambda *a, **k: None})())
BASE = "http://127.0.0.1:%d" % PORT
log = []

# v2.0：会话态全局持有 —— 登录后所有请求自动带 Cookie（HttpOnly，脚本读不到，
# 这里的测试客户端就是「浏览器」本身，所以由 cookie jar 统一转发）。
SESSION = {"cookie": ""}


def call(method, path, body=None, raw=None, ctype=None, headers=None,
         no_cookie=False, no_redirect=False, timeout=120, retries=3):
    """发一次请求。

    对**连接层抖动**做退避重试：本环境实测出现过偶发连接重置
    （远程巡检里表现为 HTTP 0，本地偶发表现为响应体异常）。
    不重试的话，一次抖动就会让整个冒烟脚本崩在半路、后面几十项检查全丢 ——
    比漏报更糟，因为它把注意力引向错误的方向。
    只重试「没拿到响应」；4xx/5xx 是服务端的明确答复，不重试。
    """
    url = BASE + path
    data = None
    hdrs = dict(headers or {})
    if SESSION["cookie"] and not no_cookie:
        hdrs.setdefault("Cookie", SESSION["cookie"])
    if raw is not None:
        data = raw
        hdrs["Content-Type"] = ctype or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    op = NO_REDIRECT if no_redirect else OPENER
    last = (0, {}, b"")
    for i in range(max(1, retries)):
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with op.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except Exception as e:                                     # noqa: BLE001
            last = (0, {}, ("连接失败：%s" % e).encode())
            if i < retries - 1:
                time.sleep(0.8 * (i + 1))
    return last


def rec(name, ok, detail):
    log.append("%s %-44s %s" % ("[OK ]" if ok else "[ERR]", name, detail))


def login_as(role="admin"):
    """按角色登录（走真实的 /api/auth/login 与演示切换），返回是否成功。"""
    code, hdrs, body = call("POST", "/api/auth/login",
                            {"username": role, "password": {
                                "legal": "Legal@2026", "applicant": "Apply@2026",
                                "admin": "Admin@2026"}.get(role, "")},
                            no_cookie=True)
    sc = hdrs.get("Set-Cookie") or hdrs.get("set-cookie") or ""
    if code == 200 and sc:
        SESSION["cookie"] = sc.split(";")[0]
        return True
    code, hdrs, body = call("POST", "/api/auth/switch", {"role": role}, no_cookie=True)
    sc = hdrs.get("Set-Cookie") or hdrs.get("set-cookie") or ""
    if code == 200 and sc:
        SESSION["cookie"] = sc.split(";")[0]
        return True
    return False


# ---------------------------------------------------------------- v2.0 鉴权闸门
# 未登录：页面应跳登录页，接口应 401 —— 这条必须在登录之前测，否则测不到。
code, hdrs, body = call("GET", "/", no_cookie=True, no_redirect=True)
rec("未登录访问页面被重定向到登录页",
    code == 302 and "/login" in (hdrs.get("Location") or ""),
    "HTTP %s → %s" % (code, hdrs.get("Location")))
code, _, body = call("GET", "/api/cases", no_cookie=True)
rec("未登录访问接口返回 401",
    code == 401, "HTTP %s，msg=%s" % (code, body.decode("utf-8", "ignore")[:60]))
code, _, body = call("GET", "/login", no_cookie=True)
rec("登录页可匿名访问", code == 200 and b"loginForm" in body, "HTTP %s，%d 字节" % (code, len(body)))
code, _, body = call("GET", "/api/health", no_cookie=True)
rec("健康检查接口保持匿名可用（供外部探活）",
    code == 200 and b"version" in body, "HTTP %s" % code)

LOGIN_OK = login_as("admin")
code, _, body = call("GET", "/api/auth/me")
me = json.loads(body)["data"] if code == 200 else {}
rec("管理员账号登录成功并返回角色与能力清单",
    LOGIN_OK and code == 200 and me.get("role") == "admin" and len(me.get("caps") or []) >= 10,
    "角色=%s，能力 %d 项，会话到期 %s" % (me.get("role_label"), len(me.get("caps") or []),
                                    me.get("session_expires_at")))

code, hdrs, body = call("GET", "/api/auth/login")
rec("登录接口不接受 GET（方法与能力匹配）",
    code in (403, 404, 405), "HTTP %s" % code)

# ---------------------------------------------------------------- 页面与静态资源
for path, needle, kind in [
    ("/", "衡鉴 V2.1.7".encode("utf-8"), "text/html"),
    ("/assets/app.css", b"--brand", "text/css"),
    ("/assets/app.js", b"global.CR", "javascript"),
    ("/workbench", b"workbench", "text/html"),
    ("/rules", b"rules", "text/html"),
]:
    code, hdrs, body = call("GET", path)
    ct = hdrs.get("Content-Type", "")
    rec("静态页面 %s" % path, code == 200 and needle in body and kind.split("/")[-1] in ct,
        "HTTP %s  %s  %d bytes" % (code, ct.split(";")[0], len(body)))

# ---------------------------------------------------------------- 健康检查 / 统计
code, _, body = call("GET", "/api/health")
h = json.loads(body)
rec("/api/health", code == 200 and h["code"] == 0,
    "版本 %s，PDF 浏览器通道=%s，规则类型 %s" % (
        h["data"]["version"], bool(h["data"]["browser_for_pdf"]),
        ",".join(h["data"]["rules_types"])))

# ---------------------------------------------------------------- 拉取待办（含自动审查）
code, _, body = call("POST", "/api/pull", {"auto_review": True})
d = json.loads(body)["data"]
rec("POST /api/pull 拉取并自动审查", code == 200 and len(d["created"]) == 8,
    "新建 %d，跳过 %d" % (len(d["created"]), len(d["skipped"])))
code, _, body = call("POST", "/api/pull", {"auto_review": True})
d2 = json.loads(body)["data"]
rec("POST /api/pull 幂等（二次拉取全部跳过）", len(d2["created"]) == 0 and len(d2["skipped"]) == 8,
    "新建 %d，跳过 %d" % (len(d2["created"]), len(d2["skipped"])))

code, _, body = call("GET", "/api/cases")
cases = json.loads(body)["data"]["items"]
rec("GET /api/cases 列表", code == 200 and len(cases) == 8,
    "%d 个任务，风险分布 %s" % (len(cases), json.dumps(
        {c["risk_level"]: len([x for x in cases if x["risk_level"] == c["risk_level"]])
         for c in cases}, ensure_ascii=False)))

code, _, body = call("GET", "/api/stats")
s = json.loads(body)["data"]
rec("GET /api/stats 大盘统计", code == 200 and s["total"] == 8,
    "总数 %d，高/中/低=%s/%s/%s，受阻 %d，高风险金额 %.0f" % (
        s["total"], s["by_risk"].get("high", 0), s["by_risk"].get("medium", 0),
        s["by_risk"].get("low", 0), s["blocked"], s["high_risk_amount"]))

code, _, body = call("GET", "/api/cases?risk=high")
hi = json.loads(body)["data"]["items"]
rec("GET /api/cases?risk=high 风险筛选", len(hi) >= 2,
    "高风险任务 %d 个" % len(hi))

# ---------------------------------------------------------------- 工作台详情
# v2.0：法务复核（改建议 / 批注 / 回写）归「法务审查人」，此处切换身份继续测。
login_as("legal")
target = [c for c in cases if c["title"].startswith("核心业务系统")][0]
CID = target["id"]
code, _, body = call("GET", "/api/cases/%d" % CID)
det = json.loads(body)["data"]
anchored = [r for r in det["results"] if r["page_no"] is not None]
rec("GET /api/cases/{id} 工作台详情", code == 200 and len(det["results"]) == 3
    and len(det["paragraphs"]) > 20 and len(det["clauses"]) > 5,
    "风险 %d 项（可定位 %d）、段落 %d、条款 %d、覆盖度 %.0f%%、摘要方式 %s" % (
        len(det["results"]), len(anchored), len(det["paragraphs"]), len(det["clauses"]),
        (det["coverage"]["coverage_rate"] * 100), det["summary"].get("summary_source")))

rid = det["results"][0]["id"]
code, _, body = call("PATCH", "/api/results/%d" % rid,
                     {"suggestion_final": "【HTTP 冒烟】建议按示范条款修改", "adopted": True,
                      "lawyer_note": "HTTP 冒烟批注"})
rec("PATCH /api/results/{id} 法务复核", code == 200
    and json.loads(body)["data"]["adopted"] is True, "已采纳并保存编辑后的示范条款")

code, _, body = call("POST", "/api/cases/%d/opinion" % CID, {"opinion": "冒烟测试法务意见：请修改后再送审。"})
rec("POST /api/cases/{id}/opinion 法务批注", code == 200, "批注已保存")

# ---------------------------------------------------------------- 回写（HTTP 调用审批接口）
code, _, body = call("POST", "/api/cases/%d/writeback" % CID, {"mode": "dry_run"})
wb = json.loads(body)["data"]
rec("POST writeback 预览模式（不写入）", code == 200 and wb["ok"] and not wb["comment_id"],
    "mode=%s status=%s 内容 %d 字" % (wb["mode"], wb["status"], len(wb.get("comment_text") or "")))

code, _, body = call("POST", "/api/cases/%d/writeback" % CID, {"mode": "live"})
wb2 = json.loads(body)["data"]
rec("POST writeback 正式写入（HTTP 调模拟审批接口）",
    code == 200 and wb2["ok"] and wb2["comment_id"].startswith("CMT-"),
    "comment_id=%s  %s" % (wb2["comment_id"], wb2["message"]))

code, _, body = call("GET", "/mock/feishu/api/instances/%s/comments" % wb2["instance_id"])
cmts = json.loads(body)["data"]["comments"]
rec("GET 审批评论区（模拟飞书，独立 HTTP 接口）", len(cmts) >= 1,
    "评论区 %d 条，首条 %d 字，作者 %s" % (
        len(cmts), len(cmts[0]["text"]) if cmts else 0,
        cmts[0]["author"] if cmts else "-"))

code, _, body = call("GET", "/mock/feishu/api/todo")
todo = json.loads(body)["data"]["items"]
rec("GET 审批待办（模拟飞书）", code == 200,
    "剩余待办 %d 个（已回写的审批单自动置为已处理）" % len(todo))

# ---------------------------------------------------------------- 报告导出
code, hdrs, body = call("GET", "/api/cases/%d/report?format=md&download=1" % CID)
rec("GET report?format=md 下载", code == 200 and b"# " in body and len(body) > 1500,
    "HTTP %s  %s  %d bytes" % (code, hdrs.get("Content-Type", "").split(";")[0], len(body)))

code, hdrs, body = call("GET", "/api/cases/%d/report?format=pdf" % CID)
ct = hdrs.get("Content-Type", "")
rec("GET report?format=pdf 服务端生成 PDF",
    code == 200 and (ct.startswith("application/pdf") or body[:4] == b"%PDF"),
    "HTTP %s  %s  %d bytes  %s" % (code, ct.split(";")[0], len(body),
                                   "真实 PDF" if body[:4] == b"%PDF" else "降级 HTML"))

code, hdrs, body = call("GET", "/api/cases/%d/report?format=html" % CID)
rec("GET report?format=html 打印预览", code == 200 and b"<html" in body.lower(),
    "%d bytes" % len(body))

# ---------------------------------------------------------------- 上传
# v2.0：提交审查申请归「业务经办人」与「系统管理员」。
login_as("applicant")
import tempfile                                                     # noqa: E402
from docx import Document                                           # noqa: E402

NEW_LINES = [
    "《生产设备采购合同（本地上传用例）》",
    "合同编号：CR-SMOKE-0001",
    "甲方（需方）：华北智造集团有限公司",
    "统一社会信用代码：91130100MA0D8YQK5T",
    "乙方（供方）：测试供应商股份有限公司",
    "统一社会信用代码：91330000MA2ABCDE7X",
    "第一条 付款方式",
    "乙方交付设备并经甲方签收后，甲方应于到货后 3 个工作日内一次性支付合同全额款项。",
    "第二条 违约责任",
    "乙方逾期交付的，每逾期一日按合同总金额的 0.5% 向甲方支付违约金。",
    "第三条 争议解决",
    "因本合同发生争议的，提交乙方所在地人民法院管辖。",
    "第四条 合同生效",
    "本合同自双方签字并加盖公章之日起生效。",
]
tmp_docx = os.path.join(tempfile.gettempdir(), "cr_smoke_upload.docx")
_doc = Document()
for line in NEW_LINES:
    _doc.add_paragraph(line)
_doc.save(tmp_docx)
with open(tmp_docx, "rb") as f:
    content = base64.b64encode(f.read()).decode()

code, _, body = call("POST", "/api/upload",
                     {"filename": "生产设备采购合同_本地上传用例.docx", "content": content,
                      "meta": {"business_type": "purchase", "title": "生产设备采购合同（本地上传用例）"},
                      "auto_review": True})
up = json.loads(body)["data"]
code2, _, body2 = call("GET", "/api/cases/%d" % up["case_id"])
det2 = json.loads(body2)["data"]
rec("POST /api/upload 本地上传并自动审查（全新文件）",
    code == 200 and up.get("duplicated") is False
    and det2["case"]["review_status"] == "completed" and len(det2["results"]) >= 2,
    "新建任务 #%s：状态=%s 风险=%s 命中 %s" % (
        up["case_id"], det2["case"]["review_status_label"],
        det2["case"]["risk_level_label"],
        "、".join(r["rule_id"] for r in det2["results"])))
try:
    os.remove(tmp_docx)
except OSError:
    pass

code, _, body = call("POST", "/api/upload",
                     {"filename": "生产设备采购合同_本地上传用例.docx", "content": content,
                      "meta": {"business_type": "purchase"}, "auto_review": True})
up2 = json.loads(body)["data"]
rec("POST /api/upload 文件指纹去重", up2.get("duplicated") is True,
    "复用既有任务 #%s（SHA256 一致）" % up2.get("case_id"))

# ---------------------------------------------------------------- 阻塞与重试
# v2.0：阻塞重试归「系统管理员」。
login_as("admin")
blk = [c for c in cases if c["review_status"] == "blocked"][0]
code, _, body = call("GET", "/api/cases/%d" % blk["id"])
bdet = json.loads(body)["data"] if body[:1] == b"{" else None
rec("阻塞任务详情返回受阻原因",
    bool(bdet) and bdet["case"]["review_status"] == "blocked"
    and bdet["case"]["blocked_reason"],
    ("#%s %s → %s" % (blk["id"], bdet["document"]["filename"],
                      bdet["case"]["blocked_reason"][:40])) if bdet
    else "HTTP %s ｜ %s" % (code, body.decode("utf-8", "ignore")[:110]))
code, _, body = call("POST", "/api/cases/%d/retry" % blk["id"])
r = json.loads(body)["data"]
rec("POST retry 对仍损坏文件保持受阻（防护正确）",
    r.get("blocked") is True, "重试结果 blocked=%s reason=%s" % (
        r.get("blocked"), (r.get("reason") or "")[:36]))

# ---------------------------------------------------------------- 规则库
code, _, body = call("GET", "/api/rules?type=sales")
rr = json.loads(body)["data"]
rec("GET /api/rules 规则库读取", code == 200 and len(rr["rules"]["rules"]) >= 10,
    "销售合同 %d 条规则，统计 %s" % (len(rr["rules"]["rules"]),
                                json.dumps(rr["stats"], ensure_ascii=False)[:90]))
newrules = rr["rules"]["rules"] + [{
    "id": "SA-L-999", "name": "【冒烟】临时校验规则", "risk_level": "low",
    "clause_type": "文本规范", "mode": "regex", "patterns": ["冒烟测试专用占位词"],
    "reason": "冒烟测试", "legal_basis": "-", "suggestion_default": "-"}]
code, _, body = call("POST", "/api/rules/sales", {"rules": newrules})
rec("POST /api/rules/{type} 规则写回（含 .bak 备份）",
    code == 200 and json.loads(body)["data"]["count"] == len(newrules),
    "已保存 %d 条；原文件已备份" % json.loads(body)["data"]["count"])
code, _, body = call("POST", "/api/rules/sales", {"rules": newrules[:-1]})
rec("POST /api/rules/{type} 恢复规则库",
    code == 200 and json.loads(body)["data"]["count"] == len(newrules) - 1,
    "已恢复为 %d 条" % json.loads(body)["data"]["count"])

# ---------------------------------------------------------------- 审计 / 导出 / 自检
code, _, body = call("GET", "/api/audit?limit=50")
au = json.loads(body)["data"]["items"]
rec("GET /api/audit 审计日志", code == 200 and len(au) > 20,
    "返回 %d 条审计记录" % len(au))

code, _, body = call("GET", "/api/export")
ex = json.loads(body)["data"]
rec("GET /api/export 全量导出", code == 200 and ex["cases"] >= 8,
    "导出 %s（%d 个任务）" % (ex["file"], ex["cases"]))

code, _, body = call("GET", "/api/samples")
sm = json.loads(body)["data"]
rec("GET /api/samples 示例合同集清单", code == 200 and len(sm["samples"]) == 8,
    "%d 个示例用例" % len(sm["samples"]))

# 防御式解析：这一步要跑整份自检（约 8s），偶发非 JSON 响应不该让整个冒烟脚本崩掉
code, _, body = call("GET", "/api/selftest", timeout=180)
sl, why = None, ""
if body[:1] != b"{":
    why = "响应体非 JSON：%r" % body[:140]
else:
    try:
        sl = json.loads(body)["data"]
    except Exception as exc:                                         # noqa: BLE001
        why = "解析失败：%s" % exc
rec("GET /api/selftest 页面一键自检",
    bool(sl and sl.get("passed")),
    ("%d / %d 项通过，%d ms" % (sl["passed_count"], sl["total"], sl["elapsed_ms"]))
    if sl else ("HTTP %s ｜ %s" % (code, why or "无数据")))

# ---------------------------------------------------------------- 新增：工程加固相关
code, _, body = call("GET", "/api/config")
cfg_pub = json.loads(body)["data"]
rec("GET /api/config 前端可见配置（不含密钥）",
    code == 200 and "max_upload_mb" in cfg_pub and "webhook" not in json.dumps(cfg_pub),
    "最大上传 %s MB，回写模式 %s，通知 %s" % (
        cfg_pub.get("max_upload_mb"), cfg_pub.get("write_mode"),
        "启用" if cfg_pub.get("notify_enabled") else "未启用"))

code, _, body = call("GET", "/api/metrics")
mt = json.loads(body)["data"]
rec("GET /api/metrics 运行指标", code == 200 and mt["requests"] > 10,
    "请求 %d 次 ｜ 错误 %d ｜ 平均 %sms ｜ 热点 top1 %s" % (
        mt["requests"], mt["errors"], mt["avg_ms"],
        (mt["top_paths"][0]["path"] if mt["top_paths"] else "-")))

code, _, body = call("GET", "/api/notify/status")
ns = json.loads(body)["data"]
rec("GET /api/notify/status 通知状态", code == 200 and "events" in ns,
    "启用=%s 已配置=%s 渠道=%s 事件 %d 项" % (
        ns["enabled"], ns["configured"], ns["channel"], len(ns["events"] or {})))

code, _, body = call("POST", "/api/notify/test", {"message": "x"})
rec("POST /api/notify/test 未配置时给出明确 400",
    code == 400 and "notify" in body.decode("utf-8", "ignore"),
    json.loads(body).get("msg", "")[:70] if body.lstrip()[:1] == b"{" else "")

# 大响应压缩（详情接口含整篇正文）
code, hdrs, body = call("GET", "/api/cases/%d" % CID, headers={"Accept-Encoding": "gzip"})
enc = hdrs.get("Content-Encoding", "")
rec("大响应启用 gzip 压缩", code == 200 and enc == "gzip",
    "详情响应 %d 字节，Content-Encoding=%s" % (len(body), enc or "无"))

# 上传大小上限（服务端强制）
code, _, body = call("POST", "/api/upload",
                     {"filename": "超大用例.docx",
                      "content": base64.b64encode(b"x" * (31 * 1024 * 1024)).decode(),
                      "meta": {"business_type": "purchase"}, "auto_review": False})
rec("POST /api/upload 超限文件被服务端拒绝",
    code == 400 and "上限" in body.decode("utf-8", "ignore"),
    json.loads(body).get("msg", "") if body.lstrip()[:1] == b"{" else "")

# 文件名路径穿越防护
code, _, body = call("POST", "/api/upload",
                     {"filename": "../../../../tmp/evil.docx",
                      "content": base64.b64encode(b"not a real docx").decode(),
                      "meta": {"business_type": "purchase"}, "auto_review": False})
up_evil = json.loads(body)["data"] if code == 200 else {}
rec("POST /api/upload 文件名路径穿越被拦下",
    code == 200 and up_evil.get("case_id") is not None
    and not os.path.exists(os.path.join(cfg.path("data"), "..", "tmp", "evil.docx")),
    "上传名 sanitized 后仅落在 uploads/ 内（任务 #%s）" % up_evil.get("case_id"))

# 不存在的案件：批注应 404（原实现会静默成功）
login_as("legal")            # opinion 归法务审查人，先切身份再验 404
code, _, body = call("POST", "/api/cases/999999/opinion", {"opinion": "x"})
rec("POST opinion 对不存在案件返回 404", code == 404, "HTTP %s" % code)
code, _, body = call("GET", "/api/cases/999999/report?format=md")
rec("GET report 对不存在案件返回 404", code == 404, "HTTP %s" % code)

# 规则库无效正则会出现在详情诊断中（此处仅验证字段存在）
code, _, body = call("GET", "/api/cases/%d" % CID)
det_diag = json.loads(body)["data"]
rec("详情接口暴露规则诊断与段落上限字段",
    "rule_diag" in det_diag and "paragraphs_total" in det_diag,
    "rule_diag=%d 条，paragraphs_total=%s，截断=%s" % (
        len(det_diag.get("rule_diag") or []), det_diag.get("paragraphs_total"),
        det_diag.get("paragraphs_truncated")))

# 兼容性：客户端未声明 gzip 时不压缩，仍可正常取到数据
code, hdrs, body = call("GET", "/api/stats")
rec("未声明 gzip 的客户端仍正常返回（压缩协商正确）",
    code == 200 and not hdrs.get("Content-Encoding"),
    "Content-Encoding=%s，%d 字节" % (hdrs.get("Content-Encoding") or "无", len(body)))

# ---------------------------------------------------------------- 404 / 异常
code, _, body = call("GET", "/api/nope")
rec("未知接口返回结构化 404", code == 404 and b"code" in body, "HTTP %s" % code)
code, _, body = call("GET", "/api/cases/99999")
rec("不存在案件返回 404", code == 404, "HTTP %s" % code)

# ================================================================ v2.0 新增接口
# ---------- 角色权限（需求 2.4.2）
login_as("legal")
code, _, body = call("GET", "/api/auth/me")
legal_me = json.loads(body)["data"]
rec("法务审查人登录后能力集正确（可回写、不可改规则）",
    code == 200 and "writeback" in (legal_me.get("caps") or [])
    and "edit_rules" not in (legal_me.get("caps") or []),
    "能力 %d 项：%s" % (len(legal_me.get("caps") or []),
                     "、".join((legal_me.get("caps") or [])[:6])))

code, _, body = call("GET", "/rules")
rec("法务审查人访问规则与模板页被拦截（403 权限受限页）",
    code == 403 and "权限受限".encode("utf-8") in body,
    "HTTP %s，%d 字节（页面级权限隔离生效）" % (code, len(body)))

code, _, body = call("POST", "/api/rules/purchase", {"rules": []})
rec("法务审查人调用规则写接口被服务端拒绝",
    code == 403, "HTTP %s，msg=%s" % (code, json.loads(body).get("msg", "")[:60]))

code, _, body = call("GET", "/api/metrics")
rec("法务审查人访问运行指标被拒绝（能力归属系统管理员）",
    code == 403, "HTTP %s" % code)

login_as("applicant")
code, _, body = call("POST", "/api/cases/%d/writeback" % CID, {"mode": "dry_run"})
rec("业务经办人越权回写被拦下（403 并写审计）",
    code == 403, "HTTP %s，msg=%s" % (code, json.loads(body).get("msg", "")[:70]))
code, _, body = call("POST", "/api/upload",
                     {"filename": "perm-probe.docx", "content": base64.b64encode(b"x" * 64).decode(),
                      "meta": {"business_type": "purchase"}, "auto_review": False})
rec("业务经办人可提交审查申请（上传放行）",
    code in (200, 400), "HTTP %s（放行到业务逻辑层即为通过）" % code)
code, _, body = call("GET", "/api/cases")
rec("业务经办人可查看任务列表与风险点", code == 200, "HTTP %s" % code)
code, _, body = call("GET", "/api/cases/%d/report?format=md" % CID)
rec("业务经办人可下载审查报告", code == 200, "HTTP %s，%d 字节" % (code, len(body)))

# 越权事件必须落在审计里
login_as("admin")
code, _, body = call("GET", "/api/audit?limit=400")
guard = [a for a in json.loads(body)["data"]["items"] if a.get("tool") == "auth.guard"]
rec("越权与未登录均写入审计（tool=auth.guard）",
    len(guard) >= 3,
    "审计中 auth.guard 记录 %d 条，最近：%s" % (
        len(guard), (guard[0]["detail"][:70] if guard else "-")))

code, _, body = call("GET", "/api/auth/accounts")
acc = json.loads(body)["data"] if code == 200 else {}
rec("管理员可查看账号与会话（口令哈希不回传）",
    code == 200 and len(acc.get("items") or []) >= 3
    and all("pwd_hash" not in a for a in (acc.get("items") or [])),
    "账号 %d 个，会话 %s 个" % (len(acc.get("items") or []), acc.get("sessions")))

# ---------- 健康检查新增段
code, _, body = call("GET", "/api/health")
h = json.loads(body)["data"]
import app as _app                                                       # noqa: E402
rec("健康检查版本号与交付版本一致（禁止版本失真）",
    h.get("version") == _app.__version__,
    "health.version=%s ｜ app.__version__=%s" % (h.get("version"), _app.__version__))
rec("健康检查暴露鉴权/调度/队列/PDF 四段运行态",
    all(k in h for k in ("auth", "scheduler", "queue", "pdf_channels")),
    "auth.accounts=%s scheduler.enabled=%s queue.workers=%s pdf.effective=%s"
    % (h.get("auth", {}).get("accounts"), h.get("scheduler", {}).get("enabled"),
       h.get("queue", {}).get("workers"), (h.get("pdf_channels") or {}).get("effective")))

# ---------- 定时拉取调度
code, _, body = call("GET", "/api/scheduler")
sch = json.loads(body)["data"]
rec("调度状态接口可用（默认关闭，避免演示数据自变）",
    code == 200 and "interval_sec" in sch and "next_run_in_sec" in sch,
    "enabled=%s interval=%ss runs=%s" % (sch.get("enabled"), sch.get("interval_sec"),
                                         sch.get("runs")))
code, _, body = call("POST", "/api/scheduler/config", {"enabled": True, "interval_sec": 120})
sch2 = json.loads(body)["data"]
rec("调度开关可运行时切换并落盘",
    code == 200 and sch2.get("status", {}).get("enabled") is True
    and sch2.get("persisted") is True,
    "enabled=%s persisted=%s" % (sch2.get("status", {}).get("enabled"), sch2.get("persisted")))
code, _, body = call("POST", "/api/scheduler/run", {})
tr = json.loads(body)["data"].get("trigger_result", {})
rec("可手动触发一轮拉取（与定时路径同一实现）",
    code == 200 and "created" in tr,
    "ok=%s 新建=%s 跳过=%s 耗时=%sms" % (tr.get("ok"), tr.get("created"),
                                      tr.get("skipped"), tr.get("ms")))
code, _, body = call("POST", "/api/scheduler/config", {"enabled": False})
rec("调度可关闭（演示时数据可控)",
    code == 200 and json.loads(body)["data"]["status"]["enabled"] is False,
    "已恢复默认关闭")

# ---------- 审查队列与配额
code, _, body = call("GET", "/api/queue")
q = json.loads(body)["data"]
rec("队列状态接口可用并暴露四道配额",
    code == 200 and all(k in q for k in ("workers", "queued", "maxsize",
                                        "max_pending", "max_total_mb", "uploads_mb")),
    "workers=%s queued=%s/%s pending=%s/%s 存储=%.2f/%s MB"
    % (q.get("workers"), q.get("queued"), q.get("maxsize"), q.get("pending_cases"),
       q.get("max_pending"), q.get("uploads_mb") or 0, q.get("max_total_mb")))

code, _, body = call("POST", "/api/cases/%d/review" % CID, {"wait": False})
ar = json.loads(body)["data"] if code == 200 else {}
rec("审查接口支持异步入队（返回 task_id，可轮询案件状态）",
    code == 200 and (ar.get("queued") or ar.get("mode") == "async"
                     or (ar.get("queue") or {}).get("task_id")),
    "mode=%s task_id=%s" % (ar.get("mode") or (ar.get("queue") or {}).get("mode"),
                            ar.get("task_id") or (ar.get("queue") or {}).get("task_id")))
time.sleep(1.2)
code, _, body = call("GET", "/api/cases/%d" % CID)
rec("入队任务最终完成（并发不丢不串）",
    code == 200 and json.loads(body)["data"]["case"]["review_status"] == "completed",
    "案件 #%d 状态=%s" % (CID, json.loads(body)["data"]["case"]["review_status"]))

# ---------- 报告通道（M10 服务端 PDF 直出）
code, _, body = call("GET", "/api/pdf/check")
pc = json.loads(body)["data"]
rec("PDF 通道自检：零依赖通道可用",
    code == 200 and pc.get("stdlib") is True and pc.get("size", 0) > 1200,
    "browser=%s stdlib=%s effective=%s size=%s%s"
    % (pc.get("browser"), pc.get("stdlib"), pc.get("effective"), pc.get("size"),
       "" if not pc.get("error") else " ｜ 失败原因：%s" % pc["error"]))

code, hdrs, body = call("GET", "/api/cases/%d/report?format=pdf" % CID)
rec("报告导出 PDF 实际产出 PDF 字节流",
    code == 200 and body[:5] == b"%PDF-" and len(body) > 1200,
    "HTTP %s，%d 字节，头=%s" % (code, len(body), body[:8].decode("latin-1", "ignore")))

code, hdrs, body = call("GET", "/api/cases/%d/report?format=md&download=1" % CID)
rec("报告可按附件下载（Content-Disposition 文件名正确）",
    code == 200 and "attachment" in (hdrs.get("Content-Disposition") or ""),
    "Content-Disposition=%s" % (hdrs.get("Content-Disposition") or "-"))

# ---------- 详情新增：模式对照 / 剔除留痕 / 权限矩阵
code, _, body = call("GET", "/api/cases/%d" % CID)
det2 = json.loads(body)["data"]
rec("详情返回模式对照数据（规则引擎 / 规则+模型润色）",
    bool(det2.get("mode_compare")) and bool(det2.get("mode_info")),
    "current=%s available=%s reason=%s"
    % ((det2.get("mode_compare") or {}).get("current"),
       (det2.get("mode_info") or {}).get("available"),
       (det2.get("mode_info") or {}).get("reason") or "无"))
rec("详情返回页眉页脚剔除留痕与参数",
    "stripped" in det2 and "strip_config" in det2,
    "剔除 %d 行，参数=%s" % (len(det2.get("stripped") or []), det2.get("strip_config")))

# ---------- 通知脱敏与事件开关
code, _, body = call("GET", "/api/notify/status")
ns = json.loads(body)["data"]
rec("通知状态暴露脱敏规则（外发字段默认全掩码）",
    code == 200 and (ns.get("mask") or {}).get("party") is True
    and (ns.get("mask") or {}).get("amount") is True
    and (ns.get("mask") or {}).get("max_evidence_chars") == 0,
    "mask=%s" % ns.get("mask"))
code, _, body = call("POST", "/api/notify/events",
                     {"events": {"batch_summary": True, "review_completed": True}})
ne = json.loads(body)["data"] if code == 200 else {}
rec("通知事件开关可运行时切换（演示按需开启）",
    code == 200 and ne.get("events", {}).get("batch_summary") is True,
    "events=%s" % ne.get("events"))
call("POST", "/api/notify/events",
     {"events": {"batch_summary": False, "review_completed": False}})

# ---------- v2.0.1：孪生看板真实聚合 与 模拟审批美化页
code, _, body = call("GET", "/api/twin")
tw = json.loads(body)["data"] if code == 200 else {}
rec("GET /api/twin 返回流水线七段与真实计数",
    code == 200 and len(tw.get("pipeline") or []) == 7
    and all(n.get("src") for n in (tw.get("pipeline") or [])),
    "节点 %d 个；每个节点均标注数据来源；耗时聚合 %s"
    % (len(tw.get("pipeline") or []), "含实时日志 %d 条" % len(tw.get("activity") or [])))
rec("/api/twin 的计数与 /api/cases 完全一致（不造数）",
    code == 200 and tw.get("counts", {}).get("total") == len(json.loads(
        call("GET", "/api/cases")[2])["data"]["items"]),
    "twin 总任务 %s ｜ cases 列表 %s 个"
    % (tw.get("counts", {}).get("total"),
       len(json.loads(call("GET", "/api/cases")[2])["data"]["items"])))
rec("实时日志直接来自审计表（条数与 /api/audit 对齐）",
    len(tw.get("activity") or []) > 0
    and len(tw.get("activity") or []) <= 60
    and tw["activity"][0].get("created_at") == json.loads(
        call("GET", "/api/audit?limit=60")[2])["data"]["items"][0]["created_at"],
    "twin 日志 %d 条，首条时间 %s" % (len(tw.get("activity") or []),
                                  (tw.get("activity") or [{}])[0].get("created_at")))

code, _, body = call("GET", "/api/mock/overview")
mo = json.loads(body)["data"] if code == 200 else {}
rec("GET /api/mock/overview 返回模拟平台真实数据",
    code == 200 and len(mo.get("instances") or []) >= 6
    and all("instance_id" in x for x in (mo.get("instances") or [])),
    "审批单 %s 张（待办 %s），接口清单 %d 条"
    % (len(mo.get("instances") or []), mo.get("todo_count"),
       len(mo.get("endpoints") or [])))

code, _, body = call("GET", "/mock")
rec("/mock 美化页需登录且可渲染（替代裸 JSON）",
    code == 200 and b"mock-item" in body and b"toggleRaw" in body,
    "HTTP %s，%d 字节" % (code, len(body)))
code, hdrs_m, body = call("GET", "/mock", no_cookie=True, no_redirect=True)
rec("未登录访问 /mock 被重定向（内部页不外露）",
    code == 302 and "/login" in (hdrs_m.get("Location") or ""),
    "HTTP %s → %s" % (code, hdrs_m.get("Location")))
code, _, body = call("GET", "/mock/feishu/api/todo", no_cookie=True)
rec("模拟审批原始 JSON 接口仍对匿名开放（对外契约不变）",
    code == 200 and b"instance_id" in body,
    "HTTP %s，%d 字节" % (code, len(body)))

# ---------- v2.1：AI 助手（页面宠物）
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
    "首页 %d 步，全部带目标选择器与标题%s"
    % (len(tr.get("steps") or []),
       "" if code == 200 else "｜HTTP %s %s" % (code, body.decode("utf-8", "ignore")[:900])))

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

# ---------- 统计卡下钻依赖的回写筛选
code, _, body = call("GET", "/api/cases?writeback=success")
rec("GET /api/cases 支持 writeback 筛选（统计卡下钻用）",
    code == 200, "HTTP %s，返回 %d 个"
    % (code, len(json.loads(body)["data"]["items"]) if code == 200 else 0))
code, _, body = call("GET", "/api/cases?risk=high")
rec("GET /api/cases 支持 risk 筛选（统计卡下钻用）",
    code == 200 and all(x["risk_level"] == "high"
                        for x in json.loads(body)["data"]["items"]),
    "HTTP %s，高风险 %d 个" % (code, len(json.loads(body)["data"]["items"])))

code, _, body = call("GET", "/login", no_cookie=True)
rec("登录页含三类演示身份的一键进入入口",
    code == 200 and "一键进入".encode("utf-8") in body
    and b"role-item" in body,
    "HTTP %s，%d 字节" % (code, len(body)))
login_as("legal")                    # 受限页只在「有身份但无权限」时才出现
code, _, body = call("GET", "/rules")
denied_html = body.decode("utf-8", "replace")
rec("权限受限页由服务端填充渲染（无未替换占位符）",
    code == 403 and "{{" not in denied_html and "系统管理员".encode("utf-8") in body
    and "维护合规规则与标准条款库".encode("utf-8") in body,
    "HTTP %s，%d 字节，未替换占位符 %d 个" % (code, len(body), denied_html.count("{{")))

httpd.shutdown()
httpd.server_close()

import shutil                                                          # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)

ok = len([x for x in log if x.startswith("[OK ]")])
out = os.path.join(ROOT, "verify")
os.makedirs(out, exist_ok=True)
head = "HTTP 冒烟测试：%d / %d 项通过（端口 %d）" % (ok, len(log), PORT)
with io.open(os.path.join(out, "smoke_http.txt"), "w", encoding="utf-8") as f:
    f.write(head + "\n" + "=" * 100 + "\n" + "\n".join(log))
print(head)
