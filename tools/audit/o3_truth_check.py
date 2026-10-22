# -*- coding: utf-8 -*-
"""判死候选真值独立复核脚本（★344批刀7 · 烛微342 挂账51）。

**为什么必须是独立脚本**：判死候选的真值（"这个符号到底还有没有人用"）
如果由框架内部的扫描器来答，就变成"自己给自己作证"——框架的
`CallGraphAnalyzer` 依赖图版本/落盘时机，图缺了就会把"图里没有"说成
"没人调用"。本脚本只做**源码级 AST 复核**，不读图、不读缓存、不 import
框架任何模块（铁律158：隔离脚本不得把框架带进来，否则取证与生产同源，
取证结论就等于生产结论，失去独立复核的意义）。

输出（固定字段，**同输入同输出**，不含时间戳）::

    {
      "query": {"file": ..., "symbol": ...},
      "definitions": {"count": 1, "items": [{file, line, end_line, kind, cls}]},
      "references":  {"call_attr": 0, "call_name": 0, "attr_read": 0,
                      "text_only": 3, "total_text": 3},
      "verdict": {"no_definition": false, "no_call_site": true,
                  "truth": "定义 1 处 / 调用 0 处"},
      "boundary": {"def_start_line": 123, "def_end_line": 179}
    }

计数口径（必须写清，否则"0 处调用"可以靠数错得到）：
  · `call_attr` = `x.<symbol>(...)` 形式的**真调用**
  · `call_name` = `<symbol>(...)` 形式的**裸名调用**
  · `attr_read` = `x.<symbol>` 取属性但**未调用**（绑定/传参/比较）
  · `text_only` = 文本里出现 `.symbol` 但 AST 里没有对应属性节点
    （注释、文档串、同名局部量）——**只作参考，不计入调用点**
  · `total_text` = 三个 AST 计数 + text_only（即文本出现总次数）

用法::

    python tools/audit/o3_truth_check.py \
        --file nucleus/CompanionBridge.py --symbol handshake \
        --json-out tmp/p344_root/d7_truth_script.json
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import logging
import os
import sys

#: 标准库 logger（铁律158：本脚本不得 import 框架模块，故不用项目 logger）
_logger = logging.getLogger("o3_truth_check")

_SKIP_DIR_PARTS = (".bak_batch", "__pycache__", "docker_build_ctx", ".git",
                   "node_modules", ".venv", "tmp")
_DEFAULT_ROOTS = ("nucleus", "organs", "base", "tools")


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _iter_py(root: str, roots) -> list:
    _out = []
    for _base in roots:
        _d = os.path.join(root, _base)
        if not os.path.isdir(_d):
            continue
        for _dp, _dns, _fns in os.walk(_d):
            _dns[:] = sorted(x for x in _dns
                             if not any(sk in x for sk in _SKIP_DIR_PARTS))
            for _fn in sorted(_fns):
                if _fn.endswith(".py"):
                    _out.append(os.path.join(_dp, _fn))
    return _out


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _parse(path: str):
    """解析文件为 `(源码, AST)`；失败返回 `(None, None)` 并留 debug 痕迹。

    用**标准库 logging**而非项目 logger：本脚本按铁律158 不得 import 框架模块
    （它是审计工具，必须能在干净隔离根里独立跑）。
    """
    try:
        _src = io.open(path, encoding="utf-8", errors="ignore").read()
    except OSError as _e:
        _logger.debug("读取失败 %s: %s: %s", path, type(_e).__name__, _e)
        return None, None
    try:
        return _src, ast.parse(_src)
    except SyntaxError as _e:
        _logger.debug("语法错误 %s: %s", path, _e)
        return None, None


def _enclosing_class(tree, node) -> str:
    for _n in ast.walk(tree):
        if isinstance(_n, ast.ClassDef):
            for _c in ast.iter_child_nodes(_n):
                if _c is node:
                    return _n.name
    return ""


def find_definitions(path: str, symbol: str) -> list:
    """在单个文件里找 def（含 async def）。"""
    _src, _tree = _parse(path)
    if _tree is None:
        return []
    _out = []
    for _n in ast.walk(_tree):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and getattr(_n, "name", "") == symbol:
            _out.append({"line": int(getattr(_n, "lineno", 0) or 0),
                         "end_line": int(getattr(_n, "end_lineno", 0) or 0),
                         "kind": ("async def" if isinstance(_n, ast.AsyncFunctionDef)
                                  else "def"),
                         "cls": _enclosing_class(_tree, _n),
                         "decorators": [ast.unparse(_d) for _d in _n.decorator_list]})
    _out.sort(key=lambda x: x["line"])
    return _out


def scan_references(path: str, symbol: str) -> dict:
    """统计一个文件里对 `symbol` 的引用（AST 分类 + 文本兜底）。"""
    _src, _tree = _parse(path)
    if _tree is None:
        return {"call_attr": 0, "call_name": 0, "attr_read": 0,
                "text_only": 0, "parse_error": True}
    _call_attr = 0
    _call_name = 0
    _attr_nodes = 0
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.Call):
            _f = _n.func
            if isinstance(_f, ast.Attribute) and getattr(_f, "attr", "") == symbol:
                _call_attr += 1
            elif isinstance(_f, ast.Name) and getattr(_f, "id", "") == symbol:
                _call_name += 1
        if isinstance(_n, ast.Attribute) and getattr(_n, "attr", "") == symbol:
            _attr_nodes += 1
    _attr_read = max(0, _attr_nodes - _call_attr)
    _text_total = _src.count("." + symbol)
    return {"call_attr": _call_attr, "call_name": _call_name,
            "attr_read": _attr_read,
            "text_only": max(0, _text_total - _attr_nodes),
            "parse_error": False}


# ===== ★344补 1（脚本侧）：删后新孤儿（第四条件）=====
def owned_member_names(node) -> list:
    """被删 def 体内引用到的成员名（`self.x` / `Cls.x` 的属性名）。"""
    _out, _seen = [], set()
    for _n in ast.walk(node):
        if isinstance(_n, ast.Attribute):
            _a = str(getattr(_n, "attr", "") or "")
            if _a and _a not in _seen:
                _seen.add(_a)
                _out.append(_a)
    return _out


def post_delete_orphans(path: str, symbol: str, root: str, roots=None) -> dict:
    """**删后新孤儿**：被删者独占引用、删后零引用的本文件成员。

    与第四条件同口径（三条同时满足）：
      1. 名字在本文件内有 `def`/`class` 定义；
      2. 删除后本文件内引用数 == 0；
      3. 跨文件（排除本文件）调用+属性引用 == 0，且扫描未被截断。
    """
    _roots = tuple(roots or _DEFAULT_ROOTS)
    _abs = os.path.join(root, path.replace("/", os.sep))
    _src_txt, _tree = _parse(_abs)   # ★本脚本的 _parse 返回 (源码, AST) 两元组
    if _tree is None:
        return {"state": "unavailable", "reason": "parse_failed", "orphans": []}
    _hit = None
    for _n in ast.walk(_tree):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == symbol:
            _hit = _n
            break
    if _hit is None:
        return {"state": "unavailable", "reason": "symbol_not_found", "orphans": []}
    _lines = io.open(_abs, encoding="utf-8", errors="ignore").read().splitlines(keepends=True)
    _start = min([int(_hit.lineno)] + [int(d.lineno) for d in _hit.decorator_list]) - 1
    _end = int(getattr(_hit, "end_lineno", 0) or 0)
    _new_src = "".join(_lines[:_start] + _lines[_end:])
    try:
        _new_tree = ast.parse(_new_src)
    except SyntaxError as _se:
        # 删后语法坏了 ⇒ 孤儿判定无意义，交给调用方另判（不猜）
        _logger.debug("删后语法错 %s: %s: %s", path, symbol, _se)
        return {"state": "unavailable", "reason": "post_delete_syntax_error", "orphans": []}
    _defined = {str(n.name) for n in ast.walk(_new_tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    _refs = {}
    for _n in ast.walk(_new_tree):
        _k = (str(getattr(_n, "attr", "") or "") if isinstance(_n, ast.Attribute)
              else (str(_n.id) if isinstance(_n, ast.Name) else ""))
        if _k:
            _refs[_k] = _refs.get(_k, 0) + 1
    _out = []
    for _m in owned_member_names(_hit):
        if _m not in _defined or _refs.get(_m, 0) > 0:
            continue
        _cross = {"call_attr": 0, "call_name": 0, "attr_read": 0, "text_only": 0}
        _trunc = False
        for _p in _iter_py(root, _roots):
            if os.path.abspath(_p) == os.path.abspath(_abs):
                continue
            _r = scan_references(_p, _m)
            for _k in _cross:
                _cross[_k] += int(_r[_k])
        if _cross["call_attr"] + _cross["call_name"] + _cross["attr_read"] > 0:
            continue
        _out.append({"member": _m, "in_file_refs": 0, "cross": _cross,
                     "truncated": _trunc})
    return {"state": "ok", "orphans": _out,
            "verdict": "新孤儿 %d 个" % len(_out)}


# ===== ★344补 6：删除类补丁「整文件快照＋还原演练」=====
#: 快照根：**隔离根**，不写生产 `data/`（任务书补 6 明确要求）
DEFAULT_SNAPSHOT_ROOT = os.path.join("tmp", "o3_snapshots")


def _sha256(path: str) -> str:
    import hashlib
    _h = hashlib.sha256()
    with open(path, "rb") as _f:
        for _b in iter(lambda: _f.read(1 << 16), b""):
            _h.update(_b)
    return _h.hexdigest()


def snapshot_file(root: str, target_file: str,
                  snapshot_root: str = "") -> dict:
    """删除动作前的**整文件快照**。

    为什么不能只靠“反向 patch”：删掉的 def 无法靠文本还原（
    原文可能已被后续补丁改过），只有整文件快照能保证“还原回当时那一份”。
    """
    _abs = os.path.join(root, target_file.replace("/", os.sep))
    if not os.path.isfile(_abs):
        return {"state": "unavailable", "reason": "target_missing",
                "target_file": target_file}
    _sroot = snapshot_root or os.path.join(root, DEFAULT_SNAPSHOT_ROOT)
    os.makedirs(_sroot, exist_ok=True)
    import time as _t
    _stamp = _t.strftime("%Y%m%d_%H%M%S", _t.localtime())
    _name = ("%s__%s.bak" % (target_file.replace("/", "__"), _stamp))
    _dst = os.path.join(_sroot, _name)
    try:
        with open(_abs, "rb") as _f, open(_dst, "wb") as _g:
            _g.write(_f.read())
    except OSError as _e:
        _logger.debug("快照写失败 %s: %s: %s", target_file, type(_e).__name__, _e)
        return {"state": "unavailable", "reason": "%s: %s" % (type(_e).__name__, _e),
                "target_file": target_file}
    return {"state": "ok", "target_file": target_file.replace("\\", "/"),
            "source": _abs.replace("\\", "/"), "snapshot_path": _dst.replace("\\", "/"),
            "bytes": os.path.getsize(_dst), "sha256": _sha256(_dst),
            "in_production_data": os.path.abspath(_dst).startswith(
                os.path.abspath(os.path.join(root, "data")) + os.sep)}


def restore_drill(snapshot_path: str, work_dir: str = "") -> dict:
    """**还原演练**：把快照还到一个临时目录并比 sha256。

    演练不动源码：只证明“快照能完整写回此字节一致的一份”，不去碰真实目标文件。
    """
    if not os.path.isfile(snapshot_path):
        return {"state": "unavailable", "reason": "snapshot_missing"}
    _wd = work_dir or os.path.join(os.path.dirname(os.path.abspath(snapshot_path)),
                                   "_drill")
    os.makedirs(_wd, exist_ok=True)
    _dst = os.path.join(_wd, os.path.basename(snapshot_path) + ".restored")
    try:
        with open(snapshot_path, "rb") as _f, open(_dst, "wb") as _g:
            _g.write(_f.read())
    except OSError as _e:
        _logger.debug("还原演练写失败 %s: %s: %s", snapshot_path, type(_e).__name__, _e)
        return {"state": "unavailable", "reason": "%s: %s" % (type(_e).__name__, _e)}
    _a, _b = _sha256(snapshot_path), _sha256(_dst)
    return {"state": "ok" if _a == _b else "mismatch", "restored_path": _dst,
            "sha_snapshot": _a, "sha_restored": _b,
            "sha_match": _a == _b, "bytes": os.path.getsize(_dst)}


def deletion_snapshot_drill(root: str, target_file: str,
                            snapshot_root: str = "", work_dir: str = "") -> dict:
    """删除动作必备项一次做完：快照 → 还原演练（输出路径＋结果）。"""
    _snap = snapshot_file(root, target_file, snapshot_root)
    _out = {"snapshot": _snap, "restore_drill": None,
            "deletion_ready": False}
    if _snap.get("state") == "ok":
        _out["restore_drill"] = restore_drill(_snap["snapshot_path"], work_dir)
        _out["deletion_ready"] = bool(_out["restore_drill"].get("sha_match"))
    return _out


def check(root: str, target_file: str, symbol: str, roots=None,
          snapshot_root: str = "", work_dir: str = "",
          drill: bool = True) -> dict:
    _roots = tuple(roots or _DEFAULT_ROOTS)
    _tgt_abs = os.path.join(root, target_file.replace("/", os.sep))

    _defs = []
    if os.path.isfile(_tgt_abs):
        for _d in find_definitions(_tgt_abs, symbol):
            _d["file"] = target_file.replace("\\", "/")
            _defs.append(_d)

    _agg = {"call_attr": 0, "call_name": 0, "attr_read": 0, "text_only": 0}
    _per_file = []
    for _p in _iter_py(root, _roots):
        _r = scan_references(_p, symbol)
        _n = (_r["call_attr"] + _r["call_name"] + _r["attr_read"]
              + _r["text_only"])
        if _n:
            _rec = {"file": _rel(_p, root)}
            _rec.update(_r)
            _per_file.append(_rec)
            for _k in _agg:
                _agg[_k] += int(_r[_k])

    _total_text = _agg["call_attr"] + _agg["call_name"] + _agg["attr_read"] \
        + _agg["text_only"]
    _call_sites = _agg["call_attr"] + _agg["call_name"]
    _boundary = {}
    if _defs:
        _boundary = {"def_start_line": _defs[0]["line"],
                     "def_end_line": _defs[0]["end_line"]}
    return {
        "query": {"file": target_file.replace("\\", "/"), "symbol": symbol,
                  "roots": list(_roots),
                  "target_exists": os.path.isfile(_tgt_abs)},
        "definitions": {"count": len(_defs), "items": _defs},
        "references": {"call_attr": _agg["call_attr"],
                       "call_name": _agg["call_name"],
                       "attr_read": _agg["attr_read"],
                       "text_only": _agg["text_only"],
                       "total_text": _total_text,
                       "files": sorted(_per_file, key=lambda x: x["file"])},
        "verdict": {
            "no_definition": len(_defs) == 0,
            "no_call_site": _call_sites == 0,
            "call_sites": _call_sites,
            "truth": "定义 %d 处 / 调用 %d 处" % (len(_defs), _call_sites),
        },
        "boundary": _boundary,
        # ★344补 1：删后新孤儿（与第四条件同口径）
        "post_delete_orphans": (post_delete_orphans(
            target_file, symbol, root, _roots)
            if _defs else {"state": "skipped", "reason": "no_definition"}),
        # ★344补 6：删除类必备的整文件快照＋还原演练
        "deletion_drill": (deletion_snapshot_drill(
            root, target_file, snapshot_root, work_dir)
            if (drill and _defs) else {"skipped": True}),
    }


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(
        description="判死候选真值独立复核（AST 级，不读图、不 import 框架）")
    _ap.add_argument("--file", required=True, help="目标文件（相对仓库根）")
    _ap.add_argument("--symbol", required=True, help="目标符号（方法/函数名）")
    _ap.add_argument("--repo-root", default=None, help="仓库根（默认按本文件位置推断）")
    _ap.add_argument("--roots", default=",".join(_DEFAULT_ROOTS),
                     help="调用点扫描域（逗号分隔，默认 %s）" % ",".join(_DEFAULT_ROOTS))
    _ap.add_argument("--json-out", default="", help="结果 JSON 落盘路径（空=只打印）")
    _ap.add_argument("--snapshot-root", default="",
                       help="删除快照根（默认 tmp/o3_snapshots，禁写生产 data/）")
    _ap.add_argument("--work-dir", default="", help="还原演练目录")
    _ap.add_argument("--no-drill", action="store_true",
                       help="跳过快照与演练（只做 AST 复核）")
    _args = _ap.parse_args(argv)

    _root = _args.repo_root or project_root()
    _roots = tuple(x.strip() for x in _args.roots.split(",") if x.strip())
    _res = check(_root, _args.file, _args.symbol, _roots,
                 snapshot_root=_args.snapshot_root, work_dir=_args.work_dir,
                 drill=not _args.no_drill)
    _txt = json.dumps(_res, ensure_ascii=False, indent=2, sort_keys=True)

    if _args.json_out:
        _out = _args.json_out
        _dir = os.path.dirname(os.path.abspath(_out))
        if _dir and not os.path.isdir(_dir):
            os.makedirs(_dir, exist_ok=True)
        with io.open(_out, "w", encoding="utf-8", newline="\n") as _f:
            _f.write(_txt + "\n")
    print(_txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
