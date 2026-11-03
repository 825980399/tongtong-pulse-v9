# -*- coding: utf-8 -*-
"""441 刀1（补 440 欠账）：`patch_chain.state` 的 degraded 判据。

440 之前：state 只看「字段读没读到」，于是「一切正常但一条都没应用」被标 ok。
440 之后：`apply==0 且 queue>0` ⇒ degraded（并写 `state_reason`）。
本文件守的就是这一条**降级判据**与它的边界。
"""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


import organs.core.PulseMetricsCollector as PMC


def _read_with(monkeypatch, rows, pending_rows=None):
    """把 history / pending 换成受控数据，跑真实 `_m421_patch_chain_read`。

    路径口径：函数内部按 `<root>/data/patches/*.json` 拼（不读模块常量），
    所以只能把**假 root 的目录结构**搭出来，不能 monkeypatch 常量。
    """
    _td = tempfile.mkdtemp()
    _dir = os.path.join(_td, "data", "patches")
    os.makedirs(_dir, exist_ok=True)
    _write(os.path.join(_dir, "patch_history.json"), rows)
    _write(os.path.join(_dir, "pending_patches.json"),
           pending_rows if pending_rows is not None else [])
    return PMC._m421_patch_chain_read(_td)


def _write(path, doc):
    import json
    with open(path, "w", encoding="utf-8") as _f:
        json.dump(doc, _f, ensure_ascii=False)


def _hist_row(applied: bool = True):
    """一条今天的 history 记录（applied_at 决定 apply 计数）。"""
    import time
    _now = time.time()
    return {"id": "h1", "file": "a.py", "method": "m",
            "generated_at": _now, "applied_at": _now if applied else None,
            "landed": applied}


# ---------------------------------------------------------------- ① 降级
def test_apply_zero_and_pending_positive_is_degraded(monkeypatch):
    """★核心：apply=0 且 queue>0 ⇒ degraded（旧口径会判 ok）。"""
    _d = _read_with(monkeypatch, rows=[_hist_row(applied=False)],
                    pending_rows=[{"id": "p1", "status": "pending"}])
    assert _d.get("apply") == 0, _d
    assert _d.get("queue") == 1, _d
    assert _d.get("state") == "degraded", f"state={_d.get('state')}（应 degraded）"
    assert "apply=0" in str(_d.get("state_reason", ""))


# ---------------------------------------------------------------- ② 不该降级的边界
def test_apply_positive_stays_ok(monkeypatch):
    """有应用 ⇒ 不降级。"""
    _d = _read_with(monkeypatch, rows=[_hist_row(applied=True)],
                    pending_rows=[{"id": "p1"}])
    assert _d.get("apply", 0) >= 1
    # 合成根里没有 ledger 源 ⇒ state 可能是 partial；判据是**不降级**，不是必须 ok
    assert _d.get("state") != "degraded", _d


def test_empty_queue_stays_ok(monkeypatch):
    """队列本来就空（没活可干）⇒ ok，不是 degraded。"""
    _d = _read_with(monkeypatch, rows=[_hist_row(applied=False)], pending_rows=[])
    assert _d.get("queue") == 0
    assert _d.get("state") != "degraded", _d


def test_unavailable_not_downgraded_to_degraded(monkeypatch):
    """读不到数据时是 unavailable，不许被误标成 degraded（两者含义不同）。"""
    _d = PMC._m421_patch_chain_read(tempfile.mkdtemp() + os.sep + "nope")
    assert _d.get("state") in ("unavailable", "partial"), _d


# ---------------------------------------------------------------- ③ 源码防回潮
def test_degraded_rule_still_wired():
    """防有人把降级判据删掉（回退到"apply=0 也报 ok"的老口径）。"""
    import inspect
    _src = inspect.getsource(PMC._m421_patch_chain_read)
    assert '"degraded"' in _src
    assert 'state_reason' in _src
