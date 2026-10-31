# -*- coding: utf-8 -*-
"""第409批 刀1 判据：假设必带可观测判据 + 观察窗 + 到期时刻。

覆盖六组：
    A 窗口解析（"24h"/"7d"/"7天"/秒数/垃圾输入）
    B 字段契约（build/validate，含 due_at 不可解析时不填假时刻）
    C 新假设注释（缺字段 → 打标 + 不算阻断）
    D 老条目标记（只标记不改写不删除 + 幂等）
    E 灰度开关双向（关 → 一个字段都不写）
    F AST 接线门禁（写入点真的调了它，且判"新条目"在前）
    G 真实盘账（漏网 0；框架改写则跳过 —— 生产数据会被运行期重写）
"""

import ast
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.evolution import hypothesis_criteria as hc  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VLH = os.path.join(_ROOT, "nucleus", "mnemosyne", "verification_learning_hub.py")
_LEDGER = os.path.join(_ROOT, "data", "evolution", "lesson_strategies.json")


class TestA_ParseWindow(unittest.TestCase):
    """窗口解析：单位必须显式，猜默认值会让 due_at 变成无依据的时刻。"""

    def test_01_common_units(self):
        self.assertEqual(86400.0, hc.parse_window("24h"))
        self.assertEqual(604800.0, hc.parse_window("7d"))
        self.assertEqual(604800.0, hc.parse_window("7天"))
        self.assertEqual(3600.0, hc.parse_window("1h"))
        self.assertEqual(120.0, hc.parse_window("2分钟"))

    def test_02_numeric_passthrough(self):
        self.assertEqual(3600.0, hc.parse_window(3600))
        self.assertEqual(1.5, hc.parse_window(1.5))

    def test_03_garbage_returns_none_not_guess(self):
        """★垃圾输入必须返回 None —— 返回"默认 24h"会静默造出假 due_at。"""
        for _bad in (None, "", "abc", "h", "24x", 0, -5, True, {}, []):
            self.assertIsNone(hc.parse_window(_bad), f"{_bad!r} 不该被解析")

    def test_04_whitespace_tolerated(self):
        self.assertEqual(86400.0, hc.parse_window("  24 h "))


class TestB_BuildValidate(unittest.TestCase):
    def test_10_build_sets_due_at(self):
        _c = hc.build_criteria({"kind": "recurrence", "signature": "a.py::t::m"},
                               "24h", now=1000.0)
        self.assertEqual(87400.0, _c["due_at"])
        self.assertEqual("24h", _c["observation_window"])

    def test_11_build_without_window_omits_due(self):
        """★窗口解析不了就**不写** due_at，而不是写 now。"""
        _c = hc.build_criteria({"kind": "recurrence"}, "??", now=1000.0)
        self.assertNotIn("due_at", _c)

    def test_12_validate_ok(self):
        _e = {"observable_criterion": {"kind": "effect", "patch_id": "p1"},
              "observation_window": "7d", "due_at": 1000.0}
        _r = hc.validate(_e, now=900.0)
        self.assertTrue(_r["ok"], _r)
        self.assertEqual([], _r["missing"])

    def test_13_validate_reports_missing(self):
        _r = hc.validate({"observable_criterion": ""}, now=1000.0)
        self.assertFalse(_r["ok"])
        self.assertIn(hc.F_CRITERION, _r["missing"])
        self.assertIn(hc.F_WINDOW, _r["missing"])

    def test_14_validate_warns_on_bad_due_type(self):
        _e = {"observable_criterion": {"kind": "effect"},
              "observation_window": "7d", "due_at": "明天"}
        _r = hc.validate(_e, now=1000.0)
        self.assertTrue(any("due_at 不是数值" in w for w in _r["warnings"]), _r)

    def test_15_validate_warns_on_unknown_kind(self):
        _e = {"observable_criterion": {"kind": "乱写的"},
              "observation_window": "7d", "due_at": 1000.0}
        _r = hc.validate(_e, now=900.0)
        self.assertTrue(any("kind 不在册" in w for w in _r["warnings"]), _r)

    def test_16_normalize_criterion(self):
        self.assertIsNotNone(hc.normalize_criterion({"kind": "recurrence"}))
        self.assertIsNone(hc.normalize_criterion({"kind": "没这个"}))
        self.assertIsNone(hc.normalize_criterion("一段人读描述"))


class TestC_AnnotateNew(unittest.TestCase):
    def test_20_new_with_window_fills_due(self):
        """新假设三字段齐、只缺 due_at → 补上，且**不该**再打 missing 标记。"""
        _e = {"observable_criterion": {"kind": "effect", "patch_id": "p"},
              "observation_window": "7d"}
        _r = hc.annotate_new_entry(_e, now=1000.0)
        self.assertEqual([hc.F_DUE], _r["applied"], _r)
        self.assertAlmostEqual(1000.0 + 604800.0, _e[hc.F_DUE], places=3)
        self.assertEqual([], _r["missing"], _r)
        self.assertTrue(_r["ok"], _r)
        self.assertNotIn(hc.F_MISSING, _e, "三字段齐了就不该打 missing 标记")

    def test_21_new_missing_window_marks_and_does_not_block(self):
        """★缺字段只打标 + 返 warnings，**绝不抛**（任务书：不阻断、灰度）。"""
        _e = {"strategy": "一段文字"}
        _r = hc.annotate_new_entry(_e, now=1000.0)
        self.assertFalse(_r["ok"])
        self.assertTrue(_e.get(hc.F_MISSING), _e)
        self.assertIn(hc.F_MISSING, _r["applied"])
        self.assertTrue(_r["missing"], _r)

    def test_22_complete_entry_untouched(self):
        _e = {"observable_criterion": {"kind": "reuse", "signature": "s"},
              "observation_window": "7d", "due_at": 5000.0}
        _before = dict(_e)
        _r = hc.annotate_new_entry(_e, now=1000.0)
        self.assertTrue(_r["ok"])
        self.assertEqual([], _r["applied"])
        self.assertEqual(_before, _e)


class TestD_MarkLegacy(unittest.TestCase):
    def test_30_marks_only_incomplete(self):
        _s = {
            "a": {"strategy": "老的"},
            "b": {"observable_criterion": {"kind": "effect"},
                  "observation_window": "7d", "due_at": 1.0},
        }
        _r = hc.mark_legacy_entries(_s)
        self.assertEqual(1, _r["marked"])
        self.assertEqual(1, _r["has_criteria"])
        self.assertTrue(_s["a"][hc.F_MISSING])
        self.assertNotIn(hc.F_MISSING, _s["b"])

    def test_31_idempotent(self):
        _s = {"a": {"strategy": "老的"}}
        hc.mark_legacy_entries(_s)
        _r2 = hc.mark_legacy_entries(_s)
        self.assertEqual(0, _r2["marked"])
        self.assertEqual(1, _r2["already"])

    def test_32_never_deletes_or_rewrites(self):
        """★只标记：原有字段一个都不许动（老账不能被程序改写）。"""
        _orig = {"rule": "x::y", "strategy": "文本", "applied_total": 7,
                 "hit_count": 3, "first_seen": 1.0, "last_seen": 2.0}
        _s = {"k": dict(_orig)}
        hc.mark_legacy_entries(_s)
        for _k, _v in _orig.items():
            self.assertEqual(_v, _s["k"][_k], _k)
        self.assertEqual(len(_orig) + 1, len(_s["k"]))

    def test_33_summarize_counts_gap(self):
        """★`no_criteria_and_no_flag` 是"漏网"的唯一可观测证据，必须存在且准确。"""
        _s = {"a": {"strategy": "b" * 5}, "b": {"strategy": "c"},
              "c": {"observable_criterion": {"kind": "effect"},
                    "observation_window": "7d", "due_at": 1.0}}
        _s0 = hc.summarize(_s)
        self.assertEqual(2, _s0["no_criteria_and_no_flag"])
        hc.mark_legacy_entries(_s)
        _s1 = hc.summarize(_s)
        self.assertEqual(0, _s1["no_criteria_and_no_flag"])
        self.assertEqual(1, _s1["has_criteria"])
        self.assertEqual(2, _s1["missing_criterion"])


class TestE_Switch(unittest.TestCase):
    """灰度双向：关掉就一个字段都不写（零行为变化）。"""

    def setUp(self):
        import config
        self._cfg = config
        self._had = hasattr(config, "ENABLE_M409_HYPOTHESIS_CRITERIA")
        self._old = getattr(config, "ENABLE_M409_HYPOTHESIS_CRITERIA", True)

    def tearDown(self):
        if self._had:
            self._cfg.ENABLE_M409_HYPOTHESIS_CRITERIA = self._old
        elif hasattr(self._cfg, "ENABLE_M409_HYPOTHESIS_CRITERIA"):
            delattr(self._cfg, "ENABLE_M409_HYPOTHESIS_CRITERIA")

    def test_40_off_writes_nothing(self):
        self._cfg.ENABLE_M409_HYPOTHESIS_CRITERIA = False
        _e = {"strategy": "x", "observation_window": "7d"}
        _before = dict(_e)
        _r = hc.annotate_new_entry(_e, now=1000.0)
        self.assertEqual("switch_off", _r["skipped"])
        self.assertEqual(_before, _e)

    def test_41_off_marks_nothing(self):
        self._cfg.ENABLE_M409_HYPOTHESIS_CRITERIA = False
        _s = {"a": {"strategy": "老的"}}
        _r = hc.mark_legacy_entries(_s)
        self.assertEqual(0, _r["marked"])
        self.assertNotIn(hc.F_MISSING, _s["a"])

    def test_42_on_by_default(self):
        self._cfg.ENABLE_M409_HYPOTHESIS_CRITERIA = True
        self.assertTrue(hc.enabled())


class TestF_AstWiring(unittest.TestCase):
    """★接线门禁：判据层建好了 ≠ 装上车（第24批 append_dialog_turn 的教训）。"""

    @classmethod
    def setUpClass(cls):
        cls._src = io.open(_VLH, "rb").read().decode("utf-8")
        cls._tree = ast.parse(cls._src)

    def _func(self, name):
        for _n in ast.walk(self._tree):
            if isinstance(_n, ast.FunctionDef) and _n.name == name:
                return _n
        return None

    def test_50_helper_defined_once(self):
        _defs = [n for n in ast.walk(self._tree)
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "_m409_annotate_entry"]
        self.assertEqual(1, len(_defs), "helper 定义数不是 1")

    def test_51_called_in_persist_batch(self):
        _fn = self._func("_m335_persist_batch")
        self.assertIsNotNone(_fn, "找不到 _m335_persist_batch")
        _calls = [n for n in ast.walk(_fn)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "_m409_annotate_entry"]
        self.assertEqual(1, len(_calls), "写入点没有（或多于一次）调用判据层")

    def test_52_is_new_computed_before_writeback(self):
        """★`_m409_is_new` 必须在 `_bk[_full] = _e` **之前**算，否则恒为 False。"""
        _fn = self._func("_m335_persist_batch")
        _src = ast.unparse(_fn)
        _i_calc = _src.find("_m409_is_new = ")
        _i_write = _src.find("_bk[_full] = _e")
        self.assertGreater(_i_calc, -1, "没算 _m409_is_new")
        self.assertGreater(_i_write, -1, "没写回 _bk")
        self.assertLess(_i_calc, _i_write, "_m409_is_new 在写回之后才算 = 恒为 False")

    def test_53_criteria_version_written(self):
        self.assertIn("criteria_version", self._src)


class TestG_RealLedger(unittest.TestCase):
    """真实盘账：漏网必须为 0。

    ★生产数据会被运行期整体重写（第52批 P2-370 已实证），所以这里断言
      **不变量**（漏网 0）而不是"恰好 30 条标了 missing"—— 后者一有新假设就红。
    """

    def setUp(self):
        if not os.path.isfile(_LEDGER):
            self.skipTest("盘账不存在")
        try:
            self._d = json.loads(io.open(_LEDGER, "rb").read().decode("utf-8"))
        except (OSError, ValueError) as _e:
            self.skipTest(f"盘账不可读: {_e}")

    def test_60_no_entry_slips_through(self):
        _s = self._d.get("strategies") or {}
        _sum = hc.summarize(_s)
        self.assertGreater(_sum["total"], 0, "盘账是空的")
        self.assertEqual(0, _sum["no_criteria_and_no_flag"],
                         f"有 {_sum['no_criteria_and_no_flag']} 条既无判据也无标记")

    def test_61_legacy_flagged_is_whole_ledger(self):
        """当前全部条目都是老账 ⇒ 标记数应等于总数（新假设进来后此断言自然失效为 >=0）。"""
        _s = self._d.get("strategies") or {}
        _sum = hc.summarize(_s)
        self.assertEqual(_sum["total"],
                         _sum["missing_criterion"] + _sum["has_criteria"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
