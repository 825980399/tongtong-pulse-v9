# -*- coding: utf-8 -*-
"""判据窗口硬编码扫描（★344批刀4）—— **AST 版，带判别力**。

为什么要重写：343 刀7 的扫描器是**字符串正则**，把 8 处全部误命中
（详见 `tmp/p344_root/criteria_hardcode_inout.md`）：

  · `nucleus/self_inspector.py:10` 模块文档串里的"（3620行）"—— 行数，不是窗口
  · `PatchManager.py:4739` `where="...L3634"` —— 行号，不是窗口
  · `PulseLiver.py:3673` `"...py:3622:..."` —— 行号，不是窗口
  · `criteria_window.py:6/31/35/36` —— **唯一源自身**的定义，不是待迁硬编码
  · `PulseKidney.py:136` `_search_content_zombie_hours = 12.0` —— 内容僵尸保留
    时长（小时），与"12 圈"毫无关系

判别力来自三条**结构性**约束（不是再加几个正则）：

  A. 只认 **AST 数值常量**（`ast.Constant` 的 int/float）—— 字符串、文档串、
     注释、行号串天然出局，这是 343 版最大的误报源；
  B. 只认**落在窗口数值带**内的值（默认 3000–4000 秒）**且**其绑定的名字/上下文
     带窗口语义（win/window/round/criteria/圈/轮）—— 两边都要满足，
     单靠数值带会把 3600（1 小时）全收进来；
  C. `criteria_window.py` 自身**豁免**（它就是唯一源），但要单独报出它内部
     还剩几个数值字面量（唯一源内部也不许有第二份口径）。

用法::

    python tools/audit/criteria_hardcode_scan.py            # 人读 + JSON 到 stdout
    python tools/audit/criteria_hardcode_scan.py --json     # 仅 JSON
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys

_ROOTS = ("nucleus", "organs", "tools", "base")
_SKIP_DIR_PARTS = (".bak_batch", "__pycache__", "docker_build_ctx", ".git", "tmp",
                   "node_modules", ".venv")

#: 唯一源（豁免；其内部字面量单独统计）
SOURCE_OF_TRUTH = "nucleus/evolution/criteria_window.py"

#: 窗口数值带（秒）。336 实测 12 圈 3594.6–3728.2s，两侧留余量。
BAND_LO = 3000.0
BAND_HI = 4000.0

#: **判据窗口**专用语义标记（344 收紧）。
#: 为什么不用宽泛的 "win"/"window"：实测那样会把 10 处 **1 小时通用窗口**
#: （`_round_interval_sec` / `in_night_window` / `_fatigue_max_window` …）
#: 全收进来，而它们与"判据圈长"根本不是一件事 —— 误命中率和 343 版一样高，
#: 只是错法从"字符串"换成了"泛窗口名"（铁律168：判据先要有判别力）。
_HARD_HINTS = ("criteria", "圈", "压测", "round_sec", "win_lo", "win_hi",
               "criteria_window", "r12")

#: 1 小时通用窗口的典型值（只做**信息项**列示，不算判据窗口硬编码）
_GENERIC_HOUR = (3600, 3600.0)


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _iter_py(root: str):
    for _base in _ROOTS:
        _d = os.path.join(root, _base)
        if not os.path.isdir(_d):
            continue
        for _dp, _dns, _fns in os.walk(_d):
            _dns[:] = [x for x in _dns
                       if not any(sk in x for sk in _SKIP_DIR_PARTS)]
            for _fn in _fns:
                if _fn.endswith(".py"):
                    yield os.path.join(_dp, _fn)


def _hint_of_node(node, parents: dict) -> str:
    """收集该常量"绑定的名字 + 所在函数名 + 所在赋值目标名"，合成一个可判的标记串。"""
    _bits = []
    _cur = node
    _guard = 0
    while _cur is not None and _guard < 8:
        _guard += 1
        _p = parents.get(id(_cur))
        if isinstance(_p, ast.Assign):
            for _t in _p.targets:
                if isinstance(_t, ast.Name):
                    _bits.append(_t.id)
                elif isinstance(_t, ast.Attribute):
                    _bits.append(_t.attr)
        elif isinstance(_p, ast.AnnAssign) and isinstance(_p.target, ast.Name):
            _bits.append(_p.target.id)
        elif isinstance(_p, ast.keyword) and _p.arg:
            _bits.append(_p.arg)
        elif isinstance(_p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _bits.append(_p.name)
        elif isinstance(_p, ast.ClassDef):
            _bits.append(_p.name)
        _cur = _p
        if isinstance(_cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Module)):
            break
    return "|".join(_bits).lower()


def _has_hard_hint(text: str) -> bool:
    return any(h in text for h in _HARD_HINTS)


def scan(root: str | None = None) -> dict:
    _root = root or _project_root()
    _hits = []
    _generic_hour = []
    _source_internal = []
    _scanned = 0
    for _p in _iter_py(_root):
        _rel = os.path.relpath(_p, _root).replace("\\", "/")
        try:
            _src = io.open(_p, encoding="utf-8", errors="ignore").read()
            _tree = ast.parse(_src)
        except (OSError, SyntaxError):
            continue
        _scanned += 1
        _parents = {}
        for _node in ast.walk(_tree):
            for _child in ast.iter_child_nodes(_node):
                _parents[id(_child)] = _node
        for _n in ast.walk(_tree):
            if not isinstance(_n, ast.Constant):
                continue
            _v = _n.value
            if isinstance(_v, bool) or not isinstance(_v, (int, float)):
                continue
            if not (BAND_LO <= float(_v) <= BAND_HI):
                continue
            _ctx = _hint_of_node(_n, _parents)
            _rec = {"file": _rel, "line": int(getattr(_n, "lineno", 0) or 0),
                    "value": _v, "context": _ctx or "(无绑定名)",
                    "in_criteria_context": _has_hard_hint(_ctx)}
            if _rel == SOURCE_OF_TRUTH:
                _source_internal.append(_rec)
                continue
            if _v in _GENERIC_HOUR:
                _generic_hour.append(_rec)
                continue
            if _rec["in_criteria_context"]:
                _hits.append(_rec)
    return {"scanned_files": _scanned, "hits": _hits,
            "generic_hour_windows": _generic_hour,
            "source_internal_literals": _source_internal,
            "band": [BAND_LO, BAND_HI],
            "roots": list(_ROOTS), "source_of_truth": SOURCE_OF_TRUTH}


def main(argv=None) -> int:
    _argv = list(sys.argv[1:] if argv is None else argv)
    _r = scan()
    _out = {
        "tool": "criteria_hardcode_scan",
        "version": "344.ast.v1",
        "scanned_files": _r["scanned_files"],
        "band": _r["band"],
        "roots": _r["roots"],
        "n_hits_outside_source": len(_r["hits"]),
        "n_source_internal_literals": len(_r["source_internal_literals"]),
        "n_generic_hour_windows": len(_r["generic_hour_windows"]),
        "generic_hour_windows": _r["generic_hour_windows"],
        "hits": _r["hits"],
        "source_internal_literals": _r["source_internal_literals"],
        "verdict": ("PASS" if not _r["hits"] else "FAIL"),
        "note": ("只认 AST 数值常量 + 必须落在窗口数值带 + 上下文带窗口语义；"
                 "字符串/文档串/行号串不出现在结果里（343 版 8 处误命中全属此类）"),
    }
    if "--json" in _argv:
        print(json.dumps(_out, ensure_ascii=False, indent=2))
        return 0 if not _r["hits"] else 1
    print("[criteria_hardcode_scan 344.ast.v1] 扫描文件 %d 个（域=%s）"
          % (_out["scanned_files"], ",".join(_out["roots"])))
    print("  唯一源豁免: %s（其内部数值字面量 %d 处）"
          % (SOURCE_OF_TRUTH, _out["n_source_internal_literals"]))
    print("  唯一源**之外**的窗口硬编码: %d 处 ⇒ %s"
          % (_out["n_hits_outside_source"], _out["verdict"]))
    for _h in _r["hits"]:
        print("   · %s:%d 值=%s 上下文=%s" % (_h["file"], _h["line"],
                                            _h["value"], _h["context"]))
    print("  【信息项】1 小时通用窗口（3600s，非判据窗口，不计入失败）: %d 处"
          % len(_r["generic_hour_windows"]))
    for _h in _r["source_internal_literals"]:
        print("   [源内] %s:%d 值=%s 上下文=%s"
              % (_h["file"], _h["line"], _h["value"], _h["context"]))
    print(json.dumps(_out, ensure_ascii=False, indent=2))
    return 0 if not _r["hits"] else 1


if __name__ == "__main__":
    sys.exit(main())
