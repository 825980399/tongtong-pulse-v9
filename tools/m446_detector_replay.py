# -*- coding: utf-8 -*-
"""446 刀1 校准工具：三个新检测器的**历史回放**与**仓内扫描**。

用途（任务书 §刀1 验证三条）
    ① 三个检测器都写出来了吗？        → `--detectors`
    ② 对历史 87 条回放，误报/漏报多少？→ 默认模式
    ③ `periodic_task_no_reentry` 能拿到 baseline>0 了吗？→ `--scan`

设计纪律
    * 只读：不改 `patch_history.json`、不改任何补丁判定。
    * 唯一写入面 = `--write-trace` 显式开启时，向 `data/patches/detector_trace.jsonl`
      追加 `source="replay446"` 的行（用于证明留痕口真的通）。
    * 「自有样本」= `issue_type == 该检测器` 的历史条目；「跨类型命中」= 拿该检测器去跑
      **别的** issue_type 条目的 `original_code`。后者不必然是误报（同一段代码可能同时
      有两类缺陷），所以工具把命中原文一并打出来，让人自己判。

用法
    python tools/m446_detector_replay.py                # 回放 87 条
    python tools/m446_detector_replay.py --scan         # 仓内真实代码扫描
    python tools/m446_detector_replay.py --write-trace  # 回放并落留痕
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc
from nucleus.evolution.patch_active_reprobe import (
    _DETECTORS,
    detector_hits,
)

HISTORY = os.path.join(_ROOT, "data", "patches", "patch_history.json")
TARGETS = ("periodic_task_no_reentry", "non_atomic_write",
           "cross_module_singleton_call")
SCAN_ROOTS = ("organs", "nucleus", "utils")
SKIP_DIRS = frozenset({"data", "logs", "tmp", "backups", "__pycache__", "venv",
                       "models", "node_modules"})


def load_history() -> list:
    try:
        with open(HISTORY, "r", encoding="utf-8") as _f:
            _d = json.load(_f)
        return _d if isinstance(_d, list) else []
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="tools/m446_detector_replay.py::load_history", level="warning")
        return []


def replay(rows: list) -> dict:
    """逐检测器回放：自有样本命中/漏报 + 跨类型命中。"""
    _out = {}
    for _name in TARGETS:
        _fn = _DETECTORS.get(_name)
        if _fn is None:
            _out[_name] = {"registered": False}
            continue
        _own = [x for x in rows if x.get("issue_type") == _name]
        _hit = [x for x in _own if _fn(x.get("original_code") or "")]
        _miss = [x for x in _own if not _fn(x.get("original_code") or "")]
        _cross = []
        for _x in rows:
            if _x.get("issue_type") == _name:
                continue
            _h = _fn(_x.get("original_code") or "")
            if _h:
                _cross.append({"id": str(_x.get("id") or "")[:16],
                               "issue_type": _x.get("issue_type"),
                               "file": _x.get("file"), "method": _x.get("method"),
                               "why": _h[0].get("why")})
        _out[_name] = {
            "registered": True,
            "own_samples": len(_own),
            "own_hit": len(_hit),
            "own_miss": len(_miss),
            "recall": (round(len(_hit) / float(len(_own)), 4) if _own else None),
            "miss_samples": [[str(x.get("file") or ""), str(x.get("method") or "")]
                             for x in _miss][:8],
            "cross_hit": len(_cross),
            "cross_rate": (round(len(_cross) / float(len(rows) - len(_own)), 4)
                           if len(rows) > len(_own) else None),
            "cross_samples": _cross[:8],
        }
    return _out


def _methods_of(path: str):
    """产出 `(方法名, 方法源码, 起始行)`；解析失败则空。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        _tree = ast.parse(_src)
    except (OSError, SyntaxError, ValueError) as _e:
        silent_exc(_e, where="tools/m446_detector_replay.py::_methods_of", level="debug")
        return
    _lines = _src.splitlines()
    for _n in ast.walk(_tree):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _end = getattr(_n, "end_lineno", None) or _n.lineno
            yield _n.name, "\n".join(_lines[_n.lineno - 1:_end]), _n.lineno


def scan_repo() -> dict:
    """仓内真实代码扫描：三类各命中多少处（= future baseline 的可达上界）。"""
    _hits = {_n: [] for _n in TARGETS}
    _files = _methods = 0
    for _root in SCAN_ROOTS:
        _abs = os.path.join(_ROOT, _root)
        for _dp, _dns, _fns in os.walk(_abs):
            _dns[:] = [d for d in _dns
                       if d not in SKIP_DIRS and not d.startswith((".bak", "."))]
            for _fn in _fns:
                if not _fn.endswith(".py"):
                    continue
                _p = os.path.join(_dp, _fn)
                _files += 1
                for _m, _body, _ln in _methods_of(_p):
                    _methods += 1
                    for _n in TARGETS:
                        _fn_det = _DETECTORS.get(_n)
                        if _fn_det is None:
                            continue
                        _h = _fn_det(_body)
                        if _h:
                            _hits[_n].append({
                                "file": os.path.relpath(_p, _ROOT).replace("\\", "/"),
                                "method": _m, "line": _ln,
                                "why": _h[0].get("why")})
    return {
        "files": _files, "methods": _methods,
        "hits": {k: {"count": len(v), "samples": v[:8]} for k, v in _hits.items()},
    }


def write_trace(rows: list) -> int:
    """把回放里**真的命中**的样本写进留痕文件（证明留痕口通）。"""
    from nucleus.evolution.baseline_probe import note_detector_hit
    _n = 0
    for _x in rows:
        _it = _x.get("issue_type")
        _h = detector_hits(_it, _x.get("original_code") or "")
        if not _h:
            continue
        _ok = note_detector_hit(
            issue_type=_it, file=str(_x.get("file") or ""),
            method=str(_x.get("method") or ""),
            rule=" ; ".join(str(_y.get("why") or "") for _y in _h[:3]),
            baseline=len(_h), after=None, verdict="replay",
            patch_id=str(_x.get("id") or ""), source="replay446")
        if _ok:
            _n += 1
    return _n


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="446 刀1 检测器校准")
    _ap.add_argument("--scan", action="store_true", help="仓内真实代码扫描")
    _ap.add_argument("--write-trace", action="store_true", help="回放并把命中落留痕")
    _ap.add_argument("--out", default="", help="把结果 JSON 写到该路径")
    _args = _ap.parse_args(argv)

    _res = {"detectors": sorted(_DETECTORS)}
    if _args.scan:
        _res["scan"] = scan_repo()
    else:
        _rows = load_history()
        _res["history_total"] = len(_rows)
        _res["replay"] = replay(_rows)
        if _args.write_trace:
            _res["trace_rows"] = write_trace(_rows)

    _txt = json.dumps(_res, ensure_ascii=False, indent=2)
    if _args.out:
        try:
            with open(_args.out, "w", encoding="utf-8") as _f:
                _f.write(_txt)
        except OSError as _e:
            silent_exc(_e, where="tools/m446_detector_replay.py::main 写 --out",
                       level="warning")
    print(_txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
