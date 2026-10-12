# -*- coding: utf-8 -*-
"""186批刀1：图谱 v0.5 事件路由边构建器（零停窗 · 纯静态 AST · tools 导出面）。

定位：
  - 在 184刀3 图谱 v0 构建器（export_organ_graph / scan_organ_classes）基础上，
    新增事件路由边提取 derive_event_edges —— 静态 AST 提取
    「器官 X 订阅类型 T / 器官 Y 发射类型 T」→ 事件路由边（Y→T→X）。
  - 复用 184刀3 organ_inventory.py 模式：权威器官清单来自 scan_organ_classes，
    全部为确定性 AST 静态扫描，零运行时依赖（不调 LLM、不读运行态、不落盘）。

★事件类型口径（实测 nucleus/const.py）：
  - 事件类型定义在 nucleus/const.py 的各 *Event 枚举类中，成员值为字符串，
    例如 HeartEvent.BEAT 的成员值为点分事件名字符串（小写，如 heartbeat 事件）。
  - emit 端：器官经 self.pulse_core.emit(source_organ=..., event_type=..., ...) 发射，
    event_type 取枚举成员（如 HeartEvent.BEAT）或字符串字面量。
  - 订阅端：器官经两种方式声明订阅的事件类型：
    1) get_resonance_conditions() 返回的 event_types 列表（可为 ["*"] 通配，或枚举成员列表）；
    2) on_pulse(self, pulse) 体内 event_type = pulse.get("event_type", "") 后
       以 if event_type == XxxEvent.YYY: 分支订阅。

★不做（任务书明确）：
  - 不接消费接线（self_inspector 画像输出 / 图谱邻接查询推 187）；
  - 不接 Cortex 主链；
  - 不翻 PHASE18_SIGNALS 开关。

导出字段（edges）：emitter / event_type / subscriber（Y→T→X）。
"""
from __future__ import annotations

import ast
import io
import json
import os
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.cognitive.organ_inventory import scan_organ_classes
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("EventGraphExport")


def _proj_root() -> str:
    """项目根：tools/export_event_graph.py → dirname^1=tools → dirname^2=项目根。"""
    _here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(_here))


# =============================================================================
# 事件类型 taxonomy：从 nucleus/const.py 确定性 AST 提取 *Event 枚举
# -----------------------------------------------------------------------------
def load_event_taxonomy() -> dict[str, Any]:
    """解析 const.py 中所有 *Event 类，返回：

    {
      "member_full": { "HeartEvent.BEAT": "<事件值字符串>", ... },
      "value_to_member": { "<事件值字符串>": "HeartEvent.BEAT", ... },
      "all_values": set(...),
    }
    """
    _const = os.path.join(_proj_root(), "nucleus", "const.py")
    _out: dict[str, Any] = {"member_full": {}, "value_to_member": {}, "all_values": set()}
    if not os.path.isfile(_const):
        _module_logger.debug("[186刀1] const.py 不存在: %s", _const)
        return _out
    try:
        _src = io.open(_const, encoding="utf-8", errors="replace").read()
        _tree = ast.parse(_src)
    except SyntaxError as _e:
        _module_logger.debug("[186刀1] const.py 解析失败: %s", _e)
        return _out
    for _node in _tree.body:
        if not (isinstance(_node, ast.ClassDef)
                and (_node.name.endswith("Event") or _node.name == "Event")):
            continue
        for _a in _node.body:
            if not isinstance(_a, ast.Assign):
                continue
            if not (len(_a.targets) == 1 and isinstance(_a.targets[0], ast.Name)):
                continue
            if not isinstance(_a.value, ast.Constant) or not isinstance(_a.value.value, str):
                continue
            _member = _a.targets[0].id
            _value = _a.value.value
            _full = f"{_node.name}.{_member}"
            _out["member_full"][_full] = _value
            _out["value_to_member"][_value] = _full
            _out["all_values"].add(_value)
    return _out


_TAXONOMY: dict[str, Any] | None = None


def _taxonomy() -> dict[str, Any]:
    global _TAXONOMY
    if _TAXONOMY is None:
        _TAXONOMY = load_event_taxonomy()
    return _TAXONOMY


def _resolve_event_type(node: Any) -> "tuple[str | None, str]":
    """将 AST 节点解析为规范化事件类型字符串。

    Returns: (canonical_string, kind)
      - kind in {"literal", "enum", "enum_unresolved", "unresolved"}
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, "literal"
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        _full = f"{node.value.id}.{node.attr}"
        _tax = _taxonomy()
        if _full in _tax["member_full"]:
            return _tax["member_full"][_full], "enum"
        return _full, "enum_unresolved"
    return None, "unresolved"


# =============================================================================
# emit / subscribe 静态提取
# -----------------------------------------------------------------------------
def _is_pulse_core_emit(call: ast.Call) -> bool:
    """判定 Call 是否为 self.pulse_core.emit / pulse_core.emit。"""
    _f = call.func
    if not isinstance(_f, ast.Attribute) or _f.attr != "emit":
        return False
    _recv = _f.value
    if isinstance(_recv, ast.Attribute) and _recv.attr == "pulse_core":
        return True
    if isinstance(_recv, ast.Name) and _recv.id == "pulse_core":
        return True
    return False


def _emit_types_of(src: str) -> "tuple[set[str], int]":
    """提取模块内经 pulse_core.emit 发射的事件类型（规范化字符串集合）。

    Returns: (resolved_types:set, unresolved_count:int)
    """
    _types: set[str] = set()
    _unresolved = 0
    try:
        _tree = ast.parse(src)
    except SyntaxError as _e:
        silent_exc(_e, where="_emit_types_of")
        return _types, _unresolved
    for _node in ast.walk(_tree):
        if not (isinstance(_node, ast.Call) and _is_pulse_core_emit(_node)):
            continue
        _et = None
        for _kw in _node.keywords:
            if _kw.arg == "event_type":
                _et = _kw.value
                break
        if _et is None and len(_node.args) >= 2:
            _et = _node.args[1]
        if _et is None:
            continue
        _canon, _kind = _resolve_event_type(_et)
        if _canon is not None:
            _types.add(_canon)
        else:
            _unresolved += 1
    return _types, _unresolved


def _subscribe_types_of(src: str) -> "tuple[set[str], bool, int]":
    """提取模块内订阅的事件类型。

    来源 1：get_resonance_conditions() 返回的 event_types 列表
           （"*" => wildcard=True；枚举成员 / 字符串字面量 => 具体类型）。
    来源 2：on_pulse 体内 event_type = pulse.get("event_type"[, ""]) / pulse["event_type"]
           后 if event_type == XxxEvent.YYY: / event_type in (...) 订阅。

    Returns: (subscribed_types:set, wildcard:bool, unresolved_count:int)
    """
    _subs: set[str] = set()
    _wild = False
    _unresolved = 0
    try:
        _tree = ast.parse(src)
    except SyntaxError as _e:
        silent_exc(_e, where="_subscribe_types_of")
        return _subs, _wild, _unresolved

    for _fn in ast.walk(_tree):
        if not isinstance(_fn, ast.FunctionDef):
            continue

        # ---- 来源 1：get_resonance_conditions ----
        if _fn.name == "get_resonance_conditions":
            for _d in ast.walk(_fn):
                if not isinstance(_d, ast.Dict):
                    continue
                for _kk, _vv in zip(_d.keys, _d.values):
                    if isinstance(_kk, ast.Constant) and _kk.value == "event_types":
                        for _elt in getattr(_vv, "elts", []):
                            if isinstance(_elt, ast.Constant) and isinstance(_elt.value, str):
                                if _elt.value == "*":
                                    _wild = True
                                else:
                                    _subs.add(_elt.value)
                            elif isinstance(_elt, ast.Attribute) and isinstance(_elt.value, ast.Name):
                                _canon, _kind = _resolve_event_type(_elt)
                                if _canon is not None:
                                    _subs.add(_canon)
                                else:
                                    _unresolved += 1

        # ---- 来源 2：on_pulse ----
        if _fn.name == "on_pulse":
            _ev_var = "event_type"
            for _a in ast.walk(_fn):
                if isinstance(_a, ast.Assign) and len(_a.targets) == 1 and isinstance(_a.targets[0], ast.Name):
                    _val = _a.value
                    if (isinstance(_val, ast.Call) and isinstance(_val.func, ast.Attribute)
                            and _val.func.attr == "get" and _val.args
                            and isinstance(_val.args[0], ast.Constant)
                            and _val.args[0].value == "event_type"):
                        _ev_var = _a.targets[0].id
                    elif (isinstance(_val, ast.Subscript)
                          and isinstance(getattr(_val, "slice", None), ast.Constant)
                          and getattr(_val.slice, "value", None) == "event_type"):
                        _ev_var = _a.targets[0].id
            for _cmp in ast.walk(_fn):
                if (isinstance(_cmp, ast.Compare) and isinstance(_cmp.left, ast.Name)
                        and _cmp.left.id == _ev_var):
                    for _op, _c in zip(_cmp.ops, _cmp.comparators):
                        if isinstance(_op, (ast.Eq, ast.In, ast.Is)):
                            _canon, _kind = _resolve_event_type(_c)
                            if _canon is not None:
                                _subs.add(_canon)
                            else:
                                _unresolved += 1
    return _subs, _wild, _unresolved


# =============================================================================
# derive_event_edges：Y→T→X
# -----------------------------------------------------------------------------
def derive_event_edges(organs: "Any" = None) -> dict[str, Any]:
    """推导事件路由边（Y→T→X）。

    对每个器官模块做静态 AST 扫描：
      - 发射集 emits[Y] = {T}（self.pulse_core.emit 的 event_type）
      - 订阅集 subs[X] = {T}（get_resonance_conditions / on_pulse 判定），
        wild[X] = True 表示订阅通配 "*"（即订阅全部已发射类型）

    边规则：对每个 (Y, T) ∈ emits 且每个 X（X ≠ Y）满足 T ∈ subs[X] 或 wild[X]，
    产出边 {emitter:Y, event_type:T, subscriber:X}。

    Returns:
        {
          "edges": [ {emitter, event_type, subscriber}, ... ],  # 已排序、已去重
          "emits": {class_name: [types...]},
          "subscribes": {class_name: [types...]},
          "wildcards": [class_name...],
          "stats": {...},
        }
    """
    if organs is None:
        organs = scan_organ_classes()

    _emits: dict[str, set[str]] = {}
    _subs: dict[str, set[str]] = {}
    _wild: dict[str, bool] = {}
    _stats = {"emit_unresolved": 0, "sub_unresolved": 0, "files_scanned": 0}

    for _o in organs:
        _cls = _o["class_name"]
        _fp = os.path.join(_proj_root(), _o["file_path"])
        if not os.path.isfile(_fp):
            continue
        _stats["files_scanned"] += 1
        try:
            _src = io.open(_fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        _et, _eu = _emit_types_of(_src)
        _st, _w, _su = _subscribe_types_of(_src)
        if _et:
            _emits[_cls] = _et
        if _st or _w:
            _subs[_cls] = _st
        _wild[_cls] = _w
        _stats["emit_unresolved"] += _eu
        _stats["sub_unresolved"] += _su

    _known_types: set[str] = set()
    for _s in _emits.values():
        _known_types |= _s
    for _s in _subs.values():
        _known_types |= _s

    _edges: dict[tuple, dict] = {}
    _wildcards = [c for c, w in _wild.items() if w]
    for _emitter, _ts in _emits.items():
        for _t in _ts:
            for _subscriber, _st in _subs.items():
                if _subscriber == _emitter:
                    continue  # 事件路由为跨器官 Y→T→X，排除自环
                if _t in _st or _wild.get(_subscriber, False):
                    _k = (_emitter, _t, _subscriber)
                    _edges[_k] = {"emitter": _emitter, "event_type": _t, "subscriber": _subscriber}

    _edge_list = sorted(_edges.values(), key=lambda e: (e["emitter"], e["event_type"], e["subscriber"]))
    return {
        "edges": _edge_list,
        "emits": {c: sorted(s) for c, s in sorted(_emits.items())},
        "subscribes": {c: sorted(s) for c, s in sorted(_subs.items())},
        "wildcards": sorted(_wildcards),
        "stats": _stats,
    }


def _wire_event_traffic(edges: "list[dict]", publish_count: "dict[str, int] | None") -> "list[dict]":
    """将运行态事件流量发布计数接入事件图谱边（第二根接线刀）。

    Args:
        edges: derive_event_edges 产出的边表。
        publish_count: InfoField._event_publish_count（event_type->发布次数）；
            None 表示未接入（纯静态现状，零行为变化）。
    Returns:
        边表（接入后每个 edge 增加 publish_count 字段；未接入时原样返回）。
    """
    if not publish_count:
        return edges
    _out = []
    for _e in edges:
        _ne = dict(_e)
        _ne["publish_count"] = publish_count.get(_e.get("event_type"))
        _out.append(_ne)
    return _out


def export_event_graph(organs: "Any" = None, as_json: bool = False, runtime_event_publish_count: "dict[str, int] | None" = None) -> "Any":
    """导出事件路由图谱 v0.5（事件路由边表）。

    Args:
        organs: 可选运行时实例映射（v0.5 以 AST 清单为准，预留扩展）。
        as_json: True 返回 JSON 字符串。

    Returns:
        dict：{version, description, organ_count, event_type_count, edge_count,
              edges, emits, subscribes, wildcards, stats}
    不落盘、不改状态。
    """
    _rows = scan_organ_classes() if organs is None else organs
    _res = derive_event_edges(_rows)
    _payload = {
        "version": "v0.5",
        "description": "事件路由边 Y→T→X：器官 Y 发射事件类型 T，器官 X 订阅类型 T（纯静态 AST 提取）",
        "organ_count": len(_rows),
        "event_type_count": len({t for s in _res["emits"].values() for t in s}
                                | {t for s in _res["subscribes"].values() for t in s}),
        "edge_count": len(_res["edges"]),
        "edges": _wire_event_traffic(_res["edges"], runtime_event_publish_count),
        "emits": _res["emits"],
        "subscribes": _res["subscribes"],
        "wildcards": _res["wildcards"],
        "stats": {**_res["stats"], "event_traffic_wired": bool(runtime_event_publish_count)},
    }
    if as_json:
        return json.dumps(_payload, ensure_ascii=False, indent=2)
    return _payload


if __name__ == "__main__":
    print(export_event_graph(as_json=True))
