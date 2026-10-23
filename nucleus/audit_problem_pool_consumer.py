# -*- coding: utf-8 -*-
"""audit_problem_pool_consumer.py —— 问题池「行动消费口」（第346批 刀1 · P0 主线）。

## 为什么需要本模块（洞鉴「发现→行动无桥」的真实断点）

345 批把池落了盘（`audit_problem_pool.persist_pool`）、也接回了读（`restart_continuity`），
但**没有任何一处把池里的条目变成"要做的事"**：

    code_defect 不会转成补丁候选 · experience 不转优化项 · pollution 不转清理动作

于是「发现」与「行动」之间仍然断在"落盘→行动"这一段——池只是个越积越多的账本。
本模块补的就是这一段，且**只做一段**：读池 → 分流 → 挂到既有行动面 → 状态推进。
**不重建池、不改审查、不动分诊链路**。

## 三条设计约束（都不是风格问题）

1. **不阻塞审查主链**：`consume_pool` 由 `main.py` 的主循环节拍调用（每
   `_POOL_CONSUME_TICKS` 秒），**不在** `run_parallel_audit` 的调用栈里。审查该多久
   还是多久，消费慢只影响消费自己。
2. **池空不造假**：无快照 / 无 open 条目 ⇒ `state="noop_pool_empty"`，
   **不返回 0 消费冒充"跑过了"**（铁律156/166：没生效与生效但产出 0 必须可区分）。
3. **不伪造代码**：池条目只有 `file/method/line/desc`，**没有任何代码文本**。
   所以 `code_defect` 的产物是**候选工单**（`state="candidate_pending_approval"`，
   明确缺 `original_code`/`modified_code`），**不是**可直接审批的补丁；
   只有当条目自身携带完整代码时才真正落进 `PatchManager.save_pending_patch` 的
   审批流（`status="pending"`，**永不 auto_apply**）。

## 状态推进与幂等

`open → claimed`（消费中）。被消费条目在池里留痕
`[346池] consume <id> → <route>`（写进 `status_history[].note`），
下轮只取 `status=="open"` ⇒ **不重复消费**。
路由文件按 `id` 去重追加 ⇒ 即使状态推进失败，重跑也不会产生重复工单。

## 与 345 刀2「三类出口」的分工

- `audit_problem_pool.export_routes`：**出口快照**（每拍整体重写，回答"池里各出口有哪些"）。
- 本模块：**行动队列**（按天追加、按 id 去重，回答"已经被认领去做的事有哪些"）。
两者不互相替代：前者是视图，后者是工单。
"""

from __future__ import annotations

import json
import os
import time

from nucleus import audit_problem_pool as _pool
from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger(__name__)

#: 每拍消费上限（任务书建议 ≤10）。★只限制"本拍认领多少"，
#: **不是**"只看前 N 条"——筛选必须先遍历全池再截断（铁律163）。
CONSUME_BATCH_MAX = 10

#: 每拍最多打印多少条 trace（防刷屏；未打印的计数如实报出）
TRACE_PRINT_MAX = 20

#: 优化项 / 清理队列落盘目录（相对项目根）
OPTIMIZE_DIR_LITERAL = "data/reports/optimize"
CLEANUP_DIR_LITERAL = "data/reports/cleanup"

OPTIMIZE_DIR_REL = os.path.join(*OPTIMIZE_DIR_LITERAL.split("/"))
CLEANUP_DIR_REL = os.path.join(*CLEANUP_DIR_LITERAL.split("/"))

#: 补丁候选工单文件名（落在池目录下的 routes/，与 345 三类出口同处可 grep）
PATCH_CANDIDATE_FILE = "patch_candidates.jsonl"

#: 消费 trace 前缀（可 grep 的唯一标记）
TRACE_TAG = "[346池] consume"

#: severity 排序权重（高优先被认领）
_SEV_WEIGHT = {"high": 3, "medium": 2, "low": 1}


def _project_root() -> str:
    """项目根（★346刀6：与 `audit_problem_pool._project_root` 同一修法）。

    本模块同样在 `nucleus/` 下 ⇒ 只上溯两级就是项目根。硬编码层级会让
    默认路径落到**仓库父目录**（345 遗留问题，346 刀6 实证并修正）。
    """
    _cur = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _first = _cur
    for _ in range(4):
        if (os.path.isfile(os.path.join(_cur, "main.py"))
                and os.path.isfile(os.path.join(_cur, "config.py"))):
            return _cur
        _parent = os.path.dirname(_cur)
        if _parent == _cur:
            break
        _cur = _parent
    _module_logger.warning(
        "[346池] 消费口项目根定位失败（无 main.py/config.py），退回 %s", _first)
    return _first


def _enabled(explicit: bool | None = None) -> bool:
    """灰度开关：显式参数 > 环境变量 `PULSE_POOL_CONSUME=0` > `config` > 默认 True。"""
    if explicit is not None:
        return bool(explicit)
    _env = os.environ.get("PULSE_POOL_CONSUME", "").strip()
    if _env in ("0", "false", "False", "off"):
        return False
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_AUDIT_POOL_CONSUME", True))
    except Exception as _e:
        silent_exc(_e, "audit_problem_pool_consumer._enabled 读开关", level="debug")
        return True


def _dir_of(kind: str, root: str | None = None) -> str:
    """产物目录（相对项目根；隔离时可经 `root` 注入）。"""
    _root = str(root) if root else _project_root()
    return os.path.join(_root, OPTIMIZE_DIR_REL if kind == "optimize" else CLEANUP_DIR_REL)


def _sev_rank(entry: dict) -> int:
    return _SEV_WEIGHT.get(str(entry.get("severity") or "").lower(), 1)


def _dedup_key_of(entry: dict) -> str:
    return str(entry.get("dedup_key") or entry.get("id") or "")


def select_consumable(entries: list, limit: int = CONSUME_BATCH_MAX) -> list:
    """从**整池**里挑出待消费条目（★先遍历全池，再排序，最后截断）。

    只取 `status == "open"`：`claimed` 及以上视为"已被认领"，不重复消费；
    `fixed/verified/wontfix` 是终态或处置中，同样不取。

    排序：severity 高优先 → first_seen 早优先（老问题先做）→ dedup_key（保证确定性）。
    """
    _open: list = []
    for _e in entries:
        if not isinstance(_e, dict):
            continue
        if str(_e.get("status") or _pool.STATUS_OPEN) != _pool.STATUS_OPEN:
            continue
        if not _dedup_key_of(_e):
            continue
        _open.append(_e)
    _open.sort(key=lambda e: (-_sev_rank(e), _pool._first_ts(e) or 0.0, _dedup_key_of(e)))
    _n = max(0, int(limit))
    return _open[:_n]


def _read_jsonl_ids(path: str) -> set:
    """读既有 jsonl 的 id 集合（去重用）。读不到返回空集（首次运行合法）。"""
    _ids: set = set()
    if not os.path.isfile(path):
        return _ids
    try:
        with open(path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _d = json.loads(_line)
                except ValueError:
                    continue
                if isinstance(_d, dict) and _d.get("id"):
                    _ids.add(str(_d["id"]))
    except OSError as _e:
        silent_exc(_e, "audit_problem_pool_consumer._read_jsonl_ids 读失败", level="warning")
    return _ids


def _append_jsonl_dedup(path: str, records: list) -> dict:
    """按 `id` 去重追加（读旧 → 合并 → `.tmp` → `os.replace`）。

    ★写盘一律「先算 data → 写 .tmp → os.replace」，绝不 `open(p,"wb").write(expr)`
    （铁律182：`open` 先截断、表达式后求值，一抛异常就留 0 字节）。
    """
    _out = {"path": path, "appended": 0, "skipped": 0, "ok": False}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as _e:
        silent_exc(_e, "audit_problem_pool_consumer._append_jsonl_dedup 建目录", level="warning")
        return _out
    _seen = _read_jsonl_ids(path)
    _lines: list = []
    for _r in records:
        _id = str((_r or {}).get("id") or "")
        if not _id or _id in _seen:
            _out["skipped"] += 1
            continue
        _seen.add(_id)
        _lines.append(json.dumps(_r, ensure_ascii=False))
        _out["appended"] += 1
    if not _lines:
        _out["ok"] = True
        return _out
    try:
        _old = b""
        if os.path.isfile(path):
            with open(path, "rb") as _f:
                _old = _f.read()
        _payload = _old + ("\n".join(_lines) + "\n").encode("utf-8")
        _tmp = path + ".tmp"
        with open(_tmp, "wb") as _f:
            _f.write(_payload)
        os.replace(_tmp, path)
        _out["ok"] = True
    except OSError as _e:
        silent_exc(_e, "audit_problem_pool_consumer._append_jsonl_dedup 写失败", level="warning")
    return _out


# ========== 三族分流 ==========


def _route_patch_candidate(entry: dict, root: str | None, now: float) -> dict:
    """`code_defect` → 补丁候选工单；带完整代码时进 `save_pending_patch` 审批流。

    ★「候选」与「补丁」必须可区分（铁律166）：池条目没有代码，产物只能是工单。
    真落审批流的唯一条件是条目**自带** `original_code` + `modified_code` + `method`。
    """
    _id = _dedup_key_of(entry)
    _rec = {
        "id": _id,
        "source": "audit_problem_pool",
        "route": _pool.ROUTE_PATCH_POOL,
        "issue_type": entry.get("issue_type"),
        "severity": entry.get("severity"),
        "file": entry.get("file"),
        "line": entry.get("line"),
        "method": entry.get("method"),
        "desc": entry.get("desc"),
        "state": "candidate_pending_approval",
        "approval_entry": "PatchManager.save_pending_patch",
        "consumed_at": now,
        "original_code": entry.get("original_code") or "",
        "modified_code": entry.get("modified_code") or "",
    }
    _has_code = bool(str(_rec["original_code"]).strip()
                     and str(_rec["modified_code"]).strip()
                     and str(_rec["method"] or "").strip())
    if not _has_code:
        # 缺代码 ⇒ 明确列出还缺什么，**不假装已进审批流**
        _rec["requires"] = ["original_code", "modified_code"]
        _rec["approval_written"] = False
        return _rec
    try:
        from nucleus.reasoning.PatchManager import PatchManager
        _pm = PatchManager(str(root) if root else _project_root())
        _pm.save_pending_patch({
            "id": "pool_%s" % _id,
            "file": _rec["file"],
            "method": _rec["method"],
            "issue_type": _rec["issue_type"],
            "original_code": _rec["original_code"],
            "modified_code": _rec["modified_code"],
            "description": "问题池消费口产出（346刀1）: %s" % (_rec["desc"] or "")[:300],
            "source": "audit_problem_pool",
            "entry_source": "audit_problem_pool",
            "risk_level": "中",
            "status": "pending",
        })
        _rec["approval_written"] = True
        _rec["approval_patch_id"] = "pool_%s" % _id
    except Exception as _e:
        silent_exc(_e, "audit_problem_pool_consumer._route_patch_candidate 入审批流",
                   level="warning")
        _rec["approval_written"] = False
        _rec["approval_error"] = "%s: %s" % (type(_e).__name__, _e)
    return _rec


def _route_optimize(entry: dict, now: float) -> dict:
    """`experience` → 优化项。"""
    return {
        "id": _dedup_key_of(entry),
        "source": "audit_problem_pool",
        "route": _pool.ROUTE_OPTIMIZE_LIST,
        "issue_type": entry.get("issue_type"),
        "klass": entry.get("klass"),
        "severity": entry.get("severity"),
        "file": entry.get("file"),
        "method": entry.get("method"),
        "desc": entry.get("desc"),
        "state": "optimize_item",
        "consumed_at": now,
    }


def _route_cleanup(entry: dict, now: float) -> dict:
    """`pollution`（含 severity 非法值）→ 清理队列。"""
    _rec = {
        "id": _dedup_key_of(entry),
        "source": "audit_problem_pool",
        "route": _pool.ROUTE_CLEANUP_QUEUE,
        "issue_type": entry.get("issue_type"),
        "klass": entry.get("klass"),
        "severity": entry.get("severity"),
        "severity_raw": entry.get("severity_raw", ""),
        "file": entry.get("file"),
        "method": entry.get("method"),
        "desc": entry.get("desc"),
        "state": "cleanup_pending",
        "consumed_at": now,
    }
    if entry.get("severity_invalid"):
        _rec["reason"] = "severity_invalid"
    return _rec


def consume_pool(limit: int | None = None, root: str | None = None,
                 audit_dir_path: str | None = None, now: float | None = None,
                 enable: bool | None = None, dry_run: bool = False) -> dict:
    """**唯一消费入口**：读池 → 取 open 条目 → 三族分流 → 落行动队列 → 状态推进。

    Returns: `{state, ...}`，`state` 取值
      · `ok`                       —— 本拍至少认领 1 条
      · `noop_pool_empty`          —— 池为空 / 无 open 条目（**不是失败，也不造假**）
      · `unavailable`              —— 池存在但读不出来（**不猜 0**）
      · `disabled`                 —— 灰度开关关闭
      · `write_failed`             —— 行动队列写失败（状态不推进，避免"认领了却没工单"）
    """
    if not _enabled(enable):
        return {"state": "disabled", "consumed": 0, "reason": "switch_off"}
    _now = float(now if now is not None else time.time())
    _limit = CONSUME_BATCH_MAX if limit is None else max(0, int(limit))

    _doc = _pool.load_pool(root, audit_dir_path, _now)
    if _doc["state"] == "empty":
        return {"state": "noop_pool_empty", "consumed": 0, "pool_total": 0,
                "open_before": 0, "reason": "no_snapshot"}
    if _doc["state"] != "ok":
        return {"state": "unavailable", "consumed": 0, "pool_total": None,
                "open_before": None, "reason": _doc.get("reason", "")}

    _entries = _doc["entries"]
    _picked = select_consumable(_entries, _limit)
    _open_before = sum(1 for _e in _entries if isinstance(_e, dict)
                       and str(_e.get("status") or _pool.STATUS_OPEN) == _pool.STATUS_OPEN)
    if not _picked:
        return {"state": "noop_pool_empty", "consumed": 0, "pool_total": len(_entries),
                "open_before": _open_before, "reason": "no_open_entry"}

    _cand: list = []
    _opt: list = []
    _cln: list = []
    _traces: list = []
    _by_route: dict[str, int] = {_pool.ROUTE_PATCH_POOL: 0,
                                 _pool.ROUTE_OPTIMIZE_LIST: 0,
                                 _pool.ROUTE_CLEANUP_QUEUE: 0}
    for _e in _picked:
        _route = str(_e.get("route") or "")
        if _route not in _by_route:
            _route = _pool.classify_route(_e.get("issue_type"), bool(_e.get("severity_invalid")))
        _id = _dedup_key_of(_e)
        if _route == _pool.ROUTE_PATCH_POOL:
            _cand.append(_route_patch_candidate(_e, root, _now))
        elif _route == _pool.ROUTE_CLEANUP_QUEUE:
            _cln.append(_route_cleanup(_e, _now))
        else:
            _opt.append(_route_optimize(_e, _now))
        _by_route[_route] += 1
        _traces.append("%s %s → %s" % (TRACE_TAG, _id, _route))

    if dry_run:
        return {"state": "ok", "dry_run": True, "consumed": 0, "would_consume": len(_picked),
                "pool_total": len(_entries), "open_before": _open_before,
                "routes": _by_route, "traces": _traces}

    _writes = {
        "patch_candidates": _append_jsonl_dedup(
            os.path.join(_pool.audit_dir(root, audit_dir_path), "routes",
                         PATCH_CANDIDATE_FILE), _cand) if _cand else {"ok": True},
        "optimize": _append_jsonl_dedup(
            os.path.join(_dir_of("optimize", root), "optimize_%s.jsonl" % _pool.day_stamp(_now)),
            _opt) if _opt else {"ok": True},
        "cleanup": _append_jsonl_dedup(
            os.path.join(_dir_of("cleanup", root), "cleanup_%s.jsonl" % _pool.day_stamp(_now)),
            _cln) if _cln else {"ok": True},
    }
    _bad = [_k for _k, _v in _writes.items() if not _v.get("ok")]
    if _bad:
        _module_logger.warning("%s 行动队列写失败，本拍不推进状态: %s", TRACE_TAG, ",".join(_bad))
        return {"state": "write_failed", "consumed": 0, "pool_total": len(_entries),
                "open_before": _open_before, "routes": _by_route,
                "failed": _bad, "traces": _traces}

    # 路由已落地才推进状态（顺序不能反：反了就会"认领了却没工单"）
    _claimed = 0
    for _e in _picked:
        _id = _dedup_key_of(_e)
        _route = str(_e.get("route") or "")
        _r = _pool.set_status(_id, _pool.STATUS_CLAIMED, root=root,
                              audit_dir_path=audit_dir_path, now=_now,
                              note="%s %s → %s" % (TRACE_TAG, _id, _route))
        if _r.get("changed"):
            _claimed += 1

    for _t in _traces[:TRACE_PRINT_MAX]:
        _module_logger.info("%s", _t)
    if len(_traces) > TRACE_PRINT_MAX:
        _module_logger.info("%s 另有 %d 条未逐条打印", TRACE_TAG, len(_traces) - TRACE_PRINT_MAX)
    _module_logger.info(
        "%s 本拍: 认领=%d/%d 补丁候选=%d 优化项=%d 清洗=%d 池总=%d open_before=%d",
        TRACE_TAG, _claimed, len(_picked), _by_route[_pool.ROUTE_PATCH_POOL],
        _by_route[_pool.ROUTE_OPTIMIZE_LIST], _by_route[_pool.ROUTE_CLEANUP_QUEUE],
        len(_entries), _open_before)
    return {"state": "ok", "consumed": len(_picked), "claimed": _claimed,
            "pool_total": len(_entries), "open_before": _open_before,
            "routes": _by_route,
            "appended": {_k: _v.get("appended", 0) for _k, _v in _writes.items()},
            "skipped": {_k: _v.get("skipped", 0) for _k, _v in _writes.items()},
            "traces": _traces}


def consume_heartbeat(project_root: str | None = None, now: float | None = None,
                      enable: bool | None = None) -> dict:
    """主循环节拍入口：异常一律吞掉并留痕（消费失败绝不能拖垮主循环）。"""
    try:
        return consume_pool(root=project_root, now=now, enable=enable)
    except Exception as _e:
        silent_exc(_e, "audit_problem_pool_consumer.consume_heartbeat", level="warning")
        return {"state": "exception", "reason": "%s: %s" % (type(_e).__name__, _e)}
