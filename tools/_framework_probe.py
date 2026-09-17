"""_framework_probe —— 框架运行探测（共享工具）

第五批 任务1/2B 共用：检测生产框架主进程（main.py）是否正在运行。
供经验池清理工具、verify_phase17_1_5.py、PeriodicTestScheduler 等复用，
避免在框架运行时误跑会写生产数据的脚本 / 误落盘历史清理。

约定：
  - 返回 True 表示「框架在跑」，调用方应据此拒绝写盘或改用隔离路径；
  - 探测失败时保守返回 True（宁可误判为在跑，也不冒险污染生产）。
"""
from __future__ import annotations
from config import TIMEOUT_CONFIG

import subprocess


def _framework_looks_running() -> bool:
    """粗略检测生产框架是否在运行（命令行含 main.py 的 python 进程）。"""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine"],
            capture_output=True, text=True, timeout=TIMEOUT_CONFIG['subprocess_default'],
            encoding="utf-8", errors="replace",
        )
        for _line in (out.stdout or "").splitlines():
            _c = _line.strip().strip('"')
            if _c.endswith("main.py"):
                return True
    except Exception:
        # 探测失败时保守认为在跑，避免误跑测试污染生产
        return True
    return False


if __name__ == "__main__":
    print("framework_running =", _framework_looks_running())
