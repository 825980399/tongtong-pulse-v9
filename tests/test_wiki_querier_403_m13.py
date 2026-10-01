# -*- coding: utf-8 -*-
"""主线第13批 T2 / P2-85：WikiQuerier 403 反爬治理 门控单测。

覆盖（全部离线，用 fetch_fn 替身，不发真实网络请求）：
  1) 合规 UA：开关开启时使用 config 中的含联系方式 UA；关闭时回落原 _UA
  2) 请求间隔：连续两次 query_baidu 之间至少间隔 min_interval（开关开启时）
  3) 开关关闭：不节流（_throttle 不 sleep）
  4) 403 降级：_default_fetch 遇 403 重试一次后仍 403 → 上抛；统计 forbidden_403
  5) 403 后 query() 返回 None（fallback 浏览器路径）
  6) <2 字超短词预处理：单字查询补全为「X是什么」形态
  7) 关闭开关时不补全
"""
import os
import sys
import time
import unittest
from urllib.error import HTTPError

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

import config
from nucleus.knowledge import WikiQuerier as _wq_mod
from nucleus.knowledge.WikiQuerier import WikiQuerier, _get_min_interval, _get_ua


def _make_querier(fetch_fn, cfg=None):
    return WikiQuerier(cfg=cfg, fetch_fn=fetch_fn)


def _isolate_cache(testcase):
    """把 WikiQuerier 模块级 _CACHE_DIR 指向本用例独享的临时目录。

    否则同一次 pytest 运行内用例共享 data/wiki_cache/，前序用例的缓存
    会让后续用例直接命中、绕过 fetch_fn（P2-85 测试隔离修复）。
    """
    import tempfile as _tf
    _dir = _tf.mkdtemp(prefix="wiki_cache_m13_")
    testcase._orig_cache_dir = _wq_mod._CACHE_DIR
    testcase._tmp_cache_dir = _dir
    _wq_mod._CACHE_DIR = _dir


def _restore_cache(testcase):
    import shutil
    _wq_mod._CACHE_DIR = getattr(testcase, "_orig_cache_dir", _wq_mod._CACHE_DIR)
    _dir = getattr(testcase, "_tmp_cache_dir", None)
    if _dir and os.path.isdir(_dir):
        shutil.rmtree(_dir, ignore_errors=True)


_HTML_OK = ('<html><head><meta name="description" content="这是一个足够长的百科摘要内容，'
            '用于通过长度校验的测试文本。"><title>测试词条_百度百科</title></head>'
            '<body></body></html>')


class TestUA(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "ENABLE_WIKI_QUERIER_RATE_LIMIT", True)

    def tearDown(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = self._orig

    def test_开启时使用合规UA含联系方式(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = True
        _ua = _get_ua()
        self.assertIn("mailto:", _ua)
        self.assertIn("Mozilla", _ua)

    def test_关闭时回落原UA(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = False
        _ua = _get_ua()
        self.assertNotIn("mailto:", _ua)


class TestThrottle(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "ENABLE_WIKI_QUERIER_RATE_LIMIT", True)
        self._orig_interval = getattr(config, "WIKI_QUERIER_MIN_INTERVAL", 1.0)
        _isolate_cache(self)

    def tearDown(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = self._orig
        config.WIKI_QUERIER_MIN_INTERVAL = self._orig_interval
        _restore_cache(self)

    def test_开启时间隔生效(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = True
        config.WIKI_QUERIER_MIN_INTERVAL = 0.3
        _q = _make_querier(lambda url, timeout=8.0: _HTML_OK)
        _q._throttle()           # 第一次不 sleep
        _t0 = time.time()
        _q._throttle()           # 第二次应等 ~0.3s
        _elapsed = time.time() - _t0
        self.assertGreaterEqual(_elapsed, 0.25)
        self.assertEqual(_q._stats["rate_limited"], 1)

    def test_关闭时不节流(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = False
        config.WIKI_QUERIER_MIN_INTERVAL = 5.0
        _q = _make_querier(lambda url, timeout=8.0: _HTML_OK)
        _q._throttle()
        _t0 = time.time()
        _q._throttle()
        _elapsed = time.time() - _t0
        self.assertLess(_elapsed, 0.2)
        self.assertEqual(_q._stats["rate_limited"], 0)

    def test_关闭时最小间隔为0(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = False
        self.assertEqual(_get_min_interval(), 0.0)


class Test403Degrade(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "ENABLE_WIKI_QUERIER_RATE_LIMIT", True)
        _isolate_cache(self)

    def tearDown(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = self._orig
        _restore_cache(self)

    def test_query_403返回None并统计(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = True

        def _fake_fetch(url, timeout=8.0):
            raise HTTPError(url, 403, "Forbidden", {}, None)

        _q = _make_querier(_fake_fetch)
        _r = _q.query("盲区")
        self.assertIsNone(_r)
        self.assertEqual(_q._stats["forbidden_403"], 1)
        self.assertEqual(_q._stats["fetch_fail"], 1)

    def test_default_fetch_403重试一次后上抛(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = True
        _calls = {"n": 0}

        def _fake_urlopen(req, timeout=None):
            _calls["n"] += 1
            raise HTTPError("http://127.0.0.1/zh/wiki/x", 403, "Forbidden", {}, None)

        _orig_urlopen = _wq_mod.urlopen
        _wq_mod.urlopen = _fake_urlopen
        try:
            with self.assertRaises(HTTPError):
                _wq_mod._default_fetch("http://127.0.0.1/zh/wiki/x", timeout=1.0)
            # ★主线第65批 T6：重试次数由 1 次提升至 2 次（共 3 次请求），降低 403 反爬瞬时失败率
            self.assertEqual(_calls["n"], 3, "开关开启时应重试 2 次（共 3 次请求）")
        finally:
            _wq_mod.urlopen = _orig_urlopen


class TestShortKeywordPreprocess(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "ENABLE_WIKI_QUERIER_RATE_LIMIT", True)
        self._seen = []
        self._orig_interval = getattr(config, "WIKI_QUERIER_MIN_INTERVAL", 1.0)
        config.WIKI_QUERIER_MIN_INTERVAL = 0.0
        _isolate_cache(self)

    def tearDown(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = self._orig
        config.WIKI_QUERIER_MIN_INTERVAL = self._orig_interval
        _restore_cache(self)

    def _spy_querier(self):
        _seen = self._seen

        def _fake_fetch(url, timeout=8.0):
            _seen.append(url)
            return _HTML_OK

        return _make_querier(_fake_fetch)

    def test_单字查询补全为是什么(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = True
        _q = self._spy_querier()
        _q.query("水")
        # URL 中包含 quote("水是什么")
        from urllib.parse import quote
        self.assertTrue(any(quote("水是什么") in u for u in self._seen), self._seen)

    def test_关闭开关时单字不补全(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = False
        _q = self._spy_querier()
        _q.query("水")
        from urllib.parse import quote
        self.assertTrue(any(quote("水是什么") not in u for u in self._seen), self._seen)

    def test_多字查询不受影响(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = True
        _q = self._spy_querier()
        _q.query("盲区")
        from urllib.parse import quote
        self.assertTrue(any(quote("盲区") in u and quote("盲区是什么") not in u
                            for u in self._seen), self._seen)


if __name__ == "__main__":
    unittest.main(verbosity=2)
