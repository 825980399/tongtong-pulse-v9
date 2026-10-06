# -*- coding: utf-8 -*-
"""第162批刀7 · 外部夜间编排退出脚本（无人值守优雅退出通道）。

用法：
    python tools/night_orch_shutdown.py [--reason TEXT] [--timeout N] [--report PATH]

职责：
  1. 互斥判据：用 psutil 确认 main.py 框架在跑（basename=="main.py" 精确匹配）；
     未运行则直接退出（不写指令）。
  2. PID 锁：data/night_orch_lock.pid 防重复编排（同型于框架单例锁）；
     锁存在且 PID 存活 → 中止。
  3. 写 data/shutdown_request.json（经 NightShutdownChannel.request_shutdown）。
  4. 轮询等待框架退出（消费标记 consumed=True 或进程消失），超时 = NIGHT_ORCH_SHUTDOWN_TIMEOUT。
  5. 写汇报 data/night_orch_report.json。

退出码：0=框架已退出；2=框架未在运行；3=PID 锁被占用/无法判定；1=等待超时/其它错误。
"""

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.evolution import night_orchestration as _orch
from nucleus._silent_except import silent_exc


def _read_config(name, default):
    try:
        import config as _cfg
        return getattr(_cfg, name, default)
    except Exception as _e:
        silent_exc(_e, where="tools.night_orch_shutdown._read_config")
        return default


def _acquire_pid_lock(lock_path):
    _pid = os.getpid()
    if os.path.exists(lock_path):
        try:
            with open(lock_path, "r", encoding="utf-8") as _f:
                _old = int((_f.read() or "0").strip() or 0)
        except Exception as _e:
            silent_exc(_e, where="tools.night_orch_shutdown._acquire_pid_lock.read")
            _old = None
        if _old:
            try:
                import psutil
                if psutil.pid_exists(_old):
                    return False  # 另一编排仍存活
            except Exception as _e:
                silent_exc(_e, where="tools.night_orch_shutdown._acquire_pid_lock.check")
                return False  # 无法判定 → 保守拒绝
    try:
        with open(lock_path, "w", encoding="utf-8") as _f:
            _f.write(str(_pid))
    except Exception as _e:
        silent_exc(_e, where="tools.night_orch_shutdown._acquire_pid_lock.write")
        return False
    return True


def _release_pid_lock(lock_path):
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception as _e:
        silent_exc(_e, where="tools.night_orch_shutdown._release_pid_lock")


def _write_report(path, ok, status):
    try:
        _d = {"ok": ok, "status": status, "at": time.time()}
        with open(path, "w", encoding="utf-8") as _f:
            json.dump(_d, _f, ensure_ascii=False, indent=2)
    except Exception as _e:
        silent_exc(_e, where="tools.night_orch_shutdown._write_report")


def main():
    ap = argparse.ArgumentParser(description="夜间编排优雅退出（写 shutdown_request.json）")
    ap.add_argument("--reason", default="night_orchestration")
    ap.add_argument("--timeout", type=float, default=None,
                    help="等待框架退出超时（秒）；默认取 config.NIGHT_ORCH_SHUTDOWN_TIMEOUT")
    ap.add_argument("--report", default=None, help="汇报路径；默认 config.NIGHT_ORCH_REPORT_PATH")
    ap.add_argument("--digest", action="store_true",
                    help="第162批刀16：产出每日自报 digest（data/reports/digest_YYYYMMDD.md）并退出")
    args = ap.parse_args()

    # 第162批刀16：每日 digest 挂靠点（与 collect 同段，由外部 04:00 调度调用）
    if args.digest:
        _path = _orch.generate_night_digest(_ROOT)
        if _path:
            print("[night_orch] digest 已产出：%s" % _path)
            return 0
        print("[night_orch] digest 产出失败（无数据或 NIGHT_ORCH_DIGEST_ENABLED=False）。")
        return 1

    _timeout = float(args.timeout) if args.timeout else float(_read_config("NIGHT_ORCH_SHUTDOWN_TIMEOUT", 120))
    _report = args.report or _read_config("NIGHT_ORCH_REPORT_PATH", "data/night_orch_report.json")
    _enabled = bool(_read_config("NIGHT_ORCH_ENABLED", True))

    _lock_path = os.path.join(_ROOT, "data", "night_orch_lock.pid")

    if not _enabled:
        print("[night_orch] NIGHT_ORCH_ENABLED=False，退出通道未启用，中止。")
        return 3

    # 1) 互斥判据：框架在跑？
    _running = _orch.is_framework_running(_ROOT)
    if _running is None:
        print("[night_orch] 无法判定框架是否运行（psutil 不可用），保守中止。")
        return 3
    if not _running:
        print("[night_orch] 框架未在运行（未检测到 main.py 进程），无需退出指令。")
        return 2

    # 2) PID 锁防重复编排
    if not _acquire_pid_lock(_lock_path):
        print("[night_orch] PID 锁被占用（另一编排进行中），中止。")
        return 3

    _chan = _orch.NightShutdownChannel(_ROOT)
    try:
        # 3) 写退出指令
        if not _chan.request_shutdown(reason=args.reason):
            print("[night_orch] 写入 shutdown_request.json 失败。")
            return 1
        print("[night_orch] 已写入 shutdown_request.json（reason=%s），等待框架退出（超时 %.0fs）..." % (args.reason, _timeout))

        # 4) 轮询等待
        _deadline = time.time() + _timeout
        while time.time() < _deadline:
            _now = _orch.is_framework_running(_ROOT)
            if _now is False:
                print("[night_orch] 框架进程已消失，退出成功。")
                _write_report(_report, True, "framework_gone")
                return 0
            # 指令已消费（consumed=True）或文件已删除 → 退出流程已接管，给宽限期确认进程消失
            try:
                _fp = os.path.join(_ROOT, "data", "shutdown_request.json")
                if not os.path.exists(_fp) or _chan.read_request().get("consumed"):
                    time.sleep(2)
                    if _orch.is_framework_running(_ROOT) is False:
                        _write_report(_report, True, "consumed_then_gone")
                        return 0
            except Exception as _e:
                silent_exc(_e, where="tools.night_orch_shutdown.main.poll")
            time.sleep(2)

        print("[night_orch] 等待超时（%.0fs），框架仍未退出。" % _timeout)
        _write_report(_report, False, "timeout")
        return 1
    finally:
        _release_pid_lock(_lock_path)


if __name__ == "__main__":
    sys.exit(main())
