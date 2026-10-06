# -*- coding: utf-8 -*-
"""163批 刀1 门控单测：推理路由入口放宽（本地推理链兜底·判定链设计 + 静态接线）。

验收口径（施工任务书 刀1，前段）：
- 开关关闭（默认）：route_with_local_fallback 对「未知」问题返回等价 select_strategy，
  不出现 local_fallback 路由（零行为变化）。
- 开关开启：未知问题返回 route="local_fallback" 且 chain==[symbolic,causal,analogy,llm]，
  顺序为兜底执行序。
- 已知类型（如「定义解释」）即便开关开启也不走兜底（检测器可识别）。
- 活体拦截率 <2%→≥40% 的验收留待双 P0 后实测，本测试只验证静态接线正确性。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.reasoning.StrategySelector as SS


class TestKnife1M163(unittest.TestCase):
    def test_switch_off_no_fallback_route(self):
        _sel = SS.StrategySelector(local_fallback_enabled=False)
        _r = _sel.route_with_local_fallback("随便问个问题", question_type="未知")
        self.assertNotEqual(_r.get("route"), "local_fallback")
        self.assertIn("strategy", _r)

    def test_switch_on_unknown_gets_local_fallback_chain(self):
        _sel = SS.StrategySelector(local_fallback_enabled=True)
        _r = _sel.route_with_local_fallback("随便问个问题", question_type="未知")
        self.assertEqual(_r.get("route"), "local_fallback")
        self.assertEqual(_r.get("chain"), ["symbolic", "causal", "analogy", "llm"])

    def test_switch_on_known_type_no_fallback(self):
        _sel = SS.StrategySelector(local_fallback_enabled=True)
        _r = _sel.route_with_local_fallback("什么是脉冲架构？", question_type="定义解释")
        self.assertNotEqual(_r.get("route"), "local_fallback")
        self.assertIn("strategy", _r)

    def test_setter_toggles_runtime(self):
        _sel = SS.StrategySelector(local_fallback_enabled=False)
        _sel.set_local_fallback_enabled(True)
        _r = _sel.route_with_local_fallback("x", question_type="未知")
        self.assertEqual(_r.get("route"), "local_fallback")


if __name__ == "__main__":
    unittest.main()
