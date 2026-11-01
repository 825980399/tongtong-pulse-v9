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
from concurrent.futures import ThreadPoolExecutor  # ★405刀1：分片并行

from nucleus._silent_except import silent_exc  # ★326批刀5：A5 异常口径与全项目一致
from nucleus.proc_gen import proc_gen_str as _m335_pg
from nucleus.data.exclude_dirs import CALL_GRAPH_EXCLUDED  # ★第49批 T5
from nucleus.data.exclude_dirs import is_call_graph_excluded  # ★397刀2
from nucleus.data.path_utils import (
    safe_relpath as _safe_relpath,  # ★第55批 T3（跨盘安全，同盘行为与 os.path.relpath 一致）
    sanitize_sys_path as _sanitize_sys_path,  # ★323批刀0：副本导入防线
)
from nucleus.logger import (
    get_module_logger,
)
from nucleus.logger import (
    noise_reduction_enabled as _noise_reduce,
)

_logger = get_module_logger("CallGraphAnalyzer")

# ★405刀1：文件签名（(mtime_ns, size)）与缓存共用一份实现
from nucleus.self_awareness.AnalyzerCache import file_sig as _file_sig

#: ★323批刀0：导入期就地剔除 sys.path 里的备份/打包目录（越早越好——
#: 晚一步，同进程后续的 `import nucleus.*` 仍可能命中副本）
_M323_SYS_PATH_PRUNED = _sanitize_sys_path()

#: ★第313批 刀2：本进程内已完成「存量合成图隔离清扫」的目录
#: （每目录只扫一次 —— 清扫要逐份解析整图，不能挂在每次落盘的热路径上反复付费）
_M313_PRUNED_DIRS: set = set()


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
#  ★397刀2：补 `hardware` / `config.py` / `pulse_doctor.py`。
#    这三处原来不在范围内 ⇒ 调用图对它们**没有节点**，用图判"零调用"时是"没采到"。
#    ★改这里**不够**：`config.py::CALL_GRAPH_SCAN_DIRS` 才是运行期生效值（本函数有
#      `from config import CALL_GRAPH_SCAN_DIRS` 覆盖），两处必须一起改。
_DEFAULT_SCAN_DIRS = ("nucleus", "organs", "functions", "base", "utils",
                      "somatics", "hardware", "main.py", "config.py",
                      "pulse_doctor.py")

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
#: ★321批刀1：external **同名通用词表**（显式排除，防"顺手放宽"凭空造边）。
#: 数据出处（烛微321 §3-维度3 实测 2026-10-20）：external 27,400 条中有
#: 1,243 条（4.5%）末段命中仓内方法名，全是 dict/str/builtin 通用名：
#: set 475 / search 343 / format 143 / get 49 / load 49 …。`dict.set` 若被接到仓内
#: `set()` 上就是凭空造边。纪律：命中本表且**接收者不是仓内模块** ⇒ 显式判
#: external，不享受任何兜底（不进 unique_name / typed_infer）。
_EXTERNAL_NAME_ALLOWLIST = frozenset({
    "set", "search", "format", "get", "load", "add", "remove", "pop", "clear", "items",
    "keys", "values", "copy", "write", "read", "readlines", "writelines", "close",
    "append", "extend", "insert", "sort", "reverse", "split", "rsplit", "join",
    "strip", "lstrip", "rstrip", "replace", "startswith", "endswith", "lower", "upper",
    "find", "rfind", "count", "index", "encode", "decode", "match", "group", "groups",
    "sub", "compile", "dumps", "loads", "exists", "isdir", "isfile", "mkdir",
    "makedirs", "listdir", "walk", "process_iter", "wait", "poll", "terminate", "kill",
    "fetchall", "fetchone", "fetchmany", "commit", "rollback", "execute", "executemany",
    "acquire", "release", "notify", "notify_all", "submit", "shutdown", "result",
    "publish", "subscribe", "emit", "connect", "recv", "send", "sendall",
})


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


#: ★323批刀0：项目根标记——两者齐备才算根（config.py + .git/nucleus）
_ROOT_MARK_FILE = "config.py"
_ROOT_MARK_DIRS = (".git", "nucleus")
_ROOT_MAX_UP = 24


def _project_root(start: str | None = None) -> str:
    """定位项目根：**按标记文件向上找**，找不到即抛错（★323批刀0 · P-02）。

    旧实现"上溯 3 层"隐含前提「本文件恒在 <root>/nucleus/self_awareness/」。
    一旦模块从 `.bak_batchNNN/`、`code_package_*/` 的整树副本被 import，前提不成立，
    算出的根落在副本或项目父目录 ⇒ `nucleus/const.py` 读不到 ⇒ 事件枚举表退化，
    event 边随之失真。故改为**只认标记、绝不猜层数**。
    """
    _env = str(os.environ.get("TT_PROJECT_ROOT") or "").strip()
    if _env and os.path.isfile(os.path.join(_env, _ROOT_MARK_FILE)):
        return os.path.abspath(_env)
    _cur = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    for _ in range(_ROOT_MAX_UP):
        if (os.path.isfile(os.path.join(_cur, _ROOT_MARK_FILE))
                and any(os.path.exists(os.path.join(_cur, _m)) for _m in _ROOT_MARK_DIRS)):
            return _cur
        _parent = os.path.dirname(_cur)
        if _parent == _cur:
            break
        _cur = _parent
    raise RuntimeError(
        "无法定位项目根：向上 %d 层未见 %s + %s｜start=%r｜"
        "请检查是否从备份副本(.bak_batch*/code_package_*)导入（323刀0 拒绝猜层数）"
        % (_ROOT_MAX_UP, _ROOT_MARK_FILE, "/".join(_ROOT_MARK_DIRS), _cur))


def _dotted(node: Any) -> str:
    """把 `ast.Name` / `ast.Attribute` 链还原为点号字符串（如 `a.b.c`）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        _base = _dotted(node.value)
        return (f"{_base}.{node.attr}") if _base else node.attr
    return ""


def _excluded_dir(parent_abs: str, dirname: str, project_root: str) -> bool:
    """★397刀2：`os.walk` 剪枝判据 —— 按**顶层相对路径**判，不再按裸目录名。

    用裸名判会把 `nucleus/data/` 一起剪掉（该目录下 6 个是真生产文件，其中
    `exclude_dirs.py` / `write_guard.py` 就是本套扫描规则本身）；被剪掉的文件在
    调用图里**没有节点**，于是"零调用"结论在这些文件上是"没采到"而不是"真没人调用"。
    """
    _rel = _safe_relpath(os.path.join(parent_abs, dirname), project_root).replace(os.sep, "/")
    return is_call_graph_excluded(_rel)


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
                        if not _excluded_dir(_dp, x, project_root)
                        and not x.startswith(_EXCLUDE_DIR_PREFIXES)]
            for _fn in _files:
                if _fn.endswith(".py"):
                    _out.append(_safe_relpath(os.path.join(_dp, _fn), project_root)
                                .replace(os.sep, "/"))
    return sorted(set(_out))


def _m344_note_tree_dropped() -> None:
    """★344批刀5：AST 面用完即删的**记账**（不持有任何树，零额外遍历开销）。

    与 `nucleus/mnemosyne/ast_release.py` 解耦：import 失败也绝不影响图分析
    （图分析是生产每 61 分钟一次的批量任务，不能被内存记账拖死）。
    """
    try:
        from nucleus.mnemosyne import ast_release as _ar344
        _ar344.note_ast_face_done(source="CallGraphAnalyzer._collect_file")
    except Exception as _e:  # noqa: BLE001
        silent_exc(_e, where="CallGraphAnalyzer::_m344_note_tree_dropped", level="debug")


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
        # ★323批刀0：**失败不写缓存**——否则一次读错（如副本根）会把
        #   event 边永久钉死在字面量口径，后续整轮读数都不可信。
        return _out
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
        # ★320批刀5：类继承关系（qualname -> [基类尾名]），供 _resolve_by_name 的
        #   父类链档使用。仅采集名字（不做类型推断），不猜多义。
        self.class_bases: dict = {}
        self._scope: list = []          # [(kind, name)]  kind in {"class","func"}

    # ---------------- 定义 ----------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # ★320批刀5：采集基类**尾名**（`mixin.Foo` → `Foo`）——图里 qualname 是
        #   仓内局部名，不做跨模块全限定（多义时靠"唯一"闸兜底，不猜）。
        _qual = ".".join([x[1] for x in self._scope if x[0] == "class"] + [node.name])
        _bases = []
        for _b in (node.bases or []):
            if isinstance(_b, ast.Name):
                _bases.append(_b.id)
            elif isinstance(_b, ast.Attribute):
                _bases.append(_b.attr)
            elif isinstance(_b, ast.Subscript):     # Generic[T] / Protocol[X]
                _v = _b.value
                if isinstance(_v, ast.Name):
                    _bases.append(_v.id)
                elif isinstance(_v, ast.Attribute):
                    _bases.append(_v.attr)
        if _bases:
            self.class_bases[_qual] = _bases
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
            # ★416刀3：方法体内的**嵌套 def**（`_scope` 里已有 func ⇒ 不是类成员）。
            #   这类函数体常被当值传给 `re.sub` / `sorted(key=)` 一类调用，
            #   静态图没有 call 边 ⇒ 会被判死。单列一标，供值位判据精确放行。
            "nested": bool(any(_k == "func" for _k, _ in self._scope)),
        })
        # ★321批刀0（C类路线①）：采集**形参类型注解** `def f(x: C)` → {x: "C"}。
        #   只认**简单名**（ast.Name）与字符串字面量注解；`Optional[C]` / `psutil.P`
        #   这类一律不采（多义或跨库，接了就是猜）。供 `x.m()` 走 typed_infer 档。
        _pt = getattr(self, "param_types", None)
        if _pt is None:
            _pt = {}
            self.param_types = _pt
        _ann: dict = {}
        try:
            _all_args = (list(getattr(node.args, "posonlyargs", []) or [])
                         + list(node.args.args or [])
                         + list(node.args.kwonlyargs or []))
            for _a in _all_args:
                _an = _a.annotation
                _nm = ""
                if isinstance(_an, ast.Name):
                    _nm = _an.id
                elif isinstance(_an, ast.Constant) and isinstance(_an.value, str):
                    _v = _an.value.strip()
                    if _v.isidentifier():
                        _nm = _v
                if _nm and _nm not in ("self", "cls"):
                    _ann[_a.arg] = _nm
        except Exception as _e:      # 注解采集失败只是少一条线索，不影响建图
            (_logger.debug if _noise_reduce() else _logger.warning)(
                "调用图分析：形参注解采集失败 %s: %s", type(_e).__name__, _e)
        if _ann:
            _pt[_qual] = _ann
        # ★322批刀1（路线② T1）：采集**返回注解**与**`v = f(...)` 赋值源**，
        #   供 `v.m()` 走「返回值→接收者类型」推断档（kind 仍 typed_infer，可审计）。
        #   判据纪律与 321 刀0 同规：只认**简单名**、只认**点号为零**的函数名，
        #   `Optional[C]` / `pkg.f()` / 链式调用一律不采（接了就是猜）。
        try:
            _ret = getattr(node, "returns", None)
            _rn = ""
            if isinstance(_ret, ast.Name):
                _rn = _ret.id
            elif isinstance(_ret, ast.Constant) and isinstance(_ret.value, str):
                _rv = _ret.value.strip()
                if _rv.isidentifier():
                    _rn = _rv
            if _rn and _rn not in ("None", "Any", "Optional"):
                _ra = getattr(self, "ret_ann", None)
                if _ra is None:
                    _ra = {}
                    self.ret_ann = _ra
                _ra[_qual_id(self.module, _qual)] = _rn
            _src: dict = {}

            def _scan_assign(_nodes, _out, _depth=0):
                """只扫本函数作用域：遇嵌套 def/class/lambda 即跳过（不越界猜）。"""
                if _depth > 10:
                    return
                for _st in (_nodes or []):
                    if isinstance(_st, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef, ast.Lambda)):
                        continue
                    if (isinstance(_st, ast.Assign) and len(_st.targets) == 1
                            and isinstance(_st.targets[0], ast.Name)
                            and isinstance(_st.value, ast.Call)):
                        _fq = _dotted(_st.value.func)
                        if _fq and "." not in _fq:
                            _out[_st.targets[0].id] = _fq
                    for _fld in ("body", "orelse", "finalbody"):
                        _scan_assign(getattr(_st, _fld, None), _out, _depth + 1)
                    for _h in (getattr(_st, "handlers", None) or []):
                        _scan_assign(getattr(_h, "body", None), _out, _depth + 1)
                    for _c in (getattr(_st, "cases", None) or []):
                        _scan_assign(getattr(_c, "body", None), _out, _depth + 1)

            _scan_assign(node.body, _src)
            if _src:
                _as = getattr(self, "assign_src", None)
                if _as is None:
                    _as = {}
                    self.assign_src = _as
                _as[_qual_id(self.module, _qual)] = _src
        except Exception as _e2:      # 采不到只是少一条线索，绝不影响建图
            (_logger.debug if _noise_reduce() else _logger.warning)(
                "调用图分析：返回注解/赋值源采集失败 %s: %s", type(_e2).__name__, _e2)
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
        # ★321批刀0：`def f(x: C)` 里 `x.m()` ⇒ 带上**接收者类型** C（typed_infer 档的输入）
        _parts_t = _text.split(".")
        _rcv_type = ""
        if len(_parts_t) >= 2:
            _pt2 = getattr(self, "param_types", None) or {}
            _rcv_type = str((_pt2.get(_caller_qual) or {}).get(_parts_t[0]) or "")
        self.raw_calls.append({
            "caller_qual": _caller_qual,
            "parts": _parts_t,
            "line": node.lineno,
            "in_class": _cls,
            "recv_type": _rcv_type,
        })
        self.generic_visit(node)


# ★401批刀3：常驻副本文件名。**必须**被 call_graph_guard 的选择器与本节流清理跳过 ——
#   它的首字母 'l' 排在所有 `graph_YYYYmmdd_HHMMSS` 之后，任何按**文件名**排序取
#   "最新"的消费方都会永远选中它；副本一旦落后就变成"看不见的旧图"。
_M401_GRAPH_LATEST_NAME = "graph_latest.json"


def _m401_keep_latest_default() -> bool:
    """★401批刀3：`--keep-latest` 的默认值（config 可关；取不到按开）。"""
    try:
        import config as _cfg401
        return bool(getattr(_cfg401, "ENABLE_M401_GRAPH_KEEP_LATEST", True))
    except Exception as _e:
        silent_exc(_e, where="CallGraphAnalyzer::_m401_keep_latest_default")
        return True


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
        self._class_bases: dict = {}     # ★320刀5：qualname -> [基类尾名]
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
        # ★318批：名称索引（刀1 R1/R2 消歧用，懒惰构建）＋ 未解析样本（刀0 R3）
        self._m318_name_index_cache: dict | None = None
        self._m318_samples: list = []
        self._m318_last_kind: str = ""

        # ★414刀1：public_api 三条实证判据的缓存（懒加载，见 _m414_* 方法）
        self._m414_all_cache: dict = {}       # rel_file -> frozenset(__all__ 名字)
        self._m414_reflect_cache = None       # main.py 反射表函数名
        self._m414_testref_cache = None       # tests/ 引用过的标识符
        self._m414_valueref_cache = None      # 方法名被当作值引用的集合
        self._m414_getattrref_cache = None    # getattr/hasattr 字面名集合
        # ★416刀3：模块级函数名出现在**值位**（dict value / Assign 右值 / import）
        self._m416_namevalref_cache = None

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
        # ★321批刀0：形参注解档的类尾名索引（每轮建图重建，防陈旧）
        self._m321_cls_index = None
        # ★322批刀1：路线② 推断态（返回注解 / 赋值源 / 名字索引 / 计数）全部按轮重建
        self.ret_ann = {}
        self.assign_src = {}
        self._m322_name_idx = None
        self._m322_infer_stats = {"param_ann": 0, "ret_value": 0}
        # ★414刀1：判据缓存按轮重建（__all__/反射表/tests 都可能在同一进程内变）
        self._m414_all_cache = {}
        self._m414_reflect_cache = None
        self._m414_testref_cache = None
        self._m414_valueref_cache = None
        self._m414_getattrref_cache = None

    #: ★405刀1：快照字段表 —— 需要跨轮缓存复用的 `_Collector` 属性。
    #: 实测 14 个属性全部 JSON 可序列化（`param_types` 是类属性，故用 getattr 兜底）。
    _SNAPSHOT_ATTRS = (
        "rel_file", "module", "defs", "bindings", "raw_calls", "emits",
        "subs", "entries", "name_tables", "class_bases", "param_types",
        "ret_ann", "assign_src",
    )
    #: ★405刀1：缓存键前缀（与分析器名一致，避免与知识质量缓存撞键）
    _CACHE_NS = "call_graph:"

    def _collect_snapshot(self, rel: str) -> dict | None:
        """解析单个文件 → 纯 JSON 快照；解析失败返回 None。

        ★405刀1：本方法**不修改分析器任何状态**（不碰 `_defs`/`_bindings`/
        `_raw_*`），因此可以安全地跑在分片线程里。合并由 `_merge_snapshot`
        单独完成 —— 拆两段是为了让「解析」可缓存、可并行，而「合并」留在
        单线程里保持顺序确定（`_defs` 首次命中口径依赖文件顺序）。
        """
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
            _m344_note_tree_dropped()
            del _tree
            return None
        except Exception as e:
            # ★第22批 T5/P2-123：同上，遍历失败跳过 → DEBUG。
            (_logger.debug if _noise_reduce() else _logger.warning)(
                "调用图分析：%s 遍历失败: %s: %s", rel, type(e).__name__, e)
            _m344_note_tree_dropped()
            del _tree
            return None
        # ★344批刀5：AST 面**用完即删**。
        #   改前 `_tree` 在 visit() 之后仍是局部变量，往下还有几十行（登记/合并/统计）
        #   一直吊着整棵树；异常路径上 `traceback → frame → _tree` 更是长期持有
        #   （只要异常对象被日志/账本留住，整棵树就活着）。此处显式断引用并记账。
        _m344_note_tree_dropped()
        del _tree
        # ★405刀1：导出快照（纯 JSON，不含 AST 引用）
        _snap: dict = {}
        for _a in self._SNAPSHOT_ATTRS:
            _snap[_a] = getattr(_c, _a, None)
        return _snap

    def _merge_snapshot(self, rel: str, snap: dict) -> None:
        """把一份快照合并进分析器状态。

        ★405刀1：逻辑逐行照搬原 `_collect_file` 后半段，**不做任何语义改动**
        （含 `_defs` 首次命中优先、`_by_module_top` 覆盖、`ret_ann`/`assign_src`
        并回）。唯一变化是从 `_c.xxx` 取值改为 `snap.get(...)`，并把
        `_module` 优先取快照里的值（缓存复用时也自洽）。
        """
        _module = str(snap.get("module") or _module_id(rel))
        # 登记
        self._bindings[rel] = dict(snap.get("bindings") or {})
        # ★320批刀5：合并本文件采到的继承关系（跨文件同名类靠尾名唯一闸消歧）
        for _k, _v in (snap.get("class_bases") or {}).items():
            self._class_bases.setdefault(_k, list(_v))
        _top: dict = {}
        for _d in (snap.get("defs") or []):
            if _d["id"] in self._defs:
                continue
            self._defs[_d["id"]] = _d
            self._by_module.setdefault(_d["module"], {})[_d["qualname"]] = _d["id"]
            if "." not in _d["qualname"]:
                _top[_d["name"]] = _d["id"]
            self._caller_module[_d["id"]] = _d["module"]
        self._by_module_top[_module] = _top
        self._raw_calls.extend(
            [dict(x, file=rel, module=_module) for x in (snap.get("raw_calls") or [])])
        # ★322批刀1：把收集器采集到的「返回注解 / 赋值源」并进分析器
        #   （收集器按文件用完即弃，不并回来 = 这档永远空转，实测 0 边就是这原因）
        _cra = snap.get("ret_ann") or {}
        if _cra:
            self.ret_ann.update(_cra)
        _cas = snap.get("assign_src") or {}
        if _cas:
            self.assign_src.update(_cas)
        self._raw_emits.extend(
            [dict(x, file=rel, module=_module) for x in (snap.get("emits") or [])])
        self._raw_subs.extend(
            [dict(x, file=rel, module=_module) for x in (snap.get("subs") or [])])
        self._raw_entries.extend(
            [dict(x, file=rel, module=_module) for x in (snap.get("entries") or [])])
        self._raw_tables.extend(
            [dict(x, file=rel, module=_module) for x in (snap.get("name_tables") or [])])

    def _collect_file(self, rel: str) -> dict | None:
        """解析并合并单个文件；解析失败返回 None。

        ★405刀1：返回类型由 `_Collector` 改为快照 dict。全库调用方已核查：
        `analyze()` / `analyze_sharded()` 只做 None 判定，
        `tests/test_log_stability_m22.py:368` 传的是 `bad.py`（走失败分支）。
        """
        _snap = self._collect_snapshot(rel)
        if _snap is None:
            return None
        self._merge_snapshot(rel, _snap)
        return _snap

    def analyze_sharded(self, shard_size: int = 120, parallel: int = 2,
                        cache: Any = None) -> dict:
        """分片 + 缓存 + 片间并行地建图，输出与 `analyze()` 完全同构。

        ★405刀1 实测口径（勿再猜）：
          · Pass1（AST 解析+采集）与 Pass2（跨文件解析）实测 **6.2s : 14.0s**
            —— Pass2 才是大头，而 Pass2 依赖全库 `_defs` 才能解析跨文件边，
            **不能按文件切分**。故本方法只分片 Pass1，Pass2 仍在主线程一次完成。
          · 4 片 × threading 的 Pass1 加速比实测 **1.00x**（CPython GIL，AST
            解析是纯 CPU 活）。分片的真实收益不在并行，而在：
            ① 片内文件签名全命中缓存时整片跳过解析；
            ② 每片工作量有界，便于将来换进程池接管（跨出 GIL）。
            `parallel` 参数如实生效，但**不要据此预期 CPU 加速**。

        Args:
            shard_size: 每片最多文件数（任务书 405 刀1 要求 ≤120）。
            parallel: 片间并发度；≤1 时串行。
            cache: `AnalyzerCache` 实例；None 时不使用缓存。

        Returns:
            与 `analyze()` 同构的 dict；`stats` 额外多 `shards` 与
            `cache_hit_files` 两个键（只增不改，按名取值的下游不受影响）。
        """
        if not _scan_enabled():
            # 开关关闭：完全复用原短路路径，不引入第二条"关闭"分支
            return self.analyze()

        _t0 = time.perf_counter()
        _files = _iter_py_files(self.project_root, self.scan_dirs)
        self._reset()
        _size = max(1, int(shard_size or 120))
        _shards = [_files[_i:_i + _size] for _i in range(0, len(_files), _size)]

        def _do_shard(_group: list) -> tuple:
            """单分片：逐文件先查缓存，未命中才解析。纯解析，不碰主实例状态。"""
            _w = CallGraphAnalyzer(self.project_root, self.scan_dirs)
            _out: list = []
            _hit = 0
            _bad: list = []
            for _rel in _group:
                _sig = None
                if cache is not None:
                    _sig = _file_sig(os.path.join(
                        self.project_root, _rel.replace("/", os.sep)))
                _cached = (cache.get(self._CACHE_NS + _rel, _sig)
                           if (_sig and cache is not None) else None)
                if _cached is not None:
                    _out.append((_rel, _cached))
                    _hit += 1
                    continue
                _snap = _w._collect_snapshot(_rel)
                if _snap is None:
                    _bad.append(_rel)
                    continue
                if cache is not None and _sig is not None:
                    cache.put(self._CACHE_NS + _rel, _sig, _snap)
                _out.append((_rel, _snap))
            return _out, _hit, _bad

        _par = int(parallel or 1)
        if _par > 1 and len(_shards) > 1:
            with ThreadPoolExecutor(max_workers=_par,
                                    thread_name_prefix="cg-shard") as _ex:
                _results = list(_ex.map(_do_shard, _shards))
        else:
            _results = [_do_shard(_g) for _g in _shards]

        _pairs: list = []
        _hit_files = 0
        for _list, _h, _bad in _results:
            _pairs.extend(_list)
            _hit_files += _h
            self._parse_errors.extend(_bad)
        # 主线程按文件顺序合并：`_defs` 首次命中口径与 `analyze()` 逐文件合并完全一致
        for _rel, _snap in _pairs:
            self._merge_snapshot(_rel, _snap)
        self._pass2_resolve([])

        _nodes = [dict(v) for v in self._defs.values()]
        _edges = []
        for _caller, _targets in self._graph.items():
            for _callee, _cnt in _targets.items():
                _edges.append({"caller": _caller, "callee": _callee,
                               "count": int(_cnt),
                               "kind": self._edge_kinds.get((_caller, _callee),
                                                            "static")})
        _elapsed_ms = round((time.perf_counter() - _t0) * 1000.0, 2)
        self._graph_data = {
            "nodes": _nodes,
            "edges": _edges,
            "stats": {
                "total_files": len(_files),
                "parsed_files": len(_pairs),
                "total_functions": len(_nodes),
                "total_calls": int(self._total_calls),
                "cross_file_calls": int(self._cross_file),
                "dynamic_calls_unresolved": int(self._unresolved + self._dynamic),
                "dynamic_calls": int(self._dynamic),
                "unresolved_calls": int(self._unresolved),
                "external_calls": int(self._external),
                "parse_errors": len(self._parse_errors),
                "elapsed_ms": _elapsed_ms,
                # ★405刀1 新增（只增不改）
                "shards": len(_shards),
                "cache_hit_files": int(_hit_files),
            },
            "parse_error_files": list(self._parse_errors[:20]),
        }
        _logger.info("调用图构建完成（分片）: %d 文件 / %d 函数 / %d 调用"
                     "（跨文件 %d / %d 片 / 缓存命中 %d）耗时 %.2fs",
                     len(_files), len(_nodes), self._total_calls,
                     self._cross_file, len(_shards), _hit_files,
                     _elapsed_ms / 1000.0)
        return self._graph_data

    #: ★318批刀0：未解析样本单轮留存上限（防内存无限增长；落盘后即清）
    #: 实测全库未解析+external ≈ 52k 条（2026-10-19），上限取 30 万防截断失真
    _M318_MAX_SAMPLES = 300000

    def _m318_name_index(self) -> dict:
        """★318批刀1：名称 → [节点 id] 索引（懒惰构建）。

        供 R1（self.X 作用域消歧）／R2（obj.X 全库唯一建边）／R3（样本分类）共用，
        避免每条未解析调用都全表扫描 `_defs`（27k × 6.8k 次 = 不可承受）。
        每个 (name, nid) 只入一次（与原 `_resolve_by_name` 的 `or` 判定等价）。
        """
        if self._m318_name_index_cache is None:
            _idx: dict = {}
            for _nid, _d in self._defs.items():
                _qn = str(_d.get("qualname") or "")
                _nm = str(_d.get("name") or "")
                _key = _qn.split(".")[-1] if _qn else _nm
                if not _key:
                    continue
                _idx.setdefault(_key, []).append(_nid)
                if _nm and _nm != _key:
                    _idx.setdefault(_nm, []).append(_nid)
            self._m318_name_index_cache = _idx
        return self._m318_name_index_cache

    def _m318_classify_unresolved(self, rc: dict) -> str:
        """★318批刀0（R3）：未解析调用分类，与烛微318§1-维度1 对表。

        类别：`self唯一` / `self多义` / `obj唯一` / `obj多义` / `external` / `混合`。
        判据只看"该名字在全库的候选数"——**不猜测**，只给攻坚靶子。
        """
        _parts = list(rc.get("parts") or [])
        if not _parts:
            return "混合"
        _is_self = _parts[0] == "self"
        _cands = self._m318_name_index().get(_parts[-1]) or []
        _uniq = len(_cands) == 1
        if _is_self:
            return "self唯一" if _uniq else "self多义"
        return "obj唯一" if _uniq else "obj多义"

    def _m320_ancestors(self, cls_qual: str) -> list[str]:
        """★320批刀5：按声明顺序返回祖先类 qualname（BFS，防环；**不做推断**）。

        只走 `_class_bases` 里**真实写在 class 定义上的基类名**；基类若在仓内
        找不到同名类定义则到此为止（父类在仓外 ⇒ 无法上溯，不猜）。
        """
        _bases = getattr(self, "_class_bases", None) or {}
        if not _bases or not cls_qual:
            return []
        # 尾名 -> qualname（同尾名多个时按"本模块优先"再"唯一"取，仍多义则不用）
        _by_tail: dict[str, list[str]] = {}
        for _q in _bases:
            _by_tail.setdefault(_q.rsplit(".", 1)[-1], []).append(_q)
        _out: list[str] = []
        _seen = {cls_qual}
        _queue = list(_bases.get(cls_qual) or [])
        while _queue:
            _b = _queue.pop(0)
            _cands = _by_tail.get(_b) or []
            if len(_cands) != 1:
                continue
            _q = _cands[0]
            if _q in _seen:
                continue
            _seen.add(_q)
            _out.append(_q)
            _queue.extend(_bases.get(_q) or [])
        return _out

    @staticmethod
    def _m321_allowlisted(rc: dict) -> bool:
        """★321批刀1：调用末段是不是「通用同名词」（⇒ 禁止靠兜底建边）。

        只认 2 段以上且**接收者不是 self/cls** 的形态：`self._pool.get()` 这类仓内属性链
        属"未知类型的属性" 而非 dict/str 通用调用，不应被本表一刀切。
        """
        _parts = rc.get("parts") or []
        if len(_parts) < 2:
            return False
        if str(_parts[0]) in ("self", "cls"):
            return False
        return str(_parts[-1]) in _EXTERNAL_NAME_ALLOWLIST

    def _m321_cls_tail_index(self) -> dict:
        """★321批刀0：类尾名 → {(类 qualname, 模块)} 索引（惰性构建，只读 _defs）。"""
        _idx = getattr(self, "_m321_cls_index", None)
        if _idx is not None:
            return _idx
        _idx2: dict = {}
        for _d in self._defs.values():
            _q = str(_d.get("qualname") or "")
            if "." not in _q:
                continue
            _cls = _q.rsplit(".", 1)[0]
            _idx2.setdefault(_cls.rsplit(".", 1)[-1], set()).add(
                (_cls, str(_d.get("module") or "")))
        self._m321_cls_index = _idx2
        return _idx2

    def _m321_typed_target(self, recv_type: str, suffix: str) -> str:
        """★321批刀0：形参注解 ⇒ 目标方法节点 id（**尾名唯一才接**，不猜）。"""
        if not (recv_type and suffix):
            return ""
        _cands = self._m321_cls_tail_index().get(recv_type) or ()
        if len(_cands) != 1:
            return ""                      # 仓内同名类多义 ⇒ 不猜
        _cls, _mod = next(iter(_cands))
        return str(self._by_module.get(_mod, {}).get("%s.%s" % (_cls, suffix)) or "")

    # ===== ★322批刀1（路线②）：返回值 → 接收者类型推断（kind 仍 typed_infer） =====
    @staticmethod
    def _m322_typed_enabled() -> bool:
        """typed_infer 档**总开关**（回退粒度按 kind：关掉只少 typed_infer，不动 static/unique_name）。

        ★337批刀9（小林拍板③）：默认值 **"1" → "0"（正式默认关闭）**。
        理由：typed_infer 在实图上只贡献 7 条边（占比 0.07%），却让图谱多一层
        不可复核的推断口径；而它的代价（4 个公开方法失去唯一入边）已在
        `nucleus.self_inspector._M337_TYPED_ONLY_METHODS` 同批登记兜住。
        仍可用 `TT_TYPED_INFER=1` 显式打开（env 覆盖，无需 config 登记）。
        """
        return str(os.environ.get("TT_TYPED_INFER", "0")).strip().lower() not in ("0", "false", "no")

    @staticmethod
    def _m322_ret_enabled() -> bool:
        """路线②（返回值推断）**单独开关**：默认关，待 T2 误建率结论由星轨拍板再开。"""
        return str(os.environ.get("TT_TYPED_INFER_RET", "0")).strip().lower() in ("1", "true", "yes")

    def _m322_inc(self, via: str) -> None:
        """分来源计数（param_ann=路线①｜ret_value=路线②），供交叉项与对表复算。"""
        _s = getattr(self, "_m322_infer_stats", None)
        if _s is None:
            _s = {"param_ann": 0, "ret_value": 0}
            self._m322_infer_stats = _s
        _s[via] = int(_s.get(via, 0)) + 1

    def _m322_name_index(self) -> dict:
        """函数名 → [节点 id]（惰性；只用于判「全库唯一」，不唯一即不猜）。"""
        _idx = getattr(self, "_m322_name_idx", None)
        if _idx is not None:
            return _idx
        _idx2: dict = {}
        for _nid, _d in self._defs.items():
            _idx2.setdefault(str(_d.get("name") or ""), []).append(str(_nid))
        self._m322_name_idx = _idx2
        return _idx2

    def _m322_var_target(self, caller_id: str, var: str, suffix: str) -> str:
        """`v = f(...)` + `f` 全库唯一 + `f -> C`（仓内类唯一）⇒ `C.suffix` 的节点 id。

        任一环不满足即返回空串（不猜）：这档只负责「解释得通的接法」，不负责多接边。
        """
        if not (caller_id and var and suffix):
            return ""
        _src = (getattr(self, "assign_src", None) or {}).get(caller_id) or {}
        _fname = str(_src.get(var) or "")
        if not _fname:
            return ""
        _ids = self._m322_name_index().get(_fname) or []
        if len(_ids) != 1:                 # 全库同名多义 ⇒ 不猜（任务书 T1 口径）
            return ""
        _rn = (getattr(self, "ret_ann", None) or {}).get(_ids[0]) or ""
        if not _rn:
            return ""
        return self._m321_typed_target(_rn, suffix)

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
        # ★318批刀1：改走名称索引（O(1) 取候选），语义不变
        _hits: list[str] = list(self._m318_name_index().get(name) or [])
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
        # ② 同文件（★318批刀1 增强：多义时先收窄到**调用方所在文件**——
        #   同文件多类同名（mixin / 私有实现）是 self.X() 未解析的主要形态之一）
        # ④ 父类链（★320批刀5：同类→同文件→同模块→**父类链**→同目录→全库唯一）
        #   只沿**声明过的基类**上溯（BFS + visited 防环），仍多义 ⇒ 空串（不猜）。
        if caller_class:
            for _anc in self._m320_ancestors(caller_class):
                _want = "%s.%s" % (_anc, name)
                _pc = [_h for _h in _hits
                       if str((self._defs.get(_h) or {}).get("qualname") or "") == _want]
                if len(_pc) == 1:
                    return _pc[0]
        if caller_file:
            _cf = caller_file.replace("\\", "/")
            _same_file = [
                _h for _h in _hits
                if str((self._defs.get(_h) or {}).get("file") or ""
                       ).replace("\\", "/") == _cf]
            if len(_same_file) == 1:
                return _same_file[0]
        # ③ 同模块
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
            self._m318_last_kind = ""
            _callee = self._resolve(_rc, _module_names)
            _m318_kind = self._m318_last_kind
            if not _callee:
                # ★口径修正：区分「项目外调用」与「项目内疑似未解析」
                _cat = ""
                if self._is_external(_rc, _module_names):
                    self._external += 1
                    _cat = "external"
                else:
                    self._unresolved += 1
                    _cat = self._m318_classify_unresolved(_rc)
                # ★318批刀0（R3）：放弃处不再是「只有计数器、无样本」——留样本给 R1 对表
                # ★401批刀3（L1）：补「回归三元组」 file:line:call_expr。
                #   318 只有 file + caller，同一个方法里十几个未解析点无法定位到行，
                #   「上一批能解析、这一批解析不出」的退化看不见。
                if len(self._m318_samples) < self._M318_MAX_SAMPLES:
                    _m401_parts = list(_rc.get("parts") or [])
                    self._m318_samples.append({
                        "cat": _cat,
                        "parts": _m401_parts[:4],
                        "module": _rc.get("module", ""),
                        "file": _rc.get("file", ""),
                        # 行号：取不到就是 None，**不填 0**（没有"第 0 行"这种事）
                        "line": _rc.get("line"),
                        "call_expr": (".".join(str(_p401) for _p401 in _m401_parts)
                                      or "<dynamic:%s>" % (_rc.get("attr_name")
                                                           or _rc.get("kind") or "?")),
                        "in_class": _rc.get("in_class", ""),
                        "caller": _rc.get("caller_qual", ""),
                    })
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
            self._edge_kinds[(_caller_id, _callee)] = (_m318_kind
                                                      or _rc.get("kind") or "static")
            if self._callee_module(_callee) != _caller_mod:
                self._cross_file += 1
        # ★第306批刀1：静态解析完成后补两类反射边（event / entry）
        self._pass2_reflect(_module_names)
        # ★318批刀0（R3）：未解析样本落盘（滚动 7 份，与 312 同规格）
        if getattr(self, "_m318_samples", None):
            try:
                from nucleus.self_awareness.unresolved_samples import dump_samples
                # ★401批刀3：传 cap 让落盘侧能把「是否截断」写进 meta
                _sp = dump_samples(self._m318_samples, self.project_root,
                                   cap=self._M318_MAX_SAMPLES)
                _logger.info("[318刀0][R3] 未解析样本落盘: %s（%d 条）",
                             _sp, len(self._m318_samples))
            except Exception as _se:
                _logger.warning("[318刀0][R3] 未解析样本落盘失败（不影响建图）: %s: %s",
                                type(_se).__name__, _se)

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
        # ★321批刀1：末段命中「通用同名词表」且接收者不是仓内模块 ⇒ 显式判外部。
        #   （接收者是仓内模块/包时不判外：`utils.get()` 这类是项目内调用，别误伤。）
        # ★321批刀1：本函数**不**参与通用词表判定——它只对"该不该算项目外"负责；
        #   通用词表做的是"不得靠 unique_name 兜底造边"（见 _resolve 关 6）。
        #   若在这里判外，会把 14,607 条 `x.get()/d.items()` 算成 external，
        #   等于变相稀释 unresolved（违背"不可把没测当没病"）。
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
            # ★318批刀1（R1 主线第一刀）：同模块 qualname 表查不到（跨模块继承 / mixin）
            #   ⇒ 改走 **作用域优先消歧**（同类 → 同模块 → 同目录 → 全库唯一），
            #   带 caller_class / caller_file；仍多义 ⇒ 空串（不猜，口径纪律不变）。
            _hit2 = self._resolve_by_name(_parts[1], _cur_mod,
                                          caller_class=rc.get("in_class", ""),
                                          caller_file=rc.get("file", ""))
            if _hit2:
                self._m318_last_kind = "self_attr"
                return _hit2
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

        # 4b) ★321批刀0（C类路线①·形参注解档）：`def f(x: C)` 里 `x.m()`
        #   判据：注解类名 C 在仓内**唯一**，且 C 里确有 `m` 的定义 ⇒ 接上，
        #   边 kind="typed_infer"（可审计）。多义/找不到 ⇒ 不猜（继续走兜底）。
        _rt = str(rc.get("recv_type") or "") if self._m322_typed_enabled() else ""
        if _rt:
            _hit_t = self._m321_typed_target(_rt, ".".join(_parts[1:]))
            if _hit_t:
                self._m318_last_kind = "typed_infer"
                self._m322_inc("param_ann")
                return _hit_t
        # 4c) ★322批刀1（路线②·返回值推断档）：`v = f(...)`，f **全库唯一**且带 `-> C`
        #   （简单名、仓内类唯一）⇒ `v.m()` 接 `C.m`。**默认关**（TT_TYPED_INFER_RET=1 才开），
        #   因为误建率尚未实测（T2）。kind 与 321 刀0 同为 typed_infer，按 kind 一行可退。
        if (self._m322_typed_enabled() and self._m322_ret_enabled()
                and len(_parts) >= 2 and _head not in ("self", "cls")):
            _hit_r = self._m322_var_target(
                _qual_id(str(rc.get("module") or ""), str(rc.get("caller_qual") or "")),
                _head, ".".join(_parts[1:]))
            if _hit_r:
                self._m318_last_kind = "typed_infer"
                self._m322_inc("ret_value")
                return _hit_r
        # 5) 全局唯一同名函数（兜底，仅在唯一时采用，避免误连）
        if len(_parts) == 1:
            _cands = [v for v in self._defs.values() if v["name"] == _head
                      and v["type"] == "function"]
            if len(_cands) == 1:
                return _cands[0]["id"]
        # 6) ★318批刀1（R2）：`obj.X()` 属性调用——**多义仍不猜**，但全库同名唯一时建边
        #    （kind="unique_name"，可审计）。硬护栏：external 一律不享受此兜底，
        #    防把 os/json 之类第三方调用算成"已解析"制造虚假提升（与刀2 同口径）。
        # ★321批刀1：末段命中「通用同名词表」⇒ **不进兜底**（不建 unique_name 边）。
        #   不判为 external（不渲染分母），而是老实留在 unresolved 里—— 这才是
        #   "护栏未开口子"与"不把没测当没病"两条纪律的交集。
        if self._is_external(rc, module_names) or self._m321_allowlisted(rc):
            return ""
        _cands2 = list(self._m318_name_index().get(_parts[-1]) or [])
        if len(_cands2) == 1:
            self._m318_last_kind = "unique_name"
            return _cands2[0]
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

    # ------------------------------------------------------------ ★414刀1 判据
    # public_api 判据重写（413 根因②）：
    #   旧判据 `not _name.startswith("_")` **没有区分力** —— 全库 5962 个生产类
    #   方法里 44.3% 不带下划线，于是它们全被贴上"公共 API"标签保护起来，
    #   而真正有对外契约的只是极少数。413 实测 589/590 条判死全归 public_api。
    #   新判据只认**可枚举的证据**：
    #     (1) 在该文件的 `__all__` 里          → public_api=True
    #     (2) 在 main.py 的 (模块, 函数) 反射表里 → public_api=True
    #     (3) 被 tests/ 引用                     → test_ref=True（独立一档，
    #        拒因 `test_only`；不混进 public_api，否则"被测试用"和"对外契约"
    #        两种完全不同的语义会被同一个字段吞掉，审计时分不清该找谁确认）
    #   三条都不满足 ⇒ public_api=False，照常进候选池。

    def _m414_all_names(self, rel_file: str) -> frozenset:
        """该文件 `__all__` 里的名字（按文件缓存）。

        口径：只看**模块顶层**的 `__all__ = [...]` 赋值，取字符串常量元素。
        动态拼的 `__all__`（如 `__all__ = [x for x in ...]`）取不到 ⇒ 返回空集，
        这是**偏危险**的方向，所以下面 `_m414_test_ref_names` 再兜一层。
        """
        _key = str(rel_file)
        if _key in self._m414_all_cache:
            return self._m414_all_cache[_key]
        _names: set = set()
        _abs = os.path.join(self.project_root, _key.replace("\\", "/").replace("/", os.sep))
        try:
            with open(_abs, encoding="utf-8", errors="ignore") as _fobj:
                _tree = ast.parse(_fobj.read())
            for _node in _tree.body:
                if isinstance(_node, ast.Assign):
                    _tgts, _val = _node.targets, _node.value
                elif isinstance(_node, ast.AnnAssign):
                    _tgts, _val = [_node.target], _node.value
                else:
                    continue
                if not any(isinstance(_t, ast.Name) and _t.id == "__all__"
                           for _t in _tgts):
                    continue
                if isinstance(_val, (ast.List, ast.Tuple, ast.Set)):
                    for _e in _val.elts:
                        if isinstance(_e, ast.Constant) and isinstance(_e.value, str):
                            _names.add(_e.value)
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer:_m414_all_names", level="debug")
        _out = frozenset(_names)
        self._m414_all_cache[_key] = _out
        return _out

    def _m414_reflect_names(self) -> frozenset:
        """main.py 里 `[(模块名, 函数名), ...]` 反射表的**函数名**集合。

        为什么用 AST 扫而不是硬编码：表会漂移 —— 413 任务书写"20 项"，
        414 实测 **21 项**。硬编码会立刻过期，AST 让判据跟着表走。
        收集口径故意放宽到"任何二元字符串元组且首元素含 `.`"，多收只是
        多保护（少删几条），不会误删。
        """
        if self._m414_reflect_cache is not None:
            return self._m414_reflect_cache
        _names: set = set()
        _abs = os.path.join(self.project_root, "main.py")
        try:
            with open(_abs, encoding="utf-8", errors="ignore") as _fobj:
                _tree = ast.parse(_fobj.read())
            for _node in ast.walk(_tree):
                if not isinstance(_node, (ast.List, ast.Tuple)):
                    continue
                for _e in _node.elts:
                    if not isinstance(_e, ast.Tuple) or len(_e.elts) != 2:
                        continue
                    _a, _b = _e.elts
                    if not (isinstance(_a, ast.Constant) and isinstance(_a.value, str)
                            and isinstance(_b, ast.Constant) and isinstance(_b.value, str)):
                        continue
                    if "." in _a.value and _b.value.isidentifier():
                        _names.add(_b.value)
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer:_m414_reflect_names", level="debug")
        self._m414_reflect_cache = frozenset(_names)
        return self._m414_reflect_cache

    def _m414_getattr_ref_names(self) -> frozenset:
        """`getattr/hasattr/setattr(obj, "名字")` 里出现的**字符串字面名**集合。

        为什么必须补这一档（414 复验实测）：修掉 `value_ref` 之后**剩下 4 条
        假死全部是它** ——
            getattr(_self, "get_total_handled")   # InfoField
            getattr(self, "evict_pattern")        # StreamMiner
            getattr(_store, "update_node")        # neo4j_store
            getattr(_organ, "get_learned_behaviors")
        这三类 `getattr/hasattr/setattr` 是**按名派发**的显式证据：方法真的会被
        调到，只是编译器/静态图看不见。self_inspector 内部早有同类判据
        （`_GETATTR_RE` → `_byname_names`），但 O3 消费的 `get_isolated_functions`
        一直没有 ⇒ 两边口径不一致，这里补齐。
        """
        if self._m414_getattrref_cache is not None:
            return self._m414_getattrref_cache
        _names: set = set()
        try:
            _files = _iter_py_files(self.project_root, self.scan_dirs)
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer:_m414_getattr_ref_names:files",
                       level="warning")
            self._m414_getattrref_cache = frozenset()
            return self._m414_getattrref_cache
        _API = ("getattr", "hasattr", "setattr", "delattr")
        for _rel in _files:
            # ★同上：`_iter_py_files` 给的是相对路径，必须 join project_root
            _abs = os.path.join(self.project_root, str(_rel).replace("/", os.sep))
            try:
                with open(_abs, encoding="utf-8", errors="ignore") as _fobj:
                    _tree = ast.parse(_fobj.read())
            except Exception as _e:
                silent_exc(_e, where="CallGraphAnalyzer:_m414_getattr_ref_names:file",
                           level="debug")
                continue
            for _n in ast.walk(_tree):
                if not isinstance(_n, ast.Call):
                    continue
                if not (isinstance(_n.func, ast.Name) and _n.func.id in _API):
                    continue
                if len(_n.args) < 2:
                    continue
                _a = _n.args[1]
                if isinstance(_a, ast.Constant) and isinstance(_a.value, str):
                    _names.add(_a.value)
        self._m414_getattrref_cache = frozenset(_names)
        return self._m414_getattrref_cache

    def _m414_value_ref_names(self) -> frozenset:
        """方法名被**当作值**引用过的集合（`ast.Attribute` 且不在 `Call.func` 位）。

        为什么必须单独一档（414 抽样实测）：`
            threading.Thread(target=self._monitor_loop, daemon=True)
            _pool.submit(self._run_guarded, scope)
            self._trigger_autonomous_derivation,     # 作为参数传下去
        ` 这类写法里方法**不是被调用**，是实现里的回调/线程体/定时器入口 ——
        调用图只统计 call 边 ⇒ 入度 0 ⇒ 被判死。413 的 22 条假死里有这一类，
        414 的 30 条抽样里 **4 条全部是它**（4/30 = 13.3%）。

        口径：全库同名属性值引用即算，**不区分类归属**。偏保守（同名巧合会
        多保护几条真死），但删除不可逆 —— 宁可漏删 10 条，不可误删 1 条。
        """
        if self._m414_valueref_cache is not None:
            return self._m414_valueref_cache
        _names: set = set()
        try:
            _files = _iter_py_files(self.project_root, self.scan_dirs)
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer:_m414_value_ref_names:files",
                       level="warning")
            self._m414_valueref_cache = frozenset()
            return self._m414_valueref_cache
        for _rel in _files:
            # ★`_iter_py_files` 返回的是**相对 project_root 的路径**，不是绝对路径
            #   ⇒ 必须 join 回来。直接 open() 会按 cwd 解析，换个工作目录就静默
            #   读到别的文件（假绿）。
            _abs = os.path.join(self.project_root, str(_rel).replace("/", os.sep))
            try:
                with open(_abs, encoding="utf-8", errors="ignore") as _fobj:
                    _tree = ast.parse(_fobj.read())
            except Exception as _e:
                silent_exc(_e, where="CallGraphAnalyzer:_m414_value_ref_names:file",
                           level="debug")
                continue
            # 先标记"调用位"的 Attribute（`x.name()` 里的 `x.name`），其余都算值引用
            _call_pos = set()
            for _n in ast.walk(_tree):
                if isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute):
                    _call_pos.add(id(_n.func))
            for _n in ast.walk(_tree):
                if isinstance(_n, ast.Attribute) and id(_n) not in _call_pos:
                    _names.add(_n.attr)
        self._m414_valueref_cache = frozenset(_names)
        return self._m414_valueref_cache

    def _m416_name_value_ref_names(self) -> frozenset:
        """★416刀3：**模块级函数名**出现在「值位」的集合。

        值位四源（415 抽验里三条假死分别落在前三条上，第四条是 416 复验补的）：
          1. `ast.Dict` 的 value —— 裸 `ast.Name`（如 `CHANNELS = {"entity": ch_entity}`）
             或字符串常量（如 `{"handler": "_on_heartbeat"}`）；
          2. `ast.Assign` / `ast.AnnAssign` 的**右值**是裸 `ast.Name`
             （如 `_tp = transport or ollama_transport`）；
          3. `ast.ImportFrom` / `ast.Import` 的绑定名
             （如 `from nucleus.evolution import get_llm_evolution_engine`）；
          4. **裸 Name 作调用实参/关键字实参**（`_re.sub(pat, fn, s)`、
             `sorted(xs, key=fn)`）—— 416 首轮复验里剩下的那 1 条假死正是它。

        为什么必须单独一档：静态调用图只统计 **call 边**，上面三种写法都
        不产生 call 边 ⇒ 入度 0 ⇒ 被判死；但运行时确实会被调到（作为回调/
        注册表项/再导出）。414 的 `value_ref` 只认 `ast.Attribute`
        （`self._monitor_loop`），**裸 Name 一个不认** —— 415 实测盲区2 的
        32 条命中里带 `value_ref` 的是 **0 条**。

        口径边界（防误保护）：
          * 只收**模块级函数**能对上的名字 —— 由调用方按 `_defs[...]["type"]`
            过滤，本函数只负责「名字在值位出现过」这一事实；
          * **排除定义处**：`def foo():` 里的 `foo` 不是引用，靠
            `ast.FunctionDef.name` 不进任何值位天然成立（这里是 Name 载入，
            定义名不是 `ast.Name` 节点）；
          * 注释/docstring 天然不算（AST 里没有它们的位置）。

        Returns: 名字集合（frozenset）。解析失败逐文件留痕跳过，不抛。
        """
        if self._m416_namevalref_cache is not None:
            return self._m416_namevalref_cache
        _names: set = set()
        try:
            _files = _iter_py_files(self.project_root, self.scan_dirs)
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer:_m416_name_value_ref_names:files",
                       level="warning")
            self._m416_namevalref_cache = frozenset()
            return self._m416_namevalref_cache
        for _rel in _files:
            # `_iter_py_files` 给的是相对 project_root 的路径，必须 join 回来
            _abs = os.path.join(self.project_root, str(_rel).replace("/", os.sep))
            try:
                with open(_abs, encoding="utf-8", errors="ignore") as _fobj:
                    _tree = ast.parse(_fobj.read())
            except Exception as _e:
                silent_exc(_e, where="CallGraphAnalyzer:_m416_name_value_ref_names:file",
                           level="debug")
                continue
            for _n in ast.walk(_tree):
                # 源1：dict value
                if isinstance(_n, ast.Dict):
                    for _v in (_n.values or []):
                        if isinstance(_v, ast.Name):
                            _names.add(_v.id)
                        elif isinstance(_v, ast.Constant) and isinstance(_v.value, str):
                            _names.add(_v.value)
                # 源2：Assign / AnnAssign 右值裸 Name
                elif isinstance(_n, (ast.Assign, ast.AnnAssign)):
                    _val = getattr(_n, "value", None)
                    if isinstance(_val, ast.Name):
                        _names.add(_val.id)
                # 源4：**裸 Name 作调用实参/关键字实参**（`re.sub(pat, fn, s)`、
                #   `sorted(xs, key=fn)`）—— 裸 Name 只可能是模块级函数或嵌套 def，
                #   类方法必写 `self.f`（Attribute，已被 value_ref 覆盖）⇒ 精确。
                elif isinstance(_n, ast.Call):
                    for _a in list(_n.args) + [kw.value for kw in _n.keywords]:
                        if isinstance(_a, ast.Name):
                            _names.add(_a.id)
                # 源3：import 绑定
                elif isinstance(_n, ast.ImportFrom):
                    for _al in (_n.names or []):
                        if _al.name and _al.name != "*":
                            _names.add(_al.asname or _al.name)
                elif isinstance(_n, ast.Import):
                    for _al in (_n.names or []):
                        if _al.name and _al.name != "*":
                            _names.add(_al.asname or _al.name.split(".")[-1])
        self._m416_namevalref_cache = frozenset(_names)
        return self._m416_namevalref_cache

    def _m414_test_ref_names(self) -> frozenset:
        """tests/ 下所有 .py 里**出现过**的标识符（`Name.id` ∪ `Attribute.attr`）。

        ★口径偏保守：只要名字在测试代码里被提到就算（不管它是被直接调用、
          还是写在 `hasattr` / `getattr` 断言里）。413 抽查的 15 条
          "仅测试引用"正是删了 `pytest` 立刻红的那批 —— 宁可多保 10 条，
          不可误删 1 条（删除不可逆）。
        """
        if self._m414_testref_cache is not None:
            return self._m414_testref_cache
        _names: set = set()
        _troot = os.path.join(self.project_root, "tests")
        try:
            for _dp, _dn, _fn in os.walk(_troot):
                _dn[:] = [d for d in _dn
                          if d != "__pycache__" and not d.startswith(".")]
                for _f in _fn:
                    if not _f.endswith(".py"):
                        continue
                    try:
                        with open(os.path.join(_dp, _f), encoding="utf-8",
                                  errors="ignore") as _fobj:
                            _tree = ast.parse(_fobj.read())
                    except Exception as _e:
                        silent_exc(_e, where="CallGraphAnalyzer:_m414_test_ref_names:file",
                                   level="debug")
                        continue
                    for _node in ast.walk(_tree):
                        if isinstance(_node, ast.Name):
                            _names.add(_node.id)
                        elif isinstance(_node, ast.Attribute):
                            _names.add(_node.attr)
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer:_m414_test_ref_names", level="warning")
        self._m414_testref_cache = frozenset(_names)
        return self._m414_testref_cache

    def get_isolated_functions(self) -> list:
        """从未被调用的函数/方法（排除魔术方法与入口类）。"""
        self._ensure()
        _reflect = self._m414_reflect_names()
        _testref = self._m414_test_ref_names()
        _valref = self._m414_value_ref_names()
        _gatref = self._m414_getattr_ref_names()
        # ★416刀3：裸 Name / 字符串 / import 绑定出现在值位的名字（只对模块级函数生效）
        _namevalref = self._m416_name_value_ref_names()
        _out = []
        for _nid, _d in self._defs.items():
            if self._in_deg.get(_nid, 0) > 0:
                continue
            _name = _d["name"]
            if _name in _MAGIC_ALLOW:
                continue
            if _name.startswith("__") and _name.endswith("__"):
                continue
            _f = _d["file"]
            # ★414刀1 改动2：public_api 只认可枚举的对外契约（见上方长注释）
            _pub = (_name in _reflect) or (_name in self._m414_all_names(_f))
            _out.append({
                "id": _nid,
                "name": _name,
                "file": _f,
                "line": _d["line"],
                "type": _d["type"],
                "public_api": bool(_pub),
                # ★414刀1 改动3：被 tests/ 引用 ⇒ 删了 pytest 就红，独立一档
                "test_ref": bool(_name in _testref),
                # ★414刀1 补档：被当作值传递（Thread target / submit 实参）
                #   ⇒ 没有 call 边但确实在用，单列一档（拒因 value_ref）
                "value_ref": bool(_name in _valref),
                # ★414刀1 补档：getattr/hasattr 字面名派发（静态图无形）
                "getattr_ref": bool(_name in _gatref),
                # ★416刀3 补档：模块级函数名出现在值位（dict value / Assign 右值 /
                #   import 绑定）⇒ 没有 call 边但确实在用。**只对模块级函数生效**
                #   （`_d["type"] == "function"`）：类方法的同名值位巧合太多，
                #   纳进来会大面积误保护真死代码。
                "value_ref_name": bool(_name in _namevalref
                                       and (_d["type"] == "function"
                                            or _d.get("nested"))),
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
    def dump_graph_snapshot(self, graph: dict | None = None, keep: int = 7,
                            keep_latest: bool | None = None) -> str:
        """★第304批 刀1：调用图**本体**落盘（图不再「分析完即弃」）。

        产出 `data/call_graph/graph_<ts>.json`，规格与 303 批刀6 per-item 落盘一致
        （原子写 tmp + `os.replace`，滚动保留最新 7 份）。

        内容：
          - `nodes[]` / `edges[]`（点号 ID：**模块.类.方法**）＋ `stats`；
          - `edge_types`：边类型**分色**统计 —— 由 `edges[].kind` 聚合而来，
            七类分列：`static` = AST 静态调用边；`unique_name` = 按唯一名命中的边；
            `self_attr` = 同对象自属性边；`byname` = `getattr(x, "y")` 按名派发边；
            `event` / `entry` = 事件边 / 注册入口边（本窗若无可解析目标则计 0）；
            `typed_infer` = 类型推断边（**软证据**，不是硬自证）。
            ★第305批刀2：分色**落到边上**（每条边自带 kind），落盘时的统计只是聚合，
            不再像 304 那样额外全库扫一遍正则（既慢又与图不一致）。
            ★328批刀1（A6聚合收口）：**按 kind 原样分列，不再把未知 kind 折叠进 static**。
            旧实现 `if _k not in _edge_types: _k = "static"` 把 unique_name/self_attr/
            typed_infer 全部并入 static（实测 7626+1297+1118+7=10048），任何按聚合取数的
            消费方都会把**软推断当硬自证**。现另落 `edge_types_total`（=逐边条数）与
            `edge_types_source="per_edge_kind"`，供 A6 断言逐档对表。

        Args:
            keep: 快照滚动保留份数（默认 7）。
            keep_latest: ★401批刀3：是否另存 `graph_latest.json` 常驻副本。
                `None`（默认）⇒ 跟随 `config.ENABLE_M401_GRAPH_KEEP_LATEST`；
                显式 `True/False` 覆盖（重建 CLI 的 `--keep-latest` / `--no-keep-latest` 走这里）。

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
            _keep_latest_on = (bool(keep_latest) if keep_latest is not None
                               else _m401_keep_latest_default())
            # ★第313批 刀2（写侧守卫，P1）：312 只补了**读侧**（选择器不取合成图），
            #   写侧仍脏 —— 实测 `pkg/a.py` 类合成图三次进生产目录，且占着滚动 7 份的配额
            #   （一份 4 文件的假图能把一份 6700 节点的真图挤出窗口）。两道闸：
            #   ① 内容判据：判据不合格（合成/过小）⇒ **不落生产目录**，落 `_quarantine/` 留证；
            #   ② 写盘守卫：测试/E2E/普通脚本进程一律拒写生产 data/，只有框架主进程可写。
            from nucleus.data.call_graph_guard import judge_graph as _m313_judge
            from nucleus.data.call_graph_guard import (
                quarantine_invalid_graphs as _m313_quarantine,
            )
            from nucleus.data.write_guard import guard_write as _m313_write_guard
            _m313_ok, _m313_why = _m313_judge(_cg)
            if not _m313_ok:
                _qdir = os.path.join(_dir, "_quarantine")
                _qpath = os.path.join(_qdir, "graph_quarantined_{}.json".format(
                    _time.strftime("%Y%m%d_%H%M%S")))
                if _m313_write_guard(_qpath, component="CallGraphAnalyzer.quarantine"):
                    try:
                        os.makedirs(_qdir, exist_ok=True)
                        self.export_json(_qpath, data=dict(_cg))
                    except Exception as _qe:
                        _logger.warning("合成图隔离落盘失败: %s: %s",
                                        type(_qe).__name__, _qe)
                _logger.warning(
                    "[313刀2] 拒落生产目录（判据不合格 %s）: nodes=%d",
                    _m313_why, len(_cg.get("nodes") or []))
                return ""
            # 存量清扫：本进程内每个目录只做一次（把历史合成图从滚动配额里摘出去）
            if _dir not in _M313_PRUNED_DIRS:
                _M313_PRUNED_DIRS.add(_dir)
                _pq = _m313_quarantine(_dir)
                if _pq.get("quarantined"):
                    _logger.warning(
                        "[313刀2] 存量合成图已隔离 %d 份 → %s（%s）",
                        len(_pq["quarantined"]), _pq.get("quarantine_dir"),
                        ", ".join("%s[%s]" % (str(_x.get("file")), str(_x.get("reason")))
                                  for _x in _pq["quarantined"]))
            _ts = _time.strftime("%Y%m%d_%H%M%S")
            _path = os.path.join(_dir, "graph_{}.json".format(_ts))
            _tmp = _path + ".tmp"
            # ★第313批 刀2：写盘守卫（拒写即返回空串，调用方按「未落盘」处理，绝不猜）
            if not _m313_write_guard(_path, component="CallGraphAnalyzer.dump_graph_snapshot"):
                return ""
            # ★第305批刀2：分色由**边自带的 kind** 聚合（不再全库重扫正则）
            # ★328批刀1（A6聚合收口）：**不再折叠未知 kind**。旧实现把不在预设键里的
            #   kind 一律记成 static（unique_name 1297 + self_attr 1118 + typed_infer 7
            #   全部并进去），聚合里 static 虚高到 10048，看图的人会把软推断当硬自证。
            #   现在：预设七类**只用来固定输出顺序**，任何 kind 都记在它自己的档上。
            _edge_types = {"static": 0, "unique_name": 0, "self_attr": 0,
                           "event": 0, "byname": 0, "entry": 0, "typed_infer": 0}
            for _e in (_cg.get("edges") or []):
                if not isinstance(_e, dict):
                    continue
                _k = str(_e.get("kind") or "static")
                _edge_types[_k] = int(_edge_types.get(_k, 0)) + 1
            _et_total = sum(int(_v) for _v in _edge_types.values())
            _payload = dict(_cg)
            _payload["edge_types"] = _edge_types
            # 独立字段（**不塞进 edge_types 字典**，否则各档之和就不等于边数了）
            _payload["edge_types_total"] = _et_total
            _payload["edge_types_source"] = "per_edge_kind"
            # ★330批刀2（P1 判据用图门槛）：图必须能自证"我是哪个代码版本、哪套判据产的"。
            #   `code_version`  = 建边语义版本（`CODE_VERSION`，改建边就要改它）
            #   `criteria_version` = 判据版本（与 self_inspector 常量**同源**，不另写一份）
            #   两者缺一，判据侧一律拒绝（UNAVAILABLE），不拿"看起来正常"的读数下结论。
            _payload["code_version"] = str(self.CODE_VERSION)
            _payload["criteria_version"] = str(self._m330_judge_criteria_version())
            _payload["version_fields_src"] = (
                "%s/criteria同源self_inspector" % self.CODE_VERSION)
            _payload["dumped_at"] = _time.time()
            self.export_json(_tmp, data=_payload)
            os.replace(_tmp, _path)
            # ★401批刀3（L1 常驻副本）：另存一份 `graph_latest.json`，**不占滚动 7 份的配额**。
            #   为什么需要：`keep=7` 按小时轮转会冲掉历史对比基线（烛微401 §风险②），
            #   而"上一个代码版本的图"恰恰是回归对比的参照物；重建一次就有一份常驻底片。
            #   副本与快照**同内容同刻**（同一份 _payload），故它不新不旧；
            #   写盘失败只留痕、不影响快照与判据（返回值仍为快照路径）。
            _m401_lp = ""
            if _keep_latest_on:
                _m401_lp = os.path.join(_dir, _M401_GRAPH_LATEST_NAME)
                try:
                    if _m313_write_guard(_m401_lp,
                                         component="CallGraphAnalyzer.graph_latest"):
                        _m401_ltmp = _m401_lp + ".tmp"
                        self.export_json(_m401_ltmp, data=_payload)
                        os.replace(_m401_ltmp, _m401_lp)
                    else:
                        _m401_lp = ""
                except Exception as _le:
                    _m401_lp = ""
                    _logger.warning("常驻副本 graph_latest 落盘失败（快照已落，不影响判据）: %s: %s",
                                    type(_le).__name__, _le)
            try:
                # ★401批刀3：常驻副本不进轮转名单 —— 否则它会占掉 7 份里的一份，
                #   且因字母序靠后而"永不被删"（真正需要滚动的反而被挤出去）。
                _olds = sorted(_f for _f in os.listdir(_dir)
                               if (_f.startswith("graph_") and _f.endswith(".json")
                                   and _f != _M401_GRAPH_LATEST_NAME))
                for _old in _olds[:-keep]:
                    os.remove(os.path.join(_dir, _old))
            except Exception as _e:
                _logger.warning("调用图滚动清理失败（不影响本次落盘）: %s: %s",
                                type(_e).__name__, _e)
            _logger.info("[304刀1] 调用图落盘: %s（nodes=%d edges=%d 边类型=%s）",
                         os.path.relpath(_path, _root),
                         len(_payload.get("nodes") or []),
                         len(_payload.get("edges") or []), _edge_types)
            if _m401_lp:
                _logger.info("[401刀3] 常驻副本: %s（不进滚动配额）",
                             os.path.relpath(_m401_lp, _root))
            # ★331批刀2：落盘即自检 —— 把"图刚落盘"变成 A6 的触发点。
            #   挂在**统一落盘口**上：任何调用路径（含 SelfAwarenessEngine 的图重建）
            #   都会经过这里，不会有挂点漏网。自检失败只留痕，**不阻断落盘**。
            self._m331_auto_a6(_path)
            return _path
        except Exception as e:
            _logger.warning("调用图落盘失败（返回空串）: %s: %s", type(e).__name__, e)
            return ""

    def cluster_view(self, class_name: str, mask_ids: list | None = None,
                     scope_files: list | None = None) -> dict:
        """★315批刀3：只读类簇视图——给定类名（cls），返回该类簇的图结构指标。

        ★337批刀6：新增 `scope_files` —— 给定时**按文件**圈定类簇（Mixin 归族），
        不给时维持原 `cls` 单名过滤（向后兼容，既有调用方一字不改）。
        为什么要这一维：`_A4_CLUSTER_SCOPE` 声明的是"5 个文件"，实现却按单一
        类名过滤，两者差 116 个节点（4 个 Mixin 109 + InferenceContext 1 +
        MockInfoField 2 + 模块级 4）——这正是 A4 gap 的真正来源，**不是"未入图"**。
        复用 `_qual_id` / `nodes` / `edges` 现成结构（与 dump_graph_snapshot 同源）。
        输出：方法数、各方法入度/出度、零入边方法列表、跨簇边界调用数。
        供 PulseCodeLearner 等消费者把「无明确入口」启发式改为图判定。
        """
        _cg = self._ensure()
        _nodes = (_cg or {}).get("nodes", []) if isinstance(_cg, dict) else []
        _edges = (_cg or {}).get("edges", []) if isinstance(_cg, dict) else []
        if scope_files:
            # ★337批刀6：文件口径（Mixin 归族）—— 与 `_A4_CLUSTER_SCOPE` 的声明一致
            _m337_scope = {str(_f).replace("\\", "/") for _f in scope_files}
            _cls_nodes = [n for n in _nodes if isinstance(n, dict)
                          and str(n.get("file") or "").replace("\\", "/") in _m337_scope]
        else:
            _cls_nodes = [n for n in _nodes
                          if isinstance(n, dict) and n.get("cls") == class_name]
        _ids = [n.get("id") for n in _cls_nodes]
        _id_set = set(_ids)
        _in = {_i: 0 for _i in _ids}
        _out = {_i: 0 for _i in _ids}

        def _cls_of(qual_id: str) -> str:
            if not qual_id or ":" not in qual_id:
                return ""
            return qual_id.split(":", 1)[-1].split(".")[0]

        _cross = 0
        for _e in _edges:
            if not isinstance(_e, dict):
                continue
            _c = _e.get("caller")
            _ce = _e.get("callee")
            if _c in _out:
                _out[_c] += 1
            if _ce in _in:
                _in[_ce] += 1
            if (_c in _id_set) != (_ce in _id_set):
                _cross += 1
        # ★319批刀0（SC-1）：**只读遮蔽**——把指定方法的入边在**内存态**视作 0
        #   （不落 data/、不改台账、不入队、不触发行动；仅用于自检"断言是否真能发现"）。
        if mask_ids:
            for _mi in mask_ids:
                if _mi in _in:
                    _in[_mi] = 0
        _zero_in = [_i for _i, _v in _in.items() if _v == 0]
        return {
            "class": class_name,
            "method_count": len(_cls_nodes),
            "in_degrees": _in,
            "out_degrees": _out,
            "zero_in_degree_methods": _zero_in,
            "cross_cluster_calls": _cross,
        }

    # ===== ★317批刀2（G-6）：A4 覆盖断言的类簇口径——显式文件清单，钉死 =====
    # 口径差（339 / 455 / 462）的成因 —— ★337批刀6 **订正**（317 旧注释错）：
    #   ★旧注释写的"4 个 mixin 的方法**未入图**（半张地图）"已被实测**推翻**：
    #     烛微 337 前置逐节点核对 graph_20261021_130221.json ⇒ 109/109 个 mixin
    #     方法**全部在图内**（mixin_only_with_existing_node_of_same_name=109），
    #     5 个源文件在图内合计 455 个节点 = 346 + 109，一个不缺。
    #   * gap 116 的**真实成因是口径**：`cluster_view` 用 `n.get("cls") == class_name`
    #     **单类名**过滤，把 4 个 Mixin 类（109）＋ PulseInnerWorld.InferenceContext（1）
    #     ＋ MockInfoField（2）＋ 模块级函数（4）共 116 个节点排除在类簇之外。
    #   * 说明书面 455：5 个源文件（PulseInnerWorld.py 346 + 4 mixin 109）的 def 总数
    #   * 烛微 462 / 6 文件：455 + nucleus/reasoning/_inner_world_math.py 的 7 个**模块级**函数
    # ⇒ 裁定：`_inner_world_math.py` **不计入**（它是数学核，模块级函数不属于该类的方法集，
    #   图类簇永不包含它；计入会把"口径差"与"真缺口"混在一起）。
    # ★337批刀6：317.a4.v1（cls 单名过滤，339/116）→ **337.a4.v2**（文件口径，455/0）
    #   跨版本**禁止比较**（与 A-4/A-14 同型）：旧报告里的 339/116 不得与新读数并列。
    _A4_CRITERIA_VERSION = "337.a4.v2"
    _A4_CLUSTER_SCOPE = {
        "class": "PulseInnerWorld",
        "files_included": [
            "organs/brain/PulseInnerWorld.py",
            "organs/brain/pulse_inner_world_creative.py",
            "organs/brain/pulse_inner_world_emotion_aug.py",
            "organs/brain/pulse_inner_world_knowledge.py",
            "organs/brain/pulse_inner_world_support.py",
        ],
        "files_excluded": {
            "nucleus/reasoning/_inner_world_math.py":
                "模块级数学核（7 个模块级 def），非该类方法；图类簇永不包含 ⇒ 计入会污染 gap",
        },
        # ★337批刀6：口径＝文件过滤（上面的 `files_included`），实测 455 == 说明书 455
        "scope_mode": "files_included",
        "manual_baseline": 455,
        "graph_baseline": 455,
        "gap_baseline": 0,
        # 旧口径留档（**跨版本禁止比较**）：cls 单名过滤 ⇒ 图类簇 339、gap 116。
        # MRO 归族口径备查：339+109=448，残余 7 = 4 模块级 + InferenceContext 1 +
        # MockInfoField 2（本批**未采用**，避免出现第三把尺子）。
        "legacy_a4_v1": {
            "criteria_version": "317.a4.v1",
            "scope_mode": "cls",
            "graph_baseline": 339,
            "gap_baseline": 116,
        },
    }

    def sc1_mask_selfcheck(self, class_name="PulseInnerWorld", sample_n: int = 5) -> dict:
        """★319批刀0（SC-1 遮蔽自检）：**拿自己考考自己**（负样本版）。

        做法：从"当前有入边"的方法里取 `sample_n` 个作为**负样本**，在内存态把它们的
        入边遮蔽（视作 0），再看断言是否真的把它们判为 failed（零入边）。
        - 被遮蔽的方法**出现在**遮蔽后的零入边集合 ⇒ 命中（断言有效）
        - 没出现 ⇒ **漏报**（"假掌握"：断言看不见自己该看见的东西）

        红线（写死）：**只产计数与结论**，不改台账、不入队、不触发行动、不落 data/。
        """
        _cv0 = self.cluster_view(class_name,
                                 scope_files=self._A4_CLUSTER_SCOPE["files_included"])
        _degs = _cv0.get("in_degrees") or {}
        _cands = [_i for _i, _v in _degs.items() if int(_v or 0) > 0]
        _picked = _cands[:max(0, int(sample_n or 0))]
        if not _picked:
            _logger.info("[SC-1] 遮蔽自检 class=%s 无可用负样本（全零入边）⇒ 本轮不判", class_name)
            return {"class": class_name, "masked": 0, "hit": 0, "miss": 0,
                    "status": "no_sample"}
        _res = self.mastery_assertion_check(class_name,
                                            mask={"hide_methods": _picked})
        return {"class": class_name, "status": "ok",
                **(_res.get("sc1_mask") or {})}

    @staticmethod
    def sc2_regression_selfcheck(before_failed: list | set, after_failed: list | set,
                                 class_name: str = "PulseInnerWorld",
                                 graph_file: str = "",
                                 criteria_version: str = "") -> dict:
        """★319批刀5（SC-2 回归自检）：补丁/规则应用前后比 A1 failed 集合——**只许减不许增**。

        同 `graph_file` ＋ 同 `criteria_version` 才可比（否则记 `not_comparable`）。
        红线：只产计数与结论，不改台账、不入队、不触发行动。
        """
        _b = set(before_failed or [])
        _a = set(after_failed or [])
        _added = sorted(_a - _b)
        _removed = sorted(_b - _a)
        _ok = not _added
        _logger.info(
            "[SC-2] 回归自检 class=%s graph_file=%s criteria_version=%s "
            "added=%d removed=%d verdict=%s",
            class_name, graph_file or "-", criteria_version or "-",
            len(_added), len(_removed), "ok" if _ok else "regression")
        return {"class": class_name, "added": _added, "removed": _removed,
                "added_count": len(_added), "removed_count": len(_removed),
                "ok": _ok, "verdict": "ok" if _ok else "regression",
                "graph_file": graph_file, "criteria_version": criteria_version}

    # ===== ★328批刀1：A6 聚合口径自检（软推断不得被折叠成硬自证）=====
    #: A6 口径版本（换口径必须换版本号，便于历史读数对表）
    #: ★330批刀2（小林拍板③·P1）：**代码版本标识**。
    #  改动了建边逻辑/kind 判定就必须改这里 —— 判据用图门槛靠它区分
    #  "图是这套代码产的" 与 "图是上一版代码产的"。不是仓库 commit id：
    #  绝大多数提交不影响图的语义，把它们也算成版本只会让门槛天天误报。
    #   ★335批刀3（334挂账③ · 2026-10-21）：升版 —— 331/333 未升版导致
    #     08:26 与 09:34:58 两张图同戳 `330.cg.v1`，版本门**分不出代次**，
    #     ⑥ 退化为恒真门（334 五条最硬⑤）。升版后版本门才真正可判。
    #   ⚠️ 结构性后果（如实标注，不掩盖）：旧图（330 戳）从此被拒 ⇒ 活体
    #     **不重启就产不出新图**，⑥ 会从"恒真"翻成"恒假"。这是**更安全**的
    #     方向（宁可判不可用，不可冒充通过），但 335/336 首跑前必须先重启产图。
    CODE_VERSION = "335.cg.v1"

    @staticmethod
    def _m330_judge_criteria_version() -> str:
        """★330批刀2：落图时写入的**判据版本**——与 self_inspector 判据常量同源。

        为什么必须同源、不能各写一份：P1 门槛判的是「这张图是**这套判据**认可的
        代码产出的」。两个字符串一旦各维护一份，改判据时必然漂移，门槛就成了摆设
        （图上写着对得上的版本，实际判据早换了）。

        读不到（模块改名/延迟加载）⇒ 返回 ``"unknown"``，由判据侧按**不匹配**拒绝
        —— 不猜、不放过。
        """
        try:
            # ★实测纠正：它是 **SelfInspector 的类属性**，不是模块级常量。
            #   第一版写成 `from ... import _DEAD_CODE_CRITERIA_VERSION` 会 ImportError，
            #   然后静默退成 "unknown" —— 图上写着 unknown 看起来也像个值，
            #   但判据侧必然拒绝，等于把门槛变成了一道永久关闭的门。
            from nucleus.self_inspector import SelfInspector as _SI330
            return str(getattr(_SI330, "_DEAD_CODE_CRITERIA_VERSION", "") or "unknown")
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer::_m330_judge_criteria_version")
            return "unknown"

    A6_CRITERIA_VERSION = "328.a6.v1"
    #: 图解析缓存（key=(路径, mtime)）——同一份图在一个进程里只解析一次
    _A6_CACHE: dict = {}

    # ★331批刀2：A6 自检"每图只留痕一次"的路径账（防 silent 分支重复打噪声）
    _M331_A6_LOGGED: set = set()

    def _m331_auto_a6(self, graph_path: str) -> dict | None:
        """★331批刀2：图落盘后自动跑一次 A6 聚合口径自检，并**无条件**留痕一行。

        为什么立这个挂点（烛微331§0-2 坐实）：A6 的三项判据自 328刀1 起**只在隔离根
        实测过**，断言从不在活体周期跑 —— 全日志检索 `[A6]` **零命中**。跨批必然回潮：
        折叠缺陷属于"旧代码产的图才有"，没人定期跑就没有人发现它又回来了。

        为什么 `folded=0` 也要打日志：只写"异常才打"，正常时日志里根本检索不到 `[A6]`，
        就无法区分"跑过且正常"与"压根没跑" —— 这与"把没测当没病"是同一个坑。
        故无条件留痕，`folded>0` 再加一条 WARNING。

        每图只打一次（`_M331_A6_LOGGED`）：同一张图被反复读时不刷屏。
        返回 A6 结果（异常/不可用则 None 或带 verdict 的字典），**不阻断落盘**。
        """
        if not graph_path:
            return None
        _key = os.path.basename(graph_path)
        if _key in self._M331_A6_LOGGED:
            return None
        self._M331_A6_LOGGED.add(_key)
        try:
            _r = self.a6_edge_kind_consistency(graph_path=graph_path)
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer::_m331_auto_a6")
            _logger.warning("[A6] 落盘自检异常（**不阻断落盘**）: %s proc_gen=%s",
                            type(_e).__name__, _m335_pg())
            return None
        if not isinstance(_r, dict):
            return None
        _v = str(_r.get("verdict") or "")
        _folded = int(_r.get("folded_into_static", 0) or 0)
        if _v == "UNAVAILABLE":
            # 版本对不上/图不可解析 ⇒ **不填 0**，如实记不可用与原因
            _logger.warning("[A6] folded=%s verdict=UNAVAILABLE reason=%s file=%s"
                            " proc_gen=%s",
                            _folded, _r.get("reason"), _key, _m335_pg())
            return _r
        _logger.info("[A6] folded=%d verdict=%s edges=%s agg_sum=%s file=%s"
                     " proc_gen=%s",
                     _folded, _v, _r.get("total_edges"), _r.get("agg_sum"), _key,
                     _m335_pg())
        # ★337批刀8（裁决①）：A6 **双证行** —— 把图内自证与图外心跳放在同一行，
        #   冲突时按 `_m337_a6_arbitrate` 仲裁（w1!=alive ⇒ provisional）。
        #   为什么必须在同一行：分开两行时读方无法判断"A6 的 PASS 到底采信了没有"，
        #   而 `verdict_final` 才是能进判据的那个值。
        try:
            from nucleus.world_model.external_heartbeat import (
                w1_verdict_brief as _w1v, _m337_a6_arbitrate as _arb,
            )
            _w1 = _w1v()
            _fin = _arb(_v, _w1)
            _logger.info(
                "[A6+W1] graph=%s dumped_at=%s snapshot_age_sec=%s w1=%s a6=%s "
                "verdict_final=%s basis=%s proc_gen=%s",
                _key, _r.get("dumped_at"), _w1.get("age_sec"), _w1.get("verdict"),
                _v, _fin.get("verdict"), _fin.get("basis"), _m335_pg())
            _r["w1"] = {"verdict": _w1.get("verdict"),
                        "age_sec": _w1.get("age_sec")}
            _r["verdict_final"] = _fin.get("verdict")
            _r["arbitration_basis"] = _fin.get("basis")
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer::_m331_auto_a6 w1_arbitrate")
            _logger.warning("[A6+W1] W1 仲裁不可用（不阻断 A6）: %s proc_gen=%s",
                            type(_e).__name__, _m335_pg())
        if _folded > 0:
            _logger.warning("[A6] folded=%d > 0 ⇒ 存在 kind 被折叠进 static（file=%s）"
                            " proc_gen=%s", _folded, _key, _m335_pg())
        return _r

    def a6_edge_kind_consistency(self, graph_path: str | None = None,
                                 root: str | None = None) -> dict:
        """A6：落盘图的**聚合 edge_types** 必须与**逐边 kind** 逐档相等，且同数。

        为什么立这条断言（328刀1）：
          `dump_graph_snapshot` 旧实现把不在预设键里的 kind 一律记成 `static`，
          于是 `unique_name` / `self_attr` / `typed_infer`（**软推断**）被折叠进
          `static`（实测 7626+1297+1118+7=10048）。判死主循环与 O3 三档分层靠的是
          "硬/软/推断"之别，聚合一旦被打平，任何按聚合取数的消费方都会把软证据当
          硬自证 —— 而这类错误**不会自己暴露**（数字看起来完全正常，只是偏大）。
          故用一条可复算的守恒断言把它钉住。

        判据（三项全过才 PASS，全部可复算）：
          1. `per_edge_total == total_edges`（逐边计数自洽）；
          2. `agg_sum == total_edges`（聚合各档之和 == 边数，即**没有丢边**）；
          3. 每个 kind 档 `agg[k] == per_edge[k]`（**没有折叠**；聚合允许多带零值键）。

        边界：图不存在/不可解析 ⇒ `UNAVAILABLE`，**不填 0** ——
        0 是"图上真的没有这种边"，和"没读到图"必须能区分开。
        本断言**不进 mastery 的 total/passed/failed**（同 A5：混进去会污染 A1/A4 基线）。
        """
        _root = root or _project_root()
        _gp = graph_path
        if not _gp:
            _dir = os.path.join(_root, "data", "call_graph")
            _cands = []
            try:
                for _f in os.listdir(_dir):
                    if _f.startswith("graph_") and _f.endswith(".json"):
                        _p = os.path.join(_dir, _f)
                        try:
                            _cands.append((os.path.getmtime(_p), _f, _p))
                        except OSError as _me:
                            silent_exc(_me, where="CallGraphAnalyzer::a6 candidate mtime")
            except OSError as _le:
                silent_exc(_le, where="CallGraphAnalyzer::a6 listdir")
            if not _cands:
                return {"assertion": "A6", "verdict": "UNAVAILABLE",
                        "reason": "无 graph_*.json（没落盘 ⇒ 不等于没有边）",
                        "criteria_version": self.A6_CRITERIA_VERSION}
            _gp = sorted(_cands)[-1][2]
        try:
            _mt = os.path.getmtime(_gp)
        except OSError as _e:
            silent_exc(_e, where="CallGraphAnalyzer::a6 graph mtime")
            _mt = 0.0
        _ck = (_gp, _mt)
        if self._A6_CACHE.get("key") == _ck and isinstance(self._A6_CACHE.get("val"), dict):
            return dict(self._A6_CACHE["val"])
        try:
            with open(_gp, encoding="utf-8", errors="replace") as _f:
                _gd = json.load(_f)
        except (OSError, ValueError) as _e:
            silent_exc(_e, where="CallGraphAnalyzer::a6 read graph")
            return {"assertion": "A6", "verdict": "UNAVAILABLE",
                    "reason": "图不可解析: %s" % type(_e).__name__,
                    "graph_file": os.path.basename(_gp),
                    "criteria_version": self.A6_CRITERIA_VERSION}
        # ★330批刀2（P1）：A6 的第一项判据由「图可解析」扩展为「图可解析 **且** 版本匹配」。
        #   缺字段/不匹配一律 UNAVAILABLE（**语义不变：没测 ≠ 没有边，不填 0**）。
        #   为什么 A6 也要卡版本：A6 断言的是"聚合 == 逐边"，而 `edge_types` 的折叠缺陷
        #   是**旧代码**产的图才有的；拿一张旧图跑 A6 得到 FAIL，会被误读成"现在的代码
        #   还在折叠"。版本对不上，FAIL 与 PASS 都不该信。
        _a6_gcv = str((_gd or {}).get("code_version") or "")
        _a6_crv = str((_gd or {}).get("criteria_version") or "")
        if not _a6_gcv or not _a6_crv:
            return {"assertion": "A6", "verdict": "UNAVAILABLE",
                    "reason": ("图缺版本字段（code_version=%r criteria_version=%r）"
                               "⇒ 330 前的旧图，无法确认与当前代码同版本"
                               % (_a6_gcv, _a6_crv)),
                    "graph_file": os.path.basename(_gp),
                    "graph_code_version": _a6_gcv,
                    "graph_criteria_version": _a6_crv,
                    "criteria_version": self.A6_CRITERIA_VERSION}
        if _a6_gcv != str(self.CODE_VERSION):
            return {"assertion": "A6", "verdict": "UNAVAILABLE",
                    "reason": ("图 code_version=%s ≠ 当前代码 %s ⇒ 跨版本图不作判据"
                               % (_a6_gcv, self.CODE_VERSION)),
                    "graph_file": os.path.basename(_gp),
                    "graph_code_version": _a6_gcv,
                    "graph_criteria_version": _a6_crv,
                    "criteria_version": self.A6_CRITERIA_VERSION}
        _edges = _gd.get("edges") if isinstance(_gd, dict) else None
        if not isinstance(_edges, list):
            return {"assertion": "A6", "verdict": "UNAVAILABLE",
                    "reason": "图内无 edges 列表",
                    "graph_file": os.path.basename(_gp),
                    "graph_code_version": _a6_gcv,
                    "graph_criteria_version": _a6_crv,
                    "criteria_version": self.A6_CRITERIA_VERSION}
        _per: dict = {}
        for _e in _edges:
            if not isinstance(_e, dict):
                continue
            _k = str(_e.get("kind") or "static")
            _per[_k] = int(_per.get(_k, 0)) + 1
        _agg_raw = _gd.get("edge_types")
        _agg: dict = {}
        if isinstance(_agg_raw, dict):
            for _k, _v in _agg_raw.items():
                try:
                    _agg[str(_k)] = int(_v)
                except (TypeError, ValueError) as _ve:
                    silent_exc(_ve, where="CallGraphAnalyzer::a6 agg value")
        _per_total = sum(_per.values())
        _agg_sum = sum(_agg.values())
        _mismatch: dict = {}
        for _k in sorted(set(_per) | set(_agg)):
            _pe = int(_per.get(_k, 0))
            _ag = int(_agg.get(_k, 0))
            if _pe != _ag:
                _mismatch[_k] = {"per_edge": _pe, "aggregated": _ag}
        # 折叠检测：聚合 static 超出逐边 static 的部分就是被折叠进来的软证据
        _fold = max(0, int(_agg.get("static", 0)) - int(_per.get("static", 0)))
        _c1 = (_per_total == len(_edges))
        _c2 = (_agg_sum == len(_edges))
        _c3 = (not _mismatch)
        _out = {
            "assertion": "A6",
            "verdict": "PASS" if (_c1 and _c2 and _c3) else "FAIL",
            "criteria_version": self.A6_CRITERIA_VERSION,
            "graph_file": os.path.basename(_gp),
            "graph_mtime": _mt,
            "total_edges": len(_edges),
            "per_edge": dict(sorted(_per.items())),
            "per_edge_total": _per_total,
            "aggregated": dict(sorted(_agg.items())),
            "agg_sum": _agg_sum,
            "edge_types_total_field": _gd.get("edge_types_total"),
            "mismatch": _mismatch,
            "folded_into_static": _fold,
            "checks": {"per_edge_self_consistent": _c1,
                       "agg_sum_equals_edges": _c2,
                       "per_kind_equal": _c3},
        }
        if _fold:
            _logger.warning(
                "[A6] 聚合 static 比逐边多 %d 条 ⇒ 疑似仍有 kind 被折叠（graph=%s）"
                " proc_gen=%s", _fold, _out["graph_file"], _m335_pg())
        self._A6_CACHE["key"] = _ck
        self._A6_CACHE["val"] = dict(_out)
        return _out

    def mastery_assertion_check(self, class_name="PulseInnerWorld",
                                manual_method_count=None,
                                mask: dict | None = None,
                                include_a5: bool = True,
                                include_a6: bool = True) -> dict:
        """★316批刀1：掌握度结构断言（只跑断言、不跑检测器）。

        **不进 issues、不进趋势门禁**（守 307—312 的 info/统计口径不污染问题清单）。
        数据源＝`cluster_view` 的 method_count ＋ 说明书方法数（由调用方传入）。

        - A1 入边断言：类簇内方法在图上有 ≥1 条入边；零入边方法计入 failed（应出现在待复核清单）。
        - A4 覆盖断言：说明书方法集大小 == 图类簇节点数；不一致即报「半张地图」缺口。

        返回四项计数 {total, passed, failed, unverifiable} ＋ a1/a4 明细。
        """
        # ★319批刀0（SC-1）：只读遮蔽（负样本）——不落 data/、不改台账
        _hide = list((mask or {}).get("hide_methods") or [])
        _cv = self.cluster_view(class_name, mask_ids=_hide or None,
                                scope_files=self._A4_CLUSTER_SCOPE["files_included"])
        _methods = int(_cv.get("method_count", 0) or 0)
        _sc1 = {"masked": len(_hide), "hit": 0, "miss": 0}
        if _hide:
            _zset = set(_cv.get("zero_in_degree_methods") or [])
            _hit = sum(1 for _m in _hide if _m in _zset)
            _sc1 = {"masked": len(_hide), "hit": _hit, "miss": len(_hide) - _hit}
            _logger.info(
                "[SC-1] 遮蔽自检 class=%s masked=%d hit=%d miss=%d"
                "（只产计数与结论：不改台账/不入队/不触发行动/不落 data/）",
                class_name, _sc1["masked"], _sc1["hit"], _sc1["miss"])
        _zero_in = list(_cv.get("zero_in_degree_methods") or [])
        _a1_failed = len(_zero_in)
        _a1_passed = max(0, _methods - _a1_failed)
        _a4 = {"manual": None, "graph": _methods, "gap": None,
               "status": "unverifiable",
               "criteria_version": self._A4_CRITERIA_VERSION,
               "scope_files": list(self._A4_CLUSTER_SCOPE["files_included"])}
        if isinstance(manual_method_count, int) and not isinstance(manual_method_count, bool) \
                and manual_method_count >= 0:
            _gap = abs(int(manual_method_count) - _methods)
            _a4 = {"manual": int(manual_method_count), "graph": _methods,
                   "gap": _gap, "status": "passed" if _gap == 0 else "failed"}
        _a5 = {"assertion": "A5", "verdict": "SKIPPED", "reason": "include_a5=False"}
        if include_a5:
            try:
                _a5 = self.a5_growth_intake_assertion()
            except Exception as _a5e:
                silent_exc(_a5e, where="CallGraphAnalyzer::mastery_assertion_check A5")
                _a5 = {"assertion": "A5", "verdict": "UNAVAILABLE",
                       "reason": "异常: %s" % type(_a5e).__name__}
        # ★328批刀1：A6 聚合口径自检 —— 与 A5 同样**单独一栏**，不进下面三项计数
        _a6 = {"assertion": "A6", "verdict": "SKIPPED", "reason": "include_a6=False"}
        if include_a6:
            try:
                _a6 = self.a6_edge_kind_consistency()
            except Exception as _a6e:
                silent_exc(_a6e, where="CallGraphAnalyzer::mastery_assertion_check A6")
                _a6 = {"assertion": "A6", "verdict": "UNAVAILABLE",
                       "reason": "异常: %s" % type(_a6e).__name__}
        _failed = _a1_failed + (1 if _a4["status"] == "failed" else 0)
        _unverifiable = 1 if _a4["status"] == "unverifiable" else 0
        _passed = max(0, _methods - _failed)
        return {
            "class": class_name,
            "sc1_mask": _sc1,
            "criteria_version": self._A4_CRITERIA_VERSION,
            "cluster_scope": self._A4_CLUSTER_SCOPE,
            "total": _methods,
            "passed": _passed,
            "failed": _failed,
            "unverifiable": _unverifiable,
            "a1_inbound": {"passed": _a1_passed, "failed": _a1_failed,
                           "zero_in_degree_sample": _zero_in[:50]},
            "a4_coverage": _a4,
            "cross_cluster_calls": _cv.get("cross_cluster_calls", 0),
            # ★326批刀5：A5 需求承接（**单独一栏**，不进 total/passed/failed——
            #   它是"需求→能力"链路的断言，不是类簇结构断言，混进去会污染 A1/A4 读数
            #   与 SC-2 回归基线）
            "a5_intake": _a5,
            # ★328批刀1：A6 聚合口径（同样单独一栏，不进 total/passed/failed）
            "a6_edge_kinds": _a6,
        }

    # ===== ★326批刀5：A5 需求承接断言（自进"变成能力"闭环的缺口）=====
    #: A5 的窗口拍数（325刀0 入队规格：每拍≤1 条；默认 5 拍内须有承接或作废）
    A5_WINDOW_TICKS = 5
    #: 视为"显式作废"的补丁状态（承接侧的状态机取值）
    A5_VOID_STATUSES = frozenset({
        "cancelled", "canceled", "expired", "obsolete", "rejected",
        "archived", "withdrawn",
    })

    def a5_growth_intake_assertion(self, n_window: int | None = None,
                                   root: str | None = None,
                                   journal_path: str | None = None,
                                   patch_history_path: str | None = None,
                                   handbook_refs: list | None = None,
                                   tick_now: int | None = None) -> dict:
        """A5：**需求驱动条目须在 N 拍内被承接（被摘要引用）或显式作废**。

        为什么加这条断言：需求源（growth_need）入队后，台账侧只有"入了几条"，
        没有任何断言回答"这些条目后来怎么了"——A1/A4 只管类簇结构与覆盖率，
        "需求→能力"这一段在断言族里是空的（烛微326 §3-维度2）。

        数据源（都可落盘、可复算）：
          * `data/reports/evolution/intake_journal.jsonl`（326刀5 新增：逐拍一行，
            带本拍入队条目 key/file/method）—— **主源**；
          * `data/patches/patch_history.json`——承接落地的证据（同 file+method 出现
            即视为被承接；状态 ∈ `A5_VOID_STATUSES` 视为显式作废）；
          * `handbook_refs`——**可选**入参：调用方在进程内采集到
            `organ_handbook_updated` 事件后把条目键传进来（该事件不落盘，
            不传就不参与判据，绝不把"没采到"当"没承接"）。

        红线（与 316刀1 一致）：只产计数与结论，**不进 issues、不进趋势门禁**。

        Returns: 计数 + 逐条 `rows`（key/tick/age/status/basis）+ `verdict`。
          无台账 ⇒ `UNAVAILABLE`（不填 0）。
        """
        import json as _json
        _win = int(self.A5_WINDOW_TICKS if n_window is None else n_window)
        _root = root or os.getcwd()
        _jp = journal_path or os.path.join(_root, "data", "reports", "evolution",
                                           "intake_journal.jsonl")
        _hp = patch_history_path or os.path.join(_root, "data", "patches",
                                                 "patch_history.json")
        _out = {"assertion": "A5", "n_window": _win, "journal": _jp,
                "journal_exists": os.path.isfile(_jp), "items": 0,
                "referenced": 0, "voided": 0, "overdue_failed": 0, "pending": 0,
                "tick_now": None, "verdict": "UNAVAILABLE", "rows": []}
        if not _out["journal_exists"]:
            _out["reason"] = ("无逐拍台账（326刀5 起才落）⇒ 承接情况不可判定，"
                             "不填 0、不当作通过")
            return _out
        _entries = []
        try:
            with open(_jp, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _entries.append(_json.loads(_line))
                    except (ValueError, TypeError) as _m369_e:
                        silent_exc(_m369_e, where="nucleus/self_awareness/CallGraphAnalyzer.py:a5_growth_intake_assertion", level="debug")
                        continue
        except OSError as _e:
            silent_exc(_e, where="CallGraphAnalyzer::a5_growth_intake_assertion 读台账")
            _out["reason"] = "台账读取失败: %s" % type(_e).__name__
            return _out
        _max_tick = 0
        for _e in _entries:
            try:
                _max_tick = max(_max_tick, int(_e.get("tick", 0) or 0))
            except (TypeError, ValueError) as _m369_e:
                silent_exc(_m369_e, where="nucleus/self_awareness/CallGraphAnalyzer.py:a5_growth_intake_assertion", level="debug")
                continue
        _out["tick_now"] = int(tick_now if tick_now is not None else _max_tick)
        # 承接证据：patch_history 的 (file, method) → status
        _landed: dict = {}
        try:
            if os.path.isfile(_hp):
                _h = _json.load(open(_hp, encoding="utf-8"))
                if isinstance(_h, dict):
                    _h = _h.get("patches") or []
                for _p in (_h or []):
                    if not isinstance(_p, dict):
                        continue
                    _k = "%s::%s" % (str(_p.get("file") or ""), str(_p.get("method") or ""))
                    _landed[_k] = str(_p.get("status") or "")
        except Exception as _e:
            silent_exc(_e, where="CallGraphAnalyzer::a5_growth_intake_assertion 读台账")
        _refs = set(handbook_refs or [])
        for _e in _entries:
            for _it in (_e.get("growth_items") or []):
                if not isinstance(_it, dict):
                    continue
                _out["items"] += 1
                _key = str(_it.get("key") or "")
                _t = int(_e.get("tick", 0) or 0)
                _age = max(0, int(_out["tick_now"]) - _t)
                _status, _basis = "pending", ""
                if _key in _refs or ("%s::%s" % (_it.get("file") or "",
                                                 _it.get("method") or "")) in _refs:
                    _status, _basis = "referenced", "handbook organ_handbook_updated 命中"
                elif _key in _landed:
                    if _landed[_key] in self.A5_VOID_STATUSES:
                        _status, _basis = "voided", "patch_history 状态=%s" % _landed[_key]
                    else:
                        _status, _basis = "referenced", "patch_history 已承接(状态=%s)" % _landed[_key]
                elif _age >= _win:
                    _status, _basis = "overdue", "超 %d 拍仍无承接/作废" % _age
                _out["rows"].append({"key": _key, "tick": _t, "age": _age,
                                     "status": _status, "basis": _basis})
                if _status == "referenced":
                    _out["referenced"] += 1
                elif _status == "voided":
                    _out["voided"] += 1
                elif _status == "overdue":
                    _out["overdue_failed"] += 1
                else:
                    _out["pending"] += 1
        if not _out["items"]:
            _out["reason"] = "台账存在但零需求条目 ⇒ 无从判定（不填 0 冒充通过）"
            return _out
        _out["verdict"] = "FAIL" if _out["overdue_failed"] else "PASS"
        _logger.info(
            "[A5] 需求承接断言 items=%d referenced=%d voided=%d overdue=%d pending=%d "
            "window=%d tick_now=%s verdict=%s",
            _out["items"], _out["referenced"], _out["voided"],
            _out["overdue_failed"], _out["pending"], _win, _out["tick_now"],
            _out["verdict"])
        return _out

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
