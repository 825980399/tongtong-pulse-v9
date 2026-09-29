# -*- coding: utf-8 -*-
"""
test_evolution_crash_patch_m15.py —— 主线第15批 任务3+任务5 门控单测

T3（P1-93 子进程崩溃）：崩溃统计、重试配置与开关、stderr 读取、降级路径、
                       源码接线（stderr/traceback 落日志、重试、降级）。
T5（P2-96 补丁中文标点）：新增映射字符、字符串/注释区不受影响、
                          通用 NFKC 兜底、两轮预检、开关默认值。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402

_SEE_SRC = os.path.join(_PROJECT_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_EW_SRC = os.path.join(_PROJECT_ROOT, "nucleus", "reasoning", "evolution_worker.py")
_AQSG_SRC = os.path.join(_PROJECT_ROOT, "nucleus", "reasoning",
                         "AdaptiveQueryStrategyGenerator.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


class _ExecutorHarness:
    def __init__(self):
        self.exe = SafeEvolutionExecutor()


# =====================================================================
# T3
# =====================================================================
class TestCrashConfig(unittest.TestCase):
    def test_defaults(self):
        self.assertIs(getattr(config, "ENABLE_EVOLUTION_CRASH_RETRY", None), True)
        self.assertEqual(getattr(config, "EVOLUTION_CRASH_MAX_RETRIES", None), 2)

    def test_retry_config_switch(self):
        h = _ExecutorHarness()
        with _Switch(ENABLE_EVOLUTION_CRASH_RETRY=True, EVOLUTION_CRASH_MAX_RETRIES=2):
            self.assertEqual(h.exe._crash_retry_config(), (True, 2))
        with _Switch(ENABLE_EVOLUTION_CRASH_RETRY=False):
            self.assertEqual(h.exe._crash_retry_config()[0], False)

    def test_retry_config_negative_clamped(self):
        h = _ExecutorHarness()
        with _Switch(EVOLUTION_CRASH_MAX_RETRIES=-5):
            self.assertEqual(h.exe._crash_retry_config()[1], 0)


class TestCrashStats(unittest.TestCase):
    def test_record_and_read(self):
        h = _ExecutorHarness()
        h.exe._record_crash("review", "ImportError: no module named x", "tb-1")
        h.exe._record_crash("review", "ImportError: another", "tb-2")
        h.exe._record_crash("repair", "TimeoutError: timeout", "")
        st = h.exe.get_crash_stats()
        self.assertEqual(st["total"], 3)
        self.assertEqual(st["review"]["count"], 2)
        self.assertEqual(st["review"]["reasons"]["ImportError"], 2)
        self.assertEqual(st["repair"]["reasons"]["TimeoutError"], 1)
        self.assertEqual(st["review"]["last_detail"], "tb-2")

    def test_stats_empty_initially(self):
        h = _ExecutorHarness()
        self.assertEqual(h.exe.get_crash_stats(), {"total": 0})


class TestStderrReader(unittest.TestCase):
    def test_read_tail(self):
        h = _ExecutorHarness()
        p = os.path.join(_PROJECT_ROOT, "tmp", "_evo_err_test.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write("A" * 5000 + "\nTAIL-MARK")
            tail = h.exe._read_text_tail(p, limit=100)
            self.assertIn("TAIL-MARK", tail)
            self.assertLessEqual(len(tail), 100)
        finally:
            if os.path.exists(p):
                os.remove(p)

    def test_read_tail_missing_file(self):
        h = _ExecutorHarness()
        self.assertEqual(h.exe._read_text_tail("Z:/not/exists.log"), "")


class TestDegradedPath(unittest.TestCase):
    def test_degrade_returns_none_when_inspector_unavailable(self):
        import nucleus.self_inspector as si
        h = _ExecutorHarness()
        _orig = si.get_self_inspector

        def _boom():
            raise RuntimeError("no inspector")

        si.get_self_inspector = _boom
        try:
            self.assertIsNone(h.exe._run_in_process_degraded([], "review", None, 5, 3))
        finally:
            si.get_self_inspector = _orig

    def test_degrade_unknown_mode_returns_none(self):
        import nucleus.self_inspector as si
        h = _ExecutorHarness()
        _orig = si.get_self_inspector
        si.get_self_inspector = lambda: object()
        try:
            self.assertIsNone(h.exe._run_in_process_degraded([], "unknown", None, 5, 3))
        finally:
            si.get_self_inspector = _orig


class TestCrashSourceWiring(unittest.TestCase):
    """源码护栏：崩溃原因可诊断 + 重试 + 降级都必须真实接线（回退即失败）。"""

    def setUp(self):
        self.src = _read(_SEE_SRC)

    def test_stderr_and_traceback_logged(self):
        self.assertIn("子进程 stderr(尾)", self.src)
        self.assertIn("子进程崩溃 traceback(尾)", self.src)
        self.assertIn("_read_text_tail(stderr_path)", self.src)

    def test_retry_loop_wired(self):
        self.assertIn("for _attempt in range(1, _attempts + 1):", self.src)
        self.assertIn("自动重试（", self.src)

    def test_degrade_wired(self):
        self.assertIn("_run_in_process_degraded(issues, mode, plans, max_issues, max_plans)",
                      self.src)
        self.assertIn("已降级为同进程同步执行", self.src)

    def test_stderr_temp_file_present(self):
        self.assertIn('suffix="_evo_err.log"', self.src)
        self.assertIn("for _p in (input_path, output_path, stderr_path):", self.src)

    def test_worker_uses_captured_entry(self):
        self.assertIn("target=run_evolution_worker_captured", self.src)


class TestWorkerEntry(unittest.TestCase):
    def test_worker_module_top_level_is_stdlib_only(self):
        head = "\n".join(_read(_EW_SRC).splitlines()[:30])
        self.assertNotIn("from nucleus.", head,
                         "spawn 入口模块顶层不得有非 stdlib 导入（否则导入期崩溃无法自报）")

    def test_captured_entry_exists(self):
        self.assertIn("def run_evolution_worker_captured(", _read(_EW_SRC))


# =====================================================================
# T5
# =====================================================================
class TestPatchPunctuation(unittest.TestCase):
    def setUp(self):
        self.exe = SafeEvolutionExecutor()

    def test_new_chars_in_map(self):
        for ch in ("「", "」", "『", "』", "。", "、", "→", "《", "》", "【", "】", "…", "～"):
            self.assertIn(ch, self.exe._FULLWIDTH_CODE_MAP, f"{ch} 应加入映射表")

    def test_code_area_punctuation_fixed(self):
        """★第55批同步：归一化**功能**必须在「返回原始」开关**关闭**时验证。

        背景（★非本批引入）：第54批 T6.2 起，归一化后语法仍不合法时
        `_clean_llm_code` 返回**原始**代码（灰度 `ENABLE_LLM_PATCH_RETURN_ORIGINAL`，
        默认 True），以避免归一化本身引入新的语法错误。
        本用例测的是「标点映射是否生效」，故关闭该开关、
        走「返回归一化结果 `_cur`」的分支来断言。
        """
        _orig = config.ENABLE_LLM_PATCH_RETURN_ORIGINAL
        try:
            config.ENABLE_LLM_PATCH_RETURN_ORIGINAL = False
            # ★铁律 49：断言必须写在开关生效期间（块内）
            fixed = self.exe._clean_llm_code('x = 「a」。b → c')
            self.assertEqual(fixed, 'x = "a".b -> c')
        finally:
            config.ENABLE_LLM_PATCH_RETURN_ORIGINAL = _orig

    def test_code_area_punctuation_returns_original_by_default(self):
        """★第54批 T6.2 行为变更守卫：开关**开启**（默认）时返回**原始**代码。

        归一化后语法仍不合法 → 返回原始，避免引入新的语法错误。
        """
        _orig = config.ENABLE_LLM_PATCH_RETURN_ORIGINAL
        try:
            config.ENABLE_LLM_PATCH_RETURN_ORIGINAL = True
            # ★铁律 49：断言在块内
            fixed = self.exe._clean_llm_code('x = 「a」。b → c')
            self.assertEqual(fixed, 'x = 「a」。b → c',
                             "开关开启时应返回原始代码（第54批 T6.2）")
        finally:
            config.ENABLE_LLM_PATCH_RETURN_ORIGINAL = _orig

    def test_actual_failure_case_now_parses(self):
        """复现实证失败样本：含「」。的补丁归一化后必须能 ast.parse。"""
        import ast
        bad = 'def f():\n    return 「x」\n'
        fixed = self.exe._clean_llm_code(bad)
        ast.parse(fixed)          # 不抛异常即通过

    def test_strings_and_comments_untouched(self):
        code = 'x = "中文，标点。「」"\n# 注释：这里，有「」和。\n'
        out = self.exe._clean_llm_code(code)
        self.assertIn('"中文，标点。「」"', out)
        self.assertIn("# 注释：这里，有「」和。", out)

    def test_generic_nfkc_fallback(self):
        """映射表未覆盖但 NFKC 可归一的兼容字符也应被修正。"""
        _code = "y = a﹕b"          # U+FE55 小型冒号
        _fixed, n = self.exe._normalize_fullwidth_in_code(_code)
        self.assertGreaterEqual(n, 1)
        self.assertNotIn("﹕", _fixed)

    def test_docstring_content_preserved(self):
        code = '"""模块说明：含「」与。"""\nx = 1\n'
        out = self.exe._clean_llm_code(code)
        self.assertIn("含「」与。", out)

    def test_llm_prompt_requires_ascii(self):
        src = _read(_AQSG_SRC)
        self.assertIn("只使用 ASCII 字符", src)
        self.assertIn("不要使用任何中文/全角标点", src)

    def test_two_round_retry_marker(self):
        self.assertIn("for _round in (1, 2):", _read(_SEE_SRC))


if __name__ == "__main__":
    unittest.main()
