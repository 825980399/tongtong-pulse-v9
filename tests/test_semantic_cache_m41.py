# -*- coding: utf-8 -*-
"""第41批 T3 门控测试：语义缓存器 v0（L1 观测级）。

★全部使用**注入的确定性假编码器** —— 不加载 90MB 真模型。
★隔离：``base_dir`` 指向项目 ``tmp/`` 下的临时目录，**绝不写生产 data/cache**。
"""
import hashlib
import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_sc = importlib.import_module("nucleus.llm.semantic_cache")


class _MapEncoder:
    """预置映射编码器：完全可控（测试用）。"""

    def __init__(self, mapping):
        self._m = dict(mapping)

    def encode_one(self, text):
        return self._m.get(text)


class _HashEncoder:
    """确定性假编码器：按文本 sha256 生成归一化向量（相同文本 → 相同向量）。

    ★``_MapEncoder({})`` 对**任意**文本返回 ``None`` → ``add()`` 拒绝写入，
    故凡"需要真正写入"的测试必须用本件。
    """

    def __init__(self, dim=8):
        self.dim = dim

    def encode_one(self, text):
        if not text:
            return None
        _h = hashlib.sha256(text.encode("utf-8")).digest()
        _v = [float(_h[i % len(_h)]) - 128.0 for i in range(self.dim)]
        _n = sum(x * x for x in _v) ** 0.5 or 1.0
        return [x / _n for x in _v]


def _vec(*vals):
    _n = sum(v * v for v in vals) ** 0.5 or 1.0
    return [v / _n for v in vals]


def _pytest_tmp_root():
    """★主线第46批 T4（P2-301/307）：测试隔离目录改用【项目根/.pytest_tmp/】。

    背景：旧实现 ``mkdtemp(prefix=..., dir=<项目>/tmp)`` 会在项目 ``tmp/`` 下
    持续留下隔离目录（``m41_t2_*`` / ``m43dq_*`` / ``_m27_*`` ...），tearDown
    清不干净时就变成"历史残留"，并与 tmp 清理工具互相打架。

    现改为项目根下的 ``.pytest_tmp/``：
      * **与项目同盘** —— Windows 跨盘 ``shutil.move`` 会 copy+unlink，
        触发沙箱删除配额（m41 实测教训），故不能用 ``tempfile.gettempdir()``
      * 不在 ``tmp/`` 下 → tmp 清理判据无需再为隔离目录开特例
      * 已纳入 ``.gitignore``
      * 可用环境变量 ``PULSE_TEST_TMP_ROOT`` 覆盖（调试/隔离用）
    """
    _env = os.environ.get("PULSE_TEST_TMP_ROOT")
    if _env:
        _d = _env
    else:
        _d = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".pytest_tmp")
    os.makedirs(_d, exist_ok=True)
    return _d


class _Base(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.mkdtemp(
            prefix="m41_t3_", dir=_pytest_tmp_root())
        self._saved = {k: getattr(config, k, None) for k in
                       ("ENABLE_SEMANTIC_CACHE_OBSERVE", "SEMANTIC_CACHE_THRESHOLD",
                        "SEMANTIC_CACHE_CAPACITY", "SEMANTIC_CACHE_TTL_DAYS",
                        "SEMANTIC_CACHE_MAX_TEXT")}

    def tearDown(self):
        for _k, _v in self._saved.items():
            if _v is not None:
                setattr(config, _k, _v)
        shutil.rmtree(self._root, ignore_errors=True)

    def _cache(self, encoder=None, auto_start=False):
        return _sc.SemanticCache(base_dir=self._root, encoder=encoder,
                                 auto_start=auto_start)


class TestConfig(_Base):
    def test_01_defaults(self):
        _c = self._cache()
        self.assertTrue(_c.enabled())
        # ★第45批 T1 反向同步：阈值经实测校准 0.92 → 0.85（保持 == 不放宽）
        self.assertEqual(_c.threshold(), 0.85)
        self.assertEqual(_c.capacity(), 10000)
        self.assertAlmostEqual(_c.ttl_sec(), 7 * 86400.0)

    def test_02_switch_off(self):
        config.ENABLE_SEMANTIC_CACHE_OBSERVE = False
        _c = self._cache()
        self.assertFalse(_c.enabled())
        self.assertFalse(_c.observe_async("a", "b"))

    def test_03_dir_override(self):
        _c = self._cache()
        self.assertEqual(_c.base_dir(), self._root)
        self.assertTrue(_c.cache_file().endswith("semantic_cache.jsonl"))


class TestCosAndLookup(_Base):
    def test_10_identical_is_one(self):
        _e = _MapEncoder({"q": _vec(1, 0, 0, 0)})
        _c = self._cache(_e)
        _c.add("q", "答", vec=_vec(1, 0, 0, 0))
        _r = _c.lookup("q", vec=_vec(1, 0, 0, 0))
        self.assertAlmostEqual(_r["similarity"], 1.0, places=4)
        self.assertTrue(_r["hit"])

    def test_11_below_threshold_is_miss(self):
        _c = self._cache(_MapEncoder({}))
        _c.add("q", "答", vec=_vec(1, 0, 0, 0))
        _r = _c.lookup("q", vec=_vec(0, 1, 0, 0))     # 正交 → cos=0
        self.assertFalse(_r["hit"])
        self.assertLess(_r["similarity"], 0.85)

    def test_12_threshold_boundary(self):
        config.SEMANTIC_CACHE_THRESHOLD = 0.5
        _c = self._cache(_MapEncoder({}))
        _c.add("q", "答", vec=_vec(1, 0, 0, 0))
        _r = _c.lookup("q", vec=_vec(1, 1, 0, 0))     # cos≈0.707
        self.assertTrue(_r["hit"])

    def test_13_hit_count_increments(self):
        _c = self._cache(_MapEncoder({}))
        _c.add("q", "答", vec=_vec(1, 0, 0, 0))
        _c.lookup("q", vec=_vec(1, 0, 0, 0))
        _r = _c.lookup("q", vec=_vec(1, 0, 0, 0))
        self.assertEqual(_r["hit_count"], 2)

    def test_14_lookup_never_returns_content(self):
        """★L1 红线：lookup 的返回**不含** response/prompt 内容。"""
        _c = self._cache(_MapEncoder({}))
        _c.add("敏感问题", "敏感回答", vec=_vec(1, 0, 0, 0))
        _r = _c.lookup("敏感问题", vec=_vec(1, 0, 0, 0))
        self.assertNotIn("response", _r)
        self.assertNotIn("prompt", _r)
        self.assertNotIn("敏感回答", json.dumps(_r, ensure_ascii=False))

    def test_15_empty_prompt(self):
        _c = self._cache(_MapEncoder({}))
        self.assertIsNone(_c.lookup(""))
        self.assertFalse(_c.add("", "x"))

    def test_16_no_entries(self):
        _c = self._cache(_MapEncoder({}))
        self.assertIsNone(_c.lookup("q", vec=_vec(1, 0, 0, 0)))

    def test_17_encoder_failure_safe(self):
        class _Boom:
            def encode_one(self, text):
                raise RuntimeError("boom")

        _c = self._cache(_Boom())
        self.assertIsNone(_c.lookup("q"))
        self.assertFalse(_c.add("q", "a"))


class TestPersistence(_Base):
    def test_20_jsonl_format(self):
        _c = self._cache(_MapEncoder({}))
        _c.add("q1", "a1", vec=_vec(1, 0, 0, 0))
        _c.add("q2", "a2", vec=_vec(0, 1, 0, 0))
        _fp = _c.cache_file()
        self.assertTrue(os.path.isfile(_fp))
        _rows = [json.loads(x) for x in io.open(_fp, encoding="utf-8") if x.strip()]
        self.assertEqual(len(_rows), 2)
        for _k in ("hash", "prompt", "response", "ts", "hit_count", "vec"):
            self.assertIn(_k, _rows[0])

    def test_21_reload_roundtrip(self):
        _c1 = self._cache(_MapEncoder({}))
        _c1.add("q1", "a1", vec=_vec(1, 0, 0, 0))
        _c2 = self._cache(_MapEncoder({}))
        _r = _c2.lookup("q1", vec=_vec(1, 0, 0, 0))
        self.assertIsNotNone(_r)
        self.assertTrue(_r["hit"])

    def test_22_dedup_by_hash(self):
        _c = self._cache(_MapEncoder({}))
        _c.add("same", "a1", vec=_vec(1, 0, 0, 0))
        _c.add("same", "a2", vec=_vec(1, 0, 0, 0))
        self.assertEqual(_c.stats()["entries"], 1)

    def test_23_lru_eviction(self):
        config.SEMANTIC_CACHE_CAPACITY = 3
        _c = self._cache(_MapEncoder({}))
        for _i in range(5):
            _c.add("q%d" % _i, "a", vec=_vec(1, 0, 0, 0))
            time.sleep(0.005)
        self.assertEqual(_c.stats()["entries"], 3)
        # 最新的应保留
        _r = _c.lookup("q4", vec=_vec(1, 0, 0, 0))
        self.assertIsNotNone(_r)

    def test_24_ttl_expired_skipped(self):
        config.SEMANTIC_CACHE_TTL_DAYS = 1
        _c = self._cache(_MapEncoder({}))
        _c.add("q", "a", vec=_vec(1, 0, 0, 0))
        # 手工把 ts 改老 → 新实例加载时应过滤
        _fp = _c.cache_file()
        _rows = [json.loads(x) for x in io.open(_fp, encoding="utf-8") if x.strip()]
        _rows[0]["ts"] = time.time() - 5 * 86400
        with io.open(_fp, "w", encoding="utf-8") as f:
            for _r in _rows:
                f.write(json.dumps(_r, ensure_ascii=False) + "\n")
        _c2 = self._cache(_MapEncoder({}))
        self.assertEqual(_c2.stats()["entries"], 0)

    def test_25_truncate(self):
        config.SEMANTIC_CACHE_MAX_TEXT = 10
        _c = self._cache(_MapEncoder({}))
        _c.add("q" * 100, "a" * 100, vec=_vec(1, 0, 0, 0))
        _rows = [json.loads(x) for x in io.open(_c.cache_file(), encoding="utf-8")
                 if x.strip()]
        self.assertEqual(len(_rows[0]["prompt"]), 10)
        self.assertEqual(len(_rows[0]["response"]), 10)


class TestObserveAsync(_Base):
    def test_30_dispatch_and_process(self):
        _e = _HashEncoder()
        _c = self._cache(_e)
        _c._ensure_worker = lambda: None      # 禁用后台线程 → 消除队列消费竞态
        self.assertTrue(_c.observe_async("q", "a"))
        self.assertEqual(_c.process_pending(), 1)
        self.assertEqual(_c.stats()["observed"], 1)
        self.assertEqual(_c.stats()["entries"], 1)

    def test_31_queue_full_dropped(self):
        _c = self._cache(_HashEncoder())
        _c._q = __import__("queue").Queue(maxsize=1)
        _c._ensure_worker = lambda: None
        self.assertTrue(_c.observe_async("q1", "a"))
        self.assertFalse(_c.observe_async("q2", "a"))
        self.assertEqual(_c.stats()["dropped"], 1)

    def test_32_test_env_guard(self):
        """★pytest 环境 + 未注入编码器 → 不投递（防加载真模型）。"""
        _c = _sc.SemanticCache(base_dir=self._root, encoder=None, auto_start=False)
        self.assertFalse(_c.observe_async("q", "a"))

    def test_33_worker_thread_lifecycle(self):
        _c = self._cache(_HashEncoder(), auto_start=True)
        _c.observe_async("q", "a")
        for _ in range(30):          # 等后台线程消费（最多 3s）
            if _c.stats()["entries"] >= 1:
                break
            time.sleep(0.1)
        _c.close(timeout=3.0)
        self.assertEqual(_c.stats()["entries"], 1)

    def test_34_hit_recorded_second_time(self):
        _e = _HashEncoder()
        _c = self._cache(_e)
        _c._ensure_worker = lambda: None
        _c.observe_async("q", "a1")
        _c.process_pending()
        _c.observe_async("q", "a2")
        _c.process_pending()
        self.assertEqual(_c.stats()["hits"], 1)      # 第二次命中

    def test_35_hit_rate(self):
        _c = self._cache(_HashEncoder())
        _c.add("q", "a", vec=_vec(1, 0, 0, 0))
        _c.lookup("q", vec=_vec(1, 0, 0, 0))         # hit
        _c.lookup("q", vec=_vec(0, 1, 0, 0))         # miss
        self.assertEqual(_c.hit_rate(), 0.5)

    def test_36_stats_fields(self):
        _c = self._cache(_HashEncoder())
        _s = _c.stats()
        for _k in ("enabled", "entries", "capacity", "threshold", "ttl_days",
                   "base_dir", "observed", "hits", "misses", "errors",
                   "writes", "dropped"):
            self.assertIn(_k, _s)


class TestSingleton(_Base):
    def setUp(self):
        super().setUp()
        _sc.reset_semantic_cache()

    def tearDown(self):
        _sc.reset_semantic_cache()
        super().tearDown()

    def test_40_singleton(self):
        _a = _sc.get_semantic_cache()
        _b = _sc.get_semantic_cache()
        self.assertIs(_a, _b)

    def test_41_reset(self):
        _a = _sc.get_semantic_cache()
        _sc.reset_semantic_cache()
        self.assertIsNot(_sc.get_semantic_cache(), _a)

    def test_42_observe_call_interface(self):
        self.assertIsInstance(_sc.observe_call("q", "a"), bool)

    def test_43_switch_off_observe_call(self):
        config.ENABLE_SEMANTIC_CACHE_OBSERVE = False
        self.assertFalse(_sc.observe_call("q", "a"))


if __name__ == "__main__":
    unittest.main()
