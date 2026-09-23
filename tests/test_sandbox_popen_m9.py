# -*- coding: utf-8 -*-
"""主线第9批 任务1 P2-76：PatchManager 的 Popen 参数错误修复 —— 回归测试。

真实根因：`nucleus/security/sandbox_limits.py` 的 `execute_code_in_subprocess`
曾把 `timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_medium"]` 当作
`subprocess.Popen(...)` 的构造参数，而 **Popen 没有 `timeout` 形参**
（只 `run()` / `communicate()` 支持），导致补丁 import 检查报
"Popen.__init__() got an unexpected keyword argument"。

任务书猜测的 `text` / `encoding` / `capture_output` 在 Python 3.12 全合法，
并非根因；本测试直接验证修复后的子进程执行路径。

覆盖：
1. 正常代码片段可经修复后的 Popen 路径执行成功（不再抛 unexpected keyword argument）
2. 墙上时钟超时仍由 `proc.communicate(timeout=...)` 兜底（移除 Popen 上误加的
   timeout 形参后，超时语义不受影响）
"""
import os
import sys

# 保证仓库根目录在 sys.path，便于直接 `python -m pytest`
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.security.sandbox_limits import execute_code_in_subprocess


def test_execute_code_in_subprocess_runs_without_popen_timeout_error():
    """修复后：普通代码片段可经 Popen 路径成功执行，不报 unexpected keyword argument。"""
    code = "print('hello_t1_popen_fix')"
    res = execute_code_in_subprocess(
        code,
        timeout_seconds=10,
        allow_full_builtins=True,
    )
    assert res.returncode == 0, f"子进程非0退出: {res.stderr!r}"
    assert "hello_t1_popen_fix" in (res.stdout or ""), f"stdout 不符: {res.stdout!r}"
    assert res.timed_out is False
    assert res.limit_hit is None


def test_execute_code_in_subprocess_wall_clock_timeout_still_works():
    """移除 Popen 上误加的 timeout 形参后，墙上时钟超时仍由 communicate 兜底。"""
    code = "import time\ntime.sleep(10)\n"
    res = execute_code_in_subprocess(
        code,
        timeout_seconds=0.5,
        allow_full_builtins=True,
    )
    assert res.timed_out is True, f"应触发超时，实际 limit_hit={res.limit_hit!r}"
    assert res.limit_hit == "wall_clock", f"应为 wall_clock，实际 {res.limit_hit!r}"
