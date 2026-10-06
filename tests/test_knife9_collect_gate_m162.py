# -*- coding: utf-8 -*-
"""第162批刀9 门控单测：collect 两级化 + 超时掩码修复。

覆盖 check_red_baseline_gate.slice_collect / _collect_full / compute_affected_test_files：
  - 超时掩码修复：full 与 incremental 超时均返回 FAIL 且日志含「超时」标记；
  - 全量门行为不变：成功/漂移/收集错误三态与改造前一致；
  - 增量门：无受影响测试直接 PASS（不跑收集）；收集错误返回 FAIL；
  - 依赖图解析失败（compute 返回 None）→ fail-safe 升级全量。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.ci.check_red_baseline_gate as gate_mod

BASE = {"collect_baseline": 4785, "known_fail": set(), "pollution_set": set(),
        "env_fingerprint": {}, "raw": {}}


def _base():
    return dict(BASE)


def test_full_timeout_returns_fail(monkeypatch, capsys):
    monkeypatch.setattr(gate_mod, "_run_once",
                        lambda args, timeout=300: ("", "timeout"))
    rc = gate_mod.slice_collect(_base(), level="full")
    assert rc == 1
    assert "超时" in capsys.readouterr().err


def test_incremental_timeout_returns_fail(monkeypatch, capsys):
    monkeypatch.setattr(gate_mod, "_run_once",
                        lambda args, timeout=300: ("", "timeout"))
    rc = gate_mod.slice_collect(_base(), level="incremental",
                                changed_files=["nucleus/foo.py"])
    assert rc == 1
    assert "超时" in capsys.readouterr().err


def test_full_success_pass(monkeypatch):
    monkeypatch.setattr(gate_mod, "_run_once",
                        lambda args, timeout=300: ("collected 4785 items\n", None))
    assert gate_mod.slice_collect(_base(), level="full") == 0


def test_full_drift_fail(monkeypatch):
    monkeypatch.setattr(gate_mod, "_run_once",
                        lambda args, timeout=300: ("collected 9999 items\n", None))
    assert gate_mod.slice_collect(_base(), level="full") == 1


def test_full_collect_error_fail(monkeypatch):
    monkeypatch.setattr(gate_mod, "_run_once",
                        lambda args, timeout=300: ("ERROR collecting x.py\n", None))
    assert gate_mod.slice_collect(_base(), level="full") == 1


def test_compute_affected_finds_importers():
    aff = gate_mod.compute_affected_test_files(
        changed_files=["nucleus/evolution/night_orchestration.py"])
    assert isinstance(aff, list)
    # 刀7/刀8 测试均 import night_orchestration → 应被反向依赖图命中
    assert "tests/test_knife7_night_orch_m162.py" in aff
    assert "tests/test_knife8_patch_gate_m162.py" in aff


def test_compute_affected_no_py_returns_empty():
    assert gate_mod.compute_affected_test_files(
        changed_files=["docs/foo.md", "README.md"]) == []


def test_incremental_no_affected_skips_collect(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(gate_mod, "_run_once",
                        lambda args, timeout=300: calls.append(args) or ("", None))
    rc = gate_mod.slice_collect(_base(), level="incremental",
                                changed_files=["docs/foo.md"])
    assert rc == 0
    assert calls == []  # 无受影响测试 → 不触发任何收集
    assert "无受影响测试文件" in capsys.readouterr().err


def test_incremental_collect_error_fail(monkeypatch):
    def fake(args, timeout=300):
        return ("ERROR collecting tests/test_knife7_night_orch_m162.py\n", None)
    monkeypatch.setattr(gate_mod, "_run_once", fake)
    rc = gate_mod.slice_collect(
        _base(), level="incremental",
        changed_files=["nucleus/evolution/night_orchestration.py"])
    assert rc == 1


def test_incremental_fail_safe_upgrade(monkeypatch, capsys):
    # 依赖图无法解析（compute 返回 None）→ 升级全量
    monkeypatch.setattr(gate_mod, "compute_affected_test_files",
                        lambda changed_files=None: None)
    calls = []
    monkeypatch.setattr(gate_mod, "_run_once",
                        lambda args, timeout=300: (calls.append(args) or
                                                   ("collected 4785 items\n", None)))
    rc = gate_mod.slice_collect(_base(), level="incremental",
                                changed_files=["nucleus/foo.py"])
    out = capsys.readouterr().err
    assert rc == 0
    assert "升级全量" in out
    # 升级后确实走了全量收集（以 "tests/" 开头）
    assert any(c and c[0] == "tests/" for c in calls)
