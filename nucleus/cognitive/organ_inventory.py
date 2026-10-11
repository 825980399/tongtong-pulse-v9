# -*- coding: utf-8 -*-
"""181批刀5：器官清单导出 export_organ_inventory()（零停窗 · 观测级）。

定位：
  - 实装任务书约定的 export_organ_inventory() 接口，供自我认知引擎消费；
  - 不构建跨文件调用图（后续批）；不实装 LFI 计算（后续批）。

★数据源决策（冒烟实测后修正，勿回退）：
  - 既有 `nucleus.self_inspector.scan_all_organs()` 的 key 是**类名**（PulseBloodVessel），
    **不是 organ_name**，且带 300s 自适应降频（二次调用返回 {}）→ 不能作为唯一数据源。
  - 故本模块采用**确定性 AST 扫描**（类名 + organ_name 默认值 + 方法数），
    并以 scan_all_organs 的 method_count 作可选补充（失败不影响）。

★偏差 D#6：任务书称「实装预埋设计约定的接口」——实测全库零命中，属**新建**而非预埋。
★偏差 D#4：任务书「九大核心脑器官」中仅「大脑皮层」「前额叶」为真实 organ_name
           （海马体/杏仁核/丘脑/下丘脑/纹状体/小脑/脑干 零命中）
           => is_core = organ_name ∈ 九大 ∩ 真实器官；另以 organs/brain/ 标 is_brain_organ 补充。

导出字段：class_name / name(organ_name) / type / health / is_core / is_brain_organ
         / file_path / method_count
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
from typing import Any

from nucleus.logger import get_module_logger

_module_logger = get_module_logger("OrganInventory")

# 任务书「九大核心脑器官」（解剖学命名，★非全部为真实 organ_name）
TASKBOOK_CORE_BRAIN = (
    "大脑皮层", "海马体", "前额叶", "杏仁核", "丘脑",
    "下丘脑", "纹状体", "小脑", "脑干",
)

# 目录 -> 系统类型
_DIR_TO_TYPE = {
    "body": "身体层",
    "brain": "脑层",
    "core": "核心层",
    "endocrine": "内分泌层",
    "genetic": "遗传层",
    "identity": "身份层",
    "immune": "免疫层",
    "motor": "运动层",
    "senses": "感知层",
}

_SKIP_DIR_NAMES = frozenset({"__pycache__"})


def _organs_root() -> str:
    """organs/ 目录绝对路径。

    ★层级：nucleus/cognitive/organ_inventory.py
      -> dirname^1 = nucleus/cognitive
      -> dirname^2 = nucleus
      -> dirname^3 = 项目根   ← organs/ 在项目根下（不是 nucleus/organs）
    """
    _here = os.path.abspath(__file__)
    _proj = os.path.dirname(os.path.dirname(os.path.dirname(_here)))
    return os.path.join(_proj, "organs")


def _method_count_of(cls_node: ast.ClassDef) -> int:
    return sum(1 for n in cls_node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))


def _organ_name_default(cls_node: ast.ClassDef) -> str | None:
    """取 __init__ 的 organ_name 参数默认值（器官名来自此处）。"""
    for fn in cls_node.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "__init__":
            a = fn.args
            pairs = list(zip(a.args[len(a.args) - len(a.defaults):], a.defaults)) if a.defaults else []
            for arg, d in pairs:
                if arg.arg == "organ_name" and isinstance(d, ast.Constant):
                    return d.value
            for arg, d in zip(a.kwonlyargs, a.kw_defaults):
                if arg.arg == "organ_name" and isinstance(d, ast.Constant):
                    return d.value
    return None


def scan_organ_classes() -> list[dict[str, Any]]:
    """确定性 AST 扫描：全部 BasePulseOrgan 子类（类名 + organ_name + 路径 + 方法数）。

    ★本函数是刀3「全覆盖」与刀5「清单」的共同数据源（避免两处各扫一套）。
    """
    _root = _organs_root()
    _out: list[dict[str, Any]] = []
    if not os.path.isdir(_root):
        _module_logger.debug("[181刀5] organs 目录不存在: %s", _root)
        return _out
    for _dp, _dns, _fns in os.walk(_root):
        _dns[:] = [d for d in _dns if d not in _SKIP_DIR_NAMES]
        for _fn in sorted(_fns):
            if not _fn.endswith(".py") or _fn.startswith("__"):
                continue
            _p = os.path.join(_dp, _fn)
            try:
                _src = io.open(_p, encoding="utf-8", errors="replace").read()
                _tree = ast.parse(_src)
            except SyntaxError as _e:
                _module_logger.debug("[181刀5] 解析失败 %s: %s", _p, _e)
                continue
            for _node in _tree.body:
                if not isinstance(_node, ast.ClassDef):
                    continue
                _bases = []
                for _b in _node.bases:
                    if isinstance(_b, ast.Name):
                        _bases.append(_b.id)
                    elif isinstance(_b, ast.Attribute):
                        _bases.append(_b.attr)
                if "BasePulseOrgan" not in _bases:
                    continue
                _rel = os.path.relpath(_p, os.path.dirname(_root)).replace("\\", "/")
                _out.append({
                    "class_name": _node.name,
                    "organ_name": _organ_name_default(_node),
                    "file_path": _rel,
                    "method_count": _method_count_of(_node),
                })
    _out.sort(key=lambda r: (r["file_path"], r["class_name"]))
    return _out


def organ_names_all() -> list[str]:
    """全部器官名（organ_name 口径，供刀3 全覆盖兜底）。"""
    _names = [r["organ_name"] for r in scan_organ_classes() if r["organ_name"]]
    return sorted(set(_names))


def _inventory_on() -> bool:
    """读取开关（默认 False -> 不主动日志；export 为纯查询仍可调用）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_ORGAN_INVENTORY_EXPORT", False))
    except Exception as _e:
        _module_logger.debug("[181刀5] 开关读取失败，按关闭处理: %s: %s", type(_e).__name__, _e)
        return False


def _type_of(rel_path: str) -> str:
    for _p in (rel_path or "").replace("\\", "/").split("/"):
        if _p in _DIR_TO_TYPE:
            return _DIR_TO_TYPE[_p]
    return "未分类"


def _health_of(organ: Any) -> float | None:
    """运行时健康度（v0：与刀3 同口径，基于 get_stats 的 is_running）。"""
    try:
        _gs = getattr(organ, "get_stats", None)
        if not callable(_gs):
            return None
        _st = _gs()
        if not isinstance(_st, dict):
            return None
        _r = _st.get("is_running", None)
        if _r is True:
            return 100.0
        if _r is False:
            return 60.0
        return 40.0
    except Exception as _e:
        _module_logger.debug("[181刀5] 健康度获取失败: %s: %s", type(_e).__name__, _e)
        return None


def export_organ_inventory(organs: Any = None, as_json: bool = False):
    """导出器官清单。

    Args:
        organs: 可选，运行时器官实例映射（{类名: 实例}）→ 补充 health。
        as_json: True 时返回 JSON 字符串。

    Returns:
        dict：{"total", "core_count", "brain_count", "core_defined", "core_present", "organs": [...]}
    """
    _rows = scan_organ_classes()

    # 运行时实例：organ_name -> 实例（key 可能是类名，取实例 organ_name）
    _by_name: dict[str, Any] = {}
    if isinstance(organs, dict):
        for _k, _inst in organs.items():
            _n = str(getattr(_inst, "organ_name", "") or _k)
            _by_name[_n] = _inst

    _core_set = set(TASKBOOK_CORE_BRAIN)
    _items: list[dict[str, Any]] = []
    for _r in _rows:
        _on = _r["organ_name"] or ""
        _rel = _r["file_path"]
        _is_brain = "/brain/" in ("/" + _rel)
        _inst = _by_name.get(_on)
        _items.append({
            "class_name": _r["class_name"],
            "name": _on,
            "type": _type_of(_rel),
            "health": _health_of(_inst) if _inst is not None else None,
            "is_core": _on in _core_set,
            "is_brain_organ": _is_brain,
            "file_path": _rel,
            "method_count": _r["method_count"],
        })

    _payload = {
        "total": len(_items),
        "core_count": sum(1 for i in _items if i["is_core"]),
        "brain_count": sum(1 for i in _items if i["is_brain_organ"]),
        "core_defined": list(TASKBOOK_CORE_BRAIN),
        "core_present": [i["name"] for i in _items if i["is_core"]],
        "organs": _items,
    }
    if as_json:
        return json.dumps(_payload, ensure_ascii=False, indent=2)
    return _payload


def log_organ_inventory(organs: Any = None) -> None:
    """日志输出清单摘要（★验收：可 grep）。"""
    if not _inventory_on():
        return
    _p = export_organ_inventory(organs)
    _module_logger.info(
        "[器官清单] 总数=%d 核心(九大中真实存在)=%d 脑器官=%d 核心命中=%s",
        _p["total"], _p["core_count"], _p["brain_count"], ",".join(_p["core_present"]) or "无",
    )


# =============================================================================
# 184批刀3：器官关联图谱 v0 构建器（零停窗 · 纯静态 AST · 导出面）
# -----------------------------------------------------------------------------
# 复用 181刀5 的 scan_organ_classes() 作为权威器官清单数据源；
# - 静态边表：扫描各器官模块对其它器官类名的 import / 实例化 / 属性引用；
# - 元数据聚合：cn_name / category 沿用 self_inspector 的同款头注释正则，
#   不调用 self_inspector.build_organ_metadata（避免 LogAnalyzer 健康扫描的
#   时间/日志非确定性副作用），保证导出结果确定性；
# - 合并调用图边：ENABLE_CALL_GRAPH_ANALYSIS 开启时 best-effort 合并
#   CallGraphAnalyzer 的函数级调用边映射到器官级（kind="call"）。
# 不接任何消费、不翻 PHASE18_SIGNALS、不落盘。
_HEAD_CN_RE_M184 = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*——[ \t]*([^ \t\r\n（）()]+)"
)


def _organ_cn_name_of(file_rel: str) -> "str | None":
    """从器官模块头注释提取中文名（与 self_inspector._HEAD_CN_RE 同口径）。"""
    _p = os.path.join(os.path.dirname(_organs_root()), file_rel)
    if not os.path.isfile(_p):
        return None
    with io.open(_p, encoding="utf-8", errors="ignore") as _f:
        _head = _f.read(800)
    _m = _HEAD_CN_RE_M184.search(_head)
    return _m.group(2) if _m else None


def _organ_edges_static(organs: list[dict]) -> "dict[tuple, dict]":
    """静态 AST 扫描器官模块，产出 organ→organ 边（import / usage）。

    Returns: {(from_cls, to_cls): {"from","to","kinds":set,"refs":int}}
    """
    _cls_set = {o["class_name"] for o in organs}
    _edges: dict = {}
    for _o in organs:
        _from = _o["class_name"]
        _fp = os.path.join(os.path.dirname(_organs_root()), _o["file_path"])
        if not os.path.isfile(_fp):
            continue
        _src = io.open(_fp, encoding="utf-8", errors="replace").read()
        _tree = ast.parse(_src)
        _refd: set = set()  # (to_cls, kind)
        for _node in ast.walk(_tree):
            if isinstance(_node, ast.Import):
                for _a in _node.names:
                    _nm = _a.name.split(".")[-1]
                    if _nm in _cls_set:
                        _refd.add((_nm, "import"))
            elif isinstance(_node, ast.ImportFrom):
                for _a in _node.names:
                    if _a.name in _cls_set:
                        _refd.add((_a.name, "import"))
            elif isinstance(_node, ast.Call):
                _func = _node.func
                if isinstance(_func, ast.Name) and _func.id in _cls_set:
                    _refd.add((_func.id, "usage"))
                elif isinstance(_func, ast.Attribute) and _func.attr in _cls_set:
                    _refd.add((_func.attr, "usage"))
            elif isinstance(_node, ast.Attribute) and _node.attr in _cls_set:
                _refd.add((_node.attr, "usage"))
        for (_to, _kind) in _refd:
            if _to == _from:
                continue
            _k = (_from, _to)
            _e = _edges.get(_k)
            if _e is None:
                _e = {"from": _from, "to": _to, "kinds": set(), "refs": 0}
                _edges[_k] = _e
            _e["kinds"].add(_kind)
            _e["refs"] += 1
    return _edges


def _organ_edges_call_graph(organs: list[dict]) -> "dict[tuple, dict]":
    """★184批刀3 线2：合并调用图边（ENABLE_CALL_GRAPH_ANALYSIS 已在产）。

    best-effort：开关关闭 / 任何异常都返回空 dict，绝不破坏导出。
    把 CallGraphAnalyzer 的函数级调用边按模块归属映射到器官级（kind="call"）。
    """
    import config as _cfg
    if not getattr(_cfg, "ENABLE_CALL_GRAPH_ANALYSIS", False):
        return {}
    from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
    # 模块路径(organs.identity.X) -> 器官类名 的索引
    # 注：scan_organ_classes 的 file_path 已含 "organs/" 前缀，故 _key 直接为
    #     "organs.body.PulseX"，与 CallGraphAnalyzer 的 module 字段同款，勿再加 "organs."
    _mod_to_cls: dict = {}
    for _o in organs:
        _key = _o["file_path"][:-3].replace("/", ".")
        _mod_to_cls.setdefault(_key, _o["class_name"])
    _g = CallGraphAnalyzer().analyze()
    _edges: dict = {}
    _by_id = {n["id"]: n for n in _g.get("nodes", [])}
    for _edge in _g.get("edges", []):
        _cn = _by_id.get(_edge.get("caller"))
        _ce = _by_id.get(_edge.get("callee"))
        if not _cn or not _ce:
            continue
        _from = _mod_to_cls.get(_cn.get("module", ""))
        _to = _mod_to_cls.get(_ce.get("module", ""))
        if not _from or not _to or _from == _to:
            continue
        _k = (_from, _to)
        _e = _edges.get(_k)
        if _e is None:
            _e = {"from": _from, "to": _to, "refs": 0}
            _edges[_k] = _e
        _e["refs"] += int(_edge.get("count", 1))
    return _edges


def export_organ_graph(organs: "Any" = None, as_json: bool = False,
                       include_call_graph: "bool | None" = None) -> "Any":
    """导出器官关联图谱 v0（确定性 organ→organ 边表）。

    Args:
        organs: 可选运行时实例映射（v0 以 AST 清单为准，预留扩展）。
        as_json: True 返回 JSON 字符串。
        include_call_graph: 是否合并调用图边；None 时跟随
            config.ENABLE_CALL_GRAPH_ANALYSIS。

    Returns:
        dict：{"version", "organ_count", "edge_count", "nodes", "edges"}
        nodes = 器官元数据（class_name/name/type/category/cn_name/method_count/
            is_core/is_brain_organ）；
        edges = [{from,to,kinds:[...],refs}]（kinds ⊂ {import,usage,call}）。
    不落盘、不改状态；未开启调用图或异常时回退纯静态边表，结果仍确定性。
    """
    _rows = scan_organ_classes()
    _nodes = []
    for _r in _rows:
        _rel = _r["file_path"]
        _nodes.append({
            "class_name": _r["class_name"],
            "name": _r["organ_name"],
            "type": _type_of(_rel),
            "category": os.path.basename(os.path.dirname(_rel)),
            "cn_name": _organ_cn_name_of(_rel),
            "method_count": _r["method_count"],
            "is_core": _r["organ_name"] in TASKBOOK_CORE_BRAIN,
            "is_brain_organ": "/brain/" in ("/" + _rel),
        })
    _nodes.sort(key=lambda n: n["class_name"])

    _edges = _organ_edges_static(_rows)
    if include_call_graph is None:
        import config as _cfg
        include_call_graph = bool(getattr(_cfg, "ENABLE_CALL_GRAPH_ANALYSIS", False))
    if include_call_graph:
        for _k, _e in _organ_edges_call_graph(_rows).items():
            _cur = _edges.get(_k)
            if _cur is None:
                _cur = {"from": _e["from"], "to": _e["to"], "kinds": set(), "refs": 0}
                _edges[_k] = _cur
            _cur["kinds"].add("call")
            _cur["refs"] += _e["refs"]

    _edge_list = sorted(
        ({"from": v["from"], "to": v["to"],
          "kinds": sorted(v["kinds"]), "refs": v["refs"]}
         for v in _edges.values()),
        key=lambda e: (e["from"], e["to"]),
    )
    _payload = {
        "version": "v0",
        "organ_count": len(_nodes),
        "edge_count": len(_edge_list),
        "nodes": _nodes,
        "edges": _edge_list,
    }
    if as_json:
        return json.dumps(_payload, ensure_ascii=False, indent=2)
    return _payload

