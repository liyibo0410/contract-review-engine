# -*- coding: utf-8 -*-
"""全局配置加载与路径解析。

设计要点：
  * 所有运行期可调参数集中在此（阈值、路径、审批对接、通知、大模型），
    代码里不再硬编码魔法数字 —— 避免「配置写了但没人读」的漂移；
  * 配置项在 DEFAULT 中给出安全默认值，config/app.json 只需覆盖需要改的项；
  * 加载时做一次类型与取值校验，非法值回退默认并给出告警，避免带着坏配置启动。
"""
from __future__ import annotations

import json
import os
import sys

DEFAULT = {
    "server": {"host": "127.0.0.1", "port": 8770,
               "access_log": False, "gzip": True},
    "paths": {"data": "data", "uploads": "data/uploads", "parsed": "data/parsed",
              "reports": "data/reports", "samples": "samples"},
    # 审查判定与解析阈值（全部在代码中实际生效）
    "review": {
        "reject_high_count": 2,      # 高风险命中数 ≥ 该值 → 建议拒绝
        "rectify_total": 1,          # 命中总数 ≥ 该值 → 需整改
        "block_on_parse_fail": True,  # 解析失败是否直接转 blocked（关掉则按低风险继续）
        "min_text_chars": 30,        # 判定「有效文本」的最小字符数（PDF/DOCX 与 OCR 共用）
        "ocr_min_confidence": 0.5,   # OCR 置信度下限，低于则判定需人工复核
        "max_upload_mb": 30,         # 单文件上传上限（服务端强制）
        "max_paragraphs_expose": 2000,  # 单次详情最多返回的段落数，防止超大文档打爆前端
        # v2.0 新增：页眉页脚剔除（P1-02）
        "strip_repeat_lines": True,      # 是否启用跨页重复短行剔除
        "strip_min_pages": 2,            # 同一短行出现在 ≥ 该页数才判定为页眉/页脚
        "strip_max_chars": 46,           # 参与判定的最大行长（超长行不视为页眉页脚）
        "strip_edge_lines": 2,           # 只检查每页最前/最后 N 行
    },
    "approval": {
        "mode": "mock",              # mock=内置模拟审批系统；http=对接真实平台
        "base_url": "",              # 留空时由服务端按实际监听端口自动填充
        "app_id": "contract-review", # 写回评论时的来源系统标识
        "write_mode": "live",        # live=默认正式写入；dry_run=默认仅预览
        "timeout": 15,
    },
    "mock": {"fault_rate": 0.0, "seed": True},
    # v2.0 新增：角色与权限（需求 2.4.2）
    "auth": {
        "enabled": True,             # 关闭后按匿名管理员放行（仅排障用）
        "demo_switch": True,         # 演示身份一键切换（生产化阶段应关闭）
        "session_ttl_sec": 28800,    # 会话有效期（8 小时）
        "cookie_secure": False,      # HTTPS 部署时置 true
        "pbkdf2_iterations": 120000,
        "accounts": [],              # 留空则使用 auth.DEMO_ACCOUNTS 内置三账号
    },
    # v2.0 新增：定时拉取调度（P1-01）
    "scheduler": {
        "enabled": False,            # 默认关闭：避免演示数据自己变化
        "interval_sec": 300,
        "max_interval_sec": 900,
        "backoff_factor": 2.0,       # 连续失败后间隔倍率
        "jitter_sec": 5,             # 随机抖动，避免多实例同刻打点
        "auto_review": True,         # 拉到待办后是否自动执行审查
    },
    # v2.0 新增：审查队列与配额（P1-04）
    "queue": {
        "enabled": True,
        "workers": 2,
        "maxsize": 50,
        "max_pending": 40,
        "max_total_mb": 2048,
    },
    "notify": {
        "enabled": False,
        "channel": "dingtalk",       # dingtalk / webhook（通用 POST）
        "webhook": "",               # 钉钉自定义机器人 Webhook 地址
        "secret": "",                # 钉钉「加签」密钥（以 SEC 开头）
        "webhook_env": "DINGTALK_WEBHOOK",   # 环境变量名（优先级高于上面的直填值）
        "secret_env": "DINGTALK_SECRET",     # 便于把密钥留在部署环境而不进配置文件
        "keyword": "",               # 可选：安全设置里的自定义关键词，会前置到标题
        "at_mobiles": [],            # @ 手机号
        "at_all": False,
        "timeout": 10,
        "events": {                  # 事件开关（未登记的事件默认不发送）
            # D-31 默认收敛：高频/低信息量的两类默认关闭，演示或需要时再开
            "review_completed": False,
            "high_risk": True,
            "blocked": True,
            "writeback_failed": True,
            "batch_summary": False,
        },
        "min_interval_sec": 0,       # 同一事件类型的最短发送间隔（防抖，0=不限）
        # D-31 外发脱敏：合同属最敏感数据，标题/相对方/金额/信用代码一律不出明文
        "mask": {
            "party": True,           # 相对方名称掩码（保留首尾各 1-2 字）
            "amount": True,          # 合同金额掩码
            "credit_code": True,     # 统一社会信用代码掩码
            "case_no": False,        # 合同编号是否掩码（默认保留，便于对号）
            "applicant": True,       # 申请人姓名掩码
            "contract_title": True,  # 合同标题掩码（标题常含相对方信息）
            "max_evidence_chars": 0, # 通知正文允许携带的合同正文片段长度（0=不允许）
        },
    },
    "llm": {"enabled": False, "base_url": "", "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini", "timeout": 30},
}

# (路径, 期望类型, 取值校验) —— 加载时逐项校验，非法则回退默认值
_SCHEMA = [
    ("server.host", str, None), ("server.port", int, lambda v: 1 <= v <= 65535),
    ("server.access_log", bool, None), ("server.gzip", bool, None),
    ("review.reject_high_count", int, lambda v: v >= 0),
    ("review.rectify_total", int, lambda v: v >= 0),
    ("review.block_on_parse_fail", bool, None),
    ("review.min_text_chars", int, lambda v: v >= 1),
    ("review.ocr_min_confidence", (int, float), lambda v: 0.0 <= v <= 1.0),
    ("review.max_upload_mb", int, lambda v: 1 <= v <= 2048),
    ("review.max_paragraphs_expose", int, lambda v: v >= 20),
    ("review.strip_repeat_lines", bool, None),
    ("review.strip_min_pages", int, lambda v: 1 <= v <= 50),
    ("review.strip_max_chars", int, lambda v: 4 <= v <= 500),
    ("review.strip_edge_lines", int, lambda v: 1 <= v <= 10),
    ("approval.mode", str, lambda v: v in ("mock", "http")),
    ("approval.base_url", str, None),
    ("approval.app_id", str, None),
    ("approval.write_mode", str, lambda v: v in ("live", "dry_run")),
    ("approval.timeout", int, lambda v: 1 <= v <= 300),
    ("mock.fault_rate", (int, float), lambda v: 0.0 <= v <= 1.0),
    ("mock.seed", bool, None),
    ("auth.enabled", bool, None),
    ("auth.demo_switch", bool, None),
    ("auth.session_ttl_sec", int, lambda v: 60 <= v <= 2592000),
    ("auth.cookie_secure", bool, None),
    ("auth.pbkdf2_iterations", int, lambda v: 10000 <= v <= 2000000),
    ("scheduler.enabled", bool, None),
    ("scheduler.interval_sec", int, lambda v: 10 <= v <= 86400),
    ("scheduler.max_interval_sec", int, lambda v: 10 <= v <= 604800),
    ("scheduler.backoff_factor", (int, float), lambda v: 1.0 <= v <= 6.0),
    ("scheduler.jitter_sec", int, lambda v: 0 <= v <= 600),
    ("scheduler.auto_review", bool, None),
    ("queue.enabled", bool, None),
    ("queue.workers", int, lambda v: 1 <= v <= 16),
    ("queue.maxsize", int, lambda v: 1 <= v <= 1000),
    ("queue.max_pending", int, lambda v: 1 <= v <= 1000),
    ("queue.max_total_mb", int, lambda v: 10 <= v <= 102400),
    ("notify.enabled", bool, None),
    ("notify.channel", str, lambda v: v in ("dingtalk", "webhook")),
    ("notify.webhook", str, None),
    ("notify.secret", str, None),
    ("notify.webhook_env", str, None),
    ("notify.secret_env", str, None),
    ("notify.timeout", int, lambda v: 1 <= v <= 120),
    ("notify.min_interval_sec", int, lambda v: v >= 0),
    ("llm.enabled", bool, None),
    ("llm.timeout", int, lambda v: 1 <= v <= 300),
]


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _pick(data, path):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _put(data, path, value):
    parts = path.split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


class Config:
    """配置对象。支持 cfg["server"]、cfg.get("notify")、cfg.path("uploads")。"""

    def __init__(self, root: str, warn=None):
        self.root = os.path.abspath(root)
        self.warnings = []
        path = os.path.join(self.root, "config", "app.json")
        raw = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f) or {}
            except Exception as exc:                             # noqa: BLE001
                self.warnings.append("config/app.json 解析失败，已使用默认配置：%s" % exc)
        self.data = _deep_merge(DEFAULT, raw)
        self._validate()
        if warn:
            for w in self.warnings:
                warn("[config] " + w)

    def _validate(self):
        for path, typ, check in _SCHEMA:
            val, ok = _pick(self.data, path)
            if not ok:
                continue
            bad = not isinstance(val, typ) or isinstance(val, bool) != (typ is bool)
            if not bad and check and not isinstance(val, bool):
                try:
                    bad = not check(val)
                except Exception:                                # noqa: BLE001
                    bad = True
            if bad:
                default, _ = _pick(DEFAULT, path)
                self.warnings.append("%s 取值非法（%r），已回退默认值 %r"
                                     % (path, val, default))
                _put(self.data, path, default)

    # ---- 便捷访问
    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    def section(self, key: str) -> dict:
        v = self.data.get(key)
        return v if isinstance(v, dict) else {}

    @property
    def review(self) -> dict:
        return self.section("review")

    @property
    def notify(self) -> dict:
        return self.section("notify")

    @property
    def approval(self) -> dict:
        return self.section("approval")

    @property
    def auth(self) -> dict:
        return self.section("auth")

    @property
    def scheduler(self) -> dict:
        return self.section("scheduler")

    @property
    def queue(self) -> dict:
        return self.section("queue")

    def path(self, key: str) -> str:
        """返回绝对路径，并确保目录存在。"""
        rel = self.section("paths").get(key) or key
        p = rel if os.path.isabs(rel) else os.path.join(self.root, rel)
        os.makedirs(p, exist_ok=True)
        return p

    def file(self, key: str) -> str:
        rel = self.section("paths").get(key) or key
        return rel if os.path.isabs(rel) else os.path.join(self.root, rel)

    def db_path(self) -> str:
        return os.path.join(self.path("data"), "review.db")

    def public(self) -> dict:
        """可安全暴露到前端的配置（不含密钥）。"""
        return {
            "max_upload_mb": self.review.get("max_upload_mb", 30),
            "write_mode": self.approval.get("write_mode", "live"),
            "notify_enabled": bool(self.notify.get("enabled")),
            "notify_channel": self.notify.get("channel", ""),
            # Webhook 可能来自环境变量（部署时凭据不落配置文件），此处同样环境变量优先
            "notify_configured": bool(
                (os.environ.get(self.notify.get("webhook_env") or "DINGTALK_WEBHOOK") or "").strip()
                or (self.notify.get("webhook") or "").strip()),
            "notify_events": self.notify.get("events", {}),
            "llm_enabled": bool(self.section("llm").get("enabled")),
        }


def warn_print(msg):
    sys.stderr.write(msg + "\n")
