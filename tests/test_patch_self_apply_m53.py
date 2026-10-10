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
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus.events.EventBus import get_event_bus, reset_event_bus
from nucleus.evolution import patch_lifecycle as _pl
from nucleus.evolution.PatchAutoApprover import (
    DECISION_APPROVE,
    DECISION_HUMAN,
    DECISION_LOW_TRUST,
    DECISION_OBSOLETE,
    PatchAutoApprover,
)
from nucleus.reasoning.PatchManager import PatchManager
from nucleus.reasoning.SelfVerifier import SelfVerifier

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


# ============================ 刀1：D-1 棘轮冷却续期 / D-3 回滚额度 ============================
class TestD1RatchetCooldown(unittest.TestCase):
    """★第159批 刀1：D-1 棘轮冷却续期（T-棘轮冷却续期-1）。

    验收(a) 锁死后冷却期满必须一次期满即放行；(c) 重启后 blocked_at 不再被拨回。
    日志守卫：patch 模块全局 _module_logger，杜绝测试串台污染生产日志口径
    （与既有 test_23 同源的「停止自动应用」WARNING 在沙箱内被静默，不落生产日志）。
    """
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m53_d1_")
        self.mgr = _mk_mgr(self.tmp)
        self.mgr._max_restart_count = 0  # 强制首次即锁死
        self.mgr._backup_manager = mock.MagicMock()  # 隔离真实备份管理器

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _blocked_at(self):
        _p = os.path.join(self.tmp, "restart_blocked_at.txt")
        if not os.path.exists(_p):
            return 0.0
        with io.open(_p, encoding="utf-8") as f:
            return float(f.read().strip() or 0)

    def test_d1_cooldown_not_refreshed(self):
        """①冷却不续期：多次拒绝后 blocked_at 保持首锁时刻，倒计时正常推进。"""
        _write_pending(self.tmp, [_patch(id="a", status="approved")])
        with mock.patch("nucleus.reasoning.PatchManager._module_logger"):
            _r1 = self.mgr.apply_all_pending(only_approved=True)
        self.assertIs(_r1.get("loop_protection"), True)
        _t1 = self._blocked_at()
        self.assertGreater(_t1, 0, "首次锁死应写入 blocked_at")

        # 第二次拒绝：blocked_at 不得被刷新（修复前会每次写 now 导致永不满期）
        _write_pending(self.tmp, [_patch(id="b", status="approved")])
        with mock.patch("nucleus.reasoning.PatchManager._module_logger"):
            _r2 = self.mgr.apply_all_pending(only_approved=True)
        self.assertIs(_r2.get("loop_protection"), True)
        _t2 = self._blocked_at()
        self.assertAlmostEqual(_t2, _t1, delta=1.0,
                               msg="blocked_at 不应随拒绝刷新（否则冷却永不满期）")

    def test_d1_cooldown_expiry_releases(self):
        """验收(a)：预置 blocked_at 早于冷却窗口 → 一次期满即放行（不计 loop_protection）。"""
        _old = time.time() - 100 * 3600.0  # 100h 前，远超默认 24h 冷却
        with io.open(os.path.join(self.tmp, "restart_blocked_at.txt"), "w", encoding="utf-8") as f:
            f.write(str(_old))
        _write_pending(self.tmp, [_patch(id="a", status="approved")])
        with mock.patch("nucleus.reasoning.PatchManager._module_logger"):
            _r = self.mgr.apply_all_pending(only_approved=True)
        self.assertIsNot(_r.get("loop_protection"), True,
                         "冷却期满应放行重试一次，不得继续锁死")
        # 期满重置：持久化计数文件应为 0（reset_restart_counter 只落盘不回写内存属性，
        # 故查磁盘而非属性——这正是期满放行的实证）。
        _rc_path = os.path.join(self.tmp, "restart_count.txt")
        self.assertTrue(os.path.exists(_rc_path), "期满应持久化重置计数")
        with io.open(_rc_path, encoding="utf-8") as f:
            self.assertEqual(int(f.read().strip()), 0, "期满应重置棘轮计数（磁盘）")

    def test_d1_blocked_at_not_reset_on_restart(self):
        """验收(c)：现网重启后 blocked_at.txt 不再被拨回（保持首锁时刻）。"""
        _write_pending(self.tmp, [_patch(id="a", status="approved")])
        with mock.patch("nucleus.reasoning.PatchManager._module_logger"):
            self.mgr.apply_all_pending(only_approved=True)
        _t_first = self._blocked_at()
        self.assertGreater(_t_first, 0)
        # 模拟"重启"：重新构造 mgr（_restart_counter 归零，blocked_at 在磁盘保留）
        _m2 = _mk_mgr(self.tmp)
        _m2._max_restart_count = 0
        _m2._backup_manager = mock.MagicMock()
        _write_pending(self.tmp, [_patch(id="b", status="approved")])
        with mock.patch("nucleus.reasoning.PatchManager._module_logger"):
            _m2.apply_all_pending(only_approved=True)
        _t_after = self._blocked_at()
        self.assertAlmostEqual(_t_after, _t_first, delta=1.0,
                               msg="重启后 blocked_at 不应被拨回（冷却正常推进）")

    def test_d1_count_and_rollback_independent(self):
        """③计数与回滚互不遮蔽：锁死计数路径与回滚额度路径独立、互不覆盖。"""
        _write_pending(self.tmp, [_patch(id="a", status="approved")])
        with mock.patch("nucleus.reasoning.PatchManager._module_logger"):
            self.mgr.apply_all_pending(only_approved=True)
        _t = self._blocked_at()
        self.assertGreater(_t, 0)
        # 回滚侧 mark_rollback_failed 不得影响 has_rolled_back / 计数
        _ver = SelfVerifier(self.tmp)
        _ver.mark_pending(1)
        _ver.mark_rollback_failed("x")
        self.assertFalse(_ver.has_rolled_back())
        # 计数仍可被期满重置（互不遮蔽）
        _old = time.time() - 100 * 3600.0
        with io.open(os.path.join(self.tmp, "restart_blocked_at.txt"), "w", encoding="utf-8") as f:
            f.write(str(_old))
        _write_pending(self.tmp, [_patch(id="c", status="approved")])
        with mock.patch("nucleus.reasoning.PatchManager._module_logger"):
            _r = self.mgr.apply_all_pending(only_approved=True)
        self.assertIsNot(_r.get("loop_protection"), True)


class TestD3RollbackQuota(unittest.TestCase):
    """★第159批 刀1：D-3 回滚额度（T-回滚额度-1）。

    验收(b)：回滚失败不得置 rolled_back（否则「每次待验证只自动回退一次」额度被烧）。
    """
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m53_d3_")
        self.ver = SelfVerifier(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mark_pending_initializes_reason(self):
        self.ver.mark_pending(1)
        _d = safe_read_json(self.ver._verify_file, default={})
        self.assertEqual(_d.get("rollback_failed_reason", "MISSING"), "")

    def test_mark_rolled_back_sets_flag_only(self):
        self.ver.mark_pending(1)
        self.ver.mark_rolled_back()
        _d = safe_read_json(self.ver._verify_file, default={})
        self.assertTrue(_d.get("rolled_back"))
        self.assertEqual(_d.get("rollback_failed_reason", ""), "")

    def test_mark_rollback_failed_burns_no_quota(self):
        """②回滚失败不烧额度：mark_rollback_failed 置原因但不置 rolled_back。"""
        self.ver.mark_pending(1)
        self.ver.mark_rollback_failed("backup_missing")
        _d = safe_read_json(self.ver._verify_file, default={})
        self.assertFalse(_d.get("rolled_back", False),
                         "回滚失败不得置位 rolled_back（额度不被烧）")
        self.assertEqual(_d.get("rollback_failed_reason"), "backup_missing")
        self.assertFalse(self.ver.has_rolled_back(),
                         "has_rolled_back 必须为 False（额度保留给真实回退）")


# ============================ 刀3：补丁生命周期表（O-B7）============================
class TestPatchLifecycle(unittest.TestCase):
    """★第159批 刀3（O-B7）：补丁生命周期表。

    直接用 patch_lifecycle 模块函数 + 显式 tmp path 验证（测试环境
    _writable 守卫禁止写生产 data/，故不依赖 PatchManager 的隐式路径）。
    """
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m53_k3_")
        self.lp = os.path.join(self.tmp, "patch_lifecycle.json")
        self.mgr = _mk_mgr(self.tmp)  # 集成测试需真实 PatchManager 实例（沙箱根）
        reset_event_bus()  # 隔离事件总线

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_60_activated_written(self):
        """验收 a：applied 补丁生命周期表可查 stage=activated + backup_path。"""
        _pl.record_activation("p1", risk_level="低", file="config.py",
                              backup_path=self.tmp, rollback_available=True,
                              path=self.lp)
        _e = _pl.load_lifecycle(self.lp)["patches"]["p1"]
        self.assertEqual(_e["stage"], "activated")
        self.assertEqual(_e["backup_path"], self.tmp)
        self.assertTrue(_e["rollback_available"])
        self.assertGreater(_e["observation_deadline"], time.time())

    def test_61_rolled_back_written(self):
        """验收 c/d：rolled_back_at 0→≥1；失败不置位由调用方保证。"""
        _pl.record_activation("p1", rollback_available=True, path=self.lp)
        _pl.record_rollback("p1", reason="test", path=self.lp)
        _e = _pl.load_lifecycle(self.lp)["patches"]["p1"]
        self.assertEqual(_e["stage"], "rolled_back")
        self.assertGreater(_e.get("rolled_back_at", 0), 0)

    def test_62_rollback_unavailable_rejected(self):
        """验收 a 硬点：rollback_available=False 者不得进入 activated。"""
        _pl.record_activation("p2", rollback_available=False, path=self.lp)
        _e = _pl.load_lifecycle(self.lp)["patches"]["p2"]
        self.assertEqual(_e["stage"], "rejected")
        self.assertNotEqual(_e["stage"], "activated")

    def test_63_reconcile_no_auto_rollback(self):
        """验收 b：observing 条目超期不被改判/自动回滚，只出诊断回执。"""
        _pl.record_activation("p3", rollback_available=True, path=self.lp)
        _d = _pl.load_lifecycle(self.lp)
        _d["patches"]["p3"]["stage"] = "observing"
        _d["patches"]["p3"]["observation_deadline"] = time.time() - 10
        with io.open(self.lp, "w", encoding="utf-8") as _f:
            json.dump(_d, _f, ensure_ascii=False)
        _res = _pl.reconcile_patches(path=self.lp)
        self.assertEqual(_res["overdue"], 1)
        _e = _pl.load_lifecycle(self.lp)["patches"]["p3"]
        self.assertEqual(_e["stage"], "observing", "超期不得改判/回滚")
        self.assertTrue(_e.get("overdue"))
        self.assertFalse(
            _e.get("overdue_receipts", [{}])[-1].get("auto_rollback"))

    def test_64_handoff_event_published(self):
        """验收 e：发布 evolution.patch_handoff 事件，订阅方收到。"""
        _received = []
        _bus = get_event_bus()
        _bus.subscribe("evolution.patch_handoff",
                       lambda ev: _received.append(ev))
        _pl.record_activation("p4", rollback_available=True, path=self.lp)
        self.assertEqual(len(_received), 1)
        _ev = _received[0]
        self.assertEqual(_ev.name, "evolution.patch_handoff")
        self.assertEqual(_ev.payload["patch_id"], "p4")
        self.assertEqual(_ev.payload["stage"], "activated")

    def test_65_apply_all_pending_triggers_activation(self):
        """写入点集成：apply_all_pending 成功应用后调用 record_activation。"""
        import nucleus.evolution.patch_lifecycle as _plmod
        _calls = []
        _orig = _plmod.record_activation

        def _spy(*a, **k):
            _calls.append((a, k))
            return _orig(*a, **k)

        with mock.patch.object(_plmod, "record_activation", _spy):
            _target = os.path.join(self.tmp, "target_mod.py")
            with io.open(_target, "w", encoding="utf-8") as _f:
                _f.write("X = 1\nY = 2\n")
            _doc = {
                "id": "int1", "file": _target, "method": "m",
                "risk_level": "低", "confidence": "high", "trust_score": 99,
                "status": "approved", "original_code": "X = 1",
                "modified_code": "X = 1  # patched",
            }
            _write_pending(self.tmp, [_doc])
            self.mgr._backup_manager = mock.MagicMock()
            _r = self.mgr.apply_all_pending(only_approved=True)
        self.assertEqual(_r["applied"], 1, "补丁应被成功应用")
        self.assertEqual(len(_calls), 1, "record_activation 应被调用一次")
        self.assertEqual(_calls[0][0][0], "int1")
        self.assertTrue(_calls[0][1].get("rollback_available"))

    def test_66_reconcile_wired_and_activated_overdue(self):
        """★第159批 上B 刀A 接线测试：启动链路调用 + activated 超期出回执不回滚。

        (a) main.py 启动段在 reconcile_on_startup() **之后**调用 reconcile_patches()
            （源码级接线断言，防脱链 / 防顺序错）。
        (b) activated 且超 observation_deadline 未 committed → 出诊断回执：
            回执含 patch_id + 超期状态（stage 仍 activated），auto_rollback=False。
        """
        # --- (a) 启动链路接线（源码级） ---
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(_root, "main.py"), encoding="utf-8") as _f:
            _src = _f.read()
        self.assertIn("reconcile_patches()", _src,
                      "main.py 启动段应调用 reconcile_patches()")
        self.assertLess(
            _src.index("reconcile_on_startup()"),
            _src.index("reconcile_patches()"),
            "reconcile_patches() 应在 reconcile_on_startup() 之后追加")
        # --- (b) activated 超期出回执、不自动回滚 ---
        _pl.record_activation("p9", rollback_available=True, path=self.lp)
        _d = _pl.load_lifecycle(self.lp)
        self.assertEqual(_d["patches"]["p9"]["stage"], "activated")
        _d["patches"]["p9"]["observation_deadline"] = time.time() - 10
        with io.open(self.lp, "w", encoding="utf-8") as _f:
            json.dump(_d, _f, ensure_ascii=False)
        _res = _pl.reconcile_patches(path=self.lp)
        self.assertEqual(_res["overdue"], 1)
        _rcpt = _res["receipts"][0]
        self.assertEqual(_rcpt["patch_id"], "p9", "回执须含 patch_id")
        self.assertEqual(_rcpt["stage"], "activated", "回执须含超期状态")
        self.assertFalse(_rcpt["auto_rollback"], "绝不自动回滚")
        _e = _pl.load_lifecycle(self.lp)["patches"]["p9"]
        self.assertEqual(_e["stage"], "activated", "超期不得改判/回滚")
        self.assertTrue(_e.get("overdue"))


if __name__ == "__main__":
    unittest.main()
