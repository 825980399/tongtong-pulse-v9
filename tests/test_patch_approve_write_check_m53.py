# -*- coding: utf-8 -*-
"""第53批 T2 门控测试：approve_* 写入成功检查（P0-补丁2）。

覆盖：
  ① `_save_json` 三路径返回显式 bool（成功 True / 守卫拒绝 False / 异常 False）
  ② approve_patch / approve_all_patches 在写盘失败时返回 ok=False
  ③ 返回值契约兼容 UI（reason 字段）与既有调用方
  ④ 灰度开关 ENABLE_PATCH_APPROVE_WRITE_CHECK 关闭 → 零回归
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io
import json
import shutil
import tempfile
import unittest
from unittest import mock

import config
import nucleus.data.write_guard as _wg
from nucleus.reasoning.PatchManager import PatchManager

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mk_mgr(tmp):
    """轻量实例（绕 __init__），全部落盘指向沙箱目录 → 不碰生产 data/。"""
    p = PatchManager.__new__(PatchManager)
    p._patch_dir = tmp
    p._history_file = os.path.join(tmp, "patch_history.json")
    p._pending_file = os.path.join(tmp, "pending_patches.json")
    return p


def _write_pending(tmp, n=2, status="pending"):
    items = [{"id": "p%d" % i, "file": "config.py", "method": "m%d" % i,
              "status": status, "risk_level": "低", "trust_score": 40,
              "original_code": "x", "modified_code": "y"}
             for i in range(n)]
    with io.open(os.path.join(tmp, "pending_patches.json"), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    return items


def _read_pending(tmp):
    with io.open(os.path.join(tmp, "pending_patches.json"), encoding="utf-8") as f:
        return json.load(f)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m53_w_")
        self.mgr = _mk_mgr(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSaveJsonReturnValue(_Base):
    """★核心：`_save_json` 必须显式返回 bool（不能返回 None）。"""

    def test_01_success_returns_true(self):
        _r = self.mgr._save_json(os.path.join(self.tmp, "ok.json"), {"a": 1})
        self.assertIs(_r, True)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "ok.json")))

    def test_02_guard_reject_returns_false(self):
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            _r = self.mgr._save_json(os.path.join(self.tmp, "d.json"), {"a": 1})
        self.assertIs(_r, False)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "d.json")))

    def test_03_write_error_returns_false(self):
        with mock.patch.object(tempfile, "mkstemp", side_effect=OSError("disk full")):
            _r = self.mgr._save_json(os.path.join(self.tmp, "e.json"), {"a": 1})
        self.assertIs(_r, False)

    def test_04_never_returns_none(self):
        """★回归护栏：三路径均不得隐式返回 None（本轮修复前成功路径返回 None）。"""
        _r1 = self.mgr._save_json(os.path.join(self.tmp, "a.json"), {})
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            _r2 = self.mgr._save_json(os.path.join(self.tmp, "b.json"), {})
        with mock.patch.object(tempfile, "mkstemp", side_effect=OSError("x")):
            _r3 = self.mgr._save_json(os.path.join(self.tmp, "c.json"), {})
        for _i, _r in enumerate((_r1, _r2, _r3), 1):
            self.assertIsInstance(_r, bool, "路径%d 返回了非 bool（None?）" % _i)

    def test_05_save_patch_list_propagates(self):
        self.assertIs(self.mgr._save_patch_list(os.path.join(self.tmp, "l.json"), []), True)
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            self.assertIs(
                self.mgr._save_patch_list(os.path.join(self.tmp, "l2.json"), []), False)

    def test_06_mkstemp_failure_no_unbound_noise(self):
        """★第53批 T2b：mkstemp 失败时清理块不得抛 UnboundLocalError。"""
        with mock.patch.object(tempfile, "mkstemp", side_effect=OSError("boom")):
            self.assertIs(self.mgr._save_json(os.path.join(self.tmp, "f.json"), {}), False)


class TestApproveAllPatchesWriteCheck(_Base):

    def test_10_success_ok_true(self):
        _write_pending(self.tmp, 2)
        _r = self.mgr.approve_all_patches()
        self.assertIs(_r["ok"], True)
        self.assertEqual(_r["approved"], 2)
        self.assertTrue(all(x["status"] == "approved" for x in _read_pending(self.tmp)))

    def test_11_guard_reject_ok_false(self):
        _write_pending(self.tmp, 2)
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            _r = self.mgr.approve_all_patches()
        self.assertIs(_r["ok"], False)
        self.assertEqual(_r["approved"], 0)

    def test_12_error_reported(self):
        _write_pending(self.tmp, 2)
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            _r = self.mgr.approve_all_patches()
        self.assertIn("WriteGuard", _r["reason"])
        self.assertTrue(_r["error"])

    def test_13_disk_not_modified_on_failure(self):
        """★核心：写盘失败时不得把内存态当成功 —— 磁盘仍保持 pending。"""
        _write_pending(self.tmp, 2)
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            self.mgr.approve_all_patches()
        self.assertTrue(all(x["status"] == "pending" for x in _read_pending(self.tmp)))

    def test_14_zero_count_still_ok(self):
        _write_pending(self.tmp, 2, status="approved")
        _r = self.mgr.approve_all_patches()
        self.assertIs(_r["ok"], True)
        self.assertEqual(_r["approved"], 0)

    def test_15_empty_pending_ok(self):
        _write_pending(self.tmp, 0)
        _r = self.mgr.approve_all_patches()
        self.assertIs(_r["ok"], True)
        self.assertEqual(_r["approved"], 0)


class TestApprovePatchWriteCheck(_Base):

    def test_20_success_ok_true(self):
        _write_pending(self.tmp, 2)
        _r = self.mgr.approve_patch(1)
        self.assertIs(_r["ok"], True)
        self.assertIn("file", _r)

    def test_21_guard_reject_ok_false(self):
        _write_pending(self.tmp, 2)
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            _r = self.mgr.approve_patch(1)
        self.assertIs(_r["ok"], False)
        self.assertEqual(_r["approved"], 0)

    def test_22_disk_not_modified_on_failure(self):
        _write_pending(self.tmp, 2)
        with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
            self.mgr.approve_patch(1)
        self.assertTrue(all(x["status"] == "pending" for x in _read_pending(self.tmp)))

    def test_23_index_out_of_range_unchanged(self):
        _write_pending(self.tmp, 2)
        _r = self.mgr.approve_patch(99)
        self.assertIs(_r["ok"], False)
        self.assertIn("越界", _r["reason"])


class TestSwitchZeroRegression(_Base):

    def test_30_switch_off_restores_old_behavior(self):
        """★零回归：关开关时忽略写入结果，恒 ok=True（与改造前一致）。"""
        _write_pending(self.tmp, 2)
        _orig = config.ENABLE_PATCH_APPROVE_WRITE_CHECK
        try:
            config.ENABLE_PATCH_APPROVE_WRITE_CHECK = False
            with mock.patch.object(_wg, "guard_write", lambda *a, **k: False):
                _r1 = self.mgr.approve_all_patches()
                _r2 = self.mgr.approve_patch(0)
            self.assertIs(_r1["ok"], True)
            self.assertEqual(_r1["approved"], 2)
            self.assertIs(_r2["ok"], True)
        finally:
            config.ENABLE_PATCH_APPROVE_WRITE_CHECK = _orig

    def test_31_switch_default_on(self):
        self.assertIs(getattr(config, "ENABLE_PATCH_APPROVE_WRITE_CHECK", None), True)

    def test_32_switch_helper_defaults_on(self):
        """开关读取失败时按「启用」处理（安全侧）。"""
        from nucleus.reasoning.PatchManager import _m53_write_check_on
        self.assertIs(_m53_write_check_on(), True)


class TestSourceWiring(unittest.TestCase):

    def _src(self):
        return io.open(os.path.join(_ROOT, "nucleus/reasoning/PatchManager.py"),
                       encoding="utf-8", errors="replace").read()

    def test_40_helper_and_calls(self):
        _s = self._src()
        self.assertEqual(_s.count("def _m53_write_check_on()"), 1)
        self.assertEqual(_s.count("if _m53_write_check_on() and not _save_ok:"), 2)

    def test_41_explicit_returns_in_save_json(self):
        _s = self._src()
        self.assertIn("return True                      # ★第53批 T2", _s)
        self.assertIn("return False                     # ★第53批 T2", _s)

    def test_42_guard_reject_logs_warning(self):
        self.assertIn("[补丁落盘] 写盘守卫拒绝写入", self._src())

    def test_43_ui_contract_key_reason(self):
        """chat_service 读 _res.get('reason') → 新返回值必须提供该键。"""
        _s = self._src()
        self.assertIn('"reason": "写入失败（可能被 WriteGuard 拦截）"', _s)


if __name__ == "__main__":
    unittest.main()
