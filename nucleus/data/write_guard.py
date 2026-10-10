"""写盘守卫 —— 测试环境不得写生产 `data/`（★主线第44批 T4 / P2-290）。

背景
----
第43批 T0 实测：`data/llm_traces/calls_20260913.jsonl` **239 条中 146 条（61.1%）**
是测试桩 —— 根因是 `PulseLung` 埋点在 pytest 环境下用**生产默认目录**落盘。
第43批裁决第 1 项要求把该防护**推广到其他写盘型组件**（经验库 / 知识库 / 补丁历史等）。

判据（与 `nucleus/llm/call_recorder.py` 的既有守卫**同源**）
------------------------------------------------------------
拒写 = **三条件同时成立**：
1. ``ENABLE_TEST_ENV_WRITE_GUARD`` 开关为真（默认 True；关闭 → 完全复现修复前行为，零回归）；
2. 当前处于 pytest 环境（`PYTEST_CURRENT_TEST` 或 `pytest` 已在 `sys.modules`）；
3. 目标路径落在**本项目的 ``data/`` 生产数据区**内，且调用方**未显式注入**目录。

★只判 ``data/`` 前缀 —— 因此：
* 写**源码文件**（补丁应用）不受影响；
* 写**临时目录 / 显式注入的隔离目录**不受影响；
* 测试构造的 ``project_root=tmp`` 天然落在真实 root 之外 → 不受影响。

★本模块**永不抛出**：任何异常都按"允许写"处理（守卫故障不得阻断正常功能）。
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any

from nucleus._silent_except import silent_exc

#: 已告警过的组件名（每组件只记一次，避免日志刷屏）
_warned: set[str] = set()
_warn_lock = threading.Lock()


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def guard_enabled() -> bool:
    """守卫总开关（``ENABLE_TEST_ENV_WRITE_GUARD``，默认 True）。"""
    try:
        return bool(_cfg("ENABLE_TEST_ENV_WRITE_GUARD", True))
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::guard_enabled L47")
        return True


def is_test_env() -> bool:
    """是否处于 pytest 环境。"""
    try:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return True
        return "pytest" in sys.modules
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::is_test_env L57")
        return False


def project_root() -> str:
    """本项目根目录（由本模块文件位置反推：``nucleus/data/write_guard.py`` → 上三级）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


# ★主线第51批 T3（P2-354）：进程级写权限判据 -------------------------------

def is_framework_process() -> bool:
    """是否**框架主进程**。

    判据（任一成立即可，双保险）：
    ① 环境变量 ``PULSE_FRAMEWORK`` 已设置 —— ``main.py`` 启动时写入，
       其派生的子进程自动继承；
    ② 主模块 ``__main__`` 的文件名为 ``main.py`` —— 兜底，防漏设环境变量
       导致框架被误拦。

    ★这是「普通脚本只读」策略的安全阀：框架进程必须始终可写。
    """
    try:
        if os.environ.get("PULSE_FRAMEWORK"):
            return True
        _m = sys.modules.get("__main__")
        _f = getattr(_m, "__file__", "") or ""
        return bool(_f) and os.path.basename(_f).lower() == "main.py"
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::is_framework_process L86")
        return False


def is_test_mode() -> bool:
    """是否**显式声明**了测试环境（``PULSE_TEST_MODE=1``）。

    供**不经 pytest** 运行的测试/治理脚本使用（如 ``tmp/check_*.py``）。
    """
    try:
        return bool(os.environ.get("PULSE_TEST_MODE"))
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::is_test_mode L97")
        return False


def resolve_env() -> str:
    """★主线第67批 T5/P2：环境判定结果 —— ``"production"`` / ``"test"``。

    判定优先级（前者为准）：
      ① ``config.WRITE_GUARD_FORCE_ENV`` 显式指定（"production"/"test"）——运维强制覆盖；
      ② ``PULSE_TEST_MODE`` 显式声明 → test；
      ③ ``is_framework_process()``（框架主进程）→ production；
      ④ ``is_test_env()``（pytest 在 sys.modules）→ test；
      ⑤ 其余 → production（普通脚本按第51批策略只读，但**不是**"测试环境"）。

    ★修复的误判：框架进程内某些工具模块会 ``import pytest``，
      使 ``"pytest" in sys.modules`` 为真 → 框架被误判为测试环境
      → ReportBus 无法写生产目录（实测 12 条 WARNING）。
      把「框架主进程」判定提到「pytest 判定」之前即可根治。
    """
    try:
        import config as _cfg67
        _force = getattr(_cfg67, "WRITE_GUARD_FORCE_ENV", None)
    except Exception:
        _force = None
    if _force in ("production", "test"):
        return str(_force)
    if is_test_mode():
        return "test"
    if is_framework_process():
        return "production"
    if is_test_env():
        return "test"
    return "production"


def env_reason() -> str:
    """★第67批 T5：环境判定的**依据**（供日志留痕，便于排查误判）。"""
    try:
        if resolve_env() == "test":
            return ("PULSE_TEST_MODE 显式声明" if is_test_mode()
                    else "pytest 环境(pytest in sys.modules)")
        return ("框架主进程(PULSE_FRAMEWORK/main.py)" if is_framework_process()
                else "普通脚本(默认生产)")
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::env_reason L140")
        return "判定异常(回退默认)"


def is_test_like_env() -> bool:
    """是否**测试类环境**（pytest 或显式 ``PULSE_TEST_MODE``）。

    ★测试类环境**一律不得**写生产 ``data/``（与第44批判据同源）——
    ``PULSE_TEST_MODE`` 是给不经 pytest 的脚本用的**过渡声明**，
    语义是"我在测试"，**不是**"我有写生产权限"。

    ★主线第67批 T5 修复：框架主进程**不得**仅因 ``pytest`` 被 import
    （``"pytest" in sys.modules``）就被判为测试环境 —— 那会让 ReportBus
    无法写生产目录。框架进程仅在**显式** ``PULSE_TEST_MODE`` 或
    ``WRITE_GUARD_FORCE_ENV="test"`` 时才仍判为测试。
    """
    _force = None
    try:
        import config as _cfg67
        _force = getattr(_cfg67, "WRITE_GUARD_FORCE_ENV", None)
    except Exception:
        _force = None
    if _force == "test":
        return True
    if _force == "production":
        return False
    if is_framework_process() and not is_test_mode():
        return False
    return is_test_env() or is_test_mode()


def is_trusted_writer() -> bool:
    """是否允许写生产 ``data/`` 的进程（= **框架主进程**）。

    ★注意：pytest / ``PULSE_TEST_MODE`` 虽属"正当用途"，但按设计
    **不得写生产 data/**，故不在此列（见 ``reject_write`` 的①分支）。
    """
    return is_framework_process()


def writer_kind() -> str:
    """当前进程类型（用于拒写日志留痕）。"""
    try:
        if is_framework_process():
            return "framework"
        if is_test_env():
            return "pytest"
        if is_test_mode():
            return "test_mode"
        return "script"
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::writer_kind L190")
        return "unknown"


def strict_enabled() -> bool:
    """严格模式开关（``ENABLE_STRICT_WRITE_GUARD``，默认 True）。

    * True  → 非可信写入者默认**拒写**生产 ``data/``（第51批新行为）；
    * False → 退回第44批行为（仅 pytest 拒写），**零回归**。
    """
    try:
        return bool(_cfg("ENABLE_STRICT_WRITE_GUARD", True))
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::strict_enabled L202")
        return True


def reject_reason(path: Any, explicit: bool = False) -> str:
    """拒写原因（供日志留痕；未拒写时返回空串）。"""
    try:
        if explicit:
            return ""
        if not guard_enabled():
            return ""
        if not is_production_data_path(path):
            return ""
        if is_test_like_env():
            return "测试类环境(进程={}) 不得写生产 data/".format(writer_kind())
        if not strict_enabled():
            return ""
        if is_trusted_writer():
            return ""
        return ("非可信写入者(进程={}) 默认只读；"
                "如需写入请设 PULSE_FRAMEWORK=1 或 explicit=True".format(writer_kind()))
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::reject_reason L224")
        return ""


def is_production_data_path(path: Any) -> bool:
    """路径是否位于本项目 ``data/`` 生产数据区。

    ★无法判定时返回 ``True``（保守：宁可误拒也不污染生产）。
    """
    try:
        if not path:
            return False
        _p = os.path.abspath(str(path)).replace("\\", "/").lower()
        _root = project_root().replace("\\", "/").lower()
        return _p.startswith(_root + "/data/")
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::is_production_data_path L239")
        return True


def reject_write(path: Any, explicit: bool = False,
                 component: str = "") -> bool:
    """是否应当**拒写**。

    Args:
        path: 目标落盘路径。
        explicit: 调用方是否**显式注入**了目录（注入即视为隔离，放行）。
        component: 组件名（用于告警去重与日志）。
    """
    try:
        if explicit:
            return False
        if not guard_enabled():
            return False
        if not is_production_data_path(path):
            return False
        # ① 第44批判据（保持）+ ★第51批扩展：测试类环境（pytest /
        #    PULSE_TEST_MODE）一律不得写生产 data/。
        if is_test_like_env():
            return True
        # ② ★第51批 T3（P2-354）：非测试类的**普通脚本进程**默认只读，
        #    仅框架主进程可写；其余拒写（需 explicit=True 显式授权）。
        #    开关关闭 → 退回旧行为（放行），零回归。
        if not strict_enabled():
            return False
        return not is_framework_process()
    except Exception as e:
        silent_exc(e, where="nucleus.data.write_guard::reject_write L269")
        return False


def guard_write(path: Any, explicit: bool = False,
                component: str = "") -> bool:
    """写盘闸门：``True`` = 允许写；``False`` = 拒写（并留一次 WARNING 痕迹）。

    用法（组件侧）::

        if not guard_write(self._pool_file, explicit=self._explicit, component="ExperiencePool"):
            return False
    """
    try:
        if not reject_write(path, explicit=explicit, component=component):
            return True
        _name = component or "unknown"
        with _warn_lock:
            _first = _name not in _warned
            _warned.add(_name)
        if _first:
            try:
                from nucleus.logger import get_module_logger
                # ★主线第51批 T3：拒写必须留痕（进程名 + 路径 + 原因）
                get_module_logger("WriteGuard").warning(
                    "[写盘守卫] 拒写生产目录（组件={}, 进程={}, 路径={}, 原因={}）".format(_name, writer_kind(), path,
                       reject_reason(path, explicit) or "未判定"))
            except Exception as _wlog_e:
                # 日志不可用时退化为 stderr 一行（不得静默）
                sys.stderr.write("[WriteGuard] 拒写日志失败: {}: {}\n".format(type(_wlog_e).__name__, _wlog_e))
        return False
    except Exception:
        # 守卫自身故障 → 放行（绝不阻断正常功能）
        return True


def reset_warned() -> None:
    """清空告警去重集合（测试用）。"""
    with _warn_lock:
        _warned.clear()

# _m51_t3_wg_funcs
# _m51_t3_wg_reject
# _m51_t3_wg_log
# _m51_t3_wg_fix2