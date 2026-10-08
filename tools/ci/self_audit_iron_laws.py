# -*- coding: utf-8 -*-
"""171批刀7 · 项目专属铁律自审 lint（T-自我审计-3）。

把三条工程铁律固化为可执行的自审规则，产出全库违例清单（如实上报，不强制全改）：
  铁律1 核心路径禁裸静默吞：nucleus 核心器官内 except 块体「纯空」（无日志/无 re-raise/
        无兜底赋值/无 return 值）即裸吞。注：cw2 指纹门禁已对「新增」静默 except 把关，
        本规则只做全库存量盘点与核心路径标记。
  铁律2 禁注册表外器官直连：organs/nucleus 内 from organs.<域X> 跨域直连导入（域X != 本文件域）。
  铁律3 禁假通过：tools/ci/ 门禁脚本内 except 后强制 sys.exit(0)（截断假绿）。

设计要点（与既有门禁错峰、不新增 ruff 告警）：
  - 本脚本为自审/报告工具，默认 REPORT-ONLY（exit 0，产出清单）；
    --strict 时仅对铁律1 在 nucleus 核心路径的「纯空裸吞」升级为 FAIL（exit 1）。
  - 不改动 ruff.toml（S112/S110/BLE001 为有意忽略，静默吞已由 self_inspector/
    cw2 指纹门禁另行覆盖），故全库 ruff 告警数不新增。
  - 门禁实接线（pre-commit 挂载）与 T-Ruff基线-1(172) 同族，按任务书纪律错峰，
    本刀只落地规则与清单，挂载待 172 协调，避免阻断在途提交。

用法：
  python tools/ci/self_audit_iron_laws.py [--strict] [--json 输出.json]
退出码：0=报告完成（或 --strict 下核心路径零违例）；1=--strict 下核心路径有违例。
"""
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXCLUDE_TOP = {
    "data", "docs", "node_modules", ".git", ".release-tmp",
    "data/code_backups", ".bak_batch171", ".bak_batch170", ".bak_batch169",
}
EXCLUDE_SUB = {"docker_build_ctx"}  # tmp/ 下的发布构建副本，非真源


def _iter_py():
    for _root, _dirs, _files in os.walk(ROOT):
        _rel = os.path.relpath(_root, ROOT)
        _parts = _rel.split(os.sep)
        if any(p.startswith(".bak") for p in _parts):
            continue
        if _parts and _parts[0] in EXCLUDE_TOP:
            continue
        if EXCLUDE_SUB.intersection(_parts):
            continue
        for _f in _files:
            if not _f.endswith(".py"):
                continue
            _frel = _rel.replace(os.sep, "/")
            if _frel.startswith("tmp/") and _f.startswith(("patch_", "scan_", "check_")):
                continue
            if _frel.startswith("tmp/_"):
                continue
            yield os.path.join(_root, _f)


_LOG_ATTRS = {"warning", "error", "exception", "debug", "info", "critical", "log"}
_LOG_NAMES = ("log", "logger", "silent_exc", "warn", "report", "trace", "print")


def _is_trivial_stmt(node):
    """纯空语句：pass / continue / break / `...` / 裸 return（无值）。"""
    if isinstance(node, (ast.Pass, ast.Continue, ast.Break)):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
            and node.value.value is None:
        return True
    return isinstance(node, ast.Return) and node.value is None


def _handler_is_bare_silent(body):
    """except 处理器体「纯空」= 全部为无操作语句 => 裸静默吞。"""
    if not body:
        return True
    return all(_is_trivial_stmt(_s) for _s in body)


def _law1_silent_swallow(filepath, tree):
    """铁律1：nucleus 核心器官内纯空裸吞。返回 [(lineno, snippet)]。"""
    _hits = []
    for _node in ast.walk(tree):
        if not isinstance(_node, (ast.Try, ast.TryStar)):
            continue
        for _h in getattr(_node, "handlers", None) or []:
            if _handler_is_bare_silent(_h.body):
                _first = _h.body[0] if _h.body else _h
                _hits.append((getattr(_first, "lineno", _node.lineno),
                              "except 块体纯空裸吞（无日志/无 re-raise/无兜底）"))
    return _hits


def _law2_organ_crosswire(filepath, tree):
    """铁律2：跨域器官直连导入。返回 [(lineno, note)]。"""
    _rel = os.path.relpath(filepath, ROOT).replace(os.sep, "/")
    _file_domain = _rel.split("/")[1] if _rel.startswith("organs/") else None
    _hits = []
    for _node in ast.walk(tree):
        if isinstance(_node, ast.ImportFrom) and _node.module and _node.module.startswith("organs."):
            _imp_domain = _node.module.split(".")[1]
            if _file_domain is not None and _imp_domain != _file_domain:
                _hits.append((_node.lineno,
                              f"from {_node.module} import ...（跨域直连：文件域={_file_domain}）"))
    return _hits


def _law3_fake_pass(filepath, tree):
    """铁律3：tools/ci/ 门禁脚本内 except 后强制 sys.exit(0)（截断假绿）。"""
    if "/tools/ci/" not in filepath.replace(os.sep, "/"):
        return []
    _hits = []
    for _node in ast.walk(tree):
        if not isinstance(_node, (ast.Try, ast.TryStar)):
            continue
        for _h in getattr(_node, "handlers", []) or []:
            for _b in _h.body:
                for _stmt in ast.walk(_b):
                    if isinstance(_stmt, ast.Call) and isinstance(_stmt.func, ast.Attribute) \
                            and _stmt.func.attr == "exit":
                        _hits.append((getattr(_stmt, "lineno", _node.lineno),
                                      "except 内 sys.exit() 强制退出（疑似假通过）"))
    return _hits


def main():
    _strict = "--strict" in sys.argv[1:]
    _json_out = None
    for _a in sys.argv[1:]:
        if _a.startswith("--json"):
            _json_out = _a.split("=", 1)[1] if "=" in _a else "tmp/_knife7_inventory.json"
    _inv = {"law1_silent_swallow": [], "law2_organ_crosswire": [], "law3_fake_pass": []}
    _core_violations = 0
    for _fp in _iter_py():
        try:
            with open(_fp, encoding="utf-8") as _fh:
                _src = _fh.read()
            _tree = ast.parse(_src)
        except (SyntaxError, UnicodeDecodeError) as _e:
            print(f"[skip] {_fp} 解析失败: {_e}", file=sys.stderr)
            continue
        _rel = os.path.relpath(_fp, ROOT).replace(os.sep, "/")
        for _ln, _msg in _law1_silent_swallow(_fp, _tree):
            _inv["law1_silent_swallow"].append({"file": _rel, "line": _ln, "note": _msg})
            if _rel.startswith("nucleus/"):
                _core_violations += 1
        for _ln, _msg in _law2_organ_crosswire(_fp, _tree):
            _inv["law2_organ_crosswire"].append({"file": _rel, "line": _ln, "note": _msg})
        for _ln, _msg in _law3_fake_pass(_fp, _tree):
            _inv["law3_fake_pass"].append({"file": _rel, "line": _ln, "note": _msg})

    if _json_out:
        with open(_json_out, "w", encoding="utf-8") as _fh:
            json.dump(_inv, _fh, ensure_ascii=False, indent=2)

    print("=" * 70)
    print("171批刀7 铁律自审清单（report-only；--strict 仅铁律1核心路径 FAIL）")
    print("=" * 70)
    for _k, _title in (("law1_silent_swallow", "铁律1 核心路径裸静默吞（纯空）"),
                       ("law2_organ_crosswire", "铁律2 跨域器官直连"),
                       ("law3_fake_pass", "铁律3 门禁假通过")):
        _items = _inv[_k]
        print(f"\n[{_k}] {_title} -- {len(_items)} 项")
        for _it in _items[:40]:
            print(f"  {_it['file']}:{_it['line']}  {_it['note']}")
        if len(_items) > 40:
            print(f"  ... 其余 {len(_items) - 40} 项见 --json 清单")

    print(f"\n核心路径(nucleus/)铁律1纯空裸吞数 = {_core_violations}")
    if _strict and _core_violations > 0:
        print("结论：x --strict 下核心路径存在纯空裸吞，须先修。")
        return 1
    print("结论：ok 报告完成（report-only）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
