"""493.5-1 四类边模块图构建器。

采集 import / inject / call / event 四类模块级边，输出 ``data/self_index/module_graph.json``。

对任务书的主动补充（路灯据实测数据决策，用户已授权补充不全面处）：
- ``CallGraphAnalyzer.analyze()`` 实测 30.8s 且主链 call 边为 0（Cortex→Lung 等靠事件/注入），
  本批**不依赖**它，改为轻量 AST 扫描（import + 属性调用消解 + inject 映射 + emit 事件边），
  全量扫 ~486 文件 <10s，落盘缓存 TTL=1h，查询时绝不重算。
- layer 由 file 路径启发式推断（organs/brain→L3，organs/*→L2，nucleus/*→L1），仅作索引提示。
- 注入/属性消解只用烛微 1.3 给出的 13 条确定映射，不硬猜别名（任务书纪律明令禁止）。
"""
from __future__ import annotations

import ast
import os
import time

from nucleus._silent_except import silent_exc
from nucleus.self_index import exporters


# ───────────── 注入属性映射表（烛微 1.3，13 条确定映射） ─────────────
# self.ATTR → 目标模块全名（属性调用消解：self.info_field.publish → InfoField.publish）
INJECT_MAP: dict[str, str] = {
    "info_field": "nucleus.field.InfoField",
    # ★493.11-6：修正 6 条错路径（任务书列 5 条，实测另有 hormones 1 条）。
    #   这些模块名同时是 call 边 `to` 与 call_graph 节点 `module` 的对齐键，
    #   写错会导致目标侧无法定位 file/line。修改后需重新落盘 module_graph.json。
    "pulse_core": "nucleus.pulse.PulseCore",              # 原 nucleus.core.PulseCore（无此文件）
    "node_pool": "nucleus.mnemosyne.PulseNodePool",       # 原 nucleus.PulseNodePool
    "knowledge_tree": "nucleus.mnemosyne.KnowledgeTree",  # 原 nucleus.knowledge.KnowledgeTree
    "qica": "nucleus.qica.QICA",
    "cortex": "organs.brain.PulseCortex",
    "lung": "organs.body.PulseLung",
    "mouth": "organs.motor.PulseMouth",                   # 原 organs.output.PulseMouth
    "inner_world": "organs.brain.PulseInnerWorld",
    "hormones": "organs.endocrine.PulseHormones",         # 原 organs.body.PulseHormones（实测补充）
    "self_awareness": "organs.identity.PulseSelfAwareness",
    "narrative_self": "organs.identity.PulseNarrativeSelf",
    "resonance_engine": "nucleus.synapsys.ResonanceEngine",  # 原 nucleus.resonance.ResonanceEngine
}

#: 493.11-6：旧（错误）模块名 → 新（正确）模块名，用于给已落盘的
#: module_graph.json 历史边做**读取期**对齐（不重写落盘文件里的 to 字段）。
INJECT_MAP_LEGACY_FIX: dict[str, str] = {
    "nucleus.core.PulseCore": "nucleus.pulse.PulseCore",
    "nucleus.PulseNodePool": "nucleus.mnemosyne.PulseNodePool",
    "nucleus.knowledge.KnowledgeTree": "nucleus.mnemosyne.KnowledgeTree",
    "organs.output.PulseMouth": "organs.motor.PulseMouth",
    "organs.body.PulseHormones": "organs.endocrine.PulseHormones",
    "nucleus.resonance.ResonanceEngine": "nucleus.synapsys.ResonanceEngine",
}

_SCAN_SUBDIRS = ("nucleus", "organs", "functions", "base")


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _py_files(root: str) -> list[str]:
    out: list[str] = []
    for sub in _SCAN_SUBDIRS:
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for dp, _dns, fns in os.walk(d):
            for fn in fns:
                if fn.endswith(".py"):
                    out.append(os.path.join(dp, fn))
    return out


def _rel_module(path: str, root: str) -> str:
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def _layer_of(rel: str) -> str:
    if rel.startswith("organs/brain"):
        return "L3"
    if rel.startswith("organs/"):
        return "L2"
    return "L1"


class _ScanVisitor(ast.NodeVisitor):
    def __init__(self, module: str):
        self.module = module
        self.imports: list[tuple[str, int]] = []                 # (to_module, line)
        self.emits: list[tuple[int, str]] = []                   # (line, event_const)
        self.subs: list[tuple[int, str]] = []                    # (line, event_const)
        self.attr_calls: list[tuple[int, str, str | None]] = []   # (line, attr, method|None)

    def visit_Import(self, node):
        for a in node.names:
            self.imports.append((a.name, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self.imports.append((node.module, node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        if node.name == "get_resonance_conditions":
            for n in ast.walk(node):
                if isinstance(n, ast.Return):
                    self._collect_event_const(n.value)
        self.generic_visit(node)

    def _collect_event_const(self, val):
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            self.subs.append((val.lineno, val.value))
        elif isinstance(val, ast.Attribute) and isinstance(val.value, ast.Name):
            self.subs.append((val.lineno, f"{val.value.id}.{val.attr}"))
        elif isinstance(val, (ast.List, ast.Tuple, ast.Set)):
            for e in val.elts:
                self._collect_event_const(e)
        elif isinstance(val, ast.Dict):
            for e in val.values:
                self._collect_event_const(e)

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("emit", "_emit"):
            if node.args:
                a0 = node.args[0]
                if isinstance(a0, ast.Attribute) and isinstance(a0.value, ast.Name):
                    self.emits.append((node.lineno, f"{a0.value.id}.{a0.attr}"))
                elif isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                    self.emits.append((node.lineno, a0.value))
        # self.ATTR(.METHOD)(...) 属性调用消解
        if isinstance(f, ast.Attribute):
            v = f.value
            if (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name)
                    and v.value.id == "self" and v.attr in INJECT_MAP):
                self.attr_calls.append((node.lineno, v.attr, f.attr))
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # 非调用形式的 self.ATTR 单独访问（注入点）
        if isinstance(node.value, ast.Name) and node.value.id == "self" and node.attr in INJECT_MAP:
            self.attr_calls.append((node.lineno, node.attr, None))
        self.generic_visit(node)


def _parse_event_classes(py_files: list[str]) -> dict[str, str]:
    """预扫事件类：ClassXxxEvent 内 ``NAME = "value"`` → ``'Class.NAME'`` → value。"""
    table: dict[str, str] = {}
    for path in py_files:
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
            tree = ast.parse(src, filename=path)
        except Exception as _e:
            silent_exc(_e, where="_parse_event_classes")
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name.endswith("Event"):
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                        t = stmt.targets[0]
                        if (isinstance(t, ast.Name) and isinstance(stmt.value, ast.Constant)
                                and isinstance(stmt.value.value, str)):
                            table[f"{node.name}.{t.id}"] = stmt.value.value
    return table


def build_module_graph(root: str | None = None) -> dict:
    root = root or _project_root()
    py_files = _py_files(root)
    event_table = _parse_event_classes(py_files)

    nodes: dict[str, dict] = {}
    edges: list[dict] = {}
    organ_by_file: dict[str, str] = {}
    try:
        inv = exporters.export_organ_inventory()
        for o in inv.get("organs", []):
            fp = o.get("file_path")
            if fp:
                organ_by_file[fp.replace("\\", "/")] = o.get("name")
    except Exception as _e:
        silent_exc(_e, where="build_module_graph::inventory")

    def ensure_node(module: str, rel: str):
        if module not in nodes:
            nodes[module] = {"module": module, "file": rel,
                             "organs": [], "layer": _layer_of(rel)}

    def norm_event(c: str) -> str:
        return event_table.get(c, c)

    parsed = []
    for path in py_files:
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
            tree = ast.parse(src, filename=path)
        except Exception as _e:
            silent_exc(_e, where="build_module_graph::parse")
            continue
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        module = _rel_module(path, root)
        ensure_node(module, rel)
        if rel in organ_by_file:
            nodes[module]["organs"].append(organ_by_file[rel])
        vis = _ScanVisitor(module)
        vis.visit(tree)
        parsed.append((module, rel, vis))

    # import 边
    for module, _rel, v in parsed:
        for to_mod, line in v.imports:
            edges.setdefault((module, to_mod, "import"),
                             {"from": module, "to": to_mod, "kind": "import",
                              "from_file": _rel, "from_line": line,
                              "to_file": None, "to_line": None, "count": 0})
            edges[(module, to_mod, "import")]["count"] += 1

    # inject + call 边（属性调用消解）
    for module, _rel, v in parsed:
        for line, attr, method in v.attr_calls:
            to_mod = INJECT_MAP[attr]
            kind = "call" if method else "inject"
            key = (module, to_mod, kind, attr, method or "")
            if key not in edges:
                edges[key] = {"from": module, "to": to_mod, "kind": kind,
                              "via": attr, "method": method,  # 493.11-6
                              "from_file": _rel, "from_line": line,
                              "to_file": None, "to_line": None, "count": 0}
            edges[key]["count"] += 1
            if to_mod not in nodes:
                nodes[to_mod] = {"module": to_mod, "file": None,
                                 "organs": [], "layer": _layer_of(to_mod.replace(".", "/"))}

    # event 边（emit × subscribe 按事件名匹配）
    emit_idx: dict[str, list[tuple[str, str, int]]] = {}
    sub_idx: dict[str, list[tuple[str, str, int]]] = {}
    for module, _rel, v in parsed:
        for line, c in v.emits:
            emit_idx.setdefault(norm_event(c), []).append((module, _rel, line))
        for line, c in v.subs:
            sub_idx.setdefault(norm_event(c), []).append((module, _rel, line))
    for ev, emiters in emit_idx.items():
        for (fm, frel, fline) in emiters:
            for (tm, trel, tline) in sub_idx.get(ev, []):
                if tm == fm:
                    continue
                key = (fm, tm, "event", ev, fline, tline)
                if key not in edges:
                    edges[key] = {"from": fm, "to": tm, "kind": "event", "event": ev,
                                  "from_file": frel, "from_line": fline,
                                  "to_file": trel, "to_line": tline, "count": 0}
                edges[key]["count"] += 1

    _out_edges = list(edges.values())
    # ★493.11-6：生成时即补 call 边目标行号（复用 graph_latest.json，不重建 call graph）
    enrich_call_edges(_out_edges)
    return {
        "version": "493.5.graph.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "nodes": list(nodes.values()),
        "edges": _out_edges,
    }


def _m49311_call_lines_on() -> bool:
    """493.11-6 开关（关 → 逐字回 493.10：call 边 to_line 全 None）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M49311_CALL_LINES", False))
    except Exception as _e:
        silent_exc(_e, where="module_graph._m49311_call_lines_on", level="debug")
        return False


def _load_call_graph_index() -> dict:
    """复用 data/call_graph/graph_latest.json（不重建 call graph）。

    返回 {(module, method_name): (file, line)}；同名方法多定义取第一个，
    用 file 与修正后的 INJECT_MAP 目标模块路径互相校验。
    """
    _idx: dict = {}
    try:
        import json
        _p = os.path.join("data", "call_graph", "graph_latest.json")
        if not os.path.isfile(_p):
            return _idx
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _g = json.load(_f)
        for _n in _g.get("nodes", []) or []:
            _mod = str(_n.get("module") or "")
            _name = str(_n.get("name") or "")
            _file, _line = _n.get("file"), _n.get("line")
            if _mod and _name and _file and _line is not None:
                _idx.setdefault((_mod, _name), (_file, int(_line)))
    except Exception as _e:
        silent_exc(_e, where="module_graph._load_call_graph_index", level="debug")
    return _idx


def _infer_call_method(edge: dict) -> str | None:
    """从边里已有的 method 字段，或 from_file:from_line 源码行解析 `self.<via>.<method>(`。"""
    _m = edge.get("method")
    if _m:
        return str(_m)
    _f, _ln, _via = edge.get("from_file"), edge.get("from_line"), edge.get("via")
    if not _f or not _ln or not _via or not os.path.isfile(_f):
        return None
    try:
        import re
        with open(_f, encoding="utf-8", errors="replace") as _fh:
            _lines = _fh.read().splitlines()
        if not (0 < int(_ln) <= len(_lines)):
            return None
        _src = _lines[int(_ln) - 1]
        _hit = re.search(r"self\." + re.escape(str(_via)) + r"\.(\w+)\s*\(", _src)
        return _hit.group(1) if _hit else None
    except Exception as _e:
        silent_exc(_e, where="module_graph._infer_call_method", level="debug")
        return None


def enrich_call_edges(edges: list) -> dict:
    """493.11-6：给 call 边补 method / to_file / to_line。

    - 命中 call_graph 节点 → 写真实 file:line（**绝不编行号**）；
    - 未命中 → 保留 to_line=None，写 ``reason="no_call_graph_node"``；
    - import / inject 边**不动**（模块级/装配级无语义）。
    """
    _stats = {"total": len(edges), "call": 0, "resolved": 0,
              "unresolved": 0, "no_method": 0}
    if not _m49311_call_lines_on():
        return _stats
    _idx = _load_call_graph_index()
    for _e in edges:
        if _e.get("kind") != "call":
            continue
        _stats["call"] += 1
        _method = _infer_call_method(_e)
        if _method:
            _e["method"] = _method
        else:
            _stats["no_method"] += 1
        _to = INJECT_MAP_LEGACY_FIX.get(str(_e.get("to") or ""), _e.get("to"))
        _hit = _idx.get((_to, _method)) if _method else None
        if _hit is None and _method:
            # 退路：按末段类名 + 方法名找（兼容 graph 里 module 前缀差异）
            _cls = str(_to).split(".")[-1]
            for (_m, _n), _v in _idx.items():
                if _m.split(".")[-1] == _cls and _n == _method:
                    _hit = _v
                    break
        if _hit:
            _e["to_file"], _e["to_line"] = _hit
            _e.pop("reason", None)
            _stats["resolved"] += 1
        else:
            _e["to_file"] = None
            _e["to_line"] = None
            _e["reason"] = "no_call_graph_node"
            _stats["unresolved"] += 1
    return _stats


def enrich_module_graph_file() -> dict:
    """一次性补齐已落盘 module_graph.json 的 call 边行号（写回原文件）。"""
    import json
    _p = os.path.join("data", "self_index", "module_graph.json")
    if not os.path.isfile(_p):
        return {"error": "module_graph.json 不存在"}
    with open(_p, encoding="utf-8", errors="replace") as _f:
        _g = json.load(_f)
    _stats = enrich_call_edges(_g.get("edges", []) or [])
    _g["call_lines_enriched"] = True
    with open(_p, "w", encoding="utf-8", newline="\n") as _f:
        json.dump(_g, _f, ensure_ascii=False, indent=1)
    return _stats


def _load_graph_from_disk() -> dict | None:
    """只读已落盘模块图（不触发重建，避免实时路径 10s 重算）。"""
    try:
        import json
        _p = os.path.join("data", "self_index", "module_graph.json")
        if os.path.isfile(_p):
            with open(_p, encoding="utf-8", errors="replace") as _f:
                return json.load(_f)
    except Exception as _e:
        silent_exc(_e, where="module_graph._load_graph_from_disk", level="debug")
    return None


def export_module_edges(module: str | None = None) -> dict:
    """493.10-5：`module.imports` 的过滤版入口。

    传 ``module``（如 ``"PulseLung"`` / ``"nucleus.field.InfoField"`` /
    ``"pulse_inner_world_support"``）→ 只返回该模块的**出边 + 入边**，
    不再吐 468 节点全图；不传 → 返回全图**摘要**（计数 + 按 kind 分桶，不含全量边）。

    返回结构恒含 ``matched``：找不到该模块 / 无任何相关边 → ``matched=False``，
    由描述符 verify（收紧型 ``module_edges``）诚实判失败，绝不放过空表。
    """
    _g = _load_graph_from_disk()
    if not _g:
        return {"module": module, "matched": False, "edges": [],
                "reason": "模块图未落盘或不可读"}
    _edges = _g.get("edges") or []
    _nodes = _g.get("nodes") or []
    if not module:
        _by_kind: dict[str, int] = {}
        for _e in _edges:
            _k = str(_e.get("kind") or "unknown")
            _by_kind[_k] = _by_kind.get(_k, 0) + 1
        return {"module": None, "matched": True, "scope": "summary",
                "node_count": len(_nodes), "edge_count": len(_edges),
                "by_kind": _by_kind, "edges": [],
                "note": "未传 module，返回全图摘要（不吐全量节点/边）"}
    _m = str(module).strip().lower()
    _terms: set = set()
    for _n in _nodes:
        _full = str(_n.get("module") or "")
        _file = str(_n.get("file") or "")
        _short = _full.split(".")[-1].lower()
        _stem = os.path.splitext(os.path.basename(_file))[0].lower()
        if (_m == _short or _m == _stem or _m == _full.lower()
                or _m in _full.lower().split(".")
                or (len(_m) >= 4 and (_m in _full.lower() or _m in _file.lower()))):
            _terms.add(_full)
    if not _terms:
        return {"module": module, "matched": False, "edges": [],
                "reason": f"模块图中未找到匹配模块: {module}"}
    _related = [_e for _e in _edges
                if _e.get("from") in _terms or _e.get("to") in _terms]
    _out = [_e for _e in _related if _e.get("from") in _terms]
    _in = [_e for _e in _related if _e.get("to") in _terms]
    return {"module": module, "matched": bool(_related), "scope": "filtered",
            "resolved_modules": sorted(_terms), "edge_count": len(_related),
            "out_edge_count": len(_out), "in_edge_count": len(_in),
            "edges": _related[:200], "truncated": len(_related) > 200,
            "version": _g.get("version")}
