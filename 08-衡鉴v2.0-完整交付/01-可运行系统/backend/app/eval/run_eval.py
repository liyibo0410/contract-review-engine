# -*- coding: utf-8 -*-
"""回归评测（黄金样例集跑分）

每次改动规则库，都必须跑一遍这里。它回答三个问题：
    1. 该报的报出来了吗？  —— 高风险召回率（recall）
    2. 不该报的报了吗？    —— 误报数（false positive）与准确率（precision）
    3. 解析还能跑通吗？    —— 解析成功率与平均耗时

与 RAGAS 的关系：RAGAS 面向的是"检索增强生成"的忠实度/相关性评价，本项目的主判定路径
是确定性规则引擎而非向量检索，因此这里实现的是**面向规则系统的检索式评测**
（等价于 RAGAS 的 context recall / context precision 在规则场景下的定义），
同时保留了 RAGAS 的四项核心指标口径，便于横向对照。详见《审核规则体系梳理》文档。

用法：
    python -m app.eval.run_eval                 # 跑全部并写入历史
    python -m app.eval.run_eval --no-persist    # 只跑不落库
    python -m app.eval.run_eval --limit 3       # 只跑前 3 份（开发期快速回归）
    python -m app.eval.run_eval --json          # 只输出 JSON
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, db  # noqa: E402
from app.services import (coordinator, parser_docx, parser_ocr, parser_pdf,
                          rule_engine)

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_cases.json"
REPORT_DIR = Path(__file__).resolve().parent / "reports"


# ------------------------------------------------------------------ 单例解析
def parse_sample(case: dict) -> dict:
    path = Path(config.SAMPLES_DIR) / case["file"]
    started = time.time()
    result = {"file": case["file"], "ok": False, "blocked": False, "block_code": "",
              "reason": "", "clauses": [], "engine": "", "pages": 0, "chars": 0}
    if not path.exists():
        result["reason"] = f"样例文件不存在：{path}（请先运行 python tools/gen_samples.py）"
        result["cost_ms"] = int((time.time() - started) * 1000)
        return result

    kind = case.get("kind", "docx")
    try:
        if kind == "docx":
            res = parser_docx.parse(path)
        elif kind in ("pdf", "encrypted_pdf"):
            res = parser_pdf.parse(path, enable_ocr=config.OCR_ENABLED)
        elif kind == "scan":
            blocks, conf = parser_ocr.recognize_file(path)
            res = {"blocks": blocks, "page_count": 1, "engine": parser_ocr.engine_name()}
        else:
            raise ValueError(f"未知类型：{kind}")
    except parser_pdf.PdfEncrypted as exc:
        result["blocked"] = True
        result["block_code"] = "DOC_ENCRYPTED"
        result["reason"] = str(exc)
        result["cost_ms"] = int((time.time() - started) * 1000)
        return result
    except parser_ocr.OcrUnavailable as exc:
        result["blocked"] = True
        result["block_code"] = "OCR_ENGINE_UNAVAILABLE"
        result["reason"] = str(exc)
        result["cost_ms"] = int((time.time() - started) * 1000)
        return result
    except Exception as exc:
        result["reason"] = f"解析异常：{exc}"
        result["cost_ms"] = int((time.time() - started) * 1000)
        return result

    blocks, full_text = coordinator.normalize_blocks(res["blocks"])
    blocked, code, reason = coordinator.decide_block({"encrypted": False}, full_text, None, None)
    if blocked:
        result.update({"blocked": True, "block_code": code, "reason": reason,
                       "cost_ms": int((time.time() - started) * 1000)})
        return result

    classified = coordinator.classify_blocks(blocks)
    result.update({
        "ok": True, "clauses": coordinator.build_clauses(classified),
        "engine": res.get("engine", ""), "pages": res.get("page_count", 0),
        "chars": len(full_text), "full_text": full_text,
        "cost_ms": int((time.time() - started) * 1000),
    })
    return result


# ------------------------------------------------------------------ 跑分主逻辑
def run(limit: int | None = None, persist: bool = True, verbose: bool = True) -> dict:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = golden["cases"]
    if limit:
        cases = cases[:limit]

    detail: list[dict] = []
    parse_ok = 0
    parseable = 0
    high_total = high_hit = 0
    medium_total = medium_hit = 0
    false_positive = 0
    costs: list[int] = []

    for case in cases:
        parsed = parse_sample(case)
        costs.append(parsed.get("cost_ms", 0))
        expect_blocked = bool(case.get("expect_blocked"))

        if expect_blocked:
            # 加密件：进入 blocked 即视为解析路径正确
            hit = parsed["blocked"] and parsed["block_code"] == "DOC_ENCRYPTED"
            detail.append({
                "file": case["file"], "name": case["name"], "blocked": True,
                "block_code": parsed["block_code"], "expect_blocked": True,
                "ok": hit, "engine": "-", "clauses": 0, "chars": 0,
                "cost_ms": parsed.get("cost_ms", 0), "high_found": [], "high_miss": [],
                "high_false": [], "note": case.get("note", ""),
            })
            if hit:
                parse_ok += 1
            parseable += 1
            continue

        parseable += 1
        if not parsed["ok"]:
            detail.append({
                "file": case["file"], "name": case["name"], "ok": False,
                "blocked": parsed.get("blocked", False),
                "block_code": parsed.get("block_code", ""),
                "engine": parsed.get("engine", ""), "clauses": 0, "chars": 0,
                "cost_ms": parsed.get("cost_ms", 0), "high_found": [], "high_miss": [],
                "high_false": [], "note": parsed.get("reason", ""),
            })
            continue

        parse_ok += 1
        ctx = rule_engine.build_ctx(case.get("biz_type"))
        findings = rule_engine.evaluate(parsed["clauses"], parsed["full_text"], ctx=ctx)
        found_high = sorted({f["rule_id"] for f in findings if f["level"] == "high"})
        found_medium = sorted({f["rule_id"] for f in findings if f["level"] == "medium"})
        exp_high = sorted(set(case.get("expect_high") or []))
        exp_medium = sorted(set(case.get("expect_medium") or []))

        hit = sorted(set(found_high) & set(exp_high))
        miss = sorted(set(exp_high) - set(found_high))
        false_pos = sorted(set(found_high) - set(exp_high))

        high_total += len(exp_high)
        high_hit += len(hit)
        medium_total += len(exp_medium)
        medium_hit += len(set(found_medium) & set(exp_medium))
        false_positive += len(false_pos)

        detail.append({
            "file": case["file"], "name": case["name"], "ok": True, "blocked": False,
            "engine": parsed["engine"], "pages": parsed["pages"],
            "clauses": len(parsed["clauses"]), "chars": parsed["chars"],
            "cost_ms": parsed["cost_ms"],
            "high_found": found_high, "high_expect": exp_high,
            "high_miss": miss, "high_false": false_pos,
            "medium_found": found_medium, "medium_miss": sorted(set(exp_medium) - set(found_medium)),
            "total_findings": len(findings), "biz_type": case.get("biz_type", ""),
            "note": case.get("note", ""),
        })

    high_recall = round(high_hit / high_total, 4) if high_total else 1.0
    medium_recall = round(medium_hit / medium_total, 4) if medium_total else 1.0
    precision = round(high_hit / (high_hit + false_positive), 4) if (high_hit + false_positive) else 1.0
    parse_rate = round(parse_ok / parseable, 4) if parseable else 0.0
    avg_ms = int(sum(costs) / len(costs)) if costs else 0

    verdict = "pass"
    if high_recall < 1.0 or false_positive > 0:
        verdict = "warn"
    if parse_rate < 1.0:
        verdict = "fail" if parse_rate < 0.9 else "warn"

    summary = {
        "rule_version": rule_engine.rule_version(),
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "total_cases": len(cases),
        "parse_ok": parse_ok,
        "parse_success_rate": parse_rate,
        "high_total": high_total,
        "high_hit": high_hit,
        "high_recall": high_recall,
        "medium_total": medium_total,
        "medium_hit": medium_hit,
        "medium_recall": medium_recall,
        "false_positive": false_positive,
        "precision": precision,
        "avg_ms": avg_ms,
        "verdict": verdict,
    }

    if persist:
        db.init_db()
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO eval_runs(rule_version, total_cases, parse_ok, high_total, high_hit,"
                " high_recall, false_positive, precision, avg_ms, verdict, detail_json, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (summary["rule_version"], summary["total_cases"], summary["parse_ok"],
                 summary["high_total"], summary["high_hit"], summary["high_recall"],
                 summary["false_positive"], summary["precision"], summary["avg_ms"],
                 summary["verdict"], json.dumps(detail, ensure_ascii=False),
                 summary["run_at"]),
            )

    report = {"summary": summary, "cases": detail}
    _write_report(report)
    if verbose:
        _print_report(report)
    return report


def _print_report(report: dict) -> None:
    s = report["summary"]
    print("=" * 78)
    print(f"衡鉴 · 黄金样例集回归评测   规则版本 {s['rule_version']}")
    print("=" * 78)
    print(f"样例总数        : {s['total_cases']}")
    print(f"解析成功率      : {s['parse_success_rate']:.1%}  ({s['parse_ok']}/{s['total_cases']})")
    print(f"高风险召回率    : {s['high_recall']:.1%}  ({s['high_hit']}/{s['high_total']})")
    print(f"中风险召回率    : {s['medium_recall']:.1%}  ({s['medium_hit']}/{s['medium_total']})")
    print(f"误报数          : {s['false_positive']}")
    print(f"准确率 precision: {s['precision']:.1%}")
    print(f"平均解析耗时    : {s['avg_ms']} ms")
    print(f"判定            : {s['verdict'].upper()}")
    print("-" * 78)
    for c in report["cases"]:
        flag = "OK " if c["ok"] else "!! "
        extra = ""
        if c.get("blocked"):
            extra = f"blocked/{c.get('block_code', '')}"
        else:
            extra = (f"clauses={c.get('clauses')} chars={c.get('chars')} "
                     f"hit={len(c.get('high_found', []))} miss={c.get('high_miss')} "
                     f"fp={c.get('high_false')}")
        print(f"{flag}{c['file'][:34]:<36} {extra}")
    print("=" * 78)


def _write_report(report: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    s = report["summary"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"eval_{stamp}.md"
    L = [
        f"# 衡鉴 · 审核规则回归评测报告",
        "",
        f"- 评测时间：{s['run_at']}",
        f"- 规则版本：{s['rule_version']}",
        f"- 样例集：{s['total_cases']} 份（含阻塞用例）",
        f"- 综合判定：**{s['verdict'].upper()}**",
        "",
        "## 一、核心指标",
        "",
        "| 指标 | 数值 | 说明 |",
        "| --- | --- | --- |",
        f"| 解析成功率 | {s['parse_success_rate']:.1%} ({s['parse_ok']}/{s['total_cases']}) | 含加密件正确进入 blocked |",
        f"| 高风险召回率 | {s['high_recall']:.1%} ({s['high_hit']}/{s['high_total']}) | 黄金标注中高风险被命中的比例 |",
        f"| 中风险召回率 | {s['medium_recall']:.1%} ({s['medium_hit']}/{s['medium_total']}) | 中风险命中比例（辅助指标） |",
        f"| 误报数 | {s['false_positive']} | 命中高风险但不在期望清单中的条数 |",
        f"| 准确率 | {s['precision']:.1%} | 高风险的 precision |",
        f"| 平均解析耗时 | {s['avg_ms']} ms | 单份合同解析 + 条款切分 |",
        "",
        "## 二、逐份样例明细",
        "",
        "| # | 样例 | 类型 | 解析 | 条款数 | 字数 | 命中高风险 | 漏报 | 误报 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, c in enumerate(report["cases"], 1):
        parse_cell = (f"blocked({c.get('block_code', '')})" if c.get("blocked")
                      else ("成功" if c["ok"] else "失败"))
        L.append(
            f"| {i} | {c['name']} | {c.get('engine', '-')} | {parse_cell} | "
            f"{c.get('clauses', 0)} | {c.get('chars', 0)} | "
            f"{','.join(c.get('high_found', [])) or '—'} | "
            f"{','.join(c.get('high_miss', [])) or '—'} | "
            f"{','.join(c.get('high_false', [])) or '—'} |"
        )
    L += ["", "## 三、结论与后续动作", ""]
    if s["verdict"] == "pass":
        L.append("- 高风险召回率 100%、误报 0，规则库可发布。")
    else:
        L.append(f"- 存在漏报 {s['high_total'] - s['high_hit']} 项、误报 {s['false_positive']} 项，需调优规则后重跑。")
    L.append("- 规则变更后必须重跑本脚本，防止「改一条、伤三处」。")
    L.append("")
    L.append(f"*报告由 `python -m app.eval.run_eval` 自动生成：{stamp}*")
    path.write_text("\n".join(L), encoding="utf-8")
    report["report_path"] = str(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="衡鉴 · 黄金样例集回归评测")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-persist", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()
    report = run(limit=args.limit or None, persist=not args.no_persist,
                 verbose=not args.json)
    if args.json:
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


if __name__ == "__main__":
    main()
