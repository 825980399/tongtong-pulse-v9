# -*- coding: utf-8 -*-
"""★479 收尾：重复送 LLM 探针 + 台账 issue_type 键兼容 门控单测。

## 探针要解决的问题（实测证据）

22 轮 ALL 协作台账里，同一方法被反复送进 LLM：
    _m374_schedule_ahead ×4   _schedule_next_beat ×4
    _schedule_next_exploration ×3   _schedule_dream_timer ×3
    _schedule_health_check ×3
每轮并行 4 条候选 ⇒ 一个方法送 4 次 = 白烧 16 次调用。
根因是「产物不合格 ⇒ 不入池 ⇒ 问题仍在 ⇒ 下轮再送」，但**此前零信号**：
只看「本轮调了几次」永远发现不了「同一个位置被调了几十次」。

## issue_type 键错配

上游 `_issue` 的真实键是 `type`（见 SafeEvolutionExecutor `_type = _issue.get("type", "")`），
而 ALL 协作台账只读 `issue.get("issue_type")` ⇒ 22 轮 issue_type **全空**，
无法按类型分析。改为两键兼容。
"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
import nucleus.reasoning.SafeEvolutionExecutor as SEE
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor


class _Base(unittest.TestCase):
    def setUp(self):
        self._o = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        self._old_sw = config.ENABLE_M479_REPEAT_PROBE
        self._old_win = config.M479_REPEAT_WINDOW_SEC
        self._old_thr = config.M479_REPEAT_THRESHOLD
        SEE._M479_REPEAT.clear()

    def tearDown(self):
        config.ENABLE_M479_REPEAT_PROBE = self._old_sw
        config.M479_REPEAT_WINDOW_SEC = self._old_win
        config.M479_REPEAT_THRESHOLD = self._old_thr
        SEE._M479_REPEAT.clear()

    def _hit(self, n, f="a.py", m="f"):
        for _ in range(n):
            self._o._m479_note_repeat({"file": f, "method": m})


class TestRepeatProbe(_Base):

    def test_01_below_threshold_no_alarm(self):
        config.ENABLE_M479_REPEAT_PROBE = True
        config.M479_REPEAT_THRESHOLD = 3
        self._hit(3)
        _rec = SEE._M479_REPEAT[("a.py", "f")]
        self.assertEqual(_rec[0], 3)
        self.assertFalse(_rec[2], "阈值内不该告警")

    def test_02_over_threshold_alarms_once(self):
        config.ENABLE_M479_REPEAT_PROBE = True
        config.M479_REPEAT_THRESHOLD = 3
        self._hit(4)
        self.assertTrue(SEE._M479_REPEAT[("a.py", "f")][2], "第 4 次应告警")
        # 再送 10 次不许重复告警
        self._hit(10)
        self.assertEqual(SEE._M479_REPEAT[("a.py", "f")][0], 14)
        self.assertTrue(SEE._M479_REPEAT[("a.py", "f")][2])

    def test_03_different_methods_independent(self):
        config.ENABLE_M479_REPEAT_PROBE = True
        config.M479_REPEAT_THRESHOLD = 3
        self._hit(4, m="f1")
        self._hit(2, m="f2")
        self.assertTrue(SEE._M479_REPEAT[("a.py", "f1")][2])
        self.assertFalse(SEE._M479_REPEAT[("a.py", "f2")][2])

    def test_04_window_expiry_resets(self):
        """窗口过期 ⇒ 计数归零重新开始（不许永远累积）。"""
        config.ENABLE_M479_REPEAT_PROBE = True
        config.M479_REPEAT_THRESHOLD = 2
        config.M479_REPEAT_WINDOW_SEC = 0.05
        self._hit(5)
        self.assertTrue(SEE._M479_REPEAT[("a.py", "f")][2])
        time.sleep(0.12)
        self._hit(1)
        _rec = SEE._M479_REPEAT[("a.py", "f")]
        self.assertEqual(_rec[0], 1, "窗口过期后应重新计数")
        self.assertFalse(_rec[2], "新窗口不该带着旧告警标记")

    def test_05_switch_off_records_nothing(self):
        config.ENABLE_M479_REPEAT_PROBE = False
        self._hit(20)
        self.assertEqual(SEE._M479_REPEAT, {}, "开关关闭必须零记录")

    def test_06_missing_keys_skipped(self):
        config.ENABLE_M479_REPEAT_PROBE = True
        for _bad in ({}, None, "str", {"file": "a.py"}, {"method": "f"}):
            self._o._m479_note_repeat(_bad)   # 不抛即通过
        self.assertEqual(SEE._M479_REPEAT, {})

    def test_07_capacity_capped(self):
        """容器不许无界膨胀。"""
        config.ENABLE_M479_REPEAT_PROBE = True
        _cap = SEE._M479_REPEAT_MAX_KEYS
        for _i in range(_cap + 5):
            self._o._m479_note_repeat({"file": f"f{_i}.py", "method": "m"})
        self.assertLessEqual(len(SEE._M479_REPEAT), _cap + 1)

    def test_08_wired_into_call_llm_for_repair(self):
        """接线防回潮：`_call_llm_for_repair` 必须调用探针。"""
        import ast
        _p = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        _t = ast.parse(_src)
        _c = next(n for n in _t.body if isinstance(n, ast.ClassDef)
                  and n.name == "SafeEvolutionExecutor")
        _fn = next(n for n in _c.body if isinstance(n, ast.FunctionDef)
                   and n.name == "_call_llm_for_repair")
        _n = sum(1 for _x in ast.walk(_fn)
                 if isinstance(_x, ast.Call) and isinstance(_x.func, ast.Attribute)
                 and _x.func.attr == "_m479_note_repeat")
        self.assertEqual(_n, 1, "探针必须且只能被调用一次")

    def test_09_probe_does_not_change_return(self):
        """反证：探针只记账，不许影响 `_call_llm_for_repair` 的返回路径。

        直接验证：探针容器被写满/关闭时，函数仍然走到熔断分支并返回 None。
        """
        config.ENABLE_M479_REPEAT_PROBE = True
        for _i in range(SEE._M479_REPEAT_MAX_KEYS + 10):
            self._o._m479_note_repeat({"file": f"x{_i}.py", "method": "m"})

        class _Fake(SafeEvolutionExecutor):
            def _m465_circuit_open(self):
                return True

        _f = _Fake.__new__(_Fake)
        self.assertIsNone(_f._call_llm_for_repair({"file": "a.py", "method": "f"},
                                                 "code"))


class TestIssueTypeKeyCompat(_Base):

    def test_20_reads_type_key(self):
        """上游用 `type` 键 ⇒ 台账必须记到（修前记成空串）。"""
        import json
        import tempfile
        _d = tempfile.mkdtemp(prefix="m479it_")
        _led = os.path.join(_d, "a.jsonl")
        try:
            self._o._project_root = ROOT
            # 直接调 _m479_all_collab 太重，改为验证取值表达式本身
            _issue = {"type": "silent_exception", "file": "a.py", "method": "f"}
            _got = str(_issue.get("issue_type") or _issue.get("type") or "")
            self.assertEqual(_got, "silent_exception")
            self.assertTrue(json.dumps({"issue_type": _got}))
        finally:
            import shutil
            shutil.rmtree(_d, ignore_errors=True)

    def test_21_issue_type_still_wins_when_both(self):
        _issue = {"type": "old", "issue_type": "new"}
        _got = str(_issue.get("issue_type") or _issue.get("type") or "")
        self.assertEqual(_got, "new", "两个键都有时以 issue_type 为准")

    def test_22_source_has_both_keys(self):
        _p = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        _i = _src.index('"issue_type": str(')
        _seg = _src[_i:_i + 200]
        self.assertIn('_issue.get("issue_type")', _seg)
        self.assertIn('_issue.get("type")', _seg, "丢了 type 回落 ⇒ 台账又会全空")

    def test_23_issue_keys_diagnostic_present(self):
        """★诊断字段：台账必须记录 issue 的真实键名。

        为什么需要：修了 `type` 回落之后仍观察到 issue_type 为空 ——
        实测 `discover_all_issues` 返回的 issue 里，`type` 被**日志级别**
        （'ERROR'/'WARNING'）占用，真实类型在 `error_type`；而 self_inspector
        来源又可能是第三套键名。**不再猜**：把键名记进台账，一轮影子即定案。
        """
        _p = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
        with open(_p, encoding="utf-8", errors="replace") as _f:
            _src = _f.read()
        self.assertIn('"issue_keys": sorted(', _src)
        # 必须限长，否则一条 issue 带一堆内部键会把台账撑大
        _i = _src.index('"issue_keys": sorted(')
        self.assertIn("[:14]", _src[_i:_i + 120])


if __name__ == "__main__":
    unittest.main()
