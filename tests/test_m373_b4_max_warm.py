# -*- coding: utf-8 -*-
"""第373批 刀4 · 冷化阈值 max_warm 调档（P2 · 与停机窗错峰）

票：`T-372-冷化阈值调档-1`

背景（372 刀12 §2.3 已实证）：`max_warm=50000` 是实测节点 19087 的 2.6 倍
⇒ `_enforce_capacity` 的 `while len(self._warm) > self._max_warm` 永不成立
⇒ 驱逐零事件 ⇒ 冷池恒空 ⇒ 回载链四个写入口一个都走不到
⇒ `349回载` 日志 0 行、`reload_stats.json` 从未生成。

★本刀的**唯一**硬约束：`PulseNodePool.__init__` 默认值与
`config.RUNTIME_PARAMS["nodepool_max_warm"]` **必须同值**。只改一处等于没改——
`refresh_runtime_params()` 会拿 config 覆盖默认值，热重载一跑就回旧值。
"""

import inspect
from pathlib import Path

import pytest

import config as config_mod
from nucleus.mnemosyne.PulseNodePool import PulseNodePool

#: 373 任务书给的区间（9500—15000 保守端）
_TASKBOOK_LO, _TASKBOOK_HI = 9500, 15000
#: 372 刀12 实测生产池节点数（调档依据）
_MEASURED_NODES = 19087

_SRC = Path("nucleus/mnemosyne/PulseNodePool.py")


def test_01_default_is_taskbook_conservative_end():
    """默认值必须落在任务书区间，且取保守端（区间上限）。"""
    d = inspect.signature(PulseNodePool.__init__).parameters["max_warm"].default
    assert d == 15000, f"__init__ max_warm 默认值 = {d}（期望 15000 保守端）"
    assert _TASKBOOK_LO <= d <= _TASKBOOK_HI, f"{d} 越出任务书区间 [{_TASKBOOK_LO},{_TASKBOOK_HI}]"


def test_02_config_and_default_must_match():
    """★两处必须同值：否则 refresh_runtime_params() 热重载即回退。"""
    d = inspect.signature(PulseNodePool.__init__).parameters["max_warm"].default
    c = config_mod.RUNTIME_PARAMS.get("nodepool_max_warm")
    assert c is not None, "config.RUNTIME_PARAMS 里没有 nodepool_max_warm"
    assert c == d, (
        f"config={c} 与默认值={d} 不一致 ⇒ refresh_runtime_params() 会把调档覆盖回去")


def test_03_threshold_actually_overflows_measured_pool():
    """调档的意义：阈值必须**小于**实测节点数，否则溢出条件永不成立。"""
    d = inspect.signature(PulseNodePool.__init__).parameters["max_warm"].default
    assert d < _MEASURED_NODES, (
        f"max_warm={d} 仍 >= 实测节点 {_MEASURED_NODES} ⇒ 温池永不溢出，冷化链仍是死路")
    _overflow = _MEASURED_NODES - d
    assert _overflow >= _MEASURED_NODES * 0.10, (
        f"溢出量 {_overflow} 不足实测节点的 10% ⇒ 事件太稀疏，等于没跑起来")


def test_04_does_not_drain_warm_pool():
    """保守性：不得把温池一次性抽干（9500 会溢出 50%，驱逐过猛）。"""
    d = inspect.signature(PulseNodePool.__init__).parameters["max_warm"].default
    _overflow_ratio = (_MEASURED_NODES - d) / _MEASURED_NODES
    assert _overflow_ratio <= 0.30, (
        f"溢出比例 {_overflow_ratio:.0%} 过大 ⇒ 温池被抽干，回载压力反噬")


def test_05_eviction_judgement_not_loosened():
    """判据本体一字未动：仍是 `len(self._warm) > self._max_warm`。"""
    src = _SRC.read_text(encoding="utf-8")
    assert "while len(self._warm) > self._max_warm:" in src, "温池溢出判据被改动（本刀严禁）"
    assert "while len(self._hot) > self._max_hot:" in src, "热池溢出判据被改动（本刀严禁）"


def test_06_runtime_hook_still_usable():
    """运行时旋钮保留（压测时临时下探、验完调回）。"""
    p = PulseNodePool.__new__(PulseNodePool)
    p._warm = {}
    p._max_warm = 50000
    assert p.set_max_warm(15000, reason="test") == 15000
    # 非法值仍被拒（不静默吞）
    assert p.set_max_warm(0) == 15000
    assert p.set_max_warm("x") == 15000


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
