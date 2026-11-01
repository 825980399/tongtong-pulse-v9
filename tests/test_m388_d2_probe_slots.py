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


def test_m388_d2_slots_density_intent_kept_by_gc_not_tracemalloc():
    """★389刀1 更正：**tracemalloc 重采**不得加密（388 这条断言被实测推翻）。

    388刀2 的原始断言是"12 个偶数槽"。两条证据把它推翻：
      ① `config.py::M339_MEMORY_PROBE_HOURS=(5,13)` 才是生产生效值
         （`_m339_cfg_get` 优先读 config）⇒ 12 槽从未生效，只让默认值骗人；
      ② 真生效会更糟：tracemalloc 单次实测 **837s**（3300 万对象做 statistics），
         12 槽 = 每 2 小时卡进程 14 分钟。
    "密度不够"的诉求改由 **gc 轻采**（2-3s，每小时已在跑）承担。
    """
    import config as _cfg
    _h = tuple(C._M339_PROBE_HOURS)
    assert len(_h) <= 3, _h
    assert _h == tuple(int(_x) for _x in _cfg.M339_MEMORY_PROBE_HOURS), _h
    # ★418 同步（第416批契约变更）：416 刀2 把 tracemalloc 重采**收敛为单槽 05:00**
    #   —— 实测单次 289~1805s，多槽等于一天内多次把进程卡住 —— config 随之改为 `(5,)`。
    #   原「13 点必须还在」写于 389 刀1，已被 416 的实测结论推翻 ⇒ 同步为单槽。
    #   「采样密度不够」的诉求改由 gc 轻采（2~3s、每小时）承担，不靠 tracemalloc。
    assert _h == (5,), _h


def test_m388_d2_all_slots_are_valid_hours():
    """槽位必须是合法小时（不校验"每 2 小时"，见上条）。"""
    for _h in C._M339_PROBE_HOURS:
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
