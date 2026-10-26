# -*- coding: utf-8 -*-
"""372 刀13 定向单测（第一批）：高危 __main__ 块逐处收口。

票 `T-371-高危59逐处收口-1`。本批收口 10 块（块内 assert==0 的纯演示块），顺延 1 块。
"""
from __future__ import annotations

import ast
import io
import json
import os

_EVID_JSON = os.path.join("docs", "路灯与星轨对话", "交付报告", "证据件", "372",
                          "刀13_高危59逐处收口_20261026.json")
_EVID_MD = os.path.join("docs", "路灯与星轨对话", "交付报告", "证据件", "372",
                        "刀13_高危59逐处收口_20261026.md")


def _blocks_in(fp):
    """返回该文件里模块级 __main__ 块的数目。"""
    _t = io.open(fp, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    _n = 0
    for _node in ast.parse(_t).body:
        if isinstance(_node, ast.If) and isinstance(_node.test, ast.Compare):
            _l = _node.test.left
            if isinstance(_l, ast.Name) and _l.id == "__name__" and _node.test.comparators \
                    and isinstance(_node.test.comparators[0], ast.Constant) \
                    and _node.test.comparators[0].value == "__main__":
                _n += 1
    return _n


def test_01_evidence_matches_reality():
    """①清单与机读 JSON 一致，且计数与"改前改后"对得上。"""
    _d = json.load(io.open(_EVID_JSON, encoding="utf-8"))
    assert _d["collected_n"] == 10
    assert _d["deferred_n"] == 1
    assert _d["baseline"]["high_risk"] - _d["after"]["high_risk"] == _d["collected_n"]
    assert _d["baseline"]["blocks"] - _d["after"]["blocks"] == _d["collected_n"]
    assert _d["baseline"]["lines"] - _d["after"]["lines"] == _d["lines_removed"]
    assert _d["remaining_high_risk"] == _d["after"]["high_risk"]


def test_02_every_collected_block_is_gone_and_deferred_kept():
    """②收口的 10 个文件已无 __main__ 块；顺延的那块**必须还在**（不许顺手删）。"""
    _d = json.load(io.open(_EVID_JSON, encoding="utf-8"))
    for _row in _d["collected"]:
        assert _blocks_in(_row["file"]) == 0, "★收口后仍有 __main__ 块: %s" % _row["file"]
        assert _row["assert_in_block"] == 0, "★本批规则：块内 assert 必须为 0"
    for _s in _d["deferred"]:
        assert _blocks_in(_s["file"]) == 1, "★顺延块不得被删: %s" % _s["file"]


def test_03_modules_still_importable_and_public_class_kept():
    """③删块后模块仍可编译（public 类未被误删）—— 用 AST 校验类定义仍在。"""
    _d = json.load(io.open(_EVID_JSON, encoding="utf-8"))
    for _row in _d["collected"]:
        _t = io.open(_row["file"], encoding="utf-8", errors="replace").read()
        _tree = ast.parse(_t.replace("\r\n", "\n"))
        _classes = [n.name for n in _tree.body if isinstance(n, ast.ClassDef)]
        assert _classes, "★%s 里一个顶层类都没有，疑似误删" % _row["file"]
        # 行尾守恒：CRLF 文件删块后仍是 CRLF
        _raw = open(_row["file"], "rb").read()
        if _row["crlf"]:
            assert _raw.count(b"\n") == _raw.count(b"\r\n"), "★CRLF 被破坏: %s" % _row["file"]


def test_04_evidence_md_written_with_rule():
    """④人读件在位且写明裁决规则（assert==0 / AST 边界 / 编译+AST 全等）。"""
    _t = io.open(_EVID_MD, encoding="utf-8").read()
    for _k in ("assert` 计数必须为 0", "AST 精确定位", "逐节点相等", "顺延"):
        assert _k in _t, "证据件缺规则: %s" % _k
    assert "111" in _t and "101" in _t and "59" in _t and "49" in _t
