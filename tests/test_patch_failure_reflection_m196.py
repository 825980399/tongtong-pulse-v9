# -*- coding: utf-8 -*-
"""196刀2 运行时失败路→反思：失败信号驱动反思断言（隔离，铁律151）。"""
import importlib

import nucleus.reasoning.PatchManager as _pm

_sre = importlib.import_module("nucleus.evolution.SelfReflectionEngine")


class _FakeEngine:
    def __init__(self, sink):
        self._sink = sink

    def reflect_and_improve(self, failed_patch, verification_detail=None, **_kw):
        self._sink.append({
            "failed_patch": failed_patch,
            "verification_detail": verification_detail,
        })


def test_switch_off_no_reflection(monkeypatch):
    _calls = []
    import config
    monkeypatch.setattr(config, "ENABLE_PATCH_FAILURE_REFLECTION", False, raising=False)
    monkeypatch.setattr(_sre, "get_self_reflection_engine",
                        lambda project_root: _FakeEngine(_calls))
    _pm._reflect_on_failed_patches(
        [{"status": "rejected_verify", "reason": "r", "id": "p1", "file": "f.py"}],
        project_root="",
    )
    assert _calls == [], "开关关闭时不应触发反思"


def test_switch_on_reflects_failed_only(monkeypatch):
    _calls = []
    import config
    monkeypatch.setattr(config, "ENABLE_PATCH_FAILURE_REFLECTION", True, raising=False)
    monkeypatch.setattr(_sre, "get_self_reflection_engine",
                        lambda project_root: _FakeEngine(_calls))
    _pm._reflect_on_failed_patches([
        {"status": "rejected_verify", "reason": "rv", "id": "p1", "file": "f.py"},
        {"status": "rejected_core_guard", "reason": "rc", "id": "p2", "file": "g.py"},
        {"status": "applied", "reason": "ok", "id": "p3", "file": "h.py"},
    ], project_root="")
    assert len(_calls) == 2, "仅 rejected_* 应触发反思"
    assert _calls[0]["failed_patch"]["id"] == "p1"
    assert _calls[1]["failed_patch"]["id"] == "p2"


def test_switch_on_engine_unavailable_no_crash(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_PATCH_FAILURE_REFLECTION", True, raising=False)

    def _boom(project_root):
        raise RuntimeError("no engine")

    monkeypatch.setattr(_sre, "get_self_reflection_engine", _boom)
    # 引擎不可用时不应抛异常（silent_exc 兜底）
    _pm._reflect_on_failed_patches(
        [{"status": "rejected_verify", "reason": "r", "id": "p1", "file": "f.py"}],
        project_root="",
    )
