# -*- coding: utf-8 -*-
"""189批刀1：LFI 五维导出（观测级 · tools 导出面 · 零停窗 · 纯静态）。

五维原始值（57 节点 · 纯静态 AST/清单计算 · 秒级）：
  1. 连通性 connectivity：事件图谱邻接中该器官的「去重邻接器官数」（无向邻接）。
  2. 中心性 centrality：度中心性 = (入度 + 出度) / (N-1)，N = 器官总数。
  3. 冗余度 redundancy：同类 category 下的其它器官数量（organ_inventory 口径）。
  4. 健康度 health：organ_health_leaderboard（181刀3）score；
       纯静态导出无运行态 => 全部 None（未装配）。
  5. 活跃度 activity：静态近似 = method_count（导出时无活体心跳，标注「静态近似」）。

★不做（任务书明确）：不设权重、不接 overall_score、不翻生产开关。

性能：复用单次 scan_organ_classes 结果；事件图谱邻接直接走 derive_event_edges
（避免 export_organ_graph 的调用图全量分析 16s+ 开销）；模块级缓存保证重复调用秒级。
"""
from __future__ import annotations

import json
import os
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.cognitive.organ_health_leaderboard import OrganHealthLeaderboard
from nucleus.cognitive.organ_inventory import scan_organ_classes
from tools.export_event_graph import derive_event_edges

# ---- 模块级缓存（单进程内复用，避免重复全量 AST 扫描）----
_ORGANS_CACHE: list[dict] | None = None
_ADJ_CACHE: tuple[dict, dict] | None = None
_HEALTH_CACHE: dict[str, Any] | None = None


def _get_organs() -> list[dict]:
    global _ORGANS_CACHE
    if _ORGANS_CACHE is None:
        _ORGANS_CACHE = scan_organ_classes()
    return _ORGANS_CACHE


def _build_adjacency(organs: list[dict]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """从事件图谱导出有向邻接（emitter/subscriber 均为 class_name）。

    Returns: (adj_out, adj_in)
      adj_out[cls] = {订阅了 cls 所发射事件的器官}
      adj_in[cls]  = {cls 所订阅事件的发射器官}
    """
    _out: dict[str, set[str]] = {}
    _in: dict[str, set[str]] = {}
    try:
        _eg = derive_event_edges(organs)
        for _e in _eg.get("edges", []):
            _em = _e.get("emitter")
            _sub = _e.get("subscriber")
            if not _em or not _sub:
                continue
            _out.setdefault(_em, set()).add(_sub)
            _in.setdefault(_sub, set()).add(_em)
    except Exception as _e:
        silent_exc(_e, where="export_lfi.event_graph")
    return _out, _in


def _get_adjacency() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    global _ADJ_CACHE
    if _ADJ_CACHE is None:
        _ADJ_CACHE = _build_adjacency(_get_organs())
    return _ADJ_CACHE


def _static_health_map() -> dict[str, Any]:
    """健康度静态采集：无运行态 => 全部 score=None（未装配）。按 organ_name 索引。"""
    _h: dict[str, Any] = {}
    try:
        _rows = OrganHealthLeaderboard().collect()
        for _r in _rows:
            _h[_r.get("organ")] = _r.get("score")
    except Exception as _e:
        silent_exc(_e, where="export_lfi.health")
    return _h


def _get_health() -> dict[str, Any]:
    global _HEALTH_CACHE
    if _HEALTH_CACHE is None:
        _HEALTH_CACHE = _static_health_map()
    return _HEALTH_CACHE


def compute_lfi(runtime_activity: dict[str, int] | None = None) -> dict[str, Any]:
    """计算 57 节点五维原始值（纯静态 · 秒级）。

    Returns: {"version", "node_count", "nodes": [...], "meta": {...}}
    """
    _organs = _get_organs()
    _nodes_meta: list[dict[str, Any]] = []
    for _o in _organs:
        _fp = _o.get("file_path", "")
        _cat = os.path.basename(os.path.dirname(_fp)) if _fp else None
        _nodes_meta.append({
            "class_name": _o.get("class_name"),
            "name": _o.get("organ_name"),
            "category": _cat,
            "method_count": _o.get("method_count", 0) or 0,
        })
    _n = len(_nodes_meta)

    _adj_out, _adj_in = _get_adjacency()
    _health = _get_health()
    _act = runtime_activity  # 运行态活跃度源（BloodVessel.get_stats()["organ_activity"]）；None 则回退 method_count

    _denom = (_n - 1) if _n > 1 else 1
    _out_nodes: list[dict[str, Any]] = []
    for _m in _nodes_meta:
        _cls = _m.get("class_name")
        _name = _m.get("name")
        _cat = _m.get("category")
        _mc = _m.get("method_count", 0) or 0
        _out_set = _adj_out.get(_cls, set())
        _in_set = _adj_in.get(_cls, set())
        _neighbors = _out_set | _in_set
        _degree = len(_out_set) + len(_in_set)
        _connectivity = len(_neighbors)
        _centrality = round(_degree / _denom, 6) if _denom else 0.0
        _redundancy = sum(
            1 for _o in _nodes_meta
            if _o.get("category") == _cat and _o.get("class_name") != _cls
        )
        _h = _health.get(_name)
        # ★第258批 刀3②：键名对齐——_act 可能以 organ_name 或 class_name 为键，两种命名逐节点比对
        _av = _act.get(_name) if _act is not None else None
        if _av is None and _act is not None:
            _av = _act.get(_cls)
        _out_nodes.append({
            "class_name": _cls,
            "name": _name,
            "category": _cat,
            "dims": {
                "connectivity": _connectivity,
                "centrality": _centrality,
                "redundancy": _redundancy,
                "health": {
                    "score": _h,
                    "status": "未装配" if _h is None else "已装配",
                },
                "activity": {
                    "value": (_av if _av is not None else _mc),
                    "approx": ("运行态(pulse计数)" if (_act is not None and (_name in _act or _cls in _act)) else "静态近似(method_count)"),
                },
            },
        })

    _out_nodes.sort(key=lambda x: (x["class_name"] or ""))
    return {
        "version": "v1",
        "node_count": _n,
        "nodes": _out_nodes,
        "meta": {
            "connectivity_def": "事件图谱去重邻接器官数（无向邻接）",
            "centrality_def": "度中心性 = (入度+出度)/(N-1)，N=器官总数",
            "redundancy_def": "同类 category 下其它器官数量（organ_inventory 口径）",
            "health_def": "organ_health_leaderboard(181刀3)；纯静态导出无运行态=未装配(None)",
            "activity_def": "运行态活跃度(BloodVessel.organ_activity, pulse计数)；未注入运行态活跃度时回退静态近似=method_count",
            "weighted": False,
            "overall_score": False,
            "production_switch_flipped": False,
        },
    }


def export_lfi(as_json: bool = False, runtime_activity: dict[str, int] | None = None) -> Any:
    """导出 LFI 五维原始值。

    Args:
        as_json: True 返回 JSON 字符串。
    不落盘、不改状态。
    """
    _payload = compute_lfi(runtime_activity=runtime_activity)
    if as_json:
        return json.dumps(_payload, ensure_ascii=False, indent=2)
    return _payload


if __name__ == "__main__":
    print(export_lfi(as_json=True))
