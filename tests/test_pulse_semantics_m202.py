# -*- coding: utf-8 -*-
"""
test_pulse_semantics_m202.py —— 第202批 刀1 隔离单测

刀1：第一层脉冲语义层（芯片无关中间表示）
    nucleus/pulse_types.py 的 Pulse 契约新增三个可选字段
    amplitude / spike_type / wire_format（NotRequired 声明）。

验收要点：
    1. 三字段已在 Pulse 契约中声明，且为「可选」（NotRequired）
       —— 既有 emit 未提供时类型检查仍通过。
    2. 默认语义常量值 = 1.0 / 'rate' / 'json'。
    3. 向后兼容：TypedDict 运行时不校验、不注入 →
       不含三字段的既有脉冲结构仍是合法 Pulse（零行为变化）。
"""
import typing

from nucleus.pulse_types import (
    DEFAULT_AMPLITUDE,
    DEFAULT_SPIKE_TYPE,
    DEFAULT_WIRE_FORMAT,
    SPIKE_TYPES,
    WIRE_FORMATS,
    Pulse,
)


def test_three_fields_declared():
    """三字段已在 Pulse 契约中声明。"""
    ann = typing.get_type_hints(Pulse, include_extras=True)
    for f in ("amplitude", "spike_type", "wire_format"):
        assert f in ann, "Pulse 契约缺少字段 %s" % f
    assert ann["amplitude"] is float
    assert ann["spike_type"] is str
    assert ann["wire_format"] is str


def test_three_fields_are_optional_notrequired():
    """三字段为 NotRequired（可选）—— 这是 TypedDict 版的「默认值」表达。

    TypedDict 语法不支持字段默认值，故用 NotRequired 声明可选，
    既有 emit 未提供该字段时静态类型检查仍通过，运行时零行为变化。
    """
    optional = getattr(Pulse, "__optional_keys__", frozenset())
    for f in ("amplitude", "spike_type", "wire_format"):
        assert f in optional, "字段 %s 应为可选（NotRequired），实际为必填" % f


def test_preexisting_fields_still_required():
    """既有字段仍是必填（未被本次改动降级为可选）。"""
    required = getattr(Pulse, "__required_keys__", frozenset()) or set(
        typing.get_type_hints(Pulse)
    ) - set(getattr(Pulse, "__optional_keys__", frozenset()))
    for f in ("pulse_id", "source_organ", "event_type", "status"):
        assert f in required, "既有字段 %s 不应变为可选" % f


def test_default_semantic_constants():
    """默认语义常量值 = 1.0 / 'rate' / 'json'。"""
    assert DEFAULT_AMPLITUDE == 1.0
    assert DEFAULT_SPIKE_TYPE == "rate"
    assert DEFAULT_WIRE_FORMAT == "json"


def test_enum_domains_contain_defaults():
    """取值域元组包含默认语义值（枚举预留自洽）。"""
    assert DEFAULT_SPIKE_TYPE in SPIKE_TYPES
    assert DEFAULT_WIRE_FORMAT in WIRE_FORMATS
    assert SPIKE_TYPES == ("rate", "temporal", "graded")
    assert WIRE_FORMATS == ("json", "binary")


def test_backward_compat_dict_without_new_fields():
    """向后兼容：不含三字段的既有脉冲结构仍是合法 Pulse（运行时零校验）。"""
    legacy = {
        "pulse_id": "pulse:心脏:heart.beat:1:0",
        "source_organ": "心脏",
        "event_type": "heart.beat",
        "priority": 5,
        "layer": "L1",
        "intent": "notify",
        "timestamp_ns": 1,
        "time_dim": {"created_at": 0.0},
        "space_dim": {"path": ""},
        "state_dim": {"state": "active"},
        "logic_dim": {"call_chain": ["心脏"]},
        "memory_dim": {"frequency_signature": 0.0, "linked_nodes": [], "hebbian_weight": 0.0},
        "payload": {},
        "ttl_ns": 5_000_000_000,
        "status": "created",
    }
    # TypedDict 运行时不做校验，直接赋值给 Pulse 注解变量即可通过
    p: Pulse = legacy  # noqa: F841
    # 关键：本批没有给既有结构注入任何新键
    for f in ("amplitude", "spike_type", "wire_format"):
        assert f not in legacy, "不得向既有脉冲结构注入新键 %s" % f


def test_consumer_default_fallback_pattern():
    """消费方兜底范式：pulse.get(k, DEFAULT_X) 能拿到默认语义值。"""
    pulse = {"event_type": "heart.beat"}
    assert pulse.get("amplitude", DEFAULT_AMPLITUDE) == 1.0
    assert pulse.get("spike_type", DEFAULT_SPIKE_TYPE) == "rate"
    assert pulse.get("wire_format", DEFAULT_WIRE_FORMAT) == "json"


def test_new_fields_accept_chip_values():
    """三字段可承载芯片语义值（SNN 膜电位 / 时间编码 / 二进制序列化）。"""
    pulse: Pulse = {  # type: ignore[typeddict-item]
        "pulse_id": "p",
        "source_organ": "s",
        "event_type": "e",
        "priority": 1,
        "layer": "L1",
        "intent": "notify",
        "timestamp_ns": 1,
        "time_dim": {"created_at": 0.0},
        "space_dim": {"path": ""},
        "state_dim": {"state": "active"},
        "logic_dim": {"call_chain": []},
        "memory_dim": {"frequency_signature": 0.0, "linked_nodes": [], "hebbian_weight": 0.0},
        "payload": {},
        "ttl_ns": 1,
        "status": "created",
        "amplitude": 0.35,
        "spike_type": "temporal",
        "wire_format": "binary",
    }
    assert pulse["amplitude"] == 0.35
    assert pulse["spike_type"] == "temporal"
    assert pulse["wire_format"] == "binary"
