#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_production_data_marks.py —— `production_data` 标记的「实测口径」（★375 刀6）

## 为什么需要这个件

`tests/conftest.py` 的 `pytest_collection_modifyitems()` 在框架运行时，
会**跳过**所有带 `pytest.mark.production_data` 的测试。标记的语义是
「该测试读生产 `data/` 的现状」，可**标记本身没有任何判据** ——
加不加全靠人工判断，于是出现两种错配：

  过度标记：测试根本不碰生产 `data/`，框架运行时却被误跳过（白丢覆盖）；
  漏标记　：测试真读生产 `data/`，框架运行时照跑，读到被活体实时改写的
           现状 ⇒ 必然假失败（第88批 m47 就是这么红的）。

本件把「触不触生产 data/」变成**可复跑的静态判据**，双向都报。

## 判据（375 刀6 定稿，四处坑都在实测里踩过）

判定「路径指向生产数据区」需**同时**满足：

  ① 路径链首段是 `data`
     —— 不是「出现过 data 字样」。`nucleus/data/exclude_dirs.py` 是**代码目录**，
        首段是 nucleus ⇒ 不算；`json.dumps({"data": {...}})` 里的 data 是 payload
        键，不是路径 ⇒ 不算。首段判据按**书写顺序**取字面量（ast.walk 是 BFS，
        顺序不对会导致 `join(_ROOT,'nucleus','data','x')` 被误判）。
  ② 基准类别是 PROD
     PROD   : 项目根名（ROOT/_ROOT/PROJECT_ROOT…）、相对字面量 `"data/..."`、
              或值本身指向 data 区的常量（`POOL = join(_ROOT,"data",...)`）
     TMP    : `tmp_path`/`tmpdir`/`tempfile.mkdtemp()`/`self._td`/`*_tmp`/sandbox
              —— 实测 76% 的「疑似写 data/」都是这类**副本**，不是生产
     UNKNOWN: 基准是函数调用结果或未知变量 —— **不计入**生产面（宁可漏报）

  基准穿透：`str(tmp_path)`、`os.path.abspath(x)`、`Path(x).resolve()` 等
  不改变「基准是谁」，一律穿透到最内层再判（不穿透会把 tmp 副本算成生产）。

## 写方向

只认显式写类：`open(mode 含 w/a/x/+)`、`write_text/write_bytes`、
`shutil.copy*/copytree/move` 的目标、`os.replace/remove/rename/mkdir/makedirs`、
`json.dump`（**不是 dumps**）、`save_json/atomic_write/guard_write/_atomic_save`。
名字带 save/record/persist/archive/prune/delete/write/cleanup 的自定义函数按写算，
但 `is_*/has_*/check_*/detect*/verify*` 一律不算（`_in_backup("data/x")`
这种纯字符串判定被误算过一次）。

## 用法

    python tools/ci/check_production_data_marks.py              # 只报
    python tools/ci/check_production_data_marks.py --strict      # 过度标记非空 ⇒ 退出 1
    python tools/ci/check_production_data_marks.py --json out.json

★ 本件**只读**：不做任何写盘动作（除 `--json` 显式指定输出件）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

SCOPE = (
    "静态 AST 判据：路径链首段==data 且基准∈{项目根, 相对字面量, data 常量} 才算指向生产数据区；"
    "基准为 tmp_path/tempfile/*_td 判 TMP；基准为函数调用结果判 UNKNOWN 且**不计入**生产面；"
    "只认显式写类调用（不含 json.dumps、不含 is_*/check_* 判定函数）；"
    "reject_write/guard_write 单列 via_guard（测守卫≠真写，测试类环境守卫一律拒写）；"
    "扫描面=tests/test_*.py；标记判定=装饰器精确匹配 pytest.mark.production_data"
)

ROOT_NAMES = frozenset({
    "ROOT", "PROJECT_ROOT", "BASE_DIR", "PROJ_ROOT", "_ROOT", "REPO_ROOT",
    "HERE", "PROJECT_DIR", "_BASE", "BASE", "_PROJ",
})
TMP_FIXTURES = frozenset({"tmp_path", "tmpdir", "tmp_path_factory", "tmpdir_factory"})
TMP_CALLS = frozenset({
    "mkdtemp", "mkstemp", "TemporaryDirectory", "NamedTemporaryFile", "TemporaryFile",
})
TMP_NAME = re.compile(r"(^_?(tmp|temp|td)\b|_td$|_tmp$|^tmp_|sandbox|scratch)", re.IGNORECASE)
# 基准穿透：这些调用不改变「基准是谁」
PASS_THROUGH = frozenset({
    "abspath", "realpath", "normpath", "expanduser", "resolve", "dirname",
    "str", "repr", "fspath", "format",
})
IO_ATTRS = frozenset({
    "read_text", "read_bytes", "listdir", "exists", "isfile", "isdir", "glob",
    "rglob", "stat", "iterdir", "scandir", "walk", "getsize", "getmtime", "load",
})
EXIST_ONLY = frozenset({"exists", "isfile", "isdir", "stat", "getsize", "getmtime"})
EXPLICIT_WRITE = frozenset({
    "write_text", "write_bytes", "touch", "truncate", "mkdir", "makedirs",
    "remove", "unlink", "rmtree", "rename", "replace", "copy", "copy2",
    "copyfile", "copyfileobj", "copytree", "move", "dump", "write_json",
    "write_csv", "save_json", "guard_write", "_atomic_save", "atomic_write",
})
WRITE_HINT = re.compile(r"(save|record|persist|archive|prune|delete|write|cleanup)", re.IGNORECASE)
READONLY_HINT = re.compile(
    r"^(is_|has_|_is_|_has_|check_|_check_|detect|assert|verify|probe|find|get|_in_|in_)",
    re.IGNORECASE)
PD_MARK = re.compile(r"\bpytest\.mark\.production_data\b")
# 守卫自身的写口：调用它们**不等于**测试真写（测试类环境下守一律拒写）
GUARD_WRITE = ("reject_write", "guard_write")
_LIT_RE = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"")


# ---------------------------------------------------------------- 路径判定
def _lit_segs(node: ast.AST) -> list[str]:
    """按**书写顺序**取表达式里的字符串字面量，拼接后切段。"""
    _parts = []
    for _m in _LIT_RE.finditer(ast.unparse(node)):
        _v = _m.group(1) if _m.group(1) is not None else _m.group(2)
        if _v:
            _parts.append(_v)
    if not _parts:
        return []
    _joined = "/".join(_parts).replace("\\", "/").lower()
    return [p for p in _joined.split("/") if p]


def _is_data_lit(s: object) -> bool:
    """整条字面量是否生产数据区路径（首段 data，不含 tmp）。"""
    if not isinstance(s, str) or not s.strip():
        return False
    _s = s.replace("\\", "/").strip().lower()
    if any(t in _s for t in ("tmp", "temp")):
        return False
    _p = [p for p in _s.split("/") if p and p != "."]
    if not _p or _p[0] != "data":
        return False
    return not (_p == ["data"] and "/" not in _s and "\\" not in s)


def _has_data_seg(node: ast.AST) -> bool:
    """路径链的**首段**是 data ⇒ 指向生产数据区（`nucleus/data/` 不算）。"""
    _s = _lit_segs(node)
    return bool(_s) and _s[0] == "data"


def _base_of(node: ast.AST) -> tuple[str, object]:
    """路径表达式的**基准**：join 第一段 / Div 链最左 / 字面量自身。"""
    if isinstance(node, ast.Call):
        _f = getattr(node.func, "attr", None) or getattr(node.func, "id", None) or ""
        if _f in ("join", "Path", "PurePath") and node.args:
            return _base_of(node.args[0])
        if _f in TMP_CALLS:
            return ("TMP", _f)
        if _f in PASS_THROUGH and node.args:
            return _base_of(node.args[0])
        return ("CALL", _f)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _base_of(node.left)
    if isinstance(node, ast.Constant):
        return ("LIT", node.value)
    if isinstance(node, ast.Name):
        return ("NAME", node.id)
    if isinstance(node, ast.Attribute):
        return ("ATTR", node.attr)
    return ("OTHER", type(node).__name__)


def _classify(expr: ast.AST, data_consts: set[str], tmp_names: set[str]) -> str:
    _k, _v = _base_of(expr)
    if _k == "TMP":
        return "TMP"
    if _k == "LIT":
        return "PROD" if _is_data_lit(_v) else "TMP"
    if _k == "NAME":
        if _v in tmp_names or TMP_NAME.search(str(_v)):
            return "TMP"
        if _v in data_consts or _v in ROOT_NAMES:
            return "PROD"
        return "UNKNOWN"
    if _k == "ATTR":
        if _v in tmp_names or TMP_NAME.search(str(_v)):
            return "TMP"
        return "PROD" if _v in data_consts else "UNKNOWN"
    return "UNKNOWN"


def points_to_prod_data(expr, data_consts, tmp_names):
    """唯一判据入口：返回 'PROD' / 'TMP' / 'UNKNOWN' / None（不指向 data 区）。"""
    _nm = getattr(expr, "id", None) or getattr(expr, "attr", None)
    # 「已知是临时副本」要**明确判 TMP**，不能因为「不含 data 字面量」就返回 None：
    # 判成 TMP 才算知道它是副本，返回 None 只是「不知道」——两件事，自检要能区分。
    if _nm and _nm in tmp_names:
        return "TMP"
    if not (_has_data_seg(expr) or (_nm and _nm in data_consts)):
        return None
    return _classify(expr, data_consts, tmp_names)


# ---------------------------------------------------------------- 常量收集
def _iter_skip_nested(stmts):
    """遍历当前层的语句，遇到 def/class **一律不进**（各有自己的收集口）。

    ★入口处也要判（375 刀6 实测）：原栈式实现只在 push 子节点时跳过 ClassDef，
      但模块级调用的初始节点里就含着 ClassDef 本身 ⇒ 它的直接子节点
      （类属性 `POOL = join(_ROOT,"data",...)`）照样被收进模块级常量集 ⇒
      「类级收集」形同虚设，同名属性还会跨类串味。
      语义应当是：模块级只收模块级赋值，类级只收类级赋值，函数级只收函数内赋值。
    """
    for _s in stmts:
        if isinstance(_s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield _s
        for _c in ast.iter_child_nodes(_s):
            if isinstance(_c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            yield from _iter_skip_nested([_c])


def collect_consts(stmts, data_consts: set[str], tmp_names: set[str]) -> None:
    """把「值指向 data 区」的赋值名收进 data_consts；临时基准收进 tmp_names。"""
    for _n in _iter_skip_nested(stmts):
        if not isinstance(_n, (ast.Assign, ast.AnnAssign)):
            continue
        _val = getattr(_n, "value", None)
        if _val is None:
            continue
        _tg = _n.targets if isinstance(_n, ast.Assign) else [_n.target]
        _names = []
        for _t in _tg:
            if isinstance(_t, ast.Name):
                _names.append(_t.id)
            elif isinstance(_t, ast.Attribute):
                _names.append(_t.attr)
        if not _names:
            continue
        _b = _base_of(_val)
        if _b[0] == "TMP" or (_b[0] == "NAME" and (_b[1] in tmp_names or TMP_NAME.search(str(_b[1])))):
            tmp_names.update(_names)
        elif _has_data_seg(_val) and _classify(_val, data_consts, tmp_names) == "PROD":
            # 统一走 _classify（v8 的坑）：`p = join(get_dir(), "data", "x")` 的基准是
            # 函数调用结果 ⇒ UNKNOWN ⇒ **不收**成数据常量，否则后面 `open(p,"w")`
            # 会被当成生产写（实测 test_11 就这么红的）。
            data_consts.update(_names)


# ---------------------------------------------------------------- 写/读目标
def write_targets(call: ast.Call) -> list:
    _f = getattr(call.func, "attr", None) or getattr(call.func, "id", None) or ""
    if _f == "open":
        _m = ""
        for _kw in call.keywords:
            if _kw.arg == "mode" and isinstance(_kw.value, ast.Constant):
                _m = str(_kw.value.value)
        if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
            _m = str(call.args[1].value)
        return [call.args[0]] if (call.args and any(c in _m for c in "wax+")) else []
    if _f in ("write_text", "write_bytes", "touch", "truncate", "mkdir", "makedirs",
              "remove", "unlink", "rmtree"):
        return [call.func.value] if isinstance(call.func, ast.Attribute) else call.args[:1]
    if _f in ("rename", "replace"):
        return call.args[1:2] or call.args[:1]
    if _f in ("copy", "copy2", "copyfile", "copyfileobj", "copytree", "move"):
        return call.args[1:2]
    if _f == "dump":
        # json.dump(obj, fp) 的写目标是**第二个参数**（文件流），不是被序列化的 obj。
        # 原实现取 args[:1] 拿到 dict ⇒ 基准恒 UNKNOWN ⇒ 写 data/ 的 dump 永远漏判。
        _kw = [k.value for k in call.keywords if k.arg == "fp"]
        return _kw or call.args[1:2]
    if _f in EXPLICIT_WRITE or (WRITE_HINT.search(_f) and not READONLY_HINT.search(_f)):
        if _f in ("dumps", "load", "loads"):
            return []  # 内存序列化/反序列化，不产生写目标
        _kw = [k.value for k in call.keywords if k.arg in ("path", "file", "target", "dst")]
        return _kw or call.args[:1]
    return []


def scan_unit(unit, base_data: set[str], base_tmp: set[str]) -> tuple[list, list, list]:
    """扫一个 class 或函数，返回 (写证据, 读证据, 基准分类列表)。

    第三个返回值是**判据的内部读数**（每个命中的目标被判成 PROD/TMP/UNKNOWN），
    供自检用例区分「判成临时副本」与「判不出来」—— 只看 kind 的话两者都是 none，
    去掉基准穿透这类注入就红不了（实测踩过）。
    """
    _data, _tmp = set(base_data), set(base_tmp)
    if isinstance(unit, ast.ClassDef):
        collect_consts(unit.body, _data, _tmp)
        _ms = [m for m in unit.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for _m in _ms:
            collect_consts(_m.body, _data, _tmp)
        _stmts = _ms
    else:
        collect_consts(unit.body, _data, _tmp)
        _stmts = [unit]
    _w, _r, _bases = [], [], []
    for _m in _stmts:
        for _n in ast.walk(_m):
            if not isinstance(_n, ast.Call):
                continue
            for _t in write_targets(_n):
                _k = points_to_prod_data(_t, _data, _tmp)
                if _k:
                    _bases.append(_k)
                if _k == "PROD":
                    _w.append(ast.unparse(_n)[:100])
            _f = getattr(_n.func, "attr", None) or getattr(_n.func, "id", None) or ""
            if _f == "open":
                _m0 = ""
                if len(_n.args) >= 2 and isinstance(_n.args[1], ast.Constant):
                    _m0 = str(_n.args[1].value)
                if _n.args and not any(c in _m0 for c in "wax+"):
                    if points_to_prod_data(_n.args[0], _data, _tmp) == "PROD":
                        _r.append(ast.unparse(_n)[:100])
                continue
            if _f in IO_ATTRS:
                _c = [_n.func.value] if isinstance(_n.func, ast.Attribute) else []
                _c += _n.args[:1]
                _c += [k.value for k in _n.keywords if k.arg in ("path", "file", "dir", "target")]
                if _f == "load" and isinstance(_n.func, ast.Attribute) and _n.args:
                    _c = _n.args[:1]
                for _x in _c:
                    _k = points_to_prod_data(_x, _data, _tmp)
                    if _k:
                        _bases.append(_k)
                    if _k == "PROD":
                        _r.append(ast.unparse(_n)[:100])
    return _w, _r, _bases


def classify_kind(writes: list, reads: list) -> str:
    """把命中分成 write / via_guard / content_read / existence_only。

    `via_guard` 是必须单列的一类（375 刀6 实测）：`wg.reject_write(_PROD)` /
    `wg.guard_write(_PROD)` 是**测守卫本身**——守卫在测试类环境下一律拒写
    （`write_guard.reject_write` 的 `is_test_like_env()` 分支），所以这类用例
    「指向生产 data/」但**写不进去**。把它算成 `write` 会得出
    「测试真写生产数据」的错误结论（实测 3 条全是这种）。
    """
    if writes:
        if all(any(g in _w for g in GUARD_WRITE) for _w in writes):
            return "via_guard"
        return "write"
    for _r in reads:
        _f = _r.split("(")[0].split(".")[-1]
        if _f not in EXIST_ONLY:
            return "content_read"
    return "existence_only"


# ---------------------------------------------------------------- 扫描入口
def analyze_source(src: str, rel: str = "<synthetic>") -> list[dict]:
    """分析一段源码，返回每个 test class/function 的判定结果（供测试件复用）。"""
    try:
        _tree = ast.parse(src)
    except SyntaxError as _e:
        _log("源码不可解析，跳过：%s（%s）" % (rel, _e))
        return []
    _data, _tmp = set(), set(TMP_FIXTURES)
    collect_consts(_tree.body, _data, _tmp)
    _out = []
    for _n in _tree.body:
        if isinstance(_n, ast.ClassDef):
            if not any(isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and m.name.startswith("test") for m in _n.body):
                continue
            _mk = any(PD_MARK.search(ast.unparse(_d)) for _d in _n.decorator_list)
            _qual = _n.name
        elif isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name.startswith("test"):
            _mk = any(PD_MARK.search(ast.unparse(_d)) for _d in _n.decorator_list)
            _qual = _n.name
        else:
            continue
        _w, _r, _bases = scan_unit(_n, _data, _tmp)
        _out.append({
            "file": rel, "qual": _qual, "marked": _mk,
            "writes": _w[:3], "reads": _r[:3], "bases": sorted(set(_bases)),
            "kind": classify_kind(_w, _r) if (_w or _r) else "none",
        })
    return _out


def analyze_tree(tests_dir: str) -> dict:
    _rows = []
    for _fn in sorted(os.listdir(tests_dir)):
        if not (_fn.startswith("test_") and _fn.endswith(".py")):
            continue
        _p = os.path.join(tests_dir, _fn)
        try:
            with open(_p, encoding="utf-8", errors="replace") as _f:
                _src = _f.read()
        except OSError as _e:
            _log("测试件不可读，跳过：%s（%s）" % (_fn, _e))
            continue
        _rows.extend(analyze_source(_src, _fn))
    _marked = [r for r in _rows if r["marked"]]
    _touch = [r for r in _rows if r["kind"] != "none"]
    return {
        "scope": SCOPE,
        "counts": {
            "marked": len(_marked),
            "touch_prod": len(_touch),
            "marked_and_touch": len([r for r in _marked if r["kind"] != "none"]),
            "over_marked": len([r for r in _marked if r["kind"] == "none"]),
            "missing_mark": len([r for r in _touch if not r["marked"]]),
        },
        # 过度标记：标了却完全不碰生产 data/ ⇒ 框架运行时被误跳过（白丢覆盖）
        "over_marked": [r for r in _marked if r["kind"] == "none"],
        # 漏标记：真碰生产 data/ 却没标 ⇒ 框架运行时照跑，读现状必假失败
        "missing_mark": [r for r in _touch if not r["marked"]],
        "marked_ok": [r for r in _marked if r["kind"] != "none"],
    }


def _fmt(row, maxn=62):
    _ev = ((row["writes"] or row["reads"] or [""])[0])[:maxn]
    return f"{row['file']:<44}{row['qual']:<38}{row['kind']:<14}{_ev}"


def _log(msg: str) -> None:
    """CI 件的最小留痕口（cw2 认 LOG_FUNCS 里的 `_log`；不引入 nucleus 依赖）。"""
    print("[ci-375] " + str(msg), file=sys.stderr)

def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="production_data 标记的实测口径（只读）")
    _ap.add_argument("--tests-dir", default=None, help="测试目录，默认 <repo>/tests")
    _ap.add_argument("--json", default=None, help="把结果写成 JSON（显式指定才写）")
    _ap.add_argument("--strict", action="store_true", help="过度标记非空则退出 1")
    _ap.add_argument("--quiet", action="store_true")
    _a = _ap.parse_args(argv)

    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _td = _a.tests_dir or os.path.join(_root, "tests")
    _r = analyze_tree(_td)
    _c = _r["counts"]

    if not _a.quiet:
        print("[375刀6] production_data 标记实测口径")
        print(f"  标记 {_c['marked']} 处 ｜ 真触生产 data/ {_c['touch_prod']} 个 ｜ "
              f"标记且真触 {_c['marked_and_touch']} ｜ 过度标记 {_c['over_marked']} ｜ "
              f"漏标记 {_c['missing_mark']}")
        if _r["over_marked"]:
            print("\n-- 过度标记（标了却完全不碰生产 data/ ⇒ 框架运行时被误跳过）--")
            for _x in _r["over_marked"]:
                print("   " + _fmt(_x))
        if _r["missing_mark"]:
            print("\n-- 漏标记（真碰生产 data/ 却没标 ⇒ 框架运行时照跑，读现状必假失败）--")
            for _x in _r["missing_mark"]:
                print("   " + _fmt(_x))
        print(f"\n口径：{SCOPE}")

    if _a.json:
        with open(_a.json, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_r, _f, ensure_ascii=False, indent=1)
        if not _a.quiet:
            print(f"\n已写出：{_a.json}")

    if _a.strict and _r["over_marked"]:
        print(f"\n[FAIL] 过度标记 {_c['over_marked']} 处：标记会让他们在框架运行时被跳过，"
              "但这些测试根本不碰生产 data/。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
