# -*- coding: utf-8 -*-
"""366 刀N4 验收：测试 data/ 沙箱根＋哨兵守卫（停机态还原 / 框架运行中不启用 / 沙箱落点）。"""
from __future__ import annotations

import builtins
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conftest as cf  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestN4Sandbox(unittest.TestCase):
    # ① 沙箱根不落 data/（任务书：沙箱产物不进 data/、不进审计池）
    def test_01_sandbox_dir_not_in_data(self):
        _d = cf.m366_sandbox_dir()
        self.assertTrue(os.path.isdir(_d))
        _norm = os.path.abspath(_d).replace("\\", "/")
        self.assertIn("/tmp/pytest_data_sandbox", _norm)
        self.assertNotIn("/data/", _norm)

    # ② 哨兵快照/比对/还原闭环：改一个字节 ⇒ 能检出并还原
    def test_02_diff_and_restore(self):
        _rel = cf._M366_SENTINELS[0]
        _abs = os.path.join(_ROOT, _rel)
        if not os.path.isfile(_abs):
            self.skipTest("哨兵文件不存在：%s" % _rel)
        _before = cf.m366_snapshot_sentinels()
        _bak = cf.m366_backup_sentinels()
        with open(_abs, "ab") as f:
            f.write(b"\n# m366 n4 test")
        try:
            _res = cf.m366_diff_and_restore(_before, _bak)
            self.assertIn(_rel, _res["changed"])
            self.assertIn(_rel, _res["restored"])
            self.assertTrue(_res["clean_after"], _res)
            self.assertEqual(cf.m366_file_sha(_rel), _before[_rel])
        finally:
            cf.m366_diff_and_restore(_before, _bak)

    # ③ 框架运行中不启用哨兵（沿用 production_data 标记口径，不改变现有行为）
    def test_03_guard_skipped_when_framework_running(self):
        _old = cf.is_framework_running
        try:
            cf.is_framework_running = lambda: True
            _gen = cf.m366_data_sentinel_guard.__wrapped__()
            _rep = next(_gen)
            self.assertEqual(_rep.get("state"), "skipped_framework_running")
            _gen.close()
        finally:
            cf.is_framework_running = _old

    # ④ 哈希工具对不存在文件记 absent（不伪造空哈希）
    def test_04_absent_not_empty_hash(self):
        self.assertEqual(cf.m366_file_sha("data/__nope__/x.json"), "absent")


if __name__ == "__main__":
    unittest.main()
