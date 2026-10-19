# -*- coding: utf-8 -*-
"""
CallGraphAnalyzer.py —— PHASE18 阶段二：跨文件调用图构建（P3-3）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 静态分析全库 Python 代码的函数/方法调用关系，构建**跨文件调用图**，
      并据此计算"代码结构健康度"（孤立函数 / 热点函数 / 循环调用 / 调用深度 /
      跨文件耦合），闭环技术债务 P3-3。
机制: 两遍扫描（纯 AST，绝不执行代码）——
      Pass1 收集函数/方法定义、import 绑定表、函数体内的调用名；
      Pass2 把调用名解析为"定义位置 → 调用位置"的有向边（支持
      `import m` / `from m import f` / `import m as a` / 相对导入）。
定位: PHASE18 自我认知引擎的「代码结构健康度」维度数据源。
      ★红线：**纯只读静态分析**，不修改任何器官代码，不执行任何被分析代码。

准确性口径（刻意保守）:
    · 只分析**明确可见**的调用：`f()` / `obj.m()` / `mod.f()` / `A.m()`
    · 动态调用（`getattr()` / `eval()` / `exec()` / 装饰器内部）**不猜测**，
      统一计入 `dynamic_calls_unresolved`
    · 记录"定义位置 → 调用位置"与次数，不记录参数/返回值

性能: 全库约 16 万行 / 266 文件，实测 < 10s（解析为单次遍历 + Tarjan O(V+E)）。

开关（config）:
    ENABLE_CALL_GRAPH_ANALYSIS   总开关（默认 True；关闭时 analyze() 直接返回空图）
    CALL_GRAPH_SCAN_DIRS         扫描范围（默认 nucleus/organs/functions/base/utils/main.py）
"""

from __future__ import annotations

import ast
import json
import os
import time
from typing import Any

from nucleus.data.exclude_dirs import CALL_GRAPH_EXCLUDED  # ★第49批 T5
from nucleus.data.path_utils import (
    safe_relpath as _safe_relpath,  # ★第55批 T3（跨盘安全，同盘行为与 os.path.relpath 一致）
)
from nucleus.logger import (
    get_module_logger,
)
from nucleus.logger import (
    noise_reduction_enabled as _noise_reduce,
)

_logger = get_module_logger("CallGraphAnalyzer")


# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------
#: 不扫描的目录名（测试/临时/数据/缓存）
#  ★主线第49批 T5（P2-310）：改为从**唯一权威来源**导入（语义与原定义**逐字一致**）。
#  原定义 = SOURCE_SCAN_DIRS | {test, .venv, docs, hardware}，见
#  `nucleus/data/exclude_dirs.py::CALL_GRAPH_EXCLUDED`。
_EXCLUDE_DIRS = CALL_GRAPH_EXCLUDED
#: 不扫描的目录名前缀（备份目录如 .bak_batchN / .bak_mainlineN）
_EXCLUDE_DIR_PREFIXES = (".bak", ".")

#: 默认扫描范围（相对项目根）
_DEFAULT_SCAN_DIRS = ("nucleus", "organs", "functions", "base", "utils",
                      "somatics", "main.py")

#: Python 魔术方法（不作为"孤立函数"计入 —— 由解释器/框架调用）
_MAGIC_ALLOW = frozenset((
    "__init__", "__new__", "__call__", "__enter__", "__exit__", "__iter__",
    "__next__", "__len__", "__str__", "__repr__", "__eq__", "__hash__",
    "__getitem__", "__setitem__", "__delitem__", "__contains__", "__bool__",
    "__add__", "__sub__", "__mul__", "__truediv__", "__lt__", "__le__",
    "__gt__", "__ge__", "__getattr__", "__setattr__", "__delattr__",
    "__getattribute__", "__post_init__", "__init_subclass__", "__class_getitem__",
))
#: 入口/回调类函数名（可能由框架/外部调用，孤立时归入"公共 API"）
_ENTRY_LIKE = frozenset((
    "main", "run", "start", "stop", "setup", "teardown", "on_pulse",
    "handler", "callback", "analyze", "scan", "report", "generate_report",
))

#: 调用图中代表"模块级调用"的虚拟调用者后缀
_MODULE_SCOPE = "<module>"

# ★口径修正：内置函数名（属于 Python 运行时，不在项目调用图内）
_BUILTIN_FUNCS = frozenset((
    "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
    "bytes", "bytearray", "memoryview", "frozenset", "complex", "object",
    "print", "open", "input", "repr", "format", "id", "hash", "vars", "dir",
    "isinstance", "issubclass", "getattr", "setattr", "hasattr", "delattr",
    "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "sum", "min", "max", "abs", "round", "any", "all", "iter", "next",
    "type", "super", "callable", "compile", "exec", "eval", "globals",
    "locals", "divmod", "pow", "chr", "ord", "hex", "oct", "bin", "slice",
    "staticmethod", "classmethod", "property", "breakpoint", "exit", "quit",
))

# ★口径修正：常见标准库顶层模块名（属于 Python 生态，不在项目调用图内）
_STDLIB_TOPS = frozenset((
    "os", "sys", "io", "re", "json", "time", "math", "random", "logging",
    "threading", "subprocess", "collections", "itertools", "functools",
    "pathlib", "typing", "dataclasses", "datetime", "shutil", "tempfile",
    "hashlib", "uuid", "ast", "inspect", "traceback", "socket", "base64",
    "csv", "sqlite3", "pickle", "copy", "enum", "abc", "contextlib",
    "warnings", "signal", "multiprocessing", "concurrent", "asyncio",
    "queue", "struct", "codecs", "gzip", "zipfile", "tarfile", "urllib",
    "http", "email", "smtplib", "ssl", "argparse", "unittest", "statistics",
    "decimal", "fractions", "array", "bisect", "heapq", "weakref", "gc",
    "platform", "getpass", "glob", "fnmatch", "textwrap", "string",
    "operator", "types", "importlib", "pkgutil", "atexit", "ctypes",
    "posixpath", "ntpath", "genericpath",
))

#: 健康度阈值
_ISOLATED_RATIO_WARN = 0.10      # 孤立占比 > 10% 视为偏高
_SUPER_FUNCTION_CALLS = 100      # 被调用 > 100 次视为"超级函数"
_DEEP_CHAIN_WARN = 15            # 最深链 > 15 层视为"过深"

#: 循环检测 / 深度计算的安全上限（防栈溢出与病态图）
_MAX_SCC_NODES = 20000
_MAX_DEPTH_LIMIT = 200


# ----------------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------------
def _scan_enabled() -> bool:
    """读取总开关（默认 True）。config 不可用时按开启处理。"""
    try:
        from config import ENABLE_CALL_GRAPH_ANALYSIS  # type: ignore[attr-defined]
        return bool(ENABLE_CALL_GRAPH_ANALYSIS)
    except Exception as e:  # 配置读取失败按默认开启
        _logger.debug("读取 ENABLE_CALL_GRAPH_ANALYSIS 失败，按开启处理: %s: %s",
                      type(e).__name__, e)
        return True


def _scan_dirs() -> list:
    """读取扫描范围（默认 _DEFAULT_SCAN_DIRS）。"""
    try:
        from config import CALL_GRAPH_SCAN_DIRS  # type: ignore[attr-defined]
        if isinstance(CALL_GRAPH_SCAN_DIRS, (list, tuple)) and CALL_GRAPH_SCAN_DIRS:
            return [str(x) for x in CALL_GRAPH_SCAN_DIRS]
    except Exception as e:
        _logger.debug("读取 CALL_GRAPH_SCAN_DIRS 失败，用默认范围: %s: %s",
                      type(e).__name__, e)
    return list(_DEFAULT_SCAN_DIRS)


def _project_root() -> str:
    """由本文件位置推导项目根（nucleus/self_awareness/ → 上溯 3 层）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dotted(node: Any) -> str:
    """把 `ast.Name` / `ast.Attribute` 链还原为点号字符串（如 `a.b.c`）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        _base = _dotted(node.value)
        return (f"{_base}.{node.attr}") if _base else node.attr
    return ""


def _iter_py_files(project_root: str, scan_dirs: list) -> list:
    """遍历扫描范围内所有 .py 文件，返回相对路径列表（已排序）。"""
    _out: list = []
    for _d in scan_dirs:
        _p = os.path.join(project_root, _d.replace("/", os.sep))
        if os.path.isfile(_p) and _p.endswith(".py"):
            _out.append(_safe_relpath(_p, project_root).replace(os.sep, "/"))
            continue
        if not os.path.isdir(_p):
            continue
        for _dp, _dirs, _files in os.walk(_p):
            _dirs[:] = [x for x in _dirs
                        if x not in _EXCLUDE_DIRS
                        and not x.startswith(_EXCLUDE_DIR_PREFIXES)]
            for _fn in _files:
                if _fn.endswith(".py"):
                    _out.append(_safe_relpath(os.path.join(_dp, _fn), project_root)
                                .replace(os.sep, "/"))
    return sorted(set(_out))


def _module_id(rel_path: str) -> str:
    """`a/b/c.py` → `a.b.c`；`a/b/__init__.py` → `a.b`。"""
    _p = rel_path.replace("\\", "/")
    _p = _p.removesuffix(".py")
    _p = _p.removesuffix("/__init__")
    return _p.replace("/", ".")


def _qual_id(module: str, qualname: str) -> str:
    """组装节点 id：`module:qualname`。"""
    return f"{module}:{qualname}"

# ----------------------------------------------------------------------
# ★第306批 刀1：事件枚举规范化 + 注册实参还原
# ----------------------------------------------------------------------
#: 事件枚举成员表（`MouthEvent.REPLY` → `"mouth.reply"`），模块级缓存。
_EVENT_TAXONOMY: dict | None = None


def _event_taxonomy() -> dict:
    """静态解析 `nucleus/const.py` 的 `*Event` 类，产出「成员全名 → 事件字符串」。

    ★为什么必须做：发/订两侧的事件名在源码里大多写成枚举成员
    （`self.pulse_core.emit(event_type=MouthEvent.REPLY)` 与
    `"event_types": [MouthEvent.REPLY]`）。不做规范化则两侧字符串不可比，
    event 边永远为 0 —— 这正是 305 批「分色落地但 event/entry 无写入路径」的根因。
    纯 AST 解析，不 import、不执行；解析失败按空表处理（退化为字面量口径）。
    """
    global _EVENT_TAXONOMY
    if _EVENT_TAXONOMY is not None:
        return _EVENT_TAXONOMY
    _out: dict = {}
    _const = os.path.join(_project_root(), "nucleus", "const.py")
    try:
        with open(_const, encoding="utf-8", errors="replace") as f:
            _tree = ast.parse(f.read())
        for _node in _tree.body:
            if not isinstance(_node, ast.ClassDef):
                continue
            if not (_node.name.endswith("Event") or _node.name == "Event"):
                continue
            for _a in _node.body:
                if (isinstance(_a, ast.Assign) and len(_a.targets) == 1
                        and isinstance(_a.targets[0], ast.Name)
                        and isinstance(_a.value, ast.Constant)
                        and isinstance(_a.value.value, str)):
                    _out["%s.%s" % (_node.name, _a.targets[0].id)] = _a.value.value
    except Exception as e:
        _logger.debug("事件枚举表解析失败（事件边退化到字面量口径）: %s: %s",
                      type(e).__name__, e)
    _EVENT_TAXONOMY = _out
    return _out


def _canon_event(node: Any) -> str:
    """把事件类型 AST 节点还原为规范化字符串；无法解析 ⇒ 空串（不猜测）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return _event_taxonomy().get("%s.%s" % (node.value.id, node.attr), "")
    return ""


def _callable_ref(node: Any) -> str:
    """把注册调用的「可调用实参」还原为点号路径（供 entry 边解析）。

    支持 `f` / `self.f` / `Cls.f` / `mod.f`，以及 `lambda ...: self.f(...)`
    ——注册条件时几乎都用 lambda 适配签名（`handler=lambda p: self._on_x(p)`）。
    """
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _dotted(node)
    if isinstance(node, ast.Lambda) and isinstance(node.body, ast.Call):
        return _dotted(node.body.func)
    return ""


# ----------------------------------------------------------------------
# Pass1 收集器
# ----------------------------------------------------------------------
class _Collector(ast.NodeVisitor):
    """单文件收集：定义表、import 绑定表、函数体内调用名。"""

    def __init__(self, rel_file: str, module: str) -> None:
        self.rel_file = rel_file
        self.module = module
        self.defs: list = []            # [{id,name,qualname,cls,module,file,line,type}]
        self.bindings: dict = {}        # alias -> 目标（模块名 或 模块.符号）
        self.raw_calls: list = []       # [{caller, parts, line, in_class}]
        # ★第306批刀1：反射证据三表（emit 事件 / 订阅声明 / 注册入口）
        self.emits: list = []           # [{caller_qual, cls, event, line}]
        self.subs: list = []            # [{parts, cls, qual, event, line}]
        self.entries: list = []         # [{caller_qual, cls, parts, line}]
        self.name_tables: list = []     # 字符串键→可调用体（按名派发表）
        self._scope: list = []          # [(kind, name)]  kind in {"class","func"}

    # ---------------- 定义 ----------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(("class", node.name))
        self.generic_visit(node)
        self._scope.pop()

    def _visit_func(self, node: Any) -> None:
        _cls = ".".join(x[1] for x in self._scope if x[0] == "class")
        _qual = (f"{_cls}.{node.name}") if _cls else node.name
        self.defs.append({
            "id": _qual_id(self.module, _qual),
            "name": node.name,
            "qualname": _qual,
            "cls": _cls,
            "module": self.module,
            "file": self.rel_file,
            "line": node.lineno,
            "type": "method" if _cls else "function",
        })
        self._scope.append(("func", node.name))
        self.generic_visit(node)
        self._collect_roles(node, _qual, _cls)
        self._scope.pop()

    def _collect_roles(self, node: Any, qual: str, cls: str) -> None:
        """★第306批刀1：从函数体提取「订阅什么事件」，落 `self.subs`。

        两个权威来源（与 `tools/export_event_graph.py` 同源判据）：
          1. `get_resonance_conditions()` 返回 dict 的 `event_types` 列表
             ⇒ 订阅方是**本类的 on_pulse**（该方法的处理器身份）；
          2. `on_pulse()` 体内 `event_type == X` / `in (...)` 判定
             ⇒ 订阅方就是 on_pulse 自身。
        `event_types=["*"]` 记通配（该器官对全部事件可见）。
        """
        if not cls:
            return
        _handler = ["self", "on_pulse"]
        if node.name == "get_resonance_conditions":
            for _d in ast.walk(node):
                if not isinstance(_d, ast.Dict):
                    continue
                for _k, _v in zip(_d.keys, _d.values):
                    if not (isinstance(_k, ast.Constant) and _k.value == "event_types"):
                        continue
                    for _elt in getattr(_v, "elts", []):
                        if isinstance(_elt, ast.Constant) and _elt.value == "*":
                            self.subs.append({"parts": _handler, "cls": cls, "qual": qual,
                                              "event": "*", "line": _d.lineno})
                            continue
                        _e = _canon_event(_elt)
                        if _e:
                            self.subs.append({"parts": _handler, "cls": cls, "qual": qual,
                                              "event": _e, "line": _d.lineno})
        elif node.name == "on_pulse":
            _ev_var = "event_type"
            for _a in ast.walk(node):
                if (isinstance(_a, ast.Assign) and len(_a.targets) == 1
                        and isinstance(_a.targets[0], ast.Name)):
                    _val = _a.value
                    _is_get = (isinstance(_val, ast.Call)
                               and isinstance(_val.func, ast.Attribute)
                               and _val.func.attr == "get" and _val.args
                               and isinstance(_val.args[0], ast.Constant)
                               and _val.args[0].value == "event_type")
                    _is_sub = (isinstance(_val, ast.Subscript)
                               and isinstance(getattr(_val, "slice", None), ast.Constant)
                               and getattr(_val.slice, "value", None) == "event_type")
                    if _is_get or _is_sub:
                        _ev_var = _a.targets[0].id
            for _cmp in ast.walk(node):
                if not (isinstance(_cmp, ast.Compare) and isinstance(_cmp.left, ast.Name)
                        and _cmp.left.id == _ev_var):
                    continue
                for _op, _c in zip(_cmp.ops, _cmp.comparators):
                    if not isinstance(_op, (ast.Eq, ast.In, ast.Is)):
                        continue
                    _cands = (_c.elts if isinstance(_c, (ast.Tuple, ast.List, ast.Set))
                              else [_c])
                    for _cc in _cands:
                        _e = _canon_event(_cc)
                        if _e:
                            self.subs.append({"parts": _handler, "cls": cls, "qual": qual,
                                              "event": _e, "line": _cmp.lineno})

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def _sniff_event_and_entry(self, node: ast.Call, text: str) -> None:
        """★第306批刀1：识别两类反射证据 —— 事件发射 / 注册入口。

        · 发射：`self.pulse_core.emit(event_type=...)` → `self.emits`
        · 入口：`*register*(...)` / `add_daily_hook(...)` 等注册调用的可调用实参
          → `self.entries`（同时把注册条件带的 `event_types` 记进 `self.subs`）
        """
        _fn = next((x[1] for x in reversed(self._scope) if x[0] == "func"), "")
        _cls = ".".join(x[1] for x in self._scope if x[0] == "class")
        _caller = ("%s.%s" % (_cls, _fn)) if _cls else _MODULE_SCOPE
        _parts = text.split(".") if text else []
        _attr = _parts[-1] if _parts else ""
        if not _attr:
            return
        # ---- ① 事件发射 ----
        if (_attr == "emit" and len(_parts) >= 2 and _parts[-2] == "pulse_core"):
            _et = None
            for _kw in node.keywords:
                if _kw.arg == "event_type":
                    _et = _kw.value
                    break
            if _et is None and len(node.args) >= 2:
                _et = node.args[1]
            _ev = _canon_event(_et) if _et is not None else ""
            if _ev:
                self.emits.append({"caller_qual": _caller, "cls": _cls,
                                   "event": _ev, "line": node.lineno})
            return
        # ---- ② 注册入口（含注册条件式事件订阅） ----
        _reg_like = (("register" in _attr) or _attr in (
            "add_daily_hook", "add_handler", "add_callback", "subscribe", "attach"))
        if not _reg_like:
            return
        _evs: list = []
        _ref = None
        for _kw in node.keywords:
            if _kw.arg == "event_types" and isinstance(
                    _kw.value, (ast.List, ast.Tuple, ast.Set)):
                for _elt in _kw.value.elts:
                    if isinstance(_elt, ast.Constant) and _elt.value == "*":
                        _evs.append("*")
                        continue
                    _e = _canon_event(_elt)
                    if _e:
                        _evs.append(_e)
            elif _kw.arg in ("handler", "callback", "fn", "func", "target"):
                _r = _callable_ref(_kw.value)
                if _r:
                    _ref = _r
        if _ref is None:
            # 位置实参：取**最后一个**可调用引用（前面多是名字/类型字符串）
            for _a in reversed(node.args):
                if isinstance(_a, (ast.Name, ast.Attribute, ast.Lambda)):
                    _r = _callable_ref(_a)
                    if _r:
                        _ref = _r
                        break
        if not _ref:
            return
        _ref_parts = _ref.split(".")
        self.entries.append({"caller_qual": _caller, "cls": _cls,
                             "parts": _ref_parts, "line": node.lineno})
        for _e in _evs:
            self.subs.append({"parts": _ref_parts, "cls": _cls, "qual": _ref,
                              "event": _e, "line": node.lineno})

    def visit_Dict(self, node: ast.Dict) -> None:
        """★第306批刀1：字符串键 → 可调用体 的「按名派发表」也算反射证据。

        形态：`{"refresh": self._do_refresh, "reset": self._do_reset}`
        （键是字符串名、值是 `self.x` / 裸函数名）。这是与
        `getattr(x, "y")()` 同源的按名派发，但 305 的 byname 判据只认后者，
        全库仅 17 处 ⇒ 按名边永远是个位数。
        """
        _fn = next((x[1] for x in reversed(self._scope) if x[0] == "func"), "")
        _cls = ".".join(x[1] for x in self._scope if x[0] == "class")
        if not _fn or not node.keys or len(node.keys) < 2:
            self.generic_visit(node)
            return
        _ok_keys = 0
        _targets: list = []
        for _k, _v in zip(node.keys, node.values):
            if isinstance(_k, ast.Constant) and isinstance(_k.value, str):
                _ok_keys += 1
            if isinstance(_v, ast.Attribute) and isinstance(_v.value, ast.Name) \
                    and _v.value.id == "self":
                _targets.append(_dotted(_v))
            elif isinstance(_v, ast.Name):
                _targets.append(_v.id)
        if _ok_keys >= 2 and len(_targets) == len(node.keys) and _cls:
            self.name_tables.append({
                "caller_qual": "%s.%s" % (_cls, _fn),
                "cls": _cls,
                "parts_list": [x.split(".") for x in _targets],
                "line": node.lineno,
            })
        self.generic_visit(node)

    # ---------------- import ----------------
    def visit_Import(self, node: ast.Import) -> None:
        for _a in node.names:
            if _a.asname:
                self.bindings[_a.asname] = _a.name
            else:
                # `import a.b.c` → 顶层名 `a` 绑定；同时登记全名便于 `a.b.c.f()` 解析
                _top = _a.name.split(".")[0]
                self.bindings.setdefault(_top, _top)
                self.bindings.setdefault(_a.name, _a.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        _level = int(getattr(node, "level", 0) or 0)
        _base = node.module or ""
        if _level:
            # 相对导入：`from .mod import x`（level=1 表示当前包）
            _pkg = self.module.split(".")
            _keep = len(_pkg) - _level
            if _keep > 0:
                _base = ".".join(_pkg[:_keep] + ([_base] if _base else []))
            else:
                pass  # _base 已为 node.module（L256），无需改写
        for _a in node.names:
            if _a.name == "*":
                continue
            _alias = _a.asname or _a.name
            self.bindings[_alias] = (f"{_base}.{_a.name}") if _base else _a.name

    # ---------------- 调用 ----------------
    def visit_Call(self, node: ast.Call) -> None:
        _text = _dotted(node.func)
        self._sniff_event_and_entry(node, _text)
        # ★动态调用：func 不是 Name/Attribute 链 —— `getattr(x, "y")()`、
        #   `d["k"]()`、`f()()` 等，无法静态解析，统一计入 dynamic_calls，
        #   **不做猜测**（任务书 §2.2 口径）。
        if not _text:
            if isinstance(node.func, (ast.Call, ast.Subscript)):
                _fn0 = next((x[1] for x in reversed(self._scope)
                             if x[0] == "func"), "")
                _cls0 = ".".join(x[1] for x in self._scope if x[0] == "class")
                # ★第305批刀2：按名派发识别 —— `getattr(x, "y")` 的**字面名** ⇒ kind="byname"
                #   （304 刀1 的分色只在 dump 时用正则数了个数，边本身没有 kind ⇒ 判据拿不到
                #    「反射可达」这张图，只能退回正则小集兜命。本批让 kind 落到边上。）
                _kind = "dynamic"
                _attr = ""
                if (isinstance(node.func, ast.Call) and isinstance(node.func.func, ast.Name)
                        and node.func.func.id == "getattr" and len(node.func.args) >= 2
                        and isinstance(node.func.args[1], ast.Constant)
                        and isinstance(node.func.args[1].value, str)):
                    _kind = "byname"
                    _attr = str(node.func.args[1].value)
                self.raw_calls.append({
                    "caller_qual": ((f"{_cls0}.{_fn0}") if _cls0 else _fn0)
                    or _MODULE_SCOPE,
                    "parts": [],
                    "line": node.lineno,
                    "in_class": _cls0,
                    "dynamic": True,
                    "kind": _kind,
                    "attr_name": _attr,
                })
            self.generic_visit(node)
            return
        _fn = next((x[1] for x in reversed(self._scope) if x[0] == "func"), "")
        _cls = ".".join(x[1] for x in self._scope if x[0] == "class")
        if _fn:
            _caller_qual = (f"{_cls}.{_fn}") if _cls else _fn
        else:
            _caller_qual = _MODULE_SCOPE
        self.raw_calls.append({
            "caller_qual": _caller_qual,
            "parts": _text.split("."),
            "line": node.lineno,
            "in_class": _cls,
        })
        self.generic_visit(node)


# ----------------------------------------------------------------------
# 主类
# ----------------------------------------------------------------------
class CallGraphAnalyzer:
    """跨文件调用图构建器（纯静态）。"""

    def __init__(self, project_root: str | None = None,
                 scan_dirs: list | None = None) -> None:
        self.project_root = os.path.abspath(project_root or _project_root())
        self.scan_dirs = list(scan_dirs) if scan_dirs else _scan_dirs()
        # ---- 状态 ----
        self._defs: dict = {}            # id -> def dict
        self._by_module: dict = {}       # module -> {qualname: id}
        self._by_module_top: dict = {}   # module -> {顶层名: id}
        self._bindings: dict = {}        # rel_file -> {alias: 目标}
        self._graph: dict = {}           # caller_id -> {callee_id: count}
        self._in_deg: dict = {}          # callee_id -> 被调用次数
        self._caller_module: dict = {}   # id -> module
        self._raw_calls: list = []
        # ★第306批刀1：反射证据（跨文件累积，Pass2 统一解析成 event / entry 边）
        self._raw_emits: list = []
        self._raw_subs: list = []
        self._raw_entries: list = []
        self._raw_tables: list = []
        self._parse_errors: list = []
        self._graph_data: dict = {}      # analyze() 结果缓存
        self._unresolved = 0
        self._cross_file = 0
        self._total_calls = 0
        #: 显式动态调用（getattr(...)() / x[k]() / f()() 等，静态不可解析）
        self._dynamic = 0
        #: 项目外调用（内置函数 / 标准库 / 第三方）—— 本就不属于项目调用图
        self._external = 0

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def analyze(self) -> dict:
        """执行全量分析，返回调用图数据（nodes / edges / stats）。"""
        if not _scan_enabled():
            _logger.debug("调用图分析开关关闭，返回空图")
            self._graph_data = {"nodes": [], "edges": [],
                                "stats": {"total_files": 0, "total_functions": 0,
                                          "total_calls": 0, "cross_file_calls": 0,
                                          "dynamic_calls_unresolved": 0,
                                          "disabled": True}}
            return self._graph_data

        _t0 = time.perf_counter()
        _files = _iter_py_files(self.project_root, self.scan_dirs)
        self._reset()
        _collectors: list = []
        for _rel in _files:
            _c = self._collect_file(_rel)
            if _c is not None:
                _collectors.append(_c)
            else:
                self._parse_errors.append(_rel)
        self._pass2_resolve(_collectors)
        _nodes = [dict(v) for v in self._defs.values()]
        _edges = []
        for _caller, _targets in self._graph.items():
            for _callee, _cnt in _targets.items():
                _edges.append({"caller": _caller, "callee": _callee, "count": int(_cnt),
                               "kind": self._edge_kinds.get((_caller, _callee), "static")})
        _elapsed_ms = round((time.perf_counter() - _t0) * 1000.0, 2)
        self._graph_data = {
            "nodes": _nodes,
            "edges": _edges,
            "stats": {
                "total_files": len(_files),
                "parsed_files": len(_collectors),
                "total_functions": len(_nodes),
                "total_calls": int(self._total_calls),
                "cross_file_calls": int(self._cross_file),
                # 任务书字段名保留：动态 + 项目内未解析（「需人工关注」口径）
                "dynamic_calls_unresolved": int(self._unresolved + self._dynamic),
                "dynamic_calls": int(self._dynamic),
                "unresolved_calls": int(self._unresolved),
                # ★口径修正：项目外调用单列（内置/标准库/第三方）
                "external_calls": int(self._external),
                "parse_errors": len(self._parse_errors),
                "elapsed_ms": _elapsed_ms,
            },
            "parse_error_files": list(self._parse_errors[:20]),
        }
        _logger.info("调用图构建完成: %d 文件 / %d 函数 / %d 调用（跨文件 %d）耗时 %.2fs",
                     len(_files), len(_nodes), self._total_calls,
                     self._cross_file, _elapsed_ms / 1000.0)
        return self._graph_data

    # ------------------------------------------------------------------
    # Pass1 / Pass2
    # ------------------------------------------------------------------
    def _reset(self) -> None:
        for _d in (self._defs, self._by_module, self._by_module_top,
                   self._bindings, self._graph, self._in_deg,
                   self._caller_module):
            _d.clear()
        # ★第305批刀2：边类型表（(caller, callee) -> kind: static/byname/event/entry）
        self._edge_kinds = {}
        self._raw_calls = []
        self._raw_emits = []
        self._raw_subs = []
        self._raw_entries = []
        self._raw_tables = []
        self._parse_errors = []
        self._unresolved = 0
        self._cross_file = 0
        self._total_calls = 0
        self._dynamic = 0
        self._external = 0

    def _collect_file(self, rel: str) -> Any:
        """解析单个文件，产出 `_Collector`；解析失败返回 None 并记 WARNING。"""
        _abs = os.path.join(self.project_root, rel.replace("/", os.sep))
        _module = _module_id(rel)
        try:
            with open(_abs, encoding="utf-8", errors="replace") as f:
                _src = f.read()
            _tree = ast.parse(_src, filename=rel)
        except SyntaxError as e:
            # ★第22批 T5/P2-123：语法错误跳过属**预期行为**（测试用例 bad/broken.py
            #   就是用来验证错误处理路径的），降为 DEBUG；关闭开关时保持 WARNING。
            (_logger.debug if _noise_reduce() else _logger.warning)(
                "调用图分析：语法错误跳过 %s (line %s): %s",
                rel, getattr(e, "lineno", "?"), e)
            return None
        except Exception as e:  # 单文件失败不应中断全库分析
            # ★第22批 T5/P2-123：同上，单文件解析失败属可预期的跳过路径 → DEBUG。
            (_logger.debug if _noise_reduce() else _logger.warning)(
                "调用图分析：解析失败跳过 %s: %s: %s", rel, type(e).__name__, e)
            return None
        _c = _Collector(rel, _module)
        try:
            _c.visit(_tree)
        except RecursionError:
            # ★第22批 T5/P2-123：单个文件 AST 过深 → 跳过，属可预期路径 → DEBUG。
            (_logger.debug if _noise_reduce() else _logger.warning)(
                "调用图分析：%s AST 递归过深，跳过", rel)
            return None
        except Exception as e:
            # ★第22批 T5/P2-123：同上，遍历失败跳过 → DEBUG。
            (_logger.debug if _noise_reduce() else _logger.warning)(
                "调用图分析：%s 遍历失败: %s: %s", rel, type(e).__name__, e)
            return None
        # 登记
        self._bindings[rel] = dict(_c.bindings)
        _top: dict = {}
        for _d in _c.defs:
            if _d["id"] in self._defs:
                continue
            self._defs[_d["id"]] = _d
            self._by_module.setdefault(_d["module"], {})[_d["qualname"]] = _d["id"]
            if "." not in _d["qualname"]:
                _top[_d["name"]] = _d["id"]
            self._caller_module[_d["id"]] = _d["module"]
        self._by_module_top[_module] = _top
        self._raw_calls.extend(
            [dict(x, file=rel, module=_module) for x in _c.raw_calls])
        self._raw_emits.extend(
            [dict(x, file=rel, module=_module) for x in _c.emits])
        self._raw_subs.extend(
            [dict(x, file=rel, module=_module) for x in _c.subs])
        self._raw_entries.extend(
            [dict(x, file=rel, module=_module) for x in _c.entries])
        self._raw_tables.extend(
            [dict(x, file=rel, module=_module) for x in _c.name_tables])
        return _c

    def _resolve_by_name(self, name: str, caller_module: str,
                         caller_class: str = "", caller_file: str = "") -> str:
        """把 `getattr(x, "name")` 的字面名解析为图节点 id（作用域优先消歧）。

        ★第306批刀1：305 的「多义即放弃」让 byname 边只剩 3 条——绝大多数字面名
        在库里都有多个同名方法（`refresh` / `_on_pulse` / `update` …），一律放弃等于
        把反射可达整片抹掉。改为**逐级收窄的作用域优先**：
            同类（`Cls.name`）→ 同模块 → 同目录 → 全库唯一；仍多义 ⇒ 空串
            （记 unresolved，**不猜测**——口径纪律不变）。
        """
        if not name:
            return ""
        _hits: list[str] = []
        for _nid, _d in self._defs.items():
            _qn = str(_d.get("qualname") or "")
            if _qn.split(".")[-1] == name or str(_d.get("name") or "") == name:
                _hits.append(_nid)
        if not _hits:
            return ""
        if len(_hits) == 1:
            return _hits[0]
        # ① 同类
        if caller_class:
            _want = "%s.%s" % (caller_class, name)
            _same_cls = [_h for _h in _hits
                         if str((self._defs.get(_h) or {}).get("qualname") or "") == _want]
            if len(_same_cls) == 1:
                return _same_cls[0]
        # ② 同模块
        _same = [_h for _h in _hits
                 if str((self._defs.get(_h) or {}).get("module") or "") == caller_module]
        if len(_same) == 1:
            return _same[0]
        # ③ 同目录
        if caller_file:
            _dir = caller_file.replace("\\", "/").rsplit("/", 1)[0]
            _same_dir = [
                _h for _h in _hits
                if str((self._defs.get(_h) or {}).get("file") or ""
                       ).replace("\\", "/").rsplit("/", 1)[0] == _dir]
            if len(_same_dir) == 1:
                return _same_dir[0]
        return ""

    def _pass2_resolve(self, collectors: list) -> None:
        """把原始调用名解析为边。"""
        _module_names = set(self._by_module.keys())
        for _rc in self._raw_calls:
            self._total_calls += 1
            if _rc.get("dynamic"):
                self._dynamic += 1
                # ★第305批刀2：按名派发**不再一律丢弃** —— getattr 字面名能解析到方法就建
                #   kind="byname" 的边（此前的边只有静态调用，反射可达在图上完全不可见）。
                if (_rc.get("kind") == "byname" and _rc.get("attr_name")):
                    _hit = self._resolve_by_name(
                        _rc.get("attr_name"), _rc.get("module", ""),
                        caller_class=_rc.get("in_class", ""),
                        caller_file=_rc.get("file", ""))
                    if _hit:
                        _c_mod = _rc.get("module", "")
                        _c_qual = _rc.get("caller_qual") or _MODULE_SCOPE
                        _c_id = _qual_id(_c_mod, _c_qual)
                        if _c_id not in self._defs:
                            _c_id = f"{_c_mod}:{_MODULE_SCOPE}"
                        if _hit != _c_id:
                            _t = self._graph.setdefault(_c_id, {})
                            _t[_hit] = _t.get(_hit, 0) + 1
                            self._in_deg[_hit] = self._in_deg.get(_hit, 0) + 1
                            self._edge_kinds[(_c_id, _hit)] = "byname"
                continue
            _callee = self._resolve(_rc, _module_names)
            if not _callee:
                # ★口径修正：区分「项目外调用」与「项目内疑似未解析」
                if self._is_external(_rc, _module_names):
                    self._external += 1
                else:
                    self._unresolved += 1
                continue
            _caller_mod = _rc.get("module", "")
            _caller_qual = _rc.get("caller_qual") or _MODULE_SCOPE
            _caller_id = _qual_id(_caller_mod, _caller_qual)
            if _caller_id not in self._defs:
                # 模块级调用：用虚拟调用者，不入 nodes
                _caller_id = f"{_caller_mod}:{_MODULE_SCOPE}"
            if _callee == _caller_id:
                continue                      # 自调用不计边（避免噪声）
            _tgt = self._graph.setdefault(_caller_id, {})
            _tgt[_callee] = _tgt.get(_callee, 0) + 1
            self._in_deg[_callee] = self._in_deg.get(_callee, 0) + 1
            # ★第305批刀2：静态边也记 kind（默认 static），与 byname/event 统一到边上
            self._edge_kinds[(_caller_id, _callee)] = _rc.get("kind") or "static"
            if self._callee_module(_callee) != _caller_mod:
                self._cross_file += 1
        # ★第306批刀1：静态解析完成后补两类反射边（event / entry）
        self._pass2_reflect(_module_names)

    def _pass2_reflect(self, module_names: set) -> None:
        """把 emit / 订阅 / 注册三类反射证据解析成边。

        `event` 边 = 发射点 → 订阅处理器（按事件名连，跨器官 Y→T→X 的方法级投影）；
        `entry` 边 = 注册点 → 被注册的处理器（总线/调度器/分析器注册入口）。
        两类都不猜测：事件名解析不出、处理器定位不到 ⇒ 不建边。
        """
        # ---------- 订阅索引：event -> [handler_id]，另有通配订阅者 ----------
        _sub_index: dict = {}
        _wild: list = []
        for _s in self._raw_subs:
            _ev = _s.get("event") or ""
            if not _ev:
                continue
            _rc = {"parts": _s.get("parts") or [], "module": _s.get("module", ""),
                   "file": _s.get("file", ""), "in_class": _s.get("cls", "")}
            _hid = self._resolve(_rc, module_names)
            if not _hid:
                continue
            if _ev == "*":
                _wild.append(_hid)
                continue
            _lst = _sub_index.setdefault(_ev, [])
            if _hid not in _lst:
                _lst.append(_hid)
        # ---------- event 边 ----------
        for _e in self._raw_emits:
            _ev = _e.get("event") or ""
            if not _ev:
                continue
            _caller_id = _qual_id(_e.get("module", ""),
                                  _e.get("caller_qual") or _MODULE_SCOPE)
            if _caller_id not in self._defs:
                _caller_id = "%s:%s" % (_e.get("module", ""), _MODULE_SCOPE)
            for _hid in list(_sub_index.get(_ev, [])) + _wild:
                if _hid == _caller_id:
                    continue
                _t = self._graph.setdefault(_caller_id, {})
                _t[_hid] = _t.get(_hid, 0) + 1
                self._in_deg[_hid] = self._in_deg.get(_hid, 0) + 1
                self._edge_kinds[(_caller_id, _hid)] = "event"
        # ---------- 按名派发表（byname） ----------
        for _tbl in self._raw_tables:
            _caller_id = _qual_id(_tbl.get("module", ""),
                                  _tbl.get("caller_qual") or _MODULE_SCOPE)
            if _caller_id not in self._defs:
                _caller_id = "%s:%s" % (_tbl.get("module", ""), _MODULE_SCOPE)
            for _parts in (_tbl.get("parts_list") or []):
                _hid = self._resolve({"parts": _parts, "module": _tbl.get("module", ""),
                                      "file": _tbl.get("file", ""),
                                      "in_class": _tbl.get("cls", "")}, module_names)
                if not _hid or _hid == _caller_id:
                    continue
                _t = self._graph.setdefault(_caller_id, {})
                _t[_hid] = _t.get(_hid, 0) + 1
                self._in_deg[_hid] = self._in_deg.get(_hid, 0) + 1
                self._edge_kinds[(_caller_id, _hid)] = "byname"
        # ---------- entry 边 ----------
        for _r in self._raw_entries:
            _rc = {"parts": _r.get("parts") or [], "module": _r.get("module", ""),
                   "file": _r.get("file", ""), "in_class": _r.get("cls", "")}
            _hid = self._resolve(_rc, module_names)
            if not _hid:
                continue
            _caller_id = _qual_id(_r.get("module", ""),
                                  _r.get("caller_qual") or _MODULE_SCOPE)
            if _caller_id not in self._defs:
                _caller_id = "%s:%s" % (_r.get("module", ""), _MODULE_SCOPE)
            if _hid == _caller_id:
                continue
            _t = self._graph.setdefault(_caller_id, {})
            _t[_hid] = _t.get(_hid, 0) + 1
            self._in_deg[_hid] = self._in_deg.get(_hid, 0) + 1
            self._edge_kinds[(_caller_id, _hid)] = "entry"

    def _is_external(self, rc: dict, module_names: set) -> bool:
        """该调用是否指向**项目外**（内置 / 标准库 / 第三方）。

        判据（保守，宁可判为 external 也不虚增 unresolved）：
            · 首段是内置函数名（len/print/open/...）
            · 首段是常见标准库顶层模块名（os/sys/json/re/...）
            · 首段在本文件 import 绑定表中，且绑定目标首段既不是标准库
              也不是本项目模块（→ 第三方库，如 requests/numpy/psutil）
        """
        _parts = rc.get("parts") or []
        if not _parts:
            return True                      # 动态调用已单独计数
        _head = _parts[0]
        if _head in _BUILTIN_FUNCS or _head in _STDLIB_TOPS:
            return True
        _b = self._bindings.get(rc.get("file", "")) or {}
        _tgt = _b.get(_head)
        if _tgt:
            _top = str(_tgt).split(".")[0]
            if _top in _STDLIB_TOPS:
                return True
            if (_top not in module_names and _top not in self._by_module
                    and _top not in ("nucleus", "organs", "functions", "base",
                                     "utils", "somatics", "tools", "config",
                                     "main")):
                return True                  # 第三方库
        return False

    def _callee_module(self, node_id: str) -> str:
        _d = self._defs.get(node_id)
        if _d:
            return _d.get("module", "")
        return node_id.split(":", 1)[0]

    def _resolve(self, rc: dict, module_names: set) -> str:
        """把一个调用解析为 callee 节点 id（解析不出返回空串）。"""
        _parts = rc.get("parts") or []
        if not _parts:
            return ""
        _cur_mod = rc.get("module", "")
        _head = _parts[0]

        # 1) self.method() → 当前类的方法
        if _head == "self" and len(_parts) >= 2 and rc.get("in_class"):
            _q = "{}.{}".format(rc["in_class"], _parts[1])
            _hit = self._by_module.get(_cur_mod, {}).get(_q)
            if _hit:
                return _hit
            return ""

        # 2) 本模块内定义（含 Class.method / Class.A.B）
        _q = ".".join(_parts)
        _hit = self._by_module.get(_cur_mod, {}).get(_q)
        if _hit:
            return _hit
        # 2b) `A.method()` 且 A 是当前模块的类
        if len(_parts) >= 2:
            _cls_q = ".".join(_parts[:-1])
            if _qual_id(_cur_mod, _cls_q) in self._by_module.get(_cur_mod, {}).values() \
                    or _cls_q in self._by_module.get(_cur_mod, {}):
                _hit = self._by_module.get(_cur_mod, {}).get(_q)
                if _hit:
                    return _hit

        # 3) 通过本文件 import 绑定解析
        _b = self._bindings.get(rc.get("file", "")) or {}
        if _head in _b:
            _target = _b[_head]                 # 形如 "pkg.mod" 或 "pkg.mod.symbol"
            _rest = _parts[1:]
            # 3a) `from m import f` 后直接 `f()` → target 形如 "pkg.m.f"
            #     ★修正：先试 target 本身是模块；再拆「模块 + 符号」
            if not _rest:
                _hit = self._lookup_in_module(_target, "")
                if _hit:
                    return _hit
                if "." in _target:
                    _mod, _sym = _target.rsplit(".", 1)
                    _hit = self._lookup_in_module(_mod, _sym)
                    if _hit:
                        return _hit
                return ""
            # 3b) `alias.func()`：target 是模块
            _hit = self._lookup_in_module(_target, ".".join(_rest))
            if _hit:
                return _hit
            # 3c) `from m import A` 后 `A.method()`
            _first = _target.rsplit(".", 1)[0] if "." in _target else ""
            _sym = _target.rsplit(".", 1)[-1]
            if _first and _sym:
                _hit = self._lookup_in_module(_first, "{}.{}".format(_sym, ".".join(_rest)))
                if _hit:
                    return _hit
            # 3d) 目标是 `pkg.mod` 且 rest 只是符号（from m import f 后 f()）
            if _first in module_names and len(_rest) >= 1:
                _hit = self._lookup_in_module(_first, _sym)
                if _hit:
                    return _hit

        # 4) 以模块名为前缀（`mod.func()` 但未 import 到本文件别名表）
        if _head in module_names:
            _hit = self._lookup_in_module(_head, ".".join(_parts[1:]))
            if _hit:
                return _hit

        # 5) 全局唯一同名函数（兜底，仅在唯一时采用，避免误连）
        if len(_parts) == 1:
            _cands = [v for v in self._defs.values() if v["name"] == _head
                      and v["type"] == "function"]
            if len(_cands) == 1:
                return _cands[0]["id"]
        return ""

    def _lookup_in_module(self, module: str, qualname: str) -> str:
        """在指定模块内查 qualname（含逐级回退：a.b.c → a.b → a）。"""
        _m = self._by_module.get(module)
        if not _m:
            return ""
        if qualname in _m:
            return _m[qualname]
        _q = qualname
        while "." in _q:
            _q = _q.rsplit(".", 1)[0]
            if _q in _m:
                return _m[_q]
        return ""

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def _ensure(self) -> dict:
        if not self._graph_data:
            self.analyze()
        return self._graph_data

    def get_isolated_functions(self) -> list:
        """从未被调用的函数/方法（排除魔术方法与入口类）。"""
        self._ensure()
        _out = []
        for _nid, _d in self._defs.items():
            if self._in_deg.get(_nid, 0) > 0:
                continue
            _name = _d["name"]
            if _name in _MAGIC_ALLOW:
                continue
            if _name.startswith("__") and _name.endswith("__"):
                continue
            _out.append({
                "id": _nid,
                "name": _name,
                "file": _d["file"],
                "line": _d["line"],
                "type": _d["type"],
                # 公共 API：非下划线开头且名字具入口特征 → 可能被外部/框架调用
                "public_api": (not _name.startswith("_")),
                "entry_like": _name in _ENTRY_LIKE,
            })
        _out.sort(key=lambda x: (x["file"], x["line"]))
        return _out

    def get_hot_functions(self, top_n: int = 20) -> list:
        """被调用次数最多的函数。"""
        self._ensure()
        _rows = []
        for _nid, _cnt in self._in_deg.items():
            _d = self._defs.get(_nid)
            if not _d:
                continue
            _rows.append({"id": _nid, "name": _d["name"],
                          "file": _d["file"], "line": _d["line"],
                          "type": _d["type"], "calls": int(_cnt)})
        _rows.sort(key=lambda x: (-x["calls"], x["id"]))
        return _rows[:max(0, int(top_n))]

    # ---- 循环 / 深度 ----
    def _adj(self) -> dict:
        """返回仅含真实函数节点的邻接表（过滤虚拟模块节点）。"""
        self._ensure()
        _out: dict = {}
        for _c, _t in self._graph.items():
            if _c not in self._defs:
                continue
            _out[_c] = {k: v for k, v in _t.items() if k in self._defs}
        for _nid in self._defs:
            _out.setdefault(_nid, {})
        return _out

    @staticmethod
    def _tarjan_scc(adj: dict) -> list:
        """迭代版 Tarjan 求强连通分量（返回 size>=2 的分量 + 自环）。"""
        _index: dict = {}
        _low: dict = {}
        _on: dict = {}
        _stack: list = []
        _result: list = []
        _counter = [0]

        for _root in adj:
            if _root in _index:
                continue
            _work = [(_root, iter(adj.get(_root, ())))]
            _index[_root] = _low[_root] = _counter[0]
            _counter[0] += 1
            _stack.append(_root)
            _on[_root] = True
            while _work:
                _v, _it = _work[-1]
                _advanced = False
                for _w in _it:
                    if _w not in _index:
                        _index[_w] = _low[_w] = _counter[0]
                        _counter[0] += 1
                        _stack.append(_w)
                        _on[_w] = True
                        _work.append((_w, iter(adj.get(_w, ()))))
                        _advanced = True
                        break
                    if _on.get(_w):
                        _low[_v] = min(_low[_v], _index[_w])
                if _advanced:
                    continue
                _work.pop()
                if _work:
                    _p = _work[-1][0]
                    _low[_p] = min(_low[_p], _low[_v])
                if _low[_v] == _index[_v]:
                    _comp = []
                    while True:
                        _w = _stack.pop()
                        _on[_w] = False
                        _comp.append(_w)
                        if _w == _v:
                            break
                    if len(_comp) >= 2:
                        _result.append(_comp)
                    elif len(_comp) == 1 and _comp[0] in adj.get(_comp[0], {}):
                        _result.append(_comp)      # 自环 A→A
        return _result

    def get_cyclic_calls(self) -> list:
        """返回循环调用链（每项为该 SCC 内的节点列表，已排序）。"""
        _adj = self._adj()
        if len(_adj) > _MAX_SCC_NODES:
            _logger.warning("调用图节点过多(%d)，跳过循环检测", len(_adj))
            return []
        _sccs = self._tarjan_scc(_adj)
        _out = []
        for _comp in _sccs:
            _sorted = sorted(_comp)
            _out.append({
                "nodes": _sorted,
                "length": len(_sorted),
                "kind": "direct" if len(_sorted) == 2 else "indirect",
                "files": sorted({self._defs[x]["file"] for x in _sorted
                                 if x in self._defs}),
            })
        _out.sort(key=lambda x: (x["length"], x["nodes"][:1]))
        return _out

    def get_deepest_call_chain(self, max_depth: int = 20) -> list:
        """返回最深调用链（节点 id 列表，从链头到链尾）。

        有环时先按 SCC 缩点成 DAG，再求最长路径（保证终止）。
        """
        _adj = self._adj()
        if not _adj:
            return []
        _limit = min(max(1, int(max_depth)), _MAX_DEPTH_LIMIT)
        _scc_of = {}
        for _i, _comp in enumerate(self._tarjan_scc(_adj)):
            for _n in _comp:
                _scc_of[_n] = _i
        # ★修正：Tarjan 只返回 size>=2 的分量；未入分量的节点必须各自分配独立编号，
        #   否则无环图上 SCC 表为空 → 深度链恒为 []（实测 bug）
        _next = (max(_scc_of.values()) + 1) if _scc_of else 0
        for _n in sorted(_adj):
            if _n not in _scc_of:
                _scc_of[_n] = _next
                _next += 1
        _n_scc = _next
        _dag: dict = {}
        _dag_nodes = [set() for _ in range(_n_scc)]
        for _u, _ts in _adj.items():
            _a = _scc_of.get(_u, -1)
            if _a < 0:
                continue
            for _v in _ts:
                _b = _scc_of.get(_v, -1)
                if _b < 0 or _a == _b:
                    continue
                _dag.setdefault(_a, set()).add(_b)
                _dag_nodes[_b].add(_a)
        # 拓扑序（Kahn）
        _indeg = {k: len(_dag_nodes[k]) for k in range(_n_scc)}
        _queue = [k for k in range(_n_scc) if _indeg.get(k, 0) == 0]
        _order = []
        while _queue:
            _u = _queue.pop()
            _order.append(_u)
            for _v in _dag.get(_u, ()):
                _indeg[_v] -= 1
                if _indeg[_v] == 0:
                    _queue.append(_v)
        # DAG 上最长路径 DP
        _dist = {k: 0 for k in range(_n_scc)}
        _prev = {}
        for _u in _order:
            for _v in _dag.get(_u, ()):
                if _dist[_u] + 1 > _dist[_v]:
                    _dist[_v] = _dist[_u] + 1
                    _prev[_v] = _u
        if not _dist:
            return []
        _end = max(_dist, key=lambda k: _dist[k])
        _path_scc = []
        _cur = _end
        while _cur is not None:
            _path_scc.append(_cur)
            _cur = _prev.get(_cur)
        _path_scc.reverse()
        # 展开为真实节点：每层取 SCC 内被调用次数最高者（无 SCC 则取自身）
        _comp_members: dict = {}
        for _n, _i in _scc_of.items():
            _comp_members.setdefault(_i, []).append(_n)
        _chain = []
        for _i in _path_scc:
            _members = _comp_members.get(_i)
            if not _members:
                continue
            _members.sort(key=lambda x: (-self._in_deg.get(x, 0), x))
            _chain.append(_members[0])
            if len(_chain) >= _limit:
                break
        return _chain

    def export_json(self, path: str, data: dict | None = None) -> int:
        """导出调用图为 JSON，返回字节数。

        ★第304批 刀1：新增可选 `data` —— 允许把**已算好的图**直接落盘（避免重复分析）；
        不传时行为与改造前逐字一致（内部 `_ensure()`）。
        生产调用方为本类的 `dump_graph_snapshot()`；此前全库唯一调用方是测试
        ⇒ 图本体（nodes/edges）每次分析完即弃，从未落盘。
        """
        _data = data if isinstance(data, dict) else self._ensure()
        _dir = os.path.dirname(os.path.abspath(path))
        if _dir and not os.path.isdir(_dir):
            os.makedirs(_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, default=str)
        return os.path.getsize(path)

    # ------------------------------------------------------------------
    # T2：健康度分析
    # ------------------------------------------------------------------
    def dump_graph_snapshot(self, graph: dict | None = None, keep: int = 7) -> str:
        """★第304批 刀1：调用图**本体**落盘（图不再「分析完即弃」）。

        产出 `data/call_graph/graph_<ts>.json`，规格与 303 批刀6 per-item 落盘一致
        （原子写 tmp + `os.replace`，滚动保留最新 7 份）。

        内容：
          - `nodes[]` / `edges[]`（点号 ID：**模块.类.方法**）＋ `stats`；
          - `edge_types`：边类型**分色**统计 —— 由 `edges[].kind` 聚合而来：
              `static` = AST 静态调用边；`byname` = `getattr(x, "y")` 按名派发边；
              `event` / `entry` = 事件边 / 注册入口边（本窗若无可解析目标则计 0）。
            ★第305批刀2：分色**落到边上**（每条边自带 kind），落盘时的统计只是聚合，
            不再像 304 那样额外全库扫一遍正则（既慢又与图不一致）。

        Returns: 落盘绝对路径；失败返回空串（绝不阻断调用方）。
        """
        import time as _time
        try:
            _cg = graph if isinstance(graph, dict) else self._ensure()
            if not isinstance(_cg, dict):
                return ""
            _root = _project_root()
            _dir = os.path.join(_root, "data", "call_graph")
            os.makedirs(_dir, exist_ok=True)
            _ts = _time.strftime("%Y%m%d_%H%M%S")
            _path = os.path.join(_dir, "graph_{}.json".format(_ts))
            _tmp = _path + ".tmp"
            # ★第305批刀2：分色由**边自带的 kind** 聚合（不再全库重扫正则）
            _edge_types = {"static": 0, "byname": 0, "event": 0, "entry": 0}
            for _e in (_cg.get("edges") or []):
                if not isinstance(_e, dict):
                    continue
                _k = str(_e.get("kind") or "static")
                if _k not in _edge_types:
                    _k = "static"
                _edge_types[_k] += 1
            _payload = dict(_cg)
            _payload["edge_types"] = _edge_types
            _payload["dumped_at"] = _time.time()
            self.export_json(_tmp, data=_payload)
            os.replace(_tmp, _path)
            try:
                _olds = sorted(_f for _f in os.listdir(_dir)
                               if _f.startswith("graph_") and _f.endswith(".json"))
                for _old in _olds[:-keep]:
                    os.remove(os.path.join(_dir, _old))
            except Exception as _e:
                _logger.warning("调用图滚动清理失败（不影响本次落盘）: %s: %s",
                                type(_e).__name__, _e)
            _logger.info("[304刀1] 调用图落盘: %s（nodes=%d edges=%d 边类型=%s）",
                         os.path.relpath(_path, _root),
                         len(_payload.get("nodes") or []),
                         len(_payload.get("edges") or []), _edge_types)
            return _path
        except Exception as e:
            _logger.warning("调用图落盘失败（返回空串）: %s: %s", type(e).__name__, e)
            return ""

    def analyze_health(self, call_graph: dict | None = None) -> dict:
        """基于调用图计算"代码结构健康度"（5 维度 + 综合评分）。

        Args:
            call_graph: 调用图数据；None 时用最近一次 analyze() 的结果。

        Returns:
            dict：score / grade / isolated / hot / cyclic / depth / coupling /
                  recommendations / dimensions
        """
        _cg = call_graph if isinstance(call_graph, dict) else self._ensure()
        _stats = (_cg or {}).get("stats") or {}
        _nodes = (_cg or {}).get("nodes") or []
        if not _nodes:
            _logger.debug("调用图无节点，健康度分析返回空结构")
            return {
                "score": 0.0, "grade": "无数据",
                "isolated": {"count": 0, "ratio": 0.0, "top20": [], "level": "无数据"},
                "hot": {"top20": [], "super_functions": [], "level": "无数据"},
                "cyclic": {"count": 0, "chains": [], "level": "无数据"},
                "depth": {"deepest": 0, "average": 0.0, "chain": [], "level": "无数据"},
                "coupling": {"cross_file_ratio": 0.0, "core_modules": [],
                             "edge_modules": [], "level": "无数据"},
                "recommendations": [],
                "dimensions": {},
                "no_data": True,
            }

        _total_fn = max(1, len(_nodes))
        # ---- 1) 孤立 ----
        _iso_all = self.get_isolated_functions()
        _iso_ratio = len(_iso_all) / float(_total_fn)
        # ★口径修正：纯静态分析看不到「外部/框架调用者」，公共 API 的孤立性
        #   不可判定 —— 评分采用**内部孤立占比**（排除 public_api），
        #   同时并列输出两种口径供人工判断。
        #   ★统一口径：`_iso_internal` = 非公共 API 的孤立项（含私有方法），
        #     供评分 / 输出 / 建议三处复用，避免「299 vs 21」式不一致。
        _iso_internal = [x for x in _iso_all if not x["public_api"]]
        _iso_internal_ratio = len(_iso_internal) / float(_total_fn)
        _iso_level = "偏高" if _iso_internal_ratio > _ISOLATED_RATIO_WARN else "正常"

        # ---- 2) 热点 ----
        _hot = self.get_hot_functions(20)
        _super = [x for x in _hot if x["calls"] > _SUPER_FUNCTION_CALLS]

        # ---- 3) 循环 ----
        _cyc = self.get_cyclic_calls()

        # ---- 4) 深度 ----
        _chain = self.get_deepest_call_chain(_MAX_DEPTH_LIMIT)
        _deepest = len(_chain)
        _avg_depth = self._average_depth()

        # ---- 5) 耦合 ----
        _cross = int(_stats.get("cross_file_calls", 0) or 0)
        _total_calls = max(1, int(_stats.get("total_calls", 0) or 0))
        _cross_ratio = round(_cross / float(_total_calls), 4)
        _mod_calls: dict = {}
        for _e in (_cg.get("edges") or []):
            _m = self._callee_module(_e.get("callee", ""))
            if _m:
                _mod_calls[_m] = _mod_calls.get(_m, 0) + int(_e.get("count", 1) or 1)
        _mod_sorted = sorted(_mod_calls.items(), key=lambda kv: (-kv[1], kv[0]))
        _core = [{"module": k, "calls": v} for k, v in _mod_sorted[:10]]
        _edge = [{"module": k, "calls": v} for k, v in _mod_sorted[-10:]]

        # ---- 评分 ----
        _d_iso = self._score_isolated(_iso_internal_ratio)
        _d_hot = self._score_hot(len(_super))
        _d_cyc = self._score_cyclic(len(_cyc))
        _d_depth = self._score_depth(_deepest)
        _d_coup = self._score_coupling(_cross_ratio)
        _score = round(_d_iso + _d_hot + _d_cyc + _d_depth + _d_coup, 2)
        _grade = self._grade(_score)

        _recs = self._recommend(_iso_all, _super, _cyc, _deepest, _cross_ratio,
                                _iso_internal)
        return {
            "score": _score,
            "grade": _grade,
            "isolated": {"count": len(_iso_all), "ratio": round(_iso_ratio, 4),
                         "top20": _iso_all[:20], "level": _iso_level,
                         "internal_count": len(_iso_internal),
                         "internal_ratio": round(_iso_internal_ratio, 4),
                         "public_api_count": len(_iso_all) - len(_iso_internal)},
            "hot": {"top20": _hot, "super_functions": _super,
                    "level": "关注" if _super else "正常"},
            "cyclic": {"count": len(_cyc), "chains": _cyc[:20],
                       "level": "需关注" if _cyc else "正常"},
            "depth": {"deepest": _deepest, "average": _avg_depth,
                      "chain": _chain,
                      "level": "过深" if _deepest > _DEEP_CHAIN_WARN else "正常"},
            "coupling": {"cross_file_ratio": _cross_ratio,
                         "core_modules": _core, "edge_modules": _edge,
                         "level": "正常"},
            "recommendations": _recs,
            "dimensions": {
                "isolated": _d_iso, "hot": _d_hot, "cyclic": _d_cyc,
                "depth": _d_depth, "coupling": _d_coup,
            },
            "summary": {
                "total_functions": len(_nodes),
                "total_calls": _total_calls,
                "cross_file_calls": _cross,
                "isolated": len(_iso_all),
                "isolated_internal": len(_iso_internal),
                "super_functions": len(_super),
                "cyclic_groups": len(_cyc),
                "deepest_chain": _deepest,
            },
        }

    def _average_depth(self) -> float:
        """平均调用深度：以入度 0 的节点为起点做 BFS 层深，取均值（近似）。"""
        _adj = self._adj()
        if not _adj:
            return 0.0
        _has_in = {v for _u, _ts in _adj.items() for v in _ts}
        _roots = [n for n in _adj if n not in _has_in]
        if not _roots:
            return 0.0
        _depth = {r: 0 for r in _roots}
        _queue = list(_roots)
        _guard = 0
        while _queue and _guard < 200000:
            _guard += 1
            _u = _queue.pop(0)
            for _v in _adj.get(_u, ()):
                if _v not in _depth:
                    _depth[_v] = _depth[_u] + 1
                    _queue.append(_v)
        if not _depth:
            return 0.0
        return round(sum(_depth.values()) / float(len(_depth)), 2)

    # ---- 评分细则（每维 20 分）----
    @staticmethod
    def _score_isolated(ratio: float) -> float:
        if ratio <= 0.02:
            return 20.0
        if ratio <= 0.10:
            return round(20.0 - (ratio - 0.02) / 0.08 * 6.0, 2)
        if ratio <= 0.25:
            return round(14.0 - (ratio - 0.10) / 0.15 * 7.0, 2)
        return round(max(0.0, 7.0 - (ratio - 0.25) * 20.0), 2)

    @staticmethod
    def _score_hot(super_count: int) -> float:
        if super_count == 0:
            return 20.0
        if super_count <= 3:
            return 16.0
        if super_count <= 10:
            return 10.0
        return 4.0

    @staticmethod
    def _score_cyclic(group_count: int) -> float:
        if group_count == 0:
            return 20.0
        if group_count <= 2:
            return 15.0
        if group_count <= 6:
            return 9.0
        return 3.0

    @staticmethod
    def _score_depth(deepest: int) -> float:
        if deepest <= 8:
            return 20.0
        if deepest <= 15:
            return round(20.0 - (deepest - 8) / 7.0 * 6.0, 2)
        if deepest <= 25:
            return round(14.0 - (deepest - 15) / 10.0 * 8.0, 2)
        return 2.0

    @staticmethod
    def _score_coupling(cross_ratio: float) -> float:
        # 跨文件占比过低 = 模块割裂；适度（0.2~0.6）最佳
        if 0.2 <= cross_ratio <= 0.6:
            return 20.0
        if cross_ratio < 0.2:
            return round(20.0 - (0.2 - cross_ratio) / 0.2 * 8.0, 2)
        return round(max(6.0, 20.0 - (cross_ratio - 0.6) / 0.4 * 14.0), 2)

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 85:
            return "优秀"
        if score >= 70:
            return "良好"
        if score >= 55:
            return "一般"
        return "需改善"

    @staticmethod
    def _recommend(iso_all: list, super_fn: list, cyc: list, deepest: int,
                   cross_ratio: float, iso_internal: list) -> list:
        _out = []
        if iso_all:
            _out.append(
                "存在 %d 个从未被调用的函数（其中内部函数 %d 个）——"
                "建议逐一确认是否为「建而不用」或拼写错误的调用名"
                % (len(iso_all), len(iso_internal)))
        if super_fn:
            _out.append(
                "存在 %d 个被调用超过 %d 次的「超级函数」——"
                "建议评估是否拆分（God Function 风险）"
                % (len(super_fn), _SUPER_FUNCTION_CALLS))
        if cyc:
            _out.append("检测到 %d 组循环调用——建议引入中间层或事件解耦打破环"
                        % len(cyc))
        if deepest > _DEEP_CHAIN_WARN:
            _out.append("最深调用链 %d 层（阈值 %d）——链路过深难以追踪，"
                        "建议收窄职责" % (deepest, _DEEP_CHAIN_WARN))
        if cross_ratio < 0.2:
            _out.append("跨文件调用占比仅 %.1f%%——模块间协作偏少，"
                        "留意功能是否被重复实现" % (cross_ratio * 100))
        elif cross_ratio > 0.6:
            _out.append("跨文件调用占比达 %.1f%%——模块耦合偏高，"
                        "改动影响面可能较大" % (cross_ratio * 100))
        if not _out:
            _out.append("代码结构健康度良好，未发现需要立即处理的问题")
        return _out[:5]


# ----------------------------------------------------------------------
# 分析器入口（供 SelfAwarenessEngine.register_analyzer 使用）
# ----------------------------------------------------------------------
# _m49_t5_cg_body_done
# _m49_t5_cg_imp_done
