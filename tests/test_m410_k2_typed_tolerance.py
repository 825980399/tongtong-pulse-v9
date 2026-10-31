# -*- coding: utf-8 -*-
"""第410批刀2 判据：分类型预测（时刻容差查表 + 分钟窗条数/批数）。

分组：
  A WindowTracker      —— 对齐切窗、空窗、跨窗续批、长空档跳跃
  B 区间/判定           —— μ±2σ、速率外推的下限、点判、三态
  C 查表               —— 七类各自 (预测型, 容差)；非七类与关开关回落 60s
  D 接线行为            —— 心跳偏 0.3s 中 / 偏 2.0s 不中（旧 60s 必中）、
                          眼睛不再恒命中、burst 点判、过期判 miss
  E 静态              —— 签名默认值、无新线程、consume 有调用、无裸事件名字面量

隔离：所有落盘都重定向到 tempfile 目录；开关在 tearDown 复原。
"""
import ast
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from nucleus.genesis.StreamMiner import StreamMiner  # noqa: E402
from nucleus.world_model import periodic_profile as pp  # noqa: E402
from nucleus.world_model import typed_prediction as tp  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD_SM = os.path.join(_ROOT, "nucleus", "genesis", "StreamMiner.py")
_MOD_TP = os.path.join(_ROOT, "nucleus", "world_model", "typed_prediction.py")

#: 合成的七类周期（秒）—— 与生产实测同量级，便于容差断言不钉死绝对值
_INTERVALS = {
    pp.EV_TOUCH_HARDWARE_SNAPSHOT: 30.0,
    pp.EV_ENERGY_METABOLISM_SNAPSHOT: 30.0,
    pp.EV_SPINAL_INSPECTION_REPORT: 40.0,
    pp.EV_HEART_ALIVE: 13.5,
    pp.EV_HEART_BEAT: 13.5,
    pp.EV_EYES_STREAM_FRAME: 2.0,
    pp.EV_DIGEST_KNOWLEDGE: 10.0,
}


def _seq(start: float, step: float, n: int) -> list:
    return [start + step * i for i in range(n)]


def _align(ts: float, w: float = 60.0) -> float:
    return float(int(ts // w) * w)


class _Iso(unittest.TestCase):
    """统一隔离：数据根指向临时目录 + 复原开关。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m410k2_")
        pp.set_data_dir(self._tmp)
        self._sw = getattr(config, "ENABLE_M410_TYPED_TOLERANCE", True)
        config.ENABLE_M410_TYPED_TOLERANCE = True

    def tearDown(self):
        config.ENABLE_M410_TYPED_TOLERANCE = self._sw
        pp.set_data_dir(None)

    def _profile(self, now=None) -> dict:
        """按 _INTERVALS 合成本地档案（走刀1 的 build，含落盘）。"""
        _t = float(now if now is not None else time.time())
        _pat = {"periodicity": {k: _seq(_t - v * 50, v, 50) for k, v in _INTERVALS.items()}}
        return pp.build(_pat, now=_t)

    def _miner(self) -> StreamMiner:
        _m = StreamMiner()
        _m.enable()
        _m.set_storage_path(os.path.join(self._tmp, "patterns.json"))
        return _m


# --------------------------------------------------------------------- A


class TestA_WindowTracker(unittest.TestCase):
    """A：窗口对齐 / 空窗 / 批切。"""

    def test_01_first_feed_aligns_to_boundary(self):
        _tr = tp.WindowTracker()
        _tr.feed(1000.0)
        self.assertEqual(960.0, _tr.current()["window_start"])
        self.assertEqual(1, _tr.current()["count"])

    def test_02_accumulate_within_window(self):
        _tr = tp.WindowTracker()
        for _i in range(4):
            _tr.feed(960.0 + _i * 10.0)
        self.assertEqual(4, _tr.current()["count"])
        self.assertEqual("960/1020", "%d/%d" % (_tr.current()["window_start"],
                                               _tr.current()["window_end"]))

    def test_03_boundary_cross_closes_one_window(self):
        _tr = tp.WindowTracker()
        _tr.feed(960.0)
        _tr.feed(970.0)
        _closed = _tr.feed(1025.0)          # 跨到下一窗
        self.assertEqual(1, len(_closed))
        self.assertEqual(2, _closed[0]["count"])
        self.assertEqual(960.0, _closed[0]["window_start"])   # 关掉的是「有事件那一窗」

    def test_04_empty_windows_are_reported(self):
        _tr = tp.WindowTracker()
        _tr.feed(960.0)
        _closed = _tr.feed(1140.0)          # 960 有事件，1020/1080 是空窗
        self.assertEqual([960.0, 1020.0, 1080.0], [c["window_start"] for c in _closed])
        self.assertEqual([1, 0, 0], [c["count"] for c in _closed])

    def test_05_close_until_without_events(self):
        _tr = tp.WindowTracker()
        _tr.feed(960.0)
        self.assertEqual([], _tr.close_until(1019.0))
        _closed = _tr.close_until(1020.0)
        self.assertEqual(1, len(_closed))
        self.assertEqual(1, _closed[0]["count"])

    def test_06_batch_gap_splits(self):
        # 窗放大到 600s，让两次批首落在同一窗口里
        _tr = tp.WindowTracker(window_sec=600.0, batch_gap_sec=60.0)
        _tr.feed(600.0)
        _tr.feed(602.0)                     # 同批
        _tr.feed(700.0)                     # gap 98s > 60 ⇒ 新批
        self.assertEqual(2, _tr.current()["batches"])
        self.assertEqual(3, _tr.current()["count"])

    def test_07_batch_continues_across_window(self):
        """批首记在「起批那一窗」；跨窗延续的老批不在新窗里重复计数。"""
        _tr = tp.WindowTracker()
        _tr.feed(950.0)                     # 落在窗 900，起批
        _closed = _tr.feed(1000.0)          # 跨窗，gap 50s < 60 ⇒ 不算新批
        self.assertEqual(900.0, _closed[0]["window_start"])
        self.assertEqual(1, _closed[0]["batches"])      # 批首确实记在窗 900
        self.assertEqual(0, _tr.current()["batches"])   # 新窗里没有批首
        self.assertEqual(1, _tr.current()["count"])

    def test_08_max_close_jumps_instead_of_spinning(self):
        _tr = tp.WindowTracker(max_close=3)
        _tr.feed(960.0)
        _now = 960.0 + 60.0 * 10
        _closed = _tr.feed(_now)            # 要关 10 个窗，上限 3
        self.assertEqual(3, len(_closed))
        self.assertEqual([960.0, 1020.0, 1080.0], [c["window_start"] for c in _closed])
        # 跳到当前对齐窗继续，不留下半开状态
        self.assertEqual(tp.window_of(_now), _tr.current()["window_start"])
        self.assertEqual(1, _tr.current()["count"])

    def test_09_empty_tracker_current(self):
        _tr = tp.WindowTracker()
        self.assertIsNone(_tr.current()["window_start"])
        self.assertEqual([], _tr.close_until(999.0))
        self.assertIsNone(_tr.last_ts())


# --------------------------------------------------------------------- B


class TestB_Range(unittest.TestCase):
    """B：区间与三态判定。"""

    def test_10_expected_range_needs_history(self):
        self.assertEqual((None, None, None), tp.expected_range([5, 6]))
        self.assertEqual((None, None, None), tp.expected_range([]))

    def test_11_expected_range_mu_and_sigma(self):
        _mu, _lo, _hi = tp.expected_range([10, 10, 10, 10])
        self.assertEqual(10.0, _mu)
        self.assertEqual((10, 10), (_lo, _hi))     # σ=0 ⇒ 区间收敛成一点

    def test_12_expected_range_lo_not_negative(self):
        _mu, _lo, _hi = tp.expected_range([0, 0, 20, 0])
        self.assertGreaterEqual(_lo, 0)
        self.assertGreaterEqual(_hi, _lo)

    def test_13_cold_start_without_median(self):
        self.assertEqual((None, None, None), tp.cold_start_range(None, 3.0))
        self.assertEqual((None, None, None), tp.cold_start_range(0.0, 3.0))

    def test_14_cold_start_from_rate(self):
        _mu, _lo, _hi = tp.cold_start_range(2.0, 3.0)   # 30 条/分钟
        self.assertAlmostEqual(30.0, _mu, places=1)
        self.assertLess(_lo, 30)
        self.assertGreater(_hi, 30)

    def test_15_rate_range_rejects_short_span(self):
        # 3 条跨 4 秒不许外推成「每分钟 15 条」
        self.assertEqual((None, None, None), tp.rate_range([1000.0, 1002.0, 1004.0]))

    def test_16_rate_range_count_vs_batch(self):
        _stamps = [0.0, 2.0, 4.0, 200.0, 202.0, 204.0]
        _mu_c, _, _ = tp.rate_range(_stamps, batch=False)
        _mu_b, _, _ = tp.rate_range(_stamps, batch=True)
        self.assertAlmostEqual(6 * 60.0 / 204.0, _mu_c, places=1)
        self.assertAlmostEqual(2 * 60.0 / 204.0, _mu_b, places=1)

    def test_17_judge_three_states(self):
        self.assertEqual(tp.VERDICT_HIT, tp.judge(30, 19, 43))
        self.assertEqual(tp.VERDICT_MISS, tp.judge(6, 19, 43))
        self.assertEqual(tp.VERDICT_NO_COVERAGE, tp.judge(None, 19, 43))
        self.assertEqual(tp.VERDICT_NO_COVERAGE, tp.judge(30, None, None))

    def test_18_judge_point(self):
        self.assertEqual(tp.VERDICT_HIT, tp.judge_point(1, 0.78))
        self.assertEqual(tp.VERDICT_MISS, tp.judge_point(2, 0.78))
        self.assertEqual(tp.VERDICT_NO_COVERAGE, tp.judge_point(1, None))
        self.assertEqual(tp.VERDICT_NO_COVERAGE, tp.judge_point(None, 0.78))

    def test_19_point_of_rounds(self):
        self.assertEqual(0, tp.point_of(0.33))
        self.assertEqual(1, tp.point_of(0.78))
        self.assertIsNone(tp.point_of(None))

    def test_20_next_window_start_is_future(self):
        _now = 1000.0
        self.assertEqual(960.0, tp.window_of(_now))
        self.assertEqual(1020.0, tp.next_window_start(_now))
        self.assertGreater(tp.next_window_start(_now), _now)   # 只能预测未开始的窗
        self.assertEqual(960.0, tp.window_of(1001.0))
        self.assertEqual(1020.0, tp.next_window_start(1001.0))

    def test_21_batch_count_offline(self):
        self.assertEqual(1, tp.batch_count([0.0, 2.0, 4.0]))
        self.assertEqual(2, tp.batch_count([0.0, 2.0, 200.0]))
        self.assertEqual(0, tp.batch_count([]))

    def test_22_burst_window_holds_at_most_one_batch(self):
        """60s 窗 + 60s 切批门槛 ⇒ 批数 ∈ {0,1}（点判而非区间判的依据）。"""
        _tr = tp.WindowTracker()
        _stamps = [_tr.feed(t) for t in (960.0, 1000.0, 1010.0, 1019.0)]
        self.assertTrue(all(s == [] for s in _stamps))
        self.assertEqual(1, _tr.current()["batches"])   # 4 条 60s 内最多起 1 批


# --------------------------------------------------------------------- C


class TestC_Table(_Iso):
    """C：分类型容差查表。"""

    def test_30_seven_types_all_typed(self):
        self._profile()
        _m = self._miner()
        for _et in pp.TARGET_EVENTS:
            _pt, _tol, _why = _m._m410_plan(_et)
            self.assertEqual("typed", _why, _et)
            self.assertEqual(pp.PREDICT_TYPE[_et], _pt, _et)

    def test_31_periodic_types_have_tolerance(self):
        self._profile()
        _m = self._miner()
        for _et in (pp.EV_HEART_BEAT, pp.EV_HEART_ALIVE, pp.EV_TOUCH_HARDWARE_SNAPSHOT,
                    pp.EV_ENERGY_METABOLISM_SNAPSHOT, pp.EV_SPINAL_INSPECTION_REPORT):
            _pt, _tol, _ = _m._m410_plan(_et)
            self.assertEqual("periodic", _pt)
            self.assertIsNotNone(_tol, _et)
            self.assertGreater(_tol, 0.0)

    def test_32_count_and_burst_have_no_time_tolerance(self):
        self._profile()
        _m = self._miner()
        for _et in (pp.EV_EYES_STREAM_FRAME, pp.EV_DIGEST_KNOWLEDGE):
            _pt, _tol, _ = _m._m410_plan(_et)
            self.assertIn(_pt, ("count", "burst"))
            self.assertIsNone(_tol, _et)

    def test_33_tolerance_is_median_times_pct(self):
        self._profile()
        _m = self._miner()
        _prof = pp.load_profile()
        for _et, _pct in ((pp.EV_HEART_BEAT, 0.05), (pp.EV_SPINAL_INSPECTION_REPORT, 0.10)):
            _med = _prof["events"][_et]["median_interval_sec"]
            _, _tol, _ = _m._m410_plan(_et)
            self.assertAlmostEqual(_med * _pct, _tol, places=2)

    def test_34_non_target_keeps_legacy_60(self):
        self._profile()
        _m = self._miner()
        _pt, _tol, _why = _m._m410_plan("time.tick")
        self.assertEqual(("periodic", 60.0, "non_target"), (_pt, _tol, _why))

    def test_35_switch_off_all_legacy(self):
        self._profile()
        config.ENABLE_M410_TYPED_TOLERANCE = False
        _m = self._miner()
        for _et in pp.TARGET_EVENTS:
            self.assertEqual(("periodic", 60.0, "legacy"), _m._m410_plan(_et), _et)

    def test_36_missing_profile_yields_none_not_60(self):
        # 不建档案 ⇒ 周期型判不了容差：必须是 None（判不了），不是回落 60s
        _m = self._miner()
        for _et in (pp.EV_HEART_BEAT, pp.EV_HEART_ALIVE):
            _pt, _tol, _why = _m._m410_plan(_et)
            self.assertEqual("no_profile", _why)
            self.assertIsNone(_tol)

    def test_37_classify_and_is_target(self):
        self.assertEqual("", tp.classify("time.tick"))
        self.assertFalse(tp.is_target("time.tick"))
        self.assertTrue(tp.is_window_type(pp.EV_EYES_STREAM_FRAME))
        self.assertTrue(tp.is_window_type(pp.EV_DIGEST_KNOWLEDGE))
        self.assertFalse(tp.is_window_type(pp.EV_HEART_BEAT))


# --------------------------------------------------------------------- D


class TestD_Wiring(_Iso):
    """D：真实接线行为（这是「废弃 60s 一刀切」的判据）。"""

    def _beat_miner(self, base: float | None = None):
        """喂 12 条心跳（13.51s 步长，收尾在 now 前 ~1.4s）并发现模式。"""
        self._profile()
        _m = self._miner()
        _base = float(base if base is not None else time.time() - 150.0)
        for _i in range(12):
            _m.feed_event({"event_type": pp.EV_HEART_BEAT, "source_organ": "心脏",
                           "timestamp": _base + _i * 13.51})
        _m.discover_patterns()
        return _m

    def test_40_registered_prediction_carries_tolerance(self):
        _m = self._beat_miner()
        self.assertEqual(1, _m.register_periodic_predictions())
        _p = _m._pending_predictions[-1]
        self.assertEqual("periodic", _p.get("predict_type"))
        self.assertLess(_p["tolerance_sec"], 2.0)      # 0.675 量级，绝不是 60
        self.assertGreater(_p["tolerance_sec"], 0.1)

    def test_41_hit_within_tolerance(self):
        _m = self._beat_miner()
        _m.register_periodic_predictions()
        _p = _m._pending_predictions[-1]
        _m.feed_event({"event_type": pp.EV_HEART_BEAT, "source_organ": "心脏",
                       "timestamp": _p["predicted_ts"] + 0.30})
        self.assertEqual(1, _m._m410_stat[pp.EV_HEART_BEAT]["hit"])

    def test_42_near_miss_now_counts_miss(self):
        """偏 2.0s：旧 60s 容差必算命中；新表容差 0.675s 必须 miss。"""
        _m = self._beat_miner()
        _m.register_periodic_predictions()
        _p = _m._pending_predictions[-1]
        self.assertLess(_p["tolerance_sec"], 2.0)
        _m.feed_event({"event_type": pp.EV_HEART_BEAT, "source_organ": "心脏",
                       "timestamp": _p["predicted_ts"] + 2.0})
        _m.sweep_typed_predictions(_p["predicted_ts"] + 3.0)
        self.assertEqual(0, _m._m410_stat[pp.EV_HEART_BEAT]["hit"])
        self.assertEqual(1, _m._m410_stat[pp.EV_HEART_BEAT]["miss"])

    def test_43_expired_prediction_judged_miss_and_popped(self):
        _m = self._miner()
        _m._pending_predictions = [{"event_type": "x.y", "predicted_ts": 1000.0,
                                    "created_at": 999.0, "tolerance_sec": 0.5}]
        _n = _m._m410_expire(1001.0)
        self.assertEqual(1, _n)
        self.assertEqual([], _m._pending_predictions)
        self.assertEqual(1, _m._m410_stat["x.y"]["miss"])

    def test_44_eyes_not_registered_as_time_prediction(self):
        self._profile()
        _m = self._miner()
        _t = time.time()
        _m._periodicity_cache[pp.EV_EYES_STREAM_FRAME] = _seq(_t - 100.0, 2.0, 50)
        _m._discovered_patterns["periodic:" + pp.EV_EYES_STREAM_FRAME] = {
            "discovery_type": "periodic", "event_type": pp.EV_EYES_STREAM_FRAME,
            "avg_interval_seconds": 2.0, "occurrence_count": 50, "regularity": 0.99}
        self.assertEqual(0, _m.register_periodic_predictions(horizon_sec=300))
        self.assertEqual([], _m._pending_predictions)

    def test_44b_typed_time_path_needs_no_periodic_pattern(self):
        """七类按档案 median 预测，不依赖「模式发现」的 CV 门槛。

        生产实况：energy.metabolism_snapshot 有 50 个样本、median 30.15s，
        却没通过 CV<0.2 ⇒ 旧路径永远登记不到预测（覆盖率 0）。
        """
        self._profile()
        _m = self._miner()
        _t = time.time()
        _et = pp.EV_ENERGY_METABOLISM_SNAPSHOT
        _m._periodicity_cache[_et] = _seq(_t - 300.0, 30.0, 11)   # 末条 = now
        self.assertEqual({}, _m._discovered_patterns)             # 刻意没有模式
        self.assertGreaterEqual(_m.register_periodic_predictions(), 1)
        _hit = [p for p in _m._pending_predictions if p["event_type"] == _et]
        self.assertEqual(1, len(_hit))
        self.assertEqual("profile", _hit[0].get("source"))
        self.assertLess(_hit[0]["tolerance_sec"], 3.0)

    def test_44c_switch_off_registers_no_typed_time(self):
        self._profile()
        _m = self._miner()
        config.ENABLE_M410_TYPED_TOLERANCE = False
        try:
            _t = time.time()
            _m._periodicity_cache[pp.EV_HEART_BEAT] = _seq(_t - 135.0, 13.5, 11)
            self.assertEqual(0, _m.register_periodic_predictions())
            self.assertEqual([], _m._pending_predictions)
        finally:
            config.ENABLE_M410_TYPED_TOLERANCE = True

    def test_45_eyes_window_hit(self):
        self._profile()
        _m = self._miner()
        _t0 = _align(time.time() - 400.0)
        for _i in range(30):
            _m.feed_event({"event_type": pp.EV_EYES_STREAM_FRAME,
                           "source_organ": "眼睛", "timestamp": _t0 + _i * 2.0})
        self.assertGreaterEqual(_m.register_typed_predictions(_t0 + 59.5), 1)
        for _i in range(30, 60):
            _m.feed_event({"event_type": pp.EV_EYES_STREAM_FRAME,
                           "source_organ": "眼睛", "timestamp": _t0 + _i * 2.0})
        _m.sweep_typed_predictions(_t0 + 121.0)
        _st = _m._m410_stat[pp.EV_EYES_STREAM_FRAME]
        self.assertEqual(1, _st["hit"])

    def test_46_eyes_dropout_is_miss_not_auto_hit(self):
        """同一条判据下掉帧必须判 miss —— 这就是「不再恒命中」。"""
        self._profile()
        _m = self._miner()
        _t0 = _align(time.time() - 400.0)
        for _i in range(30):
            _m.feed_event({"event_type": pp.EV_EYES_STREAM_FRAME,
                           "source_organ": "眼睛", "timestamp": _t0 + _i * 2.0})
        _m.register_typed_predictions(_t0 + 59.5)          # 预测第 2 窗
        for _i in range(6):                                 # 第 2 窗只来 6 条
            _m.feed_event({"event_type": pp.EV_EYES_STREAM_FRAME,
                           "source_organ": "眼睛", "timestamp": _t0 + 60.0 + _i * 2.0})
        _m.sweep_typed_predictions(_t0 + 121.0)
        _st = _m._m410_stat[pp.EV_EYES_STREAM_FRAME]
        self.assertEqual(1, _st["miss"])
        self.assertEqual(0, _st["hit"])

    def test_47_burst_point_judgement(self):
        self._profile()
        _m = self._miner()
        _t0 = _align(time.time() - 900.0)
        for _k in range(3):
            _m.feed_event({"event_type": pp.EV_DIGEST_KNOWLEDGE,
                           "source_organ": "代码学习", "timestamp": _t0 + _k * 2.0})
        for _k in range(3):
            _m.feed_event({"event_type": pp.EV_DIGEST_KNOWLEDGE,
                           "source_organ": "代码学习", "timestamp": _t0 + 150.0 + _k * 2.0})
        _m.register_typed_predictions(_t0 + 179.5)
        for _k in range(2):                                 # 预测窗里起 1 个新批
            _m.feed_event({"event_type": pp.EV_DIGEST_KNOWLEDGE,
                           "source_organ": "代码学习", "timestamp": _t0 + 220.0 + _k * 2.0})
        _m.sweep_typed_predictions(_t0 + 241.0)
        # 钉「那一个窗」的判定，而不是钉总命中数（链式登记会顺带判别的窗）
        _rec = [r for r in _m._m410_verdicts
                if r.get("event_type") == pp.EV_DIGEST_KNOWLEDGE
                and r.get("window_start") == _t0 + 180.0]
        self.assertEqual(1, len(_rec))
        self.assertEqual("hit", _rec[0]["verdict"])
        self.assertEqual(1, _rec[0]["actual"])          # 该窗起了 1 个新批

    def test_48_narrow_window_with_burst_point_miss(self):
        """expected=0 而实际起 1 批 ⇒ 必须 miss（burst 也不是恒中）。"""
        self._profile()
        _m = self._miner()
        _t0 = _align(time.time() - 900.0)
        # 造出「每分钟 0 批」的历史：两条相距 300s 的孤立事件
        _m.feed_event({"event_type": pp.EV_DIGEST_KNOWLEDGE, "source_organ": "代码学习",
                       "timestamp": _t0 + 5.0})
        _m.feed_event({"event_type": pp.EV_DIGEST_KNOWLEDGE, "source_organ": "代码学习",
                       "timestamp": _t0 + 305.0})
        _m.sweep_typed_predictions(_t0 + 306.0)
        _m.register_typed_predictions(_t0 + 359.5)
        for _k in range(2):                          # 预测窗里起 1 个新批
            _m.feed_event({"event_type": pp.EV_DIGEST_KNOWLEDGE, "source_organ": "代码学习",
                           "timestamp": _t0 + 400.0 + _k * 2.0})
        _m.sweep_typed_predictions(_t0 + 421.0)
        _st = _m._m410_stat[pp.EV_DIGEST_KNOWLEDGE]
        self.assertGreaterEqual(_st["miss"], 1)

    def test_49_switch_off_registers_nothing_typed(self):
        self._profile()
        _m = self._miner()
        config.ENABLE_M410_TYPED_TOLERANCE = False
        try:
            _t0 = _align(time.time() - 200.0)
            for _i in range(20):
                _m.feed_event({"event_type": pp.EV_EYES_STREAM_FRAME,
                               "source_organ": "眼睛", "timestamp": _t0 + _i * 2.0})
            self.assertEqual(0, _m.register_typed_predictions())
            self.assertEqual([], _m._m410_win_preds)
            self.assertEqual({"closed_windows": 0, "expired_periodic": 0,
                              "dropped_windows": 0}, _m.sweep_typed_predictions())
        finally:
            config.ENABLE_M410_TYPED_TOLERANCE = True

    def test_50_consume_exposes_typed_fields(self):
        self._profile()
        _m = self._miner()
        _out = _m.consume()
        self.assertIn("registered_typed_predictions", _out)
        self.assertIn("typed_sweep", _out)

    def test_51_stats_hit_rate_none_without_coverage(self):
        _m = self._miner()
        _m._m410_note(pp.EV_EYES_STREAM_FRAME, "no_coverage", predict_type="count")
        _st = _m.get_typed_prediction_stats()
        _e = _st["by_event"][pp.EV_EYES_STREAM_FRAME]
        self.assertIsNone(_e["hit_rate"])
        self.assertEqual(1, _e["no_coverage"])


# --------------------------------------------------------------------- E


class TestE_Static(unittest.TestCase):
    """E：静态接线（AST/regex；不跑行为）。"""

    @classmethod
    def setUpClass(cls):
        cls._src_sm = io.open(_MOD_SM, encoding="utf-8").read()
        cls._src_tp = io.open(_MOD_TP, encoding="utf-8").read()
        cls._tree = ast.parse(cls._src_sm)

    def test_60_tolerance_default_is_none(self):
        for _n in ast.walk(self._tree):
            if isinstance(_n, ast.FunctionDef) and _n.name == "_check_prediction_hits":
                _args = _n.args.args
                self.assertEqual("tolerance", _args[-1].arg)
                _def = _n.args.defaults[-1]
                self.assertIsInstance(_def, ast.Constant)
                self.assertIsNone(_def.value)
                return
        self.fail("未找到 _check_prediction_hits")

    def test_61_no_new_thread_or_scheduler(self):
        for _src in (self._src_sm, self._src_tp):
            self.assertNotIn("Thread(", _src)
            self.assertNotIn("Timer(", _src)
            self.assertNotIn("import sched", _src)

    def test_62_consume_calls_typed_hooks(self):
        _calls = set()
        for _n in ast.walk(self._tree):
            if isinstance(_n, ast.FunctionDef) and _n.name == "consume":
                for _c in ast.walk(_n):
                    if isinstance(_c, ast.Call) and isinstance(_c.func, ast.Attribute):
                        _calls.add(_c.func.attr)
        self.assertIn("register_typed_predictions", _calls)
        self.assertIn("sweep_typed_predictions", _calls)

    def test_63_no_bare_dotted_event_literals(self):
        """事件字符串门禁：新增代码不许出现裸点分事件名（含注释里的引号写法）。

        只扫 `自测` 之前的部分 —— 文件末尾的 `__main__` 自测块是存量代码，
        它里面的示例事件名不属本批新增（门禁也只查新增行）。
        """
        _vals = set(_INTERVALS) | {"time.tick"}
        _cut = self._src_sm.find("# ========== 自测 ==========")
        _body = self._src_sm if _cut < 0 else self._src_sm[:_cut]
        for _src, _name in ((_body, "StreamMiner"), (self._src_tp, "typed_prediction")):
            for _m in re.finditer(r"""['"]((?:[^'"\\]|\\.)*)['"]""", _src):
                self.assertNotIn(_m.group(1), _vals, "%s 出现裸事件名 %r" % (_name, _m.group(1)))

    def test_64_plan_used_in_both_entry_points(self):
        """容差与类型判定只走 _m410_plan 一处（避免两套口径分叉）。"""
        self.assertGreaterEqual(self._src_sm.count("self._m410_plan("), 2)
        self.assertNotIn('== "heart.beat"', self._src_sm)

    def test_65_json_roundtrip_of_verdict_record(self):
        """判定流水必须可 JSON 序列化（刀3 要落盘聚合）。"""
        _rec = {"event_type": pp.EV_EYES_STREAM_FRAME, "verdict": "hit", "at": 1.0,
                "expected": 30.0, "lo": 19, "hi": 43, "actual": 30}
        self.assertEqual(_rec, json.loads(json.dumps(_rec, ensure_ascii=False)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
