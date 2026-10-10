# -*- coding: utf-8 -*-
"""IR 链核心组件最小结构测试（T150-4）：PulseReflection。

验证两种复盘提交分支：
  - 同步分支：info_field=None 时不调用 LLM，复盘在 on_pulse 内同步完成；
  - 异步分支：info_field 带 submit_adaptive_task（mock，延迟不执行）→ SPEAK 入队、
    BEAT 批量处理。
锁定契约：SPEAK 入队+复盘、BEAT 处理返回键集、get_stats 五键。
设置 PULSE_LOG_FILE 避免日志写入 logs/ 产生副作用。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 避免日志落盘副作用
os.environ.setdefault(
    "PULSE_LOG_FILE",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs", "test_reflection.log",
    ),
)

from nucleus.const import HeartEvent, MouthEvent  # noqa: E402
from organs.brain.PulseReflection import PulseReflection  # noqa: E402


class _DeferredField:
    """mock 信息场：提供 submit_adaptive_task 但**延迟不执行**（模拟异步）。"""

    def __init__(self):
        self.published = []
        self.tasks = []

    def publish(self, *args, **kwargs):
        self.published.append((args, kwargs))

    def submit_adaptive_task(self, task_func, task_name="", priority="normal", **kwargs):
        self.tasks.append(task_func)
        return True  # 不立即执行


class TestReflectionSync(unittest.TestCase):
    def _pulse(self, et, payload):
        return {"event_type": et, "payload": payload, "priority": 3}

    def test_sync_branch_reflects_on_speak(self):
        # info_field=None → 同步复盘，不触碰 LLM
        r = PulseReflection("前额叶")
        r.start()
        self.assertTrue(r.is_running)
        r.on_pulse(self._pulse(MouthEvent.SPEAK, {
            "user_input": "你是谁",
            "response": "我是曈曈",
            "reasoning_path": "rule_match",
            "user_name": "小林",
        }))
        stats = r.get_stats()
        self.assertEqual(stats["organ"], "前额叶")
        for k in ("reflection_count", "issue_count", "queue_size", "recent_reflections"):
            self.assertIn(k, stats)
        self.assertGreaterEqual(stats["reflection_count"], 1)
        self.assertGreaterEqual(stats["queue_size"], 1)
        self.assertIsInstance(stats["issue_count"], int)

    def test_not_running_returns_none(self):
        r = PulseReflection("前额叶")  # 未 start → is_running=False
        self.assertIsNone(r.on_pulse(self._pulse(MouthEvent.SPEAK, {"user_input": "x"})))

    def test_get_stats_keys(self):
        r = PulseReflection("前额叶")
        r.start()
        self.assertEqual(
            set(r.get_stats().keys()),
            {"organ", "reflection_count", "issue_count", "queue_size", "recent_reflections"},
        )


class TestReflectionBeat(unittest.TestCase):
    def _pulse(self, et, payload):
        return {"event_type": et, "payload": payload, "priority": 3}

    def test_beat_processes_pending(self):
        # 异步分支：SPEAK 入队但复盘延迟 → BEAT 批量处理
        r = PulseReflection("前额叶")
        r.set_info_field(_DeferredField())
        r.start()
        for i in range(3):
            r.on_pulse(self._pulse(MouthEvent.SPEAK, {
                "user_input": "问题%d" % i,
                "response": "回答%d" % i,
                "reasoning_path": "rule",
                "user_name": "u",
            }))
        self.assertEqual(r.get_stats()["queue_size"], 3)
        self.assertEqual(r.get_stats()["reflection_count"], 0)  # 异步未执行
        res = r.on_pulse(self._pulse(HeartEvent.BEAT, {}))
        self.assertIsInstance(res, dict)
        self.assertEqual(res["processed"], 3)
        self.assertIn("issues_found", res)
        self.assertIn("total_reflected", res)
        self.assertEqual(r.get_stats()["reflection_count"], 3)


if __name__ == "__main__":
    unittest.main()
