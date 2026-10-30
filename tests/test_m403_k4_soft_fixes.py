# -*- coding: utf-8 -*-
"""★403 刀4 判据：原子写重试 / 坏 JSON 跳过 / 阈值告警节流。

守什么
------
1. **重试真的发生在 os.replace 上**（不是"包了一层但只调一次"）：
   前两次抛 WinError 5、第三次成功 ⇒ 结果必须成功且调用 3 次；三次全失败 ⇒ 抛。
2. **坏 JSON 不许炸、也不许静默**：跳过、计数正确、WARNING 里带文件名。
3. **告警是状态变化通知**：正常→超阈值 1 条 WARNING；持续超阈值 30s 内不再打；
   30s 后允许再打一次；恢复打 INFO 且只打一次。
4. **GC 完成不再是 WARNING**（正常行为，退到 DEBUG）。
"""
import importlib.util
import io
import json
import logging
import os
import shutil
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    _p = os.path.join(_ROOT, rel)
    _spec = importlib.util.spec_from_file_location(name, _p)
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return _m


PW = _load("m403_pending_write", os.path.join("nucleus", "evolution", "pending_write.py"))
EB = _load("m403_evolution_budget", os.path.join("nucleus", "evolution",
                                                 "evolution_budget.py"))
RM = _load("m403_runtime_metrics", os.path.join("nucleus", "runtime_metrics.py"))
CL = _load("m403_capability_ledger", os.path.join("nucleus", "self_awareness",
                                                 "capability_ledger.py"))


class _ReplaceSandbox:
    """把模块里的 os.replace 替换成"前 n 次失败"的假实现（os 是共享模块，务必还原）。"""

    def __init__(self, module, failures=2):
        self.module = module
        self.failures = failures
        self.calls = 0
        self._orig = module.os.replace

    def __enter__(self):
        def _fake(src, dst):
            self.calls += 1
            if self.calls <= self.failures:
                raise OSError(13, "WinError 5: 拒绝访问")
            return self._orig(src, dst)
        self.module.os.replace = _fake
        return self

    def __exit__(self, *exc):
        self.module.os.replace = self._orig
        return False


class TestAtomicRetry(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m403k4_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_10_pending_retry_succeeds_after_two_failures(self):
        src = os.path.join(self.d, "a.tmp")
        dst = os.path.join(self.d, "a.json")
        io.open(src, "w", encoding="utf-8").write("{}")
        with _ReplaceSandbox(PW, failures=2) as box:
            PW._replace_with_retry(src, dst)
        self.assertEqual(box.calls, 3)
        self.assertTrue(os.path.exists(dst))

    def test_11_pending_retry_raises_after_all_failures(self):
        src = os.path.join(self.d, "b.tmp")
        io.open(src, "w", encoding="utf-8").write("{}")
        with _ReplaceSandbox(PW, failures=3) as box:
            with self.assertRaises(OSError):
                PW._replace_with_retry(src, os.path.join(self.d, "b.json"))
        self.assertEqual(box.calls, 3)

    def test_12_retry_gap_is_100ms(self):
        self.assertEqual(PW._REPLACE_RETRY_GAP_S, 0.1)
        self.assertEqual(PW._REPLACE_RETRIES, 3)

    def test_13_write_pending_end_to_end(self):
        p = os.path.join(self.d, "pending.json")
        r = PW.write_pending([{"patch_id": "p1"}], path=p, writer="m403test")
        self.assertTrue(r["ok"], r)
        self.assertEqual(json.load(io.open(p, encoding="utf-8")), [{"patch_id": "p1"}])

    def test_14_budget_retry_succeeds_after_two_failures(self):
        p = os.path.join(self.d, "budget.json")
        with _ReplaceSandbox(EB, failures=2) as box:
            out = EB.save_budget({"day": 1}, path=p)
        self.assertEqual(box.calls, 3)
        self.assertTrue(os.path.exists(out))

    def test_15_budget_retry_raises_after_all_failures(self):
        p = os.path.join(self.d, "budget2.json")
        with _ReplaceSandbox(EB, failures=3) as box:
            with self.assertRaises(OSError):
                EB.save_budget({"day": 1}, path=p)
        self.assertEqual(box.calls, 3)

    def test_16_budget_source_uses_retry_loop(self):
        src = io.open(os.path.join(_ROOT, "nucleus", "evolution",
                                   "evolution_budget.py"), encoding="utf-8").read()
        self.assertIn("for _i in range(3):", src)
        self.assertIn("time.sleep(0.1)", src)


class TestBadJson(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="m403led_")
        os.makedirs(os.path.join(self.d, "_archive"), exist_ok=True)
        io.open(os.path.join(self.d, "ok.json"), "w", encoding="utf-8").write(
            json.dumps({"consumed_by": ["x"]}))
        io.open(os.path.join(self.d, "_archive", "empty.json"), "w",
                encoding="utf-8").write("")
        io.open(os.path.join(self.d, "_archive", "broken.json"), "w",
                encoding="utf-8").write('{"a": ')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_20_bad_json_counted_once_each(self):
        r = CL.reconcile_reports(self.d)
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["unparsable"], 2, "两个坏文件必须只计 2，不得重复累加")
        self.assertEqual(len(r["unparsable_files"]), 2)

    def test_21_warning_carries_file_path(self):
        CL._M356_BAD_JSON_WARNED.clear()
        logger = logging.getLogger("pulse.silent_except")

        class _H(logging.Handler):
            def __init__(self):
                super().__init__()
                self.msgs = []

            def emit(self, record):
                self.msgs.append(record.getMessage())

        h = _H()
        logger.addHandler(h)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            CL.reconcile_reports(self.d)
        finally:
            logger.removeHandler(h)
            logger.setLevel(old_level)
        joined = "\n".join(h.msgs)
        self.assertIn("broken.json", joined, "WARNING 必须能定位到具体文件")
        self.assertIn("empty.json", joined)

    def test_22_warned_once_per_file_per_process(self):
        CL._M356_BAD_JSON_WARNED.clear()
        logger = logging.getLogger("pulse.silent_except")
        hits = []

        class _H(logging.Handler):
            def emit(self, record):
                hits.append(record.getMessage())

        h = _H()
        logger.addHandler(h)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            CL.reconcile_reports(self.d)
            first = len(hits)
            CL.reconcile_reports(self.d)
            second = len(hits)
        finally:
            logger.removeHandler(h)
            logger.setLevel(old_level)
        self.assertGreater(first, 0)
        self.assertEqual(second, first, "同一坏文件不得每轮重刷（356 刀10 的判据）")

    def test_23_unparsable_excluded_from_idle_rate(self):
        r = CL.reconcile_reports(self.d)
        self.assertEqual(r["by_destination"]["true_idle"], 0)
        self.assertEqual(r["by_subclass"]["unparseable"], 2)


class TestAlarmStateMachine(unittest.TestCase):
    def setUp(self):
        RM._M403_ALARM_STATE.clear()
        self.logger = logging.getLogger("pulse.module.runtime_metrics")

    def _fire(self, triggered, key="t"):
        RM.RuntimeMetrics._m403_alarm_transition(
            self, self.logger, key, triggered, "ALARM-msg", "RECOVER-msg")

    def test_30_first_trigger_warns_once(self):
        with self.assertLogs(self.logger, level="WARNING") as cm:
            self._fire(True)
            self._fire(True)
            self._fire(True)
        self.assertEqual(len([r for r in cm.records if "ALARM-msg" in r.getMessage()]), 1,
                         "持续超阈值 30s 内不得重复打")

    def test_31_repeat_after_30s_warns_again(self):
        self._fire(True)
        RM._M403_ALARM_STATE["t"]["last"] = time.time() - 31
        with self.assertLogs(self.logger, level="WARNING") as cm:
            self._fire(True)
        self.assertEqual(len(cm.records), 1, "过了节流窗应允许再打一条，证明还活着")

    def test_32_recovery_logs_info_once(self):
        self._fire(True)
        with self.assertLogs(self.logger, level="INFO") as cm:
            self._fire(False)
            self._fire(False)
        msgs = [r.getMessage() for r in cm.records if "RECOVER-msg" in r.getMessage()]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(RM._M403_ALARM_STATE["t"]["state"], "normal")

    def test_33_throttle_constant_is_30s(self):
        self.assertEqual(RM._M403_ALARM_THROTTLE_S, 30.0)

    def test_34_simple_alarm_uses_state_machine(self):
        r = RM.RuntimeMetrics()
        r.get_memory_usage = lambda: {"percent": 95.0}
        with self.assertLogs(self.logger, level="WARNING") as cm:
            r.check_memory_alarm(80.0)
            r.check_memory_alarm(80.0)
            r.check_memory_alarm(80.0)
        self.assertEqual(len(cm.records), 1, "三次采样只应有 1 条 WARNING")

    def test_35_simple_alarm_returns_true_when_over(self):
        r = RM.RuntimeMetrics()
        r.get_memory_usage = lambda: {"percent": 95.0}
        self.assertTrue(r.check_memory_alarm(80.0))

    def test_36_gc_log_level_is_debug(self):
        src = io.open(os.path.join(_ROOT, "nucleus", "runtime_metrics.py"),
                      encoding="utf-8").read()
        idx = src.find("自动GC完成")
        self.assertGreater(idx, 0)
        head = src[max(0, idx - 400):idx]
        self.assertIn("_logger.debug(", head, "GC 完成必须降为 DEBUG")
        self.assertNotIn("_logger.warning(\n                        \"[运行时指标] 自动GC完成",
                         head)


if __name__ == "__main__":
    unittest.main(verbosity=2)
