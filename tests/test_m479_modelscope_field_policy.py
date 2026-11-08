# -*- coding: utf-8 -*-
"""★479 收尾：modelscope 渠道 reasoning_effort 契约门控单测。

## 背景（生产实测，不是推测）

logs/pulse.log 21:30:02（pid=21468）、22:41:20（pid=43552）：
    [渠道] modelscope HTTP 400 invalid_parameter_error:
    {"error":{"message":"'reasoning_effort' must be 'none' when 'enable_thinking' is false"}}

478 批把 modelscope 从「降级哨兵」恢复成正常渠道（p5，模型换成实测可用的
Qwen/Qwen3.8-Flash-Next），但**漏了配套字段策略** ⇒ 每次轮到它必 400，
等于「恢复了但用不了」。

三组实测（2026-11-08 22:4x，直打 api-inference.modelscope.cn）：
  ① thinking={"type":"disabled"} + reasoning_effort="low"  ⇒ HTTP 400（生产现状）
  ② thinking={"type":"disabled"} + reasoning_effort="none" ⇒ HTTP 200 'OK'
  ③ 只给 thinking、不带 reasoning_effort                  ⇒ HTTP 200 'OK'
⇒ 策略取 ②（与 bailian-qwen38-flash 同一口径，改动面最小、可回归）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter
from organs.body.PulseLung import PulseLung


class TestModelscopeFieldPolicy(unittest.TestCase):

    def _pl(self):
        return PulseLung.__new__(PulseLung)

    def test_01_policy_declared(self):
        _p = config.CHANNEL_REQUEST_FIELD_POLICY.get("modelscope")
        self.assertIsNotNone(_p, "modelscope 渠道没有字段策略")
        self.assertEqual(_p.get("reasoning_effort_when_off"), "none")

    def test_02_end_to_end_request_has_none(self):
        """★核心：走完「字段策略 → 适配器」后，最终请求体必须是 effort=none。"""
        _out = self._pl()._apply_channel_field_policy(
            "modelscope", {"enable_thinking": False, "max_tokens": 512})
        _req = OpenAICompatibleAdapter().build_request(
            "Qwen/Qwen3.8-Flash-Next", [{"role": "user", "content": "x"}], **_out)
        self.assertEqual(_req.get("reasoning_effort"), "none",
                         "生产会 HTTP 400：'reasoning_effort' must be 'none'")
        self.assertEqual(_req.get("thinking"), {"type": "disabled"})

    def test_03_thinking_on_untouched(self):
        """反证：开思考时不许被改成 none（否则会削弱能力）。"""
        _out = self._pl()._apply_channel_field_policy(
            "modelscope", {"enable_thinking": True, "max_tokens": 512})
        _req = OpenAICompatibleAdapter().build_request(
            "Qwen/Qwen3.8-Flash-Next", [{"role": "user", "content": "x"}], **_out)
        self.assertNotEqual(_req.get("reasoning_effort"), "none")
        self.assertEqual(_req.get("thinking"), {"type": "enabled"})

    def test_04_no_enable_thinking_key_untouched(self):
        """未传 enable_thinking ⇒ 策略不该凭空插入 reasoning_effort。"""
        _out = self._pl()._apply_channel_field_policy(
            "modelscope", {"max_tokens": 512})
        self.assertNotIn("reasoning_effort", _out)

    def test_05_other_channels_unaffected(self):
        """反证：不许把 none 传染给别的渠道。"""
        _out = self._pl()._apply_channel_field_policy(
            "deepseek", {"enable_thinking": False, "max_tokens": 512})
        self.assertNotEqual(_out.get("reasoning_effort"), "none")

    def test_06_gray_switch_off_rolls_back(self):
        """灰度开关关闭 ⇒ 逐字回到改前（effort 仍为 low）。"""
        _old = config.ENABLE_M471_CHANNEL_PARAM_CONTRACT
        try:
            config.ENABLE_M471_CHANNEL_PARAM_CONTRACT = False
            _out = self._pl()._apply_channel_field_policy(
                "modelscope", {"enable_thinking": False, "max_tokens": 512})
            self.assertNotIn("reasoning_effort", _out)
        finally:
            config.ENABLE_M471_CHANNEL_PARAM_CONTRACT = _old

    def test_07_source_has_modelscope_policy(self):
        """接线防回潮：config 里必须有 modelscope 策略段。"""
        with open(os.path.join(ROOT, "config.py"),
                  encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        _i = _src.index('"modelscope": {')
        _seg = _src[_i:_i + 200]
        self.assertIn("reasoning_effort_when_off", _seg)
        self.assertIn("none", _seg)


if __name__ == "__main__":
    unittest.main()
