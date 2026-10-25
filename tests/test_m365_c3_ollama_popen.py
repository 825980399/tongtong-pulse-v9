# -*- coding: utf-8 -*-
"""365 刀C3 验收：Ollama 启动参数误用修正（`Popen(timeout=)` → `proc.wait(timeout=)`）。

覆盖：Popen 不再收到 timeout 形参／wait 超时不判死（转 check_cmd）／命令不存在降级 stopped 留痕／
启动失败带异常类型留痕。
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from organs.core.PulseSystemManager import PulseSystemManager  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeProc:
    def __init__(self, exc=None):
        self.pid = 4321
        self._exc = exc

    def wait(self, timeout=None):
        if self._exc is not None:
            raise self._exc
        return 0


def _make_mgr(service, checks):
    _m = PulseSystemManager.__new__(PulseSystemManager)
    _m._managed_services = {"ollama": dict(service, name="ollama")}
    _m._repair_count = 0
    _m._log = lambda *a, **k: None
    _seq = iter(checks)

    def _run(cmd, *a, **k):
        # `next(iter, default)`：不用 try/except（纯 return 错误字典会被 cw2 判静默）
        return next(_seq, {"success": False})

    _m._run_command = _run
    return _m


class TestC3OllamaPopen(unittest.TestCase):
    SVC = {"start_cmd": ["ollama", "serve"], "check_cmd": ["ollama", "list"],
           "auto_start": True, "status": "stopped"}

    def test_01_popen_gets_no_timeout_kwarg(self):
        """Popen 不接受 timeout；传了必抛 TypeError（旧写法每跑必失败）。"""
        _m = _make_mgr(self.SVC, [{"success": False}, {"success": True}])
        with mock.patch("subprocess.Popen", return_value=_FakeProc()) as _p, \
             mock.patch("time.sleep", lambda *_: None):
            _m._check_and_repair_services()
        _kw = _p.call_args.kwargs
        self.assertNotIn("timeout", _kw, "Popen 不该收到 timeout 形参")
        self.assertEqual(_kw.get("creationflags", 0) >= 0, True)

    def test_02_wait_timeout_not_fatal(self):
        """常驻服务 wait 超时属预期 ⇒ 不判死，继续走 check_cmd 复核。"""
        _m = _make_mgr(self.SVC, [{"success": False}, {"success": True}])
        _proc = _FakeProc(exc=subprocess.TimeoutExpired(["ollama"], 30))
        with mock.patch("subprocess.Popen", return_value=_proc), \
             mock.patch("time.sleep", lambda *_: None):
            r = _m._check_and_repair_services()
        self.assertEqual(r.get("ollama"), "running")

    def test_03_missing_command_degrades_stopped(self):
        """命令不存在 ⇒ 降级 stopped（既有口径保留，不中断启动流程）。"""
        _m = _make_mgr(self.SVC, [{"success": False}])
        with mock.patch("subprocess.Popen", side_effect=FileNotFoundError("no ollama")), \
             mock.patch("time.sleep", lambda *_: None):
            r = _m._check_and_repair_services()
        self.assertEqual(r.get("ollama"), "stopped")

    def test_04_source_has_no_buggy_form(self):
        with open(os.path.join(_ROOT, "organs", "core", "PulseSystemManager.py"),
                  encoding="utf-8") as f:
            _s = f.read()
        self.assertNotIn('timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_medium"], )', _s)
        self.assertIn("_proc365.wait(timeout=", _s)
        # 启动失败留痕带异常类型（铁律10）
        self.assertIn("type(_e).__name__", _s)


if __name__ == "__main__":
    unittest.main()
