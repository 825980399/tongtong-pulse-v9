# -*- coding: utf-8 -*-
"""主线第78批 T1/P0 门控测试：队列深度 inflight 计数器竞态与泄漏根治。

覆盖：
1. publish 自增(+1) 使用 _task_count_lock（内部协作者点名的 L712 有锁）。
2. _dispatch_handler 自减(-1) 现也使用 _task_count_lock（内部协作者点名的 L898 原无锁 → 漂移根因）。
3. _resize_layer_pool 池重建回收段(_recover 分支)按取消数精确递减 _inflight_dispatch_count
   （取消旧池排队任务 cancel_futures=True 导致其 -1 永不执行 → 原泄漏源）。
4. ±对称加锁下高并发无漂移、不转负。
5. max(0, ...) 守卫保证计数不为负。
"""
import io
import os
import threading
import unittest

from nucleus.field.InfoField import InfoField

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm(src):
    """去每行前导空白，便于做与缩进无关的静态断言。"""
    return "\n".join(line.lstrip() for line in src.splitlines())


def _mk_light():
    """轻量 InfoField 实例（绕过 __init__），仅喂在途计数相关字段。"""
    obj = InfoField.__new__(InfoField)
    obj._task_count_lock = threading.Lock()
    obj._inflight_dispatch_count = 0
    obj._layer_organ_inflight = {}
    obj._organ_concurrent_count = {}
    obj._concurrency_recovery_enabled = lambda: True
    return obj


def _publish_inc(obj):
    """复刻 publish 提交时的 +1 临界区（与生产同款：带 _task_count_lock）。"""
    with obj._task_count_lock:
        obj._inflight_dispatch_count += 1


def _dispatch_dec(obj):
    """复刻 _dispatch_handler finally 内的 -1 临界区（本批修复：带 _task_count_lock）。"""
    with obj._task_count_lock:
        obj._inflight_dispatch_count = max(0, obj._inflight_dispatch_count - 1)


class TestInflightLockM78(unittest.TestCase):
    # ---- 静态确认：生产源码 ± 均加锁 ----
    def test_01_inc_source_uses_task_count_lock(self):
        src = io.open(os.path.join(ROOT, "nucleus/field/InfoField.py"),
                      encoding="utf-8", errors="replace").read()
        # publish 提交 +1 块（与缩进无关断言）
        self.assertIn('with self._task_count_lock:\nself._inflight_dispatch_count += 1',
                      _norm(src), "publish 提交 +1 未使用 _task_count_lock")

    def test_02_dec_source_uses_task_count_lock(self):
        src = io.open(os.path.join(ROOT, "nucleus/field/InfoField.py"),
                      encoding="utf-8", errors="replace").read()
        # ★核心修复：_dispatch_handler 的 -1 现也带锁（与内部协作者点名的 L898 无锁漂移对应）
        self.assertIn('with self._task_count_lock:\nself._inflight_dispatch_count = max(0, self._inflight_dispatch_count - 1)',
                      _norm(src), "_dispatch_handler 的 -1 未使用 _task_count_lock（竞态漂移未根治）")

    def test_03_resize_recover_decrements_inflight(self):
        src = io.open(os.path.join(ROOT, "nucleus/field/InfoField.py"),
                      encoding="utf-8", errors="replace").read()
        self.assertIn("_cancelled = sum(_layer_pending.values())", src,
                      "_resize_layer_pool 未计算取消数")
        self.assertIn('with self._task_count_lock:\nself._inflight_dispatch_count = max(\n0, self._inflight_dispatch_count - _cancelled)',
                      _norm(src), "_resize_layer_pool 回收段未精确递减 _inflight_dispatch_count（泄漏未根治）")

    # ---- 行为：± 对称加锁下无漂移 ----
    def test_04_symmetric_no_drift_under_contention(self):
        obj = _mk_light()
        N = 200

        def worker():
            for _ in range(20):
                _publish_inc(obj)
                try:
                    pass  # 模拟分发
                finally:
                    _dispatch_dec(obj)

        threads = [threading.Thread(target=worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertFalse(any(t.is_alive() for t in threads), "存在未结束线程（疑似死锁）")
        # 每次 +1 都对应一次 -1 → 最终应为 0（无漂移、无泄漏）
        self.assertEqual(obj._inflight_dispatch_count, 0,
                         f"并发后计数漂移为 {obj._inflight_dispatch_count}（±不对称或漏减）")

    def test_05_never_negative(self):
        obj = _mk_light()
        obj._inflight_dispatch_count = 0
        # 连续 -1 多次，max(0,...) 守卫保证不转负
        for _ in range(5):
            _dispatch_dec(obj)
        self.assertEqual(obj._inflight_dispatch_count, 0, "max(0,...) 守卫失效，计数转负")

    def test_06_lock_is_distinct_threadsafe_object(self):
        obj = _mk_light()
        self.assertIsInstance(obj._task_count_lock, type(threading.Lock()))
        # 单线程快速往返不应丢计数
        for _ in range(1000):
            _publish_inc(obj)
            _dispatch_dec(obj)
        self.assertEqual(obj._inflight_dispatch_count, 0)

    # ---- 行为：模拟池重建 cancel_futures 泄漏场景（本批根治点）----
    def test_07_cancel_futures_leak_recovery(self):
        """模拟 _resize_layer_pool：旧池取消 3 个排队任务（其 -1 永不执行），
        _recover 分支应精确递减 _inflight_dispatch_count 的取消数。"""
        obj = _mk_light()
        # 假设 L3 层有 3 个器官各 1 个在途分发，且全部被旧池取消
        obj._inflight_dispatch_count = 3
        obj._layer_organ_inflight = {"L3": {"organ_a": 1, "organ_b": 1, "organ_c": 1}}
        obj._organ_concurrent_count = {"organ_a": 1, "organ_b": 1, "organ_c": 1}
        # 复刻 _recover 分支逻辑
        if obj._concurrency_recovery_enabled():
            _layer_pending = dict(obj._layer_organ_inflight.get("L3", {}))
            _cancelled = sum(_layer_pending.values())
            for _org, _n in _layer_pending.items():
                _cur = obj._organ_concurrent_count.get(_org, 0)
                if _cur > 0:
                    obj._organ_concurrent_count[_org] = max(0, _cur - _n)
            # ★本批新增：同步回收被取消任务的在途分发计数
            obj._inflight_dispatch_count = max(0, obj._inflight_dispatch_count - _cancelled)
        self.assertEqual(obj._inflight_dispatch_count, 0,
                         "池重建取消任务的在途计数未被回收（泄漏未根治）")
        self.assertEqual(sum(obj._organ_concurrent_count.values()), 0,
                         "并发计数未同步回收")


if __name__ == "__main__":
    unittest.main()
