# -*- coding: utf-8 -*-
"""第162批刀7 · shutdown_request.json 优雅退出通道单测（night_orchestration）。"""

import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.evolution import night_orchestration as no


@pytest.fixture
def tmp_root(tmp_path):
    _d = tmp_path / "proj"
    (_d / "data").mkdir(parents=True, exist_ok=True)
    return str(_d)


def test_request_has_consume(tmp_root):
    _c = no.NightShutdownChannel(tmp_root)
    assert not _c.has_shutdown_request()
    assert _c.request_shutdown(reason="t")
    assert _c.has_shutdown_request()
    _req = _c.consume_shutdown_request()
    assert _req is not None
    assert _req.get("consumed") is True
    assert _req.get("reason") == "t"
    assert not _c.has_shutdown_request()


def test_consume_nonexistent(tmp_root):
    _c = no.NightShutdownChannel(tmp_root)
    assert _c.consume_shutdown_request() is None


def test_clear_stale(tmp_root):
    _c = no.NightShutdownChannel(tmp_root)
    _c.request_shutdown()
    assert os.path.exists(os.path.join(tmp_root, "data", "shutdown_request.json"))
    _c.clear_stale_request()
    assert not os.path.exists(os.path.join(tmp_root, "data", "shutdown_request.json"))


def test_cmdline_is_framework_main():
    assert no._cmdline_is_framework_main(["python", "x/main.py"]) is True
    assert no._cmdline_is_framework_main(["python", "-c", "open('main.py')"]) is False
    assert no._cmdline_is_framework_main(["python", "other.py"]) is False
    assert no._cmdline_is_framework_main(None) is False


class _FakeProc:
    def __init__(self, pid, cmdline):
        self.info = {"pid": pid, "cmdline": cmdline}


def _fake_psutil(procs):
    _mod = types.ModuleType("psutil")
    _mod.process_iter = lambda attrs=None: iter(procs)
    _mod.pid_exists = lambda pid: any(_p.info.get("pid") == pid for _p in procs)
    return _mod


def test_is_framework_running_true(monkeypatch):
    _self = os.getpid()
    _procs = [_FakeProc(999, ["python", "x/main.py"]), _FakeProc(_self, ["python", "pytest"])]
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(_procs))
    assert no.is_framework_running() is True


def test_is_framework_running_false(monkeypatch):
    _self = os.getpid()
    _procs = [_FakeProc(_self, ["python", "pytest"])]
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(_procs))
    assert no.is_framework_running() is False


def test_is_framework_running_psutil_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert no.is_framework_running() is None
