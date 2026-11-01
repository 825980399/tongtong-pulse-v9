# -*- coding: utf-8 -*-
"""420 · B 类验收集 (`tools/m420_acceptance_b.py`) 的判据自检。

## 为什么要给它写单测

这个工具的作用是「替人下结论」，所以它自己错了比没有更糟：
  * 把 `WAIT` 判成 `FAIL` ⇒ 每次没开框架都报一堆假红，几轮之后没人再看；
  * 把 `WAIT` 判成 `PASS` ⇒ 自欺，就是 416–418 连续三批"待活体验收"的翻版；
  * 判据读错字段 ⇒ 420 复核时真实踩到两个：
    ① `effect_verify` 落盘字段是 `dims.perf_ok`，**没有**顶层 `perf_delta_ms`；
    ② `verdicts` 是 **dict**（`patch_id -> verdict`），不是 list。

## 隔离

全部用 `tmp_path` + `monkeypatch` 改 `acc.ROOT`，**不读不写真实 `data/`**。
"""
import json
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import m420_acceptance_b as acc  # noqa: E402


def _w(root, rel, obj=None, text=None, mtime=None):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        if text is not None:
            f.write(text)
        else:
            json.dump(obj, f, ensure_ascii=False)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class _Base(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name
        self._old = acc.ROOT
        acc.ROOT = self.root

    def tearDown(self):
        acc.ROOT = self._old
        self._td.cleanup()

    def snap(self, age_sec=0.0, threads=42, growth=1.0, pid=1):
        _w(self.root, "data/monitor/health_snapshot.json", obj={
            "timestamp": time.time() - age_sec,
            "pid": pid,
            "proc_gen": "pid%d@test" % pid,
            "threads": {"n": threads},
            "memory": {"growth_mb_per_min": growth, "process_rss_mb": 100.0},
        })


class TestAlive(_Base):
    def test_01_fresh_snapshot_is_alive(self):
        self.snap(age_sec=10)
        _ok, _why = acc.check_alive(time.time())
        self.assertTrue(_ok, _why)

    def test_02_stale_snapshot_is_not_alive(self):
        self.snap(age_sec=acc.TH_ALIVE_SEC + 60)
        _ok, _why = acc.check_alive(time.time())
        self.assertFalse(_ok)
        self.assertIn("未更新", _why)

    def test_03_missing_snapshot_is_not_alive(self):
        _ok, _why = acc.check_alive(time.time())
        self.assertFalse(_ok)
        self.assertIn("缺失", _why)


class TestThreeState(_Base):
    """★核心：三种态不能互相冒充。"""

    def test_10_patterns_fresh_pass(self):
        self.snap(age_sec=1)
        _w(self.root, "data/stream_miner/patterns.json", obj={"patterns": {}})
        r = acc.c_patterns(time.time(), alive=True)
        self.assertEqual(acc.PASS, r.state, r.detail)

    def test_11_patterns_stale_fail_when_alive(self):
        self.snap(age_sec=1)
        _w(self.root, "data/stream_miner/patterns.json", obj={"patterns": {}},
           mtime=time.time() - acc.TH_PATTERNS_FRESH_SEC - 100)
        r = acc.c_patterns(time.time(), alive=True)
        self.assertEqual(acc.FAIL, r.state, r.detail)

    def test_12_patterns_is_wait_when_framework_down(self):
        """★框架没跑时 mtime 当然不动 —— 判 FAIL 等于把「没开机」记成「修复没生效」。"""
        _w(self.root, "data/stream_miner/patterns.json", obj={"patterns": {}},
           mtime=time.time() - 99999)
        r = acc.c_patterns(time.time(), alive=False)
        self.assertEqual(acc.WAIT, r.state, r.detail)
        self.assertIn("框架未运行", r.detail)

    def test_13_threads_wait_when_framework_down(self):
        """停机前那个 90 是**改前基线**，不能当改后结论。"""
        self.snap(age_sec=99999, threads=90)
        r = acc.c_threads(alive=False)
        self.assertEqual(acc.WAIT, r.state, r.detail)
        self.assertEqual(90, r.value["threads"])

    def test_14_threads_pass_fail_boundary(self):
        self.snap(age_sec=1, threads=acc.TH_THREADS - 1)
        self.assertEqual(acc.PASS, acc.c_threads(alive=True).state)
        self.snap(age_sec=1, threads=acc.TH_THREADS)
        self.assertEqual(acc.FAIL, acc.c_threads(alive=True).state)

    def test_15_threads_wait_when_field_missing(self):
        _w(self.root, "data/monitor/health_snapshot.json",
           obj={"timestamp": time.time(), "pid": 1})
        self.assertEqual(acc.WAIT, acc.c_threads(alive=True).state)


class TestPerfDelta(_Base):
    """★这条踩过两个坑：字段名 `dims.perf_ok`、容器是 dict。"""

    def _ev(self, verdicts):
        _w(self.root, "data/evolution/effect_verify.json",
           obj={"version": 1, "updated": time.time(), "issues": {},
                "verdicts": verdicts})

    def test_20_dict_container_is_supported(self):
        self._ev({"patch_a": {"patch_id": "patch_a", "issue_type": "silent_exception",
                              "dims": {"perf_ok": True}, "verified_at": time.time()}})
        r = acc.c_perf_delta()
        self.assertEqual(acc.PASS, r.state, r.detail)

    def test_21_stub_rows_are_excluded(self):
        """`issue_type="x"` / `file="a.py"` 是演示桩，不能算进"有数据"。"""
        self._ev({
            "p3d": {"patch_id": "p3d", "issue_type": "x", "file": "a.py",
                    "dims": {"perf_ok": True}, "verified_at": time.time()},
            "pperf": {"patch_id": "pperf", "issue_type": "x", "file": "a.py",
                      "dims": {"perf_ok": False}, "verified_at": time.time()},
            "patch_a": {"patch_id": "patch_a", "issue_type": "silent_exception",
                        "dims": {"perf_ok": None}, "verified_at": time.time()},
        })
        r = acc.c_perf_delta()
        self.assertEqual(acc.WAIT, r.state, r.detail)
        self.assertEqual(2, r.value["stub_rows"], r.value)
        self.assertEqual(1, r.value["real"], r.value)
        self.assertEqual(0, r.value["with_perf_ok"], r.value)

    def test_22_all_null_real_is_wait_not_fail(self):
        """真补丁全 null ⇒ 是「等一次新 apply」，不是「代码坏了」。"""
        self._ev({"patch_a": {"patch_id": "patch_a", "issue_type": "silent_exception",
                              "dims": {"perf_ok": None}, "verified_at": time.time()}})
        r = acc.c_perf_delta()
        self.assertEqual(acc.WAIT, r.state, r.detail)
        self.assertIn("新 apply", r.detail)

    def test_23_list_container_also_works(self):
        self._ev([{"patch_id": "patch_a", "issue_type": "silent_exception",
                   "dims": {"perf_ok": True}, "verified_at": time.time()}])
        self.assertEqual(acc.PASS, acc.c_perf_delta().state)

    def test_24_below_ratio_is_fail(self):
        _v = {}
        for i in range(10):
            _v["patch_%d" % i] = {
                "patch_id": "patch_%d" % i, "issue_type": "silent_exception",
                "dims": {"perf_ok": True if i == 0 else None},
                "verified_at": time.time()}
        self._ev(_v)
        r = acc.c_perf_delta()
        self.assertEqual(acc.FAIL, r.state, r.detail)


class TestChannels(_Base):
    def _traces(self, rows):
        _t = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        _w(self.root, "data/llm_traces/calls_%s.jsonl" % acc._today(), text=_t)

    def test_30_balanced_pass(self):
        self._traces([{"channel": c} for c in
                      ["a", "b", "c"] * 10 + ["a", "b", "c"]])
        r = acc.c_channels()
        self.assertEqual(acc.PASS, r.state, r.detail)
        self.assertEqual(3, r.value["channels_used"])

    def test_31_monopoly_fail(self):
        self._traces([{"channel": "a"}] * 90 + [{"channel": "b"}] * 10)
        r = acc.c_channels()
        self.assertEqual(acc.FAIL, r.state, r.detail)
        self.assertGreater(r.value["top_share"], acc.TH_CHANNEL_TOP_SHARE)

    def test_32_no_trace_is_wait(self):
        self.assertEqual(acc.WAIT, acc.c_channels().state)

    def test_33_empty_file_is_wait(self):
        self._traces([])
        self.assertEqual(acc.WAIT, acc.c_channels().state)


class TestChannelStreak(_Base):
    """★420/L09-6：`L09-5` 的判据在**改前代码**上就 PASS（9 渠道 / 最大 32.9%），
    没有判别力；这里按「最长同渠道连续段」判，现网改前基线是 **15**。"""

    def _traces(self, rows):
        _t = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        _w(self.root, "data/llm_traces/calls_%s.jsonl" % acc._today(), text=_t)

    def test_34_balanced_pass_with_boundary(self):
        # 40 轮 a/b/c 轮转 ⇒ 连续段恒 1，远低于线
        self._traces([{"ts": i, "channel": c}
                      for i, c in enumerate(["a", "b", "c"] * 40)])
        r = acc.c_channel_streak(time.time(), True)
        self.assertEqual(acc.PASS, r.state, r.detail)
        self.assertEqual(1, r.value["max_streak"])
        self.assertEqual(120, r.value["calls"])
        # ★边界两侧各一例（铁律 275）：== 线 通过，线+1 不通过。
        #   ★样本必须 >= TH_STREAK_MIN_CALLS，否则会先被"样本不足"判 WAIT ——
        #   第一版只造 4 次，测出来是 WAIT（工具是对的，我测试写错了）。
        _k = acc.TH_CHANNEL_MAX_STREAK
        _rows = []
        for _g in range(12):                      # 12 轮 (a×k, b) = 12*(k+1) 次
            _rows += [{"ts": _g * 10 + _i, "channel": "a"} for _i in range(_k)]
            _rows.append({"ts": _g * 10 + _k, "channel": "b"})
        self.assertGreaterEqual(len(_rows), acc.TH_STREAK_MIN_CALLS)
        self._traces(_rows)
        r = acc.c_channel_streak(time.time(), True)
        self.assertEqual(acc.PASS, r.state, r.detail)
        self.assertEqual(_k, r.value["max_streak"])

        _rows2 = []
        for _g in range(12):                      # 12 轮 (a×(k+1), b)
            _rows2 += [{"ts": _g * 10 + _i, "channel": "a"} for _i in range(_k + 1)]
            _rows2.append({"ts": _g * 10 + _k + 1, "channel": "b"})
        self._traces(_rows2)
        r = acc.c_channel_streak(time.time(), True)
        self.assertEqual(acc.FAIL, r.state, r.detail)
        self.assertEqual(_k + 1, r.value["max_streak"])

    def test_35_monopoly_fail(self):
        # 现网改前形态：单渠道长段（实测 15，这里放大到 90）
        self._traces([{"ts": i, "channel": "direct"} for i in range(90)]
                     + [{"ts": 90 + i, "channel": "zhipu"} for i in range(10)])
        r = acc.c_channel_streak(time.time(), True)
        self.assertEqual(acc.FAIL, r.state, r.detail)
        self.assertEqual(90, r.value["max_streak"])
        self.assertEqual("direct", r.value["max_streak_channel"])

    def test_36_not_alive_is_wait_not_fail(self):
        """框架不活 ⇒ WAIT，但**读数必须留着**（那是改前基线，不是"没数据"）。"""
        self._traces([{"ts": i, "channel": "direct"} for i in range(90)]
                     + [{"ts": 90 + i, "channel": "zhipu"} for i in range(10)])
        r = acc.c_channel_streak(time.time(), False)
        self.assertEqual(acc.WAIT, r.state, r.detail)
        self.assertEqual(90, r.value["max_streak"])
        self.assertFalse(r.value["framework_alive"])

    def test_37_small_sample_is_wait(self):
        """样本 < TH_STREAK_MIN_CALLS ⇒ 判不出均衡，必须 WAIT（不许硬判）。"""
        self._traces([{"ts": i, "channel": c}
                      for i, c in enumerate(["a", "b"] * 5)])
        r = acc.c_channel_streak(time.time(), True)
        self.assertEqual(acc.WAIT, r.state, r.detail)
        self.assertLess(r.value["calls"], acc.TH_STREAK_MIN_CALLS)

    def test_38_no_trace_is_wait(self):
        self.assertEqual(acc.WAIT, acc.c_channel_streak(time.time(), True).state)

    def test_39_sorted_by_ts_not_file_order(self):
        """★按 `ts` 排序后再数段：文件里的落盘顺序不等于调用顺序。

        构造：真顺序是 `a,a,b,b` 重复 10 轮（20 段 / 最长 2）；
        而按落盘顺序读是 `a,b,a,b,…`（40 段 / 最长 1）。
        ⇒ 段数 20 才说明排过序，40 就说明直接吃了文件顺序。
        """
        rows = []
        for k in range(10):
            _b = 4 * k
            rows.append({"ts": _b + 1, "channel": "a"})
            rows.append({"ts": _b + 3, "channel": "b"})   # 落盘顺序 b 在 a 前面
            rows.append({"ts": _b + 2, "channel": "a"})
            rows.append({"ts": _b + 4, "channel": "b"})
        self._traces(rows)
        r = acc.c_channel_streak(time.time(), True)
        self.assertEqual(acc.PASS, r.state, r.detail)
        self.assertEqual(20, r.value["segments"])
        self.assertEqual(2, r.value["max_streak"])
        self.assertEqual(40, r.value["calls"])


class TestExitCode(_Base):
    """★退出码是给 CI 用的：WAIT 不能等于 FAIL。"""

    def test_40_render_all_pass_returns_0(self):
        _rows = [acc.R("x", "n", acc.PASS, 1, "ok")]
        self.assertEqual(0, acc.render(_rows))

    def test_41_render_has_fail_returns_1(self):
        _rows = [acc.R("x", "n", acc.PASS, 1, "ok"), acc.R("y", "m", acc.FAIL, 0, "no")]
        self.assertEqual(1, acc.render(_rows))

    def test_42_render_only_wait_returns_2(self):
        _rows = [acc.R("x", "n", acc.PASS, 1, "ok"), acc.R("y", "m", acc.WAIT, None, "-")]
        self.assertEqual(2, acc.render(_rows))

    def test_43_render_never_raises_on_empty(self):
        self.assertEqual(0, acc.render([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
