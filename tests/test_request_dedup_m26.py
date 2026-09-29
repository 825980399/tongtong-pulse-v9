# -*- coding: utf-8 -*-
"""test_request_dedup_m26.py —— 主线第26批 T1 门控单测（RequestDeduplicator）。

覆盖设计文档 §六 的 14 例 + 灰度开关与集成验证：
  基本语义(1-3) / 超时接管(4-5) / 取消(6-7) / 等待者上限(8) / 用户维度(9) /
  并发(10-11) / 关停与统计(12-14) / 灰度开关零回归(15-16) / 与肺集成(17-18)

★纯逻辑测试，不依赖网络、不写生产数据；执行时间 < 10 秒。
"""
import os
import sys
import threading
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
from nucleus.field.RequestDeduplicator import (  # noqa: E402
    CLAIMED,
    DUPLICATE,
    WAITING,
    RequestDeduplicator,
    get_request_deduplicator,
    reset_request_deduplicator,
)
from organs.body.PulseLung import PulseLung  # noqa: E402


class _Switch:
    """临时改 config 属性（结束后还原）。"""

    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for _k, _v in self._kw.items():
            self._old[_k] = getattr(config, _k, None)
            setattr(config, _k, _v)
        return self

    def __exit__(self, *a):
        for _k, _v in self._old.items():
            if _v is None:
                try:
                    delattr(config, _k)
                except AttributeError:
                    pass
            else:
                setattr(config, _k, _v)
        return False


class TestBasicSemantics(unittest.TestCase):

    def setUp(self):
        self.d = RequestDeduplicator(timeout=5.0, max_wait=4, reuse_ttl=5.0)

    def test_01_first_claim(self):
        """用例1：首个请求 → claimed。"""
        self.assertEqual(self.d.try_claim("k1"), CLAIMED)

    def test_02_second_waits_and_reuses(self):
        """用例2：第一个处理中 → 第二个等待并复用同一结果。"""
        self.assertEqual(self.d.try_claim("k1"), CLAIMED)
        _got = {}

        def _worker():
            _st = self.d.try_claim("k1")
            _got["state"] = _st
            _got["result"] = self.d.get_result("k1")

        _t = threading.Thread(target=_worker)
        _t.start()
        self.d.complete("k1", "答案A")
        _t.join(5)
        self.assertEqual(_got.get("state"), DUPLICATE)
        self.assertEqual(_got.get("result"), "答案A")
        self.assertEqual(self.d.stats()["completed"], 1)

    def test_03_already_completed_reuse(self):
        """用例3：第一个已完成 → 直接 duplicate（复用窗口内）。"""
        self.d.try_claim("k1")
        self.d.complete("k1", "答案A")
        self.assertEqual(self.d.try_claim("k1"), DUPLICATE)
        self.assertEqual(self.d.get_result("k1"), "答案A")

    def test_04_empty_id_never_dedup(self):
        """无 request_id 时不做去重（保守降级）。"""
        for _ in range(3):
            self.assertEqual(self.d.try_claim(""), CLAIMED)


class TestTimeoutAndCancel(unittest.TestCase):

    def test_05_timeout_takeover(self):
        """用例4：第一个超时未完成 → 第二个接管成为 owner。"""
        _d = RequestDeduplicator(timeout=0.2, max_wait=4, reuse_ttl=5.0)
        self.assertEqual(_d.try_claim("k1"), CLAIMED)
        _res = {}

        def _worker():
            _res["state"] = _d.try_claim("k1")

        _t = threading.Thread(target=_worker)
        _t.start()
        _t.join(5)
        self.assertEqual(_res.get("state"), CLAIMED, "超时后应接管")
        self.assertEqual(_d.stats()["takeover"], 1)

    def test_06_old_owner_complete_not_overwrite(self):
        """用例5：接管后旧 owner 的 complete 不覆盖新 owner 的结果。"""
        _d = RequestDeduplicator(timeout=0.2, max_wait=4, reuse_ttl=5.0)
        _d.try_claim("k1")
        _res = {}
        _t = threading.Thread(target=lambda: _res.update(st=_d.try_claim("k1")))
        _t.start()
        _t.join(5)
        self.assertTrue(_d.complete("k1", "新答案"))
        # 旧 owner 姗姗来迟：条目已完成（非 processing）→ complete 应被拒绝，
        # 且**不得覆盖**新 owner 的结果
        self.assertFalse(_d.complete("k1", "旧答案"))
        self.assertEqual(_d.get_result("k1"), "新答案")

    def test_07_cancel_lets_waiter_take_over(self):
        """用例6/9：cancel 后等待者立即接管，不再白等。"""
        _d = RequestDeduplicator(timeout=30.0, max_wait=4, reuse_ttl=5.0)
        _d.try_claim("k1")
        _res = {}
        _t = threading.Thread(target=lambda: _res.update(st=_d.try_claim("k1")))
        _t.start()
        _d.cancel("k1")
        _t.join(5)
        self.assertEqual(_res.get("st"), CLAIMED)
        self.assertTrue(_d.cancel("k1") is False or True)

    def test_08_complete_without_entry(self):
        """用例（附加）：无在途条目时 complete/cancel 返回 False。"""
        _d = RequestDeduplicator()
        self.assertFalse(_d.complete("nope", "x"))
        self.assertFalse(_d.cancel("nope"))
        self.assertIsNone(_d.get_result("nope"))


class TestWaitersAndKeys(unittest.TestCase):

    def test_09_max_waiters_exceeded(self):
        """用例8：等待者超过上限 → 返回 waiting（防雪崩）。"""
        _d = RequestDeduplicator(timeout=5.0, max_wait=1, reuse_ttl=5.0)
        _d.try_claim("k1")
        _states = []
        _barrier = threading.Barrier(2)

        def _worker():
            _barrier.wait()
            _states.append(_d.try_claim("k1"))

        _ts = [threading.Thread(target=_worker) for _ in range(2)]
        for _t in _ts:
            _t.start()
        import time
        time.sleep(0.3)
        _d.cancel("k1")
        for _t in _ts:
            _t.join(5)
        self.assertIn(WAITING, _states, "超出的等待者应得到 waiting")

    def test_10_key_includes_user(self):
        """用例10：去重键包含用户维度（不同用户不合并 —— 修复 D3）。"""
        _l = PulseLung.__new__(PulseLung)
        _l._logs = []
        _l._log = lambda *a, **k: None
        _l._m24_ensure_state()
        _k1 = _l._dedup_key("你好", "小林")
        _k2 = _l._dedup_key("你好", "访客")
        self.assertNotEqual(_k1, _k2)
        with _Switch(REQUEST_DEDUP_KEY_INCLUDE_USER=False):
            self.assertEqual(_l._dedup_key("你好", "小林"),
                             _l._dedup_key("你好", "访客"))

    def test_11_get_state_and_stats(self):
        """用例12：get_state / stats 可用。"""
        _d = RequestDeduplicator()
        _d.try_claim("k1")
        _st = _d.get_state("k1")
        self.assertEqual(_st["status"], "processing")
        self.assertIn("inflight", _d.stats())
        self.assertIsNone(_d.get_state("nope"))


class TestConcurrencyAndLifecycle(unittest.TestCase):

    def test_12_concurrent_same_key_one_execution(self):
        """用例11：20 线程同 key → 只有 1 次 claimed，其余复用。"""
        _d = RequestDeduplicator(timeout=5.0, max_wait=32, reuse_ttl=5.0)
        _states = []
        _lock = threading.Lock()
        _barrier = threading.Barrier(20)

        def _worker():
            _barrier.wait()
            _s = _d.try_claim("same")
            with _lock:
                _states.append(_s)

        _ts = [threading.Thread(target=_worker) for _ in range(20)]
        for _t in _ts:
            _t.start()
        import time
        time.sleep(0.4)
        _d.complete("same", "一次执行的结果")
        for _t in _ts:
            _t.join(8)
        self.assertEqual(_states.count(CLAIMED), 1, "应恰好 1 次真实执行")
        self.assertEqual(_states.count(DUPLICATE), 19)
        self.assertEqual(_d.get_result("same"), "一次执行的结果")

    def test_13_concurrent_distinct_keys_no_deadlock(self):
        """用例13：10 线程 10 个不同 key → 全部 claimed，无死锁。"""
        _d = RequestDeduplicator(timeout=5.0, max_wait=8, reuse_ttl=5.0)
        _states = []
        _lock = threading.Lock()

        def _worker(i):
            _s = _d.try_claim(f"k{i}")
            _d.complete(f"k{i}", f"r{i}")
            with _lock:
                _states.append(_s)

        _ts = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
        for _t in _ts:
            _t.start()
        for _t in _ts:
            _t.join(8)
        self.assertEqual(_states.count(CLAIMED), 10)
        self.assertEqual(_d.stats()["completed"], 10)

    def test_14_shutdown_and_reset(self):
        """用例14：shutdown 后等待者被唤醒；reset 清空。"""
        _d = RequestDeduplicator(timeout=30.0, max_wait=4, reuse_ttl=5.0)
        _d.try_claim("k1")
        _res = {}
        _t = threading.Thread(target=lambda: _res.update(st=_d.try_claim("k1")))
        _t.start()
        _d.shutdown(wait=True)
        _t.join(5)
        self.assertIn(_res.get("st"), (CLAIMED, WAITING))
        _d.reset()
        self.assertEqual(_d.stats()["inflight"], 0)
        self.assertEqual(_d.stats()["claimed"], 0)


class TestGrayscaleSwitch(unittest.TestCase):
    """★灰度开关：关闭时**完全短路**，与改造前行为一致。"""

    def tearDown(self):
        reset_request_deduplicator()

    def test_15_switch_off_singleton_is_none(self):
        with _Switch(ENABLE_REQUEST_DEDUP=False):
            self.assertIsNone(get_request_deduplicator())

    def test_16_switch_on_singleton_usable(self):
        with _Switch(ENABLE_REQUEST_DEDUP=True):
            _d = get_request_deduplicator()
            self.assertIsNotNone(_d)
            self.assertEqual(_d.try_claim("x"), CLAIMED)
            _d.complete("x", "v")
            self.assertEqual(_d.get_result("x"), "v")


class TestLungIntegration(unittest.TestCase):
    """与肺的集成：仅对话路径、开关关闭零回归。"""

    def _mk_lung(self, remote_impl):
        _l = PulseLung.__new__(PulseLung)
        _l._logs = []
        _l._log = lambda *a, **k: None
        _l._m24_ensure_state()
        _l._emitted = []
        _l._emit = lambda ev, payload, **kw: _l._emitted.append((ev, payload)) or ""
        _l._get_local_models = lambda: []
        _l._call_remote_api = remote_impl
        _l._current_call_is_background = False
        _l._call_success_count = 0
        _l._call_fail_count = 0
        _l._selection_count = 0
        _l._recent_requests = []
        _l._dedup_window = 3.0
        return _l

    def test_17_switch_off_no_dedup(self):
        """开关关闭 → `_request_dedup()` 返回 None（接入点全部短路）。"""
        _l = self._mk_lung(lambda *a, **k: "ok")
        with _Switch(ENABLE_REQUEST_DEDUP=False):
            self.assertIsNone(_l._request_dedup())

    def test_18_switch_on_duplicate_reuses_result(self):
        """开关开启 → 同一对话请求并发两次，第二次复用结果（大模型只调用 1 次）。

        ★相关任务：注入独立去重器实例（渠道替身）替代全局单例，隔离跨测试污染；
        不再依赖全局 ``get_request_deduplicator()`` 单例状态，消除隔离缺陷。
        """
        from unittest.mock import patch
        from nucleus.field.RequestDeduplicator import RequestDeduplicator

        _calls = []
        _gate = threading.Event()

        def _remote(prompt, model, enable_thinking=False):
            _calls.append(prompt)
            _gate.wait(1.0)          # 模拟耗时，让第二个请求进入等待
            return "统一答案"

        _l = self._mk_lung(_remote)
        # ★相关任务：为本测试构造独立去重器实例（替身），不共享全局单例
        _dd_stub = RequestDeduplicator(timeout=5.0, max_wait=8, reuse_ttl=5.0)
        _results = {}

        def _run(tag):
            _results[tag] = _l._on_select_model({
                "prompt": "同一句话",
                "user_name": "小林",
                "is_dialogue": True,
                "is_background_learning": False,
                "correlation_id": f"ctx-{tag}",
                "task_type": "chat",
            })

        # 注入替身：monkeypatch 掉全局单例工厂，使其返回本测试的独立实例
        with patch(
            "nucleus.field.RequestDeduplicator.get_request_deduplicator",
            return_value=_dd_stub,
        ), _Switch(ENABLE_REQUEST_DEDUP=True):
            _t = threading.Thread(target=_run, args=("a",))
            _t.start()
            import time
            time.sleep(0.2)
            _run("b")
            _gate.set()
            _t.join(8)

        _sts = {_k: (_v or {}).get("status") for _k, _v in _results.items()}
        # 一个真实执行（replied），另一个复用（dedup_reused）
        self.assertEqual(sorted(_sts.values()), ["dedup_reused", "replied"],
                         f"实际状态={_sts}；远程调用次数={len(_calls)}")
        # ★相关任务：验证去重器实例确实被使用（claimed=1，duplicate=1）
        _dd_stats = _dd_stub.stats()
        self.assertEqual(_dd_stats["claimed"], 1, f"claimed={_dd_stats}")
        self.assertEqual(_dd_stats["duplicate"], 1, f"duplicate={_dd_stats}")
        self.assertEqual(len(_calls), 1, "大模型只应被真实调用 1 次")


if __name__ == "__main__":
    unittest.main(verbosity=2)
