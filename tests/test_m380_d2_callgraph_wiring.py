# -*- coding: utf-8 -*-
"""★380 刀2 · CallGraph 接线到自检轮次（P1）

379 刀1 的 `_build_pulse_call_chain` 建好后只有单测在调 ⇒ 本刀挂进 DailyScheduler.run_once。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.self_awareness import DailyScheduler as _ds  # noqa: E402


def test_m380_d2_snapshot_has_five_keys():
    _s = _ds._m380_pulse_snapshot(write=False)
    for _k in ("chain_path", "chain_stats", "orphans", "dead_letters", "separated"):
        assert _k in _s, _k
    assert _s["error"] == "" or isinstance(_s["error"], str)


def test_m380_d2_chain_stats_non_empty():
    """真实仓库上必须扫到发布点（14 处量级），否则说明接线是空转。"""
    _s = _ds._m380_pulse_snapshot(write=False)
    assert _s["chain_stats"].get("total_sites", 0) > 0, _s


def test_m380_d2_no_merged_total():
    """死信与孤儿必须分开（`separated=True`），不许出现合并总数。"""
    _s = _ds._m380_pulse_snapshot(write=False)
    assert _s["separated"] is True


def test_m380_d2_hooked_into_run_once():
    import io
    with io.open(os.path.join(ROOT, "nucleus", "self_awareness",
                              "DailyScheduler.py"), encoding="utf-8") as _f:
        _src = _f.read()
    assert "_m380_pulse_snapshot(write=True)" in _src
    assert "_res[\"pulse_snapshot\"]" in _src
