# -*- coding: utf-8 -*-
"""★403 刀1 判据：L4 因果时序链（同器官 / 跨器官两类，不合并）。

守什么
------
1. **两类链的口径边界**：同器官 <1s 且必须"相邻"，跨器官同 pulse_id 且 <5s；
   边界值（恰好 1.0s / 5.0s）**不算链**——阈值是开区间，别被"约等于"糊过去。
2. **一发射多接收**：一次 emit 被 3 个器官接收 = 3 条跨器官链。
   这条专门守一个真实 bug：去重键若只按 pulse_id，第二个以后的接收方会被丢掉。
3. **覆盖度诚实**：尾部按字节截断时必须落 `truncated/coverage_s/complete`，
   不许把"只覆盖 40 分钟"写成"整小时"。
4. **落盘**：一行一条 hourly_summary、纯 LF、不残留 .tmp。
5. **节流**：同一小时桶内第二次调用必须跳过（每小时一条，不是每拍一条）。
"""
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    _p = os.path.join(_ROOT, "utils", "causal_chain.py")
    _spec = importlib.util.spec_from_file_location("m403_causal_chain", _p)
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return _m


C = _load()


def _emit(pid, organ, event, ts, seq=0):
    return {"pulse_id": pid, "type": "emit", "organ": organ, "event_type": event,
            "ts": ts, "seq": seq, "matched_organs": []}


def _recv(pid, organ, event, ts, seq=0):
    return {"pulse_id": pid, "type": "receive", "organ": organ, "event_type": event,
            "ts": ts, "seq": seq, "from": ""}


class TestConstants(unittest.TestCase):
    def test_01_schema_and_thresholds(self):
        self.assertEqual(C.SCHEMA, "causal_chains/403.v1")
        self.assertEqual(C.SAME_ORGAN_MAX_GAP_S, 1.0)
        self.assertEqual(C.CROSS_ORGAN_MAX_GAP_S, 5.0)
        self.assertEqual(C.WINDOW_S, 3600.0)

    def test_02_output_path_shape(self):
        p = C.output_path(day="20261030", root=_ROOT)
        self.assertTrue(p.endswith(os.path.join("data", "monitor",
                                                 "causal_chains_20261030.jsonl")), p)


class TestSameOrgan(unittest.TestCase):
    def test_10_adjacent_within_1s_is_chain(self):
        rows = [_emit("p1", "心脏", "heart.beat", 100.0, 1),
                _emit("p2", "心脏", "heart.alive", 100.02, 2)]
        r = C.build_chains(rows)
        self.assertEqual(r["counts"]["same_organ_links"], 1)
        top = r["same_organ"]["top"][0]
        self.assertEqual((top["from"], top["to"], top["n"]), ("heart.beat", "heart.alive", 1))
        self.assertAlmostEqual(top["gap_ms_p50"], 20.0, places=1)

    def test_11_gap_equal_1s_is_not_chain(self):
        """阈值是开区间：恰好 1.0s 不算（`<` 不是 `<=`）。"""
        rows = [_emit("p1", "心脏", "heart.beat", 100.0),
                _emit("p2", "心脏", "heart.alive", 101.0)]
        self.assertEqual(C.build_chains(rows)["counts"]["same_organ_links"], 0)

    def test_12_not_adjacent_not_chain(self):
        """A→C 中间夹了 B，即使 A、C 间隔 <1s 也不成链（链式相邻而非任意两点）。"""
        rows = [_emit("p1", "胃", "a.one", 100.0),
                _emit("p2", "胃", "b.two", 100.4),
                _emit("p3", "胃", "c.three", 100.8)]
        r = C.build_chains(rows)
        pairs = {(x["from"], x["to"]) for x in r["same_organ"]["top"]}
        self.assertIn(("a.one", "b.two"), pairs)
        self.assertIn(("b.two", "c.three"), pairs)
        self.assertNotIn(("a.one", "c.three"), pairs)

    def test_13_different_organs_never_same_chain(self):
        rows = [_emit("p1", "胃", "a.one", 100.0),
                _emit("p2", "肝", "b.two", 100.1)]
        self.assertEqual(C.build_chains(rows)["counts"]["same_organ_links"], 0)


class TestCrossOrgan(unittest.TestCase):
    def test_20_same_pulse_id_emit_receive_is_chain(self):
        rows = [_emit("p1", "胃", "knowledge.written", 100.0),
                _recv("p1", "肝", "knowledge.written", 100.03)]
        r = C.build_chains(rows)
        self.assertEqual(r["counts"]["cross_organ_links"], 1)
        top = r["cross_organ"]["top"][0]
        self.assertEqual((top["from_organ"], top["to_organ"]), ("胃", "肝"))
        self.assertAlmostEqual(top["gap_ms_p50"], 30.0, places=1)

    def test_21_one_emit_three_receivers_three_chains(self):
        """★真实 bug 回归：按 pulse_id 去重会把后两个接收方吞掉。"""
        rows = [_emit("p1", "胃", "knowledge.written", 100.0),
                _recv("p1", "肝", "knowledge.written", 100.01),
                _recv("p1", "血管", "knowledge.written", 100.02),
                _recv("p1", "内在世界", "knowledge.written", 100.03)]
        r = C.build_chains(rows)
        self.assertEqual(r["counts"]["cross_organ_links"], 3)
        # 含发送方：只列接收方会漏掉"只发不收"的器官
        self.assertEqual(r["cross_organ"]["organs_involved"],
                         ["内在世界", "肝", "胃", "血管"])

    def test_22_same_organ_receive_excluded(self):
        rows = [_emit("p1", "胃", "x.one", 100.0),
                _recv("p1", "胃", "x.one", 100.01)]
        self.assertEqual(C.build_chains(rows)["counts"]["cross_organ_links"], 0)

    def test_23_gap_equal_5s_excluded(self):
        rows = [_emit("p1", "胃", "x.one", 100.0),
                _recv("p1", "肝", "x.one", 105.0)]
        self.assertEqual(C.build_chains(rows)["counts"]["cross_organ_links"], 0)

    def test_24_unmatched_receive_counted(self):
        rows = [_recv("p9", "肝", "x.one", 100.0)]
        self.assertEqual(C.build_chains(rows)["counts"]["unmatched_receive"], 1)

    def test_25_duplicate_receive_deduped_by_organ(self):
        rows = [_emit("p1", "胃", "x.one", 100.0),
                _recv("p1", "肝", "x.one", 100.01),
                _recv("p1", "肝", "x.one", 100.02)]
        r = C.build_chains(rows)
        self.assertEqual(r["counts"]["cross_organ_links"], 1)
        self.assertEqual(r["counts"]["duplicate_receive"], 1)

    def test_26_by_pair_counts(self):
        rows = [_emit("p1", "胃", "x.one", 100.0),
                _recv("p1", "肝", "x.one", 100.01),
                _emit("p2", "胃", "x.two", 101.0),
                _recv("p2", "肝", "x.two", 101.01)]
        r = C.build_chains(rows)
        self.assertEqual(r["cross_organ"]["by_pair"][0],
                         {"from_organ": "胃", "to_organ": "肝", "n": 2})


class TestSummaryShape(unittest.TestCase):
    def test_30_two_kinds_separated(self):
        rows = [_emit("p1", "胃", "a.one", 100.0),
                _emit("p2", "胃", "b.two", 100.1),
                _emit("p3", "胃", "c.three", 200.0),
                _recv("p3", "肝", "c.three", 200.02)]
        s = C.summarize_window(rows, 0.0, 3600.0)
        self.assertEqual(s["kind"], "hourly_summary")
        self.assertEqual(s["schema"], C.SCHEMA)
        self.assertEqual(s["counts"]["same_organ_links"], 1)
        self.assertEqual(s["counts"]["cross_organ_links"], 1)
        self.assertIn("same_organ", s)
        self.assertIn("cross_organ", s)

    def test_31_coverage_honest(self):
        rows = [_emit("p1", "胃", "a.one", 100.0),
                _emit("p2", "胃", "b.two", 1600.0)]
        s = C.summarize_window(rows, 0.0, 3600.0)
        self.assertAlmostEqual(s["coverage_s"], 1500.0, places=1)
        self.assertFalse(s["complete"])
        self.assertLess(s["coverage_ratio"], 1.0)

    def test_32_empty_window(self):
        s = C.summarize_window([], 0.0, 3600.0)
        self.assertEqual(s["counts"]["emit"], 0)
        self.assertEqual(s["same_organ"]["top"], [])
        self.assertEqual(s["coverage_s"], 0.0)
        self.assertIsNotNone(s["note"])

    def test_33_rule_recorded(self):
        s = C.summarize_window([], 0.0, 3600.0)
        self.assertEqual(s["rule"]["same_organ_max_gap_s"], 1.0)
        self.assertEqual(s["rule"]["cross_organ_max_gap_s"], 5.0)

    def test_34_json_serializable(self):
        rows = [_emit("p1", "胃", "a.one", 100.0), _recv("p1", "肝", "a.one", 100.02)]
        s = C.summarize_window(rows, 0.0, 3600.0)
        _r = json.loads(json.dumps(s, ensure_ascii=False))
        self.assertEqual(_r["counts"]["cross_organ_links"], 1)


class TestTailRows(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m403k1_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, rows):
        p = os.path.join(self.d, "pairs.jsonl")
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return p

    def test_40_missing_file(self):
        r = C.tail_rows(os.path.join(self.d, "nope.jsonl"))
        self.assertEqual(r["rows"], [])
        self.assertEqual(r["reason"], "file_missing")

    def test_41_window_filter(self):
        p = self._write([_emit("p1", "胃", "a.one", 100.0),
                         _emit("p2", "胃", "b.two", 9000.0)])
        r = C.tail_rows(p, window_s=3600, now=9001.0)
        self.assertEqual(len(r["rows"]), 1)
        self.assertEqual(r["rows"][0]["pulse_id"], "p2")

    def test_42_bad_lines_skipped(self):
        p = os.path.join(self.d, "bad.jsonl")
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("{not json}\n")
            f.write(json.dumps(_emit("p1", "胃", "a.one", 9000.0)) + "\n")
        r = C.tail_rows(p, window_s=3600, now=9001.0)
        self.assertEqual(len(r["rows"]), 1)
        self.assertEqual(r["skipped_lines"], 1)

    def test_43_truncated_flag_when_byte_limited(self):
        p = self._write([_emit("p%d" % i, "胃", "a.one", 9000.0 + i * 0.001)
                         for i in range(200)])
        r = C.tail_rows(p, window_s=3600, now=9001.0, max_bytes=2000)
        self.assertTrue(r["truncated"])
        self.assertLess(len(r["rows"]), 200)


class TestWriteContract(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m403k1io_")
        os.makedirs(os.path.join(self.d, "data", "monitor"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_50_append_one_line_pure_lf(self):
        s = C.summarize_window([], 0.0, 3600.0)
        p = C.append_summary(s, os.path.join(self.d, "data", "monitor", "x.jsonl"))
        b = io.open(p, "rb").read()
        self.assertEqual(b.count(b"\r\n"), 0)
        self.assertEqual(len(b.decode("utf-8").splitlines()), 1)

    def test_51_no_tmp_left(self):
        s = C.summarize_window([], 0.0, 3600.0)
        d = os.path.join(self.d, "data", "monitor")
        C.append_summary(s, os.path.join(d, "y.jsonl"))
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])

    def test_52_run_once_writes_summary(self):
        p = os.path.join(self.d, "data", "monitor",
                         "pulse_pairs_%s.jsonl" % C.time.strftime("%Y%m%d"))
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(_emit("p1", "胃", "a.one", 9000.0)) + "\n")
            f.write(json.dumps(_recv("p1", "肝", "a.one", 9000.02)) + "\n")
        r = C.run_once(now=9001.0, root=self.d)
        self.assertTrue(r["ok"])
        rows = io.open(r["path"], encoding="utf-8").read().splitlines()
        self.assertEqual(len(rows), 1)
        rec = json.loads(rows[0])
        self.assertEqual(rec["kind"], "hourly_summary")
        self.assertEqual(rec["counts"]["cross_organ_links"], 1)

    def test_53_tick_throttled_within_same_hour(self):
        """同一小时桶内只真跑一次；且**绝不真写生产目录**（run_once 打桩）。"""
        _orig = C.run_once
        _calls = []

        def _stub(now=None, root=None, window_s=3600.0):
            _calls.append(now)
            return {"ok": True, "written": 1}

        C.run_once = _stub
        try:
            C._LAST_RUN["hour"] = None
            C._LAST_RUN["ts"] = 0.0
            a = C.tick_from_heartbeat(now=10000.0)
            b = C.tick_from_heartbeat(now=10010.0)
        finally:
            C.run_once = _orig
        self.assertNotEqual(a.get("skipped"), "same_hour")
        self.assertEqual(b.get("skipped"), "same_hour")
        self.assertEqual(len(_calls), 1, "同一小时桶内只允许真跑一次")

    def test_54_tick_swallows_errors(self):
        """观测件不得反噬主循环：run_once 抛异常时 tick 只返回 ok=False。"""
        _orig = C.run_once

        def _boom(now=None, root=None, window_s=3600.0):
            raise RuntimeError("boom")

        C.run_once = _boom
        try:
            C._LAST_RUN["hour"] = None
            C._LAST_RUN["ts"] = 0.0
            r = C.tick_from_heartbeat(now=20000.0)
        finally:
            C.run_once = _orig
        self.assertFalse(r.get("ok"))
        self.assertIn("RuntimeError", r.get("error", ""))


class TestIsolation(unittest.TestCase):
    def test_60_no_toplevel_production_import(self):
        """生产中只允许**函数内**懒加载（避免 import 期成环/加负担）。"""
        src = io.open(os.path.join(_ROOT, "organs", "core",
                                   "PulseMetricsCollector.py"), encoding="utf-8").read()
        self.assertIn("from utils.causal_chain import tick_from_heartbeat", src)
        top = [ln for ln in src.splitlines()
               if ln.startswith(("import ", "from ")) and "causal_chain" in ln]
        self.assertEqual(top, [], "不得在模块顶层 import causal_chain")


if __name__ == "__main__":
    unittest.main(verbosity=2)
