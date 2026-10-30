# -*- coding: utf-8 -*-
"""404 刀2 判据：L7 记忆回放接口（`get_causal_chain_for_event`）。

三条验收（任务书原文）对应本文件的：
* 「给一个事件名能返回上下游链」→ ``TestReplayEvent`` / ``TestReplayPulse``
* 「最多回溯 5 层」             → ``TestWalk.test_30/31/46``
* 「接口有判据」                → 本文件全量

另有两条本批自加的硬约束，都有判据钉住：
* **零丢边**：跨器官链的 `from_event` 恒等于 `event`（pairs 的语义事实），
  按裸事件名建图会把它们全当自环丢掉 ⇒ 节点必须是 `器官::事件` 复合键。
* **不漂移**：图的边计数必须与刀1 `build_strength` 的 totals 逐项相等
  （``TestConsistencyWithBuildStrength``）—— 两把尺子不一致时 ruff 与单测都看不出来。
"""
import ast
import io
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import causal_chain as cc  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "utils", "causal_chain.py")

_NOW = 1_800_000_000.0
_SEP = "::"


def _emit(organ, event, ts, pid=None, seq=0):
    return {"type": "emit", "organ": organ, "event_type": event,
            "ts": ts, "pulse_id": pid, "seq": seq}


def _recv(organ, event, ts, pid=None, seq=0):
    return {"type": "receive", "organ": organ, "event_type": event,
            "ts": ts, "pulse_id": pid, "seq": seq}


def _graph(rows, window_s=3600.0, min_count=1):
    return cc.build_event_graph(rows, _NOW, window_s, min_count=min_count)


class TestConstants(unittest.TestCase):
    def test_01_schema(self):
        self.assertEqual(cc.REPLAY_SCHEMA, "causal_replay/404.v1")

    def test_02_max_depth_is_five(self):
        """任务书钉死「最多回溯 5 层，防止无限递归」。"""
        self.assertEqual(cc.REPLAY_MAX_DEPTH, 5)

    def test_03_node_sep(self):
        self.assertEqual(cc.NODE_SEP, "::")

    def test_04_note_states_semantic_boundary(self):
        """返回体必须自带语义边界，不靠文档：跨器官边是投递，不是"产出了新事件"。"""
        self.assertIn("投递事实", cc.REPLAY_NOTE)
        self.assertIn("不是", cc.REPLAY_NOTE)


class TestNodeModel(unittest.TestCase):
    def test_10_split_node_roundtrip(self):
        self.assertEqual(cc.split_node("眼睛" + _SEP + "eyes.stream_frame"),
                         ("眼睛", "eyes.stream_frame"))

    def test_11_split_node_without_sep(self):
        self.assertEqual(cc.split_node("裸事件名"), ("裸事件名", ""))
        self.assertEqual(cc.split_node(""), ("", ""))

    def test_12_cross_organ_same_event_name_is_kept(self):
        """★核心回归：跨器官链两端事件名相同，不能被当成自环丢掉。

        真实语料里 receive 行记的是"我收到了哪个事件"，所以
        `from_event == event` 占 100%。按裸事件名建图会全丢（实测丢 211 条边，
        `眼睛->血管` 这条最大的流直接消失）。
        """
        rows = [_emit("眼睛", "eyes.stream_frame", _NOW - 10, pid="p1"),
                _recv("血管", "eyes.stream_frame", _NOW - 9.9, pid="p1")]
        _g = _graph(rows)
        self.assertEqual(_g["meta"]["cross_organ_links"], 1)
        self.assertEqual(_g["meta"]["edges"], 1)
        _k = "眼睛" + _SEP + "eyes.stream_frame"
        self.assertIn(_k, _g["out_edges"])
        self.assertEqual(_g["out_edges"][_k][0]["node"],
                         "血管" + _SEP + "eyes.stream_frame")

    def test_13_same_organ_order_edge(self):
        rows = [_emit("心脏", "heart.beat", _NOW - 10.2, seq=1),
                _emit("心脏", "heart.alive", _NOW - 10.0, seq=2)]
        _g = _graph(rows)
        _k = "心脏" + _SEP + "heart.beat"
        self.assertIn(_k, _g["out_edges"])
        self.assertEqual(_g["out_edges"][_k][0]["node"], "心脏" + _SEP + "heart.alive")
        self.assertEqual(_g["out_edges"][_k][0]["kind"], "same_organ")

    def test_14_same_organ_receive_not_cross_edge(self):
        """发送方与接收方是同一器官不算跨器官边（否则自环满地跑）。"""
        rows = [_emit("心脏", "heart.beat", _NOW - 10, pid="p1"),
                _recv("心脏", "heart.beat", _NOW - 9.9, pid="p1")]
        _g = _graph(rows)
        self.assertEqual(_g["meta"]["cross_organ_links"], 0)
        self.assertEqual(_g["meta"]["edges"], 0)

    def test_15_node_info_fields(self):
        rows = [_emit("眼睛", "x", _NOW - 10, pid="p1"),
                _recv("血管", "x", _NOW - 9.9, pid="p1"),
                _recv("视觉皮层", "x", _NOW - 9.8, pid="p1")]
        _g = _graph(rows)
        _k = "眼睛" + _SEP + "x"
        _info = _g["nodes"][_k]
        self.assertEqual(_info["organ"], "眼睛")
        self.assertEqual(_info["event"], "x")
        self.assertEqual(_info["out_deg"], 2)
        self.assertEqual(_info["in_deg"], 0)
        self.assertEqual(_g["nodes"]["血管" + _SEP + "x"]["in_deg"], 1)


class TestGraphBuild(unittest.TestCase):
    def test_20_meta_counts(self):
        rows = [_emit("眼睛", "x", _NOW - 10, pid="p1"),
                _recv("血管", "x", _NOW - 9.9, pid="p1"),
                _recv("视觉皮层", "x", _NOW - 9.8, pid="p1"),
                _emit("心脏", "b", _NOW - 8, seq=1),
                _emit("心脏", "a", _NOW - 7.9, seq=2)]
        _m = _graph(rows)["meta"]
        self.assertEqual(_m["same_organ_links"], 1)
        self.assertEqual(_m["cross_organ_links"], 2)
        self.assertEqual(_m["unmatched_receive"], 0)
        self.assertEqual(_m["duplicate_receive"], 0)

    def test_21_window_filter(self):
        """窗口外的行不入图（否则回放会混进昨天的因果）。"""
        rows = [_emit("眼睛", "x", _NOW - 4000, pid="p1"),
                _recv("血管", "x", _NOW - 3999, pid="p1")]
        self.assertEqual(_graph(rows, window_s=3600.0)["meta"]["edges"], 0)

    def test_22_min_count_filters_edge(self):
        rows = []
        for _i in range(2):
            rows.append(_emit("眼睛", "x", _NOW - 10 - _i, pid="p%d" % _i))
            rows.append(_recv("血管", "x", _NOW - 9.9 - _i, pid="p%d" % _i))
        self.assertEqual(_graph(rows, min_count=1)["meta"]["edges"], 1)
        _g = _graph(rows, min_count=3)
        self.assertEqual(_g["meta"]["edges"], 0)
        self.assertEqual(_g["meta"]["below_min_count_edges"], 1)

    def test_23_bad_rows_counted(self):
        rows = [{"type": "emit", "ts": "坏"}, "不是字典",
                _emit("眼睛", "x", _NOW - 10, pid="p1"),
                _recv("血管", "x", _NOW - 9.9, pid="p1")]
        _m = _graph(rows)["meta"]
        self.assertEqual(_m["bad_rows"], 2)
        self.assertEqual(_m["edges"], 1)

    def test_24_empty_organ_not_in_graph(self):
        rows = [_emit("", "x", _NOW - 10, seq=1),
                _emit("", "y", _NOW - 9.9, seq=2)]
        _m = _graph(rows)["meta"]
        self.assertEqual(_m["edges"], 0)
        self.assertGreaterEqual(_m["no_name_links"], 1)

    def test_25_gap_over_5s_not_cross_link(self):
        rows = [_emit("眼睛", "x", _NOW - 10, pid="p1"),
                _recv("血管", "x", _NOW - 4.9, pid="p1")]
        self.assertEqual(_graph(rows)["meta"]["cross_organ_links"], 0)

    def test_26_duplicate_receive_counted_once(self):
        """同一 (pulse_id, 接收器官) 只算一条边，重复行单独计数。"""
        rows = [_emit("眼睛", "x", _NOW - 10, pid="p1"),
                _recv("血管", "x", _NOW - 9.9, pid="p1"),
                _recv("血管", "x", _NOW - 9.8, pid="p1")]
        _m = _graph(rows)["meta"]
        self.assertEqual(_m["cross_organ_links"], 1)
        self.assertEqual(_m["duplicate_receive"], 1)
        self.assertEqual(_m["edges"], 1)

    def test_27_time_decay_buckets(self):
        """近 10 分钟的边权重 ×2（与刀1 同口径，否则两处衰减会给出不同排序）。"""
        rows = [_emit("眼睛", "x", _NOW - 100, pid="p1"),
                _recv("血管", "x", _NOW - 99, pid="p1"),
                _emit("眼睛", "x", _NOW - 3000, pid="p2"),
                _recv("血管", "x", _NOW - 2999, pid="p2")]
        _e = _graph(rows)["out_edges"]["眼睛" + _SEP + "x"][0]
        self.assertEqual(_e["count"], 2)
        self.assertEqual(_e["recent_count"], 1)
        self.assertEqual(_e["base_count"], 1)
        self.assertEqual(_e["weighted_count"],
                         round(cc.BASE_WEIGHT + cc.RECENT_WEIGHT, 2))


class TestWalk(unittest.TestCase):
    def _chain(self, _n):
        """造一条 n 个节点的直线链 ``e0 -> e1 -> ... -> e(n-1)``。

        用**同器官相邻 emit**（间隔 0.1s < 1s）串起来，而不是跨器官投递 ——
        跨器官只在"收到的事件"上出边，串不成连续链（器官收到 e0 后 emit 的是 e1，
        中间的 `O1::e0 -> O1::e1` 需要有同器官相邻 emit 才成立）。
        """
        return [_emit("A", "e%d" % _i, _NOW - 100 + _i * 0.1, seq=_i)
                for _i in range(_n)]

    def test_30_depth_limit_truncates_and_flags(self):
        _g = _graph(self._chain(10))
        _start = "A" + _SEP + "e0"
        _w = cc.walk_event_chain(_g, _start, "down", max_depth=2)
        self.assertEqual(len(_w["levels"]), 2)
        self.assertTrue(_w["truncated_depth"], "撞到上限必须显式标出来")
        _w5 = cc.walk_event_chain(_g, _start, "down", max_depth=5)
        self.assertEqual(len(_w5["levels"]), 5)
        self.assertTrue(_w5["truncated_depth"])

    def test_31_short_chain_not_truncated(self):
        _g = _graph(self._chain(3))
        _w = cc.walk_event_chain(_g, "A" + _SEP + "e0", "down", max_depth=5)
        self.assertEqual(len(_w["levels"]), 2)
        self.assertFalse(_w["truncated_depth"], "链已走完就不能报截断")

    def test_32_cycle_marked_back_edge(self):
        """A->B、B->A 同时存在时必须标 back_edge，而不是悄悄丢掉一条。"""
        rows = [_emit("A", "x", _NOW - 10.0, seq=1),
                _emit("A", "y", _NOW - 9.9, seq=2),
                _emit("A", "x", _NOW - 9.8, seq=3),
                _emit("A", "y", _NOW - 9.7, seq=4)]
        _g = _graph(rows)
        _w = cc.walk_event_chain(_g, "A" + _SEP + "x", "down", max_depth=5)
        _names = [n["node"] for _l in _w["levels"] for n in _l["events"]]
        self.assertIn("A" + _SEP + "x", _names)
        _back = [n for _l in _w["levels"] for n in _l["events"] if n["node"] == "A" + _SEP + "x"]
        self.assertTrue(_back[0]["back_edge"])
        self.assertIn("A" + _SEP + "x", _w["cycles"])

    def test_33_no_infinite_loop(self):
        rows = [_emit("A", "x", _NOW - 10.0, seq=1),
                _emit("A", "y", _NOW - 9.9, seq=2),
                _emit("A", "x", _NOW - 9.8, seq=3),
                _emit("A", "y", _NOW - 9.7, seq=4)]
        _w = cc.walk_event_chain(_graph(rows), "A" + _SEP + "x", "down", max_depth=50)
        self.assertLessEqual(len(_w["levels"]), 50)
        self.assertLessEqual(len(_w["visited"]), 2)

    def test_34_direction_aliases(self):
        rows = [_emit("A", "x", _NOW - 10.0, seq=1),
                _emit("A", "y", _NOW - 9.9, seq=2)]
        _g = _graph(rows)
        for _alias in ("up", "upstream", "back", "backward", "UP", " Upstream "):
            self.assertEqual(cc.walk_event_chain(_g, "A" + _SEP + "x", _alias)["direction"], "up")
        for _alias in ("down", "downstream", "", None):
            self.assertEqual(cc.walk_event_chain(_g, "A" + _SEP + "x", _alias)["direction"], "down")

    def test_35_breadth_cap(self):
        rows = [_emit("A", "x", _NOW - 10, pid="p0")]
        for _i in range(6):
            rows.append(_recv("R%d" % _i, "x", _NOW - 9.9, pid="p0"))
        _g = _graph(rows)
        _w = cc.walk_event_chain(_g, "A" + _SEP + "x", "down", max_depth=5,
                                 max_edges_per_node=3)
        self.assertEqual(len(_w["levels"][0]["events"]), 3)
        self.assertTrue(_w["truncated_breadth"])

    def test_36_p_direction_downstream_is_p_b_given_a(self):
        """下游的 p 是 P(子|父)；上游是 P(父层|本)。方向反了整棵树都会读错。"""
        rows = [_emit("A", "x", _NOW - 10, pid="p0")]
        for _i in range(4):
            rows.append(_recv("R%d" % _i, "x", _NOW - 9.9, pid="p0"))
        _g = _graph(rows)
        _dn = cc.walk_event_chain(_g, "A" + _SEP + "x", "down")["levels"][0]["events"][0]
        self.assertAlmostEqual(_dn["p"], 0.25, places=4)
        self.assertAlmostEqual(_dn["p_reverse"], 1.0, places=4)
        _up = cc.walk_event_chain(_g, "R0" + _SEP + "x", "up")["levels"][0]["events"][0]
        self.assertAlmostEqual(_up["p"], 1.0, places=4)
        self.assertAlmostEqual(_up["p_reverse"], 0.25, places=4)

    def test_37_unknown_node_yields_empty(self):
        _g = _graph([_emit("A", "x", _NOW - 10.0, seq=1),
                     _emit("A", "y", _NOW - 9.9, seq=2)])
        _w = cc.walk_event_chain(_g, "不存在" + _SEP + "zzz", "down")
        self.assertEqual(_w["levels"], [])
        self.assertEqual(_w["visited"], ["不存在" + _SEP + "zzz"])


class TestReplayEvent(unittest.TestCase):
    def _rows(self):
        # 眼睛 --x--> 血管 / 视觉皮层；血管 之后 emit 收缩
        rows = [_emit("眼睛", "x", _NOW - 20, pid="p0"),
                _recv("血管", "x", _NOW - 19.9, pid="p0"),
                _recv("视觉皮层", "x", _NOW - 19.8, pid="p0"),
                _emit("血管", "vaso", _NOW - 19.0, seq=9)]
        return rows

    def test_40_upstream_and_downstream_returned(self):
        _r = cc.replay_from_rows(self._rows(), "x", now=_NOW, window_s=3600.0)
        self.assertEqual(_r["empty_reason"], "")
        self.assertEqual(_r["summary"]["roots"], 3)
        _dn = {n["node"] for n in _r["downstream"]}
        self.assertIn("血管" + _SEP + "x", _dn)
        self.assertIn("视觉皮层" + _SEP + "x", _dn)
        _up = {n["node"] for n in _r["upstream"]}
        self.assertIn("眼睛" + _SEP + "x", _up)

    def test_41_roots_sorted_by_degree(self):
        _r = cc.replay_from_rows(self._rows(), "x", now=_NOW)
        self.assertEqual(_r["roots"][0]["node"], "眼睛" + _SEP + "x")
        self.assertEqual(_r["roots"][0]["out_deg"], 2)

    def test_42_bad_event_name(self):
        for _bad in ("", None, "   "):
            _r = cc.replay_from_rows(self._rows(), _bad, now=_NOW)
            self.assertEqual(_r["empty_reason"], "bad_event_name")
            self.assertEqual(_r["roots"], [])

    def test_43_event_not_in_window(self):
        _r = cc.replay_from_rows(self._rows(), "根本没这个事件", now=_NOW)
        self.assertEqual(_r["empty_reason"], "event_not_in_window")
        self.assertEqual(_r["summary"]["roots"], 0)

    def test_44_pulse_not_observed(self):
        _r = cc.replay_from_rows(self._rows(), "x", "不存在的pulse", now=_NOW)
        self.assertEqual(_r["empty_reason"], "pulse_not_observed")

    def test_45_max_depth_zero_no_chain(self):
        """max_depth=0 时起点存在但一层都不展开 ⇒ 必须报 no_chain，不能报有结果。"""
        _r = cc.replay_from_rows(self._rows(), "x", now=_NOW, max_depth=0)
        self.assertEqual(_r["summary"]["roots"], 3)
        self.assertEqual(_r["upstream"], [])
        self.assertEqual(_r["downstream"], [])
        self.assertEqual(_r["empty_reason"], "no_chain")

    def test_46_schema_and_note_present(self):
        _r = cc.replay_from_rows(self._rows(), "x", now=_NOW)
        self.assertEqual(_r["schema"], cc.REPLAY_SCHEMA)
        self.assertEqual(_r["note"], cc.REPLAY_NOTE)
        self.assertEqual(_r["max_depth"], cc.REPLAY_MAX_DEPTH)

    def test_47_summary_lists_organs_and_events(self):
        _r = cc.replay_from_rows(self._rows(), "x", now=_NOW)
        self.assertEqual(sorted(_r["summary"]["organs_involved"]),
                         sorted(["眼睛", "血管", "视觉皮层"]))
        self.assertEqual(_r["summary"]["events_involved"], ["x"])


class TestReplayPulse(unittest.TestCase):
    def _rows(self):
        return [_emit("眼睛", "x", _NOW - 20, pid="p1"),
                _recv("血管", "x", _NOW - 19.9, pid="p1"),
                _recv("视觉皮层", "x", _NOW - 19.8, pid="p1"),
                # 另一个脉冲：同器官另一条链，必须被 pulse 过滤掉
                _emit("眼睛", "x", _NOW - 15, pid="p2"),
                _recv("血管", "x", _NOW - 14.9, pid="p2"),
                _emit("血管", "zzz", _NOW - 14.8, seq=7)]

    def test_50_single_pulse_two_receivers(self):
        """单脉冲 = 1 次 emit + N 次 receive：下游就是它的实际投递路径。"""
        _r = cc.replay_from_rows(self._rows(), "x", "p1", now=_NOW)
        self.assertEqual(_r["empty_reason"], "")
        _dn = [n for n in _r["downstream"] if n["depth"] == 1]
        self.assertEqual(sorted(n["organ"] for n in _dn), sorted(["血管", "视觉皮层"]))
        for _n in _dn:
            self.assertAlmostEqual(_n["p"], 0.5, places=4,
                                   msg="一发射两接收 ⇒ 各 P=0.5（条件概率天然修正放大）")

    def test_51_pulse_filter_isolates_other_pulses(self):
        _r = cc.replay_from_rows(self._rows(), "x", "p1", now=_NOW)
        self.assertEqual(_r["graph"]["pulse_filtered_rows"], 3)
        self.assertEqual(_r["graph"]["cross_organ_links"], 2)
        self.assertNotIn("zzz", _r["summary"]["events_involved"])

    def test_52_pulse_upstream_reaches_sender(self):
        _r = cc.replay_from_rows(self._rows(), "x", "p1", now=_NOW)
        _up = {n["node"] for n in _r["upstream"]}
        self.assertIn("眼睛" + _SEP + "x", _up)

    def test_53_pulse_id_int_coerced(self):
        _rows = [_emit("眼睛", "x", _NOW - 20, pid=12345),
                 _recv("血管", "x", _NOW - 19.9, pid=12345)]
        _r = cc.replay_from_rows(_rows, "x", 12345, now=_NOW)
        self.assertEqual(_r["empty_reason"], "")
        self.assertEqual(_r["summary"]["downstream_nodes"], 1)


class TestMergeLevels(unittest.TestCase):
    def test_60_min_depth_kept(self):
        _a = {"levels": [{"depth": 2, "events": [{"node": "N", "depth": 2, "count": 1}]}]}
        _b = {"levels": [{"depth": 1, "events": [{"node": "N", "depth": 1, "count": 9}]}]}
        _m = cc._merge_levels([_a, _b], set())
        self.assertEqual(len(_m), 1)
        self.assertEqual(_m[0]["depth"], 1)

    def test_61_root_back_edge_dropped(self):
        _a = {"levels": [{"depth": 1, "events": [
            {"node": "R", "depth": 1, "count": 1, "back_edge": True},
            {"node": "B", "depth": 1, "count": 2, "back_edge": False}]}]}
        _m = cc._merge_levels([_a], {"R"})
        self.assertEqual([n["node"] for n in _m], ["B"])

    def test_62_sorted_by_depth(self):
        _a = {"levels": [
            {"depth": 2, "events": [{"node": "深", "depth": 2, "count": 5}]},
            {"depth": 1, "events": [{"node": "浅", "depth": 1, "count": 1}]}]}
        _m = cc._merge_levels([_a], set())
        self.assertEqual([n["node"] for n in _m], ["浅", "深"])


class TestConsistencyWithBuildStrength(unittest.TestCase):
    """★防止刀1 与刀2 两套链口径漂移（ruff 与单测都看不出这类漂移）。"""

    def _rows(self):
        rows = []
        for _i in range(6):
            rows.append(_emit("眼睛", "x", _NOW - 60 + _i, pid="p%d" % _i))
            rows.append(_recv("血管", "x", _NOW - 59.9 + _i, pid="p%d" % _i))
            rows.append(_recv("视觉皮层", "x", _NOW - 59.8 + _i, pid="p%d" % _i))
            rows.append(_emit("眼睛", "x", _NOW - 59.7 + _i, pid="q%d" % _i))
        rows += [_emit("心脏", "b", _NOW - 30 + _i, seq=_i) for _i in range(3)]
        rows += [_emit("心脏", "a", _NOW - 29.9 + _i, seq=100 + _i) for _i in range(3)]
        rows += [_emit("孤儿", "z", _NOW - 20, pid="nope")]
        return rows

    def test_70_same_organ_links_match(self):
        _rows = self._rows()
        self.assertEqual(_graph(_rows)["meta"]["same_organ_links"],
                         cc.build_strength(_rows, _NOW, 3600.0, top_n=10 ** 9)[
                             "totals"]["same_organ_links"])

    def test_71_cross_organ_links_match(self):
        _rows = self._rows()
        self.assertEqual(_graph(_rows)["meta"]["cross_organ_links"],
                         cc.build_strength(_rows, _NOW, 3600.0, top_n=10 ** 9)[
                             "totals"]["cross_organ_links"])

    def test_72_unmatched_receive_match(self):
        _rows = self._rows()
        self.assertEqual(_graph(_rows)["meta"]["unmatched_receive"],
                         cc.build_strength(_rows, _NOW, 3600.0, top_n=10 ** 9)[
                             "totals"]["unmatched_receive"])

    def test_73_duplicate_receive_match_build_chains(self):
        _rows = self._rows()
        self.assertEqual(_graph(_rows)["meta"]["duplicate_receive"],
                         cc.build_chains(_rows)["counts"]["duplicate_receive"])


class TestDiskEntry(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m404k2_")
        self._dir = os.path.join(self._td, "data", "monitor")
        os.makedirs(self._dir, exist_ok=True)
        self._day = time.strftime("%Y%m%d")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def _write_pairs(self, rows):
        _p = os.path.join(self._dir, "pulse_pairs_%s.jsonl" % self._day)
        with io.open(_p, "w", encoding="utf-8", newline="\n") as _f:
            for _r in rows:
                _f.write(json.dumps(_r, ensure_ascii=False) + "\n")
        return _p

    def test_80_missing_pairs(self):
        _r = cc.get_causal_chain_for_event("x", root=self._td,
                                           now=time.time(), include_history=False)
        self.assertEqual(_r["empty_reason"], "event_not_in_window")
        self.assertEqual(_r["source"]["reason"], "file_missing")
        self.assertFalse(_r["source"]["exists"])

    def test_81_reads_tmp_pairs(self):
        _now = time.time()
        self._write_pairs([_emit("眼睛", "x", _now - 20, pid="p1"),
                           _recv("血管", "x", _now - 19.9, pid="p1")])
        _r = cc.get_causal_chain_for_event("x", root=self._td, now=_now,
                                           include_history=False)
        self.assertEqual(_r["empty_reason"], "")
        self.assertEqual(_r["source"]["rows"], 2)
        self.assertEqual([n["node"] for n in _r["downstream"]], ["血管" + _SEP + "x"])

    def test_82_no_new_files_written(self):
        """任务书：「不新增文件」。读一次回放前后目录清单必须一模一样。"""
        _now = time.time()
        self._write_pairs([_emit("眼睛", "x", _now - 20, pid="p1"),
                           _recv("血管", "x", _now - 19.9, pid="p1")])
        _before = sorted(os.listdir(self._dir))
        cc.get_causal_chain_for_event("x", root=self._td, now=_now)
        self.assertEqual(sorted(os.listdir(self._dir)), _before)

    def test_83_history_from_hourly_summary(self):
        _now = time.time()
        self._write_pairs([_emit("眼睛", "x", _now - 20, pid="p1"),
                           _recv("血管", "x", _now - 19.9, pid="p1")])
        _s = {"kind": "hourly_summary", "hour": "2026-10-30T16",
              "same_organ": {"top": [{"from": "x", "to": "y", "n": 7}]},
              "cross_organ": {"top": [{"event": "x", "n": 9}]}}
        _p = os.path.join(self._dir, "causal_chains_%s.jsonl" % self._day)
        with io.open(_p, "w", encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps(_s, ensure_ascii=False) + "\n")
        _r = cc.get_causal_chain_for_event("x", root=self._td, now=_now)
        self.assertEqual(_r["history"]["files"], 1)
        self.assertEqual(_r["history"]["mentions"], 7 + 9)
        self.assertEqual(_r["history"]["hours"], ["2026-10-30T16"])

    def test_84_history_absent(self):
        _now = time.time()
        self._write_pairs([_emit("眼睛", "x", _now - 20, pid="p1")])
        _r = cc.get_causal_chain_for_event("x", root=self._td, now=_now)
        self.assertEqual(_r["history"]["files"], 0)
        self.assertEqual(_r["history"]["reason"], "no_summary_file")

    def test_85_render_does_not_raise(self):
        _now = time.time()
        self._write_pairs([_emit("眼睛", "x", _now - 20, pid="p1"),
                           _recv("血管", "x", _now - 19.9, pid="p1")])
        _r = cc.get_causal_chain_for_event("x", root=self._td, now=_now,
                                           include_history=False)
        _txt = cc.render_chain_tree(_r)
        self.assertIn("眼睛" + _SEP + "x", _txt)
        self.assertIn("P(本|经)", _txt)

    def test_86_render_empty_ok(self):
        _txt = cc.render_chain_tree({"event": "x", "empty_reason": "bad_event_name"})
        self.assertIn("bad_event_name", _txt)


class TestSourceHygiene(unittest.TestCase):
    def _tree(self):
        with io.open(_SRC, encoding="utf-8") as _f:
            _t = _f.read()
        return _t, ast.parse(_t)

    def test_90_every_except_logs(self):
        _t, _tree = self._tree()
        _miss = []
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.ExceptHandler):
                _seg = ast.get_source_segment(_t, _n) or ""
                if "silent_exc" not in _seg and "logger" not in _seg:
                    _miss.append(_n.lineno)
        self.assertEqual(_miss, [], "★每个 except 必须留痕")

    def test_91_no_print_in_replay_block(self):
        _t, _tree = self._tree()
        _i = _t.find("# ===== 404 刀2：L7 记忆回放接口 =====")
        self.assertGreater(_i, 0)
        _j = _t.find('if __name__ == "__main__":')
        _seg = _t[_i:_j]
        for _n in ast.walk(ast.parse("if 1:\n" + "\n".join(
                "    " + _l for _l in _seg.splitlines()))):
            if isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name) \
                    and _n.func.id == "print":
                self.fail("回放段不得 print（接口只返回结果）")

    def test_92_no_write_open_in_replay(self):
        """回放段只读：不得出现写模式 open（「不新增文件」的机器判据）。"""
        _t, _tree = self._tree()
        _i = _t.find("# ===== 404 刀2：L7 记忆回放接口 =====")
        _j = _t.find('if __name__ == "__main__":')
        _seg = _t[_i:_j]
        for _bad in ('"w"', "'w'", '"a"', "'a'", '"wb"', "os.replace",
                     "os.makedirs", "open(_p, \"w"):
            self.assertNotIn(_bad, _seg, "回放段不得写盘：%s" % _bad)

    def test_93_replay_functions_present(self):
        _t, _ = self._tree()
        for _fn in ("split_node", "build_event_graph", "walk_event_chain",
                    "replay_from_rows", "get_causal_chain_for_event", "render_chain_tree"):
            self.assertIn("def %s(" % _fn, _t, "缺函数 %s" % _fn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
