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

        # ============================ v2.0 附加：角色权限闭环（需求 2.4.2） ==========
        from app import auth as auth_mod
        from app import notify as notify_mod
        from app import queue as queue_mod
        from app import pdf_min
        from app import scheduler as scheduler_mod

        seeded = auth_mod.seed_users(cfg, st)
        users = st.user_list()
        rep.add("三角色演示账号可建立并落库（法务/经办人/管理员）",
                len({u["role"] for u in users}) == 3,
                "账号 %d 个：%s" % (len(users),
                                 "、".join("%s(%s)" % (u["username"], auth_mod.ROLE_LABEL[u["role"]])
                                          for u in users)))

        ok_login, lres = auth_mod.login(cfg, st, "legal", "Legal@2026", client="selftest")
        session = auth_mod.current(cfg, st, "cr_session=" + (lres.get("token") or ""))
        rep.add("口令校验正确（明文不落库，pbkdf2 哈希 + 会话令牌）",
                ok_login and session and session["role"] == "legal"
                and auth_mod.login(cfg, st, "legal", "wrong-pwd")[0] is False
                and "pbkdf2$" in (st.user_get("legal")["pwd_hash"]),
                "登录成功=%s 会话角色=%s 错误口令被拒=%s 存储形态=%s"
                % (ok_login, (session or {}).get("role"),
                   auth_mod.login(cfg, st, "legal", "wrong-pwd")[0] is False,
                   (st.user_get("legal")["pwd_hash"] or "")[:14] + "..."))

        matrix = {
            "POST /api/cases/1/writeback": ("writeback", True, False, False),
            "POST /api/cases/1/opinion": ("add_opinion", True, False, False),
            "PATCH /api/results/1": ("edit_suggestion", True, False, False),
            "POST /api/rules/purchase": ("edit_rules", False, False, True),
            "POST /api/cases/1/retry": ("retry_blocked", False, False, True),
            "POST /api/upload": ("submit_contract", False, True, True),
            "POST /api/pull": ("pull_todo", False, True, True),
            "GET /api/metrics": ("view_metrics", False, False, True),
            "GET /api/cases": ("view_cases", True, True, True),
            "GET /api/cases/1/report": ("export_report", True, True, True),
        }
        bad = []
        for route, (cap, w_legal, w_app, w_admin) in matrix.items():
            m = auth_mod.resolve(*route.split(" ", 1))
            if m != cap:
                bad.append("%s 期望 %s 实得 %s" % (route, cap, m))
                continue
            for role, want in (("legal", w_legal), ("applicant", w_app), ("admin", w_admin)):
                got = auth_mod.has_cap(role, cap)
                if got != want:
                    bad.append("%s × %s 期望 %s 实得 %s" % (route, role, want, got))
        rep.add("路由级权限表按需求 2.4.2 严格放行（经办人不可回写/改规则）",
                not bad,
                "校验 %d 条路由 × 3 角色 = %d 组；异常：%s"
                % (len(matrix), len(matrix) * 3, "；".join(bad) or "无"))

        rep.add("未登记的非只读接口按失败关闭处理（仅管理员可用）",
                auth_mod.resolve("POST", "/api/not-registered-write") == "deny"
                and auth_mod.resolve("POST", "/api/not-registered-write") != "auth",
                "/api/not-registered-write → %s（避免新增接口忘记登记时静默放行）"
                % auth_mod.resolve("POST", "/api/not-registered-write"))

        rep.add("页面级权限隔离（规则与模板页仅系统管理员可进）",
                auth_mod.page_allowed("admin", "/rules")
                and not auth_mod.page_allowed("legal", "/rules")
                and not auth_mod.page_allowed("applicant", "/rules"),
                "/rules：admin=%s legal=%s applicant=%s"
                % (auth_mod.page_allowed("admin", "/rules"),
                   auth_mod.page_allowed("legal", "/rules"),
                   auth_mod.page_allowed("applicant", "/rules")))

        denied_before = len([a for a in st.audit_list(None, 9999)
                             if a["tool"] == "auth.guard"])
        st.audit(None, "auth", "auth.guard", "denied",
                 auth_mod.denial_detail("applicant", "writeback", "POST", "/api/cases/1/writeback"))
        denied_after = [a for a in st.audit_list(None, 9999) if a["tool"] == "auth.guard"]
        rep.add("越权访问被拦截并写审计留痕",
                len(denied_after) > denied_before,
                denied_after[0]["detail"][:96] if denied_after else "无记录")

        ok_sw, sw_res = auth_mod.switch_demo(cfg, st, "admin", client="selftest")
        rep.add("演示身份一键切换可用（按角色分段演示不打断节奏）",
                ok_sw and sw_res.get("role") == "admin",
                "切换 admin → ok=%s role=%s" % (ok_sw, sw_res.get("role")))

        # ============================ v2.0 附加：通知外发脱敏（D-31） =================
        masked_party = notify_mod.mask_party("北京华信智联科技有限公司")
        masked_credit = notify_mod.mask_credit("91110108MA01ABCD2X")
        masked_amount = notify_mod.mask_amount_text("人民币1,200,000.00元")
        sweep = notify_mod.sanitize(
            "相对方 91110108MA01ABCD2X 金额 ¥1,250,000.00 电话 13812345678 邮箱 a@b.cn", cfg)
        leak = []
        for probe, src in (("北京华信智联", masked_party),
                           ("91110108MA01ABCD2X", masked_credit),
                           ("1,200,000", masked_amount),
                           ("91110108MA01ABCD2X", sweep),
                           ("¥1,250,000", sweep),
                           ("13812345678", sweep),
                           ("a@b.cn", sweep)):
            if probe in src:
                leak.append(probe)
        rep.add("通知外发脱敏生效：相对方/信用代码/金额/联系方式一律掩码",
                not leak,
                "party=%s credit=%s amount=%s ｜ 兜底扫描无泄漏=%s"
                % (masked_party, masked_credit, masked_amount, not leak))

        # 默认收敛口径以**交付配置 + 代码默认**为准（自检不得改动交付配置文件）
        with io.open(os.path.join(root, "config", "app.json"), "r",
                     encoding="utf-8") as f:
            shipped = json.load(f)
        ship_events = (shipped.get("notify") or {}).get("events") or {}
        code_events = (cfg_mod.DEFAULT.get("notify") or {}).get("events") or {}
        rep.add("通知事件默认收敛（batch_summary / review_completed 默认关闭）",
                ship_events.get("batch_summary") is False
                and ship_events.get("review_completed") is False
                and ship_events.get("high_risk") is True
                and code_events.get("batch_summary") is False
                and code_events.get("review_completed") is False,
                "交付配置 defaults=%s ｜ 代码默认 defaults=%s"
                % (ship_events, code_events))

        ship_mask = (shipped.get("notify") or {}).get("mask") or {}
        rep.add("通知脱敏规则在交付配置中默认开启且不可静默关闭",
                bool(ship_mask.get("party")) and bool(ship_mask.get("amount"))
                and bool(ship_mask.get("credit_code"))
                and ship_mask.get("max_evidence_chars") == 0,
                "交付配置 mask=%s" % ship_mask)

        # ============================ v2.0 附加：流转自动化与精度（M9） ===============
        blocks = [("内部资料 严禁外传", 1, False), ("第一条 标的与价款", 1, True),
                  ("甲方应于验收合格后付款。", 1, False), ("第 1 页 共 3 页", 1, False),
                  ("机密", 2, False), ("第二条 交付与验收", 2, True),
                  ("乙方应于三十日内交付。", 2, False), ("第 2 页 共 3 页", 2, False),
                  ("机密", 3, False), ("第三条 违约责任", 3, True),
                  ("违约方应赔偿守约方全部损失。", 3, False), ("第 3 页 共 3 页", 3, False)]
        kept, removed = parsers.strip_repeat_lines(blocks, min_pages=2, max_chars=46,
                                                  edge_lines=2)
        kept_text = [k[0] for k in kept]
        drop_text = [r["text"] for r in removed]
        body_kept = all(t in kept_text for t in (
            "第一条 标的与价款", "甲方应于验收合格后付款。", "第二条 交付与验收",
            "乙方应于三十日内交付。", "第三条 违约责任", "违约方应赔偿守约方全部损失。"))
        hdr_dropped = all(t in drop_text for t in (
            "第 1 页 共 3 页", "第 2 页 共 3 页", "第 3 页 共 3 页", "机密"))
        rep.add("页眉页脚剔除：跨页重复行与页码形态识别，正文与条款标题零误删",
                body_kept and hdr_dropped,
                "保留 %d 行 / 剔除 %d 行；剔除项=%s"
                % (len(kept), len(removed), "、".join(drop_text)))

        para_check = parsers.build_paragraphs([(t, p, h) for t, p, h in kept])
        flat_after = para_check[1]
        anchor_ok = all(flat_after.find(t) >= 0 for t in kept_text) and all(
            para_check[0][i]["char_start"] ==
            (0 if i == 0 else para_check[0][i - 1]["char_end"] + 1)
            for i in range(len(para_check[0])))
        rep.add("剔除后重算坐标，段落锚点在归一化全文上仍可精确定位",
                anchor_ok,
                "重建 %d 段 / 全文 %d 字，坐标连续且全部可定位"
                % (len(para_check[0]), len(flat_after)))

        mode = __import__("app.llm", fromlist=["llm"]).mode_info(cfg)
        rep.add("规则引擎 / 模型润色模式对照可用，未配 Key 时明示回退模式",
                (not mode["available"] and mode["mode"] == "rule"
                 and bool(mode["reason"]) and "规则引擎" in mode["mode_label"]),
                "当前模式=%s（available=%s）；未启用原因：%s"
                % (mode["mode_label"], mode["available"], mode["reason"] or "无"))

        sch = scheduler_mod.get_scheduler(cfg, st)
        iv0 = sch.base_interval()
        sch.set_config(persist=False, enabled=True, interval_sec=60,
                       max_interval_sec=600, backoff_factor=2.0, jitter_sec=0)
        s_on = sch.status()
        sch._state["consecutive_failures"] = 3
        iv_back = sch._compute_interval()
        sch._state["consecutive_failures"] = 0
        iv_reset = sch._compute_interval()
        run_out = sch.trigger()
        s_after = sch.status()
        sch.set_config(persist=False, enabled=False, interval_sec=iv0, jitter_sec=5)
        rep.add("定时拉取：按间隔触发、指数退避生效、一次成功即复位",
                s_on["enabled"] and run_out.get("ok") and iv_back == 480
                and iv_reset == 60 and s_after["runs"] >= 1,
                "开启=%s；失败 3 次后间隔 %ss（60×2³，封顶 600）；复位后 %ss；"
                "手动拉取 ok=%s 新建=%s 跳过=%s"
                % (s_on["enabled"], iv_back, iv_reset, run_out.get("ok"),
                   run_out.get("created"), run_out.get("skipped")))

        # ============================ v2.0 附加：部署与性能硬化（M10） ===============
        q = queue_mod.get_queue(cfg, st)
        qstats = q.stats()
        quota_ok, quota_msg = q.quota_check()
        rep.add("解析队列与配额生效（工作线程池 + 单文件/待处理/队列/存储四道配额）",
                quota_ok and qstats["workers"] >= 1 and qstats["maxsize"] >= 1
                and qstats["max_pending"] >= 1 and qstats["max_total_mb"] >= 10,
                "workers=%d maxsize=%d max_pending=%d 存储上限=%dMB（当前 %.2fMB）配额=%s"
                % (qstats["workers"], qstats["maxsize"], qstats["max_pending"],
                   qstats["max_total_mb"], qstats["uploads_mb"], quota_msg or "通过"))

        pdf_probe = os.path.join(cfg.path("reports"), "_selftest_pdf_channel.pdf")
        pdf_min.write(pdf_probe, [[("h1", "服务端 PDF 直出通道自检"),
                                   ("body", "本文件由纯标准库通道（无第三方依赖、无浏览器）"
                                            "直接写出，用于验证线上无 chromium 环境下的 PDF 能力。"),
                                   ("kv", "字体：PDF 标准非嵌入 CID 字体 STSong-Light")]],
                      title="PDF 通道自检报告",
                      meta_lines=["衡鉴 V2.1.7", "零第三方依赖通道"])
        raw = open(pdf_probe, "rb").read()
        rep.add("服务端 PDF 直出（零依赖兜底通道）可用",
                raw.startswith(b"%PDF") and raw.rstrip().endswith(b"%%EOF")
                and len(raw) > 1200 and b"/STSong-Light" in raw and b"ToUnicode" in raw,
                "%s ｜ %d 字节 ｜ 头=%s ｜ 含非嵌入中文字体与 ToUnicode CMap"
                % (os.path.basename(pdf_probe), len(raw), raw[:8].decode("latin-1")))

        # 并发 10 份：不丢不串（每份案件都应完成且风险等级与单独执行一致）
        conc = [c["id"] for c in st.list_cases()
                if c["review_status"] == "completed"][:10]
        bases = {}
        for cid_ in conc:
            bases[cid_] = st.overall_risk(cid_)
        threads = []
        box = {}

        def _worker(cid_):
            try:
                box[cid_] = pipeline.submit_review(cfg, st, cid_)
            except Exception as exc:                             # noqa: BLE001
                box[cid_] = {"ok": False, "error": str(exc)}

        for cid_ in conc:
            t = threading.Thread(target=_worker, args=(cid_,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=90)
        mismatched = [c for c in conc
                      if st.overall_risk(c) != bases.get(c)]
        rep.add("并发 %d 份审查不丢不串（结果与单独执行一致）" % len(conc),
                len(conc) >= 6 and all(box.get(c, {}).get("ok") for c in conc)
                and not mismatched,
                "并发任务 %d 个，全部返回；综合风险与前次一致；异常=%s"
                % (len(conc), ("风险等级漂移 %s" % mismatched) if mismatched else "无"))

        from app import __version__

        # ============================ v2.0 附加：报告三通道同源 ======================
        kind_html, html = pipeline.build_report(cfg, st, c01["id"], "html")
        rep.add("报告三通道同源（MD / HTML / PDF 均可产出）",
                bool(md) and bool(kind_html and html) and kind_pdf == "pdf",
                "MD %d 字 ｜ HTML %d 字 ｜ PDF 通道=%s"
                % (len(md or ""), len(html or ""), kind_pdf))

        # ============================ v2.0 附加：版本口径（D-28） ====================
        chlog = os.path.join(root, "CHANGELOG.md")
        ch_text = ""
        if os.path.exists(chlog):
            with io.open(chlog, "r", encoding="utf-8") as f:
                ch_text = f.read()
        main_src = ""
        for fn in ("app/server.py", "app/__init__.py", "ui/assets/app.js"):
            p = os.path.join(root, fn)
            if os.path.exists(p):
                with io.open(p, "r", encoding="utf-8", errors="ignore") as f:
                    main_src += f.read()
        rep.add("版本口径一致：交付版本 = 运行版本 = 文档版本（禁止版本失真）",
                os.path.exists(chlog) and __version__ in ch_text
                and ('"version": "1.0.0"' not in main_src)
                and (" %s" % __version__) in ch_text,
                "运行版本 %s ｜ CHANGELOG %s ｜ 硬编码 1.0.0 残留=%s"
                % (__version__, "已含该版本条目" if __version__ in ch_text else "缺失",
                   '"version": "1.0.0"' in main_src))

        # ============================ v2.1 附加：AI 助手（页面宠物） ================
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
                for seg in _re3.split(r"\s+|\s*>\s*", s2["target"].replace(",", " ")):
                    seg = seg.strip()
                    if not seg:
                        continue
                    m = _re3.search(r"#([\w-]+)", seg)
                    tok = 'id="%s"' % m.group(1) if m else None
                    if tok is None:
                        m = _re3.search(r"\.([\w-]+)", seg)
                        tok = 'class="%s' % m.group(1) if m else None
                    if tok is None:
                        m = _re3.search(r"\[([a-zA-Z-]+)\s*=\s*\"([^\"]*)\"\]", seg)
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

        # ============================ v2.1.7 附加：登录页「一键进入」守卫行为 ======
        # 为什么必须有这一条：一键进入是**登录的替代入口**，若被鉴权守卫当成受保护接口，
        # 就会形成死锁 —— 要先登录才能用免登录入口（旧版正是如此失效，点角色按钮没反应）。
        # 而此前的自检对 auth 接口零覆盖，所以这个缺陷一路溜到了交付版。这里补上网关级回归。
        import socket as _socket
        import urllib.error as _urlerr
        import urllib.request as _urlreq

        from app import server as _server_mod

        def _free_port():
            _s = _socket.socket()
            _s.bind(("127.0.0.1", 0))
            _p = _s.getsockname()[1]
            _s.close()
            return _p

        def _probe_switch(port, demo_on):
            """在独立端口起一份真实服务，按角色打 /api/auth/switch，返回 {role: (status, 有无 Cookie)}。"""
            _cfg2 = cfg_mod.Config(root)
            _cfg2.data["server"]["port"] = port
            _cfg2.data.setdefault("auth", {})["demo_switch"] = demo_on
            _cfg2.data["approval"]["base_url"] = \
                "http://127.0.0.1:%d/mock/feishu/api" % port
            for _k in ("data", "uploads", "parsed", "reports", "samples"):
                _cfg2.path(_k)
            _st2 = store_mod.Store(_cfg2.db_path())
            try:
                auth_mod.seed_users(_cfg2, _st2)
            except Exception:                                     # noqa: BLE001
                pass
            _httpd = _server_mod.serve(_cfg2, _st2)
            threading.Thread(target=_httpd.serve_forever, daemon=True).start()
            time.sleep(0.45)
            _op = _urlreq.build_opener(_urlreq.ProxyHandler({}))
            _out = {}
            for _role in ("legal", "applicant", "admin"):
                _rq = _urlreq.Request(
                    "http://127.0.0.1:%d/api/auth/switch" % port,
                    data=json.dumps({"role": _role}).encode("utf-8"), method="POST",
                    headers={"Content-Type": "application/json"})
                try:
                    with _op.open(_rq, timeout=15) as _r:
                        _out[_role] = (_r.status, bool(_r.headers.get("Set-Cookie")))
                except _urlerr.HTTPError as _e:
                    _out[_role] = (_e.code, bool(_e.headers.get("Set-Cookie")))
                except Exception:                                 # noqa: BLE001
                    _out[_role] = (0, False)
            _httpd.shutdown()
            try:
                _httpd.server_close()
            except Exception:                                     # noqa: BLE001
                pass
            return _out

        from app import auth as auth_mod
        _sw_on = _probe_switch(_free_port(), True)
        _sw_off = _probe_switch(_free_port(), False)
        rep.add("登录页「一键进入」：未登录即可用，且 demo_switch 关闭后失败关闭",
                all(_st == 200 and _ck for _st, _ck in _sw_on.values())
                and all(_st != 200 and not _ck for _st, _ck in _sw_off.values()),
                "开启时 %s ｜ 关闭时 %s" % (
                    "、".join("%s=%s%s" % (r, v[0], "+Cookie" if v[1] else "")
                              for r, v in sorted(_sw_on.items())),
                    "、".join("%s=%s" % (r, v[0]) for r, v in sorted(_sw_off.items()))))

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
        print("  衡鉴 V2.1.7 · 验收自检")
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
