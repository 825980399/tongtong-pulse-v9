# -*- coding: utf-8 -*-
"""★主线第61批 T4/P2：重操作错峰调度门控单测 —— T2 独立线程任务随机首跑延迟。

覆盖：
  · 参数补丁首跑延迟（60~240 秒区间）真实生效
  · 记忆验证首跑延迟（300~1800 秒区间）真实生效
  · 自主进化首跑延迟（1200~2400 秒区间）—— 真实源码切片 exec
  · 灰度开关关闭 → 固定延迟（参数补丁 0s / 自主进化 1800s / 记忆验证 0s）
  · 三处生产调用点确实接线（防「只造轮子没装上车」）

全部走真实方法与真实源码切片；不启动框架、不写生产 data/。
"""
import os
import random
import sys
import textwrap
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.evolution.ParamPatchManager import ParamPatchManager  # noqa: E402
from nucleus.mnemosyne.PulseNodePool import PulseNodePool  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = os.path.join(_ROOT, "main.py")


def _mk_ppm():
    """轻量实例（绕 __init__），并把循环体换成 no-op，避免真跑线程逻辑。"""
    _m = ParamPatchManager.__new__(ParamPatchManager)
    _m._auto_apply_running = False
    _m._auto_apply_thread = None
    _m._auto_apply_interval = 300
    _m._last_auto_apply = 0.0
    _m._auto_apply_loop = lambda: None
    return _m


def _probe_nodepool(initial_delay_hours):
    """用 sleep 探针记录记忆验证闭环的**首次 sleep 时长**（秒）。"""
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
            interval_hours=6.0, initial_delay_hours=initial_delay_hours
        )
        _deadline = time.time() + 3
        while not _calls and time.time() < _deadline:
            pass
    finally:
        time.sleep = _orig
        try:
            _p.stop_memory_verification_loop()
        except Exception:
            pass
    return _calls


class _FakeLevel:
    DEBUG = 10


class _FakeSelf:
    def __init__(self):
        self.msgs = []

    def _log(self, *a, **k):
        self.msgs.append(a)


def _evo_delay_from_source(switch_on):
    """从 main.py **真实源码**切出自主进化首跑延迟决策块并 exec（非复刻逻辑）。"""
    _src = open(_MAIN, encoding="utf-8").read()
    _key = '_m61_evo_on = bool(getattr(config, "ENABLE_RANDOM_INITIAL_DELAY", True))'
    _i = _src.index(_key)
    _i = _src.rindex("\n", 0, _i) + 1          # ★回到行首，保留统一缩进供 dedent
    _j = _src.index("\n", _src.index("else 1800.0)", _i))
    _blk = textwrap.dedent(_src[_i:_j])
    _old = config.ENABLE_RANDOM_INITIAL_DELAY
    config.ENABLE_RANDOM_INITIAL_DELAY = switch_on
    _env = {
        "config": config,
        "_rd61e": random,
        "self": _FakeSelf(),
        "LogLevel": _FakeLevel,
    }
    try:
        exec(compile(_blk, "<main.py evo-delay slice>", "exec"), _env)
    finally:
        config.ENABLE_RANDOM_INITIAL_DELAY = _old
    return _env


class TestRandomInitialDelay(unittest.TestCase):
    def setUp(self):
        self._old_on = config.ENABLE_RANDOM_INITIAL_DELAY

    def tearDown(self):
        config.ENABLE_RANDOM_INITIAL_DELAY = self._old_on

    # ---- 1. 配置范围（60~240 / 1200~2400 / 300~1800）----
    def test_01_delay_ranges_in_config(self):
        """★三个任务的延迟范围都在 config.TASK_OFFSET_CONFIG 中（不硬编码）。"""
        _c = config.TASK_OFFSET_CONFIG
        self.assertEqual(_c["param_patch_initial_delay_min"], 60)
        self.assertEqual(_c["param_patch_initial_delay_max"], 240)
        self.assertEqual(_c["evolution_initial_delay_min"], 1200)
        self.assertEqual(_c["evolution_initial_delay_max"], 2400)
        self.assertEqual(_c["memory_verify_initial_delay_min"], 300)
        self.assertEqual(_c["memory_verify_initial_delay_max"], 1800)

    # ---- 2. 参数补丁 ----
    def test_02_param_patch_delay_applied(self):
        """★参数补丁首跑延迟真实生效：elapsed = interval - initial_delay。"""
        _m = _mk_ppm()
        try:
            _m.start_auto_apply(interval=300, initial_delay=60)
            _el = time.time() - _m._last_auto_apply
            self.assertAlmostEqual(_el, 240.0, delta=3.0)  # 300-60
        finally:
            _m.stop_auto_apply()

    def test_02b_param_patch_delay_upper_bound(self):
        """★取范围上界 240s → 首跑仅剩 60s。"""
        _m = _mk_ppm()
        try:
            _m.start_auto_apply(interval=300, initial_delay=240)
            _el = time.time() - _m._last_auto_apply
            self.assertAlmostEqual(_el, 60.0, delta=3.0)
            self.assertLess(_el, 300.0, "首跑未提前，仍是立即触发")
        finally:
            _m.stop_auto_apply()

    def test_03_param_patch_delay_zero_immediate(self):
        """★initial_delay=0 → 立即首跑（与改造前完全等价，零回归）。"""
        _m = _mk_ppm()
        try:
            _m.start_auto_apply(interval=300)
            _el = time.time() - _m._last_auto_apply
            self.assertAlmostEqual(_el, 300.0, delta=3.0)
            self.assertGreaterEqual(_el, 300.0, "delay=0 时应立即触发")
        finally:
            _m.stop_auto_apply()

    def test_04_start_auto_apply_signature(self):
        """★signature 锁死（防将来有人删掉 initial_delay 形参）。"""
        import inspect
        _sig = inspect.signature(ParamPatchManager.start_auto_apply)
        self.assertIn("initial_delay", _sig.parameters)

    # ---- 3. 记忆验证 ----
    def test_05_memory_verify_delay_lower(self):
        """★记忆验证首跑【推迟】300 秒（范围下界）→ 首跑 = 6h + 300s = 21900s。

        ★主线第62批 T1/P1（星轨裁决方案B）：语义由「提前」改为「推迟」。
          旧断言（第61批）：首跑 == 300（等于把首跑提前到启动后5分钟）
          新断言（本批）：首跑 == 21600 + 300（在原 6h 周期基础上再推迟 5 分钟）
        """
        _calls = _probe_nodepool(300.0 / 3600.0)
        self.assertTrue(_calls, "闭环未启动")
        self.assertAlmostEqual(_calls[0], 21600.0 + 300.0, delta=2.0)

    def test_05b_memory_verify_delay_upper(self):
        """★记忆验证首跑【推迟】1800 秒（范围上界）→ 首跑 = 6h + 1800s = 23400s。"""
        _calls = _probe_nodepool(1800.0 / 3600.0)
        self.assertTrue(_calls, "闭环未启动")
        self.assertAlmostEqual(_calls[0], 21600.0 + 1800.0, delta=2.0)

    def test_06_memory_verify_zero_keeps_old_behavior(self):
        """★initial_delay_hours=0 → 首次仍等一个完整周期（6h=21600s，旧行为）。"""
        _calls = _probe_nodepool(0.0)
        self.assertTrue(_calls, "闭环未启动")
        self.assertAlmostEqual(_calls[0], 21600.0, delta=2.0)

    def test_06b_memory_verify_subsequent_uses_interval(self):
        """★首跑延迟只影响第一轮，其后恢复 interval（6h）——不改变任务周期。"""
        _calls = _probe_nodepool(600.0 / 3600.0)
        self.assertGreaterEqual(len(_calls), 2, "未观测到第二轮: {}".format(_calls))
        # ★第62批 T1：首跑推迟 → 21600 + 600
        self.assertAlmostEqual(_calls[0], 21600.0 + 600.0, delta=2.0)
        # 第二轮起恢复原周期（未受推迟影响）
        self.assertAlmostEqual(_calls[1], 21600.0, delta=2.0)

    def test_07_memory_loop_signature(self):
        """★signature 锁死 initial_delay_hours。"""
        import inspect
        _sig = inspect.signature(PulseNodePool.start_memory_verification_loop)
        self.assertIn("initial_delay_hours", _sig.parameters)

    # ---- 4. 自主进化（真实源码切片）----
    def test_08_evolution_delay_in_range_when_on(self):
        """★开关开启 → 首跑延迟 ∈ [1200, 2400] 秒。"""
        for _ in range(5):
            _env = _evo_delay_from_source(True)
            _d = _env["_m61_evo_next"]
            self.assertGreaterEqual(_d, 1200.0)
            self.assertLessEqual(_d, 2400.0)

    def test_09_evolution_delay_fixed_when_off(self):
        """★开关关闭 → 首跑延迟恒为 1800 秒（固定，复现旧行为）。"""
        _env = _evo_delay_from_source(False)
        self.assertEqual(_env["_m61_evo_next"], 1800.0)
        self.assertEqual(_env["_m61_evo_on"], False)

    def test_09b_evolution_would_still_hit_180_beat_when_off(self):
        """★反证：关闭开关时首跑恰为 1800s（30min@1s/beat=第180次心跳，即重叠点）。"""
        _env = _evo_delay_from_source(False)
        self.assertEqual(int(_env["_m61_evo_next"]) // 10, 180)

    # ---- 5. 接线（生产调用点）----
    def test_10_main_wiring_present(self):
        """★三处生产调用点确实接线（防「只造轮子没装上车」）。"""
        _src = open(_MAIN, encoding="utf-8").read()
        self.assertIn("start_auto_apply(interval=300, initial_delay=_pp_delay)", _src)
        self.assertIn("initial_delay_hours=_mv_delay_h", _src)
        self.assertIn("_time.sleep(_m61_evo_next)", _src)
        self.assertNotIn("_time.sleep(1800)  # 30分钟", _src,
                         "旧的固定 1800s 首跑仍然存在")

    def test_11_switch_guards_present(self):
        """★三处都受 ENABLE_RANDOM_INITIAL_DELAY 守护（关闭=固定延迟）。"""
        _src = open(_MAIN, encoding="utf-8").read()
        self.assertGreaterEqual(
            _src.count('ENABLE_RANDOM_INITIAL_DELAY'), 3,
            "开关接线点少于 3 处")
        self.assertIn('_pp_delay = 0.0', _src)
        self.assertIn('_mv_delay_h = 0.0', _src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
