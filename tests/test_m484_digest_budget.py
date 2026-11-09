# -*- coding: utf-8 -*-
"""484-3 门控：digest.knowledge 每轮批次预算（先红后绿）。

★T0 偏差 D10：7 个 emit 点不在同一个函数里 ⇒ 用统一预算化发射口收口；
超预算入内存队列下轮续发；不 sleep；关掉开关逐字回旧行为。
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from organs.brain.PulseCodeLearner import PulseCodeLearner

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "organs", "brain", "PulseCodeLearner.py")


def _read_text(path):
    """统一用上下文管理器读源码：避免 SIM115 / UP020 净增。"""
    with open(path, encoding="utf-8") as _fh:
        return _fh.read()


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = (hasattr(config, k), getattr(config, k, None))
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, (existed, val) in self._old.items():
            if existed:
                setattr(config, k, val)
            else:
                # __enter__ 必然已 setattr，delattr 不会抛；不写 try/except
                # 以免被 cw2_t2e 静默 except 门禁判为新增静默 handler。
                delattr(config, k)
        return False


def _mk():
    cl = PulseCodeLearner.__new__(PulseCodeLearner)
    out = []
    cl._emit = lambda *a, **kw: out.append((a, kw))
    cl._log = lambda *a, **kw: None
    return cl, out


class TestDigestBudget(unittest.TestCase):
    def test_01_budget_value(self):
        self.assertEqual(getattr(config, "M484_DIGEST_BUDGET_PER_ROUND", None), 50)

    def test_02_api_present(self):
        self.assertTrue(hasattr(PulseCodeLearner, "_m484_emit_knowledge"))
        self.assertTrue(hasattr(PulseCodeLearner, "_m484_begin_round"))

    def test_03_all_seven_emit_sites_routed(self):
        """7 处 emit 全部收口；裸 _emit 只应留在两个 _m484_* 收口方法内。"""
        src = _read_text(_SRC).replace("\r\n", "\n")
        tree = ast.parse(src)
        owner_ranges = []
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("_m484_"):
                owner_ranges.append((fn.lineno, fn.end_lineno or fn.lineno))
        calls = []
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "_emit" and n.args:
                a0 = n.args[0]
                if isinstance(a0, ast.Attribute) and a0.attr == "KNOWLEDGE":
                    calls.append(n.lineno)
        # 3 个收口点：_m484_begin_round 冲刷 1 处 + _m484_emit_knowledge 的
        # 「开关关逐字旧行为」与「正常预算发射」各 1 处。
        self.assertEqual(len(calls), 3, f"裸 digest emit 应只剩 3 个收口点: {calls}")
        for ln in calls:
            self.assertTrue(any(a <= ln <= b for a, b in owner_ranges),
                            f"第 {ln} 行的裸 emit 不在 _m484_* 方法内")
        routed = src.count("self._m484_emit_knowledge(")
        self.assertGreaterEqual(routed, 7, f"收口调用点不足: {routed}")

    def test_04_round_caps_at_50(self):
        cl, out = _mk()
        cl._m484_begin_round()
        for i in range(75):
            cl._m484_emit_knowledge({"i": i}, priority=3, layer="L2")
        self.assertEqual(len(out), 50)
        self.assertEqual(len(getattr(cl, "_m484_pending_knowledge", [])), 25)

    def test_05_pending_flushed_next_round(self):
        cl, out = _mk()
        cl._m484_begin_round()
        for i in range(75):
            cl._m484_emit_knowledge({"i": i}, priority=3, layer="L2")
        out.clear()
        cl._m484_begin_round()
        for i in range(75):
            cl._m484_emit_knowledge({"i": i}, priority=3, layer="L2")
        # 上轮 25 条先冲刷（占 25 预算），本轮前 25 次发完余量，其余入队
        self.assertEqual(len(out), 50)
        self.assertEqual(out[0][0][1]["i"], 50, "冲刷必须先进先出")

    def test_06_switch_off_is_legacy(self):
        with _Switch(ENABLE_M484_DIGEST_BUDGET=False):
            cl, out = _mk()
            cl._m484_begin_round()
            for i in range(75):
                cl._m484_emit_knowledge({"i": i}, priority=3, layer="L2")
            self.assertEqual(len(out), 75)
            self.assertEqual(len(getattr(cl, "_m484_pending_knowledge", [])), 0)

    def test_07_dead_docs_guarded(self):
        src = _read_text(_SRC)
        self.assertIn("# TODO[M484]", src)
        self.assertNotIn('"BLUEPRINT_CONSTITUTION.md"', src)
        self.assertNotIn('"CODE_STYLE.md"', src)

    def test_08_no_sleep_in_budget_helper(self):
        src = _read_text(_SRC).replace("\r\n", "\n")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "_m484_emit_knowledge":
                seg = ast.get_source_segment(src, n) or ""
                self.assertNotIn("sleep(", seg)
                return
        self.fail("_m484_emit_knowledge 未定义")


if __name__ == "__main__":
    unittest.main()
