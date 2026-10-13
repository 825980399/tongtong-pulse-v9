# -*- coding: utf-8 -*-
"""第52批 T2（P1-370 部分）门控测试：PulseLung（肺）单元测试。

覆盖 4 类（初始化 / 核心方法 / 异常降级 / 边界），共 23 例。
★mock 隔离外部依赖（info_field / pulse_core）—— **绝不实际调用大模型 API**
（故不测 `analyze_semantics`，它需要真实 LLM）。

肺的职责：大模型渠道选择与调用、对话历史维护、并发统计。
"""
import os
import sys
import threading
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.body.PulseLung import PulseLung


class _Base(unittest.TestCase):
    def setUp(self):
        self.o = PulseLung(organ_name="肺")
        # ★注入 mock 依赖（属性注入，肺无 set_* 接口）
        self.o.info_field = mock.MagicMock()
        self.o.pulse_core = mock.MagicMock()


class TestInit(_Base):
    def test_01_organ_name(self):
        self.assertEqual(self.o.organ_name, "肺")

    def test_02_default_organ_name(self):
        self.assertTrue(PulseLung().organ_name)

    def test_03_custom_organ_name(self):
        self.assertEqual(PulseLung(organ_name="X").organ_name, "X")

    def test_04_log_callable(self):
        self.assertTrue(callable(getattr(self.o, "_log", None)))

    def test_05_stats_has_organ(self):
        self.assertEqual(self.o.get_stats().get("organ"), "肺")


class TestCoreMethods(_Base):
    def test_10_get_stats_shape(self):
        _st = self.o.get_stats()
        self.assertIsInstance(_st, dict)
        for _k in ("organ", "selection_count", "call_success_count",
                   "call_fail_count"):
            self.assertIn(_k, _st)

    def test_11_stats_counters_numeric(self):
        _st = self.o.get_stats()
        for _k in ("selection_count", "call_success_count", "call_fail_count"):
            self.assertIsInstance(_st[_k], int)

    def test_12_resonance_shape(self):
        _rc = self.o.get_resonance_conditions()
        self.assertIsInstance(_rc, list)
        self.assertTrue(_rc)
        self.assertEqual(_rc[0]["organ_name"], self.o.organ_name)

    def test_13_refresh_no_raise(self):
        self.o.refresh_runtime_params()

    def test_14_dialog_history_initially_empty(self):
        self.assertEqual(self.o.get_dialog_history(), [])

    def test_15_append_dialog_turn(self):
        self.o.append_dialog_turn("user", "你好")
        _h = self.o.get_dialog_history()
        self.assertTrue(_h, "追加后历史应非空")

    def test_16_append_two_turns(self):
        self.o.append_dialog_turn("user", "你好")
        self.o.append_dialog_turn("assistant", "你好呀")
        self.assertGreaterEqual(len(self.o.get_dialog_history()), 2)

    def test_17_clear_dialog_history(self):
        self.o.append_dialog_turn("user", "你好")
        self.o.clear_dialog_history()
        self.assertEqual(self.o.get_dialog_history(), [])

    def test_18_emit_dialog_progress(self):
        """进度发射不得抛异常（detail 有默认值）。"""
        self.o.emit_dialog_progress("stage_a")
        self.o.emit_dialog_progress("stage_b", "detail")

    def test_19_channel_concurrency_stats(self):
        _s = self.o.get_channel_concurrency_stats()
        self.assertIsInstance(_s, (dict, list, tuple))

    def test_20_summary_channel_concurrency(self):
        _s = self.o.summary_channel_concurrency()
        self.assertIsNotNone(_s)


class TestDegradation(_Base):
    def test_30_on_pulse_empty(self):
        self.assertIsNone(self.o.on_pulse({}))

    def test_31_on_pulse_unknown(self):
        self.assertIsNone(self.o.on_pulse({"event_type": "no.such"}))

    def test_32_on_pulse_status_request(self):
        self.o.on_pulse({"event_type": "system.status.request", "payload": {}})

    def test_33_on_pulse_without_info_field(self):
        self.o.info_field = None
        self.o.on_pulse({"event_type": "system.status.request", "payload": {}})

    def test_34_append_without_history_support(self):
        """追加对话历史不得因依赖缺失而冒泡（内部应自建容器）。"""
        _bare = PulseLung(organ_name="肺")
        _bare.append_dialog_turn("user", "x")

    def test_35_refresh_on_broken_config(self):
        class _Boom:
            def __contains__(self, _k):
                raise RuntimeError("boom")

        with mock.patch("config.RUNTIME_PARAMS", _Boom()):
            self.o.refresh_runtime_params()


class TestBoundary(_Base):
    def test_50_many_dialog_turns(self):
        for _i in range(200):
            self.o.append_dialog_turn("user", "内容%d" % _i)
        self.assertTrue(self.o.get_dialog_history())

    def test_51_long_content_turn(self):
        self.o.append_dialog_turn("user", "很长内容" * 2000)
        self.assertTrue(self.o.get_dialog_history())

    def test_52_empty_content_turn(self):
        self.o.append_dialog_turn("user", "")
        self.o.append_dialog_turn("", "")

    def test_53_concurrent_on_pulse(self):
        _errs = []

        def _w():
            try:
                for _ in range(30):
                    self.o.on_pulse({"event_type": "system.status.request",
                                     "payload": {}})
            except Exception as _e:
                _errs.append(f"{type(_e).__name__}: {_e}")

        _ts = [threading.Thread(target=_w) for _ in range(4)]
        for _t in _ts:
            _t.start()
        for _t in _ts:
            _t.join()
        self.assertEqual(_errs, [])

    def test_54_repeated_clear(self):
        for _ in range(5):
            self.o.clear_dialog_history()
        self.assertEqual(self.o.get_dialog_history(), [])

    def test_55_stats_repeatable(self):
        self.assertEqual(sorted(self.o.get_stats()), sorted(self.o.get_stats()))




class TestRoutingDecisionM204(_Base):
    """第204批 刀1：路由翻转后「默认对话侧（宁多答不误吞）」验证。

    注：470 行 _is_background_learning 翻转对 _enable_thinking 运行时无直接影响
    （475-477 已用正确逻辑覆盖 473，见交付报告 D#8）；本测试以同源实例属性
    self._current_task_is_background（451 行）断言「编程题走对话侧 / 显式后台学习走静默」。
    """
    def _route(self, payload):
        import config
        with mock.patch.dict(
            config.__dict__,
            {"REMOTE_API_CONFIG": {"enabled": True, "api_key": "x",
                                   "default_model": "m", "advanced_model": "a"}},
        ):
            with mock.patch.object(self.o, "_call_remote_api", return_value="ok"), \
                 mock.patch.object(self.o, "_assess_remote_reply_quality",
                                   return_value={"passed": True, "level": "mid", "score": 3, "issues": []}), \
                 mock.patch.object(self.o, "_append_background_learning_requirement",
                                   return_value="p"), \
                 mock.patch.object(self.o, "_build_chat_prompt", return_value="p"), \
                 mock.patch.object(self.o, "_feedback_quality_result", return_value=None):
                return self.o._on_select_model(payload)

    def test_code_routes_to_dialogue(self):
        self._route({"prompt": "q", "task_type": "code", "is_dialogue": True})
        self.assertFalse(self.o._current_task_is_background,
                         "编程题（对话侧）应为非后台学习，走直接回答")

    def test_explicit_background_learning(self):
        self._route({"prompt": "q", "is_background_learning": True, "is_dialogue": False})
        self.assertTrue(self.o._current_task_is_background,
                        "显式后台学习应为后台侧（沉默消化）")

    def test_default_presumed_dialogue(self):
        # 缺标记时主线第49批无罪推定 → 视为对话 → 非后台
        self._route({"prompt": "q", "task_type": "chat"})
        self.assertFalse(self.o._current_task_is_background,
                         "缺标记应推定对话侧（宁多答不误吞）")


if __name__ == "__main__":
    unittest.main()
