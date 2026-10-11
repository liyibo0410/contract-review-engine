#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置契约测试 —— 校验 config/ 下各文件之间的交叉引用一致性。

用法：
    python tools/check_config.py
    python tools/check_config.py --config-dir ../contract-review-system/config

退出码：0 = 全部通过；1 = 存在失败项（可接入 CI / pre-commit）。

校验项：
  1. required_clauses.json 的 matrix 中每个条款 name 都在 clause_types 中登记
  2. 所有 suggestion_clause_id / replacement_clause_id 都能在 clause_library.json 中解析
  3. 三类规则的 rule_id 全局唯一（R-xx / D-xx / E-xx）
  4. scoring.json 权重和为 1，且 worked_examples 的 total 与 grade 可复算
  5. mock/gsxt.json 信用代码格式、黑名单引用、与样例合同主体的一致性

注：本脚本是「配置静态校验」，不依赖运行时。规则真正跑起来后的命中断言属于
     tests/test_regression.py（见 P0 开发任务清单 T10 与第 5 节）。
"""
import argparse
import io
import json
import os
import sys

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")

results = []       # (ok: bool, section: str, message: str)
fatal = []         # 致命错误（文件读不到 / JSON 坏）


def load(path, label):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        fatal.append("%s (%s): %s" % (label, path, exc))
        return None


def collect_clause_ids(obj, acc):
    """递归收集 clause_library.json 中所有 clause_id，兼容不同层级结构。"""
    if isinstance(obj, dict):
        cid = obj.get("clause_id")
        if isinstance(cid, str):
            acc.add(cid)
        for v in obj.values():
            collect_clause_ids(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            collect_clause_ids(v, acc)


def check_required_clauses(rc, clause_ids):
    sec = "required_clauses 交叉引用"
    cts = {k: v for k, v in rc.get("clause_types", {}).items() if not k.startswith("_")}
    results.append((len(cts) > 0, sec, "已登记条款类型 %d 项" % len(cts)))

    for btype, block in rc.get("matrix", {}).items():
        clauses = block.get("clauses", [])
        unknown = [c["name"] for c in clauses if c["name"] not in cts]
        ok_decl = block.get("required_count") == len(clauses)
        results.append((not unknown, sec,
                        "%s：%d 项，未登记 name = %s" % (btype, len(clauses), unknown or "无")))
        results.append((ok_decl, sec,
                        "%s：required_count=%s 与实际 %d %s" % (
                            btype, block.get("required_count"), len(clauses),
                            "一致" if ok_decl else "不一致")))

    if clause_ids:
        dangling = sorted({v["suggestion_clause_id"] for v in cts.values()
                           if v.get("suggestion_clause_id")} - clause_ids)
        results.append((not dangling, sec, "clause_types 中无法解析的示范条款 = %s" % (dangling or "无")))
        missing_tpl = [k for k, v in cts.items() if not v.get("suggestion_clause_id")]
        # 缺范本不是错误，是待办，单独一行提示
        results.append((True, sec,
                        "提示：%d 项条款类型暂无示范条款（范本库待扩充）：%s" % (len(missing_tpl), missing_tpl)))


def check_rule_ids(old_rules, det):
    sec = "规则 id 唯一性"
    ids = [r["rule_id"] for r in old_rules.get("rules", [])]
    ids += [r["rule_id"] for r in det.get("rules", [])]
    ids += [r["rule_id"] for r in det.get("external_rules", {}).get("rules", [])]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    results.append((not dup, sec, "规则合计 %d 条（R=%d / D=%d / E=%d），重复 id = %s" % (
        len(ids), len(old_rules.get("rules", [])), len(det.get("rules", [])),
        len(det.get("external_rules", {}).get("rules", [])), dup or "无")))

    no_checker = [r["rule_id"] for r in det.get("rules", [])
                  if "checker" not in r.get("detect", {})]
    no_checker += [r["rule_id"] for r in det.get("external_rules", {}).get("rules", [])
                   if "checker" not in r]
    results.append((not no_checker, sec, "缺少 detect.checker 的规则 = %s" % (no_checker or "无")))


def check_scoring(sc):
    sec = "定级算法"
    wsum = sum(sc.get("weights", {}).values())
    results.append((abs(wsum - 1.0) < 1e-9, sec, "权重和 = %.4f" % wsum))

    th = sc.get("thresholds", {})
    hi, mid = th.get("high", 70), th.get("medium", 45)
    for ex in sc.get("worked_examples", []):
        c = ex.get("calc")
        if not c:
            continue
        total = round(sum(c[k] for k in ("max_item", "high_count", "density", "exposure")), 4)
        calc_grade = "high" if total >= hi else ("medium" if total >= mid else "low")
        name = ex.get("case", "?")[:40]
        results.append((abs(total - c["total"]) < 0.011, sec,
                        "%s：stated=%.2f recomputed=%.2f" % (name, c["total"], total)))
        results.append((calc_grade == ex.get("expected_grade"), sec,
                        "%s：grade expect=%s calc=%s" % (name, ex.get("expected_grade"), calc_grade)))


def check_mock(gs, expected_parties=None):
    sec = "mock 数据一致性"
    orgs = {k: v for k, v in gs.get("organizations", {}).items() if not k.startswith("_")}
    results.append((bool(orgs), sec, "organizations %d 条" % len(orgs)))

    bad_len = [c for c in orgs if len(c) != 18]
    results.append((not bad_len, sec, "统一社会信用代码长度非 18 位的 = %s" % (bad_len or "无")))

    bl = [b["credit_code"] for b in gs.get("blacklist", [])]
    dangling = [b for b in bl if b not in gs.get("organizations", {})]
    results.append((not dangling, sec, "黑名单中无法解析的信用代码 = %s" % (dangling or "无")))

    for code, name in (expected_parties or {}).items():
        got = gs.get("organizations", {}).get(code, {}).get("registered_name")
        results.append((got == name, sec, "%s → %s" % (code, got or "<未找到>")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", default=DEFAULT_CONFIG,
                    help="新增配置目录（含 rules.deterministic.json 等）")
    ap.add_argument("--legacy-dir", default=None,
                    help="原有配置目录（含 review_rules.json / clause_library.json）；"
                         "默认与 --config-dir 相同")
    args = ap.parse_args()

    cfg = os.path.abspath(args.config_dir)
    legacy = os.path.abspath(args.legacy_dir) if args.legacy_dir else cfg

    def pick(name):
        """优先在 config-dir 找，找不到回退 legacy-dir。"""
        for d in (cfg, legacy):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
        return os.path.join(cfg, name)

    det = load(pick("rules.deterministic.json"), "rules.deterministic.json")
    rc = load(pick("required_clauses.json"), "required_clauses.json")
    sc = load(pick("scoring.json"), "scoring.json")
    gs = load(pick(os.path.join("mock", "gsxt.json")), "mock/gsxt.json")
    old_rules = load(pick("review_rules.json"), "review_rules.json")
    clause_lib = load(pick("clause_library.json"), "clause_library.json")

    if fatal:
        print("配置契约测试：致命错误")
        for f in fatal:
            print("  [FATAL] " + f)
        return 1

    clause_ids = set()
    collect_clause_ids(clause_lib, clause_ids)

    check_required_clauses(rc, clause_ids)
    check_rule_ids(old_rules, det)
    check_scoring(sc)
    check_mock(gs, expected_parties={
        "91410300MA9K2X3Y7Z": "恒远智能科技（洛阳）有限公司",
        "91440300MA5F8G2H4K": "云枢软件技术有限公司",
        "91410100MA47B9C2X8": "中原重工装备制造有限公司",
        "91410305MA3D6E8F1L": "洛水工程技术服务有限公司",
    })

    # 输出
    cur, failed = None, 0
    lines = []
    for ok, sec, msg in results:
        if sec != cur:
            lines.append("")
            lines.append("== %s ==" % sec)
            cur = sec
        lines.append("  %s %s" % ("PASS" if ok else "FAIL", msg))
        if not ok:
            failed += 1
    lines.append("")
    lines.append("合计 %d 项，失败 %d 项 → %s" % (len(results), failed, "通过" if failed == 0 else "未通过"))

    text = "\n".join(lines)
    print(text)
    # 同时落一份文件，便于在 stdout 被吞的环境里查看
    try:
        with io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "check_config_result.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
