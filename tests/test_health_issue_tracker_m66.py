#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""健康诊断增强追踪器测试 —— 主线第66批 T2。

验证：
- ruff --statistics 正确解析（忽略 Found 汇总行、按规则聚合）
- 规则分级 tier_of（P0 真实 bug / P1 隐患 / P2 风格现代化 / P3 复杂度）
- 已知安全标注（S102 / PLW1510 / PLR0124 非 bug 不修）
- classify 聚合（total / by_tier / real_bugs / known_safe / autofixable）
- 增量趋势 diff_by_tier
- build_enhanced_report 生成 + 写盘
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.health_issue_tracker as _hit  # noqa: E402

# 样例 statistics（格式：count\trule\t[mark]\tname；末尾 Found 行忽略）
_SAMPLE = """\
717\tUP031\t[*] printf-string-formatting
329\tI001\t[*] unsorted-imports
24\tPLW1510\t[ ] subprocess-run-without-check
13\tS102\t[ ] exec-builtin
2\tPLR0124\t[ ] comparison-with-itself
1\tF401\t[*] unused-import
1\tB010\t[*] set-attr-with-constant
1\tC901\t[ ] too-complex
Found 1088 errors.
"""


def _write_sample():
    _f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8")
    _f.write(_SAMPLE)
    _f.close()
    return _f.name


def test_10_parse_ruff_statistics_aggregates_and_skips_found():
    _p = _write_sample()
    try:
        _stats = _hit.parse_ruff_statistics(_p)
        # Found 行被忽略，规则聚合
        assert "UP031" in _stats
        assert _stats["UP031"][0] == 717
        assert "Found" not in _stats
        # 规则总数 = 8
        assert len(_stats) == 8
    finally:
        os.remove(_p)


def test_11_tier_of_classification():
    # P0 真实 bug
    assert _hit.tier_of("F401") == "P0"
    assert _hit.tier_of("F811") == "P0"
    assert _hit.tier_of("E901") == "P0"
    # P1 隐患（exec / subprocess / bugbear / pylint-warning）
    assert _hit.tier_of("S102") == "P1"
    assert _hit.tier_of("PLW1510") == "P1"
    assert _hit.tier_of("B010") == "P1"
    # P2 风格现代化（pyupgrade / isort / ruf100 / simplify / comprehensions）
    assert _hit.tier_of("UP031") == "P2"
    assert _hit.tier_of("I001") == "P2"
    assert _hit.tier_of("RUF100") == "P2"
    assert _hit.tier_of("SIM115") == "P2"
    assert _hit.tier_of("C408") == "P2"
    # P3 复杂度 / 现代化建议（refurb / datetime-tz / 其余 ruf / C901）
    assert _hit.tier_of("FURB122") == "P3"
    assert _hit.tier_of("C901") == "P3"
    assert _hit.tier_of("PLR0913") == "P3"
    # PLR0124 属 P3（NaN 检测惯用法，由 known_safe 标注）
    assert _hit.tier_of("PLR0124") == "P3"


def test_12_known_safe_rules_tagged():
    _p = _write_sample()
    try:
        _cls = _hit.classify(_hit.parse_ruff_statistics(_p))
        # S102 + PLW1510 + PLR0124 = 13 + 24 + 2 = 39
        assert _cls["known_safe"] == 39
        assert _cls["detail"]["S102"]["known_safe"] is True
        assert _cls["detail"]["PLW1510"]["known_safe"] is True
        assert _cls["detail"]["PLR0124"]["known_safe"] is True
        # 这些非真实 bug
        assert _cls["detail"]["S102"]["tier"] != "P0"
    finally:
        os.remove(_p)


def test_13_classify_aggregate_correct():
    _p = _write_sample()
    try:
        _cls = _hit.classify(_hit.parse_ruff_statistics(_p))
        assert _cls["total"] == 1088
        # by_tier: P0=F401(1) P1=PLW1510+S102+B010=38
        #          P2=UP031+I001=1046  P3=PLR0124+C901=3
        assert _cls["by_tier"]["P0"] == 1
        assert _cls["by_tier"]["P1"] == 38
        assert _cls["by_tier"]["P2"] == 1046
        assert _cls["by_tier"]["P3"] == 3
        # real_bugs = P0 总数
        assert _cls["real_bugs"] == 1
        # autofixable: UP031(717)+I001(329)+F401(1)+B010(1)=1048
        assert _cls["autofixable"] == 1048
        assert _cls["distinct_rules"] == 8
    finally:
        os.remove(_p)


def test_14_diff_by_tier_increment():
    _prev = {"by_tier": {"P0": 1, "P1": 40, "P2": 1046, "P3": 1}}
    _cur = {"by_tier": {"P0": 0, "P1": 38, "P2": 1050, "P3": 2}}
    _d = _hit.diff_by_tier(_prev, _cur)
    assert _d == {"P0": -1, "P1": -2, "P2": 4, "P3": 1}


def test_15_build_enhanced_report_writes_file():
    _p = _write_sample()
    _out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    try:
        _rep = _hit.build_enhanced_report(_p, out_path=_out)
        assert _rep["total"] == 1088
        assert _rep["real_bugs"] == 1
        assert "note" in _rep and "655" in _rep["note"]
        assert os.path.isfile(_out)
        _loaded = __import__("json").load(open(_out, encoding="utf-8"))
        assert _loaded["total"] == 1088
        assert _loaded["known_safe"] == 39
    finally:
        os.remove(_p)
        if os.path.isfile(_out):
            os.remove(_out)
