# -*- coding: utf-8 -*-
"""修两个真 bug + 把「静默吞异常」改成可观测

bug：`notify_mod.get_notifier(cfg, st)` —— get_notifier 定义在 pipeline 里，
     不在 notify 模块。调用处各包了一层 `except Exception: xxx = {}`，
     异常被静默吞掉，于是：
       * 孪生看板「流转与调度」显示 通知渠道=未配置、脱敏=关闭（都是错的，实际全程已开启）
       * AI 助手回答「系统状态」时同样把通知与脱敏说成未配置/关闭
     两处都是**把错误状态当成事实展示**，比崩溃更危险。

改法：
  1) 改成 pipeline 里已在本模块作用域的 get_notifier(cfg, st)；
  2) **不再静默**：把异常收集进 payload 的 errors 数组，前端红字显示，自检断言为空。
     这样以后任何一处聚合出问题都会立刻暴露，而不是伪装成"未配置"。
"""
import io
import os

ROOT = r"D:\workbuddy work\2026-09-19-08-12-06\contract-review-v2.0"
log = []


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8") as f:
        c = f.read()
    for old, new in pairs:
        if old in c:
            c = c.replace(old, new, 1)
            log.append("  ok   %s :: %s" % (rel, old.strip().splitlines()[0][:54]))
        else:
            log.append("  MISS %s :: %s" % (rel, old.strip().splitlines()[0][:54]))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(c)


# ================================================================ pipeline.twin_overview
patch("app/pipeline.py", [
    ("""    rl = st.rollup()
    stats = st.stats()
    cases = st.list_cases()""",
     """    errors = []          # 聚合过程中的异常一律记下来，绝不静默吞掉：
                         # 把"取不到"伪装成"没有"，展示出来就是错误信息（本次踩过）

    rl = st.rollup()
    stats = st.stats()
    cases = st.list_cases()"""),

    ("""    try:
        from . import notify as notify_mod
        nt = notify_mod.get_notifier(cfg, st)
        nstat = nt.status()
    except Exception:                                            # noqa: BLE001
        nstat = {}""",
     """    try:
        # 注意：get_notifier 定义在**本模块**（pipeline），不在 notify 模块里。
        # 曾经写成 notify_mod.get_notifier(...) → AttributeError → 被静默吞掉 →
        # 面板把「通知渠道」显示成"未配置"、"脱敏"显示成"关闭"，全是错的。
        nt = get_notifier(cfg, st)
        nstat = nt.status()
    except Exception as exc:                                     # noqa: BLE001
        nstat = {}
        errors.append("读取通知状态失败：%s" % exc)"""),

    ("""    except Exception:                                            # noqa: BLE001
        sched = {"enabled": False, "runs": 0, "interval_sec": 0,
                 "next_run_in_sec": 0, "current_interval": 0,
                 "consecutive_failures": 0, "last_result": "", "last_run_at": ""}""",
     """    except Exception as exc:                                     # noqa: BLE001
        sched = {"enabled": False, "runs": 0, "interval_sec": 0,
                 "next_run_in_sec": 0, "current_interval": 0,
                 "consecutive_failures": 0, "last_result": "", "last_run_at": ""}
        errors.append("读取调度状态失败：%s" % exc)"""),

    ("""    except Exception:                                            # noqa: BLE001
        pass
    ct_data = review_mod.load_clause_types(cfg.root)""",
     """    except Exception as exc:                                     # noqa: BLE001
        errors.append("加载规则库失败：%s" % exc)
    ct_data = review_mod.load_clause_types(cfg.root)"""),

    ("""    except Exception:                                            # noqa: BLE001
        ocr_docs = 0""",
     """    except Exception as exc:                                     # noqa: BLE001
        ocr_docs = 0
        errors.append("统计 OCR 样本失败：%s" % exc)"""),

    ("""        "auth": denied or {},
        "activity": st.audit_list(None, 60),
    }""",
     """        "auth": denied or {},
        "activity": st.audit_list(None, 60),
        # 空数组 = 聚合全程无异常；非空时前端会红字显示出来
        "errors": errors,
    }"""),
])

# ================================================================ assistant._live_system
patch("app/assistant.py", [
    ("""def _live_system(cfg, st, sess):
    from . import notify as notify_mod
    from . import output as output_mod
    from . import pdf_min
    from . import queue as queue_mod
    from . import scheduler as sch_mod
    q = queue_mod.get_queue(cfg, st).stats()
    sch = sch_mod.get_scheduler(cfg, st).status()
    try:
        nt = notify_mod.get_notifier(cfg, st).status()
    except Exception:                                            # noqa: BLE001
        nt = {}""",
     """def _live_system(cfg, st, sess):
    from . import output as output_mod
    from . import queue as queue_mod
    from . import pipeline as pipeline_mod
    from . import scheduler as sch_mod
    q = queue_mod.get_queue(cfg, st).stats()
    sch = sch_mod.get_scheduler(cfg, st).status()
    try:
        # get_notifier 在 pipeline 里（曾经误写成 notify_mod.get_notifier，
        # 异常被吞掉 → 助手把「已配置/已开启」说成「未配置/关闭」）
        nt = pipeline_mod.get_notifier(cfg, st).status()
    except Exception as exc:                                     # noqa: BLE001
        nt = {"_error": str(exc)}"""),

    ("""               "已配置" if nt.get("configured") else "未配置",
               "已开启" if (nt.get("mask") or {}).get("party") else "关闭"))""",
     """               ("读取失败（%s）" % nt["_error"]) if nt.get("_error")
               else ("已配置" if nt.get("configured") else "未配置"),
               "未知" if nt.get("_error")
               else ("已开启" if (nt.get("mask") or {}).get("party") else "关闭")))"""),
])

# ================================================================ 前端：把 errors 显示出来
patch("ui/twin.html", [
    ("""  renderHead();
  renderKpi();""",
     """  renderHead();
  renderErrors();
  renderKpi();"""),
    ("""function renderHead() {""",
     """/* 聚合异常必须看得见 —— 否则「取不到」会被当成「没有」，
   面板显示的就是错误信息（本次踩过：通知状态读失败 → 显示"未配置/脱敏关闭"）。 */
function renderErrors() {
  var box = CR.$('#errBox');
  if (!box) { return; }
  var es = (D && D.errors) || [];
  if (!es.length) { box.innerHTML = ''; box.style.display = 'none'; return; }
  box.style.display = '';
  box.innerHTML = '<b style="color:var(--high)">服务端聚合出现 ' + es.length +
    ' 处异常，下列数字可能不完整：</b>' +
    es.map(function (e) { return '<div>· ' + CR.esc(e) + '</div>'; }).join('');
}

function renderHead() {"""),
    ("""  <div class="data-note" style="margin-bottom:14px" id="srcNote">""",
     """  <div class="data-note" id="errBox"
       style="display:none;margin-bottom:14px;border-color:var(--high-line);
              background:var(--high-bg);color:var(--ink)"></div>

  <div class="data-note" style="margin-bottom:14px" id="srcNote">"""),
])

with io.open(os.path.join(ROOT, "verify", "notify_bug_fix.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print("notify bug fix done")
