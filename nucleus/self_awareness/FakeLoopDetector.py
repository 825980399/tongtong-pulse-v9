# -*- coding: utf-8 -*-
"""
FakeLoopDetector.py —— PHASE18 阶段一：虚假闭环检测器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 静态找出"看起来有存储闭环（save/load 成对）但实际是假的"的模块 ——
      保存空操作、加载空操作、路径不一致、异常静默、返回值校验缺失，
      并按健康度评分排序，给出具体行号与修复建议。
机制: 遍历项目 .py 文件（排除 tests/ tmp/ .bak*/ __pycache__），用 AST 找出
      同时定义 save 族与 load 族方法的类（或 `__init__` 含 storage_path 类属性、
      模块级 `_STORAGE_PATH` 的模块），逐项做**纯静态**检查。
定位: PHASE18 地基之一。★红线：**纯静态分析，绝不触发任何运行时保存/加载**。
      特别注意区分「空操作」与「抽象接口」—— 方法体仅 `raise NotImplementedError`
      或 `@abstractmethod` 属正常抽象，不计为虚假闭环。

开关（config）:
    ENABLE_FAKE_LOOP_DETECTOR   总开关（默认 True，关闭时零扫描开销）
    FAKE_LOOP_SCAN_DIRS         扫描范围（默认 nucleus/organs/functions/main.py）
"""

from __future__ import annotations

import ast
import json
import os
import time
from typing import Any

from nucleus.data.exclude_dirs import SOURCE_SCAN_DIRS  # ★第49批 T5
from nucleus.data.path_utils import (
    safe_relpath as _safe_relpath,  # ★第55批 T3（跨盘安全，同盘行为与 os.path.relpath 一致）
)
from nucleus.logger import get_module_logger

_logger = get_module_logger("FakeLoopDetector")

# ★主线第49批 T5（P2-310）：统一来源（语义与原定义逐字一致）
_EXCLUDE_DIRS = SOURCE_SCAN_DIRS
_EXCLUDE_DIR_PREFIXES = (".bak", ".")

#: 保存族 / 加载族方法名前缀
_SAVE_PREFIXES = ("save", "write", "dump", "persist", "store")
_LOAD_PREFIXES = ("load", "read", "fetch", "retrieve")

#: 认定为"真实写入/读取"的调用名（末段）
_WRITE_CALLS = ("open", "dump", "dumps", "savez", "savez_compressed", "save",
                "to_csv", "to_json", "to_parquet", "to_pickle", "write",
                "writelines", "writestr", "copy", "copy2", "copyfile",
                "replace", "rename", "mkdir", "makedirs")
_READ_CALLS = ("open", "load", "loads", "loadtxt", "genfromtxt", "read_csv",
               "read_json", "read_parquet", "read_pickle", "read", "readlines",
               "readline", "exists", "isfile", "listdir")

#: 路径类属性名暗示（★刻意保持窄口径："cache"/"store" 这类会命中
#: `_max_processed_cache` 等**内存**对象，造成大量误报，故不纳入）
_PATH_ATTR_HINTS = ("path", "filename", "storage", "output", "target")

#: 严重的空返回（异常静默）
_SILENT_RETURNS = (None, False, True, 0)

#: 「确定不碰文件系统」的纯工具/日志调用。
#: 判空操作时：方法体内若出现**任何**白名单之外的调用，即视为"可能委托"，
#: 保守地**不判空操作** —— 避免把 `self._write_impl(...)` / `LazyView(...)`
#: 这类间接落盘误报成"保存空操作"（任务书 §八.5 要求控制误报）。
_HARMLESS_CALLS = frozenset((
    "_log", "_log_ignored_exception", "log", "debug", "info", "warning",
    "error", "critical", "exception", "print", "getattr", "setattr", "hasattr",
    "isinstance", "issubclass", "len", "repr", "str", "int", "float", "bool",
    "dict", "list", "set", "tuple", "bytes", "round", "sorted", "min", "max",
    "sum", "any", "all", "abs", "enumerate", "range", "zip", "format",
    "join", "split", "strip", "lstrip", "rstrip", "replace", "startswith",
    "endswith", "lower", "upper", "append", "extend", "update", "get", "keys",
    "values", "items", "pop", "copy", "deepcopy", "now", "time", "perf_counter",
    "strftime", "strptime", "localtime", "dumps", "loads", "uuid4", "hex",
    "sleep", "getpid", "getcwd", "boolean",
))


def _no_file_write(fn: ast.AST) -> bool:
    """判定「保存空操作」：无真实写入，且方法体内无任何可能委托的调用。

    ★保守策略：只要出现白名单之外的调用（如 `self._persist_impl(...)`、
    `JsonStoreWriter(...)`），就认为"可能间接落盘"，**不判空操作**。
    """
    _calls = _call_names(fn)
    for _nm, _ln, _mode in _calls:
        if _nm == "open":
            if any(c in _mode for c in ("w", "a", "x", "+")):
                return False
            return False       # 只读 open 出现在 save 里 → 非空操作（保守）
        if _nm in _WRITE_CALLS:
            return False
    return all(not (_nm and _nm not in _HARMLESS_CALLS) for _nm, _ln, _mode in _calls)


def _no_file_read(fn: ast.AST) -> bool:
    """判定「加载空操作」（保守策略同上）。"""
    _calls = _call_names(fn)
    for _nm, _ln, _mode in _calls:
        if _nm == "open":
            return False
        if _nm in _READ_CALLS:
            return False
    return all(not (_nm and _nm not in _HARMLESS_CALLS) for _nm, _ln, _mode in _calls)


def _last_name(node: ast.AST) -> str:
    """取调用名末段（`a.b.c` → `c`）。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _body_of(fn: ast.AST) -> list[ast.stmt]:
    """取函数体语句列表。"""
    return list(getattr(fn, "body", []) or [])


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
        and isinstance(node.value.value, str)


def _is_abstract(stmts: list[ast.stmt]) -> bool:
    """方法体是否只是抽象声明（docstring + raise NotImplementedError）。"""
    for _n in stmts:
        if _is_docstring(_n):
            continue
        if isinstance(_n, ast.Raise):
            _exc = _n.exc
            _nm = _last_name(_exc.func) if isinstance(_exc, ast.Call) else \
                _last_name(_exc) if _exc is not None else ""
            return _nm in ("NotImplementedError", "NotImplemented",
                           "ABCNotImplementedError")
        return False
    return False


def _call_names(fn: ast.AST) -> list[tuple[str, int, str]]:
    """收集函数体内所有调用：(末段名, 行号, 是否写入模式)。

    `open(x, 'w')` 与 `open(x, 'r')` 用第三项区分。
    """
    _out: list[tuple[str, int, str]] = []
    for _n in ast.walk(fn):
        if not isinstance(_n, ast.Call):
            continue
        _nm = _last_name(_n.func)
        _mode = ""
        if _nm == "open" and len(_n.args) > 1 and isinstance(_n.args[1], ast.Constant) \
                and isinstance(_n.args[1].value, str):
            _mode = _n.args[1].value
        _out.append((_nm, int(getattr(_n, "lineno", 0) or 0), _mode))
    return _out






def _self_path_attrs(fn: ast.AST) -> set[str]:
    """方法体内引用的、名字暗示路径的 `self.<attr>` 集合。"""
    _out = set()
    for _n in ast.walk(fn):
        if isinstance(_n, ast.Attribute) and isinstance(_n.value, ast.Name) \
                and _n.value.id == "self":
            if any(h in _n.attr.lower() for h in _PATH_ATTR_HINTS):
                _out.add(_n.attr)
    return _out


def _module_path_consts(tree: ast.AST) -> set[str]:
    """模块级 `_STORAGE_PATH` / `_SAVE_PATH` 之类常量名。"""
    _out = set()
    for _n in getattr(tree, "body", []) or []:
        if isinstance(_n, ast.Assign):
            for _t in _n.targets:
                if isinstance(_t, ast.Name) and any(
                        h in _t.id.lower() for h in _PATH_ATTR_HINTS):
                    _out.add(_t.id)
    return _out


def _silent_except(fn: ast.AST) -> list[int]:
    """找出「异常静默」的 except 处理（pass 或 return 空值），返回行号列表。"""
    _out: list[int] = []
    for _n in ast.walk(fn):
        if not isinstance(_n, ast.ExceptHandler):
            continue
        _body = [x for x in (_n.body or []) if not _is_docstring(x)]
        if not _body:
            _out.append(int(getattr(_n, "lineno", 0) or 0))
            continue
        if len(_body) == 1:
            _only = _body[0]
            if isinstance(_only, ast.Pass):
                _out.append(int(getattr(_only, "lineno", 0) or 0))
            elif isinstance(_only, ast.Return):
                _v = _only.value
                if _v is None or (isinstance(_v, ast.Constant)
                                  and _v.value in _SILENT_RETURNS):
                    _out.append(int(getattr(_only, "lineno", 0) or 0))
    return _out


def _has_return_check(fn: ast.AST) -> bool:
    """是否存在返回值校验（if data is None / if not data / if x is False 等）。"""
    for _n in ast.walk(fn):
        if not isinstance(_n, ast.If):
            continue
        _t = _n.test
        # `not x`
        if isinstance(_t, ast.UnaryOp) and isinstance(_t.op, ast.Not):
            return True
        # `x is None` / `x == None`
        if isinstance(_t, ast.Compare):
            for _op in _t.ops:
                if isinstance(_op, (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)):
                    return True
        # `if not os.path.exists(...)` 之类已由上面覆盖；`if len(x) == 0`
        if isinstance(_t, ast.Call) and _last_name(_t.func) == "len":
            return True
    return False


class _LoopClassVisitor(ast.NodeVisitor):
    """找出单个模块中所有"疑似闭环"的类/模块级承载。"""

    def __init__(self, module: str, rel_file: str, project_root: str,
                 allow_private: bool = False) -> None:
        self.module = module
        self.rel_file = rel_file
        self.project_root = project_root
        # ★T3（P2-215）：是否把私有下划线变体纳入识别
        self.allow_private = bool(allow_private)
        self.candidates: list[dict[str, Any]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        try:
            self._inspect_class(node)
        except Exception as e:  # 单类分析失败不应中断扫描
            _logger.debug("类分析失败 %s.%s: %s: %s", self.rel_file, node.name,
                          type(e).__name__, e)
        self.generic_visit(node)

    # ------------------------------------------------------------------
    def _inspect_class(self, node: ast.ClassDef) -> None:
        _methods: dict[str, ast.AST] = {}
        for _m in node.body:
            if isinstance(_m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _methods[_m.name] = _m
        _savers = [n for n in _methods
                   if _is_save_name(n, self.allow_private)]
        _loaders = [n for n in _methods
                    if _is_load_name(n, self.allow_private)]
        # `__init__` 中是否有 storage_path 类属性
        _init = _methods.get("__init__")
        _init_attrs = _self_path_attrs(_init) if _init is not None else set()
        if not ((_savers and _loaders) or (_init_attrs and (_savers or _loaders))):
            return

        _problems: list[dict[str, Any]] = []

        # ---- a/b 空操作检测（跳过抽象接口）----
        _concrete_savers = [n for n in _savers
                            if not _is_abstract(_body_of(_methods[n]))]
        _concrete_loaders = [n for n in _loaders
                             if not _is_abstract(_body_of(_methods[n]))]
        _abstract_only = bool(_savers and _loaders
                              and not _concrete_savers and not _concrete_loaders)
        if _abstract_only:
            return    # 纯抽象基类 → 不是虚假闭环

        for _n in _concrete_savers:
            if _no_file_write(_methods[_n]):
                _problems.append({
                    "type": "save_noop",
                    "severity": "severe",
                    "method": _n,
                    "line": int(getattr(_methods[_n], "lineno", 0) or 0),
                    "detail": "保存方法内没有真正的文件写入调用",
                    "private": _n.startswith("_"),
                })
        for _n in _concrete_loaders:
            if _no_file_read(_methods[_n]):
                _problems.append({
                    "type": "load_noop",
                    "severity": "severe",
                    "method": _n,
                    "line": int(getattr(_methods[_n], "lineno", 0) or 0),
                    "detail": "加载方法内没有真正的文件读取调用",
                    "private": _n.startswith("_"),
                })

        # ---- c 路径一致性 ----
        if _concrete_savers and _concrete_loaders:
            _s_attrs: set[str] = set()
            for _n in _concrete_savers:
                _s_attrs |= _self_path_attrs(_methods[_n])
            _l_attrs: set[str] = set()
            for _n in _concrete_loaders:
                _l_attrs |= _self_path_attrs(_methods[_n])
            if _s_attrs and _l_attrs and not (_s_attrs & _l_attrs):
                _problems.append({
                    "type": "path_mismatch",
                    "severity": "medium",
                    "method": "{}/{}".format(_concrete_savers[0], _concrete_loaders[0]),
                    "line": int(getattr(_methods[_concrete_savers[0]],
                                        "lineno", 0) or 0),
                    "detail": "save 用 {}，load 用 {} —— 路径可能不一致".format(sorted(_s_attrs), sorted(_l_attrs)),
                })

        # ---- d 异常静默 ----
        for _n in list(_concrete_savers) + list(_concrete_loaders):
            for _ln in _silent_except(_methods[_n]):
                _problems.append({
                    "type": "silent_except",
                    "severity": "medium",
                    "method": _n,
                    "line": _ln,
                    "detail": "异常被静默吞掉（except 内 pass 或 return 空值）",
                    "private": _n.startswith("_"),
                })

        # ---- e 返回值校验缺失（仅当有真实 IO 时才有意义）----
        for _n in _concrete_loaders:
            if not _no_file_read(_methods[_n]) and not _has_return_check(_methods[_n]):
                _problems.append({
                    "type": "no_return_check",
                    "severity": "minor",
                    "method": _n,
                    "line": int(getattr(_methods[_n], "lineno", 0) or 0),
                    "detail": "加载后未校验结果是否为空（缺 if data is None 之类）",
                    "private": _n.startswith("_"),
                })

        if not _problems:
            return
        _priv_s = sorted(n for n in _concrete_savers if n.startswith("_"))
        _priv_l = sorted(n for n in _concrete_loaders if n.startswith("_"))
        self.candidates.append({
            "module": self.module,
            "class": node.name,
            "file": self.rel_file,
            "line": int(getattr(node, "lineno", 0) or 0),
            "save_methods": sorted(_concrete_savers),
            "load_methods": sorted(_concrete_loaders),
            # ★T3（P2-215）：报告需区分「公开方法命中」与「私有方法命中」
            "private_save_methods": _priv_s,
            "private_load_methods": _priv_l,
            "has_private_hit": bool(_priv_s or _priv_l),
            "problems": _problems,
            "score": score_of(_problems),
        })


def _is_save_name(name: str, allow_private: bool = False) -> bool:
    """save / save_xxx / write / dump_xxx 形式。

    ★主线第18批：默认**不**匹配 `_save`/`_write` 私有前缀变体 —— `_read_xxx_flag`
    这类"读内存标志"的私有方法被纳入会产生大量误报（InfoField 实测）。

    ★主线第38批 T3（P2-215）：新增 ``allow_private`` —— 把私有变体的**精确形式**
    （``_save``/``_write``/``_dump``/``_persist``/``_store``，即下划线后就是完整前缀、
    **无后续后缀**）纳入识别。``_save_xxx`` 形式仍**不**纳入：实测会产生
    ``_fetch_url_text`` / ``_read_method_body`` 等明显误报（项目内 32 → 14 个候选）。
    真实项目实测：候选 9 → 19（+10 个此前完全漏判的真实私有闭环），**零误报**。
    """
    _n = name.lower()
    if _n.startswith("_"):
        if not allow_private:
            return False
        return _n[1:] in _SAVE_PREFIXES
    return any(_n == p or _n.startswith(p + "_") for p in _SAVE_PREFIXES)


def _is_load_name(name: str, allow_private: bool = False) -> bool:
    """load / load_xxx / read / fetch_xxx 形式（私有变体语义同 `_is_save_name`）。"""
    _n = name.lower()
    if _n.startswith("_"):
        if not allow_private:
            return False
        return _n[1:] in _LOAD_PREFIXES
    return any(_n == p or _n.startswith(p + "_") for p in _LOAD_PREFIXES)


def _private_match_enabled() -> bool:
    """★T3（P2-215）：私有下划线命名识别开关（``ENABLE_FAKE_LOOP_PRIVATE_MATCH``）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_FAKE_LOOP_PRIVATE_MATCH", True))
    except Exception as e:
        _logger.debug("读取私有命名识别开关失败，按开启处理: %s: %s",
                      type(e).__name__, e)
        return True


def _self_observe_excludes() -> tuple:
    """★T4（P2-217）：引擎自身产物目录（相对项目根的 POSIX 前缀），默认 data/self_awareness。

    自我认知引擎分析项目时若把**自身产物**也纳入，会产生"引擎分析自己的报告"
    的自我指涉噪声 —— 本函数返回需排除的前缀集合。
    """
    try:
        import config
        if not bool(getattr(config, "ENABLE_SELF_AWARENESS_EXCLUDE", True)):
            return ()
        _v = getattr(config, "SELF_AWARENESS_EXCLUDE_DIRS", None)
        if isinstance(_v, (list, tuple)):
            return tuple(str(x).replace("\\", "/").strip("/").lower()
                         for x in _v if str(x).strip())
    except Exception as e:
        _logger.debug("读取自我观察排除配置失败，用默认值: %s: %s",
                      type(e).__name__, e)
    return ("data/self_awareness",)


def _is_self_observe(rel_path: str, prefixes: tuple = ()) -> bool:
    """★T4（P2-217）：判断相对路径是否落在自我观察产物目录内。"""
    _p = str(rel_path or "").replace("\\", "/").lstrip("./").lower()
    if not _p:
        return False
    for _pre in (prefixes or _self_observe_excludes()):
        if _pre and (_p == _pre or _p.startswith(_pre + "/")):
            return True
    return False


def score_of(problems: list[dict[str, Any]]) -> int:
    """按任务书档次计算闭环健康度评分（100/80/60/30/0）。"""
    _severe = [p for p in problems if p.get("severity") == "severe"]
    _medium = [p for p in problems if p.get("severity") == "medium"]
    _minor = [p for p in problems if p.get("severity") == "minor"]
    if _severe:
        # 多个严重问题、或严重+中等问题 → 0
        return 0 if (len(_severe) > 1 or _medium) else 30
    if _medium:
        return 60
    if _minor:
        return 80
    return 100


def _iter_scan_files(project_root: str, scan_dirs: list[str]) -> list[str]:
    """展开扫描范围内的 .py 文件。"""
    _out: list[str] = []
    for _entry in scan_dirs:
        _p = os.path.join(project_root, _entry.replace("/", os.sep))
        if os.path.isfile(_p) and _p.endswith(".py"):
            _out.append(_p)
            continue
        if not os.path.isdir(_p):
            continue
        for _dp, _dirs, _files in os.walk(_p):
            _dirs[:] = [d for d in _dirs
                        if d not in _EXCLUDE_DIRS
                        and not any(d.startswith(x) for x in _EXCLUDE_DIR_PREFIXES)]
            for _fn in _files:
                if _fn.endswith(".py"):
                    _out.append(os.path.join(_dp, _fn))
    return _out


class FakeLoopDetector:
    """虚假闭环静态检测器。"""

    def __init__(self, project_root: str | None = None,
                 scan_dirs: list[str] | None = None) -> None:
        self.project_root = project_root or os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        self.scan_dirs = list(scan_dirs or _scan_dirs())
        # ★T3（P2-215）：私有下划线命名识别（默认开，可配置关闭 → 回到第18批行为）
        self._private_match = _private_match_enabled()

    # ------------------------------------------------------------------
    def scan(self) -> dict[str, Any]:
        """执行一次静态扫描，返回结构化结果（不落盘）。"""
        _t0 = time.perf_counter()
        _files = _iter_scan_files(self.project_root, self.scan_dirs)
        _cands: list[dict[str, Any]] = []
        _parse_errors: list[dict[str, str]] = []
        _path_const_rows: list[dict[str, Any]] = []
        # ★T4（P2-217）：自我观察噪声排除（引擎自身产物目录）
        _excl_prefixes = _self_observe_excludes()
        _excluded_self = 0

        for _abs in _files:
            _rel = _safe_relpath(_abs, self.project_root).replace(os.sep, "/")
            _mod = _rel[:-3].replace("/", ".")
            if _is_self_observe(_rel, _excl_prefixes):
                _excluded_self += 1
                continue
            try:
                with open(_abs, encoding="utf-8", errors="replace") as f:
                    _src = f.read()
                _tree = ast.parse(_src, filename=_rel)
            except SyntaxError as e:
                _parse_errors.append({"file": _rel, "error": "SyntaxError: {}".format(e)})
                continue
            except Exception as e:
                _parse_errors.append({"file": _rel,
                                      "error": "{}: {}".format(type(e).__name__, e)})
                continue

            _v = _LoopClassVisitor(_mod, _rel, self.project_root,
                                   allow_private=self._private_match)
            _v.visit(_tree)
            _cands.extend(_v.candidates)

            _consts = _module_path_consts(_tree)
            if _consts:
                _path_const_rows.append({"file": _rel, "module": _mod,
                                         "constants": sorted(_consts)})

        _cands.sort(key=lambda c: (c["score"], c["module"], c["class"]))
        _elapsed_ms = round((time.perf_counter() - _t0) * 1000.0, 2)
        _result = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "project_root": self.project_root,
            "scan_dirs": list(self.scan_dirs),
            "scanned_files": len(_files),
            "elapsed_ms": _elapsed_ms,
            "candidates": _cands,
            "module_path_consts": _path_const_rows,
            "parse_errors": _parse_errors,
            "excluded_self_observation": _excluded_self,
            "private_match": bool(self._private_match),
            "summary": self._summarize(_cands, _elapsed_ms),
        }
        # ★第37批 T4（P2-218）：措辞消歧 —— 原「%d 文件」是**源码**文件数，
        #   与"数据文件"概念易混，明确标注口径。
        # ★第38批 T3/T4：补充「私有命中」「自我观察排除」两项口径。
        _logger.info(
            "虚假闭环扫描完成: 扫描 %d 个源码文件 / %d 候选（私有命中 %d）/ "
            "排除自我观察 %d / 私有识别 %s / %.1fms",
            len(_files), len(_cands),
            sum(1 for c in _cands if c.get("has_private_hit")),
            _excluded_self, "开" if self._private_match else "关", _elapsed_ms)
        return _result

    @staticmethod
    def _summarize(cands: list[dict[str, Any]], elapsed_ms: float) -> dict[str, Any]:
        _by_score: dict[str, int] = {}
        _by_type: dict[str, int] = {}
        for _c in cands:
            _k = str(_c["score"])
            _by_score[_k] = _by_score.get(_k, 0) + 1
            for _p in _c["problems"]:
                _by_type[_p["type"]] = _by_type.get(_p["type"], 0) + 1
        _priv = sum(1 for c in cands if c.get("has_private_hit"))
        _priv_problems = sum(
            sum(1 for p in c.get("problems", []) if p.get("private"))
            for c in cands)
        return {
            "candidates": len(cands),
            "by_score": _by_score,
            "by_problem": _by_type,
            "critical": sum(1 for c in cands if c["score"] <= 30),
            # ★T3（P2-215）：公开/私有命中分开统计
            "private_hit_candidates": _priv,
            "public_hit_candidates": len(cands) - _priv,
            "private_hit_problems": _priv_problems,
            "elapsed_ms": elapsed_ms,
        }

    # ------------------------------------------------------------------
    def build_report(self, result: dict[str, Any] | None = None) -> str:
        """生成可读文本报告（健康度升序，问题最严重排最前）。"""
        _r = result if result is not None else self.scan()
        _s = _r["summary"]
        _lines = [
            "=" * 68,
            "曈曈 PulseNet · 虚假闭环检测报告",
            "生成时间: {}".format(_r["generated_at"]),
            "扫描范围: {}".format(", ".join(_r["scan_dirs"])),
            "=" * 68,
            "",
            "【概览】候选 %d 个（严重 %d），问题分布: %s，耗时 %.1fms"
            % (_s["candidates"], _s["critical"],
               _s["by_problem"] or "无", _s["elapsed_ms"]),
            # ★T3：公开/私有命中拆分；★T4：自我观察排除统计
            "【命中分布】公开方法命中 %d 个 / 私有方法命中 %d 个"
            "（私有问题 %d 条）｜已排除自我观察文件 %d 个"
            % (_s.get("public_hit_candidates", _s["candidates"]),
               _s.get("private_hit_candidates", 0),
               _s.get("private_hit_problems", 0),
               _r.get("excluded_self_observation", 0)),
            "",
        ]
        if not _r["candidates"]:
            _lines.append("未发现虚假闭环候选。")
        for _c in _r["candidates"]:
            _lines.append("─" * 60)
            _lines.append("【%d 分】%s.%s   (%s:%d)"
                          % (_c["score"], _c["module"], _c["class"],
                             _c["file"], _c["line"]))
            _lines.append("  save: {} | load: {}".format(", ".join(_c["save_methods"]) or "-",
                             ", ".join(_c["load_methods"]) or "-"))
            # ★T3（P2-215）：区分「公开方法命中」与「私有方法命中」
            _ps = _c.get("private_save_methods") or []
            _pl = _c.get("private_load_methods") or []
            if _ps or _pl:
                _lines.append("  ★私有命中: save={} | load={}".format(", ".join(_ps) or "-", ", ".join(_pl) or "-"))
            for _p in _c["problems"]:
                _lines.append("  [%s] %s (%s:%d%s) —— %s"
                              % (_p["severity"], _p["type"], _p["method"],
                                 _p["line"],
                                 "·私有" if _p.get("private") else "",
                                 _p["detail"]))
            _lines.append("  建议: {}".format(_suggest(_c)))
            _lines.append("")

        if _r["parse_errors"]:
            _lines.append("【解析失败文件】(%d)" % len(_r["parse_errors"]))
            for _pe in _r["parse_errors"][:20]:
                _lines.append("  · {}: {}".format(_pe["file"], _pe["error"]))
            _lines.append("")

        _lines.append("—— 报告结束 ——")
        return "\n".join(_lines)

    # ------------------------------------------------------------------
    def persist(self, result: dict[str, Any] | None = None,
                out_dir: str | None = None) -> dict[str, str]:
        """落盘 JSON + 文本报告，返回 {json: path, report: path}。"""
        _r = result if result is not None else self.scan()
        _dir = out_dir or os.path.join(self.project_root, "data", "self_awareness")
        os.makedirs(_dir, exist_ok=True)
        _json_p = os.path.join(_dir, "fake_loops.json")
        _txt_p = os.path.join(_dir, "fake_loops_report.txt")
        try:
            with open(_json_p, "w", encoding="utf-8") as f:
                json.dump(_r, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            _logger.warning("虚假闭环 JSON 落盘失败: %s: %s", type(e).__name__, e)
        try:
            with open(_txt_p, "w", encoding="utf-8") as f:
                f.write(self.build_report(_r))
        except Exception as e:
            _logger.warning("虚假闭环报告落盘失败: %s: %s", type(e).__name__, e)
        return {"json": _json_p, "report": _txt_p}


_SUGGEST = {
    "save_noop": "补上真实写入（open(w)/json.dump/np.savez），或删除该空实现",
    "load_noop": "补上真实读取（open(r)/json.load/np.load），或明确标注为占位",
    "path_mismatch": "统一 save/load 使用同一个路径属性（建议 __init__ 中集中定义）",
    "silent_except": "改为记录日志（含 type(e).__name__ 与真实行号），不要静默吞异常",
    "no_return_check": "加载后增加非空校验（if data is None: 记日志并返回默认值）",
}


def _suggest(cand: dict[str, Any]) -> str:
    """按问题类型给修复建议。"""
    _seen: list[str] = []
    for _p in cand["problems"]:
        _s = _SUGGEST.get(_p["type"])
        if _s and _s not in _seen:
            _seen.append(_s)
    return "；".join(_seen) if _seen else "无需处理"


def _scan_dirs() -> list[str]:
    """从 config 读取扫描范围。"""
    try:
        import config
        _v = getattr(config, "FAKE_LOOP_SCAN_DIRS", None)
        if isinstance(_v, (list, tuple)) and _v:
            return [str(x) for x in _v]
    except Exception as e:
        _logger.debug("读取扫描范围配置失败，用默认值: %s: %s", type(e).__name__, e)
    return ["nucleus", "organs", "functions", "main.py"]


def detector_enabled() -> bool:
    """读取检测器总开关（默认 True）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_FAKE_LOOP_DETECTOR", True))
    except Exception as e:
        _logger.debug("读取检测器开关失败，按开启处理: %s: %s", type(e).__name__, e)
        return True


def analyze_fake_loops(engine: Any = None) -> dict:
    """分析器入口（供 SelfAwarenessEngine 注册）。开关关闭时返回空 dict。"""
    if not detector_enabled():
        _logger.debug("虚假闭环检测器开关关闭，跳过扫描")
        return {}
    _d = FakeLoopDetector()
    _r = _d.scan()
    return {
        "summary": _r["summary"],
        "critical": [{"module": c["module"], "class": c["class"],
                      "score": c["score"], "private": c.get("has_private_hit", False)}
                     for c in _r["candidates"] if c["score"] <= 60][:50],
        "scanned_files": _r["scanned_files"],
        "elapsed_ms": _r["elapsed_ms"],
        # ★T3/T4：口径透传（公开/私有命中、自我观察排除）
        "private_hit_candidates": _r["summary"].get("private_hit_candidates", 0),
        "excluded_self_observation": _r.get("excluded_self_observation", 0),
    }
# _m49_t5_fl_body_done
# _m49_t5_fl_imp_done
