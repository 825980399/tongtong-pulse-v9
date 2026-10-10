# -*- coding: utf-8 -*-
"""179批 刀2 门控单测：Standing Orders 四字段（O-B2，隔离单跑）。

验证：
  - 四字段（grant/trigger/gate/escalate）词面可检且结构落地；
  - 闸字段引用既有 O-B1 键（OB1_SANDBOX_LEVEL 等）时校验通过，否则不通过；
  - 总开关关闭 → load_orders 返回空列表（零回归）。
不依赖框架运行（曈曈停机态亦可跑）。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.field import standing_orders as so  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    yield
    config.ENABLE_STANDING_ORDERS = False
    config.STANDING_ORDERS = []


def test_four_fields_present():
    _o = so.StandingOrder(
        grant="patch_apply",
        trigger="night_orch",
        gate="o_b1_sandbox=strict",
        escalate="severity>=P0",
    )
    assert _o.grant == "patch_apply"
    assert _o.trigger == "night_orch"
    assert _o.gate == "o_b1_sandbox=strict"
    assert _o.escalate == "severity>=P0"


def test_gate_references_ob1_true():
    # gate 引用既有 O-B1 键名 OB1_SANDBOX_LEVEL
    assert so.gate_references_ob1("o_b1_sandbox=strict") is True


def test_gate_references_ob1_false():
    assert so.gate_references_ob1("unknown_gate_x") is False


def test_load_disabled_returns_empty():
    config.ENABLE_STANDING_ORDERS = False
    config.STANDING_ORDERS = [{"grant": "g", "trigger": "t", "gate": "o_b1_sandbox=strict", "escalate": "e"}]
    assert so.load_orders() == []


def test_load_enabled_parses_four_fields():
    config.ENABLE_STANDING_ORDERS = True
    config.STANDING_ORDERS = [{"grant": "g", "trigger": "t", "gate": "o_b1_sandbox=strict", "escalate": "e"}]
    _orders = so.load_orders()
    assert len(_orders) == 1
    assert _orders[0].gate == "o_b1_sandbox=strict"
    assert so.gate_references_ob1(_orders[0].gate) is True


def test_four_field_keywords_present():
    src = open(so.__file__, "r", encoding="utf-8").read()
    for _kw in ("grant", "trigger", "gate", "escalate"):
        assert _kw in src, f"四字段词面缺失: {_kw}"
