# -*- coding: utf-8 -*-
"""482 刀20/21 门控单测：自重启接班进程「CONOUT$ 打得开但写不了」。

现场（2026-11-09 16:34:33）：
  16:33:42 周期 apply 落地补丁 → 16:33:56 优雅关闭 → 16:34:13 spawn 接班进程 pid=12996
  （已写 270 行日志、装配完成、57 器官启动）→ 16:34:33 `framework.start()`
  末尾唯一一次裸 `print()` 抛 `OSError [WinError 233] 管道的另一端上无任何进程`
  且**未捕获** ⇒ 整机退出；日志空窗到 17:59:10 用户手动拉起 pid=17736。

473刀22 只挡了「CONOUT$ 打不开」，没挡「打开成功、写入时句柄失效」；
`tests/test_m473_knife22_restart_stdout.py::test_11` 还把「打开成功 ⇒ 直接挂上」
锁成了契约。本批在**不改旧测试**的前提下补两层：
  ① 重绑前试写验证，坏句柄回退到进程自己新开的独立文件句柄；
  ② 启动期空行打印抽成受保护函数，任何 stdout 异常都不决定进程生死。
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


def _src_of(node):
    lines = _SRC.split("\n")
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def _unprotected_start_prints(src):
    """判据：PulseFramework.start() 内不在 try 里的裸 print（旧代码=1，新代码=0）。

    只认事故所在的那个 start —— 全仓另有多个类的同名方法里有 print，
    把它们算进来会让判据在旧代码上也成立（铁律35：无判别力 = 自欺）。
    """
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "PulseFramework"):
            continue
        for m in node.body:
            if not (isinstance(m, ast.FunctionDef) and m.name == "start"):
                continue
            protected = set()
            for t in ast.walk(m):
                if isinstance(t, ast.Try):
                    for x in ast.walk(t):
                        if (isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                                and x.func.id == "print"):
                            protected.add(id(x))
            for x in ast.walk(m):
                if (isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                        and x.func.id == "print" and id(x) not in protected):
                    out.append(x)
    return out


class TestConoutProbe(unittest.TestCase):
    """★行为级判据：不是看源码长什么样，而是真的让 write 抛 233。"""

    @unittest.skipUnless(sys.platform == "win32", "CONOUT$ 仅 Windows")
    def test_01_conout_write_233_falls_back_to_independent_file(self):
        bad = mock.MagicMock()
        bad.write.side_effect = OSError(233, "管道的另一端上无任何进程")
        good = mock.MagicMock()
        with mock.patch.dict(os.environ, {"PULSE_SELF_RESTART_CHILD": "1"}), \
             mock.patch("builtins.open", side_effect=[bad, good]) as op, \
             mock.patch.object(M, "silent_exc") as se:
            old_out, old_err = sys.stdout, sys.stderr
            try:
                M._m473_rebind_restart_stdout()
                self.assertIs(good, sys.stdout, "坏 CONOUT$ 不得挂到 sys.stdout")
                self.assertIs(good, sys.stderr)
                print()  # 挂上的句柄必须真的可写
            finally:
                sys.stdout, sys.stderr = old_out, old_err
        self.assertEqual(2, op.call_count, "一次 CONOUT$ 探测 + 一次独立文件回退")
        self.assertTrue(se.called, "试写失败必须留痕")

    @unittest.skipUnless(sys.platform == "win32", "CONOUT$ 仅 Windows")
    def test_02_conout_write_ok_keeps_conout(self):
        """正常接班路径：试写通过 ⇒ 仍按 473刀22 契约挂 CONOUT$（既有 test_11 不破）。"""
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
        con.write.assert_called()
        con.flush.assert_called()
        se.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "CONOUT$ 仅 Windows")
    def test_03_conout_open_fails_keeps_original_stdout(self):
        """既有 test_12 契约：连打都打不开 ⇒ 原 stdout 一字不动。"""
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

    def test_04_normal_start_is_untouched(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PULSE_SELF_RESTART_CHILD", None)
            before = sys.stdout
            M._m473_rebind_restart_stdout()
            self.assertIs(before, sys.stdout)


class TestStartupPrintNeverKills(unittest.TestCase):
    def test_05_print_raising_233_is_swallowed(self):
        with mock.patch("builtins.print", side_effect=OSError(233, "pipe gone")), \
             mock.patch.object(M, "silent_exc") as se:
            M._m482_safe_startup_blank_line()  # 不抛 ⇒ 框架继续活
        self.assertTrue(se.called)

    def test_06_bad_stdout_object_is_swallowed(self):
        class _BadOut:
            def write(self, _s):
                raise OSError(233, "管道的另一端上无任何进程")

            def flush(self):
                return None

        with mock.patch.object(sys, "stdout", _BadOut()), \
             mock.patch.object(M, "silent_exc") as se:
            M._m482_safe_startup_blank_line()
        self.assertTrue(se.called)

    def test_07_safe_helper_has_silent_exc(self):
        s = _src_of(_fn("_m482_safe_startup_blank_line"))
        self.assertIn("silent_exc", s, "铁律4：每个 except 都要留痕")

    def test_08_start_has_no_bare_print(self):
        self.assertEqual([], _unprotected_start_prints(_SRC))

    def test_09_judge_detects_old_code(self):
        """铁律35 判据有效性自测：把事故前的旧代码喂同一判据必须报红。"""
        old_code = (
            "class PulseFramework:\n"
            "    def start(self):\n"
            "        self._log(1, '等待交互...')\n"
            "        print()\n"
        )
        self.assertEqual(1, len(_unprotected_start_prints(old_code)))

    def test_10_start_calls_safe_helper(self):
        start = None
        for n in ast.walk(_TREE):
            if isinstance(n, ast.ClassDef) and n.name == "PulseFramework":
                for m in n.body:
                    if isinstance(m, ast.FunctionDef) and m.name == "start":
                        start = m
        self.assertIsNotNone(start)
        self.assertIn("_m482_safe_startup_blank_line()", _src_of(start))

    def test_11_rebind_source_has_probe_and_fallback(self):
        s = _src_of(_fn("_m473_rebind_restart_stdout"))
        self.assertIn("_con473.flush()", s)
        self.assertIn("boot_crash.log", s)
        self.assertNotIn("pass", s, "既有 test_14 契约")

    def test_12_marker_count_unchanged(self):
        """既有 test_08 契约：接班标记全仓恰好 2 处。"""
        self.assertEqual(2, _SRC.count("PULSE_SELF_RESTART_CHILD"))

    def test_13_judge_detects_old_banner(self):
        """反证：事故前 start() 的三行裸横幅，同一判据必须抓到 3 个。"""
        old_code = (
            "class PulseFramework:\n"
            "    def start(self):\n"
            "        print('====')\n"
            "        print('booting')\n"
            "        print('====')\n"
        )
        self.assertEqual(3, len(_unprotected_start_prints(old_code)))

    def test_14_banner_is_inside_try(self):
        """三行横幅现在必须都在 try 里（打印失败只留痕，不冒泡）。"""
        self.assertIn("_e482_banner", _SRC)
        self.assertIn("silent_exc(_e482_banner", _SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
