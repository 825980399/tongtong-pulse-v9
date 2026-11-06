# -*- coding: utf-8 -*-
"""第472批 刀3 门控单测：快照落盘必须是「纯 os.replace」原子覆盖（台账 D3 / F2）。

被测对象：`nucleus/mnemosyne/PulseSnapshot.py`
  · `_save_l1_locked`（经 `save_l1()`）—— 第 1 处
  · `compact()` 的 L1 / 主快照两处 —— 第 2/3 处（源码级）

为什么值得单独测：主路径第 80 批 T4 已经改对了（`:923` 附近注释写明
「前置 os.remove 冗余且目标被锁时会抛错」），但 L1 与两处 compaction 漏改。
台账把这类叫 F 类「修一半」，而 `l1_snapshot_path` 在 tests/ 里 **0 命中**，
所以漏改能一直存活。

核心判据（行为级）：把 `os.replace` 打桩成抛 OSError，
- 旧代码：先 os.remove 删掉旧档 ⇒ 替换失败 ⇒ **旧档没了**（数据丢失）
- 新代码：直接 os.replace 失败 ⇒ **旧档原封不动**
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.mnemosyne.PulseSnapshot as PS
from nucleus.mnemosyne.PulseNode import PulseNode


class _FakeNode:
    """够用的最小节点：只要 evol_level / ephemeral / to_dict 三项。"""

    def __init__(self, nid="n1"):
        self.node_id = nid
        self.evol_level = PulseNode.EVOL_L1
        self.ephemeral = False

    def to_dict(self):
        return {"id": self.node_id, "content": "hello", "evol_level": self.evol_level}


class _FakePool:
    def __init__(self, nodes):
        self._nodes = list(nodes)

    def get_all_including_evicted(self):
        return list(self._nodes)


def _make_snapshot(l1_path):
    """用 __new__ 绕开重装配：本用例只关心「落盘那 3 行」，不关心构造链。"""
    _ps = PS.PulseSnapshot.__new__(PS.PulseSnapshot)
    _ps._lock = threading.RLock()
    _ps.l1_snapshot_path = l1_path
    _ps.node_pool = _FakePool([_FakeNode()])
    _ps._log = lambda *a, **k: None
    _ps._m44_write_allowed = lambda: True
    _ps._m102_sidecar_node_ids = lambda: None
    return _ps


class TestA_AtomicReplaceBehavior(unittest.TestCase):
    """行为判据：替换失败时旧档必须还在。"""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m472k3_")
        self._l1 = os.path.join(self._td, "l1_snapshot.json")
        self._old = {"nodes": [{"id": "OLD"}], "l1_count": 1}
        with open(self._l1, "w", encoding="utf-8") as f:
            json.dump(self._old, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_01_old_file_survives_when_replace_fails(self):
        _ps = _make_snapshot(self._l1)
        with mock.patch.object(PS, "_m102_dangling_guard_on", lambda: False), \
                mock.patch.object(PS.os, "replace", side_effect=OSError("locked")):
            _ok = _ps.save_l1()
        self.assertFalse(_ok, "替换失败应返回 False")
        self.assertTrue(os.path.isfile(self._l1),
                        "旧档被 os.remove 删掉了 —— 这正是 D3 描述的崩溃窗口")
        with open(self._l1, encoding="utf-8") as f:
            self.assertEqual(json.load(f), self._old, "旧档内容必须原封不动")

    def test_02_replace_is_called_without_prior_remove(self):
        """把 os.remove 也打桩：新代码根本不该调它。"""
        _ps = _make_snapshot(self._l1)
        _removed = []
        with mock.patch.object(PS, "_m102_dangling_guard_on", lambda: False), \
                mock.patch.object(PS.os, "remove",
                                  side_effect=lambda p: _removed.append(p)):
            _ok = _ps.save_l1()
        self.assertTrue(_ok, "正常替换应成功")
        self.assertEqual(_removed, [], f"不该再对快照做 os.remove，实删了 {_removed}")

    def test_03_success_replaces_content(self):
        _ps = _make_snapshot(self._l1)
        with mock.patch.object(PS, "_m102_dangling_guard_on", lambda: False):
            _ok = _ps.save_l1()
        self.assertTrue(_ok)
        with open(self._l1, encoding="utf-8") as f:
            _d = json.load(f)
        self.assertEqual(_d.get("l1_count"), 1)
        self.assertEqual(_d["nodes"][0]["id"], "n1", "新档必须真的换上去")


class TestB_NoStalePatternInSource(unittest.TestCase):
    """源码级：三处同模式必须全部修完（防「修一半」再次发生）。"""

    @classmethod
    def setUpClass(cls):
        _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "nucleus", "mnemosyne", "PulseSnapshot.py")
        with open(_p, encoding="utf-8") as _fh:
            cls._src = _fh.read()

    def test_04_no_remove_before_l1_replace(self):
        self.assertNotIn("os.remove(self.l1_snapshot_path)", self._src,
                         "L1 快照两处仍留有非原子删除")

    def test_05_no_remove_before_main_replace(self):
        self.assertNotIn("os.remove(self.snapshot_path)", self._src,
                         "主快照 compaction 仍留有非原子删除")

    def test_06_three_atomic_replace_sites(self):
        """下限守卫：三处落盘点都还在（真正的判据是 test_04/05「不再 remove」）。

        注意 `os.replace(tmp_path, self.snapshot_path)` 在主保存路径里也有，
        所以这里只断言「至少存在」，不钉死精确次数（铁律 9：别被新增文件顶红）。
        """
        _n = self._src.count("os.replace(tmp_path, self.l1_snapshot_path)")
        self.assertGreaterEqual(_n, 2, f"L1 两处落盘点都应走纯 replace，实得 {_n}")
        _m = self._src.count("os.replace(tmp_path, self.snapshot_path)")
        self.assertGreaterEqual(_m, 1, f"主快照落盘点应走纯 replace，实得 {_m}")


if __name__ == "__main__":
    unittest.main()
