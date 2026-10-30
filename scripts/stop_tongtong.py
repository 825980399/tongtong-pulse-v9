#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stop_tongtong —— 曈曈优雅停止入口（404 刀4③，给人手用）。

用法：
    python scripts/stop_tongtong.py                # 停止（默认超时取 config，通常 120s）
    python scripts/stop_tongtong.py --dry-run       # 只看在不在跑、pid 是多少，不发指令
    python scripts/stop_tongtong.py --timeout 60    # 自定义等待上限
    python scripts/stop_tongtong.py --reason manual_stop

★为什么不去读 ``data/runtime.lock``
------------------------------------
任务书写的「读 runtime.lock 拿 pid」**前提不成立**：该文件盘上不存在，且全仓无创建代码
（``nucleus/evolution/night_orchestration.py`` 头部已把这条和"Windows 无可靠外部 SIGTERM"
一起记载下来，162 批刀7 起改用 psutil 进程判据）。本脚本复用**同一判据**——
枚举命令行指向 ``main.py`` 的进程——来拿 pid，等价、且不会像读锁文件那样恒判"没在跑"。

★为什么还要 ``scripts/`` 这个目录
----------------------------------
任务书点名这个路径，人手敲得顺手。但**核心逻辑不在这里**：写
``data/shutdown_request.json``、轮询 ``consumed``、PID 锁防重复、超时判定，全部在
``tools/night_orch_shutdown.py``。本脚本只做三件事：打印 pid、翻译参数、把退出码带出去。
两套实现必然漂移，所以这里刻意不复制。

★别改用 SIGTERM / SIGKILL
--------------------------
Windows 无可靠外部 SIGTERM（同上注释）。强杀会跳过 ``finally`` 清理段——快照写盘、
子进程回收、待批补丁落盘都在那儿，这正是本通道存在的理由。优雅退出基准约 21s，
故默认等待上限取 120s（``config.NIGHT_ORCH_SHUTDOWN_TIMEOUT``）。

退出码（与 tools/night_orch_shutdown.py 一致）：
    0=框架已退出 / 1=等待超时或错误 / 2=框架未在运行 / 3=无法判定或编排锁被占用
"""
import argparse
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:  # 脚本直跑时 sys.path[0] 是 scripts/
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402
from nucleus.evolution import night_orchestration as _orch  # noqa: E402

_DELEGATE = os.path.join("tools", "night_orch_shutdown.py")


def list_framework_pids(root=None):
    """枚举命令行指向 ``main.py`` 的进程 pid（升序）。

    Returns:
        ``list[int]``；psutil 不可用时返回 ``None``（**判不了 ≠ 没在跑**，
        调用方必须保守中止而不是当成"未运行"往下走）。
    """
    try:
        import psutil
    except Exception as _e:
        silent_exc(_e, where="scripts.stop_tongtong.list_framework_pids[import]", level="debug")
        return None
    _self = os.getpid()
    _out = []
    try:
        for _p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if _p.info.get("pid") == _self:
                    continue
                # 复用 NightOrchestration 的判据（basename=="main.py" 精确匹配），
                # 不在这里另写一套 —— 口径分叉会让"在不在跑"两个工具给出相反答案
                if _orch._cmdline_is_framework_main(_p.info.get("cmdline") or []):
                    _out.append(int(_p.info["pid"]))
            except Exception as _e:
                silent_exc(_e, where="scripts.stop_tongtong.list_framework_pids[loop]",
                           level="debug")
                continue
    except Exception as _e:
        silent_exc(_e, where="scripts.stop_tongtong.list_framework_pids")
        return None
    return sorted(_out)


def main(argv=None):
    _ap = argparse.ArgumentParser(
        description="曈曈优雅停止（写 data/shutdown_request.json，等框架自己走 finally 清理）")
    _ap.add_argument("--reason", default="manual_stop", help="写进退出指令的原因")
    _ap.add_argument("--timeout", type=float, default=None,
                     help="等待框架退出的上限（秒），默认取 config.NIGHT_ORCH_SHUTDOWN_TIMEOUT")
    _ap.add_argument("--dry-run", dest="dry_run", action="store_true",
                     help="只报告在不在跑、pid 是多少，不发任何指令")
    _a = _ap.parse_args(argv)

    _pids = list_framework_pids(_ROOT)
    if _pids is None:
        print("[stop] psutil 不可用，判不了框架是否在跑（保守中止，不当成未运行）")
        return 3
    if not _pids:
        print("[stop] 框架未在运行（没找到命令行指向 main.py 的进程），无需停止")
        return 2
    print("[stop] 检测到框架进程：%s" % ", ".join("pid=%d" % _x for _x in _pids))
    if _a.dry_run:
        print("[stop] --dry-run：未写任何指令")
        return 0

    _cmd = [sys.executable, os.path.join(_ROOT, _DELEGATE), "--reason", _a.reason]
    if _a.timeout is not None:
        _cmd += ["--timeout", str(_a.timeout)]
    print("[stop] 写 data/shutdown_request.json 并等待框架退出（优雅退出基准约 21s）…")
    try:
        _rc = subprocess.call(_cmd)
    except OSError as _e:
        silent_exc(_e, where="scripts.stop_tongtong.main[delegate]")
        print("[stop] 调用 %s 失败：%s" % (_DELEGATE, _e))
        return 1
    print("[stop] 退出码 %d（0=已退出 / 1=超时或错误 / 2=未运行 / 3=无法判定）" % _rc)
    return _rc


if __name__ == "__main__":
    sys.exit(main())
