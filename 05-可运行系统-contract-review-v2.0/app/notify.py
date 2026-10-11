# -*- coding: utf-8 -*-
"""通知模块：钉钉自定义机器人 / 通用 Webhook

职责：把关键事件（高风险审查完成、解析受阻、回写失败、批量汇总）推送到群机器人。

设计约束（重要）：
  * **通知永不阻断主流程**：发送在后台线程异步进行，任何异常只记审计与运行记录，
    绝不向调用方抛出；审核类系统不能因为「通知失败」导致审查中断。
  * **零第三方依赖**：钉钉「加签」用标准库 hmac/hashlib/base64 实现。
  * **防抖与限流**：同一事件类型可配置最短发送间隔，避免批量审查时刷屏。
  * **可观测**：保留最近发送记录与统计，页面上可查看并测试发送。

钉钉加签算法（官方要求，签名不匹配会返回 errcode 310000）：
    string_to_sign = "{timestamp}\n{secret}"
    sign = urlencode(base64(hmac_sha256(secret, string_to_sign)))
    最终请求 URL 追加 &timestamp=..&sign=..
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

EVENTS = ("review_completed", "high_risk", "blocked", "writeback_failed",
          "batch_summary")

EVENT_LABEL = {
    "review_completed": "审查完成",
    "high_risk": "高风险合同",
    "blocked": "解析受阻",
    "writeback_failed": "回写失败",
    "batch_summary": "批量汇总",
    "test": "连通性测试",
}

# ------------------------------------------------------------------ 外发脱敏（决议 D-31）
# 通知通道是**本项目唯一把数据送出本机的出口**，而合同属最敏感数据。
# 因此这里默认全掩码：相对方名称、合同金额、统一社会信用代码一律不出明文，
# 通知正文也不允许携带合同正文片段（max_evidence_chars=0）。
# 演示需要放宽时改 config/app.json 的 notify.mask，不改代码。
MASK_DEFAULT = {
    "party": True, "amount": True, "credit_code": True,
    "case_no": False, "applicant": True, "contract_title": True,
    "max_evidence_chars": 0,
}

# 18 位统一社会信用代码（GB 32100-2015 字符集，不含 I O S V Z）
_RE_CREDIT = re.compile(r"(?<![0-9A-Z])[0-9A-HJ-NPQRTUWXY]{18}(?![0-9A-Z])")
# 金额：带千分位或带单位的中文/西文写法
_RE_AMOUNT = re.compile(
    r"(?P<cur>[¥￥])?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<unit>万元|亿元|元|万|亿)?(?P<after>人民币|RMB)?")
_RE_CN_AMOUNT = re.compile(r"(?:人民币|RMB)\s*\d[\d,]*(?:\.\d+)?\s*(?:万元|亿元|元)?")
_RE_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def mask_cfg(cfg) -> dict:
    conf = (cfg.section("notify") if hasattr(cfg, "section") else (cfg or {}).get("notify")) or {}
    out = dict(MASK_DEFAULT)
    m = conf.get("mask")
    if isinstance(m, dict):
        for k, v in m.items():
            out[k] = v
    return out


def mask_party(name: str) -> str:
    """相对方名称：保留首尾各一字，中间掩码（长度 ≤2 时整体掩码）。"""
    n = (name or "").strip()
    if not n:
        return "-"
    if len(n) <= 2:
        return "*" * len(n)
    if len(n) <= 4:
        return n[0] + "*" * (len(n) - 1)
    return n[0] + "*" * (len(n) - 2) + n[-1]


def mask_person(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return "-"
    return n[0] + "*" * max(1, len(n) - 1)


def mask_credit(code: str) -> str:
    c = (code or "").strip()
    if not c:
        return "未载明"
    if len(c) <= 6:
        return "*" * len(c)
    return c[:2] + "*" * (len(c) - 4) + c[-2:]


def mask_amount_text(text: str) -> str:
    """金额文本掩码：只保留币种与量级，不保留具体数字。"""
    t = (text or "").strip()
    if not t:
        return "-"
    t = _RE_CN_AMOUNT.sub("人民币***", t)
    t = re.sub(r"(?P<cur>[¥￥])\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:万元|亿元|元))?",
               lambda m: "%s***" % m.group("cur"), t)
    t = re.sub(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", "***", t)
    return t


def mask_title(title: str) -> str:
    """合同标题常含相对方与标的，按「首 4 字 + 省略 + 类型尾缀」处理。"""
    t = (title or "").strip()
    if not t:
        return "合同"
    if len(t) <= 6:
        return t[0] + "*" * (len(t) - 1)
    return t[:4] + "…" + t[-2:]


def mask_filename(fn: str) -> str:
    """附件名：保留扩展名，主体掩码（附件名通常等于合同标题）。"""
    n = (fn or "").strip()
    if not n:
        return "-"
    stem, dot, ext = n.rpartition(".")
    if not dot:
        return mask_title(n)
    return mask_title(stem) + "." + ext


def sanitize(text: str, cfg=None, extra: dict | None = None) -> str:
    """对**整段通知正文**做兜底扫描。

    字段级掩码解决「我们知道那是哪个字段」的情况；这一层解决
    「敏感信息藏在自由文本里」的情况（附件名、解析错误、备注等），
    是最后一道闸门 —— 漏了一个字段级调用也不会导致明文外发。
    """
    m = dict(mask_cfg(cfg))
    if extra:
        m.update(extra)
    s = str(text or "")
    if m.get("credit_code", True):
        s = _RE_CREDIT.sub(lambda x: mask_credit(x.group(0)), s)
    if m.get("amount", True):
        s = _RE_CN_AMOUNT.sub("人民币***", s)
        s = re.sub(r"[¥￥]\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:万元|亿元|元))?",
                   lambda x: "%s***" % x.group(0)[0], s)
        s = re.sub(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", "***", s)
    s = _RE_MOBILE.sub(lambda x: x.group(0)[:3] + "****" + x.group(0)[-4:], s)
    s = _RE_EMAIL.sub(lambda x: x.group(0)[0] + "***@" + x.group(0).split("@")[-1], s)
    return s


def _opener():
    # 显式禁用代理：宿主环境的 http_proxy 会把出网请求劫持到代理端口
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def dingtalk_signed_url(webhook: str, secret: str, timestamp_ms: int | None = None) -> str:
    """按钉钉加签规则生成最终请求地址（secret 为空时原样返回）。"""
    if not secret:
        return webhook
    ts = str(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
    string_to_sign = "%s\n%s" % (ts, secret)
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                      digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    joiner = "&" if "?" in webhook else "?"
    return "%s%stimestamp=%s&sign=%s" % (webhook, joiner, ts, sign)


def mask_url(url: str) -> str:
    """脱敏展示：只保留服务端与参数名，隐藏 access_token。"""
    if not url:
        return ""
    return re.sub(r"(access_token=)[^&]+", r"\1***", url)


def _persist_notify(cfg):
    """把 notify 段写回 config/app.json（失败不抛，只回报）。"""
    path = os.path.join(cfg.root, "config", "app.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
        raw["notify"] = dict(cfg.data.get("notify") or {})
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True, ""
    except Exception as exc:                                     # noqa: BLE001
        return False, str(exc)


class Notifier:
    def __init__(self, cfg, store=None):
        self.cfg = cfg
        self.store = store
        self._q = queue.Queue(maxsize=500)
        self._lock = threading.RLock()
        self._worker = None
        self._last_sent = {}
        self._recent = deque(maxlen=60)
        self._stats = {"queued": 0, "sent": 0, "failed": 0, "skipped": 0}

    # ------------------------------------------------------------------ 状态
    def conf(self) -> dict:
        return self.cfg.notify if hasattr(self.cfg, "notify") else {}

    def creds(self):
        """解析 (webhook, secret)：**环境变量优先**。

        这样部署时可以把密钥放在 systemd 的 Environment（或 shell 环境）里，
        而不是明文写进随交付物一起分发的 config/app.json。
        """
        c = self.conf()
        webhook = (os.environ.get(c.get("webhook_env") or "DINGTALK_WEBHOOK") or "").strip() \
            or (c.get("webhook") or "").strip()
        secret = (os.environ.get(c.get("secret_env") or "DINGTALK_SECRET") or "").strip() \
            or (c.get("secret") or "").strip()
        return webhook, secret

    def source(self) -> str:
        """凭据来源，用于界面提示（不暴露值）。"""
        c = self.conf()
        if (os.environ.get(c.get("webhook_env") or "DINGTALK_WEBHOOK") or "").strip():
            return "env"
        return "config" if (c.get("webhook") or "").strip() else ""

    def configured(self) -> bool:
        webhook, _ = self.creds()
        return bool(self.conf().get("enabled")) and bool(webhook)

    def enabled(self, event: str) -> bool:
        if not self.configured():
            return False
        if event == "test":
            return True
        # 未在 events 中登记的事件默认**不发送**：通知宁可漏发也不要误发，
        # 想收哪类消息就在 events 里显式打开。
        return bool((self.conf().get("events") or {}).get(event, False))

    def status(self) -> dict:
        c = self.conf()
        webhook, secret = self.creds()
        with self._lock:
            return {
                "enabled": bool(c.get("enabled")),
                "configured": self.configured(),
                "channel": c.get("channel", ""),
                "webhook": mask_url(webhook),
                "webhook_source": self.source(),
                "webhook_env": c.get("webhook_env", ""),
                "secret_env": c.get("secret_env", ""),
                "has_secret": bool(secret),
                "keyword": c.get("keyword", ""),
                "events": c.get("events", {}),
                "min_interval_sec": c.get("min_interval_sec", 0),
                "mask": mask_cfg(self.cfg),
                "mask_note": "外发正文已按字段规则脱敏；合同原文片段不外发",
                "stats": dict(self._stats),
                "queue_size": self._q.qsize(),
                "recent": list(self._recent),
            }

    def set_events(self, events: dict, persist: bool = True):
        """演示用：运行时调整事件开关（写入内存，默认并落盘 config/app.json）。

        persist=False 供自检使用：只验证行为，不改交付配置。
        """
        conf = self.cfg.data.setdefault("notify", {})
        cur = dict(conf.get("events") or {})
        changed = []
        for k, v in (events or {}).items():
            if k in EVENTS and isinstance(v, bool):
                cur[k] = v
                changed.append("%s=%s" % (k, v))
        conf["events"] = cur
        ok, err = (True, "") if not persist else _persist_notify(self.cfg)
        self._audit(None, "events", "ok" if ok else "failed",
                    "事件开关更新：%s（落盘=%s）%s" % (
                        "、".join(changed) or "无变化",
                        "是" if persist else "否（临时）",
                        "" if ok else "（失败：%s）" % err))
        return {"ok": ok, "error": err, "persisted": bool(persist), "events": cur}

    def recent(self, limit: int = 20):
        with self._lock:
            return list(self._recent)[-limit:][::-1]

    # ------------------------------------------------------------------ 发送
    def _throttled(self, event: str) -> bool:
        gap = int(self.conf().get("min_interval_sec") or 0)
        if gap <= 0:
            return False
        now = time.time()
        last = self._last_sent.get(event, 0)
        if now - last < gap:
            return True
        self._last_sent[event] = now
        return False

    def notify(self, event: str, title: str, lines, case_id=None,
               at_mobiles=None, at_all=None):
        """把通知放入队列（非阻塞），返回是否已入队。"""
        if not self.enabled(event):
            with self._lock:
                self._stats["skipped"] += 1
            return {"queued": False, "reason": "未启用或未配置"}
        if self._throttled(event):
            with self._lock:
                self._stats["skipped"] += 1
                self._recent.append({
                    "event": event, "event_label": EVENT_LABEL.get(event, event),
                    "title": title, "status": "throttled",
                    "detail": "距上次发送不足 %ss" % self.conf().get("min_interval_sec"),
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
            self._audit(case_id, event, "skipped", "触发防抖，未发送")
            return {"queued": False, "reason": "throttled"}
        item = {"event": event, "title": title, "lines": list(lines),
                "case_id": case_id,
                "at_mobiles": at_mobiles if at_mobiles is not None
                else self.conf().get("at_mobiles") or [],
                "at_all": bool(at_all if at_all is not None
                               else self.conf().get("at_all"))}
        try:
            self._q.put_nowait(item)
            with self._lock:
                self._stats["queued"] += 1
        except queue.Full:
            self._audit(case_id, event, "failed", "通知队列已满，丢弃本次通知")
            return {"queued": False, "reason": "queue_full"}
        self._ensure_worker()
        return {"queued": True}

    def _ensure_worker(self):
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._loop, name="notify-worker",
                                            daemon=True)
            self._worker.start()

    def _loop(self):
        while True:
            item = self._q.get()
            try:
                self._deliver(item)
            except Exception as exc:                             # noqa: BLE001
                self._record(item, "failed", "发送线程异常：%s" % exc)
            finally:
                self._q.task_done()

    def send_now(self, event: str, title: str, lines, case_id=None,
                 at_mobiles=None, at_all=None):
        """同步发送（用于「发送测试通知」按钮，直接返回结果）。"""
        item = {"event": event, "title": title, "lines": list(lines),
                "case_id": case_id,
                "at_mobiles": at_mobiles if at_mobiles is not None
                else self.conf().get("at_mobiles") or [],
                "at_all": bool(at_all if at_all is not None
                               else self.conf().get("at_all"))}
        return self._deliver(item)

    def _deliver(self, item):
        c = self.conf()
        webhook, secret = self.creds()
        if not (c.get("enabled") and webhook):
            return self._record(item, "skipped", "通知未启用或未配置 Webhook")
        channel = c.get("channel") or "dingtalk"
        title, text = self._compose(item)
        if channel == "dingtalk":
            url = dingtalk_signed_url(webhook, secret)
            payload = {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": text},
                "at": {"atMobiles": item.get("at_mobiles") or [],
                       "isAtAll": bool(item.get("at_all"))},
            }
        else:                                                    # 通用 webhook
            url = c["webhook"]
            payload = {"title": title, "text": text, "event": item["event"]}

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        timeout = int(c.get("timeout") or 10)
        try:
            with _opener().open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            data = {}
            try:
                data = json.loads(raw) if raw else {}
            except Exception:                                    # noqa: BLE001
                data = {}
            code = data.get("errcode")
            if code is None and channel != "dingtalk":
                return self._record(item, "sent", "HTTP %s" % resp.status)
            if code == 0:
                return self._record(item, "sent", "钉钉返回 ok")
            return self._record(item, "failed",
                                "钉钉返回 errcode=%s errmsg=%s" % (code, data.get("errmsg", "")))
        except urllib.error.HTTPError as exc:
            return self._record(item, "failed", "HTTP %s：%s" % (exc.code, exc.reason))
        except Exception as exc:                                 # noqa: BLE001
            return self._record(item, "failed", "请求异常：%s" % exc)

    def _compose(self, item):
        from . import __version__
        keyword = (self.conf().get("keyword") or "").strip()
        label = EVENT_LABEL.get(item["event"], item["event"])
        prefix = "[%s] " % keyword if keyword else ""
        m = mask_cfg(self.cfg)
        title_raw = str(item.get("title") or "合同")
        title_show = mask_title(title_raw) if m.get("contract_title", True) else title_raw
        title = "%s%s · %s" % (prefix, label, title_show)
        lines = ["### %s" % title, ""]
        lines.extend(item["lines"])
        lines.append("")
        lines.append("> %s ｜ 衡鉴 V2.1.7 ｜ 运行版本 %s ｜ 已按字段规则脱敏"
                     % (time.strftime("%Y-%m-%d %H:%M:%S"), __version__))
        # 兜底扫描：字段级掩码之外，再对整段正文做一次规则扫描
        text = sanitize("\n".join(str(x) for x in lines), self.cfg)
        # 钉钉 markdown 单条上限 20000 字节，超出截断避免整条被拒
        raw = text.encode("utf-8")
        if len(raw) > 18000:
            text = raw[:18000].decode("utf-8", "ignore") + "\n\n> （内容过长已截断）"
        return title, text

    def _record(self, item, status, detail):
        entry = {
            "event": item["event"],
            "event_label": EVENT_LABEL.get(item["event"], item["event"]),
            "title": item["title"],
            "case_id": item.get("case_id"),
            "status": status,
            "detail": detail,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._lock:
            self._recent.append(entry)
            if status == "sent":
                self._stats["sent"] += 1
            elif status == "failed":
                self._stats["failed"] += 1
            else:
                self._stats["skipped"] += 1
        self._audit(item.get("case_id"), item["event"], status, detail)
        return entry

    def _audit(self, case_id, event, status, detail):
        if not self.store:
            return
        try:
            self.store.audit(case_id, "notify", "notify.%s" % event, status,
                             "[%s] %s" % (EVENT_LABEL.get(event, event), detail))
        except Exception:                                        # noqa: BLE001
            pass


# ---------------------------------------------------------------------- 事件封装
def notify_review_completed(notifier: Notifier, case: dict, hits: list, summary: dict):
    """审查完成：高风险单独用 high_risk 事件，其余用 review_completed。

    外发字段一律经掩码（D-31）：相对方名称、合同金额、统一社会信用代码不出明文；
    通知只承载「哪一类风险、什么等级、第几页」，不携带合同正文片段 ——
    正文片段留在系统内，要看到原文得进工作台。
    """
    if not notifier:
        return
    m = mask_cfg(notifier.cfg)
    high = [h for h in hits if h.get("risk_level") == "high"]
    stats = summary.get("stats") or {}

    def p(v):                        # 相对方
        if not v:
            return "-"
        return mask_party(v) if m.get("party", True) else v

    def amt(v):
        if not v:
            return "-"
        return mask_amount_text(v) if m.get("amount", True) else v

    lines = [
        "- 合同：**%s**（%s）" % (
            mask_title(case.get("title")) if m.get("contract_title", True)
            else case.get("title"),
            case.get("case_no") if not m.get("case_no") else mask_title(case.get("case_no"))),
        "- 业务类型：%s ｜ 申请人：%s" % (
            case.get("business_type_label") or "-",
            mask_person(case.get("applicant")) if m.get("applicant", True)
            else (case.get("applicant") or "-")),
        "- 相对方：%s" % p(case.get("counterparty")),
        "- 金额：%s" % amt(case.get("amount_text")),
        "- 综合风险：**%s** ｜ 结论：**%s**" % (case.get("risk_level_label"),
                                          case.get("conclusion_label")),
        "- 命中：高 %s / 中 %s / 低 %s" % (stats.get("high", 0), stats.get("medium", 0),
                                        stats.get("low", 0)),
        "- 文档：%s" % (mask_filename(case.get("document_summary") or "-")
                      if m.get("contract_title", True)
                      else (case.get("document_summary") or "-")),
    ]
    if high:
        lines.append("")
        lines.append("**高风险项**")
        for h in high[:5]:
            lines.append("- %s（%s，第 %s 页）" % (
                h.get("rule_name"), h.get("clause_type"),
                h.get("page_no") if h.get("page_no") is not None else "全文"))
        notifier.notify("high_risk", case.get("title") or "合同", lines,
                        case_id=case.get("id"), at_all=False)
    notifier.notify("review_completed", case.get("title") or "合同", lines,
                    case_id=case.get("id"))


def notify_blocked(notifier: Notifier, case: dict, reason: str):
    if not notifier:
        return
    m = mask_cfg(notifier.cfg)
    notifier.notify("blocked", case.get("title") or "合同", [
        "- 合同：**%s**（%s）" % (
            mask_title(case.get("title")) if m.get("contract_title", True)
            else case.get("title"),
            case.get("case_no") if not m.get("case_no") else mask_title(case.get("case_no"))),
        "- 文档：%s" % (mask_filename(case.get("document_summary") or "-")
                      if m.get("contract_title", True)
                      else (case.get("document_summary") or "-")),
        "- 来源：%s" % (case.get("source_label") or "-"),
        "- 受阻原因：%s" % sanitize(reason or "-", notifier.cfg),
        "",
        "请在审查大盘按「解析受阻」筛选并处理，修正文件后可一键重试。",
    ], case_id=case.get("id"))


def notify_writeback_failed(notifier: Notifier, case: dict, error: str):
    if not notifier:
        return
    m = mask_cfg(notifier.cfg)
    notifier.notify("writeback_failed", case.get("title") or "合同", [
        "- 合同：**%s**（%s）" % (
            mask_title(case.get("title")) if m.get("contract_title", True)
            else case.get("title"),
            case.get("case_no") if not m.get("case_no") else mask_title(case.get("case_no"))),
        "- 审批单号：%s" % (case.get("approval_instance") or "-"),
        "- 失败原因：%s" % sanitize(error or "-", notifier.cfg),
        "",
        "审查意见已生成但未写入审批评论区，可在工作台点击「写回审批单」重试。",
    ], case_id=case.get("id"), at_all=False)


def notify_batch_summary(notifier: Notifier, created: list, skipped: list,
                         reviewed: list):
    if not notifier or not created:
        return
    m = mask_cfg(notifier.cfg)
    highs = [r for r in reviewed if r.get("risk_level") == "high"]
    blocked = [r for r in reviewed if r.get("blocked")]
    lines = [
        "- 新建审查任务：**%d** 个" % len(created),
        "- 跳过（幂等）：%d 个" % len(skipped),
        "- 高风险：**%d** 个 ｜ 解析受阻：%d 个" % (len(highs), len(blocked)),
    ]
    if highs:
        lines.append("")
        lines.append("**高风险合同**")
        for r in highs[:6]:
            lines.append("- %s（%s）" % (
                mask_title(r.get("title")) if m.get("contract_title", True)
                else r.get("title"),
                r.get("case_no") or ""))
    notifier.notify("batch_summary", "本轮审批拉取结果", lines,
                    at_all=bool(highs))
