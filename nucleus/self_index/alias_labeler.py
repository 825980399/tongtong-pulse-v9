# -*- coding: utf-8 -*-
"""493.8-1：影子日志四类标注器 + 每日统计。

四类标签（task 书定义）：
  - alias_correct : 用户指称 + fast path 正确命中（可学：强化）
  - alias_miss    : 用户指称 + fast path 没答/答错（可学：补漏）
  - fastpath_hit  : fast path 命中但非指代类（如计数/社交）
  - fastpath_miss : fast path 未命中（非指代类，非学习池）

skipped_by_guard 的行（闸门拦下的非自我查询）不计入四类池。
"""
from __future__ import annotations

import os
import re
import json

try:
    from nucleus._silent_except import silent_exc
except Exception:
    def silent_exc(_e, where="", level="debug"):  # noqa
        return None

LABEL_TYPES = ("alias_correct", "alias_miss", "fastpath_hit", "fastpath_miss")

_SELF_KW = ("我", "你", "曈曈", "自己", "器官")


def compute_self_ref(query: str) -> bool:
    """query 是否含自我指称（自我关键词 或 已知器官引用）。"""
    try:
        from nucleus.self_index.fastpath import _has_organ_ref
        _s = str(query or "")
        if any(_k in _s for _k in _SELF_KW):
            return True
        return bool(_has_organ_ref(_s))
    except Exception as _e:
        silent_exc(_e, where="alias_labeler.compute_self_ref", level="debug")
        return False


def label_shadow_row(row: dict) -> str | None:
    """给一条影子日志行打四类标签；skipped_by_guard 返回 None。

    判定依据（用 explain 已落盘的字段，稳定可复现）：
      - would_answer 非空 = fast path 命中（有答案）
      - organ 非空 = 解析到具体器官（指代类）；否则为非器官特指（计数/社交等）
    故：
      alias_correct : 有答案 且 解析到器官（用户指称 + 正确命中）
      alias_miss    : 无答案 且 解析到器官（用户指称但没答/答错）
      fastpath_hit  : 有答案 且 未解析器官（计数/社交等非指代，正确命中）
      fastpath_miss : 无答案 且 未解析器官（非指代，未命中）
    """
    if not isinstance(row, dict):
        return None
    if row.get("skipped_by_guard"):
        return None
    _q = str(row.get("query", "") or "")
    if not _q:
        return None
    _has_answer = bool(row.get("would_answer"))
    _has_organ = bool(row.get("organ"))
    if _has_answer:
        return "alias_correct" if _has_organ else "fastpath_hit"
    return "alias_miss" if _has_organ else "fastpath_miss"


def _iter_shadow_rows(shadow_path: str):
    if not os.path.isfile(shadow_path):
        return
    try:
        with open(shadow_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    yield json.loads(_line)
                except Exception:
                    continue
    except Exception as _e:
        silent_exc(_e, where="alias_labeler._iter_shadow_rows", level="debug")


def aggregate_label_stats(rows) -> dict:
    """聚合若干影子行 → 四类计数 + 总量。"""
    _stats = {_t: 0 for _t in LABEL_TYPES}
    _total = 0
    for _row in rows:
        _lb = label_shadow_row(_row)
        if _lb is None:
            continue
        _stats[_lb] += 1
        _total += 1
    _stats["total"] = _total
    return _stats


def daily_stats(date: str = None, shadow_path: str = None) -> dict:
    """按日期统计影子日志的四类分布。

    Args:
        date: "YYYY-MM-DD"；None → 用今天。
        shadow_path: 影子日志路径，默认 data/monitor/self_query_shadow.jsonl。
    """
    if shadow_path is None:
        shadow_path = os.path.join("data", "monitor", "self_query_shadow.jsonl")
    if date is None:
        import datetime
        date = datetime.datetime.now().strftime("%Y-%m-%d")
    _rows = []
    for _row in _iter_shadow_rows(shadow_path):
        _ts = str(_row.get("ts", ""))
        # ts 可能是浮点时间戳；按日期前缀匹配
        try:
            import datetime as _dt
            _ds = _dt.datetime.fromtimestamp(float(_ts)).strftime("%Y-%m-%d")
        except Exception:
            _ds = _ts[:10]
        if _ds == date:
            _rows.append(_row)
    _stats = aggregate_label_stats(_rows)
    _stats["date"] = date
    return _stats
