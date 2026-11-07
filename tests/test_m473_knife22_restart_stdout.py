# -*- coding: utf-8 -*-
"""473刀22 门控单测：自重启接班进程的 stdout 交接。

现场（2026-11-07 15:34:55，端到端闭环验证的最后一环）：
  补丁 715f 落地成功（applied=True、效果验证通过、源码已改、落地=1 失败=0），
  15:34:50 拉起接班进程 pid=35044 —— 但 15:34:55 它在
  `framework.start()` 第一行 `print()` 抛
  `OSError: [WinError 233] 管道的另一端上无任何进程`，进程崩溃退出。
  ⇒ 闭环跑通了，系统却没活下来（与 472 批「系统自我拆除」同型）。

根因：`_spawn_self_restart` 的 `Popen` 不给 stdin/stdout/stderr ⇒ 即使
`CREATE_NEW_CONSOLE` 新建了控制台，子进程仍**继承父进程的 stdout 管道**。
父进程一退出，管道另一端消失，任何 print 都炸。

修法两处：
  ① spawn 侧显式 stdin=DEVNULL + stdout/stderr=文件句柄 + 接班标记；
  ② main.py 模块级把接班进程输出重绑回它自己的新控制台（失败不致命）。
"""
import ast
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as M

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as _fh:
    _SRC = _fh.read()
_TREE = ast.parse(_SRC)


def _fn(name):
    for n in ast.walk(_TREE):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


_SPAWN = _fn("_spawn_self_restart")
_REBIND = _fn("_m473_rebind_restart_stdout")


def _src_of(node):
    lines = _SRC.split("\n")
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


class TestSpawnStdHandles(unittest.TestCase):
    """mock 掉 Popen，直接检查实际传进去的参数（比源码断言更有判别力）。"""

    def _call(self):
        fake = mock.MagicMock()
        fake.pid = 99991
        with mock.patch("subprocess.Popen", return_value=fake) as popen, \
             mock.patch.object(M, "_write_restart_notice", return_value=True), \
             mock.patch.object(M, "_logger"):
            ok = M._spawn_self_restart(reason="unit_test")
        return ok, popen, fake

    def test_01_returns_true(self):
        ok, _, _ = self._call()
        if sys.platform != "win32":
            self.skipTest("WinError 233 是 Windows 特有")
        self.assertTrue(ok)

    @unittest.skipUnless(sys.platform == "win32", "仅 Windows 分支需要显式句柄")
    def test_02_stdin_is_devnull(self):
        import subprocess as _sp
        _, popen, _ = self._call()
        self.assertIs(_sp.DEVNULL, popen.call_args.kwargs.get("stdin"))

    @unittest.skipUnless(sys.platform == "win32", "仅 Windows 分支需要显式句柄")
    def test_03_stdout_stderr_not_inherited(self):
        """None/缺省 = 继承父进程管道 = 本次事故正主。"""
        _, popen, _ = self._call()
        kw = popen.call_args.kwargs
        self.assertIn("stdout", kw)
        self.assertIn("stderr", kw)
        self.assertIsNotNone(kw["stdout"])
        self.assertIsNotNone(kw["stderr"])

    @unittest.skipUnless(sys.platform == "win32", "仅 Windows 分支打标记")
    def test_04_child_marker_in_env(self):
        _, popen, _ = self._call()
        env = popen.call_args.kwargs.get("env") or {}
        self.assertEqual("1", env.get("PULSE_SELF_RESTART_CHILD"))
        self.assertIn("PATH", env, "不能只传标记而丢掉整个环境")

    @unittest.skipUnless(sys.platform == "win32", "仅 Windows")
    def test_05_new_console_and_cwd_preserved(self):
        import subprocess as _sp
        _, popen, _ = self._call()
        kw = popen.call_args.kwargs
        self.assertEqual(_sp.CREATE_NEW_CONSOLE, kw.get("creationflags"))
        self.assertEqual(_ROOT, kw.get("cwd"))

    @unittest.skipUnless(sys.platform == "win32", "仅 Windows")
    def test_06_pid_registered(self):
        _, _, fake = self._call()
        with M._SELF_RESTART_PIDS_LOCK:
            self.assertIn(fake.pid, M._SELF_RESTART_CHILD_PIDS)
            M._SELF_RESTART_CHILD_PIDS.discard(fake.pid)


class TestSourceShape(unittest.TestCase):
    def test_07_windows_popen_pins_all_three_handles(self):
        """防回退：Win 分支必须同时给 stdin/stdout/stderr（事故正主是"一个都不给"）。"""
        s = _src_of(_SPAWN)
        win = s.split("if _sys.platform == 'win32':", 1)[1].split("else:", 1)[0]
        for key in ("stdin=subprocess.DEVNULL", "stdout=_out473",
                    "stderr=_out473", "env=_env473"):
            self.assertIn(key, win, f"Win 分支缺 {key}")

    def test_08_both_marker_occurrences(self):
        self.assertEqual(2, _SRC.count("PULSE_SELF_RESTART_CHILD"))

    def test_09_spawn_has_silent_exc(self):
        self.assertIn("silent_exc", _src_of(_SPAWN))


class TestRebind(unittest.TestCase):
    def test_10_normal_start_is_untouched(self):
        """没标记 ⇒ 一行不改（正常手动启动路径）。"""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PULSE_SELF_RESTART_CHILD", None)
            before = sys.stdout
            M._m473_rebind_restart_stdout()
            self.assertIs(before, sys.stdout)

    @unittest.skipUnless(sys.platform == "win32", "CONOUT$ 仅 Windows")
    def test_11_child_rebinds_when_conout_available(self):
        con = mock.MagicMock()
        with mock.patch.dict(os.environ, {"PULSE_SELF_RESTART_CHILD": "1"}), \
             mock.patch("builtins.open", return_value=con) as op, \
             mock.patch.object(M, "silent_exc") as se:
            old_out, old_err = sys.stdout, sys.stderr
            try:
                M._m473_rebind_restart_stdout()
                self.assertIs(con, sys.stdout)
                self.assertIs(con, sys.stderr)
            finally:
                sys.stdout, sys.stderr = old_out, old_err
            op.assert_called_once()
            se.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "CONOUT$ 仅 Windows")
    def test_12_rebind_failure_is_silent_not_fatal(self):
        """★打印绝不能拖死框架：CONOUT$ 不可用也要安静继续。"""
        with mock.patch.dict(os.environ, {"PULSE_SELF_RESTART_CHILD": "1"}), \
             mock.patch("builtins.open", side_effect=OSError("no console")), \
             mock.patch.object(M, "silent_exc") as se:
            old_out = sys.stdout
            try:
                M._m473_rebind_restart_stdout()
                self.assertIs(old_out, sys.stdout)
            finally:
                sys.stdout = old_out
            self.assertTrue(se.called)

    def test_13_rebind_called_at_module_level(self):
        self.assertIn("\n_m473_rebind_restart_stdout()", _SRC)

    def test_14_every_except_in_rebind_has_silent_exc(self):
        s = _src_of(_REBIND)
        self.assertIn("silent_exc", s)
        self.assertNotIn("pass", s)


class TestNoRegression(unittest.TestCase):
    def test_15_knife20_console_command_still_there(self):
        self.assertIn("_m473_console_exit_worker", _SRC)

    def test_16_signal_handler_still_there(self):
        self.assertIn("signal.signal(signal.SIGINT, signal_handler)", _SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
