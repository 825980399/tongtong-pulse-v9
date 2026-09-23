"""_framework_probe —— 框架运行探测（共享工具）

第五批 任务1/2B 共用：检测生产框架主进程（main.py）是否正在运行。
供经验池清理工具、verify_phase17_1_5.py、PeriodicTestScheduler 等复用，
避免在框架运行时误跑会写生产数据的脚本 / 误落盘历史清理。

约定：
  - 返回 True 表示「框架在跑」，调用方应据此拒绝写盘或改用隔离路径；
  - 探测失败时保守返回 True（宁可误判为在跑，也不冒险污染生产）。

第82批 T-a 改造（总账 185.5 / 工具链债务）：
  - 主探改用 psutil（生产 Python 3.12 已装 7.2.2），不再首走已被微软弃用、
    且易被沙箱 Program Blacklist 拦截的 wmic；
  - psutil 不可用（未安装）时回退 wmic，并显式 WARNING；
  - 旧版 `except Exception: return True` 静默保守，导致路灯沙箱里 wmic 被拦时
    verify 误判「框架在跑」而跳过重负载用例、"0 失败"表象掩盖少跑用例。
    现任何失败路径都打 logging.WARNING（带异常类型与原因），便于调用方区分
    「真在跑」与「探测失败被迫保守」。安全契约不变：失败仍返回 True。
"""
from __future__ import annotations

import logging
import subprocess
from typing import Iterable

from config import TIMEOUT_CONFIG

logger = logging.getLogger(__name__)

# 判定框架进程时认可的 python 解释器名（小写）。
_PYTHON_NAMES = frozenset({"python.exe", "pythonw.exe", "python"})


def _cmdline_is_framework(cmdline: Iterable[str] | None) -> bool:
    """命令行是否属于「生产框架主进程」。

    与旧 wmic 判定等价且更严：仅当命令行**最后一段以 main.py 结尾**才算。
    这样 `python -c "... 'main.py' ..."` 这类自检/测试进程不会因字符串里
    出现 main.py 字样而被误判为框架在跑（第82批实测：朴素 `in` 判断会把
    psutil 自检进程本身算成框架）。
    """
    if not cmdline:
        return False
    joined = " ".join(str(c) for c in cmdline).strip().strip('"')
    return joined.endswith("main.py")


def _psutil_running() -> bool | None:
    """psutil 主探。

    返回：
      True  —— 发现命令行以 main.py 结尾的 python 进程；
      False —— psutil 可用且扫完确认无框架进程；
      None  —— psutil 未安装/不可用，交由调用方走 wmic 兜底。
    单进程级 NoSuchProcess/AccessDenied 等瞬时异常静默跳过；整批系统性
    异常向上抛，由 _framework_looks_running 统一记 WARNING。
    """
    try:
        import psutil
    except Exception:
        return None

    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name not in _PYTHON_NAMES:
                continue
            if _cmdline_is_framework(proc.info.get("cmdline")):
                return True
        except Exception:
            # 单进程在枚举中消失 / 无权限读取 → 跳过该进程，不影响整体判定
            continue
    return False


def _wmic_running() -> bool:
    """wmic 兜底（已被微软弃用，仅在无 psutil 的老环境使用）。"""
    out = subprocess.run(
        ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine"],
        capture_output=True, text=True, timeout=TIMEOUT_CONFIG['subprocess_default'],
        encoding="utf-8", errors="replace",
    )
    for _line in (out.stdout or "").splitlines():
        _c = _line.strip().strip('"')
        if _c.endswith("main.py"):
            return True
    return False


def _framework_looks_running() -> bool:
    """粗略检测生产框架是否在运行（命令行含 main.py 的 python 进程）。"""
    try:
        found = _psutil_running()
        if found is not None:
            return found
        # psutil 不可用 → wmic 兜底（不再静默）
        logger.warning(
            "psutil 不可用，回退 wmic 探测框架进程（wmic 已被微软弃用，"
            "建议安装 psutil；若本环境 wmic 被沙箱拦截将转保守 True）"
        )
        return _wmic_running()
    except Exception as e:
        # 探测失败时保守认为在跑，避免误跑测试污染生产；但必须显式留痕
        logger.warning(
            "框架运行探测失败，保守判定为「在跑」以避免污染生产（原因：%s: %s）",
            type(e).__name__, e,
        )
        return True


if __name__ == "__main__":
    print("framework_running =", _framework_looks_running())
