# -*- coding: utf-8 -*-
"""往期批次 刀3 超时故障注入测试：_ir_run_detectors 的 45s 超时中断。

验证：当推理起点时间被注入为"很久以前"时，检测器调度循环应在首轮迭代即触发
超时中断（break），返回 None，且日志输出「推理超时」——而非继续调用检测器。
"""
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from organs.brain.PulseInnerWorld import PulseInnerWorld


_DETECTOR_NAMES = [
    "_detect_simple_query_local",
    "_detect_pure_emotion",
    "_detect_force_deep_think",
    "_detect_multi_step_task",
    "_detect_multi_branch_think",
    "_detect_branch_expand_request",
    "_detect_health_check",
    "_detect_deep_review_report",
    "_detect_meta_cognitive_report",
    "_detect_long_term_evolution",
    "_detect_rule_reason",
    "_detect_experience_route",
    "_detect_conflict_exclusive",
    "_detect_file_analysis",
    "_detect_code_call_chain",
    "_detect_simple_logic",
    "_detect_composite_logic",
    "_detect_symbolic_reason",
    "_detect_cognitive_operator",
]


class TestIrDetectorTimeoutT147(unittest.TestCase):
    """刀3：检测器调度 45s 超时中断（故障注入）。"""

    def _make_iw(self):
        iw = PulseInnerWorld.__new__(PulseInnerWorld)
        iw._log = mock.MagicMock()
        self._detector_calls = []
        for name in _DETECTOR_NAMES:
            def _stub(ctx, _name=name):
                self._detector_calls.append(_name)
                return None
            setattr(iw, name, _stub)
        return iw

    def test_01_timeout_breaks_before_any_detector(self):
        """超时注入：起点在 100s 前 → 首轮即 break，检测器零调用。"""
        iw = self._make_iw()
        ctx = PulseInnerWorld.InferenceContext("测试问题", "用户", "cid", {})
        ctx._reasoning_start_time = time.time() - 100.0  # 远早于 45s 阈值

        result = iw._ir_run_detectors(ctx)

        self.assertIsNone(result, "超时中断应返回 None")
        self.assertEqual(self._detector_calls, [], "超时后不得调用任何检测器")

        # 日志含「推理超时」
        warn_msgs = []
        for c in iw._log.call_args_list:
            joined = " ".join(str(a) for a in c.args)
            if "推理超时" in joined:
                warn_msgs.append(joined)
        self.assertTrue(warn_msgs, f"未输出「推理超时」日志: {iw._log.call_args_list}")

    def test_02_no_timeout_does_not_log_timeout(self):
        """无故障：起点在当下 → 不产生「推理超时」日志（0 新增）。"""
        iw = self._make_iw()
        ctx = PulseInnerWorld.InferenceContext("测试问题", "用户", "cid", {})
        ctx._reasoning_start_time = time.time()  # 当下，未超时

        iw._ir_run_detectors(ctx)

        timeout_msgs = []
        for c in iw._log.call_args_list:
            joined = " ".join(str(a) for a in c.args)
            if "推理超时" in joined:
                timeout_msgs.append(joined)
        self.assertEqual(timeout_msgs, [], "无故障时不得出现「推理超时」日志")


if __name__ == "__main__":
    unittest.main()
