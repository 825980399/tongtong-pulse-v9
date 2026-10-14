# -*- coding: utf-8 -*-
"""201批 刀1 隔离单测：pulse_tracer 环窗参数化（config 驱动 + 非法值回退）。

覆盖：
  1. 默认（config 键为默认值）→ 三值等于改造前实测值，零行为变化
  2. config 显式调大 → 三值随之生效
  3. 键缺失 / 非数值 / 布尔 / 负数 / 零 → 一律回退默认，绝不抛
  4. 事件环裁剪按配置生效
  5. 判定窗按配置生效（大窗→不判孤儿；零窗→判孤儿）
  6. 显式传 min_age 优先于配置（含 min_age=0.0 合法值不被误当成「未传」）
  7. config.py 三键已声明且默认值符合任务书/零回归口径
"""
import time

import config
from utils import pulse_tracer as pt


def _emit(event_type="evt.ring", age=0.0, matched=None):
    """构造一条 emit 事件；age=年龄秒数（0=刚刚发射）。"""
    return {
        "timestamp": time.time() - age,
        "type": "emit",
        "organ": "OrganA",
        "event_type": event_type,
        "layer": "L0",
        "pulse_id": "p1",
        "matched_organs": matched if matched is not None else [],
    }


# ---------------------------------------------------------------- 1 默认零回归
def test_defaults_preserve_current_behavior():
    # ★210批：环容/孤儿上限 config 默认值已上调（1000 槽 / 200 条），测试跟随任务书口径
    assert pt._ring_size() == 1000
    assert pt._max_orphans() == 200
    # 判定窗回退默认仍 1.5（210批未动）
    assert pt._min_age_sec() == 1.5


# ---------------------------------------------------------------- 2 config 生效
def test_config_overrides_take_effect(monkeypatch):
    monkeypatch.setattr(config, "PULSE_TRACER_EVENT_RING_SIZE", 1000)
    monkeypatch.setattr(config, "PULSE_TRACER_MAX_ORPHANS", 500)
    monkeypatch.setattr(config, "PULSE_TRACER_MIN_AGE_SEC", 3)
    assert pt._ring_size() == 1000
    assert pt._max_orphans() == 500
    # 整型的 3 应被规整为浮点 3.0
    assert pt._min_age_sec() == 3.0


# ---------------------------------------------------------------- 3 非法值回退
def test_invalid_config_falls_back_to_default(monkeypatch):
    # 环容量 / 孤儿上限：必须为正整型 —— 0 与负数均非法（零容量环无意义）
    for bad in [None, "abc", -1, 0, True, False, [], {}]:
        monkeypatch.setattr(config, "PULSE_TRACER_EVENT_RING_SIZE", bad)
        monkeypatch.setattr(config, "PULSE_TRACER_MAX_ORPHANS", bad)
        assert pt._ring_size() == 200, "环容量非法值 %r 未回退" % (bad,)
        assert pt._max_orphans() == 100, "孤儿上限非法值 %r 未回退" % (bad,)


def test_min_age_invalid_falls_back_but_zero_is_valid(monkeypatch):
    # 判定窗：负值 / 非数值 / 布尔 非法 → 回退 1.5
    for bad in [None, "abc", -1, -0.5, True, False, [], {}]:
        monkeypatch.setattr(config, "PULSE_TRACER_MIN_AGE_SEC", bad)
        assert pt._min_age_sec() == 1.5, "判定窗非法值 %r 未回退" % (bad,)
    # ★但 0 是合法值（零窗 = 立即判定，200批单测即用 min_age=0.0），不得被误判为非法
    monkeypatch.setattr(config, "PULSE_TRACER_MIN_AGE_SEC", 0)
    assert pt._min_age_sec() == 0.0, "判定窗 0 应被接纳（零窗语义），而非回退默认"


def test_missing_keys_fall_back_to_default(monkeypatch):
    for key in ("PULSE_TRACER_EVENT_RING_SIZE", "PULSE_TRACER_MAX_ORPHANS",
                "PULSE_TRACER_MIN_AGE_SEC"):
        monkeypatch.delattr(config, key)
    assert pt._ring_size() == 200
    assert pt._max_orphans() == 100
    assert pt._min_age_sec() == 1.5


# ---------------------------------------------------------------- 4 环裁剪生效
def test_ring_trim_honors_config(monkeypatch):
    monkeypatch.setattr(config, "PULSE_TRACER_EVENT_RING_SIZE", 5)
    monkeypatch.setattr(pt, "_events", [])
    monkeypatch.setattr(pt, "_ensure_flusher", lambda: None)
    for i in range(30):
        pt.log_emit("OrganA", "evt.ring%d" % i, "L0", "p%d" % i)
    assert len(pt._events) == 5, "事件环应裁剪到配置容量 5，实际 %d" % len(pt._events)


# ---------------------------------------------------------------- 5 判定窗生效
def test_min_age_config_drives_detection(monkeypatch):
    monkeypatch.setattr(config, "PULSE_TRACER_MIN_AGE_SEC", 9999)
    # 大窗：事件刚发射（年龄 0）→ 尚未超窗 → 不判孤儿
    assert pt._detect_orphans([_emit(age=0.0)]) == []
    monkeypatch.setattr(config, "PULSE_TRACER_MIN_AGE_SEC", 0)
    # 零窗：任何 emit 都视为「已超窗」→ 判孤儿
    assert len(pt._detect_orphans([_emit(age=0.0)])) == 1


# ---------------------------------------------------------------- 6 显式优先
def test_explicit_min_age_overrides_config(monkeypatch):
    monkeypatch.setattr(config, "PULSE_TRACER_MIN_AGE_SEC", 9999)
    # 显式传 0.0 必须生效（不能因配置是大窗而被忽略，也不能被当成「未传」）
    assert len(pt._detect_orphans([_emit(age=0.0)], min_age=0.0)) == 1
    # 显式传大窗 → 不判孤儿
    assert pt._detect_orphans([_emit(age=0.0)], min_age=9999) == []


# ---------------------------------------------------------------- 7 三键声明
def test_config_keys_declared_with_defaults():
    # ★210批：config 默认值已上调（孤儿上限 200 / 环容 1000），判定窗仍 1.5
    assert config.PULSE_TRACER_MAX_ORPHANS == 200
    assert config.PULSE_TRACER_EVENT_RING_SIZE == 1000
    assert config.PULSE_TRACER_MIN_AGE_SEC == 1.5
