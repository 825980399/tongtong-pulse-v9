# -*- coding: utf-8 -*-
"""★第158批 上-A T-唯一口径件-1（P1）：指标唯一口径件。

问题（Q-09）
------------
同一指标在多处各算各的：RUF100 数、器官数、"空转率"在任务书/报告/台账里
反复出现**不同数值**，导致"总账引哪个值"无法判定。本件把每个指标收敛为
**唯一口径 + 唯一取值入口**，总账只引本件的值。

三个收敛指标（Q-09 指定）
------------------------
====================  ==========  ====================================================
指标 id                状态        口径要点
====================  ==========  ====================================================
``ruf100_total``      active      RUF100（未使用的 ``# noqa``）全仓计数；基线快照
                                   ``tools/ci/baselines/ruff_ruf100_156.json``
``organ_declared``    active      器官**声明**数 = ``ORGAN_META`` + ``FRAMEWORK_QUASI_ORGANS``
                                   （QICA 由 main Phase 0 单独实例化，不在扫描面）
``idle_rate_v2``      active      报告**真空转**率（v2 四归位）= ``true_idle_rate``
``idle_rate_v1``      ★deprecated 历史上的「空转率 71.1%」——**已证伪，禁止引用**
====================  ==========  ====================================================

★为什么废弃 ``idle_rate_v1``（71.1%）
-------------------------------------
原口径 = ``consumed_by`` 为空 / 全 388 = 71.1%（276/388），把「已存档 / 被观测 /
走事件」三类**合规去向**统统算成空转。已被 v2 四归位证伪：报告级**真空转 0.0%**
（T-审计-2 终裁实测，129→20→0 全程可复算）。本件保留该 id 仅为**拦截误引**——
引用它会拿到 ``status="deprecated"`` 与替代指标指引，而非静默给出旧数字。

唯一口径纪律
------------
* 总账 / 报告 / 台账**只引** :func:`get_value` 的值，不得自行重算；
* 每��值都带 ``definition`` / ``source`` / ``value_kind``，可复核可追溯；
* 弃用指标不删（避免历史引用断链），但显式标 ``deprecated`` 并给 ``superseded_by``。

L1 只读性质：本件只做「口径声明 + 取值」，不写任何数据文件、不改进化决策。
"""
from __future__ import annotations

import io
import json
import os
import re
from typing import Any

from nucleus._silent_except import silent_exc

__all__ = [
    "METRICS",
    "RUF100_BASELINE_PATH",
    "get",
    "get_value",
    "cite",
    "all_metrics",
    "active_metrics",
    "deprecated_metrics",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: RUF100 基线（ruff 0.16.5 钉版，batch 156 快照）。
RUF100_BASELINE_PATH = os.path.join(
    _PROJECT_ROOT, "tools", "ci", "baselines", "ruff_ruf100_156.json")


# ------------------------------------------------------------------ 取值实现
def _ruf100_total() -> tuple[Any, str, str]:
    """RUF100 计数：读基线快照（**不实时跑 ruff**，成本高且结果随工作树抖动）。"""
    if not os.path.isfile(RUF100_BASELINE_PATH):
        return None, "missing", "RUF100 基线文件缺失：{}".format(RUF100_BASELINE_PATH)
    try:
        with io.open(RUF100_BASELINE_PATH, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="nucleus.self_awareness.metrics_spec::_ruf100_total",
                   level="warning")
        return None, "error", "基线不可读：{}: {}".format(type(_e).__name__, _e)
    _total = _d.get("total")
    if not isinstance(_total, int):
        return None, "error", "基线缺 total 字段"
    return _total, "snapshot", "基线 batch={} ruff={} head={}".format(
        _d.get("batch"), _d.get("ruff_version_actual"), _d.get("generated_at_head"))


def _organ_declared() -> tuple[Any, str, str]:
    """器官声明数 = ``organs/`` 下声明 ``ORGAN_META`` 的文件数 + 准器官数。

    ★口径与第158批第6刀（N-6★「装配具名差集」）**完全一致**：该刀已定
    「以 ORGAN_META 扫描数为器官计数基线」，实测 56；QICA 由 main Phase 0 以
    ``_create_organ`` 单独实例化、不在 ``organs/`` 扫描面，故以
    ``FRAMEWORK_QUASI_ORGANS`` 单列补齐 → 声明面合计 57 = 装配集。
    """
    _organs_dir = os.path.join(_PROJECT_ROOT, "organs")
    if not os.path.isdir(_organs_dir):
        return None, "error", "organs/ 目录不存在：{}".format(_organs_dir)
    _n = 0
    try:
        for _dp, _dns, _fns in os.walk(_organs_dir):
            _dns[:] = [d for d in _dns
                       if not d.startswith(".bak") and d != "__pycache__"]
            for _fn in _fns:
                if not _fn.endswith(".py"):
                    continue
                try:
                    with io.open(os.path.join(_dp, _fn), encoding="utf-8",
                                 errors="ignore") as _f:
                        _src = _f.read()
                except OSError as _e:
                    silent_exc(_e, where="nucleus.self_awareness.metrics_spec::_organ_declined",
                               level="debug")
                    continue
                if re.search(r"^ORGAN_META\s*[:=]", _src, re.M):
                    _n += 1
    except Exception as _e:
        silent_exc(_e, where="nucleus.self_awareness.metrics_spec::_organ_declared walk",
                   level="warning")
        return None, "error", "扫描 organs/ 失败：{}: {}".format(type(_e).__name__, _e)
    _quasi = 0
    try:
        from nucleus.organ_assembler import FRAMEWORK_QUASI_ORGANS
        _quasi = len(FRAMEWORK_QUASI_ORGANS)
    except Exception as _e:
        silent_exc(_e, where="nucleus.self_awareness.metrics_spec::_organ_declared quasi",
                   level="warning")
    return _n + _quasi, "live", "ORGAN_META 声明文件数=%d + 准器官=%d" % (_n, _quasi)


def _idle_rate_v2() -> tuple[Any, str, str]:
    """报告真空转率（v2 四归位）——现役空转口径。"""
    try:
        from nucleus.self_awareness.capability_ledger import reconcile_reports
        _r = reconcile_reports()
    except Exception as _e:
        silent_exc(_e, where="nucleus.self_awareness.metrics_spec::_idle_rate_v2",
                   level="warning")
        return None, "error", "capability_ledger 不可用：{}: {}".format(type(_e).__name__, _e)
    _v = _r.get("true_idle_rate")
    _bd = _r.get("by_destination") or {}
    return _v, "live", "true_idle={} / total={}（v2 五类，unparseable 单列不计）".format(
        _bd.get("true_idle", 0), _r.get("total"))


def _idle_rate_v1_deprecated() -> tuple[Any, str, str]:
    """★已证伪口径：保留 id 仅供拦截误引，**不返回旧数字**。"""
    return None, "deprecated", (
        "71.1%（consumed_by 空即空转）已证伪：把存档/观测/事件三类合规去向误算为空转。"
        "请改引 idle_rate_v2（现役口径，真空转 0.0%）")


#: 指标注册表（唯一口径件本体）。
METRICS: dict[str, dict[str, Any]] = {
    "ruf100_total": {
        "name": "RUF100（未使用 noqa）计数",
        "status": "active",
        "unit": "count",
        "definition": "ruff check --select RUF100 全仓命中数（ruff 0.16.5 钉版）",
        "source": "tools/ci/baselines/ruff_ruf100_156.json",
        "value_fn": _ruf100_total,
    },
    "organ_declared": {
        "name": "器官声明数",
        "status": "active",
        "unit": "count",
        "definition": "ORGAN_META 条数 + FRAMEWORK_QUASI_ORGANS 条数（声明面，非装配瞬时值）",
        "source": "nucleus/organ_assembler.py",
        "value_fn": _organ_declared,
    },
    "idle_rate_v2": {
        "name": "报告真空转率（v2 四归位）",
        "status": "active",
        "unit": "ratio",
        "definition": "true_idle / total；v2 四归位（consumed/archived/design_declined/event/true_idle），"
                       "unparseable 单列不计",
        "source": "nucleus/self_awareness/capability_ledger.reconcile_reports",
        "value_fn": _idle_rate_v2,
    },
    "idle_rate_v1": {
        "name": "报告空转率（历史口径 71.1%）",
        "status": "deprecated",
        "unit": "ratio",
        "definition": "consumed_by 为空 / 全报告数 —— **已证伪，禁止引用**",
        "source": "微光《报告消费契约审计 v1》",
        "superseded_by": "idle_rate_v2",
        "value_fn": _idle_rate_v1_deprecated,
    },
}


# ------------------------------------------------------------------ 唯一取值入口
def get(metric_id: str) -> dict[str, Any]:
    """取指标的**唯一口径记录**（值 + 口径 + 数据源 + 状态）。"""
    _m = METRICS.get(str(metric_id))
    if _m is None:
        return {"id": metric_id, "status": "unknown",
                "error": "未注册指标（唯一口径件只认注册表内的 id）",
                "registered": sorted(METRICS.keys())}
    _value, _kind, _note = _m["value_fn"]()
    return {
        "id": str(metric_id),
        "name": _m["name"],
        "status": _m["status"],
        "unit": _m["unit"],
        "definition": _m["definition"],
        "source": _m["source"],
        "value": _value,
        "value_kind": _kind,          # snapshot / live / deprecated / error / missing
        "value_note": _note,
        "superseded_by": _m.get("superseded_by"),
    }


def get_value(metric_id: str) -> Any:
    """★总账**只引**这个函数的返回值——不要自行重算。"""
    return get(metric_id).get("value")


def cite(metric_id: str) -> str:
    """生成可引用的口径串（含值 + 口径 + 数据源），供总账/报告直接贴。"""
    _r = get(metric_id)
    if _r.get("status") == "deprecated":
        return "【已废弃】{}：{}（改引 {}）".format(
            _r["id"], _r["value_note"], _r.get("superseded_by"))
    if _r.get("value") is None:
        return "【不可用】{}：{}".format(_r["id"], _r.get("value_note") or _r.get("error"))
    return "{} = {} {}（口径：{}｜来源：{}｜{}）".format(
        _r["id"], _r["value"], _r["unit"], _r["definition"], _r["source"],
        _r["value_kind"])


def all_metrics() -> list[dict[str, Any]]:
    """全部注册指标（含弃用）的口径记录。"""
    return [get(_k) for _k in sorted(METRICS.keys())]


def active_metrics() -> list[dict[str, Any]]:
    """仅现役指标。"""
    return [_r for _r in all_metrics() if _r.get("status") == "active"]


def deprecated_metrics() -> list[dict[str, Any]]:
    """仅弃用指标（引用它们会被显式拦截）。"""
    return [_r for _r in all_metrics() if _r.get("status") == "deprecated"]
