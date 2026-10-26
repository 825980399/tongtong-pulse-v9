# -*- coding: utf-8 -*-
"""372 刀2 定向单测：B 低风险放行通道并入总开关 + source=llm 排除。

票 `T-371-低风险放行绕总开关-1`（登记册 427）｜裁决 **C-1 甲**。

缺口（T0 实测，HEAD `5dff8abe`）：
  · `_m101_low_risk_release_path` 改前**故意**绕开 `auto_apply_enabled`
    （旧注释原话：「比 auto_apply_enabled 更严格，故即使总开关关闭也放行」）；
  · 该通道改前**允许** source=llm，而 A 通道 `_m85` 是 llm 一律拒
    ⇒ 同一来源两条通道结论相反；账本 `patch_history.json` 12/12 全 source=llm。
  · 两调用点：`PatchManager.save_pending_patch` 与
    `SafeEvolutionExecutor.verify_submitted_patches`，都经 `_m105_try_release_low_risk`。

本刀：在 `_m101` 加两道闸（总开关 / llm），两调用点自动同受控；
不另开 B 独立开关（否 C-1 乙）；不动总开关默认值（仍 False）。
"""
from __future__ import annotations

import os
import tempfile

import pytest

from nucleus.reasoning.PatchManager import PatchManager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CFG_ON = {"auto_apply_enabled": True}
_CFG_OFF = {"auto_apply_enabled": False}


def _mk(**kw):
    """构造一条"除了被测条件外全部合格"的低风险补丁。"""
    _p = {
        "id": "p372_b2",
        "file": "nucleus/demo_target.py",
        "method": "m",
        "original_code": "def m():\n    return 0\n",
        "modified_code": "def m():\n    return 1\n",
        "risk_level": "低",
        # ★372刀1 起未登记族 fail-closed 至中风险，而 B 通道第一条判据是 risk 低
        #   ⇒ 本文件必须用**已登记的低危族**（11 族里唯一的低危族），
        #   否则用例会被刀1 的风险闸挡住，测不到刀2 的两道闸。
        "issue_type": "periodic_task_no_reentry",
        "source": "local_rule",
        "status": "pending",
        "trust_score": 90,
        "confidence": "high",
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
        "runtime_verify_result": {"verified": True, "baseline": 3},
        "baseline_errors": 3,
    }
    _p.update(kw)
    return _p


def _pm(tmp):
    _m = PatchManager(project_root=PROJECT_ROOT)
    _m._pending_file = os.path.join(tmp, "pending_patches.json")
    _m._history_file = os.path.join(tmp, "patch_history.json")
    _m._backup_manager = type("B", (), {
        "create_backup": lambda self, files=None, reason=None, source=None:
            os.path.join(tempfile.gettempdir(), "b372_bak")})()
    _m._save_restart_counter = lambda c: True
    _m._load_restart_counter = lambda: 0
    _m._max_restart_count = 10 ** 9
    return _m


# ============================================================ ① 总开关关闭即拒
def test_01_master_switch_off_rejects_b_channel():
    """总开关关闭 ⇒ B 通道同样拒（改前是"即使关闭也放行"）。"""
    _ok_patch = _mk()
    assert PatchManager._m101_low_risk_release_path(_ok_patch, _CFG_ON) is True, (
        "★判别力前提：同一份补丁在总开关开启时必须能放行，否则下面 OFF 返回 False "
        "可能只是因为别的原因被拦")
    assert PatchManager._m101_low_risk_release_path(_ok_patch, _CFG_OFF) is False

    # 默认 config（EVOLUTION_CONFIG.auto_apply_enabled = False）⇒ B 通道关闭
    import config
    assert config.EVOLUTION_CONFIG.get("auto_apply_enabled") is False, (
        "★本刀不改总开关默认值（仍 False）；若这里变了，说明有人翻了总开关")
    assert PatchManager._m101_low_risk_release_path(_mk()) is False

    # `_m105` 同样不放行（不会出现"判据 False 但状态被改"）
    _p = _mk()
    _pm_inst = _pm(tempfile.mkdtemp(prefix="m372b2_off_"))
    assert _pm_inst._m105_try_release_low_risk(_p, _CFG_OFF) is False
    assert _p.get("status") != "approved"
    assert not _p.get("auto_released")


# ============================================================ ② source=llm 排除
def test_02_llm_source_excluded_from_b_channel():
    """source=llm 一律不进 B 通道（与 A 通道 `_m85` 口径对齐）。"""
    for _src in ("llm", "LLM", " Llm ", "llm "):
        assert PatchManager._m101_low_risk_release_path(
            _mk(source=_src), _CFG_ON) is False, _src

    # 反向判别：非 llm 来源在同样条件下必须放行（证明拦的是来源、不是别的）
    for _src in ("local_rule", "human:小林", "llm_upgrade", ""):
        assert PatchManager._m101_low_risk_release_path(
            _mk(source=_src), _CFG_ON) is True, _src

    # A 通道同口径对照：llm 在 `_m85` 也拿不到放行（改前只有 B 破例）
    assert PatchManager._m85_local_low_risk_auto_apply(
        _mk(source="llm", confidence="high")) is False


# ============================================================ ③ 既有低风险路径不回归
def test_03_existing_low_risk_path_not_regressed():
    """总开关开启时，B 通道原有判据（风险/嵌套验证/baseline/核心文件/护栏）一字未松。"""
    assert PatchManager._m101_low_risk_release_path(_mk(), _CFG_ON) is True
    # 既有否决项逐条复核（这些是 101/105 批的既有不变量，本刀不得动）
    assert PatchManager._m101_low_risk_release_path(
        _mk(risk_level="高"), _CFG_ON) is False
    assert PatchManager._m101_low_risk_release_path(
        _mk(risk_level="中"), _CFG_ON) is False
    assert PatchManager._m101_low_risk_release_path(
        _mk(runtime_verify_result={"verified": False}), _CFG_ON) is False
    assert PatchManager._m101_low_risk_release_path(
        _mk(runtime_verify_result=None, baseline_errors=None), _CFG_ON) is False
    assert PatchManager._m101_low_risk_release_path(
        _mk(baseline_errors=0, runtime_verify_result={"verified": True, "baseline": 0}),
        _CFG_ON) is False
    assert PatchManager._m101_low_risk_release_path(
        _mk(file=""), _CFG_ON) is False
    assert PatchManager._m101_low_risk_release_path(
        _mk(file="main.py"), _CFG_ON) is False        # 核心文件
    assert PatchManager._m101_low_risk_release_path(
        _mk(method="_safe_eval_arithmetic"), _CFG_ON) is False   # 护栏禁改清单


# ============================================================ ④ 两调用点同受控
def test_04_two_call_sites_both_controlled(monkeypatch):
    """入队口与验证后复查口都受同一道闸约束（不靠"只改一处"）。"""
    import config
    monkeypatch.setitem(config.EVOLUTION_CONFIG, "auto_apply_enabled", True)

    # 调用点一：save_pending_patch（入队）—— llm 源即使总开关开、验证齐备也不放行
    _tmp = tempfile.mkdtemp(prefix="m372b2_enq_")
    _m = _pm(_tmp)
    _p = _mk(id="p372_b2_enq", source="llm")
    _m.save_pending_patch(_p)
    assert _p.get("status") != "approved", _p.get("status")
    assert not _p.get("auto_released")

    # 同一入队口，非 llm 源 + 总开关开 ⇒ 仍然放行（证明不是把口整体关死）。
    # ★trust 压到门槛（40）以下：否则「A2 低风险自动审批」会先一步置 approved，
    #   B 通道的 auto_released 就被掩盖，测不到本刀收口的那条路径。
    _p2 = _mk(id="p372_b2_enq2", source="local_rule", trust_score=30)
    _m.save_pending_patch(_p2)
    assert _p2.get("status") == "approved", _p2.get("status")
    assert _p2.get("auto_released") is True

    # ★跨刀交互（刀1 × 刀2）：族风险只在**入队口**重推，故这条必须在入队口验。
    #   未登记族（含 issue_type 缺失）⇒ 刀1 升为中 ⇒ 即便 source 非 llm、总开关开，
    #   也进不了 B 通道（纵深防御：两道任一即可挡）。
    for _fam in ("", "silent_exception"):
        _p3 = _mk(id="p372_b2_enq_%s" % (_fam or "empty"), source="local_rule",
                  issue_type=_fam, trust_score=30)
        _m.save_pending_patch(_p3)
        assert _p3.get("risk_level") == "中", (_fam, _p3.get("risk_level"))
        assert _p3.get("status") != "approved", (_fam, _p3.get("status"))
        assert not _p3.get("auto_released"), _fam

    # 调用点二：SafeEvolutionExecutor.verify_submitted_patches —— 连线断言
    _see = open("nucleus/reasoning/SafeEvolutionExecutor.py", "rb").read().decode(
        "utf-8", "replace")
    assert "_m105_try_release_low_risk(" in _see, (
        "★第二调用点必须仍走同一助手；若改成自己判，本刀的收口就只覆盖了一半")
    # 助手把 evo_cfg 透传给判据（否则两处可能读到不同配置口径）
    _pm_src = open("nucleus/reasoning/PatchManager.py", "rb").read().decode(
        "utf-8", "replace")
    assert "_m101_low_risk_release_path(patch, evo_cfg)" in _pm_src
    assert "self._m105_try_release_low_risk(patch, _evo_cfg_a1)" in _pm_src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
