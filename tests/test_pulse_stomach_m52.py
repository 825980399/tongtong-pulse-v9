# -*- coding: utf-8 -*-
"""第52批 T2（P1-370 部分）门控测试：PulseStomach（胃）单元测试。

覆盖 4 类（初始化 / 核心方法 / 异常降级 / 边界），共 24 例。
★mock 隔离外部依赖（节点池 / 知识树 / 频段编码器）—— **不写生产数据**。

胃的职责：知识消化（安全审查 → 伦理审查 → 关键词抽取 → 入库）、
拒绝出口事件（第18批 T7）。

★本批新增发现的**健壮性现状**（见交付报告 / P2-371）
--------------------------------------------------
单元测试暴露 3 处输入/依赖未防护（**当前行为**已用断言固化，修复时应同步更新）：

1. ``_is_safe_knowledge(None)`` → ``TypeError``（无类型防护）
2. ``on_pulse({"payload": None})`` → ``AttributeError``（无 payload 防护）
3. ``on_pulse({"event_type": "digest.knowledge", "payload": {"content": ...}})``
   在 ``node_pool`` **未注入** 时 → ``AttributeError: 'NoneType' object has no attribute 'query'``
"""
import os
import sys
import threading
import unittest
from unittest import mock

import config  # noqa: E402  ★第54批 T5：灰度开关用例需要

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.body.PulseStomach import PulseStomach  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.o = PulseStomach(organ_name="胃")
        # ★注入 mock 依赖（隔离外部依赖）
        self.o.set_node_pool(mock.MagicMock())
        self.o.set_knowledge_tree(mock.MagicMock())
        self.o.set_frequency_codec(mock.MagicMock())


class TestInit(_Base):
    """初始化。"""

    def test_01_organ_name(self):
        self.assertEqual(self.o.organ_name, "胃")

    def test_02_default_organ_name(self):
        self.assertTrue(PulseStomach().organ_name)

    def test_03_custom_organ_name(self):
        self.assertEqual(PulseStomach(organ_name="X").organ_name, "X")

    def test_04_log_callable(self):
        """基类提供日志入口（不得缺失）。"""
        self.assertTrue(callable(getattr(self.o, "_log", None)))

    def test_05_stats_has_organ_key(self):
        self.assertEqual(self.o.get_stats().get("organ"), "胃")

    def test_06_raw_construction_has_no_deps(self):
        """裸构造（不注入）时依赖属性存在但可为 None。"""
        _raw = PulseStomach(organ_name="胃")
        self.assertTrue(hasattr(_raw, "node_pool"))


class TestCoreMethods(_Base):
    """核心方法。"""

    def test_10_get_stats_shape(self):
        _st = self.o.get_stats()
        self.assertIsInstance(_st, dict)
        for _k in ("organ", "digested_count", "rejected_count"):
            self.assertIn(_k, _st)

    def test_11_stats_counters_numeric(self):
        _st = self.o.get_stats()
        for _k in ("digested_count", "rejected_count",
                   "ethics_local_passed", "ethics_pulse_used"):
            self.assertIsInstance(_st[_k], int)

    def test_12_resonance_conditions_shape(self):
        _rc = self.o.get_resonance_conditions()
        self.assertIsInstance(_rc, list)
        self.assertTrue(_rc, "应至少有一个订阅条件")
        for _c in _rc:
            self.assertIn("organ_name", _c)
            self.assertIn("event_types", _c)

    def test_13_resonance_organ_name_matches(self):
        """★实测：订阅条件的 organ_name 是注册名（'胃'），非类名。"""
        _rc = self.o.get_resonance_conditions()
        self.assertEqual(_rc[0]["organ_name"], self.o.organ_name)

    def test_14_resonance_event_types_non_empty(self):
        _rc = self.o.get_resonance_conditions()
        self.assertTrue(_rc[0]["event_types"])

    def test_15_refresh_runtime_params_no_raise(self):
        """刷新运行时参数**不得抛异常**（config 不可用时降级）。"""
        self.o.refresh_runtime_params()

    def test_16_refresh_applies_config(self):
        """注入 RUNTIME_PARAMS → 参数被应用（有该属性时）。"""
        with mock.patch.dict("config.RUNTIME_PARAMS",
                             {"stomach_keyword_min_length": 7}, clear=False):
            self.o.refresh_runtime_params()
        if hasattr(self.o, "_keyword_min_length"):
            self.assertEqual(self.o._keyword_min_length, 7)

    def test_17_safety_blocks_blacklist_word(self):
        """安全审查：**真实黑名单首个词**应被拒（自适应，不硬编码）。"""
        _kws = getattr(self.o, "_unsafe_keywords", None) or []
        if not _kws:
            self.skipTest("无黑名单")
        self.assertFalse(self.o._is_safe_knowledge("包含 %s 的内容" % _kws[0]))

    def test_18_safety_allows_normal(self):
        self.assertTrue(self.o._is_safe_knowledge("今天天气不错"))

    def test_19_safety_rule_discovery_passthrough(self):
        """规律发现内容直接放行（显式白名单）。"""
        self.assertTrue(self.o._is_safe_knowledge("[规律发现] 某规律"))

    def test_20_on_pulse_status_request_returns_dict(self):
        _r = self.o.on_pulse({"event_type": "system.status.request",
                              "payload": {}})
        self.assertIsInstance(_r, dict)

    def test_21_on_pulse_digest_knowledge(self):
        """知识消化事件应返回 dict（status 字段）。"""
        _r = self.o.on_pulse({"event_type": "digest.knowledge",
                              "payload": {"content": "测试内容" * 6}})
        self.assertIsInstance(_r, dict)
        self.assertIn("status", _r)


class TestDegradation(_Base):
    """异常降级。"""

    def test_30_on_pulse_empty(self):
        self.assertIsNone(self.o.on_pulse({}))

    def test_31_on_pulse_unknown_event(self):
        self.assertIsNone(self.o.on_pulse({"event_type": "no.such.event"}))

    def test_32_on_pulse_missing_payload(self):
        """缺 payload 键 → 取默认 {}，不崩。"""
        self.o.on_pulse({"event_type": "digest.knowledge"})

    def test_33_on_pulse_empty_payload(self):
        self.o.on_pulse({"event_type": "digest.knowledge", "payload": {}})

    def test_34_on_pulse_none_payload_degrades(self):
        """★第54批 T5 已修复（P2-371-2）：payload=None → 视为空 payload 优雅降级。

        ★铁律 32 反向同步：原用例用 assertRaises(AttributeError) 表征缺陷，
        缺陷已修复 → 改为守「当前契约」：**不抛异常**，且返回 dict / None（空内容被拒）。
        """
        _r = self.o.on_pulse({"event_type": "digest.knowledge", "payload": None})
        self.assertIsInstance(_r, (dict, type(None)),
                              "payload=None 应被降级为空 dict，不得抛异常")

    def test_35_refresh_on_broken_config(self):
        """RUNTIME_PARAMS 异常 → 记日志降级，不冒泡。"""

        class _Boom:
            def __contains__(self, _k):
                raise RuntimeError("boom")

        with mock.patch("config.RUNTIME_PARAMS", _Boom()):
            self.o.refresh_runtime_params()

    def test_36_digest_without_node_pool_degrades(self):
        """★第54批 T5 已修复（P2-371-3）：node_pool 未注入 → 降级为空列表，不冒泡。

        ★铁律 32 反向同步：原 assertRaises(AttributeError) → 改为守「当前契约」：
        **不抛异常** + 返回 dict（与 test_37 同等严格度）。
        """
        _bare = PulseStomach(organ_name="胃")
        _r = _bare.on_pulse({"event_type": "digest.knowledge",
                             "payload": {"content": "测试内容" * 6}})
        self.assertIsInstance(_r, dict, "node_pool 未注入时不得抛 AttributeError")

    def test_37_ethics_unavailable_degrades(self):
        """伦理模块不可用 → 降级通过（不阻断消化）。"""
        self.o.info_field = None
        _r = self.o.on_pulse({"event_type": "digest.knowledge",
                              "payload": {"content": "测试内容" * 6}})
        self.assertIsInstance(_r, dict)


class TestBoundary(_Base):
    """边界条件。"""

    def test_50_safety_empty_string(self):
        self.assertIsInstance(self.o._is_safe_knowledge(""), bool)

    def test_51_safety_none_degrades(self):
        """★第54批 T5 已修复（P2-371-1）：非字符串输入 → 降级为 str，不抛 TypeError。

        ★铁律 32 反向同步：原 assertRaises(TypeError) → 改为守「当前契约」：
        **不抛异常**且返回 bool。★覆盖 None / int / list 三种非字符串形态。
        """
        for _bad in (None, 123, ["a"], {"k": "v"}):
            self.assertIsInstance(self.o._is_safe_knowledge(_bad), bool,
                                  "非字符串 %r 应安全降级为 bool" % (_bad,))

    def test_55_none_guard_switch_off_reproduces_defect(self):
        """★灰度验证：ENABLE_STOMACH_NONE_GUARD 关闭 → 复现改造前行为（仍抛 TypeError）。

        （★铁律 9：每个修复点都要能用开关复现修复前行为，保证零回归可验证。）
        """
        with mock.patch.object(config, "ENABLE_STOMACH_NONE_GUARD", False):
            with self.assertRaises(TypeError):
                self.o._is_safe_knowledge(None)

    def test_52_safety_huge_input(self):
        self.assertIsInstance(self.o._is_safe_knowledge("测试" * 20000), bool)

    def test_53_on_pulse_many_unknown_events(self):
        for _i in range(200):
            self.o.on_pulse({"event_type": "e%d" % _i})

    def test_54_concurrent_on_pulse(self):
        """并发调用不崩（★已注入 mock 依赖 → 走正常路径）。"""
        _errs = []

        def _w():
            try:
                for _ in range(30):
                    self.o.on_pulse({"event_type": "digest.knowledge",
                                     "payload": {"content": "并发测试内容" * 3}})
            except Exception as _e:
                _errs.append("%s: %s" % (type(_e).__name__, _e))

        _ts = [threading.Thread(target=_w) for _ in range(4)]
        for _t in _ts:
            _t.start()
        for _t in _ts:
            _t.join()
        self.assertEqual(_errs, [])

    def test_55_stats_repeatable(self):
        self.assertEqual(sorted(self.o.get_stats()),
                         sorted(self.o.get_stats()))

    def test_56_double_injection_idempotent(self):
        """重复注入依赖不崩。"""
        for _ in range(3):
            self.o.set_node_pool(mock.MagicMock())
            self.o.set_knowledge_tree(mock.MagicMock())
        self.assertIsInstance(self.o.get_stats(), dict)


if __name__ == "__main__":
    unittest.main()
