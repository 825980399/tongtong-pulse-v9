# -*- coding: utf-8 -*-
"""主线第13批 T3 / P2-86：校验和占位符与降级 门控单测。

覆盖：
  1) _is_placeholder_checksum 判定（deadbeef / 全零 / 空 / None / 真实值）
  2) 占位符预期校验和 → 不产生 WARNING（跳过比对）
  3) 真实不匹配 + 开关开启 → DEBUG 级别（降噪）
  4) 真实不匹配 + 开关关闭 → WARNING 级别（零回归）
  5) 校验和算法版本常量存在
"""
import os
import sys
import tempfile
import unittest

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

import config
from nucleus.const import LogLevel
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.mnemosyne.PulseSnapshot import (
    CHECKSUM_ALGO_VERSION,
    PulseSnapshot,
    _is_placeholder_checksum,
)


class TestPlaceholderDetection(unittest.TestCase):
    def test_deadbeef是占位符(self):
        self.assertTrue(_is_placeholder_checksum("deadbeef"))
        self.assertTrue(_is_placeholder_checksum("DEADBEEF"))

    def test_全零是占位符(self):
        self.assertTrue(_is_placeholder_checksum("0" * 16))

    def test_空与None是占位符(self):
        self.assertTrue(_is_placeholder_checksum(""))
        self.assertTrue(_is_placeholder_checksum(None))

    def test_真实校验和不是占位符(self):
        self.assertFalse(_is_placeholder_checksum("0ff620381231fb3f"))
        self.assertFalse(_is_placeholder_checksum("7919d05e50d32ed0"))


class _CaptureLog:
    """替换 PulseSnapshot._log 以捕获 (level, msg)。"""
    def __init__(self, ps):
        self.records = []
        self._ps = ps
        self._orig = ps._log
        ps._log = self._capture

    def _capture(self, level, msg):
        self.records.append((level, msg))

    def restore(self):
        self._ps._log = self._orig

    def levels(self):
        return [lv for lv, _ in self.records]

    def messages(self):
        return [m for _, m in self.records]


_T3_SCRATCH_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tmp", "_snapshot_m13_t3")


def _purge_t3_scratch():
    """清空 T3 scratch 目录内所有 snap_m13_* 文件（含 .bak 同伴），杜绝残留。"""
    try:
        if not os.path.isdir(_T3_SCRATCH_DIR):
            return
        for _n in os.listdir(_T3_SCRATCH_DIR):
            if _n.startswith("snap_m13_"):
                try:
                    os.remove(os.path.join(_T3_SCRATCH_DIR, _n))
                except OSError:
                    pass
    except OSError:
        pass


def _snapshot_with_checksum(checksum, n=3):
    """构造一个含指定 node_list_checksum 的临时快照文件（落项目本地 tmp）。"""
    _dir = _T3_SCRATCH_DIR
    os.makedirs(_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".json", prefix="snap_m13_", dir=_dir)
    os.close(fd)
    nodes = [PulseNode(f"节点{i}", source_organ="test", evol_level=PulseNode.EVOL_L2)
             for i in range(n)]
    data = {
        "version": "v9.5",
        "created_at": "2026-09-10T00:00:00",
        "node_count_at_save": n,
        "nodes": [nd.to_dict() for nd in nodes],
        "node_list_checksum": checksum,
        "freq_index": {},
        "inference_cache": {},
        "extra_state": {},
    }
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


class TestVerifyIntegrityLevels(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "ENABLE_SNAPSHOT_CHECKSUM_DEBUG", True)
        self._paths = []

    def tearDown(self):
        config.ENABLE_SNAPSHOT_CHECKSUM_DEBUG = self._orig
        for _p in self._paths:
            for _q in (_p, _p + ".bak"):
                try:
                    if os.path.exists(_q):
                        os.remove(_q)
                except OSError:
                    pass
        _purge_t3_scratch()

    def test_占位符不产生WARNING(self):
        _p = _snapshot_with_checksum("deadbeef", n=3)
        self._paths.append(_p)
        _ps = PulseSnapshot(snapshot_path=_p)
        _cap = _CaptureLog(_ps)
        try:
            _ps.load(full_load=True)
        finally:
            _cap.restore()
        _warnings = [m for lv, m in _cap.records if lv == 10 or lv == 30]
        # 不应出现「校验和不匹配」的 WARNING
        _bad = [m for lv, m in _cap.records
                if "校验和不匹配" in m and lv >= 30]
        self.assertEqual(_bad, [], f"占位符仍产生 WARNING: {_bad}")

    def test_真实不匹配开关开启为DEBUG(self):
        config.ENABLE_SNAPSHOT_CHECKSUM_DEBUG = True
        _p = _snapshot_with_checksum("1234567890abcdef", n=3)
        self._paths.append(_p)
        _ps = PulseSnapshot(snapshot_path=_p)
        _cap = _CaptureLog(_ps)
        try:
            _ps.load(full_load=True)
        finally:
            _cap.restore()
        _mismatch = [(lv, m) for lv, m in _cap.records if "校验和不匹配" in m]
        self.assertEqual(len(_mismatch), 1, _cap.records)
        self.assertEqual(_mismatch[0][0], LogLevel.DEBUG, "开关开启应为 DEBUG")

    def test_真实不匹配开关关闭为WARNING(self):
        config.ENABLE_SNAPSHOT_CHECKSUM_DEBUG = False
        _p = _snapshot_with_checksum("1234567890abcdef", n=3)
        self._paths.append(_p)
        _ps = PulseSnapshot(snapshot_path=_p)
        _cap = _CaptureLog(_ps)
        try:
            _ps.load(full_load=True)
        finally:
            _cap.restore()
        _mismatch = [(lv, m) for lv, m in _cap.records if "校验和不匹配" in m]
        self.assertEqual(len(_mismatch), 1, _cap.records)
        self.assertEqual(_mismatch[0][0], LogLevel.WARNING, "开关关闭应为 WARNING")

    def test_算法版本常量存在(self):
        self.assertIsInstance(CHECKSUM_ALGO_VERSION, int)
        self.assertGreaterEqual(CHECKSUM_ALGO_VERSION, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
