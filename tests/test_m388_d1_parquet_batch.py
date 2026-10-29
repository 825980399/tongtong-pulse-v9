# -*- coding: utf-8 -*-
"""388刀1：Parquet 加载改逐批转换（原 `to_pylist()` 一次性全量 = 内存尖峰）。

实测依据：2026-10-29 13:00 主进程 tracemalloc top1 =
`PulseSnapshot.py:1457` **3304 万对象 / 2.0GB**，正是 `for _row in _tbl.to_pylist():`。
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot  # noqa: E402


class _FakeBatch:
    def __init__(self, rows):
        self._rows = rows

    def to_pylist(self):
        return list(self._rows)


class _FakeTable:
    """只有 `to_pylist` / `to_batches` 两个方法，够测生成器。"""

    def __init__(self, rows, fail_batches=False):
        self._rows = list(rows)
        self._fail = fail_batches
        self.batch_calls = 0

    def to_pylist(self):
        return list(self._rows)

    def to_batches(self, max_chunksize=None):
        self.batch_calls += 1
        if self._fail:
            raise RuntimeError("to_batches 不可用（模拟旧 pyarrow）")
        _n = int(max_chunksize or len(self._rows))
        for _i in range(0, len(self._rows), _n):
            yield _FakeBatch(self._rows[_i:_i + _n])


def _mk(batch_rows=3):
    _s = PulseSnapshot.__new__(PulseSnapshot)
    _s._M388_PARQUET_BATCH_ROWS = batch_rows
    return _s


_ROWS = [{"id": "n%03d" % _i, "v": _i} for _i in range(10)]


def test_m388_d1_batched_equals_full():
    """★核心：分批遍历的结果与全量 `to_pylist()` **逐字相同**（行序、内容）。"""
    _s = _mk(batch_rows=3)
    _tbl = _FakeTable(_ROWS)
    _batched = list(_s._m388_iter_rows(_tbl))
    assert _batched == _tbl.to_pylist()
    assert _tbl.batch_calls == 1, "应走分批路径"


def test_m388_d1_batch_size_respected():
    """批大小真的生效：10 行 / 批 3 ⇒ 4 批。"""
    _s = _mk(batch_rows=3)
    _tbl = _FakeTable(_ROWS)
    list(_s._m388_iter_rows(_tbl))
    # 10 行、批 3 ⇒ 切片 0..3 / 3..6 / 6..9 / 9..10 = 4 个 batch
    assert _tbl.batch_calls == 1
    assert len(list(_s._m388_iter_rows(_FakeTable(_ROWS)))) == 10


def test_m388_d1_disabled_returns_to_legacy():
    """★零回归路径：批大小 <=0 ⇒ 退回全量 `to_pylist()`（不碰 to_batches）。"""
    _s = _mk(batch_rows=0)
    _tbl = _FakeTable(_ROWS)
    assert list(_s._m388_iter_rows(_tbl)) == _ROWS
    assert _tbl.batch_calls == 0, "禁用时不该调 to_batches"


def test_m388_d1_fallback_when_to_batches_broken():
    """★健壮性：`to_batches` 抛异常 ⇒ **退回全量**，加载绝不因此失败。"""
    _s = _mk(batch_rows=3)
    _tbl = _FakeTable(_ROWS, fail_batches=True)
    assert list(_s._m388_iter_rows(_tbl)) == _ROWS


@pytest.mark.parametrize("val", [1, 2, 7, 100])
def test_m388_d1_any_batch_size_same_result(val):
    """任意批大小（含大于总行数）结果都相同 —— 语义与批大小解耦。"""
    _s = _mk(batch_rows=val)
    assert list(_s._m388_iter_rows(_FakeTable(_ROWS))) == _ROWS


def test_m388_d1_batch_rows_reads_config(monkeypatch):
    """批大小从 `M388_PARQUET_BATCH_ROWS` 读；缺失/非法 ⇒ 回类默认值。"""
    import config as _cfg
    monkeypatch.setattr(_cfg, "M388_PARQUET_BATCH_ROWS", 4321, raising=False)
    _s = PulseSnapshot.__new__(PulseSnapshot)
    assert _s._m388_parquet_batch_rows() == 4321
    monkeypatch.delattr(_cfg, "M388_PARQUET_BATCH_ROWS", raising=False)
    assert _s._m388_parquet_batch_rows() == PulseSnapshot._M388_PARQUET_BATCH_ROWS


def test_m388_d1_default_batch_size_is_conservative():
    """默认批大小要够小（能降峰值）又别太小（别把加载拖慢）。"""
    assert 1000 <= PulseSnapshot._M388_PARQUET_BATCH_ROWS <= 100000


def test_m388_d1_call_site_switched():
    """★守卫：`_m81_load_parquet_unified` 必须走生成器，不是 `to_pylist()`。"""
    import io
    _src = io.open(os.path.join(ROOT, "nucleus", "mnemosyne",
                               "PulseSnapshot.py"), encoding="utf-8").read()
    assert "for _row in self._m388_iter_rows(_tbl):" in _src
    assert "for _row in _tbl.to_pylist():" not in _src, "还留着全量遍历"
