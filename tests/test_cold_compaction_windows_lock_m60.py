# -*- coding: utf-8 -*-
"""主线第60批 T6 门控：冷存 compaction Windows 文件锁重试 + 冷却降级。

用真实 parquet 夹具驱动 compact_cold_storage，对原子替换阶段的
shutil.rmtree（删除旧目录）/ os.rename（重命名临时目录）做异常注入，
验证删除重试、重命名重试、冷却降级、临时目录清理四条防御路径。

为不真实等待 5×1s，mock time.sleep 为 no-op；失败路径用 side_effect 抛 OSError。
"""
import os
import sys
import time
import unittest.mock as mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from nucleus.mnemosyne.PulseNodePool import (  # noqa: E402
    COLD_COMPACT_MIN_FILES,
    PulseNodePool,
)


def _make_fixtures(cold_dir):
    """在 cold_dir/evol_level=L1 下写入 ≥2 个有效 parquet 文件。"""
    _l1 = os.path.join(cold_dir, "evol_level=L1")
    os.makedirs(_l1, exist_ok=True)
    for _i in range(COLD_COMPACT_MIN_FILES + 1):
        _t = pa.table({
            "node_id": [f"n{_i}"],
            "evol_level": ["L1"],
            "data": [f"v{_i}"],
        })
        pq.write_table(_t, os.path.join(_l1, f"part-{_i}.parquet"))
    return _l1


def _new_pool(cold_dir):
    _p = PulseNodePool(max_hot=10, max_warm=10)
    _p._cold_storage_enabled = True
    _p._cold_dir = cold_dir
    return _p


def test_cold_compact_disabled(tmp_path):
    """冷存未启用 → 直接返回 cold_storage_disabled。"""
    _p = PulseNodePool(max_hot=10, max_warm=10)
    _p._cold_storage_enabled = False
    _r = _p.compact_cold_storage()
    assert _r.get("reason") == "cold_storage_disabled"


def test_cold_compact_no_data(tmp_path):
    """L1 分区不存在 → 返回 no_data（success=True，不报错）。"""
    _p = _new_pool(str(tmp_path / "cold"))
    _r = _p.compact_cold_storage()
    assert _r.get("reason") == "no_data"
    assert _r.get("success") is True


def test_cold_compact_delete_retry_exhausted(tmp_path):
    """删除旧目录连续失败（重试次数=可配默认值）→ 返回 delete_retry_exhausted。

    第63批 T2：删旧目录重试次数/间隔已从硬编码 5/1s 改为可配
    （COLD_COMPACT_DELETE_RETRY_COUNT 默认 10，见 config.py）。
    所有重试 + 1 次强制删除兜底（ignore_errors）均失败时，
    rmtree 总调用次数 = 重试次数 + 1，reason=delete_retry_exhausted。
    """
    import config as _cfg
    _retry_n = int(getattr(_cfg, "COLD_COMPACT_DELETE_RETRY_COUNT", 10))
    _cold = str(tmp_path / "cold")
    _make_fixtures(_cold)
    _p = _new_pool(_cold)
    with mock.patch("time.sleep", lambda *a, **k: None):
        with mock.patch("shutil.rmtree", side_effect=OSError("locked")) as _rm:
            _r = _p.compact_cold_storage()
    assert _r.get("success") is False
    assert _r.get("reason") == "delete_retry_exhausted"
    assert _rm.call_count == _retry_n + 1, \
        f"rmtree 应被调用 重试次数+1次(含强制删除兜底)，实际 {_rm.call_count}"


def test_cold_compact_delete_ok_rename_exhausted(tmp_path):
    """删除成功但重命名连续失败 5 次 → 返回 rename_retry_exhausted，rename 被调用 5 次。"""
    _cold = str(tmp_path / "cold")
    _make_fixtures(_cold)
    _p = _new_pool(_cold)
    with mock.patch("time.sleep", lambda *a, **k: None):
        with mock.patch("shutil.rmtree", return_value=None):
            with mock.patch("os.rename", side_effect=OSError("locked")) as _rn:
                _r = _p.compact_cold_storage()
    assert _r.get("success") is False
    assert _r.get("reason") == "rename_retry_exhausted"
    assert _rn.call_count == 5


def test_cold_compact_rename_success_after_retries(tmp_path):
    """重命名前 4 次失败、第 5 次成功 → 整体 success=True。"""
    _cold = str(tmp_path / "cold")
    _make_fixtures(_cold)
    _p = _new_pool(_cold)
    _calls = {"n": 0}

    def _rename_fail4(*a, **k):
        _calls["n"] += 1
        if _calls["n"] < 5:
            raise OSError("locked")
        return None

    with mock.patch("time.sleep", lambda *a, **k: None):
        with mock.patch("shutil.rmtree", return_value=None):
            with mock.patch("os.rename", side_effect=_rename_fail4) as _rn:
                _r = _p.compact_cold_storage()
    assert _r.get("success") is True
    assert _rn.call_count == 5


def test_cold_compact_cooldown_skip(tmp_path):
    """冷却期内 → 跳过（reason=cooldown），不执行任何删除/重命名。"""
    _cold = str(tmp_path / "cold")
    _make_fixtures(_cold)
    _p = _new_pool(_cold)
    _p._cold_compact_cooldown_until = time.time() + 3600
    with mock.patch("shutil.rmtree") as _rm:
        with mock.patch("os.rename") as _rn:
            _r = _p.compact_cold_storage()
    assert _r.get("reason") == "cooldown"
    assert _r.get("success") is False
    _rm.assert_not_called()
    _rn.assert_not_called()


def test_cold_compact_temp_dir_cleanup(tmp_path):
    """预置旧临时目录 → 原子替换前被清理，结束后临时目录不复存在。"""
    _cold = str(tmp_path / "cold")
    _make_fixtures(_cold)
    _tmp_dir = _cold + ".compact_tmp"
    os.makedirs(_tmp_dir, exist_ok=True)  # 模拟上轮中断残留的临时目录
    _p = _new_pool(_cold)
    _r = _p.compact_cold_storage()  # 真实 rmtree / rename
    assert _r.get("success") is True
    assert not os.path.exists(_tmp_dir)  # 临时目录已清理/已重命名走
    assert os.path.exists(_cold)
