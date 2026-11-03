# -*- coding: utf-8 -*-
"""第434批刀3 单测：线程预算收敛顺序修正（先算 cap 再建池 + 读 l1/l3_threads）。

守两件事：
  ① l1_threads / l3_threads 配置项真的被读取（不再被 cpu 公式覆盖）；
  ② cap 收敛在建池**之前**执行（池的 max_workers 是 cap 后的预算，不是 raw）。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import nucleus.field.InfoField as IF


def _bare_field():
    """不经 __init__ 的裸实例（只测 _init_layer_pools 的池装配，不起 L0 看门狗）。"""
    _f = object.__new__(IF.InfoField)
    _f._layer_pools = {}
    return _f


def test_l1_l3_threads_read_from_config(monkeypatch):
    """l1_threads=4 / l3_threads=2 被读取；cpu 公式（64 核 → 32/16）不得生效。"""
    monkeypatch.setattr(IF, "PULSE_LAYER", {
        "l1_threads": 4, "l2_threads": 8, "l3_threads": 2,
    })
    monkeypatch.setattr(IF.os, "cpu_count", lambda: 64)
    _f = _bare_field()
    _f._init_layer_pools()
    assert _f._layer_pools["L1"]._max_workers == 4, \
        f"L1={_f._layer_pools['L1']._max_workers}（应为 4，读 l1_threads）"
    assert _f._layer_pools["L3"]._max_workers == 2, \
        f"L3={_f._layer_pools['L3']._max_workers}（应为 2，读 l3_threads）"


def test_cap_applied_before_pool_build(monkeypatch):
    """l2_threads=100 超预算 ⇒ 建池前被 cap 压到 6（改前 cap 在建池后，池仍是 100）。"""
    monkeypatch.setattr(IF, "PULSE_LAYER", {
        "l1_threads": 4, "l2_threads": 100, "l3_threads": 2,
    })
    monkeypatch.setattr(IF.os, "cpu_count", lambda: 64)
    _f = _bare_field()
    _f._init_layer_pools()
    assert _f._layer_pools["L2"]._max_workers == 6, \
        f"L2={_f._layer_pools['L2']._max_workers}（应为 6，cap 在建池前压到预算）"
    assert _f._layer_pools["L1"]._max_workers == 4, \
        f"L1={_f._layer_pools['L1']._max_workers}（应为 4，l1_threads 未被 cpu 公式覆盖）"
