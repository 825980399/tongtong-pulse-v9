# -*- coding: utf-8 -*-
"""404 刀3 判据：七层自我分析体系自检面板（`tools/m404_layer_health_check.py`）。

本文件钉住三类东西：
1. **三态语义**：PASS / FAIL / SKIP 各自由谁触发；SKIP 绝不冒充 PASS，也绝不被吞成 PASS
   （停机时 L5 判 FAIL 就是最典型的假警报，本文件 must_skip 那条专门守它）。
2. **阈值来源**：L1 的 409 是**下限**、L3 的 cap 与 L5 的 90s 都必须**从源码静态解析**
   （不 import 生产单例），解析不到才回落常量 —— 防"配置改了判据没跟着改"的静默漂移。
3. **退出码**：0 无 FAIL / 1 有 FAIL / 2 `--strict` 下有 SKIP。CI 只认退出码。
"""
import ast
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402
from tools import m404_layer_health_check as lh  # noqa: E402

_SRC = os.path.join(_ROOT, "tools", "m404_layer_health_check.py")


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as _f:
        if isinstance(obj, str):
            _f.write(obj)
        else:
            _f.write(json.dumps(obj, ensure_ascii=False))


def _mkroot():
    return tempfile.mkdtemp(prefix="m404lh_")


def _ts(y, m, d, hh, mm=0):
    return time.mktime((y, m, d, hh, mm, 0, 0, 0, -1))


class TestConstants(unittest.TestCase):
    def test_01_schema_and_version(self):
        self.assertEqual(lh.SCHEMA, "layer_health/404.v1")
        self.assertEqual(lh.CRITERIA_VERSION, "404.layer_health.v1")

    def test_02_l1_floor_is_lower_bound_not_exact(self):
        """409 是下限（铁律 9：钉死精确值会被"新增一个正常文件"顶红）。"""
        self.assertEqual(lh.L1_MIN_FILES, 409)

    def test_03_fallback_thresholds(self):
        self.assertEqual(lh.DEFAULT_PAIRS_CAP, 500000)
        self.assertEqual(lh.DEFAULT_SNAPSHOT_MAX_AGE_S, 90.0)
        self.assertEqual(lh.TASK_LITERAL_STALE_S, 5.0)

    def test_04_seven_layers_in_order(self):
        self.assertEqual([c[0] for c in lh.CHECKS],
                         ["L1", "L2", "L3", "L4", "L5", "L6", "L7"])

    def test_05_three_statuses_only(self):
        self.assertEqual({lh.PASS, lh.FAIL, lh.SKIP}, {"PASS", "FAIL", "SKIP"})


class TestHelpers(unittest.TestCase):
    def setUp(self):
        self._td = _mkroot()

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_10_int_safe(self):
        self.assertEqual(lh._int("12"), 12)
        self.assertEqual(lh._int(None), 0)
        self.assertEqual(lh._int("坏"), 0)

    def test_11_age_none_when_missing(self):
        """没有读数 ≠ 读数为 0：取不到年龄必须返回 None。"""
        self.assertIsNone(lh._age(os.path.join(self._td, "不存在"), time.time()))

    def test_12_age_positive(self):
        _p = os.path.join(self._td, "a.txt")
        _write(_p, "x")
        _a = lh._age(_p, time.time())
        self.assertIsNotNone(_a)
        self.assertGreaterEqual(_a, 0.0)

    def test_13_count_lines_skips_blank(self):
        _p = os.path.join(self._td, "a.jsonl")
        _write(_p, "a\n\nb\n   \nc\n")
        self.assertEqual(lh._count_lines(_p), 3)

    def test_14_count_lines_missing(self):
        self.assertIsNone(lh._count_lines(os.path.join(self._td, "没")))

    def test_15_load_json_bad(self):
        _p = os.path.join(self._td, "bad.json")
        _write(_p, "{不是json")
        self.assertIsNone(lh._load_json(_p))

    def test_16_read_const_from_source(self):
        _p = os.path.join(self._td, "m.py")
        _write(_p, "X = 1.5\nOTHER = 2\n")
        self.assertEqual(lh._read_const(_p, "X", float), (1.5, 1))
        self.assertEqual(lh._read_const(_p, "OTHER", int), (2, 2))

    def test_17_read_const_absent(self):
        _p = os.path.join(self._td, "m.py")
        _write(_p, "A = 1\n")
        self.assertEqual(lh._read_const(_p, "NOPE", float), (None, None))


class TestCapAndThresholdSources(unittest.TestCase):
    """阈值必须来自源码静态解析，不能是"抄一遍数字"。"""

    def setUp(self):
        self._td = _mkroot()

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_20_pairs_cap_from_real_config(self):
        _cap, _src = lh.read_pairs_cap(_ROOT)
        self.assertEqual(_cap, 500000)
        self.assertTrue(_src.startswith("config.py:"), "阈值应带行号来源，实际 %r" % _src)

    def test_21_pairs_cap_from_tmp_config(self):
        _write(os.path.join(self._td, "config.py"),
               "RUNTIME_PARAMS = {\n    'pulse_pair_file_cap': 1234,\n}\n")
        _cap, _src = lh.read_pairs_cap(self._td)
        self.assertEqual(_cap, 1234)
        self.assertTrue(_src.startswith("config.py:"))

    def test_25_pairs_cap_nonpositive_falls_back(self):
        """★与 utils/pulse_tracer._pair_file_cap 同语义：<=0 属无效配置 → 回落 50 万。"""
        _write(os.path.join(self._td, "config.py"),
               "RUNTIME_PARAMS = {\n    'pulse_pair_file_cap': 0,\n}\n")
        self.assertEqual(lh.read_pairs_cap(self._td), (lh.DEFAULT_PAIRS_CAP, "default"))
        _write(os.path.join(self._td, "config.py"),
               "RUNTIME_PARAMS = {\n    'pulse_pair_file_cap': -5,\n}\n")
        self.assertEqual(lh.read_pairs_cap(self._td), (lh.DEFAULT_PAIRS_CAP, "default"))
        _write(os.path.join(self._td, "config.py"),
               "RUNTIME_PARAMS = {\n    'pulse_pair_file_cap': True,\n}\n")
        self.assertEqual(lh.read_pairs_cap(self._td), (lh.DEFAULT_PAIRS_CAP, "default"))

    def test_22_pairs_cap_missing_file_falls_back(self):
        _cap, _src = lh.read_pairs_cap(self._td)
        self.assertEqual((_cap, _src), (lh.DEFAULT_PAIRS_CAP, "default"))

    def test_23_snapshot_max_age_from_real_module(self):
        _v, _src = lh.read_snapshot_max_age(_ROOT)
        self.assertEqual(_v, 90.0)
        self.assertIn("external_heartbeat.py:", _src)

    def test_24_snapshot_max_age_override(self):
        _write(os.path.join(self._td, "nucleus", "world_model", "external_heartbeat.py"),
               "DEFAULT_MAX_AGE_SEC = 42.0\n")
        _v, _src = lh.read_snapshot_max_age(self._td)
        self.assertEqual(_v, 42.0)
        self.assertIn("external_heartbeat.py:", _src)


class TestL1(unittest.TestCase):
    def setUp(self):
        self._td = _mkroot()
        self._p = os.path.join(self._td, "data", "call_graph", "graph_latest.json")
        self._ctx = lh.make_context(root=self._td, now=time.time(), framework_running=False)

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _graph(self, **kw):
        _st = {"total_files": 412, "parsed_files": 412, "parse_errors": 0,
               "total_functions": 7976, "total_calls": 78933, "cross_file_calls": 8921}
        _st.update(kw)
        _write(self._p, {"stats": _st})

    def test_30_missing_fail(self):
        _r = lh.check_l1(self._ctx)
        self.assertEqual(_r["status"], lh.FAIL)

    def test_31_bad_json_fail(self):
        _write(self._p, "{坏")
        self.assertEqual(lh.check_l1(self._ctx)["status"], lh.FAIL)

    def test_32_files_below_floor_fail(self):
        self._graph(total_files=380, parsed_files=380)
        _r = lh.check_l1(self._ctx)
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertIn("380", _r["detail"])

    def test_33_files_equal_floor_pass(self):
        self._graph(total_files=lh.L1_MIN_FILES, parsed_files=lh.L1_MIN_FILES)
        self.assertEqual(lh.check_l1(self._ctx)["status"], lh.PASS)

    def test_34_parse_errors_fail(self):
        self._graph(parse_errors=3)
        self.assertEqual(lh.check_l1(self._ctx)["status"], lh.FAIL)

    def test_35_partial_parse_fail(self):
        self._graph(parsed_files=400)
        _r = lh.check_l1(self._ctx)
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertIn("400", _r["detail"])

    def test_36_ok_pass_with_evidence(self):
        self._graph()
        _r = lh.check_l1(self._ctx)
        self.assertEqual(_r["status"], lh.PASS)
        self.assertEqual(_r["evidence"]["total_files"], 412)


class TestL2(unittest.TestCase):
    def setUp(self):
        self._td = _mkroot()
        self._p = os.path.join(self._td, "data", "contracts", "event_dictionary.json")
        self._ctx = lh.make_context(root=self._td, now=time.time(), framework_running=False)

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _dict(self, ue=185, ce=205):
        _write(self._p, {"schema": "event_dictionary/402.v1",
                         "totals": {"unique_events": ue, "constant_entries": ce,
                                    "emitted_in_prod": 142, "never_emitted": 43}})

    def test_40_missing_fail(self):
        self.assertEqual(lh.check_l2(self._ctx)["status"], lh.FAIL)

    def test_41_zero_entries_fail(self):
        self._dict(ue=0, ce=0)
        self.assertEqual(lh.check_l2(self._ctx)["status"], lh.FAIL)

    def test_42_unique_more_than_constants_fail(self):
        """唯一事件数不可能多于常量条目数 —— 口径自相矛盾就是字典坏了。"""
        self._dict(ue=300, ce=205)
        _r = lh.check_l2(self._ctx)
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertIn("矛盾", _r["detail"])

    def test_43_ok_pass(self):
        self._dict()
        _r = lh.check_l2(self._ctx)
        self.assertEqual(_r["status"], lh.PASS)
        self.assertEqual(_r["evidence"]["unique_events"], 185)


class TestL3(unittest.TestCase):
    def setUp(self):
        self._td = _mkroot()
        self._now = time.time()
        self._day = time.strftime("%Y%m%d", time.localtime(self._now))
        self._p = os.path.join(self._td, "data", "monitor",
                               "pulse_pairs_%s.jsonl" % self._day)

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _ctx(self, **kw):
        return lh.make_context(root=self._td, now=self._now, framework_running=False, **kw)

    def test_50_missing_is_skip_not_fail(self):
        """本层只判容量；文件不存在属"落盘断没断"，是别的层的判据 ⇒ SKIP。"""
        _r = lh.check_l3(self._ctx())
        self.assertEqual(_r["status"], lh.SKIP)
        self.assertEqual(_r["reason"], "pairs_file_missing")

    def test_51_under_cap_pass(self):
        _write(self._p, "".join('{"a":%d}\n' % _i for _i in range(10)))
        _r = lh.check_l3(self._ctx(pairs_cap=100))
        self.assertEqual(_r["status"], lh.PASS)
        self.assertEqual(_r["evidence"]["rows"], 10)
        self.assertEqual(_r["evidence"]["usage_ratio"], 0.1)

    def test_52_over_cap_fail(self):
        _write(self._p, "".join('{"a":%d}\n' % _i for _i in range(10)))
        _r = lh.check_l3(self._ctx(pairs_cap=5))
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertIn("10", _r["detail"])

    def test_53_cap_defaults_from_config(self):
        """不给 --pairs-cap 时必须走 config.py 静态解析，而不是硬编码。"""
        _write(os.path.join(self._td, "config.py"),
               "RUNTIME_PARAMS = {\n    'pulse_pair_file_cap': 3,\n}\n")
        _write(self._p, "".join('{"a":%d}\n' % _i for _i in range(10)))
        _r = lh.check_l3(self._ctx())
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertEqual(_r["evidence"]["cap"], 3)
        self.assertTrue(_r["evidence"]["cap_source"].startswith("config.py:"))

    def test_54_yesterday_fallback(self):
        _y = time.strftime("%Y%m%d", time.localtime(self._now - 86400))
        _py = os.path.join(self._td, "data", "monitor", "pulse_pairs_%s.jsonl" % _y)
        _write(_py, '{"a":1}\n')
        _r = lh.check_l3(self._ctx(pairs_cap=100))
        self.assertEqual(_r["status"], lh.PASS)
        self.assertEqual(_r["evidence"]["day"], _y)


class TestL4(unittest.TestCase):
    def setUp(self):
        self._td = _mkroot()

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _ctx(self, _now):
        return lh.make_context(root=self._td, now=_now, framework_running=False)

    def _path(self, _now):
        return os.path.join(self._td, "data", "monitor",
                            "causal_chains_%s.jsonl"
                            % time.strftime("%Y%m%d", time.localtime(_now)))

    def test_60_missing_before_one_am_is_skip(self):
        _now = _ts(2026, 10, 30, 0, 30)
        _r = lh.check_l4(self._ctx(_now))
        self.assertEqual(_r["status"], lh.SKIP)
        self.assertEqual(_r["reason"], "day_too_young")

    def test_61_missing_after_one_am_is_fail(self):
        _now = _ts(2026, 10, 30, 15, 0)
        _r = lh.check_l4(self._ctx(_now))
        self.assertEqual(_r["status"], lh.FAIL)

    def test_62_file_without_summary_fail(self):
        _now = _ts(2026, 10, 30, 15, 0)
        _write(self._path(_now), '{"kind":"other"}\n')
        self.assertEqual(lh.check_l4(self._ctx(_now))["status"], lh.FAIL)

    def test_63_ok_pass_with_latest_hour(self):
        _now = _ts(2026, 10, 30, 15, 0)
        _rows = [{"kind": "hourly_summary", "hour": "2026-10-30T14", "complete": True},
                 {"kind": "hourly_summary", "hour": "2026-10-30T13", "complete": False}]
        _write(self._path(_now), "\n".join(json.dumps(r) for r in _rows) + "\n")
        _r = lh.check_l4(self._ctx(_now))
        self.assertEqual(_r["status"], lh.PASS)
        self.assertEqual(_r["evidence"]["summaries"], 2)
        self.assertEqual(_r["evidence"]["latest_hour"], "2026-10-30T14")
        self.assertEqual(_r["evidence"]["stale_hours"], 1)
        self.assertEqual(_r["evidence"]["incomplete"], 1)

    def test_64_bad_lines_skipped(self):
        _now = _ts(2026, 10, 30, 15, 0)
        _write(self._path(_now),
               '{坏行\n' + json.dumps({"kind": "hourly_summary", "hour": "2026-10-30T14"}) + "\n")
        self.assertEqual(lh.check_l4(self._ctx(_now))["status"], lh.PASS)


class TestL5(unittest.TestCase):
    def setUp(self):
        self._td = _mkroot()
        self._p = os.path.join(self._td, "data", "monitor", "health_snapshot.json")
        self._now = _ts(2026, 10, 30, 15, 0)

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _snap(self, _age_s):
        _write(self._p, {"timestamp": self._now - _age_s, "beat_count": 21, "pid": 999})
        try:
            os.utime(self._p, (self._now - _age_s, self._now - _age_s))
        except OSError as _e:
            # Windows 上刚写完的文件可能被占用，改不动 mtime 就用 payload timestamp 兜底
            silent_exc(_e, where="tests.test_m404_k3_layer_health.TestL5._snap", level="debug")

    def _ctx(self, **kw):
        kw.setdefault("framework_running", True)
        return lh.make_context(root=self._td, now=self._now, **kw)

    def test_70_missing_is_skip(self):
        _r = lh.check_l5(self._ctx())
        self.assertEqual(_r["status"], lh.SKIP)
        self.assertEqual(_r["reason"], "snapshot_unavailable")

    def test_71_framework_down_skips_even_if_stale(self):
        """★本批最重要的一条：停机时快照必然旧，判 FAIL 就是假警报。"""
        self._snap(4000)
        _r = lh.check_l5(self._ctx(framework_running=False))
        self.assertEqual(_r["status"], lh.SKIP)
        self.assertEqual(_r["reason"], "framework_down")

    def test_72_psutil_unavailable_skips(self):
        self._snap(10)
        _r = lh.check_l5(self._ctx(framework_running=None))
        self.assertEqual(_r["status"], lh.SKIP)
        self.assertEqual(_r["reason"], "psutil_unavailable")

    def test_73_running_and_fresh_pass(self):
        self._snap(12)
        _r = lh.check_l5(self._ctx())
        self.assertEqual(_r["status"], lh.PASS)
        self.assertEqual(_r["evidence"]["max_age_s"], 90.0)
        self.assertFalse(_r["evidence"]["strict_5s"], "12s 不满足任务书字面 5s 口径")

    def test_74_running_and_stale_fail(self):
        self._snap(400)
        _r = lh.check_l5(self._ctx())
        self.assertEqual(_r["status"], lh.FAIL)

    def test_75_task_literal_5s_mode(self):
        self._snap(8)
        _r = lh.check_l5(self._ctx(snapshot_5s=True))
        self.assertEqual(_r["evidence"]["max_age_s"], 5.0)
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertFalse(_r["evidence"]["strict_5s"])

    def test_76_task_literal_5s_mode_pass(self):
        self._snap(3)
        _r = lh.check_l5(self._ctx(snapshot_5s=True))
        self.assertEqual(_r["status"], lh.PASS)
        self.assertTrue(_r["evidence"]["strict_5s"])

    def test_77_age_takes_more_conservative_of_two(self):
        """文件刚被 touch 但内容旧 ⇒ 必须按内容 age 判（防"touch 一下就算新鲜"）。"""
        self._snap(0)
        _write(self._p, {"timestamp": self._now - 500, "beat_count": 21})
        _r = lh.check_l5(self._ctx())
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertGreaterEqual(_r["evidence"]["age_s"], 499)

    def test_78_max_age_cli_overrides(self):
        self._snap(30)
        _r = lh.check_l5(self._ctx(snapshot_max_age=10.0))
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertEqual(_r["evidence"]["max_age_source"], "cli")


class TestL6(unittest.TestCase):
    def setUp(self):
        self._td = _mkroot()
        self._p = os.path.join(self._td, "data", "monitor", "health_snapshot_hourly.json")
        self._ctx = lh.make_context(root=self._td, now=time.time(), framework_running=False)

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _snap(self, **kw):
        _d = {"schema": "snapshot_tiers/403.v1", "warm": {"organs": {"total": 66}},
              "cold": {"knowledge_evolution": {}}, "warm_age_s": 0.1, "cold_age_s": 62.5}
        _d.update(kw)
        _write(self._p, _d)

    def test_80_missing_fail(self):
        self.assertEqual(lh.check_l6(self._ctx)["status"], lh.FAIL)

    def test_81_wrong_schema_fail(self):
        self._snap(schema="别的东西/1.0")
        self.assertEqual(lh.check_l6(self._ctx)["status"], lh.FAIL)

    def test_82_missing_warm_fail(self):
        self._snap(warm=None)
        self.assertEqual(lh.check_l6(self._ctx)["status"], lh.FAIL)

    def test_83_both_empty_fail(self):
        self._snap(warm={}, cold={})
        _r = lh.check_l6(self._ctx)
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertIn("都是空", _r["detail"])

    def test_84_ok_pass(self):
        self._snap()
        _r = lh.check_l6(self._ctx)
        self.assertEqual(_r["status"], lh.PASS)
        self.assertTrue(_r["evidence"]["has_cold"])


class TestL7(unittest.TestCase):
    def setUp(self):
        self._td = _mkroot()
        self._p = os.path.join(self._td, "data", "monitor", "health_snapshot.json")
        self._ctx = lh.make_context(root=self._td, now=time.time(), framework_running=False)

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _snap(self, _hb):
        _write(self._p, {"heartbeat": _hb, "beat_count": 21})

    def test_90_missing_snapshot_skip(self):
        _r = lh.check_l7(self._ctx)
        self.assertEqual(_r["status"], lh.SKIP)
        self.assertEqual(_r["reason"], "snapshot_unavailable")

    def test_91_field_absent_fail(self):
        self._snap({"interval": 11.0})
        _r = lh.check_l7(self._ctx)
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertIn("未接线", _r["detail"])

    def test_92_not_list_fail(self):
        self._snap({"prev_beat_events": "heart.beat"})
        self.assertEqual(lh.check_l7(self._ctx)["status"], lh.FAIL)

    def test_93_bad_element_fail(self):
        self._snap({"prev_beat_events": ["heart.beat", None]})
        self.assertEqual(lh.check_l7(self._ctx)["status"], lh.FAIL)

    def test_94_count_mismatch_fail(self):
        self._snap({"prev_beat_events": ["a", "b"], "prev_beat_event_count": 3})
        _r = lh.check_l7(self._ctx)
        self.assertEqual(_r["status"], lh.FAIL)
        self.assertIn("不一致", _r["detail"])

    def test_95_count_consistent_pass(self):
        self._snap({"prev_beat_events": ["a", "b"], "prev_beat_event_count": 2,
                    "prev_beat_span_s": 16.065})
        _r = lh.check_l7(self._ctx)
        self.assertEqual(_r["status"], lh.PASS)
        self.assertEqual(_r["evidence"]["events_len"], 2)

    def test_96_empty_list_pass(self):
        """空列表是合法的（上一拍没有事件），不能当"没接线"。"""
        self._snap({"prev_beat_events": [], "prev_beat_event_count": 0})
        self.assertEqual(lh.check_l7(self._ctx)["status"], lh.PASS)

    def test_97_top_level_fallback(self):
        _write(self._p, {"prev_beat_events": ["a"], "beat_count": 1})
        _r = lh.check_l7(self._ctx)
        self.assertEqual(_r["status"], lh.PASS)
        self.assertEqual(_r["evidence"]["field"], "prev_beat_events")


class TestAggregate(unittest.TestCase):
    """整体结论：有 FAIL ⇒ FAIL；只有 SKIP ⇒ PARTIAL；全 PASS 才 PASS。"""

    def setUp(self):
        self._td = _mkroot()
        # 00:30 建上下文：L4 因"当天首个小时没过完"必 SKIP，L5 因框架停机必 SKIP
        self._now = _ts(2026, 10, 30, 0, 30)

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _ctx(self, **kw):
        kw.setdefault("framework_running", False)
        return lh.make_context(root=self._td, now=self._now, **kw)

    def _populate(self):
        """补齐 5 层（L1/L2/L3/L6/L7）的合法落盘，留 L4/L5 走 SKIP。"""
        _m = os.path.join(self._td, "data", "monitor")
        _write(os.path.join(self._td, "data", "call_graph", "graph_latest.json"),
               {"stats": {"total_files": 412, "parsed_files": 412, "parse_errors": 0,
                          "total_functions": 7976, "total_calls": 78933}})
        _write(os.path.join(self._td, "data", "contracts", "event_dictionary.json"),
               {"schema": "event_dictionary/402.v1",
                "totals": {"unique_events": 185, "constant_entries": 205}})
        _write(os.path.join(_m, "pulse_pairs_20261030.jsonl"), '{"a":1}\n{"a":2}\n')
        _write(os.path.join(_m, "health_snapshot.json"),
               {"heartbeat": {"prev_beat_events": ["a", "b"], "prev_beat_event_count": 2},
                "beat_count": 21})
        _write(os.path.join(_m, "health_snapshot_hourly.json"),
               {"schema": "snapshot_tiers/403.v1", "warm": {"organs": {}},
                "cold": {"knowledge_evolution": {}}})

    def test_a0_empty_root_has_fails(self):
        _res = lh.run_all(self._ctx())
        self.assertEqual(_res["overall"], lh.FAIL)
        self.assertEqual(_res["exit_code"], 1)
        _layers = {c["layer"] for c in _res["checks"] if c["status"] == lh.FAIL}
        self.assertEqual(_layers, {"L1", "L2", "L6"}, "空目录应只有落盘类三层 FAIL")

    def test_a1_fail_wins_over_skip(self):
        _res = lh.run_all(self._ctx())
        self.assertGreaterEqual(_res["counts"]["fail"], 1)
        self.assertGreaterEqual(_res["counts"]["skip"], 1)
        self.assertEqual(_res["overall"], lh.FAIL, "有 FAIL 时不能被 SKIP 稀释成 PARTIAL")

    def test_a2_partial_when_only_skip(self):
        """★5 层 PASS + 2 层 SKIP ⇒ PARTIAL 且 exit 0（停机期间提交不该被挡）。"""
        self._populate()
        _res = lh.run_all(self._ctx())
        self.assertEqual(_res["counts"], {"pass": 5, "fail": 0, "skip": 2, "total": 7})
        self.assertEqual(_res["overall"], "PARTIAL")
        self.assertEqual(_res["exit_code"], 0)
        _skip = {c["layer"]: c["reason"] for c in _res["checks"] if c["status"] == lh.SKIP}
        self.assertEqual(_skip, {"L4": "day_too_young", "L5": "framework_down"})

    def test_a3_strict_makes_skip_fail(self):
        self._populate()
        _res = lh.run_all(self._ctx(strict=True))
        self.assertEqual(_res["overall"], "PARTIAL")
        self.assertEqual(_res["exit_code"], 2)

    def test_a4_all_pass_when_framework_running(self):
        """7 层全 PASS：把 L4/L5 也补齐（L4 造一条汇总、L5 换 15:00 + 框架在跑）。"""
        self._populate()
        _now = _ts(2026, 10, 30, 15, 0)
        _write(os.path.join(self._td, "data", "monitor", "causal_chains_20261030.jsonl"),
               json.dumps({"kind": "hourly_summary", "hour": "2026-10-30T14"}) + "\n")
        _p = os.path.join(self._td, "data", "monitor", "health_snapshot.json")
        _write(_p, {"timestamp": _now - 3,
                    "heartbeat": {"prev_beat_events": ["a", "b"], "prev_beat_event_count": 2},
                    "beat_count": 21})
        os.utime(_p, (_now - 3, _now - 3))
        _res = lh.run_all(lh.make_context(root=self._td, now=_now, framework_running=True))
        self.assertEqual(_res["counts"], {"pass": 7, "fail": 0, "skip": 0, "total": 7})
        self.assertEqual(_res["overall"], lh.PASS)
        self.assertEqual(_res["exit_code"], 0)

    def test_a5_counts_add_up(self):
        _res = lh.run_all(self._ctx())
        _k = _res["counts"]
        self.assertEqual(_k["total"], 7)
        self.assertEqual(_k["pass"] + _k["fail"] + _k["skip"], 7)


class TestCli(unittest.TestCase):
    def setUp(self):
        self._td = _mkroot()

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _run(self, *args):
        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            _code = lh.main(list(args))
        return _code, _buf.getvalue()

    def test_b0_json_output_parsable(self):
        _code, _out = self._run("--root", self._td, "--json", "--layer", "L3")
        _d = json.loads(_out)
        self.assertEqual(_d["schema"], lh.SCHEMA)
        self.assertEqual([c["layer"] for c in _d["checks"]], ["L3"])
        self.assertEqual(_code, 0)

    def test_b1_layer_filter(self):
        _code, _out = self._run("--root", self._td, "--layer", "L1,L2")
        self.assertIn("L1", _out)
        self.assertIn("L2", _out)
        self.assertNotIn("L6", _out)

    def test_b2_fail_exit_code(self):
        _write(os.path.join(self._td, "data", "monitor",
                            "pulse_pairs_%s.jsonl" % time.strftime("%Y%m%d")),
               '{"a":1}\n{"a":2}\n')
        _code, _ = self._run("--root", self._td, "--layer", "L3", "--pairs-cap", "1")
        self.assertEqual(_code, 1)

    def test_b5_nonpositive_cap_rejected(self):
        """cap<=0 在 config 侧是"无效→回落 50 万"，CLI 侧必须直接拒绝，不能静默改值。"""
        with self.assertRaises(SystemExit):
            lh.main(["--root", self._td, "--pairs-cap", "0"])

    def test_b3_report_has_summary_line(self):
        _code, _out = self._run("--root", self._td, "--layer", "L4")
        self.assertIn("整体=", _out)
        self.assertIn("criteria=", _out)

    def test_b4_skip_reason_printed(self):
        _code, _out = self._run("--root", self._td, "--layer", "L3")
        self.assertIn("skip_reason=pairs_file_missing", _out)


class TestSourceHygiene(unittest.TestCase):
    def _tree(self):
        with io.open(_SRC, encoding="utf-8") as _f:
            _t = _f.read()
        return _t, ast.parse(_t)

    def test_c0_every_except_logs(self):
        _t, _tree = self._tree()
        _miss = []
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.ExceptHandler):
                _seg = ast.get_source_segment(_t, _n) or ""
                if "silent_exc" not in _seg and "logger" not in _seg:
                    _miss.append(_n.lineno)
        self.assertEqual(_miss, [], "★每个 except 必须留痕")

    def test_c1_no_bare_except_pass(self):
        _t, _tree = self._tree()
        _bad = []
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.ExceptHandler) and len(_n.body) == 1 \
                    and isinstance(_n.body[0], ast.Pass):
                _bad.append(_n.lineno)
        self.assertEqual(_bad, [])

    def test_c2_tool_is_read_only(self):
        """自检面板不得写文件（`--json` 只到 stdout，不落盘）。"""
        _t, _tree = self._tree()
        _bad = []
        for _n in ast.walk(_tree):
            if not isinstance(_n, ast.Call):
                continue
            _f = _n.func
            if isinstance(_f, ast.Name) and _f.id == "open":
                _mode = ""
                if len(_n.args) >= 2 and isinstance(_n.args[1], ast.Constant):
                    _mode = str(_n.args[1].value)
                if any(_ch in _mode for _ch in "wax+"):
                    _bad.append(_n.lineno)
        self.assertEqual(_bad, [], "★只读面板不得以写模式 open")

    def test_c3_no_write_helpers(self):
        _t, _ = self._tree()
        for _bad in ("os.replace", "json.dump(", "os.makedirs", "shutil.copy"):
            self.assertNotIn(_bad, _t, "只读面板不得出现 %s" % _bad)

    def test_c4_no_production_singleton_import(self):
        """不在线 import config：阈值走 AST 静态解析（与 gate_health_check 惯例同构）。"""
        _t, _tree = self._tree()
        _mods = []
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.Import):
                _mods += [_a.name.split(".")[0] for _a in _n.names]
            elif isinstance(_n, ast.ImportFrom) and _n.module:
                _mods.append(_n.module.split(".")[0])
        self.assertNotIn("config", _mods)
        self.assertNotIn("organs", _mods)
        self.assertTrue(any(_m == "nucleus" for _m in _mods), "运行判据复用 nucleus 的 psutil 判定")


class TestHookWiring(unittest.TestCase):
    """CI 接线：真实入口是 `.git/hooks/pre-commit`（任务书写的 `gates_run.py` 不存在）。

    ★这个 hook **未纳入版本管理**（`tools/ci/hooks/` 只版本化了 commit-msg），
    所以用例在文件不存在时跳过；存在就钉住三件事：已接入、受 SKIP_CODE_GATES 管辖、
    默认**不阻断**（把运行时判据做成阻断门等于"停机维护期间禁止提交"）。
    """

    _HOOK = os.path.join(_ROOT, ".git", "hooks", "pre-commit")

    @unittest.skipUnless(os.path.exists(os.path.join(_ROOT, ".git", "hooks", "pre-commit")),
                         "pre-commit hook 未安装（未版本化，跳过）")
    def test_d0_hook_calls_layer_health(self):
        with io.open(self._HOOK, encoding="utf-8") as _f:
            _t = _f.read()
        self.assertIn("tools/m404_layer_health_check.py", _t)

    @unittest.skipUnless(os.path.exists(os.path.join(_ROOT, ".git", "hooks", "pre-commit")),
                         "pre-commit hook 未安装（未版本化，跳过）")
    def test_d1_hook_default_is_non_blocking(self):
        with io.open(self._HOOK, encoding="utf-8") as _f:
            _t = _f.read()
        self.assertIn("LAYER_HEALTH_GATE:-warn", _t,
                      "默认必须是 warn：L4/L5 是运行时判据，停机时会 FAIL")
        self.assertIn('LAYER_HEALTH_GATE:-warn}" = "block"', _t,
                      "阻断必须由显式开关触发")


if __name__ == "__main__":
    unittest.main(verbosity=2)
