# -*- coding: utf-8 -*-
"""
test_ark_seed_routing_m58.py —— 主线第58批 T2（P2-393延伸）门控单测

ark-seed 深度思考渠道优化：
  1) 请求复杂度分类 _classify_request_complexity（simple/medium/complex）
  2) 动态 max_tokens 解析 _resolve_ark_seed_max_tokens（受渠道上限封顶）
  3) ark-seed 复杂度路由 _apply_ark_seed_complexity_routing（灰度开关 + thinking 开关）
  4) 渠道推理速度画像 ChannelSpeedProfiler + PulseLung.get_channel_speed_profile
  5) 适配器兼容：预置 thinking 字典时优先使用（规避 Ark v3 disabled+low 400）
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.llm.channel_speed_profiler import ChannelSpeedProfiler  # noqa: E402
from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter  # noqa: E402
from organs.body.PulseLung import PulseLung  # noqa: E402


def _make_lung():
    """轻量构造 PulseLung，避免触发完整 __init__。"""
    _l = PulseLung.__new__(PulseLung)
    _l._log = lambda *a, **k: None  # 静默日志
    return _l


def _with_config(**kw):
    """临时改写 config 开关，退出时还原。"""
    class _Ctx:
        def __enter__(self):
            self._old = {_k: getattr(config, _k, None) for _k in kw}
            for _k, _v in kw.items():
                setattr(config, _k, _v)
            return self

        def __exit__(self, *exc):
            for _k, _v in self._old.items():
                if _v is None:
                    if hasattr(config, _k):
                        delattr(config, _k)
                else:
                    setattr(config, _k, _v)
            return False
    return _Ctx()


class TestClassifyComplexity(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(PulseLung._classify_request_complexity("你好"), "simple")
        self.assertEqual(PulseLung._classify_request_complexity(""), "simple")
        self.assertEqual(PulseLung._classify_request_complexity("今天天气怎么样"), "simple")

    def test_medium(self):
        self.assertEqual(
            PulseLung._classify_request_complexity("请把下面的内容整理成表格并列出要点"),
            "medium")
        # 80 字以上
        self.assertEqual(
            PulseLung._classify_request_complexity("甲" * 90), "medium")

    def test_complex(self):
        self.assertEqual(
            PulseLung._classify_request_complexity("def add(a, b):\n    return a + b"),
            "complex")
        self.assertEqual(
            PulseLung._classify_request_complexity("请分析一下分布式系统的原理并给出设计方案"),
            "complex")
        self.assertEqual(
            PulseLung._classify_request_complexity("乙" * 350), "complex")


class TestResolveMaxTokens(unittest.TestCase):
    def test_capped_by_override(self):
        _l = _make_lung()
        # ark-seed-21-turbo 的 max_tokens_override=1024，复杂档 2048 应封顶到 1024
        self.assertEqual(_l._resolve_ark_seed_max_tokens("ark-seed-21-turbo", "complex"), 1024)
        self.assertEqual(_l._resolve_ark_seed_max_tokens("ark-seed-21-turbo", "medium"), 1024)
        # 简单档 512 < 1024，不封顶
        self.assertEqual(_l._resolve_ark_seed_max_tokens("ark-seed-21-turbo", "simple"), 512)


class TestArkSeedRouting(unittest.TestCase):
    def test_routing_off_returns_unchanged(self):
        _l = _make_lung()
        with _with_config(ENABLE_ARK_SEED_COMPLEXITY_ROUTING=False):
            _out = _l._apply_ark_seed_complexity_routing(
                "ark-seed-21-turbo", {"max_tokens": 4096}, "你好")
        self.assertEqual(_out, {"max_tokens": 4096})

    def test_non_ark_channel_untouched(self):
        _l = _make_lung()
        _out = _l._apply_ark_seed_complexity_routing(
            "deepseek", {"max_tokens": 4096, "enable_thinking": True}, "请分析原理")
        self.assertEqual(_out.get("thinking"), None)
        self.assertEqual(_out.get("max_tokens"), 4096)

    def test_simple_disables_thinking_no_effort(self):
        _l = _make_lung()
        _out = _l._apply_ark_seed_complexity_routing(
            "ark-seed-21-turbo", {"enable_thinking": True}, "你好")
        self.assertEqual(_out.get("thinking"), {"type": "disabled"})
        self.assertNotIn("reasoning_effort", _out)  # 关键：规避 Ark v3 disabled+low 400
        self.assertNotIn("enable_thinking", _out)
        self.assertEqual(_out.get("max_tokens"), 512)

    def test_complex_enables_thinking(self):
        _l = _make_lung()
        _out = _l._apply_ark_seed_complexity_routing(
            "ark-seed-21-pro", {}, "def f():\n    return 1")
        self.assertEqual(_out.get("thinking"), {"type": "enabled"})
        self.assertEqual(_out.get("reasoning_effort"), "high")
        self.assertEqual(_out.get("max_tokens"), 1024)  # 上限封顶

    def test_thinking_subswitch_off_keeps_default(self):
        _l = _make_lung()
        with _with_config(ARK_SEED_THINKING_BY_COMPLEXITY=False):
            _out = _l._apply_ark_seed_complexity_routing(
                "ark-seed-21-turbo", {"enable_thinking": True}, "你好")
        # thinking 子开关关闭：只做动态 max_tokens，不强制思考开关
        self.assertNotIn("thinking", _out)
        self.assertNotIn("reasoning_effort", _out)
        self.assertEqual(_out.get("max_tokens"), 512)


class TestAdapterHonorsPresetThinking(unittest.TestCase):
    def test_preset_thinking_disabled_no_effort(self):
        _req = OpenAICompatibleAdapter().build_request(
            "m", [{"role": "user", "content": "x"}],
            thinking={"type": "disabled"})
        self.assertEqual(_req.get("thinking"), {"type": "disabled"})
        self.assertNotIn("reasoning_effort", _req)  # 不附带，规避 400

    def test_preset_thinking_enabled_with_effort(self):
        _req = OpenAICompatibleAdapter().build_request(
            "m", [{"role": "user", "content": "x"}],
            thinking={"type": "enabled"}, reasoning_effort="high")
        self.assertEqual(_req.get("thinking"), {"type": "enabled"})
        self.assertEqual(_req.get("reasoning_effort"), "high")

    def test_enable_thinking_backward_compatible(self):
        _req = OpenAICompatibleAdapter().build_request(
            "m", [{"role": "user", "content": "x"}], enable_thinking=True)
        self.assertEqual(_req.get("thinking"), {"type": "enabled"})
        self.assertEqual(_req.get("reasoning_effort"), "high")

    def test_no_thinking_flag(self):
        _req = OpenAICompatibleAdapter().build_request(
            "m", [{"role": "user", "content": "x"}])
        self.assertNotIn("thinking", _req)
        self.assertNotIn("reasoning_effort", _req)


class TestChannelSpeedProfiler(unittest.TestCase):
    def test_record_and_profile(self):
        _p = ChannelSpeedProfiler()
        for _s in (1.0, 2.0, 3.0, 1.5, 2.5):
            _p.record("ark-seed-21-turbo", _s)
        _prof = _p.profile("ark-seed-21-turbo")
        self.assertEqual(_prof["count"], 5)
        self.assertAlmostEqual(_prof["avg"], 2.0, places=3)
        self.assertEqual(_prof["min"], 1.0)
        self.assertEqual(_prof["max"], 3.0)
        self.assertEqual(_prof["last"], 2.5)
        self.assertGreaterEqual(_prof["p95"], 2.5)

    def test_rejects_negative_and_nan(self):
        _p = ChannelSpeedProfiler()
        _p.record("c", -1.0)
        _p.record("c", float("nan"))
        self.assertEqual(_p.profile("c")["count"], 0)

    def test_profile_unknown_channel(self):
        _p = ChannelSpeedProfiler()
        self.assertEqual(_p.profile("nope")["count"], 0)

    def test_full_profile_dict(self):
        _p = ChannelSpeedProfiler()
        _p.record("a", 1.0)
        _p.record("b", 2.0)
        _all = _p.profile()
        self.assertIn("a", _all)
        self.assertIn("b", _all)


class TestPulseLungSpeedProfile(unittest.TestCase):
    def test_record_and_get_profile(self):
        _l = _make_lung()
        for _s in (0.5, 1.0, 1.5):
            _l._record_channel_latency("ark-seed-evolving", _s)
        _prof = _l.get_channel_speed_profile("ark-seed-evolving")
        self.assertEqual(_prof["count"], 3)
        self.assertAlmostEqual(_prof["avg"], 1.0, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
