# -*- coding: utf-8 -*-
"""
test_dialog_guard_m15.py —— 主线第15批 任务1 门控单测（P1-98 对话回复错位）

覆盖：开关默认值、关闭零副作用、首次输入派发、queue/cancel 两种策略、
      cid 校验（作废/过期丢弃）、输出完成后自动派发队列、排队 TTL 加长、
      源码接线点存在性、cid 唯一且递增。
"""
import os
import sys
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from organs.brain.PulseCortex import PulseCortex  # noqa: E402

_SRC = os.path.join(_PROJECT_ROOT, "organs", "brain", "PulseCortex.py")


def _read_src():
    with open(_SRC, encoding="utf-8") as f:
        return f.read()


class _CortexHarness:
    """把真实 PulseCortex 的 _emit/_log 换成记录器，其余逻辑走真实代码。"""

    def __init__(self):
        self.emits = []
        self.logs = []
        self.cortex = PulseCortex("测试皮层")
        self.cortex._emit = self._emit
        self.cortex._log = self._log

    def _emit(self, event, payload=None, **kw):
        self.emits.append((event, payload or {}))

    def _log(self, level, msg):
        self.logs.append((level, msg))

    def emitted_names(self):
        return [e for e, _ in self.emits]

    def speak_payloads(self):
        # MouthEvent.SPEAK 是 str 常量（值为 "mouth.speak"），故按字符串匹配
        return [p for e, p in self.emits if "speak" in str(e).lower()]

    def classify_cids(self):
        return [p.get("correlation_id") for e, p in self.emits
                if e == "semantic.classify"]


class _Switch:
    """临时改 config 开关并在退出时还原。"""

    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


class TestDialogGuardDefaults(unittest.TestCase):
    def test_config_defaults(self):
        self.assertIs(getattr(config, "ENABLE_DIALOG_LOCK_GUARD", None), True)
        self.assertEqual(getattr(config, "DIALOG_QUEUE_STRATEGY", None), "queue")

    def test_stats_shape(self):
        h = _CortexHarness()
        st = h.cortex.get_dialog_guard_stats()
        for key in ("enabled", "strategy", "queue_len", "latest_correlation_id",
                    "active_correlation_id", "checked", "dropped_cancelled",
                    "dropped_stale", "queued", "hint_sent"):
            self.assertIn(key, st)


class TestGuardDisabled(unittest.TestCase):
    def test_zero_side_effects_when_disabled(self):
        with _Switch(ENABLE_DIALOG_LOCK_GUARD=False):
            h = _CortexHarness()
            a = h.cortex._dialog_register_input("ctx:1:1")
            self.assertEqual(a["action"], "dispatch")
            # 第二个输入也不排队
            b = h.cortex._dialog_register_input("ctx:2:2")
            self.assertEqual(b["action"], "dispatch")
            self.assertEqual(len(h.cortex._dialog_queue), 0)
            # 输出校验恒放行
            self.assertTrue(h.cortex._dialog_should_output("ctx:1:1"))
            self.assertTrue(h.cortex._dialog_should_output(""))
            self.assertEqual(h.cortex.get_dialog_guard_stats()["dropped_stale"], 0)


class TestFirstInput(unittest.TestCase):
    def test_first_input_dispatched(self):
        h = _CortexHarness()
        r = h.cortex._dialog_register_input("ctx:1:100")
        self.assertEqual(r["action"], "dispatch")
        self.assertEqual(h.cortex._latest_correlation_id, "ctx:1:100")
        self.assertEqual(h.cortex._active_correlation_id, "ctx:1:100")


class TestQueueStrategy(unittest.TestCase):
    def test_second_input_queued_with_hint(self):
        with _Switch(DIALOG_QUEUE_STRATEGY="queue"):
            h = _CortexHarness()
            h.cortex._dialog_register_input("ctx:1:100")
            r2 = h.cortex._dialog_register_input("ctx:2:200")
            self.assertEqual(r2["action"], "queued")
            self.assertEqual(r2["ahead"], 1)
            self.assertIn("ctx:2:200", list(h.cortex._dialog_queue))
            # 旧会话仍是 active（queue 策略不抢占）
            self.assertEqual(h.cortex._active_correlation_id, "ctx:1:100")
            # 用户收到明确提示
            h.cortex._dialog_notify_busy("用户", 1)
            self.assertTrue(any("请稍候" in (p.get("content") or "")
                                for p in h.speak_payloads()))
            self.assertEqual(h.cortex.get_dialog_guard_stats()["hint_sent"], 1)

    def test_output_done_dispatches_queued(self):
        with _Switch(DIALOG_QUEUE_STRATEGY="queue"):
            h = _CortexHarness()
            h.cortex._dialog_register_input("ctx:1:100")
            h.cortex._dialog_register_input("ctx:2:200")
            # 排队消息必须在暂存里，才能被派发
            h.cortex._pending_messages["ctx:2:200"] = {
                "content": "第二个问题", "user_name": "用户", "timestamp": time.time()}
            h.cortex._dialog_mark_output_done("ctx:1:100")
            self.assertEqual(h.classify_cids(), ["ctx:2:200"])
            self.assertEqual(h.cortex._active_correlation_id, "ctx:2:200")
            self.assertEqual(len(h.cortex._dialog_queue), 0)

    def test_queue_full_cancels_oldest(self):
        with _Switch(DIALOG_QUEUE_STRATEGY="queue"):
            h = _CortexHarness()
            h.cortex._dialog_max_queue = 2
            h.cortex._dialog_register_input("ctx:1:1")
            h.cortex._dialog_register_input("ctx:2:2")
            h.cortex._dialog_register_input("ctx:3:3")
            h.cortex._dialog_register_input("ctx:4:4")   # 溢出，挤掉 ctx:2
            self.assertNotIn("ctx:2:2", list(h.cortex._dialog_queue))
            self.assertFalse(h.cortex._dialog_should_output("ctx:2:2"))


class TestCancelStrategy(unittest.TestCase):
    def test_previous_cancelled_and_reply_dropped(self):
        with _Switch(DIALOG_QUEUE_STRATEGY="cancel"):
            h = _CortexHarness()
            h.cortex._dialog_register_input("ctx:1:100")
            r2 = h.cortex._dialog_register_input("ctx:2:200")
            self.assertEqual(r2["action"], "dispatch")
            self.assertEqual(h.cortex._active_correlation_id, "ctx:2:200")
            # 旧会话回复必须被丢弃
            self.assertFalse(h.cortex._dialog_should_output("ctx:1:100"))
            self.assertTrue(h.cortex._dialog_should_output("ctx:2:200"))
            self.assertEqual(h.cortex.get_dialog_guard_stats()["dropped_cancelled"], 1)


class TestShouldOutput(unittest.TestCase):
    def test_unknown_cid_dropped_when_stale(self):
        h = _CortexHarness()
        h.cortex._dialog_register_input("ctx:1:100")
        h.cortex._dialog_register_input("ctx:2:200")  # queue 策略下 ctx:1 仍 active
        # ctx:1 是 active → 允许输出
        self.assertTrue(h.cortex._dialog_should_output("ctx:1:100"))
        # 一个既非 latest 也非 active 的 cid（如超时被摘掉的会话）→ 丢弃
        self.assertFalse(h.cortex._dialog_should_output("ctx:99:999"))
        self.assertEqual(h.cortex.get_dialog_guard_stats()["dropped_stale"], 1)

    def test_empty_cid_always_allowed(self):
        h = _CortexHarness()
        self.assertTrue(h.cortex._dialog_should_output(""))


class TestQueuedTtl(unittest.TestCase):
    def test_cleanup_pending_keeps_queued_longer(self):
        h = _CortexHarness()
        now = time.time()
        h.cortex._pending_messages["ctx:queued:1"] = {"timestamp": now - 60}
        h.cortex._pending_messages["ctx:plain:2"] = {"timestamp": now - 60}
        h.cortex._dialog_queue.append("ctx:queued:1")
        h.cortex._cleanup_pending()
        self.assertIn("ctx:queued:1", h.cortex._pending_messages,
                      "排队会话 60s 内不应被清理（TTL 180s）")
        self.assertNotIn("ctx:plain:2", h.cortex._pending_messages,
                         "非排队会话仍按 30s TTL 清理")


class TestSourceWiring(unittest.TestCase):
    """源码接线护栏：三处输出 + 输入登记必须都挂了防护（回退即失败）。"""

    def setUp(self):
        self.src = _read_src()

    def test_input_registration_wired(self):
        self.assertIn("_dlg_state = self._dialog_register_input(correlation_id)", self.src)
        self.assertIn('return {\n                "status": "dialog_queued",', self.src)

    def test_three_output_sites_guarded(self):
        """★主线第33批 T2（P2-195）：此处**刻意保留精确计数**。

        这不是「实现投影」而是**语义要求** —— 出现第 4 个 dialog 输出点却不过
        守卫即真实回归（会造成重复/越权输出），故**不**放宽为 `>=`。
        """
        self.assertEqual(self.src.count("self._dialog_should_output(correlation_id)"), 3,
                         "普通/带文件/超时兜底 三处输出都要校验 cid")

    def test_mark_output_done_wired(self):
        """与守卫**配对**：过守卫的输出必须登记已完成（否则 cid 状态泄漏）。"""
        self.assertEqual(self.src.count("self._dialog_mark_output_done(correlation_id)"), 3)

    def test_head_no_e402_pattern(self):
        """头部不得再出现「赋值语句关闭 import 区」的写法。"""
        head = "\n".join(self.src.splitlines()[:60])
        _i_logger = head.index("_module_logger = logging.getLogger(__name__)")
        _i_utils = head.index("from utils.time_utils import get_current_datetime")
        self.assertGreater(_i_logger, _i_utils,
                           "_module_logger 赋值必须位于全部 import 之后（否则 E402）")


class TestCorrelationIdUnique(unittest.TestCase):
    def test_ids_unique_and_increasing(self):
        """cid 由自增计数器生成：唯一、递增、不重复（对应「无跳号/无复用」）。"""
        h = _CortexHarness()
        c = h.cortex
        ids = []
        for _ in range(20):
            c._correlation_counter += 1
            ids.append(f"ctx:{c._correlation_counter}:{int(time.time() * 1000)}")
        self.assertEqual(len(set(ids)), 20)
        nums = [int(i.split(":")[1]) for i in ids]
        self.assertEqual(nums, sorted(nums))
        self.assertEqual(nums[-1] - nums[0], 19, "计数器必须逐 1 递增，不得跳号")


if __name__ == "__main__":
    unittest.main()
