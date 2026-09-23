# -*- coding: utf-8 -*-
"""主线第29批 门控测试：日志级别误判修正（T4/P2-176）。

根因：LogAnalyzer / HealthScore 用 `"ERROR" in _upper` 子串匹配判定级别，
导致消息正文含 "JSONDecodeError" 等字样的 WARNING 行被误判为 ERROR。
修复：改用「真实级别标记」解析（extract_log_level）。

覆盖：级别解析口径 / LogAnalyzer 不再误判 / HealthScore 不再重复计数 /
      无标记行向后兼容 / 真实 ERROR 仍被识别。
"""
import os
import shutil
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from nucleus.evolution.HealthScore import HealthScore  # noqa: E402
from nucleus.evolution.LogAnalyzer import LogAnalyzer, extract_log_level  # noqa: E402

# 关键回归样本：WARNING 级别，但消息正文含 "JSONDecodeError"（含 ERROR 子串）
_LINE_WARNING_WITH_ERROR_WORD = (
    "2026-09-11 19:25:16 [胃] WARNING: PulseStomach JSON策略2(正则提取)失败: "
    "JSONDecodeError: Expecting ':' delimiter: line 1 column 261 (char 260)"
)
_LINE_REAL_ERROR = (
    "2026-09-12 04:00:41 [控制器] ERROR: 导航失败: Page.goto: "
    "net::ERR_NAME_NOT_RESOLVED at https://example.com"
)
_LINE_INFO_WITH_ERROR_WORD = (
    "2026-09-12 11:42:22 [SafeEvolutionExecutor] INFO: [修复蒸馏] 改用文件/ERROR消息作为对比输入"
)
_LINE_DEBUG = (
    "2026-09-12 11:42:22 [SafeEvolutionExecutor] DEBUG: [修复蒸馏] 方法体为空，跳过"
)


class TestExtractLogLevel(unittest.TestCase):
    """级别标记解析口径。"""

    def test_01_warning_with_error_word(self):
        """★核心回归：含 ERROR 字样的 WARNING 行必须解析为 WARNING。"""
        self.assertEqual(extract_log_level(_LINE_WARNING_WITH_ERROR_WORD), "WARNING")

    def test_02_real_error(self):
        """真实 ERROR 行解析为 ERROR。"""
        self.assertEqual(extract_log_level(_LINE_REAL_ERROR), "ERROR")

    def test_03_info_and_debug(self):
        """INFO / DEBUG 正确解析（即便正文含 ERROR 字样）。"""
        self.assertEqual(extract_log_level(_LINE_INFO_WITH_ERROR_WORD), "INFO")
        self.assertEqual(extract_log_level(_LINE_DEBUG), "DEBUG")

    def test_04_critical(self):
        """CRITICAL 正确解析。"""
        self.assertEqual(extract_log_level("2026-09-12 10:00:00 [X] CRITICAL: boom"), "CRITICAL")

    def test_05_bracket_level_form(self):
        """兼容 `[器官] [LEVEL]` 方括号包裹形式。"""
        self.assertEqual(extract_log_level("2026-09-12 10:00:00 [X] [ERROR] boom"), "ERROR")

    def test_06_no_marker_returns_empty(self):
        """无级别标记行（如裸 Traceback 堆栈）返回空串。"""
        self.assertEqual(extract_log_level('  File "x.py", line 3, in f'), "")
        self.assertEqual(extract_log_level(""), "")

    def test_07_none_safe(self):
        """None 输入不崩溃。"""
        self.assertEqual(extract_log_level(None), "")


class _TmpLog:
    """临时日志文件上下文（退出即清理，清理失败不致命）。"""

    def __init__(self, lines):
        self.lines = lines
        self.dir = None

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="_m29log_")
        p = os.path.join(self.dir, "pulse.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        return p

    def __exit__(self, *a):
        try:
            shutil.rmtree(self.dir, ignore_errors=True)
        except BaseException:  # ★沙箱删除配额被拦时不冒泡
            pass
        return False


class TestLogAnalyzerNoMisjudge(unittest.TestCase):
    """LogAnalyzer 不再把 WARNING 误判为 ERROR。"""

    def test_08_warning_not_counted_as_error(self):
        """★核心回归：3 条含 ERROR 字样的 WARNING + 1 条真实 ERROR → errors 应为 1。"""
        with _TmpLog([_LINE_WARNING_WITH_ERROR_WORD] * 3 + [_LINE_REAL_ERROR]) as p:
            r = LogAnalyzer(_PROJECT_ROOT).analyze(log_file=p)
        self.assertEqual(r["summary"]["errors"], 1, r["summary"])
        errs = [i for i in r["issues"] if i.get("error_type") == "ERROR"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0].get("organ"), "控制器")

    def test_09_error_still_detected(self):
        """真实 ERROR 行仍被识别（不能因修复而漏报）。"""
        with _TmpLog([_LINE_REAL_ERROR]) as p:
            r = LogAnalyzer(_PROJECT_ROOT).analyze(log_file=p)
        self.assertGreaterEqual(r["summary"]["errors"], 1)

    def test_10_info_debug_ignored(self):
        """INFO/DEBUG 行（即便含 ERROR 字样）不计入 errors。"""
        with _TmpLog([_LINE_INFO_WITH_ERROR_WORD, _LINE_DEBUG]) as p:
            r = LogAnalyzer(_PROJECT_ROOT).analyze(log_file=p)
        self.assertEqual(r["summary"]["errors"], 0)


class TestHealthScoreNoMisjudge(unittest.TestCase):
    """HealthScore 日志级别计数不再重复/虚高。"""

    def test_11_warning_not_counted_as_error(self):
        """★核心回归：WARNING 行只进 warnings，不进 errors。"""
        with _TmpLog([_LINE_WARNING_WITH_ERROR_WORD] * 2) as p:
            errors, criticals, tracebacks, total = HealthScore(_PROJECT_ROOT)._scan_log(p, "stability")
        self.assertEqual(errors, 0, f"errors={errors}")
        self.assertEqual(criticals, 0)

    def test_12_warning_mode_counts_warning(self):
        """noise 模式仍正确统计 warnings。"""
        with _TmpLog([_LINE_WARNING_WITH_ERROR_WORD] * 2) as p:
            warnings, total, _rep = HealthScore(_PROJECT_ROOT)._scan_log(p, "noise")
        self.assertEqual(warnings, 2)

    def test_13_real_error_counted(self):
        """真实 ERROR 行正常计入 errors。"""
        with _TmpLog([_LINE_REAL_ERROR]) as p:
            errors, _c, _t, _tot = HealthScore(_PROJECT_ROOT)._scan_log(p, "stability")
        self.assertEqual(errors, 1)

    def test_14_legacy_line_backward_compatible(self):
        """无级别标记但含 ERROR 关键字的旧格式行 → 保留原宽松判定（向后兼容）。"""
        with _TmpLog(["2026-01-01 00:00:00 legacy line without level but has ERROR keyword"]) as p:
            errors, _c, _t, _tot = HealthScore(_PROJECT_ROOT)._scan_log(p, "stability")
        self.assertEqual(errors, 1)


if __name__ == "__main__":
    unittest.main()
