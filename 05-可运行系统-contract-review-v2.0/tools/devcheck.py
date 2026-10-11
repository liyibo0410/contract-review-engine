# -*- coding: utf-8 -*-
"""v2.0 开发期自检：编译 + 导入 + 关键路径冒烟（结果写文件，不依赖 stdout）。"""
import io
import os
import py_compile
import sys
import tempfile
import traceback

# 根目录必须从本文件位置推导，不能写死。
# 本行此前硬编码为 `D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0` ——
# 那是本工程被复制到交付目录之前的旧落点。复制后没跟着改，于是这个脚本一直去检查
# 旧副本（那份目录当时还在），对它所在的交付本体零覆盖，却照样输出「检查完毕」。
# 这是最危险的一类假绿灯：验证工具指错了对象。统一改为相对推导，和 tools/ 下其它脚本一致。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "verify", "devcheck.txt")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
sys.path.insert(0, ROOT)
log = []


def L(s):
    log.append(str(s))


# ---------- 1. 编译
L("=== 1) 语法编译 ===")
bad = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in ("data", "__pycache__", ".idea", ".venv")]
    for fn in filenames:
        if fn.endswith(".py"):
            p = os.path.join(dirpath, fn)
            try:
                py_compile.compile(p, doraise=True, cfile=os.path.join(
                    tempfile.gettempdir(), "cr_pyc_%s.pyc" % fn))
            except Exception as exc:
                bad.append("%s -> %s" % (os.path.relpath(p, ROOT), exc))
L("编译失败 %d 个" % len(bad))
for b in bad:
    L("  " + b)

# ---------- 2. 导入
L("")
L("=== 2) 模块导入 ===")
for mod in ("app", "app.config", "app.store", "app.auth", "app.scheduler", "app.queue",
            "app.pdf_min", "app.parsers", "app.review", "app.output", "app.notify",
            "app.pipeline", "app.server", "app.llm", "app.writeback", "mock.feishu"):
    try:
        __import__(mod)
        L("  ok   %s" % mod)
    except Exception as exc:
        L("  FAIL %s -> %s" % (mod, exc))
        L(traceback.format_exc())

# ---------- 3. 关键路径
L("")
L("=== 3) 关键路径 ===")
try:
    from app import config as cfg_mod, store as store_mod, auth, server as server_mod
    from app import scheduler as sch_mod, queue as q_mod, pdf_min, parsers
    from mock import feishu

    tmp = tempfile.mkdtemp(prefix="cr_v2_")
    cfg = cfg_mod.Config(ROOT)
    cfg.data["paths"] = {"data": os.path.join(tmp, "data"),
                         "uploads": os.path.join(tmp, "data/uploads"),
                         "parsed": os.path.join(tmp, "data/parsed"),
                         "reports": os.path.join(tmp, "data/reports"),
                         "samples": os.path.join(ROOT, "samples")}
    for k in ("data", "uploads", "parsed", "reports"):
        cfg.path(k)
    cfg.data["server"]["port"] = 8799
    st = store_mod.Store(cfg.db_path())
    feishu.init(ROOT, os.path.join(cfg.path("data"), "mock_approval.json"),
                cfg.path("samples"), force_reseed=True)

    L("配置告警：%s" % (cfg.warnings or "无"))

    boot = server_mod.boot(cfg, st)
    L("boot -> %s" % boot)
    L("账号：%s" % [(u["username"], u["role"]) for u in st.user_list()])

    # 口令校验
    ok, res = auth.login(cfg, st, "legal", "Legal@2026", client="devcheck")
    L("legal 登录 -> ok=%s role=%s" % (ok, res.get("role")))
    tok = res.get("token")
    sess = auth.current(cfg, st, "cr_session=" + tok)
    L("会话解析 -> %s" % (str(sess and (sess["username"], sess["role"])),))
    L("非法口令 -> %s" % (auth.login(cfg, st, "legal", "wrong")[0],))
    ok2, res2 = auth.switch_demo(cfg, st, "admin", client="devcheck")
    L("演示切换 admin -> ok=%s role=%s" % (ok2, res2.get("role")))

    # 权限解析
    L("--- 路由→能力 ---")
    cases = [("GET", "/"), ("GET", "/rules"), ("GET", "/api/cases"),
             ("POST", "/api/cases/1/writeback"), ("POST", "/api/rules/purchase"),
             ("POST", "/api/upload"), ("GET", "/api/health"), ("GET", "/login"),
             ("POST", "/api/cases/1/retry"), ("POST", "/api/unknown-write"),
             ("GET", "/mock/feishu/api/todo")]
    for m, p in cases:
        need = auth.resolve(m, p)
        legal = "OK" if auth.has_cap("legal", need) else "--"
        app_ = "OK" if auth.has_cap("applicant", need) else "--"
        adm = "OK" if auth.has_cap("admin", need) else "--"
        L("  %-6s %-32s need=%-16s legal=%s applicant=%s admin=%s"
          % (m, p, need, legal, app_, adm))
    L("  page /rules: legal=%s applicant=%s admin=%s" % (
        auth.page_allowed("legal", "/rules"), auth.page_allowed("applicant", "/rules"),
        auth.page_allowed("admin", "/rules")))

    # 脱敏
    from app import notify as nt_mod
    L("--- 通知脱敏 ---")
    L("  party  -> %s" % nt_mod.mask_party("北京华信智联科技有限公司"))
    L("  credit -> %s" % nt_mod.mask_credit("91110108MA01ABCD2X"))
    L("  amount -> %s" % nt_mod.mask_amount_text("人民币1,200,000.00元（大写：壹佰贰拾万元整）"))
    L("  title  -> %s" % nt_mod.mask_title("北京华信智联科技有限公司软件采购合同"))
    L("  sweep  -> %s" % nt_mod.sanitize(
        "相对方 91110108MA01ABCD2X 金额 ¥1,250,000.00 手机 13812345678 a@b.com", cfg))

    # PDF 直出
    L("--- PDF 直出 ---")
    pth = os.path.join(cfg.path("reports"), "devcheck_probe.pdf")
    pdf_min.write(pth, [[("h1", "服务端 PDF 直出探针"), ("body", "纯标准库通道，中文渲染测试。")]],
                  title="自检报告", meta_lines=["版本 2.0.0"])
    L("  写入 %s，%d 字节，头 %r" % (pth, os.path.getsize(pth),
                                 open(pth, "rb").read(9)))

    # 页眉页脚剔除
    L("--- 页眉页脚剔除 ---")
    blocks = [("内部资料 严禁外传", 1, False), ("第一条 标的与价款", 1, True),
              ("甲方应于验收后付款。", 1, False), ("第 1 页 共 3 页", 1, False),
              ("保密", 2, False), ("第二条 交付与验收", 2, True),
              ("乙方应于三十日内交付。", 2, False), ("第 2 页 共 3 页", 2, False),
              ("保密", 3, False), ("第三条 违约责任", 3, True),
              ("违约方应赔偿守约方损失。", 3, False), ("第 3 页 共 3 页", 3, False)]
    kept, removed = parsers.strip_repeat_lines(blocks, min_pages=2, max_chars=46, edge_lines=2)
    L("  保留 %d 行 / 剔除 %d 行" % (len(kept), len(removed)))
    for r in removed:
        L("    剔除「%s」@第%s页 重复于 %s → %s"
          % (r["text"], r["page"], r["repeat_pages"], r["reason"]))
    L("  保留内容：%s" % [k[0] for k in kept])
    # 断言：① 条款标题与正文行必须全部保留 ② 页码与密级行必须全被剔除
    must_keep = {"第一条 标的与价款", "甲方应于验收后付款。", "第二条 交付与验收",
                 "乙方应于三十日内交付。", "第三条 违约责任", "违约方应赔偿守约方损失。"}
    must_drop = {"第 1 页 共 3 页", "第 2 页 共 3 页", "第 3 页 共 3 页", "保密"}
    kept_texts = {k[0] for k in kept}
    dropped_texts = {r["text"] for r in removed}
    assert must_keep <= kept_texts, "正文/条款标题被误删：%s" % (must_keep - kept_texts)
    assert must_drop <= dropped_texts, "页码/密级未被剔除：%s" % (must_drop - dropped_texts)
    L("  保护名单与剔除断言通过（正文全保 / 页码密级全剔）")

    # 锚点一致性：剔除后重建坐标必须自洽（P1-02 验收要求）
    import re as _re
    flat = "\n".join(k[0] for k in kept)
    ok_anchor = True
    for item in must_keep:
        if flat.find(item) < 0:
            ok_anchor = False
    L("  剔除后锚点可定位：%s（重建全文 %d 字）" % (ok_anchor, len(flat)))
    assert ok_anchor, "剔除后正文锚点丢失"

    # 队列
    L("--- 审查队列 ---")
    q = q_mod.get_queue(cfg, st)
    L("  stats -> workers=%s maxsize=%s quota=%s" % (q.workers, q.maxsize, q.quota_check()))

    # 调度
    L("--- 定时拉取调度 ---")
    sch = sch_mod.get_scheduler(cfg, st)
    s0 = sch.status()
    L("  enabled=%s interval=%s next_in=%s" % (s0["enabled"], s0["interval_sec"],
                                             s0["next_run_in_sec"]))
    sch.set_config(persist=False, enabled=True, interval_sec=60)
    L("  开启后 -> %s" % {k: sch.status()[k] for k in ("enabled", "interval_sec", "alive")})
    r = sch.trigger()
    L("  手动拉取 -> %s" % r)
    L("  status -> %s" % {k: sch.status()[k] for k in
                        ("runs", "created_total", "last_result", "consecutive_failures",
                         "current_interval")})
    sch.set_config(persist=False, enabled=False)

    L("")
    L("=== 4) 审查链路（含队列 + 模式对照 + 页眉页脚） ===")
    from app import pipeline
    rows = st.list_cases()
    L("  库内案件 %d 个" % len(rows))
    done = [c for c in rows if c["review_status"] == "completed"]
    L("  已完成 %d 个，受阻 %d 个" % (
        len(done), len([c for c in rows if c["review_status"] == "blocked"])))
    L("  状态分布：%s" % st.stats()["by_status"])
    if done:
        cid = done[0]["id"]
        d = pipeline.detail(cfg, st, cid)
        L("  详情：案件 %d %s / 段落 %d / 风险项 %d" % (
            cid, d["case"]["title"], len(d["paragraphs"]), len(d["results"])))
        L("  摘要模式：%s（available=%s reason=%s）" % (
            d["summary"].get("mode_label"), d["summary"].get("mode_available"),
            d["summary"].get("mode_reason") or "-"))
        L("  mode_compare：%s" % {k: (v.get("label") if isinstance(v, dict) else v)
                                for k, v in d["mode_compare"].items()})
        L("  剔除页眉页脚 %d 行；剔除明细 %s" % (
            len(d["stripped"]),
            [(x["text"][:16], x["source"] if "source" in x else x.get("channel"))
             for x in d["stripped"]][:4]))
        rk = d["results"][0] if d["results"] else None
        if rk:
            L("  首条风险：%s / %s / p%s / char[%s,%s) 证据「%s」" % (
                rk["rule_name"], rk["risk_level_label"], rk["page_no"],
                rk["char_start"], rk["char_end"], (rk["evidence"] or "")[:24]))
        md = pipeline.build_report(cfg, st, cid, "md")
        html = pipeline.build_report(cfg, st, cid, "html")
        pdf = pipeline.build_report(cfg, st, cid, "pdf")
        L("  报告：MD %d 字 / HTML %d 字 / PDF=%s %s" % (
            len(md[1]), len(html[1]), pdf[0],
            ("%d 字节" % os.path.getsize(pdf[1])) if pdf[0] == "pdf" else pdf[1]))
        # 队列异步提交
        async_out = pipeline.submit_review(cfg, st, cid, wait=False)
        L("  异步入队 -> %s" % async_out)
        import time as _t
        _t.sleep(1.5)
        L("  队列状态 -> %s" % {k: q_mod.get_queue(cfg, st).stats()[k]
                              for k in ("submitted", "completed", "workers", "queued")})
        # 回写
        wb = d
        text = __import__("app.output", fromlist=["output"]).compose_comment(
            wb["case"], wb["document"], wb["results"], wb["summary"], wb["meta"], "")
        L("  回写正文 %d 字，预览头：%s" % (len(text), text.splitlines()[0][:60]))
        from app import writeback
        res = writeback.send(cfg, st, st.get_case(cid), text, mode="live")
        L("  回写 -> status=%s comment_id=%s" % (res.get("status"), res.get("comment_id")))
        L("  回写后案件状态 -> %s" % st.get_case(cid)["writeback_status"])
    blocked = [c for c in rows if c["review_status"] == "blocked"]
    if blocked:
        L("  受阻案件示例 -> #%d %s：%s" % (
            blocked[0]["id"], blocked[0]["title"], blocked[0]["blocked_reason"][:60]))
        rr = pipeline.retry(cfg, st, blocked[0]["id"])
        L("  重试 -> %s" % {k: rr.get(k) for k in ("ok", "blocked", "reason")})
except Exception as exc:
    L("关键路径异常：%s" % exc)
    L(traceback.format_exc())

io.open(OUT, "w", encoding="utf-8").write("\n".join(log))

# 退出码必须反映结果。原实现无论内部怎么失败都返回 0 —— 配合上面那个写死的 ROOT，
# 就组合成了「全 FAIL 但 exit=0」的假绿灯，run_all.py 与 CI 都无法察觉。
problems = 0
for _line in log:
    if _line.startswith("编译失败") and _line != "编译失败 0 个":
        problems += 1
    elif _line.startswith("  FAIL"):
        problems += 1
    elif _line.startswith("关键路径异常"):
        problems += 1

print("devcheck done ->", OUT)
print("devcheck problems: %d" % problems)
sys.exit(1 if problems else 0)
