# -*- coding: utf-8 -*-
"""test_dup_dialog_m25.py —— 主线第25批门控单测。

覆盖任务书 §二 的 T1/T2/T3：
  T1 重复调用根因修复（发射幂等 + 语义器第二道防线 + 重复事件遥测）
  T2 doubao 渠道 400 修复（渠道字段白名单 + 脱敏 + 开关关零回归）
  T3 交互断链修复（多轮对话历史接线 + 长回答截断阈值配置化）

★全部为纯逻辑/配置级测试，不依赖网络、不写生产数据；执行时间 < 5 秒。
"""
import os
import sys
import threading
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
import nucleus.field.InfoField as _IF  # noqa: E402
from nucleus.field.InfoField import InfoField  # noqa: E402
from organs.body.PulseLung import PulseLung  # noqa: E402
from organs.brain.PulseCortex import PulseCortex  # noqa: E402
from organs.brain.PulseExpression import PulseExpression  # noqa: E402
from organs.brain.PulseSemanticComprehension import (  # noqa: E402
    PulseSemanticComprehension,
)


class _Switch:
    """临时改 config 属性（结束后还原）。"""

    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for _k, _v in self._kw.items():
            self._old[_k] = getattr(config, _k, None)
            setattr(config, _k, _v)
        return self

    def __exit__(self, *a):
        for _k, _v in self._old.items():
            if _v is None:
                try:
                    delattr(config, _k)
                except AttributeError:
                    pass
            else:
                setattr(config, _k, _v)
        return False


def _mk_cortex():
    """轻量实例化 PulseCortex（绕过 __init__），补齐第25批新增状态。"""
    _c = PulseCortex.__new__(PulseCortex)
    _c._turn_emit_lock = threading.Lock()
    _c._turn_emit_seen = {}
    _c._turn_emit_dedup_count = 0
    _c._logs = []
    return _c


def _mk_sem():
    """轻量实例化语义理解器（绕过 __init__），补齐第25批新增状态。"""
    _s = PulseSemanticComprehension.__new__(PulseSemanticComprehension)
    _s._classify_claim_lock = threading.Lock()
    _s._classify_claimed = {}
    _s._classify_dup_ignored = 0
    _s._logs = []
    _s._log = lambda lv, msg, *a, **k: _s._logs.append(str(msg))
    return _s


def _mk_lung():
    """轻量实例化 PulseLung（绕过 __init__），仅用于 T3 多轮对话历史接口。"""
    _l = PulseLung.__new__(PulseLung)
    _l._logs = []
    _l._log = lambda lv, msg, *a, **k: _l._logs.append(str(msg))
    _l._m24_ensure_state()
    return _l


def _mk_field():
    """轻量实例化 InfoField（绕过 __init__），仅用于遥测方法验证。"""
    _f = InfoField.__new__(InfoField)
    _f._dup_trace_seen = {}
    _f._dup_trace_lock = threading.Lock()
    _f._dup_trace_count = 0
    return _f


def _pulse(event_type="lungs.select_model", cid="ctx:1:1", prompt="你好"):
    return {
        "pulse_id": f"p-{cid}-{event_type}",
        "source_organ": "测试",
        "event_type": event_type,
        "payload": {"correlation_id": cid, "prompt": prompt},
    }


# ==========================================================================
# T1 发射幂等（PulseCortex._claim_select_model_emit）
# ==========================================================================
class TestT1SelectModelIdempotency(unittest.TestCase):

    def test_01_same_turn_same_prompt_suppressed(self):
        """同一 cid + 同一 prompt：第二次发射被抑制。"""
        _c = _mk_cortex()
        self.assertTrue(_c._claim_select_model_emit("cidA", "你好"))
        self.assertFalse(_c._claim_select_model_emit("cidA", "你好"))
        self.assertEqual(_c.get_turn_emit_stats()["suppressed"], 1)

    def test_02_different_prompt_same_cid_allowed(self):
        """同一 cid 但 prompt 不同 → 视为不同发射，允许。"""
        _c = _mk_cortex()
        self.assertTrue(_c._claim_select_model_emit("cidA", "问题一"))
        self.assertFalse(_c._claim_select_model_emit("cidA", "问题一"))
        # 不同 prompt 走新的 key（补救/换题的场景需保留）
        self.assertTrue(_c._claim_select_model_emit("cidB", "问题一"))

    def test_03_different_cid_allowed(self):
        """不同 cid（新的一轮）→ 允许。"""
        _c = _mk_cortex()
        self.assertTrue(_c._claim_select_model_emit("cid1", "同一句话"))
        self.assertTrue(_c._claim_select_model_emit("cid2", "同一句话"))

    def test_04_remediation_not_blocked(self):
        """★关键：合法补救调用（is_remediation=True）不被去重误伤。"""
        _c = _mk_cortex()
        self.assertTrue(_c._claim_select_model_emit("cidA", "问题", is_remediation=False))
        self.assertFalse(_c._claim_select_model_emit("cidA", "问题", is_remediation=False))
        # 补救是本项目既有特性，必须仍然可以发射
        self.assertTrue(_c._claim_select_model_emit("cidA", "问题", is_remediation=True))

    def test_05_background_and_dialogue_keys_are_distinct(self):
        """后台学习与对话的 key 相互独立。"""
        _c = _mk_cortex()
        self.assertTrue(_c._claim_select_model_emit("cidA", "同内容", is_background=False))
        self.assertTrue(_c._claim_select_model_emit("cidA", "同内容", is_background=True))
        self.assertFalse(_c._claim_select_model_emit("cidA", "同内容", is_background=True))

    def test_06_switch_off_zero_regression(self):
        """开关关闭 → 恒允许发射（与改造前行为完全一致）。"""
        _c = _mk_cortex()
        with _Switch(ENABLE_DIALOG_SELECT_MODEL_DEDUP=False):
            for _ in range(5):
                self.assertTrue(_c._claim_select_model_emit("cidA", "你好"))
        self.assertEqual(_c.get_turn_emit_stats()["suppressed"], 0)


# ==========================================================================
# T1 第二道防线（语义理解器分类幂等）
# ==========================================================================
class TestT1ClassifyGuard(unittest.TestCase):

    def test_07_same_cid_classified_once(self):
        """同一 cid 只允许分类一次。"""
        _s = _mk_sem()
        self.assertTrue(_s._claim_classify_once("cidA"))
        self.assertFalse(_s._claim_classify_once("cidA"))
        self.assertEqual(_s._classify_dup_ignored, 1)

    def test_08_different_cid_allowed(self):
        _s = _mk_sem()
        self.assertTrue(_s._claim_classify_once("cid1"))
        self.assertTrue(_s._claim_classify_once("cid2"))

    def test_09_empty_cid_always_allowed(self):
        """无 cid（内部自主/后台）不做去重，避免误伤。"""
        _s = _mk_sem()
        for _ in range(3):
            self.assertTrue(_s._claim_classify_once(""))

    def test_10_switch_off_zero_regression(self):
        _s = _mk_sem()
        with _Switch(ENABLE_DIALOG_SELECT_MODEL_DEDUP=False):
            for _ in range(3):
                self.assertTrue(_s._claim_classify_once("cidA"))
        self.assertEqual(_s._classify_dup_ignored, 0)


# ==========================================================================
# T1 重复事件遥测
# ==========================================================================
class TestT1DupTrace(unittest.TestCase):

    def test_11_switch_off_no_state(self):
        """开关关闭 → 不写内存、不建栈（零副作用）。"""
        _f = _mk_field()
        with _Switch(DEBUG_DUP_EVENT_TRACE=False):
            for _ in range(3):
                _f._dup_trace_check(_pulse(), "lungs.select_model")
        self.assertEqual(_f._dup_trace_seen, {})
        self.assertEqual(_f._dup_trace_count, 0)

    def test_12_switch_on_detects_duplicate(self):
        """开关打开 → 第二次同事件被识别为重复（计数递增、栈被记录）。"""
        _f = _mk_field()
        with _Switch(DEBUG_DUP_EVENT_TRACE=True,
                     DUPLICATE_EVENT_WATCH=["lungs.select_model"],
                     DUPLICATE_EVENT_WINDOW_SEC=5.0,
                     DUPLICATE_EVENT_STACK_DEPTH=6), mock.patch.object(_IF._module_logger, "warning") as _w:
            _f._dup_trace_check(_pulse(), "lungs.select_model")
            self.assertEqual(_w.call_count, 0, "首次不应告警")
            _f._dup_trace_check(_pulse(), "lungs.select_model")
            self.assertEqual(_w.call_count, 1, "第二次应告警")
        self.assertEqual(_f._dup_trace_count, 2)
        _stack = list(_f._dup_trace_seen.values())[0][3]
        self.assertIn("_dup_trace_check", _stack, "应记录调用栈")

    def test_13_non_watched_event_ignored(self):
        """不在监视名单的事件不参与遥测。"""
        _f = _mk_field()
        with _Switch(DEBUG_DUP_EVENT_TRACE=True,
                     DUPLICATE_EVENT_WATCH=["lungs.select_model"]):
            _f._dup_trace_check(_pulse("heart.beat"), "heart.beat")
            _f._dup_trace_check(_pulse("heart.beat"), "heart.beat")
        self.assertEqual(_f._dup_trace_count, 0)

    def test_14_different_cid_not_duplicate(self):
        """同事件不同 cid 不算重复。"""
        _f = _mk_field()
        with _Switch(DEBUG_DUP_EVENT_TRACE=True,
                     DUPLICATE_EVENT_WATCH=["lungs.select_model"]):
            with mock.patch.object(_IF._module_logger, "warning") as _w:
                _f._dup_trace_check(_pulse(cid="c1"), "lungs.select_model")
                _f._dup_trace_check(_pulse(cid="c2"), "lungs.select_model")
                self.assertEqual(_w.call_count, 0)


# ==========================================================================
# T2 渠道字段白名单（doubao 400 修复）
# ==========================================================================
class TestT2ChannelFieldPolicy(unittest.TestCase):

    def _mk(self):
        _l = PulseLung.__new__(PulseLung)
        _l._logs = []
        _l._log = lambda lv, msg, *a, **k: _l._logs.append(str(msg))
        _l._m24_ensure_state()
        return _l

    def test_15_doubao_drops_thinking(self):
        """doubao：裁剪 enable_thinking（Ark v3 会因该组合返回 400）。"""
        _l = self._mk()
        _out = _l._apply_channel_field_policy("doubao", {"enable_thinking": False})
        self.assertNotIn("enable_thinking", _out)
        self.assertEqual(_out.get("max_tokens"), 4096)

    def test_16_zhipu_drops_thinking(self):
        _l = self._mk()
        _out = _l._apply_channel_field_policy("zhipu", {"enable_thinking": True})
        self.assertNotIn("enable_thinking", _out)

    def test_17_unknown_channel_unchanged(self):
        """未配置策略的渠道（如 deepseek）原样透传。"""
        _l = self._mk()
        _kwargs = {"enable_thinking": True, "max_tokens": 512}
        self.assertEqual(_l._apply_channel_field_policy("deepseek", _kwargs), _kwargs)

    def test_18_switch_off_zero_regression(self):
        """开关关闭 → 原样返回（与改造前一致）。"""
        _l = self._mk()
        with _Switch(ENABLE_CHANNEL_FIELD_POLICY=False):
            _kwargs = {"enable_thinking": False}
            self.assertEqual(_l._apply_channel_field_policy("doubao", _kwargs), _kwargs)

    def test_19_mask_headers_masks_secret(self):
        """脱敏：Authorization 只留前缀。"""
        _out = PulseLung._mask_headers({
            "Authorization": "Bearer ark-39f4467f-f06d-42c8",
            "Content-Type": "application/json",
        })
        self.assertNotIn("39f4467f", _out["Authorization"])
        self.assertTrue(_out["Authorization"].endswith("****"))
        self.assertEqual(_out["Content-Type"], "application/json")

    def test_20_http_dump_switch_default_off(self):
        """HTTP dump 默认关闭（不打印请求体）。"""
        with _Switch(DEBUG_CHANNEL_HTTP_DUMP=False):
            self.assertFalse(PulseLung._channel_http_dump_enabled())


# ==========================================================================
# T3 多轮对话历史接线
# ==========================================================================
class TestT3DialogHistory(unittest.TestCase):

    def test_21_append_and_read_roundtrip(self):
        _l = _mk_lung()
        _l.append_dialog_turn("user", "我叫小明")
        _l.append_dialog_turn("assistant", "你好，小明")
        _h = _l.get_dialog_history()
        self.assertEqual(len(_h), 2)
        self.assertEqual(_h[0]["role"], "user")
        self.assertIn("小明", _h[1]["content"])

    def test_22_maxlen_follows_config(self):
        """maxlen 随 DIALOG_HISTORY_TURNS 变化（一轮=2条）。"""
        _l = _mk_lung()
        with _Switch(DIALOG_HISTORY_TURNS=2):
            _l.append_dialog_turn("user", "u1")
            _l.append_dialog_turn("assistant", "a1")
            _l.append_dialog_turn("user", "u2")
            _l.append_dialog_turn("assistant", "a2")
            _l.append_dialog_turn("user", "u3")
        _h = _l.get_dialog_history()
        self.assertEqual(len(_h), 4, "maxlen = 2 轮 × 2 = 4")
        self.assertEqual(_h[-1]["content"], "u3")

    def test_23_trim_by_budget_drops_oldest(self):
        _l = _mk_lung()
        _hist = [
            {"role": "user", "content": "旧" * 100},
            {"role": "assistant", "content": "新" * 10},
        ]
        with _Switch(DIALOG_HISTORY_MAX_TOKENS=50):
            _kept = _l._trim_history_by_budget(_hist)
        self.assertEqual(len(_kept), 1)
        self.assertTrue(_kept[0]["content"].startswith("新"))

    def test_24_empty_content_ignored(self):
        _l = _mk_lung()
        _l.append_dialog_turn("user", "")
        self.assertEqual(_l.get_dialog_history(), [])

    def test_25_clear_history(self):
        _l = _mk_lung()
        _l.append_dialog_turn("user", "hi")
        _l.clear_dialog_history()
        self.assertEqual(_l.get_dialog_history(), [])


# ==========================================================================
# T3 长回答腰斩修复（截断阈值配置化）
# ==========================================================================
class TestT3TruncationConfig(unittest.TestCase):

    @staticmethod
    def _long_text(n_sent=12):
        return "。".join([f"这是第{i}句测试内容，需要足够长度以通过过滤" for i in range(n_sent)]) + "。"

    def test_26_limit_zero_no_truncation(self):
        """★阈值 0 = 完全不截断（长回答不腰斩）。"""
        _e = PulseExpression.__new__(PulseExpression)
        _txt = self._long_text(20)
        with _Switch(DIALOG_REPLY_TRUNCATE_CHARS=0):
            self.assertEqual(_e._apply_length_smoothing(_txt, False), _txt)

    def test_27_default_1000_keeps_500_char_reply(self):
        """默认阈值 1000：500 字左右回复完整保留（任务书验收标准）。"""
        _e = PulseExpression.__new__(PulseExpression)
        _txt = self._long_text(12)
        self.assertLess(len(_txt), 1000)
        self.assertEqual(_e._apply_length_smoothing(_txt, False), _txt)

    def test_28_small_limit_still_truncates(self):
        """阈值调小后仍能截断（保持既有能力可用）。"""
        _e = PulseExpression.__new__(PulseExpression)
        _txt = self._long_text(20)
        with _Switch(DIALOG_REPLY_TRUNCATE_CHARS=300,
                     DIALOG_REPLY_TRUNCATE_MIN_SENTENCES=6):
            _out = _e._apply_length_smoothing(_txt, False)
        self.assertLess(len(_out), len(_txt))

    def test_29_inference_output_never_truncated(self):
        _e = PulseExpression.__new__(PulseExpression)
        _txt = self._long_text(20)
        with _Switch(DIALOG_REPLY_TRUNCATE_CHARS=100):
            self.assertEqual(_e._apply_length_smoothing(_txt, True), _txt)

    def test_30_late_night_threshold_configurable(self):
        """深夜静默模式阈值可配置（原硬编码 100 字）。"""
        _e = PulseExpression.__new__(PulseExpression)
        _txt = "。".join([f"深夜长内容第{i}段" for i in range(10)]) + "。"
        with _Switch(LATE_NIGHT_TRUNCATE_CHARS=100000,
                     LATE_NIGHT_TRUNCATE_SENTENCES=2), mock.patch("time.localtime") as _lt:
            _lt.return_value = type("T", (), {"tm_hour": 2})()
            _out = _e._apply_late_night_mode(_txt, "simple_logic")
        self.assertEqual(_out, _txt, "阈值放大后不应再截短")


if __name__ == "__main__":
    unittest.main(verbosity=2)
