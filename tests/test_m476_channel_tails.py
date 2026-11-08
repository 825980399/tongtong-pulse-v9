# -*- coding: utf-8 -*-
"""476 追补：渠道尾巴修复的门控单测。

覆盖两块：
① 百炼免费档进价格表（qwen3.7-flash 等），消除「未定价」假账；
② kimi-k3 不接受 temperature ⇒ 适配器支持显式省略 + 渠道策略接线。

设计原则（铁律 30/35）：基准来自**另一个入口**（适配器常量 / 官方免费档事实），
不拿被测函数的输出当基准；并带反证（付费档不得被误改成 0.0）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("PULSE_FRAMEWORK", "1")

import config
from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter
from organs.body.PulseLung import PulseLung


class TestPriceTable:
    """① 百炼免费档必须有 0.0 定价（≠ 缺键/None 的「未定价」）。"""

    def test_qwen37_flash_priced_zero(self):
        assert "qwen3.7-flash" in config.EVOLUTION_LLM_PRICE_TABLE
        assert config.EVOLUTION_LLM_PRICE_TABLE["qwen3.7-flash"] == 0.0

    def test_bailian_free_tier_all_priced(self):
        for _m in ("qwen3.8-flash", "qwen3.7-plus", "qwen3.8-max", "glm-5.3",
                   "kimi-k3", "deepseek-v4.1-flash",
                   # ★478：耗尽后换上的满额带日期别名
                   "deepseek-v4.1-flash-1017", "qwen3.7-plus-2026-05-26",
                   "deepseek-v4-pro-0813", "glm-5.2"):
            assert _m in config.EVOLUTION_LLM_PRICE_TABLE, f"缺价: {_m}"
            assert config.EVOLUTION_LLM_PRICE_TABLE[_m] == 0.0, f"{_m} 应为免费档 0.0"

    def test_paid_models_not_zeroed(self):
        # 反证：付费档不能被误改成 0.0（否则熔断器看不见真实花费）
        assert config.EVOLUTION_LLM_PRICE_TABLE["deepseek-v4-flash"] > 0
        assert config.EVOLUTION_LLM_PRICE_TABLE["deepseek-v4-pro"] > 0


class TestOmitTemperature:
    """② kimi-k3 不接受 temperature ⇒ 策略置 None、适配器省略。"""

    def setup_method(self):
        # 只调纯策略函数，不跑 __init__（避免拉起器官依赖）
        self._pl = PulseLung.__new__(PulseLung)

    def test_kimi_k3_policy_sets_none(self):
        _out = self._pl._apply_channel_field_policy("bailian-kimi-k3", {"max_tokens": 512})
        assert _out["temperature"] is None

    def test_adapter_omits_when_none(self):
        _req = OpenAICompatibleAdapter().build_request("kimi-k3", [], temperature=None)
        assert "temperature" not in _req, "None 哨兵必须省略 temperature"

    def test_adapter_default_when_absent(self):
        _req = OpenAICompatibleAdapter().build_request("m", [])
        assert _req["temperature"] == OpenAICompatibleAdapter.DEFAULT_TEMPERATURE

    def test_adapter_numeric_kept(self):
        _req = OpenAICompatibleAdapter().build_request("m", [], temperature=0.2)
        assert _req["temperature"] == 0.2

    def test_other_channel_temperature_untouched(self):
        # 反证：未登记 omit_temperature 的渠道，temperature 原样透传
        _out = self._pl._apply_channel_field_policy("zhipu", {"temperature": 0.3})
        assert _out.get("temperature") == 0.3

    def test_end_to_end_kimi_request_has_no_temperature(self):
        _out = self._pl._apply_channel_field_policy("bailian-kimi-k3", {"max_tokens": 512})
        _req = OpenAICompatibleAdapter().build_request("kimi-k3", [], **_out)
        assert "temperature" not in _req
        assert _req["max_tokens"] == 512


class TestVoiceListenGuard:
    """③ 3.14t PortAudio 原生崩溃止血：语音监听默认必须关。"""

    def test_default_off(self):
        assert config.ENABLE_VOICE_LISTEN is False, (
            "3.14t 下 sounddevice/PortAudio 录音会 0xC0000005 硬崩，默认必须关闭语音监听"
        )

    def test_source_hardcoded_true_gone(self):
        # 反证：PulseEars 不能再有无条件 True（否则开关形同虚设）
        _p = os.path.join(ROOT, "organs", "senses", "PulseEars.py")
        with open(_p, "r", encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        assert "self._voice_enabled = True" not in _src, "残留硬编码 True"
