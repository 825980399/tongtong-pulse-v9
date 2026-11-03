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

import time
from typing import Any

__all__ = [
    "TERMINAL_STATUSES",
    "TIME_KEYS",
    "attempt_ts",
    "dedup_history",
    "dedup_stats",
    "fix_detail_consistency",
    "fix_details_in_history",
    "merge_group",
    "pick_keeper",
    "record_time",
    "terminal_ts",
    "upsert_by_id",
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


# ======================================================================
# ★433刀2：同 id 历史快照合并（upsert by id）
# ======================================================================
# 病因（433 T0 实测）：`patch_history` 是 append-only，同一补丁的两个生命周期快照
# 被追加成两行。盘上实测 1 组：`o3_1792638584_57885429` 在 index 85
# （status=wont_fix，status_history 长 1）与 index 86（status=approved，长 0），
# 除 status / status_history / o3_auto_dispatch_ready / 验证结论外其余字段逐字相同。
# 两行的后果：
#   · 账本 `history_total` 虚高、`real_fix_rate` 分母多算；
#   · `verify_applied_patches` 把同一补丁验两遍、`total` 多计；
#   · `clear_cancelled_pending` 的 `_wont` 映射按 id 建表，后写者覆盖前者 ⇒
#     撤单信号可能被 approved 快照顶掉。
# 本组是**纯函数**（不读盘、不写盘），落盘与一次性迁移由调用方负责。

#: 人工终态 —— 出现即代表「人已裁决」。
TERMINAL_STATUSES = ("wont_fix", "wontfix", "cancelled", "revoked",
                     "rejected", "rolled_back")

#: 各终态对应的裁决时刻字段（缺字段时回退 status_history / record_time）。
_TERMINAL_TS_KEYS = {
    "wont_fix": ("wont_fix_at",),
    "wontfix": ("wont_fix_at",),
    "cancelled": ("cancelled_at",),
    "revoked": ("revoked_at",),
    "rejected": ("rejected_at",),
    "rolled_back": ("rolled_back_at",),
}

#: ★433刀2 合并策略：True ⇒ 组内出现人工终态时以终态为准（默认）。
#: 置 False 则逐字退回「基底 status 原样保留」，用于 A/B 对照与紧急回滚。
TERMINAL_STATUS_WINS = True


def _is_blank(v: Any) -> bool:
    """空值判定（``None`` / 空串 / 空容器）。``0`` 与 ``False`` **不算空**。"""
    return v is None or v == "" or v == [] or v == {}


def attempt_ts(rec: dict) -> float:
    """该记录的 ``runtime_verify_last_attempt_at``（缺失/非法 ⇒ ``0.0``）。"""
    if not isinstance(rec, dict):
        return 0.0
    _v = rec.get("runtime_verify_last_attempt_at")
    if isinstance(_v, bool) or not isinstance(_v, (int, float)):
        return 0.0
    return float(_v) if _v > 0 else 0.0


def terminal_ts(rec: dict):
    """该记录**当前 status** 若为人工终态 → 返回裁决时刻；否则 ``None``。

    时刻优先取状态专属字段（``wont_fix_at`` 等）；缺失时回退
    ``status_history`` 里最后一条同状态记录的 ``at``；再缺失回退 ``record_time``。
    """
    if not isinstance(rec, dict):
        return None
    _st = str(rec.get("status") or "").strip().lower()
    if _st not in TERMINAL_STATUSES:
        return None
    _ts = 0.0
    for _k in _TERMINAL_TS_KEYS.get(_st, ()):
        _v = rec.get(_k)
        if isinstance(_v, bool) or not isinstance(_v, (int, float)):
            continue
        if _v > 0:
            _ts = max(_ts, float(_v))
    if _ts <= 0:
        for _h in rec.get("status_history") or []:
            if (isinstance(_h, dict)
                    and str(_h.get("status") or "").strip().lower() == _st):
                _v = _h.get("at")
                if isinstance(_v, (int, float)) and not isinstance(_v, bool):
                    _ts = max(_ts, float(_v))
    return _ts if _ts > 0 else record_time(rec)


def merge_group(group: list, now: float | None = None) -> dict:
    """把**同一 id** 的多条生命周期快照合并成一条（纯函数，不写盘）。

    规则（确定性、可解释）：
      1. **基底**取 ``runtime_verify_last_attempt_at`` 最大的一条 —— 它带着最新的
         验证结论（失败原因/模式/fail_count），是这条补丁的"数据载体"；
         并列取后出现者（稳定）。
      2. 基底为空的字段用其余行的非空值补 —— **不覆盖**基底已有的真值。
      3. ``status_history`` 取并集（按 ``(status, at)`` 去重、按时间升序）。
      4. ``wont_fix_*`` 三字段任一行为空即取非空者（撤单理由不得因合并丢失）。
      5. **状态**：组内存在人工终态时取**裁决时刻最晚**的那个终态（``0/1`` 由
         :data:`TERMINAL_STATUS_WINS` 控制），并把 ``o3_auto_dispatch_ready``
         置 False —— 348刀10 + 350刀4 + 385 的判据都要求"撤单后不得再派单"。
      6. 落 ``m433_merged_from`` 留痕（条数 / 来源 id / 时刻 / 批次）。

    ★为什么终态优先于基底 status：基底只按"验证数据最全"选，与"人最后怎么裁"
      无关。实测那组里 wont_fix 的 ``wont_fix_at``=1792740262 晚于基底
      （approved 行）的 ``runtime_verify_last_attempt_at``=1792736813 ——
      若按基底保留 approved，`clear_cancelled_pending` 的 `_wont` 映射会丢掉
      这个 id，那条补丁就重新具备被派单的资格。
    """
    _recs = [r for r in group if isinstance(r, dict)]
    if not _recs:
        return {}
    if len(_recs) == 1:
        return dict(_recs[0])

    # ① 基底：验证结论最新最全的一条
    _base = _recs[0]
    for _r in _recs[1:]:
        if attempt_ts(_r) >= attempt_ts(_base):
            _base = _r
    _out = dict(_base)

    # ② 其余行的非空字段只补基底的空缺
    for _r in _recs:
        if _r is _base:
            continue
        for _k, _v in _r.items():
            if _k in ("status", "status_history"):
                continue
            if _is_blank(_out.get(_k)) and not _is_blank(_v):
                _out[_k] = _v

    # ③ status_history：并集
    _sh, _seen = [], set()
    for _r in _recs:
        for _h in _r.get("status_history") or []:
            if not isinstance(_h, dict):
                continue
            _key = (str(_h.get("status") or ""), repr(_h.get("at")))
            if _key in _seen:
                continue
            _seen.add(_key)
            _sh.append(dict(_h))
    if _sh:
        _sh.sort(key=lambda _x: float(_x.get("at") or 0.0))
        _out["status_history"] = _sh

    # ④ wont_fix_*：撤单理由不得因合并丢失
    for _k in ("wont_fix_reason", "wont_fix_at", "wont_fix_by"):
        if not _is_blank(_out.get(_k)):
            continue
        for _r in _recs:
            if not _is_blank(_r.get(_k)):
                _out[_k] = _r[_k]
                break

    # ⑤ 状态：人工终态优先
    _tstat, _tts = "", None
    if TERMINAL_STATUS_WINS:
        for _r in _recs:
            _ts = terminal_ts(_r)
            if _ts is None:
                continue
            if _tts is None or _ts >= _tts:
                _tstat, _tts = str(_r.get("status") or "").strip().lower(), _ts
    if _tstat:
        _out["status"] = _tstat
        _out["o3_auto_dispatch_ready"] = False

    # ⑥ 追溯
    _out["m433_merged_from"] = {
        "n": len(_recs),
        "ids": [str(_r.get("id") or "") for _r in _recs],
        "base_attempt_at": attempt_ts(_base),
        "at": float(now if now is not None else time.time()),
        "batch": "433",
    }
    return _out


def upsert_by_id(hist: Any, now: float | None = None) -> tuple:
    """按 id upsert：同一 id 的多个快照合并成一条（幂等，保持首次出现位置）。

    ★同一实现同时服务于**落盘端**（`PatchManager._save_patch_list`）与
      **读入端**（`_load_patch_list` / `verify_applied_patches`）——
      两端用同一规则才不会出现"写进去 1 行、读出来 2 条"。

    Returns:
        ``(kept, stats)``。``stats`` = ``{before, after, merged, merged_ids}``。
        非 list 输入 → ``(原输入, 空统计)``。
    """
    if not isinstance(hist, list):
        return hist, {"before": 0, "after": 0, "merged": 0, "merged_ids": []}
    _out: list = []
    _groups: dict = {}
    _pos: dict = {}
    for _r in hist:
        if not isinstance(_r, dict):
            _out.append(_r)
            continue
        _id = str(_r.get("id") or "").strip()
        if not _id:
            # 无 id 的记录不是"重复"，只是没有标识（与 dedup_history 同裁决）
            _out.append(_r)
            continue
        if _id not in _groups:
            _groups[_id] = []
            _pos[_id] = len(_out)
            _out.append(None)
        _groups[_id].append(_r)
    _merged_ids = []
    for _id, _g in _groups.items():
        if len(_g) == 1:
            _out[_pos[_id]] = _g[0]
            continue
        _out[_pos[_id]] = merge_group(_g, now=now)
        _merged_ids.append(_id)
    return _out, {
        "before": len(hist),
        "after": len(_out),
        "merged": len(_merged_ids),
        "merged_ids": _merged_ids,
    }