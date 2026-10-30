# -*- coding: utf-8 -*-
"""404 刀1 判据：L4 因果强度判定（频次 / 条件概率 / 时间衰减）。

口径与 403 刀1 的 `build_chains` 严格一致，本文件用**交叉断言**防止两套实现漂移
（第60批 §60.6 的教训：两把尺子不一致时 ruff 与单测都看不出来）。
"""
import ast
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import causal_chain as cc  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _emit(organ, event, ts, pid=None, seq=0):
    return {"type": "emit", "organ": organ, "event_type": event,
            "ts": ts, "pulse_id": pid, "seq": seq}


def _recv(organ, event, ts, pid=None, seq=0):
    return {"type": "receive", "organ": organ, "event_type": event,
            "ts": ts, "pulse_id": pid, "seq": seq}


_NOW = 1_800_000_000.0


class TestConstants(unittest.TestCase):
    def test_01_strength_schema(self):
        self.assertEqual(cc.STRENGTH_SCHEMA, "causal_strength/404.v1")

    def test_02_weights_and_window(self):
        self.assertEqual(cc.RECENT_WINDOW_S, 600.0)
        self.assertEqual(cc.RECENT_WEIGHT, 2.0)
        self.assertEqual(cc.BASE_WEIGHT, 1.0)

    def test_03_hook_name_unique(self):
        self.assertEqual(cc.STRENGTH_HOOK, "causal_strength_daily")


class TestFrequency(unittest.TestCase):
    def test_10_count_basic(self):
        rows = [_emit("A", "e1", _NOW - 300 + i, pid="p%d" % i, seq=i) for i in range(3)]
        rows += [_emit("A", "e2", _NOW - 300 + i + 0.1, pid="q%d" % i, seq=i) for i in range(3)]
        st = cc.build_strength(rows, _NOW)
        _row = [r for r in st["same_organ"] if r["from"] == "e1" and r["to"] == "e2"]
        self.assertEqual(len(_row), 1)
        self.assertEqual(_row[0]["count"], 3)

    def test_11_gap_equal_1s_is_not_chain(self):
        """gap 恰好 1.0s 不算链（开区间，与 build_chains 同口径）。"""
        rows = [_emit("A", "e1", _NOW - 300.0, seq=0),
                _emit("A", "e2", _NOW - 299.0, seq=1)]
        st = cc.build_strength(rows, _NOW)
        self.assertEqual(st["totals"]["same_organ_links"], 0)

    def test_12_not_adjacent_not_chain(self):
        """非相邻（中间隔一个 emit）不构成链。"""
        rows = [_emit("A", "e1", _NOW - 300.0, seq=0),
                _emit("A", "eX", _NOW - 299.8, seq=1),
                _emit("A", "e2", _NOW - 299.6, seq=2)]
        st = cc.build_strength(rows, _NOW)
        _pairs = {(r["from"], r["to"]) for r in st["same_organ"]}
        self.assertNotIn(("e1", "e2"), _pairs)
        self.assertIn(("e1", "eX"), _pairs)
        self.assertIn(("eX", "e2"), _pairs)

    def test_13_row_before_window_ignored(self):
        rows = [_emit("A", "e1", _NOW - cc.WINDOW_S - 10, seq=0),
                _emit("A", "e2", _NOW - cc.WINDOW_S - 9, seq=1)]
        st = cc.build_strength(rows, _NOW)
        self.assertEqual(st["totals"]["emit"], 0)


class TestConditionalProbability(unittest.TestCase):
    def test_20_p_b_given_a(self):
        """A 出 3 条链（2 到 B、1 到 C）⇒ P(B|A)=2/3。"""
        rows = [
            _emit("O", "A", _NOW - 500.0, seq=0), _emit("O", "B", _NOW - 499.8, seq=1),
            _emit("O", "A", _NOW - 400.0, seq=2), _emit("O", "B", _NOW - 399.8, seq=3),
            _emit("O", "A", _NOW - 300.0, seq=4), _emit("O", "C", _NOW - 299.8, seq=5),
        ]
        st = cc.build_strength(rows, _NOW)
        _ab = [r for r in st["same_organ"] if r["from"] == "A" and r["to"] == "B"][0]
        self.assertEqual(_ab["count"], 2)
        self.assertEqual(_ab["p_b_given_a"], 0.6667)

    def test_21_p_a_given_b(self):
        """B 的入链只来自 A ⇒ P(A|B)=1.0（另一条 C→E 与 B 无关）。"""
        rows = [
            _emit("O", "A", _NOW - 500.0, seq=0), _emit("O", "B", _NOW - 499.8, seq=1),
            _emit("O", "C", _NOW - 400.0, seq=2), _emit("O", "E", _NOW - 399.8, seq=3),
        ]
        st = cc.build_strength(rows, _NOW)
        _ab = [r for r in st["same_organ"] if r["from"] == "A" and r["to"] == "B"][0]
        self.assertEqual(_ab["p_a_given_b"], 1.0)

    def test_22_zero_denominator_returns_none(self):
        """分母为 0 ⇒ None（没观测到 ≠ 概率 0）。"""
        self.assertIsNone(cc._ratio(0, 0))
        self.assertIsNone(cc._ratio(1, 0))

    def test_23_ratio_rounding(self):
        self.assertEqual(cc._ratio(1, 3), 0.3333)
        self.assertEqual(cc._ratio(2, 2), 1.0)

    def test_24_cross_organ_one_emit_two_receivers(self):
        """一次 emit 被两个器官接收 ⇒ 两条跨器官链，各 P=0.5（分母=发送器官出链数）。"""
        rows = [
            _emit("眼", "eyes.frame", _NOW - 300.0, pid="p1", seq=0),
            _recv("血管", "eyes.frame", _NOW - 299.99, pid="p1", seq=1),
            _recv("视皮", "eyes.frame", _NOW - 299.98, pid="p1", seq=2),
        ]
        st = cc.build_strength(rows, _NOW)
        self.assertEqual(st["totals"]["cross_organ_links"], 2)
        for _r in st["cross_organ"]:
            self.assertEqual(_r["p_b_given_a"], 0.5)


class TestTimeDecay(unittest.TestCase):
    def test_30_recent_weighted_double(self):
        """早段 1 条（×1）+ 近段 2 条（×2）⇒ weighted_count = 5。"""
        rows = [_emit("O", "A", _NOW - 1800.0, seq=0), _emit("O", "B", _NOW - 1799.8, seq=1),
                _emit("O", "A", _NOW - 300.0, seq=2), _emit("O", "B", _NOW - 299.8, seq=3),
                _emit("O", "A", _NOW - 200.0, seq=4), _emit("O", "B", _NOW - 199.8, seq=5)]
        st = cc.build_strength(rows, _NOW)
        _ab = [r for r in st["same_organ"] if r["from"] == "A" and r["to"] == "B"][0]
        self.assertEqual(_ab["count"], 3)
        self.assertEqual(_ab["base_count"], 1)
        self.assertEqual(_ab["recent_count"], 2)
        self.assertEqual(_ab["weighted_count"], 5.0)

    def test_31_all_recent(self):
        rows = [_emit("O", "A", _NOW - 300.0, seq=0), _emit("O", "B", _NOW - 299.8, seq=1)]
        st = cc.build_strength(rows, _NOW)
        _ab = [r for r in st["same_organ"] if r["from"] == "A"][0]
        self.assertEqual(_ab["weighted_count"], 2.0)

    def test_32_all_base(self):
        rows = [_emit("O", "A", _NOW - 3000.0, seq=0), _emit("O", "B", _NOW - 2999.8, seq=1)]
        st = cc.build_strength(rows, _NOW)
        _ab = [r for r in st["same_organ"] if r["from"] == "A"][0]
        self.assertEqual(_ab["weighted_count"], 1.0)

    def test_33_sorted_by_weighted_count(self):
        rows = []
        # 链1：3 条全在早段 ⇒ w=3
        for i in range(3):
            rows += [_emit("O", "A", _NOW - 3000.0 + i, seq=i * 2),
                     _emit("O", "B", _NOW - 2999.8 + i, seq=i * 2 + 1)]
        # 链2：2 条全在近段 ⇒ w=4
        for i in range(2):
            rows += [_emit("O", "C", _NOW - 300.0 + i, seq=100 + i * 2),
                     _emit("O", "D", _NOW - 299.8 + i, seq=101 + i * 2)]
        st = cc.build_strength(rows, _NOW)
        self.assertEqual(st["same_organ"][0]["from"], "C")
        self.assertEqual(st["same_organ"][0]["weighted_count"], 4.0)


class TestConsistencyWithBuildChains(unittest.TestCase):
    """★交叉断言：两套实现必须同口径（漂移时 ruff 与单测都看不出来）。"""

    def _rows(self):
        rows = []
        for i in range(5):
            rows += [_emit("A", "e1", _NOW - 1000.0 + i, pid="s%d" % i, seq=i * 2),
                     _emit("A", "e2", _NOW - 999.7 + i, pid="t%d" % i, seq=i * 2 + 1)]
        for i in range(4):
            rows += [_emit("B", "x1", _NOW - 500.0 + i, pid="c%d" % i, seq=100 + i),
                     _recv("C", "x1", _NOW - 499.9 + i, pid="c%d" % i, seq=200 + i)]
        return rows

    def test_40_same_links_match(self):
        _r = self._rows()
        _st = cc.build_strength(_r, _NOW)
        _ch = cc.build_chains(_r)
        self.assertEqual(_st["totals"]["same_organ_links"], _ch["counts"]["same_organ_links"])
        self.assertGreater(_st["totals"]["same_organ_links"], 0)

    def test_41_cross_links_match(self):
        _r = self._rows()
        _st = cc.build_strength(_r, _NOW)
        _ch = cc.build_chains(_r)
        self.assertEqual(_st["totals"]["cross_organ_links"], _ch["counts"]["cross_organ_links"])
        self.assertGreater(_st["totals"]["cross_organ_links"], 0)

    def test_42_unmatched_match(self):
        _r = self._rows() + [_recv("Z", "ghost", _NOW - 100.0, pid="nope", seq=999)]
        _st = cc.build_strength(_r, _NOW)
        _ch = cc.build_chains(_r)
        self.assertEqual(_st["totals"]["unmatched_receive"], _ch["counts"]["unmatched_receive"])


class TestWriteContract(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m404k1_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_50_output_path_shape(self):
        _p = cc.strength_output_path(day="20261030", root=self._td)
        self.assertTrue(_p.endswith(os.path.join("data", "monitor", "causal_strength_20261030.json")))
        self.assertFalse(os.path.exists(_p))

    def test_51_write_json_atomic_pure_lf(self):
        _p = os.path.join(self._td, "s.json")
        self.assertTrue(cc._write_json_atomic({"a": 1, "中文": "值"}, _p))
        _raw = io.open(_p, "rb").read()
        self.assertEqual(_raw.count(b"\r\n"), 0, "★原子写必须纯 LF（不进 Windows CRLF）")
        self.assertIn("中文".encode("utf-8"), _raw)

    def test_52_run_strength_daily_writes(self):
        _pairs = os.path.join(self._td, "pulse_pairs_20261030.jsonl")
        _rows = [_emit("O", "A", _NOW - 300.0, seq=0), _emit("O", "B", _NOW - 299.8, seq=1)]
        with io.open(_pairs, "w", encoding="utf-8", newline="\n") as f:
            for _r in _rows:
                f.write(json.dumps(_r, ensure_ascii=False) + "\n")
        with mock.patch.object(cc, "pairs_path", return_value=_pairs):
            _res = cc.run_strength_daily(now=_NOW, root=self._td, force=True)
        self.assertTrue(_res["ok"])
        self.assertEqual(_res["written"], 1)
        with io.open(_res["path"], encoding="utf-8") as f:
            _rec = json.load(f)
        self.assertEqual(_rec["schema"], cc.STRENGTH_SCHEMA)
        self.assertEqual(_rec["kind"], "daily_strength")
        self.assertIn("totals", _rec)
        self.assertIn("rule", _rec)
        self.assertEqual(_rec["same_organ"][0]["count"], 1)

    def test_53_run_strength_daily_skips_recent(self):
        _pairs = os.path.join(self._td, "pulse_pairs_20261030.jsonl")
        with io.open(_pairs, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(_emit("O", "A", _NOW - 300.0, seq=0)) + "\n")
        with mock.patch.object(cc, "pairs_path", return_value=_pairs):
            _r1 = cc.run_strength_daily(now=_NOW, root=self._td, force=True)
            self.assertTrue(_r1["ok"])
            _r2 = cc.run_strength_daily(now=_NOW + 60, root=self._td)
        self.assertEqual(_r2.get("skipped"), "recent", "★同日一小时内必须跳过（不重复写）")

    def test_54_force_overrides_skip(self):
        _pairs = os.path.join(self._td, "pulse_pairs_20261030.jsonl")
        with io.open(_pairs, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(_emit("O", "A", _NOW - 300.0, seq=0)) + "\n")
        with mock.patch.object(cc, "pairs_path", return_value=_pairs):
            cc.run_strength_daily(now=_NOW, root=self._td, force=True)
            _r2 = cc.run_strength_daily(now=_NOW + 60, root=self._td, force=True)
        self.assertEqual(_r2["written"], 1, "force 必须覆盖")

    def test_55_stale_file_is_rewritten(self):
        """文件年龄超过 STRENGTH_MIN_GAP_S ⇒ 允许重写（判重用墙钟，不是传入的 now）。"""
        _pairs = os.path.join(self._td, "pulse_pairs_20261030.jsonl")
        with io.open(_pairs, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(_emit("O", "A", _NOW - 300.0, seq=0)) + "\n")
        with mock.patch.object(cc, "pairs_path", return_value=_pairs):
            _r1 = cc.run_strength_daily(now=_NOW, root=self._td, force=True)
            # 把落盘文件 mtime 往前推（模拟"当天早些时候已写过一份"）
            _old = time.time() - (cc.STRENGTH_MIN_GAP_S + 600)
            os.utime(_r1["path"], (_old, _old))
            _r2 = cc.run_strength_daily(now=_NOW + 60, root=self._td)
        self.assertEqual(_r2["written"], 1)


class TestRegister(unittest.TestCase):
    def test_60_register_with_scheduler(self):
        _hooks = {}

        class _S:
            def add_daily_hook(self, name, fn):
                _hooks[name] = fn
                return name

        _s = cc.register_causal_strength_schedule(_S())
        self.assertIsNotNone(_s)
        self.assertIn(cc.STRENGTH_HOOK, _hooks)

    def test_61_register_without_hook_support(self):
        class _Bad:
            pass

        self.assertIsNone(cc.register_causal_strength_schedule(_Bad()))

    def test_62_register_swallows_errors(self):
        class _Boom:
            def add_daily_hook(self, name, fn):
                raise RuntimeError("boom")

        self.assertIsNone(cc.register_causal_strength_schedule(_Boom()))


class TestWiring(unittest.TestCase):
    def _main_src(self):
        return io.open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()

    def test_70_main_calls_register(self):
        _src = self._main_src()
        self.assertEqual(_src.count("register_causal_strength_schedule()"), 1,
                         "★main.py 必须恰好挂一次（多挂会重复注册钩子）")

    def test_71_main_imports_from_utils(self):
        _src = self._main_src()
        self.assertIn("from utils.causal_chain import register_causal_strength_schedule", _src)

    def test_72_wiring_inside_daily_schedule_try(self):
        """挂点必须在既有每日调度 try 内（与 organ_emits 相邻）。"""
        _src = self._main_src()
        _i = _src.find("register_organ_emits_schedule()")
        _j = _src.find("register_causal_strength_schedule()")
        self.assertGreater(_i, 0)
        self.assertGreater(_j, _i)
        self.assertLess(_j - _i, 800, "挂点应紧邻 organ_emits（同挂点）")


class TestSourceHygiene(unittest.TestCase):
    def test_80_no_bare_except_pass(self):
        _p = os.path.join(_ROOT, "utils", "causal_chain.py")
        _t = io.open(_p, encoding="utf-8").read()
        _tree = ast.parse(_t)
        _bad = []
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.ExceptHandler) and len(_n.body) == 1 \
                    and isinstance(_n.body[0], ast.Pass):
                _bad.append(_n.lineno)
        self.assertEqual(_bad, [], "★静默 except: pass 一律禁止（留痕或让异常上抛）")

    def test_81_every_except_logs(self):
        _p = os.path.join(_ROOT, "utils", "causal_chain.py")
        _t = io.open(_p, encoding="utf-8").read()
        _tree = ast.parse(_t)
        _miss = []
        for _n in ast.walk(_tree):
            if isinstance(_n, ast.ExceptHandler):
                _seg = ast.get_source_segment(_t, _n) or ""
                if "silent_exc" not in _seg and "logger" not in _seg:
                    _miss.append(_n.lineno)
        self.assertEqual(_miss, [], "★每个 except 必须留痕")

    def test_82_strength_block_has_no_warning_calls(self):
        """任务书要求「只计算不告警」：强度段内不得有 logger.warning/error。"""
        _p = os.path.join(_ROOT, "utils", "causal_chain.py")
        _t = io.open(_p, encoding="utf-8").read()
        _i = _t.find("# ===== 404 刀1：L4 因果强度判定 =====")
        self.assertGreater(_i, 0)
        _seg = _t[_i:]
        for _fn in ("_write_json_atomic", "run_strength_daily", "build_strength"):
            _j = _seg.find("def %s(" % _fn)
            self.assertGreater(_j, 0, "缺函数 %s" % _fn)
        self.assertNotIn("logger.warning", _seg.split("def register_causal_strength_schedule")[0],
                         "强度计算段不得告警（只计算不告警）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
