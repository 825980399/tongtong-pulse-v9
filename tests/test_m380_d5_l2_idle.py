# -*- coding: utf-8 -*-
"""★380 刀5 · L2 空转诊断收口（P2）

票面假设：「`_l2_classify_scene` 把所有请求判成非 code 导致 L2 不启用」。
T0 结论：**该假设不成立**（378 刀5 已把闸改成"只拒 code"，且 user_query 全放）；
真问题是 `hits/misses 双 0` 这个读数**分不出**"没人调"与"调了全 miss"。
本刀加 `idle_state` 四态；并用 code 场景证明计数器本身是好的（断言 hits>0）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.llm import semantic_cache as _sc  # noqa: E402

_VEC = [1.0, 0.0, 0.0]
_CODE = "def foo():\n    import os\n    return os.path.join('a', 'b')\n"


def _cache():
    config.ENABLE_SEMANTIC_CACHE_L2 = True
    return _sc.SemanticCacheL2()


def test_m380_d5_idle_state_no_calls_vs_calls_no_hit():
    """★核心：两个不同事实必须分开标。"""
    _c = _cache()
    assert _c.stats()["idle_state"] == "no_calls"
    _c.store("请详细介绍一下量子计算的基本原理", "答案", origin="user_query", vec=_VEC)
    _c.lookup("完全不同的一段问题内容用于制造未命中", origin="user_query", vec=[0.0, 1.0, 0.0])
    assert _c.stats()["idle_state"] == "calls_no_hit"


def test_m380_d5_code_scenario_hits_counter():
    """★验收：构造成 code 场景请求，L2 命中计数器必须 > 0（证明空转不是计数器坏）。"""
    _c = _cache()
    assert _c.store(_CODE, "这是一段代码改写后的结果说明", origin="user_query", vec=_VEC) is True
    _hit = _c.lookup(_CODE, origin="user_query", vec=_VEC)
    assert _hit is not None
    _s = _c.stats()
    assert _s["hits"] > 0 and _s["idle_state"] == "ok"


def test_m380_d5_disabled_is_its_own_state(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_SEMANTIC_CACHE_L2", False, raising=False)
    _c = _sc.SemanticCacheL2()
    assert _c.stats()["idle_state"] == "disabled"
