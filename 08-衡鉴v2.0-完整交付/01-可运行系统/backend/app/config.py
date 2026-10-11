# -*- coding: utf-8 -*-
"""衡鉴 · 全局配置

所有可调参数集中在此，支持 .env 覆盖。设计原则：
1. 零外部服务依赖 —— 默认 SQLite(WAL)，不需要 MySQL/Redis；
2. 演示零事故 —— LLM 未配置/超时/返回非法 JSON 时，自动降级到模板生成器；
3. 一切路径基于 BASE_DIR 推导，换机器不用改代码。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- 路径
BASE_DIR = Path(__file__).resolve().parent.parent          # -> hengjian/backend
PROJECT_ROOT = BASE_DIR.parent                             # -> hengjian/
FRONTEND_DIR = PROJECT_ROOT / "frontend"
RULES_DIR = Path(__file__).resolve().parent / "rules"
EVAL_DIR = Path(__file__).resolve().parent / "eval"
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
REPORT_DIR = DATA_DIR / "reports"
SAMPLES_DIR = BASE_DIR / "samples"


def _load_dotenv() -> None:
    """极简 .env 读取：只支持 KEY=VALUE，避免引入 python-dotenv 依赖。"""
    for candidate in (PROJECT_ROOT / ".env", BASE_DIR / ".env"):
        if not candidate.exists():
            continue
        try:
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
        except OSError:
            pass


_load_dotenv()


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------- 服务
HOST = os.environ.get("HJ_HOST", "127.0.0.1")
PORT = int(os.environ.get("HJ_PORT", "8130"))
ROLE = os.environ.get("HJ_ROLE", "full").strip().lower()   # full | demo
DEMO_MODE = ROLE == "demo"
DB_PATH = Path(os.environ.get("HJ_DB", str(DATA_DIR / "hengjian.db")))

# ---------------------------------------------------------------- LLM
# OpenAI 兼容协议；DeepSeek / 通义 / 本地 Ollama 均可切换
LLM_ENABLED = _flag("HJ_LLM_ENABLED", "1")
LLM_BASE_URL = os.environ.get("HJ_LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_API_KEY = os.environ.get("HJ_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("HJ_LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = float(os.environ.get("HJ_LLM_TIMEOUT", "8"))     # 秒，超时即降级
LLM_MAX_RETRY = int(os.environ.get("HJ_LLM_MAX_RETRY", "1"))
LLM_TEMPERATURE = float(os.environ.get("HJ_LLM_TEMPERATURE", "0"))
LLM_MAX_TOKENS = int(os.environ.get("HJ_LLM_MAX_TOKENS", "1600"))

# ---------------------------------------------------------------- 解析
OCR_ENABLED = _flag("HJ_OCR_ENABLED", "1")
OCR_MIN_CONFIDENCE = float(os.environ.get("HJ_OCR_MIN_CONFIDENCE", "0.60"))
OCR_BLOCK_THRESHOLD = float(os.environ.get("HJ_OCR_BLOCK_THRESHOLD", "0.35"))
MIN_TEXT_CHARS = int(os.environ.get("HJ_MIN_TEXT_CHARS", "120"))   # 低于此字数视为空文 -> blocked
MAX_UPLOAD_MB = int(os.environ.get("HJ_MAX_UPLOAD_MB", "30"))

# ---------------------------------------------------------------- 规则
RULE_FILE = os.environ.get("HJ_RULE_FILE", str(RULES_DIR / "rules_v1.yaml"))
TEMPLATE_FILE = os.environ.get("HJ_TEMPLATE_FILE", str(RULES_DIR / "clause_templates.yaml"))

# ---------------------------------------------------------------- 回写
WRITEBACK_DRY_RUN = _flag("HJ_WRITEBACK_DRY_RUN", "0")   # 1 = 全局预览模式，只落库不外发
APPROVAL_SIM_BASE = os.environ.get("HJ_APPROVAL_SIM_BASE", "")   # 留空 = 使用内置模拟器

# ---------------------------------------------------------------- 协作
TEAM_NAME = os.environ.get("HJ_TEAM", "第X组")
OWNER_NAME = os.environ.get("HJ_OWNER", "李奕波")
PROJECT_CODE = "衡鉴"
PROJECT_CODE_EN = "HengJian"
PROJECT_VERSION = "2.0.0"

for _d in (DATA_DIR, UPLOAD_DIR, REPORT_DIR, SAMPLES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 状态枚举
TASK_STATES = ["pending", "parsing", "reviewing", "blocked", "completed"]
WRITEBACK_STATES = ["not_written", "writing", "success", "failed"]
RISK_LEVELS = ["high", "medium", "low", "none"]
RISK_LABEL = {"high": "高风险", "medium": "中风险", "low": "低风险", "none": "无风险"}
RISK_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}

# 九类法定与业务条款（条款本体树一级节点）
CLAUSE_TYPES = [
    "subject",          # 签约主体与资质
    "subject_matter",   # 标的物
    "payment",          # 价款与支付
    "delivery",         # 交付与验收
    "liability",        # 违约责任
    "confidentiality",  # 保密义务
    "ip",               # 知识产权
    "data_security",    # 数据安全
    "dispute",          # 争议解决与管辖
    "validity",         # 合同效力（生效条件/期限）
    "force_majeure",    # 不可抗力
]
CLAUSE_LABEL = {
    "subject": "签约主体与资质",
    "subject_matter": "标的物",
    "payment": "价款与支付",
    "delivery": "交付与验收",
    "liability": "违约责任",
    "confidentiality": "保密义务",
    "ip": "知识产权",
    "data_security": "数据安全",
    "dispute": "争议解决与管辖",
    "validity": "合同效力",
    "force_majeure": "不可抗力",
    "other": "其他条款",
}
