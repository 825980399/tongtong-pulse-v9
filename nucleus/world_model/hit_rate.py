# -*- coding: utf-8 -*-
"""hit_rate.py —— 第410批刀3：预测命中率日报表 + 失配告警通路

契约：410.3

要解决的两个黑洞：
    1. 「预测准不准」没人算 —— StreamMiner 的 `_pattern_quality` 只有累计
       predictions/hits，没有时间窗、没有分母口径，且 miss 从不入账
       （生产实测 432/408＝94.4%，其中眼睛帧 32/32＝100%，因为 60s 容差）。
    2. 「预测错了」没人管 —— 告警写进 InsightBoard 后 query_count 永远是 0。

本模块的三条通路（都不新建调度器）：
    * 分钟级：`sample()` 挂在 TimeCore 现有 60s 广播上（time.tick 条件订阅），
      每窗读一次 StreamMiner 的累计计数、取差分、算窗口命中率、推进连击。
    * 日级：`write_report()` 挂在 night_orchestration 现有 04:00 段，
      落 `data/world_model/hit_rate_YYYYMMDD.json`（滚动 24h + 分类 + 总计）。
    * 快照级：`snapshot_for_health()` 由 PulseMetricsCollector 每次心跳写入
      `data/monitor/health_snapshot.json` 的 `world_model` 字段 —— 这是「不靠人
      去看黑板也能看见」的那一路（InsightBoard.query_count=0 的教训）。

告警规则（任务书刀3）：
    连续 `M410_HIT_RATE_ALERT_STREAK`(2) 个采样窗 < `M410_HIT_RATE_ALERT_BELOW`(0.5)
        ⇒ 发 InsightBoard 条目 + 计入 health_snapshot.mismatches
    连续 `M410_HIT_RATE_EVICT_STREAK`(5) 个采样窗 < `M410_HIT_RATE_EVICT_BELOW`(0.3)
        ⇒ 淘汰该模式的周期记录（`StreamMiner.evict_pattern`），并在报表里留痕

口径纪律（沿用 409/410 批）：
    * 命中率分母只算**有判定**的样本（hit + miss），`no_coverage` 单列。
      把查不到当 miss 是拿沉默当反证，当 hit 是拿愿望当证据。
    * 样本不足 `MIN_WINDOW_SAMPLES` 的窗口不推进连击（不判），也不计入分母。
    * 计数器重启归零时识别为 reset 并跳过该窗（否则一次重启就是一个假 miss 窗）。
"""

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc

#: 契约版本
_M410_HITRATE_VERSION = "410.3"

#: 状态文件（滚动聚合 + 连击 + 告警流水）
STATE_REL = os.path.join("data", "world_model", "hit_rate_state.json")

#: 日报表目录（文件名 hit_rate_YYYYMMDD.json）
REPORT_DIR_REL = os.path.join("data", "world_model")

#: 滚动窗口长度（24 小时）
ROLLING_SEC = 86400.0

#: 一个采样窗至少要有几个「有判定」样本才参与连击判定
MIN_WINDOW_SAMPLES = 3

#: 告警 / 淘汰默认阈值（config 里同名常量优先）
ALERT_RATE_DEFAULT = 0.5
EVICT_RATE_DEFAULT = 0.3
ALERT_STREAK_DEFAULT = 2
EVICT_STREAK_DEFAULT = 5

#: InsightBoard 条目类型（未登记 TTL 的类型按 3600s，见 InsightBoard._ttl_config）
INSIGHT_TYPE = "prediction_mismatch"

#: 测试/工具用的落盘根重定向
_DATA_DIR: str | None = None

#: 状态文件 mtime 缓存（health_snapshot 每 5s 读一次，不重复解析）
_CACHE: dict[str, Any] = {"mtime": 0.0, "state": {}}


# --------------------------------------------------------------------- 开关/配置


def enabled() -> bool:
    """灰度开关：关闭时 sample/write_report 不落盘、不告警。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M410_HIT_RATE_ALERT", True))
    except Exception as _exc:
        silent_exc(_exc, where="world_model.hit_rate.enabled", level="debug")
        return True


def _cfg(key: str, default: Any) -> Any:
    try:
        import config as _c
        return getattr(_c, key, default)
    except Exception as _exc:
        silent_exc(_exc, where="world_model.hit_rate._cfg:%s" % key, level="debug")
        return default


def alert_rate() -> float:
    return float(_cfg("M410_HIT_RATE_ALERT_BELOW", ALERT_RATE_DEFAULT))


def evict_rate() -> float:
    return float(_cfg("M410_HIT_RATE_EVICT_BELOW", EVICT_RATE_DEFAULT))


def alert_streak() -> int:
    return int(_cfg("M410_HIT_RATE_ALERT_STREAK", ALERT_STREAK_DEFAULT))


def evict_streak() -> int:
    return int(_cfg("M410_HIT_RATE_EVICT_STREAK", EVICT_STREAK_DEFAULT))


# --------------------------------------------------------------------- 路径


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_root() -> str:
    return _DATA_DIR or project_root()


def set_data_dir(path: str | None) -> None:
    """重定向数据根（测试/工具用）。传 None 复原。"""
    global _DATA_DIR
    _DATA_DIR = path
    _CACHE["mtime"] = 0.0
    _CACHE["state"] = {}


def state_path() -> str:
    return os.path.join(data_root(), STATE_REL)


def report_dir() -> str:
    return os.path.join(data_root(), REPORT_DIR_REL)


def report_path(now: float | None = None) -> str:
    _now = float(now if now is not None else time.time())
    return os.path.join(report_dir(), "hit_rate_%s.json" % time.strftime("%Y%m%d", time.localtime(_now)))


# --------------------------------------------------------------------- 状态读写


def _empty_state() -> dict:
    return {
        "version": _M410_HITRATE_VERSION,
        "updated_at": 0.0,
        "last": {},        # event_type -> 上次读到的累计计数（取差分用）
        "hours": {},       # event_type -> [[hour_start, hit, miss, no_cov], ...]
        "streak": {},      # event_type -> {"low": n, "crit": n, "evicted": bool}
        "alerts": [],      # 告警/淘汰流水（审计用，最多 200 条）
        "windows": 0,      # 已采样窗数
    }


def load_state() -> dict:
    """读状态文件。缺失/损坏 → 空状态（不抛异常，也不猜历史）。"""
    _p = state_path()
    try:
        _mt = os.path.getmtime(_p)
    except OSError as _exc:
        silent_exc(_exc, where="world_model.hit_rate.load_state:stat", level="debug")
        return _empty_state()
    if _CACHE["state"] and abs(_CACHE["mtime"] - _mt) < 1e-6:
        return dict(_CACHE["state"])
    _st = _empty_state()
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
        if isinstance(_d, dict):
            _st.update({k: _d.get(k, _st[k]) for k in _st})
    except (OSError, ValueError) as _exc:
        silent_exc(_exc, where="world_model.hit_rate.load_state", level="warning")
        return _empty_state()
    _CACHE["state"] = dict(_st)
    _CACHE["mtime"] = _mt
    return _st


def save_state(state: dict) -> str | None:
    """原子写状态文件。返回路径；失败 None（不阻断调用方）。"""
    _p = state_path()
    _st = dict(state or {})
    _st["version"] = _M410_HITRATE_VERSION
    _st["updated_at"] = time.time()
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        _tmp = _p + ".tmp410"
        with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_st, _f, ensure_ascii=False, indent=2)
            _f.write("\n")
        os.replace(_tmp, _p)
        _CACHE["state"] = dict(_st)
        try:
            _CACHE["mtime"] = os.path.getmtime(_p)
        except OSError as _exc:
            silent_exc(_exc, where="world_model.hit_rate.save_state:mtime", level="debug")
            _CACHE["mtime"] = 0.0
        return _p
    except OSError as _exc:
        silent_exc(_exc, where="world_model.hit_rate.save_state", level="warning")
        return None


# --------------------------------------------------------------------- 读数


def _miner():
    """取 StreamMiner 单例（调用方也可显式传入替身）。"""
    try:
        from nucleus.genesis.StreamMiner import get_stream_miner
        return get_stream_miner()
    except Exception as _exc:
        silent_exc(_exc, where="world_model.hit_rate._miner", level="warning")
        return None


def read_counters(miner=None) -> dict:
    """读累计计数：{event_type: {"hit","miss","no_coverage"}}。

    取不到（miner 未就绪/方法缺失）返回空 dict —— 空 = 本轮不采样，
    绝不写 0（写 0 会把「没测到」变成「测到了 0 命中」）。
    """
    _m = miner if miner is not None else _miner()
    if _m is None:
        return {}
    try:
        _st = _m.get_typed_prediction_stats()
    except Exception as _exc:
        silent_exc(_exc, where="world_model.hit_rate.read_counters", level="warning")
        return {}
    _by = (_st or {}).get("by_event") or {}
    _out = {}
    for _et, _q in _by.items():
        _out[str(_et)] = {
            "hit": int(_q.get("hit", 0) or 0),
            "miss": int(_q.get("miss", 0) or 0),
            "no_coverage": int(_q.get("no_coverage", 0) or 0),
        }
    return _out


# --------------------------------------------------------------------- 聚合


def _hour_of(ts: float) -> float:
    return float(int(float(ts) // 3600.0) * 3600)


def rolling(now: float | None = None, state: dict | None = None) -> dict:
    """滚动 24h 聚合：分类 + 总计（分母只算 hit+miss）。"""
    _now = float(now if now is not None else time.time())
    _st = state if state is not None else load_state()
    _lo = _now - ROLLING_SEC
    _types: dict[str, dict] = {}
    _tot = {"hit": 0, "miss": 0, "no_coverage": 0}
    for _et, _segs in (_st.get("hours") or {}).items():
        _h = _mi = _nc = 0
        for _seg in _segs or []:
            try:
                _t, _a, _b, _c = _seg[0], _seg[1], _seg[2], _seg[3]
            except (IndexError, TypeError, ValueError) as _exc:
                silent_exc(_exc, where="world_model.hit_rate.rolling:seg", level="debug")
                continue
            if float(_t) < _lo:
                continue
            _h += int(_a)
            _mi += int(_b)
            _nc += int(_c)
        _cov = _h + _mi
        _types[_et] = {
            "hit": _h, "miss": _mi, "no_coverage": _nc, "covered": _cov,
            "hit_rate": (round(_h / _cov, 4) if _cov else None),
        }
        _tot["hit"] += _h
        _tot["miss"] += _mi
        _tot["no_coverage"] += _nc
    _cov = _tot["hit"] + _tot["miss"]
    _tot["covered"] = _cov
    _tot["hit_rate"] = round(_tot["hit"] / _cov, 4) if _cov else None
    return {"sampled_at": _now, "rolling_sec": ROLLING_SEC,
            "types": _types, "total": _tot}


def report(now: float | None = None, state: dict | None = None) -> dict:
    """日报表内容（供落盘/工具/测试共用，不写盘）。"""
    _now = float(now if now is not None else time.time())
    _st = state if state is not None else load_state()
    _agg = rolling(_now, _st)
    _streak = _st.get("streak") or {}
    _low = [_et for _et, _v in _streak.items()
            if int((_v or {}).get("low", 0)) > 0 and not (_v or {}).get("evicted")]
    _evicted = [_et for _et, _v in _streak.items() if (_v or {}).get("evicted")]
    return {
        "version": _M410_HITRATE_VERSION,
        "generated_at": _now,
        "day": time.strftime("%Y%m%d", time.localtime(_now)),
        "rolling_sec": ROLLING_SEC,
        "windows_sampled": int(_st.get("windows", 0) or 0),
        "types": _agg["types"],
        "total": _agg["total"],
        "mismatches": _low,
        "evicted": _evicted,
        "alerts": list(_st.get("alerts") or [])[-20:],
        "criteria": {
            "alert_below": alert_rate(), "alert_streak": alert_streak(),
            "evict_below": evict_rate(), "evict_streak": evict_streak(),
            "min_window_samples": MIN_WINDOW_SAMPLES,
            "hit_rate_denominator": "hit+miss（no_coverage 单列，不进分母）",
        },
    }


def write_report(now: float | None = None) -> str | None:
    """落盘日报表（开关关闭 / 写失败 → None）。"""
    if not enabled():
        return None
    _now = float(now if now is not None else time.time())
    _rep = report(_now)
    _p = report_path(_now)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        _tmp = _p + ".tmp410"
        with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_rep, _f, ensure_ascii=False, indent=2)
            _f.write("\n")
        os.replace(_tmp, _p)
        return _p
    except OSError as _exc:
        silent_exc(_exc, where="world_model.hit_rate.write_report", level="warning")
        return None


# --------------------------------------------------------------------- 告警


def post_alert(text: str, event_types: list[str], level: str = "warn",
               confidence: float = 0.6) -> str:
    """发 InsightBoard 条目。返回条目 id；黑板不可用返回空串（不影响落盘留痕）。"""
    try:
        from nucleus.InsightBoard import get_insight_board
        return str(get_insight_board().post(
            insight_type=INSIGHT_TYPE,
            content=text,
            source_loop="world_model",
            related_dimension="预测对账",
            confidence=confidence,
            keywords=["world_model", "hit_rate", level] + list(event_types or []),
        ) or "")
    except Exception as _exc:
        # 黑板不可用不能阻断对账（报表/快照两路仍然要落），但要留痕
        silent_exc(_exc, where="world_model.hit_rate.post_alert", level="warning")
        return ""


def _evict(miner, event_type: str, why: str) -> bool:
    _m = miner if miner is not None else _miner()
    if _m is None:
        return False
    _fn = getattr(_m, "evict_pattern", None)
    if not callable(_fn):
        return False
    try:
        return bool(_fn(event_type, reason=why))
    except Exception as _exc:
        silent_exc(_exc, where="world_model.hit_rate._evict", level="warning")
        return False


# --------------------------------------------------------------------- 采样（分钟级）


def sample(now: float | None = None, miner=None) -> dict:
    """分钟级采样：读累计计数 → 取差分 → 更新滚动聚合与连击 → 触发告警/淘汰。

    Returns:
        {"sampled_at", "windows", "window": {et: {...}}, "alerts": [...],
         "evicted": [...], "skipped": reason}
    """
    _now = float(now if now is not None else time.time())
    _out = {"sampled_at": _now, "windows": 0, "window": {},
            "alerts": [], "evicted": [], "skipped": ""}
    if not enabled():
        _out["skipped"] = "switch_off"
        return _out
    _cur = read_counters(miner)
    if not _cur:
        _out["skipped"] = "no_counters"     # 没测到 ≠ 测得 0：本轮不写任何数
        return _out

    _st = load_state()
    _last = _st.get("last") or {}
    _hour = _hour_of(_now)
    _hours = _st.get("hours") or {}
    _streak = _st.get("streak") or {}
    _reset = []
    _a_rate, _e_rate = alert_rate(), evict_rate()
    _a_n, _e_n = alert_streak(), evict_streak()

    for _et, _c in _cur.items():
        _prev = _last.get(_et) or {"hit": 0, "miss": 0, "no_coverage": 0}
        _dh = _c["hit"] - int(_prev.get("hit", 0) or 0)
        _dm = _c["miss"] - int(_prev.get("miss", 0) or 0)
        _dn = _c["no_coverage"] - int(_prev.get("no_coverage", 0) or 0)
        if _dh < 0 or _dm < 0 or _dn < 0:
            # 计数器变小 ⇒ miner 重启过。本窗作废（否则一次重启就是一个假 miss 窗）
            _reset.append(_et)
            continue
        if _dh == 0 and _dm == 0 and _dn == 0:
            continue
        _segs = _hours.setdefault(_et, [])
        if _segs and float(_segs[-1][0]) == _hour:
            _segs[-1][1] = int(_segs[-1][1]) + _dh
            _segs[-1][2] = int(_segs[-1][2]) + _dm
            _segs[-1][3] = int(_segs[-1][3]) + _dn
        else:
            _segs.append([_hour, _dh, _dm, _dn])
        # 裁剪到滚动窗内
        _lo = _now - ROLLING_SEC
        _hours[_et] = [s for s in _segs if float(s[0]) >= _lo]

        _cov = _dh + _dm
        _rate = (round(_dh / _cov, 4) if _cov else None)
        _sk = _streak.setdefault(_et, {"low": 0, "crit": 0, "evicted": False})
        _out["window"][_et] = {"hit": _dh, "miss": _dm, "no_coverage": _dn,
                               "covered": _cov, "hit_rate": _rate}
        if _cov < MIN_WINDOW_SAMPLES:
            continue                     # 样本不足不判（不推进也不清零连击）
        if _rate is not None and _rate < _a_rate:
            _sk["low"] = int(_sk.get("low", 0)) + 1
        else:
            _sk["low"] = 0
        if _rate is not None and _rate < _e_rate:
            _sk["crit"] = int(_sk.get("crit", 0)) + 1
        else:
            _sk["crit"] = 0
        if int(_sk["low"]) == _a_n:
            _txt = ("预测命中率失配：%s 连续 %d 个采样窗 < %.2f（本窗 %s，覆盖 %d 条）"
                    % (_et, _a_n, _a_rate, _rate, _cov))
            _iid = post_alert(_txt, [_et], level="warn", confidence=0.6)
            _st.setdefault("alerts", []).append(
                {"at": _now, "event_type": _et, "level": "warn", "text": _txt,
                 "insight_id": _iid, "window_hit_rate": _rate, "covered": _cov})
            _out["alerts"].append({"event_type": _et, "level": "warn", "text": _txt})
        if int(_sk["crit"]) == _e_n and not _sk.get("evicted"):
            _why = "hit_rate<%.2f x%d" % (_e_rate, _e_n)
            _done = _evict(miner, _et, _why)
            _sk["evicted"] = bool(_done)
            _txt = ("预测模式淘汰：%s 连续 %d 个采样窗 < %.2f（本窗 %s）⇒ %s"
                    % (_et, _e_n, _e_rate, _rate, "已摘除周期记录" if _done else "摘除未生效"))
            _iid = post_alert(_txt, [_et], level="evict", confidence=0.9)
            _st.setdefault("alerts", []).append(
                {"at": _now, "event_type": _et, "level": "evict", "text": _txt,
                 "insight_id": _iid, "window_hit_rate": _rate, "evicted": _done})
            _out["evicted"].append({"event_type": _et, "ok": _done, "text": _txt})

    _st["last"] = {k: dict(v) for k, v in _cur.items()}
    _st["hours"] = _hours
    _st["streak"] = _streak
    _st["alerts"] = list(_st.get("alerts") or [])[-200:]
    _st["windows"] = int(_st.get("windows", 0) or 0) + 1
    if _reset:
        _out["reset"] = _reset
    save_state(_st)
    _out["windows"] = int(_st["windows"])
    return _out


# --------------------------------------------------------------------- 快照（心跳级）


def snapshot_for_health(now: float | None = None) -> dict:
    """给 health_snapshot.json 的 `world_model` 字段（每 5s 被读，走 mtime 缓存）。

    字段刻意扁平 + 自解释：看快照的人不需要知道本模块的内部结构，
    只需要知道「24 小时命中率是多少 / 现在有几类在失配 / 有没有被淘汰的」。
    """
    _now = float(now if now is not None else time.time())
    if not enabled():
        return {"enabled": False}
    _rep = report(_now)
    _tot = _rep.get("total") or {}
    return {
        "enabled": True,
        "hit_rate": _tot.get("hit_rate"),
        "covered": int(_tot.get("covered", 0) or 0),
        "no_coverage": int(_tot.get("no_coverage", 0) or 0),
        "mismatches": len(_rep.get("mismatches") or []),
        "mismatch_types": list(_rep.get("mismatches") or []),
        "evicted": list(_rep.get("evicted") or []),
        "windows_sampled": int(_rep.get("windows_sampled", 0) or 0),
        "criteria": _rep.get("criteria"),
        "source": STATE_REL.replace(os.sep, "/"),
    }


def version() -> str:
    return _M410_HITRATE_VERSION
