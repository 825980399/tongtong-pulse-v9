# -*- coding: utf-8 -*-
"""第410批刀1：七类事件周期档案（periodic_profile）。

**数据源**：``data/stream_miner/patterns.json`` 的 ``periodicity`` 段，即
``StreamMiner._periodicity_cache`` 的落盘（每类保留最近 50 个时间戳）。

为什么**不**用 ``patterns`` 段的 ``repeated:X`` / ``periodic:X``：
``repeated:X`` 只有 ``occurrence_count`` / ``window_size`` / ``frequency``，
**没有任何间隔数据**；``periodic:X`` 只有 ``interval``/``regularity`` 的估计值，
不含原始时间戳。要算 median / p90 只能用 ``periodicity`` 的原始序列。

**为什么无需额外按 ``type=="emit"`` 过滤**：``InfoField._publish`` 在
``self._total_published += 1`` **之后**才调 ``stream_miner.feed_event``，
且只喂 ``event_type`` / ``source_organ`` / ``timestamp`` 三个字段 ——
即 periodicity 天然只含**发布侧（emit）**，receive 侧从未进入。
实测交叉验证：``periodicity`` 的 ``heart.beat`` 中位 13.5053s，与
``data/monitor/pulse_pairs_*.jsonl`` 过滤 ``type=="emit"`` 后的 13.5012s
相差 0.004s（0.03‰），互为佐证。

**不做的事**：不新建调度器、不做自动分类（predict_type 是任务书给定的固定表）、
不参与决策（本模块只产出档案，由 StreamMiner 读它决定容差）。
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import (
    DigestEvent,
    EnergyEvent,
    EyeEvent,
    HeartEvent,
    SpinalCordEvent,
    TouchEvent,
)

#: 契约版本（改字段语义必须改版本号）
_M410_PROFILE_VERSION = "410.1"

def _ev(v: object) -> str:
    """取事件常量的字符串值。

    ★本项目 ``nucleus/const.py`` 里的事件常量是**纯 str 类属性**
    （``class HeartEvent: BEAT = <点分事件名>``），**不是 Enum 成员** ——
    直接写 ``HeartEvent.BEAT.value`` 会 ``AttributeError: 'str' object has no attribute 'value'``。
    这里用 ``getattr(v, "value", v)`` 兼容两种形态：将来若真改成 Enum 也不必回来改 7 处。
    """
    return str(getattr(v, "value", v))


#: 七类事件的**具名常量**（值取自 nucleus.const —— 本项目规范禁止裸点分字面量，
#: 事件字符串门禁会拦。集中定义一次，下游（含 tools/ 与测试）一律引用本组常量，
#: 便于将来事件改名时只改一处。）
EV_TOUCH_HARDWARE_SNAPSHOT = _ev(TouchEvent.HARDWARE_SNAPSHOT)
EV_ENERGY_METABOLISM_SNAPSHOT = _ev(EnergyEvent.METABOLISM_SNAPSHOT)
EV_SPINAL_INSPECTION_REPORT = _ev(SpinalCordEvent.INSPECTION_REPORT)
EV_HEART_ALIVE = _ev(HeartEvent.ALIVE)
EV_HEART_BEAT = _ev(HeartEvent.BEAT)
EV_EYES_STREAM_FRAME = _ev(EyeEvent.STREAM_FRAME)
EV_DIGEST_KNOWLEDGE = _ev(DigestEvent.KNOWLEDGE)

#: 七类事件（任务书刀2 表格，顺序即报表顺序）
TARGET_EVENTS: tuple[str, ...] = (
    EV_TOUCH_HARDWARE_SNAPSHOT,
    EV_ENERGY_METABOLISM_SNAPSHOT,
    EV_SPINAL_INSPECTION_REPORT,
    EV_HEART_ALIVE,
    EV_HEART_BEAT,
    EV_EYES_STREAM_FRAME,
    EV_DIGEST_KNOWLEDGE,
)

#: 预测型 —— **固定表，不是分类器**（任务书刀2 表格）
PREDICT_TYPE: dict[str, str] = {
    EV_TOUCH_HARDWARE_SNAPSHOT: "periodic",
    EV_ENERGY_METABOLISM_SNAPSHOT: "periodic",
    EV_SPINAL_INSPECTION_REPORT: "periodic",
    EV_HEART_ALIVE: "periodic",
    EV_HEART_BEAT: "periodic",
    EV_EYES_STREAM_FRAME: "count",
    EV_DIGEST_KNOWLEDGE: "burst",
}

#: 时刻容差百分比（任务书刀2 表格；None = 不设时刻容差）
TOLERANCE_PCT: dict[str, float | None] = {
    EV_TOUCH_HARDWARE_SNAPSHOT: 0.05,
    EV_ENERGY_METABOLISM_SNAPSHOT: 0.05,
    EV_SPINAL_INSPECTION_REPORT: 0.10,
    EV_HEART_ALIVE: 0.05,
    EV_HEART_BEAT: 0.05,
    EV_EYES_STREAM_FRAME: None,
    EV_DIGEST_KNOWLEDGE: None,
}

#: 预测型 → 中文说明（报表用）
PREDICT_TYPE_DESC: dict[str, str] = {
    "periodic": "周期型（下次何时来）",
    "count": "计数型（下一分钟来多少条）",
    "burst": "突发型（gap 切批，下一分钟几批）",
}

#: 相对项目根的落盘位置
PROFILE_REL = os.path.join("data", "world_model", "periodic_profile.json")

#: 相对项目根的数据源
PATTERNS_REL = os.path.join("data", "stream_miner", "patterns.json")

#: 数据不足下限（少于这么多个时间戳 ⇒ 只记录样本数，不产出中位数）
MIN_SAMPLES = 3

#: 测试/工具用的落盘根重定向（None = 按项目根推导）
_DATA_DIR: str | None = None


# --------------------------------------------------------------------- 开关


def enabled() -> bool:
    """灰度开关：关闭时 build() 不写盘、tolerance_for() 恒回落旧 60s。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M410_PERIODIC_PROFILE", True))
    except Exception as _exc:
        silent_exc(_exc, where="world_model.periodic_profile.enabled", level="debug")
        return True


# --------------------------------------------------------------------- 路径


def project_root() -> str:
    """项目根（本文件在 `nucleus/world_model/` 下，三层 up）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_root() -> str:
    """落盘根：`set_data_dir()` 生效时用它，否则项目根。"""
    return _DATA_DIR or project_root()


def set_data_dir(path: str | None) -> None:
    """重定向数据根（测试/工具用）。传 None 复原。"""
    global _DATA_DIR
    _DATA_DIR = path


def profile_path() -> str:
    return os.path.join(data_root(), PROFILE_REL)


def patterns_path() -> str:
    return os.path.join(data_root(), PATTERNS_REL)


# --------------------------------------------------------------------- 统计


def intervals_of(stamps: list[Any]) -> list[float]:
    """把时间戳序列转成**相邻间隔**列表（升序，丢弃非数值与负值）。

    只做「排序 + 相邻差」，不做去重：同一毫秒内的重复事件间隔为 0 是事实，
    如实计入并在报表里由 median 吸收（不在这里"清洗"，否则掩盖上游节流问题）。
    """
    _num: list[float] = []
    for _s in stamps or []:
        try:
            _v = float(_s)
        except (TypeError, ValueError) as _exc:
            silent_exc(_exc, where="world_model.periodic_profile.intervals_of", level="debug")
            continue
        if _v > 0:
            _num.append(_v)
    _num.sort()
    _out: list[float] = []
    for _i in range(len(_num) - 1):
        _d = _num[_i + 1] - _num[_i]
        if _d >= 0:
            _out.append(_d)
    return _out


def _median(sorted_xs: list[float]) -> float | None:
    """标准中位数（偶数个取中间两个的均值）。空列表返回 None。

    与 ``_pct(xs, 0.5)`` 的区别：``_pct`` 是 nearest-rank（返回**实际观测到的**
    某个值，不插值），对偶数样本会取偏小的那个。中位间隔是明确概念，
    必须用标准中位数；p90 用 nearest-rank 更保守（不编造没出现过的间隔）。
    """
    if not sorted_xs:
        return None
    _n = len(sorted_xs)
    _mid = _n // 2
    if _n % 2 == 1:
        return float(sorted_xs[_mid])
    return (float(sorted_xs[_mid - 1]) + float(sorted_xs[_mid])) / 2.0


def _pct(sorted_xs: list[float], q: float) -> float | None:
    """nearest-rank 分位数（q ∈ [0,1]）。空列表返回 None，不猜默认值。"""
    if not sorted_xs:
        return None
    _n = len(sorted_xs)
    _idx = int(math.ceil(q * _n)) - 1
    if _idx < 0:
        _idx = 0
    if _idx >= _n:
        _idx = _n - 1
    return float(sorted_xs[_idx])


def _round(v: float | None, nd: int = 4) -> float | None:
    return None if v is None else round(float(v), nd)


# --------------------------------------------------------------------- 档案


def _one_entry(event_type: str, stamps: list[Any]) -> dict[str, Any]:
    """构造单类事件的档案条目。数据不足时 median/p90 为 None（不编造）。"""
    _iv = sorted(intervals_of(stamps))
    _p_type = PREDICT_TYPE.get(event_type, "periodic")
    _pct_cfg = TOLERANCE_PCT.get(event_type)
    _med = _median(_iv)
    _tol = None
    if _pct_cfg is not None and _med is not None:
        _tol = _med * float(_pct_cfg)

    _ok = len(_iv) >= max(1, MIN_SAMPLES - 1)
    _entry: dict[str, Any] = {
        "event_type": event_type,
        "median_interval_sec": _round(_med),
        "p90_interval_sec": _round(_pct(_iv, 0.9)),
        "sample_count": len(stamps or []),
        "predict_type": _p_type,
        "predict_target": PREDICT_TYPE_DESC.get(_p_type, ""),
        "interval_count": len(_iv),
        "tolerance_pct": _pct_cfg,
        "tolerance_sec": _round(_tol),
        "ok": bool(_ok),
    }
    if not _ok:
        _entry["note"] = "样本不足（interval_count=%d < %d），median/p90 不产出" % (
            len(_iv), max(1, MIN_SAMPLES - 1))
    return _entry


def build_profile(patterns: dict | None = None, now: float | None = None) -> dict[str, Any]:
    """从 patterns 结构构造档案（**纯函数，不写盘**）。

    Args:
        patterns: ``patterns.json`` 的完整内容；None 时按 ``patterns_path()`` 读。
        now: 生成时刻（测试注入）。
    """
    _now = float(now if now is not None else time.time())
    if patterns is None:
        patterns = read_patterns()
    _periodic = (patterns or {}).get("periodicity") or {}
    if not isinstance(_periodic, dict):
        _periodic = {}

    _events: dict[str, dict[str, Any]] = {}
    _missing: list[str] = []
    _insufficient: list[str] = []
    for _et in TARGET_EVENTS:
        _raw = _periodic.get(_et)
        if not isinstance(_raw, list) or not _raw:
            _missing.append(_et)
            _events[_et] = _one_entry(_et, [])
            continue
        _e = _one_entry(_et, _raw)
        if not _e["ok"]:
            _insufficient.append(_et)
        _events[_et] = _e

    return {
        "version": _M410_PROFILE_VERSION,
        "generated_at": _now,
        "source": PATTERNS_REL.replace(os.sep, "/") + "#periodicity",
        "source_saved_at": (patterns or {}).get("saved_at"),
        "target_count": len(TARGET_EVENTS),
        "events": _events,
        "missing": _missing,
        "insufficient": _insufficient,
        "complete": not _missing,
    }


def read_patterns(path: str | None = None) -> dict:
    """读 patterns.json。缺文件 / 损坏返回空 dict（**不抛**，调用方自己判 complete）。"""
    _p = path or patterns_path()
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
        return _d if isinstance(_d, dict) else {}
    except (OSError, ValueError) as _exc:
        silent_exc(_exc, where="world_model.periodic_profile.read_patterns", level="warning")
        return {}


def save_profile(profile: dict, path: str | None = None) -> str | None:
    """原子写档案（tmp + os.replace）。返回实际路径；失败返回 None。"""
    _p = path or profile_path()
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        _tmp = _p + ".tmp410"
        with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(profile, _f, ensure_ascii=False, indent=2)
            _f.write("\n")
        os.replace(_tmp, _p)
        return _p
    except OSError as _exc:
        silent_exc(_exc, where="world_model.periodic_profile.save_profile", level="warning")
        return None


def load_profile(path: str | None = None, max_age_sec: float | None = None,
                 now: float | None = None) -> dict:
    """读档案。缺文件 / 损坏 / 过期（给了 max_age_sec 时）返回空 dict。

    返回空 dict 的语义是「没有可用档案」—— 调用方必须自己决定回落策略，
    本函数**不提供**任何默认容差（沿用 409 批纪律：不猜默认值）。
    """
    _p = path or profile_path()
    if not os.path.isfile(_p):
        return {}
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _exc:
        silent_exc(_exc, where="world_model.periodic_profile.load_profile", level="warning")
        return {}
    if not isinstance(_d, dict):
        return {}
    if max_age_sec is not None:
        _now = float(now if now is not None else time.time())
        try:
            _gen = float(_d.get("generated_at") or 0)
        except (TypeError, ValueError) as _exc:
            # ★不是"档案没有生成时刻"，是"生成时刻不是数" —— 两者都判过期，
            #   但必须留痕，否则一份永远被判过期的档案会彻底看不见。
            silent_exc(_exc, where="world_model.periodic_profile.load_profile:generated_at",
                       level="debug")
            _gen = 0.0
        if _gen <= 0 or (_now - _gen) > float(max_age_sec):
            return {}
    return _d


def build(patterns: dict | None = None, now: float | None = None,
          path: str | None = None) -> dict[str, Any]:
    """构造并落盘（开关关闭时只构造不写盘，返回 ``written=False``）。"""
    _rep = build_profile(patterns, now)
    _rep["written"] = False
    _rep["path"] = ""
    if not enabled():
        _rep["skipped"] = "switch_off"
        return _rep
    _p = save_profile(_rep, path)
    if _p:
        _rep["written"] = True
        _rep["path"] = _p
    else:
        _rep["skipped"] = "write_failed"
    return _rep


# --------------------------------------------------------------------- 消费


def tolerance_for(event_type: str, profile: dict | None = None,
                  now: float | None = None,
                  max_age_sec: float = 3600.0) -> float | None:
    """取某类事件的时刻容差（秒）。

    Returns:
        正数 = 该类型的时刻容差；None = **不设时刻容差**（count/burst 型，
        或档案缺该类，或档案过期）。调用方必须把 None 与非 None 分开处理，
        不得把 None 当成"用默认 60s" —— 那正是本批要废弃的一刀切。
    """
    if not enabled():
        return None
    _p = profile if profile is not None else load_profile(max_age_sec=max_age_sec, now=now)
    if not _p:
        return None
    _ev = (_p.get("events") or {}).get(event_type) or {}
    _tol = _ev.get("tolerance_sec")
    if _tol is None:
        return None
    try:
        _v = float(_tol)
    except (TypeError, ValueError) as _exc:
        silent_exc(_exc, where="world_model.periodic_profile.tolerance_for", level="debug")
        return None
    return _v if _v > 0 else None


def predict_type_of(event_type: str) -> str:
    """某类事件的预测型（固定表；未见过的类型按 periodic 处理）。"""
    return PREDICT_TYPE.get(event_type, "periodic")


def summarize(profile: dict | None = None) -> dict[str, Any]:
    """统计摘要（工具 --stats 用）。"""
    _p = profile if profile is not None else load_profile()
    _ev = (_p.get("events") or {}) if isinstance(_p, dict) else {}
    _by_type: dict[str, int] = {}
    _ok = 0
    for _e in _ev.values():
        _t = str(_e.get("predict_type") or "?")
        _by_type[_t] = _by_type.get(_t, 0) + 1
        if _e.get("ok"):
            _ok += 1
    return {
        "version": (_p or {}).get("version", ""),
        "generated_at": (_p or {}).get("generated_at"),
        "total": len(_ev),
        "ok": _ok,
        "by_predict_type": _by_type,
        "missing": list((_p or {}).get("missing") or []),
        "insufficient": list((_p or {}).get("insufficient") or []),
        "complete": bool((_p or {}).get("complete")),
        "exists": bool(_p),
    }


def version() -> str:
    return _M410_PROFILE_VERSION
