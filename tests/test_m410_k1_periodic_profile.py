# -*- coding: utf-8 -*-
"""第410批刀1 判据：七类事件周期档案（periodic_profile）。

分组：
  A intervals_of       —— 相邻间隔的构造口径（不猜、不丢、不清洗）
  B 分位数             —— 空列表返 None，绝不编造默认值
  C build_profile      —— 七类齐全 / 缺类 / 样本不足 / 固定表 / 容差
  D 开关               —— 关掉不写盘、tolerance_for 恒 None（零行为变化）
  E 真实数据           —— 真跑生产 patterns.json，heart.beat 落在 11~16s
  F AST 接线           —— 固定表与目标集一致、开关读取处唯一
  G 落盘/读回          —— 原子写、过期、损坏
"""
import ast
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.world_model import periodic_profile as pp  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD = os.path.join(_ROOT, "nucleus", "world_model", "periodic_profile.py")
_NOW = 1793450000.0


def _mk_patterns(mapping: dict) -> dict:
    """构造 patterns 结构（只填 periodicity）。"""
    return {"version": 1, "saved_at": _NOW, "periodicity": dict(mapping)}


def _seq(start: float, step: float, n: int) -> list:
    return [start + step * i for i in range(n)]


class TestA_IntervalsOf(unittest.TestCase):
    """A：相邻间隔构造。"""

    def test_01_regular_sequence(self):
        _iv = pp.intervals_of([1000.0, 1010.0, 1020.0, 1030.0])
        self.assertEqual([10.0, 10.0, 10.0], _iv)

    def test_02_unsorted_input_is_sorted_first(self):
        _iv = pp.intervals_of([1030.0, 1000.0, 1020.0, 1010.0])
        self.assertEqual([10.0, 10.0, 10.0], _iv)

    def test_03_non_numeric_skipped_not_crash(self):
        _iv = pp.intervals_of([1000.0, "x", 1010.0, None, 1020.0])
        self.assertEqual([10.0, 10.0], _iv)

    def test_04_single_point_yields_empty(self):
        self.assertEqual([], pp.intervals_of([5.0]))
        self.assertEqual([], pp.intervals_of([]))
        self.assertEqual([], pp.intervals_of(None))

    def test_05_non_positive_stamps_dropped(self):
        # <=0 的时间戳是脏数据（epoch 0 不是有效时刻），丢弃；
        # 但**正数之间的 0 间隔要保留**（那是"同一刻两条事件"，是真事实）
        _iv = pp.intervals_of([-1.0, 0.0, 1000.0, 1010.0, 1020.0])
        self.assertEqual([10.0, 10.0], _iv)

    def test_06_zero_interval_is_kept(self):
        # 同一时刻的两条事件 ⇒ 间隔 0 是事实，不许在统计层"清洗"掉
        _iv = pp.intervals_of([1000.0, 1000.0, 1010.0])
        self.assertEqual([0.0, 10.0], _iv)


class TestB_Percentile(unittest.TestCase):
    """B：分位数。"""

    def test_10_median_even_takes_mean(self):
        """中位间隔是标准中位数：偶数样本取中间两个的均值。

        ★不能用 _pct(xs, 0.5) 代替 —— nearest-rank 会取偏小的那个（10.0），
        而"中位间隔"的语义是 15.0。两者对奇数样本一致、对偶数样本分叉。
        """
        self.assertEqual(15.0, pp._median([10.0, 20.0]))
        self.assertEqual(10.0, pp._pct([10.0, 20.0], 0.5), "p90 那套是 nearest-rank")
        self.assertEqual(10.0, pp._median([10.0]))
        self.assertIsNone(pp._median([]))

    def test_11_p90_nearest_rank(self):
        _xs = [float(i) for i in range(1, 11)]  # 1..10
        self.assertEqual(9.0, pp._pct(_xs, 0.9))

    def test_12_empty_returns_none_not_default(self):
        self.assertIsNone(pp._pct([], 0.5))
        self.assertIsNone(pp._pct([], 0.9))

    def test_13_q_zero_and_one_bounded(self):
        _xs = [1.0, 2.0, 3.0]
        self.assertEqual(1.0, pp._pct(_xs, 0.0))
        self.assertEqual(3.0, pp._pct(_xs, 1.0))


class TestC_BuildProfile(unittest.TestCase):
    """C：档案构造。"""

    def _full(self, step: float = 10.0) -> dict:
        return _mk_patterns({et: _seq(0.0, step, 10) for et in pp.TARGET_EVENTS})

    def test_20_all_seven_present(self):
        _r = pp.build_profile(self._full(), now=_NOW)
        self.assertTrue(_r["complete"])
        self.assertEqual([], _r["missing"])
        self.assertEqual(set(pp.TARGET_EVENTS), set(_r["events"]))

    def test_21_missing_event_listed_not_crashed(self):
        _m = {et: _seq(0.0, 10.0, 10) for et in pp.TARGET_EVENTS[:-1]}
        _r = pp.build_profile(_mk_patterns(_m), now=_NOW)
        self.assertFalse(_r["complete"])
        self.assertEqual([pp.TARGET_EVENTS[-1]], _r["missing"])
        # 缺的那类仍出现在 events 里（值为空），便于下游统一遍历
        self.assertIn(pp.TARGET_EVENTS[-1], _r["events"])
        self.assertIsNone(_r["events"][pp.TARGET_EVENTS[-1]]["median_interval_sec"])

    def test_22_insufficient_samples_no_median(self):
        _m = {et: _seq(0.0, 10.0, 10) for et in pp.TARGET_EVENTS}
        _m["heart.beat"] = [0.0, 13.0]  # 只有 1 个间隔 < MIN_SAMPLES-1
        _r = pp.build_profile(_mk_patterns(_m), now=_NOW)
        _hb = _r["events"]["heart.beat"]
        self.assertFalse(_hb["ok"])
        self.assertIsNone(_hb["median_interval_sec"])
        self.assertIn("heart.beat", _r["insufficient"])
        self.assertIn("note", _hb)

    def test_23_predict_type_is_fixed_table(self):
        _r = pp.build_profile(self._full(), now=_NOW)
        self.assertEqual("periodic", _r["events"]["heart.beat"]["predict_type"])
        self.assertEqual("count", _r["events"]["eyes.stream_frame"]["predict_type"])
        self.assertEqual("burst", _r["events"]["digest.knowledge"]["predict_type"])

    def test_24_tolerance_from_median_pct(self):
        _r = pp.build_profile(self._full(step=10.0), now=_NOW)
        # periodic 5% ⇒ 10 * 0.05 = 0.5
        self.assertAlmostEqual(0.5, _r["events"]["heart.beat"]["tolerance_sec"], places=6)
        # spinal 10% ⇒ 1.0
        self.assertAlmostEqual(1.0, _r["events"]["spinal.inspection_report"]["tolerance_sec"], places=6)

    def test_25_count_and_burst_have_no_time_tolerance(self):
        _r = pp.build_profile(self._full(), now=_NOW)
        self.assertIsNone(_r["events"]["eyes.stream_frame"]["tolerance_sec"])
        self.assertIsNone(_r["events"]["digest.knowledge"]["tolerance_sec"])

    def test_26_bad_periodicity_type_does_not_crash(self):
        _r = pp.build_profile({"periodicity": "not-a-dict"}, now=_NOW)
        self.assertEqual(len(pp.TARGET_EVENTS), len(_r["missing"]))

    def test_27_median_reflects_step(self):
        _r = pp.build_profile(
            _mk_patterns({et: _seq(0.0, 13.5, 50) for et in pp.TARGET_EVENTS}), now=_NOW)
        self.assertAlmostEqual(13.5, _r["events"]["heart.beat"]["median_interval_sec"], places=4)


class TestD_Switch(unittest.TestCase):
    """D：灰度双向（关掉 = 零行为变化）。"""

    def setUp(self):
        import config
        self._cfg = config
        self._had = hasattr(config, "ENABLE_M410_PERIODIC_PROFILE")
        self._old = getattr(config, "ENABLE_M410_PERIODIC_PROFILE", True)
        self._tmp = tempfile.mkdtemp(prefix="m410k1_")
        self._saved = pp._DATA_DIR
        pp.set_data_dir(self._tmp)

    def tearDown(self):
        if self._had:
            self._cfg.ENABLE_M410_PERIODIC_PROFILE = self._old
        elif hasattr(self._cfg, "ENABLE_M410_PERIODIC_PROFILE"):
            delattr(self._cfg, "ENABLE_M410_PERIODIC_PROFILE")
        pp.set_data_dir(self._saved)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_30_on_by_default(self):
        self._cfg.ENABLE_M410_PERIODIC_PROFILE = True
        self.assertTrue(pp.enabled())

    def test_31_off_writes_nothing(self):
        self._cfg.ENABLE_M410_PERIODIC_PROFILE = False
        _r = pp.build(_mk_patterns({et: _seq(0.0, 10.0, 10) for et in pp.TARGET_EVENTS}),
                      now=_NOW)
        self.assertFalse(_r["written"])
        self.assertEqual("switch_off", _r["skipped"])
        self.assertFalse(os.path.exists(pp.profile_path()), "开关关闭时不得落盘")

    def test_32_off_tolerance_always_none(self):
        # 先在有开关时写一份合格档案
        self._cfg.ENABLE_M410_PERIODIC_PROFILE = True
        pp.build(_mk_patterns({et: _seq(0.0, 10.0, 10) for et in pp.TARGET_EVENTS}), now=_NOW)
        self.assertIsNotNone(pp.tolerance_for("heart.beat", now=_NOW))
        # 关掉后恒 None（调用方回落旧 60s，而不是"用档案但不写盘"的半开状态）
        self._cfg.ENABLE_M410_PERIODIC_PROFILE = False
        self.assertIsNone(pp.tolerance_for("heart.beat", now=_NOW))


class TestE_RealData(unittest.TestCase):
    """E：真实 patterns.json（只读，不重定向）。"""

    def test_40_real_patterns_complete(self):
        _pat = pp.read_patterns(os.path.join(_ROOT, pp.PATTERNS_REL))
        if not _pat:
            self.skipTest("生产 patterns.json 不存在或不可读")
        _r = pp.build_profile(_pat)
        self.assertEqual([], _r["missing"], "七类事件必须全部出现在 periodicity 里")
        self.assertTrue(_r["complete"])

    def test_41_real_heart_beat_around_13s(self):
        """★任务书验收：heart.beat 的 median 在 13s 左右（不是 0s）。

        区间取 [11, 16] 而不是 `== 13.0`：钉精确值会被"多跑一次心跳"顶红
        （铁律 9：>= 下限 + 具名判据，不锁历史快照）。
        """
        _pat = pp.read_patterns(os.path.join(_ROOT, pp.PATTERNS_REL))
        if not _pat:
            self.skipTest("生产 patterns.json 不存在或不可读")
        _r = pp.build_profile(_pat)
        _med = _r["events"]["heart.beat"]["median_interval_sec"]
        self.assertIsNotNone(_med, "heart.beat 没有中位间隔")
        self.assertGreaterEqual(_med, 11.0, "heart.beat 中位间隔低于 11s ⇒ 疑似混入 receive 侧")
        self.assertLessEqual(_med, 16.0, "heart.beat 中位间隔高于 16s ⇒ 疑似漏采")

    def test_42_real_receive_not_mixed_in(self):
        """★反证：若 receive 侧混入，同类型会有大量 0 间隔 ⇒ median 会塌到 0 附近。

        periodicity 天然只含 emit 侧（InfoField 在 _total_published 之后才 feed），
        所以这里断言「心跳类 median > 1s」，等价于「没有被 receive 污染」。
        """
        _pat = pp.read_patterns(os.path.join(_ROOT, pp.PATTERNS_REL))
        if not _pat:
            self.skipTest("生产 patterns.json 不存在或不可读")
        _r = pp.build_profile(_pat)
        for _et in ("heart.beat", "heart.alive"):
            _med = _r["events"][_et]["median_interval_sec"]
            if _med is None:
                continue
            self.assertGreater(_med, 1.0, "%s 疑似被 receive 侧污染（median=%.4f）" % (_et, _med))


class TestF_AstWiring(unittest.TestCase):
    """F：结构契约（固定表完整、开关读取处唯一）。"""

    def setUp(self):
        self._src = io.open(_MOD, encoding="utf-8").read()
        self._tree = ast.parse(self._src, filename=_MOD)

    def test_50_target_events_exactly_seven(self):
        self.assertEqual(7, len(pp.TARGET_EVENTS))
        self.assertEqual(7, len(set(pp.TARGET_EVENTS)))

    def test_51_predict_type_covers_all_targets(self):
        self.assertEqual(set(pp.TARGET_EVENTS), set(pp.PREDICT_TYPE))
        self.assertEqual(set(pp.TARGET_EVENTS), set(pp.TOLERANCE_PCT))

    def test_52_predict_type_values_are_the_three_kinds(self):
        self.assertEqual({"periodic", "count", "burst"}, set(pp.PREDICT_TYPE.values()))

    def test_53_switch_read_once(self):
        """开关只允许在 enabled() 里读 —— 多处读会漂移。"""
        _hits = [n for n in ast.walk(self._tree)
                 if isinstance(n, ast.Constant)
                 and n.value == "ENABLE_M410_PERIODIC_PROFILE"]
        self.assertEqual(1, len(_hits), "ENABLE_M410_PERIODIC_PROFILE 应只在一个地方读取")

    def test_54_no_new_scheduler(self):
        """红线：不新建调度器 —— 本模块不得 import threading / 不得起 Thread。"""
        _bad = []
        for n in ast.walk(self._tree):
            if isinstance(n, ast.Import):
                _bad += [a.name for a in n.names if a.name in ("threading", "sched", "apscheduler")]
            if isinstance(n, ast.ImportFrom) and (n.module or "") in ("threading", "sched"):
                _bad.append(n.module)
        self.assertEqual([], _bad, "本模块不得引入调度/线程依赖")
        self.assertNotIn("Thread(", self._src)
        self.assertNotIn("Timer(", self._src)


class TestG_SaveLoad(unittest.TestCase):
    """G：落盘与读回。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m410k1g_")
        self._saved = pp._DATA_DIR
        pp.set_data_dir(self._tmp)

    def tearDown(self):
        pp.set_data_dir(self._saved)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_60_save_then_load_roundtrip(self):
        _prof = pp.build_profile(_mk_patterns({et: _seq(0.0, 10.0, 10) for et in pp.TARGET_EVENTS}),
                                 now=_NOW)
        _p = pp.save_profile(_prof)
        self.assertIsNotNone(_p)
        self.assertTrue(os.path.isfile(_p))
        _back = pp.load_profile(now=_NOW)
        self.assertEqual(_prof["version"], _back["version"])
        self.assertEqual(_prof["events"]["heart.beat"]["median_interval_sec"],
                         _back["events"]["heart.beat"]["median_interval_sec"])

    def test_61_stale_profile_returns_empty(self):
        _prof = pp.build_profile(_mk_patterns({et: _seq(0.0, 10.0, 10) for et in pp.TARGET_EVENTS}),
                                 now=_NOW)
        pp.save_profile(_prof)
        # 档案 age = 10 天 > 1 小时 ⇒ 视为无档案（不许拿过期容差去判命中）
        self.assertEqual({}, pp.load_profile(max_age_sec=3600.0, now=_NOW + 864000.0))
        # 未过期则正常返回
        self.assertNotEqual({}, pp.load_profile(max_age_sec=3600.0, now=_NOW + 10.0))

    def test_62_corrupt_profile_returns_empty(self):
        _p = pp.profile_path()
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        io.open(_p, "w", encoding="utf-8", newline="\n").write("{not json")
        self.assertEqual({}, pp.load_profile())
        self.assertFalse(pp.summarize()["exists"], "损坏档案不得被 summarize 当成存在")

    def test_63_missing_profile_returns_empty(self):
        self.assertEqual({}, pp.load_profile())
        self.assertIsNone(pp.tolerance_for("heart.beat"))

    def test_64_no_tmp_file_left(self):
        _prof = pp.build_profile(_mk_patterns({et: _seq(0.0, 10.0, 10) for et in pp.TARGET_EVENTS}),
                                 now=_NOW)
        pp.save_profile(_prof)
        _dir = os.path.dirname(pp.profile_path())
        _left = [f for f in os.listdir(_dir) if f.endswith(".tmp410")]
        self.assertEqual([], _left, "原子写的临时文件不得残留")


if __name__ == "__main__":
    unittest.main(verbosity=2)
