# -*- coding: utf-8 -*-
"""相关任务 单测：PHASE18 阶段二 L2 对话主动提及六道门。

覆盖：开关关→逐字不变 / 引擎缺失→原样 / 低健康(critical)注入含"知识质量+建议"且不含禁词
/ healthy·moderate 跳过 / 过期画像跳过 / 冷却内跳过 / concerning 需关键词 / 禁词整句丢弃。
不依赖 framework：_FakeCortex 继承 PulseCortex 但跳过真实 __init__。
"""
import unittest

import config
from organs.brain.PulseCortex import PulseCortex as PC


class _FakeEngine:
    def __init__(self, level="critical", fresh=True, summary=None, issues=None):
        self._level = level
        self._fresh = fresh
        self._summary = summary or {
            "health_level": level, "overall_score": 35.0,
            "best_dimension": {"name": "代码健康", "score": 80.0},
            "worst_dimension": {"name": "知识质量", "score": 35.0},
            "headline_issue": "知识库存在两处冲突",
        }
        self._issues = issues if issues is not None else [
            {"dimension": "knowledge_health", "suggestion": "建议优先处理知识矛盾消解"}]

    def get_health_level(self):
        return self._level

    def is_fresh(self, max_age_sec=86400.0):
        return self._fresh

    def get_public_summary(self):
        return self._summary

    def get_top_issues(self, n=5):
        return self._issues[:n]


class _FakeCortex(PC):
    def __init__(self, eng):
        # 跳过真实 __init__（避免依赖 framework）
        self.self_awareness = eng
        self._self_state_mention_last_ts = 0.0
        self._self_state_mention_cids = set()
        self._log = lambda *a, **k: None


class TestT115fSelfStateMention(unittest.TestCase):
    def setUp(self):
        self._orig_enabled = getattr(config, "SELF_AWARENESS_MENTION_ENABLED", None)
        self._orig_cd = getattr(config, "SELF_AWARENESS_MENTION_COOLDOWN_SEC", None)
        config.SELF_AWARENESS_MENTION_ENABLED = True
        config.SELF_AWARENESS_MENTION_COOLDOWN_SEC = 7200

    def tearDown(self):
        if self._orig_enabled is not None:
            config.SELF_AWARENESS_MENTION_ENABLED = self._orig_enabled
        if self._orig_cd is not None:
            config.SELF_AWARENESS_MENTION_COOLDOWN_SEC = self._orig_cd

    def _call(self, eng, answer="原回复", cid="", user_input=""):
        return _FakeCortex(eng)._maybe_append_self_state(answer, cid, user_input)

    def test_switch_off_returns_original(self):
        config.SELF_AWARENESS_MENTION_ENABLED = False
        self.assertEqual(self._call(_FakeEngine()), "原回复")

    def test_engine_missing_returns_original(self):
        self.assertEqual(_FakeCortex(None)._maybe_append_self_state("原回复"), "原回复")

    def test_low_health_mentions_knowledge_and_suggestion(self):
        out = self._call(_FakeEngine(level="critical", fresh=True), "原回复", cid="c1")
        self.assertNotEqual(out, "原回复")
        self.assertIn("知识质量", out)
        self.assertIn("建议", out)
        for w in PC._SELF_STATE_FORBIDDEN_WORDS:
            self.assertNotIn(w, out)

    def test_healthy_skips(self):
        self.assertEqual(self._call(_FakeEngine(level="healthy"), "原回复", cid="c1"), "原回复")

    def test_moderate_skips(self):
        self.assertEqual(self._call(_FakeEngine(level="moderate"), "原回复", cid="c1"), "原回复")

    def test_stale_profile_skips(self):
        self.assertEqual(self._call(_FakeEngine(level="critical", fresh=False), "原回复", cid="c1"), "原回复")

    def test_cooldown_skips(self):
        eng = _FakeEngine(level="critical", fresh=True)
        c = _FakeCortex(eng)
        first = c._maybe_append_self_state("原回复", "c1")
        self.assertNotEqual(first, "原回复")
        # 冷却内再次调用（全局冷却 + 本会话去重都生效）
        self.assertEqual(c._maybe_append_self_state("原回复", "c2"), "原回复")

    def test_concerning_requires_keyword(self):
        eng = _FakeEngine(level="concerning", fresh=True)
        self.assertEqual(self._call(eng, "原回复", cid="c1", user_input="今天天气不错"), "原回复")
        out = self._call(eng, "原回复", cid="c2", user_input="你最近怎么样")
        self.assertNotEqual(out, "原回复")
        self.assertIn("知识质量", out)

    def test_forbidden_word_dropped(self):
        eng = _FakeEngine(level="critical", fresh=True, summary={
            "health_level": "critical", "overall_score": 30.0,
            "best_dimension": None,
            "worst_dimension": {"name": "知识质量", "score": 30.0},
            "headline_issue": "我崩溃了，知识库全坏"})
        self.assertEqual(self._call(eng, "原回复", cid="c1"), "原回复")


if __name__ == "__main__":
    unittest.main()
