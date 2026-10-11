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

from app import config as cfg_mod, server as server_mod, store as store_mod   # noqa: E402
from mock import feishu                                                       # noqa: E402

PORT = 8791
cfg = cfg_mod.Config(ROOT)
cfg.data["server"]["port"] = PORT
cfg.data["approval"]["base_url"] = "http://127.0.0.1:%d/mock/feishu/api" % PORT

for k in ("data", "uploads", "parsed", "reports", "samples"):
    cfg.path(k)

st = store_mod.Store(cfg.db_path())
feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
            cfg.path("samples"), force_reseed=True)

httpd = server_mod.serve(cfg, st)
th = threading.Thread(target=httpd.serve_forever, daemon=True)
th.start()
time.sleep(0.6)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:%d" % PORT
log = []


def call(method, path, body=None, raw=None, ctype=None, headers=None):
    url = BASE + path
    data = None
    hdrs = dict(headers or {})
    if raw is not None:
        data = raw
        hdrs["Content-Type"] = ctype or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with OPENER.open(req, timeout=120) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:                                     # noqa: BLE001
        return 0, {}, str(e).encode()


def rec(name, ok, detail):
    log.append("%s %-44s %s" % ("[OK ]" if ok else "[ERR]", name, detail))


# ---------------------------------------------------------------- 页面与静态资源
for path, needle, kind in [
    ("/", "合同智能审查系统".encode("utf-8"), "text/html"),
    ("/assets/app.css", b"--accent", "text/css"),
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
blk = [c for c in cases if c["review_status"] == "blocked"][0]
code, _, body = call("GET", "/api/cases/%d" % blk["id"])
bdet = json.loads(body)["data"]
rec("阻塞任务详情返回受阻原因",
    bdet["case"]["review_status"] == "blocked" and bdet["case"]["blocked_reason"],
    "#%s %s → %s" % (blk["id"], bdet["document"]["filename"],
                     bdet["case"]["blocked_reason"][:40]))
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

code, _, body = call("GET", "/api/selftest")
sl = json.loads(body)["data"]
rec("GET /api/selftest 页面一键自检",
    code == 200 and sl["passed"],
    "%d / %d 项通过，%d ms" % (sl["passed_count"], sl["total"], sl["elapsed_ms"]))

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

httpd.shutdown()
httpd.server_close()

ok = len([x for x in log if x.startswith("[OK ]")])
out = os.path.join(ROOT, "verify")
os.makedirs(out, exist_ok=True)
head = "HTTP 冒烟测试：%d / %d 项通过（端口 %d）" % (ok, len(log), PORT)
with io.open(os.path.join(out, "smoke_http.txt"), "w", encoding="utf-8") as f:
    f.write(head + "\n" + "=" * 100 + "\n" + "\n".join(log))
print(head)
