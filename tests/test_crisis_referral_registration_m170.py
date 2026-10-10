# -*- coding: utf-8 -*-
"""第170批 C7（T-CRISIS_REFERRAL注册-1）配套断言：

· `PulseCortex.get_resonance_conditions()` 必须声明订阅 `RiskEvent.CRISIS_REFERRAL`
  （="risk.crisis_referral"），否则 `PulseCortex._on_crisis_referral`（L569→L1770）
  收不到危机转介事件，危机等级记忆 `_crisis_referral_level` 与文案通道无法激活。
· 守卫已广播该事件（PulseEthics:158 / PulseRiskPerception:529），本批补齐订阅端。

★纯注册声明断言，不触碰生产链路；与业务提交拆分（门禁隔离守卫）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.const import RiskEvent  # noqa: E402
from organs.brain.PulseCortex import PulseCortex  # noqa: E402


class TestCrisisReferralRegistration(unittest.TestCase):
    def _all_event_types(self):
        _cx = PulseCortex.__new__(PulseCortex)
        _cx.organ_name = "大脑皮层"
        _types = []
        for _cond in _cx.get_resonance_conditions():
            _types.extend(_cond.get("event_types", []))
        return _types

    def test_crisis_referral_subscribed(self):
        _types = self._all_event_types()
        self.assertIn(
            RiskEvent.CRISIS_REFERRAL, _types,
            "risk.crisis_referral 必须注册到大脑皮层共振条件，否则 _on_crisis_referral 收不到")

    def test_crisis_referral_value_is_risk_dot(self):
        self.assertEqual(RiskEvent.CRISIS_REFERRAL, "risk.crisis_referral")


if __name__ == "__main__":
    unittest.main()
