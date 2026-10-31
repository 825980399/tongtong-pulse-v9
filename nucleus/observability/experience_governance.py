# -*- coding: utf-8 -*-
"""经验池治理（★412 批刀2）—— 先查清 `content` 到底怎么回事，再治模板污染。

T0 实测（本刀最重要的一段，结论与任务书措辞不同）
------------------------------------------------
任务书说："经验池 379 条但 `content` 字段一条都没有，369 条是已摘要
（只有 summarized_text 没有原始 content）。"

实测：**条目里根本没有 `content` 这个字段**，且**也不存在 `summarized_text`**。
380 条经验的真字段是：

  * `summary`（380/380）—— 入库时由 `record_experience(content=...)` **截断前 200 字**
    直接落成（`experience_pool.py:179` `"summary": content[:200]`）；
  * `raw_summary` / `compressed_summary`（各 369）—— 47 批 T2 之后"压缩不覆盖原文"
    的产物；`is_summarized` 369 条为 True。

⇒ **`content` 是入参不是字段，这是设计如此，不是 bug**。本模块因此**不补 content**，
   只做两件事：①把这件事**变成可复核的读数**（`content_coverage()`）；
   ②治模板污染（打 `is_template` 标记 + 检索降权）。要不要真的加一个 content
   字段，留给 413（见交付报告 §待裁）。

为什么模板要标记而不是删
------------------------
411 实测：380 条里 125 条不同文本，最热那段模板占 19%，`with_content=0`。
删除会丢事实（模板也是"当时确实发生过"的证据），所以走**标记 + 降权**：
排序时模板类经验按 `TEMPLATE_WEIGHT` 打折，但不从池里消失、不阻断召回。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ExperienceGovernance")

_M412_EG_VERSION = "412.2"

#: 模板类经验在检索排序时的权重（1.0 = 不降权）
TEMPLATE_WEIGHT = 0.3

#: 同一归一化文本至少出现这么多次才算"模板"
TEMPLATE_MIN_DUP = 3

#: 归一化键长度（与 411刀4 `experience_hits` 同口径）
KEY_CHARS = 120

#: 灰度开关
SWITCH = "ENABLE_M412_EXPERIENCE_GOVERNANCE"

#: 一条经验的可读文本字段名（**按实测顺序**，不是任务书里的 content/summarized_text）
TEXT_FIELDS = ("summary", "raw_summary", "compressed_summary", "motivation")


def enabled() -> bool:
    """灰度开关 `ENABLE_M412_EXPERIENCE_GOVERNANCE`；缺省 True（只标记+降权，不删）。"""
    try:
        import config as _c
        return bool(getattr(_c, SWITCH, True))
    except Exception as _e:
        silent_exc(_e, where="observability.experience_governance:enabled")
        return True


def _pool_of(pool: Any = None, limit: int = 0):
    _p = pool
    try:
        if _p is None:
            from nucleus.mnemosyne.experience_pool import get_experience_pool
            _p = get_experience_pool()
        _rows = _p.query_experiences(limit=int(limit) if limit else 100000)
    except Exception as _e:
        silent_exc(_e, where="observability.experience_governance:_pool_of", level="debug")
        return None, []
    return _p, [x for x in (_rows or []) if isinstance(x, dict)]


def text_of(exp: dict) -> str:
    """一条经验的可读文本（★真字段名，不用 `content`）。"""
    if not isinstance(exp, dict):
        return ""
    for _k in TEXT_FIELDS:
        _v = exp.get(_k)
        if isinstance(_v, str) and _v.strip():
            return _v.strip()
    return ""


def norm_key(exp: dict) -> str:
    return re.sub(r"\s+", "", text_of(exp))[:KEY_CHARS]


# ------------------------------------------------------------------ ① content 真相

def content_coverage(pool: Any = None, limit: int = 0) -> dict[str, Any]:
    """`content` 字段的现状读数 —— **先查清再动手**。

    Returns:
        ``{"state", "total", "has_content_field", "content_non_empty",
           "summary_non_empty", "raw_summary_non_empty", "compressed_summary_non_empty",
           "is_summarized", "verdict", "reason"}``

        ★`verdict` 三值：**design**（只存摘要，无独立 content 字段 → 不必补）/
        **bug_or_partial**（字段存在却大面积为空 → 要补写入）/ **unknown**（读不到池）。
    """
    _out: dict[str, Any] = {"state": "unavailable", "total": 0,
                            "has_content_field": 0, "content_non_empty": 0,
                            "summary_non_empty": 0, "raw_summary_non_empty": 0,
                            "compressed_summary_non_empty": 0, "is_summarized": 0,
                            "verdict": "unknown", "reason": ""}
    _p, _rows = _pool_of(pool, limit)
    if _p is None:
        _out["reason"] = "query_failed"
        return _out
    if not _rows:
        _out["state"] = "empty"
        _out["verdict"] = "unknown"
        _out["reason"] = "pool_returned_nothing"
        return _out

    def _cnt(pred):
        return sum(1 for _x in _rows if pred(_x))

    _has = _cnt(lambda x: "content" in x)
    _ne = _cnt(lambda x: isinstance(x.get("content"), str) and x.get("content").strip())
    _sum = _cnt(lambda x: isinstance(x.get("summary"), str) and x.get("summary").strip())
    _raw = _cnt(lambda x: isinstance(x.get("raw_summary"), str) and x.get("raw_summary").strip())
    _cmp = _cnt(lambda x: isinstance(x.get("compressed_summary"), str)
                and x.get("compressed_summary").strip())
    _isz = _cnt(lambda x: bool(x.get("is_summarized")))
    _out.update({"state": "ok", "total": len(_rows), "has_content_field": _has,
                 "content_non_empty": _ne, "summary_non_empty": _sum,
                 "raw_summary_non_empty": _raw, "compressed_summary_non_empty": _cmp,
                 "is_summarized": _isz})
    if _has == 0 and _sum >= len(_rows) // 2:
        _out["verdict"] = "design"
        _out["reason"] = ("条目无独立 content 字段，summary 由入库入参截断而成 "
                          "（record_experience(content=...) → summary）⇒ 设计如此，不必补")
    elif _has > 0 and _ne < _has // 2:
        _out["verdict"] = "bug_or_partial"
        _out["reason"] = "content 字段存在但多数为空 ⇒ 要补写入，不是检索侧能解决的"
    elif _has > 0:
        _out["verdict"] = "design"
        _out["reason"] = "content 字段存在且多数非空 ⇒ 检索用 content 即可"
    else:
        _out["verdict"] = "unknown"
        _out["reason"] = "既无 content 也无足够 summary ⇒ 读写两侧都取不到，另查"
    return _out


# ------------------------------------------------------------------ ② 模板污染

def is_template(exp: dict) -> bool:
    """条目是否已被标记为模板（**只看标记**，不在这里猜）。"""
    return isinstance(exp, dict) and bool(exp.get("is_template"))


def retrieval_weight(exp: dict) -> float:
    """检索排序权重：模板类打折，其余 1.0（**降权不是删除**）。"""
    return TEMPLATE_WEIGHT if is_template(exp) else 1.0


def scan_templates(pool: Any = None, limit: int = 0,
                   min_dup: int = TEMPLATE_MIN_DUP) -> dict[str, Any]:
    """扫描模板污染（只读）。"""
    _out: dict[str, Any] = {"state": "unavailable", "total": 0, "distinct_texts": 0,
                            "template_keys": [], "template_ratio": None,
                            "already_marked": 0, "min_dup": int(min_dup), "reason": ""}
    _p, _rows = _pool_of(pool, limit)
    if _p is None:
        _out["reason"] = "query_failed"
        return _out
    if not _rows:
        _out["state"] = "empty"
        _out["reason"] = "pool_returned_nothing"
        return _out
    _c = Counter(norm_key(_x) for _x in _rows)
    _keys = [(k, n) for k, n in _c.most_common() if n >= max(2, int(min_dup))]
    _out.update({"state": "ok", "total": len(_rows), "distinct_texts": len(_c),
                 "template_keys": [{"key": _k[:60], "count": _n,
                                    "ratio": round(_n / len(_rows), 4)}
                                   for _k, _n in _keys[:10]],
                 "template_ratio": (round(_keys[0][1] / len(_rows), 4) if _keys else 0.0),
                 "already_marked": sum(1 for _x in _rows if is_template(_x))})
    return _out


def annotate(pool: Any = None, dry_run: bool = True, limit: int = 0,
             min_dup: int = TEMPLATE_MIN_DUP) -> dict[str, Any]:
    """给模板类经验打 `is_template` / `template_key`（**不删条目**）。

    Returns:
        ``{"ok", "scanned", "changed", "template_rows", "written", "reason"}``
    """
    if not enabled():
        return {"ok": False, "scanned": 0, "changed": 0, "template_rows": 0,
                "written": False, "reason": "disabled"}
    _p, _rows = _pool_of(pool, limit)
    if _p is None:
        return {"ok": False, "scanned": 0, "changed": 0, "template_rows": 0,
                "written": False, "reason": "query_failed"}
    if not _rows:
        return {"ok": True, "scanned": 0, "changed": 0, "template_rows": 0,
                "written": False, "reason": "pool_returned_nothing"}
    _c = Counter(norm_key(_x) for _x in _rows)
    _tpl = {_k for _k, _n in _c.items() if _n >= max(2, int(min_dup))}
    _changed = 0
    _n_rows = 0
    for _x in _rows:
        _k = norm_key(_x)
        _flag = _k in _tpl
        if _flag:
            _n_rows += 1
        if bool(_x.get("is_template")) != _flag or _flag and (
                _x.get("template_key") or "")[:KEY_CHARS] != _k:
            if not dry_run:
                _x["is_template"] = _flag
                if _flag:
                    _x["template_key"] = _k[:KEY_CHARS]
                else:
                    _x.pop("template_key", None)
            _changed += 1
    if dry_run or _changed == 0:
        return {"ok": True, "scanned": len(_rows), "changed": _changed,
                "template_rows": _n_rows, "written": False,
                "reason": ("dry_run" if dry_run else "no_change")}
    try:
        _p._save()
    except Exception as _e:
        silent_exc(_e, where="observability.experience_governance:annotate:save",
                   level="warning")
        return {"ok": False, "scanned": len(_rows), "changed": _changed,
                "template_rows": _n_rows, "written": False,
                "reason": "save_failed:%s" % type(_e).__name__}
    _module_logger.info("[412刀2] 模板标记：扫描 %d 条，标记 %d 条（改 %d）",
                        len(_rows), _n_rows, _changed)
    return {"ok": True, "scanned": len(_rows), "changed": _changed,
            "template_rows": _n_rows, "written": True, "reason": ""}


def version() -> str:
    return _M412_EG_VERSION
