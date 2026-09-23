# -*- coding: utf-8 -*-
"""reprobe_patches.py —— 补丁主动复现**回填工具**（★主线第51批 T1，P0-2）

作用
----
用 `nucleus/evolution/patch_active_reprobe.py` 的**主动复现**机制，
对 `data/patches/patch_history.json` 的历史补丁逐条重新验证，并（可选）回填：

    reprobe_verdict / reprobe_issue_type / reprobe_baseline_hits
    reprobe_after_hits / reprobe_detail / reprobe_version

★与旧口径的区别
--------------
旧：在 `pulse.log` 里数目标文件的错误行数（**日志会被轮转截断** → 基线恒 0 → 假通过）。
新：从补丁自身的 ``original_code`` / ``modified_code`` **代码对**做静态复现 → **不依赖日志**。

用法
----
    python tools/reprobe_patches.py                     # 只报告（默认）
    python tools/reprobe_patches.py --apply             # 回填（自动备份 + sha256）
    python tools/reprobe_patches.py --report out.json   # 另存 JSON 报告
    python tools/reprobe_patches.py --limit 10          # 只验证前 N 条
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nucleus.evolution.patch_active_reprobe import (  # noqa: E402
    F_REPROBE_AFTER, F_REPROBE_BASELINE, F_REPROBE_DETAIL, F_REPROBE_ISSUE,
    F_REPROBE_VERSION, F_REPROBE_VERDICT, V_TRUE_PASS,
    reprobe_batch, true_fix_rate, verdict_label)

HISTORY = os.path.join(ROOT, "data", "patches", "patch_history.json")


def _sha256(path: str) -> str:
    _h = hashlib.sha256()
    with io.open(path, "rb") as _f:
        for _chunk in iter(lambda: _f.read(65536), b""):
            _h.update(_chunk)
    return _h.hexdigest()


def _load(path: str):
    _d = json.load(io.open(path, encoding="utf-8"))
    _ps = _d.get("patches") if isinstance(_d, dict) and "patches" in _d else _d
    return _d, (_ps if isinstance(_ps, list) else [])


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="补丁主动复现回填（默认只报告）")
    _ap.add_argument("--apply", action="store_true", help="回填到补丁历史（自动备份）")
    _ap.add_argument("--report", default="", help="JSON 报告输出路径")
    _ap.add_argument("--limit", type=int, default=0, help="只验证前 N 条（0=全部）")
    _ns = _ap.parse_args(argv)

    if not os.path.isfile(HISTORY):
        print("[ERR] 补丁历史不存在: %s" % HISTORY)
        return 2

    _doc, _ps = _load(HISTORY)
    _target = _ps[:_ns.limit] if _ns.limit > 0 else _ps
    print("=== 补丁主动复现（%s）===" % ("回填" if _ns.apply else "只报告"))
    print("  补丁历史: %s" % os.path.relpath(HISTORY, ROOT))
    print("  补丁总数: %d（本次验证 %d）" % (len(_ps), len(_target)))
    print()

    _rep = reprobe_batch(_target)
    print("--- 按结论分布 ---")
    for _k, _v in sorted(_rep["by_verdict"].items(), key=lambda kv: -kv[1]):
        print("   %-24s %-22s %d" % (_k, verdict_label(_k), _v))
    print()
    _strict = true_fix_rate(_target, strict=True)
    _loose = true_fix_rate(_target, strict=False)
    print("  ★严格口径修复率（问题完全清零）: %s" % _strict)
    print("  ★宽松口径修复率（含部分修复）  : %s" % _loose)
    print()

    # 明细（非 true_pass 优先展示，便于人工复核）
    _rows = sorted(_rep["rows"],
                   key=lambda r: (r[F_REPROBE_VERDICT] == V_TRUE_PASS,
                                  str(r.get("id"))))
    print("--- 明细（非 true_pass 优先）---")
    for _r in _rows[:15]:
        print("   %-28s %-20s base=%-4s after=%-4s" % (
            str(_r.get("id"))[:28], _r[F_REPROBE_VERDICT],
            _r[F_REPROBE_BASELINE], _r[F_REPROBE_AFTER]))
        print("        %s" % str(_r[F_REPROBE_DETAIL])[:130])
    if len(_rows) > 15:
        print("   ...（共 %d 条）" % len(_rows))
    print()

    if not _ns.apply:
        print("（只报告：未改动补丁历史。加 --apply 回填）")
        if _ns.report:
            io.open(_ns.report, "w", encoding="utf-8").write(json.dumps(
                {"mode": "dry_run", "summary": _rep,
                 "true_fix_rate_strict": _strict,
                 "true_fix_rate_loose": _loose},
                ensure_ascii=False, indent=2))
        return 0

    # ---- 回填（备份 + sha256）----
    _ts = time.strftime("%Y%m%d_%H%M%S")
    _bak_dir = os.path.join(ROOT, "data", "_archive", "patch_reprobe")
    os.makedirs(_bak_dir, exist_ok=True)
    _bak = os.path.join(_bak_dir, "patch_history.%s.json" % _ts)
    _before_sha = _sha256(HISTORY)
    shutil.copy2(HISTORY, _bak)
    assert _sha256(_bak) == _before_sha, "备份 sha256 不一致，中止"

    _map = {str(r.get("id")): r for r in _rep["rows"]}
    _n = 0
    for _p in _target:
        if not isinstance(_p, dict):
            continue
        _r = _map.get(str(_p.get("id")))
        if _r is None:
            continue
        _p[F_REPROBE_VERDICT] = _r[F_REPROBE_VERDICT]
        _p[F_REPROBE_ISSUE] = _r[F_REPROBE_ISSUE]
        _p[F_REPROBE_BASELINE] = _r[F_REPROBE_BASELINE]
        _p[F_REPROBE_AFTER] = _r[F_REPROBE_AFTER]
        _p[F_REPROBE_DETAIL] = _r[F_REPROBE_DETAIL]
        _p[F_REPROBE_VERSION] = _r[F_REPROBE_VERSION]
        _n += 1

    if isinstance(_doc, dict):
        _doc["reprobe_at"] = time.time()
        _doc["reprobe_version"] = 1
        _doc["reprobe_true_fix_rate_strict"] = _strict
        _doc["reprobe_true_fix_rate_loose"] = _loose
        _out = _doc
    else:
        _out = _ps
    with io.open(HISTORY, "w", encoding="utf-8") as _f:
        _f.write(json.dumps(_out, ensure_ascii=False, indent=2))

    _after_sha = _sha256(HISTORY)
    print("[回填] %d 条已写入（备份: %s）" % (_n, os.path.relpath(_bak, ROOT)))
    print("  sha256 before=%s" % _before_sha[:32])
    print("  sha256 after =%s" % _after_sha[:32])
    print("  变更: %s" % ("是" if _before_sha != _after_sha else "否"))
    if _ns.report:
        io.open(_ns.report, "w", encoding="utf-8").write(json.dumps(
            {"mode": "apply", "summary": _rep,
             "true_fix_rate_strict": _strict,
             "true_fix_rate_loose": _loose,
             "backup": os.path.relpath(_bak, ROOT),
             "sha256_before": _before_sha, "sha256_after": _after_sha},
            ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
