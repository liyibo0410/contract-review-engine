#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""钉钉机器人通知 · 离线自测工具（零依赖，不需要启动本系统）

用途：先把机器人配好、确认能通，再谈系统接入。可在任意一台能出网的机器上跑。

用法：
    # 1) 直接传参数（最常用）
    python tools/test_notify.py --webhook "https://oapi.dingtalk.com/robot/send?access_token=xxx" \
                               --secret "SECxxxxxxxx" [--keyword 合同审查]

    # 2) 只检查签名与请求地址，不真的发消息
    python tools/test_notify.py --webhook "..." --secret "..." --dry-run

    # 3) 不带参数：依次从环境变量 → config/app.json 读取
    set DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
    set DINGTALK_SECRET=SECxxxxxxxx
    python tools/test_notify.py

参数优先级：命令行 > 环境变量（notify.webhook_env / notify.secret_env）> config/app.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import config as cfg_mod            # noqa: E402
from app import notify as notify_mod         # noqa: E402

# 钉钉常见错误码 → 人话化处理建议
HINTS = {
    310000: "签名校验失败或关键词不匹配。检查：① secret 是否与机器人「加签」密钥完全一致；"
            "② 若机器人安全设置是「自定义关键词」，确认关键词出现在消息内容里（用 --keyword 指定）",
    300001: "消息内容为空或超长（markdown 上限约 20000 字节）",
    130101: "发送过于频繁被限流（机器人限制约 20 条/分钟）",
    300005: "未找到机器人：access_token 无效，或机器人已被移除",
    400013: "机器人已被停用",
}


def parse_args():
    p = argparse.ArgumentParser(description="钉钉机器人通知自测")
    p.add_argument("--webhook", default="", help="钉钉自定义机器人 Webhook 地址")
    p.add_argument("--secret", default="", help="加签密钥（SEC 开头）")
    p.add_argument("--keyword", default=None, help="安全设置里的自定义关键词（留空则不前置）")
    p.add_argument("--at", default="", help="要 @ 的手机号，多个用逗号分隔")
    p.add_argument("--text", default="这是一条来自衡鉴 V2.1.7的连通性测试消息。",
                   help="测试消息正文")
    p.add_argument("--dry-run", action="store_true", help="只打印签名后的请求地址，不真的发送")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = cfg_mod.Config(ROOT)

    webhook = args.webhook.strip() or (os.environ.get(
        cfg.notify.get("webhook_env") or "DINGTALK_WEBHOOK") or "").strip() \
        or (cfg.notify.get("webhook") or "").strip()
    secret = args.secret.strip() or (os.environ.get(
        cfg.notify.get("secret_env") or "DINGTALK_SECRET") or "").strip() \
        or (cfg.notify.get("secret") or "").strip()
    keyword = args.keyword if args.keyword is not None else (cfg.notify.get("keyword") or "")
    ats = [x.strip() for x in args.at.split(",") if x.strip()]

    print("=" * 74)
    print("  钉钉机器人通知 · 自测")
    print("=" * 74)
    print("  Webhook    : %s" % (notify_mod.mask_url(webhook) or "（未提供）"))
    print("  加签密钥   : %s" % ("已提供（%d 字符，以 %s 开头）"
                                 % (len(secret), secret[:3]) if secret else "未提供"))
    print("  自定义关键词: %s" % (keyword or "（未设置）"))
    print("  @手机号    : %s" % ("、".join(ats) if ats else "（无）"))
    print("-" * 74)

    if not webhook:
        print("  ✗ 缺少 Webhook 地址。钉钉群 → 群设置 → 智能群助手 → 添加机器人 →")
        print("    自定义 → 复制 Webhook 地址（形如 https://oapi.dingtalk.com/robot/send?access_token=xxx）")
        print("=" * 74)
        return 1

    signed = notify_mod.dingtalk_signed_url(webhook, secret)
    print("  签名后地址 : %s" % notify_mod.mask_url(signed))
    print("  加签参数   : %s" % ("已附加 timestamp + sign" if secret else "无（未配置加签）"))
    print("-" * 74)

    if args.dry_run:
        print("  --dry-run：仅校验签名生成，未实际发送。")
        print("=" * 74)
        return 0

    # 直接复用系统内的通知器，保证「自测通过」等于「系统里也能通」
    conf = dict(cfg.notify)
    conf.update({"enabled": True, "webhook": webhook, "secret": secret,
                 "keyword": keyword, "timeout": 15})
    n = notify_mod.Notifier(_Cfg(conf))
    res = n.send_now("test", "通知连通性测试", [
        "- 来源：衡鉴 V2.1.7 · 离线自测工具",
        "- 渠道：dingtalk",
        "- 加签：%s" % ("已配置" if secret else "未配置"),
        "- 消息：%s" % args.text,
        "",
        "收到这条消息说明机器人配置正确。",
    ], at_mobiles=ats, at_all=False)

    status = res.get("status")
    if status == "sent":
        print("  ✓ 发送成功（钉钉返回 errcode=0）")
        print("    请查看机器人所在群是否收到消息。")
        print("=" * 74)
        return 0

    detail = res.get("detail") or ""
    print("  ✗ 发送失败：%s" % detail)
    for code, hint in HINTS.items():
        if str(code) in detail:
            print("    处理建议：%s" % hint)
            break
    else:
        print("    处理建议：确认本机可访问 oapi.dingtalk.com；若在受限网络内，")
        print("              该请求需要走代理或换用能出网的机器执行本工具。")
    print("=" * 74)
    return 1


class _Cfg:
    """把 dict 包装成 Notifier 期望的 cfg 对象（Notifier 只用到 cfg.notify）。"""

    def __init__(self, data):
        self.data = data

    @property
    def notify(self):
        return self.data


if __name__ == "__main__":
    sys.exit(main())
