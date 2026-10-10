# -*- coding: utf-8 -*-
"""162批刀6 · 状态枚举校验 门控单测。

覆盖 任务书判据：
① 构造非法 state 节点 → 入库被拒（返回空串）并留 WARNING 日志；
② 合法三类（active / dormant / locked）照常入库；
③ 既有快照/加载测试全绿——3 合法 state 经 to_dict / from_dict 往返一致。

落点：PulseNode.VALID_STATES + is_valid_state；PulseNodePool.add 入库前闸门。
登记册侧 check_pending_register.py:25 合法枚举不动。
"""
import pytest

import nucleus.mnemosyne.PulseNodePool as PNP
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.mnemosyne.PulseNodePool import PulseNodePool


def _make_node(state="active", idx=0):
    """构造一个节点并把 state 置为指定值（构造函数只产出合法值，故测试手动覆写）。

    idx 用于让每次调用产生唯一 node_id 与关键词，避免触发 PulseNodePool.add
    的内容级去重（_try_merge_duplicate），从而真实验证每类 state 各自入库。
    """
    _n = PulseNode(
        value=f"刀6状态校验测试节点_{state}_{idx}",
        keywords=["test", "k6", f"k6_{state}_{idx}"],
        source_organ="内在世界",
        evol_level=PulseNode.EVOL_L1,
        importance=PulseNode.IMPORTANCE_C,
        abstraction=0.1,
        space_path="/test/k6_state",
    )
    _n.state = state
    return _n


def _capture_warning():
    """临时把模块级 _module_logger.warning 重定向到列表，返回 (list, restore_fn)。"""
    _cap = []
    _ow = PNP._module_logger.warning
    PNP._module_logger.warning = lambda m, *a, **k: _cap.append((m % a) if a else m)
    def _restore():
        PNP._module_logger.warning = _ow
    return _cap, _restore


def test_invalid_state_rejected_and_logged():
    """判据①：非法 state 节点 → 入库被拒（返回空串）并留 WARNING 日志。"""
    pool = PulseNodePool()
    _cap, _restore = _capture_warning()
    try:
        _n = _make_node(state="frozen", idx=99)  # 非法取值，不在 VALID_STATES
        _rid = pool.add(_n)
    finally:
        _restore()
    assert _rid == "", "非法 state 节点应被拒绝入库（返回空串）"
    _joined = " ".join(str(x) for x in _cap)
    assert "刀6状态校验" in _joined, "应记录拒绝入库 WARNING（含『刀6状态校验』标记）"


def test_valid_three_states_stored():
    """判据②：合法三类（active / dormant / locked）照常入库。"""
    pool = PulseNodePool()
    for _i, _st in enumerate(("active", "dormant", "locked")):
        _n = _make_node(state=_st, idx=_i)
        _rid = pool.add(_n)
        assert _rid == _n.node_id, f"{_st} 应正常入库并返回 node_id（实际={_rid!r}）"
        assert (_rid in pool._hot or _rid in pool._warm or _rid in pool._cold), \
            f"{_st} 应存在于某池中"


def test_snapshot_load_roundtrip_valid_states():
    """判据③：既有快照/加载测试全绿——3 合法 state 经 to_dict / from_dict 往返一致。"""
    for _st in ("active", "dormant", "locked"):
        _n = _make_node(state=_st)
        _d = _n.to_dict()
        assert _d["state"] == _st, f"to_dict 应保留 {_st}"
        _r = PulseNode.from_dict(_d)
        assert _r.state == _st, f"from_dict 应保留 {_st}（实际={_r.state!r}）"
