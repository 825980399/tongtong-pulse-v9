# -*- coding: utf-8 -*-
"""★380 刀1 · 三类合成 tick 死信定位拼接点（P1）

票面：runtime.metrics.collect（350）/ metrics.collector.tick（70）/ snapshot.rotate.tick（11）
共 431 条死信，全库 grep 不到源码字面量 —— "按名字拼出来的调度 tick"。

T0 结论（不是拼接，是**配置项**）：
  `config.TIMECORE_SCHEDULE_CONFIG`（config.py:143-145）里三条 203 批刀3 登记的
  "B类家务 Top3"，`event_type` 直接写成**字符串字面量**（不是 const 枚举，故 grep 事件名
  搜不到发布点）；注释原文即「消费侧接线待观察窗」—— 事实是**从未接线**。
  唯一出口是 `TimeCore._dispatch_managed_tasks`（按 interval 到期就发，不看有没有人接）。

本件验：闸门判据（InfoField 侧）＋ 调度侧闸门（TimeCore 侧）＋ 开关回退。
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.chronos import TimeCore as _tc  # noqa: E402
from nucleus.field.InfoField import InfoField  # noqa: E402

_TICKS = ("runtime.metrics.collect", "metrics.collector.tick", "snapshot.rotate.tick")


def _field():
    """裸装一个 InfoField：只装判定要用的三件（锁 / 条件表 / 匹配函数是静态的）。"""
    _f = InfoField.__new__(InfoField)
    _f._lock = threading.RLock()
    _f._conditions = {}
    return _f


def _cond(organ, event_types):
    return {"condition": {"organ_name": organ, "event_types": list(event_types)}}


def test_m380_d1_config_really_holds_these_three_ticks():
    """★先钉前提：三个 tick 确实来自 config 表，不是代码里拼出来的。"""
    _cfg = getattr(config, "TIMECORE_SCHEDULE_CONFIG", {}) or {}
    _ets = {k: (v or {}).get("event_type") for k, v in _cfg.items()}
    for _e in _TICKS:
        assert _e in _ets.values(), "config 表里没有 %s（前提变了，需重新定位）" % _e


def test_m380_d1_no_condition_means_no_consumer():
    _f = _field()
    for _e in _TICKS:
        assert _f.has_real_consumer(_e) is False


def test_m380_d1_universal_condition_is_not_a_consumer():
    """反控：血管那种 `["*"]` 万能条件不算消费者（264刀1 口径）。"""
    _f = _field()
    _f._conditions["cond:血管"] = _cond("血管", ["*"])
    assert _f.has_real_consumer("runtime.metrics.collect") is False


def test_m380_d1_real_condition_counts():
    _f = _field()
    _f._conditions["cond:指标采集器"] = _cond("指标采集器", ["runtime.metrics.*"])
    assert _f.has_real_consumer("runtime.metrics.collect") is True
    assert _f.has_real_consumer("snapshot.rotate.tick") is False


def test_m380_d1_timecore_gate_blocks_and_allows():
    class _F:
        def __init__(self, ok):
            self.ok = ok
            self.seen = []

        def has_real_consumer(self, et):
            self.seen.append(et)
            return self.ok

    assert _tc._m380_tick_consumer_ok(_F(False), "runtime.metrics.collect") is False
    assert _tc._m380_tick_consumer_ok(_F(True), "runtime.metrics.collect") is True


def test_m380_d1_gate_switch_off_always_allows(monkeypatch):
    class _F:
        @staticmethod
        def has_real_consumer(_et):
            return False

    monkeypatch.setattr(config, "ENABLE_M380_TICK_CONSUMER_GATE", False, raising=False)
    assert _tc._m380_tick_consumer_ok(_F(), "runtime.metrics.collect") is True


def test_m380_d1_missing_api_is_conservative():
    """InfoField 老版本没有这个方法时必须照发（宁可多发，不可误封）。"""
    class _Old:
        pass

    assert _tc._m380_tick_consumer_ok(_Old(), "runtime.metrics.collect") is True


def test_m380_d1_gated_note_is_once():
    _tc._M380_GATED_TICKS.clear()
    _tc._m380_note_gated("runtime_metrics", "runtime.metrics.collect")
    _tc._m380_note_gated("runtime_metrics", "runtime.metrics.collect")
    assert _tc._M380_GATED_TICKS == {"runtime_metrics"}
    _tc._M380_GATED_TICKS.clear()
