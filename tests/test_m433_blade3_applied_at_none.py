# -*- coding: utf-8 -*-
"""433 刀3：`applied_at` 哨兵 0 → None（+ schema 归一/校验）。

守护四件事：
  1) 哨兵 ``0`` / ``"0"`` / ``""`` / ``"0.0"`` / ``False`` 一律归一为 ``None``；
     合法时间戳（含数字字符串）原样保留并转成 float；
  2) schema 判据只有两档：``None`` 或 ``> MIN_APPLIED_AT``；
  3) 落盘/读入两端都归一（``_save_patch_list`` / ``_load_patch_list``）；
  4) 4 个补丁构造点写的是 ``None`` 而不是 ``0``（源码级，防回退）。

★为什么要 None 而不是 0（433 T0 实测）：报表按时间戳渲染，``0`` 会显示成
  ``1970-01-01 08:00`` —— 一个看起来"有值"、实际含义是"从未落地"的假象。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.reasoning.PatchManager import (
    MIN_APPLIED_AT,
    PatchManager,
    _m433_applied_at_valid,
    _m433_normalize_applied_at,
    _m433_normalize_applied_at_in,
)

#: 4 个构造点（433 任务书点名；T0 已逐个核实存在）
CTOR_FILES = (
    "nucleus/evolution/LLMEvolutionEngine.py",
    "nucleus/evolution/SelfReflectionEngine.py",
    "nucleus/reasoning/SafeEvolutionExecutor.py",
)


def _src(path: str) -> str:
    with open(os.path.join(ROOT, path), "r", encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


class TestNormalize:
    @pytest.mark.parametrize("bad", [0, 0.0, "0", "", "0.0", "  ", False, "abc", "None"])
    def test_01_sentinels_become_none(self, bad):
        _new, _chg = _m433_normalize_applied_at(bad)
        assert _new is None, f"{bad!r} 应归一为 None，实际 {_new!r}"
        assert _chg is True

    @pytest.mark.parametrize("good", [1792740262.0651288, 946684801, "1792740262.065"])
    def test_02_valid_ts_kept(self, good):
        """合法时刻原样保留（数字不动类型，数字字符串转 float）—— 不制造类型漂移。"""
        _new, _chg = _m433_normalize_applied_at(good)
        assert not isinstance(_new, bool) and isinstance(_new, (int, float))
        assert float(_new) == float(good)
        assert _chg is False, "合法时刻不该被标记为改动"

    def test_03_none_is_noop(self):
        assert _m433_normalize_applied_at(None) == (None, False)

    def test_04_schema_only_two_states(self):
        assert _m433_applied_at_valid(None) is True
        assert _m433_applied_at_valid(MIN_APPLIED_AT + 1) is True
        assert _m433_applied_at_valid(MIN_APPLIED_AT) is False, "下界是开区间"
        assert _m433_applied_at_valid(0) is False
        assert _m433_applied_at_valid("1792740262") is False, "字符串不是合法 schema 值"
        assert _m433_applied_at_valid(True) is False

    def test_05_six_rows_on_disk_are_all_sentinels(self):
        """433 T0 实测：盘上 6 条未落地补丁的 applied_at 全是哨兵。"""
        _hp = os.path.join(ROOT, "data", "patches", "patch_history.json")
        if not os.path.exists(_hp):
            pytest.skip("生产补丁历史不在本机")
        with open(_hp, encoding="utf-8-sig") as _f:
            _hist = json.load(_f)
        _hit = [r for r in _hist if isinstance(r, dict) and r.get("applied_at") in (0, "0", "")]
        # 这两行是 T0 的现场读数；数据被迁走/新增都不该让断言变味
        for _r in _hit:
            assert _m433_normalize_applied_at(_r.get("applied_at"))[0] is None


class TestPorts:
    def test_06_save_patch_list_normalizes(self, tmp_path):
        _pm = PatchManager(str(tmp_path / "patches"))
        _hp = _pm.get_history_file()
        _rows = [{"id": "a", "applied_at": 0}, {"id": "b", "applied_at": ""},
                 {"id": "c", "applied_at": 1792740262.0651288}]
        _pm._save_patch_list(_hp, _rows)
        with open(_hp, encoding="utf-8-sig") as _f:
            _back = {r["id"]: r.get("applied_at") for r in json.load(_f)}
        assert _back["a"] is None and _back["b"] is None
        assert _back["c"] == 1792740262.0651288

    def test_07_load_patch_list_normalizes(self, tmp_path):
        _pm = PatchManager(str(tmp_path / "patches"))
        _hp = _pm.get_history_file()
        with open(_hp, "w", encoding="utf-8") as _f:
            json.dump([{"id": "a", "applied_at": 0}], _f)
        _rows = _pm._load_patch_list(_hp)
        assert _rows[0]["applied_at"] is None

    def test_08_batch_count_is_reported(self):
        _rows = [{"applied_at": 0}, {"applied_at": "0"}, {"applied_at": 1792740262.0},
                 {"applied_at": None}, {"nothing": 1}]
        assert _m433_normalize_applied_at_in(_rows) == 2
        assert [_r.get("applied_at") for _r in _rows] == [None, None, 1792740262.0, None, None]


class TestConstructors:
    def test_09_no_zero_literal_left(self):
        """4 个构造点不得再有 `"applied_at": 0`（回退会立刻被这条抓住）。"""
        for _rel in CTOR_FILES:
            _code = _src(_rel)
            assert '"applied_at": 0' not in _code, f"{_rel} 仍有 applied_at: 0"
            assert '"applied_at": 0,' not in _code, f"{_rel} 仍有 applied_at: 0,"

    def test_10_four_sites_write_none(self):
        """★4 处构造器：SafeEvolutionExecutor 2 处 + 另两个引擎各 1 处。"""
        _expect = {"nucleus/evolution/LLMEvolutionEngine.py": 1,
                   "nucleus/evolution/SelfReflectionEngine.py": 1,
                   "nucleus/reasoning/SafeEvolutionExecutor.py": 2}
        _tot = 0
        for _rel, _n in _expect.items():
            _got = _src(_rel).count('"applied_at": None')
            assert _got == _n, f"{_rel} 期望 {_n} 处 applied_at: None，实际 {_got}"
            _tot += _got
        assert _tot == 4


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
