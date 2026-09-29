"""往期批次 相关任务 回归测试：节点池硬上限（既有 _enforce_capacity）行为锁定。

注意：节点池硬上限 + 超上限淘汰冷节点已由既有实现覆盖：
- 热/温池分级硬上限 _max_hot / _max_warm
- _enforce_capacity()：热池超限降级到温池、温池超限降级/驱逐到冷存
- 冷池上限 _max_cold_cache + _enforce_cold_cache()（冷存开启时驱逐到磁盘）
本测试锁定既有行为，防止回归；不引入新逻辑（避免重复打补丁 / 冗余风险代码）。
"""
import time
import types

from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.mnemosyne.PulseNodePool import PulseNodePool


def _mk(nid, level, t):
    return types.SimpleNamespace(node_id=nid, evol_level=level,
                                  last_activated=t, importance="C")


def test_enforce_capacity_bounds_tiers_and_keeps_l3_and_conserves_total():
    pool = PulseNodePool(max_hot=3, max_warm=5)
    pool._cold_storage_enabled = False  # 冷存关：仅降级不落盘，总节点守恒
    now = time.time()

    # 热池塞 6 个非 L3（应降级到 <=3）
    for i in range(6):
        n = _mk(f"h{i}", PulseNode.EVOL_L2, now - i)
        pool._hot[n.node_id] = n
    # 温池塞 10 个非 L1/L3（应降级到 <=5，溢出进冷池）
    for i in range(10):
        n = _mk(f"w{i}", PulseNode.EVOL_L1, now - i)
        pool._warm[n.node_id] = n
    # 2 个 L3 生命线（必须永不降级）
    for i in range(2):
        n = _mk(f"l{i}", PulseNode.EVOL_L3, now - i)
        pool._hot[n.node_id] = n

    pool._enforce_capacity()

    assert len(pool._hot) <= 3, f"热池应 <=3, got {len(pool._hot)}"
    assert len(pool._warm) <= 5, f"温池应 <=5, got {len(pool._warm)}"
    # L3 必须全部保留
    assert all(f"l{i}" in pool._hot for i in range(2))
    # 冷存关：仅分级降级，总节点数守恒（18 = 6+10+2）
    total = (len(pool._hot) + len(pool._warm)
             + len(pool._cold) + len(pool._instinct))
    assert total == 18, f"总节点应守恒=18(6+10+2), got {total}"


def test_enforce_capacity_never_demotes_l3_under_pressure():
    pool = PulseNodePool(max_hot=1, max_warm=1)
    pool._cold_storage_enabled = False
    now = time.time()
    # 仅 3 个 L3，热池上限 1 —— L3 永不降级
    for i in range(3):
        n = _mk(f"L3_{i}", PulseNode.EVOL_L3, now - i)
        pool._hot[n.node_id] = n
    pool._enforce_capacity()
    assert len(pool._hot) == 3, "L3 不应被降级，即便超上限"
