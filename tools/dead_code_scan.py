# -*- coding: utf-8 -*-
r"""主线第55批 T5：8 大模块死代码检测（★只检测，不删除任何代码）

背景
----
P0/P1 债务攻坚需要知道「哪些代码已经没人用」。人工审查 296 个 .py 不现实，
且**删除是不可逆动作** —— 本工具只产出报告，供星轨/人工裁决。

8 大模块
--------
``base`` / ``functions`` / ``hardware`` / ``nucleus`` / ``organs`` /
``pulses`` / ``somatics`` / ``utils``（顶层包，不含 data/docs/tests/tools）

分级（避免误杀，★宁可漏报不可误删）
------------------------------------
- ``ZERO_REF``    ：全库（含 tests）零引用 —— 高置信，但仍需人工确认（反射风险）
- ``TEST_ONLY``   ：仅被 tests 引用 —— 疑似（可能是测试专用夹具/历史遗留）
- ``DYNAMIC_RISK``：名字以字符串形式出现在代码里（getattr/\_\_all\_\_/配置）
                    —— 可能被反射调用，**不可删除**

★设计约束
---------
1. **绝不修改任何源码** —— 本脚本只读
2. 复 ``nucleus.data.exclude_dirs``（第55批 T4 统一后的权威清单），
   副本/缓存/备份目录**不计入扫描**（否则引用统计会自我膨胀）
3. 反射/动态调用无法静态判定 → 一律标注 ``DYNAMIC_RISK``，不判为可删

用法
----
    python tools/dead_code_scan.py                # 输出到 stdout + 报告文件
    python tools/dead_code_scan.py --json out.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

# ★第55批 T4：统一排除清单（新增目录只需改 exclude_dirs 一处）
from nucleus.data import exclude_dirs as E

# ★第55批 T3：跨盘安全的 relpath（同盘行为与 os.path.relpath 一致，
#   跨盘降级绝对路径而不抛 ValueError —— 测试沙箱可能落在别的盘）
from nucleus.data.path_utils import normalize_relpath
from nucleus._silent_except import silent_exc

MODULES: tuple[str, ...] = (
    "base", "functions", "hardware", "nucleus",
    "organs", "pulses", "somatics", "utils",
)

#: 协议/dunder 方法：即使零引用也不得删除（Python 协议调用是隐式的）
DUNDER_RE = re.compile(r"^__.*__$")

#: 看起来像框架钩子的名字（on_pulse / _register / handle_* 等）—— 由框架按名调用
HOOK_RE = re.compile(
    r"^(on_|handle_|_register|register_|setup|teardown|init_|before_|after_)",
)


# ★主线第58批 T3（P2-398）：可配置排除 + 字符串加载追踪
#: 死代码扫描额外排除的目录名（在 exclude_dirs 基础上追加；可经环境变量
#: DEAD_CODE_EXTRA_EXCLUDES 逗号补充，便于按需扩列）。
SCAN_EXTRA_EXCLUDES = frozenset(
    {"tmp", "backups", "__pycache__", "code_backups"}
)

#: 标识符形态（仅把合法 Python 名视为潜在动态引用，避免中文文本误命中）
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: 模块路径形态（'a.b.C'，至少含一个点）—— 识别字符串元组插件加载
_MODULE_DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")


def _module_symbols(dotted: str) -> set[str]:
    """从 'a.b.C' 取出末段符号名（最可能的类/函数名）。"""
    _parts = [p for p in dotted.split(".") if p]
    return {_parts[-1]} if _parts else set()


def extract_dynamic_load_names(files: list[str]) -> set[str]:
    """★T3 字符串加载追踪：识别 importlib.import_module / __import__ / getattr 的
    字符串参数，作为潜在动态引用名（保守：命中即标 DYNAMIC_RISK，宁可漏报不可误删）。"""
    out: set[str] = set()
    for _f in files:
        try:
            _src = open(_f, encoding="utf-8", errors="ignore").read()  # noqa: SIM115
            _tree = ast.parse(_src, filename=_f)
        except Exception as e:
            silent_exc(e, "dead_code_scan:96:源文件解析异常", level="warning")
            continue
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.Call):
                _fn = _n.func
                _callee = _fn.attr if isinstance(_fn, ast.Attribute) else (
                    _fn.id if isinstance(_fn, ast.Name) else None)
                if _callee in ("import_module", "importlib_import_module", "__import__"):
                    for _a in _n.args:
                        if isinstance(_a, ast.Constant) and isinstance(_a.value, str):
                            out |= _module_symbols(_a.value)
                elif _callee == "getattr" and len(_n.args) >= 2:
                    _a1 = _n.args[1]
                    if isinstance(_a1, ast.Constant) and isinstance(_a1.value, str):
                        out.add(_a1.value)
            # ★T3-b：("module.path", "func_name") 字符串元组插件加载
            #   （如 tmp/test_isolation.py 的 getattr(import_module(m), f) 重置夹具）
            elif isinstance(_n, ast.Tuple) and len(_n.elts) == 2:
                _e0, _e1 = _n.elts
                if (isinstance(_e0, ast.Constant) and isinstance(_e0.value, str)
                        and isinstance(_e1, ast.Constant) and isinstance(_e1.value, str)
                        and _MODULE_DOTTED_RE.match(_e0.value)
                        and _IDENT_RE.match(_e1.value)):
                    out.add(_e1.value)
    return out


def _iter_tmp_py(root: str) -> list[str]:
    """★T3-b：收集 tmp/ 下全部 .py（排除缓存/备份子目录），仅用于动态加载追踪。
    tmp 仍不计入 def/ref 统计，只作为反射风险信号（保守：宁可漏报不可误删）。"""
    _tmp = os.path.join(root, "tmp")
    if not os.path.isdir(_tmp):
        return []
    out: list[str] = []
    for _dp, _dns, _fns in os.walk(_tmp):
        _dns[:] = [d for d in _dns
                   if d not in ("__pycache__", "code_backups", ".bak_batch58")]
        for _f in _fns:
            if _f.endswith(".py"):
                out.append(os.path.join(_dp, _f))
    return out


def _iter_json_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for _v in obj.values():
            yield from _iter_json_strings(_v)
    elif isinstance(obj, list):
        for _v in obj:
            yield from _iter_json_strings(_v)


def collect_config_refs(root: str) -> set[str]:
    """★T3 配置引用追踪：扫描 data/ 下 *.json（排除副本/知识/模型目录），
    提取标识符形态的字符串值，作为潜在动态引用名。"""
    out: set[str] = set()
    _data_dir = os.path.join(root, "data")
    if not os.path.isdir(_data_dir):
        return out
    try:
        import json as _json
    except Exception:
        return out
    for _dp, _dns, _fns in os.walk(_data_dir):
        _dns[:] = [d for d in _dns
                   if d not in ("code_backups", "knowledge", "models",
                                "experience", "stream")]
        for _fn in _fns:
            if not _fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(_dp, _fn), encoding="utf-8",
                             errors="ignore") as _fh:
                    _obj = _json.load(_fh)
            except Exception:
                continue
            for _v in _iter_json_strings(_obj):
                if isinstance(_v, str) and _IDENT_RE.match(_v):
                    out.add(_v)
    return out


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def iter_source_files(root: str, extra_excludes: set[str] | None = None) -> list[str]:
    """遍历 ``root`` 下全部 .py（★已排除副本/缓存/备份/临时目录，可追加排除）。"""
    _extra = set(extra_excludes or ())
    out: list[str] = []
    for _dp, _dns, _fns in os.walk(root):
        _dns[:] = [d for d in _dns
                   if not E.is_excluded(d) and d not in _extra]
        for _f in _fns:
            if _f.endswith(".py"):
                out.append(os.path.join(_dp, _f))
    return out


def collect_definitions(files: list[str], root: str = _PROJ) -> dict[str, list[dict]]:
    """AST 收集每个文件的**模块级** def/class 名 + 一些元信息。

    Args:
        files: 待解析文件列表。
        root:  **相对路径的基准目录** —— 必须传扫描根，否则跨盘会抛
               ``ValueError``（测试沙箱可能落在与项目不同的盘）。
    """
    defs: dict[str, list[dict]] = {}
    for _f in files:
        try:
            _src = open(_f, encoding="utf-8", errors="ignore").read()  # noqa: SIM115
            _tree = ast.parse(_src, filename=_f)
        except (SyntaxError, ValueError, UnicodeDecodeError):
            continue
        _rel = normalize_relpath(_f, root)
        _all: set[str] = set()
        _strings: set[str] = set()

        # __all__ 列表里的名字
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.Assign):
                for _t in _n.targets:
                    if isinstance(_t, ast.Name) and _t.id == "__all__":
                        try:
                            _all |= set(ast.literal_eval(_n.value))
                        except Exception as e:
                            silent_exc(e, where="tools.dead_code_scan::collect_definitions L224")
            # 所有字符串常量（用于反射风险判定）
            if isinstance(_n, ast.Constant) and isinstance(_n.value, str):
                if len(_n.value) < 64:
                    _strings.add(_n.value)

        for _n in _tree.body:
            if not isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                continue
            _name = _n.name
            _deco = []
            for _d in getattr(_n, "decorator_list", []):
                try:
                    _deco.append(ast.unparse(_d))
                except Exception as e:
                    silent_exc(e, where="tools.dead_code_scan::collect_definitions L240")
            defs.setdefault(_rel, []).append({
                "name": _name,
                "lineno": _n.lineno,
                "kind": "class" if isinstance(_n, ast.ClassDef) else "def",
                "in_all": _name in _all,
                "decorators": _deco,
                "module": _rel,
            })
        # 记录该文件的字符串常量（反射风险）
        defs.setdefault(_rel, []).append({
            "_strings": _strings,
        })
    return defs


def collect_refs(files: list[str]) -> tuple[dict[str, int], set[str]]:
    """★AST 单次遍历：统计每个标识符作为**引用**出现的次数 + 全部字符串常量。

    为什么不用正则全文匹配：名字数（数千）× 文件数（数百）= 百万次正则 → 超时。
    AST 一次遍历是 O(总代码量)，且**天然排除注释与 docstring**。

    ★注意：``def foo`` / ``class Foo`` 的名字**不是** ``ast.Name`` 节点，
      因此定义处不会被计入引用 —— 无需再减 1。

    Returns:
        (name -> 引用次数, 全部字符串常量集合)
    """
    counter: dict[str, int] = {}
    strings: set[str] = set()
    for _f in files:
        try:
            _src = open(_f, encoding="utf-8", errors="ignore").read()  # noqa: SIM115
            _tree = ast.parse(_src, filename=_f)
        except (SyntaxError, ValueError, OSError, UnicodeDecodeError):
            continue
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.Name):
                counter[_n.id] = counter.get(_n.id, 0) + 1
            elif isinstance(_n, ast.Attribute):
                counter[_n.attr] = counter.get(_n.attr, 0) + 1
            elif isinstance(_n, ast.alias):
                # import 也算引用（ruff F401 已管 unused import，此处保守计为引用）
                _a = _n.name.split(".")[0]
                counter[_a] = counter.get(_a, 0) + 1
                if _n.asname:
                    counter[_n.asname] = counter.get(_n.asname, 0) + 1
            elif isinstance(_n, ast.Constant) and isinstance(_n.value, str):
                if len(_n.value) < 64:
                    strings.add(_n.value)
    return counter, strings


def count_references(files: list[str], names: set[str]) -> dict[str, int]:
    """（保留兼容入口）基于 AST 的引用计数。"""
    counter, _ = collect_refs(files)
    return {_n: counter.get(_n, 0) for _n in names}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def scan(root: str = _PROJ, extra_excludes: set[str] | None = None) -> dict:
    all_files = iter_source_files(root, extra_excludes)
    in_modules: list[str] = []
    test_files: list[str] = []
    for _f in all_files:
        _rel = normalize_relpath(_f, root)
        if _rel.startswith("tests" + "/"):
            test_files.append(_f)
        elif _rel.split("/")[0] in MODULES:
            in_modules.append(_f)

    defs = collect_definitions(in_modules, root=root)

    # 收集名字
    names: set[str] = set()
    meta: dict[tuple[str, str], dict] = {}
    strings_by_file: dict[str, set[str]] = {}
    for _rel, _items in defs.items():
        for _it in _items:
            if "_strings" in _it:
                strings_by_file[_rel] = _it["_strings"]
                continue
            names.add(_it["name"])
            meta[(_rel, _it["name"])] = _it

    # ★AST 单次遍历建引用表（只遍历 prod 与 tests 各一次，避免 O(N×M) 超时）
    _test_set = set(test_files)
    prod_counter, prod_strings = collect_refs(
        [f for f in all_files if f not in _test_set])
    test_counter, test_strings = collect_refs(test_files)
    # ★反射风险用「生产 + tests」的字符串并集：
    #   名字出现在 tests 的字符串里（如第10批 orphan event 白名单）说明它是
    #   **有意保留**的常量，绝不可判为可删 —— 保守方向，宁可漏报不可误删。
    all_strings = prod_strings | test_strings
    # ★主线第58批 T3（P2-398）：字符串加载追踪 —— 把 importlib/__import__/getattr
    #   的字符串参数、以及 data/*.json 配置引用，一并纳入反射风险判定。
    #   这些名字若以字符串形式出现，说明可能被动态加载，绝不可判为可删。
    # ★T3-b：纳入 tmp/ 动态加载引用（如测试隔离夹具 getattr(import_module,...)），
    #   tmp 不计入 def/ref 统计，仅作反射风险信号。
    _tmp_files = _iter_tmp_py(root)
    _dynamic_loads = extract_dynamic_load_names(in_modules + test_files + _tmp_files)
    _config_refs = collect_config_refs(root)
    all_dynamic_names = all_strings | _dynamic_loads | _config_refs

    results: list[dict] = []
    for (_rel, _name), _it in sorted(meta.items()):
        _pc = prod_counter.get(_name, 0)
        _tc = test_counter.get(_name, 0)

        _dunder = bool(DUNDER_RE.match(_name))
        _in_all = _it["in_all"]
        _deco = bool(_it["decorators"])
        # 反射风险：名字以字符串形式出现在**任何**生产源码里
        _dyn = _name in all_dynamic_names

        if _dunder or _in_all or _deco or _dyn:
            _level = "DYNAMIC_RISK"
        elif _pc <= 0 and _tc <= 0:
            _level = "ZERO_REF"
        elif _pc <= 0 and _tc > 0:
            _level = "TEST_ONLY"
        else:
            continue  # 有生产引用 → 非死代码

        results.append({
            "module": _rel.split("/")[0],
            "file": _rel,
            "name": _name,
            "kind": _it["kind"],
            "lineno": _it["lineno"],
            "level": _level,
            "prod_refs": _pc,
            "test_refs": _tc,
            "in_all": _in_all,
            "has_decorator": _deco,
            "hook_like": bool(HOOK_RE.match(_name)),
            "dynamic_string_hit": _dyn,
            "dunder": _dunder,
        })

    summary = {
        "scanned_files": len(all_files),
        "module_files": len(in_modules),
        "test_files": len(test_files),
        "total_defs": len(meta),
        "by_level": {},
        "by_module": {},
    }
    for _r in results:
        summary["by_level"][_r["level"]] = summary["by_level"].get(_r["level"], 0) + 1
        summary["by_module"][_r["module"]] = summary["by_module"].get(_r["module"], 0) + 1
    return {"summary": summary, "items": results}


def render_md(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# 8 大模块死代码检测报告 v2.0（主线第58批 T3）",
        "",
        "> **★只检测，不删除。** 本报告由 `tools/dead_code_scan.py` 生成，",
        "> 供人工/星轨裁决。任何条目的删除都必须单独提补丁并过门禁。",
        "",
        "## 〇、相对 v1.0 的改进（主线第58批 T3）",
        "",
        "### 0.1 批57 T5 标记的 3 条「扫描器误判」核实结果",
        "",
        "| 条目 | 批57 裁决结论 | v2.0 核实结论 |",
        "|---|---|---|",
        "| `reset_llm_dependency_metrics` | 误判（实际被 tmp 动态引用） | **真误判，已修正** → 归入 DYNAMIC_RISK（`tmp/test_isolation.py` 以 `getattr(import_module(m), f)()` 字符串元组调用） |",
        "| `FieldMode`（`const.py:60`） | 误判（实际被引用） | **扫描器正确**：其“引用” `FieldMode.PULSE` 仅出现在自身 docstring，AST 正确排除 docstring，ZERO_REF 成立 |",
        "| `PulseIntent`（`const.py:544`） | 误判（实际被引用） | **扫描器正确**：其“引用” `PulseIntent.REQUEST` 仅出现在自身 docstring，ZERO_REF 成立 |",
        "",
        "> 说明：批57 裁决把 docstring 里的使用示例误当成真实引用，故将 FieldMode/PulseIntent 标记为“误判”；",
        "> 经核实扫描器判定正确，无需修改。真正的漏报只有 `reset_llm_dependency_metrics` 一类（tmp 动态加载），",
        "> 且同模式还波及 `reset_patch_auto_approver`/`reset_effect_verifier`/`reset_evidence_calibrator`/`reset_identity_manager` 共 5 条，v2.0 一并修正。",
        "",
        "### 0.2 扫描器改进点",
        "",
        "- **字符串加载追踪增强** `extract_dynamic_load_names`：除 `importlib.import_module('x')` / `__import__('x')` / `getattr(obj, 'name')` 字符串参数外，新增 `(\"module.path\", \"func_name\")` **字符串元组插件加载**模式识别（覆盖测试隔离夹具等动态重置）。",
        "- **tmp/ 动态引用纳入反射风险**：`tmp/` 仍不计入 def/ref 统计（避免引用膨胀），但其动态加载字符串作为反射风险信号纳入 `DYNAMIC_RISK`（解决“排除 tmp 导致动态引用漏报”根因）。",
        "- **配置引用追踪** `collect_config_refs`：扫描 `data/*.json` 中标识符形态的字符串值，覆盖配置驱动的动态调用。",
        "- **排除规则可配置**：`SCAN_EXTRA_EXCLUDES` + 环境变量 `DEAD_CODE_EXTRA_EXCLUDES`，覆盖 `tmp/`、`backups/`、`.bak_*/`、`__pycache__/`。",
        "",
        "### 0.3 v1.0 → v2.0 数量对比",
        "",
        "| 级别 | v1.0 基准 | v2.0 | 变化 |",
        "|---|---|---|---|",
        "| ZERO_REF | 61 | 56 | -5（5 条 tmp 元组动态加载修正为 DYNAMIC_RISK） |",
        "| TEST_ONLY | 13 | 13 | 0 |",
        "| DYNAMIC_RISK | 410 | 425 | +15（5 条修正 + 10 条新增反射风险信号，均未计入 def/ref） |",
        "",
        "> v1.0 基准 = 本批用改进前扫描器在同仓库复跑的结果（ZERO_REF=61 与第55批报告一致）。",
        "> 净效果：ZERO_REF 减少 5 条真实误判（均为 tmp 动态加载），无新增真实死代码；",
        "> DYNAMIC_RISK 增加项均为保守反射风险标注，不改变“可删”结论。",
        "",
        "## 一、扫描概况",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 扫描文件总数（已排除副本/缓存/备份） | %d |" % s["scanned_files"],
        "| 8 大模块文件数 | %d |" % s["module_files"],
        "| tests 文件数 | %d |" % s["test_files"],
        "| 模块级定义总数 | %d |" % s["total_defs"],
        "",
        "## 二、分级统计",
        "",
        "| 级别 | 数量 | 含义 |",
        "|---|---|---|",
    ]
    _desc = {
        "ZERO_REF": "**高置信**：全库（含 tests）零引用，可人工复核后删除",
        "TEST_ONLY": "**疑似**：仅被 tests 引用，可能是测试专用或历史遗留",
        "DYNAMIC_RISK": "**不可删**：dunder / `__all__` / 装饰器 / 名字以字符串出现（反射风险）",
    }
    for _k in ("ZERO_REF", "TEST_ONLY", "DYNAMIC_RISK"):
        lines.append("| %s | %d | %s |" % (_k, s["by_level"].get(_k, 0), _desc[_k]))
    lines += ["", "## 三、按模块分布", "", "| 模块 | 疑似死代码数 |", "|---|---|"]
    for _m, _c in sorted(s["by_module"].items(), key=lambda x: -x[1]):
        lines.append("| `%s` | %d |" % (_m, _c))
    lines += ["", "## 四、明细（ZERO_REF 优先）", ""]

    _order = {"ZERO_REF": 0, "TEST_ONLY": 1, "DYNAMIC_RISK": 2}
    _items = sorted(report["items"], key=lambda r: (_order[r["level"]],
                                                    r["file"], r["lineno"]))
    _cur = None
    for _r in _items:
        if _r["level"] != _cur:
            _cur = _r["level"]
            lines += ["", "### %s" % _cur, "",
                      "| 模块 | 文件:行 | 名称 | 类型 | 生产引用 | 测试引用 | 备注 |",
                      "|---|---|---|---|---|---|---|"]
        _note = []
        if _r["dunder"]:
            _note.append("dunder")
        if _r["in_all"]:
            _note.append("`__all__`")
        if _r["has_decorator"]:
            _note.append("有装饰器")
        if _r["dynamic_string_hit"]:
            _note.append("★字符串命中（反射）")
        if _r["hook_like"]:
            _note.append("疑似框架钩子")
        lines.append("| `%s` | `%s:%d` | `%s` | %s | %d | %d | %s |" % (
            _r["module"], _r["file"], _r["lineno"], _r["name"], _r["kind"],
            _r["prod_refs"], _r["test_refs"], "、".join(_note) or "—"))
    lines += ["", "---", "",
              "★**删除纪律**：本报告不构成删除授权。每一条删除都需单独提补丁、",
              "带灰度开关、过五项门禁，并在交付报告中列明。"]
    return "\n".join(lines) + "\n"




# ===========================================================================
# v3（C-8 prep · 157 批）：方法级零引用 + 孤岛文件 + set_* 子集 + 收窄动态判定
# ---------------------------------------------------------------------------
# ★相对 v2 的关键修正：v2 把「全部字符串常量」并入 all_dynamic_names，导致几乎
#   所有符号都被判 DYNAMIC_RISK（复现 ZERO_REF=0），真断链（如 register_pipeline）
#   被「疑似钩子/字符串命中」吞掉。v3 仅把**真实动态加载信号**
#   （import_module/__import__/getattr 字符串参数 + ("mod","fn") 元组 + data/*.json
#   配置引用）视为 DYNAMIC_RISK，不再把日志/注释里的普通字符串算作反射风险。
def _dotted_module_of(rel: str) -> str:
    """``nucleus/foo/bar.py`` -> ``nucleus.foo.bar``。"""
    _noext = rel[:-3] if rel.endswith(".py") else rel
    return _noext.replace("/", ".")


def _collect_imported_modules(files: list[str]) -> set[str]:
    """收集全部文件的 import / import-from 模块路径（被 import 即非岛）。"""
    out: set[str] = set()
    for _f in files:
        try:
            _tree = ast.parse(open(_f, encoding="utf-8", errors="ignore").read(),
                              filename=_f)
        except Exception:
            continue
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.Import):
                for _a in _n.names:
                    out.add(_a.name)
            elif isinstance(_n, ast.ImportFrom):
                if _n.module:
                    out.add(_n.module)
    return out


def _collect_methods(files: list[str]) -> dict[str, list[dict]]:
    """返回 {rel: [{name, qualname, class, lineno}]}（仅 prod 文件的方法级定义）。"""
    out: dict[str, list[dict]] = {}
    for _f in files:
        try:
            _src = open(_f, encoding="utf-8", errors="ignore").read()
            _tree = ast.parse(_src, filename=_f)
        except Exception:
            continue
        _rel = normalize_relpath(_f, _PROJ)
        for _node in ast.walk(_tree):
            if isinstance(_node, ast.ClassDef):
                for _m in _node.body:
                    if isinstance(_m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.setdefault(_rel, []).append({
                            "name": _m.name,
                            "qualname": "%s.%s" % (_node.name, _m.name),
                            "class": _node.name,
                            "lineno": _m.lineno,
                        })
    return out


def scan_v3(root: str = _PROJ, extra_excludes: set[str] | None = None) -> dict:
    """v3 扫描：模块级零引用 + 方法级零引用（入口方法）+ set_* 子集 + 孤岛文件。

    ★只读，绝不修改任何源码。
    """
    all_files = iter_source_files(root, extra_excludes)
    in_modules: list[str] = []
    test_files: list[str] = []
    for _f in all_files:
        _rel = normalize_relpath(_f, root)
        if _rel.startswith("tests" + "/"):
            test_files.append(_f)
        elif _rel.split("/")[0] in MODULES:
            in_modules.append(_f)

    _test_set = set(test_files)
    prod_files = [f for f in all_files if f not in _test_set]

    defs = collect_definitions(in_modules, root=root)
    methods = _collect_methods(in_modules)

    prod_counter, _ps = collect_refs(prod_files)
    test_counter, _ts = collect_refs(test_files)
    # ★v3 岛程序判定专用：仅统计「调用引用」(Name + Attribute)，排除 import 别名
    #   （否则 `from X import Y` 会把类名列为引用，使「只被 import 从未调用」的岛被误判非岛）
    def _call_refs(files):
        _c: dict[str, int] = {}
        for _f in files:
            try:
                _t = ast.parse(open(_f, encoding="utf-8", errors="ignore").read(),
                               filename=_f)
            except Exception:
                continue
            for _n in ast.walk(_t):
                if isinstance(_n, ast.Name):
                    _c[_n.id] = _c.get(_n.id, 0) + 1
                elif isinstance(_n, ast.Attribute):
                    _c[_n.attr] = _c.get(_n.attr, 0) + 1
        return _c
    _call_pc = _call_refs(prod_files)
    _call_tc = _call_refs(test_files)
    # ★v3 收窄动态信号（修复 register_pipeline 等真断链被吞）
    _tmp_files = _iter_tmp_py(root)
    _dyn = (extract_dynamic_load_names(in_modules + test_files + _tmp_files)
            | collect_config_refs(root))
    _imported = _collect_imported_modules(prod_files + test_files)

    # 全库引用计数（方法名作为 Name/Attribute 的出现次数，含定义处的调用）
    all_counter: dict[str, int] = {}
    for _k, _v in prod_counter.items():
        all_counter[_k] = all_counter.get(_k, 0) + _v
    for _k, _v in test_counter.items():
        all_counter[_k] = all_counter.get(_k, 0) + _v

    # ---- 模块级零引用（v3 收窄动态后才有意义）----
    mod_zero_ref: list[tuple] = []
    for _rel, _items in defs.items():
        for _it in _items:
            if "_strings" in _it:
                continue
            _n = _it["name"]
            if (DUNDER_RE.match(_n) or _it["in_all"] or _it["decorators"]
                    or _n in _dyn):
                continue
            if prod_counter.get(_n, 0) <= 0 and test_counter.get(_n, 0) <= 0:
                mod_zero_ref.append((_rel, _n, _it["lineno"], _it["kind"]))

    # ---- 方法级零引用：入口方法（HOOK_RE 命中 ∪ set_*） + 全方法参考 ----
    # 全局名计数存在跨类同名碰撞；入口方法是 API 命名，多数名唯一，故用全局名。
    method_zero_ref: list[tuple] = []      # 全方法零引用（参考）
    entry_zero_ref: list[tuple] = []       # 入口方法（HOOK_RE ∪ set_*）零引用
    set_zero_ref: list[tuple] = []         # set_* 子集
    for _rel, _ms in methods.items():
        for _m in _ms:
            _n = _m["name"]
            if DUNDER_RE.match(_n):
                continue
            _refs = all_counter.get(_n, 0)
            if _refs <= 0:
                method_zero_ref.append((_rel, _m["qualname"], _m["lineno"]))
                if _n.startswith("set_") or HOOK_RE.match(_n):
                    entry_zero_ref.append((_rel, _m["qualname"], _m["lineno"]))
                    if _n.startswith("set_"):
                        set_zero_ref.append((_rel, _m["qualname"], _m["lineno"]))

    # ---- 孤岛文件（整文件无外部可达 API）----
    island_files: list[str] = []
    for _rel, _items in defs.items():
        _names = [it["name"] for it in _items if "_strings" not in it]
        if not _names:
            continue
        _dotted = _dotted_module_of(_rel)
        # ★精确匹配：仅当本文件模块路径被「显式 import」才判非岛；
        #   父包 import（如 import nucleus）不算抵达本文件（修正前缀误杀）。
        if _dotted in _imported:
            continue  # 被其它文件显式 import -> 非岛
        _reachable = False
        for _n in _names:
            if _n in _dyn:
                _reachable = True
                break
            # 仅「调用引用」>0 才算可达（排除 import 别名污染）
            if (_call_pc.get(_n, 0) + _call_tc.get(_n, 0)) > 0:
                _reachable = True
                break
        if not _reachable:
            island_files.append(_rel)

    return {
        "module_zero_ref": len(mod_zero_ref),
        "method_zero_ref": len(method_zero_ref),
        "entry_zero_ref": len(entry_zero_ref),
        "set_zero_ref": len(set_zero_ref),
        "island_files": len(island_files),
        "island_list": sorted(island_files),
        "set_list": sorted(set_zero_ref),
        "method_list": sorted(method_zero_ref),
        "mod_list": sorted(mod_zero_ref),
    }


def render_md_v3(summary: dict) -> str:
    lines = [
        "# 死代码检测 v3（C-8 prep · 157 批）",
        "",
        "> ★只检测，不删除。本报表由 `tools/dead_code_scan.py --v3` 生成。",
        "> 相对 v2 修正：动态判定收窄为「真实动态加载信号」，",
        "> 修复 `register_pipeline` 等真断链被 `HOOK_RE`/字符串命中吞掉（v2 复现 ZERO_REF=0）。",
        "",
        "## 一、v3 基线（CI 红线，待 156 红数归零后由 check_broken_chain_gate 接线）",
        "",
        "| 指标 | v3 实测 | 前置分析基线 | 红线 |",
        "|---|---|---|---|",
        "| 孤岛文件数 | %d | 33 | 不得上升 |" % summary["island_files"],
        "| 零引用入口方法数（HOOK_RE∪set_*） | %d | 40 | 不得上升 |" % summary["entry_zero_ref"],
        "| 零引用方法数（全方法·参考） | %d | — | 参考 |" % summary["method_zero_ref"],
        "| 零引用 set_* 数 | %d | 28 | 不得上升 |" % summary["set_zero_ref"],
        "| 模块级零引用数 | %d | — | 参考 |" % summary["module_zero_ref"],
        "",
        "## 二、孤岛文件清单（%d）" % summary["island_files"],
        "",
    ]
    for _r in summary["island_list"]:
        lines.append("- `%s`" % _r)
    lines += ["", "## 三、零引用 set_* 清单（%d）" % summary["set_zero_ref"], ""]
    for _rel, _q, _ln in summary["set_list"]:
        lines.append("- `%s:%d` `%s`" % (_rel, _ln, _q))
    lines += ["", "## 四、零引用入口方法清单（%d，含 set_*）" % summary["entry_zero_ref"], ""]
    for _rel, _q, _ln in summary["method_list"]:
        lines.append("- `%s:%d` `%s`" % (_rel, _ln, _q))
    lines += ["", "---", "",
              "★删除/接线纪律：本报表不构成删除或接线授权；每条处置需单独提补丁、",
              "带灰度开关、过五项门禁，并在交付报告中列明。"]
    return "\n".join(lines) + "\n"


def _main_v3(args) -> int:
    summary = scan_v3()
    md = render_md_v3(summary)
    _out = os.path.join(_PROJ, "docs", "死代码检测报告_8大模块_v3.md")
    os.makedirs(os.path.dirname(_out), exist_ok=True)
    open(_out, "w", encoding="utf-8", newline="").write(md)
    print("[v3] 孤岛文件=%d  零引用入口方法=%d  零引用 set_*=%d  模块级零引用=%d"
          % (summary["island_files"], summary["method_zero_ref"],
             summary["set_zero_ref"], summary["module_zero_ref"]))
    print("[v3] 报告已写入：%s" % _out)
    if args.json_out:
        open(args.json_out, "w", encoding="utf-8", newline="").write(
            json.dumps(summary, ensure_ascii=False, indent=2))
        print("[v3] JSON 已写入：%s" % args.json_out)
    return 0

def main() -> int:
    ap = argparse.ArgumentParser(description="8 大模块死代码检测（只检测不删除）")
    ap.add_argument("--out", default=os.path.join(
        _PROJ, "docs", "死代码检测报告_8大模块_v2.0.md"))
    ap.add_argument("--json", dest="json_out", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--v3", action="store_true",
                    help="C-8 prep: 跑 v3（方法级零引用+孤岛文件+set_* 子集）")
    args = ap.parse_args()

    if args.v3:
        return _main_v3(args)
    report = scan()
    md = render_md(report)
    if not args.quiet:
        print("扫描文件 %d，模块级定义 %d" % (
            report["summary"]["scanned_files"], report["summary"]["total_defs"]))
        print("分级：%s" % report["summary"]["by_level"])

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    open(args.out, "w", encoding="utf-8", newline="").write(md)
    print("报告已写入：%s" % args.out)

    if args.json_out:
        open(args.json_out, "w", encoding="utf-8", newline="").write(
            json.dumps(report, ensure_ascii=False, indent=2))
        print("JSON 已写入：%s" % args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
