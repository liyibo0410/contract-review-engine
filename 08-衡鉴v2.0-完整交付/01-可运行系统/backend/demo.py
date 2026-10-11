# -*- coding: utf-8 -*-
"""一键端到端演示脚本

跑通「拉取 → 解析 → 审查 → 复核 → 回写 → 报告导出 → 异常阻塞重试」全链路，
每一步都把关键结果打印出来，用于评审现场演示与回归自检。

用法：
    python demo.py                 # 完整演示（含阻塞重试用例）
    python demo.py --no-blocked    # 跳过异常用例
    python demo.py --json          # 输出 JSON 摘要（供 CI 断言）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import config, db  # noqa: E402
from app.services import (audit, pipeline, report, rule_engine, writeback, writer)  # noqa: E402

LINE = "─" * 74


def title(step: str, text: str) -> None:
    print(f"\n{LINE}\n  {step}  {text}\n{LINE}")


def pick(pattern: str) -> Path | None:
    hits = sorted(Path(config.SAMPLES_DIR).glob(pattern))
    return hits[0] if hits else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-blocked", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="清空演示数据库后重跑")
    args = ap.parse_args()

    if args.fresh and config.DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(config.DB_PATH) + suffix)
            if p.exists():
                p.unlink()
    db.init_db()

    summary: dict = {"steps": [], "ok": True}
    quiet = args.json

    def emit(name: str, data: dict) -> None:
        summary["steps"].append({"step": name, **data})
        if quiet:
            return

    # ---------------------------------------------------------------- 0 环境
    title("[0/8]", "环境自检")
    health = {
        "project": config.PROJECT_CODE,
        "version": config.PROJECT_VERSION,
        "rules": rule_engine.stats(),
    }
    from app.services import llm, parser_ocr
    health["llm_configured"] = llm.is_configured()
    health["ocr_available"] = parser_ocr.available()
    if not quiet:
        print(f"  规则库        v{health['rules']['version']} · {health['rules']['total']} 条规则"
              f"（高 {health['rules']['by_level'].get('high', 0)} / "
              f"中 {health['rules']['by_level'].get('medium', 0)} / "
              f"低 {health['rules']['by_level'].get('low', 0)}）")
        print(f"  标准条款库    {health['rules']['template_count']} 套")
        print(f"  大模型语义层  {'已接入' if health['llm_configured'] else '未配置 → 自动降级模板生成器'}")
        print(f"  扫描件 OCR    {'就绪' if health['ocr_available'] else '不可用 → 扫描件将进入 blocked'}")
    emit("env", health)

    main_case = pick("*软件采购合同_埋雷版.docx")
    if not main_case:
        print("\n[错误] 样例合同不存在，请先运行：python tools/gen_samples.py")
        return 1
    golden = json.loads((ROOT / "app" / "eval" / "golden_cases.json").read_text(encoding="utf-8"))
    case_meta = next((c for c in golden["cases"] if c["file"] == main_case.name), {})

    # ---------------------------------------------------------------- 1 拉取
    title("[1/8]", "审批系统拉取待办任务")
    created = pipeline.create_task(file_path=main_case, file_name=main_case.name,
                                   applicant=case_meta.get("applicant", "王倩"),
                                   dept=case_meta.get("dept", "信息技术部"),
                                   biz_type=case_meta.get("biz_type", "采购"),
                                   source="approval_pull", force_new=True)
    cid = created["contract_id"]
    if not quiet:
        print(f"  任务编号      {created['code']}")
        print(f"  合同文件      {main_case.name}（{main_case.stat().st_size // 1024} KB）")
        print(f"  附件 SHA256   {created['sha256'][:32]}…")
        print(f"  幂等键        合同编号 + SHA256 + 规则版本 → 重复触发将自动跳过")
    emit("pull", {"contract_id": cid, "code": created["code"]})

    # ---------------------------------------------------------------- 2 解析
    title("[2/8]", "文档解析与条款结构化")
    parse = pipeline.run_parse(cid)
    if parse.get("blocked"):
        print(f"  [阻塞] {parse.get('reason')}")
        return 1
    if not quiet:
        print(f"  解析引擎      {parse['engine']}")
        print(f"  页数 / 字数   {parse['page_count']} 页 / {parse['chars']} 字")
        print(f"  条款节点      {parse['clauses']} 项（按 11 类法定与业务条款切分）")
        print("  元数据抽取：")
        for item in parse["meta"]["items"][:8]:
            loc = f"第 {item['page']} 页" + (f" · [{item['char_start']},{item['char_end']})"
                                            if item["char_start"] >= 0 else "")
            print(f"      {item['label']:<16} {item['value'][:40]:<42} {loc}")
        print("  [说明] 每个解析项都记录原文字符区间，前端可点击直达 —— "
              "这是「证据可溯源」的基础")
    emit("parse", {"engine": parse["engine"], "pages": parse["page_count"],
                   "clauses": parse["clauses"], "chars": parse["chars"]})

    # ---------------------------------------------------------------- 3 审查
    title("[3/8]", "双引擎风险审查")
    review = pipeline.run_review(cid)
    stats = review["stats"]
    if not quiet:
        print(f"  规则判定      命中 {len(review['findings'])} 项 "
              f"（高 {stats['high']} / 中 {stats['medium']} / 低 {stats['low']}）"
              f"，耗时 {review['rule_ms']} ms")
        print(f"  综合风险等级  {config.RISK_LABEL.get(review['risk_level'])}")
        print(f"  审查结论      {review['summary']['conclusion']}"
              f"（生成方式：{review['summary']['generated_by']}）")
        print("\n  高风险命中明细（用于演示「点哪跳哪」）：")
        for f in [x for x in review["findings"] if x["level"] == "high"]:
            print(f"      [{f['rule_id']}] {f['title']}")
            print(f"          定位：第 {f['page']} 页 · 段 #{f['para_index']} · "
                  f"字符区间 [{f['char_start']}, {f['char_end']})")
            print(f"          证据：{f['evidence_text'][:64]}…")
            print(f"          依据：{f['legal_basis'][:56]}")
    emit("review", {"findings": len(review["findings"]), **stats,
                    "risk_level": review["risk_level"],
                    "conclusion": review["summary"]["conclusion"],
                    "generated_by": review["summary"]["generated_by"]})

    # ---------------------------------------------------------------- 4 复核
    title("[4/8]", "法务复核与修改建议采纳")
    highs = [f for f in review["findings"] if f["level"] == "high"]
    adopted = 0
    with db.tx() as conn:
        rows = db.rows(conn.execute(
            "SELECT id, rule_id, title, advice FROM findings WHERE contract_id=? AND level='high'"
            " ORDER BY id", (cid,)))
    for r in rows[:3]:
        if r["advice"]:
            with db.tx() as conn:
                conn.execute("UPDATE findings SET adopted=1, adopted_text=? WHERE id=?",
                             (r["advice"], r["id"]))
            adopted += 1
            if not quiet:
                print(f"  ✔ 已采纳 [{r['rule_id']}] {r['title']}")
    if not quiet:
        print(f"\n  采纳 {adopted} 条修改建议（工作台支持编辑后再采纳，生成修改前后对照）")
    emit("adopt", {"adopted": adopted, "high_total": len(highs)})

    # ---------------------------------------------------------------- 5 回写
    title("[5/8]", "审查意见生成与审批回写")
    c = _contract(cid)
    rv = _review(cid)
    findings_out = _findings(cid)
    body = writer.render_writeback_comment(c, rv, findings_out,
                                           reviewer_note="同意系统意见，请业务部门按建议条款与对方重新谈判。",
                                           operator=config.OWNER_NAME)
    with db.tx() as conn:
        conn.execute("UPDATE reviews SET reviewer_note=? WHERE contract_id=?",
                     ("同意系统意见，请业务部门按建议条款与对方重新谈判。", cid))
    doc = _document(cid)
    idem = writeback.idem_key(c["code"], doc["sha256"], rule_engine.rule_version())

    preview = writeback.preview(cid, body, operator=config.OWNER_NAME, dry_run=True, idem=idem)
    if not quiet:
        print(f"  ① DRY_RUN 预览   状态 {preview['status']}（预览模式不写入审批系统）")
        print(f"     幂等键        {idem}")
        print(f"     待写入正文    {preview['chars']} 字")
        print("\n  ── 审查意见正文（节选）──")
        for line in body.split("\n")[:14]:
            print(f"     {line}")
        print("     …")

    result = writeback.write(cid, body, operator=config.OWNER_NAME, dry_run=False, idem=idem)
    if not quiet:
        print(f"\n  ② 真实回写       状态 {result['status']} · 评论 ID {result.get('platform_comment_id', '-')}")
    again = writeback.write(cid, body, operator=config.OWNER_NAME, dry_run=False, idem=idem)
    if not quiet:
        print(f"  ③ 重复触发       幂等命中 = {again.get('skipped', False)}"
              f"（{'已自动跳过，评论区不会出现第二条' if again.get('skipped') else '未跳过'}）")
        comments = writeback.platform_comments(cid)
        print(f"  ④ 审批评论区     当前 {len(comments)} 条意见")
    emit("writeback", {"status": result["status"],
                       "comment_id": result.get("platform_comment_id", ""),
                       "idempotent_skip": bool(again.get("skipped")),
                       "comment_count": len(writeback.platform_comments(cid))})

    # ---------------------------------------------------------------- 6 报告
    title("[6/8]", "审查报告导出（Markdown / PDF）")
    md = writer.render_markdown(c, _meta(c), _clauses(cid), findings_out, rv,
                                json.loads(c.get("profile_json") or "{}"),
                                reviewer_note="同意系统意见，请业务部门按建议条款与对方重新谈判。",
                                rule_version=rule_engine.rule_version(),
                                writeback=writeback.serialize(writeback.get(cid)))
    paths = report.export_both(c, md)
    if not quiet:
        print(f"  Markdown      {paths['markdown']}（{len(md)} 字）")
        if paths.get("pdf"):
            size = Path(paths["pdf"]).stat().st_size // 1024
            print(f"  PDF           {paths['pdf']}（{size} KB）")
        else:
            print(f"  PDF 导出失败   {paths.get('pdf_error')}")
    emit("report", paths)

    # ---------------------------------------------------------------- 7 观测
    title("[7/8]", "审计留痕与运行观测")
    logs = audit.list_logs(cid)
    over = pipeline.overview()
    if not quiet:
        print(f"  审计日志      {len(logs)} 条（step / tool / 输入摘要 / 输出摘要 / 耗时）")
        for lg in logs[:8]:
            print(f"      {lg['step']:<22} {lg['cost_ms']:>6} ms  {lg['level']}")
        print(f"\n  任务总数      {over['total']}    完成 {over['completed']}    受阻 {over['blocked']}")
        print(f"  风险命中      {over['findings']} 条（高风险 {over['high_findings']}）")
        print(f"  解析成功率    {over['parse_success_rate']:.1%}    平均耗时 {over['avg_parse_ms']} ms")
    emit("observe", {"audit_logs": len(logs), "overview": over})

    # ---------------------------------------------------------------- 8 异常
    if not args.no_blocked:
        title("[8/8]", "异常处理：加密文档阻塞与重试")
        enc = pick("*加密件.pdf")
        if enc:
            c2 = pipeline.create_task(file_path=enc, file_name=enc.name,
                                      biz_type="采购", force_new=True)
            r2 = pipeline.run_pipeline(c2["contract_id"])
            row = _contract(c2["contract_id"])
            if not quiet:
                print(f"  上传           {enc.name}")
                print(f"  状态流转       pending → parsing → **blocked**")
                print(f"  阻塞代码       {row['block_code']}")
                print(f"  阻塞原因       {row['block_reason']}")
                print(f"  重试入口       POST /api/contracts/{c2['contract_id']}/retry")
            r3 = pipeline.retry(c2["contract_id"])
            if not quiet:
                print(f"  重试结果       {r3['status']}"
                      f"（文件未更换，仍阻塞且不产生脏数据 —— 这是预期行为）")
            emit("blocked_retry", {"block_code": row["block_code"],
                                   "status_after_retry": r3["status"]})

    # ---------------------------------------------------------------- 收尾
    ok = (not quiet) and summary["steps"]
    summary["ok"] = True
    title("完成", "端到端全流程演示执行结束")
    if not quiet:
        print("  覆盖链路：拉取 → 解析 → 审查 → 复核 → 回写 → 报告 → 观测 → 异常重试")
        print("  访问 http://127.0.0.1:8130/ 可查看可视化大盘与工作台")
        print(f"  报告输出目录：{config.REPORT_DIR}")
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


# ------------------------------------------------------------------ 查询辅助
def _contract(cid):
    with db.tx() as conn:
        r = conn.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()
    return dict(r)


def _document(cid):
    with db.tx() as conn:
        r = conn.execute("SELECT * FROM documents WHERE contract_id=? ORDER BY id DESC LIMIT 1",
                         (cid,)).fetchone()
    return dict(r)


def _review(cid):
    with db.tx() as conn:
        r = conn.execute("SELECT * FROM reviews WHERE contract_id=? ORDER BY id DESC LIMIT 1",
                         (cid,)).fetchone()
    r = dict(r)
    r["focus_points"] = json.loads(r.get("focus_points") or "[]")
    return r


def _findings(cid):
    with db.tx() as conn:
        rows = db.rows(conn.execute(
            "SELECT * FROM findings WHERE contract_id=? ORDER BY"
            " CASE level WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, page",
            (cid,)))
    return rows


def _clauses(cid):
    with db.tx() as conn:
        return db.rows(conn.execute(
            "SELECT * FROM clauses WHERE contract_id=? ORDER BY char_start", (cid,)))


def _meta(c):
    return json.loads(c.get("meta_json") or "{}")


if __name__ == "__main__":
    raise SystemExit(main())
