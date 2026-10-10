# -*- coding: utf-8 -*-
"""第48批 T2 门控测试：跨盘安全的路径工具（P2-320）

覆盖：
  * ``safe_relpath``：跨盘降级为绝对路径；同盘行为与 ``os.path.relpath`` **完全一致**
  * ``drive_of`` / ``same_drive``：盘符判定（含 UNC）
  * ``safe_commonpath``：跨盘返回空串
  * 回归：本项目真实跨盘场景（沙箱 C: ↔ 项目 D:）不抛异常
  * 集成：两个 CLI 工具在跨盘路径下不崩

★测试隔离：全部使用 ``tempfile.mkdtemp()``。
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.data.path_utils import drive_of, safe_commonpath, safe_relpath, same_drive  # noqa: E402

#: 本项目（D:）与系统临时目录（通常 C:）—— 真实跨盘场景
_PROJECT_DRIVE = drive_of(_ROOT)
_TEMP_DRIVE = drive_of(tempfile.gettempdir())
_CROSS_DRIVE_AVAILABLE = (_PROJECT_DRIVE != "" and _TEMP_DRIVE != ""
                          and _PROJECT_DRIVE != _TEMP_DRIVE)


# ==================== 基本语义 ====================

class TestDriveHelpers(unittest.TestCase):
    def test_01_drive_of(self):
        if os.name != "nt":
            self.skipTest("盘符判定仅 Windows")
        self.assertEqual(drive_of(r"C:\a\b"), "C:")
        self.assertEqual(drive_of(r"D:\a\b"), "D:")

    def test_02_drive_of_relative(self):
        if os.name != "nt":
            self.skipTest("盘符判定仅 Windows")
        # 相对路径经 abspath 后应能取到当前盘
        self.assertNotEqual(drive_of("a/b"), "")

    def test_03_drive_of_invalid(self):
        self.assertEqual(drive_of(None), "")
        self.assertEqual(drive_of(""), drive_of(os.getcwd()))

    def test_04_same_drive(self):
        if os.name != "nt":
            self.skipTest("盘符判定仅 Windows")
        self.assertTrue(same_drive(r"D:\a", r"D:\b"))
        self.assertFalse(same_drive(r"C:\a", r"D:\b"))

    def test_05_same_drive_pure_paths(self):
        self.assertTrue(same_drive(_ROOT, os.path.join(_ROOT, "x")))


class TestSafeRelpath(unittest.TestCase):
    def test_10_same_drive_equals_std(self):
        """★同盘行为必须与 os.path.relpath **完全一致**（零行为变化）。"""
        _p = os.path.join(_ROOT, "nucleus", "logger.py")
        self.assertEqual(safe_relpath(_p, _ROOT),
                         os.path.relpath(_p, _ROOT))

    def test_11_same_drive_nested(self):
        _a = os.path.join(_ROOT, "a", "b", "c.py")
        self.assertEqual(safe_relpath(_a, os.path.join(_ROOT, "a")),
                         os.path.join("b", "c.py"))

    def test_12_cross_drive_returns_abs(self):
        """★核心：跨盘时返回绝对路径，绝不抛异常。"""
        if not _CROSS_DRIVE_AVAILABLE:
            self.skipTest("当前环境无跨盘（同盘或非 Windows）")
        _c = os.path.join(tempfile.gettempdir(), "m48", "x.json")
        with self.assertRaises(ValueError):
            os.path.relpath(_c, _ROOT)          # 原生会炸
        _r = safe_relpath(_c, _ROOT)             # 本工具不炸
        self.assertTrue(os.path.isabs(_r))

    def test_13_cross_drive_no_exception(self):
        if not _CROSS_DRIVE_AVAILABLE:
            self.skipTest("当前环境无跨盘")
        # ★不用 noqa: BLE001（ruff 未启用该规则 → 会变成"死 noqa"，被
        #   test_entry_probe_m35 的死 noqa 守卫检出）。改为断言"不抛异常"。
        _raised = None
        try:
            safe_relpath(tempfile.gettempdir(), _ROOT)
        except (ValueError, OSError, TypeError) as _e:
            _raised = _e
        self.assertIsNone(_raised, "safe_relpath 不应抛异常，实得 %r" % _raised)

    def test_14_start_none(self):
        """start=None 时等价于 os.path.relpath(path)（基于 cwd）。"""
        _p = os.path.join(_ROOT, "main.py")
        self.assertEqual(safe_relpath(_p, None), os.path.relpath(_p))

    def test_15_invalid_input_returns_str(self):
        self.assertIsInstance(safe_relpath(None), str)

    def test_16_symlink_absent_dir_ok(self):
        """不存在的路径也应正常相对化（不要求 exists）。"""
        _p = os.path.join(_ROOT, "no", "such", "file.py")
        self.assertEqual(safe_relpath(_p, _ROOT),
                         os.path.join("no", "such", "file.py"))


class TestSafeCommonpath(unittest.TestCase):
    def test_20_same_drive(self):
        _a = os.path.join(_ROOT, "a")
        _b = os.path.join(_ROOT, "b")
        self.assertEqual(safe_commonpath([_a, _b]), os.path.abspath(_ROOT))

    def test_21_cross_drive_returns_empty(self):
        if not _CROSS_DRIVE_AVAILABLE:
            self.skipTest("当前环境无跨盘")
        self.assertEqual(safe_commonpath([_ROOT, tempfile.gettempdir()]), "")

    def test_22_invalid_returns_empty(self):
        self.assertEqual(safe_commonpath([]), "")
        self.assertEqual(safe_commonpath([None]), "")


# ==================== 集成：CLI 工具跨盘不崩 ====================

class TestToolIntegration(unittest.TestCase):
    def test_30_serp_analyzer_cross_drive_pool(self):
        """★--pool 传跨盘路径时，serp_pollution_analyzer 不得崩。"""
        if not _CROSS_DRIVE_AVAILABLE:
            self.skipTest("当前环境无跨盘")
        _d = tempfile.mkdtemp(prefix="m48serp_")
        try:
            _pool = os.path.join(_d, "pool.json")
            with io.open(_pool, "w", encoding="utf-8") as _f:
                _f.write('{"experiences": []}')
            _r = subprocess.run(
                [sys.executable,
                 os.path.join(_ROOT, "tools", "serp_pollution_analyzer.py"),
                 "--pool", _pool],
                cwd=_ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180)
            _out = (_r.stdout or "") + (_r.stderr or "")
            self.assertNotIn("ValueError", _out,
                             "跨盘路径导致 ValueError: %s" % _out[-300:])
            self.assertEqual(_r.returncode, 0, _out[-300:])
        finally:
            shutil.rmtree(_d, ignore_errors=True)

    def test_31_backfill_cross_drive_target(self):
        """★--target 传跨盘路径时，回填工具不得崩（dry-run）。"""
        if not _CROSS_DRIVE_AVAILABLE:
            self.skipTest("当前环境无跨盘")
        _d = tempfile.mkdtemp(prefix="m48bf_")
        try:
            _t = os.path.join(_d, "patch_history.json")
            with io.open(_t, "w", encoding="utf-8") as _f:
                _f.write('{"patches": []}')
            _r = subprocess.run(
                [sys.executable,
                 os.path.join(_ROOT, "tools",
                              "backfill_patch_verification_split.py"),
                 "--target", _t],
                cwd=_ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=180)
            _out = (_r.stdout or "") + (_r.stderr or "")
            self.assertNotIn("ValueError", _out,
                             "跨盘路径导致 ValueError: %s" % _out[-300:])
            self.assertEqual(_r.returncode, 0, _out[-300:])
        finally:
            shutil.rmtree(_d, ignore_errors=True)


# ==================== 源码层：改造已落地 ====================

class TestAdoption(unittest.TestCase):
    def _read(self, rel):
        _p = os.path.join(_ROOT, rel)
        return io.open(_p, encoding="utf-8", errors="replace").read() \
            if os.path.isfile(_p) else None

    def test_40_serp_uses_safe(self):
        _t = self._read(os.path.join("tools", "serp_pollution_analyzer.py"))
        self.assertIn("safe_relpath", _t)
        self.assertNotIn("os.path.relpath(path, ROOT)", _t)

    def test_41_backfill_uses_safe(self):
        _t = self._read(os.path.join("tools",
                                     "backfill_patch_verification_split.py"))
        self.assertIn("safe_relpath", _t)
        self.assertNotIn("os.path.relpath(path, ROOT)", _t)
        self.assertNotIn("os.path.relpath(_dst, ROOT)", _t)

    def test_42_tmp_backup_delegates(self):
        _t = self._read(os.path.join("tools", "tmp_backup.py"))
        self.assertIn("nucleus.data.path_utils", _t)

    def test_43_logger_already_guarded(self):
        """logger.py 路径相对化：★第55批 T3 已接入 safe_relpath（契约同步，强度不降）。

        旧契约（第48批）：用 os.path.relpath，靠 try/except 兜底跨盘 ValueError。
        新契约（第55批）：改用 nucleus.data.path_utils.safe_relpath（跨盘降级绝对路径），
        仍保留 try/except 兜底。两条断言同时成立才算守住契约。
        """
        _t = self._read(os.path.join("nucleus", "logger.py"))
        if _t is None:
            self.skipTest("logger.py 不存在")

        # 1) 已接入 path_utils.safe_relpath（跨盘安全）
        self.assertIn("nucleus.data.path_utils", _t, "logger.py 应接入 path_utils")
        _i = _t.find("safe_relpath")
        self.assertGreater(_i, 0, "logger.py 应调用 safe_relpath")

        # 2) 不再直接使用裸 os.path.relpath 做路径相对化
        self.assertNotIn("os.path.relpath(", _t, "logger.py 不应残留裸 os.path.relpath")

        # 3) 仍保留 try/except 保护（原判据保留，强度不降）
        _seg = _t[max(0, _i - 300):_i + 300]
        self.assertIn("except", _seg, "logger.py 的 relpath 应有 try/except 保护")


if __name__ == "__main__":
    unittest.main(verbosity=2)
