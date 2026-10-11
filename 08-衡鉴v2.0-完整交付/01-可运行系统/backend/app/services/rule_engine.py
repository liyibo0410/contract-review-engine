# -*- coding: utf-8 -*-
"""确定性规则引擎（主判定路径）

为什么规则引擎是主路径、LLM 只做增强？——因为审查结论要"可复现、可审计、可追责"。
同一份合同、同一版规则，任何时候跑都必须给出同一份命中清单；LLM 做不到这一点，
所以它只被允许做三件事：解释成因、起草建议、检测跨条款矛盾。判定这件事由规则引擎负责。

支持的判定类型（type 字段）：
    keyword        关键词命中（any / all / none 组合）
    regex          正则匹配
    numeric        数值阈值比对（违约金比例、赔偿上限等）
    imbalance      权责不对等（强弱措辞在同类条款中同时出现）
    missing_clause 必备条款缺失
    required_combo 条款组合缺失（如"付款"存在但缺少"验收"前置条件）
    absent         全篇缺少某项必要约定（通知送达、签署日期、变更形式等）

每条规则声明 clause_type（作用域）与 scope（clause / document），
命中后输出带原文坐标的完整证据链。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .. import config

_SENT_SPLIT = re.compile(r"[。；;]\s*|\n+")


class RuleError(Exception):
    pass


# ------------------------------------------------------------------ 视角解析
# 为什么需要这一步？"知识产权归甲方所有"这句话，在采购合同里是**对我方有利**的，
# 在销售合同里却是**对我方不利**的。脱离合同视角的关键词匹配必然会大面积误报，
# 所以规则库用 {我方} / {对方} 占位书写，运行期再按合同视角实例化。
DEFAULT_CTX = {"our_party": "甲方", "opponent": "乙方"}


def build_ctx(biz_type: str | None, first_party: str = "", second_party: str = "") -> dict:
    """按业务类型推断"我方"是哪一方。

    约定：采购 / 服务 / 劳动 → 我方是甲方（需方 / 委托方 / 用人单位）；
          销售 → 我方是乙方（卖方 / 供方）。
    """
    biz = (biz_type or "").strip()
    our = "乙方" if biz == "销售" else "甲方"
    opponent = "乙方" if our == "甲方" else "甲方"
    return {"our_party": our, "opponent": opponent, "biz_type": biz,
            "first_party": first_party, "second_party": second_party}


def _resolve(text: str, ctx: dict) -> str:
    if not isinstance(text, str):
        return text
    return (text.replace("{我方}", ctx.get("our_party", "甲方"))
                .replace("{对方}", ctx.get("opponent", "乙方")))


def _resolve_list(values, ctx: dict):
    return [_resolve(v, ctx) for v in (values or [])]


# ------------------------------------------------------------------ 加载与编译
@lru_cache(maxsize=8)
def _load(path_str: str, mtime: float) -> dict:
    path = Path(path_str)
    if not path.exists():
        raise RuleError(f"规则文件不存在：{path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rules = data.get("rules") or []
    if not isinstance(rules, list):
        raise RuleError("规则文件的 rules 必须是列表")
    seen = set()
    for r in rules:
        for req in ("id", "title", "level", "type"):
            if not r.get(req):
                raise RuleError(f"规则缺少必填字段 {req}：{r}")
        if r["id"] in seen:
            raise RuleError(f"规则 ID 重复：{r['id']}")
        seen.add(r["id"])
        if r["level"] not in ("high", "medium", "low"):
            raise RuleError(f"规则 {r['id']} 的风险等级非法：{r['level']}")
    data["_path"] = str(path)
    return data


def load_rules() -> dict:
    path = Path(config.RULE_FILE)
    mtime = path.stat().st_mtime if path.exists() else 0.0
    return _load(str(path), mtime)


def rule_version() -> str:
    try:
        return str(load_rules().get("version", "unknown"))
    except Exception:
        return "unknown"


@lru_cache(maxsize=8)
def _load_templates(path_str: str, mtime: float) -> dict:
    path = Path(path_str)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_templates() -> dict:
    path = Path(config.TEMPLATE_FILE)
    mtime = path.stat().st_mtime if path.exists() else 0.0
    return _load_templates(str(path), mtime)


def template_for(ref: str | None) -> dict:
    if not ref:
        return {}
    tpl = load_templates().get("templates") or {}
    for t in tpl:
        if t.get("id") == ref:
            return t
    return {}


# ------------------------------------------------------------------ 工具
def _sentences(clause: dict) -> list[dict]:
    """把条款切成句子，并保留每句在全文中的绝对字符区间（证据可以精确到句）。"""
    text = clause["text"]
    base = clause["char_start"]
    out = []
    pos = 0
    for piece in _SENT_SPLIT.split(text):
        if not piece:
            continue
        raw = piece.strip()
        i = text.find(raw, pos)
        if i < 0:
            i = pos
        pos = i + len(raw)
        out.append({
            "text": raw,
            "char_start": base + i,
            "char_end": base + i + len(raw),
        })
    if not out and text.strip():
        out.append({"text": text.strip(), "char_start": base,
                    "char_end": base + len(text.strip())})
    return out


def _clause_units(clauses: list[dict], scope_type: str | None) -> list[dict]:
    if not scope_type or scope_type == "*":
        return clauses
    return [c for c in clauses if c["clause_type"] == scope_type]


def _mk_finding(rule: dict, clause: dict | None, evidence: str,
                char_start: int, char_end: int, page: int, para_index: int,
                clause_type: str, extra_reason: str = "") -> dict:
    tpl = template_for(rule.get("advice_ref"))
    advice = tpl.get("text", "")
    reason = rule.get("reason", "")
    if extra_reason:
        reason = (reason + " " + extra_reason).strip()
    return {
        "rule_id": rule["id"],
        "rule_version": rule_version(),
        "title": rule.get("title", rule["id"]),
        "level": rule["level"],
        "clause_type": clause_type,
        "judge_mode": "rule",
        "evidence_text": evidence[:600],
        "page": page,
        "para_index": para_index,
        "char_start": char_start,
        "char_end": char_end,
        "reason": reason,
        "legal_basis": rule.get("legal_basis", ""),
        "advice": advice or rule.get("advice", ""),
        "advice_source": "template" if advice else "rule",
        "confidence": 1.0,
        "tags": rule.get("tags", []),
    }


# ------------------------------------------------------------------ 各类型判定
def _judge_keyword(rule: dict, clauses: list[dict], ctx: dict) -> list[dict]:
    p = rule.get("params") or {}
    any_w = _resolve_list(p.get("any"), ctx)
    all_w = _resolve_list(p.get("all"), ctx)
    none_w = _resolve_list(p.get("none"), ctx)
    out = []
    for clause in _clause_units(clauses, rule.get("clause_type")):
        for sent in _sentences(clause):
            t = sent["text"]
            if none_w and any(w in t for w in none_w):
                continue
            hit_all = all(w in t for w in all_w) if all_w else True
            hit_any = any(w in t for w in any_w) if any_w else True
            if not (all_w or any_w):
                hit_any = False
            if hit_all and hit_any:
                out.append(_mk_finding(rule, clause, t, sent["char_start"], sent["char_end"],
                                       clause["page"], clause["para_index"], clause["clause_type"]))
    return out


def _judge_regex(rule: dict, clauses: list[dict], ctx: dict) -> list[dict]:
    p = rule.get("params") or {}
    pattern = _resolve(p.get("pattern"), ctx)
    if not pattern:
        return []
    flags = re.I if p.get("ignore_case", True) else 0
    rx = re.compile(pattern, flags)
    min_hits = int(p.get("min_hits", 1))
    excludes = _resolve_list(p.get("exclude_keywords"), ctx)
    out = []
    for clause in _clause_units(clauses, rule.get("clause_type")):
        for sent in _sentences(clause):
            t = sent["text"]
            if excludes and any(k in t for k in excludes):
                continue
            m = rx.search(t)
            if m and len(rx.findall(t)) >= min_hits:
                out.append(_mk_finding(rule, clause, t, sent["char_start"], sent["char_end"],
                                       clause["page"], clause["para_index"], clause["clause_type"],
                                       extra_reason=_resolve(p.get("hit_note", ""), ctx)))
    return out


def _judge_numeric(rule: dict, clauses: list[dict], ctx: dict) -> list[dict]:
    p = rule.get("params") or {}
    patterns = p.get("patterns") or ([p["pattern"]] if p.get("pattern") else [])
    patterns = [_resolve(x, ctx) for x in patterns]
    op = p.get("op", ">")
    threshold = float(p.get("threshold", 0))
    factor = float(p.get("factor", 1.0))
    excludes = _resolve_list(p.get("exclude_keywords"), ctx)
    if not patterns:
        return []
    compiled = [re.compile(x, re.I) for x in patterns]
    ops = {
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        "==": lambda a, b: abs(a - b) < 1e-9,
    }
    cmp = ops.get(op)
    if cmp is None:
        raise RuleError(f"规则 {rule['id']} 的 op 非法：{op}")
    out = []
    for clause in _clause_units(clauses, rule.get("clause_type")):
        for sent in _sentences(clause):
            t = sent["text"]
            # 排除语境：如"累计不超过合同总价的 20%"讲的是上限，不是违约金比例
            if excludes and any(k in t for k in excludes):
                continue
            for rx in compiled:
                hit = False
                for m in rx.finditer(t):
                    try:
                        value = float(m.group(1).replace(",", "")) * factor
                    except (ValueError, IndexError):
                        continue
                    if cmp(value, threshold):
                        note = _resolve(p.get("hit_note", ""), ctx).format(
                            value=value, threshold=threshold)
                        out.append(_mk_finding(rule, clause, t, sent["char_start"],
                                               sent["char_end"], clause["page"],
                                               clause["para_index"], clause["clause_type"],
                                               extra_reason=note))
                        hit = True
                        break
                if hit:
                    break
    return out


def _judge_imbalance(rule: dict, clauses: list[dict], ctx: dict) -> list[dict]:
    """权责不对等：同一份合同里，我方义务被"加重措辞"修饰，而对方义务被"减轻措辞"修饰。

    这是一个只有跨句、跨条款比较才能发现的问题 —— 单看任一句都"没问题"。
    """
    p = rule.get("params") or {}
    heavy = _resolve_list(p.get("heavy"), ctx)     # 加重措辞（指向我方）
    light = _resolve_list(p.get("light"), ctx)     # 减轻措辞（指向对方）
    window = int(p.get("window", 6))               # 相对距离上限（句数）
    out = []
    for clause in _clause_units(clauses, rule.get("clause_type")):
        sents = _sentences(clause)
        heavy_idx = [i for i, s in enumerate(sents) if any(w in s["text"] for w in heavy)]
        light_idx = [i for i, s in enumerate(sents) if any(w in s["text"] for w in light)]
        if not heavy_idx or not light_idx:
            continue
        for hi in heavy_idx:
            close = [li for li in light_idx if 0 < abs(li - hi) <= window]
            if close:
                s = sents[hi]
                out.append(_mk_finding(rule, clause, s["text"], s["char_start"], s["char_end"],
                                       clause["page"], clause["para_index"], clause["clause_type"],
                                       extra_reason=_resolve(p.get("hit_note", ""), ctx)))
                break
    return out


def _judge_missing_clause(rule: dict, clauses: list[dict], full_text: str,
                          ctx: dict) -> list[dict]:
    p = rule.get("params") or {}
    target = p.get("clause_type") or rule.get("clause_type")
    if not target:
        return []
    if any(c["clause_type"] == target for c in clauses):
        return []
    # 允许用关键词兜底：有些合同把"验收"写在付款条款里，没有独立小节
    rescue = _resolve_list(p.get("rescue_keywords"), ctx)
    if rescue and any(k in full_text for k in rescue):
        return []
    anchor = _resolve(p.get("anchor_keyword"), ctx) or ""
    start, end = -1, -1
    if anchor:
        i = full_text.find(anchor)
        if i >= 0:
            start, end = i, i + len(anchor)
    label = config.CLAUSE_LABEL.get(target, target)
    reason = _resolve(p.get("hit_note"), ctx) or f"全篇未检索到「{label}」条款"
    f = _mk_finding(rule, None, anchor or f"（缺失条款：{label}）", start, end, 1, 0, target,
                    extra_reason=reason)
    f["judge_mode"] = "rule"
    return [f]


def _judge_required_combo(rule: dict, clauses: list[dict], full_text: str, ctx: dict) -> list[dict]:
    """条款组合缺失：某类条款存在，但缺少配套的必要条件。

    判据是"共现"而不是"全文出现过"：
    只有同一句话里既出现付款语境、又出现验收语境，才能认定"付款已挂钩验收"。
    否则会出现漏报 —— 质量保证条款里一句"质保期为验收合格后 12 个月"，
    会把付款环节根本没有验收前置的问题掩盖掉。
    """
    p = rule.get("params") or {}
    if_clause = p.get("if_clause_type")
    require = _resolve_list(p.get("require_keywords"), ctx)
    cooccur = _resolve_list(p.get("cooccur_keywords"), ctx) or [""]
    # 触发前提：合同里必须先出现 if_keywords 里的关键词，本规则才成立。
    # 例：没有约定竞业限制的劳动合同，谈不上"竞业限制没有补偿"。
    if_keywords = _resolve_list(p.get("if_keywords"), ctx)
    if if_keywords and not any(k in full_text for k in if_keywords):
        return []
    # 反向排除：句中虽出现必要条件关键词，但被否定（"无需支付经济补偿"）不算满足
    require_exclude = _resolve_list(p.get("require_exclude"), ctx)
    out = []
    if not if_clause or not require:
        return []
    units = _clause_units(clauses, if_clause)
    if not units:
        return []

    for clause in clauses:
        for sent in _sentences(clause):
            t = sent["text"]
            if require_exclude and any(x in t for x in require_exclude):
                continue
            if any(c in t for c in cooccur) and any(r in t for r in require):
                return []          # 已存在"条件 + 必要条件"的绑定表述

    anchor_unit = units[0]
    sent = _sentences(anchor_unit)[0]
    out.append(_mk_finding(rule, anchor_unit, sent["text"], sent["char_start"], sent["char_end"],
                           anchor_unit["page"], anchor_unit["para_index"], anchor_unit["clause_type"],
                           extra_reason=_resolve(p.get("hit_note", ""), ctx)))
    return out


def _judge_imbalance_regex(rule: dict, clauses: list[dict], ctx: dict) -> list[dict]:
    """权责不对等的正则版：单条措辞内部可能隔着若干字才出现"承担无限"等结论词，
    纯关键词列表匹配不到（"甲方逾期支付款项的，应承担无限赔偿责任"就是这种），
    所以用正则表达"同一方 + 若干字符内 + 加重/减轻措辞"。
    """
    p = rule.get("params") or {}
    heavy_rx = [re.compile(_resolve(x, ctx), re.I) for x in (p.get("heavy_patterns") or [])]
    light_rx = [re.compile(_resolve(x, ctx), re.I) for x in (p.get("light_patterns") or [])]
    window = int(p.get("window", 10))
    if not heavy_rx or not light_rx:
        return []
    out = []
    for clause in _clause_units(clauses, rule.get("clause_type")):
        sents = _sentences(clause)
        heavy_idx = [i for i, s in enumerate(sents) if any(r.search(s["text"]) for r in heavy_rx)]
        light_idx = [i for i, s in enumerate(sents) if any(r.search(s["text"]) for r in light_rx)]
        if not heavy_idx or not light_idx:
            continue
        for hi in heavy_idx:
            close = [li for li in light_idx if 0 < abs(li - hi) <= window]
            if close:
                s = sents[hi]
                out.append(_mk_finding(rule, clause, s["text"], s["char_start"], s["char_end"],
                                       clause["page"], clause["para_index"],
                                       clause["clause_type"],
                                       extra_reason=_resolve(p.get("hit_note", ""), ctx)))
                break
    return out


def _judge_absent(rule: dict, clauses: list[dict], full_text: str, ctx: dict) -> list[dict]:
    """全篇缺失型：一份正常合同"应该有"的约定，通篇找不到。

    与 missing_clause 的区别：missing_clause 判的是"整类条款节点不存在"，
    absent 判的是"某类条款在，但其中缺少某个必要要素"（如保密在、保密期限不在）。
    """
    p = rule.get("params") or {}
    must_absent = _resolve_list(p.get("none"), ctx)
    if not must_absent:
        return []
    # 前置条件：只有当合同中确实出现了 if_any 里的关键词，才检查"缺少某要素"。
    # 例：只有合同真的约定了保密，才谈得上"保密没写期限"。
    if_any = _resolve_list(p.get("if_any"), ctx)
    if if_any and not any(k in full_text for k in if_any):
        return []
    if any(k in full_text for k in must_absent):
        return []
    anchor = _resolve(p.get("anchor_keyword"), ctx) or ""
    start, end, page, para = -1, -1, 1, 0
    unit = None
    if anchor:
        i = full_text.find(anchor)
        if i >= 0:
            start, end = i, i + len(anchor)
            page, para = coordinator_coord(full_text, clauses, i)
            unit = next((c for c in clauses if c["char_start"] <= i <= c["char_end"]), None)
    label = p.get("label") or rule["title"]
    note = _resolve(p.get("hit_note"), ctx) or f"全篇未检索到「{label}」相关约定"
    f = _mk_finding(rule, unit, anchor or f"（缺失约定：{label}）", start, end, page, para,
                    (unit or {}).get("clause_type", rule.get("clause_type", "other")),
                    extra_reason=note)
    return [f]


def coordinator_coord(full_text: str, clauses: list[dict], pos: int) -> tuple[int, int]:
    for c in clauses:
        if c["char_start"] <= pos <= c["char_end"]:
            return c["page"], c["para_index"]
    return 1, 0


_JUDGES = {
    "keyword": lambda r, c, t, x: _judge_keyword(r, c, x),
    "regex": lambda r, c, t, x: _judge_regex(r, c, x),
    "numeric": lambda r, c, t, x: _judge_numeric(r, c, x),
    "imbalance": lambda r, c, t, x: _judge_imbalance(r, c, x),
    "imbalance_regex": lambda r, c, t, x: _judge_imbalance_regex(r, c, x),
    "missing_clause": lambda r, c, t, x: _judge_missing_clause(r, c, t, x),
    "required_combo": lambda r, c, t, x: _judge_required_combo(r, c, t, x),
    "absent": lambda r, c, t, x: _judge_absent(r, c, t, x),
}


# ------------------------------------------------------------------ 主入口
def evaluate(clauses: list[dict], full_text: str, *, ctx: dict | None = None,
             rule_filter: str | None = None) -> list[dict]:
    """执行全部规则，返回按风险等级排序的命中清单（已去重）。

    ctx 提供合同视角（我方 / 对方）与业务类型，用于：
      · 把规则里的 {我方} / {对方} 占位实例化；
      · 按 biz_types 过滤不适用该业务类型的规则（如"付款须挂钩验收"对劳动合同不成立）。
    """
    data = load_rules()
    context = {**DEFAULT_CTX, **(ctx or {})}
    biz = (context.get("biz_type") or "").strip()
    findings: list[dict] = []
    for rule in data.get("rules", []):
        if rule_filter and rule["id"] != rule_filter:
            continue
        if rule.get("enabled") is False:
            continue
        biz_types = rule.get("biz_types")
        if biz_types and biz and biz not in biz_types:
            continue
        judge = _JUDGES.get(rule["type"])
        if judge is None:
            raise RuleError(f"规则 {rule['id']} 的 type 不支持：{rule['type']}")
        try:
            findings.extend(judge(rule, clauses, full_text, context))
        except re.error as exc:
            raise RuleError(f"规则 {rule['id']} 正则非法：{exc}") from exc

    # 去重：同一规则 + 同一字符区间只保留一条
    seen = set()
    uniq = []
    for f in findings:
        key = (f["rule_id"], f["char_start"], f["char_end"], f["evidence_text"][:40])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)

    uniq.sort(key=lambda f: (config.RISK_ORDER.get(f["level"], 9), f["page"], f["char_start"]))
    return uniq


def rule_tree() -> list[dict]:
    """按条款类型分组，供规则配置页展示树状列表。"""
    data = load_rules()
    groups: dict[str, list[dict]] = {}
    for r in data.get("rules", []):
        groups.setdefault(r.get("clause_type") or "other", []).append(r)
    tree = []
    for ctype, rules in groups.items():
        tree.append({
            "clause_type": ctype,
            "label": config.CLAUSE_LABEL.get(ctype, ctype),
            "count": len(rules),
            "rules": sorted(rules, key=lambda r: (config.RISK_ORDER.get(r["level"], 9), r["id"])),
        })
    tree.sort(key=lambda g: (-g["count"], g["label"]))
    return tree


def stats() -> dict:
    data = load_rules()
    rules = data.get("rules", [])
    by_level: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rules:
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    return {
        "version": data.get("version", "unknown"),
        "updated_at": data.get("updated_at", ""),
        "total": len(rules),
        "by_level": by_level,
        "by_type": by_type,
        "template_count": len((load_templates().get("templates") or [])),
    }


def save_rule(rule_id: str, patch: dict[str, Any]) -> dict:
    """就地修改规则文件中的一条规则（保持 YAML 可读性，不做全量重写格式化）。"""
    path = Path(config.RULE_FILE)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rules = data.get("rules") or []
    for r in rules:
        if r.get("id") == rule_id:
            allowed = {"title", "level", "clause_type", "enabled", "reason", "legal_basis", "params"}
            for k, v in patch.items():
                if k in allowed:
                    r[k] = v
            data["updated_at"] = __import__("datetime").date.today().isoformat()
            with path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=200)
            _load.cache_clear()
            return r
    raise RuleError(f"规则不存在：{rule_id}")
