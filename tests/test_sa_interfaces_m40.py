# -*- coding: utf-8 -*-
"""第40批 T4 门控测试：PHASE18 阶段二 · L1 观测级接入。

验证：
  · 6 个只读接口的数据结构（get_latest_profile / get_top_issues /
    get_health_level / get_dimension_score / is_fresh / get_public_summary）；
  · 公开摘要**不含**文件名 / 行号 / 内部路径；
  · 灰度开关默认值（观测级 True / 影响级 False）；
  · PulseCortex 旁路观测**不修改任何决策**（只记日志 + cid 去重）。

★测试隔离：输出目录指向临时目录，不读写生产 data/self_awareness/。
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from organs.brain.PulseCortex import PulseCortex  # noqa: E402

_eng = importlib.import_module("nucleus.self_awareness.SelfAwarenessEngine")
_Profile = _eng.SelfAwarenessProfile


def _sample_profile(ts=None):
    _p = _Profile(timestamp=ts or time.strftime("%Y-%m-%dT%H:%M:%S"),
                  overall_score=77.5, health_level="moderate")
    _p.code_health = {"score": 90.0}
    _p.knowledge_health = {"score": 55.0}
    _p.evolution_health = {"score": 80.0}
    _p.top_issues = [{
        "dimension": "knowledge_health", "dimension_label": "知识质量",
        "severity": "high", "original_severity": "error",
        "description": "data/knowledge/evol.json:12 与 neuron.py:88 存在冲突",
        "suggestion": "去重",
    }]
    return _p


class _IsoBase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="m40_t4_")
        self._orig_dir = getattr(config, "SELF_AWARENESS_OUTPUT_DIR", None)
        config.SELF_AWARENESS_OUTPUT_DIR = self._dir

    def tearDown(self):
        if self._orig_dir is not None:
            config.SELF_AWARENESS_OUTPUT_DIR = self._orig_dir
        shutil.rmtree(self._dir, ignore_errors=True)


class TestInterfaces(_IsoBase):
    def test_01_latest_profile_from_memory(self):
        _p = _sample_profile()
        _e = _eng.SelfAwarenessEngine()
        _e.set_profile(_p)
        _lp = _e.get_latest_profile()
        self.assertIsNotNone(_lp)
        self.assertEqual(_lp.health_level, "moderate")

    def test_02_top_issues(self):
        _e = _eng.SelfAwarenessEngine()
        _e.set_profile(_sample_profile())
        _is = _e.get_top_issues(5)
        self.assertEqual(len(_is), 1)
        self.assertEqual(_is[0]["severity"], "high")
        self.assertEqual(_e.get_top_issues(0), [])
        self.assertEqual(_e.get_top_issues(-1), [])
        self.assertEqual(_e.get_top_issues(None), _is)

    def test_03_health_level(self):
        _e = _eng.SelfAwarenessEngine()
        _e.set_profile(_sample_profile())
        self.assertEqual(_e.get_health_level(), "moderate")

    def test_04_dimension_score(self):
        _e = _eng.SelfAwarenessEngine()
        _e.set_profile(_sample_profile())
        self.assertEqual(_e.get_dimension_score("code_health"), 90.0)
        self.assertIsNone(_e.get_dimension_score("unknown_dim"))
        self.assertIsNone(_e.get_dimension_score(""))
        self.assertIsNone(_e.get_dimension_score(None))

    def test_05_is_fresh_true(self):
        _e = _eng.SelfAwarenessEngine()
        _e.set_profile(_sample_profile())
        self.assertTrue(_e.is_fresh(86400))

    def test_06_is_fresh_false_when_old(self):
        _old = time.strftime("%Y-%m-%dT%H:%M:%S",
                             time.localtime(time.time() - 3 * 86400))
        _e = _eng.SelfAwarenessEngine()
        _e._profile = _sample_profile(_old)   # merge 语义会保留较新时间戳
        self.assertFalse(_e.is_fresh(86400))

    def test_07_top_issues_never_none(self):
        _e = _eng.SelfAwarenessEngine()
        self.assertEqual(_e.get_top_issues(5), [])


class TestPublicSummary(_IsoBase):
    def test_20_sanitized(self):
        _e = _eng.SelfAwarenessEngine()
        _e.set_profile(_sample_profile())
        _s = _e.get_public_summary()
        self.assertNotIn("evol.json", _s["headline_issue"])
        self.assertNotIn("neuron.py", _s["headline_issue"])
        self.assertNotIn(":12", _s["headline_issue"])
        self.assertIn("<内部文件>", _s["headline_issue"])

    def test_21_fields(self):
        _e = _eng.SelfAwarenessEngine()
        _e.set_profile(_sample_profile())
        _s = _e.get_public_summary()
        for _k in ("health_level", "overall_score", "best_dimension",
                   "worst_dimension", "headline_issue"):
            self.assertIn(_k, _s)
        self.assertEqual(_s["health_level"], "moderate")
        self.assertEqual(_s["overall_score"], 77.5)
        self.assertEqual(_s["best_dimension"]["name"], "代码静态")
        self.assertEqual(_s["worst_dimension"]["name"], "知识质量")

    def test_22_no_internal_refs_anywhere(self):
        _e = _eng.SelfAwarenessEngine()
        _e.set_profile(_sample_profile())
        _txt = json.dumps(_e.get_public_summary(), ensure_ascii=False)
        for _bad in ("evol.json", "neuron.py", ".py:", "data/knowledge/"):
            self.assertNotIn(_bad, _txt)

    def test_23_empty_safe(self):
        _e = _eng.SelfAwarenessEngine()
        _s = _e.get_public_summary()
        self.assertEqual(_s["health_level"], "unknown")
        self.assertIsNone(_s["overall_score"])
        self.assertIsNone(_s["best_dimension"])
        self.assertEqual(_s["headline_issue"], "")

    def test_24_sanitize_helper(self):
        _s = _eng._sanitize_public_text("xxx.py:123 有问题")
        self.assertNotIn(".py", _s)
        self.assertIn("<内部文件>", _s)
        self.assertEqual(_eng._sanitize_public_text("无内部信息"), "无内部信息")
        self.assertEqual(_eng._sanitize_public_text(None), "")


class TestColdStart(_IsoBase):
    def test_30_no_data_returns_none(self):
        _e = _eng.SelfAwarenessEngine()
        self.assertIsNone(_e.get_latest_profile())
        self.assertEqual(_e.get_health_level(), "unknown")

    def test_31_file_fallback(self):
        _fp = os.path.join(self._dir, "profile_20260101_000000.json")
        with open(_fp, "w", encoding="utf-8") as f:
            json.dump(_sample_profile().to_dict(), f, ensure_ascii=False)
        _e = _eng.SelfAwarenessEngine()
        self.assertIsNotNone(_e.get_latest_profile())
        self.assertEqual(_e.get_health_level(), "moderate")
        self.assertEqual(_e.get_dimension_score("code_health"), 90.0)

    def test_32_picks_newest_file(self):
        for _n, _sc in (("profile_20260101_000000.json", 10.0),
                        ("profile_20260202_000000.json", 88.0)):
            _p = _sample_profile()
            _p.code_health = {"score": _sc}
            with open(os.path.join(self._dir, _n), "w", encoding="utf-8") as f:
                json.dump(_p.to_dict(), f, ensure_ascii=False)
        # 让第二个文件 mtime 更新
        _old = os.path.join(self._dir, "profile_20260101_000000.json")
        _t = time.time() - 1000
        os.utime(_old, (_t, _t))
        _e = _eng.SelfAwarenessEngine()
        self.assertEqual(_e.get_dimension_score("code_health"), 88.0)

    def test_33_ignores_non_profile_files(self):
        with open(os.path.join(self._dir, "report_x.txt"), "w",
                  encoding="utf-8") as f:
            f.write("x")
        with open(os.path.join(self._dir, "calls_20260101.jsonl"), "w",
                  encoding="utf-8") as f:
            f.write("{}\n")
        self.assertIsNone(_eng.SelfAwarenessEngine().get_latest_profile())


class TestSwitchDefaults(unittest.TestCase):
    def test_40_defaults(self):
        self.assertIs(config.ENABLE_SELF_AWARENESS_OBSERVATION, True)
        self.assertIs(config.ENABLE_SELF_AWARENESS_INFLUENCE_DECISION, False)


class TestCortexObservation(unittest.TestCase):
    def setUp(self):
        self._logs = []
        self._cx = PulseCortex.__new__(PulseCortex)
        self._cx._log = lambda lvl, msg: self._logs.append((lvl, msg))
        self._cx.self_awareness = _eng.SelfAwarenessEngine()
        self._cx.self_awareness.set_profile(_sample_profile())

    def test_50_observe_logs_once(self):
        _r = self._cx._m40_observe_self_awareness("回复", "cid-1")
        self.assertIsNone(_r, "旁路观测必须无返回值（不改决策）")
        self.assertEqual(len(self._logs), 1)
        self.assertIn("自我认知观测", self._logs[0][1])

    def test_51_cid_dedup(self):
        self._cx._m40_observe_self_awareness("回复", "cid-1")
        self._cx._m40_observe_self_awareness("回复", "cid-1")
        self.assertEqual(len(self._logs), 1)
        self._cx._m40_observe_self_awareness("回复2", "cid-2")
        self.assertEqual(len(self._logs), 2)

    def test_52_switch_off_zero_log(self):
        _orig = config.ENABLE_SELF_AWARENESS_OBSERVATION
        config.ENABLE_SELF_AWARENESS_OBSERVATION = False
        try:
            self._cx._m40_observe_self_awareness("回复", "cid-x")
            self.assertEqual(self._logs, [])
        finally:
            config.ENABLE_SELF_AWARENESS_OBSERVATION = _orig

    def test_53_no_engine_silent(self):
        self._cx.self_awareness = None
        self._cx._m40_observe_self_awareness("回复", "cid-y")
        self.assertEqual(self._logs, [])

    def test_54_engine_without_api_silent(self):
        """★B156-3 T-A09 接线点：生产 _m40_observe_self_awareness 经 T-113b 加固，
        注入对象缺少 get_public_summary（假能力标记）时由「静默 return」改为
        「首报 WARNING 并安全旁路」。本测试原守「空日志」合约已过期，改为守
        「不堵溃 + 记录一条 WARNING（假能力标记拦截）。
        """
        class _Bare:
            pass

        self._cx.self_awareness = _Bare()
        self._cx._m40_observe_self_awareness("回复", "cid-z")
        # 不堵溃（观测旁路安全降级），且对假能力标记首报一条 WARNING
        self.assertEqual(len(self._logs), 1)
        _msg = self._logs[0][1]
        self.assertIn("get_public_summary", _msg)
        self.assertIn("假能力标记", _msg)

    def test_55_influence_flag_reflected_in_log(self):
        _orig = config.ENABLE_SELF_AWARENESS_INFLUENCE_DECISION
        config.ENABLE_SELF_AWARENESS_INFLUENCE_DECISION = True
        try:
            self._cx._m40_observe_self_awareness("回复", "cid-i")
            self.assertIn("影响级=开", self._logs[0][1])
        finally:
            config.ENABLE_SELF_AWARENESS_INFLUENCE_DECISION = _orig
        # L1 仍不改变任何行为（仅日志标注）
        self.assertIsNone(self._cx._m40_observe_self_awareness("回复", "cid-i2"))

    def test_56_never_raises_on_broken_engine(self):
        class _Boom:
            def get_public_summary(self):
                raise RuntimeError("boom")

        self._cx.self_awareness = _Boom()
        self._cx._m40_observe_self_awareness("回复", "cid-b")  # 不得抛出


if __name__ == "__main__":
    unittest.main()
