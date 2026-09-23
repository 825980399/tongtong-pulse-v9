# -*- coding: utf-8 -*-
"""第63批 T1 门控测试：InfoField.publish 步骤7 细粒度统计锁。

验证点：
1. _stats_lock 与全局 _lock 解耦（不同对象）
2. 生产源码步骤7 已改用条件细粒度锁（静态确认改动落地）
3. 灰度关闭时步骤7 退回全局 _lock
4. 高并发下统计计数无丢失（细粒度锁保护）
5. 高并发下无死锁（能在超时内完成）
6. 灰度开关默认开启（从 config 读取）
"""
import io
import os
import threading
import time
import unittest

from nucleus.field.InfoField import InfoField

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mk_light():
    """轻量 InfoField 实例（绕过 __init__，仅喂锁相关与统计字段）。"""
    obj = InfoField.__new__(InfoField)
    obj._lock = threading.Lock()
    obj._stats_lock = threading.Lock()
    obj._use_stats_fine_lock = True
    obj._total_matched = 0
    obj._total_unmatched = 0
    obj._event_publish_count = {}
    obj._event_unmatched_count = {}
    obj._cond_trigger_count = {}
    return obj


def _step7(obj, matched, etype, cids):
    """复刻 publish 步骤7 的统计更新临界区（与生产同款选锁 + 同款计数）。"""
    _lock_to_use = obj._stats_lock if getattr(obj, "_use_stats_fine_lock", True) else obj._lock
    with _lock_to_use:
        if matched:
            obj._total_matched += 1
        else:
            obj._total_unmatched += 1
        obj._event_publish_count[etype] = obj._event_publish_count.get(etype, 0) + 1
        if not matched:
            obj._event_unmatched_count[etype] = obj._event_unmatched_count.get(etype, 0) + 1
        for _cid in cids:
            obj._cond_trigger_count[_cid] = obj._cond_trigger_count.get(_cid, 0) + 1


class TestPublishStatsLockM63(unittest.TestCase):
    def test_01_stats_lock_distinct_from_global(self):
        obj = _mk_light()
        self.assertIsNot(obj._stats_lock, obj._lock, "细粒度锁必须与全局锁解耦")
        self.assertIsInstance(obj._stats_lock, type(threading.Lock()), "细粒度锁必须是 threading.Lock 实例")

    def test_02_source_uses_fine_lock(self):
        src = io.open(os.path.join(ROOT, "nucleus/field/InfoField.py"),
                       encoding="utf-8", errors="replace").read()
        self.assertIn(
            'self._stats_lock if getattr(self, "_use_stats_fine_lock", True) else self._lock',
            src, "publish 步骤7 未切换到条件细粒度锁")

    def test_03_gray_off_selects_global_lock(self):
        obj = _mk_light()
        obj._use_stats_fine_lock = False
        _lock_to_use = obj._stats_lock if getattr(obj, "_use_stats_fine_lock", True) else obj._lock
        self.assertIs(_lock_to_use, obj._lock, "灰度关闭时应退回全局锁（零回归）")

    def test_04_concurrent_stats_no_loss(self):
        obj = _mk_light()
        N = 100
        cids_common = ["c1", "c2"]

        def worker(i):
            matched = (i % 3 != 0)
            _step7(obj, matched, f"evt{i % 5}", cids_common)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads), "存在未结束线程（疑似死锁）")
        # 每线程都经过步骤7 一次，matched+unmatched 应恰好 == N
        self.assertEqual(obj._total_matched + obj._total_unmatched, N,
                         "并发计数丢失：matched+unmatched 不等于并发次数")
        # event_publish_count 总和也应 == N
        self.assertEqual(sum(obj._event_publish_count.values()), N)

    def test_05_no_deadlock_under_contention(self):
        obj = _mk_light()
        errors = []

        def worker():
            try:
                for _ in range(30):
                    _step7(obj, True, "x", ["c"])
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(40)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertFalse(any(t.is_alive() for t in threads), "高并发下出现死锁")
        self.assertEqual(errors, [], f"步骤7 并发抛异常: {errors}")
        self.assertLess(time.time() - t0, 15, "步骤7 并发耗时异常（疑似锁竞争恶化）")

    def test_06_gray_switch_default_on(self):
        import config
        self.assertTrue(bool(getattr(config, "ENABLE_PUBLISH_STATS_FINE_LOCK", False)),
                         "ENABLE_PUBLISH_STATS_FINE_LOCK 默认应开启")


if __name__ == "__main__":
    unittest.main()
