# -*- coding: utf-8 -*-
"""审批回写模块

职责：把审查意见组织成格式化文本，调用审批系统的评论接口写回，并记录回写日志。

安全边界（对齐架构图）：本模块**只写评论**，不提供通过、拒绝、转交、撤回等
改变审批结论或审批流向的能力。审批平台中的最终动作仍由审批人完成。

工程约定：
  * base_url 为空 → 走同进程的模拟审批系统；填了地址 → 走真实 HTTP 接口，
    两条路径共用同一套回写流程与幂等控制，便于在无企业账号环境下完整演示；
  * 回写失败会写入日志并触发通知（通知失败不影响回写状态本身）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import store

CHANNEL_MOCK = "feishu_mock"      # 内置模拟审批系统（同进程）
CHANNEL_HTTP = "approval_http"    # 外部审批平台（HTTP）


def _approval_cfg(cfg) -> dict:
    return cfg.approval if hasattr(cfg, "approval") else (cfg.get("approval") or {})


def channel_of(cfg) -> str:
    return CHANNEL_HTTP if (_approval_cfg(cfg).get("base_url") or "").strip() \
        else CHANNEL_MOCK


def app_id(cfg) -> str:
    return _approval_cfg(cfg).get("app_id") or "contract-review"


def _opener():
    # 显式禁用代理：宿主/内网的 http_proxy 会把本机与内网请求劫持到代理端口
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _call(cfg, method: str, path: str, payload=None):
    """调用审批系统接口。base_url 为空时直接走同进程的模拟实现。"""
    base = (_approval_cfg(cfg).get("base_url") or "").rstrip("/")
    if not base:
        from mock import feishu                                    # noqa: WPS433
        return feishu.handle(method, "/mock/feishu/api" + path, payload)

    url = base + path
    timeout = _approval_cfg(cfg).get("timeout", 15)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with _opener().open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        try:
            body = json.loads(raw)
        except Exception:                                          # noqa: BLE001
            body = {"msg": raw[:300]}
        return exc.code, body
    except Exception as exc:                                       # noqa: BLE001
        return 0, {"msg": "接口调用异常：%s" % exc}


def fetch_todo(cfg):
    status, body = _call(cfg, "GET", "/todo")
    if status == 200:
        return (body.get("data") or {}).get("items", []), ""
    return [], (body or {}).get("msg", "HTTP %s" % status)


def fetch_comments(cfg, instance_id: str):
    status, body = _call(cfg, "GET", "/instances/%s/comments" % instance_id)
    if status == 200:
        return (body.get("data") or {}).get("comments", []), ""
    return [], (body or {}).get("msg", "HTTP %s" % status)


def ensure_instance(cfg, case):
    """为缺失审批单的合同（本地/示例上传）登记虚拟审批实例。"""
    iid = case.get("approval_instance") or ""
    if iid:
        return iid
    iid = "FS-AP-LOCAL-%d" % case["id"]
    base = (_approval_cfg(cfg).get("base_url") or "").rstrip("/")
    meta = {
        "instance_id": iid, "title": case.get("title") or "本地上传合同",
        "applicant": case.get("applicant") or "本地上传",
        "send_dept": case.get("send_dept") or "法务部",
        "business_type": case.get("business_type") or "purchase",
        "counterparty": case.get("counterparty") or "",
        "amount": case.get("amount"),
        "perform_period": case.get("perform_period") or "",
    }
    if not base:
        from mock import feishu                                    # noqa: WPS433
        feishu.ensure_instance(**meta)
    return iid


def send(cfg, st: store.Store, case: dict, comment_text: str,
         mode: str = "dry_run", author: str = "衡鉴 V2.1.7"):
    """执行回写。

    mode = dry_run ：预览模式，只记录待写入内容与状态，不真正写入审批平台
    mode = live    ：真实写入（对接模拟审批系统或真实评论接口）
    """
    case_id = case["id"]
    ch = channel_of(cfg)
    inst = ensure_instance(cfg, case)
    if not inst:
        inst = case.get("approval_instance") or ""
    if inst and not case.get("approval_instance"):
        st.update_case(case_id, approval_instance=inst)
        st.audit(case_id, "writeback", "approval.ensure_instance", "ok",
                 "登记虚拟审批实例 %s" % inst)

    # 幂等：已成功回写的结果不重复写入
    if mode == "live" and case.get("writeback_status") == store.WB_SUCCESS:
        last = st.writeback_list(case_id)
        st.audit(case_id, "writeback", "approval.post_comment", "skipped",
                 "该审查结果已回写成功，跳过重复写入")
        return {
            "ok": True, "skipped": True, "status": store.WB_SUCCESS,
            "comment_id": (last[0]["comment_id"] if last else ""),
            "mode": mode, "channel": ch, "instance_id": inst,
            "message": "该审查结果已回写成功，已跳过重复写入（幂等控制）",
            "payload": comment_text,
        }

    st.update_case(case_id, writeback_status=store.WB_WRITING)
    st.audit(case_id, "writeback", "approval.post_comment", "running",
             "mode=%s channel=%s app_id=%s instance=%s chars=%d" % (
                 mode, ch, app_id(cfg), inst, len(comment_text)))

    if mode == "dry_run":
        st.add_writeback(case_id, "dry_run", ch, store.WB_NOT_WRITTEN, comment_text)
        st.update_case(case_id, writeback_status=store.WB_NOT_WRITTEN)
        st.audit(case_id, "writeback", "approval.post_comment", "ok",
                 "预览模式：已生成待写入评论内容（%d 字），未写入审批平台" % len(comment_text))
        return {
            "ok": True, "skipped": False, "status": store.WB_NOT_WRITTEN,
            "comment_id": "", "mode": mode, "channel": ch,
            "instance_id": inst,
            "message": "预览模式：评论内容已生成并留存，未写入审批平台",
            "payload": comment_text,
        }

    status, body = _call(cfg, "POST", "/comments",
                         {"instance_id": inst, "text": comment_text, "author": author,
                          "app_id": app_id(cfg)})
    if status == 200 and (body.get("code") == 0):
        cid = (body.get("data") or {}).get("comment_id", "")
        st.add_writeback(case_id, "live", ch, store.WB_SUCCESS,
                         comment_text, comment_id=cid)
        st.update_case(case_id, writeback_status=store.WB_SUCCESS)
        st.audit(case_id, "writeback", "approval.post_comment", "ok",
                 "回写成功 comment_id=%s" % cid)
        return {
            "ok": True, "skipped": False, "status": store.WB_SUCCESS,
            "comment_id": cid, "mode": mode, "channel": ch,
            "instance_id": inst, "message": "审查意见已成功写回审批单评论区",
            "payload": comment_text,
        }

    err = (body or {}).get("msg") or "HTTP %s" % status
    st.add_writeback(case_id, "live", ch, store.WB_FAILED, comment_text, error=err)
    st.update_case(case_id, writeback_status=store.WB_FAILED)
    st.audit(case_id, "writeback", "approval.post_comment", "failed", err)
    _notify_failed(cfg, st, case_id, err if status else (body or {}).get("msg", ""),
                   channel=ch)
    return {
        "ok": False, "skipped": False, "status": store.WB_FAILED,
        "comment_id": "", "mode": mode, "channel": ch,
        "instance_id": inst, "error": err,
        "message": "回写失败：%s（可点击「重试回写」再次提交）" % err,
        "payload": comment_text,
    }


def _notify_failed(cfg, st, case_id, err, channel=""):
    """回写失败通知（旁路能力，异常不外抛）。"""
    try:
        from . import pipeline as pipeline_mod
        case = st.get_case(case_id)
        if case:
            pipeline_mod.notify_writeback_failed(cfg, st, case, err)
    except Exception:                                             # noqa: BLE001
        pass
