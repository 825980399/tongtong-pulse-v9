# -*- coding: utf-8 -*-
"""主线第29批 门控测试：深度思考入口路由优化（T1/P2-175）。

覆盖：
  - 关键词触发（原行为）与高复杂度触发（新增）双分支
  - 阈值 / 长度下限 / 灰度开关的配置化
  - 真多步任务与简单问题不被误伤（零回归）
  - 端到端 _detect_force_deep_think 路由生效 + 异常降级
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

# 高复杂度（估算 0.7：基础 0.3 + 分析/系统/架构/设计/优化/方案/对比/综合 ×0.05）且长度 ≥30
_HIGH_Q = "请分析当前系统的架构设计与优化方向，对比多种方案的优劣，综合权衡后给出建议。"
# 含多步指令（先…然后…最后）
_MULTISTEP_Q = "先分析这个系统的架构，然后对比两种方案的优劣，最后给出优化建议和落地步骤。"
_SIMPLE_Q = "你好"


class _Ctx:
    """最小 InferenceContext 替身（避免构造 22k 行器官的真实上下文）。"""

    def __init__(self, question, complexity=0.0):
        self.question = question
        self._question_complexity = complexity
        self.user_name = "测试"
        self.correlation_id = "cid-m29"
        self.empathetic_note = None
        self._memory_context = None
        self.payload = {}
        self._reasoning_start_time = 0.0


class _Switch:
    """临时改写 config 属性（用例结束后还原）。"""

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


def _mk_lung(deep_result="深度思考的完整回答内容" * 3):
    """构造轻量实例（绕 __init__），mock 掉执行流水线的全部副作用。"""
    l = PulseInnerWorld.__new__(PulseInnerWorld)
    l._log = lambda *a, **k: None
    l._reasoning_pool = None          # 强制走主进程回退路径
    l._inference_count = 0
    l._deep_think_max_rounds = 3
    l._deep_think_timeout = 45
    l._deep_think = lambda *a, **k: deep_result
    l._enhance_answer = lambda answer, **k: answer
    l._cache_inference = lambda *a, **k: None
    l._trace_inference = lambda *a, **k: None
    l._emit = lambda *a, **k: None
    l._m27_timeout_protection_on = lambda: True
    l._m27_deep_think_budget = lambda: 40.0
    return l


class TestTriggerReason(unittest.TestCase):
    """触发原因判定（T1 核心）。"""

    def test_01_keyword_trigger_still_works(self):
        """原有关键词触发不受影响。"""
        self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx("请用三轮递进的方式思考", 0.2)),
                         "keyword")

    def test_02_high_complexity_triggers(self):
        """高复杂度长问题 → complexity（本批新增能力）。"""
        self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx(_HIGH_Q, 0.7)),
                         "complexity")

    def test_03_low_complexity_not_triggered(self):
        """复杂度不足 → 不触发（避免拖慢普通问题）。

        ★主线第30批 T1 同步：阈值由 0.6 调整为 0.3（对齐 InnerWorld 标度），
          故本用例的「低复杂度」取样值由 0.3 下调为 0.2。
        """
        self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx(_HIGH_Q, 0.2)), "")

    def test_04_short_question_not_triggered(self):
        """复杂度够但问题过短 → 不触发。"""
        self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(
            _Ctx("请分析系统设计优化方案对比综合", 0.9)), "")

    def test_05_multi_step_task_not_hijacked(self):
        """含明确多步指令 → 仍交由 multi_step_execute（不抢单）。"""
        self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx(_MULTISTEP_Q, 0.8)), "")

    def test_06_simple_query_not_triggered(self):
        """简单询问词 → 保持快速路径。"""
        self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx(_SIMPLE_Q, 0.9)), "")

    def test_07_none_question_robust(self):
        """question=None / 复杂度 None 不崩溃。"""
        l = _mk_lung()
        self.assertEqual(l._m29_deep_think_trigger_reason(_Ctx(None, 0.9)), "")
        self.assertEqual(l._m29_deep_think_trigger_reason(_Ctx(_HIGH_Q, None)), "")


class TestGraySwitchAndConfig(unittest.TestCase):
    """灰度开关与配置化。"""

    def test_08_switch_off_full_rollback(self):
        """开关关闭 → 高复杂度不再触发（完全回退）。"""
        with _Switch(ENABLE_DEEP_THINK_ROUTING_OPTIMIZATION=False):
            self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx(_HIGH_Q, 0.7)), "")

    def test_09_switch_off_keeps_keyword(self):
        """开关关闭 → 关键词路径保持（原行为不能被灰度破坏）。"""
        with _Switch(ENABLE_DEEP_THINK_ROUTING_OPTIMIZATION=False):
            self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx("三轮递进", 0.2)),
                             "keyword")

    def test_10_threshold_configurable(self):
        """阈值可配（调低后可触发、调高后不触发）。"""
        with _Switch(DEEP_THINK_COMPLEXITY_THRESHOLD=0.5):
            self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx(_HIGH_Q, 0.5)),
                             "complexity")
        with _Switch(DEEP_THINK_COMPLEXITY_THRESHOLD=0.9):
            self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx(_HIGH_Q, 0.7)), "")

    def test_11_min_chars_configurable(self):
        """长度下限可配。"""
        with _Switch(DEEP_THINK_MIN_QUESTION_CHARS=200):
            self.assertEqual(_mk_lung()._m29_deep_think_trigger_reason(_Ctx(_HIGH_Q, 0.7)), "")

    def test_12_config_defaults_present(self):
        """三项配置默认值齐备且合理。"""
        self.assertTrue(hasattr(config, "ENABLE_DEEP_THINK_ROUTING_OPTIMIZATION"))
        self.assertEqual(config.ENABLE_DEEP_THINK_ROUTING_OPTIMIZATION, True)
        # ★主线第30批 T1 同步：0.6 → 0.3（InnerWorld 标度对齐；详见 config 注释）
        self.assertAlmostEqual(float(config.DEEP_THINK_COMPLEXITY_THRESHOLD), 0.3, places=3)
        self.assertGreaterEqual(int(config.DEEP_THINK_MIN_QUESTION_CHARS), 10)


class TestDetectorEndToEnd(unittest.TestCase):
    """检测器端到端（路由是否真的进入 _deep_think）。"""

    def test_13_high_complexity_enters_deep_think(self):
        """高复杂度长问题 → deep_think_forced。"""
        r = _mk_lung()._detect_force_deep_think(_Ctx(_HIGH_Q, 0.7))
        self.assertIsInstance(r, dict)
        self.assertEqual(r.get("status"), "deep_think_forced")
        self.assertTrue(r.get("answer"))

    def test_14_keyword_path_unchanged(self):
        """关键词路径行为不变（零回归）。"""
        r = _mk_lung()._detect_force_deep_think(_Ctx("请用三轮递进结构回答", 0.1))
        self.assertIsInstance(r, dict)
        self.assertEqual(r.get("status"), "deep_think_forced")

    def test_15_multi_step_falls_through(self):
        """真多步任务不被深度思考拦截（返回 None 交由后续检测器）。"""
        self.assertIsNone(_mk_lung()._detect_force_deep_think(_Ctx(_MULTISTEP_Q, 0.8)))

    def test_16_low_complexity_falls_through(self):
        """低复杂度问题不被拦截。"""
        self.assertIsNone(_mk_lung()._detect_force_deep_think(_Ctx(_HIGH_Q, 0.2)))

    def test_17_deep_think_exception_degrades(self):
        """_deep_think 抛异常 → 降级为 deep_think_fallback，不向上抛。"""
        l = _mk_lung()
        l._deep_think = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟失败"))
        r = l._detect_force_deep_think(_Ctx(_HIGH_Q, 0.7))
        self.assertIsInstance(r, dict)
        self.assertEqual(r.get("status"), "deep_think_fallback")


class TestLazyStateAndStats(unittest.TestCase):
    """惰性状态与统计（兼容 Cls.__new__ 场景）。"""

    def test_18_lazy_state_available(self):
        """未调 __init__ 也有统计字段（防 AttributeError）。"""
        s = _mk_lung().get_deep_think_routing_stats()
        self.assertIsInstance(s, dict)
        for k in ("keyword", "complexity", "rejected_multi_step"):
            self.assertIn(k, s)

    def test_19_stats_counting(self):
        """三类计数正确累加。"""
        l = _mk_lung()
        l._m29_deep_think_trigger_reason(_Ctx("三轮递进", 0.1))
        l._m29_deep_think_trigger_reason(_Ctx(_HIGH_Q, 0.7))
        l._m29_deep_think_trigger_reason(_Ctx(_MULTISTEP_Q, 0.8))
        s = l.get_deep_think_routing_stats()
        self.assertEqual(s.get("keyword"), 1)
        self.assertEqual(s.get("complexity"), 1)
        self.assertEqual(s.get("rejected_multi_step"), 1)

    def test_20_helpers_are_pure(self):
        """多步信号与简单询问词判定可独立复用。"""
        l = _mk_lung()
        self.assertTrue(l._m29_has_multi_step_signal("先做A，然后做B"))
        self.assertFalse(l._m29_has_multi_step_signal("分析一下这个系统的架构"))
        self.assertTrue(l._m29_is_simple_query("你好"))
        self.assertFalse(l._m29_is_simple_query("请系统分析架构设计的权衡"))


if __name__ == "__main__":
    unittest.main()
