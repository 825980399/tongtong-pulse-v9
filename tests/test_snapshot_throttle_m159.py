# -*- coding: utf-8 -*-
"""第159批 刀4（票3 快照增量接管降频 + D-4 节流误报修复）测试。

对应任务书 §四.4.6 三例：
  ① 节流命中不得返回「已保存」（D-4）
  ② FULL=21600 时 6h 内走增量、越阈走全量并清空
  ③ 全量写失败保留增量 jsonl（零丢失承诺，文件未被误删）

落盘全部指向沙箱目录，绝不触碰生产 data/。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot


class _FakeNode:
    def __init__(self, nid, checksum="cs", ephemeral=False):
        self.node_id = nid
        self.checksum = checksum
        self.ephemeral = ephemeral

    def to_dict(self):
        return {"node_id": self.node_id, "checksum": self.checksum}


class _FakePool:
    def __init__(self, nodes):
        self._nodes = nodes

    def get_all_including_evicted(self):
        return list(self._nodes)


class TestSnapshotThrottle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m159_snap_")
        self.snap_path = os.path.join(self.tmp, "pulse_knowledge_snapshot.json")
        self.snap = PulseSnapshot(snapshot_path=self.snap_path)
        self.pool = _FakePool([_FakeNode("n1"), _FakeNode("n2"), _FakeNode("n3")])
        self.snap.set_node_pool(self.pool)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_d4_throttle_returns_not_saved(self):
        """① D-4：节流命中不得返回 True（已保存），且 pending_flush=True。"""
        self.snap._last_saved_checksum = "stale"   # 与当前不同 → 不命中无变更跳过
        self.snap._last_write_time = time.time()  # 命中节流（距上次写盘 < 30s）
        self.snap._last_save_time = 0.0            # 避免 seconds_since_last_save < 5 分支
        self.snap._pending_flush = False
        _r = self.snap._save_locked(force_full=False)
        self.assertIsNot(_r, True, "节流命中不得返回 True（已保存）")
        self.assertEqual(_r, PulseSnapshot.SAVE_THROTTLED)
        self.assertTrue(self.snap.pending_flush, "节流命中应置位 pending_flush=True")

    def test_full_interval_21600_incremental_then_full(self):
        """② FULL=21600 时：6h 内走增量；越阈走全量。"""
        # 让 snapshot_path 存在以允许增量路径（os.path.exists 检查）
        open(self.snap_path, "w").close()
        self.snap._last_write_time = 0.0  # 避免节流
        _calls = []
        self.snap._m67_incremental_log_save = lambda *a, **k: (_calls.append("inc"), True)[1]
        self.snap._m67_full_checkpoint = lambda *a, **k: (_calls.append("full"), True)[1]
        self.snap._m67_incremental_log_enabled = lambda: True
        _t = {"now": 1_000_000.0}
        self.snap._last_full_save_time = _t["now"]
        with mock.patch.object(time, "time", lambda: _t["now"]):
            self.snap._save_locked(force_full=False)   # 距上次全量 0 < 21600 → 增量
        self.assertIn("inc", _calls, "6h 内应走增量")
        # 推进时间越过 21600s
        _t["now"] += 21601
        _calls.clear()
        with mock.patch.object(time, "time", lambda: _t["now"]):
            self.snap._save_locked(force_full=False)   # 越阈 → 全量
        self.assertIn("full", _calls, "越过 21600s 应走全量")

    def test_full_save_failure_preserves_jsonl(self):
        """③ 全量写失败保留增量 jsonl（零丢失承诺，文件未被误删）。"""
        _jsonl = self.snap._m67_incremental_log_path()
        with open(_jsonl, "w", encoding="utf-8") as f:
            f.write('{"node_id":"n1","action":"upsert"}\n')
        self.snap._last_write_time = 0.0
        # 模拟全量保存失败：_full_save 返回 False → _m67_full_checkpoint 保留 jsonl
        self.snap._full_save = lambda *a, **k: False
        _r = self.snap._save_locked(force_full=True)
        self.assertFalse(_r, "全量失败应返回 False")
        self.assertTrue(os.path.exists(_jsonl), "全量失败不得误删增量 jsonl（零丢失）")


if __name__ == "__main__":
    unittest.main()
