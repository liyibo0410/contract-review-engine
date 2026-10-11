# -*- coding: utf-8 -*-
"""上线后的交付目录回写：把「已部署 + 巡检结果」补进交付文档，并刷新 v2.0 运行本体。

范围与安全约束：
  * 只更新**上一次由本工具创建的** `05-可运行系统-contract-review-v2.0/` 里的文件
    与交付总览的附录文字；不删除、不重命名、不动 v1.0 的 04/05；
  * `00-交付总览.md` 再备份一次（`*.bak-20260919-2`）后做定向替换。
"""
from __future__ import annotations

import io
import os
import shutil
import time

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = r"E:\桌面\合同审批系统-李奕波\05-可运行系统-contract-review-v2.0"
OVERVIEW = r"E:\桌面\合同审批系统-李奕波\00-交付总览.md"
LOG = os.path.join(SRC, "verify", "delivery_sync2.txt")

log = []
n = [0]


def L(s=""):
    log.append(str(s))


def tree(src, dst, exclude_dirs=(), label=""):
    c = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fn in filenames:
            if fn.endswith(".pyc") or fn.endswith(".bak") or fn.endswith(".bak-20260919"):
                continue
            s = os.path.join(dirpath, fn)
            rel = os.path.relpath(s, src)
            d = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            c += 1
    n[0] += c
    L("  [TREE] %-40s %d 个文件" % (label, c))


L("=" * 100)
L("上线后交付目录回写（%s）" % time.strftime("%Y-%m-%d %H:%M:%S"))
L("=" * 100)

# ---------------------------------------------------------------- 1 交付总览附录
L("")
L("--- 1) 00-交付总览.md：把「待执行」改为「已完成」 ---")
if os.path.exists(OVERVIEW):
    bak = OVERVIEW + ".bak-20260919-2"
    if not os.path.exists(bak):
        shutil.copy2(OVERVIEW, bak)
        L("  [BAK ] %s" % os.path.basename(bak))
    with io.open(OVERVIEW, "r", encoding="utf-8") as f:
        doc = f.read()
    old = """| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | 线上实例部署与巡检 | 会覆盖正在运行的 8160 实例，属对外动作，待明确后再执行 |
| 2 | 关键路径录屏 / 演示彩排 2 次 | 需人工操作屏幕 |
| 3 | 交付截止日 / LLM Key / 钉钉可外发字段范围 | 第二次扩大会议待裁决事项 1~3 |
| 4 | 对标批次（BM 21 项 / M12~M16） | 启动条件为 M11 验收完成（D-39），且需先做契约文档技术栈修订（D-32） |"""
    new = """| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | ~~线上实例部署与巡检~~ | **已完成**（2026-09-19 09:21）：`/opt/contract-review` 已切到 v2.0.0，公网巡检 **31 / 31 通过**（`verify/remote_inspect.txt`）；上线前已整目录备份于 `/root/contract-review-backup-20260919-*.tar.gz` |
| 2 | 关键路径录屏 / 演示彩排 2 次 | 需人工操作屏幕（只有本人能做） |
| 3 | 交付截止日 / LLM Key / 钉钉可外发字段范围 | 第二次扩大会议待裁决事项 1~3 |
| 4 | 对标批次（BM 21 项 / M12~M16） | 启动条件为 M11 验收完成（D-39），且需先做契约文档技术栈修订（D-32） |

### 上线记录（2026-09-19）

| 项 | 结果 |
| --- | --- |
| 部署目标 | `http://39.96.200.45:8160/`（systemd 服务 `contract-review`） |
| 部署脚本 | `05-可运行系统-contract-review-v2.0/deploy/deploy_v2.py` |
| 数据处置 | **`data/` 全程未动**，既有 8 个审查任务 / 附件 / 审计日志 / 报告全部保留 |
| 运行时配置 | 服务器既有 `notify`（含钉钉凭据，走 systemd Environment）与 `approval` 设置**原样保留**；仅强制覆盖 v2.0 的脱敏与事件收敛口径（D-31） |
| 服务器自检 | **52 / 52 通过**（在服务器上执行 `python3.11 selftest.py`） |
| 公网巡检 | **31 / 31 通过** |
| 版本核对 | `/api/health` 返回 `2.0.0` |
| 运行形态 | `pdf_channels.effective = stdlib` —— 线上无 chromium，**服务端 PDF 走纯标准库直出通道** |"""
    if old in doc:
        doc = doc.replace(old, new)
        with io.open(OVERVIEW, "w", encoding="utf-8") as f:
            f.write(doc)
        L("  [EDIT] 未闭环事项表已更新，并追加「上线记录」小节")
    else:
        L("  [SKIP] 未匹配到待替换段落（可能已更新过）")
else:
    L("  [MISS] %s" % OVERVIEW)

# ---------------------------------------------------------------- 2 刷新运行本体
L("")
L("--- 2) 刷新 05-可运行系统-contract-review-v2.0（只覆盖本工具此前创建的文件） ---")
if os.path.isdir(DST):
    tree(SRC, DST,
         exclude_dirs=("data", "__pycache__", ".idea", "_jschk"),
         label="v2.0 可运行本体")
else:
    L("  [MISS] 目标目录不存在，跳过")

L("")
L("=" * 100)
L("回写完成：共更新 %d 个文件" % n[0])
L("安全声明：未删除、未移动、未重命名交付目录中的任何内容；")
L("          00-交付总览.md 已留 .bak-20260919-2 备份。")
L("=" * 100)

with io.open(LOG, "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("delivery refresh done: %d files" % n[0])
