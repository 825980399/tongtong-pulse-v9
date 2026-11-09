# -*- coding: utf-8 -*-
"""485刀5（P1）：issue_type 与 diff 构造的匹配检查 —— 名实不符的补丁直接拒。

★三类匹配口径（烛微 §2.6）：
  * silent_exception      ⇒ 改后必须有 except 且不是纯吞（pass/continue）；
  * print_instead_of_log  ⇒ 原文有 print(、改后无 print( 且新增了日志调用；
  * periodic_task_no_reentry ⇒ 新增行里必须有重入守卫习语。
其余 issue_type 本批**不装全覆盖**（诚实口径：匹配不了就放行 406 原判）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.evolution import patch_risk_gate


def _rec(orig, new, issue_type, fname="organs/brain/PulseCortex.py"):
    return {"id": "m485i", "file": fname, "method": "m",
            "issue_type": issue_type, "original_code": orig,
            "modified_code": new,
            "verification": {"passed": True},
            "no_regression": True, "post_apply_errors": None,
            "verified": True, "status": "runtime_verified"}


# ---------------------------------------------- 1) silent_exception 名实相符
def test_silent_exception_match_ok(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_ISSUE_MATCH_GATE", True, raising=False)
    orig = ("try:\n    do()\nexcept Exception:\n    pass\n")
    new = ("try:\n    do()\nexcept Exception as e:\n"
           "    self._log('WARN', f'失败: {e}')\n")
    j = patch_risk_gate.judge(_rec(orig, new, "silent_exception"),
                              project_root=os.getcwd())
    assert not j.get("m485_reject_reason"), j


# ---------------------------------------------- 2) silent_exception 仍在吞 ⇒ 拒
def test_silent_exception_still_swallowing_rejected(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_ISSUE_MATCH_GATE", True, raising=False)
    orig = ("try:\n    do()\nexcept Exception:\n    pass\n")
    new = ("try:\n    do()\nexcept Exception:\n    continue\n")
    j = patch_risk_gate.judge(_rec(orig, new, "silent_exception"),
                              project_root=os.getcwd())
    r = j.get("m485_reject_reason") or ""
    assert r.startswith("issue_type_mismatch"), j
    assert "still_swallowing" in r, r
    assert j["eligible"] is False, j


# ---------------------------------------------- 3) print_instead_of_log 名实相符
def test_print_to_log_match_ok(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_ISSUE_MATCH_GATE", True, raising=False)
    orig = ("def f():\n    print('调试信息', x)\n")
    new = ("def f():\n    self._log('INFO', f'调试信息 {x}')\n")
    j = patch_risk_gate.judge(_rec(orig, new, "print_instead_of_log"),
                              project_root=os.getcwd())
    assert not j.get("m485_reject_reason"), j


# ---------------------------------------------- 4) print 还在 ⇒ 拒
def test_print_still_present_rejected(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_ISSUE_MATCH_GATE", True, raising=False)
    orig = ("def f():\n    print('调试信息', x)\n")
    new = ("def f():\n    print('调试信息', x)  # 已修复\n")
    j = patch_risk_gate.judge(_rec(orig, new, "print_instead_of_log"),
                              project_root=os.getcwd())
    r = j.get("m485_reject_reason") or ""
    assert "print_still_present" in r, (r, j)


# ---------------------------------------------- 5) periodic 无重入守卫 ⇒ 拒
def test_periodic_no_reentry_guard_rejected(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_ISSUE_MATCH_GATE", True, raising=False)
    orig = ("def tick():\n    run_job()\n")
    new = ("def tick():\n    log_status()\n    run_job()\n")
    j = patch_risk_gate.judge(_rec(orig, new, "periodic_task_no_reentry"),
                              project_root=os.getcwd())
    r = j.get("m485_reject_reason") or ""
    assert "no_reentry_guard" in r, (r, j)


# ---------------------------------------------- 6) periodic 有守卫 ⇒ 放行
def test_periodic_with_reentry_guard_ok(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_ISSUE_MATCH_GATE", True, raising=False)
    orig = ("def tick():\n    run_job()\n")
    new = ("def tick():\n"
           "    if self._job_in_progress:\n        return\n"
           "    self._job_in_progress = True\n    run_job()\n")
    j = patch_risk_gate.judge(_rec(orig, new, "periodic_task_no_reentry"),
                              project_root=os.getcwd())
    assert not j.get("m485_reject_reason"), j


# ---------------------------------------------- 7) 未登记 issue_type ⇒ 不装全覆盖放行
def test_unknown_issue_type_not_blocked(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_ISSUE_MATCH_GATE", True, raising=False)
    j = patch_risk_gate.judge(
        _rec("a = 1\n", "a = 2\n", "some_future_type"), project_root=os.getcwd())
    assert not j.get("m485_reject_reason"), j


# ---------------------------------------------- 8) 开关关 ⇒ 无匹配判定
def test_switch_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M485_ISSUE_MATCH_GATE", False, raising=False)
    j = patch_risk_gate.judge(
        _rec("try:\n    do()\nexcept Exception:\n    pass\n",
             "try:\n    do()\nexcept Exception:\n    continue\n",
             "silent_exception"), project_root=os.getcwd())
    assert not j.get("m485_reject_reason"), j
