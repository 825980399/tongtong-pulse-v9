#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第161批下 刀6 门控单测：补丁审批真值校验（T-补丁审批真值校验-1，P1）。

根因：_approve_via（第129批治理五口收敛器的**唯一** approved 写入口）旧有
  两关 fail-closed（_govern_fields_ok 字段契约 + _boundary_check 边界），
  但**不校验「申报的成功」是否为真** ⇒ baseline_errors==0（无错可修）却申报
  verified=True 的补丁被静默批准 ⇒ t100a 数据侧带红 24 条「假成功补丁」。

修复：新增 fail-closed 第三关 _truth_ok（判据与 t100a 既有口径逐字一致，
  单一真相源），命中即不升 approved + 留痕 truth_check_failed / truth_reason。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning.PatchManager import PatchManager  # noqa: E402

PM = PatchManager


def _good_patch(**over):
    """一条**正常**补丁：有错可修（baseline_errors=1）且申报验证通过。"""
    p = {
        "id": "patch_test_normal",
        "file": "organs/brain/PulseRiskPerception.py",
        "original_code": "x = 1",
        "modified_code": "x = 2",
        "status": "runtime_verified",
        "baseline_errors": 1,
        "runtime_verify_result": {"verified": True, "effectiveness": 1.0},
    }
    p.update(over)
    return p


def _false_success_patch(**over):
    """一条**假成功**补丁：无错可修（baseline_errors=0）却申报验证通过。"""
    p = {
        "id": "patch_test_false",
        "file": "organs/brain/PulseRiskPerception.py",
        "original_code": "x = 1",
        "modified_code": "x = 2",
        "status": "runtime_verified",
        "baseline_errors": 0,
        "runtime_verify_result": {"verified": True, "effectiveness": 1.0},
    }
    p.update(over)
    return p


class TestK6Switch:
    def test_01_switch_default_on(self):
        import config
        assert bool(getattr(config, "PATCH_APPROVE_TRUTH_CHECK", True)) is True

    def test_02_enabled_reads_switch(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "PATCH_APPROVE_TRUTH_CHECK", False)
        assert PM._truth_check_enabled() is False


class TestK6Judge:
    def test_03_false_success_detected(self):
        assert PM._is_false_success(_false_success_patch()) is True

    def test_04_normal_patch_not_flagged(self):
        """有错可修且申报成功 = 正常，不得误判。"""
        assert PM._is_false_success(_good_patch()) is False

    def test_05_not_declared_success_passes(self):
        """未申报成功（verified=False / 无 runtime_verify_result）→ 不涉及真值校验。"""
        assert PM._is_false_success(_good_patch(
            baseline_errors=0, runtime_verify_result={"verified": False})) is False
        assert PM._is_false_success(_good_patch(
            baseline_errors=0, runtime_verify_result={})) is False

    def test_06_scope_limited_to_pending_chain(self):
        """已离开待审批链路的（如 applied）不在本关范围内。"""
        assert PM._is_false_success(_false_success_patch(status="applied")) is False


class TestK6ApproveGate:
    def test_07_false_success_blocked(self):
        """★核心：假成功补丁不得升 approved，且留痕真值不符。"""
        p = _false_success_patch()
        assert PM._approve_via(p, "auto", "auto:t101a") is False
        assert p.get("status") == "runtime_verified", "假成功被升 approved！"
        assert p.get("truth_check_failed") is True
        assert "truth_reason" in p and p["truth_reason"]

    def test_08_normal_patch_still_approved(self):
        """正常补丁不受影响（防过度拦截）。"""
        p = _good_patch()
        assert PM._approve_via(p, "auto", "auto:t101a") is True
        assert p.get("status") == "approved"
        assert p.get("truth_check_failed") is None

    def test_09_switch_off_full_rollback(self, monkeypatch):
        """★一键回退：开关关闭后回到施工前口径（假成功也能过）。"""
        import config
        monkeypatch.setattr(config, "PATCH_APPROVE_TRUTH_CHECK", False)
        p = _false_success_patch()
        assert PM._approve_via(p, "auto", "auto:t101a") is True
        assert p.get("status") == "approved"

    def test_10_first_two_gates_still_work(self):
        """第三关不得抢先/取代前两关（空壳补丁、非法 source 仍被拦）。"""
        # 空壳补丁（无代码段）→ 第一关拦
        assert PM._approve_via({"file": "a.py", "status": "pending"},
                               "auto", "auto:t101a") is False
        # 非法 source → 第二关拦
        assert PM._approve_via(_good_patch(), "ghost", "auto:t101a") is False
        # human 口缺人工签名 → 第二关拦
        assert PM._approve_via(_good_patch(), "human", "auto:bot") is False

    def test_11_human_gate_unaffected(self):
        """人工口在真值校验开启时仍可批准正常补丁。"""
        p = _good_patch()
        assert PM._approve_via(p, "human", "human:小林") is True
        assert p.get("status") == "approved"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
