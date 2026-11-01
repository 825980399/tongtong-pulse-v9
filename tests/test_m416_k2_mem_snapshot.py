# -*- coding: utf-8 -*-
"""第416批刀2 单测：内存快照/对比工具（mock 大小）。

覆盖：单文件/目录尺寸、缺失不填 0、差分拆「已钉对象 vs 未归因」、
rss 缺失时如实 unavailable、partial 态、watch 采样节拍。
"""
import json

import pytest

import tools.m416_mem_snapshot as MS


def _mk(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


@pytest.fixture()
def fake_root(tmp_path):
    _mk(tmp_path / "big.bin", 2 * 1024 * 1024)          # 2 MiB
    _mk(tmp_path / "sub" / "small.bin", 512 * 1024)     # 0.5 MiB
    return tmp_path


def test_file_size(fake_root):
    r = MS._size_mb(str(fake_root / "big.bin"))
    assert r["state"] == "ok"
    assert abs(r["mb"] - 2.0) < 0.01
    assert r["files"] == 1


def test_dir_size(fake_root):
    r = MS._size_mb(str(fake_root / "sub"))
    assert r["state"] == "ok"
    assert abs(r["mb"] - 0.5) < 0.01


def test_missing_is_null_not_zero(tmp_path):
    r = MS._size_mb(str(tmp_path / "nope.json"))
    assert r["state"] == "missing"
    assert r["mb"] is None, "缺失必须为 None，不能填 0（铁律 276）"


def test_collect_with_injected_paths(fake_root):
    _paths = [("big", "big.bin"), ("gone", "gone.bin"), ("sub", "sub")]
    s = MS.collect(root=str(fake_root), paths=_paths,
                   health={"state": "ok"}, now=1000.0)
    assert s["at"] == 1000.0
    assert abs(s["objects"]["big"]["mb"] - 2.0) < 0.01
    assert s["objects"]["gone"]["state"] == "missing"
    assert abs(s["objects"]["sub"]["mb"] - 0.5) < 0.01


def test_diff_basic():
    a = {"at": 100.0, "rss_mb": 1000.0,
         "objects": {"x": {"mb": 100.0, "state": "ok"},
                     "y": {"mb": 50.0, "state": "ok"}}}
    b = {"at": 700.0, "rss_mb": 1400.0,
         "objects": {"x": {"mb": 400.0, "state": "ok"},
                     "y": {"mb": 50.0, "state": "ok"}}}
    d = MS.diff(a, b)
    assert d["state"] == "ok"
    assert d["seconds"] == 600.0
    assert d["rss_delta_mb"] == 400.0
    assert d["measured_delta_mb"] == 300.0
    assert d["unattributed_mb"] == 100.0
    assert d["items"][0]["name"] == "x"
    assert d["items"][0]["delta_mb"] == 300.0
    assert "未归因" in d["verdict"]


def test_diff_rss_missing_is_unavailable():
    d = MS.diff({"at": 1, "rss_mb": None, "objects": {}},
                {"at": 2, "rss_mb": 10.0, "objects": {}})
    assert d["state"] == "unavailable"
    assert d["rss_delta_mb"] is None
    assert d["unattributed_mb"] is None


def test_diff_partial_when_object_missing():
    a = {"at": 1, "rss_mb": 100.0,
         "objects": {"x": {"mb": 10.0}, "z": {"mb": 5.0}}}
    b = {"at": 2, "rss_mb": 200.0,
         "objects": {"x": {"mb": 30.0}, "z": {"mb": None}}}
    d = MS.diff(a, b)
    assert d["state"] == "partial"
    assert d["missing_objects"] == ["z"]
    assert d["measured_delta_mb"] == 20.0
    assert d["unattributed_mb"] == 80.0


def test_diff_shrinking_is_negative():
    a = {"at": 1, "rss_mb": 900.0, "objects": {"x": {"mb": 800.0}}}
    b = {"at": 2, "rss_mb": 300.0, "objects": {"x": {"mb": 200.0}}}
    d = MS.diff(a, b)
    assert d["rss_delta_mb"] == -600.0
    assert d["measured_delta_mb"] == -600.0
    assert d["unattributed_mb"] == 0.0


def test_watch_sample_count_and_interval(fake_root, monkeypatch):
    monkeypatch.setattr(MS, "_proc_rss",
                        lambda pid=None: {"state": "ok", "rss_mb": 1234.0,
                                          "system_percent": 10.0,
                                          "pid": 4321})
    _sleeps = []
    w = MS.watch(minutes=1.0, interval=30.0, root=str(fake_root),
                 paths=[("big", "big.bin")], sleep=lambda s: _sleeps.append(s))
    assert w["n"] == 3            # 0s / 30s / 60s
    assert len(_sleeps) == 2      # 最后一次不再睡
    assert all(s == 30.0 for s in _sleeps)
    assert w["diff"]["rss_delta_mb"] == 0.0


def test_health_missing_is_labelled(fake_root):
    h = MS._read_health(str(fake_root))
    assert h["state"] == "missing"


def test_cli_take_and_diff(fake_root, tmp_path, capsys):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"at": 1, "rss_mb": 10.0,
                             "objects": {"x": {"mb": 1.0}}}), encoding="utf-8")
    b.write_text(json.dumps({"at": 2, "rss_mb": 20.0,
                             "objects": {"x": {"mb": 4.0}}}), encoding="utf-8")
    rc = MS._main(["--diff", str(a), str(b)])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"rss_delta_mb": 10.0' in out
    assert '"unattributed_mb": 7.0' in out


def test_collect_records_target_pid(monkeypatch):
    """★416 实测教训回归：采样器默认只测自己 ⇒ RSS 恒几十 MB，看着像"没涨"（假绿）。

    这里钉住：`collect(pid=...)` 必须把目标 pid 透到 `_proc_rss` 并落到快照里。
    """
    _seen = {}

    def _fake(pid=None):
        _seen["pid"] = pid
        return {"state": "ok", "rss_mb": 7000.0, "system_percent": 30.0,
                "pid": 999}

    monkeypatch.setattr(MS, "_proc_rss", _fake)
    s = MS.collect(paths=[], health={"state": "ok"}, pid="auto")
    assert _seen["pid"] == "auto"
    assert s["target_pid"] == 999
    assert s["rss_mb"] == 7000.0


def test_find_engine_pid_returns_none_or_int():
    pid = MS._find_engine_pid()
    assert pid is None or isinstance(pid, int)
