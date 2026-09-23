# -*- coding: utf-8 -*-
"""主线第30批 门控测试：日志级别子串判定残留修复（T2/P2-177）。

本批将 4 处「日志文件级别统计」改为复用 `extract_log_level` / `is_error_level_line`，
并对 2 处（stderr 判定、日志取证）经评估**保留**原宽松判据。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from nucleus.evolution.LogAnalyzer import (  # noqa: E402
    extract_log_level,
    is_error_level_line,
)

# 关键回归样本：WARNING 级但正文含 "JSONDecodeError"
_WARN_WITH_ERROR_WORD = (
    "2026-09-11 19:25:16 [胃] WARNING: PulseStomach JSON策略2(正则提取)失败: "
    "JSONDecodeError: Expecting ':' delimiter: line 1 column 261 (char 260)"
)
_REAL_ERROR = "2026-09-12 04:00:41 [控制器] ERROR: 导航失败: net::ERR_NAME_NOT_RESOLVED"
_REAL_CRITICAL = "2026-09-12 10:00:00 [X] CRITICAL: 内存不足"
_INFO_WITH_ERROR_WORD = "2026-09-12 11:42:22 [SEE] INFO: 改用文件/ERROR消息作为对比输入"
_LEGACY_NO_MARKER = "2026-01-01 00:00:00 legacy line without level but has ERROR keyword"


class TestIsErrorLevelLine(unittest.TestCase):
    """共享判据（本批新增）。"""

    def test_01_warning_with_error_word_not_error(self):
        """★核心回归：WARNING 行（含 JSONDecodeError）不算错误级。"""
        self.assertFalse(is_error_level_line(_WARN_WITH_ERROR_WORD))

    def test_02_real_error_is_error(self):
        """真实 ERROR 行算错误级。"""
        self.assertTrue(is_error_level_line(_REAL_ERROR))

    def test_03_critical_is_error(self):
        """CRITICAL 行算错误级。"""
        self.assertTrue(is_error_level_line(_REAL_CRITICAL))

    def test_04_info_with_error_word_not_error(self):
        """INFO 行（含 ERROR 字样）不算错误级。"""
        self.assertFalse(is_error_level_line(_INFO_WITH_ERROR_WORD))

    def test_05_legacy_line_backward_compatible(self):
        """无级别标记但含 ERROR → 回退宽松判定（向后兼容）。"""
        self.assertTrue(is_error_level_line(_LEGACY_NO_MARKER))

    def test_06_traceback_line_not_error_level(self):
        """裸堆栈行（无级别标记、无 ERROR 字样）不算错误级（由 Traceback 判据单独处理）。"""
        self.assertFalse(is_error_level_line('  File "x.py", line 3, in f'))


class TestExtractLevelConsistency(unittest.TestCase):
    """与第29批工具口径一致。"""

    def test_07_level_extraction(self):
        self.assertEqual(extract_log_level(_WARN_WITH_ERROR_WORD), "WARNING")
        self.assertEqual(extract_log_level(_REAL_ERROR), "ERROR")
        self.assertEqual(extract_log_level(_LEGACY_NO_MARKER), "")

    def test_08_four_modules_use_shared_helper(self):
        """★4 处日志统计模块已改用共享判据（防回退到子串匹配）。"""
        targets = {
            "nucleus/reasoning/SafeEvolutionExecutor.py": "is_error_level_line",
            "nucleus/evolution/ParamABTestEngine.py": "extract_log_level",
            "nucleus/evolution/ParamPatchEffectVerifier.py": "extract_log_level",
            "nucleus/evolution/ParamPatchManager.py": "extract_log_level",
        }
        for rel, needle in targets.items():
            src = open(os.path.join(_PROJECT_ROOT, rel), encoding="utf-8").read()
            self.assertIn(needle, src, f"{rel} 未使用共享判据")

    def test_09_stderr_case_kept_intentionally(self):
        """`PatchManager` 的 stderr 判定经评估**保留**（应有说明注释）。"""
        src = open(os.path.join(_PROJECT_ROOT, "nucleus/reasoning/PatchManager.py"),
                      encoding="utf-8").read()
        self.assertIn("保守正确", src)


if __name__ == "__main__":
    unittest.main()
