# -*- coding: utf-8 -*-
"""★366刀B4：问题池候选工单的**生成器侧读取方**（只读；不写盘、不改行、不删行）。

契约（星轨 2026-10-25 回读放行 `dc79a746`，8 条约束）：
  ① **白名单取字段**：只取六类最小集 `file/method/line`、`issue_type`、`severity`、`desc`
     （＋`m323_truth.method_before`、`dedup_key`）；其余池/候选字段**一律不进**上下文
     —— 黑名单会随字段膨胀漏排，白名单是结构保障；
  ② **读写不交叉**：`patch_candidates.jsonl` 由 `audit_problem_pool_consumer._route_patch_candidate`
     唯一写入（`consumer:519`），本模块**只读**；文件只增不改不删，不引入 `.consumed` 标记文件；
  ③ **键继承**：候选行 `id`（＝池 `dedup_key`）→ `entry.dedup_key`；缺失才用
     `audit_problem_pool.make_dedup_key` 兜底（**键公式单点**，不抄第二份 sha1）；
     不给历史 76 条补键、不从 verdict 反推键；
  ④ **跨拍去重边界**：单次运行内按 `candidate.id` 去重；跨拍/重启后查
     `pending_patches.json` 的 `patch_id`/`dedup_key` 反查"已入审批流"——
     这是**消费去重**，不是键继承（两件事，口径不混）；
  ⑤ 三闸不豁免：本模块只做取字段与去重，质量门/证据门/风险闸由生成链路照常判。

本模块**不生成补丁**（生成动作在 `patch_generator.generate_from_candidates`，受
`ENABLE_PATCH_GENERATOR` 控制，默认 False ⇒ 零行为变化）。
"""
from __future__ import annotations

import json
import os

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("patch_candidates_consumer")

#: 候选文件相对路径（与 `audit_problem_pool_consumer.PATCH_CANDIDATE_FILE` 同源语义）
_CANDIDATE_REL = os.path.join("data", "reports", "audit", "routes",
                              "patch_candidates.jsonl")
_PENDING_REL = os.path.join("data", "patches", "pending_patches.json")

#: ★契约①：进生成器上下文的**白名单**（其余字段一律丢弃）
CONTEXT_FIELDS = ("file", "method", "line", "issue_type", "severity", "desc")
#: 必填字段（缺 ⇒ 拒收重生成，不猜）
REQUIRED_FIELDS = ("file", "method", "issue_type", "desc")


def candidate_path(project_root: str | None = None) -> str:
    """候选 jsonl 路径（路径注入优先，铁律158）。"""
    if project_root:
        return os.path.join(str(project_root), _CANDIDATE_REL)
    _root = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(_root, _CANDIDATE_REL)


def _pending_keys(project_root: str | None = None,
                  path: str | None = None) -> tuple:
    """读已入审批流条目的 `patch_id` / `dedup_key` 集（契约④ 跨拍去重）。

    只读；文件不存在 ⇒ 两个空集（首次运行，不是错误）。
    """
    _p = path or (os.path.join(str(project_root), _PENDING_REL) if project_root
                  else os.path.join(os.path.dirname(os.path.dirname(
                      os.path.dirname(os.path.abspath(__file__)))), _PENDING_REL))
    _ids, _keys = set(), set()
    if not os.path.isfile(_p):
        return _ids, _keys
    try:
        with open(_p, encoding="utf-8") as f:
            _d = json.load(f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="patch_candidates_consumer::_pending_keys", level="warning")
        _module_logger.warning("[366B4] pending 台账不可读（跨拍去重降级为仅本次运行内去重）：%s: %s",
                               type(_e).__name__, _e)
        return _ids, _keys
    _rows = _d.get("patches") if isinstance(_d, dict) else _d
    for _r in (_rows or []):
        if not isinstance(_r, dict):
            continue
        if _r.get("patch_id"):
            _ids.add(str(_r["patch_id"]))
        if _r.get("dedup_key"):
            _keys.add(str(_r["dedup_key"]))
        if _r.get("id"):
            _ids.add(str(_r["id"]))
    return _ids, _keys


def _project_entry(row: dict) -> tuple:
    """★契约①：**白名单**投影一条候选 → 生成器 entry。

    Returns: ``(entry | None, reason)``；缺必填字段 ⇒ `(None, "missing:<字段>")`。
    """
    if not isinstance(row, dict):
        return None, "row_not_dict"
    _miss = [k for k in REQUIRED_FIELDS if not str(row.get(k) or "").strip()]
    if _miss:
        return None, "missing:%s" % ",".join(_miss)
    _entry = {k: row.get(k) for k in CONTEXT_FIELDS}
    _truth = row.get("m323_truth")
    if isinstance(_truth, dict) and _truth.get("method_before"):
        # 只取这一个子字段（B5 回填来源），不把整个 m323_truth 塞进上下文
        _entry["m323_truth"] = {"method_before": str(_truth["method_before"])}
    # ★契约③：键继承 —— 候选行 id 就是池 dedup_key；缺失才兜底（公式单点）
    _key = str(row.get("dedup_key") or row.get("id") or "").strip()
    if _key:
        _entry["dedup_key"] = _key
    else:
        from nucleus.audit_problem_pool import make_dedup_key
        _entry["dedup_key"] = make_dedup_key(
            _entry.get("file"), _entry.get("method"), _entry.get("issue_type"))
        _module_logger.info("[366B4] 候选缺 id/dedup_key ⇒ 用 make_dedup_key 兜底：%s",
                            _entry["dedup_key"])
    return _entry, "ok"


def consume_patch_candidates(limit: int = 5, project_root: str | None = None,
                             pending_path: str | None = None,
                             candidate_file: str | None = None) -> dict:
    """读取候选工单并把它们投影成生成器 entry（**只读**，不写任何文件）。

    Args:
        limit: 本次最多返回多少条**可用** entry（默认 5，防止一次性把池噪声灌进生成器）。
        pending_path: 仅测试注入（跨拍去重用）。
        candidate_file: 仅测试注入（候选 jsonl 路径）。

    Returns:
        ``{"state", "read", "used", "skipped_dup", "skipped_bad", "entries", "path", "why"}``
        `state`: ok / absent / unreadable / no_new
    """
    _p = candidate_file or candidate_path(project_root)
    if not os.path.isfile(_p):
        return {"state": "absent", "read": 0, "used": 0, "skipped_dup": 0,
                "skipped_bad": 0, "entries": [], "path": _p,
                "why": "候选文件不存在（消费段尚未产出工单）"}
    _ids, _keys = _pending_keys(project_root, pending_path)
    _seen: set = set()
    _entries, _dup, _bad = [], 0, 0
    _read = 0
    try:
        with open(_p, encoding="utf-8") as f:
            for _ln in f:
                _ln = _ln.strip()
                if not _ln:
                    continue
                _read += 1
                try:
                    _row = json.loads(_ln)
                except ValueError as _e:
                    _bad += 1
                    silent_exc(_e, where="patch_candidates_consumer::consume jsonl",
                               level="debug")
                    continue
                _id = str((_row or {}).get("id") or "").strip()
                # 契约④ 单次运行内去重 + 跨拍已入流跳过
                if _id and _id in _seen:
                    _dup += 1
                    continue
                if _id and (_id in _ids or _id in _keys):
                    _dup += 1
                    _seen.add(_id)
                    continue
                _entry, _why = _project_entry(_row)
                if _entry is None:
                    _bad += 1
                    _module_logger.info("[366B4] 候选拒收（%s）id=%s", _why, _id or "-")
                    continue
                if _id:
                    _seen.add(_id)
                _entries.append(_entry)
                if len(_entries) >= max(1, int(limit)):
                    break
    except OSError as _e:
        silent_exc(_e, where="patch_candidates_consumer::consume open", level="warning")
        _module_logger.warning("[366B4] 候选文件不可读 %s：%s: %s", _p,
                               type(_e).__name__, _e)
        return {"state": "unreadable", "read": _read, "used": 0, "skipped_dup": _dup,
                "skipped_bad": _bad, "entries": [], "path": _p,
                "why": "%s: %s" % (type(_e).__name__, _e)}
    _state = "ok" if _entries else "no_new"
    return {"state": _state, "read": _read, "used": len(_entries),
            "skipped_dup": _dup, "skipped_bad": _bad, "entries": _entries,
            "path": _p, "why": ""}
