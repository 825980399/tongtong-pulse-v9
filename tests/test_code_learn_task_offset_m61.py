# -*- coding: utf-8 -*-
"""★主线第61批 T4/P2：重操作错峰调度（临时拉平）门控单测 —— T1。

覆盖：
  · 定期测试偏移 ∈ [0, code_learn_test_offset_max]
  · 代码审视偏移 ∈ [0, code_learn_review_offset_max]
  · 灰度开关关闭 → 两偏移均为 0（复现旧行为）
  · 偏移上界取自 config.TASK_OFFSET_CONFIG（不硬编码）
  · random.randint 实参 = 配置上界（证明配置真被消费）
  · 代码学习触发条件保持不变
  · 加偏移后三任务不再同点触发 + 反证（偏移=0 时确实重叠）

全部使用真实 PulseCodeLearner 实例与真实 _on_heartbeat；不触碰生产 data/。
"""
import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from organs.brain.PulseCodeLearner import PulseCodeLearner  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PCL = importlib.import_module("organs.brain.PulseCodeLearner")


class _FakeInfoField:
    """只记录提交，不真正执行（避免跑真实测试/学习任务）。"""

    def __init__(self):
        self.tasks = []

    def submit_adaptive_task(self, fn, task_name=None, priority=None):
        self.tasks.append(task_name)
        return True


class _FakeScheduler:
    def run_cycle(self, include_heavy=False):
        return {"total_passed": 0, "total_failed": 0, "light": {}, "heavy": {}}


class _FakeExecutor:
    def verify_submitted_patches(self):
        return {"total": 0}

    def verify_applied_patches(self):
        return {"total": 0}


class _FixedTempo:
    """固定 tempo=1.0，使 learn/review 间隔可确定（tempo 会乘 15/500，
    实测生产 tempo≈0.53 会把 learn 间隔 15→8，导致重叠点漂移）。
    """

    def get_background_tempo(self):
        return 1.0


def _mk_learner(test_off, review_off):
    _c = PulseCodeLearner()
    _c._test_heartbeat_offset = test_off
    _c._review_heartbeat_offset = review_off
    _c.info_field = _FakeInfoField()
    _c._periodic_test_scheduler = _FakeScheduler()
    _c._maybe_trigger_health_driven_evolution = lambda: None
    return _c


def _scan_simultaneous(learner, limit):
    """扫描 1..limit 心跳，返回「三个任务同点触发」的心跳点 → 任务名列表。

    ★固定 tempo=1.0：否则 learn/review 间隔随运行期 tempo 缩放，重叠点会漂移。
    """
    _hits = {}
    with mock.patch("nucleus.runtime_tempo.get_runtime_tempo", lambda: _FixedTempo()), \
            mock.patch("nucleus.reasoning.SafeEvolutionExecutor.SafeEvolutionExecutor",
                       _FakeExecutor):
        for _i in range(1, limit + 1):
            learner._heartbeat_count = _i - 1
            learner.info_field.tasks = []
            learner._on_heartbeat({})
            _set = set(learner.info_field.tasks)
            if len(_set) >= 3:
                _hits[_i] = sorted(_set)
    return _hits


class TestCodeLearnTaskOffset(unittest.TestCase):
    def setUp(self):
        self._old_cfg = dict(config.TASK_OFFSET_CONFIG)
        self._old_on = config.ENABLE_CODE_LEARN_TASK_OFFSET

    def tearDown(self):
        config.TASK_OFFSET_CONFIG.clear()
        config.TASK_OFFSET_CONFIG.update(self._old_cfg)
        config.ENABLE_CODE_LEARN_TASK_OFFSET = self._old_on

    # ---- 1. 偏移范围 ----
    def test_01_test_offset_within_range(self):
        """★定期测试偏移 ∈ [0, 99]（config 默认上界）。"""
        _c = PulseCodeLearner()
        self.assertGreaterEqual(_c._test_heartbeat_offset, 0)
        self.assertLessEqual(_c._test_heartbeat_offset, 99)

    def test_02_review_offset_within_range(self):
        """★代码审视偏移 ∈ [0, 499]（config 默认上界）。"""
        _c = PulseCodeLearner()
        self.assertGreaterEqual(_c._review_heartbeat_offset, 0)
        self.assertLessEqual(_c._review_heartbeat_offset, 499)

    # ---- 2. 灰度开关 ----
    def test_03_switch_off_zeros_offsets(self):
        """★灰度关闭 → 两偏移均为 0（复现「三任务同步触发」的旧行为）。"""
        config.ENABLE_CODE_LEARN_TASK_OFFSET = False
        _c = PulseCodeLearner()
        self.assertEqual(_c._test_heartbeat_offset, 0)
        self.assertEqual(_c._review_heartbeat_offset, 0)

    def test_03b_switch_on_same_instance_still_random(self):
        """★灰度开启（默认）→ 偏移为随机值（与关闭态形成对照）。"""
        config.ENABLE_CODE_LEARN_TASK_OFFSET = True
        _seen = set()
        for _ in range(5):
            _c = PulseCodeLearner()
            _seen.add((_c._test_heartbeat_offset, _c._review_heartbeat_offset))
        self.assertGreater(len(_seen), 1, "偏移未随机化: {}".format(_seen))

    # ---- 3. 配置化 ----
    def test_04_offset_bounds_from_config(self):
        """★偏移上界取自 config.TASK_OFFSET_CONFIG（代码不硬编码范围）。"""
        config.TASK_OFFSET_CONFIG["code_learn_test_offset_max"] = 3
        config.TASK_OFFSET_CONFIG["code_learn_review_offset_max"] = 7
        _t, _r = set(), set()
        for _ in range(8):
            _c = PulseCodeLearner()
            _t.add(_c._test_heartbeat_offset)
            _r.add(_c._review_heartbeat_offset)
        self.assertTrue(all(0 <= _v <= 3 for _v in _t), _t)
        self.assertTrue(all(0 <= _v <= 7 for _v in _r), _r)

    def test_05_randint_called_with_config_bounds(self):
        """★random.randint 的实参即配置上界（证明配置被真实消费）。"""
        _cap = []
        _real = _PCL.random.randint

        def _fake(a, b):
            _cap.append((a, b))
            return _real(a, b)

        with mock.patch.object(_PCL.random, "randint", _fake):
            PulseCodeLearner()
        self.assertIn((0, 99), _cap, "未按配置上界 99 生成定期测试偏移")
        self.assertIn((0, 499), _cap, "未按配置上界 499 生成代码审视偏移")
        self.assertIn((1, 499), _cap, "代码学习随机初始计数(1~499)被改动")

    # ---- 4. 代码学习保持不变 ----
    def test_06_learn_trigger_unchanged(self):
        """★代码学习触发条件未叠加偏移（任务书要求保持不变）。"""
        _src = open(
            os.path.join(_ROOT, "organs", "brain", "PulseCodeLearner.py"),
            encoding="utf-8",
        ).read()
        self.assertIn("if self._heartbeat_count % _learn_interval == 0:", _src)
        self.assertNotIn("_heartbeat_count + _m61_learn_off", _src)
        _c = PulseCodeLearner()
        self.assertTrue(1 <= _c._heartbeat_count <= 499)

    def test_06b_triggers_carry_offset(self):
        """★定期测试/代码审视的触发条件确实带上了各自偏移。"""
        _src = open(
            os.path.join(_ROOT, "organs", "brain", "PulseCodeLearner.py"),
            encoding="utf-8",
        ).read()
        self.assertIn(
            "(self._heartbeat_count + _m61_review_off) % _review_interval == 0", _src
        )
        self.assertIn(
            "(self._heartbeat_count + _m61_test_off) % self._test_run_interval != 0", _src
        )

    # ---- 5. 不同步（核心验收） ----
    def test_07_three_tasks_not_simultaneous(self):
        """★加偏移后，三任务不再在同一心跳点同时触发（真实 _on_heartbeat 扫描）。"""
        _c = _mk_learner(37, 211)
        _hits = _scan_simultaneous(_c, 3000)
        self.assertEqual(_hits, {}, "存在三任务同点触发: {}".format(_hits))

    def test_08_without_offset_they_overlap(self):
        """★反证（对照实验）：偏移=0 的旧行为下确实存在三任务同点触发。

        否则 test_07 可能只是「扫描窗口太短」，无法证明偏移真的起作用。
        lcm(15,100,500)=1500 → 第 1500 次心跳三任务同点。
        """
        _c = _mk_learner(0, 0)
        _hits = _scan_simultaneous(_c, 1500)
        self.assertIn(1500, _hits, "旧行为下第1500次心跳本应三者同点: {}".format(_hits))


if __name__ == "__main__":
    unittest.main(verbosity=2)
