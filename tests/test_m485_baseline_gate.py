# -*- coding: utf-8 -*-
"""485刀7（P1）：baseline=0 的补丁禁入自动审批 —— A1 内联档与 _m85 放行档补上判据。

★T0 现状（烛微 §3.5 坑三的怀疑，施工时实测钉死）：
  * B 通道 `_m101_low_risk_release_path`：**早已查** baseline>0（第105批 T-105a）；
  * 老化 `_m94_aging_eligible`：**早已查**（第105批）；
  * approval_bridge 八闸：**早已含** baseline_positive（447刀1）；
  * **缺口 = A1 内联自动审批**（save_pending_patch 内 risk+trust+whitelist 即升
    approved）与 **_m85 本地低风险放行档** —— 两条都不看 baseline。
★口径：baseline **显式为 0** ⇒ 拒；**缺失/无法判定 ⇒ 不拦**（铁律 102：
  没测到 ≠ 真 0，不拿缺失当 0 去冤枉补丁）。开关关 ⇒ 逐字回改前。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.reasoning.PatchManager import PatchManager

_CFG = {"auto_apply_enabled": True, "local_auto_apply_enabled": True,
        "allow_core_auto_apply": False, "auto_apply_min_trust": 40,
        "auto_apply_max_risk": 2}

_ORIG_C = "def f(self):\n    # 旧注释\n    return 1\n"
_NEW_C = "def f(self):\n    # 新注释\n    return 1\n"


def _rec(**kw):
    p = {
        "id": "m485b", "file": "organs/senses/PulseTouch.py", "method": "_on_touch",
        "original_code": _ORIG_C, "modified_code": _NEW_C,
        "verification": {"passed": True}, "no_regression": True,
        "post_apply_errors": None, "verified": True,
        "status": "runtime_verified", "risk_level": "低",
        "source": "local_rule", "confidence": "high",
        "runtime_verify_result": {"verified": True}, "baseline_errors": 2,
    }
    p.update(kw)
    return p


# ---------------------------------------------- 1) _m85：baseline=0 ⇒ 拦
def test_m85_blocks_baseline_zero(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_BASELINE_GATE", True, raising=False)
    ok = PatchManager._m85_local_low_risk_auto_apply(
        _rec(baseline_errors=0), _CFG)
    assert ok is False, "baseline=0 ⇒ 禁入本地低风险放行（任务书验收）"


# ---------------------------------------------- 2) _m85：baseline>0 ⇒ 照旧放行
def test_m85_allows_baseline_positive(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_BASELINE_GATE", True, raising=False)
    ok = PatchManager._m85_local_low_risk_auto_apply(_rec(), _CFG)
    assert ok is True, "baseline>0 且其余达标 ⇒ 与改前一致放行"


# ---------------------------------------------- 3) _m85：baseline 缺失 ⇒ 不拦（不是 0）
def test_m85_missing_baseline_not_treated_as_zero(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_BASELINE_GATE", True, raising=False)
    ok = PatchManager._m85_local_low_risk_auto_apply(
        _rec(baseline_errors=None, runtime_verify_result={"verified": True}), _CFG)
    assert ok is True, "缺失 ≠ 0：铁律 102，不拿缺失冤枉补丁"


# ---------------------------------------------- 4) 开关关 ⇒ 逐字回改前
def test_m85_switch_off_old_behavior(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_BASELINE_GATE", False, raising=False)
    ok = PatchManager._m85_local_low_risk_auto_apply(
        _rec(baseline_errors=0), _CFG)
    assert ok is True, "开关关 ⇒ baseline=0 也放行（改前行为）"


# ---------------------------------------------- 5) B 通道不受影响（早已查）
def test_m101_baseline_zero_still_blocked():
    ok = PatchManager._m101_low_risk_release_path(
        _rec(baseline_errors=0), _CFG)
    assert ok is False, "B 通道既有判据不变（第105批 T-105a）"


# ---------------------------------------------- 6) A1 判据函数：口径单元
def test_baseline_positive_helper_semantics():
    f = PatchManager._m485_baseline_positive
    assert f({"baseline_errors": 0}) is False
    assert f({"baseline_errors": 2}) is True
    assert f({"runtime_verify_result": {"baseline_errors": 0}}) is False
    assert f({"runtime_verify_result": {"baseline": 3}}) is True
    assert f({}) is True, "缺失 ⇒ 不拦"
    assert f({"baseline_errors": "abc"}) is True, "非法值 ⇒ 不拦（无法判定 ≠ 0）"
