"""★487-3 / 486 可观测性：对话链 7 跳 cid 串联埋点。

新增 ``data/monitor/dialog_latency_YYYYMMDD.jsonl``，每行记录：
    {cid, event, ts, layer, node, phase, is_synthetic}

由 ``ENABLE_M486_DIALOG_TRACE`` 门控；开关关 ⇒ ``record()`` 直接 no-op（不打任何埋点）。
风格对齐 ``nucleus.dialogue_process_trace``：用 ``data.write_guard`` 保护写、失败静默。
"""
from __future__ import annotations

import os
import time
import json
from typing import Any

try:
    from nucleus._silent_except import silent_exc
except Exception:  # pragma: no cover - 极端导入失败时退化为空
    def silent_exc(*_a, **_k):  # type: ignore
        return None


def _enabled() -> bool:
    """门控：开关开才打埋点。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M486_DIALOG_TRACE", False))
    except Exception as _e:
        silent_exc(_e, where="dialog_latency_trace::_enabled", level="debug")
        return False


def _root() -> str:
    """仓库根（本文件在 nucleus/ 下 → 上一级）。"""
    _here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(_here)


def out_path(now: float | None = None) -> str:
    """当天的 jsonl 路径（按本地日期切分，跨天自动换文件）。"""
    _t = time.localtime(float(now if now is not None else time.time()))
    _day = time.strftime("%Y%m%d", _t)
    return os.path.join(_root(), "data", "monitor", f"dialog_latency_{_day}.jsonl")


def record(cid: str, event: str, layer: str, node: str, phase: str,
           is_synthetic: bool = False, now: float | None = None) -> "dict | None":
    """记录一条对话链埋点。

    Returns:
        落盘的行 dict（供单测/活体自检）；开关关、cid 为空、写失败 ⇒ None（无副作用）。
    """
    if not _enabled():
        return None
    if not cid:
        return None
    _row = {
        "cid": str(cid),
        "event": str(event),
        "ts": round(now if now is not None else time.time(), 3),
        "layer": str(layer),
        "node": str(node),
        "phase": str(phase),
        "is_synthetic": bool(is_synthetic),
    }
    _path = out_path(_row["ts"])
    try:
        from nucleus.data.write_guard import guard_write as _gw
        if not _gw(_path, explicit=False, component="DialogLatencyTrace"):
            return _row
    except Exception as _e:
        silent_exc(_e, where="dialog_latency_trace::record:guard", level="debug")
    try:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(_path, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps(_row, ensure_ascii=False) + "\n")
    except Exception as _e:
        silent_exc(_e, where="dialog_latency_trace::record:write", level="debug")
    return _row
