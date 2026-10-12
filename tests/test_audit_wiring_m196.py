# -*- coding: utf-8 -*-
"""196刀1 接线审计专项：审计报告存在 + 结构断言（隔离，铁律151）。"""
import os

_AUDIT_PATH = os.path.join("docs", "分析报告", "路灯_196批接线审计专项报告_20261012.md")

_SIX = [
    "_behavior_check_counter",
    "_security_event_count",
    "_cpu_high_count",
    "_device_check_counter",
    "_cold_recall_fail_count",
    "_load_probe_fail_count",
]


def test_audit_report_exists():
    assert os.path.isfile(_AUDIT_PATH), "审计报告缺失: %s" % _AUDIT_PATH


def test_audit_six_cases_present():
    _txt = open(_AUDIT_PATH, encoding="utf-8").read()
    for _s in _SIX:
        assert _s in _txt, "审计报告缺少六例之一: %s" % _s


def test_audit_three_dingan_table():
    _txt = open(_AUDIT_PATH, encoding="utf-8").read()
    assert "三例定案表" in _txt, "缺少三例定案表"
    assert "telemetry" in _txt and "SelfReflectionEngine" in _txt and "organ_activity" in _txt


def test_audit_telemetry_root_cause():
    _txt = open(_AUDIT_PATH, encoding="utf-8").read()
    assert "例①½" in _txt, "缺少例①½定案grep"
    # 实证修正：根因是开关默认关，而非未挂心跳总线
    assert "_orchestrator_enabled" in _txt
