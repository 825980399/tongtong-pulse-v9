# -*- coding: utf-8 -*-
"""474 批第二期门控单测：推理域观测桥（旁路、结构化、不外联）。

核心判据：
  · 曈曈已有的 `verification_learning_hub` 记录被转成**自主性事实**，
    而不是又写进某个外部模型的 prompt；
  · 不修改 hub / 内在世界 / 任何传入对象（旁路）；
  · 同一个 CapabilityKernel 同时服务 code_repair 与 reasoning 域（整体利用）。
"""
import ast
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus._silent_except import silent_exc
from nucleus.autonomy import CapabilityKernel
from nucleus.autonomy.reasoning import DOMAIN_REASONING, ReasoningObserver, classify_hub_entry

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD = os.path.join(_ROOT, "nucleus", "autonomy", "reasoning.py")


def _entry(eid, solved=True, api_better=False, needs_verify=False,
           task="symbolic_compare", organ="inner_world"):
    return {"id": eid, "task_type": task, "organ": organ,
            "local_result": {"solved": solved, "answer": "x"},
            "api_better": api_better, "needs_verification": needs_verify}


def _kernel(path=None):
    return CapabilityKernel(ledger_path=path, ledger_enabled=True, enforce=False)


class TestClassify(unittest.TestCase):
    def test_01_local_solved(self):
        r = classify_hub_entry(_entry("a", solved=True))
        self.assertTrue(r["solved_locally"])
        self.assertFalse(r["external_used"])

    def test_02_api_better_is_external(self):
        r = classify_hub_entry(_entry("b", solved=True, api_better=True))
        self.assertTrue(r["external_used"])

    def test_03_unsolved_needs_verify_is_external(self):
        r = classify_hub_entry(_entry("c", solved=False, needs_verify=True))
        self.assertTrue(r["external_used"])
        self.assertFalse(r["solved_locally"])

    def test_04_only_explicit_true_counts(self):
        """★不猜：solved=1 / 'true' 都不算显式 True。"""
        e = _entry("d")
        e["local_result"]["solved"] = 1
        self.assertFalse(classify_hub_entry(e)["solved_locally"])
        e["local_result"]["solved"] = "true"
        self.assertFalse(classify_hub_entry(e)["solved_locally"])

    def test_05_pure_function_does_not_mutate(self):
        e = _entry("e")
        before = copy.deepcopy(e)
        classify_hub_entry(e)
        self.assertEqual(before, e)

    def test_06_bad_input_is_safe(self):
        self.assertEqual("unknown", classify_hub_entry(None)["task_type"])
        self.assertEqual("unknown", classify_hub_entry({"local_result": []})["organ"])


class TestObserver(unittest.TestCase):
    def test_07_empty_input_changes_nothing(self):
        """★反证：没有观测时自给率必须保持 0，不能凭空造能力。"""
        o = ReasoningObserver(_kernel())
        s = o.observe([])
        self.assertEqual(0, s["new"])
        self.assertEqual(0, s["total"])

    def test_08_dedup(self):
        o = ReasoningObserver(_kernel())
        e = _entry("same")
        self.assertEqual(1, o.observe([e])["new"])
        self.assertEqual(0, o.observe([e])["new"])
        self.assertEqual(1, o.snapshot()["total"])

    def test_09_metrics_and_by_task(self):
        o = ReasoningObserver(_kernel())
        o.observe([_entry("a"), _entry("b", solved=False, needs_verify=True),
                   _entry("c", task="symbolic_sort")])
        s = o.snapshot()
        self.assertEqual(3, s["total"])
        self.assertEqual(2, s["solved_locally"])
        self.assertEqual(1, s["external"])
        self.assertIn("symbolic_sort", s["by_task_type"])
        self.assertIn(DOMAIN_REASONING, s["kernel"]["by_domain"])
        self.assertAlmostEqual(1 / 3, s["external_dependency_rate"], places=3)

    def test_10_seen_is_bounded(self):
        o = ReasoningObserver(_kernel(), max_seen=16)
        o.observe([_entry(f"e{i}") for i in range(40)])
        self.assertLessEqual(len(o._seen), 16)
        self.assertEqual(40, o.snapshot()["total"])

    def test_11_does_not_mutate_entries(self):
        rows = [_entry("a"), _entry("b", api_better=True)]
        before = copy.deepcopy(rows)
        ReasoningObserver(_kernel()).observe(rows)
        self.assertEqual(before, rows, "旁路不许改 hub 记录")

    def test_12_structured_ledger_not_prompt(self):
        p = os.path.join(_ROOT, "tmp", "m474_reasoning_probe.jsonl")
        try:
            os.remove(p)
        except OSError as _e:
            silent_exc(_e, where="m474.test_12 cleanup", level="debug")
        ReasoningObserver(_kernel(p)).observe([_entry("a"), _entry("b", api_better=True)])
        with open(p, encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh]
        self.assertEqual(2, len(rows))
        for r in rows:
            self.assertEqual(DOMAIN_REASONING, r["domain"])
            self.assertEqual("reasoning_observation", r["kind"])
            self.assertIn("schema", r)
            self.assertIn("at", r)
            self.assertNotIn("prompt", r)
            self.assertNotIn("messages", r)
        os.remove(p)

    def test_13_shares_kernel_with_code_repair(self):
        """★整体利用：一个 kernel 同时承载两个域。"""
        k = _kernel()
        k.note_experience({"kind": "operator_applied", "capability_id": "x"},
                          domain="code_repair")
        ReasoningObserver(k).observe([_entry("a")])
        doms = k.snapshot()["by_domain"]
        self.assertIn("code_repair", doms)
        self.assertIn(DOMAIN_REASONING, doms)


class TestIronLaw304(unittest.TestCase):
    def test_14_no_external_calls_or_inner_world_imports(self):
        with open(_MOD, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = " ".join(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = str(node.module or "")
            else:
                continue
            for bad in ("openai", "zhipu", "bailian", "requests", "httpx",
                        "pulseinnerworld", "verification_learning_hub"):
                self.assertNotIn(bad, names.lower(), f"旁路引入了禁项: {names}")

    def test_15_observer_is_read_only_contract(self):
        """旁路只调 kernel.note_experience，不得调用任何写 hub 的口。"""
        with open(_MOD, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("record", "register_applier", "save", "write"):
                    self.fail(f"旁路出现写操作: {node.func.attr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
