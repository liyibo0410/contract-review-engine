# -*- coding: utf-8 -*-
"""品牌改名**杠杆**（幂等）—— 全站品牌名的唯一入口。

口径（奕波 2026-09-19 裁定，2026-09-19 二次修订）：
  * **品牌名（对外名称）= 衡鉴 V<发布版本>** —— 凡是「名字 / 版本展示」出现的地方一律用这个；
    最初对外名是「衡鉴 V2.0.2」（与语义化技术版本解耦）；后来奕波看到界面上
    还挂着旧版本号，要求「更新到最新版本」，于是改为**品牌版本跟到发布版本**：
    改 `BRAND` 常量 + 重跑本脚本即可，全站一起走。
  * **技术版本 = app/__init__.py 的 __version__** —— 语义化版本照常走；
    /api/health.version 与 CHANGELOG 保持语义化，两者并存不冲突。

设计要点：
  1. **幂等**：重复执行不会二次污染（已替换的痕迹会被识别为「已完成」）；
  2. **可审计**：逐文件逐条记录「命中 / 已完成 / 未命中」，落 verify/rebrand_hengjian.txt；
  3. **有边界**：不碰 docs/reference/**（老师原始需求基线，必须逐字保留）、
     verify/**（生成物）、data/**（运行时数据）、tools/_onetime/**（历史存档脚本）、
     samples/**（二进制样例）、以及各种虚拟环境；
  4. **不碰 CHANGELOG.md**：它是**历史记录**，把旧条目里的品牌名改成新值等于篡改历史
     （会出现「2.1.4 的品牌收口写成了 2.1.6」这种自相矛盾）。CHANGELOG 里属于
     **当前口径**的部分（H1 标题、版本表、D-43 说明）由人工改。
  5. **不碰历史归档文档**：`docs/` 下以「v + 数字」开头的 Markdown 是版本编年史，
     与 CHANGELOG 同性质，一律不改。本条的由来 —— 2.1.6 那次把品牌从 V2.0.2 迁到 V2.1.6
     之后跨版本重跑，历史文档被就地改写（同一批还暴露了 GENERIC 里写死的旧目标值），
     靠逐文件 diff 才逮到，已整体回滚。**升版本时请一并把 BRAND_PREV / BRAND_VER_PREV
     改到"改之前的当前值"**，否则迁移规则会空转。

用法：
    python tools/_onetime/rebrand_hengjian_v202.py
"""
from __future__ import annotations

import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "verify", "rebrand_hengjian.txt")

# ⚠️ 这两个值**必须成对维护**，且 BRAND_PREV 要等于「当前树上真正存在的值」。
#    历史上 BRAND_PREV 长期停留在最初的 `衡鉴 V2.0.2`，版本一路升到 2.1.6 都没人动它，
#    于是"把已品牌化的树迁到新名"这条规则逐渐空转 —— 一旦跨版本重跑，
#    它会把历史文档里遗留的 V2.0.2 就地改成**当时写死的另一个值**（见 GENERIC 注释）。
#    现在的规矩：改 BRAND 时，把 BRAND_PREV 改成**改之前的** BRAND。
BRAND = "衡鉴 V2.1.7"          # ← 改品牌名只改这一行，然后重跑本脚本
BRAND_PREV = "衡鉴 V2.1.6"     # 上一版品牌名（= 改之前树上实际存在的值）
BRAND_VER = "V2.1.7"           # 裸版本串的新值（由 BRAND 推导，勿单独硬编码）
BRAND_VER_PREV = "V2.1.6"      # 裸版本串的旧值（与 BRAND_PREV 同级维护）
BRAND_DESC = "企业合同智能审查系统"
BRAND_FULL_TITLE = BRAND + " · " + BRAND_DESC

# 需要处理的文件（相对路径 glob）
SCAN = [
    "app/*.py",
    "mock/*.py",
    "deploy/*.py",
    "tools/*.py",
    "ui/*.html",
    "ui/assets/*.js",
    "ui/assets/*.css",
    "*.md",
    "docs/*.md",
    "run.py",
    "selftest.py",
]

# 永不触碰：按**路径分量**判断（不是子串）
SKIP_DIRS = {"verify", "data", "samples", "_onetime", "site-packages", "__pycache__",
             ".git", ".idea", ".venv", "node_modules"}
# 本脚本自身 + CHANGELOG.md（历史记录，不机械改写；见文件头「设计要点 4」）
SKIP_NAMES = ("rebrand_hengjian_v202.py", "CHANGELOG.md")
# 历史归档文档（设计要点 5）：docs/ 下以「v + 数字」开头的 Markdown 全是**版本编年史**
# （v2.0.1-界面整改说明、v2.1.4-全盘复检报告、v2.1.6-工作台交互修复说明…），
# 记录的是**当时**的事实。机械改写会把它们变成「2.1.4 的报告写成了 V2.1.7」，
# 同样是篡改历史。这条豁免是 2.1.6 那次跨版本重跑事故的直接产物。
HIST_DOC_RE = re.compile(r"^docs/v\d", re.I)

# ---------------------------------------------------------------- 替换表
# 精确项：(限定文件, old, new)。只在指定文件内生效，避免满屏无效日志。
SPECIAL = [
    # ---- 前端品牌对象：拆成 短名 / 对外全名 / 英文 / 描述 ----
    ("ui/assets/app.js",
     "  const BRAND = { name: '合同智能审查系统', owner: '亦大', version: 'v2.0' };",
     "  const BRAND = {\n"
     "    name: '衡鉴',\n"
     "    full: '衡鉴 V2.0.2',        // 对外完整名称（品牌 + 对外版本标识）\n"
     "    version: 'V2.0.2',\n"
     "    owner: '衡鉴',\n"
     "    en: 'HENGJIAN',\n"
     "    desc: '企业合同智能审查系统'\n"
     "  };"),
    # 顶栏品牌区：主标题用对外全名，副标题用中文描述
    ("ui/assets/app.js",
     "      '<span class=\"brand-txt\"><b>' + BRAND.name + '</b>',\n"
     "      '<i>' + BRAND.owner + ' · intelligent contract review</i></span>',",
     "      '<span class=\"brand-txt\"><b>' + BRAND.full + '</b>',\n"
     "      '<i>' + BRAND.desc + ' · intelligent contract review</i></span>',"),
    ("ui/assets/app.js",
     "alt=\"' + BRAND.owner + ' 标识\" width=\"30\" height=\"31\">',",
     "alt=\"' + BRAND.full + ' 标识\" width=\"30\" height=\"31\">',"),
    ("ui/assets/app.js",
     "'\" alt=\"' + BRAND.owner + '\">' +",
     "'\" alt=\"' + BRAND.full + '\">' +"),
    ("ui/assets/app.js",
     "'<span class=\"sig\">（' + BRAND.owner + '）</span>' +",
     "'<span class=\"sig\">（' + BRAND.en + '）</span>' +"),
    ("ui/assets/app.js",
     "'<span>© ' + new Date().getFullYear() + ' ' + BRAND.name + ' ' + BRAND.version + ' · 保留所有权利</span>' +",
     "'<span>© ' + new Date().getFullYear() + ' ' + BRAND.full + ' · 保留所有权利</span>' +"),
    # ---- 登录页品牌块 ----
    ("ui/login.html",
     "      <img src=\"/assets/logo-sm.png\" alt=\"亦大 标识\" id=\"loginLogo\" width=\"38\" height=\"39\">\n"
     "      <div>\n"
     "        <b>合同智能审查系统</b>\n"
     "        <i>亦大 · intelligent contract review</i>",
     "      <img src=\"/assets/logo-sm.png\" alt=\"" + BRAND + " 标识\" id=\"loginLogo\" width=\"38\" height=\"39\">\n"
     "      <div>\n"
     "        <b>" + BRAND + "</b>\n"
     "        <i>" + BRAND_DESC + "</i>"),
    # ---- systemd 单元描述 ----
    ("deploy/deploy_to_server.py",
     "Description=Contract Review System (合同智能审查系统) v1.0",
     "Description=HengJian Contract Review System (" + BRAND + ")"),
    # ---- 带 %s 的模板：必须保留运行版本占位符，否则 TypeError ----
    ("app/server.py",
     'meta_lines=["合同智能审查系统 v%s" % __version__])',
     'meta_lines=["' + BRAND + ' ｜ 运行版本 %s" % __version__])'),
    ("app/notify.py",
     'lines.append("> %s ｜ 合同智能审查系统 v%s ｜ 已按字段规则脱敏"',
     'lines.append("> %s ｜ ' + BRAND + ' ｜ 运行版本 %s ｜ 已按字段规则脱敏"'),
    ("tools/run_selftest.py",
     '"  合同智能审查系统 v%s · 验收自检（selftest）" % _app.__version__,',
     '"  ' + BRAND + ' · 验收自检（selftest）｜运行版本 %s" % _app.__version__,'),
    ("tools/bench.py",
     '"合同智能审查系统 v%s · 性能基准（bench）" % _app.__version__)',
     '"' + BRAND + ' · 性能基准（bench）｜运行版本 %s" % _app.__version__)'),
    ("deploy/verify_remote_v2.py",
     '"合同智能审查系统 v%s · 线上实例巡检" % _app.__version__)',
     '"' + BRAND + ' · 线上实例巡检｜运行版本 %s" % _app.__version__)'),
    ("tools/verify_http.py",
     '"/", "合同智能审查系统".encode("utf-8"), "text/html"),',
     '"/", "' + BRAND + '".encode("utf-8"), "text/html"),'),
    # ---- 补救：上面 4 处曾被 v1 脚本的泛化规则「吃掉 %s」，这里列出已被改坏的形态，
    #      使脚本对「已经跑过一次的树」同样幂等可用 ----
    ("app/server.py",
     'meta_lines=["衡鉴 V2.0.2" % __version__])',
     'meta_lines=["' + BRAND + ' ｜ 运行版本 %s" % __version__])'),
    ("app/notify.py",
     'lines.append("> %s ｜ 衡鉴 V2.0.2 ｜ 已按字段规则脱敏"',
     'lines.append("> %s ｜ ' + BRAND + ' ｜ 运行版本 %s ｜ 已按字段规则脱敏"'),
    ("tools/run_selftest.py",
     '"  衡鉴 V2.0.2 · 验收自检（selftest）" % _app.__version__,',
     '"  ' + BRAND + ' · 验收自检（selftest）｜运行版本 %s" % _app.__version__,'),
    ("tools/bench.py",
     '"衡鉴 V2.0.2 · 性能基准（bench）" % _app.__version__)',
     '"' + BRAND + ' · 性能基准（bench）｜运行版本 %s" % _app.__version__)'),
]

GENERIC = [
    # ---- 品牌版本迁移：把已品牌化的树从旧版名迁到新版名。
    #      必须放在最前面，后面各条才好识别。顺序讲究：先改「衡鉴 V<旧>」整串，
    #      再用裸串兜住 `BRAND.version = 'V<旧>'` 这类只带版本号的地方。
    #
    #      ⚠️ 两条都必须由常量推导，**不得写死旧目标值**。
    #      历史事故：此处曾写死 ("V2.0.2", "V2.1.6")。版本升到 2.1.7 后重跑时，
    #      历史文档里遗留的 V2.0.2 会被改成 V2.1.6（上一版号）而不是当前版本，
    #      出现「本报告写于品牌版本 = 衡鉴 V2.0.2 的时期」被改错这类自相矛盾。
    #      当时靠逐文件 diff 才发现，随后整体回滚。现在改为与 BRAND 同级维护。
    (BRAND_PREV, BRAND),
    (BRAND_VER_PREV, BRAND_VER),
    # ⚠️ 顺序与「保护词」都有关：`企业合同智能审查系统` 里含有 `合同智能审查系统` 子串，
    #    若不先保护，兜底规则会把它二次替换成 `企业衡鉴 V2.0.2`（页面标题被污染成
    #    「衡鉴 V2.0.2 · 企业衡鉴 V2.0.2」）——v1 就踩过这个坑，由前端深检逮到。
    #    保护词在 apply_generic() 里用哨兵替换后处理，这里列出的是「已被污染的形态」，
    #    使脚本对跑过 v1 的树同样可修复。
    ("企业衡鉴 V2.0.2", BRAND_DESC),
    ("亦大合同智能审查系统", BRAND_FULL_TITLE),
    ("合同智能审查系统 v1.0", BRAND),
    ("合同智能审查系统 v2.0.0", BRAND),
    ("合同智能审查系统 v2.0.1", BRAND),
    ("合同智能审查系统 v2.0", BRAND),
    ("合同智能审查系统 v%s", BRAND),
    ("合同智能审查系统", BRAND),
]
PROTECT = ["企业合同智能审查系统"]   # 兜底替换时先保护，替换完再还原


def iter_files():
    seen = set()
    for pat in SCAN:
        if "*" in pat:
            d, g = os.path.split(pat)
            base = os.path.join(ROOT, d) if d else ROOT
            if not os.path.isdir(base):
                continue
            for n in sorted(os.listdir(base)):
                if not _glob_ok(n, g):
                    continue
                p = os.path.join(base, n)
                if os.path.isfile(p) and p not in seen:
                    seen.add(p)
                    yield p
        else:
            p = os.path.join(ROOT, pat)
            if os.path.isfile(p) and p not in seen:
                seen.add(p)
                yield p


def _glob_ok(name, g):
    if g == "*":
        return True
    if g.startswith("*."):
        return name.lower().endswith(g[1:].lower())
    return name == g


def skipped(path):
    if os.path.basename(path) in SKIP_NAMES:
        return True
    rel = os.path.relpath(path, ROOT)
    parts = rel.split(os.sep)
    if len(parts) >= 2 and parts[0] == "docs" and parts[1] == "reference":
        return True
    if HIST_DOC_RE.match(rel.replace(os.sep, "/")):
        return True
    return any(p in SKIP_DIRS for p in parts[:-1])


def main():
    log, n_hit, n_done, n_missing = [], 0, 0, 0

    for path in iter_files():
        if skipped(path):
            continue
        rel = os.path.relpath(path, ROOT)
        key = rel.replace(os.sep, "/")
        try:
            with io.open(path, "r", encoding="utf-8") as f:
                src = f.read()
        except (UnicodeDecodeError, OSError):
            log.append("[skip] %-40s 非 UTF-8 文本" % rel)
            continue

        text, file_log = src, []

        for hint, old, new in SPECIAL:
            if os.path.normpath(rel) != os.path.normpath(hint):
                continue
            if old in text:
                text = text.replace(old, new)
                n_hit += 1
                file_log.append("  ✓ 精确 :: %s" % _brief(old))
            elif new in text:
                n_done += 1
                file_log.append("  = 已是新值 :: %s" % _brief(new))
            else:
                n_missing += 1
                file_log.append("  ! 未命中 :: %s" % _brief(old))

        for old, new in GENERIC:
            if old == new:
                continue
            # 兜底替换前先把「保护词」换成哨兵，替换完再还原：
            # 否则 `企业合同智能审查系统` 会被 `合同智能审查系统` 规则二次命中，
            # 变成 `企业衡鉴 V2.0.2`（页面标题被污染成「衡鉴 V2.0.2 · 企业衡鉴 V2.0.2」）。
            guard = {}
            for gi, ph in enumerate(PROTECT):
                if ph in text:
                    marker = "\x00P%d\x00" % gi
                    guard[marker] = ph
                    text = text.replace(ph, marker)
            c = text.count(old)
            if c:
                text = text.replace(old, new)
                n_hit += 1
                file_log.append("  ✓ 泛化 ×%d :: %s → %s" % (c, old, new))
            for marker, ph in guard.items():
                text = text.replace(marker, ph)

        if text != src:
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            log.append("[write] %s" % rel)
            log.extend(file_log)
        elif file_log:
            log.append("[--] %s（无需改动）" % rel)
            log.extend(file_log)

    head = [
        "衡鉴品牌化（一次性变更）：合同智能审查系统 → %s" % BRAND,
        "=" * 92,
        "命中替换 %d 处 ｜ 已是新值 %d 处 ｜ 精确项未命中 %d 处" % (n_hit, n_done, n_missing),
        "口径：品牌名 = %s（对外展示）；技术版本 = app/__init__.py 的 __version__（语义化）" % BRAND,
        "=" * 92,
    ]
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(head + log) + "\n")
    print("rebrand: hit=%d done=%d miss=%d -> %s" % (n_hit, n_done, n_missing, OUT))


def _brief(s):
    return re.sub(r"\s+", " ", s)[:78]


if __name__ == "__main__":
    main()
