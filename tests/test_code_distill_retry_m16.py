# -*- coding: utf-8 -*-
"""
test_code_distill_retry_m16.py —— 主线第16批 任务3 门控单测（P2-97 蒸馏子进程）

根因：`PulseCodeLearner` 代码修复蒸馏失败时只打 status、把第15批 T3 已落盘的
`crash_traceback` / `stderr_tail` / `error` 字段丢弃，导致「有原因却看不见」。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from organs.brain.PulseCodeLearner import PulseCodeLearner  # noqa: E402


def _learner():
    """轻量实例化（只挂统计容器，不需要完整 __init__）。"""
    obj = PulseCodeLearner.__new__(PulseCodeLearner)
    obj._code_distill_stats = {"attempts": 0, "success": 0, "failed": 0,
                               "retry_success": 0, "reasons": {},
                               "degraded_to_main": 0}
    return obj


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


class TestFailureReason(unittest.TestCase):
    def test_config_defaults(self):
        self.assertIs(getattr(config, "ENABLE_CODE_DISTILL_RETRY", None), True)
        self.assertEqual(getattr(config, "CODE_DISTILL_MAX_RETRIES", None), 1)

    def test_retry_config(self):
        lr = _learner()
        self.assertEqual(lr._code_distill_retry_config(), (True, 1))
        with _Switch(ENABLE_CODE_DISTILL_RETRY=False):
            self.assertFalse(lr._code_distill_retry_config()[0])
        with _Switch(CODE_DISTILL_MAX_RETRIES=-3):
            self.assertEqual(lr._code_distill_retry_config()[1], 0)

    def test_reason_from_error_field(self):
        r = _describe = PulseCodeLearner._describe_code_distill_failure(
            {"status": "crashed", "error": "ImportError: no module named x"})
        self.assertIn("crashed", r)
        self.assertIn("ImportError", r)

    def test_reason_from_traceback_tail(self):
        r = PulseCodeLearner._describe_code_distill_failure({
            "status": "crashed",
            "crash_traceback": "Traceback...\n  File a.py\nValueError: boom"})
        self.assertIn("ValueError: boom", r)

    def test_reason_from_stderr_tail(self):
        r = PulseCodeLearner._describe_code_distill_failure({
            "status": "error", "stderr_tail": "...\nFatal Python error"})
        self.assertIn("Fatal Python error", r)

    def test_reason_fallback(self):
        self.assertIn("无详细信息",
                      PulseCodeLearner._describe_code_distill_failure({"status": "x"}))
        self.assertEqual(PulseCodeLearner._describe_code_distill_failure(None),
                         "结果格式异常")


class TestStats(unittest.TestCase):
    def test_success_and_failure_stats(self):
        lr = _learner()
        lr._record_code_distill("success", "")
        lr._record_code_distill("failed", "crashed: ImportError: x")
        lr._record_code_distill("failed", "crashed: ImportError: y")
        lr._record_code_distill("failed", "timeout: 超时600s")
        st = lr.get_code_distill_stats()
        self.assertEqual(st["attempts"], 4)
        self.assertEqual(st["success"], 1)
        self.assertEqual(st["failed"], 3)
        self.assertEqual(st["reasons"]["crashed"], 2)
        self.assertEqual(st["reasons"]["timeout"], 1)
        self.assertAlmostEqual(st["success_rate"], 0.25, places=4)

    def test_stats_empty_shape(self):
        lr = _learner()
        st = lr.get_code_distill_stats()
        self.assertEqual(st["attempts"], 0)
        self.assertEqual(st["success_rate"], 0.0)
        self.assertEqual(st["reasons"], {})

    def test_stats_survives_missing_container(self):
        obj = PulseCodeLearner.__new__(PulseCodeLearner)   # 无 _code_distill_stats
        self.assertEqual(obj.get_code_distill_stats()["success_rate"], 0.0)


class TestSourceWiring(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(_PROJECT_ROOT, "organs", "brain",
                                  "PulseCodeLearner.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_reason_logged(self):
        self.assertIn("原因={_reason}", self.src)
        self.assertIn("代码修复蒸馏崩溃 traceback(尾)", self.src)
        self.assertIn("代码修复蒸馏 stderr(尾)", self.src)

    def test_retry_loop_wired(self):
        self.assertIn("for _attempt in range(1, _attempts + 1):", self.src)
        self.assertIn("准备重试", self.src)
        self.assertIn('== "timeout"', self.src)

    def test_degrade_to_main_still_present(self):
        # ★第95批 T-95d：`repair_with_distillation` 调用新增 `scene=` 关键字 ⇒
        #   原跨行文本断言改为 **AST 判定**（铁律 100：脆弱源码文本断言改版本无关复算）
        import ast as _ast
        _hit = False
        for _n in _ast.walk(_ast.parse(self.src)):
            if (isinstance(_n, _ast.Call)
                    and isinstance(_n.func, _ast.Attribute)
                    and _n.func.attr == "repair_with_distillation"
                    and _n.args
                    and _ast.unparse(_n.args[0]) == "issues"):
                _hit = True
        self.assertTrue(_hit, "★「降级到主通道」的蒸馏调用必须仍在")
        self.assertIn("degraded_to_main", self.src)


if __name__ == "__main__":
    unittest.main()
