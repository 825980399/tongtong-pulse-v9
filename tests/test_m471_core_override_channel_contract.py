# -*- coding: utf-8 -*-
"""471 批单测：①核心文件人工批准豁免　③两条百炼渠道的参数契约。

① 判据必须**从严**：只认 `approved_source == "human"`（唯一人工口 `_approve_via` 写入），
   刻意不收 legacy（`approved_source` 缺失 ⇒ 无法证明是人批的 ⇒ fail-closed）。
   否则 T-135d 的 `test_apply_now_landing_core_rejected`（approved + 无 auto 标记）
   会被这条豁免放行，等于把安全回归改成放行。

③ 两条渠道的 400 原文（logs/pulse.log 2026-11-06 20:02:28-29）：
   · bailian-qwen38-flash: 'reasoning_effort' must be 'none' when 'enable_thinking' is false
   · bailian-glm53:        The value of the enable_thinking parameter is restricted to True
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as _cfg
from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter
from nucleus.reasoning.PatchManager import PatchManager
from organs.body.PulseLung import PulseLung


# =====================================================================
# ① 核心文件人工批准豁免
# =====================================================================
class TestM471CoreHumanExempt:
    def test_human_approved_is_exempt(self):
        _p = {"status": "approved", "approved_source": "human",
              "approved_signer": "human:lin"}
        assert PatchManager._m471_core_human_exempt(_p) is True

    def test_auto_approved_not_exempt(self):
        _p = {"status": "approved", "approved_source": "human",
              "auto_approved": True}
        assert PatchManager._m471_core_human_exempt(_p) is False

    def test_auto_released_not_exempt(self):
        _p = {"status": "approved", "approved_source": "human",
              "auto_released": True}
        assert PatchManager._m471_core_human_exempt(_p) is False

    def test_machine_sources_not_exempt(self):
        for _src in ("auto", "aging", "save"):
            _p = {"status": "approved", "approved_source": _src}
            assert PatchManager._m471_core_human_exempt(_p) is False, _src

    def test_legacy_without_source_not_exempt(self):
        # ★刻意 fail-closed：不能证明是人批的
        assert PatchManager._m471_core_human_exempt({"status": "approved"}) is False
        assert PatchManager._m471_core_human_exempt(
            {"status": "approved", "approved_source": ""}) is False

    def test_not_approved_not_exempt(self):
        _p = {"status": "verified", "approved_source": "human"}
        assert PatchManager._m471_core_human_exempt(_p) is False

    def test_bad_input_not_exempt(self):
        assert PatchManager._m471_core_human_exempt({}) is False
        assert PatchManager._m471_core_human_exempt(None) is False

    def test_switch_off_returns_false(self, monkeypatch):
        monkeypatch.setattr(_cfg, "ENABLE_M471_CORE_HUMAN_OVERRIDE", False)
        _p = {"status": "approved", "approved_source": "human"}
        assert PatchManager._m471_core_human_exempt(_p) is False

    def test_gate_is_wired_in_apply_all_pending(self):
        """防回潮：豁免必须真的接在落地闸门上，而不只是有个孤儿函数。"""
        with open(os.path.join(ROOT, "nucleus", "reasoning", "PatchManager.py"),
                     "r", encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        assert "def _m471_core_human_exempt" in _src
        _idx = _src.index("def apply_all_pending")
        _tail = _src[_idx:]
        assert "_m471_core_human_exempt(patch)" in _tail, "豁免未接入 apply_all_pending"
        assert "not _m471_exempt" in _tail, "闸门条件未使用豁免判定"


# =====================================================================
# ③ 渠道参数契约
# =====================================================================
class TestM471ChannelParamContract:
    def setup_method(self):
        # 只调纯策略函数，不跑 __init__（避免拉起器官依赖）
        self._pl = PulseLung.__new__(PulseLung)

    def test_qwen38_off_sends_effort_none(self):
        _out = self._pl._apply_channel_field_policy(
            "bailian-qwen38-flash", {"enable_thinking": False})
        assert _out["reasoning_effort"] == "none"
        _req = OpenAICompatibleAdapter().build_request("qwen3.8-flash", [], **_out)
        assert _req["thinking"] == {"type": "disabled"}
        assert _req["reasoning_effort"] == "none"

    def test_qwen38_on_keeps_thinking(self):
        _out = self._pl._apply_channel_field_policy(
            "bailian-qwen38-flash", {"enable_thinking": True})
        # 开思考时不写入 effort 覆盖，适配器按原推导给 high
        assert "reasoning_effort" not in _out
        _req = OpenAICompatibleAdapter().build_request("qwen3.8-flash", [], **_out)
        assert _req["thinking"] == {"type": "enabled"}
        assert _req["reasoning_effort"] == "high"

    def test_glm53_forces_enable_thinking(self):
        _out = self._pl._apply_channel_field_policy(
            "bailian-glm53", {"enable_thinking": False})
        assert _out["enable_thinking"] is True
        _req = OpenAICompatibleAdapter().build_request("glm-5.3", [], **_out)
        assert _req["thinking"] == {"type": "enabled"}

    def test_other_channel_unchanged(self):
        _out = self._pl._apply_channel_field_policy(
            "bailian-qwen-max", {"enable_thinking": False})
        assert _out == {"enable_thinking": False}
        _req = OpenAICompatibleAdapter().build_request("qwen-max", [], **_out)
        assert _req["reasoning_effort"] == "low", "未登记渠道必须保持原行为"

    def test_switch_off_restores_old_behavior(self, monkeypatch):
        monkeypatch.setattr(_cfg, "ENABLE_M471_CHANNEL_PARAM_CONTRACT", False)
        _out = self._pl._apply_channel_field_policy(
            "bailian-glm53", {"enable_thinking": False})
        assert _out == {"enable_thinking": False}
        _out2 = self._pl._apply_channel_field_policy(
            "bailian-qwen38-flash", {"enable_thinking": False})
        assert _out2 == {"enable_thinking": False}

    def test_adapter_respects_explicit_effort(self):
        """适配器向后兼容：显式 reasoning_effort 优先，未显式时维持 high/low。"""
        _a = OpenAICompatibleAdapter()
        _r1 = _a.build_request("m", [], enable_thinking=False, reasoning_effort="none")
        assert _r1["reasoning_effort"] == "none"
        _r2 = _a.build_request("m", [], enable_thinking=False)
        assert _r2["reasoning_effort"] == "low"
        _r3 = _a.build_request("m", [], enable_thinking=True)
        assert _r3["reasoning_effort"] == "high"
