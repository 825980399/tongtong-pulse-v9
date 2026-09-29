# -*- coding: utf-8 -*-
"""往期批次 相关任务（P1）门控单测：百科查询器 + 主动学习 wiki 双腿复通。

全部离线（替身 urlopen / 路由器），不发真实网络请求。覆盖任务书三项修复：
  1) UA 决策一处收口：首次请求使用合规 _get_ua()；仅被拒（403）后才轮换 _UA_POOL。
  2) 403 响应体留证：命中 403 时 warning 日志含前 512B 响应体 + Retry-After/X-Baidu-* 头。
  3) wiki 双腿断点复通：_rss_collect_for_direction 在 channel=='wiki' 时经
     DigestEvent.KNOWLEDGE 走正常消化链路（不再 return False 丢弃）。
"""
import io
import email
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                                  # noqa: E402
import time                                                    # noqa: E402
from urllib.error import HTTPError                            # noqa: E402

from nucleus.knowledge import WikiQuerier as _wq_mod          # noqa: E402
from nucleus.knowledge.WikiQuerier import (                   # noqa: E402
    _get_ua, _UA_POOL,
)
from nucleus.knowledge.WikiQuerier import WikiResult          # noqa: E402
from organs.motor.PulseLegs import PulseLegs, DigestEvent     # noqa: E402
import nucleus.knowledge.KnowledgeAcquisitionRouter as _router_mod  # noqa: E402


def _isolate_cache(testcase):
    _dir = __import__("tempfile").mkdtemp(prefix="wiki_cache_t105d_")
    testcase._orig_cache_dir = _wq_mod._CACHE_DIR
    testcase._tmp_cache_dir = _dir
    _wq_mod._CACHE_DIR = _dir


def _restore_cache(testcase):
    _wq_mod._CACHE_DIR = getattr(testcase, "_orig_cache_dir", _wq_mod._CACHE_DIR)
    _dir = getattr(testcase, "_tmp_cache_dir", None)
    if _dir and os.path.isdir(_dir):
        __import__("shutil").rmtree(_dir, ignore_errors=True)


class _FakeResp:
    def __init__(self, body=b"<html></html>"):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_403(code=403, body="403 反爬验证文本 body",
              hdrs="Retry-After: 10\nX-Baidu-Error: captcha\nX-Baidu-Trace: abc\n"):
    _hdrs = email.message_from_string(hdrs)
    _fp = io.BytesIO(body.encode("utf-8"))
    return HTTPError("https://x", code, "Forbidden", _hdrs, _fp)


# ============================================================ 1) UA 收口
class TestUAConvergence(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "ENABLE_WIKI_QUERIER_RATE_LIMIT", True)
        _isolate_cache(self)

    def tearDown(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = self._orig
        _restore_cache(self)

    def test_首次请求使用合规UA(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = True
        _seen = {}

        def _fake_urlopen(req, timeout=None):
            _seen["ua"] = req.headers.get("User-agent")
            return _FakeResp()

        with mock.patch.object(_wq_mod, "urlopen", _fake_urlopen):
            _wq_mod._default_fetch("https://x", timeout=1.0)
        self.assertEqual(_seen.get("ua"), _get_ua(),
                         "首次请求必须使用合规 UA（一处收口，避免被无条件轮换覆盖）")

    def test_被拒后轮换UA池(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = True
        _calls = []

        def _fake_urlopen(req, timeout=None):
            _calls.append(req.headers.get("User-agent"))
            if len(_calls) == 1:
                # 首次 403 → 触发重试（仅此时才轮换 UA 池）
                raise _make_403()
            return _FakeResp()

        with mock.patch.object(_wq_mod, "urlopen", _fake_urlopen), \
                mock.patch.object(time, "sleep", lambda *a, **k: None):
            _wq_mod._default_fetch("https://x", timeout=1.0)
        self.assertGreaterEqual(len(_calls), 2, "403 后应进入重试（轮换 UA）")
        self.assertEqual(_calls[0], _get_ua(), "首次仍用合规 UA")
        self.assertIn(_calls[1], _UA_POOL, "重试应轮换到 _UA_POOL")
        self.assertNotEqual(_calls[1], _get_ua(), "重试 UA 不应仍等于合规 UA")


# ============================================================ 2) 403 留证
class Test403Evidence(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "ENABLE_WIKI_QUERIER_RATE_LIMIT", True)
        _isolate_cache(self)

    def tearDown(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = self._orig
        _restore_cache(self)

    def test_403响应体留证含body与头(self):
        config.ENABLE_WIKI_QUERIER_RATE_LIMIT = True
        _logger_mock = mock.Mock()

        def _raise_403(req, timeout=None):
            raise _make_403()

        with mock.patch.object(_wq_mod, "urlopen", _raise_403), \
                mock.patch.object(time, "sleep", lambda *a, **k: None), \
                mock.patch.object(_wq_mod, "_logger", _logger_mock):
            with self.assertRaises(HTTPError):
                _wq_mod._default_fetch("https://x", timeout=1.0)
        _texts = [str(c.args[0]) for c in _logger_mock.warning.call_args_list]
        _joined = "\n".join(_texts)
        self.assertTrue(any("403 反爬命中" in t for t in _texts),
                        "应留证 403 反爬命中日志")
        self.assertIn("403 反爬验证文本 body", _joined, "应保留 403 响应体前 512B")
        self.assertIn("Retry-After", _joined, "应保留 Retry-After 头")
        self.assertIn("X-Baidu-Error", _joined, "应保留 X-Baidu-Error 头")


# ============================================================ 3) wiki 双腿断点复通
class _FakeRouter:
    def __init__(self, route):
        self._route = route

    def acquire(self, direction, intent):
        return self._route


def _bare_legs():
    """轻量实例（沿用 test_legs_learning_governance_m49 惯例：__new__ 绕 __init__）。"""
    l = PulseLegs.__new__(PulseLegs)
    l._learn_queue = []
    l._learn_active_directions = set()
    l._emit = mock.Mock()
    l._log = mock.Mock()
    l._learn_count = 0
    l._success_count = 0
    l._consecutive_failures = 0
    return l


class TestWikiBranch(unittest.TestCase):
    def setUp(self):
        self._orig_rss = getattr(config, "ENABLE_RSS_COLLECTOR", None)
        config.ENABLE_RSS_COLLECTOR = True

    def tearDown(self):
        if self._orig_rss is None:
            if hasattr(config, "ENABLE_RSS_COLLECTOR"):
                delattr(config, "ENABLE_RSS_COLLECTOR")
        else:
            config.ENABLE_RSS_COLLECTOR = self._orig_rss

    def _wiki_route(self):
        _wiki = WikiResult(keyword="黑洞", title="黑洞",
                           summary="黑洞是时空曲率大到光都无法逃逸的天体。",
                           url="https://baike.baidu.com/item/黑洞", fetched_at=0.0)
        return {"channel": "wiki", "payload": _wiki, "delegate_browser": False}

    def test_wiki命中经KNOWLEDGE消化返回True(self):
        l = _bare_legs()
        _router = _FakeRouter(self._wiki_route())
        with mock.patch.object(_router_mod, "get_shared_knowledge_router",
                               return_value=_router):
            _ok = l._rss_collect_for_direction("黑洞是什么", "high")
        self.assertTrue(_ok, "wiki 命中应返回 True（不再被丢弃）")
        self.assertEqual(l._emit.call_count, 1, "应恰好经一次 KNOWLEDGE 消化")
        _ev, _payload = l._emit.call_args[0][0], l._emit.call_args[0][1]
        self.assertIs(_ev, DigestEvent.KNOWLEDGE)
        _content = _payload["content"]
        self.assertIn("[主动学习·百科·", _content)
        self.assertIn("黑洞", _content)
        self.assertIn("光都无法逃逸", _content)
        self.assertEqual(_payload["source_organ"], "百科查询器")
        self.assertEqual(l._learn_count, 1)
        self.assertEqual(l._success_count, 1)

    def test_wiki无payload走原路径返回False(self):
        l = _bare_legs()
        _route = {"channel": "wiki", "payload": None, "delegate_browser": False}
        _router = _FakeRouter(_route)
        with mock.patch.object(_router_mod, "get_shared_knowledge_router",
                               return_value=_router):
            _ok = l._rss_collect_for_direction("测试方向", "high")
        self.assertFalse(_ok, "wiki 无 payload 应走原搜索路径（return False）")
        self.assertEqual(l._emit.call_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
