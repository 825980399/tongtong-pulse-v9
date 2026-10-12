# -*- coding: utf-8 -*-
"""190刀3 隔离单测：社会层刀A 影子画像观测（内存态、零落盘、零LLM）。

验收：以 get_current_user 为键聚合三维（address/summary/freq）；多用户键隔离；
会话级清空；INFO 探针就位。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import nucleus.social.shadow_profile_observer as obs  # noqa: E402


class TestBlade3ShadowProfile(unittest.TestCase):
    def setUp(self):
        obs.clear_all()

    def test_observe_aggregates_three_dims(self):
        """以用户键聚合三维：address / summary / freq。"""
        p = obs.observe("alice", summary="你好曈曈")
        self.assertIsNotNone(p)
        self.assertEqual(p["address"], "alice")
        self.assertEqual(p["freq"], 1)
        self.assertEqual(p["summary"], "你好曈曈")

    def test_freq_increments(self):
        obs.observe("alice", summary="a")
        p = obs.observe("alice", summary="b")
        self.assertEqual(p["freq"], 2)
        self.assertEqual(p["summary"], "b")  # 最近一次交互摘要

    def test_multi_user_isolation(self):
        obs.observe("alice", summary="a")
        obs.observe("alice", summary="a")
        obs.observe("bob", summary="b")
        snap = obs.snapshot()
        self.assertIn("alice", snap)
        self.assertIn("bob", snap)
        self.assertEqual(snap["alice"]["freq"], 2)
        self.assertEqual(snap["bob"]["freq"], 1)
        self.assertEqual(obs.get_profile("alice")["freq"], 2)
        self.assertEqual(obs.get_profile("bob")["freq"], 1)

    def test_session_clear(self):
        """会话级清空：snapshot 归空、各键画像不可见（内存态不落盘）。"""
        obs.observe("alice")
        obs.clear_all()
        self.assertEqual(obs.snapshot(), {})
        self.assertIsNone(obs.get_profile("alice"))

    def test_empty_user_ignored(self):
        """解析为空（? / None / 纯空白）的用户键不入库。"""
        self.assertIsNone(obs.observe("?"))
        self.assertIsNone(obs.observe(None))
        self.assertIsNone(obs.observe("   "))
        self.assertEqual(obs.snapshot(), {})

    def test_info_probe_emitted(self):
        """INFO 探针就位：每次观测均以 INFO 级落日志（可观测性）。"""
        orig = obs._log
        mock = MagicMock()
        obs._log = mock
        try:
            obs.observe("alice", summary="x")
        finally:
            obs._log = orig
        self.assertTrue(mock.info.called)
        self.assertIn("alice", str(mock.info.call_args))


if __name__ == "__main__":
    unittest.main()
