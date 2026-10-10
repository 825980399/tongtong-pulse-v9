# -*- coding: utf-8 -*-
"""主线第62批 T1 专项：记忆验证闭环首跑延迟语义 = 「推迟」（星轨裁决方案B）。

背景（第61批遗留问题）：
  第61批按任务书实现了 ``initial_delay_hours``，但语义做成了「**提前**」——
  ``_first_delay > 0`` 时首次只等 ``_first_delay``，把首跑从「启动后 6 小时」
  **提前**到「启动后 5~30 分钟」，与「重操作错峰避峰」的原意**正好相反**。

本批（星轨裁决方案B）修正为「**推迟**」：

  * ``initial_delay_hours > 0`` → 首跑 sleep = interval + delay
  * ``initial_delay_hours = 0`` → 首跑 sleep = interval（与改造前一致，零回归）
  * 第二轮起一律 = interval

验收（对应任务书 T1 验收标准 1~5）。
"""
import io
import os
import sys
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: E402

_SRC = io.open(os.path.join(_PROJECT_ROOT, "nucleus", "mnemosyne", "PulseNodePool.py"),
               encoding="utf-8").read()
_MAIN = io.open(os.path.join(_PROJECT_ROOT, "main.py"), encoding="utf-8").read()

_INTERVAL_H = 6.0
_INTERVAL_S = _INTERVAL_H * 3600.0          # 21600 秒


def _probe(initial_delay_hours, interval_hours=_INTERVAL_H):
    """用 sleep 探针记录记忆验证闭环的 sleep 时长序列（秒）。"""
    _p = PulseNodePool.__new__(PulseNodePool)
    _p._mem_verify_thread = None
    _p._mem_verify_logger = None
    _p.run_memory_verification = lambda stale_days=None: {}
    _p.purge_obsolete = lambda max_age_days=None: 0
    _calls = []
    _orig = time.sleep
    time.sleep = lambda _s: _calls.append(_s)
    try:
        _p.start_memory_verification_loop(
            interval_hours=interval_hours, initial_delay_hours=initial_delay_hours)
        _deadline = time.time() + 3
        while len(_calls) < 2 and time.time() < _deadline:
            pass
    finally:
        time.sleep = _orig
        try:
            _p.stop_memory_verification_loop()
        except Exception:
            pass
    return _calls


class TestPostponeSemantics(unittest.TestCase):
    """① 推迟语义：首跑 = interval + delay。"""

    def test_01_delay_lower_bound(self):
        """delay=300s（范围下界）→ 首跑 = 21600 + 300。"""
        _calls = _probe(300.0 / 3600.0)
        self.assertTrue(_calls, "闭环未启动")
        self.assertAlmostEqual(_calls[0], _INTERVAL_S + 300.0, delta=2.0)

    def test_02_delay_upper_bound(self):
        """delay=1800s（范围上界）→ 首跑 = 21600 + 1800。"""
        _calls = _probe(1800.0 / 3600.0)
        self.assertTrue(_calls, "闭环未启动")
        self.assertAlmostEqual(_calls[0], _INTERVAL_S + 1800.0, delta=2.0)

    def test_03_delay_zero_keeps_old_behavior(self):
        """delay=0 → 首跑 = interval（与第61批改造前完全一致，零回归）。"""
        _calls = _probe(0.0)
        self.assertTrue(_calls, "闭环未启动")
        self.assertAlmostEqual(_calls[0], _INTERVAL_S, delta=2.0)

    def test_04_direction_guard_must_be_later(self):
        """★方向守卫：首跑必须**晚于**原周期，绝不能早于（防退回「提前」语义）。"""
        _calls = _probe(600.0 / 3600.0)
        self.assertTrue(_calls, "闭环未启动")
        self.assertGreater(_calls[0], _INTERVAL_S,
                           "首跑早于原周期 → 语义又退回成「提前」了")

    def test_05_subsequent_runs_use_interval(self):
        """第二轮起恢复 interval（推迟量只影响第一轮）。"""
        _calls = _probe(600.0 / 3600.0)
        self.assertGreaterEqual(len(_calls), 2, "未观测到第二轮: {}".format(_calls))
        self.assertAlmostEqual(_calls[1], _INTERVAL_S, delta=2.0)

    def test_06_interval_change_still_respected(self):
        """换周期后推迟语义仍成立：首跑 = 新 interval + delay。"""
        _calls = _probe(600.0 / 3600.0, interval_hours=2.0)
        self.assertTrue(_calls, "闭环未启动")
        self.assertAlmostEqual(_calls[0], 2.0 * 3600.0 + 600.0, delta=2.0)


class TestSourceGuards(unittest.TestCase):
    """② 源码守卫：实现方式必须是「相加」，不是条件替换。"""

    def test_10_source_uses_addition(self):
        self.assertIn("_sleep_s = _interval + _first_delay", _SRC)

    def test_11_no_old_conditional_form(self):
        self.assertNotIn("_sleep_s = _first_delay if _first_delay > 0 else _interval", _SRC)

    def test_12_docstring_states_postpone(self):
        self.assertIn("首次等 interval + 该时长", _SRC)
        self.assertIn("第62批 T1/P1（星轨裁决方案B）", _SRC)


class TestGraySwitch(unittest.TestCase):
    """③ 灰度开关：关闭时行为与第61批改造前完全一致。"""

    def test_20_switch_wiring_present(self):
        self.assertIn("ENABLE_RANDOM_INITIAL_DELAY", _MAIN)
        self.assertIn("initial_delay_hours=_mv_delay_h", _MAIN)

    def test_21_off_branch_starts_from_zero(self):
        """关闭分支：_mv_delay_h 初值 0.0，且只在开关为真时才被赋值。"""
        self.assertIn("_mv_delay_h = 0.0", _MAIN)
        self.assertIn('if bool(getattr(config, "ENABLE_RANDOM_INITIAL_DELAY", True)):',
                      _MAIN)


if __name__ == "__main__":
    unittest.main()
