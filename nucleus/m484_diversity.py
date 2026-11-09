# -*- coding: utf-8 -*-
"""484-6 取种多样性埋点（烛微细前置设计 §3）。

data/monitor/seed_diversity_YYYYMMDD.jsonl，一行一 JSON，追加写。
★exit_reason 是关键：只有 ok 行进重合率统计，其余行计入 attempts，
用于回答「这条取种路径到底跑没跑」——防止「0 行日志 = 多样性完美」的假通过。
★测试隔离：未显式注入 out_dir 且处于 pytest 环境时拒写生产 data/（第43批铁律）。
"""
import hashlib
import json
import os
import sys
import time

from nucleus._silent_except import silent_exc

EXIT_REASONS = ("ok", "no_pool", "empty", "clean_reject", "deep_followup")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CALLER = "subconscious.self_question"


def _in_test_env() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _append(path, row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _prev_day_ids(out_dir, ts):
    """跨天首写时读前一日文件，得到并集口径的日集合重合率。"""
    _yest = time.strftime("%Y%m%d", time.localtime(ts - 86400))
    p = os.path.join(out_dir, "seed_diversity_" + _yest + ".jsonl")
    ids = set()
    rows = 0
    if not os.path.isfile(p):
        return p, rows, ids, False
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rows += 1
                try:
                    r = json.loads(line)
                except ValueError as _e:
                    silent_exc(_e, where="m484_diversity::_prev_day_ids.line",
                               level="debug")
                    continue
                if r.get("type") == "seed" and r.get("exit_reason") == "ok":
                    ids.update(r.get("ids") or [])
    except OSError as _e:
        silent_exc(_e, where="m484_diversity::_prev_day_ids.open", level="debug")
        return p, rows, ids, False
    return p, rows, ids, True


def record_seed(*, out_dir=None, l3_n=0, l2_n=0, ids=(), chosen=None,
                pool=None, filters=None, shortfall=False, weighted=False,
                elapsed_ms=0.0, exit_reason="ok", proc_gen="", now_ts=None,
                row_type="seed"):
    """写一行取种留痕，返回写入的 row；不写时返回 None（调用方零感知）。"""
    try:
        import config as _c
        if not bool(getattr(_c, "M484_DIVERSITY_LOG", True)):
            return None
        _max_lines = int(getattr(_c, "M484_DIVERSITY_MAX_LINES", 5000))
    except Exception as _e:
        silent_exc(_e, where="m484_diversity::record_seed.cfg", level="debug")
        return None

    if out_dir is None:
        if _in_test_env():
            return None
        out_dir = os.path.join(_ROOT, "data", "monitor")

    try:
        ts = float(now_ts) if now_ts is not None else time.time()
        os.makedirs(out_dir, exist_ok=True)
        day = time.strftime("%Y%m%d", time.localtime(ts))
        path = os.path.join(out_dir, "seed_diversity_" + day + ".jsonl")

        existing = 0
        has_open = False
        capped = False
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    existing += 1
                    if '"day_open"' in line:
                        has_open = True
                    if '"capped"' in line:
                        capped = True

        if not has_open:
            prev_file, prev_rows, prev_ids, computed = _prev_day_ids(out_dir, ts)
            today_ids = {str(x) for x in ids}
            inter = len(today_ids & prev_ids)
            union = len(today_ids | prev_ids)
            _append(path, {
                "ts": ts, "type": "day_open", "caller": _CALLER,
                "prev_file": prev_file if computed else None,
                "prev_rows": prev_rows,
                "overlap_ratio": round(inter / len(today_ids), 6) if today_ids else 0.0,
                "jaccard": round(inter / union, 6) if union else 0.0,
                "computed": computed,
                "n_today": existing,
            })
            existing += 1

        if capped or existing >= _max_lines:
            if not capped:
                _append(path, {"ts": ts, "type": "capped", "caller": _CALLER,
                               "lines": existing, "max_lines": _max_lines})
            return None

        sorted_ids = sorted(str(x) for x in ids)
        fingerprint = hashlib.sha1(
            "\n".join(sorted_ids).encode("utf-8")).hexdigest()[:16]
        row = {
            "ts": ts,
            "proc_gen": str(proc_gen or ""),
            "type": row_type,
            "caller": _CALLER,
            "l3_n": int(l3_n),
            "l2_n": int(l2_n),
            "total_n": int(l3_n) + int(l2_n),
            "ids": sorted_ids,
            "fingerprint": fingerprint,
            "chosen": chosen,
            "pool": dict(pool or {}),
            "filters": dict(filters or {}),
            "shortfall": bool(shortfall),
            "weighted": bool(weighted),
            "elapsed_ms": float(elapsed_ms),
            "exit_reason": str(exit_reason),
        }
        _append(path, row)
        return row
    except Exception as _e:
        silent_exc(_e, where="m484_diversity::record_seed", level="debug")
        return None


__all__ = ["EXIT_REASONS", "record_seed"]
