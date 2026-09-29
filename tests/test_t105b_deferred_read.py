# -*- coding: utf-8 -*-
"""★第105批 T-105b（P1）：延期标记只写不读修复 —— 读取点（次级排序键）验证。

任务书 T-105b（P1）：``_deferred_from_prev_round`` 此前**只写不读**（全仓 0 读取点），
导致被截断延期的问题永久轮不上（实测 8 位置饥饿）。第105批补**读取点**：
``_plan_multi_step_repair`` 以「是否延期」作次级排序键，延期组排到同优先级队尾。

本文件固化该读取点语义（真实源码，不复刻逻辑）：
  A. 同优先级下，延期组排在新鲜组之后（step_index 更大）。
  B. 窗口容量充足时，延期组仍获 slots（不被永久排除）。
  C. 窗口触顶截断时，优先保留新鲜组、丢弃延期组（饥饿被打破）。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor  # noqa: E402


def _issue(iid, method, deferred=False, itype="print_instead_of_log"):
    _d = {"id": iid, "file": "organs/body/PulseStomach.py", "method": method,
          "type": itype, "priority": 0}
    if deferred:
        _d["_deferred_from_prev_round"] = True
    return _d


class TestT105bDeferredRead(unittest.TestCase):
    def setUp(self):
        self.exe = SafeEvolutionExecutor()

    def _step_ids(self, issues, max_steps):
        self.exe._max_repair_steps = max_steps
        _steps = self.exe._plan_multi_step_repair(issues)
        _ids = []
        for _s in _steps:
            for _i in _s.get("issues", []):
                if isinstance(_i, dict):
                    _ids.append(_i.get("id"))
        return _ids

    def test_A_deferred_sorted_after_fresh_same_priority(self):
        """★A：同优先级下，延期组必须排到新鲜组之后。"""
        _issues = [
            _issue("a", "fresh_a", deferred=False),
            _issue("b", "deferred_b", deferred=True),
        ]
        self.exe._max_repair_steps = 10  # 容量充足，两组都入窗
        _ids = self._step_ids(_issues, 10)
        self.assertIn("a", _ids)   # fresh_a
        self.assertIn("b", _ids)   # deferred_b
        self.assertLess(_ids.index("a"), _ids.index("b"),
                        "★同优先级下延期组必须排到新鲜组之后")

    def test_B_deferred_gets_slot_when_capacity_allows(self):
        """★B：容量充足时延期组仍须获 slots（非永久排除）。"""
        _issues = [
            _issue("a", "fresh_a", deferred=False),
            _issue("b", "fresh_b", deferred=False),
            _issue("c", "deferred_c", deferred=True),
        ]
        _ids = self._step_ids(_issues, 10)
        self.assertIn("c", _ids,
                      "★容量充足时延期组仍须获 slots（非永久排除）")

    def test_C_truncation_keeps_fresh_drops_deferred(self):
        """★C：窗口触顶截断时，优先保留新鲜组、丢弃延期组（饥饿被打破）。"""
        _issues = [
            _issue("a", "fresh_a", deferred=False),
            _issue("b", "deferred_b1", deferred=True),
            _issue("c", "deferred_b2", deferred=True),
        ]
        self.exe._max_repair_steps = 1  # 仅容 1 步
        _ids = self._step_ids(_issues, 1)
        self.assertEqual(1, len(_ids))
        self.assertIn("a", _ids)   # fresh_a 优先保留
        self.assertNotIn("b", _ids)
        self.assertNotIn("c", _ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
