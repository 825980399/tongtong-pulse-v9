# -*- coding: utf-8 -*-
"""第41批 T1 门控测试：进化验证真实基线（判据修正 / 窗口语义 / post_apply_errors）。

★测试隔离：轻量实例的 ``_project_root`` 指向 ``tempfile.mkdtemp()``，
在临时目录内造 ``logs/pulse.log`` —— **绝不读生产日志**。
"""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402

_LOG_FMT = "%s [SafeEvolutionExecutor] %s: %s"


def _mk_exec(root):
    _e = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
    _e._project_root = root
    return _e


class _Base(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.mkdtemp(prefix="m41_t1_")
        os.makedirs(os.path.join(self._root, "logs"), exist_ok=True)
        self._ex = _mk_exec(self._root)
        self._orig_mode = getattr(config, "EVOLUTION_BASELINE_MATCH_MODE", None)
        self._orig_on = getattr(config, "ENABLE_EVOLUTION_BASELINE_FIX", None)
        self._orig_win = getattr(config, "EVOLUTION_BASELINE_WINDOW_DAYS", None)

    def tearDown(self):
        for _k, _v in (("EVOLUTION_BASELINE_MATCH_MODE", self._orig_mode),
                       ("ENABLE_EVOLUTION_BASELINE_FIX", self._orig_on),
                       ("EVOLUTION_BASELINE_WINDOW_DAYS", self._orig_win)):
            if _v is not None:
                setattr(config, _k, _v)
        shutil.rmtree(self._root, ignore_errors=True)

    def _write_log(self, rows, name="pulse.log"):
        _p = os.path.join(self._root, "logs", name)
        with io.open(_p, "w", encoding="utf-8") as f:
            for _r in rows:
                f.write(_r + "\n")
        return _p


class TestMatchModes(_Base):
    def test_01_file_method_mode_legacy_zero(self):
        """旧判据（文件名+方法名）：日志无方法名 → 命中 0（复现修复前）。"""
        config.EVOLUTION_BASELINE_MATCH_MODE = "file_method"
        self._write_log([
            "2026-09-13 12:00:00 [Lung] ERROR: PulseLung.py 调用失败: TimeoutError",
            "2026-09-13 12:00:01 [Lung] ERROR: PulseLung.py 另一个错误",
        ])
        self.assertEqual(
            self._ex._count_errors_for_location("organs/body/PulseLung.py",
                                                "_call_channel"), 0)

    def test_02_file_mode_hits(self):
        """新判据（仅文件名）：按文件命中。"""
        config.EVOLUTION_BASELINE_MATCH_MODE = "file"
        self._write_log([
            "2026-09-13 12:00:00 [Lung] ERROR: PulseLung.py 调用失败",
            "2026-09-13 12:00:01 [Lung] ERROR: PulseLung.py 另一个错误",
            "2026-09-13 12:00:02 [Heart] ERROR: PulseHeart.py 无关错误",
        ])
        self.assertEqual(
            self._ex._count_errors_for_location("organs/body/PulseLung.py",
                                                "_call_channel"), 2)

    def test_03_file_or_method_mode(self):
        config.EVOLUTION_BASELINE_MATCH_MODE = "file_or_method"
        self._write_log([
            "2026-09-13 12:00:00 [X] ERROR: PulseLung.py 失败",
            "2026-09-13 12:00:01 [X] ERROR: 方法 _call_channel 异常",
            "2026-09-13 12:00:02 [X] ERROR: 无关内容",
        ])
        self.assertEqual(
            self._ex._count_errors_for_location("organs/body/PulseLung.py",
                                                "_call_channel"), 2)

    def test_04_invalid_mode_falls_back_to_file(self):
        config.EVOLUTION_BASELINE_MATCH_MODE = "bogus"
        self.assertEqual(SafeEvolutionExecutor._m41_match_mode(), "file")

    def test_05_non_error_lines_ignored(self):
        config.EVOLUTION_BASELINE_MATCH_MODE = "file"
        self._write_log([
            "2026-09-13 12:00:00 [Lung] INFO: PulseLung.py 正常运行",
            "2026-09-13 12:00:01 [Lung] DEBUG: PulseLung.py 调试信息",
            "2026-09-13 12:00:02 [Lung] WARNING: PulseLung.py 警告",
        ])
        self.assertEqual(
            self._ex._count_errors_for_location("organs/body/PulseLung.py",
                                                "_call_channel"), 0)


class TestWindow(_Base):
    _ROWS = [
        "2026-09-10 10:00:00 [L] ERROR: PulseLung.py 旧错误",
        "2026-09-12 10:00:00 [L] ERROR: PulseLung.py 中期错误",
        "2026-09-13 12:00:00 [L] ERROR: PulseLung.py 新错误",
    ]

    def test_10_since_filters(self):
        config.EVOLUTION_BASELINE_MATCH_MODE = "file"
        self._write_log(self._ROWS)
        _since = time.mktime(time.strptime("2026-09-12 00:00:00",
                                           "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(
            self._ex._count_errors_for_location("x/PulseLung.py", "m",
                                                since=_since), 2)

    def test_11_until_filters(self):
        config.EVOLUTION_BASELINE_MATCH_MODE = "file"
        self._write_log(self._ROWS)
        _until = time.mktime(time.strptime("2026-09-12 00:00:00",
                                           "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(
            self._ex._count_errors_for_location("x/PulseLung.py", "m",
                                                until=_until), 1)

    def test_12_window_range(self):
        config.EVOLUTION_BASELINE_MATCH_MODE = "file"
        self._write_log(self._ROWS)
        _a = time.mktime(time.strptime("2026-09-11 00:00:00", "%Y-%m-%d %H:%M:%S"))
        _b = time.mktime(time.strptime("2026-09-13 00:00:00", "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(
            self._ex._count_errors_for_location("x/PulseLung.py", "m",
                                                since=_a, until=_b), 1)

    def test_13_baseline_since_days(self):
        config.EVOLUTION_BASELINE_WINDOW_DAYS = 7
        _s = self._ex._m41_baseline_since()
        self.assertAlmostEqual(time.time() - _s, 7 * 86400, delta=60)

    def test_14_baseline_since_zero_means_all(self):
        config.EVOLUTION_BASELINE_WINDOW_DAYS = 0
        self.assertEqual(self._ex._m41_baseline_since(), 0.0)


class TestSwitchRegression(_Base):
    def test_20_switch_off_restores_legacy(self):
        """开关关闭 → 判据与窗口都回到修复前（零回归）。"""
        config.ENABLE_EVOLUTION_BASELINE_FIX = False
        config.EVOLUTION_BASELINE_MATCH_MODE = "file"   # 即使配了 file，关开关也应走旧判据
        self._write_log([
            "2026-09-13 12:00:00 [L] ERROR: PulseLung.py 失败",
        ])
        self.assertEqual(
            self._ex._count_errors_for_location("x/PulseLung.py", "m"), 0)
        self.assertEqual(self._ex._m41_baseline_since(), 0.0)

    def test_21_switch_on_default(self):
        self.assertTrue(SafeEvolutionExecutor._m41_baseline_fix_on())

    def test_22_empty_file_path(self):
        self.assertEqual(self._ex._count_errors_for_location("", "m"), 0)

    def test_23_missing_log_file(self):
        self.assertEqual(
            self._ex._count_errors_for_location("x/PulseLung.py", "m"), 0)


class TestPostApplyErrors(_Base):
    def _patch(self):
        return {"file": os.path.join(self._root, "PulseLung.py"),
                "method": "_call_channel",
                "fixed_at": time.time() - 1000,
                "baseline_errors": 3}

    def test_30_result_has_post_apply_errors(self):
        self._write_log(["2026-09-13 12:00:00 [L] ERROR: PulseLung 失败"])
        _r = self._ex.verify_fix_from_logs(self._patch())
        self.assertIn("post_apply_errors", _r)
        self.assertEqual(_r["post_apply_errors"], _r["after_fix"])

    def test_31_effectiveness_uses_baseline(self):
        self._write_log([])
        _r = self._ex.verify_fix_from_logs(self._patch())
        self.assertEqual(_r["baseline"], 3)
        self.assertEqual(_r["after_fix"], 0)
        self.assertEqual(_r["effectiveness"], 1.0)

    def test_32_no_fixed_at(self):
        _p = self._patch()
        _p["fixed_at"] = 0
        _r = self._ex.verify_fix_from_logs(_p)
        self.assertFalse(_r["verified"])
        self.assertIn("post_apply_errors", _r)

    def test_33_too_soon(self):
        _p = self._patch()
        _p["fixed_at"] = time.time() - 10      # < 300s
        _r = self._ex.verify_fix_from_logs(_p)
        self.assertFalse(_r["verified"])

    def test_34_never_raises(self):
        _r = self._ex.verify_fix_from_logs({"file": "", "method": ""})
        self.assertIsInstance(_r, dict)


class TestBackfillTool(unittest.TestCase):
    def test_40_tool_importable(self):
        import importlib
        _m = importlib.import_module("tools.backfill_baseline_errors")
        self.assertTrue(callable(_m.main))

    def test_41_effectiveness_formula(self):
        import importlib
        _m = importlib.import_module("tools.backfill_baseline_errors")
        self.assertEqual(_m._effectiveness(4, 4), 0.0)
        self.assertEqual(_m._effectiveness(4, 0), 1.0)
        self.assertEqual(_m._effectiveness(0, 0), 1.0)
        self.assertEqual(_m._effectiveness(0, 3), 0.5)
        self.assertEqual(_m._effectiveness(10, 8), 0.2)

    def test_42_dry_run_does_not_write(self):
        import importlib
        _m = importlib.import_module("tools.backfill_baseline_errors")
        self.assertEqual(_m.main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
