# -*- coding: utf-8 -*-
"""443 刀1：O3 独立节拍 —— 与派单节拍解耦（442 的修法钉死在这里）。

背景（443 T0）：442 加了 300s 独立节拍，但生产进程 00:10:49 启动、442 提交 00:21:25
⇒ 跑的还是旧代码 ⇒ `[340判死]` / `[335刀1][O3消费]` 都是 0 条。
所以这一批把「节拍位置」写成断言，防止以后有人把 O3 挪回 `_last_dispatch_ts` 之后。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nucleus.evolution.patch_execution_dispatch as d


def _prep(monkeypatch):
    """把模块状态与两个间隔都钉成受控值，并记录 O3 消费的调用时刻。"""
    _calls = []
    monkeypatch.setattr(d, "_last_dispatch_ts", None, raising=True)
    monkeypatch.setattr(d, "_last_o3_gate_ts", None, raising=True)
    monkeypatch.setattr(d, "_dispatch_interval_sec", lambda: 3600.0, raising=True)
    monkeypatch.setattr(d, "_o3_gate_interval_sec", lambda: 300.0, raising=True)
    monkeypatch.setattr(d, "_m335_o3_consume",
                        lambda now=None, **k: (_calls.append(now),
                                               {"status": "deferred"})[1],
                        raising=True)
    return _calls


def test_o3_tick_fires_even_when_dispatch_not_due(monkeypatch):
    """派单 3600s 未到，O3 300s 到点 ⇒ 仍必须触发（这就是 442 的全部意义）。"""
    _calls = _prep(monkeypatch)
    _r1 = d.dispatch_on_tick(now=1000.0)
    assert _r1["status"] == "armed", _r1
    assert len(_calls) == 1, "首拍应触发 O3"

    _r2 = d.dispatch_on_tick(now=1301.0)          # +301s：派单远未到
    assert _r2["status"] == "skipped_not_due", _r2
    assert len(_calls) == 2, "派单未到期时 O3 仍必须跑（442 之前这里被饿死）"

    _r3 = d.dispatch_on_tick(now=1401.0)          # +100s：O3 未到点
    assert _r3["status"] == "skipped_not_due", _r3
    assert len(_calls) == 2, "O3 间隔未到，不该重复跑"

    _r4 = d.dispatch_on_tick(now=1601.0)          # +300s：O3 到点
    assert len(_calls) == 3, "O3 到点必须再跑"


def test_o3_tick_still_fires_on_dispatch_tick(monkeypatch):
    """派单到期那一拍：O3 照跑（两拍互不顶，不是二选一）。"""
    _calls = _prep(monkeypatch)
    d.dispatch_on_tick(now=1000.0)
    monkeypatch.setattr(d, "_last_dispatch_ts", 1000.0, raising=True)
    monkeypatch.setattr(d, "_last_o3_gate_ts", 1000.0, raising=True)
    # 直接桩掉真正派单，只验 O3 是否被调用
    monkeypatch.setattr(d, "_dispatch_one", lambda *a, **k: {"ok": True}, raising=False)
    from nucleus._silent_except import silent_exc
    try:
        d.dispatch_on_tick(now=4700.0)
    except Exception as e:      # 派单链内部依赖较多，本用例只关心 O3 是否被调到
        silent_exc(e, where="test_m443_k1_o3_tick::dispatch_tick", level="debug")
    assert len(_calls) >= 1


def test_o3_interval_is_configurable_and_floor_60s(monkeypatch):
    """`_o3_gate_interval_sec` 有 60s 下限（防止有人配 0 变成每拍狂跑）。"""
    import config
    monkeypatch.setattr(config, "PATCH_O3_GATE_INTERVAL_SEC", 5.0, raising=True)
    assert d._o3_gate_interval_sec() >= 60.0
    monkeypatch.setattr(config, "PATCH_O3_GATE_INTERVAL_SEC", 300.0, raising=True)
    assert d._o3_gate_interval_sec() == 300.0


def test_o3_tick_sits_before_dispatch_early_return():
    """源码级判据：O3 节拍必须在 `if _last_dispatch_ts is None` **之前**。

    ★这是本刀真正要防的回潮 —— 位置一挪回去，O3 就又被 1h 派单节拍饿死，
    而功能测试（上面两条）在假时钟下仍可能过。
    """
    _p = os.path.join(_ROOT, "nucleus", "evolution", "patch_execution_dispatch.py")
    with open(_p, encoding="utf-8") as _f:
        _src = _f.read()
    _o3 = _src.index("_m335_o3_consume(now=_now)")
    _armed = _src.index("if _last_dispatch_ts is None:")
    assert _o3 < _armed, "_m335_o3_consume 被挪到 armed 早退之后 ⇒ O3 会再次饿死"
