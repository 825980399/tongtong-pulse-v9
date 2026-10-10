# -*- coding: utf-8 -*-
"""主线第42批 T3（P1-265）门控测试：经验语义检索器。

覆盖：污染分析 / 候选过滤 / 语义检索与缓存 / 对比观测 / 开关 / IO 防御 / 接线。
★ 全部使用构造池 + mock 编码器（不加载真实模型，毫秒级）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
import unittest

import numpy as np

import config
from nucleus.mnemosyne.experience_retriever import (
    DEFAULT_POLLUTION_REPORT_PATH,
    ExperienceRetriever,
    analyze_pollution,
    experience_text,
    get_retriever,
    reset_retriever,
    retriever_enabled,
    save_pollution_report,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _pytest_tmp_root():
    """★主线第46批 T4（P2-301/307）：测试隔离目录改用【系统临时目录】。

    背景：旧实现 ``mkdtemp(prefix=..., dir=<项目>/tmp)`` 会在项目 ``tmp/`` 下
    持续留下隔离目录（``m41_t2_*`` / ``m43dq_*`` / ``_m27_*`` ...），tearDown
    清不干净时就变成"历史残留"，并与清理工具互相打架。

    现在改为系统临时目录下的 ``pulse_pytest/``：
      * 不污染项目 ``tmp/`` → 清理判据不再需要为它们开特例
      * 由操作系统回收 → 残留不再累积
      * 可用环境变量 ``PULSE_TEST_TMP_ROOT`` 覆盖（调试/隔离用）
    """
    _env = os.environ.get("PULSE_TEST_TMP_ROOT")
    if _env:
        _d = _env
    else:
        # ★与项目同盘：Windows 跨盘 shutil.move 会 copy+unlink →
        #   触发沙箱删除配额（m41 实测教训）。故不用 tempfile.gettempdir()，
        #   改用项目根下的 .pytest_tmp/（同盘 + 不污染 tmp/）。
        _d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".pytest_tmp")
    os.makedirs(_d, exist_ok=True)
    return _d


_TMP_ROOT = _pytest_tmp_root()


def _safe_rmtree(path):
    if not os.path.isdir(path):
        return
    for dp, dn, fn in os.walk(path, topdown=False):
        for i in range(0, len(fn), 50):
            for f in fn[i:i + 50]:
                try:
                    os.remove(os.path.join(dp, f))
                except BaseException as e:
                    print("cleanup warn: {}: {}".format(type(e).__name__, e))
        try:
            os.rmdir(dp)
        except BaseException as e:
            print("cleanup warn: {}: {}".format(type(e).__name__, e))


class _MockEncoder:
    """关键词槽位向量：含同一关键词的文本必然同向（sim=1），便于确定性断言。"""

    _SLOTS = (("量子", 0), ("文档", 1), ("平衡", 2))

    def __init__(self, available=True, dim=16):
        self._av = available
        self._dim = dim
        self.encode_calls = 0
        self.one_calls = 0

    def is_available(self):
        return self._av

    def _vec(self, text):
        _v = np.zeros(self._dim, dtype="float32")
        _t = str(text)
        for _kw, _i in self._SLOTS:
            if _kw in _t:
                _v[_i] = 1.0
        if not _v.any():
            _v[9] = 1.0
        return _v

    def encode(self, texts):
        self.encode_calls += 1
        return np.array([self._vec(t) for t in texts], dtype="float32")

    def encode_one(self, text):
        self.one_calls += 1
        return self._vec(text)


class _FakePool:
    def __init__(self, exps):
        self._experiences = exps
        self.query_calls = 0

    def query_experiences(self, limit=20, **kw):
        self.query_calls += 1
        _o = sorted(self._experiences, key=lambda e: e.get("timestamp", 0), reverse=True)
        return [dict(e) for e in _o[:limit]]


def _mk(eid, motive, ts, polluted=False, qw=1.0, act=0, no_text=False):
    _e = {"id": eid, "motivation": motive, "summary": "我曾因{}而行动".format(motive),
          "timestamp": ts, "polluted": polluted, "quality_weight": qw,
          "activation_count": act, "is_summarized": True}
    if no_text:
        _e["motivation"] = ""
        _e["summary"] = ""
    return _e


def _pool():
    return _FakePool([
        _mk("clean_sim", "学习量子计算的基础原理", 500),
        _mk("clean_doc", "整理项目文档结构", 400),
        _mk("poll_a", "维持系统平衡", 300, polluted=True, qw=0.0),
        _mk("poll_b", "维持系统平衡", 200, polluted=True, qw=0.0),
        _mk("clean_third", "研究量子纠缠现象", 100),
        _mk("no_text", "x", 50, no_text=True),
    ])


# ================================================================ 工具函数
class TestHelpers(unittest.TestCase):
    def test_01_experience_text(self):
        self.assertIn("动机", experience_text({"motivation": "动机", "summary": "摘要"}))
        self.assertEqual(experience_text({"summary": "只有摘要"}), "只有摘要")
        self.assertEqual(experience_text({}), "")
        self.assertEqual(experience_text(None), "")


# ================================================================ 污染分析
class TestAnalyzePollution(unittest.TestCase):
    def test_10_counts(self):
        _r = analyze_pollution(pool=_pool())
        self.assertEqual(_r["total"], 6)
        self.assertEqual(_r["polluted"], 2)
        self.assertEqual(_r["clean"], 4)
        self.assertAlmostEqual(_r["pollution_rate"], round(2 / 6, 4))

    def test_11_findings(self):
        _r = analyze_pollution(pool=_pool())
        self.assertIsInstance(_r["findings"], list)
        _p = analyze_pollution(pool=_FakePool([_mk("a", "m", 1, polluted=True)] * 3
                                              + [_mk("b", "n", 2)]))
        self.assertTrue(any("污染率" in f for f in _p["findings"]))

    def test_12_empty_pool(self):
        _r = analyze_pollution(pool=_FakePool([]))
        self.assertEqual(_r["total"], 0)
        self.assertEqual(_r["pollution_rate"], 0.0)

    def test_13_bad_pool_is_safe(self):
        _r = analyze_pollution(pool=object())
        self.assertEqual(_r["total"], 0)


# ================================================================ 候选集
class TestCandidates(unittest.TestCase):
    def setUp(self):
        self.r = ExperienceRetriever(pool=_pool(), encoder=_MockEncoder())

    def test_20_excludes_polluted_by_default(self):
        _ids = [e["id"] for e in self.r.candidates()]
        self.assertNotIn("poll_a", _ids)
        self.assertNotIn("poll_b", _ids)
        self.assertIn("clean_sim", _ids)

    def test_21_include_polluted(self):
        _ids = [e["id"] for e in self.r.candidates(include_polluted=True)]
        self.assertIn("poll_a", _ids)
        self.assertIn("poll_b", _ids)

    def test_22_limit_applies(self):
        self.assertEqual(len(self.r.candidates(limit=2)), 2)

    def test_23_skips_empty_text(self):
        _ids = [e["id"] for e in self.r.candidates(include_polluted=True)]
        self.assertNotIn("no_text", _ids)

    def test_24_sorted_by_timestamp_desc(self):
        _ts = [e["timestamp"] for e in self.r.candidates()]
        self.assertEqual(_ts, sorted(_ts, reverse=True))


# ================================================================ 检索
class TestRetrieve(unittest.TestCase):
    def setUp(self):
        self.enc = _MockEncoder()
        self.r = ExperienceRetriever(pool=_pool(), encoder=self.enc, top_k=3)

    def test_30_topk_and_relevance(self):
        _h = self.r.retrieve("学习量子计算的基础原理", top_k=2)
        self.assertTrue(_h)
        self.assertEqual(_h[0]["id"], "clean_sim")
        self.assertAlmostEqual(_h[0]["_similarity"], 1.0, places=4)

    def test_31_no_polluted_in_results(self):
        _h = self.r.retrieve("维持系统平衡", top_k=3)
        self.assertTrue(all(not e.get("polluted") for e in _h))

    def test_32_encoder_unavailable(self):
        _r = ExperienceRetriever(pool=_pool(), encoder=_MockEncoder(available=False))
        self.assertEqual(_r.retrieve("量子"), [])
        self.assertFalse(_r.available())

    def test_33_empty_query(self):
        self.assertEqual(self.r.retrieve(""), [])
        self.assertEqual(self.r.retrieve(None), [])

    def test_34_vector_cache_reused(self):
        self.r.retrieve("量子", top_k=2)
        _c1 = self.enc.encode_calls
        self.r.retrieve("文档", top_k=2)
        self.assertEqual(self.enc.encode_calls, _c1, "候选集未变时不应重新批量编码")

    def test_35_quality_weight_affects_score(self):
        _p = _FakePool([
            _mk("low_q", "量子", 100, qw=0.0),
            _mk("high_q", "量子", 50, qw=1.0),
        ])
        _r = ExperienceRetriever(pool=_p, encoder=_MockEncoder(), top_k=2)
        _h = _r.retrieve("量子", top_k=2)
        self.assertEqual(len(_h), 2)
        self.assertGreater(_h[0]["_score"], _h[1]["_score"])
        self.assertEqual(_h[0]["id"], "high_q")


# ================================================================ 对比
class TestCompare(unittest.TestCase):
    def setUp(self):
        self.pool = _pool()
        self.r = ExperienceRetriever(pool=self.pool, encoder=_MockEncoder(), top_k=3)

    def test_40_compare_counts(self):
        _c = self.r.compare("量子", top_k=3)
        self.assertEqual(_c["new_count"], len(self.r.retrieve("量子", top_k=3)))
        self.assertEqual(_c["old_count"], len(self.r.baseline_retrieve("量子", limit=3)))
        self.assertLessEqual(_c["overlap"], min(_c["new_count"], _c["old_count"]))
        self.assertIsInstance(_c["old_ids"], list)

    def test_41_baseline_has_pollution_new_has_none(self):
        _c = self.r.compare("维持系统平衡", top_k=3)
        self.assertEqual(_c["new_polluted"], 0)
        self.assertGreater(_c["old_polluted"], 0)

    def test_42_baseline_uses_existing_query(self):
        self.r.baseline_retrieve("x", limit=3)
        self.assertGreater(self.pool.query_calls, 0)


# ================================================================ 观测
class TestObserve(unittest.TestCase):
    def setUp(self):
        self.logs = []

        class _L:
            def info(_self, msg, *a):
                self.logs.append(msg % a if a else msg)

        self.L = _L
        self.r = ExperienceRetriever(pool=_pool(), encoder=_MockEncoder(), top_k=3)

    def test_50_observe_log_format(self):
        _c = self.r.observe("量子", logger=self.L())
        self.assertIsNotNone(_c)
        self.assertIn("[经验检索]", self.logs[-1])
        self.assertIn("新检索器返回", self.logs[-1])
        self.assertIn("重叠", self.logs[-1])

    def test_51_observe_returns_none_when_disabled(self):
        _bak = config.ENABLE_EXPERIENCE_RETRIEVER_OBSERVE
        try:
            config.ENABLE_EXPERIENCE_RETRIEVER_OBSERVE = False
            self.assertFalse(retriever_enabled())
            self.assertIsNone(self.r.observe("量子"))
            self.assertEqual(self.r.observe_daily()["status"], "disabled")
        finally:
            config.ENABLE_EXPERIENCE_RETRIEVER_OBSERVE = _bak

    def test_52_observe_daily_aggregates(self):
        _s = self.r.observe_daily(sample=2, logger=self.L())
        self.assertEqual(_s["status"], "ok")
        self.assertEqual(_s["samples"], 2)
        self.assertIn("old_polluted_ratio_avg", _s)

    def test_53_observe_daily_encoder_unavailable(self):
        _r = ExperienceRetriever(pool=_pool(), encoder=_MockEncoder(available=False))
        self.assertEqual(_r.observe_daily()["status"], "encoder_unavailable")

    def test_54_stats_shape(self):
        _s = self.r.stats()
        for _k in ("enabled", "encoder_available", "top_k", "filter_polluted",
                   "cached_vectors", "observations"):
            self.assertIn(_k, _s)


# ================================================================ IO / 单例
class TestIOAndSingleton(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="m42er_", dir=_TMP_ROOT)

    def tearDown(self):
        _safe_rmtree(self.dir)

    def test_60_save_report_rejects_production_in_test_env(self):
        self.assertIsNone(save_pollution_report({"x": 1}))
        _prod = os.path.abspath(DEFAULT_POLLUTION_REPORT_PATH).replace("\\", "/").lower()
        self.assertIn("/data/experience/", _prod)

    def test_61_save_report_explicit_path(self):
        _p = os.path.join(self.dir, "p.json")
        self.assertEqual(save_pollution_report({"total": 7}, _p), _p)
        self.assertEqual(json.load(open(_p, encoding="utf-8"))["total"], 7)

    def test_62_singleton_reset(self):
        reset_retriever()
        _a = get_retriever()
        self.assertIs(_a, get_retriever())
        reset_retriever()
        self.assertIsNot(_a, get_retriever())
        reset_retriever()


# ================================================================ 接线
class TestWiring(unittest.TestCase):
    def test_70_config_switches(self):
        for _k in ("ENABLE_EXPERIENCE_RETRIEVER_OBSERVE", "EXPERIENCE_RETRIEVER_TOP_K",
                   "EXPERIENCE_RETRIEVER_FILTER_POLLUTED", "EXPERIENCE_RETRIEVER_SCAN_LIMIT"):
            self.assertTrue(hasattr(config, _k), _k)

    def test_71_daily_scheduler_observes(self):
        _src = open(os.path.join(_ROOT, "nucleus", "self_awareness", "DailyScheduler.py"),
                    encoding="utf-8").read()
        self.assertIn("observe_daily(", _src)
        self.assertIn("analyze_pollution(", _src)

    def test_72_tools_entry_exists(self):
        self.assertTrue(os.path.isfile(
            os.path.join(_ROOT, "tools", "run_experience_analysis.py")))

    def test_73_does_not_replace_existing_retriever(self):
        """L1 红线：模块不得改写 ExperiencePool 的查询方法。"""
        _src = open(os.path.join(_ROOT, "nucleus", "mnemosyne",
                                 "experience_retriever.py"), encoding="utf-8").read()
        self.assertNotIn("ExperiencePool.query_experiences =", _src)
        self.assertNotIn("setattr(pool, \"query_experiences\"", _src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
