# -*- coding: utf-8 -*-
"""主线第43批 T4（P1-280 / P1-279）门控测试：补丁历史去重与字段一致性。

覆盖：保留规则 / 去重 / 统计口径 / detail 一致性修复 / 工具与备份产物。
"""
import os
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import unittest

from nucleus.evolution.evolution_stats import stats_from_patch_history
from nucleus.evolution.patch_dedup import (
    TIME_KEYS,
    dedup_history,
    dedup_stats,
    fix_detail_consistency,
    fix_details_in_history,
    pick_keeper,
    record_time,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _p(pid="a", applied=True, applied_at=100.0, saved_at=None,
       baseline=None, after=None, detail=None, eff=None):
    _vr = {}
    if baseline is not None:
        _vr["baseline"] = baseline
    if after is not None:
        _vr["after_fix"] = after
    if eff is not None:
        _vr["effectiveness"] = eff
    if detail is not None:
        _vr["detail"] = detail
    _r = {"id": pid, "file": "x.py", "method": "m", "applied": applied,
          "applied_at": applied_at}
    if saved_at is not None:
        _r["saved_at"] = saved_at
    if _vr:
        _r["runtime_verify_result"] = _vr
    return _r


# ============================================================ 保留规则
class TestPickKeeper(unittest.TestCase):
    def test_01_single_record(self):
        _r = _p("a")
        self.assertIs(pick_keeper([_r]), _r)

    def test_02_prefers_applied_true(self):
        _f = _p("a", applied=False, applied_at=999.0)
        _t = _p("a", applied=True, applied_at=100.0)
        self.assertIs(pick_keeper([_f, _t]), _t)
        self.assertIs(pick_keeper([_t, _f]), _t)

    def test_03_latest_time_when_same_applied(self):
        _old = _p("a", applied=True, applied_at=100.0)
        _new = _p("a", applied=True, applied_at=200.0)
        self.assertIs(pick_keeper([_old, _new]), _new)

    def test_04_empty_returns_blank(self):
        self.assertEqual(pick_keeper([]), {})
        self.assertEqual(pick_keeper([None, "x"]), {})

    def test_05_time_fallback_chain(self):
        _r1 = {"applied_at": 0, "saved_at": 50.0}
        _r2 = {"applied_at": 0, "saved_at": 0, "generated_at": 10.0}
        self.assertEqual(record_time(_r1), 50.0)
        self.assertEqual(record_time(_r2), 10.0)
        self.assertEqual(record_time({}), 0.0)
        self.assertEqual(record_time(None), 0.0)

    def test_06_time_keys_order(self):
        self.assertEqual(TIME_KEYS[0], "applied_at")


# ============================================================ 去重
class TestDedupHistory(unittest.TestCase):
    def test_10_no_duplicates_unchanged(self):
        _h = [_p("a"), _p("b"), _p("c")]
        _k, _r = dedup_history(_h)
        self.assertEqual(len(_k), 3)
        self.assertEqual(_r, [])

    def test_11_dedup_pairs(self):
        _h = [_p("a", applied=False, applied_at=1.0),
              _p("a", applied=True, applied_at=2.0),
              _p("b"), _p("b"),
              _p("c")]
        _k, _r = dedup_history(_h)
        self.assertEqual(len(_k), 3)
        self.assertEqual(len(_r), 2)
        self.assertTrue(next(x for x in _k if x["id"] == "a")["applied"])

    def test_12_order_preserved(self):
        _h = [_p("c"), _p("a"), _p("b"), _p("a")]
        _k, _r = dedup_history(_h)
        self.assertEqual([x["id"] for x in _k], ["c", "a", "b"])

    def test_13_removed_are_the_others(self):
        _f = _p("a", applied=False, applied_at=1.0)
        _t = _p("a", applied=True, applied_at=2.0)
        _k, _r = dedup_history([_f, _t])
        self.assertEqual(len(_r), 1)
        self.assertFalse(_r[0]["applied"])

    def test_14_bad_input(self):
        for bad in (None, "x", 3, {}):
            _k, _r = dedup_history(bad)
            self.assertEqual((_k, _r), ([], []))

    def test_15_dedup_stats(self):
        _h = [_p("a"), _p("a"), _p("b"), _p("b"), _p("c")]
        _s = dedup_stats(_h)
        self.assertEqual(_s["before"], 5)
        self.assertEqual(_s["after"], 3)
        self.assertEqual(_s["removed"], 2)
        self.assertEqual(_s["duplicate_id_count"], 2)
        self.assertEqual(sorted(_s["duplicate_ids"]), ["a", "b"])

    def test_16_non_dict_skipped(self):
        _h = [_p("a"), None, "x", 3, _p("a")]
        _k, _r = dedup_history(_h)
        self.assertEqual(len(_k), 1)


# ============================================================ P1-279
class TestDetailConsistency(unittest.TestCase):
    def test_20_fixes_contradiction(self):
        _r = _p("a", baseline=4, after=4, eff=0.0,
                detail="修复前错误=0, 修复后错误=0, 效果=100%")
        self.assertTrue(fix_detail_consistency(_r))
        self.assertIn("修复前错误=4", _r["runtime_verify_result"]["detail"])
        self.assertIn("修复后错误=4", _r["runtime_verify_result"]["detail"])
        self.assertIn("0%", _r["runtime_verify_result"]["detail"])

    def test_21_already_consistent_noop(self):
        _r = _p("a", baseline=4, after=4, eff=0.0)
        fix_detail_consistency(_r)
        _d = _r["runtime_verify_result"]["detail"]
        self.assertFalse(fix_detail_consistency(_r))
        self.assertEqual(_r["runtime_verify_result"]["detail"], _d)

    def test_22_zero_baseline_is_unjudgeable(self):
        _r = _p("a", baseline=0, after=0, eff=1.0,
                detail="修复前错误=0, 修复后错误=0, 效果=100%")
        fix_detail_consistency(_r)
        _d = _r["runtime_verify_result"]["detail"]
        self.assertIn("不可判定", _d)
        self.assertNotIn("100%", _d)

    def test_23_no_vr_skipped(self):
        self.assertFalse(fix_detail_consistency({"id": "a"}))
        self.assertFalse(fix_detail_consistency(None))
        self.assertFalse(fix_detail_consistency({"runtime_verify_result": "x"}))

    def test_24_fixes_in_history(self):
        _h = [_p("a", baseline=4, after=4, eff=0.0, detail="旧文案"),
              _p("b", baseline=0, after=0, eff=1.0, detail="旧文案")]
        _n, _ids = fix_details_in_history(_h)
        self.assertEqual(_n, 2)
        self.assertEqual(sorted(_ids), ["a", "b"])

    def test_25_bad_input(self):
        self.assertEqual(fix_details_in_history(None), (0, []))


# ============================================================ 统计口径
class TestStatsIntegration(unittest.TestCase):
    def test_30_stats_has_dedup_fields(self):
        _s = stats_from_patch_history([_p("a")])
        self.assertIn("raw_total", _s)
        self.assertIn("duplicates_removed", _s)

    def test_31_stats_dedups_by_id(self):
        _h = [_p("a", applied=True), _p("a", applied=False),
              _p("b", applied=True)]
        _s = stats_from_patch_history(_h)
        self.assertEqual(_s["raw_total"], 3)
        self.assertEqual(_s["duplicates_removed"], 1)
        self.assertEqual(_s["total"], 2)
        self.assertEqual(_s["applied"], 2)

    def test_32_stats_unchanged_for_unique(self):
        _h = [_p("a"), _p("b"), _p("c")]
        _s = stats_from_patch_history(_h)
        self.assertEqual(_s["raw_total"], 3)
        self.assertEqual(_s["duplicates_removed"], 0)
        self.assertEqual(_s["total"], 3)


# ============================================================ 产物
@pytest.mark.production_data
class TestArtifacts(unittest.TestCase):
    def test_40_tool_exists(self):
        self.assertTrue(os.path.isfile(
            os.path.join(_ROOT, "tools", "dedup_patch_history.py")))

    def test_41_production_history_deduped(self):
        """★生产历史应已去重（80 以内且无重复 id）。"""
        _p_ = os.path.join(_ROOT, "data", "patches", "patch_history.json")
        if not os.path.isfile(_p_):
            self.skipTest("生产历史不存在")
        _d = json.load(open(_p_, encoding="utf-8"))
        _ids = [str(x.get("id")) for x in _d if isinstance(x, dict)]
        self.assertEqual(len(_ids), len(set(_ids)), "生产历史仍有重复 id")

    def test_42_backups_exist(self):
        for _rel in ("data/patches/patch_history.json.bak_m43_dedup",
                     "data/patches/patch_history_duplicates.json.bak",
                     "data/patches/dedup_report.json"):
            _p_ = os.path.join(_ROOT, _rel.replace("/", os.sep))
            if os.path.isfile(_p_):
                self.assertGreater(os.path.getsize(_p_), 0, _rel)

    def test_43_report_shape(self):
        _p_ = os.path.join(_ROOT, "data", "patches", "dedup_report.json")
        if not os.path.isfile(_p_):
            self.skipTest("报告不存在")
        _d = json.load(open(_p_, encoding="utf-8"))
        self.assertIn("dedup", _d)
        self.assertIn("before", _d["dedup"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
