"""补丁历史去重与字段一致性 —— 主线第43批 T4（P1-280 / P1-279）。

背景（第42批 T2 实测）
----------------------
``data/patches/patch_history.json`` 共 **67 条记录**，但只有 **60 个唯一 id** ——
7 个 id 各有 2 条：先是 ``applied=False``（``apply_error=信任分数不足(30<60)``），
后是 ``applied=True``（10 秒内批量应用）。→ 统计口径混乱
（"总补丁数"、"成功率"、"质量分布"全部失真）。

本模块提供**纯函数**实现（可单测、不直接写盘）：
  · :func:`pick_keeper`  —— 同 id 多条中选"应保留"的一条
  · :func:`dedup_history` —— 全量去重，返回 (唯一列表, 被移除列表)
  · :func:`fix_detail_consistency` —— P1-279：``runtime_verify_result.detail``
    与 ``baseline``/``after_fix`` 字段矛盾时**按字段重写 detail**

保留规则（确定性，可解释）
--------------------------
1. **优先 ``applied=True``**（真正生效过的记录信息更完整）
2. 同为真/同为空 → 取**时间最新**者（``applied_at`` → ``saved_at`` →
   ``updated_at`` → ``generated_at`` → ``fixed_at`` 依次回退）
3. 仍相同 → 取**后出现**者（稳定）

★ 只读 + 纯函数：本模块**不写任何文件**；落盘由 ``tools/dedup_patch_history.py`` 负责
（带备份、幂等）。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "TIME_KEYS",
    "dedup_history",
    "dedup_stats",
    "fix_detail_consistency",
    "fix_details_in_history",
    "pick_keeper",
    "record_time",
]

#: 时间字段优先级（取第一个可用者）。
TIME_KEYS = ("applied_at", "saved_at", "updated_at", "generated_at", "fixed_at")


def record_time(rec: dict) -> float:
    """取记录时间（按 :data:`TIME_KEYS` 优先级）；全缺返回 ``0.0``。"""
    if not isinstance(rec, dict):
        return 0.0
    for _k in TIME_KEYS:
        _v = rec.get(_k)
        if isinstance(_v, bool) or not isinstance(_v, (int, float)):
            continue
        if _v:
            return float(_v)
    return 0.0


def pick_keeper(records: list) -> dict:
    """从**同一 id** 的多条记录中选出应保留的一条。

    Args:
        records: 同一 id 的记录列表（长度 >= 1）。

    Returns:
        应保留的记录；空列表返回 ``{}``。
    """
    _recs = [r for r in records if isinstance(r, dict)]
    if not _recs:
        return {}
    if len(_recs) == 1:
        return _recs[0]
    # ① applied=True 优先；② 时间最新；③ 后者优先
    _best = _recs[0]
    for _r in _recs[1:]:
        _a, _b = bool(_r.get("applied")), bool(_best.get("applied"))
        if _a != _b:
            if _a:
                _best = _r
            continue
        if record_time(_r) >= record_time(_best):
            _best = _r
    return _best


def dedup_history(hist: Any) -> tuple:
    """全量去重。

    Returns:
        ``(kept, removed)`` —— 去重后的列表（**保持首次出现顺序**）与被移除的记录列表。
        非 list 输入 → ``([], [])``。
    """
    if not isinstance(hist, list):
        return [], []
    # ★只有**非空 id** 才参与去重：缺 id 的记录不是"重复"，只是没有标识，
    #   把它们并入同一组会把 N 条压成 1 条（本批实测踩到：既有测试记录多无 id）。
    _by_id: dict = {}
    _keep_idx: dict = {}          # 原索引 → 保留的记录（用于还原顺序）
    _removed: list = []
    for _i, _r in enumerate(hist):
        if not isinstance(_r, dict):
            continue
        _id = str(_r.get("id") or "").strip()
        if not _id:
            _keep_idx[_i] = _r
            continue
        _by_id.setdefault(_id, []).append((_i, _r))
    for _id, _g in _by_id.items():
        if len(_g) == 1:
            _keep_idx[_g[0][0]] = _g[0][1]
            continue
        _k = pick_keeper([r for _i, r in _g])
        # ★保留者占据该 id **首次出现的位置** —— 去重后顺序与原顺序一致
        #   （若用保留者自身索引，会把"后出现的更优记录"挪到后面，顺序失真）。
        _keep_idx[_g[0][0]] = _k
        # ★被移除的是「所有非保留者」（不能写成 _g[1:]：保留者可能是后一条，
        #   那样会把保留者自己也列进 removed —— 本批实测踩到）。
        _removed.extend(_r for _i, _r in _g if _r is not _k)
    _kept = [_keep_idx[i] for i in sorted(_keep_idx)]
    return _kept, _removed


def dedup_stats(hist: Any) -> dict:
    """去重前后统计（供报告/日志）。"""
    _all = [r for r in hist if isinstance(r, dict)] if isinstance(hist, list) else []
    _kept, _removed = dedup_history(hist)
    _dup_ids = sorted({str(r.get("id")) for r in _removed})
    return {
        "before": len(_all),
        "after": len(_kept),
        "removed": len(_removed),
        "duplicate_ids": _dup_ids,
        "duplicate_id_count": len(_dup_ids),
    }


# ------------------------------------------------------------------ P1-279
def _fmt_eff(eff: float | None) -> str:
    if eff is None:
        return "不可判定"
    return "%.0f%%" % (eff * 100.0)


def fix_detail_consistency(rec: dict) -> bool:
    """★P1-279：让 ``runtime_verify_result.detail`` 与 ``baseline``/``after_fix`` 一致。

    症状（第42批实测 2 条）：``detail`` 写「修复前错误=0, 修复后错误=0, 效果=100%」，
    而 ``baseline``/``after_fix``/``effectiveness`` 已被第41批回填改写（如 4/4/0.0）
    → **同一记录内自相矛盾**。

    做法：**以字段为准**重写 detail（字段是可计算的，detail 只是文案）。
    ``baseline == 0`` 时效果标为「不可判定」（不写 100%）。

    Returns:
        是否发生了修改。
    """
    if not isinstance(rec, dict):
        return False
    _vr = rec.get("runtime_verify_result")
    if not isinstance(_vr, dict):
        return False
    _b = _vr.get("baseline")
    _a = _vr.get("after_fix")
    _eff = _vr.get("effectiveness")
    _has = any(isinstance(x, (int, float)) and not isinstance(x, bool)
               for x in (_b, _a, _eff))
    if not _has:
        return False
    _b = _b if isinstance(_b, (int, float)) and not isinstance(_b, bool) else None
    _a = _a if isinstance(_a, (int, float)) and not isinstance(_a, bool) else None
    _calc = None
    if _b is not None and _b > 0 and _a is not None:
        _calc = (_b - _a) / _b
    _new = "修复前错误={}, 修复后错误={}, 效果={}".format(
        "?" if _b is None else int(_b), "?" if _a is None else int(_a), _fmt_eff(_calc))
    _changed = str(_vr.get("detail") or "") != _new
    if _changed:
        _vr["detail"] = _new
    # ★T-99d：baseline=0 时 effectiveness 字段同步为 None，修复历史不一致
    # （此前只改 detail 文案为「不可判定」，字段仍记 1.0 假成功）。
    if _b == 0 and _vr.get("effectiveness") is not None:
        _vr["effectiveness"] = None
        _changed = True
    return _changed


def fix_details_in_history(hist: Any) -> tuple:
    """对整份历史执行 detail 一致性修复（原地修改 dict）。

    Returns:
        ``(fixed_count, fixed_ids)``。
    """
    _fixed, _ids = 0, []
    if not isinstance(hist, list):
        return 0, []
    for _r in hist:
        if fix_detail_consistency(_r):
            _fixed += 1
            _ids.append(str(_r.get("id")))
    return _fixed, _ids
