# -*- coding: utf-8 -*-
"""定时拉取调度（主线 C / P1-01，零第三方依赖）

职责：让系统自己按间隔去审批平台拉待办并触发审查，而不是全靠人点按钮。

设计要点：
  * **默认关闭**：P1-01 的风险预案明确要求「自动拉取默认关闭」，否则演示数据
    会在无人操作时自己变化。演示时按需开启并能当场看到开关效果。
  * **指数退避**：连续失败后间隔按 backoff 倍数递增，封顶 max_interval_sec；
    成功一次即复位 —— 避免审批平台故障时把对端打挂。
  * **单线程守护**：只用标准库 threading + Event，不引入调度框架；线程随进程退出。
  * **可观测**：暴露 runs / failures / created / last_error / next_run_at_in 等字段，
    大盘直接显示「上一轮 / 下一轮 / 累计拉取」。
"""
from __future__ import annotations

import random
import threading
import time

_SCHED = {}
_LOCK = threading.RLock()


class Scheduler:
    def __init__(self, cfg, st):
        self.cfg = cfg
        self.store = st
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread = None
        self._stop = False
        self._running = False            # 本轮是否在执行（防止重入）
        self._next_at = 0.0
        self._state = {
            "runs": 0, "failures": 0, "consecutive_failures": 0,
            "created_total": 0, "skipped_total": 0, "reviewed_total": 0,
            "last_run_at": "", "last_result": "", "last_error": "",
            "current_interval": 0, "manual_runs": 0,
        }

    # ------------------------------------------------------------ 配置
    def conf(self) -> dict:
        return self.cfg.section("scheduler")

    def enabled(self) -> bool:
        return bool(self.conf().get("enabled", False))

    def base_interval(self) -> int:
        return max(10, self._num("interval_sec", 300))

    def max_interval(self) -> int:
        return max(self.base_interval(), self._num("max_interval_sec", 900))

    def backoff(self) -> float:
        try:
            v = float(self._num("backoff_factor", 2))
        except (TypeError, ValueError):
            v = 2.0
        return min(6.0, max(1.0, v))

    def jitter(self) -> int:
        return max(0, self._num("jitter_sec", 5))

    def _num(self, key, default):
        """读取数值配置。

        注意不能用 `conf.get(key) or default` —— **0 是合法取值**（jitter_sec=0
        表示关闭抖动），用 `or` 会把它当假值回退成默认值，导致「配置写了但没生效」。
        """
        v = self.conf().get(key)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------ 生命周期
    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop = False
            self._thread = threading.Thread(target=self._loop, name="pull-scheduler",
                                            daemon=True)
            self._thread.start()
        self.store.audit(None, "scheduler", "scheduler.start", "ok",
                         "定时拉取调度线程已启动（enabled=%s）" % self.enabled())
        return True

    def stop(self, reason: str = "手动停止"):
        with self._lock:
            self._stop = True
            self._wake.set()
        self.store.audit(None, "scheduler", "scheduler.stop", "ok", reason)
        return True

    # ------------------------------------------------------------ 主循环
    def _loop(self):
        # 启动后先等一个基础间隔，避免服务刚起来就抢资源
        self._next_at = time.time() + self.base_interval()
        while not self._stop:
            wait = max(0.2, self._next_at - time.time())
            self._wake.wait(timeout=wait)
            self._wake.clear()
            if self._stop:
                break
            if not self.enabled():
                self._next_at = time.time() + self.base_interval()
                continue
            if time.time() < self._next_at:
                continue
            self.run_once(trigger="timer")

    # ------------------------------------------------------------ 执行
    def run_once(self, trigger: str = "manual"):
        with self._lock:
            if self._running:
                return {"ok": False, "error": "上一轮拉取尚未结束，本次跳过"}
            self._running = True
        t0 = time.time()
        created = skipped = reviewed = 0
        err = ""
        try:
            from . import pipeline
            auto = bool(self.conf().get("auto_review", True))
            out = pipeline.pull_from_approval(self.cfg, self.store, auto_review=auto)
            if not out.get("ok"):
                err = out.get("error") or "拉取失败"
            created = len(out.get("created") or [])
            skipped = len(out.get("skipped") or [])
            reviewed = len(out.get("reviewed") or [])
        except Exception as exc:                                 # noqa: BLE001
            err = "调度执行异常：%s" % exc
        finally:
            with self._lock:
                self._running = False

        cost = int((time.time() - t0) * 1000)
        with self._lock:
            self._state["runs"] += 1
            if trigger == "manual":
                self._state["manual_runs"] += 1
            self._state["created_total"] += created
            self._state["skipped_total"] += skipped
            self._state["reviewed_total"] += reviewed
            self._state["last_run_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if err:
                self._state["failures"] += 1
                self._state["consecutive_failures"] += 1
                self._state["last_error"] = err
                self._state["last_result"] = "失败"
            else:
                self._state["consecutive_failures"] = 0
                self._state["last_error"] = ""
                self._state["last_result"] = "新建 %d / 跳过 %d / 审查 %d" % (
                    created, skipped, reviewed)
            self._state["current_interval"] = self._compute_interval()
            self._next_at = time.time() + self._state["current_interval"]

        self.store.audit(None, "scheduler", "scheduler.run",
                         "failed" if err else "ok",
                         "触发方式=%s 耗时=%dms 新建=%d 跳过=%d 审查=%d%s"
                         % (trigger, cost, created, skipped, reviewed,
                            (" 错误=%s" % err) if err else ""))
        return {"ok": not err, "created": created, "skipped": skipped,
                "reviewed": reviewed, "error": err, "ms": cost,
                "trigger": trigger}

    def _compute_interval(self) -> int:
        back = self.backoff()
        fails = self._state.get("consecutive_failures") or 0
        base = self.base_interval()
        if fails <= 0:
            iv = base
        else:
            iv = min(self.max_interval(), int(base * (back ** fails)))
        j = self.jitter()
        return int(iv + (random.randint(0, j) if j else 0))

    # ------------------------------------------------------------ 控制 / 状态
    def trigger(self):
        """手动立即拉取一次（不影响既定节奏，仅把下一轮回拨到下一间隔）。"""
        out = self.run_once(trigger="manual")
        self._wake.set()
        return out

    def status(self) -> dict:
        with self._lock:
            st = dict(self._state)
            nxt = max(0, int(self._next_at - time.time())) if self._next_at else 0
        st.update({
            "enabled": self.enabled(),
            "running": self._running,
            "alive": bool(self._thread and self._thread.is_alive()),
            "interval_sec": self.base_interval(),
            "max_interval_sec": self.max_interval(),
            "backoff_factor": self.backoff(),
            "auto_review": bool(self.conf().get("auto_review", True)),
            "next_run_in_sec": nxt,
            "next_run_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime(self._next_at)) if self._next_at else "",
        })
        return st

    def set_config(self, persist: bool = True, **kw):
        """运行时改开关（大盘按钮即时生效，并持久化到 config/app.json）。

        persist=False 供自检/巡检使用 —— 那类调用只应验证行为，绝不能把
        交付配置改掉（自检污染交付物是审计上的硬伤）。
        """
        sched = self.cfg.data.setdefault("scheduler", {})
        for k in ("enabled", "interval_sec", "max_interval_sec", "backoff_factor",
                  "jitter_sec", "auto_review"):
            if k in kw and kw[k] is not None:
                sched[k] = kw[k]
        saved, err = (True, "") if not persist else _persist(self.cfg)
        with self._lock:
            self._next_at = time.time() + self.base_interval()
        self.store.audit(None, "scheduler", "scheduler.config", "ok" if saved else "failed",
                         "调度参数更新：enabled=%s interval=%ss jitter=%ss 落盘=%s%s"
                         % (self.enabled(), self.base_interval(), self.jitter(),
                            "是" if persist else "否（临时）",
                            "" if saved else "（失败：%s）" % err))
        self._wake.set()
        return {"ok": saved, "error": err, "persisted": bool(persist),
                "status": self.status()}


def _persist(cfg):
    """把运行期改动写回 config/app.json（失败不抛，只回报）。"""
    import json
    import os
    path = os.path.join(cfg.root, "config", "app.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
        raw["scheduler"] = dict(cfg.data.get("scheduler") or {})
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True, ""
    except Exception as exc:                                     # noqa: BLE001
        return False, str(exc)


def get_scheduler(cfg, st=None):
    """按配置对象缓存单例（与 Notifier 同构）。"""
    with _LOCK:
        key = id(cfg)
        s = _SCHED.get(key)
        if s is None:
            s = Scheduler(cfg, st)
            _SCHED[key] = s
        elif st is not None and getattr(s, "store", None) is None:
            s.store = st
        return s
