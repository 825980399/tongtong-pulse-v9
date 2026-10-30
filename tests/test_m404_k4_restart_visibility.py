# -*- coding: utf-8 -*-
"""404 刀4 判据：自动重启窗口可见性（窗口标题 / restart_notice.txt / 优雅停止脚本）。

任务书三条，本文件的对应：
* ①「`CREATE_NEW_CONSOLE` 窗口标题设成 `曈曈 pid=XXXX 启动于YYYY-MM-DD HH:MM:SS`」
  → ``TestConsoleTitle``（格式纯函数 + 真实 `SetConsoleTitleW` 往返）+ ``TestEntryWiring``
* ②「重启前在 `data/runtime.lock` 旁边写 `restart_notice.txt`」
  → ``TestRestartNotice``。★`data/runtime.lock` **盘上不存在、全仓无创建代码**，
    便条改放 `data/`（与真正的控制文件 `data/shutdown_request.json` 同目录）
* ③「`scripts/stop_tongtong.py` 读 `runtime.lock` 拿 pid 发优雅停止信号」
  → ``TestStopScript``。★同上，改走 psutil 判据 + `data/shutdown_request.json` 通道

为什么这些判据要测"失败也不影响重启"：可见性是**增强**，不是关键路径。
写便条失败、设标题失败都只能留痕，绝不能把一次自重启搞崩。
"""
import ast
import io
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import main as _main  # noqa: E402
from scripts import stop_tongtong as ss  # noqa: E402

_MAIN_SRC = os.path.join(_ROOT, "main.py")
_STOP_SRC = os.path.join(_ROOT, "scripts", "stop_tongtong.py")

_TITLE_RE = re.compile(r"^曈曈 pid=\d+ 启动于\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _read(path):
    with io.open(path, encoding="utf-8") as _f:
        return _f.read()


class TestConsoleTitle(unittest.TestCase):
    def test_10_format_matches_spec(self):
        """任务书字面：`曈曈 pid=XXXX 启动于YYYY-MM-DD HH:MM:SS`。"""
        _t = _main._console_title_text(4321, 1793354350.0)
        self.assertRegex(_t, _TITLE_RE)
        self.assertIn("pid=4321", _t)

    def test_11_local_time_not_utc(self):
        _ts = 1793354350.0
        _t = _main._console_title_text(1, _ts)
        _want = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_ts))
        self.assertIn(_want, _t, "必须用本地时间（和日志/看板同一时区）")

    def test_12_defaults_to_self_pid_and_now(self):
        _t = _main._console_title_text()
        self.assertIn("pid=%d" % os.getpid(), _t)
        self.assertRegex(_t, _TITLE_RE)

    def test_13_prefix_constant(self):
        self.assertEqual(_main._CONSOLE_TITLE_PREFIX, "曈曈")

    def test_14_negative_and_zero_pid_safe(self):
        """pid 0 / 负数只是难看，不能抛（格式化路径不许有异常分支）。"""
        self.assertIn("pid=0", _main._console_title_text(0, 1793354350.0))

    def test_15_real_set_and_restore(self):
        """真调 `SetConsoleTitleW` 验一次往返，**无论成败都还原原标题**。"""
        if sys.platform != "win32":
            self.skipTest("仅 Windows")
        import ctypes
        _k = ctypes.windll.kernel32
        _buf = ctypes.create_unicode_buffer(1024)
        if not _k.GetConsoleTitleW(_buf, 1024):
            self.skipTest("当前进程没有控制台窗口（headless），跳过真实设置")
        _orig = _buf.value
        try:
            self.assertTrue(_main._set_console_title("曈曈 unittest 临时标题"))
            _k.GetConsoleTitleW(_buf, 1024)
            self.assertEqual(_buf.value, "曈曈 unittest 临时标题")
        finally:
            _k.SetConsoleTitleW(_orig)

    def test_16_non_windows_is_noop(self):
        with mock.patch.object(_main.sys, "platform", "linux"):
            self.assertFalse(_main._set_console_title("x"))

    def test_17_failure_returns_false(self):
        """ctypes 出问题时只能返回 False（可见性不许拖垮启动）。"""
        import ctypes
        with mock.patch.object(
                ctypes, "windll", create=True,
                new=mock.MagicMock(**{"kernel32.SetConsoleTitleW.side_effect":
                                      RuntimeError("boom")})):
            self.assertFalse(_main._set_console_title("x"))


class TestRestartNotice(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="m404notice_")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_20_writes_under_data(self):
        _p = _main._write_restart_notice(self._td, 111, 222, reason="self_restart")
        self.assertIsNotNone(_p)
        self.assertEqual(os.path.dirname(_p), os.path.join(self._td, "data"))
        self.assertEqual(os.path.basename(_p), "restart_notice.txt")

    def test_21_content_has_both_pids_and_reason(self):
        _p = _main._write_restart_notice(self._td, 111, 222, reason="patch_apply")
        _t = _read(_p)
        self.assertIn("父进程 pid=111", _t)
        self.assertIn("接班 pid=222", _t)
        self.assertIn("patch_apply", _t)

    def test_22_content_has_child_window_title(self):
        """便条里要写出接班窗口的标题 —— 人手对照两个窗口时靠这一行。"""
        _p = _main._write_restart_notice(self._td, 111, 222)
        _t = _read(_p)
        self.assertIn("接班窗口标题：曈曈 pid=222 启动于", _t)

    def test_23_pure_lf(self):
        _p = _main._write_restart_notice(self._td, 1, 2)
        _b = open(_p, "rb").read()
        self.assertEqual(_b.count(b"\r\n"), 0, "便条必须是纯 LF（Windows 文本模式会翻 CRLF）")

    def test_24_overwrites_not_appends(self):
        _main._write_restart_notice(self._td, 1, 2, reason="第一次")
        _p = _main._write_restart_notice(self._td, 3, 4, reason="第二次")
        _t = _read(_p)
        self.assertIn("第二次", _t)
        self.assertNotIn("第一次", _t, "每次重启覆盖，不能越堆越长")

    def test_25_creates_missing_data_dir(self):
        _sub = os.path.join(self._td, "还没有 data")
        _p = _main._write_restart_notice(_sub, 1, 2)
        self.assertTrue(os.path.exists(_p))

    def test_26_failure_returns_none_not_raise(self):
        """data 被同名文件占住时只能返回 None（不影响重启）。"""
        _bad = os.path.join(self._td, "占位")
        os.makedirs(_bad, exist_ok=True)
        with open(os.path.join(_bad, "data"), "w", encoding="utf-8") as _f:
            _f.write("x")
        self.assertIsNone(_main._write_restart_notice(_bad, 1, 2))

    def test_27_reason_default(self):
        _p = _main._write_restart_notice(self._td, 1, 2)
        self.assertIn("self_restart", _read(_p))


class TestSpawnWiring(unittest.TestCase):
    """自重启段：便条必须写在 Popen **之后**（否则拿不到接班 pid）。"""

    @classmethod
    def setUpClass(cls):
        cls._src = _read(_MAIN_SRC)

    def test_30_signature_has_reason_with_default(self):
        _tree = ast.parse(self._src)
        _fn = next(n for n in _tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_spawn_self_restart")
        _args = [a.arg for a in _fn.args.args]
        self.assertEqual(_args, ["reason"])
        self.assertEqual(len(_fn.args.defaults), 1, "reason 必须有默认值（5 个旧调用点不传）")

    def test_31_notice_call_after_pid_registration(self):
        _i_add = self._src.index("_SELF_RESTART_CHILD_PIDS.add(_proc.pid)")
        _i_notice = self._src.index("_write_restart_notice(_project_root")
        self.assertGreater(_i_notice, _i_add, "便条必须在 Popen 拿到 pid 之后写")
        _i_ret = self._src.index("return True", _i_notice)
        self.assertLess(_i_notice, _i_ret, "便条必须在 return True 之前写")

    def test_32_notice_pass_reason(self):
        self.assertIn("reason=reason", self._src)

    def test_33_create_new_console_still_used(self):
        """①的前提：接班进程仍走 CREATE_NEW_CONSOLE（标题才有"窗口"可设）。"""
        self.assertIn("creationflags=subprocess.CREATE_NEW_CONSOLE", self._src)

    def test_34_all_call_sites_still_argless(self):
        """5 个旧调用点不该被改动（改了就说明签名不兼容）。"""
        _n = len(re.findall(r"_spawn_self_restart\(\)", self._src))
        self.assertGreaterEqual(_n, 4, "应至少还有 4 个不带参的调用点")


class TestEntryWiring(unittest.TestCase):
    def test_40_main_block_sets_title_before_main(self):
        _src = _read(_MAIN_SRC)
        _i = _src.rindex('if __name__ == "__main__":')  # 取最后一处，防字符串/注释里有同款
        _seg = _src[_i:]
        self.assertIn("_set_console_title()", _seg)
        self.assertLess(_seg.index("_set_console_title()"), _seg.index("main()"),
                        "标题要在 main() 之前设（启动期崩溃也能看出窗口是谁）")

    def test_41_console_title_helpers_exist(self):
        for _fn in ("_console_title_text", "_set_console_title", "_write_restart_notice"):
            self.assertTrue(callable(getattr(_main, _fn, None)), "缺函数 %s" % _fn)


class TestStopScript(unittest.TestCase):
    def test_50_list_pids_returns_list_or_none(self):
        _r = ss.list_framework_pids(_ROOT)
        self.assertTrue(_r is None or isinstance(_r, list))

    def test_51_main_psutil_unavailable_is_3(self):
        with mock.patch.object(ss, "list_framework_pids", return_value=None):
            self.assertEqual(ss.main([]), 3)

    def test_52_main_not_running_is_2(self):
        with mock.patch.object(ss, "list_framework_pids", return_value=[]):
            self.assertEqual(ss.main([]), 2)

    def test_53_dry_run_sends_nothing(self):
        with mock.patch.object(ss, "list_framework_pids", return_value=[123]), \
                mock.patch.object(ss.subprocess, "call") as _call:
            self.assertEqual(ss.main(["--dry-run"]), 0)
            _call.assert_not_called()

    def test_54_delegates_with_reason_and_timeout(self):
        with mock.patch.object(ss, "list_framework_pids", return_value=[123]), \
                mock.patch.object(ss.subprocess, "call", return_value=0) as _call:
            self.assertEqual(ss.main(["--reason", "r1", "--timeout", "30"]), 0)
            _cmd = _call.call_args[0][0]
            self.assertIn("night_orch_shutdown.py", " ".join(_cmd))
            self.assertEqual(_cmd[_cmd.index("--reason") + 1], "r1")
            self.assertEqual(float(_cmd[_cmd.index("--timeout") + 1]), 30.0)

    def test_55_delegate_oserror_is_1(self):
        with mock.patch.object(ss, "list_framework_pids", return_value=[123]), \
                mock.patch.object(ss.subprocess, "call", side_effect=OSError("no py")):
            self.assertEqual(ss.main([]), 1)

    def test_56_delegate_exit_code_passthrough(self):
        for _rc in (0, 1, 2, 3):
            with mock.patch.object(ss, "list_framework_pids", return_value=[1]), \
                    mock.patch.object(ss.subprocess, "call", return_value=_rc):
                self.assertEqual(ss.main([]), _rc)

    def test_57_does_not_read_runtime_lock(self):
        """③的票面前提不成立：**代码**不该去读盘上不存在的锁文件（注释/文档可以提它）。"""
        _src = _read(_STOP_SRC)
        _tree = ast.parse(_src)
        _docs = set()
        for _n in ast.walk(_tree):
            if isinstance(_n, (ast.Module, ast.FunctionDef, ast.ClassDef,
                               ast.AsyncFunctionDef)):
                _d = ast.get_docstring(_n, clean=False)
                if _d:
                    _docs.add(_d)
        _hits = [n.value for n in ast.walk(_tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and "runtime.lock" in n.value and n.value not in _docs]
        self.assertEqual(_hits, [], "代码（非注释/文档）不该引用 runtime.lock")
        self.assertNotIn("os.kill", _src, "不许发 SIGTERM/SIGKILL（Windows 不可靠且跳过 finally）")
        self.assertNotIn("psutil.Process(", _src, "停止走 shutdown_request 通道，不强杀")

    def test_58_reuses_project_process_criteria(self):
        """判据必须复用 NightOrchestration，不能另写一套（口径分叉会给出相反答案）。"""
        _src = _read(_STOP_SRC)
        self.assertIn("_cmdline_is_framework_main", _src)
        self.assertIn("night_orchestration", _src)


class TestSourceHygiene(unittest.TestCase):
    def _hygiene(self, path):
        _t = _read(path)
        _miss = []
        for _n in ast.walk(ast.parse(_t)):
            if isinstance(_n, ast.ExceptHandler):
                _seg = ast.get_source_segment(_t, _n) or ""
                if "silent_exc" not in _seg and "logger" not in _seg:
                    _miss.append(_n.lineno)
        return _t, _miss

    def test_60_every_except_logs_in_stop_script(self):
        _t, _miss = self._hygiene(_STOP_SRC)
        self.assertEqual(_miss, [], "★每个 except 必须留痕")

    def test_61_no_silent_except_in_404_block(self):
        """只钉本刀新增的段。

        ★main.py 全文件另有 **77 处历史存量**静默 handler（模块级导入兜底等，
        如 L90 的 `except Exception:` 兜底绑定 stub）—— 那是既有状态，由
        `tools/ci/cw2_t2e_ci_gate_silent_except.py` 按"只降不升"管着，
        本刀不越界去钉它（钉了就是给自己造一条永久红）。
        """
        _t = _read(_MAIN_SRC)
        _i = _t.index("# ===== 404 刀4：自动重启窗口可见性 =====")
        _j = _t.index("def _apply_pending_patches_and_restart")
        _seg = _t[_i:_j]
        _wrapped = "if 1:\n" + "\n".join("    " + _l for _l in _seg.splitlines())
        _bad = []
        for _n in ast.walk(ast.parse(_wrapped)):
            if isinstance(_n, ast.ExceptHandler):
                _src = ast.get_source_segment(_wrapped, _n) or ""
                if "silent_exc" not in _src and "logger" not in _src:
                    _bad.append(_n.lineno)
        self.assertEqual(_bad, [], "★404 刀4 新增段的每个 except 必须留痕")

    def test_62_no_bare_except_pass(self):
        for _p in (_MAIN_SRC, _STOP_SRC):
            _t = _read(_p)
            for _n in ast.walk(ast.parse(_t)):
                if isinstance(_n, ast.ExceptHandler) and len(_n.body) == 1 \
                        and isinstance(_n.body[0], ast.Pass):
                    self.fail("%s:%d 有 except: pass" % (_p, _n.lineno))

    def test_63_main_py_pure_lf(self):
        _b = open(_MAIN_SRC, "rb").read()
        self.assertEqual(_b.count(b"\r\n"), 0, "main.py 必须保持纯 LF")

    def test_64_stop_script_pure_lf(self):
        _b = open(_STOP_SRC, "rb").read()
        self.assertEqual(_b.count(b"\r\n"), 0, "scripts/stop_tongtong.py 必须纯 LF")


if __name__ == "__main__":
    unittest.main(verbosity=2)
