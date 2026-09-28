# -*- coding: utf-8 -*-
"""主线第13批 T1 / P2-88：快照路径白名单 + 临时残留清理 门控单测。

覆盖：
  1) _is_temp_snapshot_path 判定（正/负例、空路径、项目内路径）
  2) load() 白名单拦截临时快照（返回 []）
  3) 开关关闭时行为回退（不拦截，走原加载路径）
  4) cleanup_temp_snapshot_residue 清理陈旧残留 / 保留新文件
  5) 开关关闭时构造不触发清理
"""
import os
import sys
import tempfile
import time
import unittest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

import config
from nucleus.mnemosyne.PulseSnapshot import (
    PulseSnapshot,
    _is_temp_snapshot_path,
    cleanup_temp_snapshot_residue,
)


class TestTempPathDetection(unittest.TestCase):
    def test_临时目录内的快照被识别(self):
        _temp = tempfile.gettempdir()
        self.assertTrue(_is_temp_snapshot_path(os.path.join(_temp, "snap_t4_x.json")))
        self.assertTrue(_is_temp_snapshot_path(os.path.join(_temp, "snap_t4_x.json.bak")))

    def test_临时目录子路径被识别(self):
        _temp = tempfile.gettempdir()
        self.assertTrue(_is_temp_snapshot_path(os.path.join(_temp, "sub", "a.json")))

    def test_项目内路径不被误判(self):
        self.assertFalse(_is_temp_snapshot_path(
            os.path.join(_PROJ, "data", "knowledge", "pulse_knowledge_snapshot.json")))
        self.assertFalse(_is_temp_snapshot_path(
            os.path.join(_PROJ, "tests", "tmp_x", "a.json")))

    def test_空路径返回False(self):
        self.assertFalse(_is_temp_snapshot_path(""))
        self.assertFalse(_is_temp_snapshot_path(None))


class TestLoadWhitelistBlock(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "ENABLE_SNAPSHOT_PATH_WHITELIST", True)
        config.ENABLE_SNAPSHOT_PATH_WHITELIST = True
        self._temp = tempfile.gettempdir()
        self._path = os.path.join(self._temp, "snap_t4_gate13.json")
        with open(self._path, "w", encoding="utf-8") as f:
            f.write('{"version":"v9.5","node_count_at_save":0,"nodes":[]}')

    def tearDown(self):
        config.ENABLE_SNAPSHOT_PATH_WHITELIST = self._orig
        for _p in (self._path, self._path + ".bak"):
            try:
                if os.path.exists(_p):
                    os.remove(_p)
            except OSError:
                pass

    def test_开启时拦截临时快照返回空列表(self):
        _ps = PulseSnapshot(snapshot_path=self._path)
        self.assertEqual(_ps.load(full_load=True), [])

    def test_关闭时走原加载路径(self):
        config.ENABLE_SNAPSHOT_PATH_WHITELIST = False
        _ps = PulseSnapshot(snapshot_path=self._path)
        _res = _ps.load(full_load=True)
        self.assertIsInstance(_res, list)


class TestTempResidueCleanup(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.gettempdir()
        self._created = []

    def tearDown(self):
        for _p in self._created:
            try:
                if os.path.exists(_p):
                    os.remove(_p)
            except OSError:
                pass

    def _touch(self, name, age_seconds=0.0):
        _p = os.path.join(self._temp, name)
        with open(_p, "w", encoding="utf-8") as f:
            f.write("{}")
        if age_seconds > 0:
            _t = time.time() - age_seconds
            os.utime(_p, (_t, _t))
        self._created.append(_p)
        return _p

    def test_清理陈旧残留(self):
        _p = self._touch("snap_t4_gate13_old.json", age_seconds=300.0)
        cleanup_temp_snapshot_residue(min_age_seconds=60.0)
        self.assertFalse(os.path.exists(_p))

    def test_保留新文件(self):
        _p = self._touch("snap_t4_gate13_new.json", age_seconds=1.0)
        cleanup_temp_snapshot_residue(min_age_seconds=60.0)
        self.assertTrue(os.path.exists(_p))

    def test_清理bak同伴(self):
        _p = self._touch("snap_t4_gate13_old.json.bak", age_seconds=300.0)
        cleanup_temp_snapshot_residue(min_age_seconds=60.0)
        self.assertFalse(os.path.exists(_p))

    def test_不误删非snap_t4文件(self):
        _p = self._touch("other_t4_gate13.json", age_seconds=300.0)
        cleanup_temp_snapshot_residue(min_age_seconds=60.0)
        self.assertTrue(os.path.exists(_p))


if __name__ == "__main__":
    unittest.main(verbosity=2)
