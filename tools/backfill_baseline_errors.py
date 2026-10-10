# -*- coding: utf-8 -*-
"""进化验证基线离线回填（主线第41批 T1 / P0-263）。

背景
----
56 个已应用补丁的 ``baseline_errors`` 恒为 0（"验证空转"），根因是
``SafeEvolutionExecutor._count_errors_for_location`` 的判据要求
「同一行同时含**文件名**与**方法名**」；而框架日志格式为
``[模块名] 级别: 消息``（如 ``[渠道] test-ch 调用失败: TimeoutError: ...``），
**从不输出方法名** → 判据结构性恒 false。
实测：56 个已应用补丁位置在全量日志中合计命中 **0** 条。

本工具用**修正后的判据**（第41批 T1）对历史补丁**离线重算**基线错误数：

* **不重新应用补丁**、不触发任何 apply/verify/rollback —— 纯读日志 + 写历史文件；
* 写回 ``baseline_errors``（顶层）与 ``runtime_verify_result.baseline``，
  并按 ``(baseline - post) / max(baseline, 1)`` 重算 ``effectiveness``；
* 幂等：已回填（含 ``baseline_backfilled_at``）的条目**跳过**；
* 执行前自动备份原文件为 ``<hist>.bak_m41_baseline``。

用法
----
    python tools/backfill_baseline_errors.py            # 执行回填（自动备份）
    python tools/backfill_baseline_errors.py --dry-run  # 只报告，不写盘
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import time
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_HIST = os.path.join(_ROOT, "data", "patches", "patch_history.json")


def _load_executor() -> Any:
    """轻量实例（绕 ``__init__``）：只需 ``_project_root`` + 判据方法。"""
    from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
    _e = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
    _e._project_root = _ROOT
    return _e


def _recompute(executor: Any, patch: dict) -> tuple[int, int]:
    """重算 (baseline, after_fix)。窗口 = 修复前 N 天 / 修复后至今。"""
    _file = patch.get("file", "") or ""
    _method = patch.get("method", "") or ""
    _fixed_at = float(patch.get("fixed_at", 0) or 0)
    if not _file:
        return 0, 0
    # 基线：修复前 N 天窗口（与线上新逻辑一致）
    _win_since = executor._m41_baseline_since()
    if _win_since and _win_since < _fixed_at:
        _baseline = executor._count_errors_for_location(
            _file, _method, since=_win_since, until=_fixed_at)
    else:
        _baseline = executor._count_errors_for_location(_file, _method, since=0)
    _after = executor._count_errors_for_location(
        _file, _method, since=_fixed_at) if _fixed_at else 0
    return int(_baseline), int(_after)


def _effectiveness(baseline: int, after: int) -> float:
    if baseline > 0:
        return round(max(0.0, min(1.0, 1.0 - after / baseline)), 2)
    return 1.0 if after == 0 else 0.5


def main(argv: list[str]) -> int:
    _dry = "--dry-run" in argv
    if not os.path.isfile(_HIST):
        print("[FAIL] 补丁历史不存在:", _HIST)
        return 1
    _data = json.load(io.open(_HIST, encoding="utf-8", errors="replace"))
    if not isinstance(_data, list):
        print("[FAIL] 补丁历史顶层非 list")
        return 1

    _ex = _load_executor()
    _applied = [p for p in _data if isinstance(p, dict) and p.get("applied")]
    _before_zero = sum(1 for p in _applied
                       if not int(p.get("baseline_errors", 0) or 0))
    _before_eff = [float((p.get("runtime_verify_result") or {}).get("effectiveness", 0) or 0)
                   for p in _applied]

    _changed = 0
    _positive = 0
    _skipped = 0
    _rows = []
    for _p in _applied:
        if _p.get("baseline_backfilled_at"):
            _skipped += 1
            _positive += 1 if int(_p.get("baseline_errors", 0) or 0) > 0 else 0
            continue
        _b, _a = _recompute(_ex, _p)
        _eff = _effectiveness(_b, _a)
        _rows.append((_p.get("file", ""), _p.get("method", ""), _b, _a, _eff))
        if _b != int(_p.get("baseline_errors", 0) or 0):
            _changed += 1
        if _b > 0:
            _positive += 1
        if not _dry:
            _p["baseline_errors"] = _b
            _p["post_apply_errors"] = _a
            _p["baseline_backfilled_at"] = time.time()
            _vr = _p.get("runtime_verify_result")
            if isinstance(_vr, dict):
                _vr["baseline"] = _b
                _vr["after_fix"] = _a
                _vr["effectiveness"] = _eff

    _after_eff = [float((p.get("runtime_verify_result") or {}).get("effectiveness", 0) or 0)
                  for p in _applied]

    print("=" * 74)
    print("进化验证基线离线回填报告（第41批 T1 / P0-263）")
    print("=" * 74)
    print("历史总条数            : %d" % len(_data))
    print("applied 条数          : %d" % len(_applied))
    print("回填前 baseline==0    : %d / %d" % (_before_zero, len(_applied)))
    print("回填后 baseline>0     : %d / %d  (%.1f%%)"
          % (_positive, len(_applied),
             100.0 * _positive / max(1, len(_applied))))
    print("本次改变条数           : %d（跳过已回填 %d）" % (_changed, _skipped))
    _bavg = sum(_before_eff) / max(1, len(_before_eff))
    _aavg = sum(_after_eff) / max(1, len(_after_eff))
    print("avg_effectiveness 前→后: {:.4f} → {:.4f}".format(_bavg, _aavg))
    print()
    if _rows:
        print("--- 重算明细（非零基线优先）---")
        _rows.sort(key=lambda x: -x[2])
        for _f, _m, _b, _a, _e in _rows[:25]:
            print("  baseline=%-3d after=%-3d eff=%.2f  %s.%s"
                  % (_b, _a, _e, os.path.basename(_f), _m))

    if _dry:
        print()
        print("[DRY-RUN] 未写盘。")
        return 0

    _bak = _HIST + ".bak_m41_baseline"
    if not os.path.isfile(_bak):
        shutil.copy2(_HIST, _bak)
        print()
        print("已备份原文件 →", os.path.basename(_bak))
    with io.open(_HIST, "w", encoding="utf-8") as _f:
        json.dump(_data, _f, ensure_ascii=False, indent=2)
    print("已写回:", _HIST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
