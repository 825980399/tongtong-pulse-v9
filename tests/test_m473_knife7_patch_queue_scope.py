# -*- coding: utf-8 -*-
"""第473批 刀7 门控单测：补丁链队列深度口径（degraded 自锁死）。

根因
----
`organs/core/PulseMetricsCollector.py::_m421_patch_chain_read` 原实现：

    _out["queue"] = len(_pend)

无差别计数 —— 把 `status=wont_fix`（已判不修）这类**终态**补丁也算进队列。
而 degraded 判据是「apply==0 且 queue>0」，于是：

    wont_fix 补丁留在队列里 ⇒ queue 永远 >= 1
    ⇒ 只要某天 apply 掉到 0，state 永久 degraded，再也回不到 ok。

现场证据（2026-11-07 01:24 日志）：
    pending_patches.json 共 2 条 = 1 条 wont_fix + 1 条 verified
    真实可推进只有 1 条，却被报成 `pending=2`。

修复口径
--------
终态（wont_fix / rejected / voided / abandoned / invalid）不计入队列深度；
非 dict 的**坏行仍计入**（fail-closed：宁可多报，不漏报）。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.core.PulseMetricsCollector import _m421_patch_chain_read


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


class _Base(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.mkdtemp(prefix="m473q_")
        self._patches = os.path.join(self._root, "data", "patches")
        os.makedirs(self._patches, exist_ok=True)
        # history 置空 ⇒ apply=0（degraded 判据的一半）
        _write_json(os.path.join(self._patches, "patch_history.json"), [])

    def tearDown(self):
        shutil.rmtree(self._root, ignore_errors=True)

    def _pending(self, rows):
        _write_json(os.path.join(self._patches, "pending_patches.json"), rows)
        return _m421_patch_chain_read(root=self._root)


class TestA_QueueDepthExcludesTerminal(_Base):
    """判据：终态补丁不占队列深度。"""

    def test_01_only_wont_fix_means_zero_queue(self):
        _r = self._pending([{"id": "a", "status": "wont_fix", "file": "x.py"}])
        self.assertEqual(_r["queue"], 0,
                         f"只有 wont_fix 时队列深度应为 0，实得 {_r['queue']}")
        self.assertNotEqual(_r["state"], "degraded",
                            f"wont_fix 不该触发 degraded，实得 {_r['state']}: {_r.get('state_reason')}")

    def test_02_verified_counts(self):
        _r = self._pending([
            {"id": "a", "status": "wont_fix"},
            {"id": "b", "status": "verified"},
        ])
        self.assertEqual(_r["queue"], 1, f"应只剩 1 条可推进，实得 {_r['queue']}")
        self.assertEqual(_r["state"], "degraded",
                         f"apply=0 且 queue=1 应判 degraded，实得 {_r['state']}")

    def test_03_all_terminal_families_excluded(self):
        _r = self._pending([{"status": s} for s in
                            ("wont_fix", "rejected", "voided", "abandoned", "invalid")])
        self.assertEqual(_r["queue"], 0, f"五个终态族都该排除，实得 {_r['queue']}")

    def test_04_case_and_whitespace_insensitive(self):
        _r = self._pending([{"status": "  WONT_FIX  "}, {"status": "Verified"}])
        self.assertEqual(_r["queue"], 1,
                         f"大小写/空白应归一化（1 条终态 + 1 条可推进），实得 {_r['queue']}")

    def test_05_pending_and_approved_count(self):
        _r = self._pending([{"status": "pending"}, {"status": "approved"}, {"status": ""}])
        self.assertEqual(_r["queue"], 3,
                         f"pending/approved/空状态都算可推进，实得 {_r['queue']}")


class TestB_FailClosedOnBadRows(_Base):
    """判据：非 dict 的坏行必须**计入**（宁可多报，不漏报）。"""

    def test_01_bad_rows_still_counted(self):
        _r = self._pending([{"status": "wont_fix"}, "坏行", 42, None])
        self.assertEqual(_r["queue"], 3,
                         f"3 个非 dict 坏行应全部计入，实得 {_r['queue']}")

    def test_02_empty_queue_not_degraded(self):
        _r = self._pending([])
        self.assertEqual(_r["queue"], 0)
        self.assertNotEqual(_r["state"], "degraded",
                            "空队列不该 degraded（apply=0 但没有待办件）")


class TestC_SourceLevelGuard(unittest.TestCase):
    """源码级：防后人把无差别 len 接回来。"""

    def _src(self):
        _p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "organs", "core", "PulseMetricsCollector.py")
        with open(_p, encoding="utf-8") as fh:
            return fh.read()

    def test_01_no_bare_len_assign(self):
        _s = self._src()
        self.assertNotIn('_out["queue"] = len(_pend)', _s,
                         "队列深度又变回无差别 len(_pend) —— 刀7 回归")

    def test_02_terminal_set_present(self):
        self.assertIn("_M473_TERMINAL_STATUS", self._src())

    def test_03_wont_fix_in_terminal_set(self):
        _s = self._src()
        _i = _s.find("_M473_TERMINAL_STATUS = ")
        self.assertGreater(_i, 0)
        self.assertIn("wont_fix", _s[_i:_i + 200],
                      "wont_fix 必须留在终态集合里（去掉就等于没修）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
