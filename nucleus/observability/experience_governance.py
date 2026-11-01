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
        ``{"ok", "scanned", "changed", "template_rows", "applied",
           "written", "landed", "reason"}``

        ★`written` 只在**读回校验通过**时才为 True（413 刀4）。旧实现只要
          `_save()` 没抛异常就报 True，而写盘守卫是**静默 return** 的
          ⇒ "报成功、零落盘"（412 实测：`written=true` 但 `is_template` 落 0 条）。
        ★`applied` / `landed` 分开：前者是"回写原对象触及的条目数"，
          后者是"重新查一遍看到的已标记条数"。
    """
    if not enabled():
        return {"ok": False, "scanned": 0, "changed": 0, "template_rows": 0,
                "applied": 0, "written": False, "landed": 0, "reason": "disabled"}
    _p, _rows = _pool_of(pool, limit)
    if _p is None:
        return {"ok": False, "scanned": 0, "changed": 0, "template_rows": 0,
                "applied": 0, "written": False, "landed": 0, "reason": "query_failed"}
    if not _rows:
        return {"ok": True, "scanned": 0, "changed": 0, "template_rows": 0,
                "applied": 0, "written": False, "landed": 0,
                "reason": "pool_returned_nothing"}
    _c = Counter(norm_key(_x) for _x in _rows)
    _tpl = {_k for _k, _n in _c.items() if _n >= max(2, int(min_dup))}
    _changed = 0
    _n_rows = 0
    _flags: dict[str, dict[str, Any]] = {}
    for _x in _rows:
        _k = norm_key(_x)
        _flag = _k in _tpl
        if _flag:
            _n_rows += 1
        # 与现状有差异才动它（"上次标了、这次不该标"也算差异 ⇒ 清得掉）
        _dirty = (bool(_x.get("is_template")) != _flag
                  or (_flag and (_x.get("template_key") or "")[:KEY_CHARS] != _k))
        if not _dirty:
            continue
        if not dry_run:
            _x["is_template"] = _flag
            if _flag:
                _x["template_key"] = _k[:KEY_CHARS]
            else:
                _x.pop("template_key", None)
        _changed += 1
        _id = str(_x.get("id") or "")
        if _id:
            # ★只把**差异条目**交给回写层：全量交会把 398 条全写上
            #   `is_template=False`（语义等价，但白污染一遍池文件）
            _flags[_id] = ({"is_template": True, "template_key": _k[:KEY_CHARS]}
                           if _flag else {"is_template": False, "template_key": None})
    if dry_run or _changed == 0:
        return {"ok": True, "scanned": len(_rows), "changed": _changed,
                "template_rows": _n_rows, "applied": 0, "written": False, "landed": 0,
                "reason": ("dry_run" if dry_run else "no_change")}

    # ① 把标记回写到池里的**原对象**（`_rows` 是副本，改它没用）
    _applied = 0
    _apply = getattr(_p, "annotate_flags", None)
    if callable(_apply):
        try:
            _applied = int(_apply(_flags) or 0)
        except Exception as _e:
            silent_exc(_e, where="observability.experience_governance:annotate:apply",
                       level="warning")
            return {"ok": False, "scanned": len(_rows), "changed": _changed,
                    "template_rows": _n_rows, "applied": 0, "written": False,
                    "landed": 0, "reason": "apply_failed:%s" % type(_e).__name__}
    else:
        # 引用型池（返回值就是内部对象）⇒ 上面的循环已经改到了
        _applied = _changed

    # ② 先看写盘闸门：`_save()` 遇守卫是静默 return，不会抛异常
    _gw: Any = None
    _pf = getattr(_p, "_pool_file", None)
    if _pf is not None:
        try:
            from nucleus.data.write_guard import guard_write as _gw_fn
            _gw = _gw_fn(_pf,
                         explicit=bool(getattr(_p, "_m44_base_dir_explicit", False)),
                         component="ExperienceGovernance")
        except ImportError as _e_imp:
            silent_exc(_e_imp, where="observability.experience_governance:annotate:guard",
                       level="debug")
            _gw = None
    if _gw is False:
        return {"ok": False, "scanned": len(_rows), "changed": _changed,
                "template_rows": _n_rows, "applied": _applied, "written": False,
                "landed": 0, "reason": "write_guard_refused"}

    # ③ 落盘
    try:
        _p._save()
    except Exception as _e:
        silent_exc(_e, where="observability.experience_governance:annotate:save",
                   level="warning")
        return {"ok": False, "scanned": len(_rows), "changed": _changed,
                "template_rows": _n_rows, "applied": _applied, "written": False,
                "landed": 0, "reason": "save_failed:%s" % type(_e).__name__}

    # ④ **读回校验** —— 只有真落盘才报 written=True
    _landed = -1
    try:
        _back = _p.query_experiences(limit=len(_rows) + 1000) or []
        _landed = sum(1 for _x in _back if isinstance(_x, dict) and _x.get("is_template"))
    except Exception as _e:
        silent_exc(_e, where="observability.experience_governance:annotate:verify",
                   level="warning")
        _landed = -1

    _ok = (_landed == _n_rows)
    if not _ok:
        silent_exc(RuntimeError("annotate_verify_mismatch"),
                   where="observability.experience_governance:annotate:verify_mismatch",
                   level="warning")
    else:
        _module_logger.info("[412刀2] 模板标记：扫描 %d 条，标记 %d 条（改 %d）",
                            len(_rows), _n_rows, _changed)
    return {"ok": _ok, "scanned": len(_rows), "changed": _changed,
            "template_rows": _n_rows, "applied": _applied, "written": _ok,
            "landed": _landed,
            "reason": "" if _ok else "verify_mismatch:%s!=%d" % (_landed, _n_rows)}


def version() -> str:
    return _M412_EG_VERSION
