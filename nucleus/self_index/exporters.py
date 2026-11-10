# -*- coding: utf-8 -*-
"""493批刀2/3/4：Self-Index 三个导出器（只读，数据全部动态读取，不硬编码）。

  · export_event_subscribers()  -> 事件↔订阅者关系（493-2）
  · export_organs_runtime()     -> 器官运行态（493-3）
  · export_intent_map()         -> 意图→方法映射（493-4）

设计原则（任务书 §纪律 + 烛微前置分析 §11.8）：
  * 纯只读，不修改任何现有器官逻辑 / 应答路径；
  * 所有字段从运行态 / 代码 / 静态表动态读取，不在此硬编码实体；
  * 每个导出都带 `generated_at` 时间戳字段（纪律 §2「加时间戳」）；
  * 任一数据源不可用时降级为空 / None，**用 silent_exc 合规记录**，保证下游可解析。
"""
from __future__ import annotations

import json
import os
import time

from nucleus._silent_except import silent_exc
from nucleus.self_index.schema import SELF_INDEX_VERSION, _now_str

# 本地处理 vs 需调大模型：rule_reason/health_check 为纯本地；其余走 LLM / 混合
_LOCAL_METHODS = frozenset({"rule_reason", "health_check"})


def _self_index_on() -> bool:
    """493-1 总开关（fail-closed：读不到 config 时默认开）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M493_SELF_INDEX", True))
    except Exception as _e:
        silent_exc(_e, where="exporters._self_index_on", level="debug")
        return True


def _read_health_snapshot_raw() -> dict:
    """读 health_snapshot.json 原始 dict（用于取 event_fanout 等透传字段）。"""
    _p = os.path.join("data", "monitor", "health_snapshot.json")
    try:
        with open(_p, encoding="utf-8", errors="replace") as _f:
            return json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="exporters._read_health_snapshot_raw", level="debug")
        return {}


def _parse_ts(ts) -> float | None:
    """解析 intent_samples 的 `YYYY-MM-DD HH:MM:SS` 时间戳为 epoch。"""
    if not ts or not isinstance(ts, str):
        return None
    for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(ts, _fmt))
        except (ValueError, OverflowError) as _e:
            silent_exc(_e, where="exporters._parse_ts", level="debug")
            continue
    return None


# ───────────────────────── 493-2 事件↔订阅者关系 ─────────────────────────
def export_event_subscribers() -> dict:
    """从 InfoField 条件表 + 事件账本导出每个事件的完整订阅者列表。

    返回：
        {"generated_at", "version", "event_count",
         "events": {event_name: [{"organ","handler","avg_latency_ms"}]},
         "fanout": {event_name: 当前扇出(current)}}
    InfoField 单例不可用时降级为 {'events': {}, 'fanout': {}}。
    """
    _events: dict[str, list[dict]] = {}
    _fanout: dict[str, float] = {}
    try:
        _raw = _read_health_snapshot_raw()
        _ef = (_raw.get("info_field") or {}).get("event_fanout") or {}
        for _ev_name, _v in _ef.items():
            if isinstance(_v, dict):
                _fanout[_ev_name] = _v.get("current")
            else:
                _fanout[_ev_name] = _v
        from nucleus.field.InfoField import get_info_field
        _field = get_info_field()
        if _field is not None:
            _conds = getattr(_field, "_conditions", {}) or {}
            for _cid, _c in _conds.items():
                if not isinstance(_c, dict):
                    continue
                _cond = _c.get("condition", {}) or {}
                _ets = _cond.get("event_types") or []
                _organ = _cond.get("organ_name", "unknown")
                _handler = _c.get("handler")
                _handler_name = (getattr(_handler, "__name__", None)
                                 or (str(_handler) if _handler else None))
                for _et in _ets:
                    if not _et:
                        continue
                    _events.setdefault(_et, []).append({
                        "organ": _organ,
                        "handler": _handler_name,
                        # 事件账本无延迟埋点，诚实标 null（待 493-8 调用图补齐）
                        "avg_latency_ms": None,
                    })
    except Exception as _e:
        silent_exc(_e, where="exporters.export_event_subscribers", level="debug")
    return {
        "generated_at": _now_str(),
        "version": SELF_INDEX_VERSION,
        "event_count": len(_events),
        "events": _events,
        "fanout": _fanout,
    }


# ───────────────────────── 493-3 器官运行态 ─────────────────────────
def export_organs_runtime() -> dict:
    """从 organ_inventory + health_snapshot 导出每个器官的实时状态。

    返回：
        {"generated_at","version","organ_count",
         "organs":[{name,file,type,is_core,is_brain_organ,health,method_count,
                    memory_est_mb,queue_depth,thread_count,
                    success_rate,avg_latency_ms}]}
    注：队列深度按器官所属层近似（核心→L2、大脑→L3）；thread_count / success_rate /
        avg_latency_ms 当前无按器官数据源，诚实标 null（运行态埋点待补）。
    """
    _organs_out: list[dict] = []
    try:
        from nucleus.cognitive.organ_inventory import export_organ_inventory
        _inv = export_organ_inventory() or {}
    except Exception as _e:
        silent_exc(_e, where="exporters.export_organs_runtime:inventory", level="debug")
        _inv = {}
    _items = (_inv.get("organs") or []) if isinstance(_inv, dict) else []

    # 运行态指标：内存按器官、队列深度按层
    _mem_by_organ: dict[str, float] = {}
    _layer_qdepth: dict[str, int] = {}
    try:
        from nucleus.world_model.external_heartbeat import read_health_snapshot
        _snap = read_health_snapshot() or {}
        _mp = _snap.get("memory_pressure") or {}
        _mbo = _mp.get("memory_by_organ") or {}
        for _o in (_mbo.get("organs") or []):
            if isinstance(_o, dict) and _o.get("organ"):
                _mem_by_organ[_o["organ"]] = _o.get("est_mb")
        _bp = _snap.get("backpressure") or {}
        if isinstance(_bp, dict):
            for _layer, _v in _bp.items():
                if isinstance(_v, dict):
                    _layer_qdepth[_layer] = _v.get("queue_depth")
    except Exception as _e:
        silent_exc(_e, where="exporters.export_organs_runtime:snapshot", level="debug")

    for _it in _items:
        if not isinstance(_it, dict):
            continue
        _name = _it.get("name")
        _is_brain = bool(_it.get("is_brain_organ"))
        _layer = "L3" if _is_brain else "L2"
        _organs_out.append({
            "name": _name,
            "file": _it.get("file_path"),          # 代码路径（行号待 493-8 CallGraphAnalyzer 补齐）
            "type": _it.get("type"),
            "is_core": _it.get("is_core"),
            "is_brain_organ": _it.get("is_brain_organ"),
            "health": _it.get("health"),
            "method_count": _it.get("method_count"),
            "memory_est_mb": _mem_by_organ.get(_name),
            "queue_depth": _layer_qdepth.get(_layer),
            "thread_count": None,                   # 全局线程数非按器官，诚实标 null
            "success_rate": None,                   # 运行态埋点待补
            "avg_latency_ms": None,                 # 运行态埋点待补
        })
    return {
        "generated_at": _now_str(),
        "version": SELF_INDEX_VERSION,
        "organ_count": len(_organs_out),
        "organs": _organs_out,
    }


# ───────────────────────── 493-4 意图→方法映射 ─────────────────────────
def export_intent_map() -> dict:
    """从 QICA INTENT_TO_METHOD + intent_samples.jsonl 导出意图映射。

    返回：
        {"generated_at","version","intent_count",
         "intents":[{intent,method,local,sample_count_7d,
                     sample_count_all,examples,avg_latency_ms}]}
    注：样本无延迟字段，avg_latency_ms 诚实标 null；local=True 表示纯本地处理。
    """
    try:
        from nucleus.qica.IntentChannels import INTENT_TO_METHOD
        _im = dict(INTENT_TO_METHOD)
    except Exception as _e:
        silent_exc(_e, where="exporters.export_intent_map:import", level="debug")
        _im = {}

    # 样本统计：按 qica_intent_type 分组，统计近7天量 + 取示例问句
    _by_intent: dict[str, dict] = {}
    _cut = time.time() - 7 * 86400
    try:
        _p = os.path.join("data", "eval", "intent_samples.jsonl")
        if os.path.isfile(_p):
            with open(_p, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _r = json.loads(_line)
                    except (ValueError, TypeError) as _e:
                        silent_exc(_e, where="exporters.export_intent_map:json", level="debug")
                        continue
                    _int = _r.get("qica_intent_type") or ""
                    _rec = _by_intent.setdefault(
                        _int, {"count_7d": 0, "count_all": 0, "examples": []})
                    _rec["count_all"] += 1
                    _t = _parse_ts(_r.get("ts"))
                    if _t is not None and _t >= _cut:
                        _rec["count_7d"] += 1
                    if len(_rec["examples"]) < 3 and _r.get("query"):
                        _rec["examples"].append(_r["query"])
    except Exception as _e:
        silent_exc(_e, where="exporters.export_intent_map:samples", level="debug")

    _intents_out = []
    for _intent, _method in _im.items():
        _s = _by_intent.get(_intent, {})
        _intents_out.append({
            "intent": _intent,
            "method": _method,
            "local": _method in _LOCAL_METHODS,
            "sample_count_7d": _s.get("count_7d", 0),
            "sample_count_all": _s.get("count_all", 0),
            "examples": _s.get("examples", []),
            "avg_latency_ms": None,               # 样本无延迟字段，诚实标 null
        })
    return {
        "generated_at": _now_str(),
        "version": SELF_INDEX_VERSION,
        "intent_count": len(_intents_out),
        "intents": _intents_out,
    }


# ───────────────────────── 连接关系推导（body_relations） ─────────────────────────
def derive_body_relations(event_subscribers: dict) -> list[dict]:
    """从事件订阅者反推「谁订阅谁」连接关系（493-1 schema 的 body_relations）。

    「谁调用谁」需调用图（CallGraphAnalyzer，493-8 范围），本批暂无数据源 ⇒ 留空。
    返回 [{from(订阅器官), relation:"subscribes", event}]。
    """
    _rel: list[dict] = []
    if not isinstance(event_subscribers, dict):
        return _rel
    _events = event_subscribers.get("events", {}) or {}
    for _ev, _subs in _events.items():
        if not isinstance(_subs, list):
            continue
        for _s in _subs:
            if isinstance(_s, dict) and _s.get("organ"):
                _rel.append({
                    "from": _s["organ"],
                    "relation": "subscribes",
                    "event": _ev,
                })
    return _rel
