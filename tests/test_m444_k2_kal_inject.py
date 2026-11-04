# -*- coding: utf-8 -*-
"""444 刀2：全局 `get_kal()` 注入 node_pool。

443 普查结论：全仓没有 `get_kal(node_pool=...)` 调用 ⇒ 全局单例 `_node_pool` 恒 None
⇒ 所有走全局单例的读取静默拿不到数据。本刀的修法是在装配点注入，而不是逐处改调用方。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.knowledge_access_layer import get_kal
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.mnemosyne.PulseNodePool import PulseNodePool


def _pool_with_node():
    _pool = PulseNodePool()
    _n = PulseNode("444刀2测试节点", keywords=["m444"], space_path="/m444/k2")
    _pool.add(_n)
    return _pool, _n


def test_global_kal_returns_node_after_injection():
    """注入后 `get_kal().get_node()` 必须能拿到真节点（这就是本刀的目标）。"""
    _pool, _n = _pool_with_node()
    get_kal(node_pool=_pool, refresh=True)
    assert get_kal()._node_pool is _pool
    _r = get_kal().get_node(_n.node_id)
    assert _r is not None, "注入后全局 KAL 仍拿不到节点"
    assert getattr(_r, "node_id", None) == _n.node_id


def test_global_kal_query_nodes_works():
    _pool, _ = _pool_with_node()
    get_kal(node_pool=_pool, refresh=True)
    _rows = get_kal().query_nodes(limit=10)
    assert isinstance(_rows, list) and _rows, "query_nodes 仍返回空"


def test_global_kal_count_works():
    _pool, _ = _pool_with_node()
    get_kal(node_pool=_pool, refresh=True)
    assert get_kal().get_node_count() >= 1


def test_main_injects_pool_into_global_kal():
    """装配点必须有注入语句（源码级，防回潮）。"""
    with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as _f:
        _src = _f.read()
    assert "_get_kal_m444(node_pool=self.node_pool)" in _src, "main.py 的全局 KAL 注入被删了"
    assert "[444刀2]" in _src
