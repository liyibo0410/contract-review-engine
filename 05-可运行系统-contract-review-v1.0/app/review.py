# -*- coding: utf-8 -*-
"""智能审查引擎 + 条款推荐引擎（零第三方依赖）

设计原则（对齐架构图「风险审查层」）：
  * 风险识别主路径是**确定性规则**（配置化：关键词 / 正则 / 必备条款缺失 / 数值阈值 / 不对称配对），
    大模型只用于生成中文审查摘要与优化表达，不参与风险判定，也不触发审批动作；
  * 每条命中都必须回带原文证据片段与可定位坐标（页码 + 段落索引 + 字符区间）；
  * 综合风险等级取所有命中项中的最高等级。
"""
from __future__ import annotations

import json
import os
import re

from . import store

RULES_DIR = os.path.join("config", "rules")
CLAUSE_TYPE_FILE = os.path.join("config", "clause_types.json")
CLAUSE_LIB_FILE = os.path.join("config", "clauses", "clauses.json")

_CACHE = {}


# ------------------------------------------------------------------ 基础工具
def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_clause_types(root):
    key = ("ct", root)
    if key not in _CACHE:
        _CACHE[key] = _read_json(os.path.join(root, CLAUSE_TYPE_FILE))
    return _CACHE[key]


def load_clause_library(root):
    key = ("cl", root)
    if key not in _CACHE:
        _CACHE[key] = _read_json(os.path.join(root, CLAUSE_LIB_FILE))
    return _CACHE[key]


def list_rule_files(root):
    d = os.path.join(root, RULES_DIR)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            out.append(fn[:-5])
    return out


def load_rules(root, contract_type):
    d = os.path.join(root, RULES_DIR)
    files = list_rule_files(root)
    if contract_type in files:
        data = _read_json(os.path.join(d, contract_type + ".json"))
    else:
        data = {"contract_type": contract_type, "type_label": contract_type,
                "stance": "", "rules": [], "version": "1.0"}
    return data


def save_rules(root, contract_type, data):
    """规则可维护：写回配置文件（供管理端页面使用）。"""
    path = os.path.join(root, RULES_DIR, contract_type + ".json")
    bak = path + ".bak"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            old = f.read()
        with open(bak, "w", encoding="utf-8") as f:
            f.write(old)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _CACHE.pop(("cl", root), None)
    return path


# ------------------------------------------------------------------ 文本规整
_CJK = r"\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"


def despace(text):
    """去掉 CJK 字符之间的多余空格（PDF 抽取常见「逐字空格」现象）。

    返回 (clean_text, idx_map)，idx_map[i] 为 clean 第 i 个字符在原文中的下标，
    因此命中位置可以无损映射回原文坐标。
    """
    clean = []
    idx = []
    n = len(text)
    for i, ch in enumerate(text):
        if ch == " ":
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < n else ""
            if re.match("[" + _CJK + "]", prev or "") and re.match("[" + _CJK + "]", nxt or ""):
                continue
        clean.append(ch)
        idx.append(i)
    return "".join(clean), idx


def _sentence_around(text, start, end):
    """截取命中位置所在的句子作为证据片段。"""
    left = max(text.rfind("。", 0, start), text.rfind("；", 0, start),
               text.rfind("\n", 0, start), text.rfind("：", 0, start))
    right_candidates = [p for p in (text.find("。", end), text.find("；", end),
                                    text.find("：", end)) if p != -1]
    right = min(right_candidates) if right_candidates else -1
    a = left + 1 if left >= 0 else max(0, start - 60)
    b = right + 1 if right >= 0 else min(len(text), end + 60)
    return text[a:b].strip(), a


# ------------------------------------------------------------------ 元数据抽取
CREDIT_RE = re.compile(r"\b([0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10})\b")
AMOUNT_RE = re.compile(
    r"(?:合同)?(?:总)?(?:金额|价款|总价|价税合计|合同价)[^\d]{0,12}"
    r"([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(万元|万|元|美元|USD|CNY|人民币)")
PERIOD_RE = re.compile(
    r"(?:履行期限|合同期限|服务期限|工期)[^。；\n]{0,40}"
    r"|自\s*(\d{4}\s*年\s*\d{1,2}\s*月[^。；\n]{0,20}?)(?:起|至|到)\s*"
    r"(\d{4}\s*年\s*\d{1,2}\s*月[^。；\n]{0,12})"
    r"|(?:自|从)(?:本)?合同(?:生效|签订|签署)(?:之日)?(?:起|后)\s*"
    r"(\d+(?:\.\d+)?)\s*(个)?\s*(日历日|工作日|自然日|个月|月|日|年)")
EFFECT_RE = re.compile(
    r"(?:自|经)[^。；\n]{0,60}?(?:签字|盖章|签署)[^。；\n]{0,40}?(?:生效|成立)")
PARTY_A = re.compile(r"甲\s*方\s*[（(]?[^）)\n：:]{0,6}[）)]?\s*[：:]\s*([^\n。；，,]{2,40})")
PARTY_B = re.compile(r"乙\s*方\s*[（(]?[^）)\n：:]{0,8}[）)]?\s*[：:]\s*([^\n。；，,]{2,40})")
TITLE_RE = re.compile(r"[《]([^》\n]{4,60})[》]")


def extract_metadata(paras, text, filename=""):
    """抽取合同核心元数据：主体、信用代码、金额、币种、期限、生效条件。"""
    meta = {
        "title": "", "party_a": "", "party_b": "", "credit_code": "",
        "amount": None, "amount_raw": "", "currency": "CNY",
        "perform_period": "", "effective_condition": "", "contract_no": "",
    }
    head = text[:600]

    m = TITLE_RE.search(head) or TITLE_RE.search(text[:2000])
    if m:
        meta["title"] = m.group(1).strip()
    elif filename:
        meta["title"] = re.sub(r"\.(docx|pdf|png|jpg|jpeg)$", "", filename, flags=re.I)

    m = PARTY_A.search(text[:3000])
    if m:
        meta["party_a"] = m.group(1).strip()
    m = PARTY_B.search(text[:3000])
    if m:
        meta["party_b"] = m.group(1).strip()

    m = CREDIT_RE.search(text)
    if m:
        meta["credit_code"] = m.group(1)

    m = AMOUNT_RE.search(text)
    if m:
        raw_num = m.group(1).replace(",", "").replace("，", "")
        try:
            val = float(raw_num)
        except ValueError:
            val = None
        unit = m.group(2)
        factor = 1.0
        if unit in ("万元", "万"):
            factor = 10000.0
        if unit in ("美元", "USD"):
            meta["currency"] = "USD"
        elif unit in ("元", "人民币", "CNY"):
            meta["currency"] = "CNY"
        if val is not None:
            meta["amount"] = val * factor
            meta["amount_raw"] = m.group(0).strip()

    m = PERIOD_RE.search(text)
    if m:
        meta["perform_period"] = re.sub(r"\s+", "", m.group(0))[:80]

    m = EFFECT_RE.search(text)
    if m:
        meta["effective_condition"] = re.sub(r"\s+", "", m.group(0))[:100]

    m = re.search(r"(?:合同编号|合同号|编号)\s*[：:]\s*([A-Za-z0-9\-/（）()]{3,40})", text)
    if m:
        meta["contract_no"] = m.group(1).strip()

    # 主体名称缺失时用「甲方/乙方」占位提示
    if not meta["party_b"]:
        m = re.search(r"乙\s*方[^。；\n]{0,30}?([\u4e00-\u9fff（）()]{4,40}(?:公司|中心|学院|医院|工厂|事务所|商行|店))", text)
        if m:
            meta["party_b"] = m.group(1)
    return meta


# ------------------------------------------------------------------ 条款切分
CLAUSE_HEAD_RE = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百零〇\d]+\s*[条章])"
    r"|^\s*[一二三四五六七八九十]+\s*[、．.]"
    r"|^\s*\d+(?:\.\d+)+\s*"
    r"|^\s*[（(]\s*[一二三四五六七八九十\d]+\s*[）)]")


def classify_clause(text, clause_types):
    """按关键词词典给段落/条款打条款类型标签。"""
    best = (0, clause_types["fallback"])
    for t in clause_types["types"]:
        score = sum(1 for kw in t["keywords"] if kw in text)
        if score > best[0]:
            best = (score, {"code": t["code"], "label": t["label"]})
    return best[1]


def split_clauses(paras, clause_types):
    """把段落树切分为条款层级结构，并给每个段落附条款类型。"""
    for p in paras:
        c = classify_clause(p["text"], clause_types)
        p["clause_code"] = c["code"]
        p["clause_label"] = c["label"]

    clauses = []
    cur = None
    for p in paras:
        starts_new = bool(CLAUSE_HEAD_RE.match(p["text"])) or p.get("is_heading")
        if starts_new or cur is None:
            if cur:
                clauses.append(cur)
            head = re.sub(r"^\s*", "", p["text"])[:60]
            no_match = re.match(r"^\s*(第\s*[一二三四五六七八九十百零〇\d]+\s*[条章])", p["text"])
            cur = {
                "seq": len(clauses) + 1,
                "no": no_match.group(1) if no_match else "",
                "title": head,
                "clause_code": p["clause_code"],
                "clause_label": p["clause_label"],
                "page": p["page"],
                "char_start": p["char_start"],
                "char_end": p["char_end"],
                "para_start": p["index"],
                "para_end": p["index"],
                "text": p["text"],
            }
        else:
            cur["para_end"] = p["index"]
            cur["text"] += "\n" + p["text"]
            cur["char_end"] = p["char_end"]
            # 类型取该条款内出现次数最多的标签
            if cur["clause_label"] == clause_types["fallback"]["label"]:
                cur["clause_code"] = p["clause_code"]
                cur["clause_label"] = p["clause_label"]
    if cur:
        clauses.append(cur)
    for c in clauses:
        c["char_count"] = len(c["text"])
    return clauses


def clause_coverage(paras, clause_types):
    """条款覆盖度：用于规则配置页与工作台展示「已识别条款类型占比」。"""
    total = len(clause_types["types"])
    hit = {}
    for p in paras:
        hit[p["clause_code"]] = hit.get(p["clause_code"], 0) + 1
    present = [t["label"] for t in clause_types["types"] if t["code"] in hit]
    missing = [t["label"] for t in clause_types["types"] if t["code"] not in hit]
    return {
        "total_types": total,
        "present": present,
        "missing": missing,
        "coverage_rate": round(len(present) / total, 4) if total else 0.0,
        "counts": hit,
    }


# ------------------------------------------------------------------ 命中锚定
def _locate_para(paras, offset):
    for p in paras:
        if p["char_start"] <= offset <= p["char_end"]:
            return p
    return paras[0] if paras else None


def _compile(pattern, diag, where):
    """预编译正则。非法表达式记入诊断而不是静默失效（规则页支持在线编辑，必须能发现写错）。"""
    try:
        return re.compile(pattern)
    except re.error as exc:
        diag.append({"where": where, "pattern": pattern, "error": str(exc)})
        return None


def _match_compiled(text, cache, rx):
    """用已编译正则匹配：先在原文上试，再在去空格文本上试并映射回原文坐标。

    返回 (start, end, matched, evidence, evidence_offset) 或 None。
    """
    m = rx.search(text)
    if m:
        ev, off = _sentence_around(text, m.start(), m.end())
        return m.start(), m.end(), m.group(0), ev, off
    clean, idx_map = cache
    if not clean:
        return None
    m = rx.search(clean)
    if not m:
        return None
    s = idx_map[m.start()]
    e = idx_map[min(m.end(), len(idx_map)) - 1] + 1
    ev, off = _sentence_around(text, s, e)
    return s, e, m.group(0), ev, off


def run_rules(text, paras, rules_data, clause_types, diag=None):
    """执行规则库，输出带回原文坐标的命中清单。

    性能：每个段落的「去空格文本」只计算一次（缓存在本次调用内的字典里，不污染段落数据），
    正则也预编译一次复用 —— 原实现是「每条规则 × 每个段落 × 每个表达式」都重算一遍，
    长合同下这是主要的 CPU 开销。

    健壮性：非法正则会写入 diag 并暴露到规则页与审计，而不是让规则悄悄失效。
    """
    hits = []
    if diag is None:
        diag = []
    _despaced = {}
    _whole = []

    def para_cache(p):
        key = p["index"]
        c = _despaced.get(key)
        if c is None:
            c = despace(p["text"])
            _despaced[key] = c
        return c

    def whole_cache():
        if not _whole:
            _whole.append(despace(text))
        return _whole[0]

    for rule in rules_data.get("rules", []):
        rid = rule.get("id") or "?"
        mode = rule.get("mode", "keyword")
        raw = rule.get("patterns") or []

        if mode == "absence":
            present = False
            cache = whole_cache()
            for pat in raw:
                src = pat if _looks_like_regex(pat) else re.escape(pat)
                rx = _compile(src, diag, "%s(%s)" % (rid, mode))
                if rx and _match_compiled(text, cache, rx):
                    present = True
                    break
            if not present:
                hits.append(_mk_hit(rule, None, "", None))
            continue

        compiled = []
        for pat in raw:
            src = pat if mode in ("regex", "pair", "asymmetry", "numeric") \
                else re.escape(pat)
            rx = _compile(src, diag, "%s(%s)" % (rid, mode))
            if rx:
                compiled.append(rx)

        found = None
        for p in paras:
            cache = para_cache(p)
            for rx in compiled:
                r = _match_compiled(p["text"], cache, rx)
                if r:
                    found = (p, r[0], r[1], r[2], r[3])
                    break
            if found:
                break

        if mode in ("pair", "asymmetry"):
            alt_compiled = []
            for pat in (rule.get("patterns_alt") or []):
                rx = _compile(pat, diag, "%s(%s.alt)" % (rid, mode))
                if rx:
                    alt_compiled.append(rx)
            alt = None
            for p in paras:
                cache = para_cache(p)
                for rx in alt_compiled:
                    r = _match_compiled(p["text"], cache, rx)
                    if r:
                        alt = (p, r[0], r[1], r[2], r[3])
                        break
                if alt:
                    break
            if found and alt:
                ev = "【本方不利表述】%s\n【相对方免责/封顶表述】%s" % (found[4], alt[4])
                hits.append(_mk_hit(rule, found[0], ev, found[1], found[2],
                                    alt_para=alt[0], alt_evidence=alt[4]))
            continue

        if mode == "numeric":
            if found:
                m = re.search(r"([0-9]+(?:\.[0-9]+)?)", found[3] or "")
                if m:
                    try:
                        val = float(m.group(1))
                    except ValueError:
                        val = None
                    if val is not None and _cmp(val, rule.get("numeric_op", "gt"),
                                                float(rule.get("numeric_threshold", 0))):
                        hits.append(_mk_hit(
                            rule, found[0], found[4], found[1], found[2],
                            extra="（实测值 %s，阈值 %s %s）" % (
                                m.group(1), rule.get("numeric_op", "gt"),
                                rule.get("numeric_threshold"))))
            continue

        if found:
            hits.append(_mk_hit(rule, found[0], found[4], found[1], found[2]))
    return hits


def _looks_like_regex(pat):
    return bool(re.search(r"[\\\[\](){}|?*+^$]", pat))


def _cmp(a, op, b):
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b,
            "eq": a == b}.get(op, False)


def _mk_hit(rule, para, evidence, start, end=None, extra="", alt_para=None,
            alt_evidence=None):
    return {
        "rule_id": rule.get("id"),
        "rule_name": rule.get("name"),
        "risk_level": rule.get("risk_level"),
        "clause_type": rule.get("clause_type"),
        "match_mode": rule.get("mode"),
        "evidence": evidence or "",
        "page_no": para["page"] if para else None,
        "para_index": para["index"] if para else None,
        "char_start": para["char_start"] + start if (para and start is not None) else None,
        "char_end": para["char_start"] + end if (para and end is not None) else None,
        "reason": rule.get("reason", ""),
        "legal_basis": rule.get("legal_basis", ""),
        "suggestion": rule.get("suggestion_default", ""),
        "extra": extra,
        "alt_page_no": alt_para["page"] if alt_para else None,
        "alt_para_index": alt_para["index"] if alt_para else None,
        "alt_evidence": alt_evidence or "",
        "anchored": para is not None,
    }


# ------------------------------------------------------------------ 条款推荐
def build_suggestion(rule_or_hit, library):
    """条款推荐引擎：优先取标准条款库，回退到规则内置示范条款。"""
    rid = rule_or_hit.get("rule_id")
    options = (library.get("library") or {}).get(rid)
    if options:
        primary = options[0]["text"]
        alts = options[1:]
        return primary, alts
    return rule_or_hit.get("suggestion", ""), []


# ------------------------------------------------------------------ 结论
def decide_conclusion(risk_level, hit_count_high, hit_count_total, thresholds):
    if hit_count_high >= thresholds.get("reject_high_count", 2):
        return "reject"
    if hit_count_high >= 1 or hit_count_total >= thresholds.get("rectify_total", 1):
        return "rectify"
    if risk_level in (store.R_MEDIUM, store.R_LOW):
        return "rectify"
    return "pass"


CONCLUSION_LABEL = {"pass": "通过", "rectify": "需整改", "reject": "建议拒绝"}
