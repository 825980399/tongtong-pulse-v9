# -*- coding: utf-8 -*-
"""438 刀3 单测：延迟复验「baseline>0 才可判 verified」（真跑 verify_submitted_patches）。

判据来自**行为**：构造 needs_reverify 补丁 → 跑真实方法 → 看落回的 status。
不复制判据公式（铁律 30）。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor


class _FakePM:
    """只实现被测方法用到的那两个口（load_json / get_pending_file）。"""

    def __init__(self, patches):
        self._patches = patches
        self.rollback_calls = []

    def load_json(self, path, default=None):
        return list(self._patches)

    def get_pending_file(self):
        return "pending_patches.json"

    def rollback_patch(self, pid):
        self.rollback_calls.append(pid)
        return {"ok": True}

    # 方法尾部会回写队列 / 尝试释放低风险件；测试里都当 no-op
    def _save_json(self, path, data):
        return True

    def _m105_try_release_low_risk(self, *a, **k):
        return None


def _run(patch_builder, monkeypatch, strict=True, after=0, rollback=None):
    monkeypatch.setattr(config, "ENABLE_M438_DELAYED_VERIFY_STRICT", strict, raising=True)
    _p = patch_builder()
    _pm = _FakePM([_p])
    _ex = object.__new__(SafeEvolutionExecutor)
    _ex._patch_manager = _pm
    _ex._count_errors_for_location = lambda *a, **k: after
    _ex._m84_recompute_split = lambda p: None
    _ex._learn_from_verification = lambda p, v: None
    _rep = _ex.verify_submitted_patches()
    return _p, _rep, _pm


def _mk_patch(**kw):
    _b = {
        "id": "p438",
        "file": "organs/brain/X.py",
        "method": "m",
        "fixed_at": time.time() - 7200,        # 2h 前修好（genuine）
        "status": "needs_reverify",
        "reverify_after": 0,                   # 已到点
        "needs_runtime_verify": True,
        # ★468刀1 前置闸门：没落地的补丁不复验（verify_submitted_patches 先过
        #   _m468_reverify_gate，applied=False ⇒ needs_runtime_verify 被置 False
        #   ⇒ 本文件后续全部判据都到不了，补丁停在 needs_reverify 原状）。
        #   438 批写此夹具时还没有该闸门，468 批加入后未同步 ⇒ 5 条长期红。
        #   此处只让夹具通过前置闸门，断言与判据一字未改。
        "applied": True,
        "baseline_errors": 3,
    }
    _b.update(kw)
    return _b


# ---------------------------------------------------------------- ① 不可判定
def test_baseline_zero_becomes_undecidable(monkeypatch):
    """★核心：baseline=0 + after=0 必须是 runtime_undecidable，不是 verified。"""
    _p, _rep, _ = _run(lambda: _mk_patch(baseline_errors=0), monkeypatch)
    assert _p["status"] == "runtime_undecidable", _p["status"]
    assert _p["runtime_verified"] is False
    assert _p["runtime_verify_result"]["undecidable"] is True
    assert _p["runtime_verify_result"]["verified"] is False
    assert _rep["reverified"] == 0, "不可判定却被计进 reverified"


def test_missing_baseline_becomes_undecidable(monkeypatch):
    """baseline 字段缺失（老件）同样不可判定 —— `or 0` 不得再把它折成 0 后判通过。"""
    _p, _, _ = _run(lambda: _mk_patch(baseline_errors=None), monkeypatch)
    assert _p["status"] == "runtime_undecidable"


def test_undecidable_does_not_rollback(monkeypatch):
    """不可判定不许触发回滚（回滚可能真修好了的补丁是更贵的错）。"""
    _p, _, _pm = _run(lambda: _mk_patch(baseline_errors=0, applied=True), monkeypatch)
    assert _p["status"] == "runtime_undecidable"
    assert _pm.rollback_calls == [], f"不可判定却回滚了: {_pm.rollback_calls}"
    assert not _p.get("needs_repair"), "不可判定被标成需返修"


# ---------------------------------------------------------------- ② 正常判定不受影响
def test_positive_baseline_and_zero_after_is_verified(monkeypatch):
    """baseline=3 + after=0 ⇒ 仍是 runtime_verified（真修复不能被误伤）。"""
    _p, _rep, _ = _run(lambda: _mk_patch(baseline_errors=3), monkeypatch)
    assert _p["status"] == "runtime_verified", _p["status"]
    assert _p["runtime_verified"] is True
    assert _rep["reverified"] == 1


def test_positive_baseline_and_residual_after_is_failed(monkeypatch):
    """baseline=3 + after=1 ⇒ runtime_failed + needs_repair。"""
    _p, _rep, _ = _run(lambda: _mk_patch(baseline_errors=3), monkeypatch, after=1)
    assert _p["status"] == "runtime_failed", _p["status"]
    assert _p.get("needs_repair") is True
    assert _rep["reverify_failed"] == 1


# ---------------------------------------------------------------- ③ 灰度回退
def test_switch_off_restores_legacy_verified(monkeypatch):
    """关闸 ⇒ 逐字回到 `verified = (after == 0)`（baseline=0 也判通过）。"""
    _p, _, _ = _run(lambda: _mk_patch(baseline_errors=0), monkeypatch, strict=False)
    assert _p["status"] == "runtime_verified", "关闸后未回到旧判据"
    assert _p["runtime_verify_result"]["undecidable"] is False


# ---------------------------------------------------------------- ④ 源码位置（防回潮）
def test_strict_gate_still_wired():
    """`_rpassed` 必须同时含 after==0 与 undecidable 两个条件（防有人改回 `after == 0`）。"""
    import inspect
    _src = inspect.getsource(SafeEvolutionExecutor.verify_submitted_patches)
    assert "_rb_undecidable" in _src
    assert 'if _rpassed:' in _src
    assert 'if _rafter == 0:\n                    _reverified += 1' not in _src, \
        "旧判据 `if _rafter == 0` 又回来了"
