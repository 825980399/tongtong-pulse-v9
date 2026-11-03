# -*- coding: utf-8 -*-
"""443 刀3：PulseLiver 的 KAL 取法顺序（self._kal → 全局 → 直连）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.knowledge_access_layer import get_kal
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.mnemosyne.PulseNodePool import PulseNodePool
from organs.body.PulseLiver import PulseLiver


def _liver_with_node():
    _pool = PulseNodePool()
    _n = PulseNode("443刀3测试节点", keywords=["m443"], space_path="/m443/k3")
    _pool.add(_n)
    _k = PulseLiver()
    _k.set_node_pool(_pool)
    return _k, _n


def test_liver_hits_self_kal_without_fallback(monkeypatch):
    """★核心：`self._kal` 必须真命中 —— 改前全局单例没注入 pool，永远回落直连。"""
    _k, _n = _liver_with_node()
    _hit = {"n": 0}
    _orig = _k._m70_direct_get_node

    def _spy(_id):
        _hit["n"] += 1
        return _orig(_id)

    monkeypatch.setattr(_k, "_m70_direct_get_node", _spy, raising=True)
    _r = _k._m70_kal_get_node(_n.node_id)
    assert getattr(_r, "node_id", None) == _n.node_id, "KAL 通道没取到已入池节点"
    assert _hit["n"] == 0, "命中 KAL 时不该再走直连（说明 self._kal 分支失效）"


def test_liver_falls_back_to_direct_when_missing(monkeypatch):
    """取不到 ⇒ 回退直连必须还在（不许因为改了顺序把回退弄丢）。"""
    _k, _ = _liver_with_node()
    _hit = {"n": 0}
    _orig = _k._m70_direct_get_node

    def _spy(_id):
        _hit["n"] += 1
        return _orig(_id)

    monkeypatch.setattr(_k, "_m70_direct_get_node", _spy, raising=True)
    assert _k._m70_kal_get_node("m443-not-exist") is None
    assert _hit["n"] == 1, "未命中时必须回退一次直连"


def test_global_kal_has_no_pool_in_process():
    """钉住前提：全局 `get_kal()` 的 node_pool 为 None（所以它不能当主通道）。"""
    assert get_kal()._node_pool is None, (
        "若哪天全局单例真被注入了 pool，本刀的『self._kal 优先』理由需要重新评估")


def test_liver_has_no_direct_pool_calls():
    """PulseLiver 里不许再出现 `node_pool.get_node/query_nodes` 直连。"""
    _p = os.path.join(_ROOT, "organs", "body", "PulseLiver.py")
    with open(_p, encoding="utf-8") as _f:
        _src = _f.read()
    assert ".node_pool.get_node(" not in _src
    assert ".node_pool.query_nodes(" not in _src
