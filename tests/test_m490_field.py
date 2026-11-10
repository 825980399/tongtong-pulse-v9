# -*- coding: utf-8 -*-
"""490-1 / 490-2 / 490-4 门控单测：每个开关 on/off 均逐字可回退。"""
import time

import pytest

import config
from nucleus.const import ChatEvent, DigestEvent, HeartEvent, HormonesEvent
from nucleus.field.InfoField import InfoField

_SWITCHES = (
    "ENABLE_M490_PULSE_ID_BACKFILL",
    "ENABLE_M490_COND_INDEX",
    "ENABLE_M490_DEDUP_BY_EVENT",
    "ENABLE_M488_EVENT_LEDGER",
    "ENABLE_M488_FANOUT_STAT",
    "ENABLE_M489_HANDLER_TIMING",
    "ENABLE_M489_IDLE_DETECT",
)


@pytest.fixture
def cfg():
    _saved = {k: getattr(config, k, None) for k in _SWITCHES}
    yield config
    for k, v in _saved.items():
        if v is None:
            if hasattr(config, k):
                delattr(config, k)
        else:
            setattr(config, k, v)


@pytest.fixture
def field(cfg):
    cfg.ENABLE_M488_EVENT_LEDGER = False
    cfg.ENABLE_M488_FANOUT_STAT = False
    cfg.ENABLE_M489_HANDLER_TIMING = False
    cfg.ENABLE_M489_IDLE_DETECT = False
    return InfoField(max_history=50)


class _FP:
    """让业务指纹稳定：只有 pulse_core 存在时 publish 才用指纹做去重键。"""

    def business_fingerprint(self, pulse):
        return "fp:" + str(pulse.get("event_type"))


# ---------------------------------------------------------------- 490-1
def test_4901_backfill_on(field, cfg):
    cfg.ENABLE_M490_PULSE_ID_BACKFILL = True
    cfg.ENABLE_M490_DEDUP_BY_EVENT = False
    p = {"event_type": ChatEvent.MESSAGE, "source_organ": "TimeCore", "priority": 5}
    r = field.publish(p)
    assert r["pulse_id"].startswith("pulse:bf:TimeCore:")
    assert p["pulse_id"] == r["pulse_id"]          # 写回原字典
    assert field._pulse_id_backfilled.get("TimeCore") == 1
    field.publish({"event_type": ChatEvent.MESSAGE, "source_organ": "TimeCore",
                   "priority": 5})
    field.publish({"event_type": ChatEvent.MESSAGE, "source_organ": "Other",
                   "priority": 5})
    assert field._pulse_id_backfilled == {"TimeCore": 2, "Other": 1}


def test_4901_backfill_off(field, cfg):
    cfg.ENABLE_M490_PULSE_ID_BACKFILL = False
    cfg.ENABLE_M490_DEDUP_BY_EVENT = False
    p = {"event_type": ChatEvent.MESSAGE, "source_organ": "TimeCore", "priority": 5}
    r = field.publish(p)
    assert r["pulse_id"] == "?"
    assert "pulse_id" not in p                      # 逐字回改前：不写回
    assert field._pulse_id_backfilled == {}


# ---------------------------------------------------------------- 490-2
def _register_all(f):
    f.register_condition("exactA", event_types=[ChatEvent.MESSAGE])
    f.register_condition("exactB", event_types=[DigestEvent.KNOWLEDGE])
    f.register_condition("wild", event_types=["heart.*"])
    f.register_condition("star", event_types=["*"])
    f.register_condition("univ", event_types=[])


def test_4902_index_equals_linear(field, cfg):
    """索引路径与线性路径的 matched_organs / total_conditions 完全一致。"""
    cfg.ENABLE_M490_PULSE_ID_BACKFILL = False
    cfg.ENABLE_M490_DEDUP_BY_EVENT = False
    _register_all(field)
    events = [ChatEvent.MESSAGE, DigestEvent.KNOWLEDGE,
              HeartEvent.BEAT, "unknown.event"]

    cfg.ENABLE_M490_COND_INDEX = False
    linear = [field.publish({"pulse_id": f"L{i}", "event_type": et, "priority": 5})
              for i, et in enumerate(events)]
    cfg.ENABLE_M490_COND_INDEX = True
    indexed = [field.publish({"pulse_id": f"I{i}", "event_type": et, "priority": 5})
               for i, et in enumerate(events)]

    for _lin, _idx in zip(linear, indexed):
        assert _idx["matched_organs"] == _lin["matched_organs"]
        assert _idx["total_conditions"] == _lin["total_conditions"]


def test_4902_off_falls_back_to_full_snapshot(field, cfg):
    cfg.ENABLE_M490_COND_INDEX = False
    _register_all(field)
    _snap = tuple(field._conditions.items())
    assert field._m490_match_items(ChatEvent.MESSAGE, _snap) is _snap


def test_4902_unregister_rebuilds_index(field, cfg):
    cfg.ENABLE_M490_COND_INDEX = True
    cid = field.register_condition("temp", event_types=[ChatEvent.MESSAGE])
    assert field._cond_index["count"] == 1
    assert field.unregister_condition(cid) is True
    assert field._cond_index["count"] == 0
    r = field.publish({"pulse_id": "u1", "event_type": ChatEvent.MESSAGE,
                       "priority": 5})
    assert r["matched_organs"] == []


# ---------------------------------------------------------------- 490-4
def _pub(f, et):
    return f.publish({"pulse_id": "x", "event_type": et, "source_organ": "t",
                      "priority": 5, "payload": {"k": "v"}}).get("status")


def test_4904_dedup_windows_on(field, cfg):
    cfg.ENABLE_M490_PULSE_ID_BACKFILL = False
    cfg.ENABLE_M490_DEDUP_BY_EVENT = True
    field.pulse_core = _FP()
    # 对话类窗口 0 ⇒ 两条都执行
    assert _pub(field, ChatEvent.MESSAGE) == "published"
    assert _pub(field, ChatEvent.MESSAGE) == "published"
    # 情绪类 60s 窗口 ⇒ 第二条丢
    assert _pub(field, HormonesEvent.EMOTION_DETECTED) == "published"
    assert _pub(field, HormonesEvent.EMOTION_DETECTED) == "duplicate"
    # 知识类 5s 窗口 ⇒ 第二条丢
    assert _pub(field, DigestEvent.KNOWLEDGE) == "published"
    assert _pub(field, DigestEvent.KNOWLEDGE) == "duplicate"
    # 回拨 6s：知识类(5s)过期重放；情绪类(60s)仍在窗口内
    for k in list(field._processed_pulses):
        if DigestEvent.KNOWLEDGE in k or HormonesEvent.EMOTION_DETECTED in k:
            field._processed_pulses[k] = time.time() - 6.0
    assert _pub(field, DigestEvent.KNOWLEDGE) == "published"
    assert _pub(field, HormonesEvent.EMOTION_DETECTED) == "duplicate"


def test_4904_off_uses_global_ttl(field, cfg):
    cfg.ENABLE_M490_PULSE_ID_BACKFILL = False
    cfg.ENABLE_M490_DEDUP_BY_EVENT = False
    field.pulse_core = _FP()
    assert field._m490_dedup_ttl(ChatEvent.MESSAGE) == field._processed_ttl_seconds
    # 关开关 ⇒ 逐字回旧全局 5s，对话类也会被去重
    assert _pub(field, ChatEvent.MESSAGE) == "published"
    assert _pub(field, ChatEvent.MESSAGE) == "duplicate"
