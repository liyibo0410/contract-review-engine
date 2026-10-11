# -*- coding: utf-8 -*-
"""模拟审批系统（飞书审批形态）

作用：在没有企业审批平台账号的前提下，提供**真实可交互**的审批侧能力，
让「拉取待办 → 审查 → 评论回写」这条链路可以被完整演示与验收。

对外暴露浏览器可访问的 HTTP 接口（与真实平台形态一致）：
    GET    /mock/feishu/api/todo                        待办审批列表
    GET    /mock/feishu/api/instances/{id}               审批实例详情
    GET    /mock/feishu/api/instances/{id}/comments      评论区
    POST   /mock/feishu/api/comments                     写入评论（回写目标接口）
    POST   /mock/feishu/api/_config                      注入故障率（演示接口异常）

数据落盘在 data/mock_approval.json，便于查看与重置。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time

_LOCK = threading.RLock()
_STATE = {"loaded": False, "db": None, "path": "", "samples_cfg": "", "root": ""}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _samples(root, samples_dir):
    cfg = _read_json(os.path.join(root, "config", "samples.json"), {}) or {}
    out = []
    for s in cfg.get("samples", []):
        fpath = os.path.join(samples_dir, s["file"])
        out.append({
            "instance_id": "FS-AP-%s" % s["code"],
            "title": s["title"],
            "applicant": s["applicant"],
            "send_dept": s["send_dept"],
            "business_type": s["business_type"],
            "counterparty": s.get("counterparty", ""),
            "credit_code": s.get("credit_code", ""),
            "amount": s.get("amount"),
            "currency": "CNY",
            "perform_period": s.get("perform_period", ""),
            "status": "todo",                     # todo / done
            "attachment": s["file"],
            "attachment_exists": os.path.exists(fpath),
            "expect_note": (s.get("expect") or {}).get("note", ""),
            "created_at": _now(),
            "comments": [],
        })
    return out


def init(root: str, data_path: str, samples_dir: str, force_reseed: bool = False):
    """首次运行或强制重置时，用配置里的示例合同集生成待办。"""
    with _LOCK:
        db = None if force_reseed else _read_json(data_path)
        if not db or not db.get("instances"):
            db = {"seeded_at": _now(), "instances": _samples(root, samples_dir),
                  "fault_rate": 0.0}
        db.setdefault("fault_rate", 0.0)
        _write_json(data_path, db)
        _STATE.update({"loaded": True, "db": db, "path": data_path,
                       "samples_cfg": os.path.join("config", "samples.json"),
                       "root": root})
        return db


def _db():
    if not _STATE["loaded"]:
        raise RuntimeError("mock not initialized")
    return _STATE["db"]


def save():
    with _LOCK:
        _write_json(_STATE["path"], _STATE["db"])


def todo():
    with _LOCK:
        db = _db()
        return [dict(i) for i in db["instances"] if i["status"] == "todo"]


def instances():
    with _LOCK:
        return [dict(i) for i in _db()["instances"]]


def get(instance_id: str):
    with _LOCK:
        for i in _db()["instances"]:
            if i["instance_id"] == instance_id:
                return dict(i)
        return None


def set_fault_rate(rate: float):
    with _LOCK:
        _db()["fault_rate"] = float(rate)
        save()
        return _db()["fault_rate"]


def fault_rate():
    with _LOCK:
        return float(_db().get("fault_rate", 0.0))


def comments(instance_id: str):
    inst = get(instance_id)
    return list(inst["comments"]) if inst else []


def add_comment(instance_id: str, text: str, author: str = "合同智能审查系统"):
    """回写目标接口。故障注入用于演示「接口异常 → 回写失败 → 重试」。"""
    rate = fault_rate()
    if rate > 0 and random.random() < rate:
        return None, "审批系统评论接口返回 500：服务暂时不可用（模拟故障注入）"

    with _LOCK:
        db = _db()
        for inst in db["instances"]:
            if inst["instance_id"] != instance_id:
                continue
            cid = "CMT-%s-%03d" % (instance_id.split("-")[-1], len(inst["comments"]) + 1)
            inst["comments"].append({
                "comment_id": cid,
                "author": author,
                "text": text,
                "created_at": _now(),
            })
            inst["status"] = "done"
            save()
            return cid, ""
    return None, "审批实例不存在：%s" % instance_id


def reset():
    return init(_STATE["root"], _STATE["path"],
                os.path.join(_STATE["root"], "samples"), force_reseed=True)


def ensure_instance(instance_id: str, title: str = "", applicant: str = "本地上传",
                    send_dept: str = "法务部", business_type: str = "purchase",
                    counterparty: str = "", amount=None,
                    perform_period: str = "", attachment: str = ""):
    """本地上传的合同没有真实审批单，这里为其登记一个虚拟审批实例，
    使「评论回写」在任意来源的合同上都能被完整演示。"""
    with _LOCK:
        db = _db()
        for inst in db["instances"]:
            if inst["instance_id"] == instance_id:
                return inst
        inst = {
            "instance_id": instance_id, "title": title or "本地上传合同",
            "applicant": applicant, "send_dept": send_dept,
            "business_type": business_type, "counterparty": counterparty,
            "credit_code": "", "amount": amount, "currency": "CNY",
            "perform_period": perform_period, "status": "todo",
            "attachment": attachment, "attachment_exists": True,
            "expect_note": "本地上传合同（系统自动登记的虚拟审批单）",
            "created_at": _now(), "comments": [],
        }
        db["instances"].append(inst)
        save()
        return inst


# ------------------------------------------------------------------ HTTP 分发
def handle(method: str, path: str, body):
    """返回 (status_code, payload_dict)。路径以 /mock/feishu/api 开头。"""
    p = path.split("?", 1)[0]
    rel = p[len("/mock/feishu/api"):] or "/"

    if method == "GET" and rel == "/todo":
        return 200, {"code": 0, "data": {"items": todo()}}

    if method == "GET" and rel == "/instances":
        return 200, {"code": 0, "data": {"items": instances()}}

    if method == "GET" and rel.startswith("/instances/"):
        seg = rel[len("/instances/"):].split("/")
        iid = seg[0]
        if len(seg) > 1 and seg[1] == "comments":
            inst = get(iid)
            if not inst:
                return 404, {"code": 404, "msg": "审批实例不存在"}
            return 200, {"code": 0, "data": {"instance_id": iid,
                                             "comments": inst["comments"]}}
        inst = get(iid)
        if not inst:
            return 404, {"code": 404, "msg": "审批实例不存在"}
        return 200, {"code": 0, "data": inst}

    if method == "POST" and rel == "/comments":
        body = body or {}
        iid = body.get("instance_id") or ""
        text = body.get("text") or ""
        author = body.get("author") or "合同智能审查系统"
        if not iid or not text:
            return 400, {"code": 400, "msg": "instance_id 与 text 必填"}
        cid, err = add_comment(iid, text, author)
        if err:
            return 500, {"code": 500, "msg": err}
        return 200, {"code": 0, "data": {"comment_id": cid, "instance_id": iid}}

    if method == "POST" and rel == "/_config":
        body = body or {}
        rate = set_fault_rate(body.get("fault_rate", 0))
        return 200, {"code": 0, "data": {"fault_rate": rate}}

    if method == "POST" and rel == "/_reset":
        reset()
        return 200, {"code": 0, "data": {"ok": True}}

    return 404, {"code": 404, "msg": "mock 接口不存在：%s" % rel}
