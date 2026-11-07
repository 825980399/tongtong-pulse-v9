# -*- coding: utf-8 -*-
"""474 刀24：分层背压「降级放行」绝对硬顶的门控单测。

用 `object.__new__(InfoField)` + 假线程池**直接测生产方法**，不重写判据，
避免「测试里的简化实现碰巧是绿的」这种自欺（铁律 30/35）。
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config
from nucleus.field import InfoField as _IF_MOD
from nucleus.field.InfoField import InfoField, _m474_hard_cap_multiplier

_IF_SRC = os.path.join(_ROOT, "nucleus", "field", "InfoField.py")


class _Queue:
    def __init__(self, depth):
        self.depth = depth

    def qsize(self):
        return self.depth


class _Pool:
    def __init__(self, depth):
        self._work_queue = _Queue(depth)


def _field():
    """构造只带 `_admit_layer_task` 所需属性的最小实例（不跑 __init__）。"""
    _f = object.__new__(InfoField)
    _f._layer_queue_limits = {"L0": 0, "L1": 500, "L2": 300, "L3": 300}
    _f._overflow_priority_allow = 3
    _f._layer_overflow_allowed = {}
    _f._layer_rejected_count = {}
    return _f


def _admit(depth, layer="L2", priority=1):
    with mock.patch.object(_IF_MOD, "_module_logger") as _log:
        _f = _field()
        _ok = _f._admit_layer_task(layer, _Pool(depth), "单测", priority)
        return _ok, _f, _log


class TestHardCap(unittest.TestCase):
    """硬顶：只拦极端深度，日常语义不变。"""

    def test_01_real_production_case_is_now_rejected(self):
        """实测现场：L2 深度 2588/上限 300、priority=1，旧逻辑放行 2300 次。"""
        _ok, _f, _ = _admit(2588, "L2", 1)
        self.assertFalse(_ok)
        self.assertEqual(1, _f._layer_rejected_count.get("L2"))

    def test_02_counterproof_switch_off_restores_old_behavior(self):
        """★反证：关掉开关后同一输入必须放行，否则判据无判别力。"""
        with mock.patch.object(config, "ENABLE_M474_OVERFLOW_HARD_CAP", False, create=True):
            _ok, _f, _ = _admit(2588, "L2", 1)
        self.assertTrue(_ok)
        self.assertEqual(0, _f._layer_rejected_count.get("L2", 0))

    def test_03_below_hard_cap_keeps_priority_overflow(self):
        _ok, _f, _ = _admit(1199, "L2", 1)
        self.assertTrue(_ok)
        self.assertEqual(1, _f._layer_overflow_allowed.get("L2"))

    def test_04_exact_boundary_is_rejected(self):
        _ok, _, _ = _admit(1200, "L2", 1)
        self.assertFalse(_ok)

    def test_05_l0_lifeline_never_affected(self):
        _ok, _f, _ = _admit(99999, "L0", 1)
        self.assertTrue(_ok)
        self.assertEqual(0, _f._layer_rejected_count.get("L0", 0))

    def test_06_l1_soft_zone_still_allows_low_priority(self):
        _ok, _f, _ = _admit(600, "L1", 9)
        self.assertTrue(_ok)
        self.assertEqual(0, _f._layer_rejected_count.get("L1", 0))

    def test_07_l1_above_soft_but_below_hard_keeps_overflow(self):
        _ok, _f, _ = _admit(1500, "L1", 1)
        self.assertTrue(_ok)
        self.assertEqual(1, _f._layer_overflow_allowed.get("L1"))

    def test_08_l1_hard_cap_rejects(self):
        _ok, _, _ = _admit(2000, "L1", 1)
        self.assertFalse(_ok)

    def test_09_rejected_counter_increments(self):
        _f = _field()
        with mock.patch.object(_IF_MOD, "_module_logger"):
            for _ in range(3):
                self.assertFalse(
                    _f._admit_layer_task("L2", _Pool(1300), "单测", 1))
        self.assertEqual(3, _f._layer_rejected_count["L2"])

    def test_10_log_is_throttled(self):
        _f = _field()
        with mock.patch.object(_IF_MOD, "_module_logger") as _log:
            for _ in range(3):
                _f._admit_layer_task("L2", _Pool(1300), "单测", 1)
        self.assertEqual(1, _log.warning.call_count, "每 100 次才打一条")


class TestHardCapConfig(unittest.TestCase):
    """配置：None=缺省、0=显式关、异常 fail-open。"""

    def test_11_default_is_four(self):
        with mock.patch.object(config, "ENABLE_M474_OVERFLOW_HARD_CAP", True, create=True), \
             mock.patch.object(config, "M474_OVERFLOW_HARD_MULTIPLIER", None, create=True):
            self.assertEqual(4, _m474_hard_cap_multiplier())

    def test_12_explicit_false_returns_zero(self):
        with mock.patch.object(config, "ENABLE_M474_OVERFLOW_HARD_CAP", 0, create=True):
            self.assertEqual(0, _m474_hard_cap_multiplier())

    def test_13_multiplier_has_floor_of_two(self):
        with mock.patch.object(config, "M474_OVERFLOW_HARD_MULTIPLIER", 1, create=True):
            self.assertEqual(2, _m474_hard_cap_multiplier())

    def test_14_invalid_value_fails_open(self):
        with mock.patch.object(config, "M474_OVERFLOW_HARD_MULTIPLIER", "abc", create=True):
            self.assertEqual(0, _m474_hard_cap_multiplier())

    def test_15_custom_multiplier_changes_boundary(self):
        with mock.patch.object(config, "M474_OVERFLOW_HARD_MULTIPLIER", 2, create=True):
            _ok, _, _ = _admit(600, "L2", 1)
        self.assertFalse(_ok)


class TestHardCapContract(unittest.TestCase):
    """契约：硬顶必须在降级放行之前；L0 必须仍是最先返回。"""

    def test_16_hard_cap_precedes_overflow_in_source(self):
        with open(_IF_SRC, encoding="utf-8") as _fh:
            _src = _fh.read()
        _fn = next(_n for _n in ast.walk(ast.parse(_src))
                   if isinstance(_n, ast.FunctionDef) and _n.name == "_admit_layer_task")
        _unparsed = ast.unparse(_fn)
        self.assertLess(_unparsed.find("_hard_mult"),
                        _unparsed.find("_overflow_priority_allow"))
        self.assertIn("_hard_mult", _unparsed)

    def test_17_config_documents_rollback(self):
        with open(os.path.join(_ROOT, "config.py"), encoding="utf-8") as _fh:
            _src = _fh.read()
        self.assertIn("ENABLE_M474_OVERFLOW_HARD_CAP = True", _src)
        self.assertIn("False ⇒ 逐字回滚", _src)
        self.assertIn("不调整队列上限数字", _src)


if __name__ == "__main__":
    unittest.main()
