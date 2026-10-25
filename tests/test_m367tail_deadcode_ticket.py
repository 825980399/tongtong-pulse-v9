#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T-死代码放大器修复-1 三件事的回归用例（367 后续）。

覆盖：
  ① `cap_low_severity` 低危限额（纯函数）
  ② 趋势三种显式归类 `KLASS_TREND` + 路由到 optimize_list
  ③ `trend_board_snapshot` 只读看板出口（三态不伪装）
"""
from __future__ import annotations

import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.audit_problem_pool import (  # noqa: E402
    KLASS_TREND, MAX_LOW_ENTRIES, ROUTE_OPTIMIZE_LIST, ROUTE_PATCH_POOL,
    TREND_ISSUE_TYPES, cap_low_severity, classify_klass, classify_route,
    trend_board_snapshot,
)


def _e(i, sev, status="open", ts=None):
    return {"id": "x%d" % i, "severity": sev, "status": status,
            "first_seen_ts": i if ts is None else ts}


class TestLowCap:
    def test_01_no_cap_returns_as_is(self):
        """未超限 ⇒ 原样返回（连顺序都不动），dropped 空。"""
        rows = [_e(3, "low"), _e(1, "high"), _e(2, "low")]
        kept, dropped = cap_low_severity(rows, cap=5)
        assert [x["id"] for x in kept] == ["x3", "x1", "x2"]
        assert dropped == []

    def test_02_cap_only_hits_low(self):
        """超限只砍 low；high/medium 一条都不能少（限额绝不挤掉可修项）。"""
        rows = [_e(1, "low"), _e(2, "high"), _e(3, "low"), _e(4, "medium"), _e(5, "low")]
        kept, dropped = cap_low_severity(rows, cap=1)
        assert len(dropped) == 2
        sevs = sorted(x["severity"] for x in kept)
        assert sevs == ["high", "low", "medium"], "high/medium 必须全留且只留 1 条 low"
        assert all(x["severity"] == "low" for x in dropped)

    def test_03_kept_order_preserved(self):
        """kept 保持入参相对顺序（下游按序消费，不能被重排）。"""
        rows = [_e(1, "low"), _e(2, "high"), _e(3, "low"), _e(4, "low")]
        kept, _ = cap_low_severity(rows, cap=2)
        ids = [x["id"] for x in kept]
        assert ids == sorted(ids, key=lambda s: int(s[1:])), "kept 必须保序：%s" % ids

    def test_04_open_first_then_newest(self):
        """超限时保留规则：open 优先 → 首次时间新优先（与全局截断同序）。"""
        rows = [
            _e(1, "low", status="wontfix", ts=999),  # 真终结态（TERMINAL_STATUSES=(verified,wontfix)）但很新
            _e(2, "low", status="open", ts=10),     # open 旧
            _e(3, "low", status="open", ts=50),     # open 新
        ]
        kept, dropped = cap_low_severity(rows, cap=1)
        assert [x["id"] for x in kept] == ["x3"], "应保 open 里最新的 x3"
        # cap=1 ⇒ 3 条 low 里只留 1 条，另 2 条都进 dropped（已结案的最先被砍）
        assert [x["id"] for x in dropped] == ["x2", "x1"],             "dropped 应含 x2（open 但较旧）与 x1（已结案，与 open 不同组、排最后）"

    def test_05_cap_zero_and_negative(self):
        """cap=0 / 负数 ⇒ low 全不入快照，但不抛异常（high 仍全留）。"""
        rows = [_e(1, "low"), _e(2, "high")]
        kept, dropped = cap_low_severity(rows, cap=0)
        assert [x["id"] for x in kept] == ["x2"]
        assert len(dropped) == 1
        kept2, dropped2 = cap_low_severity(rows, cap=-5)
        assert [x["id"] for x in kept2] == ["x2"]
        assert len(dropped2) == 1

    def test_06_default_cap_is_module_constant(self):
        """不传 cap ⇒ 用 MAX_LOW_ENTRIES；且常量落在合理区间（不是 0、不是全池）。"""
        assert isinstance(MAX_LOW_ENTRIES, int) and 0 < MAX_LOW_ENTRIES < 5000
        rows = [_e(i, "low") for i in range(MAX_LOW_ENTRIES + 3)]
        kept, dropped = cap_low_severity(rows)
        assert len([x for x in kept if x["severity"] == "low"]) == MAX_LOW_ENTRIES
        assert len(dropped) == 3

    def test_07_empty_input(self):
        assert cap_low_severity([], cap=3) == ([], [])


class TestTrendKlass:
    def test_08_three_trends_are_trend_class(self):
        """三种趋势 ⇒ klass=trend（**不再**是 unknown 兜底）。"""
        for t in TREND_ISSUE_TYPES:
            assert classify_klass(t) == KLASS_TREND, t

    def test_09_three_trends_route_to_optimize_list(self):
        """显式路由到 optimize_list（看板侧），不进 patch_pool。"""
        for t in TREND_ISSUE_TYPES:
            assert classify_route(t) == ROUTE_OPTIMIZE_LIST, t
            assert classify_route(t) != ROUTE_PATCH_POOL, t

    def test_10_no_regression_on_existing_klasses(self):
        """既有族判定不受影响（防止改族表把别的类型串过去）。"""
        assert classify_klass("dead_code") == "code_defect"
        assert classify_route("dead_code") == ROUTE_PATCH_POOL
        assert classify_klass("pollution") == "pollution"
        assert classify_klass("experience") == "experience"
        assert classify_klass("完全没见过的类型") == "unknown"

    def test_11_trend_list_is_subset_of_klass_table(self):
        """TREND_ISSUE_TYPES 与族表同源：漏一个就会退回兜底，必须对称。"""
        from nucleus.audit_problem_pool import _KLASS_TABLE
        assert set(TREND_ISSUE_TYPES) == set(_KLASS_TABLE[KLASS_TREND])


class TestTrendBoard:
    def _write(self, d, rows):
        p = os.path.join(d, "routes")
        os.makedirs(p, exist_ok=True)
        with io.open(os.path.join(p, "optimize_list.jsonl"), "w",
                     encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_12_absent_is_not_zero(self, tmp_path):
        """文件不存在 ⇒ state=absent / total=None（不伪装成「0 条」）。"""
        r = trend_board_snapshot(audit_dir_path=str(tmp_path))
        assert r["state"] == "absent"
        assert r["total"] is None
        assert all(v["count"] is None for v in r["trends"].values())

    def test_13_empty_file_is_zero(self, tmp_path):
        """文件存在但没内容 ⇒ state=empty / total=0（与 absent 严格区分）。"""
        self._write(str(tmp_path), [])
        r = trend_board_snapshot(audit_dir_path=str(tmp_path))
        assert r["state"] == "empty"
        assert r["total"] == 0
        assert all(v["count"] == 0 for v in r["trends"].values())

    def test_14_counts_and_others(self, tmp_path):
        """按 issue_type 计数；非趋势条目进 others（不混算）。"""
        self._write(str(tmp_path), [
            {"issue_type": "cc_ge50_new", "status": "open", "first_seen_ts": 10},
            {"issue_type": "cc_ge50_new", "status": "wontfix", "first_seen_ts": 20},
            {"issue_type": "silent_except_weekly_rise", "status": "open", "first_seen_ts": 30},
            {"issue_type": "experience", "status": "open", "first_seen_ts": 40},
        ])
        r = trend_board_snapshot(audit_dir_path=str(tmp_path))
        assert r["state"] == "ok"
        assert r["total"] == 4
        assert r["trends"]["cc_ge50_new"]["count"] == 2
        assert r["trends"]["cc_ge50_new"]["open"] == 1
        assert r["trends"]["cc_ge50_new"]["latest_ts"] == 20
        assert r["trends"]["code_loc_weekly_growth"]["count"] == 0
        assert r["others"] == 1, "experience 不该被算进任何趋势"

    def test_15_bad_line_counted_not_raised(self, tmp_path):
        """坏行只计数不抛（看板出口不能因一行脏数据整块失效）。"""
        p = os.path.join(str(tmp_path), "routes")
        os.makedirs(p, exist_ok=True)
        with io.open(os.path.join(p, "optimize_list.jsonl"), "w",
                     encoding="utf-8", newline="\n") as fh:
            fh.write('{"issue_type":"cc_ge50_new","status":"open"}\n')
            fh.write("{这不是合法 json\n")
        r = trend_board_snapshot(audit_dir_path=str(tmp_path))
        assert r["state"] == "ok"
        assert r["truncated_lines"] == 1
        assert r["trends"]["cc_ge50_new"]["count"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
