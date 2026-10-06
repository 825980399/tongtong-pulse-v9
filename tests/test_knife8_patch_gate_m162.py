# -*- coding: utf-8 -*-
"""第162批刀8 门控单测：编排退出前置补丁闸门。

覆盖：
  - count_approved_patches：正常计数 / fail-open（PatchManager 不可用返回 0 不抛）
  - orchestration_exit_blocked_by_patches：开关关闭/无补丁/有补丁三种判定
  - write_exit_blocked_report：留痕文件内容正确 + 目录缺失时 fail-safe 不抛
"""
import json
import os

from nucleus.evolution import night_orchestration as no_mod


class _FakePM:
    def __init__(self, patches):
        self._patches = patches

    def list_pending_patches(self):
        return self._patches


def _install_fake_pm(monkeypatch, patches):
    import nucleus.reasoning.PatchManager as pm_mod
    monkeypatch.setattr(
        pm_mod, "PatchManager", lambda root: _FakePM(patches), raising=True
    )


def test_count_approved_zero(monkeypatch):
    _install_fake_pm(monkeypatch, [])
    assert no_mod.count_approved_patches("/tmp/fake_root") == 0


def test_count_approved_some(monkeypatch):
    _patches = [
        {"status": "approved"},
        {"status": "pending"},
        {"status": "approved"},
        {"status": "verified"},
    ]
    _install_fake_pm(monkeypatch, _patches)
    assert no_mod.count_approved_patches("/tmp/fake_root") == 2


def test_count_fail_open(monkeypatch):
    import nucleus.reasoning.PatchManager as pm_mod

    class _Boom:
        def __init__(self, root):
            raise RuntimeError("simulated PM failure")

    monkeypatch.setattr(pm_mod, "PatchManager", _Boom, raising=True)
    # 异常 → fail-open 返回 0，绝不抛出
    assert no_mod.count_approved_patches("/tmp/fake_root") == 0


def test_orchestration_blocked_disabled(monkeypatch):
    _install_fake_pm(monkeypatch, [{"status": "approved"}, {"status": "approved"}])
    assert (
        no_mod.orchestration_exit_blocked_by_patches("/tmp/fake_root", enabled=False)
        is False
    )


def test_orchestration_blocked_enabled_zero(monkeypatch):
    _install_fake_pm(monkeypatch, [{"status": "pending"}])
    assert (
        no_mod.orchestration_exit_blocked_by_patches("/tmp/fake_root", enabled=True)
        is False
    )


def test_orchestration_blocked_enabled_some(monkeypatch):
    _install_fake_pm(monkeypatch, [{"status": "approved"}, {"status": "pending"}])
    assert (
        no_mod.orchestration_exit_blocked_by_patches("/tmp/fake_root", enabled=True)
        is True
    )


def test_write_exit_blocked_report(tmp_path):
    _root = str(tmp_path)
    no_mod.write_exit_blocked_report(3, _root)
    _p = os.path.join(_root, "data", "night_orch_exit_blocked.json")
    assert os.path.exists(_p), "blocked report not written"
    _d = json.load(open(_p, encoding="utf-8"))
    assert _d["approved_count"] == 3
    assert _d["reason"] == "EXIT_BLOCKED_BY_PATCH"
    assert "blocked_at" in _d


def test_write_exit_blocked_report_dir_missing(tmp_path):
    # data 子目录不存在时应 fail-safe（silent_exc），不抛异常
    _root = os.path.join(str(tmp_path), "nox")
    no_mod.write_exit_blocked_report(1, _root)
