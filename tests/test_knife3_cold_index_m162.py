# -*- coding: utf-8 -*-
"""162批刀3 · 冷索引可观测 S1-S4 + 冷驱逐接线 门控单测。

覆盖：
- S1 重建失败 DEBUG→WARNING（写明"冷索引未重建，条目数维持 N"）+ stale 置位；
- S2 json.dump 写失败→_cold_index_stale=True；写成功→复位；
- S3 _ensure_cold_index 恢复后比对 mtime，落后超阈值打"侧车索引落后 X 天"并置 stale；
- 接线修复：_enforce_cold_cache 不再被 cold_compaction 节流早返回阻断（runtime_metrics
  在位且 should_execute=False 时驱逐仍须跑）→ 验证"驱逐→flush→索引重建→新鲜度复位"闭环。
"""
import os
import time
import json
import tempfile
import importlib
import shutil

import pytest

from nucleus.mnemosyne.PulseNodePool import PulseNodePool
from nucleus.mnemosyne.PulseNode import PulseNode

import nucleus.mnemosyne.PulseNodePool as PNP


def _capture_logger():
    """临时把模块级 _module_logger 的 warning/info 重定向到列表，返回 (list, restore_fn)。"""
    _cap = []
    _ow = PNP._module_logger.warning
    _oi = PNP._module_logger.info
    PNP._module_logger.warning = lambda m, *a, **k: _cap.append(("W", m))
    PNP._module_logger.info = lambda m, *a, **k: _cap.append(("I", m))
    def _restore():
        PNP._module_logger.warning = _ow
        PNP._module_logger.info = _oi
    return _cap, _restore


def test_knife3_s1_rebuild_failure_warning_and_stale():
    pool = PulseNodePool()
    _d = tempfile.mkdtemp(prefix="k3_s1_")
    try:
        pool._cold_dir = _d
        pool._cold_storage_enabled = True
        pool._cold_index_stale = False
        pool._cold_index = {"existing": "x"}  # 维持条目数=1

        def _boom():
            raise RuntimeError("simulated read failure")
        pool._read_cold_all_rows = _boom
        _cap, _restore = _capture_logger()
        try:
            pool._rebuild_cold_index()
        finally:
            _restore()
        assert any("冷索引未重建" in m for _, m in _cap), _cap
        assert any("条目数维持 1" in m for _, m in _cap), _cap
        assert pool._cold_index_stale is True
    finally:
        shutil.rmtree(_d, ignore_errors=True)


def test_knife3_s2_write_failure_stale():
    pool = PulseNodePool()
    _d = tempfile.mkdtemp(prefix="k3_s2_")
    try:
        # 把索引路径做成目录，使 open("w") 抛 IsADirectoryError
        _idx_dir = _d + ".index.json"
        os.makedirs(_idx_dir)
        pool._cold_dir = _d
        pool._cold_storage_enabled = True
        pool._cold_index_stale = False
        pool._cold_index = {}
        pool._read_cold_all_rows = lambda: []
        pool._rebuild_cold_index()
        assert pool._cold_index_stale is True
    finally:
        shutil.rmtree(_d, ignore_errors=True)
        shutil.rmtree(_d + ".index.json", ignore_errors=True)


def test_knife3_s2_success_resets_stale():
    pool = PulseNodePool()
    _d = tempfile.mkdtemp(prefix="k3_s2ok_")
    try:
        pool._cold_dir = _d
        pool._cold_storage_enabled = True
        pool._cold_index_stale = True  # 模拟此前陈旧
        pool._cold_index = {}
        pool._read_cold_all_rows = lambda: []
        pool._rebuild_cold_index()
        assert pool._cold_index_stale is False
        assert os.path.isfile(_d + ".index.json")
    finally:
        shutil.rmtree(_d, ignore_errors=True)
        shutil.rmtree(_d + ".index.json", ignore_errors=True)


def test_knife3_s3_stale_warning():
    pool = PulseNodePool()
    _d = tempfile.mkdtemp(prefix="k3_s3_")
    try:
        pool._cold_dir = _d
        pool._cold_storage_enabled = True
        _pf = os.path.join(_d, "x.parquet")
        with open(_pf, "wb") as _f:
            _f.write(b"x")
        os.utime(_pf, None)
        _ip = _d + ".index.json"
        with open(_ip, "w", encoding="utf-8") as _f:
            json.dump({}, _f)
        _old = time.time() - 2 * 86400
        os.utime(_ip, (_old, _old))

        import config as _cfgmod
        pool._cold_index_loaded = False
        pool._cold_index = {}
        _cap, _restore = _capture_logger()
        try:
            pool._ensure_cold_index()
        finally:
            _restore()
        # 默认阈值 3 天，落后 2 天 → INFO（不超阈值），stale=False
        assert pool._cold_index_stale is False
        assert any("落后" in m for _, m in _cap), _cap

        # 覆盖阈值=1 天 → 落后 2 天应 WARNING + stale=True
        _cfgmod.COLD_INDEX_STALE_WARN_DAYS = 1
        pool._cold_index_loaded = False
        pool._cold_index_stale = False
        _cap2, _restore2 = _capture_logger()
        try:
            pool._ensure_cold_index()
        finally:
            _restore2()
            del _cfgmod.COLD_INDEX_STALE_WARN_DAYS
        assert pool._cold_index_stale is True
        assert any("侧车索引落后" in m for _, m in _cap2), _cap2
    finally:
        shutil.rmtree(_d, ignore_errors=True)
        shutil.rmtree(_d + ".index.json", ignore_errors=True)


def test_knife3_enforce_cold_cache_not_blocked_by_throttle():
    """★接线回归：runtime_metrics 在位且 should_execute=False 时，驱逐仍必须跑（修复前被早返回跳过）。"""
    pool = PulseNodePool()
    _d = tempfile.mkdtemp(prefix="k3_wire_")
    try:
        pool._cold_dir = _d
        pool._cold_storage_enabled = True
        pool._max_cold_cache = 2
        for _i in range(5):
            _nd = PulseNode("k3w%d" % _i, source_organ="test")
            _nd.last_activated = float(_i)
            pool._cold[_nd.node_id] = _nd

        class _FakeCtrl:
            def register(self, k, v):
                return None
            def should_execute(self, k):
                return False  # 修复前会导致驱逐早返回被跳过

        import nucleus.runtime_metrics as RM
        _orig = RM.get_adaptive_controller
        RM.get_adaptive_controller = lambda: _FakeCtrl()
        pool._write_cold_node_to_disk = lambda node: True  # 隔离真实落盘，聚焦节流逻辑
        _cap, _restore = _capture_logger()
        try:
            pool._enforce_cold_cache()
        finally:
            _restore()
            RM.get_adaptive_controller = _orig
        assert len(pool._cold) <= 2, (len(pool._cold), "驱逐未跑（被节流早返回阻断）")
        assert len(pool._cold_evicted) >= 3
        assert any("冷池超限驱逐" in m for _, m in _cap), _cap
    finally:
        shutil.rmtree(_d, ignore_errors=True)


def test_knife3_eviction_rebuild_closure():
    """★打桩证据：驱逐→flush→索引重建→新鲜度告警复位 闭环真实跑过一次（需 pyarrow）。"""
    if importlib.util.find_spec("pyarrow") is None:
        pytest.skip("pyarrow 不可用，跳过真实驱逐→重建闭环（throttle 接线测试已覆盖）")
    pool = PulseNodePool()
    _d = tempfile.mkdtemp(prefix="k3_close_")
    try:
        pool._cold_dir = _d
        pool._cold_storage_enabled = True
        pool._max_cold_cache = 2
        pool._cold_index_stale = True  # 模拟此前陈旧
        for _i in range(4):
            _nd = PulseNode("k3c%d" % _i, source_organ="test")
            _nd.last_activated = float(_i)
            _nd.value = "v%d" % _i
            pool._cold[_nd.node_id] = _nd
        # 驱逐（真实落盘）
        pool._enforce_cold_cache()
        assert len(pool._cold) <= 2
        assert len(pool._cold_evicted) >= 2
        # flush + 重建索引
        pool.flush_cold_buffer()
        pool._rebuild_cold_index()
        assert os.path.isfile(_d + ".index.json")
        # 成功重建复位陈旧标记
        assert pool._cold_index_stale is False
    finally:
        shutil.rmtree(_d, ignore_errors=True)
        shutil.rmtree(_d + ".index.json", ignore_errors=True)
