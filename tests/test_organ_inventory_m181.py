# -*- coding: utf-8 -*-
"""第181批 刀3+刀5：器官健康排行榜 + 器官清单导出 门控单测（只读，零写盘）。

刀5：AST 扫描确定性产出 57 器官；核心脑标记口径可复现；JSON 可解析。
刀3：排行榜 collect 全覆盖（运行时 + 静态兜底），排序降序、None 排末尾。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.cognitive.organ_health_leaderboard import OrganHealthLeaderboard  # noqa: E402
from nucleus.cognitive.organ_inventory import (  # noqa: E402
    TASKBOOK_CORE_BRAIN,
    export_organ_inventory,
    organ_names_all,
    scan_organ_classes,
)


def test_scan_organ_classes_deterministic():
    """AST 扫描：两次调用结果一致（确定性，不受运行时装配状态影响）。"""
    first = scan_organ_classes()
    second = scan_organ_classes()
    assert first == second, "AST 扫描非确定性（受运行时状态污染）"
    assert len(first) >= 50, "器官总数异常：%d" % len(first)
    for item in first:
        assert set(item.keys()) >= {"class_name", "organ_name", "file_path", "method_count"}


def test_organ_names_all_and_export():
    """organ_names_all 与 export_organ_inventory 口径一致；JSON 可解析。"""
    names = organ_names_all()
    assert isinstance(names, list) and len(names) == len(scan_organ_classes())
    inv = export_organ_inventory()
    assert inv["total"] == len(names)
    assert isinstance(inv["core_count"], int)
    # 核心脑标记只认真实存在的器官名
    for _core in inv["core_present"]:
        assert _core in TASKBOOK_CORE_BRAIN
        assert _core in names
    assert isinstance(export_organ_inventory(as_json=True), str)
    assert json.loads(export_organ_inventory(as_json=True))["total"] == inv["total"]


def test_export_organ_inventory_layer_distribution():
    """导出口径含分层分布与大脑层计数，逐条 type 计数之和等于总数。"""
    inv = export_organ_inventory()
    organs = inv["organs"]
    assert len(organs) == inv["total"]
    dist = {}
    for _o in organs:
        _t = _o.get("type") or "未分层"
        dist[_t] = dist.get(_t, 0) + 1
    assert sum(dist.values()) == inv["total"]
    assert len(dist) >= 2, "分层分布异常：%s" % dist
    assert sum(1 for _o in organs if _o.get("is_brain_organ")) == inv["brain_count"]


def test_leaderboard_collect_and_rank():
    """排行榜：全覆盖（运行时 + 静态兜底），降序且缺失分排末尾。"""
    board = OrganHealthLeaderboard()
    rows = board.collect()
    assert len(rows) == len(organ_names_all()), "排行榜未全覆盖"
    ranked = board.rank(rows)
    scores = [_r["score"] for _r in ranked]
    known = [_s for _s in scores if _s is not None]
    assert known == sorted(known, reverse=True), "排行榜未降序"
    assert all(_s is None for _s in scores[len(known):]), "None 未排末尾"
    text = board.report_text(top_n=5)
    assert isinstance(text, str) and text.strip()
    payload = board.as_dimension_payload()
    assert isinstance(payload, dict)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
