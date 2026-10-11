# -*- coding: utf-8 -*-
"""角色与权限（RBAC）—— 需求 2.4.2 三角色闭环，零第三方依赖。

设计要点：
  * **口令不落明文**：pbkdf2_hmac('sha256') + 每账号独立随机盐，迭代次数可配置；
    配置文件里只在「演示种子」段落出现初始口令，首次启动即写库哈希，之后不再回写。
  * **会话用服务端令牌**：Cookie 只带随机 token，角色与会话内容全在库里 —— 改前端
    伪造 Cookie 无法提权；token 过期即失效，登出即删除。
  * **权限是「能力（capability）」而非「角色」**：路由 → 能力 → 角色集合三级映射，
    新增接口只要登记能力即可，不会出现「忘了给某角色放行」的静默漏洞。
  * **失败关闭（fail-closed）**：未登记的非 GET 接口一律按管理员专属处理；
    未登录访问页面跳登录页、访问接口返回 401。
  * **越权留痕**：任何 401/403 都写审计（tool=auth.denied），可在大盘审计日志复查。

三角色边界（严格对齐 2.4.2）：
    法务审查人 legal     —— 查看详情、核对高亮、采纳/调整建议、补充法务意见、触发回写
    业务经办人 applicant —— 提交审查申请（上传/拉取）、查看风险点与整改建议、下载报告
    系统管理员 admin     —— 配置对接参数、维护规则与标准条款库、监控任务状态、处理阻塞重试
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time

ROLE_LEGAL = "legal"
ROLE_APPLICANT = "applicant"
ROLE_ADMIN = "admin"

ROLE_LABEL = {
    ROLE_LEGAL: "法务审查人",
    ROLE_APPLICANT: "业务经办人",
    ROLE_ADMIN: "系统管理员",
}
ROLE_ORDER = (ROLE_LEGAL, ROLE_APPLICANT, ROLE_ADMIN)
ROLE_DESC = {
    ROLE_LEGAL: "查看合同审查详情、核对原文高亮证据、采纳或调整修改建议、补充法务意见并触发审批回写",
    ROLE_APPLICANT: "提交合同审查申请、查看合同风险点与整改建议、下载审查报告",
    ROLE_ADMIN: "配置审批流对接参数、维护合规审查规则与标准条款库、监控审查任务状态并处理阻塞重试",
}

# ------------------------------------------------------------------ 能力定义
CAPS = {
    "view_cases":      {ROLE_LEGAL, ROLE_APPLICANT, ROLE_ADMIN},
    "view_detail":     {ROLE_LEGAL, ROLE_APPLICANT, ROLE_ADMIN},
    "export_report":   {ROLE_LEGAL, ROLE_APPLICANT, ROLE_ADMIN},
    "submit_contract": {ROLE_APPLICANT, ROLE_ADMIN},
    "pull_todo":       {ROLE_APPLICANT, ROLE_ADMIN},
    "edit_suggestion": {ROLE_LEGAL},
    "add_opinion":     {ROLE_LEGAL},
    "writeback":       {ROLE_LEGAL},
    "rerun_review":    {ROLE_LEGAL, ROLE_ADMIN},
    "retry_blocked":   {ROLE_ADMIN},
    "edit_rules":      {ROLE_ADMIN},
    "export_audit":    {ROLE_LEGAL, ROLE_ADMIN},
    "notify_test":     {ROLE_ADMIN},
    "fault_inject":    {ROLE_ADMIN},
    "reset_data":      {ROLE_ADMIN},
    "run_selftest":    {ROLE_ADMIN},
    "view_metrics":    {ROLE_ADMIN},
    "view_users":      {ROLE_ADMIN},
    "manage_scheduler": {ROLE_ADMIN},
    "baseline":        {ROLE_ADMIN},
}

CAP_LABEL = {
    "view_cases": "查看审查任务列表", "view_detail": "查看审查详情",
    "export_report": "下载审查报告", "submit_contract": "提交合同审查申请",
    "pull_todo": "拉取审批待办", "edit_suggestion": "采纳/调整修改建议",
    "add_opinion": "补充法务批注", "writeback": "回写审批意见",
    "rerun_review": "重新执行审查", "retry_blocked": "阻塞任务重试",
    "edit_rules": "维护合规规则与标准条款库", "export_audit": "导出审计数据",
    "notify_test": "发送通知连通性测试", "fault_inject": "故障注入",
    "reset_data": "重置演示数据", "run_selftest": "执行系统自检",
    "view_metrics": "查看运行指标", "view_users": "查看账号与会话",
    "manage_scheduler": "管理定时拉取调度",
    "baseline": "维护演示基线",
}

# ------------------------------------------------------------------ 路由 → 能力
# 顺序敏感：先匹配到的生效，因此把更长/更具体的路径写在前面。
ROUTES = (
    # 认证自身（登录后可自助查询与登出/切换演示身份）
    (r"^/api/auth/me$", {"GET"}, "auth"),
    (r"^/api/auth/logout$", {"POST"}, "auth"),
    (r"^/api/auth/switch$", {"POST"}, "auth"),
    (r"^/api/auth/accounts$", {"GET"}, "view_users"),
    (r"^/api/cases$", {"GET"}, "view_cases"),
    (r"^/api/cases/\d+/report$", {"GET"}, "export_report"),
    (r"^/api/cases/\d+/opinion$", {"POST"}, "add_opinion"),
    (r"^/api/cases/\d+/writeback$", {"POST"}, "writeback"),
    (r"^/api/cases/\d+/review$", {"POST"}, "rerun_review"),
    (r"^/api/cases/\d+/retry$", {"POST"}, "retry_blocked"),
    (r"^/api/cases/\d+$", {"GET"}, "view_detail"),
    (r"^/api/results/\d+$", {"POST", "PATCH"}, "edit_suggestion"),
    (r"^/api/upload$", {"POST"}, "submit_contract"),
    (r"^/api/pull$", {"POST"}, "pull_todo"),
    (r"^/api/rules/[a-z_]+$", {"POST", "PUT"}, "edit_rules"),
    (r"^/api/export$", {"GET", "POST"}, "export_audit"),
    (r"^/api/notify/test$", {"POST"}, "notify_test"),
    (r"^/api/notify/events$", {"POST"}, "notify_test"),
    (r"^/api/queue$", {"GET"}, "auth"),
    (r"^/api/twin$", {"GET"}, "auth"),
    # AI 助手（页面宠物）：只读，不改变任何业务状态，所以归在「已登录即可」这一档。
    # 但它能读到真实任务数、风险与规则，所以**不能匿名** —— 未登录一律 401。
    (r"^/api/assistant/ask$", {"POST"}, "auth"),
    (r"^/api/assistant/tour$", {"GET"}, "auth"),
    (r"^/api/assistant/kb$", {"GET"}, "auth"),
    # AI 助手（页面宠物）：只读，不改变任何业务状态，所以归在「已登录即可」这一档。
    # 但它能读到真实任务数、风险与规则，所以**不能匿名** —— 未登录一律 401。
    (r"^/api/assistant/ask$", {"POST"}, "auth"),
    (r"^/api/assistant/tour$", {"GET"}, "auth"),
    (r"^/api/assistant/kb$", {"GET"}, "auth"),
    (r"^/api/mock/overview$", {"GET"}, "auth"),
    (r"^/api/mock/fault$", {"POST"}, "fault_inject"),
    (r"^/api/mock/reset$", {"POST"}, "reset_data"),
    (r"^/api/selftest$", {"GET", "POST"}, "run_selftest"),
    (r"^/api/metrics$", {"GET"}, "view_metrics"),
    (r"^/api/scheduler/.+$", {"POST", "PUT"}, "manage_scheduler"),
    (r"^/api/baseline/.+$", {"POST", "PUT"}, "baseline"),
    # 登录即可访问的只读接口
    (r"^/api/config$", {"GET"}, "auth"),
    (r"^/api/stats$", {"GET"}, "auth"),
    (r"^/api/samples$", {"GET"}, "auth"),
    (r"^/api/rules$", {"GET"}, "auth"),
    (r"^/api/audit$", {"GET"}, "auth"),
    (r"^/api/notify/status$", {"GET"}, "auth"),
    (r"^/api/notify/recent$", {"GET"}, "auth"),
    (r"^/api/mock/comments$", {"GET"}, "auth"),
    (r"^/api/scheduler$", {"GET"}, "auth"),
    (r"^/api/baseline$", {"GET"}, "auth"),
    (r"^/api/pdf/check$", {"GET"}, "auth"),
)

# 页面 → 能力（"auth" 表示登录即可）
PAGES = {
    "/": "auth",
    "/workbench": "auth",
    "/report": "auth",
    "/twin": "auth",
    "/rules": "edit_rules",       # 需求 2.4.3：规则与模板页归系统管理员
    "/mock": "auth",              # 模拟审批的美化查看页（内部页，需登录）
}

PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/health"}
# /mock/ 是「模拟审批平台」的对外接口，按真实平台形态独立暴露，
# 不参与本系统的登录态（系统内部也通过 HTTP 自调用它）。
# 只放行「模拟审批系统」的对外 JSON 接口本身；/mock 是内部查看页，需要登录
PUBLIC_PREFIX = ("/assets/", "/favicon", "/logo", "/mock/feishu/api")

COOKIE_NAME = "cr_session"
# 会话令牌长度（token_urlsafe 的字节数，输出约 1.33 倍长度）
TOKEN_BYTES = 32


# ------------------------------------------------------------------ 口令哈希
def hash_password(password: str, iterations: int = 120000) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                             salt.encode("utf-8"), int(iterations))
    return "pbkdf2$%d$%s$%s" % (int(iterations), salt, dk.hex())


def verify_password(password: str, stored: str) -> bool:
    """常量时间比较，避免按字符逐位试探。"""
    try:
        algo, iters, salt, hexhash = (stored or "").split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                                 salt.encode("utf-8"), int(iters))
        return hmac.compare_digest(dk.hex(), hexhash)
    except (ValueError, AttributeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


# ------------------------------------------------------------------ 权限查询
def caps_of(role: str) -> list:
    return sorted([c for c, roles in CAPS.items() if role in roles])


def has_cap(role: str, cap: str) -> bool:
    if cap in (None, "", "auth"):
        return bool(role)
    return role in CAPS.get(cap, set())


def resolve(method: str, path: str):
    """返回该请求所需的能力。

    返回值语义：
      None      —— 公开路径，无需登录
      "auth"    —— 登录即可
      <cap>     —— 需要具体能力
      "deny"    —— 未登记的非只读接口：失败关闭，仅管理员可用
    """
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIX):
        return None
    method = (method or "GET").upper()
    for pattern, methods, cap in ROUTES:
        if method in methods and re.match(pattern, path):
            return cap
    if path.startswith("/api/"):
        # 未登记的写操作一律按管理员专属处理（新增接口忘记登记时只会更严，不会更松）
        return "auth" if method == "GET" else "deny"
    # 页面/静态
    if path in PAGES:
        return PAGES[path]
    return "auth"


def page_allowed(role: str, path: str) -> bool:
    need = PAGES.get(path)
    if need is None:
        return True
    return has_cap(role, need)


# ------------------------------------------------------------------ 账号种子
DEMO_ACCOUNTS = (
    # (用户名, 显示名, 角色, 初始口令)
    ("legal", "法务·李律", ROLE_LEGAL, "Legal@2026"),
    ("applicant", "业务·王经办", ROLE_APPLICANT, "Apply@2026"),
    ("admin", "系统·赵管理", ROLE_ADMIN, "Admin@2026"),
)


def seed_users(cfg, st, warn=None):
    """把演示账号写入库（幂等：已存在的账号不覆盖密码）。"""
    auth_cfg = (cfg.get("auth") or {})
    accounts = auth_cfg.get("accounts") or []
    if not accounts:
        accounts = [{"username": u, "display": d, "role": r, "password": p}
                    for u, d, r, p in DEMO_ACCOUNTS]
    created = []
    for a in accounts:
        u = (a.get("username") or "").strip()
        role = (a.get("role") or "").strip()
        if not u or role not in ROLE_LABEL:
            if warn:
                warn("[auth] 跳过非法演示账号定义：%r" % (a,))
            continue
        made = st.user_upsert(u, a.get("display") or u, role,
                              hash_password(a.get("password") or "ChangeMe@2026"),
                              reset=bool(a.get("reset_password")))
        if made:
            created.append(u)
    if created and warn:
        warn("[auth] 已创建演示账号：%s" % "、".join(created))
    st.audit(None, "auth", "auth.seed_users", "ok",
             "账号种子完成：新建 %d 个 / 共 %d 个" % (len(created), len(st.user_list())))
    return created


# ------------------------------------------------------------------ 会话
def login(cfg, st, username: str, password: str, client: str = ""):
    """校验口令并落会话，返回 (ok, payload)。"""
    auth_cfg = (cfg.get("auth") or {})
    if not bool(auth_cfg.get("enabled", True)):
        # 关闭鉴权时按管理员会话放行（用于排障；交付默认开启）
        tok = new_token()
        st.session_create(tok, "anonymous", ROLE_ADMIN, 3600, client)
        return True, {"ok": True, "token": tok, "role": ROLE_ADMIN,
                      "username": "anonymous", "auth_disabled": True}
    row = st.user_get((username or "").strip())
    if not row or not row.get("enabled"):
        st.audit(None, "auth", "auth.login", "failed",
                 "登录失败：账号 %s 不存在或已停用（来源 %s）" % (username or "-", client or "-"))
        return False, {"error": "账号或口令不正确"}
    if not verify_password(password, row["pwd_hash"]):
        st.audit(None, "auth", "auth.login", "failed",
                 "登录失败：账号 %s 口令错误（来源 %s）" % (row["username"], client or "-"))
        return False, {"error": "账号或口令不正确"}
    ttl = int(auth_cfg.get("session_ttl_sec") or 28800)
    tok = new_token()
    st.session_create(tok, row["username"], row["role"], ttl, client)
    st.user_mark_login(row["username"])
    st.audit(None, "auth", "auth.login", "ok",
             "%s（%s）登录成功，会话有效期 %d 分钟" % (
                 row["username"], ROLE_LABEL.get(row["role"], row["role"]), ttl // 60))
    return True, {"ok": True, "token": tok, "role": row["role"],
                  "username": row["username"], "display": row["display"],
                  "ttl_sec": ttl}


def logout(st, token: str, who: str = ""):
    if token:
        st.session_delete(token)
        st.audit(None, "auth", "auth.logout", "ok", "%s 主动登出" % (who or "-"))
    return {"ok": True}


# ------------------------------------------------------------------ 演示身份切换
def switch_demo(cfg, st, role: str, client: str = ""):
    """演示用的一键切换：按角色取种子账号直接建立会话。

    仅在 auth.demo_switch=true 时可用；默认开启，便于演示时按角色分段走流程，
    避免现场反复输密码打断节奏。生产化部署应在附录 A 阶段关闭此项。
    """
    auth_cfg = (cfg.get("auth") or {})
    if not bool(auth_cfg.get("demo_switch", True)):
        return False, {"error": "已禁用演示身份切换"}
    if role not in ROLE_LABEL:
        return False, {"error": "未知角色：%s" % role}
    # 取该角色的第一个启用账号
    cand = [u for u in st.user_list()
            if u["role"] == role and u.get("enabled")]
    if not cand:
        return False, {"error": "该角色暂无可用账号"}
    username = cand[0]["username"]
    ttl = int(auth_cfg.get("session_ttl_sec") or 28800)
    tok = new_token()
    st.session_create(tok, username, role, ttl, client)
    st.audit(None, "auth", "auth.switch", "ok",
             "演示身份切换为 %s（%s）" % (username, ROLE_LABEL[role]))
    return True, {"ok": True, "token": tok, "role": role, "username": username,
                  "display": cand[0]["display"], "ttl_sec": ttl}


# ------------------------------------------------------------------ 会话解析
def current(cfg, st, cookie_header: str):
    """从 Cookie 头解析当前会话，返回 dict 或 None。"""
    tok = parse_cookie(cookie_header)
    if not tok:
        return None
    row = st.session_get(tok)
    if not row:
        return None
    return {"token": tok, "username": row["username"], "role": row["role"],
            "display": _display_of(st, row["username"]) or row["username"],
            "created_at": row["created_at"], "expires_at": row["expires_at"]}


def _display_of(st, username):
    row = st.user_get(username)
    return row.get("display") if row else None


def parse_cookie(header: str):
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME and v:
            return v.strip()
    return ""


def cookie_header(token: str, ttl_sec: int, secure: bool = False) -> str:
    bits = ["%s=%s" % (COOKIE_NAME, token), "Path=/", "HttpOnly", "SameSite=Lax",
            "Max-Age=%d" % int(ttl_sec)]
    if secure:
        bits.append("Secure")
    return "; ".join(bits)


def clear_cookie_header() -> str:
    return "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE_NAME


def client_of(handler) -> str:
    """取客户端标识（IP + User-Agent 摘要），仅用于审计与界面提示。"""
    try:
        ip = handler.client_address[0] if handler.client_address else "-"
    except Exception:                                            # noqa: BLE001
        ip = "-"
    ua = (handler.headers.get("User-Agent") or "")[:90]
    return "%s | %s" % (ip, ua)


def denial_detail(role, cap, method, path):
    need = ROLE_LABEL.get(role, role or "未登录")
    if cap == "deny":
        return "越权拦截：%s（%s）请求 %s %s，该接口未授权任何角色，仅系统管理员可用" % (
            need, role, method, path)
    if not role:
        return "越权拦截：未登录会话请求 %s %s（需 %s）" % (
            method, path, CAP_LABEL.get(cap, cap))
    allow = "、".join(ROLE_LABEL[r] for r in ROLE_ORDER if r in CAPS.get(cap, set()))
    return "越权拦截：%s（%s）请求 %s %s，需「%s」权限，仅 %s 可用" % (
        need, role, method, path, CAP_LABEL.get(cap, cap), allow or "-")


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")
