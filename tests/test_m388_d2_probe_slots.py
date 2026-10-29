# -*- coding: utf-8 -*-
"""388刀2：tracemalloc 槽位加密 + 采样成本护栏。

两件事：
  · 槽位从每天 2 次(5/13) 加密到**每 2 小时**（387刀2 结论：密度远远不够）
  · ★补上 387刀3 开的漏洞：gc 截断成了"万能放行符"，让一次 837s 的采样也写盘
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from organs.core.PulseMetricsCollector import PulseMetricsCollector as C  # noqa: E402

SRC = os.path.join(ROOT, "organs", "core", "PulseMetricsCollector.py")


def test_m388_d2_slots_every_two_hours():
    """★核心：槽位 = 每 2 小时（12 个），不再是 (5, 13)。"""
    assert len(C._M339_PROBE_HOURS) == 12, C._M339_PROBE_HOURS
    assert C._M339_PROBE_HOURS == tuple(range(0, 24, 2))
    assert 13 not in C._M339_PROBE_HOURS, "不再是 13 点单槽"
    assert 12 in C._M339_PROBE_HOURS


def test_m388_d2_all_slots_are_even_hours():
    """槽位全是偶数小时 ⇒ 任意时刻距下次采样不超过 2 小时。"""
    for _h in C._M339_PROBE_HOURS:
        assert _h % 2 == 0, _h
        assert 0 <= _h <= 23, _h


def test_m388_d2_cost_guardrails_in_place():
    """★加密必须有护栏：gc 预算(2s) < 超时线(3s)（386刀3 的梯度不能破）。"""
    assert C._M339_GC_SCAN_BUDGET_SEC < C._M339_PROBE_MAX_SEC
    assert float(C._M339_PROBE_MAX_SEC) == 3.0


def test_m388_d2_tm_over_budget_blocks_writing():
    """★核心（补 387漏洞）：tracemalloc 阶段超预算 ⇒ 不放行写盘。

    13:00 实测 elapsed=837s，其中 gc 只占约 2s（被预算截断），835s 是 tracemalloc
    ⇒ gc 截断不该成为整份采样的放行符。
    """
    _s = io.open(SRC, encoding="utf-8").read()
    assert "_m388_tm_sec" in _s, "必须记录 tracemalloc 阶段耗时"
    assert "_tm_over" in _s, "必须有 tracemalloc 超预算判定"
    assert "not _tm_over" in _s, "放行条件必须排除 tracemalloc 超时"


def test_m388_d2_gc_only_truncation_still_writes():
    """★不误伤：gc 截断而 tracemalloc 不超时 ⇒ 仍写盘（386刀3 的原意保留）。

    放行条件应是「gc 截断 且 写盘开关开 且 超时 且 **非** tracemalloc 超时」——
    四个条件的合取，少一个都不行。
    """
    _s = io.open(SRC, encoding="utf-8").read()
    _i = _s.index("if _trunc and _write_trunc and _elapsed > _maxsec")
    _cond = _s[_i:_i + 120]
    for _part in ("_trunc", "_write_trunc", "_elapsed > _maxsec", "not _tm_over"):
        assert _part in _cond, "放行条件缺 %s" % _part


def test_m388_d2_tm_sec_recorded_before_gc_phase():
    """tracemalloc 耗时必须在 **gc 阶段之前** 记录（否则记的是总耗时，判据失效）。"""
    _s = io.open(SRC, encoding="utf-8").read()
    _i_rec = _s.index("self._m388_tm_sec = time.perf_counter() - _t0")
    _i_gc = _s.index("if _heap_hi or _mode == \"gc_objects\":", _i_rec)
    assert _i_rec < _i_gc, "记录点必须在 gc 阶段之前"


def test_m388_d2_why_encrypted_is_documented():
    """★理由留证：为什么加密（一天 2 次不够）以及为什么要有护栏（采样 14 分钟）。"""
    _s = io.open(SRC, encoding="utf-8").read()
    assert "388刀2" in _s
    _i = _s.index("★388刀2：槽位从")
    _blk = _s[_i:_i + 700]
    assert "14 分钟" in _blk or "837" in _blk, "应写明实测的采样成本"
