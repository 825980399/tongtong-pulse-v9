# -*- coding: utf-8 -*-
"""★491-3 / 491-4：LLM 渠道状态持久化 + 每月 15 号最小探测复活。

纯治理层，不参与渠道调用本身：
  - 491-3：按 HTTP 错误分状态落盘 data/monitor/llm_channel_state.json，
    选路时跳过 exhausted/dead/未到期 rate_limited；
  - 491-4：每月 15 号（本地时间）对 exhausted 的月度免费渠道发
    max_tokens=1 的最小探测，成功复活、连续 3 月失败标 dead。

纪律：每刀独立开关，关掉首行即返回，不读写文件、不发探针、不改选路。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("M491ChannelState")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STATE_PATH = os.path.join(_ROOT, "data", "monitor", "llm_channel_state.json")

_LOCK = threading.RLock()
_STATES: dict[str, dict] = {}
_LAST_RESET_MONTH = ""
_LOADED = False

#: 允许的状态（与任务书一致）
_OK = "ok"
_EXHAUSTED = "exhausted"
_RATE_LIMITED = "rate_limited"
_AUTH_FAILED = "auth_failed"
_DEAD = "dead"


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::_cfg", level="debug")
        return default


def persist_on() -> bool:
    return bool(_cfg("ENABLE_M491_CHANNEL_STATE_PERSIST", False))


def revive_on() -> bool:
    return bool(_cfg("ENABLE_M491_MONTHLY_REVIVE", False))


def _path() -> str:
    return str(_cfg("M491_CHANNEL_STATE_PATH", "") or _STATE_PATH)


def reset_for_tests() -> None:
    """清空进程内状态（测试隔离；不删磁盘文件）。"""
    global _LAST_RESET_MONTH, _LOADED
    with _LOCK:
        _STATES.clear()
        _LAST_RESET_MONTH = ""
        _LOADED = False


def _load_locked() -> None:
    global _LAST_RESET_MONTH, _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        if os.path.exists(_path()):
            with open(_path(), "r", encoding="utf-8", errors="replace") as _f:
                _d = json.load(_f)
            if isinstance(_d, dict):
                _rows = _d.get("channels") or {}
                if isinstance(_rows, dict):
                    for _k, _v in _rows.items():
                        if isinstance(_v, dict):
                            _STATES[str(_k)] = dict(_v)
                _LAST_RESET_MONTH = str(_d.get("last_reset_month") or "")
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::_load_locked", level="debug")


def _save_locked() -> bool:
    """原子写（tmp + os.replace），写前经 guard_write 守卫。"""
    _p = _path()
    try:
        from nucleus.data.write_guard import guard_write
        if not guard_write(_p, explicit=False, component="M491ChannelState"):
            return False
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::_save_locked:guard", level="debug")
        return False
    _snap = {"channels": {k: dict(v) for k, v in _STATES.items()},
             "last_reset_month": _LAST_RESET_MONTH,
             "updated_at": round(time.time(), 3)}
    try:
        os.makedirs(os.path.dirname(_p) or ".", exist_ok=True)
        _tmp = _p + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(_snap, _f, ensure_ascii=False, indent=2)
            _f.flush()
        os.replace(_tmp, _p)
        return True
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::_save_locked:write", level="debug")
        return False


def _row_locked(name: str) -> dict:
    _r = _STATES.get(name)
    if _r is None:
        _r = {"state": _OK, "fail_count": 0, "next_revive_at": 0.0,
              "probe_fail_months": 0, "last_error": ""}
        _STATES[name] = _r
    return _r


def _next_month_reset_ts(now_ts: float) -> float:
    """★492.5-5：exhausted 的下次复活时间戳（纯诊断，不参与月度复活门控）。

    取 config.M491_MONTHLY_RESET_DAY（默认15）所在"下一次月内该日"零点 + 随机0-59分，
    与 491-4 月度复活口径一致，便于在账本里直观看到复活档期。
    """
    import datetime as _dt
    import random as _rnd
    _day = int(_cfg("M491_MONTHLY_RESET_DAY", 15))
    _t = _dt.datetime.fromtimestamp(float(now_ts))
    if _t.day >= _day:
        _nm = _t.month + 1
        if _nm > 12:
            _y, _m = _t.year + 1, 1
        else:
            _y, _m = _t.year, _nm
    else:
        _y, _m = _t.year, _t.month
    _target = _dt.datetime(_y, _m, _day, 0, 0, 0)
    _target += _dt.timedelta(minutes=_rnd.randint(0, 59))
    return _target.timestamp()


def mark_http_error(name: str, status_code: int,
                    error_type: str = "") -> dict | None:
    """491-3：按 HTTP 状态码记账。关开关时首行返回（逐字回改前）。

    - 403/quota_exhausted 连续 M491_HTTP_EXHAUST_THRESHOLD 次 → exhausted；
    - 429 → rate_limited，M491_RATE_LIMIT_REVIVE_SEC 后自动恢复；
    - 401/402 → auth_failed（鉴权/欠费，修法与额度耗尽不同，不自动恢复）。
    """
    if not persist_on():
        return None
    try:
        with _LOCK:
            _load_locked()
            _r = _row_locked(str(name))
            _now = time.time()
            _r["last_error"] = str(error_type or status_code)
            if status_code == 429:
                _r["state"] = _RATE_LIMITED
                _r["next_revive_at"] = _now + float(
                    _cfg("M491_RATE_LIMIT_REVIVE_SEC", 60.0))
            elif status_code in (401, 402):
                _r["state"] = _AUTH_FAILED
                _r["next_revive_at"] = 0.0
            elif status_code == 403:
                _r["fail_count"] = int(_r.get("fail_count", 0)) + 1
                if _r["fail_count"] >= int(_cfg("M491_HTTP_EXHAUST_THRESHOLD", 3)):
                    _r["state"] = _EXHAUSTED
                    # ★492.5-5：诊断用写"下月15号+随机分"（默认开）；关 ⇒ 回退 0.0 哨兵
                    _r["next_revive_at"] = (
                        _next_month_reset_ts(_now)
                        if bool(_cfg("ENABLE_M4925_CHANNEL_NEXT_REVIVE", True))
                        else 0.0)
            _save_locked()
            return dict(_r)
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::mark_http_error", level="debug")
        return None


def mark_success(name: str) -> None:
    """调用成功 → 恢复 ok 并清零计数（不置顶、不动 priority）。"""
    if not persist_on():
        return
    try:
        with _LOCK:
            _load_locked()
            _r = _row_locked(str(name))
            _r.update({"state": _OK, "fail_count": 0, "next_revive_at": 0.0,
                       "probe_fail_months": 0, "last_error": ""})
            _save_locked()
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::mark_success", level="debug")


def state_of(name: str) -> str:
    """读状态；rate_limited 到期自动视为 ok（内存 + 落盘）。"""
    if not persist_on():
        return _OK
    try:
        with _LOCK:
            _load_locked()
            _r = _STATES.get(str(name))
            if not _r:
                return _OK
            _st = str(_r.get("state") or _OK)
            if _st == _RATE_LIMITED and time.time() >= float(
                    _r.get("next_revive_at", 0.0)):
                _r.update({"state": _OK, "fail_count": 0, "next_revive_at": 0.0})
                _save_locked()
                return _OK
            return _st
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::state_of", level="debug")
        return _OK


def filter_candidates(candidates: list) -> list:
    """491-3 选路接线：跳过 exhausted/dead/auth_failed/未到期 rate_limited。

    关开关时**原样返回**（逐字回改前），且不读状态文件。
    """
    if not persist_on():
        return candidates
    try:
        _out = []
        for _c in candidates or []:
            _name = str(_c.get("name", "")) if isinstance(_c, dict) else ""
            if _name and state_of(_name) in (
                    _EXHAUSTED, _DEAD, _AUTH_FAILED, _RATE_LIMITED):
                continue
            _out.append(_c)
        return _out
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::filter_candidates", level="debug")
        return candidates


def snapshot() -> dict:
    """只读快照（诊断 / 单测）。"""
    if not persist_on():
        return {}
    with _LOCK:
        _load_locked()
        return {"channels": {k: dict(v) for k, v in _STATES.items()},
                "last_reset_month": _LAST_RESET_MONTH}


# ---------------------------------------------------------------------------
# 491-4：每月 15 号最小探测复活
# ---------------------------------------------------------------------------
def _monthly_channels() -> list[dict]:
    """取 quota_type == monthly_free 的渠道配置（只读，不改 enabled/priority）。"""
    try:
        import config
        _pool = (getattr(config, "REMOTE_API_CHANNELS", None) or {}).get(
            "default_channels") or []
        return [dict(_c) for _c in _pool
                if isinstance(_c, dict)
                and str(_c.get("quota_type", "")) == "monthly_free"]
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::_monthly_channels", level="debug")
        return []


def _probe(channel: dict) -> bool:
    """最小探测：复用 channel_client.call_channel，max_tokens=1。

    不复用 PulseLung._call_channel（其字段策略会覆盖 max_tokens，且拿不到状态码）。
    """
    try:
        from nucleus.reasoning.channel_client import call_channel
        _r = call_channel(channel, "ping", max_tokens=1, timeout=10,
                          is_probe=True)
        return bool(isinstance(_r, dict) and _r.get("ok"))
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::_probe", level="debug")
        return False


def tick_monthly_revive(now=None, probe=None, channels=None) -> dict | None:
    """491-4：本地日期 ≥ M491_MONTHLY_RESET_DAY 时，对 exhausted 的月度免费渠道探测。

    幂等：last_reset_month 落盘，同月重复启动不重复跑。
    成功 → ok（回到配置原 priority，绝不置顶）；失败 → 保持 exhausted，
    连续 M491_MONTHLY_DEAD_AFTER 个月失败 → dead + WARNING。
    `now` 用 naive datetime 注入（测试用），`probe` 可注入假探测函数。
    """
    if not revive_on():
        return None
    try:
        import datetime as _dt
        global _LAST_RESET_MONTH
        _now = now if now is not None else _dt.datetime.now()
        _day = int(_cfg("M491_MONTHLY_RESET_DAY", 15))
        _month = _now.strftime("%Y-%m")
        if _now.day < _day:
            return None
        with _LOCK:
            _load_locked()
            if _LAST_RESET_MONTH == _month:
                return {"ran": False, "reason": "already_this_month",
                        "month": _month}
            _targets = [(_n, _r) for _n, _r in list(_STATES.items())
                        if str(_r.get("state")) == _EXHAUSTED]
            _LAST_RESET_MONTH = _month
            _pool = channels if channels is not None else _monthly_channels()
            _by_name = {str(_c.get("name")): _c for _c in _pool}
            _res = {"ran": True, "month": _month, "checked": len(_targets),
                    "revived": [], "still": [], "dead": []}
            for _name, _row in _targets:
                _ch = _by_name.get(_name)
                if not _ch:
                    _res["still"].append(_name)
                    continue
                _ok = bool((probe or _probe)(_ch))
                if _ok:
                    _row.update({"state": _OK, "fail_count": 0,
                                 "next_revive_at": 0.0,
                                 "probe_fail_months": 0, "last_error": ""})
                    _res["revived"].append(_name)
                else:
                    _months = int(_row.get("probe_fail_months", 0)) + 1
                    _row["probe_fail_months"] = _months
                    if _months >= int(_cfg("M491_MONTHLY_DEAD_AFTER", 3)):
                        _row["state"] = _DEAD
                        _res["dead"].append(_name)
                        _module_logger.warning(
                            "[491-4] 渠道 %s 连续 %d 月探测失败，标 dead（模型可能已下线）",
                            _name, _months)
                    else:
                        _res["still"].append(_name)
            _save_locked()
            return _res
    except Exception as _e:
        silent_exc(_e, where="m491_channel_state::tick_monthly_revive", level="debug")
        return None
