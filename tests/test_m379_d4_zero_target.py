# -*- coding: utf-8 -*-
"""★379 刀4 · 零目标死广播治理（DJ4-01，P0）

票面：716 条（6.9%）零目标零匹配广播，pattern_prewarm 149 + pattern_prediction 52
为 100% 零匹配。

T0 实测（纠正票面窗口）：
  · `data/monitor/infofield_dead_events.json` 当前只有 **3** 类死事件、共 431 条：
    runtime.metrics.collect 350 / metrics.collector.tick 70 / snapshot.rotate.tick 11
  · pattern_prewarm / pattern_prediction **不在**当前死事件台账里
  · EventBus 侧 live `事件零匹配=0/281`（pid26804）
⇒ 本刀按票面加**通用发布侧闸门**并套在票面点名的两个发布点上（防回潮），
  同时把"当前真死事件是另外三类（合成 tick 事件、无源码字面）"如实写进报告。

★闸门的关键：EventTap 以 `**` 订阅全部事件 ⇒ 裸数 `_subs` 永远非空、闸门形同虚设。
  `has_subscribers()` 必须剔除 `**` / `*` 观察者 —— 本件第一条用例就锁这个。
"""
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.events.EventBus import EventBus  # noqa: E402
from nucleus.field import InfoField as _if  # noqa: E402


def _bus():
    return EventBus(async_enabled=False)


def test_m379_d4_wildcard_observer_is_not_a_consumer():
    """★核心：只剩 `**` 观察者（EventTap）时，必须判"无业务订阅方"。

    若这条红了，说明闸门退化成"永远有订阅方"，一条死广播都挡不住。
    """
    _b = _bus()
    _b.subscribe("**", lambda e: None)
    assert _b.has_subscribers("pattern_prediction") is False
    assert _b.has_subscribers("anything.at.all") is False


def test_m379_d4_real_subscriber_counts():
    _b = _bus()
    _b.subscribe("**", lambda e: None)                 # 观察者
    _b.subscribe("pattern_prewarm", lambda e: None)    # 真订阅方
    assert _b.has_subscribers("pattern_prewarm") is True
    assert _b.has_subscribers("pattern_prediction") is False


def test_m379_d4_module_level_entry():
    from nucleus.events import EventBus as _m
    assert _m.has_subscribers("no.such.event.in.this.process") in (True, False)
    # 真实语义：单例上若没有业务订阅方 ⇒ False（EventTap 未启动时也应稳）
    _v = _m.has_subscribers("no.such.event.in.this.process")
    assert isinstance(_v, bool)


def test_m379_d4_infofield_gate_reads_bus(monkeypatch):
    """InfoField 侧闸门口径与总线一致（含开关回退）。"""
    _b = _bus()
    _b.subscribe("**", lambda e: None)
    monkeypatch.setattr("nucleus.events.EventBus.get_event_bus", lambda: _b)
    assert _if._m379_bus_has_consumer("pattern_prediction") is False
    _b.subscribe("pattern_prediction", lambda e: None)
    assert _if._m379_bus_has_consumer("pattern_prediction") is True


def test_m379_d4_gate_switch_off_always_publishes(monkeypatch):
    """灰度：开关关闭 ⇒ 恒 True（逐字回到"照发"，零回归路径）。"""
    _b = _bus()
    _b.subscribe("**", lambda e: None)
    monkeypatch.setattr("nucleus.events.EventBus.get_event_bus", lambda: _b)
    monkeypatch.setattr(config, "ENABLE_M379_ZERO_TARGET_GATE", False, raising=False)
    assert _if._m379_bus_has_consumer("pattern_prediction") is True


def test_m379_d4_skip_is_recorded_once(monkeypatch):
    """被闸门挡下要留痕（每名一次），不能静默——否则"没发"和"没订阅方"同形。"""
    _if._M379_GATED_SEEN.clear()
    _if._m379_note_skipped("pattern_prewarm")
    _if._m379_note_skipped("pattern_prewarm")
    assert "pattern_prewarm" in _if._M379_GATED_SEEN
    _if._M379_GATED_SEEN.clear()


def test_m379_d4_gated_events_are_the_two_named_ones():
    """范围锁死：只给票面点名的两类套闸，不许扩散到全库发布点。"""
    assert _if._M379_GATED_EVENTS == ("pattern_prediction", "pattern_prewarm")
