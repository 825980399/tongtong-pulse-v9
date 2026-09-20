# -*- coding: utf-8 -*-
"""主线第78批 T4/P1 门控测试：快照保存合并触发（coalesce）根治堆积。

原问题：保存进行中(_m67_is_saving)再有触发直接 drop → 保存期间发生的变更永不被持久化。
修复：保存进行中置 _m67_pending_save，当前保存结束后据此补一次，保证最多 1 个保存线程且不丢数据。

覆盖：
1. 空闲时触发 → 直接启动保存线程（无 pending）。
2. 保存进行中触发 → 置 pending，且不另开线程（不堆积）。
3. 保存进行中多次触发 → 仍只补一次（pending 幂等）。
4. 当前保存结束后 → 恰好补一次（总调用=2，最大并发=1）。
5. 源码含 _m67_pending_save 合并分支。
"""
import io
import os
import threading
import time
import unittest

from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mk_snapshot():
    obj = PulseSnapshot.__new__(PulseSnapshot)
    obj._m67_is_saving = False
    obj._m67_pending_save = False
    obj._log = lambda *a, **k: None  # 静默日志，避免污染测试输出
    obj._save_sync_calls = 0
    return obj


def _make_slow_save(started, release, calls, cur, mx, lk):
    def _save(force_full=False):
        with lk:
            calls[0] += 1
            cur[0] += 1
            mx[0] = max(mx[0], cur[0])
        started.set()
        release.wait(5.0)
        with lk:
            cur[0] -= 1
        return True
    return _save


class TestSnapshotCoalesceM78(unittest.TestCase):
    def test_01_idle_starts_save(self):
        obj = _mk_snapshot()
        obj._save_async()
        self.assertTrue(obj._m67_is_saving, "空闲触发应直接启动保存")
        self.assertFalse(obj._m67_pending_save, "空闲触发不应置 pending")
        # 清理：释放可能的保存线程
        time.sleep(0.2)

    def test_02_in_progress_sets_pending_no_new_thread(self):
        obj = _mk_snapshot()
        started = threading.Event()
        release = threading.Event()
        calls, cur, mx = [0], [0], [0]
        lk = threading.Lock()
        obj._save_sync = _make_slow_save(started, release, calls, cur, mx, lk)
        obj._save_async()  # 线程1 启动
        self.assertTrue(started.wait(5), "首个保存线程未启动")
        # 保存进行中再触发
        obj._save_async()
        self.assertTrue(obj._m67_pending_save, "进行中触发应置 pending")
        self.assertEqual(calls[0], 1, "进行中触发不应另开保存线程（堆积）")
        release.set()
        # 等待合并线程结束
        for _ in range(100):
            if not obj._m67_is_saving:
                break
            time.sleep(0.1)
        self.assertEqual(calls[0], 2, "进行中触发应补一次保存（共2次）")
        self.assertLessEqual(mx[0], 1, "并发保存线程数不得超过1（堆积未根治）")

    def test_03_multiple_triggers_one_resave(self):
        obj = _mk_snapshot()
        started = threading.Event()
        release = threading.Event()
        calls, cur, mx = [0], [0], [0]
        lk = threading.Lock()
        obj._save_sync = _make_slow_save(started, release, calls, cur, mx, lk)
        obj._save_async()  # 线程1
        self.assertTrue(started.wait(5))
        for _ in range(3):  # 进行中触发 3 次
            obj._save_async()
        self.assertTrue(obj._m67_pending_save)
        self.assertEqual(calls[0], 1, "多次进行中触发不应各开线程")
        release.set()
        for _ in range(100):
            if not obj._m67_is_saving:
                break
            time.sleep(0.1)
        # pending 幂等 → 只补一次
        self.assertEqual(calls[0], 2, "pending 幂等：多次触发仍只补一次（共2次）")

    def test_04_source_has_pending_branch(self):
        src = io.open(os.path.join(ROOT, "nucleus/mnemosyne/PulseSnapshot.py"),
                      encoding="utf-8", errors="replace").read()
        self.assertIn("_m67_pending_save = True", src,
                      "保存进行中未置 _m67_pending_save（合并分支缺失）")
        self.assertIn("self._save_async()", src,
                      "保存结束后未据 pending 补一次保存")

    def test_05_no_coalesce_when_idle(self):
        obj = _mk_snapshot()
        calls, cur, mx = [0], [0], [0]
        lk = threading.Lock()
        started = threading.Event()
        release = threading.Event()
        obj._save_sync = _make_slow_save(started, release, calls, cur, mx, lk)
        obj._save_async()
        self.assertTrue(started.wait(5))
        # 进行中触发 → pending（非直接再调用）
        obj._save_async()
        self.assertEqual(calls[0], 1)
        release.set()
        for _ in range(100):
            if not obj._m67_is_saving:
                break
            time.sleep(0.1)
        self.assertEqual(calls[0], 2)


if __name__ == "__main__":
    unittest.main()
