# -*- coding: utf-8 -*-
"""483 批 · 刀 483-1「启动后三拍 gc 类型差分」门控单测。

★四条修正：采样槽位只有 05 / M339_TRACEMALLOC_MODE=off / window 差分不可达 /
resident 在 4.1GB 堆上不能开 ⇒ 本批只走 gc_objects，绝不启 tracemalloc。
所有产物写到 tmp/_m483_probe/ 隔离目录，不碰 data/reports/memory/。
"""
import glob
import json
import os
import shutil
import time
import tracemalloc
import unittest
from unittest import mock

import config
from organs.core.PulseMetricsCollector import PulseMetricsCollector


def _read_src(path):
    with open(path, encoding="utf-8") as _f:
        return _f.read()


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tmp", "_m483_probe")
PM_SRC = os.path.join(ROOT, "organs", "core", "PulseMetricsCollector.py")


class _FakeIF:
    @staticmethod
    def _m169_memory_pressure():
        return {"process_rss_mb": 4125.6}


def _mk_probe(done=0, due=None, boot_ts=None):
    m = PulseMetricsCollector.__new__(PulseMetricsCollector)
    m._log = lambda *a, **k: None
    m.info_field = _FakeIF()
    m._m483_boot_ts = boot_ts if boot_ts is not None else time.time() - 700.0
    m._m483_done = done
    m._m483_next_due = due if due is not None else time.time() - 1.0
    m._m483_prev = None
    m._m416_probe_busy_until = 0.0
    return m


def _slot_now():
    lt = time.localtime()
    return (f"{lt.tm_year:04d}{lt.tm_mon:02d}{lt.tm_mday:02d}"
            f"-{lt.tm_hour:02d}")


class TestM483BootProbe(unittest.TestCase):

    def setUp(self):
        if os.path.isdir(OUT):
            shutil.rmtree(OUT, ignore_errors=True)
        os.makedirs(OUT, exist_ok=True)

    def tearDown(self):
        # 清掉隔离产物，避免污染后续轮次（tmp/ 已 gitignore）
        shutil.rmtree(OUT, ignore_errors=True)

    def _load(self, out):
        self.assertTrue(out.get("ran"), out)
        self.assertTrue(os.path.isfile(out["path"]), out)
        with open(out["path"], encoding="utf-8") as f:
            return json.load(f)

    def test_01_signature_new_params_have_defaults(self):
        import inspect
        sig = inspect.signature(PulseMetricsCollector._m339_memory_probe)
        for name in ("rss_mb", "trigger", "boot_seq"):
            self.assertIn(name, sig.parameters)
            self.assertNotEqual(sig.parameters[name].default, inspect.Parameter.empty)
        # 既有单参调用点与探针自身都还能用默认值
        self.assertNotEqual(sig.parameters["heap_mb"].default, inspect.Parameter.empty)

    def test_02_first_boot_beat_product_fields(self):
        m = _mk_probe()
        out = m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT)
        d = self._load(out)
        self.assertEqual(out["mode"], "gc_objects")
        self.assertNotIn("tracemalloc", out["mode"])
        self.assertEqual(d["trigger"], "boot")
        self.assertEqual(d["boot_seq"], 1)
        self.assertEqual(d["top20_source"], "gc_types")
        self.assertGreaterEqual(len(d["gc_type_rows"]), 20)
        self.assertIsNotNone(d["rss_mb"])
        self.assertIsInstance(d["uptime_sec"], (int, float))
        self.assertGreaterEqual(d["uptime_sec"], 600.0)
        for k in ("rss_delta_mb", "gc_delta_rows", "gc_delta_state", "gc_delta_basis"):
            self.assertIn(k, d)
        self.assertIsInstance(d["gc_delta_rows"], list)
        self.assertIn(d["gc_delta_state"], ("ok", "no_growth", "no_baseline", "unreadable"))

    def test_03_three_beats_then_stop(self):
        m = _mk_probe()
        seen = []
        for _ in range(3):
            m._m416_probe_busy_until = 0.0
            m._m483_next_due = time.time() - 1.0
            d = self._load(m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT))
            seen.append((d["trigger"], d["boot_seq"]))
        self.assertEqual(seen, [("boot", 1), ("boot", 2), ("boot", 3)])
        self.assertEqual(m._m483_done, 3)
        # 第 4 拍：boot 计数不再前进（顺带压掉按小时 gc 槽位，隔离出「停止」语义）
        m._m416_probe_busy_until = 0.0
        m._m339_probe_last_gc_slot = _slot_now()
        m._m339_probe_last_slot = _slot_now()
        out = m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT)
        self.assertFalse(out["ran"])
        self.assertEqual(m._m483_done, 3)

    def test_04_boot_never_writes_daily_slot_lock(self):
        """★边界：boot 路径不得写 _m339_probe_last_slot（会把 05:00 日采样顶掉）。"""
        m = _mk_probe()
        m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT)
        self.assertIsNone(getattr(m, "_m339_probe_last_slot", None))
        self.assertNotIn("tracemalloc", str(m.__dict__.get("_m339_probe_last_gc_slot", "")))

    def test_05_gc_delta_baseline_is_same_proc_gen(self):
        m = _mk_probe()
        d1 = self._load(m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT))
        self.assertIn(d1["gc_delta_state"], ("no_baseline", "unreadable", "ok"))
        m._m416_probe_busy_until = 0.0
        m._m483_next_due = time.time() - 1.0
        d2 = self._load(m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT))
        self.assertIn(d2["gc_delta_state"], ("ok", "no_growth"))
        if d2["gc_delta_rows"]:
            self.assertEqual(d2["gc_delta_state"], "ok")
        self.assertTrue(d2["gc_delta_basis"].startswith("top_types_"))
        self.assertEqual(d2["proc_gen"], d1["proc_gen"])
        self.assertIsNotNone(d2["rss_delta_mb"])

    def test_06_switch_off_no_boot_beat(self):
        _old = getattr(config, "ENABLE_M483_BOOT_PROBE", None)
        config.ENABLE_M483_BOOT_PROBE = False
        try:
            m = _mk_probe()
            m._m339_probe_last_gc_slot = _slot_now()   # 压掉按小时 gc 槽位
            m._m339_probe_last_slot = _slot_now()      # 压掉 05:00 日槽位（含恰逢 05 点）
            out = m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT)
        finally:
            if _old is None:
                delattr(config, "ENABLE_M483_BOOT_PROBE")
            else:
                config.ENABLE_M483_BOOT_PROBE = _old
        self.assertFalse(out["ran"])
        self.assertIn("not_in_slot", str(out["reason"]))
        self.assertEqual(len(glob.glob(os.path.join(OUT, "top_types_*.json"))), 0)

    def test_07_no_tracemalloc_started(self):
        was = tracemalloc.is_tracing()
        m = _mk_probe()
        out = m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT)
        self.assertEqual(tracemalloc.is_tracing(), was)
        self.assertEqual(out["mode"], "gc_objects")
        d = self._load(out)
        self.assertEqual(d["tracemalloc_mode"], "off")

    def test_08_note_boundaries_written(self):
        m = _mk_probe()
        d = self._load(m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT))
        note = str(d["note"])
        self.assertIn("gc_bytes_kind=shallow", note)
        self.assertIn("gc_objects_scope=gc_tracked_only", note)
        self.assertIn("gc_truncated=true", note)
        self.assertEqual(d["gc_bytes_kind"], "shallow")
        self.assertEqual(d["gc_objects_scope"], "gc_tracked_only")

    def test_09_write_failure_does_not_advance_boot_counter(self):
        m = _mk_probe()
        with mock.patch("os.replace", side_effect=OSError("disk full (stub)")):
            out = m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT)
        self.assertFalse(out["ran"])
        self.assertEqual(out["reason"], "write_failed")
        self.assertEqual(m._m483_done, 0, "写盘失败必须不推进，下一拍重试")

    def test_10_interval_greater_than_lease(self):
        """§1.3：间隔必须 > 单飞租约 900s，否则第 2 拍被自己护栏挡掉。"""
        self.assertGreater(config.M483_PROBE_INTERVAL_SEC,
                           config.M416_MEMORY_PROBE_MAX_RUNTIME_SEC)
        self.assertEqual(config.M483_PROBE_SAMPLES, 3)
        self.assertEqual(config.M483_PROBE_FIRST_SEC, 600.0)

    def test_11_new_fields_written_in_payload_source(self):
        src = _read_src(PM_SRC)
        for key in ("trigger", "boot_seq", "uptime_sec", "rss_mb",
                    "rss_delta_mb", "gc_delta_rows", "gc_delta_state", "gc_delta_basis"):
            self.assertIn(f'"{key}"', src)
        self.assertIn("_m483_done", src)
        self.assertIn("_m483_next_due", src)

    def test_12_judgement_self_check_old_product_is_red(self):
        """★反假通过：旧产物 ran=true / top20_source=gc_types 但缺 trigger 等字段，
        只看 self_probe.ran 会放过空壳 —— 这条把判据钉在有判别力的字段上。
        """
        old = {"ran": True, "mode": "gc_objects", "top20_source": "gc_types",
               "gc_type_rows": [{"type": "dict"}] * 20}
        self.assertNotIn("trigger", old)
        self.assertNotIn("rss_mb", old)
        m = _mk_probe()
        d = self._load(m._m339_memory_probe(heap_mb=5000.0, out_dir=OUT))
        self.assertIn("trigger", d)
        self.assertIn("rss_mb", d)


if __name__ == "__main__":
    unittest.main()
