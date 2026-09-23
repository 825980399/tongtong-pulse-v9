# -*- coding: utf-8 -*-
"""
主线第80批 T7 (P0-3) · 核心文件免签改写收口 —— 真实端到端回归（禁止 mock）。

设计原则（对齐任务书 T7-5 / T8 门禁）：
- 用**真实 PatchManager** + 临时目录假根，禁止 MagicMock 掉被测函数本身；
- 复现第三方 E 代理端到端脚本思路：verified 核心补丁 → 入队 → 断言 status 不被升
  approved、文件不被自动改写；
- 三条旁路（入队覆写 / 反思直批 / 落地 apply_now）任一都无法绕过「核心性 + 总开关」闸门；
- 关键用例先红后绿：本文件在修复（PatchManager._m80_* 闸门）合入后通过；修复前
  save_pending_patch 会无条件把核心补丁升 approved，本文件即失败。

覆盖：
- P0-3 主场景：verified 核心补丁，auto_apply 关闭 → 保持 verified、落盘 verified；
- 反思直批旁路：核心补丁被直接置 approved → 入队兜底回退 verified；
- 核心文件即便总开关打开也绝不自动 approved；
- 非核心低风险 + 总开关开 → 正常自动 approved；
- 落地双判定：legacy 已 approved 核心补丁在 apply_all_pending 被拒、文件不被改写；
- T7-3：_is_core_file 含 nucleus/security/、nucleus/evolution/、安全/宪法器官；
- T7-4：回滚目标路径越界 → 拒绝回滚。
"""

import os
import sys
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as _cfg  # noqa: E402  # 真实 config 模块（仅切 EVOLUTION_CONFIG 字典，不 mock 逻辑）

from nucleus.reasoning.PatchManager import PatchManager  # noqa: E402
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402


class TestCoreFileNoAutoApproveM80:
    def setup_method(self):
        self._tmp = os.path.join(ROOT, "tmp", "m80_core_" + os.urandom(6).hex())
        os.makedirs(self._tmp, exist_ok=True)
        self._pm = PatchManager(self._tmp)
        # 用副本替换 EVOLUTION_CONFIG，避免污染全局；teardown 还原
        self._orig_evo = _cfg.EVOLUTION_CONFIG
        self._evo = dict(self._orig_evo)
        _cfg.EVOLUTION_CONFIG = self._evo
        # 默认关闭总开关（安全基线），核心红线默认关
        self._evo["auto_apply_enabled"] = False
        self._evo["allow_core_auto_apply"] = False

    def teardown_method(self):
        _cfg.EVOLUTION_CONFIG = self._orig_evo
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---------- 辅助 ----------
    def _mk_patch(self, pid, file, status="pending", risk="极低", trust=95,
                  method="some_method"):
        return {
            "id": pid,
            "file": file,
            "method": method,
            "original_code": "OLD_CODE_X",
            "modified_code": "NEW_CODE_X",
            "aesthetic_score": {"total": 90.0, "grade": "A", "syntax_valid": True},
            "risk_level": risk,
            "trust_score": trust,
            "status": status,
            "description": "m80 测试补丁",
        }

    def _pending_status(self, pid):
        _saved = self._pm._load_patch_list(self._pm._pending_file)
        for _p in _saved:
            if _p.get("id") == pid:
                return _p.get("status"), _p.get("auto_approved")
        return None, None

    # ---------- P0-3 主场景 ----------
    def test_verified_core_not_upgraded_when_auto_apply_off(self):
        p = self._mk_patch("p_core_1", "config.py", status="verified")
        self._pm.save_pending_patch(p)
        assert p.get("status") == "verified"
        assert p.get("auto_approved") is not True
        _st, _aa = self._pending_status("p_core_1")
        assert _st == "verified"
        assert _aa is not True

    def test_pending_core_not_auto_approved_when_auto_apply_off(self):
        p = self._mk_patch("p_core_2", "main.py", status="pending")
        self._pm.save_pending_patch(p)
        # 关闭总开关 → 核心文件保持待人工（verified）
        assert p.get("status") == "verified"
        assert p.get("auto_approved") is not True

    # ---------- 反思直批旁路 ----------
    def test_reflection_bypass_core_downgraded(self):
        # 模拟 main.py:3480 反思改进版直接置 approved 的核心补丁
        p = self._mk_patch("p_core_3",
                           "nucleus/reasoning/SafeEvolutionExecutor.py",
                           status="approved")
        self._pm.save_pending_patch(p)
        # 顶部闸门兜底：核心文件即便被置 approved 也回退 verified
        assert p.get("status") == "verified"
        assert p.get("auto_approved") is not True

    # ---------- 即便总开关打开，核心文件也禁止 ----------
    def test_core_never_auto_approved_even_auto_apply_on(self):
        self._evo["auto_apply_enabled"] = True
        p = self._mk_patch("p_core_4", "config.py", status="pending",
                           risk="极低", trust=95)
        self._pm.save_pending_patch(p)
        assert p.get("status") == "verified"
        assert p.get("auto_approved") is not True

    # ---------- 非核心低风险 + 总开关开 → 正常 approved ----------
    def test_non_core_low_risk_auto_approved_when_on(self):
        self._evo["auto_apply_enabled"] = True
        p = self._mk_patch("p_nc_1", "organs/body/PulseLiver.py", status="pending",
                           risk="极低", trust=95)
        self._pm.save_pending_patch(p)
        assert p.get("status") == "approved"
        assert p.get("auto_approved") is True

    # ---------- 落地双判定（apply_now 旁路收敛点）----------
    def test_apply_now_landing_core_rejected(self):
        _target = os.path.join(self._tmp, "config.py")
        with open(_target, "w", encoding="utf-8") as _f:
            _f.write("# original\nX = 1\n")
        _pending = [{
            "id": "p_land_1",
            "file": _target,
            "method": "m",
            "original_code": "X = 1",
            "modified_code": "X = 2",
            "aesthetic_score": {"total": 90.0, "grade": "A", "syntax_valid": True},
            "risk_level": "极低",
            "trust_score": 95,
            "status": "approved",
            "applied": False,
        }]
        self._pm._save_patch_list(self._pm._pending_file, _pending)
        # 核心红线默认关 → 落地闸门拦截
        _res = self._pm.apply_all_pending(only_approved=True)
        assert _res["applied"] == 0
        _detail = _res["details"][0]
        assert _detail["status"] == "rejected_core_guard"
        # 文件未被改写
        with open(_target, encoding="utf-8") as _f:
            assert "X = 1" in _f.read()

    # ---------- T7-3 核心文件清单补全 ----------
    def test_core_markers_supplemented(self):
        _core_cases = [
            "nucleus/security/SandboxCore.py",
            "nucleus/evolution/EvolutionDriver.py",
            "organs/identity/PulseSpiritConstitution.py",
            "organs/identity/PulsePersonalityKernel.py",
            "organs/identity/PulseEthics.py",
            "config.py",
            "main.py",
            "nucleus/reasoning/PatchManager.py",
        ]
        for _c in _core_cases:
            assert PatchManager._m80_is_core_file(_c) is True, f"应判为核心: {_c}"
            assert SafeEvolutionExecutor._is_core_file(_c) is True, f"SafeEvolution 应判为核心: {_c}"
        # 非核心不应误判
        assert PatchManager._m80_is_core_file("organs/body/PulseLiver.py") is False
        assert PatchManager._m80_is_core_file("nucleus/evolution_stat_helper.py") is False

    # ---------- T7-4 回滚路径越界拒绝 ----------
    def test_rollback_path_escaping_refused(self):
        _history = [{
            "id": "p_esc_1",
            "file": "../escape.py",  # 越出项目根
            "applied": True,
            "rollback_available": True,
            "backup_path": os.path.join(self._tmp, "bk"),
        }]
        self._pm._save_patch_list(self._pm._history_file, _history)
        _ok = self._pm.rollback_last()
        assert _ok is False
