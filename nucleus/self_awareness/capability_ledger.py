# -*- coding: utf-8 -*-
"""★第158批 上-A T-自我审计-2（P1）：能力四态账本（空转读数改 v2 四归位）。

★71.1% 已证伪，不得用作基线
---------------------------
原始出处是微光《报告消费契约审计 v1》「全局空转率」= ``consumed_by`` 为空 /
全 388 = **71.1%（276/388）**。该口径**已被 v2 证伪**：它把「已存档 / 被观测 /
走事件」三类**合规去向**统统算成"空转"。v2 拆分后报告级**真空转 0.0%**，
本刀据此建模（星轨 2026-10-03 裁定，见 `星轨_夜间施工包跳刀裁定_20261003.md` §二）。

v2 四归位判据（全部基于**可证据字段**，不臆造）
----------------------------------------------
按优先级归位，每份报告**只落一处**，避免重复计数：

======================  =========================================================
归位                判据（证据字段）
======================  =========================================================
``consumed``          ``consumed_by`` 非空 —— 已被真实消费
``archived``          路径含 ``_archive`` —— 已归档（合规去向）
``design_declined``   ``consume_results`` 有记录且 ``accepted=false``
                      —— 消费点**看见了并显式拒绝**（设计性拒绝，不是"没人看见"）
``event``             ``routing_order`` 非空 —— 已路由到消费者（事件已投递）
``true_idle``         以上全无 —— **真正的真空转**
======================  =========================================================

★"设计性拒绝"建模要点：``consume_results[].accepted=false`` 是**消费点主动拒绝**
的留痕（与"压根没有消费点"语义不同）。故此类**不记为真空转**，单独归位，
防止幻影复燃——否则会把契约设计本身误判成缺陷。

能力四态（declared / wired / consumed / idle）
---------------------------------------------
能力清单来源 = PCM（``ProductionConsumptionMatcher``，任务书裁定：其 70 命中集合）。
PCM 侧 category 与本账本的映射：

* ``normal`` → **consumed**（有生产有消费）
* ``no_producer`` → **wired**（声明了接线但无生产）
* ``no_consumer`` → **idle**（写了没人读）
* ``excluded`` / ``dynamic_path`` / ``path_not_found`` → **declared**（自观测/动态，
  不构成能力闭环）

共基建（与 O-A2 一件勿重做）
---------------------------
台账落盘**复用** :mod:`nucleus.self_awareness.maturity_ledger` 的
``build_entry`` / ``append_entry``，本件只负责「四态 + 四归位」的**取数与建模**。

L1 只读性质：只写 maturity_ledger 的台账文件；不改进化决策、不发网络请求。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc

__all__ = [
    "DESTINATIONS",
    "PCM_CATEGORY_TO_STATE",
    "reports_root",
    "classify_report",
    "reconcile_reports",
    "capability_states",
    "register_capability_ledger",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: v2 四归位（+ 已消费，共 5 类；每份报告只落其一）。
DESTINATIONS = ("consumed", "archived", "design_declined", "event", "true_idle")

#: PCM category → 能力四态映射。
PCM_CATEGORY_TO_STATE = {
    "normal": "consumed",
    "no_consumer": "idle",
    "no_producer": "wired",
    "excluded": "declared",
    "dynamic_path": "declared",
    "path_not_found": "declared",
    "unknown": "declared",
}


def reports_root() -> str:
    """报告根目录（``data/reports``）。"""
    return os.path.join(_PROJECT_ROOT, "data", "reports")


def _is_test_env() -> bool:
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.capability_ledger::_is_test_env")
        return False


# ------------------------------------------------------------------ v2 四归位
def classify_report(rel_path: str, data: Any) -> tuple[str, str]:
    """把一份报告归位到 v2 五类之一。

    Args:
        rel_path: 相对 ``data/reports`` 的路径（用于识别 ``_archive``）。
        data: 报告 JSON 内容（非 dict 视为不可解析）。

    Returns:
        ``(归位名, 依据)``，依据用于台账留痕、可复核。
    """
    if not isinstance(data, dict):
        return "true_idle", "报告不可解析（JSON 非对象）"
    # ① 真实消费
    _cb = data.get("consumed_by")
    if isinstance(_cb, (list, tuple)) and len(_cb) > 0:
        return "consumed", "consumed_by 非空（%d 个消费点）" % len(_cb)
    if isinstance(_cb, str) and _cb.strip():
        return "consumed", "consumed_by 非空"
    # ② 归档（合规去向）
    if "_archive" in str(rel_path).replace("\\", "/"):
        return "archived", "路径含 _archive（已归档）"
    # ③ 设计性拒绝：消费点看见了但显式不收
    _cr = data.get("consume_results")
    if isinstance(_cr, (list, tuple)) and len(_cr) > 0:
        _names = []
        _all_rejected = True
        for _r in _cr:
            if isinstance(_r, dict):
                _names.append(str(_r.get("consumer") or "?"))
                if _r.get("accepted") is True:
                    _all_rejected = False
        if _all_rejected:
            return ("design_declined",
                    "consume_results 有记录但 accepted=false（消费点显式拒绝，非真空转）"
                    "：%s" % ", ".join(_names[:5]))
        return "event", "consume_results 有记录且含 accepted=true"
    # ④ 事件路由
    _ro = data.get("routing_order")
    if isinstance(_ro, (list, tuple)) and len(_ro) > 0:
        return "event", "routing_order 非空（已路由 %d 个消费点）" % len(_ro)
    if isinstance(_ro, str) and _ro.strip():
        return "event", "routing_order 非空"
    # ⑤ 真空转
    return "true_idle", "consumed_by 空 + 无 consume_results + 无 routing_order + 未归档"


def reconcile_reports(root: str | None = None) -> dict[str, Any]:
    """全量对账 ``data/reports``，输出 v2 四归位统计（**空转读数单一**）。

    Returns:
        ``{total, by_destination, true_idle_rate, unparsable, by_reason_sample,
        scanned_at, source}``。
    """
    _base = root or reports_root()
    _out: dict[str, Any] = {
        "total": 0,
        "by_destination": {k: 0 for k in DESTINATIONS},
        "true_idle_rate": None,
        "unparsable": 0,
        "by_reason_sample": {},
        "scanned_at": time.time(),
        "source": "data/reports",
    }
    if not os.path.isdir(_base):
        _out["source"] = "reports_dir_missing"
        return _out
    for _dp, _dns, _fns in os.walk(_base):
        for _fn in _fns:
            if not _fn.endswith(".json"):
                continue
            _fp = os.path.join(_dp, _fn)
            _rel = os.path.relpath(_fp, _base).replace("\\", "/")
            _d: Any = None
            try:
                with io.open(_fp, encoding="utf-8") as _f:
                    _d = json.load(_f)
            except (OSError, ValueError) as _e:
                _out["unparsable"] += 1
                silent_exc(_e, where="nucleus.self_awareness.capability_ledger::reconcile",
                           level="debug")
            _dest, _why = classify_report(_rel, _d)
            _out["by_destination"][_dest] = _out["by_destination"].get(_dest, 0) + 1
            _out["total"] += 1
            if _dest not in _out["by_reason_sample"]:
                _out["by_reason_sample"][_dest] = {"sample_path": _rel, "reason": _why}
    if _out["total"]:
        _out["true_idle_rate"] = round(
            _out["by_destination"].get("true_idle", 0) / float(_out["total"]), 4)
    return _out


# ------------------------------------------------------------------ 能力四态
def capability_states() -> dict[str, Any]:
    """能力四态读数（清单来源 = PCM，任务书裁定）。

    复用 ``ProductionConsumptionMatcher.scan()`` 的 category 统计，映射为
    declared / wired / consumed / idle 四态。PCM 不可用时返回 ``source=pcm_unavailable``。
    """
    _out: dict[str, Any] = {
        "states": {k: 0 for k in ("declared", "wired", "consumed", "idle")},
        "pcm_total": 0,
        "by_category": {},
        "source": "pcm",
        "note": "",
    }
    try:
        from nucleus.self_awareness.ProductionConsumptionMatcher import (
            ProductionConsumptionMatcher,
        )
        _m = ProductionConsumptionMatcher()
        _res = _m.scan()
    except Exception as _e:
        silent_exc(_e, where="nucleus.self_awareness.capability_ledger::capability_states")
        _out["source"] = "pcm_unavailable"
        _out["note"] = "%s: %s" % (type(_e).__name__, _e)
        return _out

    _sum = (_res or {}).get("summary") or _res or {}
    if not isinstance(_sum, dict):
        _out["source"] = "pcm_unexpected_shape"
        return _out
    # ★白名单取 category 计数（黑名单会误排 no_consumer/excluded 等主计数）。
    #   实测 PCM summary 的 category 键：normal / no_consumer / no_producer /
    #   excluded / unknown / dynamic_path / path_not_found。
    for _cat in ("normal", "no_consumer", "no_producer", "excluded",
                 "unknown", "dynamic_path", "path_not_found"):
        _n = _sum.get(_cat)
        if not isinstance(_n, int) or _n <= 0:
            continue
        _st = PCM_CATEGORY_TO_STATE.get(_cat)
        if _st:
            _out["states"][_st] += _n
            _out["by_category"][_cat] = _n
    _out["pcm_total"] = sum(_out["states"].values())
    return _out


# ------------------------------------------------------------------ 落台账（复用 O-A2 基建）
def register_capability_ledger(batch: str = "", pcm_result: dict | None = None) -> dict[str, Any]:
    """把「能力四态 + v2 四归位」登记进台账（**复用 maturity_ledger**）。

    复用方式：借 ``maturity_ledger.append_entry`` 落一条**台账条目**，
    把四态与四归位挂在 ``rates`` 之外的 ``capability`` 段——不另建台账文件、
    不重复实现落盘，保证与 O-A2 **一件勿重做**。
    """
    try:
        from nucleus.self_awareness import maturity_ledger as ML
    except Exception as _e:
        silent_exc(_e, where="nucleus.self_awareness.capability_ledger::register import")
        return {"error": "%s: %s" % (type(_e).__name__, _e)}

    _reports = reconcile_reports()
    _caps = pcm_result if isinstance(pcm_result, dict) else capability_states()

    # 复用 O-A2 的 build_entry/append_entry：造一个最小 result 走同一条落盘路径
    _pseudo = {
        "score": None, "dimensions": {}, "available": [],
        "reason": "capability_ledger", "version": "158A-T2-1",
    }
    _entry = ML.build_entry(_pseudo, batch=batch or "158上-A")
    _entry["capability"] = {
        "version": "158A-T2-1",
        "states": _caps.get("states"),
        "pcm_total": _caps.get("pcm_total"),
        "pcm_source": _caps.get("source"),
        "by_category": _caps.get("by_category"),
    }
    # ★空转读数单一出口：v2 四归位（71.1% 口径**不再使用**）
    _entry["idle_v2"] = {
        "by_destination": _reports.get("by_destination"),
        "total": _reports.get("total"),
        "true_idle_rate": _reports.get("true_idle_rate"),
        "unparsable": _reports.get("unparsable"),
        "caveat": ("★v2 四归位口径；71.1%（consumed_by 空即空转）已证伪，"
                   "不作为基线。design_declined=消费点显式拒绝，非真空转。"),
    }
    if _is_test_env():
        return {"skipped_test_env": True, "capability": _entry["capability"],
                "idle_v2": _entry["idle_v2"]}
    ML.append_entry(_entry)
    return {"ledger_entry_ts": _entry.get("ts"), "capability": _entry["capability"],
            "idle_v2": _entry["idle_v2"]}
