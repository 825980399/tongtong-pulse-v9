# -*- coding: utf-8 -*-
"""代码掌握率落盘（★411 批刀3）—— **唯一写入口**。

问题（410 后体检 + 本批 T0 实测）
--------------------------------
`data/context/code_progress.json` **全仓不存在**，`get_code_learning_stats()` 取数口
返回 `understood=None`，于是「代码掌握率 55.1%」其实是**历史快照**在别处被反复引用。

T0 核实推翻了任务书的一半判断：**写入逻辑早就存在** ——
`nucleus/mnemosyne/ContextSnapshot.save_code_progress()` 是原子写、默认目录
`data/context` 也正确。真正断的是 **`ContextSnapshot` 从来没有被实例化过**
（全仓 `ContextSnapshot(` 零个调用点，只有定义与自测块），
所以 `save_all()` 里那句 `save_code_progress(...)` **一次都没执行过**。

本模块的定位
------------
不做"第二套写入"，而是把落盘收成**一个入口**：

  * :func:`store` —— 原子写（`.tmp411` + `os.replace`），带 `ts` / `gen` / `source`；
  * :func:`load` —— 读回并判 `ok / stale / missing / broken`（**四态，不合并**）；
  * :func:`summary` —— 给快照/渲染用的紧凑读法。

`ContextSnapshot.save_code_progress` 改为**委派本模块**（保持其公开 API 不变），
于是"谁写"只有一个答案。

语义边界
--------
* `understood` / `total` 只接受**非负整数**；取不到就是 `None`，**绝不写 0 冒充读数**
  （铁律 276：0 与"没采到"必须可分）。
* 不回填历史 55.1%：本模块只记录**真实跑出来的下一轮**，`source="live"`；
  要看历史值请到原出处，别在这里造。
* `stale` 是**读数状态**不是错误：超过 `max_age_sec`（默认 24h）就如实报 stale，
  让渲染侧标注而不是继续当"当前值"。
"""

from __future__ import annotations

import io
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("CodeProgressStore")

#: 契约版本（产物里可见，便于日后判代）
_M411_CP_VERSION = "411.3"

#: 落盘相对路径（唯一口径；`ContextSnapshot` 的 base_dir 默认值也是这个目录）
PROGRESS_REL = os.path.join("data", "context", "code_progress.json")

#: 默认新鲜期（秒）—— 超过就算 stale（任务书要求 86400）
DEFAULT_MAX_AGE_SEC = 86400.0

#: 四态（不同事实不同措辞）
STATE_OK = "ok"
STATE_STALE = "stale"
STATE_MISSING = "missing"
STATE_BROKEN = "broken"

_DATA_DIR: str | None = None


def enabled() -> bool:
    """灰度开关 `ENABLE_M411_CODE_PROGRESS`；缺省 True（本刀是补生产者，默认生效）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M411_CODE_PROGRESS", True))
    except Exception as _e:
        silent_exc(_e, where="observability.code_progress_store:enabled", level="debug")
        return True


def project_root() -> str:
    """项目根 = 本文件上溯三层（`nucleus/observability/x.py`）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_root() -> str:
    """数据根（单测可用 :func:`set_data_dir` 重定向到隔离目录）。"""
    return _DATA_DIR or os.path.join(project_root(), "data")


def set_data_dir(path: str | None) -> None:
    """重定向**读与写**的根（两处同改 —— 409 批踩过只改一半的坑）。"""
    global _DATA_DIR
    _DATA_DIR = path


def progress_path() -> str:
    """产物绝对路径。"""
    return os.path.join(data_root(), "context", "code_progress.json")


def _as_count(value: Any) -> int | None:
    """非负整数归一；布尔/字符串/负数一律 None（**不折算成 0**）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        _n = int(value)
        return _n if _n >= 0 else None
    return None


def _atomic_write(path: str, payload: dict) -> tuple[bool, str]:
    """原子写 JSON（同目录 .tmp 再 replace）。返回 (ok, reason)。"""
    _dir = os.path.dirname(path)
    try:
        os.makedirs(_dir, exist_ok=True)
    except Exception as _e:
        silent_exc(_e, where="observability.code_progress_store:_atomic_write:mkdir", level="debug")
        return False, "mkdir_failed:%s" % type(_e).__name__
    _tmp = path + ".tmp411"
    try:
        _txt = json.dumps(payload, ensure_ascii=False, indent=2)
        with io.open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            _f.write(_txt)
        os.replace(_tmp, path)
        return True, ""
    except Exception as _e:
        silent_exc(_e, where="observability.code_progress_store:_atomic_write:write",
                   level="warning")
        try:
            if os.path.exists(_tmp):
                os.remove(_tmp)
        except Exception as _e2:
            silent_exc(_e2, where="observability.code_progress_store:_atomic_write:cleanup",
                       level="debug")
        return False, "write_failed:%s" % type(_e).__name__


def store(understood: Any, total: Any, gen: str = "",
          now: float | None = None, source: str = "live") -> dict[str, Any]:
    """写一次掌握率快照。

    Args:
        understood: 已理解方法数；取不到传 ``None``（**不传 0**）。
        total: 方法总数；取不到传 ``None``。
        gen: 生产者标识（如 ``"PulseCodeLearner._learn_own_code_structure"``）。
        now: 注入时钟（单测用）。
        source: ``live``（真实跑出来的） / ``probe``（工具造数）。

    Returns:
        ``{"ok": bool, "path": str, "reason": str, "written": dict|None}``
    """
    if not enabled():
        return {"ok": False, "path": progress_path(), "reason": "disabled", "written": None}
    _u = _as_count(understood)
    _t = _as_count(total)
    if _u is None and _t is None:
        # 两个都取不到 ⇒ 不落盘。写一个 0/0 的文件比不写更糟：它会被当成真实读数。
        return {"ok": False, "path": progress_path(),
                "reason": "no_counts(understood/total 都为 None，拒绝写 0)",
                "written": None}
    _ts = float(now if now is not None else time.time())
    _payload = {
        "version": _M411_CP_VERSION,
        "ts": round(_ts, 3),
        "gen": str(gen or ""),
        "source": str(source or "live"),
        "understood": _u,
        "total_methods": _t,
    }
    if _u is not None and _t is not None and _t > 0:
        _payload["mastery_pct"] = round(min(100.0, _u / _t * 100), 1)
    _ok, _why = _atomic_write(progress_path(), _payload)
    if _ok:
        _module_logger.debug("[411刀3] 掌握率落盘: understood=%s total=%s gen=%s",
                             _u, _t, gen)
    else:
        _module_logger.warning("[411刀3] 掌握率落盘失败: %s", _why)
    return {"ok": _ok, "path": progress_path(), "reason": _why,
            "written": _payload if _ok else None}


def load(max_age_sec: float = DEFAULT_MAX_AGE_SEC,
         now: float | None = None) -> dict[str, Any]:
    """读回掌握率并判四态。

    Returns:
        ``{"state": ok|stale|missing|broken, "understood": int|None,
           "total_methods": int|None, "mastery_pct": float|None,
           "ts": float|None, "age_sec": float|None, "gen": str,
           "source": str, "path": str, "reason": str}``

        ★`state="stale"` 时**数值照旧返回**（它确实存在过），只是调用方必须标注新鲜度；
        `missing` / `broken` 才把数值置 None。
    """
    _p = progress_path()
    _out: dict[str, Any] = {
        "state": STATE_MISSING, "understood": None, "total_methods": None,
        "mastery_pct": None, "ts": None, "age_sec": None, "gen": "",
        "source": "", "path": _p, "reason": "",
    }
    if not os.path.exists(_p):
        _out["reason"] = "file_not_found"
        return _out
    try:
        _raw = io.open(_p, encoding="utf-8").read()
        _d = json.loads(_raw)
    except Exception as _e:
        silent_exc(_e, where="observability.code_progress_store:load:read", level="debug")
        _out["state"] = STATE_BROKEN
        _out["reason"] = "read_or_parse_failed:%s" % type(_e).__name__
        return _out
    if not isinstance(_d, dict):
        _out["state"] = STATE_BROKEN
        _out["reason"] = "not_a_dict:%s" % type(_d).__name__
        return _out

    _u = _as_count(_d.get("understood"))
    _t = _as_count(_d.get("total_methods"))
    try:
        _ts = float(_d.get("ts") or 0.0)
    except (TypeError, ValueError) as _e:
        # ★不是"没有时间戳"，是"时间戳不是数" —— 两种事实都要留痕
        silent_exc(_e, where="observability.code_progress_store:load:ts", level="debug")
        _ts = 0.0
    if _ts <= 0:
        # 老格式（ContextSnapshot 直写、没有 ts）⇒ 用文件 mtime 兜底，并标记来源
        try:
            _ts = float(os.path.getmtime(_p))
            _out["reason"] = "ts_from_mtime"
        except Exception as _e:
            silent_exc(_e, where="observability.code_progress_store:load:mtime", level="debug")
            _ts = 0.0

    _now = float(now if now is not None else time.time())
    _age = round(max(0.0, _now - _ts), 1) if _ts > 0 else None
    _out.update({
        "understood": _u, "total_methods": _t,
        "mastery_pct": (_d.get("mastery_pct")
                        if isinstance(_d.get("mastery_pct"), (int, float)) else None),
        "ts": round(_ts, 3) if _ts > 0 else None,
        "age_sec": _age,
        "gen": str(_d.get("gen") or ""),
        "source": str(_d.get("source") or ""),
    })
    if _age is not None and _age > float(max_age_sec):
        _out["state"] = STATE_STALE
        if not _out["reason"]:
            _out["reason"] = "age_sec>max_age_sec(%.0fs)" % float(max_age_sec)
        return _out
    _out["state"] = STATE_OK
    return _out


def summary(max_age_sec: float = DEFAULT_MAX_AGE_SEC,
            now: float | None = None) -> dict[str, Any]:
    """紧凑读法（给 health_snapshot / 渲染侧；不含 path 等冗长字段）。"""
    _r = load(max_age_sec=max_age_sec, now=now)
    return {
        "state": _r["state"],
        "understood": _r["understood"],
        "total_methods": _r["total_methods"],
        "mastery_pct": _r["mastery_pct"],
        "age_sec": _r["age_sec"],
        "source": _r["source"] or ("file" if _r["state"] != STATE_MISSING else ""),
        "reason": _r["reason"],
    }


def version() -> str:
    """契约版本。"""
    return _M411_CP_VERSION
