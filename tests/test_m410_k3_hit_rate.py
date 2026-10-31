# -*- coding: utf-8 -*-
"""第410批刀3 判据：hit_rate 日报表 + 失配告警通路。

分组：
  A 状态读写      —— 缺失/损坏/原子写/mtime 缓存
  B 差分采样      —— 首窗全量、无变化跳过、只有 no_coverage、重启回退、小时桶合并、滚动裁剪
  C 连击与告警    —— 第 2 窗告警（只响一次）、样本不足不判、第 5 窗淘汰、恢复清零
  D 报表与落盘    —— 分类/总计、分母不含 no_coverage、文件按日落、关开关不写
  E 快照字段      —— health_snapshot.world_model{hit_rate, mismatches, evicted}
  F 静态纪律      —— 不入队、不新建线程、口径写进报表

隔离：数据根重定向到 tempfile；开关在 tearDown 复原。
"""
import ast
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.InsightBoard import get_insight_board, shutdown_insight_board  # noqa: E402
from nucleus.world_model import hit_rate as hr  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD = os.path.join(_ROOT, "nucleus", "world_model", "hit_rate.py")
_T0 = 1793450000.0          # 固定基准时刻（对齐小时：1793450000 / 3600 = 498180.55…）


class FakeMiner:
    """替身：只提供 hit_rate 需要的两个方法，便于逐窗编排。"""

    def __init__(self):
        self.counters: dict = {}
        self.evicted: list = []

    def get_typed_prediction_stats(self):
        return {"by_event": {k: dict(v) for k, v in self.counters.items()}}

    def evict_pattern(self, event_type, reason=""):
        self.evicted.append((event_type, reason))
        return True

    def bump(self, et, hit=0, miss=0, nc=0):
        _c = self.counters.setdefault(et, {"hit": 0, "miss": 0, "no_coverage": 0})
        _c["hit"] += hit
        _c["miss"] += miss
        _c["no_coverage"] += nc


class _Iso(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m410k3t_")
        hr.set_data_dir(self._tmp)
        self._sw = getattr(config, "ENABLE_M410_HIT_RATE_ALERT", True)
        config.ENABLE_M410_HIT_RATE_ALERT = True
        shutdown_insight_board()

    def tearDown(self):
        config.ENABLE_M410_HIT_RATE_ALERT = self._sw
        hr.set_data_dir(None)
        shutdown_insight_board()


# --------------------------------------------------------------------- A


class TestA_State(_Iso):
    def test_01_missing_state_is_empty(self):
        _st = hr.load_state()
        self.assertEqual({}, _st["last"])
        self.assertEqual(0, _st["windows"])

    def test_02_roundtrip(self):
        _st = hr.load_state()
        _st["windows"] = 7
        _st["last"] = {"x.y": {"hit": 1, "miss": 2, "no_coverage": 3}}
        _p = hr.save_state(_st)
        self.assertTrue(os.path.isfile(_p))
        self.assertEqual(7, hr.load_state()["windows"])
        self.assertEqual(1, hr.load_state()["last"]["x.y"]["hit"])

    def test_03_corrupt_state_yields_empty_not_crash(self):
        os.makedirs(os.path.dirname(hr.state_path()), exist_ok=True)
        with open(hr.state_path(), "w", encoding="utf-8") as _f:
            _f.write("{ not json")
        hr.set_data_dir(self._tmp)          # 清 mtime 缓存
        self.assertEqual(0, hr.load_state()["windows"])

    def test_04_no_tmp_left(self):
        hr.save_state(hr.load_state())
        self.assertFalse(os.path.exists(hr.state_path() + ".tmp410"))

    def test_05_state_file_is_lf(self):
        hr.save_state(hr.load_state())
        _b = open(hr.state_path(), "rb").read()
        self.assertEqual(0, _b.count(b"\r\n"))
        self.assertEqual(0, _b.count(b"\r"))

    def test_06_version_and_criteria(self):
        self.assertEqual("410.3", hr.version())
        _rep = hr.report(now=_T0)
        self.assertEqual(0.5, _rep["criteria"]["alert_below"])
        self.assertEqual(2, _rep["criteria"]["alert_streak"])
        self.assertEqual(0.3, _rep["criteria"]["evict_below"])
        self.assertEqual(5, _rep["criteria"]["evict_streak"])


# --------------------------------------------------------------------- B


class TestB_Sample(_Iso):
    def test_10_first_sample_takes_all_as_delta(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=2, miss=1)
        _o = hr.sample(now=_T0, miner=_m)
        self.assertEqual({"hit": 2, "miss": 1, "no_coverage": 0},
                         {k: _o["window"]["a.b"][k] for k in ("hit", "miss", "no_coverage")})
        self.assertEqual(1, _o["windows"])

    def test_11_second_sample_is_diff(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=2, miss=1)
        hr.sample(now=_T0, miner=_m)
        _m.bump("a.b", hit=1, miss=0)
        _o = hr.sample(now=_T0 + 60, miner=_m)
        self.assertEqual(1, _o["window"]["a.b"]["hit"])     # 不是 3
        self.assertEqual(0, _o["window"]["a.b"]["miss"])

    def test_12_no_change_window_yields_nothing(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=1, miss=0)
        hr.sample(now=_T0, miner=_m)
        _o = hr.sample(now=_T0 + 60, miner=_m)
        self.assertEqual({}, _o["window"])

    def test_13_no_counters_skips(self):
        _o = hr.sample(now=_T0, miner=FakeMiner())
        self.assertEqual("no_counters", _o["skipped"])

    def test_14_counter_reset_detected(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=5, miss=5)
        hr.sample(now=_T0, miner=_m)
        _m.counters = {"a.b": {"hit": 1, "miss": 0, "no_coverage": 0}}
        _o = hr.sample(now=_T0 + 60, miner=_m)
        self.assertEqual(["a.b"], _o.get("reset"))
        self.assertEqual({}, _o["window"])                  # 该窗作废，不记假 miss

    def test_15_hour_bucket_merges(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=1, miss=1)
        hr.sample(now=_T0, miner=_m)
        _m.bump("a.b", hit=1, miss=1)
        hr.sample(now=_T0 + 120, miner=_m)                  # 同一小时内
        _st = hr.load_state()
        self.assertEqual(1, len(_st["hours"]["a.b"]))
        self.assertEqual(2, _st["hours"]["a.b"][0][1])

    def test_16_rolling_drops_old_hours(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=1, miss=0)
        hr.sample(now=_T0, miner=_m)
        _m.bump("a.b", hit=1, miss=0)
        hr.sample(now=_T0 + hr.ROLLING_SEC + 3600, miner=_m)  # 26 小时后
        _st = hr.load_state()
        self.assertEqual(1, len(_st["hours"]["a.b"]))
        self.assertEqual(1, hr.report(now=_T0 + hr.ROLLING_SEC + 3600)["types"]["a.b"]["hit"])

    def test_17_no_coverage_only_window_streak_unchanged(self):
        _m = FakeMiner()
        _m.bump("a.b", nc=40)
        hr.sample(now=_T0, miner=_m)
        _sk = hr.load_state()["streak"]["a.b"]
        self.assertEqual((0, 0), (_sk["low"], _sk["crit"]))


# --------------------------------------------------------------------- C


class TestC_Streak(_Iso):
    def _bad(self, miner, n_windows, hit=1, miss=3):
        _o = None
        for _i in range(n_windows):
            miner.bump("a.b", hit=hit, miss=miss)
            _o = hr.sample(now=_T0 + 60.0 * (_i + 1), miner=miner)
        return _o

    def test_20_alert_at_exactly_two_windows(self):
        _m = FakeMiner()
        self.assertEqual([], self._bad(_m, 1)["alerts"])   # 第 1 窗：不响
        _m.bump("a.b", hit=1, miss=3)
        _o2 = hr.sample(now=_T0 + 180.0, miner=_m)          # 第 2 窗：响一次
        self.assertEqual(1, len(_o2["alerts"]))
        self.assertEqual("a.b", _o2["alerts"][0]["event_type"])

    def test_21_alert_not_repeated_while_streak_grows(self):
        _m = FakeMiner()
        _alerts = 0
        for _i in range(4):
            _m.bump("a.b", hit=1, miss=3)
            _alerts += len(hr.sample(now=_T0 + 60.0 * (_i + 1), miner=_m)["alerts"])
        self.assertEqual(1, _alerts)

    def test_22_good_window_resets_streak(self):
        _m = FakeMiner()
        self._bad(_m, 2)
        _m.bump("a.b", hit=9, miss=1)
        hr.sample(now=_T0 + 200.0, miner=_m)
        _sk = hr.load_state()["streak"]["a.b"]
        self.assertEqual((0, 0), (_sk["low"], _sk["crit"]))

    def test_23_small_window_does_not_judge(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=0, miss=1)          # 只 1 条，不足 MIN_WINDOW_SAMPLES
        hr.sample(now=_T0 + 60.0, miner=_m)
        _sk = hr.load_state()["streak"]["a.b"]
        self.assertEqual(0, _sk["low"])

    def test_24_evict_at_five_windows(self):
        _m = FakeMiner()
        _o = self._bad(_m, 5)
        self.assertEqual([("a.b", "hit_rate<0.30 x5")], _m.evicted)
        self.assertEqual(1, len(_o["evicted"]))
        self.assertTrue(_o["evicted"][0]["ok"])

    def test_25_alert_threshold_is_half(self):
        _m = FakeMiner()
        for _i in range(2):                     # 2/4 = 0.5 ⇒ 不告警（严格小于）
            _m.bump("a.b", hit=2, miss=2)
            _o = hr.sample(now=_T0 + 60.0 * (_i + 1), miner=_m)
        self.assertEqual([], _o["alerts"])
        self.assertEqual(0, hr.load_state()["streak"]["a.b"]["low"])

    def test_26_alert_posted_to_insight_board(self):
        _m = FakeMiner()
        self._bad(_m, 2)
        _ent = get_insight_board().query(insight_type=hr.INSIGHT_TYPE, limit=10)
        self.assertGreaterEqual(len(_ent), 1)
        self.assertIn("命中率", _ent[0]["content"])
        self.assertEqual("world_model", _ent[0]["source_loop"])

    def test_27_alert_recorded_even_if_board_missing(self):
        _m = FakeMiner()
        self._bad(_m, 2)
        _al = hr.load_state()["alerts"]
        self.assertEqual(1, len(_al))
        self.assertIn("event_type", _al[0])
        self.assertIn("at", _al[0])

    def test_28_switch_off_skips_sampling(self):
        config.ENABLE_M410_HIT_RATE_ALERT = False
        _m = FakeMiner()
        _m.bump("a.b", hit=0, miss=9)
        _o = hr.sample(now=_T0, miner=_m)
        self.assertEqual("switch_off", _o["skipped"])
        self.assertFalse(os.path.exists(hr.state_path()))


# --------------------------------------------------------------------- D


class TestD_Report(_Iso):
    def test_30_denominator_excludes_no_coverage(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=3, miss=1, nc=100)
        hr.sample(now=_T0, miner=_m)
        _t = hr.report(now=_T0)["types"]["a.b"]
        self.assertEqual(4, _t["covered"])
        self.assertEqual(0.75, _t["hit_rate"])
        self.assertEqual(100, _t["no_coverage"])

    def test_31_hit_rate_none_when_no_coverage_samples(self):
        _m = FakeMiner()
        _m.bump("a.b", nc=10)
        hr.sample(now=_T0, miner=_m)
        self.assertIsNone(hr.report(now=_T0)["types"]["a.b"]["hit_rate"])

    def test_32_total_aggregates_types(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=3, miss=1)
        _m.bump("c.d", hit=1, miss=1)
        hr.sample(now=_T0, miner=_m)
        _tot = hr.report(now=_T0)["total"]
        self.assertEqual(6, _tot["covered"])
        self.assertEqual(0.6667, _tot["hit_rate"])

    def test_33_report_file_name_by_day(self):
        _p = hr.report_path(_T0)
        self.assertTrue(os.path.basename(_p).startswith("hit_rate_"))
        self.assertTrue(_p.endswith(".json"))
        self.assertEqual(hr.report_path(_T0 + 86400) != _p, True)

    def test_34_write_report_creates_file(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=1, miss=1)
        hr.sample(now=_T0, miner=_m)
        _p = hr.write_report(now=_T0)
        self.assertTrue(os.path.isfile(_p))
        _d = json.load(open(_p, encoding="utf-8"))
        self.assertEqual("410.3", _d["version"])
        self.assertIn("types", _d)

    def test_35_write_report_off(self):
        config.ENABLE_M410_HIT_RATE_ALERT = False
        self.assertIsNone(hr.write_report(now=_T0))

    def test_36_mismatches_listed(self):
        _m = FakeMiner()
        for _i in range(2):
            _m.bump("a.b", hit=1, miss=3)
            hr.sample(now=_T0 + 60.0 * (_i + 1), miner=_m)
        _rep = hr.report(now=_T0 + 200.0)
        self.assertIn("a.b", _rep["mismatches"])
        self.assertEqual([], _rep["evicted"])


# --------------------------------------------------------------------- E


class TestE_Snapshot(_Iso):
    def test_40_fields_present(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=3, miss=1)
        hr.sample(now=_T0, miner=_m)
        _s = hr.snapshot_for_health(now=_T0)
        for _k in ("enabled", "hit_rate", "covered", "no_coverage", "mismatches",
                   "mismatch_types", "evicted", "windows_sampled", "criteria", "source"):
            self.assertIn(_k, _s)
        self.assertEqual(0.75, _s["hit_rate"])

    def test_41_hit_rate_none_not_zero(self):
        _s = hr.snapshot_for_health(now=_T0)
        self.assertIsNone(_s["hit_rate"])
        self.assertEqual(0, _s["covered"])

    def test_42_mismatches_is_count(self):
        _m = FakeMiner()
        for _i in range(2):
            _m.bump("a.b", hit=1, miss=3)
            hr.sample(now=_T0 + 60.0 * (_i + 1), miner=_m)
        _s = hr.snapshot_for_health(now=_T0 + 200.0)
        self.assertEqual(1, _s["mismatches"])
        self.assertEqual(["a.b"], _s["mismatch_types"])

    def test_43_snapshot_switch_off(self):
        config.ENABLE_M410_HIT_RATE_ALERT = False
        self.assertEqual({"enabled": False}, hr.snapshot_for_health(now=_T0))

    def test_44_snapshot_json_serializable(self):
        _m = FakeMiner()
        _m.bump("a.b", hit=1, miss=1)
        hr.sample(now=_T0, miner=_m)
        json.dumps(hr.snapshot_for_health(now=_T0), ensure_ascii=False)


# --------------------------------------------------------------------- F


class TestF_Static(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._src = io.open(_MOD, encoding="utf-8").read()
        cls._tree = ast.parse(cls._src)

    def test_50_never_enqueues(self):
        """刀3 红线：预测失败不自动入队打补丁 —— 连入队 API 都不许出现。"""
        for _bad in ("enqueue", "append_to_queue", "add_task", "motivation",
                     "violated", "self_patch", "auto_patch"):
            self.assertNotIn(_bad, self._src, "命中禁用词 %r" % _bad)

    def test_51_no_new_thread_or_scheduler(self):
        self.assertNotIn("Thread(", self._src)
        self.assertNotIn("Timer(", self._src)
        self.assertNotIn("import sched", self._src)
        self.assertNotIn("time.sleep", self._src)

    def test_52_only_reads_miner_two_methods(self):
        """只调 get_typed_prediction_stats 与 evict_pattern，不改 miner 内部状态。"""
        _calls = {n.func.attr for n in ast.walk(self._tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertIn("get_typed_prediction_stats", _calls)
        # 淘汰走 getattr 取方法（替身不必是真 miner），不直连实例属性
        self.assertIn('getattr(_m, "evict_pattern", None)', self._src)
        for _bad in ("_pending_predictions", "_discovered_patterns", "_m410_stat"):
            self.assertNotIn(_bad, self._src)

    def test_53_imports_silent_exc(self):
        self.assertIn("from nucleus._silent_except import silent_exc", self._src)

    def test_54_writes_only_under_world_model(self):
        self.assertIn(os.path.join("data", "world_model"), hr.STATE_REL)
        self.assertIn(os.path.join("data", "world_model"), hr.REPORT_DIR_REL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
