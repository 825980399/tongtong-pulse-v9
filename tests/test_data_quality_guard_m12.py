# -*- coding: utf-8 -*-
"""主线第12批 T4：DataQualityGuard 单节点检查 + 三调用点接线 —— 门控单测。

覆盖：
  - check_node 返回结构 / clean 判定
  - 采样日志（每 sample_every 次一条；异常节点立即记）
  - get_check_stats 累计统计
  - 灰度关闭时节点池写入零副作用
  - 三调用点开关存在性
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
from nucleus.knowledge.DataQualityGuard import (  # noqa: E402
    get_data_quality_guard,
    reset_data_quality_guard,
)


def setup_function(_fn):
    reset_data_quality_guard()


def test_check_node_returns_structure():
    g = get_data_quality_guard()
    r = g.check_node(
        {"node_id": "n1", "value": "Python 异步编程的核心是事件循环与协程调度",
         "space_path": "/编程开发/Python", "keywords": ["Python", "异步", "协程"]},
        context="单测")
    assert set(r.keys()) >= {"flag", "reason", "checked", "logged"}
    assert r["flag"] in ("clean", "suspect", "polluted")
    assert r["checked"] == 1


def test_check_node_never_writes_flag():
    """check_node 只判定，不得写标记（热路径安全）。"""
    g = get_data_quality_guard()
    node = {"node_id": "n2", "value": "测试内容", "space_path": "/测试",
            "keywords": ["A", "B", "C"]}
    g.check_node(node, context="单测")
    assert "quality_flag" not in node


def test_check_node_sampling_interval():
    """采样：sample_every=20 时，第 20 次必记日志。"""
    g = get_data_quality_guard()
    g._check_sample_every = 20
    logged_at = []
    for i in range(20):
        r = g.check_node({"node_id": f"s{i}", "value": f"正常内容{i}",
                          "space_path": "/测试", "keywords": ["A", "B", "C"]})
        if r["logged"]:
            logged_at.append(r["checked"])
    assert 20 in logged_at


def test_check_node_accumulates_stats():
    g = get_data_quality_guard()
    for i in range(5):
        g.check_node({"node_id": f"c{i}", "value": f"内容{i}", "space_path": "/测试",
                      "keywords": ["A", "B", "C"]})
    st = g.get_check_stats()
    assert st["checked"] == 5
    assert st["sample_every"] >= 1


def test_check_node_handles_none_gracefully():
    g = get_data_quality_guard()
    r = g.check_node(None, context="单测")
    assert r["flag"] == "clean"
    assert r["checked"] >= 1


def test_checkpoint_switch_exists():
    assert hasattr(cfg, "ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS")
    assert isinstance(cfg.ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS, bool)


def test_pool_add_disabled_zero_side_effect():
    """灰度关闭时，节点池写入不触发 Guard 检查。"""
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    g = get_data_quality_guard()
    before = g.get_check_stats()["checked"]

    old = cfg.ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS
    cfg.ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS = False
    try:
        pool = PulseNodePool()
        node = PulseNode(value="灰度关闭测试内容", keywords=["A", "B", "C"],
                         source_organ="测试", space_path="/测试")
        pool.add(node)
    finally:
        cfg.ENABLE_DATA_QUALITY_GUARD_CHECKPOINTS = old

    after = g.get_check_stats()["checked"]
    assert after == before
