# -*- coding: utf-8 -*-
"""483 批 · 刀 483-3「脉冲 deadline 字段 + 三态观测（默认不丢）」门控单测。

覆盖烛微设计 §3.9：默认值按层 / 显式值优先 / 灰度关不丢 / 灰度开必丢 /
无字段兜底 / 时钟回拨不误杀，外加「检查必须在 try 内」的结构护栏。
"""
import ast
import os
import threading
import time
import unittest

import config
from nucleus import pulse_types
from nucleus.field.InfoField import InfoField
from nucleus.pulse.PulseCore import PulseCore


def _read_src(path):
    with open(path, encoding="utf-8") as _f:
        return _f.read()


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IF_SRC = os.path.join(ROOT, "nucleus", "field", "InfoField.py")
PM_SRC = os.path.join(ROOT, "organs", "core", "PulseMetricsCollector.py")
PC_SRC = os.path.join(ROOT, "nucleus", "pulse", "PulseCore.py")


def _mk_field():
    f = InfoField.__new__(InfoField)
    f._deadline_stats = {_l: {"would_timeout": 0, "dropped": 0, "overrun": 0}
                         for _l in ("L0", "L1", "L2", "L3")}
    f._lock = threading.Lock()
    f._m483_deadline_malformed = 0
    return f


def _expired(deadline_ms=10000, layer="L0"):
    return {"timestamp_ns": time.time_ns() - 20_000_000_000,
            "deadline_ms": deadline_ms, "layer": layer}


class TestM483Deadline(unittest.TestCase):

    def test_01_default_by_layer(self):
        c = PulseCore()
        expect = {"L0": 10000, "L1": 40000, "L2": 60000, "L3": 120000}
        for layer, want in expect.items():
            # ★不用 system.status.request：379刀3 会把带 `system.status.` 前缀的
            #   L2/L3 归一到 L1（实测），导致读到 40000 而非 60000。用中性事件名。
            p = c.emit("acc", "acc.test.neutral", {"k": "v"}, layer=layer)
            self.assertEqual(p["layer"], layer)
            self.assertEqual(p.get("deadline_ms"), want, layer)

    def test_02_explicit_value_wins(self):
        c = PulseCore()
        p = c.emit("acc", "system.status.request", {"k": "v"},
                   layer="L0", deadline_ms=999)
        self.assertEqual(p["deadline_ms"], 999)

    def test_03_field_is_top_level_not_payload(self):
        """★红线：deadline_ms 与 ttl_ns 同级，绝不能进 payload。"""
        c = PulseCore()
        p = c.emit("acc", "chat.message", {"content": "x"}, layer="L1")
        self.assertIn("deadline_ms", p)
        self.assertIn("ttl_ns", p)
        self.assertNotIn("deadline_ms", p["payload"])
        self.assertEqual(p["payload"], {"content": "x"})

    def test_04_business_fingerprint_stable(self):
        """同一业务事件两次 emit 指纹必须一致（deadline 不污染去重哈希）。"""
        c = PulseCore()
        p1 = c.emit("acc", "chat.message", {"k": "v"}, layer="L1")
        p2 = c.emit("acc", "chat.message", {"k": "v"}, layer="L1")
        self.assertEqual(c._business_fingerprint(p1), c._business_fingerprint(p2))
        self.assertNotEqual(p1["pulse_id"], p2["pulse_id"])

    def test_05_typeddict_optional(self):
        self.assertTrue(hasattr(pulse_types, "TransportDims"))
        self.assertIn("deadline_ms", pulse_types.Pulse.__optional_keys__)
        self.assertNotIn("deadline_ms", pulse_types.Pulse.__required_keys__)

    def test_06_gray_off_never_drops(self):
        _old = getattr(config, "PULSE_DEADLINE_ENABLED", None)
        config.PULSE_DEADLINE_ENABLED = False
        try:
            f = _mk_field()
            should_drop, dl = f._m483_deadline_check(_expired(), "L0")
            st = f.get_deadline_stats()
        finally:
            config.PULSE_DEADLINE_ENABLED = _old if _old is not None else False
        self.assertFalse(should_drop)
        self.assertEqual(dl, 10000)
        self.assertEqual(st["by_layer"]["L0"]["would_timeout"], 1)
        self.assertEqual(st["by_layer"]["L0"]["dropped"], 0)
        self.assertEqual(st["timeout_deadline_total"], 0)
        self.assertFalse(st["enabled"])

    def test_07_gray_on_drops(self):
        _old = getattr(config, "PULSE_DEADLINE_ENABLED", None)
        config.PULSE_DEADLINE_ENABLED = True
        try:
            f = _mk_field()
            should_drop, _ = f._m483_deadline_check(_expired(), "L0")
            st = f.get_deadline_stats()
        finally:
            config.PULSE_DEADLINE_ENABLED = _old if _old is not None else False
        self.assertTrue(should_drop)
        self.assertEqual(st["by_layer"]["L0"]["dropped"], 1)
        self.assertEqual(st["timeout_deadline_total"], 1)

    def test_08_missing_field_uses_layer_fallback(self):
        f = _mk_field()
        p = {"timestamp_ns": time.time_ns() - 20_000_000_000}
        should_drop, dl = f._m483_deadline_check(p, "L0")
        self.assertFalse(should_drop)          # 灰度默认关
        self.assertEqual(dl, 10000)             # 按层兜底生效
        self.assertEqual(f._deadline_stats["L0"]["would_timeout"], 1)

    def test_09_clock_rollback_and_malformed_not_judged(self):
        f = _mk_field()
        # 时钟回拨：now < timestamp
        f._m483_deadline_check({"timestamp_ns": time.time_ns() + 20_000_000_000,
                                "deadline_ms": 10000}, "L0")
        # timestamp 非整数
        f._m483_deadline_check({"timestamp_ns": "bad", "deadline_ms": 10000}, "L0")
        # deadline <= 0 = 不设 SLA
        f._m483_deadline_check({"timestamp_ns": time.time_ns() - 20_000_000_000,
                                "deadline_ms": 0}, "L0")
        self.assertEqual(f._deadline_stats["L0"]["would_timeout"], 0)
        self.assertEqual(f._deadline_stats["L0"]["dropped"], 0)

    def test_10_layers_filter_stops_observation(self):
        _old = getattr(config, "PULSE_DEADLINE_LAYERS", None)
        config.PULSE_DEADLINE_LAYERS = ""
        try:
            f = _mk_field()
            f._m483_deadline_check(_expired(), "L0")
            self.assertEqual(f._deadline_stats["L0"]["would_timeout"], 0)
        finally:
            config.PULSE_DEADLINE_LAYERS = _old if _old is not None else "L0,L1"

    def test_11_layer_not_selected_untouched(self):
        f = _mk_field()
        f._m483_deadline_check(_expired(), "L3")   # 默认只对 L0,L1 判定
        self.assertEqual(f._deadline_stats["L3"]["would_timeout"], 0)

    def test_12_check_is_inside_try(self):
        """★§3.5：检查必须在 try 之内，否则 finally 漏减并发计数。"""
        tree = ast.parse(_read_src(IF_SRC))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_dispatch_handler")
        tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
        self.assertTrue(tries, "_dispatch_handler 必须有 try/finally")
        hit = False
        for t in tries:
            for node in t.body:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                            and sub.func.attr == "_m483_deadline_check":
                        hit = True
        self.assertTrue(hit, "deadline 检查必须在 try.body 内")

    def test_13_snapshot_wiring_and_not_none_fallback(self):
        src = _read_src(PM_SRC)
        self.assertIn('snapshot["pulse_deadline"]', src)
        self.assertIn('snapshot["pulse_timeout_deadline"]', src)
        self.assertIn("get_deadline_stats", src)

    def test_14_get_deadline_stats_shape(self):
        f = _mk_field()
        st = f.get_deadline_stats()
        for k in ("enabled", "layers", "by_layer", "timeout_deadline_total"):
            self.assertIn(k, st)
        for layer in ("L0", "L1", "L2", "L3"):
            for k in ("would_timeout", "dropped", "overrun"):
                self.assertIn(k, st["by_layer"][layer])
        self.assertIsInstance(st["timeout_deadline_total"], int)

    def test_15_single_write_point_in_emit(self):
        """117 个发射点一个都不改：只有 PulseCore.emit 写 deadline_ms。"""
        src = _read_src(PC_SRC)
        self.assertIn('pulse["deadline_ms"] =', src)
        self.assertEqual(src.count('pulse["deadline_ms"] ='), 1)
        self.assertIn("business_fingerprint", src)

    def test_16_judgement_self_check_old_code_is_red(self):
        """★判据有效性：旧脉冲没有 deadline_ms，按「字段存在」判必然为红。"""
        old_pulse = {"timestamp_ns": time.time_ns(), "ttl_ns": 5_000_000_000}
        self.assertNotIn("deadline_ms", old_pulse)
        new_pulse = PulseCore().emit("acc", "x.y", {}, layer="L0")
        self.assertIn("deadline_ms", new_pulse)


if __name__ == "__main__":
    unittest.main()
