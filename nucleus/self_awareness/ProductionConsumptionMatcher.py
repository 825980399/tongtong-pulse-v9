# -*- coding: utf-8 -*-
"""
ProductionConsumptionMatcher.py —— PHASE18 阶段一：产出-消费配对器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 用 **纯静态 AST 扫描** 找出项目中"谁写了数据文件、谁读了数据文件"，
      并标记「疑似无消费」（写了没人读 = 白写）与「疑似无产出」（读了没人写 = 幽灵依赖）。
机制: 遍历项目中 .py 文件（排除 tests/ tmp/ .bak*/ __pycache__），对每个调用点判定
      产出（open w/a/x、json.dump、np.savez/save、df.to_csv/json/parquet、shutil.copy dst、
      方法名 save/write/dump/persist/store）或消费（open r、json.load、np.load/loadtxt、
      pd.read_csv/json/parquet、方法名 load/read/fetch/retrieve），把静态字符串路径归一化后
      建立「文件 → 产出方/消费方」映射；含变量/格式化的路径显式标记为「动态路径」。
定位: PHASE18 地基之一。★红线：**纯静态分析，绝不触发任何运行时保存/加载**，
      不 import 被扫描模块、不执行其代码。

开关（config）:
    ENABLE_PRODUCTION_CONSUMPTION_MATCHER   总开关（默认 True，关闭时零扫描开销）
    PRODUCTION_CONSUMPTION_SCAN_DIRS        源码扫描范围（默认 nucleus/organs/functions/main.py）
    PRODUCTION_CONSUMPTION_SCAN_DISK        磁盘枚举通道开关（★第37批 T2，默认 True）
    PRODUCTION_CONSUMPTION_DISK_ROOTS       磁盘枚举根（默认 ["."]，相对项目根）
    PRODUCTION_CONSUMPTION_SCAN_EXTENSIONS  磁盘数据文件扩展名白名单
    PRODUCTION_CONSUMPTION_DISK_MAX_DEPTH   磁盘枚举深度上限（0=不限）

★主线第37批 T2（P2-211）：**双通道**。原实现只解析源码 AST 里的路径字面量，
    磁盘 2321 个数据文件仅覆盖 17 个（0.73%）。现新增「磁盘枚举通道」按扩展名
    遍历磁盘，与源码通道**求并集**；每条记录 `sources`（"source"/"disk"）。
★主线第37批 T3（P2-212）：路径解析与误报收敛。原「疑似无消费」11 条中 7 条指向
    不存在的文件（误报 64%）→ 现拆为 `dynamic_path`（占位符模板）/
    `path_not_found`（解析后不存在）/ `no_consumer`（★存在且确无消费方）。
# _m37_t2t3
"""

from __future__ import annotations

import ast
import json
import os
import time
from typing import Any

from nucleus.logger import get_module_logger
from nucleus.data.exclude_dirs import (  # ★第49批 T5
    DISK_SCAN_EXCLUDED, SOURCE_SCAN_DIRS)


_logger = get_module_logger("ProductionConsumptionMatcher")

#: 目录名精确排除
#  ★主线第49批 T5（P2-310）：统一来源（语义与原定义逐字一致）
_EXCLUDE_DIRS = SOURCE_SCAN_DIRS
#: 目录名前缀排除（备份目录 .bak_batchN 等）
_EXCLUDE_DIR_PREFIXES = (".bak", ".")

#: 认定为「数据文件」的扩展名（用于降低误报：只跟踪数据落盘/读取）
_DATA_EXTS = (".json", ".npz", ".npy", ".csv", ".txt", ".parquet", ".pkl",
              ".pickle", ".db", ".sqlite", ".log", ".yaml", ".yml", ".md",
              ".bin", ".dat")

#: ★T2：磁盘枚举通道的目录排除（**不含 data/logs** —— 它们正是目标数据目录，
#:   源码通道排除它们、磁盘通道必须包含，故两套排除集合分开维护）
#  ★主线第49批 T5（P2-310）：统一来源（语义与原定义逐字一致）
_DISK_EXCLUDE_DIRS = DISK_SCAN_EXCLUDED
#: ★T2：磁盘枚举默认根（相对项目根）
_DISK_DEFAULT_ROOTS = (".",)
#: ★T2：磁盘枚举默认扩展名
_DISK_DEFAULT_EXTS = _DATA_EXTS + (".jsonl",)

#: ★T3：路径字面量中的动态模板标记（含占位符 → 运行时才能确定）
_DYNAMIC_PATH_MARKS = ("{", "}", "%s", "%d", "%(", "<", ">")

#: 方法名启发式
_PRODUCER_METHODS = ("save", "write", "dump", "persist", "store")
_CONSUMER_METHODS = ("load", "read", "fetch", "retrieve")

#: `open()` 模式判定
_WRITE_CHARS = ("w", "a", "x", "+")

#: 形参名暗示路径（方法名规则里用于摘取路径实参）
_PATH_HINTS = ("path", "file", "dir", "dst", "target", "output")


class _Context:
    """AST 遍历过程中的位置上下文（模块.类.方法）。"""

    __slots__ = ("module", "cls", "func")

    def __init__(self, module: str = "") -> None:
        self.module = module
        self.cls = ""
        self.func = ""

    @property
    def location(self) -> str:
        _bits = [self.module]
        if self.cls:
            _bits.append(self.cls)
        if self.func:
            _bits.append(self.func)
        return ".".join(b for b in _bits if b)


def _attr_name(node: ast.AST) -> str:
    """取 `a.b.c` 形式调用名中的最后一段（c）。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _dotted(node: ast.AST) -> str:
    """还原 `np.savez` 这类点号名（取最后两段）。"""
    _parts = []
    _cur = node
    while isinstance(_cur, ast.Attribute):
        _parts.append(_cur.attr)
        _cur = _cur.value
    if isinstance(_cur, ast.Name):
        _parts.append(_cur.id)
    return ".".join(reversed(_parts))


def _looks_like_data_path(path: str) -> bool:
    """粗判字符串是否是"数据文件路径"（用于降误报）。"""
    if not path:
        return False
    _p = path.replace("\\", "/").strip()
    if not _p:
        return False
    # ★第18批：只认"看起来是数据文件"的路径 —— 必须带数据扩展名。
    #   曾把 `os.path.join("data", name)` 这类**目录片段**误当数据文件
    #   （产出方只剩 `data`，属误报），故收紧为扩展名判据。
    return _p.lower().endswith(_DATA_EXTS)


def _normalize_path(path: str, project_root: str) -> str:
    """归一化路径：统一分隔符 + 消除 ./ ../ + 绝对路径转相对项目根。"""
    try:
        _p = os.path.normpath(path.replace("\\", os.sep))
        if os.path.isabs(_p):
            try:
                _p = os.path.relpath(_p, project_root)
            except ValueError:
                pass
        return _p.replace("\\", "/")
    except Exception as e:
        _logger.debug("路径归一化失败 %r: %s: %s", path, type(e).__name__, e)
        return path


def _join_literal_parts(node: Any, project_root: str) -> tuple[str | None, bool]:
    """`os.path.join(...)` 的字面量片段拼接。

    真实项目里数据路径几乎都是 `os.path.join(ROOT, "data", "x.json")` —— 根为变量。
    此处把**字面量片段**拼成部分路径（如 `data/x.json`），使静态分析可用；
    含变量根时返回 static=False（标记为"部分静态"，供人工确认，而非当作完全确定）。
    """
    try:
        if not (isinstance(node, ast.Call)
                and _dotted(node.func) in ("os.path.join", "path.join",
                                           "posixpath.join", "ntpath.join")):
            return None, False
        _lit: list[str] = []
        _has_var = False
        for _a in node.args:
            if isinstance(_a, ast.Constant) and isinstance(_a.value, str):
                _lit.append(_a.value)
            else:
                _has_var = True
        if not _lit:
            return None, False
        _joined = "/".join(x.strip("/\\") for x in _lit if x)
        if not _looks_like_data_path(_joined):
            return None, False
        return _normalize_path(_joined, project_root), (not _has_var)
    except Exception as e:
        _logger.debug("路径拼接解析失败: %s: %s", type(e).__name__, e)
        return None, False


def _static_path_of(node: Any, project_root: str,
                    locals_map: dict | None = None) -> tuple[str | None, bool]:
    """从 AST 实参提取路径。

    Returns:
        (归一化路径, 是否静态)。静态=False 表示含变量/格式化（动态路径，无法静态确定）。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if not _looks_like_data_path(node.value):
            return None, True          # 是字面量但不是数据路径 → 忽略
        return _normalize_path(node.value, project_root), True
    # ★局部变量路径追踪：`p = os.path.join(ROOT, "data", "x.json")` 后 `open(p, 'w')`
    #   项目里 open() 首参 98% 是变量（Name），不做追踪则几乎识别不到数据文件。
    if isinstance(node, ast.Name) and locals_map and node.id in locals_map:
        return locals_map[node.id]
    if isinstance(node, ast.JoinedStr):
        return None, False             # f-string → 动态
    if isinstance(node, ast.Call):
        _p, _full = _join_literal_parts(node, project_root)
        if _p:
            return _p, _full
        return None, False             # 其他调用 → 路径需运行时确定
    if isinstance(node, (ast.Name, ast.Attribute, ast.BinOp,
                         ast.Subscript, ast.IfExp)):
        # 属性名暗示路径时，仍属动态（值需运行时确定）
        if isinstance(node, ast.Attribute) and any(
                h in node.attr.lower() for h in _PATH_HINTS):
            return None, False
        if isinstance(node, ast.Name) and any(
                h in node.id.lower() for h in _PATH_HINTS):
            return None, False
        if isinstance(node, ast.BinOp) or isinstance(node, ast.JoinedStr):
            return None, False
        return None, True              # 与路径无关的实参 → 忽略
    return None, True


class _CallCollector(ast.NodeVisitor):
    """收集单个模块内的产出/消费调用点。"""

    def __init__(self, module: str, rel_file: str, project_root: str) -> None:
        self.ctx = _Context(module)
        self.rel_file = rel_file
        self.project_root = project_root
        self.producers: list[dict[str, Any]] = []
        self.consumers: list[dict[str, Any]] = []
        self.dynamic: list[dict[str, Any]] = []
        self.call_count = 0
        #: 函数内局部变量 → (路径, 是否完全静态)；用于解析 `p = join(...)` 后的 `open(p)`
        self._local_paths: dict[str, tuple[str | None, bool]] = {}

    # -- 位置上下文维护 --
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        _prev = self.ctx.cls
        self.ctx.cls = node.name
        self.generic_visit(node)
        self.ctx.cls = _prev

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        _prev = self.ctx.func
        _prev_locals = self._local_paths
        self.ctx.func = node.name
        self._local_paths = {}          # 函数级作用域（不做跨函数传播）
        self.generic_visit(node)
        self._local_paths = _prev_locals
        self.ctx.func = _prev

    def visit_Assign(self, node: ast.Assign) -> None:
        """记录 `name = <路径表达式>`，供后续 open(name) 解析（单函数内线性传播）。"""
        try:
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                _name = node.targets[0].id
                _p, _static = _static_path_of(node.value, self.project_root,
                                              self._local_paths)
                if _p:
                    self._local_paths[_name] = (_p, _static)
                else:
                    self._local_paths.pop(_name, None)
        except Exception as e:  # 赋值解析失败不应中断扫描
            _logger.debug("赋值路径解析失败 %s:%s: %s: %s",
                          self.rel_file, getattr(node, "lineno", "?"),
                          type(e).__name__, e)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    # -- 调用点判定 --
    def visit_Call(self, node: ast.Call) -> None:
        self.call_count += 1
        try:
            self._classify(node)
        except Exception as e:  # 单个调用点解析失败不应中断扫描
            _logger.debug("调用点判定失败 %s:%s: %s: %s",
                          self.rel_file, getattr(node, "lineno", "?"),
                          type(e).__name__, e)
        self.generic_visit(node)

    def _record(self, bucket: list, kind: str, path: str | None, how: str,
                line: int, is_static: bool) -> None:
        _row = {
            "kind": kind,
            "location": self.ctx.location,
            "file": self.rel_file,
            "line": line,
            "how": how,
            "static": bool(is_static),
        }
        if path:
            _row["path"] = path
            bucket.append(_row)
        else:
            self.dynamic.append(_row)

    def _classify(self, node: ast.Call) -> None:
        _name = _dotted(node.func)
        _last = _attr_name(node.func)
        _line = int(getattr(node, "lineno", 0) or 0)
        _args = node.args

        def _first_path():
            if not _args:
                return None, True
            return _static_path_of(_args[0], self.project_root, self._local_paths)

        # ---- open(path, mode) ----
        if _last == "open" and _args:
            _path, _static = _static_path_of(_args[0], self.project_root, self._local_paths)
            _mode = ""
            if len(_args) > 1 and isinstance(_args[1], ast.Constant) \
                    and isinstance(_args[1].value, str):
                _mode = _args[1].value
            _is_write = any(c in _mode for c in _WRITE_CHARS)
            _kind = "produce" if _is_write else "consume"
            _how = "open(%s)" % (_mode or "r")
            self._record(self.producers if _is_write else self.consumers,
                         _kind, _path, _how, _line, _static)
            return

        # ---- json.dump / json.load ----
        if _name.endswith("json.dump"):
            # json.dump(obj, f)：路径来自文件对象，静态不可知 → 依赖 open() 判定
            return
        if _name.endswith("json.load"):
            return   # 同上，由 open() 记录

        # ---- numpy ----
        if _last in ("savez", "savez_compressed", "save") and _name.startswith("np"):
            _path, _static = _first_path()
            self._record(self.producers, "produce", _path,
                         "np.%s" % _last, _line, _static)
            return
        if _last in ("load", "loadtxt", "genfromtxt") and _name.startswith("np"):
            _path, _static = _first_path()
            self._record(self.consumers, "consume", _path,
                         "np.%s" % _last, _line, _static)
            return

        # ---- pandas ----
        if _last in ("to_csv", "to_json", "to_parquet", "to_pickle", "to_excel"):
            _path, _static = _first_path()
            self._record(self.producers, "produce", _path,
                         "df.%s" % _last, _line, _static)
            return
        if _last in ("read_csv", "read_json", "read_parquet", "read_pickle",
                     "read_excel"):
            _path, _static = _first_path()
            self._record(self.consumers, "consume", _path,
                         "pd.%s" % _last, _line, _static)
            return

        # ---- shutil.copy(src, dst) ----
        if _last in ("copy", "copy2", "copyfile", "move") \
                and _name.startswith("shutil"):
            if len(_args) >= 2:
                _src, _s_static = _static_path_of(_args[0], self.project_root, self._local_paths)
                _dst, _d_static = _static_path_of(_args[1], self.project_root, self._local_paths)
                self._record(self.consumers, "consume", _src,
                             "shutil.%s(src)" % _last, _line, _s_static)
                self._record(self.producers, "produce", _dst,
                             "shutil.%s(dst)" % _last, _line, _d_static)
            return

        # ---- 方法名启发式（save/write/dump... / load/read/fetch...）----
        _low = _last.lower()
        _is_producer = any(_low == m or _low.startswith((m + "_", "_" + m)) for m in _PRODUCER_METHODS)
        _is_consumer = any(_low == m or _low.startswith((m + "_", "_" + m)) for m in _CONSUMER_METHODS)
        if _is_producer or _is_consumer:
            # 只有当首个实参"看起来是路径"时才记录，避免 f.write(content) 误报
            if _args:
                _a0 = _args[0]
                _pathy = (
                    (isinstance(_a0, ast.Constant) and isinstance(_a0.value, str))
                    or (isinstance(_a0, ast.Attribute) and any(
                        h in _a0.attr.lower() for h in _PATH_HINTS))
                    or (isinstance(_a0, ast.Name) and any(
                        h in _a0.id.lower() for h in _PATH_HINTS))
                )
                if not _pathy:
                    return
            else:
                return
            _path, _static = _static_path_of(_args[0], self.project_root, self._local_paths)
            if _is_producer and not _is_consumer:
                self._record(self.producers, "produce", _path,
                             "method:%s" % _last, _line, _static)
            elif _is_consumer and not _is_producer:
                self._record(self.consumers, "consume", _path,
                             "method:%s" % _last, _line, _static)


def _iter_scan_files(project_root: str, scan_dirs: list[str]) -> list[str]:
    """展开扫描范围内所有 .py 文件（绝对路径）。"""
    _out: list[str] = []
    for _entry in scan_dirs:
        # ★第18批：显式列入 scan_dirs 的 tests/tmp 同样排除
        #   （任务书要求「扫描排除 tests/、tmp/、.bak_*/」）
        _base = os.path.basename(str(_entry).rstrip("/\\"))
        if _base in _EXCLUDE_DIRS or any(
                _base.startswith(x) for x in _EXCLUDE_DIR_PREFIXES):
            continue
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


class ProductionConsumptionMatcher:
    """产出-消费静态配对器。"""

    def __init__(self, project_root: str | None = None,
                 scan_dirs: list[str] | None = None) -> None:
        self.project_root = project_root or os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        self.scan_dirs = list(scan_dirs or _scan_dirs())

    # ------------------------------------------------------------------
    def scan(self) -> dict[str, Any]:
        """执行一次静态扫描，返回结构化结果（不落盘）。"""
        _t0 = time.perf_counter()
        _files = _iter_scan_files(self.project_root, self.scan_dirs)
        _map: dict[str, dict[str, Any]] = {}
        _dynamic: list[dict[str, Any]] = []
        _parse_errors: list[dict[str, str]] = []
        _total_calls = 0

        for _abs in _files:
            _rel = os.path.relpath(_abs, self.project_root).replace(os.sep, "/")
            _mod = _rel[:-3].replace("/", ".")
            try:
                with open(_abs, encoding="utf-8", errors="replace") as f:
                    _src = f.read()
                _tree = ast.parse(_src, filename=_rel)
            except SyntaxError as e:
                _parse_errors.append({"file": _rel, "error": "SyntaxError: %s" % e})
                continue
            except Exception as e:  # 单文件失败不中断全盘扫描
                _parse_errors.append({"file": _rel,
                                      "error": "%s: %s" % (type(e).__name__, e)})
                continue

            _col = _CallCollector(_mod, _rel, self.project_root)
            _col.visit(_tree)
            _total_calls += _col.call_count
            _dynamic.extend(_col.dynamic)
            for _row in _col.producers:
                _entry = _map.setdefault(_row["path"], _blank_entry(_row["path"]))
                _entry["producers"].append(_row)
            for _row in _col.consumers:
                _entry = _map.setdefault(_row["path"], _blank_entry(_row["path"]))
                _entry["consumers"].append(_row)

        # ★T2：源码通道来源标记（磁盘通道稍后追加 "disk"）
        _src_paths = len(_map)
        for _e in _map.values():
            _e["sources"] = ["source"]

        # ★T2（P2-211）：磁盘枚举通道 —— 与源码通道**求并集**
        _disk_files = 0
        _disk_data_files = 0
        if _scan_disk_enabled():
            try:
                _disk_paths = _iter_disk_data_files(
                    self.project_root, _disk_roots(), _disk_extensions(),
                    _disk_max_depth())
            except Exception as e:      # 磁盘通道失败不影响源码通道结果
                _disk_paths = []
                _logger.warning("磁盘枚举通道失败（仅用源码通道）: %s: %s",
                                type(e).__name__, e)
            _disk_files = len(_disk_paths)
            _disk_data_files = len([p for p in _disk_paths if p.startswith("data/")])
            for _rel in _disk_paths:
                _entry = _map.setdefault(_rel, _blank_entry(_rel))
                _sy = _entry.setdefault("sources", [])
                if "disk" not in _sy:
                    _sy.append("disk")
        else:
            _logger.debug("产出-消费磁盘枚举通道关闭"
                          "（PRODUCTION_CONSUMPTION_SCAN_DISK=False）")

        for _path, _entry in _map.items():
            self._finalize(_path, _entry)

        _elapsed_ms = round((time.perf_counter() - _t0) * 1000.0, 2)
        _result = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "project_root": self.project_root,
            "scan_dirs": list(self.scan_dirs),
            "scanned_files": len(_files),
            "total_calls": _total_calls,
            "elapsed_ms": _elapsed_ms,
            "files": _map,
            "dynamic_calls": _dynamic,
            "parse_errors": _parse_errors,
            "summary": self._summarize(_map, _dynamic, _elapsed_ms,
                                       _src_paths, _disk_files, _disk_data_files),
        }
        # ★T4（P2-218）：措辞消歧 —— 原「%d 文件」的数是**源码**文件数，
        #   与 summary.data_files（数据路径数）极易混读，现逐项标明口径。
        _s = _result["summary"]
        _cov = _s.get("coverage_ratio")
        _logger.info(
            "产出-消费扫描完成: %d 源码文件 / %d 源码数据路径 / %d 磁盘数据文件 / "
            "并集 %d / 覆盖率 %s / %d 调用点 / %.1fms",
            len(_files), _src_paths, _disk_files, _s["union_files"],
            ("%.1f%%" % (_cov * 100.0)) if _cov is not None else "N/A",
            _total_calls, _elapsed_ms)
        return _result

    # ------------------------------------------------------------------
    @staticmethod
    def _summarize(_map: dict, _dynamic: list, _elapsed_ms: float,
                   _src_paths: int = 0, _disk_files: int = 0,
                   _disk_data_files: int = 0) -> dict[str, Any]:
        """汇总统计。

        ★主线第37批 T2（P2-211）/ T3（P2-212）：新增**来源口径**与**路径解析口径**，
        让「覆盖率」「误报率」可复现、可比较。

        · `coverage_ratio` = 并集 / 磁盘数据文件数（第36批口径为 源码路径/磁盘文件
          = 17/2321 = 0.73%，本批补齐磁盘通道后应 > 80%）；
        · `false_positive_rate` = 「无消费」中 size 为 None（即解析后不存在）的占比。
        """
        _cats: dict[str, int] = {}
        _src_only = 0
        _exc_by_reason: dict[str, int] = {}
        # ★第39批 T1（P2-238）：白名单**豁免**计数（这些条目**在** no_consumer 中）
        _exempted = 0
        _exempted_by_reason: dict[str, int] = {}
        for _e in _map.values():
            _cats[_e["category"]] = _cats.get(_e["category"], 0) + 1
            if "disk" not in (_e.get("sources") or []):
                _src_only += 1
            _rk = str(_e.get("exclude_reason", "") or "")
            if _e.get("category") == "excluded":
                _exc_by_reason[_rk or "unknown"] = \
                    _exc_by_reason.get(_rk or "unknown", 0) + 1
            elif _rk.endswith("_exempted"):
                _exempted += 1
                _exempted_by_reason[_rk] = _exempted_by_reason.get(_rk, 0) + 1
        _union = len(_map)
        _no_c = [e for e in _map.values() if e["category"] == "no_consumer"]
        _nc_missing = len([e for e in _no_c if e.get("size") is None])
        # ★T5（P2-231）：信噪比 —— 有效无消费 /（有效无消费 + 未排除的磁盘独有）
        _eff = len([e for e in _no_c if e["producers"]])
        _diskonly = len([e for e in _no_c if not e["producers"]])
        return {
            "data_files": _union,
            "no_consumer": len(_no_c),
            # ★T4/T5：排除统计
            "excluded": _cats.get("excluded", 0),
            "excluded_by_reason": dict(sorted(_exc_by_reason.items())),
            "excluded_self_observation": _exc_by_reason.get("self_observation", 0),
            # ★第39批 T1：白名单**豁免**（真问题被保留的条数）
            "ext_whitelist_exempted": _exempted_by_reason.get(
                "ext_whitelist_exempted", 0),
            "dir_whitelist_exempted": _exempted_by_reason.get(
                "dir_whitelist_exempted", 0),
            "whitelist_exempted_total": _exempted,
            "exempted_by_reason": dict(sorted(_exempted_by_reason.items())),
            "effective_no_consumer": _eff,
            "disk_only_unexcluded": _diskonly,
            # ★双口径（第38批 T5）：
            #   · signal_ratio         = 有效无消费 /（有效无消费 + 未排除的磁盘独有）
            #                             —— 「剩下的条目里有多少是真问题」
            #   · noise_filtered_ratio = 已排除噪声 /（已排除噪声 + 未排除的磁盘独有）
            #                             —— 「噪声被白名单识别隔离的比例」
            #   任务书 §T5.5「信噪比 > 50%」按**后者**口径校核（排除白名单的
            #   直接目的即是隔离噪声）；前者同时如实输出，供上游判断。
            "signal_ratio": (round(_eff / (_eff + _diskonly), 4)
                             if (_eff + _diskonly) > 0 else None),
            "noise_filtered_ratio": (round(_cats.get("excluded", 0)
                                           / (_cats.get("excluded", 0) + _diskonly), 4)
                                     if (_cats.get("excluded", 0) + _diskonly) > 0
                                     else None),
            "no_producer": _cats.get("no_producer", 0),
            "normal": _cats.get("normal", 0),
            "unknown": _cats.get("unknown", 0),
            "dynamic_calls": len(_dynamic),
            "elapsed_ms": _elapsed_ms,
            # ★T2：来源口径
            "source_paths": _src_paths,
            "source_only_paths": _src_only,
            "disk_files": _disk_files,
            "disk_data_files": _disk_data_files,
            "union_files": _union,
            "coverage_ratio": (round(_union / _disk_files, 4)
                               if _disk_files else None),
            # ★T3：路径解析口径
            "dynamic_path": _cats.get("dynamic_path", 0),
            "path_not_found": _cats.get("path_not_found", 0),
            "no_consumer_missing": _nc_missing,
            "false_positive_rate": (round(_nc_missing / len(_no_c), 4)
                                   if _no_c else 0.0),
            # ★T2b：把「无消费」细分 —— 只有 with_producer 才是"写了没人读"的真问题
            "no_consumer_with_producer": len([e for e in _no_c if e["producers"]]),
            "no_consumer_disk_only": len([e for e in _no_c if not e["producers"]]),
        }

    def _finalize(self, path: str, entry: dict[str, Any]) -> None:
        """补齐绝对路径、大小、来源、分类与建议。

        ★主线第37批 T3（P2-212）：修复误报 —— 原 11 条「疑似无消费」中 7 条指向
        不存在的文件（误报率 64%，超 20% 阈值）。新判定顺序：

            ① **动态路径模板**（含 ``{}``/``%s`` 等占位符）→ ``dynamic_path``
               （运行时才确定，保守起见不做消费判定）；
            ② 解析为绝对路径后**文件不存在** → ``path_not_found``
               （不是「无消费」，而是「路径未命中」，需人工确认）；
            ③ 文件**存在** → 按产出/消费方判定；其中★经磁盘枚举命中的文件视为
               「确已产出」（运行时真的写过它），故无消费方即 ``no_consumer``。
        """
        entry.setdefault("sources", [])
        # ⓪ ★第38批 T4/T5：排除白名单（自我观察 / 目录 / 扩展名）→ excluded
        #   在不计入 no_consumer 的前提下单列，报告区分三类（任务书 §T5.4）
        #   ★第39批 T1（P2-238）：扩展名白名单**豁免源码产出方命中的路径** ——
        #   ``ext_whitelist_exempted`` 仅作标记、**不排除**，继续走正常分类
        #   （否则 `logs/pulse_crash.log` 这类「写了没人读」的真问题会被噪声白名单吞掉）。
        _reason = exclude_reason(path, has_producer=bool(entry.get("producers")))
        if _reason.endswith("_exempted"):
            entry["exclude_reason"] = _reason
        elif _reason:
            entry["size"] = None
            entry["abs_path"] = None
            entry["path_kind"] = "excluded"
            entry["category"] = "excluded"
            entry["exclude_reason"] = _reason
            entry["suggestion"] = _EXCLUDE_SUGGEST.get(_reason, "按排除白名单跳过")
            return
        # ① 动态路径模板（占位符 → 非确定路径）
        if any(_m in path for _m in _DYNAMIC_PATH_MARKS):
            entry["size"] = None
            entry["abs_path"] = None
            entry["path_kind"] = "dynamic"
            entry["category"] = "dynamic_path"
            entry["suggestion"] = ("动态路径模板（含占位符）：运行时才能确定，"
                                   "不做消费判定（保守策略）")
            return
        # ② 绝对路径解析（Windows/Linux 分隔符兼容）+ 存在性
        _norm = path.replace("/", os.sep).replace("\\", os.sep)
        _abs = _norm if os.path.isabs(_norm) else os.path.join(self.project_root, _norm)
        try:
            _exists = os.path.isfile(_abs)
            _size = os.path.getsize(_abs) if _exists else None
        except Exception as e:      # 取大小失败不影响分类
            _exists = False
            _size = None
            _logger.debug("路径解析/取大小失败 %r: %s: %s", path, type(e).__name__, e)
        entry["abs_path"] = _abs
        entry["size"] = _size
        entry["path_kind"] = "existing" if _exists else "missing"
        if not _exists:
            entry["category"] = "path_not_found"
            entry["suggestion"] = ("路径未命中：解析后文件不存在（动态命名/相对路径"
                                   "偏差）→ 非有效结论，需人工确认")
            return
        # ③ 存在文件：正常分类
        _p, _c = len(entry["producers"]), len(entry["consumers"])
        _from_disk = "disk" in entry["sources"]
        # ★语义修正（第37批 T2/T3）：磁盘枚举只能证明「文件**存在**」，
        #   即确有产出（运行时被写过）；它**不能**证明代码写了它——
        #   可能是人工放置 / 外部程序写的。
        #   故：判定 no_consumer 时认可磁盘来源（存在即算有产出）；
        #       判定 **no_producer**（读了没人写）时**只认源码里的产出调用**，
        #       否则磁盘上的文件会永远不可能是 no_producer。
        _has_output = bool(_p) or _from_disk
        if _has_output and not _c:
            entry["category"] = "no_consumer"
            # ★T2b：区分「代码写了没人读」（真问题）与「磁盘上有但源码未引用」
            #   （多为运行产物 / 文档 / 手工文件）—— 后者直接建议"删除/补消费方"会误导。
            if _p:
                entry["suggestion"] = ("疑似无消费：确认是否废弃 → 删除 / 归档 / 补消费方")
            else:
                entry["suggestion"] = ("磁盘存在但源码未引用（运行产物 / 文档 / "
                                       "手工文件？）→ 确认归属，勿直接删除")
# _m37_t2b
        elif _c and not _p:
            entry["category"] = "no_producer"
            entry["suggestion"] = "疑似无产出：可能是外部输入或遗留读取 → 确认来源"
        elif _has_output and _c:
            entry["category"] = "normal"
            entry["suggestion"] = "无需处理（有产出有消费）"
        else:
            entry["category"] = "unknown"
            entry["suggestion"] = "无产出无消费（仅记录）"

    # ------------------------------------------------------------------
    def build_report(self, result: dict[str, Any] | None = None) -> str:
        """生成可读文本报告（疑似无消费按文件大小降序排最前）。"""
        _r = result if result is not None else self.scan()
        _s = _r["summary"]
        _lines = [
            "=" * 68,
            "曈曈 PulseNet · 产出-消费配对报告",
            "生成时间: %s" % _r["generated_at"],
            "扫描范围: %s" % ", ".join(_r["scan_dirs"]),
            "=" * 68,
            "",
            # ★T2（P2-218）：来源口径分开显示，避免「255 文件」与「数据文件 17」
            #   被混读（原措辞歧义即此）
            "【扫描渠道】源码数据路径 %d 个 / 磁盘数据文件 %d 个"
            "（其中 data/ %d 个）/ 并集 %d 个 / 覆盖率 %s"
            % (_s.get("source_paths", 0), _s.get("disk_files", 0),
               _s.get("disk_data_files", 0),
               _s.get("union_files", _s["data_files"]),
               ("%.1f%%" % (_s["coverage_ratio"] * 100.0))
               if _s.get("coverage_ratio") is not None else "N/A"),
            "【概览】数据文件 %d 个（无消费 %d / 无产出 %d / 正常 %d / "
            "动态路径 %d / 路径未命中 %d），动态调用 %d 处，耗时 %.1fms"
            % (_s["data_files"], _s["no_consumer"], _s["no_producer"],
               _s["normal"], _s.get("dynamic_path", 0),
               _s.get("path_not_found", 0), _s["dynamic_calls"], _s["elapsed_ms"]),
            # ★T2b：无消费细分 —— 只有「写了没人读」才是真问题
            "【无消费细分】写了没人读（源码有产出方）%d 个 / "
            "磁盘存在但源码未引用 %d 个"
            % (_s.get("no_consumer_with_producer", 0),
               _s.get("no_consumer_disk_only", 0)),
            # ★T3：路径解析统计（任务书验收要求）
            "【路径解析】解析路径 %d 个 / 动态路径 %d 个 / 路径未命中 %d 个 / "
            "有效无消费 %d 个（误报率 %.1f%%）"
            % (_s.get("union_files", _s["data_files"]), _s.get("dynamic_path", 0),
               _s.get("path_not_found", 0), _s.get("no_consumer", 0),
               (_s.get("false_positive_rate") or 0.0) * 100.0),
            # ★第38批 T4/T5：信噪比三类（任务书 §T5.4）
            "【信噪比】有效无消费（真问题）%d 个 / 磁盘独有未排除 %d 个 / "
            "已排除噪声 %d 个（自我观察 %d）"
            % (_s.get("effective_no_consumer", 0),
               _s.get("disk_only_unexcluded", 0), _s.get("excluded", 0),
               _s.get("excluded_self_observation", 0)),
            "        · 真问题占比 %s ｜ 噪声过滤率 %s"
            % (("%.1f%%" % (_s["signal_ratio"] * 100.0))
               if _s.get("signal_ratio") is not None else "N/A",
               ("%.1f%%" % (_s["noise_filtered_ratio"] * 100.0))
               if _s.get("noise_filtered_ratio") is not None else "N/A"),
            "【排除明细】%s" % (_s.get("excluded_by_reason") or "无"),
            # ★第39批 T1（P2-238）：扩展名白名单豁免统计
            "【白名单豁免】共豁免 %d 个真问题（%s）"
            "—— 源码有产出方 → 保留在「有效无消费」中，不被 .log/.md/.txt 或运行态目录吞掉"
            % (_s.get("whitelist_exempted_total", 0),
               _s.get("exempted_by_reason") or "无"),
            "",
        ]

        def _dump_rows(rows: list, title: str, sort_by_size: bool = False,
                       limit: int = 80) -> None:
            if not rows:
                return
            if sort_by_size:
                rows = sorted(rows, key=lambda e: (-(e.get("size") or 0), e["path"]))
            else:
                rows = sorted(rows, key=lambda e: e["path"])
            _lines.append("【%s】(%d)" % (title, len(rows)))
            for _e in rows[:limit]:
                _size = _e.get("size")
                _size_s = "未知" if _size is None else _fmt_size(_size)
                _lines.append("  · %s  [%s]" % (_e["path"], _size_s))
                for _p in _e["producers"][:3]:
                    _lines.append("      写 ← %s  (%s:%d, %s)"
                                  % (_p["location"], _p["file"], _p["line"], _p["how"]))
                for _c in _e["consumers"][:3]:
                    _lines.append("      读 → %s  (%s:%d, %s)"
                                  % (_c["location"], _c["file"], _c["line"], _c["how"]))
                _lines.append("      建议: %s" % _e["suggestion"])
            _lines.append("")

        def _dump(cat: str, title: str, sort_by_size: bool = False) -> None:
            _dump_rows([e for e in _r["files"].values() if e["category"] == cat],
                       title, sort_by_size)

        # ★T2：磁盘枚举后「无消费」条数可能较多，先给**目录分布**保证可读性
        _nc_rows = [e for e in _r["files"].values() if e["category"] == "no_consumer"]
        if len(_nc_rows) > 20:
            _by_dir: dict[str, int] = {}
            for _e in _nc_rows:
                _d = _e["path"].rsplit("/", 1)[0] if "/" in _e["path"] else "(根)"
                _by_dir[_d] = _by_dir.get(_d, 0) + 1
            _lines.append("【无消费文件按目录分布】(%d 个目录，共 %d 文件)"
                          % (len(_by_dir), len(_nc_rows)))
            for _d, _n in sorted(_by_dir.items(), key=lambda x: (-x[1], x[0]))[:25]:
                _lines.append("  · %s: %d 个" % (_d, _n))
            _lines.append("")
        # ★T2b：无消费拆两组 —— 真问题（写了没人读）优先，磁盘独有其次
        _dump_rows([e for e in _r["files"].values()
                    if e["category"] == "no_consumer" and e["producers"]],
                   "★写了没人读（源码有产出方、无消费方｜真问题）", True, 60)
        _dump_rows([e for e in _r["files"].values()
                    if e["category"] == "no_consumer" and not e["producers"]],
                   "磁盘存在但源码未引用（运行产物/文档/手工文件）", True, 40)
        # ★T5（P2-231）：已排除文件单列（保留可查性，避免"静默丢弃"）
        _dump("excluded", "已排除（噪声白名单：自我观察/归档文档/测试产物/文档日志）")
        _dump("path_not_found", "路径未命中（解析后不存在，非有效结论）")
        _dump("dynamic_path", "动态路径模板（含占位符，运行时才确定）")
        _dump("no_producer", "疑似无产出（读了没人写）")
        _dump("normal", "正常配对")

        if _r["dynamic_calls"]:
            _lines.append("【动态路径调用】(%d，需运行时验证)" % len(_r["dynamic_calls"]))
            for _d in _r["dynamic_calls"][:40]:
                _lines.append("  · %s  (%s:%d, %s)"
                              % (_d["location"], _d["file"], _d["line"], _d["how"]))
            _lines.append("")

        if _r["parse_errors"]:
            _lines.append("【解析失败文件】(%d)" % len(_r["parse_errors"]))
            for _pe in _r["parse_errors"][:20]:
                _lines.append("  · %s: %s" % (_pe["file"], _pe["error"]))
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
        _json_p = os.path.join(_dir, "production_consumption.json")
        _txt_p = os.path.join(_dir, "production_consumption_report.txt")
        try:
            with open(_json_p, "w", encoding="utf-8") as f:
                json.dump(_r, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            _logger.warning("产出-消费 JSON 落盘失败: %s: %s", type(e).__name__, e)
        try:
            with open(_txt_p, "w", encoding="utf-8") as f:
                f.write(self.build_report(_r))
        except Exception as e:
            _logger.warning("产出-消费报告落盘失败: %s: %s", type(e).__name__, e)
        return {"json": _json_p, "report": _txt_p}


def _blank_entry(path: str) -> dict[str, Any]:
    """新建一条文件级记录。

    ★主线第37批 T2（P2-211）：新增 `sources` 字段记录该路径的来源通道
    （"source"=源码 AST 字面量 / "disk"=磁盘枚举），供报告与覆盖率统计区分。
    """
    return {
        "path": path,
        "size": None,
        "producers": [],
        "consumers": [],
        "sources": [],
        "category": "unknown",
        "suggestion": "",
        # ★T5（P2-231）：排除原因（"" = 未被排除）
        "exclude_reason": "",
    }


def _fmt_size(n: int) -> str:
    """人类可读的文件大小。"""
    try:
        _f = float(n)
        for _unit in ("B", "KB", "MB", "GB"):
            if _f < 1024 or _unit == "GB":
                return ("%.0f %s" % (_f, _unit)) if _unit == "B" else \
                    ("%.1f %s" % (_f, _unit))
            _f /= 1024.0
    except Exception as e:
        _logger.debug("大小格式化失败: %s: %s", type(e).__name__, e)
    return str(n)


#: ★T5（P2-231）：目录排除白名单默认值（相对项目根的 POSIX 前缀）
#: ★第39批 T2（P2-240）：追加**运行态目录** —— 这些是框架运行时自动生成的数据文件，
#:   "源码未引用"是**常态**而非缺陷；第38批实测它们贡献了 135 条未排除的磁盘独有噪声。
#:   · data/context            对话上下文快照
#:   · data/knowledge          知识图谱 / 索引 / parquet 快照
#:   · data/evolution          进化数据（含 test_runs，见上一条）
#:   · data/stream             流式日志（hormone_emotion_log 等）
#:   · data/runtime_trajectory 运行轨迹
#:   ★刻意**不含** `data/root 其他目录` 与 `data/self_awareness`（后者由 T4 单独机制处理）。
_EXCLUDE_DIRS_DEFAULT = ("data/evolution/test_runs", "data/probe", "docs", "tmp",
                         "data/context", "data/knowledge", "data/evolution",
                         "data/stream", "data/runtime_trajectory",
                         "nucleus/data",
                         "data/patches", "data/qica", "data/metrics",
                         "data/monitor", "data/param_tuning", "data/wiki_cache",
                         "data/experience", "data/learning", "data/reasoning",
                         "data/rss_cache", "data/stream_miner")
#: ★P2-258：data根下的运行态文件白名单（无子目录可依，目录白名单无法覆盖）
#:   这些是框架运行时自动生成的JSON文件，"源码未引用"是常态而非缺陷。
#:   遵循第39批T1裁决：有源码产出方的文件标记为豁免（保留为真问题），无产出方的排除。
_EXCLUDE_FILES_DEFAULT = (
    "data/channel_quota_usage.json",      # 额度监控用量记录
    "data/config_override.json",          # 配置热重载覆盖
    "data/false_positive_rules.json",     # 误报规则
    "data/fuse_cooldown.json",            # 熔断器冷却
    "data/growth_comparisons.json",       # 成长对比
    "data/identity_knowledge.json",       # 身份知识
    "data/param_patch_history.json",      # 参数补丁历史
    "data/param_presets.json",            # 参数预设
    "data/probe_strategy.json",           # 探针策略
    "data/pulse_instinct_snapshot.json",  # 本能快照
    "data/runtime_state.json",            # 运行时状态
    "data/semantic_lessons.json",         # 语义经验
    "data/tool_strategy_memory.json",     # 工具策略记忆
    "data/verification_learning.json",    # 验证学习
)
#: ★T5（P2-231）：扩展名排除白名单默认值（明确的文档 / 日志类）
_EXCLUDE_EXTS_DEFAULT = (".md", ".txt", ".log")
#: ★T4（P2-217）：自我观察目录默认值（引擎自身产物）
_SELF_OBSERVE_DEFAULT = ("data/self_awareness",)

#: ★T5：排除原因 → 建议文案
_EXCLUDE_SUGGEST = {
    "self_observation": "引擎自身产物（自我指涉噪声）→ 不纳入产出-消费统计",
    "dir_whitelist": "目录命中排除白名单（归档文档 / 测试产物 / 探针 / 运行态目录）→ 噪声",
    "ext_whitelist": "扩展名命中排除白名单（文档 / 日志）→ 噪声",
    # ★T1：豁免 ≠ 排除 —— 该路径**保留**在 no_consumer 中（真问题）
    "ext_whitelist_exempted": "扩展名白名单**豁免**（源码有产出方 → 真问题，保留）",
    "dir_whitelist_exempted": "目录白名单**豁免**（源码有产出方 → 真问题，保留）",
    "file_whitelist": "文件命中排除白名单（data根运行态文件）→ 噪声",
    "file_whitelist_exempted": "文件白名单**豁免**（源码有产出方 → 真问题，保留）",
}


def _norm_prefix(x: Any) -> str:
    """归一化相对路径前缀：反斜杠 → 正斜杠、去首尾斜杠、小写。"""
    return str(x or "").replace("\\", "/").strip().strip("/").lower()


def _match_prefix(rel_path: str, prefixes: tuple) -> bool:
    """判断相对路径是否位于任一前缀目录内（精确匹配该目录本身或其子项）。"""
    _p = str(rel_path or "").replace("\\", "/").lstrip("./").lower()
    if not _p:
        return False
    for _pre in prefixes:
        if _pre and (_p == _pre or _p.startswith(_pre + "/")):
            return True
    return False


def _exclude_enabled() -> bool:
    """★T5：排除白名单总开关（``ENABLE_PRODUCTION_CONSUMPTION_EXCLUDE``，默认 True）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_PRODUCTION_CONSUMPTION_EXCLUDE", True))
    except Exception as e:
        _logger.debug("读取排除白名单开关失败，按开启处理: %s: %s",
                      type(e).__name__, e)
        return True


def _exclude_dirs() -> tuple:
    """★T5（P2-231）：目录排除白名单（配置可扩展，默认见 `_EXCLUDE_DIRS_DEFAULT`）。"""
    try:
        import config
        _v = getattr(config, "PRODUCTION_CONSUMPTION_EXCLUDE_DIRS", None)
        if isinstance(_v, (list, tuple)):
            return tuple(_norm_prefix(x) for x in _v if str(x).strip())
    except Exception as e:
        _logger.debug("读取目录排除白名单失败，用默认值: %s: %s",
                      type(e).__name__, e)
    return _EXCLUDE_DIRS_DEFAULT


def _exclude_extensions() -> tuple:
    """★T5（P2-231）：扩展名排除白名单（默认 ``.md`` / ``.txt`` / ``.log``）。

    ★注意（任务书 §T5 风险提示）：``.json``/``.jsonl``/``.npz``/``.db`` 等
    **数据文件不排除** —— 它们是产出-消费分析的主体。
    """
    try:
        import config
        _v = getattr(config, "PRODUCTION_CONSUMPTION_EXCLUDE_EXTENSIONS", None)
        if isinstance(_v, (list, tuple)):
            return tuple(str(x).lower() if str(x).startswith(".")
                         else "." + str(x).lower()
                         for x in _v if str(x).strip())
    except Exception as e:
        _logger.debug("读取扩展名排除白名单失败，用默认值: %s: %s",
                      type(e).__name__, e)
    return _EXCLUDE_EXTS_DEFAULT


def _exclude_files() -> tuple:
    """★P2-258：文件级排除白名单（data根下的运行态文件，无子目录可依）。

    配置项：``PRODUCTION_CONSUMPTION_EXCLUDE_FILES``（可选，覆盖默认值）。
    遵循第39批T1裁决：有源码产出方的文件标记为豁免（保留为真问题）。
    """
    try:
        import config
        _v = getattr(config, "PRODUCTION_CONSUMPTION_EXCLUDE_FILES", None)
        if isinstance(_v, (list, tuple)):
            return tuple(_norm_prefix(x) for x in _v if str(x).strip())
    except Exception as e:
        _logger.debug("读取文件排除白名单失败，用默认值: %s: %s",
                      type(e).__name__, e)
    return _EXCLUDE_FILES_DEFAULT


def _self_observe_dirs() -> tuple:
    """★T4（P2-217）：自我观察目录（引擎自身产物，默认 ``data/self_awareness``）。"""
    try:
        import config
        if not bool(getattr(config, "ENABLE_SELF_AWARENESS_EXCLUDE", True)):
            return ()
        _v = getattr(config, "SELF_AWARENESS_EXCLUDE_DIRS", None)
        if isinstance(_v, (list, tuple)):
            return tuple(_norm_prefix(x) for x in _v if str(x).strip())
    except Exception as e:
        _logger.debug("读取自我观察排除配置失败，用默认值: %s: %s",
                      type(e).__name__, e)
    return _SELF_OBSERVE_DEFAULT


def exclude_reason(rel_path: str, has_producer: bool = False) -> str:
    """★T4/T5：返回该相对路径的排除原因（``""`` 表示不排除）。

    ★主线第39批 T1（P2-238）：新增 ``has_producer`` —— **扩展名白名单豁免真问题**。
    第38批实测：``logs/pulse_crash.log``（6.49MB，源码有产出方、无消费方）被 ``.log``
    白名单排除，导致"写了没人读"的真问题不可见。星轨裁决（债务清单 §73.3-①）
    采用选项 (c)：**扩展名白名单不作用于源码产出方命中的路径**。

    Args:
        rel_path: 相对项目根的 POSIX 路径。
        has_producer: 该路径在**源码通道**中是否有产出方（``entry["producers"]`` 非空）。

    Returns:
        ``"self_observation"`` / ``"dir_whitelist"`` / ``"ext_whitelist"`` /
        ``"ext_whitelist_exempted"``（**豁免**：标记但**不排除**，保留在 no_consumer）/ ``""``
    """
    if not _exclude_enabled():
        return ""
    if _match_prefix(rel_path, _self_observe_dirs()):
        return "self_observation"
    if _match_prefix(rel_path, _exclude_dirs()):
        # ★T1 同原则（第39批主动扩展）：目录白名单**同样豁免源码产出方命中的路径** ——
        #   否则 T2 新纳入的 data/stream 等目录会把「写了没人读」的真问题一并吞掉
        #   （实测 data/stream/hormone_emotion_log.json / legs_learn_log.json 两条）。
        return "dir_whitelist_exempted" if has_producer else "dir_whitelist"
    # ★P2-258：文件级白名单（data根下的运行态文件）
    _norm_path = _norm_prefix(rel_path)
    if _norm_path and _norm_path in _exclude_files():
        # ★同T1原则：有源码产出方 → 豁免（真问题优先于文件白名单）
        return "file_whitelist_exempted" if has_producer else "file_whitelist"
    _ext = os.path.splitext(str(rel_path or ""))[1].lower()
    if _ext and _ext in _exclude_extensions():
        # ★T1：有源码产出方 → 豁免（真问题优先于扩展名白名单）
        return "ext_whitelist_exempted" if has_producer else "ext_whitelist"
    return ""


def _scan_disk_enabled() -> bool:
    """★T2：磁盘枚举通道开关（`PRODUCTION_CONSUMPTION_SCAN_DISK`，默认 True）。"""
    try:
        import config
        return bool(getattr(config, "PRODUCTION_CONSUMPTION_SCAN_DISK", True))
    except Exception as e:
        _logger.debug("读取磁盘枚举开关失败，按开启处理: %s: %s", type(e).__name__, e)
        return True


def _disk_roots() -> list[str]:
    """★T2：磁盘枚举根目录（相对项目根，默认 ["."]）。"""
    try:
        import config
        _v = getattr(config, "PRODUCTION_CONSUMPTION_DISK_ROOTS", None)
        if isinstance(_v, (list, tuple)) and _v:
            return [str(x) for x in _v]
    except Exception as e:
        _logger.debug("读取磁盘枚举根失败，用默认值: %s: %s", type(e).__name__, e)
    return list(_DISK_DEFAULT_ROOTS)


def _disk_extensions() -> tuple:
    """★T2：磁盘数据文件扩展名白名单（默认 `_DISK_DEFAULT_EXTS`）。"""
    try:
        import config
        _v = getattr(config, "PRODUCTION_CONSUMPTION_SCAN_EXTENSIONS", None)
        if isinstance(_v, (list, tuple)) and _v:
            return tuple(str(x).lower() for x in _v)
    except Exception as e:
        _logger.debug("读取扩展名白名单失败，用默认值: %s: %s", type(e).__name__, e)
    return _DISK_DEFAULT_EXTS


def _disk_max_depth() -> int:
    """★T2：磁盘枚举深度上限（0=不限）。"""
    try:
        import config
        return int(getattr(config, "PRODUCTION_CONSUMPTION_DISK_MAX_DEPTH", 0) or 0)
    except Exception:
        return 0


def _iter_disk_data_files(project_root: str, roots: list[str],
                          exts: tuple, max_depth: int = 0) -> list[str]:
    """★T2（P2-211）：枚举磁盘上的数据文件，返回**相对项目根的 POSIX 路径**。

    ★性能：只读目录项与文件名，**不读文件内容**（大目录实测 <1s）。

    参数:
        project_root: 项目根（绝对路径）。
        roots: 枚举根（相对项目根，如 ["data", "logs"] 或 ["."]）。
        exts: 扩展名白名单（小写，含点）。
        max_depth: 深度上限（相对根，0=不限）。

    返回:
        排序后的相对路径列表（POSIX 分隔符），已按排除规则过滤、已去重。

    示例:
        >>> _iter_disk_data_files(".", ["data"], (".json",))[:2]   # doctest: +SKIP
        ['data/aaa.json', 'data/bbb.json']
    """
    _out: list[str] = []
    _seen: set[str] = set()
    _base = os.path.abspath(project_root).rstrip(os.sep)
    for _entry in roots:
        _p = os.path.join(_base, str(_entry).replace("/", os.sep))
        if not os.path.isdir(_p):
            continue
        for _dp, _dirs, _files in os.walk(_p):
            _dirs[:] = [d for d in _dirs
                        if d not in _DISK_EXCLUDE_DIRS
                        and not any(d.startswith(x) for x in _EXCLUDE_DIR_PREFIXES)]
            if max_depth > 0:
                # ★深度语义：max_depth=1 表示「根 + 第一层子目录」。
                #   os.walk 是「先 yield 当前目录、再按其 dirs 下钻」，
                #   故必须在父层就置空 dirs，才能挡住更深一层。
                _rel = os.path.relpath(_dp, _p)
                _depth = 0 if _rel == "." else _rel.count(os.sep) + 1
                if _depth >= max_depth:
                    _dirs[:] = []
            for _fn in _files:
                if not _fn.lower().endswith(exts):
                    continue
                _rel = os.path.relpath(os.path.join(_dp, _fn),
                                       _base).replace(os.sep, "/")
                if _rel in _seen:
                    continue
                _seen.add(_rel)
                _out.append(_rel)
    _out.sort()
    return _out


def _scan_dirs() -> list[str]:
    """从 config 读取扫描范围（默认 nucleus/organs/functions/main.py）。"""
    try:
        import config
        _v = getattr(config, "PRODUCTION_CONSUMPTION_SCAN_DIRS", None)
        if isinstance(_v, (list, tuple)) and _v:
            return [str(x) for x in _v]
    except Exception as e:
        _logger.debug("读取扫描范围配置失败，用默认值: %s: %s", type(e).__name__, e)
    return ["nucleus", "organs", "functions", "main.py"]


def matcher_enabled() -> bool:
    """读取配对器总开关（默认 True）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_PRODUCTION_CONSUMPTION_MATCHER", True))
    except Exception as e:
        _logger.debug("读取配对器开关失败，按开启处理: %s: %s", type(e).__name__, e)
        return True


def analyze_production_consumption(engine: Any = None) -> dict:
    """分析器入口（供 SelfAwarenessEngine 注册）。

    开关关闭时返回空 dict（零扫描开销）。
    """
    if not matcher_enabled():
        _logger.debug("产出-消费配对器开关关闭，跳过扫描")
        return {}
    _m = ProductionConsumptionMatcher()
    _r = _m.scan()
    return {
        "summary": _r["summary"],
        "no_consumer": [e["path"] for e in _r["files"].values()
                        if e["category"] == "no_consumer"][:50],
        "no_producer": [e["path"] for e in _r["files"].values()
                        if e["category"] == "no_producer"][:50],
        # ★主线第37批 T3（P2-212）：路径解析结果单列（不再混入「无消费」）
        "path_not_found": [e["path"] for e in _r["files"].values()
                           if e["category"] == "path_not_found"][:50],
        "dynamic_path": [e["path"] for e in _r["files"].values()
                         if e["category"] == "dynamic_path"][:50],
        "scanned_files": _r["scanned_files"],
        "elapsed_ms": _r["elapsed_ms"],
        # ★第38批 T4/T5：排除口径 + 信噪比透传
        "excluded": [e["path"] for e in _r["files"].values()
                     if e["category"] == "excluded"][:50],
        "excluded_count": _r["summary"].get("excluded", 0),
        "effective_no_consumer": _r["summary"].get("effective_no_consumer", 0),
        "signal_ratio": _r["summary"].get("signal_ratio"),
        # ★第39批 T1：扩展名白名单豁免（真问题保留数）
        "ext_whitelist_exempted": _r["summary"].get("ext_whitelist_exempted", 0),
    }
# _m49_t5_pc_body_done
# _m49_t5_pc_disk_done
# _m49_t5_pc_imp_done
