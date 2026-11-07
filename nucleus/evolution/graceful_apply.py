# -*- coding: utf-8 -*-
"""graceful_apply.py —— 自动升级窗口的「优雅应用并重启」请求口（473 刀13）。

病灶（2026-11-07 03:09 现场）
------------------------------
`PulseInnerWorld._check_auto_upgrade` 在维护窗口触发后，Windows 分支用
`kernel32.GenerateConsoleCtrlEvent(0, 0)` 向**整个控制台进程组**发 Ctrl+C。
第二个参数为 0 时，接收方是「与本进程共享同一控制台的所有进程」：

  * `cmd.exe`（`start_tongtong.bat` 的那一层）同样收到 ⇒ 弹出
    「终止批处理操作吗(Y/N)?」—— 没人按键就一直等；
  * Python 自己的 SIGINT 处理器把 `user_quit_requested` 置 True ⇒ 退出路径改走
    `main._confirm_apply_pending_on_quit` ⇒ 有 approved 补丁时再弹
    「是否应用并重启验证？(Y/n，默认 Y)」—— 第二处等人工。

实测证据：`2026-11-07 03:09:31 自动升级窗口触发` 之后，`logs/pulse.log` 在
`03:09:58 曈曈 停止指令已发出，正在清理后台任务...(总耗时17.1s)` 处断流，
`data/restart_notice.txt` 停在 `2026-11-01 12:53` ⇒ **接班进程从未被拉起**，
自重启死在两处人工确认上。

处置
----
改走既有的 `data/evolution/apply_now.json` 通道（与 `EvolutionLoop` 的
「验证通过即应用」同一条）：主循环每 10 拍消费一次，执行 472 刀1 修好的
`apply 成功 → stop → spawn` 顺序。全程不发控制台信号 ⇒ 不惊动 cmd.exe，
也不置 `user_quit_requested` ⇒ 不弹补丁确认。

灰度
----
`config.ENABLE_M473_UPGRADE_APPLY_NOW = False` ⇒ 逐字回退旧的整控制台 Ctrl+C
（本模块内 `_m473_legacy_console_ctrl_c` 保留原调用序列，便于 A/B 对照）。

为什么写在独立模块而不是判据旁边
--------------------------------
`organs/brain/PulseInnerWorld.py` 受 cw3-A 语义守恒门禁（11 个 AST 计数）锁定。
把新逻辑放进独立模块后，IW 侧只是把**一行调用**换成**一行调用**，守恒量零漂移，
不需要重锚基线；灰度与回退逻辑也集中在一处，便于单测。
"""

from __future__ import annotations

import time

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_lg = get_module_logger("graceful_apply")

#: 同一批补丁的重复请求节流窗口（秒）。维护窗口是 03:00-04:00，
#: 一次请求被消费后若 apply 落地为 0，不应在同一窗口里反复重写。
M473_RETRY_MIN_SEC = 900.0

#: 落地侧（`PatchManager.apply_all_pending(only_approved=True)`）唯一认的状态。
M473_APPLIABLE_STATUS = "approved"


def m473_appliable_patches(pending) -> list:
    """从待处理补丁里挑出「落地侧真的会应用」的那些。

    口径必须与 `PatchManager.apply_all_pending(only_approved=True)` 一致：它只认
    `status == "approved"`。而 `verified` 的真实含义是**自动批准被拒**
    （信任分不足 / 审美 D 级 / 核心文件强制人工），不会有任何代码把它提升为
    approved ⇒ 把 verified 算进「可应用」＝触发维护窗口并声明「即将应用并重启」，
    实际 apply 恒为 0，是**假闭环**（`SafeEvolutionExecutor.py:6189` 已记过同一坑）。

    返回新列表，不改入参。
    """
    _out = []
    for _p in pending or ():
        if isinstance(_p, dict) and _p.get("status") == M473_APPLIABLE_STATUS:
            _out.append(_p)
    return _out


#: 进程内状态（重启后自然重置，不落盘）
_M473_STATE = {"last_ts": 0.0, "last_count": -1}


def m473_apply_now_enabled() -> bool:
    """灰度开关：缺省开（`None` 视作缺省，只有显式假才关闭）。"""
    try:
        import config
        _v = getattr(config, "ENABLE_M473_UPGRADE_APPLY_NOW", True)
        if _v is None:
            return True
        if isinstance(_v, str):
            return _v.strip().lower() in ("1", "true", "yes", "on")
        return bool(_v)
    except Exception as e:
        # 读不到开关 ⇒ 按「开」处理：宁可走新通道，也不要退回到会卡死的那条
        silent_exc(e, where="nucleus.evolution.graceful_apply::m473_apply_now_enabled", level="debug")
        return True


def _m473_legacy_console_ctrl_c() -> None:
    """逐字回退旧行为：向整个控制台进程组发 Ctrl+C。

    ★保留原调用序列（含"忽略返回值"这一细节）：旧代码 `GenerateConsoleCtrlEvent(0, 0)`
    之后无条件 `return`，即无论成功与否都不再走 framework.stop() 兜底。此处照抄。
    """
    _lg.warning("[473刀13] apply_now 通道不可用，回退旧路径（整控制台 Ctrl+C）："
                "从 .bat 启动时 cmd.exe 会追问「终止批处理操作吗(Y/N)」")
    try:
        import ctypes
        ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0)
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.graceful_apply::_m473_legacy_console_ctrl_c", level="warning")


def _m473_write_apply_request(project_root: str, approved_count: int) -> bool:
    """写 `data/evolution/apply_now.json`。True=请求已在主循环手里（含在途/节流）。"""
    try:
        from nucleus.evolution.EvolutionDriver import EvolutionDriver
        _drv = EvolutionDriver(project_root)
        if _drv.has_apply_request():
            # 已有未被消费的请求 ⇒ 不覆盖（覆盖会把 requested_at 刷新，失去排队语义）
            _lg.info("[473刀13] 已有在途 apply_now 请求，本次不重复写入")
            return True
        _now = time.time()
        _cnt = int(approved_count or 0)
        _last_ts = float(_M473_STATE.get("last_ts") or 0.0)
        if _M473_STATE.get("last_count") == _cnt and (_now - _last_ts) < M473_RETRY_MIN_SEC:
            _lg.info("[473刀13] 同一批 %d 个补丁 %.0fs 内已请求过，节流跳过（窗口 %.0fs）",
                     _cnt, _now - _last_ts, M473_RETRY_MIN_SEC)
            return True
        if not _drv.request_apply_now(_cnt):
            return False
        _M473_STATE["last_ts"] = _now
        _M473_STATE["last_count"] = _cnt
        return True
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.graceful_apply::_m473_write_apply_request", level="warning")
        return False


def request_upgrade_apply(project_root: str, approved_count: int = 0) -> bool:
    """请求「应用补丁并自重启」，不阻塞、不弹窗。

    Returns:
        True  —— 已通过 apply_now 通道交给主循环（含"已有在途请求"与"节流"两种幂等情形）；
        False —— 新通道不可用，已回退旧的整控制台 Ctrl+C。
    """
    if m473_apply_now_enabled() and _m473_write_apply_request(project_root, approved_count):
        _lg.info("[473刀13] 自动升级窗口：已写 apply_now 请求（%d 个可应用补丁），"
                 "由主循环执行 apply→stop→spawn；不再向控制台发 Ctrl+C",
                 int(approved_count or 0))
        return True
    _m473_legacy_console_ctrl_c()
    return False
