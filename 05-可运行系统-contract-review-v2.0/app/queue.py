# -*- coding: utf-8 -*-
"""审查队列与配额（主线 D / P1-04，零第三方依赖）

为什么要它：
  1.0 的审查是「请求里同步跑完」——单份没问题，10 份并发同时进来会一次性开 10 个
  解析任务（每个都可能是 OCR），内存与 CPU 直接打满，且没有排队概念。
  2.0 引入 `queue.Queue` + 固定工作线程池，把并发度钉死在配置上，多出来的排队等。

三条配额（全部服务端强制，前端无法绕过）：
  * 单文件：`review.max_upload_mb`（既有）
  * 队列长度：`queue.maxsize`，满了直接拒绝并写审计（不静默丢弃）
  * 待处理堆积：`queue.max_pending`，避免有人刷单把系统堵死
  * 存储总量：`queue.max_total_mb`，uploads 目录总量上限

兼容性：`submit(wait=True)` 走「入队 + 等结果」，HTTP 语义与 1.0 完全一致，
现有接口与自检不受影响；`wait=False` 用于并发演示，立即返回 task_id。
"""
from __future__ import annotations

import os
import queue
import threading
import time

_QUEUES = {}
_LOCK = threading.RLock()


class ReviewQueue:
    def __init__(self, cfg, st):
        self.cfg = cfg
        self.store = st
        q = self.conf()
        self.maxsize = max(1, int(q.get("maxsize") or 50))
        self.workers = max(1, min(16, int(q.get("workers") or 2)))
        self._q = queue.Queue(maxsize=self.maxsize)
        self._lock = threading.RLock()
        self._threads = []
        self._inflight = {}
        self._done = {}
        self._seq = 0
        self._stats = {"submitted": 0, "completed": 0, "failed": 0,
                       "rejected_queue_full": 0, "rejected_pending": 0, "inline": 0}
        self._started = False

    # ------------------------------------------------------------ 配置
    def conf(self) -> dict:
        return self.cfg.section("queue")

    def enabled(self) -> bool:
        return bool(self.conf().get("enabled", True))

    def max_pending(self) -> int:
        return max(1, int(self.conf().get("max_pending") or 40))

    def max_total_mb(self) -> int:
        return max(1, int(self.conf().get("max_total_mb") or 2048))

    # ------------------------------------------------------------ 工作线程
    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            for i in range(self.workers):
                t = threading.Thread(target=self._worker, name="review-worker-%d" % (i + 1),
                                     daemon=True)
                t.start()
                self._threads.append(t)

    def _worker(self):
        while True:
            task = self._q.get()
            if task is None:
                self._q.task_done()
                return
            cid, box, ev = task
            try:
                from . import pipeline
                res = pipeline.run_review(self.cfg, self.store, cid)
                box["result"] = res
                ok = bool(res.get("ok"))
            except Exception as exc:                             # noqa: BLE001
                box["result"] = {"ok": False, "error": "队列执行异常：%s" % exc}
                ok = False
            finally:
                with self._lock:
                    self._inflight.pop(cid, None)
                    self._done[cid] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                       "ok": bool((box.get("result") or {}).get("ok"))}
                    if len(self._done) > 200:                    # 只留最近 200 条
                        for old in list(self._done.keys())[:-200]:
                            self._done.pop(old, None)
                    self._stats["completed" if ok else "failed"] += 1
                if ev is not None:
                    ev.set()
                self._q.task_done()

    # ------------------------------------------------------------ 配额
    def quota_check(self):
        """返回 (ok, reason)。队列满 / 待处理超限 / 存储超限均拒绝。"""
        if self._q.qsize() >= self.maxsize:
            return False, "审查队列已满（%d/%d），请稍后重试" % (self._q.qsize(), self.maxsize)
        pend = self._pending_count()
        if pend >= self.max_pending():
            return False, "待处理任务已达上限（%d/%d），请先处理积压任务" % (pend, self.max_pending())
        total = self.uploads_bytes()
        cap = self.max_total_mb() * 1024 * 1024
        if total >= cap:
            return False, "附件存储已达上限（%.1f MB / %d MB），请清理后重试" % (
                total / 1048576.0, self.max_total_mb())
        return True, ""

    def _pending_count(self) -> int:
        from . import store as store_mod
        try:
            rows = self.store.list_cases(status=store_mod.S_PENDING)
            rows += self.store.list_cases(status=store_mod.S_PARSING)
            rows += self.store.list_cases(status=store_mod.S_REVIEWING)
            return len(rows)
        except Exception:                                        # noqa: BLE001
            return 0

    def uploads_bytes(self) -> int:
        d = self.cfg.path("uploads")
        total = 0
        try:
            for fn in os.listdir(d):
                fp = os.path.join(d, fn)
                if os.path.isfile(fp):
                    total += os.path.getsize(fp)
        except OSError:
            pass
        return total

    # ------------------------------------------------------------ 提交
    def submit(self, case_id: int, wait: bool = True, timeout: float = 180.0):
        ok, reason = self.quota_check()
        if not ok:
            with self._lock:
                self._stats["rejected_queue_full" if "队列" in reason
                            else "rejected_pending"] += 1
            self.store.audit(case_id, "queue", "queue.reject", "failed", reason)
            return {"ok": False, "error": reason, "queued": False}

        with self._lock:
            self._stats["submitted"] += 1
            self._seq += 1
            task_id = self._seq

        # 关闭队列时退回 1.0 的同步行为（排障用；默认开启）
        if not self.enabled() or wait is None:
            with self._lock:
                self._stats["inline"] += 1
            from . import pipeline
            return {"ok": True, "queued": False, "task_id": task_id,
                    "mode": "inline", "result": pipeline.run_review(self.cfg, self.store, case_id)}

        self.start()
        box, ev = {}, threading.Event()
        with self._lock:
            self._inflight[case_id] = {"task_id": task_id, "since": time.time()}
        try:
            self._q.put_nowait((case_id, box, ev))
        except queue.Full:
            with self._lock:
                self._inflight.pop(case_id, None)
                self._stats["rejected_queue_full"] += 1
            self.store.audit(case_id, "queue", "queue.reject", "failed",
                             "队列已满，任务未入队")
            return {"ok": False, "error": "审查队列已满，请稍后重试", "queued": False}

        if not wait:
            return {"ok": True, "queued": True, "task_id": task_id, "mode": "async"}

        got = ev.wait(timeout=timeout)
        if not got:
            self.store.audit(case_id, "queue", "queue.timeout", "failed",
                             "等待队列执行超时（%.0fs）" % timeout)
            return {"ok": False, "error": "队列执行超时", "queued": True,
                    "task_id": task_id, "mode": "async"}
        return {"ok": True, "queued": True, "task_id": task_id, "mode": "async",
                "result": box.get("result")}

    def stats(self) -> dict:
        with self._lock:
            s = dict(self._stats)
            inflight = [{"case_id": k, "task_id": v["task_id"],
                         "since_sec": round(time.time() - v["since"], 1)}
                        for k, v in self._inflight.items()]
        s.update({
            "enabled": self.enabled(),
            "workers": self.workers,
            "maxsize": self.maxsize,
            "queued": self._q.qsize(),
            "inflight": inflight,
            "pending_cases": self._pending_count(),
            "max_pending": self.max_pending(),
            "uploads_mb": round(self.uploads_bytes() / 1048576.0, 2),
            "max_total_mb": self.max_total_mb(),
        })
        return s


def get_queue(cfg, st=None):
    with _LOCK:
        key = id(cfg)
        q = _QUEUES.get(key)
        if q is None:
            q = ReviewQueue(cfg, st)
            _QUEUES[key] = q
        elif st is not None and getattr(q, "store", None) is None:
            q.store = st
        return q
