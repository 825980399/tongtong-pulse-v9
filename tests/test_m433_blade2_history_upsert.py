# -*- coding: utf-8 -*-
"""433 刀2：patch_history 按 id upsert（同 id 多快照合并成一行）。

守护四件事：
  1) 同一 id 的多个生命周期快照合并成 **1 行**，且 ``status_history`` /
     ``wont_fix_*`` 等审计信息不丢；
  2) 验证字段取「数据最全」的那条（``runtime_verify_last_attempt_at`` 最大）；
  3) 幂等 —— 对合并结果再合并一次结果不变；
  4) 落盘端（``_save_patch_list``）与读入端（``_load_patch_list``）用**同一实现**，
     不会出现"写进去 1 行、读出来 2 条"。

★反证（铁律 30）：期望值全部**手写在用例里**（来自 433 T0 对盘上真实那一组的
  实测读数），不取被测函数的输出当基准。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import nucleus.evolution.patch_dedup as _pd
from nucleus.evolution.patch_dedup import (
    TERMINAL_STATUSES,
    merge_group,
    terminal_ts,
    upsert_by_id,
)
from nucleus.reasoning.PatchManager import PatchManager

PID = "o3_1792638584_57885429"


def _older_snapshot() -> dict:
    """较早的验证快照 + 后加的人工撤单（对应盘上 index 85）。"""
    return {
        "id": PID, "file": "nucleus/CompanionBridge.py", "method": "handshake",
        "applied": False, "status": "wont_fix",
        "status_history": [{"status": "wont_fix", "at": 1792740262.0651288,
                            "from": "approved",
                            "reason": "74号裁定：协议接口不可删除", "source": "348刀10"}],
        "wont_fix_reason": "74号裁定：协议接口不可删除",
        "wont_fix_at": 1792740262.0651288,
        "wont_fix_by": "human:小林(74号裁定)",
        "o3_auto_dispatch_ready": False,
        "runtime_verify_last_attempt_at": 1792670329.928142,
        "runtime_verify_fail_count": 0,
        "o3_dispatch_attempts": 2,
        "approved_at": 1792644691.0,
    }


def _newer_snapshot() -> dict:
    """验证数据更全的快照（对应盘上 index 86，status 仍是 approved）。"""
    return {
        "id": PID, "file": "nucleus/CompanionBridge.py", "method": "handshake",
        "applied": False, "status": "approved",
        "o3_auto_dispatch_ready": True,
        "runtime_verify_last_attempt_at": 1792736813.7301075,
        "runtime_verify_mode": "ast_delete",
        "runtime_verify_fail_count": 2,
        "runtime_verify_failure_reason": "ast_delete:post_delete_orphans",
        "o3_dispatch_attempts": 4,
        "approved_at": 1792644691.0,
    }


class TestMergeGroup:
    def test_01_single_row_out(self):
        _m = merge_group([_older_snapshot(), _newer_snapshot()], now=1800000000.0)
        assert isinstance(_m, dict) and _m["id"] == PID

    def test_02_terminal_status_wins(self):
        """人已撤单 ⇒ status 取 wont_fix（不能落回 approved，否则撤单信号丢失）。"""
        _m = merge_group([_older_snapshot(), _newer_snapshot()], now=1800000000.0)
        assert _m["status"] == "wont_fix"
        # 350刀4 的判据：撤单后不得再派单
        assert _m["o3_auto_dispatch_ready"] is False

    def test_03_wont_fix_reason_kept(self):
        _m = merge_group([_older_snapshot(), _newer_snapshot()], now=1800000000.0)
        assert _m["wont_fix_reason"] == "74号裁定：协议接口不可删除"
        assert _m["wont_fix_by"] == "human:小林(74号裁定)"
        assert _m["wont_fix_at"] == 1792740262.0651288
        _sh = _m.get("status_history") or []
        assert len(_sh) == 1 and _sh[0]["status"] == "wont_fix"
        assert _sh[0]["from"] == "approved"

    def test_04_verify_payload_from_fullest_row(self):
        """验证字段必须取自 `runtime_verify_last_attempt_at` 最大的那条。"""
        _m = merge_group([_older_snapshot(), _newer_snapshot()], now=1800000000.0)
        assert _m["runtime_verify_last_attempt_at"] == 1792736813.7301075
        assert _m["runtime_verify_mode"] == "ast_delete"
        assert _m["runtime_verify_fail_count"] == 2
        assert _m["o3_dispatch_attempts"] == 4
        assert _m["approved_at"] == 1792644691.0

    def test_05_status_history_union_dedup_and_sorted(self):
        _a = {"id": "x", "status": "approved",
              "status_history": [{"status": "pending", "at": 100.0},
                                 {"status": "approved", "at": 200.0}]}
        _b = {"id": "x", "status": "wont_fix", "wont_fix_at": 300.0,
              "status_history": [{"status": "approved", "at": 200.0},
                                 {"status": "wont_fix", "at": 300.0}]}
        _m = merge_group([_a, _b], now=1800000000.0)
        _sh = _m["status_history"]
        assert [h["at"] for h in _sh] == [100.0, 200.0, 300.0], (
            "status_history 应并集去重并按时间升序")
        assert _m["status"] == "wont_fix"

    def test_06_switch_off_keeps_base_status(self, monkeypatch):
        """★灰度开关：关掉终态优先后，status 逐字回到基底值（approved）。"""
        monkeypatch.setattr(_pd, "TERMINAL_STATUS_WINS", False)
        _m = merge_group([_older_snapshot(), _newer_snapshot()], now=1800000000.0)
        assert _m["status"] == "approved", "开关关闭时不应改动状态策略"
        assert _m["wont_fix_reason"] == "74号裁定：协议接口不可删除", "撤单理由仍要保留"

    def test_07_terminal_ts_reads_dedicated_field(self):
        assert terminal_ts({"status": "wont_fix", "wont_fix_at": 123.0}) == 123.0
        assert terminal_ts({"status": "approved"}) is None
        assert terminal_ts({"status": "WONT_FIX", "wont_fix_at": 9.0}) == 9.0
        assert "wont_fix" in TERMINAL_STATUSES


class TestUpsertList:
    def test_08_merges_only_duplicate_ids(self):
        _hist = [{"id": "a", "status": "approved"}, {"id": "b", "status": "pending"},
                 {"id": "a", "status": "wont_fix", "wont_fix_at": 5.0},
                 {"no_id": 1}, {"no_id": 2}]
        _kept, _st = upsert_by_id(_hist, now=1800000000.0)
        assert len(_kept) == 4, "a 的两行应合并，其余原样"
        assert _st["merged"] == 1 and _st["merged_ids"] == ["a"]
        assert _st["before"] == 5 and _st["after"] == 4
        # 无 id 的记录不是"重复"，不得被压成一条
        assert sum(1 for _r in _kept if "no_id" in _r) == 2

    def test_09_keeps_first_appearance_position(self):
        _hist = [{"id": "a", "status": "approved"}, {"id": "b", "status": "pending"},
                 {"id": "a", "status": "wont_fix", "wont_fix_at": 5.0}]
        _kept, _ = upsert_by_id(_hist, now=1800000000.0)
        assert [r["id"] for r in _kept] == ["a", "b"], "合并结果必须占首次出现的位置"

    def test_10_idempotent(self):
        _hist = [_older_snapshot(), _newer_snapshot()]
        _once, _ = upsert_by_id(_hist, now=1800000000.0)
        _twice, _st2 = upsert_by_id(_once, now=1800000000.0)
        assert len(_twice) == 1 and _st2["merged"] == 0
        assert _twice[0] == _once[0], "幂等：再合并一次不得改动内容"

    def test_11_non_list_input_passthrough(self):
        _out, _st = upsert_by_id({"not": "a list"})
        assert _out == {"not": "a list"} and _st["merged"] == 0


class TestWriteAndReadPort:
    def test_12_save_patch_list_upserts_history(self, tmp_path):
        _pm = PatchManager(str(tmp_path / "patches"))
        _hp = _pm.get_history_file()
        assert _pm._save_patch_list(_hp, [_older_snapshot(), _newer_snapshot()]) is True
        with open(_hp, encoding="utf-8-sig") as _f:
            _back = json.load(_f)
        assert len(_back) == 1, f"落盘仍有 {len(_back)} 行重复"
        assert _back[0]["status"] == "wont_fix"
        assert _back[0]["wont_fix_reason"] == "74号裁定：协议接口不可删除"

    def test_13_load_patch_list_dedups_history(self, tmp_path):
        """读入端同源：手里已有 2 行时读出来必须是 1 条。"""
        _pm = PatchManager(str(tmp_path / "patches"))
        _hp = _pm.get_history_file()
        with open(_hp, "w", encoding="utf-8") as _f:
            json.dump([_older_snapshot(), _newer_snapshot()], _f, ensure_ascii=False)
        _rows = _pm._load_patch_list(_hp)
        assert len(_rows) == 1 and _rows[0]["status"] == "wont_fix"

    def test_14_pending_path_is_not_upserted(self, tmp_path):
        """本刀只治 history：pending 的唯一写口是 write_pending，不得被顺手合并。"""
        _pm = PatchManager(str(tmp_path / "patches"))
        _pp = _pm.get_pending_file()
        _rows = [{"id": "p1", "status": "pending", "applied": False},
                 {"id": "p1", "status": "pending", "applied": False}]
        _pm._save_patch_list(_pp, _rows)
        with open(_pp, encoding="utf-8-sig") as _f:
            _back = json.load(_f)
        assert len(_back) == 2, f"pending 被误合并成 {len(_back)} 行"

    def test_15_dedup_history_rows_method_exists(self, tmp_path):
        """给直接 load_json 的读点留的入口必须存在。"""
        _pm = PatchManager(str(tmp_path / "patches"))
        assert callable(getattr(_pm, "dedup_history_rows", None))
        _rows = _pm.dedup_history_rows([_older_snapshot(), _newer_snapshot()])
        assert len(_rows) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
