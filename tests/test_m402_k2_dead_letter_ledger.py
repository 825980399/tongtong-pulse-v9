# -*- coding: utf-8 -*-
"""402 刀2 判据：死信三列台账（结构面 / 路由面 / 运行面并列，不合成一条）。

★这组测试守的是「三列不许塌成一列」这件事本身：
  1. 每条事件行必须**同时**带 subscribed / routed / delivered 三列，且各自可独立为假；
  2. routed 为 None（未发布，判不了）不得被当成 False —— 那会把"没发过"算成"路由坏"；
  3. delivered=False 不许等价于"投递故障"：窗口内没发射只是未接线，
     真丢件由 lost_in_flight（发了却没投递）单独标；
  4. 每批必落一条 tick_summary —— 这是"每 5 分钟一条"的硬保证；
  5. 事件行按状态变化稀疏落盘，稳定期不得每批重写几百行。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_MOD = os.path.join(_ROOT, "utils", "dead_letter_ledger.py")


def _load():
    spec = importlib.util.spec_from_file_location("m402_dead_letter_ledger", _MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _load()


def _fake_routed(mapping):
    return lambda: mapping


def _fake_traffic(emits=None, receives=None):
    _e = dict(emits or {})
    _r = dict(receives or {})
    return lambda: {"emits": _e, "receives": _r,
                    "distinct_emitted": len(_e), "distinct_received": len(_r)}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m402k2_")
        self.path = os.path.join(self.tmp, "dead_letters_test.jsonl")
        L.reset_state()
        self._orig = (L.face_routed, L.face_delivered, L.face_subscribed,
                      L.face_dead_letters, L.const_events)

    def tearDown(self):
        (L.face_routed, L.face_delivered, L.face_subscribed,
         L.face_dead_letters, L.const_events) = self._orig
        L.reset_state()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch(self, routed=None, emits=None, receives=None, consts=None,
               subs=None, dead_letters=None):
        L.face_routed = _fake_routed(routed or {})
        L.face_delivered = _fake_traffic(emits, receives)
        L.const_events = (lambda: set(consts)) if consts is not None else (lambda: set())
        L.face_subscribed = (lambda events: {e: bool((subs or {}).get(e, False))
                                             for e in events})
        L.face_dead_letters = (lambda: dict(dead_letters or {}))

    def _rows(self):
        return [r for r in L.read_ledger(self.path) if r.get("kind") == "event"]

    def _summary(self):
        return [r for r in L.read_ledger(self.path) if r.get("kind") == "tick_summary"]


class TestThreeColumns(_Base):
    """三列必须并存，且各自独立。"""

    def test_01_three_columns_present(self):
        self._patch(routed={"a.one": {"published": 3, "has_real_consumer": True}},
                    emits={"a.one": 5}, receives={"a.one": 4}, consts=["a.one"])
        L.tick(force=True, path=self.path)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        for f in ("event", "ts", "subscribed", "routed", "delivered", "dead_axes"):
            self.assertIn(f, r, "事件行缺列 %s" % f)
        self.assertEqual(r["event"], "a.one")

    def test_02_columns_do_not_collapse(self):
        """★核心：有订阅、有路由、却零投递 —— 三列必须各说各话。"""
        self._patch(routed={"x.y": {"published": 9, "has_real_consumer": True}},
                    emits={"x.y": 9}, receives={}, consts=["x.y"], subs={"x.y": True})
        L.tick(force=True, path=self.path)
        r = self._rows()[0]
        self.assertIs(r["subscribed"], True)
        self.assertIs(r["routed"], True)
        self.assertIs(r["delivered"], False)
        self.assertEqual(r["dead_axes"], ["delivered"], "只有投递面是假的")
        self.assertTrue(r["lost_in_flight"], "发了 9 次零接收 ⇒ 真丢件")

    def test_03_no_emit_is_not_delivery_fault(self):
        """窗口内没发射 ⇒ delivered=False，但**不算丢件**。"""
        self._patch(routed={"x.y": {"published": 0, "has_real_consumer": None}},
                    emits={}, receives={}, consts=["x.y"], subs={"x.y": True})
        L.tick(force=True, path=self.path)
        r = self._rows()[0]
        self.assertIs(r["delivered"], False)
        self.assertFalse(r["lost_in_flight"], "没发射就没投递，不是投递故障")
        self.assertIsNone(r["routed"], "未发布时路由面应报「判不了」而不是 False")

    def test_04_routed_unknown_not_false(self):
        """★routed=None 必须原样保留：把"没发过"算成"路由坏"会虚增故障面。"""
        self._patch(routed={"p.q": {"published": 0, "has_real_consumer": None}},
                    consts=["p.q"])
        L.tick(force=True, path=self.path)
        r = self._rows()[0]
        self.assertIsNone(r["routed"])
        self.assertNotIn("routed", r["dead_axes"])

    def test_05_dead_axes_accumulates(self):
        """三面全假 ⇒ dead_axes 三列齐全（不是合成一个布尔）。"""
        self._patch(routed={"d.e": {"published": 2, "has_real_consumer": False}},
                    emits={"d.e": 2}, receives={}, consts=["d.e"], subs={"d.e": False})
        L.tick(force=True, path=self.path)
        r = self._rows()[0]
        self.assertEqual(sorted(r["dead_axes"]), ["delivered", "routed", "subscribed"])
        s = self._summary()[0]
        self.assertEqual(s["counts"]["dead_all_three"], 1)


class TestCadence(_Base):
    """节奏与稀疏落盘。"""

    def test_10_gate_blocks_before_interval(self):
        self._patch(consts=["a.b"])
        self.assertIsNotNone(L.tick(now=1000.0, path=self.path), "首批应放行")
        r = L.tick(now=1001.0, path=self.path)
        self.assertIsNone(r, "未到 300s 必须零 IO 直接返回 None")
        self.assertFalse(os.path.exists(self.path) and self._summary()[1:],
                         "不得落下第二条批次行")

    def test_11_gate_allows_after_interval(self):
        self._patch(consts=["a.b"])
        L.tick(now=1000.0, path=self.path)
        r = L.tick(now=1300.0, path=self.path)
        self.assertIsNotNone(r, "满 300s 应放行")
        self.assertEqual(len(self._summary()), 2)

    def test_12_force_ignores_gate(self):
        self._patch(consts=["a.b"])
        L.tick(now=1000.0, path=self.path)
        self.assertIsNotNone(L.tick(now=1001.0, force=True, path=self.path))

    def test_13_summary_every_tick(self):
        """★每批必有 tick_summary —— 「台账停更」与「无变化」必须能区分。"""
        self._patch(consts=["a.b"], receives={"a.b": 1})
        for _i in range(3):
            L.tick(force=True, path=self.path)
        self.assertEqual(len(self._summary()), 3)

    def test_14_sparse_when_stable(self):
        """状态不变 ⇒ 事件行不重复写（只写批次行），否则一天几十 MB。

        注意别拿「本窗口收到过投递」的事件来测稀疏：第二批它的 delivered 会因
        窗口增量归零而**合理地**翻成 False（见 test_21），那不是重写而是状态变化。
        """
        self._patch(consts=["c.d"])
        r1 = L.tick(force=True, path=self.path)
        r2 = L.tick(force=True, path=self.path)
        self.assertEqual(r1["written"], 1)
        self.assertEqual(r2["written"], 0, "稳定期不得重写事件行")
        self.assertEqual(len(self._rows()), 1)
        self.assertEqual(len(self._summary()), 2)

    def test_15_state_change_rewrites(self):
        """状态变化 ⇒ 该事件行必须重新落一条（否则台账会漏掉复活/恶化）。"""
        self._patch(consts=["a.b"], receives={"a.b": 1})
        L.tick(force=True, path=self.path)
        L.face_delivered = _fake_traffic({}, {})   # 投递消失
        L.tick(force=True, path=self.path)
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertIs(rows[0]["delivered"], True)
        self.assertIs(rows[1]["delivered"], False)

    def test_16_include_all_forces_rows(self):
        self._patch(consts=["a.b"], receives={"a.b": 1})
        L.tick(force=True, path=self.path)
        r = L.tick(force=True, path=self.path, include_all=True)
        self.assertEqual(r["written"], 1)


class TestWindowDelta(_Base):
    """窗口增量 = 累计量相减（事件环只有 200 槽，跨不过 5 分钟）。"""

    def test_20_delta_positive_only(self):
        self.assertEqual(L._delta({"a": 5}, {"a": 2}), {"a": 3})
        self.assertEqual(L._delta({"a": 5}, {"a": 5}), {})
        self.assertEqual(L._delta({"a": 1}, {"a": 9}), {}, "计数器重置不得产生负增量")

    def test_21_second_tick_uses_window(self):
        """第二批必须用窗口增量：累计 5 次但本窗口 0 次 ⇒ delivered=False。"""
        self._patch(consts=["a.b"], receives={"a.b": 5})
        L.tick(force=True, path=self.path)
        L.tick(force=True, path=self.path)      # 累计未变 ⇒ 窗口增量 0
        rows = self._rows()
        self.assertIs(rows[0]["delivered"], True)
        self.assertIs(rows[-1]["delivered"], False)
        self.assertEqual(rows[-1]["detail"]["receives_window"], 0)
        self.assertEqual(rows[-1]["detail"]["baseline"], "window")


class TestUniverse(_Base):
    """事件全集与裸字符串事件。"""

    def test_30_const_events_real_import(self):
        """常量全集走运行时导入：本项目应有 180+ 个事件值。"""
        ev = L.const_events()
        self.assertGreaterEqual(len(ev), 180, "const.py 的事件值数量")
        self.assertIn("heart.beat", ev)
        self.assertIn("chat.message", ev)

    def test_31_runtime_only_event_included(self):
        """★只在运行时出现（无常量）的裸字符串事件也要进台账。"""
        self._patch(routed={"snapshot.auto_save": {"published": 4,
                                                   "has_real_consumer": True}},
                    consts=["a.b"])
        L.tick(force=True, path=self.path)
        by = {r["event"]: r for r in self._rows()}
        self.assertIn("snapshot.auto_save", by)
        self.assertFalse(by["snapshot.auto_save"]["detail"]["has_constant"])
        self.assertEqual(self._summary()[0]["counts"]["no_constant"], 1)

    def test_32_face_failure_degrades(self):
        """某面取不到读数 ⇒ 其余面照常，不许整批失败。"""
        self._patch(consts=["a.b"], receives={"a.b": 2})

        def _boom():
            raise RuntimeError("模拟 InfoField 不可用")

        L.face_routed = _boom
        r = L.tick(force=True, path=self.path)
        self.assertIsNotNone(r, "单面异常不得让整批失败")
        row = self._rows()[0]
        self.assertIsNone(row["routed"])


class TestIO(_Base):
    """落盘契约。"""

    def test_40_lines_are_valid_json_with_ts(self):
        self._patch(consts=["a.b", "c.d"], receives={"a.b": 1})
        L.tick(force=True, path=self.path)
        with io.open(self.path, encoding="utf-8") as f:
            for ln in f:
                rec = json.loads(ln)
                self.assertIn("ts", rec)
                self.assertIn("kind", rec)
                self.assertIn(rec["kind"], ("event", "tick_summary"))

    def test_41_append_not_overwrite(self):
        self._patch(consts=["a.b"])
        L.tick(force=True, path=self.path)
        L.tick(force=True, path=self.path)
        self.assertEqual(len(self._summary()), 2, "同一文件必须追加而不是覆盖")

    def test_42_path_daily_rotation(self):
        p1 = L.ledger_path(now=1793334627.0)
        self.assertRegex(os.path.basename(p1), r"^dead_letters_\d{8}\.jsonl$")
        self.assertIn(os.path.join("data", "monitor"), p1)

    def test_43_read_ledger_skips_bad_lines(self):
        with io.open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write('{"kind": "event", "ts": "x"}\n')
            f.write("{坏行\n")
        self.assertEqual(len(L.read_ledger(self.path)), 1)

    def test_44_production_hook_exists(self):
        """生产挂载点必须直通 tick()（PulseMetricsCollector 每拍调用它）。"""
        self.assertTrue(callable(getattr(L, "tick_from_heartbeat", None)))
        _orig = L.tick
        _calls = []
        L.tick = lambda: _calls.append(1)
        try:
            L.tick_from_heartbeat()
        finally:
            L.tick = _orig
        self.assertEqual(_calls, [1], "挂载点必须直通 tick()，不得自己另写一套")


if __name__ == "__main__":
    unittest.main()
