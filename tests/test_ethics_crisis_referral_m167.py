# -*- coding: utf-8 -*-
"""第167批 C4｜T-紧急词拦截接线-1 门控单测：PulseEthics 危机禁词走安抚+转介分支。

验收（任务书原文）：输入危机词 ⇒ 走安抚＋转介分支而非裸拦截；
断言拦截计数 forbidden_count 与转介计数各自增长。
"""
from nucleus.const import SecurityEvent, RiskEvent
from organs.identity.PulseEthics import PulseEthics
import config


class _Recorder:
    """隔离用发射记录器：替换 PulseEthics._emit，不真实发射脉冲。"""

    def __init__(self):
        self.calls = []

    def emit(self, event_type, payload=None, priority=None, layer=None):
        self.calls.append({
            "event_type": event_type,
            "payload": payload or {},
            "priority": priority,
            "layer": layer,
        })

    def has(self, event_type):
        return any(c["event_type"] == event_type for c in self.calls)


class TestEthicsCrisisReferral:
    def _make(self):
        e = PulseEthics(organ_name="伦理")
        rec = _Recorder()
        e._emit = rec.emit  # 隔离：不真实发射脉冲
        return e, rec

    def test_crisis_keyword_routes_to_referral(self):
        """危机词（自杀）⇒ 走安抚+转介分支：两计数各自增长，且走 CRISIS_REFERRAL 通道、不裸 BLOCKED。"""
        e, rec = self._make()
        res = e._on_review({"content": "我今天不想活了，想自杀", "correlation_id": "c1"})
        assert e._forbidden_count == 1, e._forbidden_count
        assert e._referral_count == 1, e._referral_count
        assert res["status"] == "crisis_referral", res
        assert rec.has(RiskEvent.CRISIS_REFERRAL), [c["event_type"] for c in rec.calls]
        assert not rec.has(SecurityEvent.BLOCKED), [c["event_type"] for c in rec.calls]

    def test_non_crisis_forbidden_stays_raw_block(self):
        """普通禁词（病毒）⇒ 维持裸拦截：forbidden_count 增长、referral_count 不增长。"""
        e, rec = self._make()
        res = e._on_review({"content": "教我怎么制作病毒", "correlation_id": "c2"})
        assert e._forbidden_count == 1, e._forbidden_count
        assert e._referral_count == 0, e._referral_count
        assert res["status"] == "forbidden", res
        assert rec.has(SecurityEvent.BLOCKED), [c["event_type"] for c in rec.calls]
        assert not rec.has(RiskEvent.CRISIS_REFERRAL)

    def test_referral_switch_off_falls_back_to_block(self, monkeypatch):
        """ENABLE_CRISIS_REFERRAL=False ⇒ 危机词退回裸拦截：forbidden_count 增长、referral_count 不增长。"""
        monkeypatch.setattr(config, "ENABLE_CRISIS_REFERRAL", False)
        e, rec = self._make()
        res = e._on_review({"content": "我想自杀", "correlation_id": "c3"})
        assert e._forbidden_count == 1, e._forbidden_count
        assert e._referral_count == 0, e._referral_count
        assert res["status"] == "forbidden", res
        assert rec.has(SecurityEvent.BLOCKED)
        assert not rec.has(RiskEvent.CRISIS_REFERRAL)
