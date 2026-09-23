#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归基线测试 —— 主线第66批 T5。

验证分片回归判定的关键 API（防第55批「0/0 误读为通过」），并确认本批 4 个
m66 测试文件均可被 pytest 正常收集（无语法/导入层面阻断）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.pytest_shard as _ps  # noqa: E402

_M66_FILES = [
    "tests/test_memory_growth_analyzer_m66.py",
    "tests/test_health_issue_tracker_m66.py",
    "tests/test_experience_quarantine_m66.py",
    "tests/test_regression_baseline_m66.py",
]


def test_10_classify_exit_code_mapping():
    assert _ps.classify_exit_code(0) == "pass"
    assert _ps.classify_exit_code(1) == "fail"
    assert _ps.classify_exit_code(2) == "interrupt"
    assert _ps.classify_exit_code(3) == "internal_error"
    assert _ps.classify_exit_code(4) == "cmdline_error"
    assert _ps.classify_exit_code(5) == "no_tests_collected"


def test_11_evaluate_shard_pass_when_real_results():
    _out = "....\n5 passed in 1.23s\n"
    _r = _ps.evaluate_shard(_out, 0, 1)
    assert _r["status"] == "pass"
    assert _r["passed"] == 5
    assert _r["alarm"] is False


def test_12_evaluate_shard_alarm_on_zero_zero():
    # passed+failed==0 但确有文件 → 绝不误读为通过（第55批根因防护）
    _r = _ps.evaluate_shard("", 0, 2)
    assert _r["alarm"] is True
    assert _r["status"] == "suspicious"
    assert _r["passed"] == 0 and _r["failed"] == 0


def test_13_m66_test_files_collectable():
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _rel in _M66_FILES:
        _p = os.path.join(_root, _rel)
        assert os.path.isfile(_p), f"缺失 m66 测试文件: {_rel}"
        # 收集该文件（不执行），exit 0 即收集成功
        import subprocess
        _pr = subprocess.run(
            [sys.executable, "-m", "pytest", _p, "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            cwd=_root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
        assert _pr.returncode == 0, (
            f"{_rel} 收集失败: {_pr.stdout[-300:]}{_pr.stderr[-300:]}")
