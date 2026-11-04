# -*- coding: utf-8 -*-
"""447 刀2：apply 落地留痕（前/后全文）+ 进程级 apply 锁。

判据来自任务书 §刀2：不改既有备份/回滚/landed 机制，只加留痕与并发锁。
"""
import json
import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.reasoning.PatchManager import PatchManager as PM

_BEFORE = "def target(self, x):\n    return x\n"
_AFTER = "def target(self, x):\n    if x is None:\n        return 0\n    return x\n"


def _trace(tmp_path):
    _p = tmp_path / "apply_trace.jsonl"
    if not os.path.exists(str(_p)):
        return []
    with open(str(_p), encoding="utf-8") as _f:
        return [json.loads(x) for x in _f if x.strip()]


def _wire(tmp_path, monkeypatch, on=True):
    monkeypatch.setattr(config, "M447_APPLY_TRACE_PATH",
                        str(tmp_path / "apply_trace.jsonl"), raising=True)
    monkeypatch.setattr(config, "ENABLE_M447_APPLY_TRACE", on, raising=True)
    monkeypatch.setattr(config, "M447_APPLY_TRACE_MAX_CHARS", 0, raising=True)


def test_note_apply_writes_full_content(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _patch = {"id": "patch_m447_a1", "file": "organs/body/PulseHeart.py",
              "method": "target", "issue_type": "silent_exception",
              "backup_path": "data/code_backups/bk1", "rollback_available": True,
              "line": 444}
    assert PM._m447_note_apply(_patch, _BEFORE, _AFTER, line=444) is True
    _r = _trace(tmp_path)[0]
    assert _r["patch_id"] == "patch_m447_a1", _r
    assert _r["before_content"] == _BEFORE, "修改前必须完整"
    assert _r["after_content"] == _AFTER, "修改后必须完整"
    assert _r["truncated"] is False, _r
    assert len(_r["before_sha256"]) == 64 and len(_r["after_sha256"]) == 64, _r
    assert _r["before_sha256"] != _r["after_sha256"], "前/后内容不同，指纹必须不同"
    assert _r["backup_path"] == "data/code_backups/bk1", _r
    assert _r["rollback_path"] == _r["backup_path"], "回滚路径默认就是备份路径"
    assert _r["line"] == 444, _r


def test_note_apply_switch_off(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, on=False)
    assert PM._m447_note_apply({"id": "x"}, _BEFORE, _AFTER) is False
    assert _trace(tmp_path) == []


def test_note_apply_truncation_is_explicit(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "M447_APPLY_TRACE_MAX_CHARS", 20, raising=True)
    PM._m447_note_apply({"id": "x"}, _BEFORE, _AFTER)
    _r = _trace(tmp_path)[0]
    assert _r["truncated"] is True, "截断必须显式标记，不能静默丢"
    assert _r["before_chars"] == len(_BEFORE), "原长度仍如实记"
    assert len(_r["before_content"]) == 20, _r


def test_note_apply_never_raises_on_bad_patch():
    assert PM._m447_note_apply(None, None, None) in (True, False)
    assert PM._m447_note_apply({}, "", "") in (True, False)


class _SlowPM:
    """假 PM：apply 慢 0.35s，用来制造并发窗口。"""

    def __init__(self):
        self.calls = 0

    def apply_all_pending(self, only_approved=False):
        self.calls += 1
        time.sleep(0.35)
        return {"applied": 1, "failed": 0, "details": [],
                "only_approved": only_approved}


def test_apply_approved_once_passes_only_approved():
    _pm = _SlowPM()
    _r = PM.apply_approved_once(_pm, reason="unit")
    assert _pm.calls == 1, _pm.calls
    assert _r["applied"] == 1 and _r["apply_reason"] == "unit", _r


def test_apply_lock_blocks_concurrent_entry():
    """★并发保护：同一进程两路同时进 ⇒ 第二路直接跳过（不排队、不覆盖）。"""
    _pm = _SlowPM()
    _out = {}

    def _run():
        _out["r"] = PM.apply_approved_once(_pm, reason="unit-2")

    _t = threading.Thread(target=_run)
    _t.start()
    time.sleep(0.08)                       # 让第一路先拿到锁
    _second = PM.apply_approved_once(_pm, reason="unit-3")
    _t.join()
    assert _second.get("skipped_locked") is True, _second
    assert _second["applied"] == 0 and _second["details"] == [], _second
    assert _pm.calls == 1, "被锁挡下的那次绝不能真的 apply"
    assert _out["r"]["applied"] == 1, _out


def test_apply_lock_released_after_entry():
    _pm = _SlowPM()
    PM.apply_approved_once(_pm, reason="a")
    _r = PM.apply_approved_once(_pm, reason="b")
    assert _r.get("skipped_locked") is None, "上一轮结束后锁必须已释放"
    assert _pm.calls == 2, _pm.calls


def test_main_uses_locked_entry():
    """源码级断言：三个触发口统一走带锁入口，别再裸调 apply_all_pending。"""
    _p = os.path.join(_ROOT, "main.py")
    with open(_p, encoding="utf-8") as _f:
        _src = _f.read()
    assert "apply_approved_once(reason=" in _src
    assert "_patch_mgr.apply_all_pending(only_approved=True)" not in _src, \
        "主路径仍在裸调 apply_all_pending（并发锁被绕过）"
