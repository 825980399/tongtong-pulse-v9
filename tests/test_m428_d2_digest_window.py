# -*- coding: utf-8 -*-
"""428 批刀2 单测：digest 窗口判据收紧到 04:00±5min。

钉语义：
  · 03:55:00 之前**不跑**；03:55:00–04:05:00 跑；04:05:01 之后记为 missed_window；
  · 收紧只作用于 digest —— 内存释放闸门的夜间窗（release_policy）仍是 ±600s。
"""
from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

no = importlib.import_module("nucleus.evolution.night_orchestration")


@pytest.fixture
def iso_root(tmp_path, monkeypatch):
    """隔离根 + 桩住 digest 产物（避免拉起期望账本等重链路）。"""
    (tmp_path / "data" / "reports").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(no, "generate_night_digest",
                        lambda *a, **k: "STUB_DIGEST", raising=True)
    return tmp_path


def _run(root, hhmmss, tag):
    _now = datetime.strptime("2026-11-02 " + hhmmss,  # noqa: DTZ007 —— 被测函数吃 naive 本地墙钟
                             "%Y-%m-%d %H:%M:%S")
    return no._m348_impl_maybe_run_night_digest(
        str(root), now=_now,
        state_path=str(root / f"st_{tag}.json"), enabled=True)


def test_01_before_0355_does_not_run(iso_root):
    for _t in ("03:50:00", "03:52:00", "03:54:59"):
        _r = _run(iso_root, _t, _t.replace(":", ""))
        assert _r["reason"] == "out_of_window", (_t, _r)
        assert _r["ran"] is False


def test_02_window_edges_run_and_after_0405_is_missed(iso_root):
    for _t in ("03:55:00", "04:00:00", "04:05:00"):
        _r = _run(iso_root, _t, _t.replace(":", ""))
        assert _r["reason"] == "ok" and _r["ran"] is True, (_t, _r)
    _r = _run(iso_root, "04:05:01", "040501")
    assert _r["reason"] == "missed_window" and _r["ran"] is False


def test_03_release_policy_night_window_stays_600():
    """收紧 digest 不能连带改内存释放闸门的夜间静默窗（原口径 600s）。"""
    from nucleus.mnemosyne import release_policy as rp
    assert no.NIGHT_DIGEST_TOLERANCE_SEC == 300.0
    assert no.NIGHT_WINDOW_TOLERANCE_SEC == 600.0
    assert rp.night_window_sec() == (4, 0, 600.0)
