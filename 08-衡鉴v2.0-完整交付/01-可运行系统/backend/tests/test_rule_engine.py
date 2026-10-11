# -*- coding: utf-8 -*-
"""规则引擎单元测试

覆盖六种判定类型与视角解析 —— 这些是"判定正确性"的最后一道防线。
运行：pytest（在 backend 目录下）
"""
from __future__ import annotations

from app.services import coordinator, rule_engine


def _clauses(pairs):
    """pairs: [(clause_type, text)] -> 带坐标的条款列表（模拟 coordinator 的产出）"""
    offset = 0
    out = []
    for ctype, text in pairs:
        out.append({
            "clause_type": ctype, "heading": text[:20], "page": 1, "para_index": 0,
            "char_start": offset, "char_end": offset + len(text), "text": text,
            "confidence": 1.0,
        })
        offset += len(text) + 1
    return out


def _full(clauses):
    return "\n".join(c["text"] for c in clauses)


def _hits(clauses, ctx=None, rule=None):
    return rule_engine.evaluate(clauses, _full(clauses), ctx=ctx or {"our_party": "甲方",
                                                                    "opponent": "乙方"},
                                rule_filter=rule)


# ------------------------------------------------------------------ 数据校验
def test_rule_library_loads_and_is_consistent():
    stats = rule_engine.stats()
    assert stats["total"] >= 30, "规则总数应不少于 30 条"
    assert stats["by_level"].get("high", 0) >= 10
    assert stats["template_count"] >= 13


def test_rule_tree_groups_by_clause_type():
    tree = rule_engine.rule_tree()
    assert tree and all("rules" in g and g["count"] == len(g["rules"]) for g in tree)


# ------------------------------------------------------------------ 视角解析
def test_perspective_ip_clause_is_flagged_only_when_owned_by_opponent():
    text = "本合同项下交付成果的知识产权归乙方所有"
    # 我方=甲方（采购）：归乙方所有 = 对我方不利 -> 命中
    hit_a = _hits(_clauses([("ip", text)]), {"our_party": "甲方", "opponent": "乙方"},
                  "HJ-H-001")
    assert len(hit_a) == 1
    # 我方=乙方（销售）：归乙方所有 = 归我方 -> 不应命中
    hit_b = _hits(_clauses([("ip", text)]), {"our_party": "乙方", "opponent": "甲方"},
                  "HJ-H-001")
    assert hit_b == []


def test_build_ctx_maps_biz_type_to_our_party():
    assert rule_engine.build_ctx("采购")["our_party"] == "甲方"
    assert rule_engine.build_ctx("服务")["our_party"] == "甲方"
    assert rule_engine.build_ctx("劳动")["our_party"] == "甲方"
    assert rule_engine.build_ctx("销售")["our_party"] == "乙方"


# ------------------------------------------------------------------ 各判定类型
def test_keyword_rule_fires_with_coordinates():
    clauses = _clauses([("ip", "开发成果的知识产权归乙方所有。")])
    hits = _hits(clauses, rule="HJ-H-001")
    assert len(hits) == 1
    f = hits[0]
    assert f["level"] == "high"
    assert f["char_start"] >= 0 and f["char_end"] > f["char_start"]
    assert "知识产权归乙方所有" in f["evidence_text"]
    assert f["legal_basis"] and f["advice"]


def test_regex_rule_anchors_on_our_party_and_respects_exclude():
    risky = _clauses([("liability", "甲方逾期支付款项的，应承担无限赔偿责任，并赔偿乙方一切损失。")])
    assert len(_hits(risky, rule="HJ-H-008")) == 1
    safe = _clauses([("liability", "甲方承担的赔偿责任以合同总价为上限，不超过合同总价。")])
    assert _hits(safe, rule="HJ-H-008") == []


def test_numeric_rule_ignores_cap_wording():
    """'累计不超过合同总价的 20%'是上限保护，不是违约金比例风险。"""
    cap = _clauses([("liability", "支付违约金，标准为每日万分之五，累计不超过合同总价的 20%。")])
    assert _hits(cap, rule="HJ-M-006") == []
    rate = _clauses([("liability", "任何一方违约的，应向守约方支付合同总价 18% 的违约金。")])
    assert len(_hits(rate, rule="HJ-M-006")) == 1


def test_imbalance_regex_detects_asymmetric_liability():
    """销售视角：我方=乙方，对方=甲方。我方无上限赔偿 + 对方万分之一滞纳金。"""
    text = ("甲方逾期付款的，仅承担每日万分之一的滞纳金。"
            "乙方不设赔偿上限，应赔偿甲方因此遭受的全部损失。")
    hits = _hits(_clauses([("liability", text)]),
                 {"our_party": "乙方", "opponent": "甲方"}, "HJ-H-003")
    assert len(hits) == 1
    assert "不设赔偿上限" in hits[0]["evidence_text"] or "仅承担" in hits[0]["evidence_text"]
    # 对称的违约责任不应命中
    balanced = ("任何一方违约的，应支付违约金，累计不超过合同总价的 20%。"
                "双方累计赔偿责任均以合同总价为上限。")
    assert _hits(_clauses([("liability", balanced)]),
                 {"our_party": "乙方", "opponent": "甲方"}, "HJ-H-003") == []


def test_missing_clause_rule_reports_absence():
    clauses = _clauses([("payment", "甲方应支付合同价款 100 万元。")])
    hits = _hits(clauses, rule="HJ-H-009")     # 缺失违约责任条款
    assert len(hits) == 1
    assert hits[0]["clause_type"] == "liability"


def test_absent_rule_respects_if_keywords():
    """没有约定竞业限制，就不该报"竞业限制没有补偿"。"""
    no_compete_absent = _clauses([("confidentiality", "双方对商业秘密负有保密义务。")])
    assert _hits(no_compete_absent, {"our_party": "甲方", "opponent": "乙方",
                                     "biz_type": "劳动"}, "HJ-H-011") == []
    no_compete_present = _clauses([("confidentiality",
                                    "乙方离职后 2 年内不得从事同业竞争业务，甲方无需另行支付任何经济补偿。")])
    assert len(_hits(no_compete_present, {"our_party": "甲方", "opponent": "乙方",
                                          "biz_type": "劳动"}, "HJ-H-011")) == 1


def test_biz_types_restricts_rule_scope():
    """'付款须挂钩验收'对劳动合同不成立。"""
    clauses = _clauses([("payment", "甲方于每月 15 日前支付上月工资。")])
    labor = _hits(clauses, {"our_party": "甲方", "opponent": "乙方", "biz_type": "劳动"},
                  "HJ-H-002")
    purchase = _hits(clauses, {"our_party": "甲方", "opponent": "乙方", "biz_type": "采购"},
                     "HJ-H-002")
    assert labor == []
    assert len(purchase) == 1


def test_required_combo_detects_payment_without_acceptance_binding():
    """全篇有"验收合格"，但只出现在质保条款里 —— 付款仍未挂钩验收，应命中。"""
    clauses = _clauses([
        ("payment", "甲方应在收到货物后 60 日内一次性支付全部货款。"),
        ("liability", "乙方保证质保期为验收合格后 12 个月。"),
    ])
    assert len(_hits(clauses, {"our_party": "甲方", "opponent": "乙方",
                               "biz_type": "采购"}, "HJ-H-002")) == 1
    bound = _clauses([
        ("payment", "各期付款均以甲方出具的验收合格证明为前置条件，验收合格后 15 日内支付。"),
    ])
    assert _hits(bound, {"our_party": "甲方", "opponent": "乙方",
                         "biz_type": "采购"}, "HJ-H-002") == []


# ------------------------------------------------------------------ 去重与排序
def test_findings_are_deduplicated_and_sorted_by_level():
    text = "甲方逾期支付款项的，应承担无限赔偿责任。甲方单方解除的，应承担无限赔偿责任。"
    hits = _hits(_clauses([("liability", text)]), rule="HJ-H-008")
    keys = {(h["rule_id"], h["char_start"], h["char_end"]) for h in hits}
    assert len(keys) == len(hits), "同一规则同一区间不应重复"
    all_hits = rule_engine.evaluate(_clauses([("ip", "知识产权归乙方所有。"),
                                              ("liability", "甲方应承担无限赔偿责任。")]),
                                    "知识产权归乙方所有。\n甲方应承担无限赔偿责任。",
                                    ctx={"our_party": "甲方", "opponent": "乙方"})
    levels = [h["level"] for h in all_hits]
    assert levels == sorted(levels, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x])


def test_unknown_rule_type_raises():
    """规则库里出现引擎不支持的 type 时必须显式报错，不能静默跳过。"""
    import pytest
    data = rule_engine.load_rules()
    original = list(data["rules"])
    try:
        data["rules"].append({"id": "HJ-X-999", "title": "非法类型",
                              "level": "low", "type": "__not_supported__"})
        with pytest.raises(rule_engine.RuleError):
            rule_engine.evaluate(_clauses([("other", "文本")]), "文本")
    finally:
        data["rules"][:] = original
