# -*- coding: utf-8 -*-
"""
test_weight_map_m202.py —— 第202批 刀2 隔离单测

刀2：第二层权重表示层（四元组化）
    nucleus/synapsys/ResonanceDetector.py 新增 weight_map
    键=(pre_id, post_id)，值=(weight, delay, encoding_type)

验收要点：
    1. weight_map 默认为空字典 → 现有共振检测逻辑零回归。
    2. get_weight / set_weight / update_weight 增删改查正确。
    3. update_weight 对未登记突触以 DEFAULT_WEIGHT 为起点累加。
    4. 既有共振记录逻辑（record_resonance）不受影响。
"""
from nucleus.synapsys.ResonanceDetector import (
    DEFAULT_DELAY,
    DEFAULT_ENCODING_TYPE,
    DEFAULT_WEIGHT,
    ResonanceDetector,
)


def test_defaults():
    """默认语义常量与默认空映射。"""
    assert DEFAULT_WEIGHT == 1.0
    assert DEFAULT_DELAY == 0.0
    assert DEFAULT_ENCODING_TYPE == "rate"

    d = ResonanceDetector()
    stats = d.get_weight_stats()
    assert stats["synapses"] == 0, "weight_map 默认应为空字典"
    assert stats["total_weight"] == 0


def test_get_weight_default_when_absent():
    """未登记突触查询返回默认语义三元组。"""
    d = ResonanceDetector()
    assert d.get_weight("a", "b") == (DEFAULT_WEIGHT, DEFAULT_DELAY, DEFAULT_ENCODING_TYPE)


def test_set_and_get_weight():
    """set_weight 后 get_weight 能取回同一四元组。"""
    d = ResonanceDetector()
    d.set_weight("心", "脑", 0.75, delay=0.02, encoding_type="temporal")
    assert d.get_weight("心", "脑") == (0.75, 0.02, "temporal")


def test_set_weight_defaults():
    """set_weight 的 delay / encoding_type 默认值。"""
    d = ResonanceDetector()
    d.set_weight("x", "y", 2.5)
    assert d.get_weight("x", "y") == (2.5, DEFAULT_DELAY, DEFAULT_ENCODING_TYPE)


def test_set_weight_overwrite():
    """重复 set_weight 覆盖旧值。"""
    d = ResonanceDetector()
    d.set_weight("x", "y", 1.0)
    d.set_weight("x", "y", 3.0)
    assert d.get_weight("x", "y")[0] == 3.0
    assert d.get_weight_stats()["synapses"] == 1, "覆盖不应新增条目"


def test_update_weight_on_registered():
    """已登记突触按增量更新，delay / encoding_type 保持不变。"""
    d = ResonanceDetector()
    d.set_weight("a", "b", 1.0, delay=0.05, encoding_type="graded")
    new_w = d.update_weight("a", "b", 0.4)
    assert new_w == 1.4
    w, delay, enc = d.get_weight("a", "b")
    assert (w, delay, enc) == (1.4, 0.05, "graded"), "增量更新不应改动 delay/encoding_type"


def test_update_weight_on_unregistered():
    """未登记突触以 DEFAULT_WEIGHT 为起点累加（STDP 预留语义）。"""
    d = ResonanceDetector()
    new_w = d.update_weight("p", "q", -0.3)
    assert new_w == DEFAULT_WEIGHT - 0.3
    assert d.get_weight("p", "q")[0] == DEFAULT_WEIGHT - 0.3


def test_update_weight_no_self_deadlock():
    """★回归：update_weight 不得在持锁内再调 get_weight（Lock 非可重入会自死锁）。"""
    d = ResonanceDetector()
    d.set_weight("a", "b", 1.0)
    # 若实现嵌套取锁，此调用会永久阻塞（非可重入 Lock）
    assert d.update_weight("a", "b", 0.1) == 1.1


def test_weight_stats_aggregation():
    """统计聚合正确。"""
    d = ResonanceDetector()
    d.set_weight("a", "b", 1.0)
    d.set_weight("c", "d", 2.0)
    stats = d.get_weight_stats()
    assert stats["synapses"] == 2
    assert stats["total_weight"] == 3.0


def test_weight_map_independent_of_enabled_flag():
    """权重映射不依赖功能开关（默认关闭下仍可用，纯数据结构）。"""
    d = ResonanceDetector()
    assert d.is_enabled() is False, "ResonanceDetector 默认关闭（既有行为）"
    d.set_weight("a", "b", 0.5)
    assert d.get_weight("a", "b")[0] == 0.5


def test_existing_resonance_logic_unaffected():
    """既有共振记录逻辑零回归。"""
    d = ResonanceDetector()
    # 默认关闭时记录应被忽略（既有行为）
    d.record_resonance("心脏", "heart.beat")
    assert d.get_stats()["total_detections"] == 0

    d.enable()
    d.record_resonance("心脏", "heart.beat")
    d.record_resonance("血管", "heart.beat")
    d.record_co_response(["心脏", "血管"], "heart.beat")

    stats = d.get_stats()
    assert stats["total_detections"] == 2
    assert stats["tracked_organs"] == 2
    assert stats["co_response_pairs"] == 1
    # 权重映射与共振记录互不影响
    assert d.get_weight_stats()["synapses"] == 0
