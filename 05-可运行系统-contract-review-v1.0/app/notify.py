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

    def configured(self) -> bool:
        c = self.conf()
        return bool(c.get("enabled")) and bool(c.get("webhook"))

    def enabled(self, event: str) -> bool:
        c = self.conf()
        if not (c.get("enabled") and c.get("webhook")):
            return False
        if event == "test":
            return True
        # 未在 events 中登记的事件默认**不发送**：通知宁可漏发也不要误发，
        # 想收哪类消息就在 events 里显式打开。
        return bool((c.get("events") or {}).get(event, False))

    def status(self) -> dict:
        c = self.conf()
        with self._lock:
            return {
                "enabled": bool(c.get("enabled")),
                "configured": self.configured(),
                "channel": c.get("channel", ""),
                "webhook": mask_url(c.get("webhook", "")),
                "has_secret": bool(c.get("secret")),
                "keyword": c.get("keyword", ""),
                "events": c.get("events", {}),
                "min_interval_sec": c.get("min_interval_sec", 0),
                "stats": dict(self._stats),
                "queue_size": self._q.qsize(),
                "recent": list(self._recent),
            }

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
        if not (c.get("enabled") and c.get("webhook")):
            return self._record(item, "skipped", "通知未启用或未配置 Webhook")
        channel = c.get("channel") or "dingtalk"
        title, text = self._compose(item)
        if channel == "dingtalk":
            url = dingtalk_signed_url(c["webhook"], c.get("secret") or "")
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
        keyword = (self.conf().get("keyword") or "").strip()
        label = EVENT_LABEL.get(item["event"], item["event"])
        prefix = "[%s] " % keyword if keyword else ""
        title = "%s%s · %s" % (prefix, label, item["title"])
        lines = ["### %s" % title, ""]
        lines.extend(item["lines"])
        lines.append("")
        lines.append("> %s ｜ 合同智能审查系统 v1.0" % time.strftime("%Y-%m-%d %H:%M:%S"))
        text = "\n".join(str(x) for x in lines)
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
    """审查完成：高风险单独用 high_risk 事件，其余用 review_completed。"""
    if not notifier:
        return
    high = [h for h in hits if h.get("risk_level") == "high"]
    stats = summary.get("stats") or {}
    lines = [
        "- 合同：**%s**（%s）" % (case.get("title"), case.get("case_no")),
        "- 业务类型：%s ｜ 申请人：%s" % (case.get("business_type_label") or "-",
                                    case.get("applicant") or "-"),
        "- 相对方：%s" % (case.get("counterparty") or "-"),
        "- 金额：%s" % (case.get("amount_text") or "-"),
        "- 综合风险：**%s** ｜ 结论：**%s**" % (case.get("risk_level_label"),
                                          case.get("conclusion_label")),
        "- 命中：高 %s / 中 %s / 低 %s" % (stats.get("high", 0), stats.get("medium", 0),
                                        stats.get("low", 0)),
        "- 文档：%s" % (case.get("document_summary") or "-"),
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
    notifier.notify("blocked", case.get("title") or "合同", [
        "- 合同：**%s**（%s）" % (case.get("title"), case.get("case_no")),
        "- 文档：%s" % (case.get("document_summary") or "-"),
        "- 来源：%s" % (case.get("source_label") or "-"),
        "- 受阻原因：%s" % (reason or "-"),
        "",
        "请在审查大盘按「解析受阻」筛选并处理，修正文件后可一键重试。",
    ], case_id=case.get("id"))


def notify_writeback_failed(notifier: Notifier, case: dict, error: str):
    if not notifier:
        return
    notifier.notify("writeback_failed", case.get("title") or "合同", [
        "- 合同：**%s**（%s）" % (case.get("title"), case.get("case_no")),
        "- 审批单号：%s" % (case.get("approval_instance") or "-"),
        "- 失败原因：%s" % (error or "-"),
        "",
        "审查意见已生成但未写入审批评论区，可在工作台点击「写回审批单」重试。",
    ], case_id=case.get("id"), at_all=False)


def notify_batch_summary(notifier: Notifier, created: list, skipped: list,
                         reviewed: list):
    if not notifier or not created:
        return
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
            lines.append("- %s（%s）" % (r.get("title"), r.get("case_no") or ""))
    notifier.notify("batch_summary", "本轮审批拉取结果", lines,
                    at_all=bool(highs))
