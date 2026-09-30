# -*- coding: utf-8 -*-
"""经验库清洗与 L3 检索闸门 —— 主线第50批 T2（P0-3）

背景（P0-3：SERP 污染锁死 L3）
-----------------------------
第46批实测：经验库 1501 条中 **1140 条（75.9%）污染**，其中
**953 条是摘要模板的产物**（``summary`` 被覆盖成 15 种模板句之一，
Top1 出现 791 次；``raw_summary`` **全为空** → 原文已永久丢失、信息量为 0）。

★**关键实测（第50批）**：生产检索路径
（``main.py`` / ``PulseNarrativeSelf`` / ``PulseMotivationCycle`` / ``PulseGlobalLearner``）
调用的是 ``ExperiencePool`` 的裸查询方法（``query_experiences`` /
``get_experiences_for_narrative`` / ``get_positive_experiences`` /
``get_negative_experiences``）—— **均不过滤 ``polluted``**。
而 ``config.EXPERIENCE_RETRIEVER_FILTER_POLLUTED`` 属于
``nucleus/mnemosyne/experience_retriever.py``，该模块在 config 中**明示为
「仅观测，不替换现有检索逻辑」** → 生产链路**确实**被污染记录灌入。

本模块提供两件事
----------------
1. **清洗（标记策略，不删除）**：``mark_polluted()`` 给污染记录打上
   ``is_cleaned=False`` / ``pollution_risk="high"`` / ``cleanup_reason`` 等标记。
   ★ 数据**一条不删** —— 完整备份 + 可 ``restore()`` 回滚。
2. **检索闸门**：``is_retrievable()`` / ``filter_retrievable()``，
   由 ``ExperiencePool`` 的 4 个查询方法调用（开关
   ``ENABLE_EXPERIENCE_CLEANUP_FILTER``，默认开）。

★**零回归保证**：闸门只在记录**显式**带 ``is_cleaned=False`` 或
``polluted=True`` 时拦截；未标记的记录行为与改造前**完全一致**。

核心接口
--------
* :func:`is_retrievable` —— 单条记录是否可被 L3 检索（**闸门判据**）
* :func:`filter_retrievable` —— 列表过滤（供 ``ExperiencePool`` 4 个查询方法调用）
* :func:`classify` —— 污染形态分类（``template`` / ``write_side`` / ``other``）
* :func:`mark_polluted` —— 批量**标记**（不删除，写入 ``is_cleaned=False`` /
  ``pollution_risk`` / ``cleanup_batch``）
* :func:`restore` —— 按批次号**回滚**标记
* :func:`stats` —— 清洗前后统计
* :func:`filter_enabled` —— 读开关 ``ENABLE_EXPERIENCE_CLEANUP_FILTER``

使用示例
--------
标记与回滚（★一条不删，完整可回滚）::

    from nucleus.data.experience_cleanup import (
        mark_polluted, restore, stats, is_retrievable)
    recs = [...]                                  # 经验记录列表
    report = mark_polluted(recs, batch_no=50)     # 标记（不删数据）
    stats(recs)                                   # {'polluted': ..., 'clean': ...}
    restore(recs, batch_no=50)                    # 回滚该批次标记

单条闸门判据（未标记记录一律放行 → 零回归）::

    is_retrievable({"summary": "正常经验"})        # True
    is_retrievable({"polluted": True})            # False
    is_retrievable({"is_cleaned": False})         # False
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
from nucleus._silent_except import silent_exc

__all__ = [
    "F_IS_CLEANED", "F_POLLUTION_RISK", "F_CLEANUP_REASON", "F_CLEANUP_BATCH",
    "F_CLEANUP_AT", "CLASS_TEMPLATE", "CLASS_WRITE_SIDE", "CLASS_OTHER",
    "classify", "is_retrievable", "filter_retrievable", "filter_enabled",
    "mark_polluted", "restore", "cleanup_file", "rollback_file", "stats",
]

# ---------------------------------------------------------------- 字段名
F_IS_CLEANED = "is_cleaned"
F_POLLUTION_RISK = "pollution_risk"
F_CLEANUP_REASON = "cleanup_reason"
F_CLEANUP_BATCH = "cleanup_batch"
F_CLEANUP_AT = "cleanup_at"

#: 清洗分类
CLASS_TEMPLATE = "template_summary_legacy"   # 摘要模板产物（原文已丢失）
CLASS_WRITE_SIDE = "write_side_boilerplate"  # 写入侧样板短句（元数据式）
CLASS_OTHER = "other_polluted"

DEFAULT_SWITCH = "ENABLE_EXPERIENCE_CLEANUP_FILTER"

#: 摘要模板句（第46批实测：953 条，仅 15 种）
_TPL = re.compile(r"^我曾因.{1,50}而行动，获得了\w+奖赏(?:，感受到.{1,20})?$")

#: 写入侧样板短句（元数据式，无实质内容）
_BOILERPLATE = (
    "动机循环内部评估",
    "完成了一次全域自学习审计",
    "复盘确认对话质量良好",
    "感受到满足",
)


def filter_enabled() -> bool:
    """清洗闸门开关（默认 True；关闭 → 与改造前完全一致）。"""
    try:
        import config as _c
        return bool(getattr(_c, DEFAULT_SWITCH, True))
    except Exception as e:
        silent_exc(e, where="nucleus.data.experience_cleanup::filter_enabled L108")
        return True


# ---------------------------------------------------------------- 分类
def classify(exp: dict) -> str:
    """给一条污染记录分类（用于 ``cleanup_reason`` 与统计）。"""
    if not isinstance(exp, dict):
        return CLASS_OTHER
    _s = str(exp.get("summary") or "")
    if _TPL.match(_s):
        return CLASS_TEMPLATE
    for _b in _BOILERPLATE:
        if _b and _b in _s:
            return CLASS_WRITE_SIDE
    return CLASS_OTHER


# ---------------------------------------------------------------- 闸门
def is_retrievable(exp: dict) -> bool:
    """★L3 检索闸门：该条记录是否可供生产检索使用。

    拦截条件（**仅显式标记**才拦，未标记的记录零影响）：
      * ``polluted is True``
      * ``is_cleaned is False``
    """
    if not isinstance(exp, dict):
        return False
    if exp.get("polluted") is True:
        return False
    if exp.get(F_IS_CLEANED) is False:
        return False
    return True


def filter_retrievable(records) -> list:
    """按闸门过滤（开关关闭时原样返回）。"""
    _lst = list(records or [])
    if not filter_enabled():
        return _lst
    return [e for e in _lst if is_retrievable(e)]


# ---------------------------------------------------------------- 标记 / 回滚
def mark_polluted(records, batch_no: int = 50, dry_run: bool = False,
                  now: float | None = None) -> dict:
    """★标记策略：给污染记录打标，**不删除任何数据**。

    写入字段：``is_cleaned=False``、``pollution_risk="high"``、
    ``cleanup_reason``、``cleanup_batch``、``cleanup_at``。
    ★``polluted`` 字段**原样保留**（既有筛选/度量不受影响，且便于回滚）。
    """
    _ts = time.time() if now is None else now
    _hit = 0
    _by_cls: dict[str, int] = {}
    _already = 0
    for e in records or []:
        if not isinstance(e, dict):
            continue
        if e.get("polluted") is not True:
            continue
        if e.get(F_IS_CLEANED) is False and e.get(F_CLEANUP_BATCH) == batch_no:
            _already += 1
            continue
        _cls = classify(e)
        _by_cls[_cls] = _by_cls.get(_cls, 0) + 1
        _hit += 1
        if dry_run:
            continue
        e[F_IS_CLEANED] = False
        e[F_POLLUTION_RISK] = "high"
        e[F_CLEANUP_REASON] = _cls
        e[F_CLEANUP_BATCH] = batch_no
        e[F_CLEANUP_AT] = _ts
    return {"marked": _hit, "already_marked": _already,
            "by_class": _by_cls, "dry_run": dry_run, "batch": batch_no}


def restore(records, batch_no: int | None = None) -> dict:
    """回滚标记（幂等）：移除本模块写入的清洗字段。

    ★不触碰 ``polluted`` 等既有字段。
    """
    _n = 0
    for e in records or []:
        if not isinstance(e, dict):
            continue
        if batch_no is not None and e.get(F_CLEANUP_BATCH) != batch_no:
            continue
        _changed = False
        for _k in (F_IS_CLEANED, F_POLLUTION_RISK, F_CLEANUP_REASON,
                   F_CLEANUP_BATCH, F_CLEANUP_AT):
            if _k in e:
                del e[_k]
                _changed = True
        if _changed:
            _n += 1
    return {"restored": _n, "batch": batch_no}


# ---------------------------------------------------------------- 统计
def stats(records) -> dict:
    """清洗前后状态统计。"""
    _all = [e for e in (records or []) if isinstance(e, dict)]
    _pol = [e for e in _all if e.get("polluted") is True]
    _marked = [e for e in _all if e.get(F_IS_CLEANED) is False]
    _by_cls: dict[str, int] = {}
    for e in _marked:
        _c = str(e.get(F_CLEANUP_REASON) or "unknown")
        _by_cls[_c] = _by_cls.get(_c, 0) + 1
    _retr = [e for e in _all if is_retrievable(e)]
    return {
        "total": len(_all),
        "polluted": len(_pol),
        "polluted_rate": round(len(_pol) / max(1, len(_all)), 4),
        "marked_not_cleaned": len(_marked),
        "marked_by_class": _by_cls,
        "retrievable": len(_retr),
        "retrievable_rate": round(len(_retr) / max(1, len(_all)), 4),
    }


# ---------------------------------------------------------------- 文件级操作
def _read_pool(path: str) -> dict:
    return json.load(io.open(path, encoding="utf-8"))


def _sha256(path: str) -> str:
    return hashlib.sha256(io.open(path, "rb").read()).hexdigest()


def cleanup_file(path: str, batch_no: int = 50, backup: str | None = None,
                 dry_run: bool = False) -> dict:
    """对经验库文件执行清洗（标记策略 + 可选备份 + sha256 校验）。

    Returns:
        dict: 含 ``backup`` / ``sha256_before`` / ``sha256_backup`` /
              ``stats_before`` / ``stats_after`` / ``marked`` 等。
    """
    _out: dict = {"path": path, "dry_run": dry_run, "batch": batch_no}
    if not os.path.isfile(path):
        _out["error"] = "文件不存在"
        return _out
    _out["sha256_before"] = _sha256(path)

    if backup and not dry_run:
        if not os.path.isfile(backup):
            shutil.copy2(path, backup)
            _out["backup_created"] = True
        else:
            _out["backup_existing"] = True
        _out["backup"] = backup
        _out["sha256_backup"] = _sha256(backup)
        _out["backup_matches_source"] = (_out["sha256_backup"] == _out["sha256_before"])

    _d = _read_pool(path)
    _rec = _d.get("experiences", []) if isinstance(_d, dict) else []
    _out["stats_before"] = stats(_rec)
    _out["mark"] = mark_polluted(_rec, batch_no=batch_no, dry_run=dry_run)
    _out["stats_after"] = stats(_rec)

    if not dry_run:
        _d["experiences"] = _rec
        _d["cleanup_batch"] = batch_no
        _d["cleanup_at"] = time.time()
        _tmp = path + ".m50tmp"
        with io.open(_tmp, "w", encoding="utf-8") as f:
            json.dump(_d, f, ensure_ascii=False, indent=2)
        os.replace(_tmp, path)
        _out["sha256_after"] = _sha256(path)
    return _out


def rollback_file(path: str, backup: str) -> dict:
    """从备份整体回滚（校验 sha256 后覆盖）。"""
    if not os.path.isfile(backup):
        return {"error": "备份不存在", "backup": backup}
    _h = _sha256(backup)
    shutil.copy2(backup, path)
    return {"rolled_back": True, "backup": backup,
            "sha256_backup": _h, "sha256_restored": _sha256(path)}


if __name__ == "__main__":   # pragma: no cover - 手工自检
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _P = os.path.join(_ROOT, "data", "experience", "experience_pool.json")
    print(json.dumps(cleanup_file(_P, dry_run=True), ensure_ascii=False, indent=1)[:1500])
