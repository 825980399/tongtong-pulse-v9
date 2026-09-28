# -*- coding: utf-8 -*-
"""主线第32批 门控测试：分支生成接入渠道并发管控（T4 / P2-188）。

背景：`_generate_branch_with_model` 第31批已走渠道池端点，但仍是**裸 HTTP** ——
不占渠道并发配额、无熔断保护。本批改为 acquire/release + record_result，
与 `PulseLung._call_via_channels` 同口径。
"""
import os
import sys
import threading
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
import nucleus.ssrf_guard as _ssrf  # noqa: E402
import nucleus.api_rate_limiter as _rl  # noqa: E402
from nucleus.llm.ChannelConcurrency import (  # noqa: E402
    get_channel_concurrency_manager,
    reset_channel_concurrency_manager,
)
from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402

_IW_SRC = open(os.path.join(_PROJECT_ROOT, "organs", "brain", "PulseInnerWorld.py"),
               encoding="utf-8").read()

_CH = "ark-ds-v4-flash"          # 取一个真实存在的渠道名
_OLD_HTTP = _ssrf.safe_http_json
_OLD_CFG = _rl.get_llm_call_config


class _Switch:
    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(config, k, v)
        return False


def _mk_iw():
    iw = PulseInnerWorld.__new__(PulseInnerWorld)
    iw._log = lambda *a, **k: None
    iw._model_cache = {}
    iw._model_cache_ttl = 3600
    iw._model_cache_max = 100
    iw._m31_branch_endpoint = lambda: (
        "https://example.invalid/v1/chat", "sk-test", "test-model", f"channel:{_CH}")
    return iw


def _fake_http_factory(hook=None):
    _content = "这是一段足够长的模拟分支内容用于通过十五字长度校验。"
    _resp = {"choices": [{"message": {"content": _content}}]}

    def _fake(url, method="POST", data=None, headers=None, timeout=None):
        if hook is not None:
            hook()
        return True, _resp
    return _fake


def _fake_cfg():
    return {"enable_rate_limit": False,
            "timeout_by_purpose": {"inner_world_chat": 5}}


class _Base(unittest.TestCase):
    def setUp(self):
        reset_channel_concurrency_manager()
        _ssrf.safe_http_json = _fake_http_factory()
        _rl.get_llm_call_config = _fake_cfg

    def tearDown(self):
        _ssrf.safe_http_json = _OLD_HTTP
        _rl.get_llm_call_config = _OLD_CFG
        reset_channel_concurrency_manager()


class TestSourceWiring(unittest.TestCase):
    def test_01_switch_default_on(self):
        self.assertTrue(getattr(config, "ENABLE_BRANCH_GEN_CONCURRENCY_GUARD", False))
        self.assertTrue(PulseInnerWorld._m32_branch_concurrency_on())

    def test_02_call_site_has_guard(self):
        for _k in ("_m32_mgr.acquire(", "_m32_mgr.release(_m32_ch)",
                   "_m32_mgr.record_result(", "_m32_slot"):
            self.assertIn(_k, _IW_SRC, f"缺少并发管控要素: {_k}")

    def test_03_release_in_finally(self):
        """release 必须在 finally 中（异常路径不得泄漏许可）。"""
        _i = _IW_SRC.index("_m32_slot = _m32_mgr.acquire(")
        _seg = _IW_SRC[_i:_i + 2600]
        self.assertIn("finally:", _seg)
        self.assertIn("_m32_mgr.release(_m32_ch)", _seg)


class TestConcurrencyGuard(_Base):
    """★核心：并发请求确实受渠道并发上限控制。"""

    def test_10_holds_slot_during_call(self):
        """调用期间该渠道的在途计数必须 +1（证明占用了许可）。"""
        _seen = {}

        def _hook():
            _mgr = get_channel_concurrency_manager()
            _seen["in_use"] = _mgr.get_channel_stats(_CH)["current_concurrent"]

        _ssrf.safe_http_json = _fake_http_factory(_hook)
        _iw = _mk_iw()
        with _Switch(ENABLE_LOCAL_REASONING_LENGTH_OPTIMIZATION=False):
            _r = _iw._generate_branch_with_model("测试问题", "现状分析", "请分析现状")
        self.assertIsNotNone(_r, "应成功返回分支内容")
        self.assertGreaterEqual(_seen.get("in_use", 0), 1,
                                "调用期间应持有渠道许可")

    def test_11_slot_released_after_call(self):
        """调用结束后许可必须归还（在途归零）。"""
        _iw = _mk_iw()
        with _Switch(ENABLE_LOCAL_REASONING_LENGTH_OPTIMIZATION=False):
            _iw._generate_branch_with_model("测试问题", "现状分析", "请分析现状")
        _mgr = get_channel_concurrency_manager()
        self.assertEqual(_mgr.get_channel_stats(_CH)["current_concurrent"], 0,
                         "流程结束不得残留许可占用")

    def test_12_skips_when_channel_full(self):
        """★渠道并发满 → 直接跳过本次生成（返回 None），不发起 HTTP。"""
        _mgr = get_channel_concurrency_manager()
        _mgr.register_channels(config.get_active_channels())
        _sem = _mgr.get_semaphore(_CH)
        _n = _sem.max_value
        for _ in range(_n):
            _sem.acquire(blocking=False)

        _http_called = []

        def _spy(*a, **k):
            _http_called.append(1)
            return True, {"choices": [{"message": {"content": "x" * 40}}]}

        _ssrf.safe_http_json = _spy
        _iw = _mk_iw()
        try:
            with _Switch(ENABLE_LOCAL_REASONING_LENGTH_OPTIMIZATION=False):
                _r = _iw._generate_branch_with_model("测试问题", "现状分析", "请分析现状")
            self.assertIsNone(_r, "渠道并发满时应跳过（返回 None）")
            self.assertFalse(_http_called, "并发满时不得发起 HTTP 调用")
        finally:
            for _ in range(_n):
                _sem.release()

    def test_13_switch_off_bypasses_guard(self):
        """★灰度关闭 → 不占许可（回退裸 HTTP），行为与改造前一致。"""
        _seen = {}
        _mgr = get_channel_concurrency_manager()
        _mgr.register_channels(config.get_active_channels())
        _sem = _mgr.get_semaphore(_CH)
        _n = _sem.max_value
        for _ in range(_n):                     # 占满也不该影响
            _sem.acquire(blocking=False)

        def _hook():
            _seen["in_use"] = _mgr.get_channel_stats(_CH)["current_concurrent"]

        _ssrf.safe_http_json = _fake_http_factory(_hook)
        _iw = _mk_iw()
        try:
            with _Switch(ENABLE_BRANCH_GEN_CONCURRENCY_GUARD=False,
                         ENABLE_LOCAL_REASONING_LENGTH_OPTIMIZATION=False):
                _r = _iw._generate_branch_with_model("测试问题", "现状分析", "请分析现状")
            self.assertIsNotNone(_r, "关闭开关时应正常发起调用（不占许可）")
        finally:
            for _ in range(_n):
                _sem.release()

    def test_14_single_endpoint_fallback_not_guarded(self):
        """单端点回退（source=remote_config）不是渠道池成员 → 不占许可。"""
        _iw = _mk_iw()
        _iw._m31_branch_endpoint = lambda: (
            "https://example.invalid/v1/chat", "sk", "m", "remote_config")
        with _Switch(ENABLE_LOCAL_REASONING_LENGTH_OPTIMIZATION=False):
            _r = _iw._generate_branch_with_model("测试问题", "现状分析", "请分析现状")
        self.assertIsNotNone(_r)

    def test_15_manager_unavailable_degrades_gracefully(self):
        """管控不可用（如导入失败）→ 降级直连，不得阻断分支生成。"""
        _iw = _mk_iw()
        import nucleus.llm.ChannelConcurrency as _cc
        _old = _cc.get_channel_concurrency_manager
        _cc.get_channel_concurrency_manager = lambda: (_ for _ in ()).throw(
            RuntimeError("simulated"))
        try:
            with _Switch(ENABLE_LOCAL_REASONING_LENGTH_OPTIMIZATION=False):
                _r = _iw._generate_branch_with_model("测试问题", "现状分析", "请分析现状")
            self.assertIsNotNone(_r, "管控不可用时应降级直连而非失败")
        finally:
            _cc.get_channel_concurrency_manager = _old


class TestConcurrentThreads(_Base):
    """多线程并发调用：在途许可数不得超过渠道上限。"""

    def test_20_never_exceeds_channel_max(self):
        _mgr = get_channel_concurrency_manager()
        _mgr.register_channels(config.get_active_channels())
        _max = _mgr.get_semaphore(_CH).max_value
        _peak = {"v": 0}
        _lock = threading.Lock()

        def _hook():
            _cur = _mgr.get_channel_stats(_CH)["current_concurrent"]
            with _lock:
                _peak["v"] = max(_peak["v"], _cur)

        _ssrf.safe_http_json = _fake_http_factory(_hook)
        _errs = []

        def _worker():
            try:
                _mk_iw()._generate_branch_with_model("并发问题", "现状分析", "请分析现状")
            except Exception as _e:
                _errs.append(_e)

        _ths = [threading.Thread(target=_worker) for _ in range(_max + 4)]
        with _Switch(ENABLE_LOCAL_REASONING_LENGTH_OPTIMIZATION=False):
            for _t in _ths:
                _t.start()
            for _t in _ths:
                _t.join(timeout=30)
        self.assertFalse(_errs, f"并发调用不应抛异常: {_errs[:2]}")
        self.assertLessEqual(_peak["v"], _max,
                             f"在途许可 {_peak['v']} 不得超过渠道上限 {_max}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
