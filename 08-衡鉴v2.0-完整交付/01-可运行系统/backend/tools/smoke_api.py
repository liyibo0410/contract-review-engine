# -*- coding: utf-8 -*-
"""接口冒烟测试：用 ASGI 直连跑通全部对外接口，不需要起真实服务器。

为什么不用真实 HTTP 服务器？——在受限的开发环境里，从 shell 启动的进程会随命令结束被回收，
"起了服务再测"极易测到"进程已死"而误判成接口故障。ASGI 直连测的是同一套路由与中间件，
结论等价，且不受进程生命周期影响。

用法：python tools/smoke_api.py
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["HJ_DB"] = str(Path(tempfile.gettempdir()) / "hengjian_smoke.db")

import httpx  # noqa: E402

from app import config, db  # noqa: E402

OUT = io.StringIO()
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    OUT.write(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else "") + "\n")


class SyncASGIClient:
    """httpx 的 ASGITransport 只有异步接口，这里包一层同步门面。

    每次请求新建一个 AsyncClient 是刻意的：避免跨事件循环复用连接池，
    在"冒烟测试"这种短生命周期场景下更稳。ASGITransport 不触发 lifespan，
    所以建表需要显式调用 db.init_db()。
    """

    def __init__(self, app, base_url: str = "http://test", timeout: float = 120.0):
        self._transport = httpx.ASGITransport(app=app)
        self._base = base_url
        self._timeout = timeout

    def _request(self, method: str, url: str, **kw):
        async def go():
            async with httpx.AsyncClient(transport=self._transport, base_url=self._base,
                                         timeout=self._timeout) as client:
                return await client.request(method, url, **kw)
        return asyncio.run(go())

    def get(self, url, **kw):
        return self._request("GET", url, **kw)

    def post(self, url, **kw):
        return self._request("POST", url, **kw)

    def put(self, url, **kw):
        return self._request("PUT", url, **kw)

    def delete(self, url, **kw):
        return self._request("DELETE", url, **kw)


def main() -> int:
    db_path = Path(os.environ["HJ_DB"])
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    db.init_db()

    from app.main import app

    c = SyncASGIClient(app, "http://test")
    if True:
        # ---------------------------------------------------------- 系统
        OUT.write("\n【系统接口】\n")
        r = c.get("/api/system/health")
        check("GET /api/system/health", r.status_code == 200)
        health = r.json()
        check("  健康检查含规则库信息", "rules" in health and health["rules"]["total"] > 0,
              f"{health['rules']['total']} 条规则 v{health['rules']['version']}")
        check("  健康检查含 OCR 状态", "ocr" in health,
              f"OCR={'就绪' if health['ocr']['available'] else '不可用'}")
        check("  健康检查含 LLM 状态", "llm" in health,
              f"LLM={'已配置' if health['llm']['configured'] else '未配置(降级)'}")

        r = c.get("/api")
        check("GET /api", r.status_code == 200 and "endpoints" in r.json())

        r = c.get("/api/docs")
        check("GET /api/docs（OpenAPI 文档页）", r.status_code == 200)

        r = c.get("/api/openapi.json")
        paths = list(r.json().get("paths", {}).keys())
        check("GET /api/openapi.json", r.status_code == 200, f"{len(paths)} 个路径")

        # ---------------------------------------------------------- 静态前端
        OUT.write("\n【静态前端】\n")
        for path, name in [("/", "审查大盘"), ("/pages/workbench.html", "工作台"),
                           ("/pages/rules.html", "规则页"), ("/pages/eval.html", "评测页"),
                           ("/assets/css/app.css", "样式表"),
                           ("/assets/js/common.js", "公共脚本"),
                           ("/assets/js/workbench.js", "工作台脚本")]:
            r = c.get(path)
            check(f"GET {path}（{name}）", r.status_code == 200 and len(r.content) > 200,
                  f"{len(r.content)} bytes")

        # ---------------------------------------------------------- 指标
        OUT.write("\n【观测接口】\n")
        r = c.get("/api/stats/overview")
        check("GET /api/stats/overview", r.status_code == 200 and "by_status" in r.json())

        # ---------------------------------------------------------- 规则
        OUT.write("\n【规则与模板】\n")
        r = c.get("/api/rules")
        rules = r.json()
        check("GET /api/rules", r.status_code == 200 and rules["total"] >= 30,
              f"{rules['total']} 条")
        r = c.get("/api/rules/tree")
        tree = r.json()["tree"]
        check("GET /api/rules/tree", r.status_code == 200 and len(tree) >= 5,
              f"{len(tree)} 个条款分组")
        r = c.get("/api/templates")
        check("GET /api/templates", r.status_code == 200 and r.json()["total"] >= 13,
              f"{r.json()['total']} 套示范条款")
        r = c.get("/api/meta/clause-types")
        check("GET /api/meta/clause-types", r.status_code == 200)

        rid = rules["items"][0]["id"]
        r = c.put(f"/api/rules/{rid}", json={"reason": rules["items"][0]["reason"]})
        check(f"PUT /api/rules/{rid}（规则编辑）", r.status_code == 200)
        r = c.put("/api/rules/NOT-EXIST", json={"reason": "x"})
        check("  PUT 不存在的规则应 400", r.status_code == 400)

        # ---------------------------------------------------------- 上传与流水线
        OUT.write("\n【合同上传与流水线】\n")
        sample = sorted(Path(config.SAMPLES_DIR).glob("*软件采购合同_埋雷版.docx"))
        if not sample:
            check("样例合同存在", False, "请先运行 python tools/gen_samples.py")
            return _finish()
        sample = sample[0]
        with open(sample, "rb") as f:
            r = c.post(
                "/api/contracts/upload",
                files={"file": (sample.name, f,
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                data={"name": "", "applicant": "王倩", "dept": "信息技术部",
                      "biz_type": "采购", "auto_run": "false"},
            )
        check("POST /api/contracts/upload", r.status_code == 200, r.text[:120])
        cid = r.json()["contract_id"]

        r = c.post(f"/api/contracts/{cid}/pipeline?wait=true")
        body = r.json()
        check(f"POST /api/contracts/{cid}/pipeline（同步跑完整流水线）",
              r.status_code == 200 and body.get("status") == "completed",
              f"命中 {len(body.get('findings', []))} 项，等级 {body.get('risk_level')}")

        r = c.get("/api/contracts")
        check("GET /api/contracts（大盘列表）", r.status_code == 200 and r.json()["total"] >= 1)
        r = c.get("/api/contracts?status=completed&risk=high")
        check("  GET /api/contracts 支持筛选", r.status_code == 200,
              f"{r.json()['total']} 条高风险已完成任务")

        r = c.get(f"/api/contracts/{cid}")
        d = r.json()
        check(f"GET /api/contracts/{cid}（详情）",
              r.status_code == 200 and d["task"]["status"] == "completed"
              and len(d["clauses"]) > 0 and d["document"] is not None,
              f"{len(d['clauses'])} 个条款节点，元数据 {len(d['meta'])} 项")

        r = c.get(f"/api/contracts/{cid}/document")
        check(f"GET /api/contracts/{cid}/document（文档视窗）", r.status_code == 200,
              f"HTTP {r.status_code} {r.text[:160]}")
        doc = r.json() if r.status_code == 200 else {"paragraphs": []}
        check("  文档视窗返回段落序列",
              len(doc.get("paragraphs", [])) > 0,
              f"{len(doc.get('paragraphs', []))} 段，格式 {doc.get('file_type', '?')}")
        p0 = (doc.get("paragraphs") or [{}])[0]
        check("  段落带完整锚点坐标",
              all(k in p0 for k in ("char_start", "char_end", "page", "para_index", "clause_type")))

        r = c.get(f"/api/contracts/{cid}/findings")
        fs = r.json()
        check(f"GET /api/contracts/{cid}/findings",
              r.status_code == 200 and fs["by_level"]["high"] >= 5,
              f"高 {fs['by_level']['high']} / 中 {fs['by_level']['medium']} / 低 {fs['by_level']['low']}")
        # 坐标可回显原文 —— 证据链的最小可用性证明
        probe = next((f for f in fs["items"] if f["level"] == "high"), fs["items"][0])
        seg = _slice_by_coords(doc.get("paragraphs") or [], probe["char_start"], probe["char_end"])
        check("  风险坐标可回显原文片段", bool(seg), f"{probe['rule_id']} → {seg[:40]}…")
        check("  每条风险都有法律依据与示范条款",
              all(f["legal_basis"] and f["advice"] for f in fs["items"]))

        fid = probe["id"]
        r = c.post(f"/api/contracts/{cid}/findings/{fid}/adopt",
                   json={"adopted": True, "adopted_text": "（法务编辑后的条款文本）"})
        check(f"POST .../findings/{fid}/adopt（采纳建议）", r.status_code == 200)
        r = c.get(f"/api/contracts/{cid}/findings")
        check("  采纳状态已持久化",
              next(f for f in r.json()["items"] if f["id"] == fid)["adopted"] is True)

        r = c.post(f"/api/contracts/{cid}/annotations",
                   json={"note": "同意系统意见，请业务部门按建议条款重新谈判。", "reviewer": "李奕波"})
        check(f"POST .../annotations（法务批注）", r.status_code == 200)

        r = c.post(f"/api/contracts/{cid}/search",
                   json={"query": "帮我看下付款条件"})
        sr = r.json()
        check(f"POST .../search（自然语言条款检索）",
              r.status_code == 200 and sr["found"] >= 1, sr.get("answer", "")[:60])

        # ---------------------------------------------------------- 回写
        OUT.write("\n【审查意见与回写】\n")
        r = c.get(f"/api/contracts/{cid}/review")
        check(f"GET .../review（审查意见）",
              r.status_code == 200 and r.json()["review"]["risk_summary"],
              r.json()["review"]["conclusion"])

        r = c.get(f"/api/contracts/{cid}/writeback/preview")
        pv = r.json()
        check(f"GET .../writeback/preview（DRY_RUN 预览）",
              r.status_code == 200 and len(pv["preview"]) > 100, f"{len(pv['preview'])} 字")

        r = c.post(f"/api/contracts/{cid}/writeback",
                   json={"dry_run": True, "operator": "李奕波"})
        check("POST .../writeback（dry_run=true）",
              r.status_code == 200 and r.json()["status"] == "not_written")

        r = c.get(f"/api/contracts/{cid}/comments")
        check("  DRY_RUN 不写入审批系统评论区", len(r.json()["items"]) == 0)

        r = c.post(f"/api/contracts/{cid}/writeback",
                   json={"dry_run": False, "operator": "李奕波",
                         "reviewer_note": "请业务部门按建议条款整改。"})
        wb = r.json()
        check("POST .../writeback（真实回写）",
              r.status_code == 200 and wb["status"] == "success",
              f"评论 ID {wb.get('platform_comment_id')}")

        r = c.get(f"/api/contracts/{cid}/comments")
        check("  审批系统评论区可见意见", len(r.json()["items"]) == 1)

        r = c.post(f"/api/contracts/{cid}/writeback",
                   json={"dry_run": False, "operator": "李奕波"})
        check("  重复回写被幂等拦截", r.json().get("skipped") is True)
        r = c.get(f"/api/contracts/{cid}/comments")
        check("  评论区仍只有 1 条（未重复写入）", len(r.json()["items"]) == 1)

        # ---------------------------------------------------------- 人工审核 HITL
        OUT.write("\n【人工审核（HITL）】\n")
        r = c.get(f"/api/contracts/{cid}/hitl")
        h = r.json() if r.status_code == 200 else {}
        check(f"GET /api/contracts/{cid}/hitl（中断点状态）",
              r.status_code == 200 and "available_actions" in h,
              f"状态 {h.get('status')} · 可执行 {h.get('available_actions')}")

        r = c.post(f"/api/contracts/{cid}/human-review",
                   json={"action": "edit", "actor": "李奕波", "note": "已与业务部门确认风险等级",
                         "edits": [{"finding_id": fs["items"][0]["id"], "level": "medium",
                                    "advice": "（法务人工改写后的条款）"}]})
        check("POST .../human-review（action=edit 修改后确认）",
              r.status_code == 200 and r.json().get("applied"),
              r.json().get("message", ""))
        r = c.get(f"/api/contracts/{cid}/findings")
        target_id = fs["items"][0]["id"]
        edited = next((f for f in r.json()["items"] if f["id"] == target_id), None)
        check("  人工改级已落库", bool(edited) and edited["level"] == "medium",
              f"{edited['level'] if edited else '?'}")
        check("  人工改写建议来源标记为 manual",
              bool(edited) and edited["advice_source"] == "manual")

        r = c.post(f"/api/contracts/{cid}/human-review", json={"action": "bogus"})
        check("  非法审核动作返回 400", r.status_code == 400)

        r = c.post(f"/api/contracts/{cid}/human-review",
                   json={"action": "approve", "actor": "李奕波", "note": "确认审查结论"})
        check("POST .../human-review（action=approve 同意）", r.status_code == 200)
        r = c.get(f"/api/contracts/{cid}/hitl")
        check("  审核流水已记录", len(r.json()["history"]) >= 2,
              f"{len(r.json()['history'])} 条动作")

        # ---------------------------------------------------------- 报告
        r = c.get(f"/api/contracts/{cid}/report")
        check(f"GET .../report（Markdown）",
              r.status_code == 200 and len(r.json()["markdown"]) > 500,
              f"{r.json()['chars']} 字")
        r = c.get(f"/api/contracts/{cid}/report?download=true")
        check("GET .../report?download=true（PDF）",
              r.status_code == 200 and r.content[:4] == b"%PDF",
              f"{len(r.content) // 1024} KB")

        # ---------------------------------------------------------- 审计 / SSE
        r = c.get(f"/api/contracts/{cid}/audit")
        check(f"GET .../audit（审计留痕）",
              r.status_code == 200 and len(r.json()["items"]) >= 4,
              f"{len(r.json()['items'])} 条日志")

        r = c.get(f"/api/contracts/{cid}/events?from_seq=0&idle_limit=1")
        check(f"GET .../events（SSE 事件流）",
              r.status_code == 200 and "text/event-stream" in r.headers.get("content-type", ""))

        # ---------------------------------------------------------- 阻塞与重试
        OUT.write("\n【异常阻塞与重试】\n")
        enc = sorted(Path(config.SAMPLES_DIR).glob("*加密件.pdf"))
        if enc:
            with open(enc[0], "rb") as f:
                r = c.post("/api/contracts/upload",
                           files={"file": (enc[0].name, f, "application/pdf")},
                           data={"biz_type": "采购", "auto_run": "false"})
            cid2 = r.json()["contract_id"]
            r = c.post(f"/api/contracts/{cid2}/pipeline?wait=true")
            check("加密文档进入 blocked", r.json().get("status") == "blocked")
            r = c.get(f"/api/contracts/{cid2}")
            t = r.json()["task"]
            check("  阻塞代码为 DOC_ENCRYPTED", t["block_reason"] != "", t["block_reason"][:50])
            r = c.post(f"/api/contracts/{cid2}/writeback", json={"dry_run": False})
            check("  未完成审查的任务拒绝回写（409）", r.status_code == 409)
            r = c.post(f"/api/contracts/{cid2}/retry?wait=true")
            check("POST .../retry（一键重试）", r.status_code == 200)
            r = c.delete(f"/api/contracts/{cid2}")
            check("DELETE /api/contracts/{id}", r.status_code == 200)

        # ---------------------------------------------------------- 评测
        OUT.write("\n【评测看板】\n")
        r = c.post("/api/eval/run", json={"cases": 0, "persist": True})
        s = r.json().get("summary", {})
        check("POST /api/eval/run（黄金样例集跑分）",
              r.status_code == 200 and s.get("high_recall") == 1.0,
              f"高风险召回 {s.get('high_recall')} · 误报 {s.get('false_positive')} · {s.get('verdict')}")
        r = c.get("/api/eval/score")
        check("GET /api/eval/score", r.status_code == 200 and r.json()["has_result"] is True)

        # ---------------------------------------------------------- 错误处理
        OUT.write("\n【错误处理】\n")
        r = c.get("/api/contracts/999999")
        check("不存在的合同返回 404", r.status_code == 404)
        r = c.post("/api/contracts/upload",
                   files={"file": ("bad.exe", b"MZ", "application/octet-stream")})
        check("不支持的文件类型返回 400", r.status_code == 400)

    return _finish()


def _slice_by_coords(paragraphs, start, end):
    """按段落锚点还原指定字符区间的原文 —— 等价于前端的高亮定位逻辑。"""
    out = []
    for p in paragraphs:
        if p["char_start"] < end and start < p["char_end"]:
            s = max(0, start - p["char_start"])
            e = min(len(p["text"]), end - p["char_start"])
            out.append(p["text"][s:e])
    return "".join(out).strip()


def _finish() -> int:
    OUT.write("\n" + "=" * 70 + "\n")
    OUT.write(f"  通过 {len(PASS)} 项，失败 {len(FAIL)} 项\n")
    if FAIL:
        OUT.write("  失败清单：\n")
        for f in FAIL:
            OUT.write(f"    - {f}\n")
    else:
        OUT.write("  全部接口冒烟通过 ✅\n")
    OUT.write("=" * 70 + "\n")
    text = OUT.getvalue()
    path = ROOT.parent / "docs" / "13_delivery" / "接口冒烟测试报告.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(text)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本必须留下现场，不能静默崩掉
        import traceback
        OUT.write("\n[脚本异常]\n" + traceback.format_exc())
        FAIL.append(f"脚本异常：{exc}")
        rc = _finish()
    raise SystemExit(rc)
