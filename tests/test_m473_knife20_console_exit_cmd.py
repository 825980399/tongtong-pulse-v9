# -*- coding: utf-8 -*-
"""473刀20 门控单测：控制台退出命令（exit / quit / q / 退出）。

现场（2026-11-07 用户报障）：
  「我在脚本里面输入退出 exit，只能退出对话，不能退出整个框架；
    然后我使用 Ctrl+C 退出就会出现输入 y n 的选择项。」

两条根因完全不同，必须分开治：
  1. **y/n 是 cmd 对 .bat 的提示**，不是 Python 的 —— 走 `start_tongtong.bat`
     启动时 Ctrl+C 打到 cmd，cmd 追问「终止批处理操作吗(Y/N)」。直接
     `python main.py` 就没有（Ctrl+C 直达 Python，走 signal_handler）。
     这条**不用改代码**：改的是启动方式。
  2. **exit 根本没人接** —— `exit_requested` 只由 `signal_handler`（SIGINT/SIGTERM）
     置位；全库只有一处 `input()`（退出确认），没有任何控制台命令循环。
     `start_tongtong.bat` 注释里那句「在对话里输入 quit / exit」是**从未实现的承诺**。
     本刀补上。

设计上两个必须钉死的点：
  * **用 msvcrt 轮询，不用 readline 线程** —— `_confirm_apply_pending_on_quit`
    退出时要 `input()` 读 y/n，阻塞式 readline 会与它抢 stdin，确认框收不到输入。
  * **循环条件是 `not exit_requested`** —— 主循环一退，监听器当拍就停，
    把 stdin 完整交还给确认框。
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as CFG

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as _fh:
    _SRC = _fh.read()
_TREE = ast.parse(_SRC)
_LINES = _SRC.split("\n")


def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


_WORKER = _find_func(_TREE, "_m473_console_exit_worker")
_MAIN = _find_func(_TREE, "main")


def _worker_src():
    """取出 worker 的源码文本（按 AST 行号区间）。"""
    assert _WORKER is not None
    return "\n".join(_LINES[_WORKER.lineno - 1:_WORKER.end_lineno])


def _seg_after_signal_handler(n: int = 90) -> str:
    """signal_handler 注册之后的 n 行 —— 开关块与线程启动都在这里。"""
    for i, line in enumerate(_LINES):
        if line.strip() == "signal.signal(signal.SIGTERM, signal_handler)":
            return "\n".join(_LINES[i:i + n])
    return ""


class TestSwitch(unittest.TestCase):
    def test_01_switch_exists_and_default_on(self):
        self.assertIs(True, getattr(CFG, "ENABLE_M473_CONSOLE_EXIT_CMD", None))

    def test_02_switch_declared_in_config_with_rollback_note(self):
        # 灰度开关必须写清「False 会怎样」，否则下一个人不敢关
        with open(os.path.join(_ROOT, "config.py"), encoding="utf-8") as _fh2:
            _cfg_src = _fh2.read()
        self.assertIn("ENABLE_M473_CONSOLE_EXIT_CMD = True", _cfg_src)
        _i = _cfg_src.find("ENABLE_M473_CONSOLE_EXIT_CMD = True")
        self.assertIn("逐字回滚", _cfg_src[max(0, _i - 300):_i])


class TestWorkerShape(unittest.TestCase):
    def test_03_worker_defined_inside_main(self):
        """必须在 main() 内 —— 它闭包读 exit_requested、调 signal_handler。"""
        self.assertIsNotNone(_WORKER)
        _in_main = any(
            isinstance(n, ast.FunctionDef) and n.name == "main" and _WORKER in ast.walk(n)
            for n in ast.walk(_TREE)
        )
        self.assertTrue(_in_main, "worker 不在 main() 内 ⇒ 闭包拿不到 exit_requested")

    def test_04_uses_msvcrt_polling_not_readline(self):
        _s = _worker_src()
        self.assertIn("msvcrt.kbhit", _s)
        self.assertIn("msvcrt.getwch", _s)
        # ★防回退：阻塞式 readline 会与退出确认的 input() 抢 stdin
        self.assertNotIn("stdin.readline", _s)
        self.assertNotIn("input(", _s)

    def test_05_loop_condition_is_exit_requested(self):
        """主循环一退监听器就停 ⇒ 确认框拿到干净 stdin。"""
        _s = _worker_src()
        self.assertIn("while not exit_requested", _s)

    def test_06_accepts_exit_quit_q_and_chinese(self):
        _s = _worker_src()
        for _cmd in ('"exit"', '"quit"', '"q"', '"退出"'):
            self.assertIn(_cmd, _s, f"缺少退出命令 {_cmd}")

    def test_07_special_keys_consumed(self):
        """方向键是两字节前缀，不处理会把尾字节当命令字符攒进 buffer。"""
        _s = _worker_src()
        self.assertIn('"\\x00"', _s)
        self.assertIn('"\\xe0"', _s)

    def test_08_backspace_supported(self):
        self.assertIn('"\\x08"', _worker_src())

    def test_09_buffer_bounded(self):
        """无回车乱敲不能把 buffer 涨上去。"""
        self.assertIn("[-32:]", _worker_src())

    def test_10_calls_signal_handler(self):
        """复用既有优雅退出路径（finally 里的 6 步清理），不另写一套。"""
        self.assertIn("signal_handler(None, None)", _worker_src())

    def test_11_logs_the_command(self):
        self.assertIn("退出命令", _worker_src())


class TestEveryExceptHasSilentExc(unittest.TestCase):
    def test_12_worker_handlers_all_silent(self):
        """铁律 4：每个 except 都要 silent_exc（本文件同受管）。"""
        self.assertIsNotNone(_WORKER)
        _bad = []
        for _node in ast.walk(_WORKER):
            if not isinstance(_node, ast.ExceptHandler):
                continue
            _has = any(
                isinstance(_n, ast.Call)
                and isinstance(_n.func, ast.Name)
                and _n.func.id == "silent_exc"
                for _n in ast.walk(_node)
            )
            if not _has:
                _bad.append(_node.lineno)
        self.assertEqual([], _bad, f"这些 except 缺 silent_exc：{_bad}")

    def test_13_handler_count_reasonable(self):
        """worker 内至少覆盖 import / 读键 / 特殊键 / 留痕 四处。"""
        self.assertIsNotNone(_WORKER)
        _n = sum(1 for _x in ast.walk(_WORKER) if isinstance(_x, ast.ExceptHandler))
        self.assertGreaterEqual(_n, 4)


class TestStartupBlock(unittest.TestCase):
    def test_14_switch_read_does_not_swallow_zero(self):
        """★`getattr(cfg, X, True) or True` 会把 0 吞成 True（本项目栽过两次）。"""
        _s = _seg_after_signal_handler()
        self.assertIn("ENABLE_M473_CONSOLE_EXIT_CMD", _s)
        self.assertIn("is None", _s)
        self.assertNotIn("or True", _s)

    def test_15_only_starts_on_tty(self):
        """重定向/管道/服务方式下不抢输入。"""
        self.assertIn("isatty", _seg_after_signal_handler())

    def test_16_thread_is_daemon(self):
        _s = _seg_after_signal_handler()
        self.assertIn("daemon=True", _s)
        self.assertIn('name="m473-console-exit"', _s)

    def test_17_startup_after_signal_handler(self):
        """顺序依赖：必须先有 signal_handler 才能被 worker 调用。"""
        _s = _SRC
        _i_sig = _s.find("def signal_handler(sig, frame):")
        _i_worker = _s.find("def _m473_console_exit_worker():")
        self.assertGreater(_i_sig, 0)
        self.assertGreater(_i_worker, _i_sig)

    def test_18_prints_hint(self):
        self.assertIn("控制台输入 exit 或 quit", _SRC)


class TestNoRegressionToBat(unittest.TestCase):
    def test_19_main_py_still_has_sigint_path(self):
        """Ctrl+C 路径不能因为本刀被改坏。"""
        self.assertIn("signal.signal(signal.SIGINT, signal_handler)", _SRC)

    def test_20_confirm_prompt_untouched(self):
        """退出确认仍走 input()，本刀不改它。"""
        self.assertIn('_box["v"] = input(prompt)', _SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
