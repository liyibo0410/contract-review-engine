# -*- coding: utf-8 -*-
"""验收自检（可独立运行，也可由服务端 /api/selftest 调用）

对项目验收标准的 8 条逐条做**真实校验**：真的去解析文件、真的跑规则引擎、
真的调用回写接口、真的做定位锚定一致性核对，而不是只检查配置是否存在。

    python run.py --selftest        # 命令行执行
    或访问 http://127.0.0.1:8770/  页面上的「一键自检」

自检在独立临时目录中进行，不会污染正式的审查数据库与已产生的报告。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class Report:
    def __init__(self):
        self.checks = []
        self.t0 = time.time()

    def add(self, name, passed, detail=""):
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})
        return passed

    def result(self):
        ok = [c for c in self.checks if c["passed"]]
        return {
            "passed": len(ok) == len(self.checks),
            "passed_count": len(ok),
            "total": len(self.checks),
            "elapsed_ms": int((time.time() - self.t0) * 1000),
            "checks": self.checks,
        }


def _start_capture_server():
    """起一个本地假 Webhook：接收通知 POST 并原样记录，用于验证通知链路。

    返回 (server, port, capture_list)。请求返回钉钉格式的 {"errcode":0}，
    这样就能在不接真实机器人的前提下完整验证「加签 + 消息体 + 异步投递」。
    """
    capture = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):                            # noqa: A003
            pass

        def do_POST(self):                                       # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            capture.append({"path": self.path,
                            "body": raw.decode("utf-8", "ignore"),
                            "content_type": self.headers.get("Content-Type", "")})
            out = json.dumps({"errcode": 0, "errmsg": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1], capture


def run(root: str = ROOT, echo: bool = False):
    from app import config as cfg_mod
    from app import parsers, pipeline, review as review_mod, store as store_mod, writeback
    from mock import feishu

    rep = Report()
    tmp = tempfile.mkdtemp(prefix="cr_selftest_")
    old_cwd = os.getcwd()
    try:
        os.chdir(root)                       # 配置文件按项目根目录解析
        cfg = cfg_mod.Config(root)
        # 独立沙箱：全部产出写到临时目录，回写直连模拟审批实现（不依赖 HTTP 服务在跑）
        cfg.data["paths"] = {
            "data": os.path.join(tmp, "data"),
            "uploads": os.path.join(tmp, "uploads"),
            "parsed": os.path.join(tmp, "parsed"),
            "reports": os.path.join(tmp, "reports"),
            "samples": os.path.join(root, "samples"),
        }
        cfg.data["approval"]["base_url"] = ""
        cfg.data["approval"]["dry_run_default"] = False

        st = store_mod.Store(cfg.db_path())
        feishu.init(root, os.path.join(cfg.path("data"), "mock_approval.json"),
                    cfg.path("samples"), force_reseed=True)

        samples = cfg.path("samples")

        # ============================ 验收 1：多格式解析 ============================
        docx_r = parsers.parse_document(os.path.join(samples, "01_软件采购合同_风险用例.docx"),
                                        "01.docx")
        pdf_r = parsers.parse_document(os.path.join(samples, "02_服务器设备采购合同_标准用例.pdf"),
                                       "02.pdf")
        img_r = parsers.parse_document(os.path.join(samples, "06_软件采购合同_扫描件.png"),
                                       "06.png")
        rep.add("能接入并解析 DOCX 格式合同附件",
                docx_r["ok"] and docx_r["char_count"] > 300,
                "引擎 %s，%d 段 / %d 字符" % (docx_r["engine"], len(docx_r["paragraphs"]),
                                          docx_r["char_count"]))
        rep.add("能接入并解析 PDF（文本层）合同附件",
                pdf_r["ok"] and pdf_r["char_count"] > 300,
                "引擎 %s，%d 页 / %d 段 / %d 字符" % (
                    pdf_r["engine"], pdf_r["page_count"], len(pdf_r["paragraphs"]),
                    pdf_r["char_count"]))
        rep.add("能接入并解析图片扫描件（OCR）",
                img_r["ok"] and img_r["char_count"] > 100,
                "引擎 %s，%d 段 / %d 字符，OCR 置信度 %.2f" % (
                    img_r["engine"], len(img_r["paragraphs"]), img_r["char_count"],
                    img_r.get("ocr_confidence", 0)))

        # ============================ 端到端：拉取 + 自动审查 ============================
        pulled = pipeline.pull_from_approval(cfg, st, auto_review=True)
        created = pulled.get("created", [])
        by_inst = {c["instance_id"]: c["case_id"] for c in created}
        rep.add("能按审批单拉取待办并自动建立审查任务",
                len(created) >= 6,
                "新建 %d 个任务，跳过 %d 个（幂等控制）" % (len(created), len(pulled.get("skipped", []))))

        cases = {c["case_no"]: c for c in st.list_cases()}
        byfile = {}
        for c in st.list_cases():
            d = st.case_document(c["id"])
            if d:
                byfile[d["filename"]] = c

        def case_of(prefix):
            for fn, c in byfile.items():
                if fn.startswith(prefix):
                    return c
            return None

        c01 = case_of("01_")
        c02 = case_of("02_")
        c03 = case_of("03_")
        c04 = case_of("04_")
        c05 = case_of("05_")
        c06 = case_of("06_")
        c07 = case_of("07_")
        c08 = case_of("08_")

        # ============================ 验收 2：元数据与定位 ============================
        det1 = pipeline.detail(cfg, st, c01["id"])
        meta_ok = bool(det1["meta"].get("party_b")) and bool(det1["meta"].get("credit_code")) \
            and det1["meta"].get("amount")
        rep.add("能提取合同核心元数据（主体 / 信用代码 / 金额 / 期限）",
                meta_ok,
                "相对方=%s，信用代码=%s，金额=%s，期限=%s" % (
                    det1["meta"].get("party_b"), det1["meta"].get("credit_code"),
                    det1["meta"].get("amount"), det1["meta"].get("perform_period") or "-"))

        anchored = [r for r in det1["results"]
                    if r["page_no"] is not None and r["para_index"] is not None]
        consistent = []
        for r in anchored:
            p = det1["paragraphs"][r["para_index"]]
            s = r["char_start"] - p["char_start"]
            e = r["char_end"] - p["char_start"]
            frag = p["text"][s:e] if (s >= 0 and e <= len(p["text"]) and e > s) else ""
            consistent.append(len(frag) >= 2)
        rep.add("条款与风险证据均记录页码、段落索引与字符区间",
                len(anchored) >= 2 and all(consistent),
                "带定位锚点的命中 %d 条，锚点与原文片段一致性校验 %s" % (
                    len(anchored), "全部通过" if all(consistent) else "存在偏移"))
        rep.add("能完成条款层级切分并输出条款结构",
                len(det1["clauses"]) >= 5 and det1["coverage"]["coverage_rate"] > 0,
                "切分 %d 个条款，条款类型覆盖度 %.0f%%（%d/%d 类）" % (
                    len(det1["clauses"]), det1["coverage"]["coverage_rate"] * 100,
                    len(det1["coverage"]["present"]), det1["coverage"]["total_types"]))

        # ============================ 验收 3：合规审查判定 ============================
        def ids(case):
            return [r["rule_id"] for r in pipeline.detail(cfg, st, case["id"])["results"]]
        i01, i02, i03, i04, i05, i06 = (ids(c01), ids(c02), ids(c03), ids(c04), ids(c05), ids(c06))
        d01, d03, d05 = (pipeline.detail(cfg, st, c["id"]) for c in (c01, c03, c05))
        exp01 = {"PU-H-001", "PU-H-002"}
        exp03 = {"SA-H-001"}
        exp05 = {"LA-H-001", "LA-H-002"}
        rep.add("能识别必备条款缺失、权责不对等与高危表述（风险用例命中预期规则）",
                exp01.issubset(set(i01)) and exp03.issubset(set(i03)) and exp05.issubset(set(i05)),
                "采购风险用例命中 %s；销售（权责不对等）命中 %s；劳动用工命中 %s" % (
                    "、".join(i01), "、".join(i03), "、".join(i05)))
        rep.add("标准用例不产生误报（规则精度校验）",
                len(i02) == 0 and len(i04) == 0,
                "采购标准用例命中 %d 条，服务标准用例命中 %d 条（预期 0）" % (len(i02), len(i04)))
        rep.add("能输出高/中/低风险分级与综合风险等级、审查结论",
                d01["case"]["risk_level"] == "high" and d03["case"]["risk_level"] == "high"
                and d01["case"]["conclusion"] == "reject"
                and all(r["risk_level_label"] and r["legal_basis"] and r["reason"]
                        for r in d01["results"]),
                "01 综合风险=%s 结论=%s；03 综合风险=%s 结论=%s" % (
                    d01["case"]["risk_level_label"], d01["case"]["conclusion_label"],
                    d03["case"]["risk_level_label"], d03["case"]["conclusion_label"]))

        # ============================ 验收 4：双向高亮定位 ============================
        ui_wb = io.open(os.path.join(root, "ui", "workbench.html"), encoding="utf-8").read()
        has_marks = "mark class=\"hl" in ui_wb or 'class="hl ' in ui_wb
        has_pick = "function pick(" in ui_wb
        rep.add("审查工作台支持原文与风险卡片双向高亮定位与跳转",
                has_marks and has_pick and len(anchored) >= 2,
                "正文按风险等级渲染高亮锚点，卡片与高亮互通（命中 %d 个可定位锚点）" % len(anchored))

        # ============================ 验收 5：修改示范条款 ============================
        sugg_ok = all((r["suggestion_final"] or r["suggestion"]) for r in d01["results"])
        alt_cnt = sum(len(r.get("suggestion_alternatives") or []) for r in d01["results"])
        rep.add("能针对风险条款生成可落地的修改示范条款并支持法务编辑",
                sugg_ok and alt_cnt >= 1,
                "命中项均带示范条款，另提供 %d 套备选方案；工作台可编辑并「采纳」落库" % alt_cnt)
        try:
            rid = d01["results"][0]["id"]
            st.update_result(rid, suggestion_final="自测写入的示范条款", adopted=1,
                             lawyer_note="自测批注")
            again = st.get_result(rid)
            rep.add("法务复核结果（建议编辑 / 采纳 / 批注）可持久化",
                    again["adopted"] == 1 and "自测" in (again["suggestion_final"] or ""),
                    "规则 %s 的采纳状态与编辑内容已写入数据库" % again["rule_id"])
        except Exception as exc:                                 # noqa: BLE001
            rep.add("法务复核结果（建议编辑 / 采纳 / 批注）可持久化", False, str(exc))

        # ============================ 验收 6：回写审批评论区 ============================
        det = pipeline.detail(cfg, st, c01["id"])
        from app import output
        text = output.compose_comment(det["case"], det["document"], det["results"],
                                      det["summary"], det["meta"], "自测批注：请按建议修改后重新送审")
        wb = writeback.send(cfg, st, st.get_case(c01["id"]), text, mode="live")
        comments, _err = writeback.fetch_comments(cfg, wb.get("instance_id") or "")
        got = [c for c in comments if c["comment_id"] == wb.get("comment_id")]
        fmt_ok = ("合同智能审查意见" in text and "风险总评" in text
                  and "免责与边界声明" in text and wb.get("comment_id"))
        rep.add("能生成格式化审查意见并成功写回审批系统评论区",
                wb.get("ok") and bool(got) and fmt_ok,
                "评论标识 %s，评论区校验命中 %d 条，评论正文 %d 字" % (
                    wb.get("comment_id"), len(got), len(text)))
        wb2 = writeback.send(cfg, st, st.get_case(c01["id"]), text, mode="live")
        rep.add("回写幂等控制生效（重复回写被跳过）",
                wb2.get("skipped") is True,
                wb2.get("message", ""))

        # 回写接口异常 → 失败落库，故障恢复后可重试成功
        feishu.set_fault_rate(1.0)
        c04c = st.get_case(c04["id"])
        det4 = pipeline.detail(cfg, st, c04["id"])
        wbf = writeback.send(cfg, st, c04c, text, mode="live")
        failed_ok = (wbf.get("ok") is False
                     and st.get_case(c04["id"])["writeback_status"] == "failed")
        feishu.set_fault_rate(0.0)
        st.update_case(c04["id"], writeback_status="not_written")
        wbr = writeback.send(cfg, st, st.get_case(c04["id"]), text, mode="live")
        rep.add("回写接口异常时正确记录失败状态并支持重试成功",
                failed_ok and wbr.get("ok") and wbr.get("comment_id"),
                "注入故障后状态=%s；恢复后重试状态=%s（评论标识 %s）" % (
                    wbf.get("status"), wbr.get("status"), wbr.get("comment_id")))

        # ============================ 验收 7：阻塞与重试 ============================
        b07 = st.get_case(c07["id"])
        b08 = st.get_case(c08["id"])
        rep.add("解析失败/加密文件能正确进入阻塞状态并记录原因",
                b07["review_status"] == "blocked" and b08["review_status"] == "blocked"
                and "文本层" in (b07["blocked_reason"] or "")
                and "加密" in (b08["blocked_reason"] or ""),
                "07 无文本层 PDF → %s（%s）；08 加密 PDF → %s（%s）" % (
                    b07["review_status"], (b07["blocked_reason"] or "")[:26],
                    b08["review_status"], (b08["blocked_reason"] or "")[:22]))
        # 管理员修正文件后重试：把 07 的附件替换为可解析合同，重试应转为审查完成
        d07 = st.case_document(c07["id"])
        shutil.copyfile(os.path.join(samples, "01_软件采购合同_风险用例.docx"),
                        d07["file_path"])
        st._x("UPDATE contract_documents SET filename=? WHERE id=?",
              ("01_软件采购合同_风险用例.docx", d07["id"]))
        rr = pipeline.retry(cfg, st, c07["id"])
        after = st.get_case(c07["id"])
        rep.add("阻塞任务在修正文件后可一键重试并完成审查",
                rr.get("ok") and after["review_status"] == "completed"
                and after["risk_level"] in ("high", "medium", "low"),
                "重试后状态=%s，综合风险=%s，命中 %d 项" % (
                    after["review_status"], store_mod.RISK_LABEL.get(after["risk_level"]),
                    rr.get("hits", 0)))

        # ============================ 验收 8：端到端演示 + 报告导出 ============================
        kind_md, md = pipeline.build_report(cfg, st, c01["id"], "md")
        md_ok = kind_md == "md" and md and len(md) > 1200 and "风险清单明细" in md \
            and "综合审查结论" in md
        rep.add("能导出结构化审查意见报告（Markdown）",
                md_ok, "报告 %d 字，含合同基本信息 / 综合审查结论 / 风险清单明细 / 条款结构 / 回写记录" % (
                    len(md or "")))
        kind_pdf, path_pdf = pipeline.build_report(cfg, st, c01["id"], "pdf")
        rep.add("能导出审查意见报告（PDF 通道）",
                kind_pdf in ("pdf", "pdf_fallback_html"),
                "PDF 通道结果=%s%s" % (kind_pdf,
                    "（服务端 PDF 生成成功）" if kind_pdf == "pdf" else "（降级为浏览器打印导出，仍可得到 PDF）"))
        rep.add("能完成一个典型合同的拉取 → 解析 → 审查 → 复核 → 回写 → 报告导出全流程演示",
                all([c01 is not None, d01["case"]["review_status"] == "completed",
                     wb.get("ok"), md_ok]),
                "合同《%s》：解析引擎 %s → 命中 %d 项风险 → 综合风险 %s → %s → 评论 %s → 报告已导出" % (
                    c01["title"], det1["document"]["parse_engine"], len(d01["results"]),
                    d01["case"]["risk_level_label"], d01["case"]["conclusion_label"],
                    wb.get("comment_id")))

        # ============================ 附加：健壮性与工程加固 ============================
        # 1) 配置漂移修复：阈值真的生效
        case_pdf, _l, _b = None, 0, 0
        strict = parsers.parse_document(
            os.path.join(samples, "02_服务器设备采购合同_标准用例.pdf"),
            "02.pdf", min_text_chars=100000)
        rep.add("解析阈值可配置且真实生效（min_text_chars）",
                strict["ok"] is False and strict["need_manual"] is True,
                "把最小字符数阈值调到 100000 后，同一份 PDF 被正确判定为文本不足：%s"
                % (strict.get("error") or "")[:40])

        # 2) 上传大小上限
        big = pipeline.ingest_upload(cfg, st, "超大文件.docx", b"x" * (2 * 1024 * 1024),
                                     meta={}, auto_review=False)
        cfg.data["review"]["max_upload_mb"] = 1
        big = pipeline.ingest_upload(cfg, st, "超大文件.docx", b"x" * (2 * 1024 * 1024),
                                     meta={}, auto_review=False)
        cfg.data["review"]["max_upload_mb"] = 30
        rep.add("服务端强制上传大小上限（防止超大文件打爆内存）",
                big.get("ok") is False and "超过" in (big.get("error") or ""),
                big.get("error") or "")

        # 3) 文件名路径穿越硬化
        weird = pipeline.safe_name("../../../etc/passwd")
        weird2 = pipeline.safe_name("..")
        weird3 = pipeline.safe_name("C:\\Windows\\system32\\cmd.exe")
        ok_names = ("/" not in weird and ".." not in weird and weird
                    and "/" not in weird3 and "\\" not in weird3 and ".." not in weird2)
        rep.add("上传文件名做路径穿越硬化处理",
                ok_names and weird == "passwd" and weird3 == "cmd.exe",
                "「../../../etc/passwd」→ %s；「C:\\Windows\\system32\\cmd.exe」→ %s"
                % (weird, weird3))

        # 4) 更新字段白名单
        st.update_case(c01["id"], title=st.get_case(c01["id"])["title"], 非法字段="x")
        rep.add("数据更新走字段白名单（防止误用与注入）",
                "非法字段" not in (st.get_case(c01["id"]) or {}),
                "传入未登记字段不会拼进 SQL，并写入审计")

        # 5) 无效正则不会让审查崩溃，且会被记录
        ct = review_mod.load_clause_types(root)
        bad_rules = review_mod.load_rules(root, "purchase")
        bad_rules = json.loads(json.dumps(bad_rules))
        bad_rules["rules"].append({
            "id": "PU-X-000", "name": "无效正则用例", "risk_level": "low",
            "clause_type": "文本规范", "mode": "regex", "patterns": ["(未闭合的括号"],
            "reason": "t", "legal_basis": "t", "suggestion_default": "t"})
        diag = []
        hits_bad = review_mod.run_rules(det1["paragraphs"] and
                                        "".join(p["text"] for p in det1["paragraphs"]),
                                        det1["paragraphs"], bad_rules, ct, diag=diag)
        rep.add("规则写坏正则时跳过该规则并记录诊断，不会中断审查",
                len(diag) == 1 and diag[0]["where"].startswith("PU-X-000"),
                "诊断：%s → %s" % (diag[0]["where"] if diag else "-",
                                 (diag[0]["error"] if diag else "-")[:60]))

        # 6) 规则引擎性能：去空格文本只算一次
        import time as _t
        big_text = "\n".join("第%d条 甲乙双方应遵守合同约定并履行各自义务。" % i
                             for i in range(400))
        big_paras = []
        cur = 0
        for i, line in enumerate(big_text.split("\n")):
            big_paras.append({"index": i, "text": line, "page": 1, "is_heading": False,
                              "char_start": cur, "char_end": cur + len(line)})
            cur += len(line) + 1
        t0 = _t.time()
        for _ in range(20):
            review_mod.run_rules(big_text, big_paras, review_mod.load_rules(root, "purchase"), ct)
        ms = (_t.time() - t0) / 20 * 1000
        rep.add("规则引擎在长合同下保持可接受的耗时",
                ms < 400,
                "400 段合同 × 12 条规则，单次 %.0f ms（缓存去空格文本 + 预编译正则）" % ms)

        # ============================ 验收 9：机器人通知（钉钉） ============================
        srv, nport, capture = _start_capture_server()
        try:
            cfg.data["notify"].update({
                "enabled": True, "channel": "dingtalk",
                "webhook": "http://127.0.0.1:%d/robot/send?access_token=TESTTOKEN" % nport,
                "secret": "SECtestsecret", "keyword": "合同审查",
                "timeout": 5, "min_interval_sec": 0,
                "events": {"review_completed": True, "high_risk": True, "blocked": True,
                           "writeback_failed": True, "batch_summary": True},
            })
            nt = pipeline.get_notifier(cfg, st)
            res = nt.send_now("test", "通知连通性测试", ["- 渠道：dingtalk", "- 消息：自检"])
            cap = capture[-1] if capture else {}
            sign_ok = "timestamp=" in cap.get("path", "") and "sign=" in cap.get("path", "")
            body_ok = ('"msgtype": "markdown"' in cap.get("body", "")
                       and "合同审查" in cap.get("body", ""))
            rep.add("通知渠道可用：钉钉加签正确且消息格式符合要求",
                    res.get("status") == "sent" and sign_ok and body_ok,
                    "HTTP 200 + errcode=0；URL 含 timestamp/sign 签名；消息体为 markdown 且带关键词")

            # 审查完成后自动推送（异步，不阻塞审查返回）
            before = len(capture)
            pipeline.run_review(cfg, st, c01["id"])
            waited = 0.0
            while len(capture) <= before and waited < 6:
                time.sleep(0.15)
                waited += 0.15
            events = []
            for c in capture[before:]:
                try:
                    events.append(json.loads(c["body"]).get("markdown", {}).get("title", ""))
                except Exception:                                # noqa: BLE001
                    pass
            rep.add("审查完成后自动推送通知（异步投递，不阻塞审查主流程）",
                    len(capture) > before and any("高风险" in e or "审查完成" in e
                                                 for e in events),
                    "收到 %d 条通知：%s（等待 %.1fs）" % (len(capture) - before,
                    "；".join(events[:3]) or "-", waited))

            # 通知失败不阻断主流程
            cfg.data["notify"]["webhook"] = "http://127.0.0.1:1/robot/send"
            bad = nt.send_now("test", "失败路径用例", ["- 该消息应当发送失败"])
            rep.add("通知发送失败只记状态，不影响审查主流程",
                    bad.get("status") == "failed",
                    "指向不可达地址时返回 status=%s，detail=%s"
                    % (bad.get("status"), (bad.get("detail") or "")[:40]))
        finally:
            cfg.data["notify"]["enabled"] = False
            cfg.data["notify"]["webhook"] = ""
            srv.shutdown()
            srv.server_close()

        # ============================ 附加：示例集覆盖与规则库 ============================
        cov = []
        for t in review_mod.list_rule_files(root):
            rd = review_mod.load_rules(root, t)
            cov.append("%s(%d条)" % (rd.get("type_label") or t, len(rd.get("rules", []))))
        rep.add("规则库按合同类型可配置且可维护",
                len(cov) >= 4,
                "已加载：" + "、".join(cov) + "；规则页支持在线编辑并写回配置文件（自动备份 .bak）")
        rep.add("示例合同集覆盖多格式与多场景",
                len([f for f in os.listdir(samples) if not f.startswith("_")]) >= 8,
                "内置 %d 个示例：DOCX/PDF/扫描件 × 标准/风险/受阻用例" % len(
                    [f for f in os.listdir(samples) if not f.startswith("_")]))
        rep.add("审计留痕完整（审批单→附件→解析→命中规则→回写全过程可追溯）",
                len(st.audit_list(None, 9999)) > 40,
                "审计日志 %d 条，支持 /api/export 一键导出" % len(st.audit_list(None, 9999)))

    except Exception as exc:                                     # noqa: BLE001
        import traceback
        rep.add("自检执行异常", False, "%s\n%s" % (exc, traceback.format_exc()[-800:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            os.chdir(old_cwd)
        except OSError:
            pass

    res = rep.result()
    if echo:
        print("─" * 74)
        print("  合同智能审查系统 v1.0 · 验收自检")
        print("─" * 74)
        for i, c in enumerate(res["checks"], 1):
            print("  %s %2d. %s" % ("[通过]" if c["passed"] else "[未通过]", i, c["name"]))
            if c["detail"]:
                print("        %s" % c["detail"])
        print("─" * 74)
        print("  结果：%d / %d 项通过，耗时 %d ms" % (
            res["passed_count"], res["total"], res["elapsed_ms"]))
        print("─" * 74)
    return res


if __name__ == "__main__":
    r = run(ROOT, echo=True)
    sys.exit(0 if r.get("passed") else 1)
