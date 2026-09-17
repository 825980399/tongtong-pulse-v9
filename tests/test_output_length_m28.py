# -*- coding: utf-8 -*-
"""主线第28批 T1 门控测试：大模型输出长度优化（P2-171）。

★根因（实测）：输出偏短是 prompt 侧问题 —— `PulseLung._build_chat_prompt` 结尾
硬编码「建议3-6句话，50字以上」；26 条真实样本中位 87 字、均值 102 字。
本文件覆盖：分级判定 / 两条路径（对话 prompt 与渠道 system）生效 / max_tokens /
灰度关闭零回归 / 自定义阈值。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from organs.body.PulseLung import PulseLung  # noqa: E402

_LONG_Q = "请深入分析并论述：为什么说知识的沉淀与知识的反复验证之间存在内在张力？"
_SHORT_Q = "你好"
_LEGACY = "请用完整有内容的回复（建议3-6句话，50字以上，避免一句话敷衍）"


class _Switch:
    """临时改 config 属性（异常也保证还原）。"""

    def __init__(self, **kw):
        self._kw = kw

    def __enter__(self):
        self._old = {k: getattr(config, k, None) for k in self._kw}
        for k, v in self._kw.items():
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            setattr(config, k, v)
        return False


def _mk_lung():
    """轻量肺实例（绕 __init__），只挂被测路径所需属性。"""
    lung = PulseLung.__new__(PulseLung)
    lung._log = lambda *a, **k: None
    lung._dialog_history = []
    lung.ollama_base_url = "http://127.0.0.1:11434"
    lung.ollama_timeout = 30
    lung._get_correction_hint = lambda q: ""   # 依赖 _correction_memory_lock，打桩
    return lung


class TestConfigItems(unittest.TestCase):
    """配置项就位与默认值。"""

    def test_01_switch_default_on(self):
        self.assertIs(config.ENABLE_OUTPUT_LENGTH_OPTIMIZATION, True)

    def test_02_target_and_short_length(self):
        self.assertEqual(config.LLM_TARGET_OUTPUT_LENGTH, 300)
        self.assertEqual(config.LLM_SHORT_REPLY_LENGTH, 50)

    def test_03_long_question_threshold(self):
        self.assertEqual(config.LLM_LONG_QUESTION_MIN_CHARS, 30)

    def test_04_max_tokens_override(self):
        self.assertEqual(config.LLM_MAX_TOKENS_OVERRIDE, 1024)

    def test_05_keywords_present(self):
        self.assertGreaterEqual(len(config.LLM_LONG_REPLY_KEYWORDS), 10)
        self.assertIn("请写", config.LLM_LONG_REPLY_KEYWORDS)


class TestLongReplyJudgement(unittest.TestCase):
    """分级判定：长问题要求展开，短问题保持简洁。"""

    def test_10_long_question_true(self):
        self.assertTrue(_mk_lung()._m28_needs_long_reply(_LONG_Q))

    def test_11_short_greeting_false(self):
        self.assertFalse(_mk_lung()._m28_needs_long_reply(_SHORT_Q))

    def test_12_keyword_hit_true(self):
        """短句但含长文意图关键词（'为什么'）→ 视为需要展开。"""
        self.assertTrue(_mk_lung()._m28_needs_long_reply("为什么天是蓝的"))

    def test_13_empty_inputs_false(self):
        _l = _mk_lung()
        self.assertFalse(_l._m28_needs_long_reply(""))
        self.assertFalse(_l._m28_needs_long_reply(None))

    def test_14_threshold_configurable(self):
        """阈值可配置：调低后较短的句子也进入「需要展开」档。"""
        with _Switch(LLM_LONG_QUESTION_MIN_CHARS=3):
            self.assertTrue(_mk_lung()._m28_needs_long_reply("你好吗"))
        with _Switch(LLM_LONG_QUESTION_MIN_CHARS=50):
            self.assertFalse(_mk_lung()._m28_needs_long_reply("你好吗"))


class TestLengthHints(unittest.TestCase):
    """两档文案 + 两条路径（对话 prompt / 渠道 system）。"""

    def test_20_chat_hint_long_has_target(self):
        _h = _mk_lung()._m28_chat_length_hint(_LONG_Q)
        self.assertIn("300", _h)
        self.assertTrue(("展开" in _h) or ("层次" in _h))

    def test_21_chat_hint_short_keeps_brief(self):
        _h = _mk_lung()._m28_chat_length_hint(_SHORT_Q)
        self.assertIn("50", _h)
        self.assertIn("简洁", _h)

    def test_22_two_tiers_differ(self):
        _l = _mk_lung()
        self.assertNotEqual(_l._m28_chat_length_hint(_LONG_Q),
                            _l._m28_chat_length_hint(_SHORT_Q))

    def test_23_system_suffix_long_nonempty(self):
        _s = _mk_lung()._m28_system_length_suffix(_LONG_Q)
        self.assertIn("【本次输出要求】", _s)
        self.assertIn("300", _s)

    def test_24_system_suffix_short_empty(self):
        """短问题不加后缀 → 渠道 system prompt 保持原样。"""
        self.assertEqual(_mk_lung()._m28_system_length_suffix(_SHORT_Q), "")

    def test_25_channel_messages_injects_suffix(self):
        _msgs = _mk_lung()._build_channel_messages(_LONG_Q)
        self.assertEqual(len(_msgs), 2)
        self.assertIn("【本次输出要求】", _msgs[0]["content"])
        self.assertTrue(_msgs[0]["content"].startswith("你是曈曈"))

    def test_26_channel_messages_short_untouched(self):
        _msgs = _mk_lung()._build_channel_messages(_SHORT_Q)
        self.assertNotIn("【本次输出要求】", _msgs[0]["content"])

    def test_27_chat_prompt_uses_new_hint(self):
        _p = _mk_lung()._build_chat_prompt(_LONG_Q, "星轨")
        self.assertIn("300", _p)
        self.assertNotIn("建议3-6句话", _p)


class TestMaxTokens(unittest.TestCase):
    """max_tokens 配置化（非渠道路径原硬编码 512）。"""

    def test_30_nonchannel_uses_override(self):
        self.assertEqual(_mk_lung()._m28_max_tokens_nonchannel(), 1024)

    def test_31_nonchannel_floor_512(self):
        """配错（小于 512）时不降低上限。"""
        with _Switch(LLM_MAX_TOKENS_OVERRIDE=100):
            self.assertEqual(_mk_lung()._m28_max_tokens_nonchannel(), 512)


class TestZeroRegression(unittest.TestCase):
    """★灰度关闭 → 三处完全回退到改造前行为。"""

    def test_40_switch_off_reads_false(self):
        with _Switch(ENABLE_OUTPUT_LENGTH_OPTIMIZATION=False):
            self.assertFalse(_mk_lung()._m28_length_optimization_on())

    def test_41_chat_hint_returns_legacy(self):
        with _Switch(ENABLE_OUTPUT_LENGTH_OPTIMIZATION=False):
            self.assertEqual(_mk_lung()._m28_chat_length_hint(_LONG_Q), _LEGACY)

    def test_42_system_suffix_empty(self):
        with _Switch(ENABLE_OUTPUT_LENGTH_OPTIMIZATION=False):
            self.assertEqual(_mk_lung()._m28_system_length_suffix(_LONG_Q), "")

    def test_43_max_tokens_back_to_512(self):
        with _Switch(ENABLE_OUTPUT_LENGTH_OPTIMIZATION=False):
            self.assertEqual(_mk_lung()._m28_max_tokens_nonchannel(), 512)

    def test_44_chat_prompt_contains_legacy_text(self):
        with _Switch(ENABLE_OUTPUT_LENGTH_OPTIMIZATION=False):
            self.assertIn("建议3-6句话，50字以上",
                          _mk_lung()._build_chat_prompt(_LONG_Q, "星轨"))

    def test_45_channel_system_identical_when_off(self):
        with _Switch(ENABLE_OUTPUT_LENGTH_OPTIMIZATION=False):
            _sys = _mk_lung()._build_channel_messages(_LONG_Q)[0]["content"]
            self.assertNotIn("【本次输出要求】", _sys)
            self.assertIn("避免过于简短敷衍", _sys)   # 原有引导仍在


class TestCustomThresholds(unittest.TestCase):
    """阈值可配置（不依赖写死的 300/50）。"""

    def test_50_custom_target(self):
        with _Switch(LLM_TARGET_OUTPUT_LENGTH=800):
            self.assertIn("800", _mk_lung()._m28_chat_length_hint(_LONG_Q))

    def test_51_custom_short(self):
        with _Switch(LLM_SHORT_REPLY_LENGTH=20):
            self.assertIn("20", _mk_lung()._m28_chat_length_hint(_SHORT_Q))


if __name__ == "__main__":
    unittest.main(verbosity=2)
