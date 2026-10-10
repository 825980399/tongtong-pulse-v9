# -*- coding: utf-8 -*-
"""LLM 留存字段历史回填工具（★主线第44批 T1 / P1-285 + P1-286）。

背景
----
第43批 T1 数据质量评估实测：``data/llm_traces/calls_20260913.jsonl`` 中
``prompt_version`` **100% 缺失**（246/246），111 条 ``failed`` 记录的 ``error``
**全空**。历史数据若不回填，调用精简 / 归因分析只能从"本批之后"起算。

行为
----
* ``prompt_version`` 缺失/空 → 填 ``"unknown"``（或 ``--version`` 指定值）。
* ``failed``（``status != success``）且 ``error`` 为空 →
  * ``response`` 非空 → 取 ``response`` 前若干字符并加 ``backfilled_from_response:`` 前缀；
  * 否则 → ``"(backfilled: source had no error detail)"``。
* **不改动 ``prompt`` / ``response`` / ``status`` 等业务字段**，只补两个字段。

安全
----
* **默认 dry-run**，只打印统计与样例，**不写任何文件**。
* ``--apply`` 才写盘；写前做**两层备份**：
  1. **整目录归档**：``data/_archive/llm_traces_backup_YYYYMMDD_HHMMSS/``
     （★第45批 T4 新增：逐文件 size 校验 + sha256 前 16 位，写 ``BACKUP_MANIFEST.json``）
  2. 单文件 ``<file>.bak_batch44``（已存在则复用）
* 支持 ``--dir`` 指定目录（测试注入隔离目录）；``--no-backup`` 跳过归档备份。

用法
----
    python tools/backfill_llm_trace_fields.py                 # dry-run
    python tools/backfill_llm_trace_fields.py --apply         # 实际写盘
    python tools/backfill_llm_trace_fields.py --dir <dir> [--apply]
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 回填时 ``prompt_version`` 的缺省值（与 ``call_recorder.DEFAULT_PROMPT_VERSION`` 同源）。
BACKFILL_PROMPT_VERSION = "unknown"

#: ``failed`` 记录在源文件里连 ``response`` 都没有时的占位说明。
BACKFILL_NO_DETAIL = "(backfilled: source had no error detail)"

#: 从 ``response`` 提取 error 时的单字段上限。
BACKFILL_ERROR_LIMIT = 200

#: 备份后缀。
BACKUP_SUFFIX = ".bak_batch44"


def _day_files(directory: str, prefix: str = "calls") -> list[str]:
    """按文件名排序返回 ``<directory>/<prefix>_*.jsonl``。"""
    return sorted(glob.glob(os.path.join(directory, "{}_*.jsonl".format(prefix))))

def archive_backup(src_dir: str,
                   archive_root: str | None = None) -> dict[str, Any]:
    """把整个留存目录归档备份到 ``<archive_root>/llm_traces_backup_YYYYMMDD_HHMMSS/``。

    ★完整性校验：逐文件比对 **size**，不一致或缺失则记入 ``mismatch``；
    ★写 ``BACKUP_MANIFEST.json`` 记录每个文件的 size + sha256 前 16 位（供事后核对）；
    ★**不删除源目录**（归档 = 复制）。
    """
    import datetime as _dt2
    import hashlib as _hl
    _root = archive_root or os.path.join(_ROOT, "data", "_archive")
    _stamp = _dt2.datetime.now().strftime("%Y%m%d_%H%M%S")
    _dest = os.path.join(_root, "llm_traces_backup_{}".format(_stamp))
    os.makedirs(_dest, exist_ok=True)
    _manifest: list[dict[str, Any]] = []
    _mismatch: list[str] = []
    _total = 0
    for _fn in sorted(os.listdir(src_dir)):
        _sp = os.path.join(src_dir, _fn)
        if not os.path.isfile(_sp):
            continue
        _dp = os.path.join(_dest, _fn)
        with open(_sp, "rb") as _f:
            _raw = _f.read()
        with open(_dp, "wb") as _f:
            _f.write(_raw)
        _ok = os.path.getsize(_dp) == len(_raw)
        if not _ok:
            _mismatch.append(_fn)
        _total += len(_raw)
        _manifest.append({"file": _fn, "size": len(_raw), "ok": _ok,
                          "sha256": _hl.sha256(_raw).hexdigest()[:16]})
    _mf = os.path.join(_dest, "BACKUP_MANIFEST.json")
    with io.open(_mf, "w", encoding="utf-8") as f:
        f.write(json.dumps({"src": src_dir, "stamp": _stamp,
                            "files": _manifest, "mismatch": _mismatch,
                            "total_bytes": _total},
                           ensure_ascii=False, indent=2))
    return {"dest": _dest, "files": len(_manifest), "total_bytes": _total,
            "mismatch": _mismatch, "manifest": _mf, "ok": not _mismatch}



def _needs_backfill(rec: dict[str, Any]) -> tuple[bool, bool]:
    """返回 ``(需要补 prompt_version, 需要补 error)``。"""
    _pv_missing = not str(rec.get("prompt_version", "") or "").strip()
    _err_missing = (str(rec.get("status", "success")) != "success"
                    and not str(rec.get("error", "") or "").strip())
    return _pv_missing, _err_missing


def _derive_error(rec: dict[str, Any]) -> str:
    """为缺失 ``error`` 的 failed 记录派生一个可分析的值。"""
    _resp = str(rec.get("response", "") or "").strip()
    if _resp:
        return ("backfilled_from_response: "
                + _resp[:BACKFILL_ERROR_LIMIT])
    return BACKFILL_NO_DETAIL


def plan_file(path: str, version: str = BACKFILL_PROMPT_VERSION) -> dict[str, Any]:
    """扫描单个 JSONL，返回「将要做的改动」统计与样例（**只读**）。"""
    _total = 0
    _pv_fill = 0
    _err_fill = 0
    _bad_lines = 0
    _samples: list[dict[str, Any]] = []
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        for _ln, _line in enumerate(f, 1):
            _s = _line.strip()
            if not _s:
                continue
            try:
                _rec = json.loads(_s)
            except Exception:
                _bad_lines += 1
                continue
            if not isinstance(_rec, dict):
                _bad_lines += 1
                continue
            _total += 1
            _pv_need, _err_need = _needs_backfill(_rec)
            if _pv_need:
                _pv_fill += 1
            if _err_need:
                _err_fill += 1
            if (_pv_need or _err_need) and len(_samples) < 3:
                _samples.append({
                    "line": _ln,
                    "trace_id": _rec.get("trace_id", ""),
                    "status": _rec.get("status", ""),
                    "set_prompt_version": version if _pv_need else None,
                    "set_error": _derive_error(_rec) if _err_need else None,
                })
    return {"file": path, "total": _total, "prompt_version_fill": _pv_fill,
            "error_fill": _err_fill, "bad_lines": _bad_lines,
            "samples": _samples}


def apply_file(path: str, version: str = BACKFILL_PROMPT_VERSION) -> dict[str, Any]:
    """执行回填：先备份，再重写（逐行 JSON，保持 UTF-8 与 ``ensure_ascii=False``）。"""
    _plan = plan_file(path, version)
    _bak = path + BACKUP_SUFFIX
    if not os.path.exists(_bak):
        with open(path, "rb") as _f:
            _raw = _f.read()
        with open(_bak, "wb") as _f:
            _f.write(_raw)
    _out: list[str] = []
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        for _line in f:
            _s = _line.strip()
            if not _s:
                continue
            try:
                _rec = json.loads(_s)
            except Exception:
                _out.append(_s)
                continue
            if not isinstance(_rec, dict):
                _out.append(_s)
                continue
            _pv_need, _err_need = _needs_backfill(_rec)
            if _pv_need:
                _rec["prompt_version"] = version
            if _err_need:
                _rec["error"] = _derive_error(_rec)
            _out.append(json.dumps(_rec, ensure_ascii=False, default=str))
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(_out) + ("\n" if _out else ""))
    # ★口径与 plan_file 对齐（拍平）—— run() 的合计依赖 total/prompt_version_fill/error_fill
    return {**_plan, "backup": _bak, "applied": True}


def run(directory: str, apply: bool = False,
        version: str = BACKFILL_PROMPT_VERSION,
        backup: bool = True, archive_root: str | None = None) -> dict[str, Any]:
    """对目录下全部 ``calls_*.jsonl`` 执行（或演练）回填。

    ``apply=True`` 且 ``backup=True`` 时，**先做整目录归档备份**再逐文件回填。
    """
    _files = _day_files(directory)
    _report: dict[str, Any] = {"dir": directory, "apply": bool(apply),
                               "version": version, "files": [],
                               "archive_backup": None}
    if apply and backup:
        _report["archive_backup"] = archive_backup(
            directory, archive_root or os.path.join(_ROOT, "data", "_archive"))
    for _fp in _files:
        if apply:
            _report["files"].append(apply_file(_fp, version))
        else:
            _report["files"].append(plan_file(_fp, version))
    _report["total_records"] = sum(x["total"] for x in _report["files"])
    _report["total_prompt_version_fill"] = sum(
        x["prompt_version_fill"] for x in _report["files"])
    _report["total_error_fill"] = sum(x["error_fill"] for x in _report["files"])
    return _report


def _default_dir() -> str:
    try:
        import config
        _rel = str(getattr(config, "LLM_TRACE_DIR", "data/llm_traces"))
    except Exception:
        _rel = "data/llm_traces"
    if os.path.isabs(_rel):
        return _rel
    return os.path.join(_ROOT, _rel.replace("/", os.sep))


def main(argv: list[str] | None = None) -> int:
    _ap = argparse.ArgumentParser(description="LLM 留存字段历史回填（第44批 T1）")
    _ap.add_argument("--dir", default=None, help="留存目录（默认取 config.LLM_TRACE_DIR）")
    _ap.add_argument("--apply", action="store_true", help="实际写盘（默认 dry-run）")
    _ap.add_argument("--version", default=BACKFILL_PROMPT_VERSION,
                     help="回填用的 prompt_version 值")
    _ap.add_argument("--report", default=None, help="报告 JSON 输出路径（--apply 时）")
    _ap.add_argument("--archive-root", default=os.path.join(_ROOT, "data", "_archive"),
                     help="整目录归档根（默认 data/_archive）")
    _ap.add_argument("--no-backup", action="store_true",
                     help="跳过「整目录归档备份」（不推荐）")
    _ns = _ap.parse_args(argv)
    _dir = _ns.dir or _default_dir()
    _rep = run(_dir, apply=_ns.apply, version=_ns.version,
               backup=not _ns.no_backup, archive_root=_ns.archive_root)
    print("[回填] dir={} apply={}".format(_dir, _ns.apply))
    for _f in _rep["files"]:
        print("  - %s 记录=%d prompt_version补=%d error补=%d"
              % (os.path.basename(_f["file"]), _f["total"],
                 _f["prompt_version_fill"], _f["error_fill"]))
    print("[回填] 合计 记录=%d prompt_version补=%d error补=%d"
          % (_rep["total_records"], _rep["total_prompt_version_fill"],
             _rep["total_error_fill"]))
    _ab = _rep.get("archive_backup")
    if _ab:
        print("[回填] 归档备份 → %s（%d 文件 / %.2f MB，校验=%s）"
              % (_ab["dest"], _ab["files"], _ab["total_bytes"] / 1048576.0,
                 "OK" if _ab["ok"] else "MISMATCH:{}".format(_ab["mismatch"])))
    if _ns.report:
        with io.open(_ns.report, "w", encoding="utf-8") as f:
            f.write(json.dumps(_rep, ensure_ascii=False, indent=2))
        print("[回填] 报告 →", _ns.report)
    if not _ns.apply:
        print("[回填] dry-run（未写盘）。加 --apply 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
