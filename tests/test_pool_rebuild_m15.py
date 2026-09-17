# -*- coding: utf-8 -*-
"""
test_pool_rebuild_m15.py —— 主线第15批 任务2 门控单测（P1-92 进程池崩溃自动重建）

覆盖：崩溃检测→自动重建→重试提交、连续失败→降级同步、定期健康检查提前重建、
      灰度关闭→完全退回修复前行为、停机后禁止复活、重建统计结构。
"""
import os
import sys
import unittest
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402
import nucleus.reasoning.ReasoningWorkerPool as RWP  # noqa: E402
from nucleus.reasoning.ReasoningWorkerPool import ReasoningWorkerPool  # noqa: E402


class _FakePool:
    """假进程池：可指定前 N 次 submit 抛 BrokenProcessPool，或直接标记 broken。"""

    def __init__(self, max_workers=2, fail_times=0, broken=False):
        self._max_workers = max_workers
        self._broken = broken
        self._fail_times = fail_times
        self.submit_calls = 0
        self.shutdown_called = False

    def submit(self, fn, *args, **kwargs):
        self.submit_calls += 1
        if self._broken or self._fail_times > 0:
            self._fail_times -= 1
            # 真实 ProcessPoolExecutor 崩溃后会把 _broken 置 True，此处等价模拟，
            # 否则 _rebuild_pool 的「池仍可用」短路会阻止重建
            self._broken = True
            raise BrokenProcessPool("A child process terminated abruptly")
        f = Future()
        f.set_result({"func_name": args[0] if args else "?", "status": "fake-ok"})
        return f

    def shutdown(self, wait=False, cancel_futures=False):
        self.shutdown_called = True


class _FakeFactory:
    """按顺序产出假池；耗尽后重复最后一个。raise_times>0 时先抛构造异常。"""

    def __init__(self, specs, raise_times=0):
        self._specs = list(specs)
        self._raise_times = raise_times
        self.created = []
        self.construct_calls = 0

    def __call__(self, max_workers=None, **kw):
        self.construct_calls += 1
        if self._raise_times > 0:
            self._raise_times -= 1
            raise OSError("创建进程池失败(模拟)")
        spec = self._specs.pop(0) if len(self._specs) > 1 else self._specs[0]
        p = _FakePool(max_workers=max_workers or 2, **spec)
        self.created.append(p)
        return p


class _PoolHarness:
    """真实 ReasoningWorkerPool + 假 ProcessPoolExecutor。"""

    def __init__(self, specs, raise_times=0):
        self._orig = RWP.ProcessPoolExecutor
        self.factory = _FakeFactory(specs, raise_times=raise_times)
        # 替身必须覆盖整个用例生命周期（重建发生在 submit 期，而非 __init__ 期）
        RWP.ProcessPoolExecutor = self.factory
        self.pool = ReasoningWorkerPool()

    def close(self):
        try:
            self.pool.shutdown(timeout=0.1)
        except Exception:
            pass
        RWP.ProcessPoolExecutor = self._orig


class _Switch:
    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = getattr(config, k, None)
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                if hasattr(config, k):
                    delattr(config, k)
            else:
                setattr(config, k, v)
        return False


class TestConfigDefaults(unittest.TestCase):
    def test_defaults(self):
        self.assertIs(getattr(config, "ENABLE_POOL_AUTO_REBUILD", None), True)
        self.assertEqual(getattr(config, "POOL_REBUILD_MAX_RETRIES", None), 3)
        self.assertEqual(getattr(config, "POOL_HEALTH_CHECK_INTERVAL", None), 60)

    def test_stats_shape(self):
        h = _PoolHarness([{}])
        try:
            st = h.pool.get_rebuild_stats()
            for k in ("auto_rebuild_enabled", "max_retries", "health_check_interval",
                      "rebuild_count", "consecutive_failures", "broken_detected_count",
                      "rebuild_duration_ms_max", "pool_generation", "degraded_sync"):
                self.assertIn(k, st)
            # get_stats 也已并入
            self.assertIn("rebuild_count", h.pool.get_stats())
        finally:
            h.close()


class TestAutoRebuild(unittest.TestCase):
    def test_broken_pool_triggers_rebuild_and_retry(self):
        # 第 1 个池：首次 submit 抛 BrokenProcessPool；第 2 个池：正常
        h = _PoolHarness([{"fail_times": 1}, {}])
        try:
            self.assertEqual(h.pool._rebuild_count, 0)
            fut = h.pool.submit("X.task", 1, 2)
            self.assertIsNotNone(fut, "重建后重试提交应成功")
            self.assertEqual(fut.result()["status"], "fake-ok")
            st = h.pool.get_rebuild_stats()
            self.assertEqual(st["rebuild_count"], 1)
            self.assertEqual(st["broken_detected_count"], 1)
            self.assertEqual(st["pool_generation"], 2)
            self.assertGreaterEqual(st["rebuild_duration_ms_max"], 0.0)
            self.assertFalse(st["degraded_sync"])
        finally:
            h.close()

    def test_pool_missing_triggers_rebuild(self):
        h = _PoolHarness([{}])
        try:
            h.pool._pool = None            # 模拟池被摘引用（崩溃后置空）
            fut = h.pool.submit("X.task")
            self.assertIsNotNone(fut)
            self.assertEqual(h.pool._rebuild_count, 1)
        finally:
            h.close()

    def test_consecutive_failures_degrade_to_sync(self):
        # 构造进程池永远失败 → 连续失败达上限后降级同步
        h = _PoolHarness([{}], raise_times=99)
        try:
            with _Switch(POOL_REBUILD_MAX_RETRIES=3):
                for _ in range(5):
                    h.pool._pool = _FakePool(broken=True)
                    h.pool._rebuild_pool(reason="模拟重建失败")
                st = h.pool.get_rebuild_stats()
                self.assertTrue(st["degraded_sync"],
                                "连续重建失败达上限后必须降级为同步执行")
                self.assertEqual(st["consecutive_failures"], 3,
                                 "连续失败计数应封顶在 max_retries")
                # 降级后 submit 仍返回可用的 Future（功能不失效，结果来自同进程执行）
                fut = h.pool.submit("X.task")
                self.assertIsNotNone(fut)
                self.assertTrue(fut.done())
                self.assertEqual(fut.result()["status"], "delegated")
        finally:
            h.close()

    def test_health_check_triggers_early_rebuild(self):
        h = _PoolHarness([{}, {}])
        try:
            h.pool._pool._broken = True          # 静默损坏（submit 前）
            h.pool._last_health_check = 0.0      # 让健康检查必定到期
            fut = h.pool.submit("X.task")
            self.assertIsNotNone(fut)
            self.assertGreaterEqual(h.pool._broken_detected_count, 1)
            self.assertGreaterEqual(h.pool._rebuild_count, 1)
        finally:
            h.close()


class TestSwitchOff(unittest.TestCase):
    def test_switch_off_behaves_as_before(self):
        """灰度关闭：BrokenProcessPool 只记 ERROR 并返回 None（与修复前一致）。"""
        h = _PoolHarness([{"fail_times": 99}])
        try:
            with _Switch(ENABLE_POOL_AUTO_REBUILD=False):
                fut = h.pool.submit("X.task")
                self.assertIsNone(fut, "开关关闭时应返回 None（修复前行为）")
                self.assertEqual(h.pool._rebuild_count, 0, "开关关闭时不得重建")
                self.assertFalse(h.pool.get_rebuild_stats()["degraded_sync"])
        finally:
            h.close()

    def test_switch_off_and_pool_missing_returns_none(self):
        h = _PoolHarness([{}])
        try:
            with _Switch(ENABLE_POOL_AUTO_REBUILD=False):
                h.pool._pool = None
                self.assertIsNone(h.pool.submit("X.task"))
                self.assertIsNone(h.pool._pool, "开关关闭时不得复活进程池")
        finally:
            h.close()


class TestClosed(unittest.TestCase):
    def test_closed_pool_not_resurrected(self):
        """已 shutdown 的池不得被 submit 复活（防停机期重建）。"""
        h = _PoolHarness([{}, {}])
        try:
            h.pool.shutdown(timeout=0.1)
            fut = h.pool.submit("X.task")
            self.assertIsNone(fut, "停机后 submit 必须返回 None")
            self.assertIsNone(h.pool._pool)
            self.assertEqual(h.pool._rebuild_count, 0)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
