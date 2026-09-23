# -*- coding: utf-8 -*-
"""第53批 T3：补丁自应用机制综合测试（4 场景 / ≥15 例）。

场景：
  ① 自动审批逻辑（低风险+高信任通过 / 低信任转人工 / 高风险转人工）
  ② 审批写入检查（成功 ok=True / 失败 ok=False）
  ③ 补丁应用（可应用 / 过期补丁归档 / 落地前安全门 / 防循环重启 fail-closed）
  ④ 状态流转（pending → approved → 可落地）

全部落盘指向沙箱目录，绝不触碰生产 data/。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io
import json
import shutil
import tempfile
import time
import unittest
from unittest import mock

import config
import nucleus.data.write_guard as _wg
from nucleus.evolution.PatchAutoApprover import (
    DECISION_APPROVE,
    DECISION_HUMAN,
    DECISION_LOW_TRUST,
    DECISION_OBSOLETE,
    PatchAutoApprover,
)
from nucleus.reasoning.PatchManager import PatchManager

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_RISK_MAP = {"极低": 1, "低": 2, "中等": 3, "高": 4}


def _mk_mgr(tmp):
    p = PatchManager.__new__(PatchManager)
    # ★安全：_project_root 指向沙箱 → 任何路径解析都落在临时目录，
    #   即使测试意外走到真实应用分支也不可能写生产源码。
    p._project_root = tmp
    p._patch_dir = tmp
    p._history_file = os.path.join(tmp, "patch_history.json")
    p._pending_file = os.path.join(tmp, "pending_patches.json")
    p._max_restart_count = 10
    p._restart_counter = 0
    return p


def _write_pending(tmp, items):
    with io.open(os.path.join(tmp, "pending_patches.json"), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)


def _read_pending(tmp):
    with io.open(os.path.join(tmp, "pending_patches.json"), encoding="utf-8") as f:
        return json.load(f)


def _patch(**kw):
    base = {
        "id": kw.get("id", "p1"),
        "file": kw.get("file", "config.py"),
        "method": kw.get("method", "m1"),
        "risk_level": kw.get("risk_level", "低"),
        "confidence": kw.get("confidence", "high"),
        "trust_score": kw.get("trust_score", 40),
        "status": kw.get("status", "pending"),
        "original_code": kw.get("original_code", "PATCH_AUTO_APPROVE_TRUST_THRESHOLD = 40"),
        "modified_code": kw.get("modified_code", "PATCH_AUTO_APPROVE_TRUST_THRESHOLD = 40  # x"),
    }
    base.update({k: v for k, v in kw.items() if k not in base})
    return base


# ============================ 场景① 自动审批逻辑 ============================
class TestAutoApproval(unittest.TestCase):
    """入队自动审批判据（与 PatchManager:135-166 同源）。"""

    def _would(self, risk, trust):
        evo = config.EVOLUTION_CONFIG
        _r = _RISK_MAP.get(risk, 99) if isinstance(risk, str) else risk
        return (_r <= evo.get("auto_apply_max_risk", 1)) and \
            (float(trust) >= evo.get("auto_apply_min_trust", 60))

    def test_01_low_risk_high_trust_approved(self):
        self.assertTrue(self._would("低", 40))

    def test_02_low_risk_low_trust_to_human(self):
        self.assertFalse(self._would("低", 20))

    def test_03_high_risk_to_human(self):
        self.assertFalse(self._would("高", 95))

    def test_04_medium_risk_to_human(self):
        self.assertFalse(self._would("中等", 95))

    def test_05_classify_combo_approve(self):
        """PatchAutoApprover 组合判定：verified+high+low+等待达标 → 自动放行。"""
        a = PatchAutoApprover(base_dir=tempfile.mkdtemp())
        p = _patch(trust_score=40, confidence="high", risk_level="低",
                   runtime_verified=True, status="runtime_verified",
                   generated_at=time.time() - 48 * 3600.0)
        dec, _ = a.classify(p)
        self.assertEqual(dec, DECISION_APPROVE)

    def test_06_classify_low_trust_to_human(self):
        a = PatchAutoApprover(base_dir=tempfile.mkdtemp())
        _p = _patch(trust_score=10, runtime_verified=True, status="runtime_verified",
                    generated_at=time.time() - 48 * 3600.0)
        dec, _ = a.classify(_p)
        self.assertEqual(dec, DECISION_LOW_TRUST)

    def test_07_classify_high_risk_rejected(self):
        a = PatchAutoApprover(base_dir=tempfile.mkdtemp())
        _p = _patch(risk_level="高", trust_score=95, runtime_verified=True,
                    status="runtime_verified", generated_at=time.time() - 48 * 3600.0)
        dec, _ = a.classify(_p)
        self.assertEqual(dec, DECISION_LOW_TRUST)

    def test_08_classify_waiting_to_human(self):
        a = PatchAutoApprover(base_dir=tempfile.mkdtemp())
        _p = _patch(generated_at=time.time(), runtime_verified=True,
                    status="runtime_verified", confidence="high", risk_level="低")
        dec, reason = a.classify(_p)
        self.assertEqual(dec, DECISION_HUMAN)
        self.assertIn("等待", reason)


# ============================ 场景② 审批写入检查 ============================
class TestApproveWriteCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m53_ap_")
        self.mgr = _mk_mgr(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_10_approve_all_success(self):
        _write_pending(self.tmp, [_patch(id="a"), _patch(id="b")])
        _r = self.mgr.approve_all_patches()
        self.assertIs(_r["ok"], True)
        self.assertEqual(_r["approved"], 2)

    def test_11_approve_all_write_failure(self):
        _write_pending(self.tmp, [_patch(id="a")])
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            _r = self.mgr.approve_all_patches()
        self.assertIs(_r["ok"], False)
        self.assertEqual(_r["approved"], 0)
        self.assertTrue(_r.get("error"))

    def test_12_approve_one_success(self):
        _write_pending(self.tmp, [_patch(id="a"), _patch(id="b")])
        _r = self.mgr.approve_patch(0)
        self.assertIs(_r["ok"], True)

    def test_13_approve_one_write_failure(self):
        _write_pending(self.tmp, [_patch(id="a")])
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            _r = self.mgr.approve_patch(0)
        self.assertIs(_r["ok"], False)


# ============================ 场景③ 补丁应用 ============================
class TestPatchApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m53_ap2_")
        self.mgr = _mk_mgr(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_20_no_pending_applies_zero(self):
        _write_pending(self.tmp, [])
        _r = self.mgr.apply_all_pending(only_approved=True)
        self.assertEqual(_r["applied"], 0)
        self.assertEqual(_r["failed"], 0)

    def test_21_unapproved_skipped(self):
        """未审批补丁不得被应用（审批过滤）。"""
        _write_pending(self.tmp, [_patch(id="a", status="pending")])
        _r = self.mgr.apply_all_pending(only_approved=True)
        self.assertEqual(_r["applied"], 0)
        self.assertEqual(_r.get("skipped_unapproved"), 1)

    def test_22_restart_counter_failclosed(self):
        """★防循环重启 fail-closed：计数写失败 → 拒绝应用。"""
        _write_pending(self.tmp, [_patch(id="a", status="approved")])
        with mock.patch.object(self.mgr, "_save_restart_counter", lambda _c: False):
            _r = self.mgr.apply_all_pending(only_approved=True)
        self.assertEqual(_r["applied"], 0)
        self.assertIs(_r.get("loop_protection"), True)

    def test_23_restart_limit_blocks(self):
        _write_pending(self.tmp, [_patch(id="a", status="approved")])
        self.mgr._max_restart_count = 0
        _r = self.mgr.apply_all_pending(only_approved=True)
        self.assertEqual(_r["applied"], 0)
        self.assertIs(_r.get("loop_protection"), True)

    def test_24_obsolete_patch_detected(self):
        """★过期补丁（original_code 不在目标文件中）→ 判 obsolete → 自动归档。"""
        a = PatchAutoApprover(base_dir=self.tmp)
        self.assertTrue(a.is_obsolete(_patch(file="nonexistent_x.py", original_code="zzz")))

    def test_25_obsolete_archived_by_prune(self):
        _write_pending(self.tmp, [_patch(id="o1", file="nonexistent_x.py", original_code="zzz")])
        a = PatchAutoApprover(base_dir=self.tmp)
        _res = a.prune_pending()
        self.assertGreaterEqual(_res["pruned"], 1)
        self.assertEqual(_read_pending(self.tmp), [])

    def test_26_fresh_patch_not_obsolete(self):
        a = PatchAutoApprover(base_dir=self.tmp)
        self.assertFalse(a.is_obsolete(_patch()))

    def test_27_obsolete_decision_label(self):
        a = PatchAutoApprover(base_dir=self.tmp)
        dec, _ = a.classify(_patch(file="nonexistent_x.py", original_code="zzz"))
        self.assertEqual(dec, DECISION_OBSOLETE)

    # --- 落地前安全门（_check_patch_safety）---
    def test_28_safety_approved_bypasses(self):
        _r = self.mgr._check_patch_safety(_patch(status="approved", trust_score=0))
        self.assertIs(_r["safe"], True)

    def test_29_safety_low_trust_blocks(self):
        _r = self.mgr._check_patch_safety(_patch(trust_score=10))
        self.assertIs(_r["safe"], False)
        self.assertIn("信任", _r["reason"])

    def test_30_safety_high_risk_blocks(self):
        _r = self.mgr._check_patch_safety(_patch(trust_score=95, risk_level="高"))
        self.assertIs(_r["safe"], False)

    def test_31_safety_cooldown_blocks(self):
        self.mgr._last_apply_time = time.time()
        _r = self.mgr._check_patch_safety(_patch(trust_score=95, risk_level="低"))
        self.assertIs(_r["safe"], False)
        self.assertIn("冷却", _r["reason"])


# ============================ 场景④ 状态流转 ============================
class TestStateFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m53_sf_")
        self.mgr = _mk_mgr(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_40_pending_to_approved(self):
        _write_pending(self.tmp, [_patch(id="a", status="pending")])
        self.mgr.approve_patch(0)
        self.assertEqual(_read_pending(self.tmp)[0]["status"], "approved")

    def test_41_approved_has_timestamp(self):
        _write_pending(self.tmp, [_patch(id="a")])
        self.mgr.approve_patch(0)
        self.assertGreater(_read_pending(self.tmp)[0].get("approved_at", 0), 0)

    def test_42_already_approved_rejected(self):
        _write_pending(self.tmp, [_patch(id="a", status="approved")])
        _r = self.mgr.approve_patch(0)
        self.assertIs(_r["ok"], False)
        self.assertIn("已批准", _r["reason"])

    def test_43_batch_transitions_all(self):
        _write_pending(self.tmp, [_patch(id="a"), _patch(id="b"), _patch(id="c")])
        self.mgr.approve_all_patches()
        self.assertTrue(all(x["status"] == "approved" for x in _read_pending(self.tmp)))

    def test_44_approved_is_apply_eligible(self):
        """状态流转终态：approved 补丁通过落地安全门（可被应用）。"""
        _write_pending(self.tmp, [_patch(id="a")])
        self.mgr.approve_patch(0)
        _p = _read_pending(self.tmp)[0]
        self.assertIs(self.mgr._check_patch_safety(_p)["safe"], True)

    def test_45_only_approved_selected(self):
        """审批过滤：只有 approved 的补丁进入应用候选。

        ★安全：沙箱内不存在目标文件 → 应用必然失败（applied=0），
        details 中只应出现 approved 的那一条，pending 那条不得出现。
        """
        _write_pending(self.tmp, [_patch(id="a", status="approved"),
                                  _patch(id="b", status="pending")])
        _r = self.mgr.apply_all_pending(only_approved=True)
        self.assertEqual(_r["applied"], 0)
        _ids = [d.get("id") for d in _r["details"]]
        self.assertEqual(_ids, ["a"], "未审批补丁不得进入应用候选")
        self.assertNotIn("b", _ids)


class TestConfigWiring(unittest.TestCase):
    def test_50_switch_keys_present(self):
        self.assertTrue(hasattr(config, "ENABLE_PATCH_APPROVE_WRITE_CHECK"))
        self.assertEqual(config.EVOLUTION_CONFIG.get("auto_apply_min_trust"), 40)

    def test_51_config_flag_not_in_hot_blacklist(self):
        self.assertNotIn("ENABLE_PATCH_APPROVE_WRITE_CHECK",
                         getattr(config, "_HOT_RELOAD_BLACKLIST", set()))


if __name__ == "__main__":
    unittest.main()
