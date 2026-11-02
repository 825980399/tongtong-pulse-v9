# -*- coding: utf-8 -*-
"""narrative_digest —— Step5b 三字段（headline / delta / intent）的**纯函数**产出层。

设计约束（413 批任务书）：
  * **不引入生成模型** —— headline 从当日已有事件里按模板挑，不做任何自然语言生成；
  * **取不到就不给** —— 每个量独立 try，失败写 `reason`，绝不填 0 假装有值；
  * **可审计** —— 每个结论都带 `basis`（用了哪条规则）与 `evidence_refs`（回溯锚点）。

`intent_verdict` 在项目里**没有既有定义**（`prev_intent_verdict` 是 412 批预留的空字段），
本模块的取值域见 `VERDICTS`，判定输入只有三个可量化信号：
当日事件量、是否首现新类型、连续天数对前一日的变化。**不做语义猜测。**

行格式见 `nucleus/observability/narrative_store.row_of`。
"""

from __future__ import annotations

import os
import time
from typing import Any

from nucleus._silent_except import silent_exc

_M413_DG_VERSION = "413.1"

#: 灰度开关（413 批登记于 config.py `[M413-CFG]`）
SWITCH = "ENABLE_M413_NARRATIVE_DIGEST"

#: 情绪词 → 分值（只用于 headline 的「情绪峰值」规则；未知词记 0）
TONE_SCORE = {
    "positive": 3, "joy": 3, "happy": 3, "excited": 3, "grateful": 3,
    "negative": -3, "sad": -3, "angry": -3, "anxious": -2, "tired": -2,
    "neutral": 0,
}

#: 当日叙事倾向的**全部**可能取值（可审计的封闭集合）
VERDICTS = ("deepening", "broadening", "stable", "resuming", "quiet", "declining")

#: headline 的选分规则名（写进 `headline_basis`）
BASES = ("continuity", "first_type", "tone_peak", "first_event", "empty")

#: patches 统计只看这两个时间字段（当日口径）
_PATCH_TIME_KEYS = ("applied_at", "fixed_at", "generated_at")

#: 落盘时刻早于此小时 ⇒ 认为在总结**前一天**（夜摘要 04:00 落前一日）
#: ★容差：夜窗是 04:00±10min，取 6 而非 4 —— 否则 04:10 会被误判成"当天"
DIGEST_SETTLE_HOUR = 6


def enabled() -> bool:
    """灰度开关；缺省 True（关闭时 `persist` 走 412 的原路径，三字段留空）。"""
    try:
        import config as _c
        return bool(getattr(_c, SWITCH, True))
    except Exception as _e:
        silent_exc(_e, where="observability.narrative_digest:enabled", level="debug")
        return True


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def day_stamp(now: float | None = None) -> str:
    """本地日 YYYYMMDD（与 `narrative_store.day_stamp` 同口径）。"""
    return time.strftime("%Y%m%d", time.localtime(float(now if now is not None else time.time())))


def digest_anchor(now: float | None = None) -> float:
    """**摘要日锚点** —— 这一行摘要算哪一天。

    夜摘要定时在 04:00（±10min）跑，总结的是**刚过去的那一天**，不是当天 0~4 点。
    `narrative_store.continuous_days` 的 docstring 早已这么约定
    （"04:00 落盘的是**前一天**的摘要"），而 412 的 `persist()` 仍按 `now` 落当天文件
    —— 两者自相矛盾；413 统一按「前一天」。

    Args:
        now: 落盘时刻；缺省取当前时间。

    Returns:
        落在**摘要日**内的一个 epoch（当日 `DIGEST_SETTLE_HOUR` 之后返回 ``now``
        本身，之前返回 ``now - 86400``）。★只用于取日戳与跨日对照，
        **不写进任何时间戳字段** —— 落盘真实时刻仍记在 `generated_at`。
    """
    _t = float(now if now is not None else time.time())
    try:
        if time.localtime(_t).tm_hour < int(DIGEST_SETTLE_HOUR):
            return _t - 86400.0
    except (OSError, OverflowError, ValueError) as _e:
        silent_exc(_e, where="observability.narrative_digest:digest_anchor",
                   level="debug")
    return _t


def digest_day(now: float | None = None) -> str:
    """摘要日 `YYYYMMDD`（= ``day_stamp(digest_anchor(now))``）。"""
    return day_stamp(digest_anchor(now))


# ------------------------------------------------------------------ 事件分层

def events_of_day(events: Any, now: float | None = None) -> list[dict[str, Any]]:
    """从（可能含 7 天回放的）事件列表里**只取当日**的那些。

    ★412 的 `persist()` 把回放进来的历史事件也一起数了，`narrative_events`
      记的是「内存里全部」而不是「当日」。本函数是 413 的修正口径。
    """
    _day = digest_day(now)          # ★摘要日：04:00 落盘看**前一天**
    _out: list[dict[str, Any]] = []
    for _e in (events or []):
        if not isinstance(_e, dict):
            continue
        try:
            _ts = float(_e.get("timestamp") or 0.0)
        except (TypeError, ValueError) as _e_ts:
            silent_exc(_e_ts, where="observability.narrative_digest:events_of_day ts",
                       level="debug")
            continue
        if _ts <= 0:
            continue
        if day_stamp(_ts) == _day:
            _out.append(_e)
    return _out


def _types_of(evs: list[dict[str, Any]]) -> set[str]:
    return {str(e.get("type") or "unknown") for e in evs}


def _prev_types_of(prev_row: dict | None) -> set[str]:
    """前一日的**事件类型集合**（顶层与 `extra` 都看）。

    ★`row_of` 的骨架只放必需键，`event_types` 会落进 `extra` —— 只读顶层
      恒得空集，`first_type` 与 `broadening` 两条规则就**永远不可能命中**。
      这里顶层优先、`extra` 兜底（将来若把它提进骨架也不用改）。
    """
    _p = prev_row if isinstance(prev_row, dict) else {}
    _v = _p.get("event_types")
    if not _v:
        _v = (_p.get("extra") or {}).get("event_types")
    try:
        return {str(x) for x in (_v or []) if x}
    except TypeError as _e:
        silent_exc(_e, where="observability.narrative_digest:_prev_types_of",
                   level="debug")
        return set()


def _one_line(text: Any, cap: int = 80) -> str:
    """压成**一行**并限长 —— `headline` 必须是"一句话"。

    ★事件 `content` 里可能带换行（用户一次说多句、或跨行输入），
      直接切 `[:40]` 会把换行带进 jsonl 的 headline，读方按行解析就断了。
    """
    _s = " ".join(str(text or "").split())
    return _s[:max(1, int(cap))]


def _tone_of(ev: dict[str, Any]) -> int:
    return int(TONE_SCORE.get(str(ev.get("emotional_tone") or "").lower(), 0))


# ------------------------------------------------------------------ headline

def pick_headline(day_evs: list[dict[str, Any]], prev_row: dict | None = None,
                  cont_days: int | None = None) -> dict[str, Any]:
    """按**固定优先级**从当日事件里挑一条最重要的，拼一句话。

    Returns:
        ``{"text", "basis", "event_id", "detail"}``；`basis ∈ BASES`。
        ★不做生成 —— `text` 全部由模板字符串 + 变量拼成。
        ★`text` 恒为**单行**且 ≤80 字符（`_one_line`），jsonl 按行解析才安全。

    优先级（先命中先返回）：
        1. ``continuity``  连续天数比前一日 +1 且 ≥2
        2. ``first_type``  当日出现前一日没有的事件类型
        3. ``tone_peak``   情绪分值绝对值最大的一条（≠0 才算）
        4. ``first_event`` 当日第一条
        5. ``empty``       当日无事件
    """
    _prev = prev_row if isinstance(prev_row, dict) else {}
    _prev_days = _prev.get("continuous_days")
    try:
        _prev_days = int(_prev_days) if _prev_days is not None else None
    except (TypeError, ValueError) as _e_pd:
        silent_exc(_e_pd, where="observability.narrative_digest:pick_headline prev_days",
                   level="debug")
        _prev_days = None
    _cur_days = int(cont_days) if isinstance(cont_days, int) else None

    # 1) 连续性跃迁
    if _cur_days is not None and _cur_days >= 2 and _prev_days is not None \
            and _cur_days > _prev_days:
        return {"text": _one_line("叙事连续性推进到第 %d 天" % _cur_days),
                "basis": "continuity", "event_id": None,
                "detail": {"continuous_days": _cur_days, "prev_days": _prev_days}}

    if not day_evs:
        return {"text": _one_line("当日无叙事事件"), "basis": "empty", "event_id": None,
                "detail": {"day_events": 0}}

    # 2) 首现事件类型
    _prev_types = _prev_types_of(_prev)
    _cur_types = _types_of(day_evs)
    _new = sorted(_cur_types - _prev_types) if _prev_types else []
    if _new:
        _t = _new[0]
        _first = next((e for e in day_evs if str(e.get("type") or "") == _t), None)
        return {"text": _one_line("首次出现「%s」类叙事" % _t),
                "basis": "first_type", "event_id": (_first or {}).get("id"),
                "detail": {"new_types": _new, "prev_types": sorted(_prev_types)[:8]}}

    # 3) 情绪峰值
    _best, _best_score = None, 0
    for e in day_evs:
        _s = abs(_tone_of(e))
        if _s > _best_score:
            _best, _best_score = e, _s
    if _best is not None:
        _snip = str(_best.get("content") or "")[:40]
        return {"text": _one_line("%s（情绪强度 %d/%s）" % (
                    _snip or "情绪峰值事件", _best_score, _best.get("emotional_tone"))),
                "basis": "tone_peak", "event_id": _best.get("id"),
                "detail": {"tone": _best.get("emotional_tone"), "score": _best_score}}

    # 4) 当日首条
    _f = day_evs[0]
    return {"text": _one_line(str(_f.get("content") or "")[:40] or "当日首条叙事"),
            "basis": "first_event", "event_id": _f.get("id"),
            "detail": {"type": _f.get("type")}}


# ------------------------------------------------------------------ delta

def _cell(cur: Any, prev: Any, source: str, reason: str = "") -> dict[str, Any]:
    """一格对比：`{cur, prev, delta, source[, reason]}`。取不到时 cur/prev/delta 全 None。"""
    _d = {"cur": cur, "prev": prev, "delta": None, "source": source}
    if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
        _d["delta"] = round(float(cur) - float(prev), 6)
    if reason:
        _d["reason"] = reason
    elif _d["delta"] is None:
        _d["reason"] = "missing_one_side"
    return _d


def collect_stats(now: float | None = None) -> dict[str, Any]:
    """取 delta 三量的**当期值**。每一项独立 try，失败只记 reason。

    Returns:
        ``{"nodes": {"value"|None, "source", "reason"}, "llm_ratio": {...},
           "patches": {...}}``
    """
    _out: dict[str, Any] = {}

    # --- nodes：节点池总数
    #   ★实测口径（413 T0）：节点池规模落在**小时快照**里，
    #     `data/monitor/health_snapshot_hourly.json` → `warm.nodes.total`（实测 14411）。
    #     `data/knowledge/snapshot.json` 这个路径**根本不存在**，按它探会恒 None。
    import json
    _out["nodes"] = {"value": None, "source": "", "reason": "not_probed"}
    _tried: list[str] = []
    for _rel, _keys in (
        ("data/monitor/health_snapshot_hourly.json", ("warm", "nodes", "total")),
        ("data/monitor/health_snapshot.json", ("nodes", "total")),
    ):
        _tried.append(_rel)
        try:
            _p = os.path.join(project_root(), *_rel.split("/"))
            if not os.path.isfile(_p):
                continue
            with open(_p, encoding="utf-8", errors="replace") as _fh:
                _d = json.load(_fh)
            _v = _d
            for _k in _keys:
                _v = _v.get(_k) if isinstance(_v, dict) else None
            if isinstance(_v, int):
                _out["nodes"] = {"value": _v,
                                 "source": "%s:%s" % (_rel, ".".join(_keys)),
                                 "reason": ""}
                break
        except Exception as _e:
            silent_exc(_e, where="narrative_digest:collect_stats nodes", level="debug")
    if _out["nodes"]["value"] is None:
        _out["nodes"] = {"value": None, "source": ",".join(_tried),
                         "reason": "node_count_not_found"}

    # --- llm_ratio：依赖度快照（只读）
    #   ★实测口径（413 T0）：比例在 `derived` 子字典里（实测 0.6148），
    #     读顶层会恒得 None ⇒ 顶层与 derived 都看，顶层优先。
    _out["llm_ratio"] = {"value": None, "source": "", "reason": "not_probed"}
    try:
        from nucleus.LLMDependencyMetrics import get_dependency_snapshot
        _snap = get_dependency_snapshot() or {}
        _r = _snap.get("llm_dependency_ratio")
        _where = "get_dependency_snapshot()"
        if not isinstance(_r, (int, float)):
            _r = (_snap.get("derived") or {}).get("llm_dependency_ratio")
            _where = "get_dependency_snapshot().derived"
        if isinstance(_r, (int, float)):
            _out["llm_ratio"] = {"value": round(float(_r), 6),
                                 "source": _where, "reason": ""}
        else:
            _out["llm_ratio"] = {"value": None, "source": _where,
                                 "reason": "key_absent"}
    except Exception as _e:
        silent_exc(_e, where="narrative_digest:collect_stats llm_ratio", level="debug")
        _out["llm_ratio"] = {"value": None, "source": "",
                             "reason": "error:%s" % type(_e).__name__}

    # --- patches：当日 applied/fixed 条数
    _out["patches"] = {"value": None, "source": "", "reason": "not_probed"}
    try:
        _p = os.path.join(project_root(), "data", "patches", "patch_history.json")
        if not os.path.isfile(_p):
            _out["patches"] = {"value": None, "source": "", "reason": "history_not_found"}
        else:
            with open(_p, encoding="utf-8", errors="replace") as _fh:
                _rows = json.load(_fh)
            if not isinstance(_rows, list):
                _rows = []
            _day = digest_day(now)          # ★摘要日口径，与 headline/delta 一致
            _n = 0
            for _r in _rows:
                if not isinstance(_r, dict):
                    continue
                for _k in _PATCH_TIME_KEYS:
                    _v = _r.get(_k)
                    if isinstance(_v, (int, float)) and _v > 0 \
                            and day_stamp(float(_v)) == _day:
                        _n += 1
                        break
            _out["patches"] = {"value": _n,
                               "source": "data/patches/patch_history.json(当日)", "reason": ""}
    except Exception as _e:
        silent_exc(_e, where="narrative_digest:collect_stats patches", level="debug")
        _out["patches"] = {"value": None, "source": "",
                           "reason": "error:%s" % type(_e).__name__}
    return _out


def _prev_of(prev_row: dict | None, key: str) -> Any:
    """从前一日行里取某个 delta 格子的 `cur`（= 前一日的当期值）。"""
    _d = (prev_row or {}).get("delta")
    if isinstance(_d, dict):
        _cell_prev = _d.get(key)
        if isinstance(_cell_prev, dict):
            return _cell_prev.get("cur")
        return _cell_prev
    return None


def diff_of(cur_stats: dict[str, Any], prev_row: dict | None = None,
            day_events: int | None = None) -> dict[str, Any]:
    """当日 vs 前一日 的差异统计（三量 + 当日事件数 + 连续天数）。

    ★每一格都带 `source` 与 `reason` —— 取不到时**显式**写原因，不填 0 假充。
    """
    _cs = cur_stats if isinstance(cur_stats, dict) else {}
    _out: dict[str, Any] = {}
    for _k in ("nodes", "llm_ratio", "patches"):
        _c = _cs.get(_k) or {}
        _out[_k] = _cell(_c.get("value"), _prev_of(prev_row, _k),
                         str(_c.get("source") or ""), str(_c.get("reason") or ""))
    # 当日事件数（叙事器官自有量，恒可算）
    _prev_ev = (prev_row or {}).get("narrative_events")
    try:
        _prev_ev = int(_prev_ev) if _prev_ev is not None else None
    except (TypeError, ValueError) as _e_pe:
        silent_exc(_e_pe, where="observability.narrative_digest:diff_of prev_events",
                   level="debug")
        _prev_ev = None
    _out["narrative_events"] = _cell(
        int(day_events) if isinstance(day_events, int) else None,
        _prev_ev, "PulseNarrativeSelf._narrative_events(当日)")
    return _out


# ------------------------------------------------------------------ intent

def intent_of(day_evs: list[dict[str, Any]], prev_row: dict | None = None,
              cont_days: int | None = None) -> dict[str, Any]:
    """当日叙事倾向判定（封闭取值域 `VERDICTS`）。

    判定顺序（先命中先返回，`reason` 写清依据）：
        quiet     —— 当日无事件
        resuming  —— 当日有事件，但前一日事件数为 0（中断后恢复）
        declining —— 当日事件数 < 前一日 × 0.6
        broadening—— 当日出现前一日没有的事件类型
        deepening —— 事件数 ≥ 前一日 且 连续天数 ≥ 2
        stable    —— 其余
    """
    _prev = prev_row if isinstance(prev_row, dict) else {}
    _prev_n = _prev.get("narrative_events")
    try:
        _prev_n = int(_prev_n) if _prev_n is not None else None
    except (TypeError, ValueError) as _e_pn:
        silent_exc(_e_pn, where="observability.narrative_digest:intent_of prev_events",
                   level="debug")
        _prev_n = None
    _cur_n = len(day_evs)
    _cd = int(cont_days) if isinstance(cont_days, int) else 0
    _prev_v = _prev.get("intent") or None

    def _ret(v: str, reason: str, **basis: Any) -> dict[str, Any]:
        return {"verdict": v, "prev_verdict": _prev_v,
                "changed": bool(_prev_v) and _prev_v != v,
                "reason": reason, "basis": basis}

    if _cur_n == 0:
        return _ret("quiet", "当日 0 条叙事事件", day_events=0, prev_events=_prev_n)

    if _prev_n == 0:
        return _ret("resuming", "前一日 0 条，当日恢复 %d 条" % _cur_n,
                    day_events=_cur_n, prev_events=0)

    if _prev_n and _cur_n < _prev_n * 0.6:
        return _ret("declining", "当日 %d 条 < 前一日 %d 条 × 0.6" % (_cur_n, _prev_n),
                    day_events=_cur_n, prev_events=_prev_n)

    _prev_types = _prev_types_of(_prev)
    _new = sorted(_types_of(day_evs) - _prev_types) if _prev_types else []
    if _new:
        return _ret("broadening", "新出现事件类型 %s" % ",".join(_new[:3]),
                    day_events=_cur_n, prev_events=_prev_n, new_types=_new[:5])

    if _prev_n is not None and _cur_n >= _prev_n and _cd >= 2:
        return _ret("deepening", "事件数 %d ≥ 前一日 %d 且连续 %d 天" % (
            _cur_n, _prev_n, _cd), day_events=_cur_n, prev_events=_prev_n,
            continuous_days=_cd)

    return _ret("stable", "事件数 %d，与前一日 %s 持平" % (
        _cur_n, _prev_n if _prev_n is not None else "未知"),
        day_events=_cur_n, prev_events=_prev_n)


# ------------------------------------------------------------------ 汇总

def evidence_of(day_evs: list[dict[str, Any]], headline: dict | None = None,
                stats: dict[str, Any] | None = None, patch_ids: list[str] | None = None
                ) -> list[str]:
    """回溯锚点：主事件 id + 当日前 3 个事件 id + 补丁 id + 数据源。"""
    _refs: list[str] = []
    _hid = (headline or {}).get("event_id")
    if _hid is not None:
        _refs.append("event#%s" % _hid)
    for e in day_evs[:3]:
        _eid = e.get("id")
        if _eid is not None and ("event#%s" % _eid) not in _refs:
            _refs.append("event#%s" % _eid)
    for _p in (patch_ids or [])[:3]:
        if _p and ("patch#%s" % _p) not in _refs:
            _refs.append("patch#%s" % _p)
    _src = ((stats or {}).get("llm_ratio") or {}).get("source")
    if _src:
        _refs.append("src:%s" % _src)
    return _refs


def build(events: Any, now: float | None = None, prev_row: dict | None = None,
          stats: dict[str, Any] | None = None, cont_days: int | None = None
          ) -> dict[str, Any]:
    """一次性产出 Step5b 需要的全部字段（供 `PulseNarrativeSelf.persist` 透传）。

    Returns:
        ``{"headline","headline_basis","headline_event_id","delta","intent",
           "prev_intent_verdict","intent_reason","evidence_refs",
           "narrative_events","narrative_events_total","event_types"}``
    """
    _now = float(now if now is not None else time.time())
    _all = [x for x in (events or []) if isinstance(x, dict)]
    _day = events_of_day(_all, now=_now)
    _stats = stats if isinstance(stats, dict) else collect_stats(now=_now)

    _hl = pick_headline(_day, prev_row=prev_row, cont_days=cont_days)
    _it = intent_of(_day, prev_row=prev_row, cont_days=cont_days)
    _dt = diff_of(_stats, prev_row=prev_row, day_events=len(_day))
    _refs = evidence_of(_day, headline=_hl, stats=_stats)

    return {
        "headline": _hl.get("text") or "",
        "headline_basis": _hl.get("basis") or "",
        "headline_event_id": _hl.get("event_id"),
        "headline_detail": _hl.get("detail") or {},
        "delta": _dt,
        "intent": _it.get("verdict") or "",
        "prev_intent_verdict": _it.get("prev_verdict"),
        "intent_reason": _it.get("reason") or "",
        "intent_changed": bool(_it.get("changed")),
        "evidence_refs": _refs,
        "narrative_events": len(_day),
        "narrative_events_total": len(_all),
        "event_types": sorted(_types_of(_day)),
        "day": digest_day(_now),
    }


def version() -> str:
    return _M413_DG_VERSION
