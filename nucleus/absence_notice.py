# -*- coding: utf-8 -*-
"""缺席提示器：同一路径的「文件不存在」只在**首次**或**状态变化**时留痕。

## 为什么要这个（票 T-350开闸回载缺口-1 / 烛微 362 候选 E10）

心跳每拍检查两份台账是否存在（`data/knowledge/cold/reload_stats.json`、
`data/reports/memory/live_gate_state.json`）。"文件不存在"是**确定性事实**——
没跑过回载就不会有统计、没做出过决策就不会有台账——但原实现**每拍打一条 DEBUG**
⇒ 实测约 **2 条/拍**、10 族合计约 1000 行/h，其中这两条是纯重复。

## 为什么不干脆删掉日志

因为"不存在"**是有信息量的**：首次不存在说明机制还没跑起来；
从「存在」变「不存在」說明台账**被删了或被清了**（这才是事故信号）。
所以不做"降级/静默"，而是**按状态变化留痕**（符合铁律 156：禁静默降级）：

| 场景 | 是否留痕 |
|---|---|
| 首次发现不存在 | ✅ 留痕（说明机制未启动） |
| 连续多拍仍不存在 | ❌ 不打（同一事实重复） |
| 曾存在 → 现在不存在 | ✅ 留痕（**台账消失＝事故信号**） |
| 不存在 → 出现 | ✅ 留痕（状态变化） |

## 边界

- 只记"状态"，不读文件内容、不做 IO；
- 进程级内存态（重启即重置），不落盘 —— 它服务的是"本进程日志别刷屏"；
- 线程安全（心跳与自检可能不同线程）。

## 退出

`absence_stats()` 给出各路径的当前状态与累计变化次数，供自检/诊断读取。
"""
from __future__ import annotations

import threading

_LOCK = threading.RLock()

#: path -> True(上次见到存在) / False(上次见到不存在)；缺键＝从未见过
_STATE: dict = {}
#: path -> {"absent_first": n, "absent_repeat": n, "absent_after_present": n,
#:          "present_first": n, "present_after_absent": n}
_COUNTS: dict = {}


def _bump(path: str, key: str) -> None:
    _d = _COUNTS.setdefault(path, {})
    _d[key] = int(_d.get(key, 0)) + 1


def note_absent(path: str) -> bool:
    """登记一次「文件不存在」。

    Returns:
        True = **本次应留痕**（首次不存在，或曾存在现在又不见了）；
        False = 同一事实重复，不该再打日志。
    """
    _p = str(path or "")
    with _LOCK:
        _prev = _STATE.get(_p)
        _STATE[_p] = False
        if _prev is False:
            _bump(_p, "absent_repeat")
            return False
        if _prev is True:
            _bump(_p, "absent_after_present")
            return True
        _bump(_p, "absent_first")
        return True


def note_present(path: str) -> bool:
    """登记一次「文件存在」。

    Returns:
        True = 本次应留痕（从不存在 → 存在，或首次就存在）；
        False = 一直存在，不必重复开会话级噪声。
    """
    _p = str(path or "")
    with _LOCK:
        _prev = _STATE.get(_p)
        _STATE[_p] = True
        if _prev is True:
            return False
        if _prev is False:
            _bump(_p, "present_after_absent")
            return True
        _bump(_p, "present_first")
        return True


def state_of(path: str):
    """当前已知状态：True / False / None（未知）。"""
    with _LOCK:
        return _STATE.get(str(path or ""))


def absence_stats() -> dict:
    """只读快照：{path: {state, counts}}，供自检/诊断出口读取。"""
    with _LOCK:
        return {_p: {"state": _STATE.get(_p), "counts": dict(_COUNTS.get(_p, {}))}
                for _p in set(_STATE) | set(_COUNTS)}


def reset() -> None:
    """清空状态（**仅供测试**；生产运行期不要调）。"""
    with _LOCK:
        _STATE.clear()
        _COUNTS.clear()


__all__ = ["note_absent", "note_present", "state_of", "absence_stats", "reset"]
