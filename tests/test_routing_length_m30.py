# -*- coding: utf-8 -*-
"""主线第30批 门控测试：路由标度修复 + 操作指令正则 + 本地推理长度（T1）。

背景（本批 T0 实测）：
  第29批按 `StrategySelector.estimate_complexity` 的标度设阈值 0.6，
  但 InnerWorld 实际用 `_assess_question_complexity`（同一问题仅 0.15）
  → 95 号检测器几乎永不触发。本批改为 0.3 并回归验证。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

# InnerWorld 标度：技术域(系统/架构) + 因果(为什么) + 价值(意义) + 身份(意义) + 长度>30
_Q_INNER_HIGH = "请分析数字生命的系统架构与自我认知的意义，为什么成长会影响身份认同？"
# 论述框架（含"首先…最后""第一部分"）——不应被当作操作指令
# 同时满足 InnerWorld 复杂度：技术域(系统/架构) + 身份域(意义) + 因果(为什么) + 价值(意义) + 长
_Q_STRUCTURED = ("请分三部分论述：第一部分分析数字生命的系统架构，"
                 "第二部分讨论自我认知的意义，为什么成长会影响身份？")
# 真操作类指令
_Q_OPERATIONAL = "先打开设置，再点击蓝牙，然后配对设备。"
_Q_SIMPLE = "你好"


class _Ctx:
    def __init__(self, question, complexity=0.0):
        self.question = question
        self._question_complexity = complexity
        self.user_name = "测试"
        self.correlation_id = "cid-m30"
        self.empathetic_note = None
        self._memory_context = None
        self.payload = {}
        self._reasoning_start_time = 0.0


class _Switch:
    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(config, k, v)
        return False


def _mk_lung():
    l = PulseInnerWorld.__new__(PulseInnerWorld)
    l._log = lambda *a, **k: None
    return l


class TestOperationalSignal(unittest.TestCase):
    """操作类指令判定（本批修正子串匹配缺陷）。"""

    def test_01_structured_framework_not_operational(self):
        """★核心回归：含"首先…最后"的**结构化论述**不得被判为操作指令。"""
        self.assertFalse(_mk_lung()._m29_has_multi_step_signal(_Q_STRUCTURED))

    def test_02_first_part_not_operational(self):
        """★"第一部分/第二部分"是论述框架，不是操作指令。"""
        self.assertFalse(_mk_lung()._m29_has_multi_step_signal(
            "请分三部分说明：第一部分讲背景，第二部分讲方法，第三部分讲结论。"))

    def test_03_real_operational_detected(self):
        """真正的操作序列仍被识别（无回归）。"""
        self.assertTrue(_mk_lung()._m29_has_multi_step_signal(_Q_OPERATIONAL))

    def test_04_step_chain_detected(self):
        """显式步骤链仍被识别。"""
        self.assertTrue(_mk_lung()._m29_has_multi_step_signal(
            "第一步安装依赖，第二步运行脚本，然后检查输出。"))

    def test_05_plain_question_not_operational(self):
        """普通长问题不误判。"""
        self.assertFalse(_mk_lung()._m29_has_multi_step_signal(
            "请分析人工智能的发展现状与潜在风险。"))


class TestComplexityScale(unittest.TestCase):
    """复杂度标度校准（本批核心修正）。"""

    def test_06_inner_world_scale_baseline(self):
        """★回归锚点：InnerWorld 标度下，普通问题复杂度低于阈值。"""
        l = _mk_lung()
        _c = l._assess_question_complexity("你好")
        self.assertLess(_c, 0.3, f"实测 {_c}")

    def test_07_inner_world_scale_high(self):
        """★核心：多域+因果+价值的长问题在 InnerWorld 标度下达阈值。"""
        l = _mk_lung()
        _c = l._assess_question_complexity(_Q_INNER_HIGH)
        self.assertGreaterEqual(_c, 0.3, f"实测 {_c}（阈值 0.3）")

    def test_08_threshold_default_aligned(self):
        """配置默认阈值已对齐 InnerWorld 标度（0.3），不再是 0.6。"""
        self.assertAlmostEqual(float(config.DEEP_THINK_COMPLEXITY_THRESHOLD), 0.3, places=3)

    def test_09_triggers_with_inner_scale(self):
        """★核心：InnerWorld 标度达标 → 触发 reason=complexity。"""
        l = _mk_lung()
        _c = l._assess_question_complexity(_Q_INNER_HIGH)
        self.assertEqual(l._m29_deep_think_trigger_reason(_Ctx(_Q_INNER_HIGH, _c)), "complexity")

    def test_10_low_complexity_not_triggered(self):
        """低复杂度不触发（保持快速路径）。"""
        self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx(_Q_SIMPLE, 0.15)), "")

    def test_11_structured_passes_now(self):
        """★核心修复：结构化论述问题现在能进入深度思考（此前被误排除）。"""
        l = _mk_lung()
        _c = l._assess_question_complexity(_Q_STRUCTURED)
        self.assertGreaterEqual(_c, 0.3, f"InnerWorld 复杂度实测 {_c}")
        self.assertEqual(l._m29_deep_think_trigger_reason(_Ctx(_Q_STRUCTURED, _c)), "complexity")


class TestLocalLengthHint(unittest.TestCase):
    """本地推理输出长度（本批新增）。"""

    def test_12_hint_for_long_question(self):
        """长问题 → 生成长度指令。"""
        h = _mk_lung()._m30_local_length_hint(_Q_INNER_HIGH)
        self.assertTrue(h)
        self.assertIn(str(config.LLM_TARGET_OUTPUT_LENGTH), h)

    def test_13_no_hint_for_short_question(self):
        """短问题 → 不注入指令（保持简洁体验）。"""
        self.assertEqual(_mk_lung()._m30_local_length_hint("你好"), "")

    def test_14_switch_off_rolls_back(self):
        """★灰度：开关关闭 → 判定为「未启用」。"""
        with _Switch(ENABLE_LOCAL_REASONING_LENGTH_OPTIMIZATION=False):
            self.assertFalse(_mk_lung()._m30_local_length_enabled())

    def test_15_switch_on_by_default(self):
        """默认开启。"""
        self.assertTrue(_mk_lung()._m30_local_length_enabled())

    def test_16_hint_target_zero_disables(self):
        """目标字数 0 → 不生成指令。"""
        with _Switch(LLM_TARGET_OUTPUT_LENGTH=0):
            self.assertEqual(_mk_lung()._m30_local_length_hint(_Q_INNER_HIGH), "")


class TestDegradedGuard(unittest.TestCase):
    """降级标记不得拼进用户可见答案（本批修复）。"""

    def test_17_degraded_marker_not_leaked_source(self):
        """源码层：融合前存在 degraded 判定分支。"""
        src = open(os.path.join(_PROJECT_ROOT, "organs/brain/PulseInnerWorld.py"),
                      encoding="utf-8").read()
        self.assertIn("_m30_degraded", src)
        self.assertIn("深度思考返回降级标记", src)

    def test_18_threshold_not_regressed_to_06(self):
        """防止阈值被改回旧标度值（回归护栏）。"""
        self.assertNotAlmostEqual(float(config.DEEP_THINK_COMPLEXITY_THRESHOLD), 0.6, places=3)


if __name__ == "__main__":
    unittest.main()
