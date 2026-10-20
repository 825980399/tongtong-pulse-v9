# -*- coding: utf-8 -*-
"""第295批刀2/刀3：行动面通电——派单路径 + 执行结果回写。

刀2：从 patch_history 取 status=runtime_verified 且 risk=低 的补丁（13 条先例），
     按风险排序（低风险优先），单批上限 1 条，路由 motor.execute（PulseCodeSandbox）执行。
刀3：执行后回写 patch_history 的 runtime_verified（成功 True / 失败 False + 原因）。

边界（任务书明令）：
  * 首批只路由 runtime_verified 类低风险补丁（13 条先例）；
  * 单批上限 1 条；
  * 黑名单硬约束 = PulseCodeSandbox 自带的 DANGEROUS_KEYWORDS 安全检查（派单不绕过）；
  * 这是「执行验证」不是「放权自改」——只在沙箱子进程里 exec，绝不 apply 到源码。

挂载：register_with_daily_schedule 注册为 DailyScheduler 每日钩子（每日派单 1 条）。
framework 经 set_dispatch_framework 注入（main.py 装配后调用）。
"""
from __future__ import annotations

import io
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("PatchExecutionDispatch")

# ===== ★317批刀0：派单节拍可配（G-1）=====
# 背景：决定/动手此前只在「每日 03:00 一拍」，任何并行/裁判都看不见效果。
# 设计：保留日频为默认（86400s），配置调小即得更密档位；**安全闸不动**
#       （ParamPatchManager._HARD_CAP=1、select_for_auto_triage 的 max_blast_radius=1）。
_DISPATCH_INTERVAL_DEFAULT_SEC = 86400.0
_last_dispatch_ts: float | None = None


def _dispatch_interval_sec() -> float:
    """读派单最小间隔（秒）；缺失/非法回退日频，下限 60s 防每轮询重入。"""
    _iv = _DISPATCH_INTERVAL_DEFAULT_SEC
    try:
        import config as _cfg
        _v = getattr(_cfg, "PATCH_DISPATCH_INTERVAL_SEC", _DISPATCH_INTERVAL_DEFAULT_SEC)
        if _v is not None:
            _iv = float(_v)
    except Exception as _e:
        silent_exc(_e, where="patch_execution_dispatch._dispatch_interval_sec")
    return max(60.0, _iv)


def get_last_dispatch_ts() -> float | None:
    """返回上次派单时间戳（测试/观测用；纯读）。"""
    return _last_dispatch_ts


def reset_last_dispatch_ts(value: float | None = None) -> None:
    """★测试/压测专用：重置派单计时基线（不改任何安全闸，仅时间戳）。"""
    global _last_dispatch_ts
    _last_dispatch_ts = value


def dispatch_on_tick(now: float | None = None) -> dict[str, Any]:
    """★317批刀0：按可配节拍派单（最小间隔 + 同拍一次）。

    - 首次调用只「起算」不派单（避免挂载瞬间抢跑一拍）；
    - `elapsed < interval` ⇒ **显式留痕「钩子未到时 ⇒ 未触发」**（回应烛微 G-1 降级项2）；
    - 到期 ⇒ 派单 1 条（`_HARD_CAP=1` 与 `max_blast_radius=1` 维持不变）。
    """
    global _last_dispatch_ts
    _now = float(time.time() if now is None else now)
    _iv = _dispatch_interval_sec()
    if _last_dispatch_ts is None:
        _last_dispatch_ts = _now
        _module_logger.info(
            "[317刀0][分诊节拍] 挂载起算：interval=%.0fs，下次最早 %.0fs 后（同拍一次）",
            _iv, _iv)
        return {"status": "armed", "interval_sec": _iv}
    _elapsed = _now - _last_dispatch_ts
    if _elapsed < _iv:
        _module_logger.info(
            "[317刀0][分诊节拍] 钩子未到时 ⇒ 未触发 elapsed=%.0fs < interval=%.0fs",
            _elapsed, _iv)
        return {"status": "skipped_not_due", "elapsed_sec": round(_elapsed, 1),
                "interval_sec": _iv}
    _last_dispatch_ts = _now
    _r = dispatch_runtime_verified_patches(limit=1)
    # ★324批刀0：日志三拆——"派单动作"不得再被读成"自转成果"
    _module_logger.info(
        "[317刀0][分诊节拍] 触发自动分诊 interval=%.0fs selected=%s executed=%s "
        "applied=%s blocked=%s hard_cap=1 blast_cap=1（安全闸未放宽）",
        _iv,
        _r.get("dispatched_selected") if isinstance(_r, dict) else "?",
        _r.get("dispatched") if isinstance(_r, dict) else "?",
        _r.get("dispatched_applied") if isinstance(_r, dict) else "?",
        (_r.get("blocked_reason") or "-") if isinstance(_r, dict) else "?")
    return {"status": "dispatched", "interval_sec": _iv, "result": _r}

#: 全局 framework 桥接（main.py 装配后 set_dispatch_framework 注入）
_framework: Any = None


def set_dispatch_framework(fw: Any) -> None:
    """main.py 装配完成后注入 framework（供派单拿 code_sandbox 器官）。"""
    global _framework
    _framework = fw


def get_dispatch_framework() -> Any:
    return _framework


def _history_path() -> str:
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(_root, "data", "patches", "patch_history.json")


# ===== ★326批刀1：fail_count 落生产台账 + **持久性语义**（小林裁决④）=====
# 实测事实（本批复核，非推测）：
#   · 生产 `patch_history.json` 84 条记录里 `runtime_verify_fail_count` **0 条命中**；
#     但 `patch_1788891582_6ecc` 明明带着 `runtime_verify_result` /
#     `runtime_verify_failure_reason`（2026-10-20 19:39 那次执行写的），
#     且 `patch_dispatch_trace.jsonl` 显示它已连败 2 次并触达 cap；
#   · 即「字段有写入代码，但没持久住」——多写者各持内存副本，后写者按自己的
#     副本落盘，陌生字段被静默抹掉。只往记录里塞字段解决不了这个问题。
# 故本批**双写 + 单调侧账**：
#   ① 记录字段 `runtime_verify_fail_count`（patch_history.json）—— 人可读主口径，
#      成功清零（与 324刀0 语义相同）；
#   ② 侧账 `data/patches/patch_fail_counts.json` —— **单调不减**的权威累计
#      （抗覆盖、抗重启），成功时置 0 但保留 `max_ever` 审计值；
#   ③ 判定一律取 `max(字段, 侧账)` ⇒ **重启后按累计持久值判 cap**，不再依赖内存态。
_M326_FAIL_LEDGER_REL = os.path.join("data", "patches", "patch_fail_counts.json")
_M326_FAIL_LEDGER_VERSION = "326.1"


def _m326_fail_ledger_path(path: str | None = None) -> str:
    """侧账路径（`path` 仅供隔离验证注入，生产调用方不传）。"""
    if path:
        return path
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(_root, _M326_FAIL_LEDGER_REL)


def _m326_load_fail_ledger(path: str | None = None) -> dict:
    """读侧账；缺失/损坏一律返回空结构（**绝不因侧账坏了阻断派单**）。"""
    _p = _m326_fail_ledger_path(path)
    try:
        if not os.path.isfile(_p):
            return {"version": _M326_FAIL_LEDGER_VERSION, "patches": {}}
        _d = json.load(io.open(_p, encoding="utf-8"))
        if not isinstance(_d, dict):
            return {"version": _M326_FAIL_LEDGER_VERSION, "patches": {}}
        if not isinstance(_d.get("patches"), dict):
            _d["patches"] = {}
        _d.setdefault("version", _M326_FAIL_LEDGER_VERSION)
        return _d
    except Exception as e:
        silent_exc(e, where="patch_execution_dispatch::_m326_load_fail_ledger",
                   level="warning")
        return {"version": _M326_FAIL_LEDGER_VERSION, "patches": {}}


def _m326_save_fail_ledger(data: dict, path: str | None = None) -> bool:
    """原子写侧账；失败只留痕返回 False（不影响派单主流程）。"""
    _p = _m326_fail_ledger_path(path)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        _body = dict(data or {})
        _body["version"] = _M326_FAIL_LEDGER_VERSION
        _body["updated_at"] = time.time()
        _tmp = _p + ".tmp"
        with io.open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_body, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp, _p)
        return True
    except Exception as e:
        silent_exc(e, where="patch_execution_dispatch::_m326_save_fail_ledger",
                   level="warning")
        return False


def _m326_bump_fail_ledger(patch_id: str, fail_count: int | None = None,
                           reason: str = "", path: str | None = None) -> int:
    """把侧账累计值抬到 `max(现值, fail_count)`（单调不减）；缺 fail_count 则 +1。

    Returns: 侧账当前值（0 = 没有记录）。
    """
    _pid = str(patch_id or "")
    if not _pid:
        return 0
    _d = _m326_load_fail_ledger(path)
    _rec = _d["patches"].get(_pid)
    if not isinstance(_rec, dict):
        _rec = {"fail_count": 0, "max_ever": 0, "first_at": time.time()}
    _cur = max(0, int(_rec.get("fail_count", 0) or 0))
    _new = (_cur + 1) if fail_count is None else max(_cur, int(fail_count or 0))
    _rec["fail_count"] = _new
    _rec["max_ever"] = max(int(_rec.get("max_ever", 0) or 0), _new)
    _rec["last_reason"] = str(reason or "")[:200]
    _rec["updated_at"] = time.time()
    _rec.setdefault("first_at", time.time())
    _d["patches"][_pid] = _rec
    _m326_save_fail_ledger(_d, path)
    return _new


def _m326_reset_fail_ledger(patch_id: str, path: str | None = None) -> int:
    """成功 ⇒ 侧账置 0（`max_ever` 保留作审计）。返回置零后的值（0）。"""
    _pid = str(patch_id or "")
    if not _pid:
        return 0
    _d = _m326_load_fail_ledger(path)
    _rec = _d["patches"].get(_pid)
    if not isinstance(_rec, dict):
        return 0
    _rec["max_ever"] = max(int(_rec.get("max_ever", 0) or 0),
                           int(_rec.get("fail_count", 0) or 0))
    _rec["fail_count"] = 0
    _rec["last_success_at"] = time.time()
    _rec["updated_at"] = time.time()
    _d["patches"][_pid] = _rec
    _m326_save_fail_ledger(_d, path)
    return 0


def _m326_persisted_fail_count(patch: dict, ledger: dict | None = None,
                               path: str | None = None) -> int:
    """**持久累计失败次数** = max(记录字段, 侧账)。cap 判定只看这个值。"""
    if not isinstance(patch, dict):
        return 0
    _field = _m324_fail_count(patch)
    _d = ledger if isinstance(ledger, dict) else _m326_load_fail_ledger(path)
    _rec = (_d.get("patches") or {}).get(str(patch.get("id") or ""))
    _led = 0
    if isinstance(_rec, dict):
        try:
            _led = max(0, int(_rec.get("fail_count", 0) or 0))
        except (TypeError, ValueError) as _e:
            # 侧账里写了非数值 ⇒ 按 0 处理（记录字段仍参与 max，不会丢失败次数）
            silent_exc(_e, where="patch_execution_dispatch::_m326_persisted_fail_count 非数值",
                       level="debug")
            _led = 0
    return max(_field, _led)


def _m326_seed_from_trace(trace_path: str | None = None, dry_run: bool = True,
                          path: str | None = None) -> dict:
    """用派单留痕（`patch_dispatch_trace.jsonl`）**一次性**回填侧账。

    判据（可核、不猜）：一条 trace 记录里 `dispatched_verify_failed==1` 且
    `patch_ids` 恰好 1 个 ⇒ 该补丁 +1 次失败。只增不减（`max`），幂等可重跑。
    """
    _tp = trace_path or _m324_trace_path()
    _stat = {"trace_path": _tp, "lines": 0, "evidence_fails": 0,
             "patches_touched": [], "dry_run": bool(dry_run), "written": False}
    _ledger = _m326_load_fail_ledger(path)
    _acc: dict[str, int] = {}
    try:
        if os.path.isfile(_tp):
            for _line in io.open(_tp, encoding="utf-8"):
                _line = _line.strip()
                if not _line:
                    continue
                _stat["lines"] += 1
                try:
                    _r = json.loads(_line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(_r, dict):
                    continue
                _ids = [str(_i) for _i in (_r.get("patch_ids") or []) if _i]
                _failed = int(_r.get("dispatched_verify_failed", 0) or 0)
                if _failed >= 1 and len(_ids) == 1:
                    _acc[_ids[0]] = _acc.get(_ids[0], 0) + _failed
                    _stat["evidence_fails"] += _failed
    except Exception as e:
        silent_exc(e, where="patch_execution_dispatch::_m326_seed_from_trace",
                   level="warning")
    for _pid, _n in sorted(_acc.items()):
        _rec = _ledger["patches"].get(_pid)
        if not isinstance(_rec, dict):
            _rec = {"fail_count": 0, "max_ever": 0, "first_at": time.time()}
        _rec["fail_count"] = max(int(_rec.get("fail_count", 0) or 0), int(_n))
        _rec["max_ever"] = max(int(_rec.get("max_ever", 0) or 0), int(_rec["fail_count"]))
        _rec["source"] = "trace_seed_326"
        _rec["updated_at"] = time.time()
        _ledger["patches"][_pid] = _rec
        _stat["patches_touched"].append({"patch_id": _pid, "fail_count": _rec["fail_count"]})
    if not dry_run and _acc:
        _stat["written"] = _m326_save_fail_ledger(_ledger, path)
    return _stat


def get_m326_fail_ledger(path: str | None = None) -> dict:
    """★326批刀1：侧账内容（只读拷贝），供报表/验收取数。"""
    _d = _m326_load_fail_ledger(path)
    return {"version": _d.get("version"), "patches": dict(_d.get("patches") or {})}


def _extract_def_name(code: str) -> str:
    """提取代码首个 def 名（与 PatchManager._m280_extract_def_name 同源）；无 def → 空串。"""
    if not code or not isinstance(code, str):
        return ""
    try:
        import re
        _m = re.search(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", code, re.MULTILINE)
        return _m.group(1) if _m else ""
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::_extract_def_name")
        return ""


def is_fragment_to_full(patch: dict) -> bool:
    """★第295批刀5形态判据：original 是方法体片段（无 def）而 modified 是完整方法（含 def）。

    该形态应用到原方法体内会「再嵌一个 def」→ 结构破坏 → 判可疑，不派单。
    """
    _orig = patch.get("original_code") or ""
    _mod = patch.get("modified_code") or ""
    return (_extract_def_name(_orig) == "") and (_extract_def_name(_mod) != "")


#: ★第306批 刀4：**生命周期名** —— 静态图对它们结构性失明。
#:   实例化（`__init__`）、析构、上下文管理、启停框架钩子都由解释器/框架调用，
#:   源码里没有调用点 ⇒ 入边 0 是**图的盲区**，不是「没人依赖它」。
#:   这类方法的牵连半径**不可判定** ⇒ 一律转人工，绝不按 0 放行。
_LIFECYCLE_NAMES = frozenset((
    "__init__", "__new__", "__del__", "__post_init__", "__enter__", "__exit__",
    "__aenter__", "__aexit__", "__call__", "__getattr__",
    "start", "stop", "setup", "teardown", "init", "initialize", "close",
    "shutdown", "reset", "run", "on_start", "on_stop", "on_init",
))

#: ★第306批 刀4：**受控试点首批白名单** —— 只有这 2 条 `refresh_runtime_params`
#:   进自动分诊（305 刀3 修正牵连闸后，13 条候选里实际可派的就是这 2 条；
#:   `__init__` 等生命周期名看似「入边 0」实为图盲区，属假安全）。
#:   白名单外的候选**不自动派单**，转人工评估后再放行。
PILOT_FIRST_BATCH = frozenset({
    ("organs/motor/PulseController.py", "refresh_runtime_params"),
    ("organs/motor/PulseCodeSandbox.py", "refresh_runtime_params"),
})

#: ★第307批 刀3：**定位失败原因码** —— 305 刀3 起所有定位失败共用一句日志
#:   （「起点无法定位 ⇒ 转人工」），无法区分是图缺节点、宿主文件错位（Mixin 拆文件），
#:   还是压根没给 file 无法消歧。三种失败的处理方式完全不同：
#:     - no_node         ：图里没有这个节点 ⇒ 要补图/补扫描范围
#:     - file_mismatch   ：图里有同名方法但宿主文件不同 ⇒ 补丁 file 字段过时（Mixin/搬迁）
#:     - no_file         ：裸方法名 + 无 file ⇒ 补丁记录缺字段，无法消歧
LOCATE_FAIL_NO_NODE = "no_node"
LOCATE_FAIL_FILE_MISMATCH = "file_mismatch"
LOCATE_FAIL_NO_FILE = "no_file"

#: 三类失败的**进程内计数**（可读性优先，供每日派单日志一行汇总）
_LOCATE_FAIL_COUNTS: dict[str, int] = {
    LOCATE_FAIL_NO_NODE: 0,
    LOCATE_FAIL_FILE_MISMATCH: 0,
    LOCATE_FAIL_NO_FILE: 0,
    "file_mismatch_rescued": 0,
}


def get_locate_fail_counts() -> dict[str, int]:
    """★307刀3：三类定位失败的累计计数（只读副本，供报表/单测）。"""
    return dict(_LOCATE_FAIL_COUNTS)


def _m307_locate_fail_reason(qualified: str, file: str | None,
                             nodes: list) -> tuple[str, list]:
    """★307刀3：给「起点无法定位」分类并给出候选宿主文件。

    Returns: ``(reason_code, candidate_files[:3])``
    """
    _name = str(qualified or "").split(".")[-1]
    _cands = []
    for _n in nodes or []:
        if isinstance(_n, dict) and str(_n.get("name") or "") == _name:
            _cands.append(str(_n.get("file", "") or "").replace("\\", "/"))
    _files = sorted({x for x in _cands if x})
    if not _files:
        return LOCATE_FAIL_NO_NODE, []
    if not (file or "").strip():
        return LOCATE_FAIL_NO_FILE, _files[:3]
    # file 已给出：判定是否 mismatch（file 不在候选集）
    _file_norm = (file or "").replace("\\", "/").strip()
    if _file_norm not in _files:
        # ★315批刀4：二级兜底——同名只落唯一文件时按该唯一文件定位（唯一性约束，
        #   防止 332 次重名那类 __init__ 合并进起点）；跨多文件仍返 file_mismatch 转人工。
        if len(_files) == 1:
            _u = _files[0]
            _c = _LOCATE_FAIL_COUNTS.get("file_mismatch_rescued", 0) + 1
            _LOCATE_FAIL_COUNTS["file_mismatch_rescued"] = _c
            _module_logger.info(
                "[315刀4] file_mismatch 唯一类兜底救援: name=%s file=%s rescued=%s",
                _name, _file_norm, _u)
            return "file_mismatch_rescued", [_u]
        return LOCATE_FAIL_FILE_MISMATCH, _files[:3]
    # file 命中候选集 => 视为可定位（保持原 file_mismatch 标记语义，转人工复核）
    return LOCATE_FAIL_FILE_MISMATCH, _files[:3]


#: ★第305批 刀4：落盘调用图**读缓存**（对齐 DailyScheduler:573 既有口径
#:   「运行时消费者只读缓存，绝不现扫」）。304 刀5 在 select 循环里每条候选
#:   json.load 一次 3.1MB 图 ⇒ N×3MB 磁盘读，与该口径冲突。
_call_graph_cache: dict[str, Any] = {"path": None, "graph": None,
                                     "out_adj": None, "in_adj": None}


def load_call_graph_cache(force: bool = False,
                          graph_dir: str | None = None) -> dict[str, Any] | None:
    """装载最新**合格**落盘调用图（**带缓存**）；无合格图 ⇒ None。

    同时构建出边/入边邻接表（`out_adj` / `in_adj`），供牵连半径 BFS 使用。

    ★第312批 刀1：`graph_dir` 仅用于**隔离验证**（指向沙箱图目录，证守卫不改变读数）；
    生产调用方一律不传，走默认的 `<仓库根>/data/call_graph`。
    """
    global _call_graph_cache
    try:
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # ★第312批 刀1：图污染守卫（与 self_inspector 同一判据）—— 合成夹具（12 节点 / 4 文件）
        #   一旦排在最后，牵连闸的起点就定位不到 ⇒ 整池 blast=None 转人工（fail-closed 掐死闭环）。
        #   宁可用旧的真图，也不信新的假图。
        from nucleus.data.call_graph_guard import (
            format_rejected as _fmt_rej2,
            select_latest_graph as _sel_graph2,
        )
        _pick2 = _sel_graph2(graph_dir or os.path.join(_root, "data", "call_graph"))
        if _pick2.get("rejected"):
            _module_logger.warning(
                "[312刀1] 牵连闸图守卫：拒绝 %d 份不合格图（不当判据、也不删）: %s",
                len(_pick2["rejected"]), _fmt_rej2(_pick2["rejected"]))
        if not _pick2.get("ok"):
            return None
        _latest = _pick2["path"]
        if (not force and _call_graph_cache["path"] == _latest
                and _call_graph_cache["graph"] is not None):
            return _call_graph_cache["graph"]
        _gd = _pick2["graph"]
        _out_adj: dict[str, list[str]] = {}
        _in_adj: dict[str, list[str]] = {}
        for _e in (_gd.get("edges") or []):
            if isinstance(_e, dict) and _e.get("caller") and _e.get("callee"):
                _out_adj.setdefault(_e["caller"], []).append(_e["callee"])
                _in_adj.setdefault(_e["callee"], []).append(_e["caller"])
        _call_graph_cache = {"path": _latest, "graph": _gd,
                             "out_adj": _out_adj, "in_adj": _in_adj}
        return _gd
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::load_call_graph_cache")
        return None


def _bfs_count(adj: dict[str, list[str]], starts: list[str], max_depth: int) -> int:
    """从起点集沿邻接表 BFS，返回可达节点数（不含起点自身）。"""
    _seen = set(starts)
    _frontier = list(starts)
    for _ in range(max(1, int(max_depth))):
        _next: list[str] = []
        for _nid in _frontier:
            for _nxt in adj.get(_nid, []):
                if _nxt not in _seen:
                    _seen.add(_nxt)
                    _next.append(_nxt)
        _frontier = _next
        if not _frontier:
            break
    return len(_seen) - len(set(starts))


def blast_radius_of(qualified: str, file: str | None = None,
                    max_depth: int = 3, direction: str = "in") -> int | None:
    """★第305批 刀3：牵连半径 —— 默认**入边方向（谁依赖我）**。

    304 刀5 的三处缺陷与本批修正：
      ① **方向错**：沿 caller→callee 出边 BFS ⇒ 度量的是「我依赖谁」，
         而改动风险的实际来源是「**谁依赖我**」⇒ 默认改 `direction="in"`（入边）。
      ② **缺名返回 0** ⇒ 调用方按「不拦」放行，把「查不到」当成「牵连小」（**假通过**）
         ⇒ 改为**返回 None**，调用方 `is None` 必须**转人工**。
      ③ **裸 name 起 BFS** ⇒ 同名方法跨文件合并，量到的是别人的半径
         ⇒ **起点消歧**：优先用 `file` + 方法名精确定位（补丁的 file/method 字段），
         其次要求 `Class.method` / `module.Class.method` 限定名；
         仅给裸 name 且无 file ⇒ 返回 None（消歧失败，转人工）。

    Returns: 可达节点数（不含起点）；**图缺失或起点无法确定 ⇒ None**（绝不返回 0 冒充「牵连小」）。
    """
    if not qualified or not isinstance(qualified, str):
        return None
    _gd = load_call_graph_cache()
    if not _gd:
        return None
    _nodes = _gd.get("nodes") or []
    _q = qualified.strip()
    # ★第306批 刀4：生命周期名 ⇒ 牵连不可判定（图的盲区，不是 0）
    if _q.split(".")[-1] in _LIFECYCLE_NAMES:
        _module_logger.info(
            "[306刀4] 生命周期名牵连不可判定 ⇒ 转人工: %s（file=%s）",
            _q, file or "-")
        return None
    _f = str(file or "").replace("\\", "/").strip()
    _name = _q.split(".")[-1]
    _starts: list[str] = []
    # 一级：文件 + 方法名（补丁的 file/method ⇒ 精确，不要求类限定，避免「饿死」）
    if _f and _name:
        for _n in _nodes:
            if not isinstance(_n, dict):
                continue
            if (str(_n.get("file", "") or "").replace("\\", "/") == _f
                    and str(_n.get("name") or "") == _name):
                _starts.append(_n.get("id"))
    # 二级：限定名（Class.method / module.Class.method）
    if not _starts and "." in _q:
        for _n in _nodes:
            if not isinstance(_n, dict):
                continue
            _nid = str(_n.get("id") or "")
            _qual = str(_n.get("qualname") or "")
            if _nid.endswith(_q) or _qual == _q or _qual.endswith("." + _q):
                _starts.append(_nid)
    if not _starts:
        # ★第307批 刀3：三类失败分原因码 + 可区分计数（不再共用一句日志）
        _reason, _cand_files = _m307_locate_fail_reason(_q, _f, _nodes)
        _LOCATE_FAIL_COUNTS[_reason] = _LOCATE_FAIL_COUNTS.get(_reason, 0) + 1
        _module_logger.info(
            "[307刀3] 起点无法定位 ⇒ 转人工: reason=%s name=%s file=%s 候选宿主=%s"
            "（累计 no_node=%d file_mismatch=%d no_file=%d）",
            _reason, _q, _f or "-", _cand_files or "-",
            _LOCATE_FAIL_COUNTS[LOCATE_FAIL_NO_NODE],
            _LOCATE_FAIL_COUNTS[LOCATE_FAIL_FILE_MISMATCH],
            _LOCATE_FAIL_COUNTS[LOCATE_FAIL_NO_FILE])
        return None
    _adj = _call_graph_cache.get("in_adj") if direction == "in" else _call_graph_cache.get("out_adj")
    if _adj is None:
        return None
    return _bfs_count(_adj, [str(_s) for _s in _starts if _s], max_depth)


# ===== ★325批刀1：危险关键字预检（**唯一实现**，生成侧与派单侧共用）=====
# 为什么放在这里而不是各写一份：沙箱黑名单是 `PulseCodeSandbox.DANGEROUS_KEYWORDS`，
# 抄第二份必然漂移。本模块已因 `is_fragment_to_full` 承担"派单口径单一真源"的角色。
#: ★326批刀4：**本仓可信代码惯用法登记册**（唯一真源，`_M325_IDIOM_KEYWORDS` 由它派生）。
#: 登记口径（每一条都要写"为什么它在本仓是惯用法"）——不写理由的一律不许登记，
#: 否则登记册会变成"把黑名单搬空"的后门。
TRUSTED_CODE_IDIOMS: dict[str, str] = {
    "getattr(": "热加载/配置读取惯用法：getattr(cfg,'X',default)，全仓数百处，非危险调用",
    "setattr(": "运行期参数回填惯用法：refresh_runtime_params 系补丁的常见写法",
    "delattr(": "属性清理惯用法：与 setattr 成对出现于状态复位路径",
    "open(": "文件 IO 惯用法：读写 data/ 下 jsonl/json，仓内各器官常规操作",
    "file(": "历史兼容写法（py2 遗留词法），仅作为字符串/老代码出现",
    "input(": "交互式输入惯用法：控制台脚本与工具入口",
    "raw_input(": "历史兼容写法（py2 遗留词法）",
}

#: 旧名保持可用（325 及既有调用方按 frozenset 用），值**由登记册派生**，不重复维护
_M325_IDIOM_KEYWORDS = frozenset(TRUSTED_CODE_IDIOMS)


# ===== ★326批刀4：两通道判别标准（**只出方案与判别，不动拦截行为**）=====
# 背景（325刀1 实测）：沙箱 `_security_precheck` 是纯子串匹配，它面对的本来是
#   "不可信外部片段"；而派单把**本仓方法体整段**喂进去 ⇒ 84 条历史补丁命中 61 条
#   （72.62%），其中 getattr( 占 54 条、真逃逸 **2** 条。这是**口径错配**，不是生成质量问题。
# 本批定义两条通道的判别口径（可复算、不看主观）：
#   trusted_repo     ：补丁带 `file` 且该路径落在仓内、`modified_code` 可 AST 解析、
#                      且 `original_code` 非空（= 本仓方法体的替换重放）
#                      ⇒ 走**惯用法白名单**（`TRUSTED_CODE_IDIOMS`）解释命中
#   external_fragment：无 file / file 在仓外 / 原码为空（= 外部片段或整段新代码）
#                      ⇒ 走**黑名单**（沙箱 DANGEROUS_KEYWORDS，逐字同判据）
#   unparsed         ：AST 解析失败 ⇒ **按不可信处理**（fail-closed），不许"解析不了所以放行"
# 红线（本批明令）：`DANGEROUS_KEYWORDS` 不动、`_security_precheck` 不改、
#   `would_block` 判据不变 ⇒ **沙箱行为零变化**。本段只做归类与留痕。
CHANNEL_TRUSTED = "trusted_repo"
CHANNEL_EXTERNAL = "external_fragment"
CHANNEL_UNPARSED = "unparsed"


def _m326_repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def classify_code_channel(patch: dict, project_root: str | None = None) -> dict:
    """★326批刀4：判该补丁走**可信通道**还是**外部片段通道**（只读，不改任何字段）。

    判别只用三个可观测事实：file 是否落仓内、原码是否非空、`modified_code` 能否解析。
    每次判别都给 `reasons` 与 `untrusted_by`（可复算），不给"看起来像"这种话。

    ★第一版实现用"在理由字符串里找关键字"来判否定项 ⇒ `无 file 字段` 这类理由
      不含"仓外/不存在/为空"，被判成可信通道（实测 5 个样本里 3 个误判）。
      故改为**显式布尔标记**：每个判据命中即往 `untrusted_by` 追加一个码，
      有没有否定项一眼可数，不做字符串嗅探。
    """
    _root = os.path.abspath(project_root or _m326_repo_root())
    _p = patch if isinstance(patch, dict) else {}
    _file = str(_p.get("file") or "").replace("\\", "/").strip()
    _code = str(_p.get("modified_code") or "")
    _orig = str(_p.get("original_code") or "")
    _reasons: list[str] = []
    _untrusted: list[str] = []
    if not _file:
        _reasons.append("无 file 字段 ⇒ 无法证明是本仓方法体")
        _untrusted.append("no_file")
    else:
        _abs = os.path.abspath(os.path.join(_root, _file))
        try:
            _in_repo = os.path.commonpath([_root, _abs]) == _root
        except ValueError as _e:
            # 跨盘/不同 drive 时 commonpath 抛 ValueError ⇒ 记不可信（fail-closed）
            silent_exc(_e, where="patch_execution_dispatch::classify_code_channel 跨盘",
                       level="debug")
            _in_repo = False
        if not _in_repo:
            _reasons.append("file 落在仓外(%s) ⇒ 外部片段" % _file)
            _untrusted.append("outside_repo")
        elif not os.path.isfile(_abs):
            _reasons.append("file 在仓内但文件不存在(%s) ⇒ 不可信" % _file)
            _untrusted.append("file_missing")
        else:
            _reasons.append("file 落仓内且存在(%s)" % _file)
    if not _orig.strip():
        _reasons.append("original_code 为空 ⇒ 非「方法体替换」，按外部片段处理")
        _untrusted.append("no_original")
    else:
        _reasons.append("original_code 非空 ⇒ 属方法体替换")
    if _m325_ast_tokens(_code) is None:
        _reasons.append("modified_code AST 解析失败 ⇒ fail-closed 按不可信")
        return {"channel": CHANNEL_UNPARSED, "file": _file, "reasons": _reasons,
                "untrusted_by": _untrusted + ["unparsed"],
                "registry": "unparsed ⇒ 黑名单 + 不赦免",
                "judge": "沙箱 DANGEROUS_KEYWORDS（逐字同判据）"}
    if _untrusted:
        return {"channel": CHANNEL_EXTERNAL, "file": _file, "reasons": _reasons,
                "untrusted_by": _untrusted,
                "registry": "黑名单（DANGEROUS_KEYWORDS）",
                "judge": "沙箱 DANGEROUS_KEYWORDS（逐字同判据）"}
    return {"channel": CHANNEL_TRUSTED, "file": _file, "reasons": _reasons,
            "untrusted_by": [],
            "registry": "惯用法白名单（TRUSTED_CODE_IDIOMS，%d 条）" % len(TRUSTED_CODE_IDIOMS),
            "judge": "子串判据不变；命中且全属登记册 ⇒ 记 idiom，不算生成质量问题"}


def sandbox_channel_policy() -> dict:
    """★326批刀4：两通道判别标准（机器可读版；文档见
    `docs/路灯与星轨对话/施工预案/沙箱两通道判别标准_v1.md`）。

    **本函数只描述口径，不改变任何拦截行为**：`DANGEROUS_KEYWORDS` 原样、
    `_security_precheck` 原样、`would_block` 原样。
    """
    return {
        "version": "326.1",
        "channels": {
            CHANNEL_TRUSTED: {
                "criteria": ["patch.file 落在仓内且文件存在",
                             "patch.original_code 非空（方法体替换）",
                             "patch.modified_code 可 AST 解析"],
                "registry": "TRUSTED_CODE_IDIOMS（%d 条，带理由登记）"
                            % len(TRUSTED_CODE_IDIOMS),
                "action": "命中且全属登记册 ⇒ class=idiom（不算生成质量问题）；"
                          "命中含非登记册真实调用 ⇒ class=real（真危险，转人工）",
            },
            CHANNEL_EXTERNAL: {
                "criteria": ["无 file 字段", "或 file 在仓外", "或 original_code 为空"],
                "registry": "沙箱 DANGEROUS_KEYWORDS（单一真源，不抄第二份）",
                "action": "子串命中即拦（与沙箱逐字同判据），无豁免、无解释",
            },
            CHANNEL_UNPARSED: {
                "criteria": ["modified_code AST 解析失败"],
                "registry": "同外部片段",
                "action": "fail-closed：按不可信处理，不许「解析不了所以放行」",
            },
        },
        "blacklist_unchanged": True,
        "interception_unchanged": True,
        "red_line": "本批不动 DANGEROUS_KEYWORDS、不改 _security_precheck、不改 would_block",
    }


def sandbox_keywords() -> list:
    """取沙箱黑名单（单一真源）。沙箱不可用时返回空表并留痕，绝不本地抄一份。"""
    try:
        from organs.motor.PulseCodeSandbox import PulseCodeSandbox as _S
        return list(getattr(_S, "DANGEROUS_KEYWORDS", []) or [])
    except Exception as e:
        silent_exc(e, where="patch_execution_dispatch::sandbox_keywords")
        return []


def _m325_ast_tokens(code: str) -> set | None:
    """提取代码里的「真实标识符/调用」集合（**排除字符串字面量**）。

    返回 None = 解析失败（片段带缩进等），此时调用方一律按"不可判定"处理。
    """
    import ast as _ast
    import textwrap as _tw
    _src = code or ""
    for _cand in (_src, _tw.dedent(_src)):
        try:
            _tr = _ast.parse(_cand)
        except (SyntaxError, ValueError):
            continue
        _toks: set = set()
        for _n in _ast.walk(_tr):
            if isinstance(_n, _ast.Attribute):
                _toks.add(_n.attr.lower())
            elif isinstance(_n, _ast.Name):
                _toks.add(_n.id.lower())
            elif isinstance(_n, _ast.Call):
                _f = _n.func
                if isinstance(_f, _ast.Name):
                    _toks.add(_f.id.lower() + "(")
                elif isinstance(_f, _ast.Attribute):
                    _toks.add(_f.attr.lower() + "(")
        return _toks
    return None


def scan_dangerous_keywords(code: str) -> dict:
    """扫补丁代码对沙箱黑名单的命中，并区分三类。

    为什么必须区分：沙箱 `_security_precheck` 是**纯子串匹配**
    （`keyword.lower() in code.lower()`），它面对的是"不可信外部片段"；而派单把
    **本仓方法体整段**喂进去 ⇒ 本仓合法的 `getattr(cfg,'X',d)` 热加载惯用法、甚至
    PulseSkin 那种"把危险关键字写成字符串清单"的代码都会命中。
    实测 84 条历史补丁：命中 61 条（72.6%），`getattr(` 占 54 条、真逃逸模式 **0** 条
    —— 这不是生成质量问题，是**口径错配**（报告已如实申报）。

    返回：
      sandbox_blocked  子串命中的关键字（= 沙箱一定会拦的，判据与沙箱逐字一致）
      real_calls       去掉字符串字面量后仍在真实调用位的关键字
      literal_only     命中全部来自字符串字面量/注释
      idiom_only       命中全部属本仓合法惯用法
      class            none | idiom | literal | real
      would_block      沙箱是否必然拦截（子串命中即 True）
    """
    _kw = sandbox_keywords()
    _low = str(code or "").lower()
    _blocked = [_k for _k in _kw if str(_k).lower() in _low]
    if not _blocked:
        return {"sandbox_blocked": [], "real_calls": [], "literal_only": False,
                "idiom_only": False, "class": "none", "would_block": False}
    _toks = _m325_ast_tokens(code)
    if _toks is None:
        # 解析失败 ⇒ 不可判定：只报子串命中，不猜"真危险"
        return {"sandbox_blocked": _blocked, "real_calls": [], "literal_only": False,
                "idiom_only": False, "class": "unparsed", "would_block": True}
    _real = []
    for _k in _blocked:
        _probe = str(_k).lower()
        _bare = _probe.rstrip("(").rstrip(".")
        if _probe.endswith("("):
            if _probe in _toks:
                _real.append(_k)
        elif _bare in _toks:
            _real.append(_k)
    _idiom = [k for k in _blocked if k in _M325_IDIOM_KEYWORDS]
    # ★分类顺序很重要：`getattr(` 既是**真实调用**又是**本仓合法惯用法**，
    #   先判"real"会把它误报成生成质量问题（实测 84 条里 54 条这样）。
    #   口径：非惯用法的真实调用才算 real；惯用法真实调用记 idiom；只在字符串里出现记 literal。
    _non_idiom_real = [k for k in _real if k not in _M325_IDIOM_KEYWORDS]
    if _non_idiom_real:
        _class = "real"
    elif _real:
        _class = "idiom"
    elif _idiom:
        _class = "literal"
    else:
        _class = "literal"
    return {"sandbox_blocked": _blocked, "real_calls": _real,
            "literal_only": (not _real and not _idiom),
            "idiom_only": _class == "idiom", "class": _class,
            "would_block": True}


def is_sandbox_incompatible(patch: dict) -> bool:
    """该补丁是否**结构性**无法通过沙箱（子串黑名单必拦）。

    只读；兼容老补丁（无 `sandbox_incompatible` 字段时现算一次，不写回）。
    """
    if not isinstance(patch, dict):
        return False
    if patch.get("sandbox_incompatible") is True:
        return True
    if patch.get("sandbox_incompatible") is False:
        return False
    _code = patch.get("modified_code") or patch.get("original_code") or ""
    if not _code:
        return False
    return bool(scan_dangerous_keywords(_code).get("would_block"))


def select_patch_for_execution(patch_history: list, limit: int = 1,
                               max_blast_radius: int = 1,
                               pilot_only: bool = True) -> list:
    """从 patch_history 选「status=runtime_verified 且 risk=低」的补丁（低风险优先）。

    纯函数（便于隔离单测）；只读，不改任何字段。
    ★第295批刀5：先跑形态判据回扫，「片段→完整」（嵌套 def）补丁过滤掉，不派单。
    ★第304批刀5：再查**牵连半径**（读落盘调用图）—— 牵连节点数 > `max_blast_radius`
      者**不入队、转人工**（自动分诊的前置依赖；图缺失时 blast=0 ⇒ 按「不拦」处理，零回归）。
    ★第306批刀4：`pilot_only=True` 时再按**受控试点首批白名单**收敛（只放 2 条
      `refresh_runtime_params`）。自动分诊的首批刻意不给「全池放行」——
      305 修正牵连闸后 13 条候选里真正可派的就这 2 条，其余要么牵连不可判定，
      要么候选本身就是生命周期名。`pilot_only=False` 仅供人工复核/单测显式越权使用。
    """
    if not isinstance(patch_history, list):
        return []
    _cands = [p for p in patch_history if isinstance(p, dict)
              and p.get("status") == "runtime_verified"
              and p.get("risk_level") in ("低", "low")]
    # ★第298批刀3：is_fragment_to_full 为 select 永久排除项（非首批边界）——
    #   即使后续放宽到 approved（71条），片段→完整结构损坏补丁（含2条problem_fixed=True）也绝不派单。
    _safe = [p for p in _cands if not is_fragment_to_full(p)]
    # ★325批刀1：**结构性必败**的补丁不派单（沙箱子串黑名单必拦）。
    #   凭据：324刀0 实测 12:38—17:38 六圈同一 patch_1788891582_6ecc 全失败，
    #   而它的 modified_code 是合法的 `getattr(_cfg,'RUNTIME_PARAMS',{})` 热加载惯用法
    #   —— 派六次就失败六次，纯浪费。这类**转人工**，不再自动重试（不改沙箱判据）。
    _m325_skip = []
    _safe2 = []
    for _p in _safe:
        if is_sandbox_incompatible(_p):
            _m325_skip.append(_p)
            _module_logger.info(
                "[325刀1] 沙箱预检必拦 ⇒ 转人工(不派单): patch=%s method=%s kw=%s",
                str(_p.get("id", ""))[:16], str(_p.get("method") or ""),
                ",".join((scan_dangerous_keywords(
                    _p.get("modified_code") or _p.get("original_code") or ""
                ).get("sandbox_blocked") or [])[:4]))
            continue
        _safe2.append(_p)
    if _m325_skip:
        _M325_DISPATCH_REJ["sandbox_incompatible"] = len(_m325_skip)
    _safe = _safe2
    # ★第304批刀5：牵连半径闸门（> max_blast_radius ⇒ 转人工，不自动派单）
    _scoped = []
    for _p in _safe:
        _m = str(_p.get("method") or "")
        _f = str(_p.get("file") or "")
        # ★第305批刀3：入边方向（谁依赖我）；**None = 不可判定 ⇒ 转人工**（禁止当 0 放行）
        _br = blast_radius_of(_m, file=_f)
        if _br is None:
            _module_logger.info(
                "[305刀3] 牵连不可判定（图缺/起点未定位）⇒ 转人工: patch=%s method=%s file=%s",
                str(_p.get("id", ""))[:16], _m, _f or "-")
            continue
        if _br > max_blast_radius:
            _module_logger.info(
                "[304刀5] 牵连半径超限转人工: patch=%s method=%s blast=%d > %d",
                str(_p.get("id", ""))[:16], _m, _br, max_blast_radius)
            continue
        _scoped.append(_p)
    # ★第306批刀4：受控试点首批白名单（只放两个 refresh_runtime_params）
    if pilot_only:
        _pilot = []
        for _p in _scoped:
            _key = (str(_p.get("file") or "").replace("\\", "/"),
                    str(_p.get("method") or ""))
            if _key in PILOT_FIRST_BATCH:
                _pilot.append(_p)
            else:
                _module_logger.info(
                    "[306刀4] 非首批试点 ⇒ 转人工: patch=%s method=%s",
                    str(_p.get("id", ""))[:16], _key[1])
        _scoped = _pilot
    # 低风险优先；同风险按 trust_score 降序
    _scoped.sort(key=lambda p: (
        0 if p.get("risk_level") in ("低", "low") else 1,
        -(float(p.get("trust_score", 0) or 0))))
    return _scoped[:limit]


#: ★325批刀1：本批派单侧的拒收分布（只增不重置，便于一次运行内归因）
_M325_DISPATCH_REJ: dict = {}


def get_m325_dispatch_rej() -> dict:
    """★325批刀1：读本进程内派单拒收分布（只读拷贝）。"""
    return dict(_M325_DISPATCH_REJ)


#: ★第311批 刀1：自动分诊的「高置信」门槛（历史 trust_score 实测取值 30/40/45/50/80）
AUTO_TRIAGE_MIN_TRUST = 40.0


def select_for_auto_triage(patch_history: list, limit: int = 1,
                           max_blast_radius: int = 1) -> list:
    """★第311批 刀1：**高置信 · 低风险**自动分诊池（单批硬上限 1 条）。

    与 `select_patch_for_execution` 的区别只有一处：**不再用 2 条白名单**，
    改用可解释的高置信规则 —— 306 的首批白名单是「先证明能跑通」的临时边界，
    本批把边界升级成规则，池子才不会长期停在 2 条。

    硬约束（全部为「不可判定 ⇒ 转人工」）：
      * `status=runtime_verified` 且 `risk_level` 低；
      * 非 `is_fragment_to_full`（片段→完整 = 嵌套 def 风险）；
      * `trust_score >= AUTO_TRIAGE_MIN_TRUST`；
      * 方法名非生命周期名（`_LIFECYCLE_NAMES`：构造/析构/启停，图上结构性失明）；
      * `blast_radius_of` 可判定且 `<= max_blast_radius`（`None` 一律转人工）；
      * `limit` 硬上限 1（自动分诊只做「一次一条」，不做批量放行）。
    """
    try:
        _lim = min(1, int(limit))
    except (TypeError, ValueError) as _le:
        _module_logger.debug("[311刀1] limit 非整数，按 1 处理: %s: %s", type(_le).__name__, _le)
        _lim = 1
    if _lim <= 0 or not isinstance(patch_history, list):
        return []
    _out: list = []
    _rej = {"status": 0, "risk": 0, "morph": 0, "trust": 0, "lifecycle": 0, "blast": 0}
    for _p in patch_history:
        if not isinstance(_p, dict):
            continue
        _status = _p.get("status")
        # ★315批刀5：小林裁定——approved 低风险（risk∈{低,low} 且 trust≥阈值）纳入自动分诊池；
        #   其余非 runtime_verified 的 approved（高风险）及所有其它状态仍转人工。
        if _status not in ("runtime_verified", "approved"):
            _rej["status"] += 1
            continue
        if _p.get("risk_level") not in ("低", "low"):
            _rej["risk"] += 1
            continue
        if is_fragment_to_full(_p):
            _rej["morph"] += 1
            continue
        try:
            _trust = float(_p.get("trust_score", 0) or 0)
        except (TypeError, ValueError) as _te:
            _module_logger.debug("[311刀1] trust_score 非数值，按 0 处理: %s: %s",
                                 type(_te).__name__, _te)
            _trust = 0.0
        if _trust < AUTO_TRIAGE_MIN_TRUST:
            _rej["trust"] += 1
            continue
        _m = str(_p.get("method") or "")
        if _m in _LIFECYCLE_NAMES:
            _rej["lifecycle"] += 1
            _module_logger.info("[311刀1] 自动分诊排除生命周期名 ⇒ 转人工: %s", _m)
            continue
        _br = blast_radius_of(_m, file=str(_p.get("file") or ""))
        if _br is None or _br > max_blast_radius:
            _rej["blast"] += 1
            _module_logger.info(
                "[311刀1] 自动分诊牵连不可判定/超限 ⇒ 转人工: method=%s blast=%s",
                _m, _br)
            continue
        _out.append(_p)
    # ★324批刀0：拒收分布对外可见（blocked_reason 指认拦截环节的唯一来源）
    _LAST_TRIAGE_REJ.clear()
    _LAST_TRIAGE_REJ.update(dict(_rej))
    _out.sort(key=lambda p: (-float(p.get("trust_score", 0) or 0),))
    _module_logger.info(
        "[311刀1] 自动分诊池: 命中 %d 条（拒收 status=%d risk=%d 形态=%d 置信=%d "
        "生命周期=%d 牵连=%d），单批派 %d 条",
        len(_out), _rej["status"], _rej["risk"], _rej["morph"], _rej["trust"],
        _rej["lifecycle"], _rej["blast"], min(len(_out), _lim))
    return _out[:_lim]



# ===== ★319批刀5（SC-2 回归自检 · 挂点）=====
_SC2_LAST: dict = {}
_SC2_MIN_INTERVAL_SEC = 600.0
_SC2_LAST_TS: dict = {}


def _sc2_probe(class_name: str = "PulseInnerWorld", reason: str = "") -> dict:
    """★319批刀5：跑一次 A1 并与进程内上次结果比对（**只许减不许增**）。

    只读：从最新图快照注入（不触发全量重扫）；节流 600s 防热点。
    红线：只产计数与结论，不改台账、不入队、不触发行动。
    """
    _now = time.time()
    if _now - float(_SC2_LAST_TS.get(class_name, 0.0) or 0.0) < _SC2_MIN_INTERVAL_SEC:
        return {"status": "throttled"}
    _SC2_LAST_TS[class_name] = _now
    try:
        from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer
        import json as _json
        _dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "data", "call_graph")
        _fs = sorted(f for f in os.listdir(_dir)
                     if f.startswith("graph_") and f.endswith(".json"))
        if not _fs:
            return {"status": "no_graph"}
        with open(os.path.join(_dir, _fs[-1]), encoding="utf-8") as _f:
            _g = _json.load(_f)
        _a = CallGraphAnalyzer()
        _a._graph_data = _g
        _r = _a.mastery_assertion_check(class_name)
        _failed = set((_r.get("a1_inbound") or {}).get("zero_in_degree_sample") or [])
        _prev = _SC2_LAST.get(class_name)
        _SC2_LAST[class_name] = _failed
        if _prev is None:
            _module_logger.info("[SC-2] 回归自检 基线建立 class=%s failed=%d reason=%s"
                                "（首跑不发结论）", class_name, len(_failed), reason or "-")
            return {"status": "baseline", "failed": len(_failed)}
        _res = CallGraphAnalyzer.sc2_regression_selfcheck(
            _prev, _failed, class_name=class_name,
            criteria_version=str(_r.get("criteria_version") or ""))
        _res["status"] = _res.get("verdict")
        return _res
    except Exception as _e:
        _module_logger.warning("[SC-2] 回归自检探针不可用（不影响主流程）: %s: %s",
                               type(_e).__name__, _e)
        return {"status": "error", "error": type(_e).__name__}

# ===== ★324批刀0：P-15 派单语义三拆（selected / applied / blocked_reason）=====
# 语义钉死（实测依据：logs/pulse.log 12:38—17:38 六圈同 id 全 False）：
#   · 历史 `dispatched` = len(_results) = **已在沙箱执行并回写**的条数
#     （既非"仅选中"，也非"应用到源码"——sandbox 的 motor.execute 只验证不落源码）
#   · `_write_back` 失败时**不改 status** ⇒ 同一补丁被每圈重复选中，
#     于是"dispatched=1 但账本零变化"恒成立：那不是自转成果，是**重复失败循环**
#   · 故本批补三件：三拆计数 / 失败退出上限 / 拦截环节指认
_DISPATCH_FAIL_CAP = 2      # 同一补丁累计验证失败达此次数 ⇒ 转人工，不再自动派单
_M324_TRACE_KEEP = 500      # 派单留痕文件最多保留行数（超限滚动截断，防无界增长）

#: 自动分诊最近一次的拒收分布（供 blocked_reason 指认拦截环节；`select_for_auto_triage` 写）
_LAST_TRIAGE_REJ: dict = {}


def _m324_trace_path() -> str:
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(_root, "data", "reports", "patch_dispatch_trace.jsonl")


def _m324_append_trace(rec: dict) -> str:
    """★324批刀0：派单留痕（追加一行 JSON；失败不影响派单主流程）。返回路径或空串。"""
    _p = _m324_trace_path()
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with io.open(_p, "a", encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as _e:
        silent_exc(_e, where="patch_execution_dispatch::_m324_append_trace", level="warning")
        return ""
    return _p


def _m324_fail_count(patch: dict) -> int:
    try:
        return max(0, int(patch.get("runtime_verify_fail_count", 0) or 0))
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="patch_execution_dispatch::_m324_fail_count 非数值按0",
                   level="debug")
        return 0


def _m324_blocked_reason(patch: dict, ledger: dict | None = None) -> str:
    """★324批刀0 / ★326批刀1：该补丁不可自动派单的原因（空串 = 可派）。只读。

    ★326：cap 判定用**持久累计值** `max(记录字段, 侧账)`——重启后不会归零，
    因此"永久失败补丁无限重试"不再可能（6ecc 实测：字段缺失但 trace 有 2 次失败证据）。
    """
    _fc = _m326_persisted_fail_count(patch, ledger)
    if _fc >= _DISPATCH_FAIL_CAP:
        return "verify_fail_cap(%d>=%d,persisted)" % (_fc, _DISPATCH_FAIL_CAP)
    return ""


def _m324_rej_reason() -> str:
    """★324批刀0：把自动分诊拒收分布压成一句"拦截环节"（按计数从大到小）。"""
    _items = [(k, int(v)) for k, v in (_LAST_TRIAGE_REJ or {}).items() if int(v or 0) > 0]
    if not _items:
        return ""
    _items.sort(key=lambda kv: (-kv[1], kv[0]))
    return "triage:" + ",".join("%s=%d" % kv for kv in _items)


def dispatch_and_verify(sandbox: Any, patch: dict) -> dict:
    """把补丁 modified_code 路由 sandbox 执行，返回执行结论（不落盘，由调用方回写）。

    执行走 PulseCodeSandbox.on_pulse(MotorEvent.EXECUTE)，命中其 DANGEROUS_KEYWORDS
    黑名单与子进程隔离沙箱；绝不 apply 到源码。
    """
    _pid = patch.get("id", "unknown")
    _code = patch.get("modified_code") or patch.get("original_code") or ""
    _lang = patch.get("language", "python")
    if not _code:
        return {"patch_id": _pid, "success": False, "error": "empty_code"}
    try:
        _res = sandbox.on_pulse({
            "event_type": "motor.execute",  # 等价 MotorEvent.EXECUTE
            "payload": {"code": _code, "language": _lang,
                        "user_name": "patch_dispatch_295", "task_id": _pid},
        })
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::dispatch_and_verify sandbox")
        return {"patch_id": _pid, "success": False,
                "error": f"sandbox_exc:{type(e).__name__}"}
    _ok = bool(isinstance(_res, dict) and _res.get("success"))
    _err = "" if _ok else ((_res or {}).get("error", "") if isinstance(_res, dict) else str(_res))
    return {"patch_id": _pid, "success": _ok, "error": _err}


def _write_back(patch: dict, verdict: dict) -> None:
    """刀3：把执行结果回写补丁字段（内存态；落盘由调用方统一原子写）。"""
    _ok = bool(verdict.get("success"))
    _err = verdict.get("error", "")
    patch["runtime_verified"] = _ok
    patch["runtime_verify_result"] = {
        "verified": _ok,
        "detail": ("[执行验证] motor.execute " + ("成功" if _ok else "失败")
                   + (f": {_err}" if _err else "")),
        "execution_source": "motor.execute",
        "executed_at": time.time(),
    }
    if not _ok:
        patch["runtime_verify_failure_reason"] = _err
        # ★324批刀0：失败必须可退出——否则同一补丁每圈重复选中
        #   （实测 12:38—17:38 六圈同一 id、结果全 False、账本零变化）
        # ★326批刀1：**双写**——既落记录字段，也落单调侧账（后者抗覆盖/抗重启）
        _persisted = _m326_bump_fail_ledger(patch.get("id"), reason=_err)
        patch["runtime_verify_fail_count"] = max(
            _m324_fail_count(patch) + 1, int(_persisted or 0))
        patch["runtime_verify_last_attempt_at"] = time.time()
        # ★326批刀1：留痕——本值来自"持久累计"而非内存计数
        patch["fail_count_persisted"] = int(patch["runtime_verify_fail_count"])
        patch["fail_count_source"] = "ledger+field(326.1)"
    else:
        patch["runtime_verify_fail_count"] = 0
        _m326_reset_fail_ledger(patch.get("id"))
        patch["fail_count_persisted"] = 0
        patch["fail_count_source"] = "ledger+field(326.1)"


def dispatch_runtime_verified_patches(limit: int = 1, framework: Any = None) -> dict:
    """主入口：每日派单 ≤1 条 runtime_verified 低风险补丁到 motor.execute，回写 runtime_verified。"""
    _fw = framework or get_dispatch_framework()
    if _fw is None:
        return {"status": "no_framework", "dispatched": 0}
    _sandbox = getattr(_fw, "organs", {}).get("代码沙箱")
    if _sandbox is None or not hasattr(_sandbox, "on_pulse"):
        return {"status": "no_sandbox", "dispatched": 0}
    if not getattr(_sandbox, "is_running", False):
        return {"status": "sandbox_stopped", "dispatched": 0}
    _hp = _history_path()
    if not os.path.isfile(_hp):
        return {"status": "no_history", "dispatched": 0}
    try:
        _hist = json.load(io.open(_hp, encoding="utf-8"))
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::dispatch load history")
        return {"status": "load_failed", "dispatched": 0}
    if isinstance(_hist, dict):
        _hist = _hist.get("patches") or []
    if not isinstance(_hist, list):
        return {"status": "history_not_list", "dispatched": 0}
    # ★第295批刀5：派单前先回扫全部 runtime_verified 低风险补丁，标记「片段→完整」可疑形态
    _rv_cands = [p for p in _hist if isinstance(p, dict)
                 and p.get("status") == "runtime_verified"
                 and p.get("risk_level") in ("低", "low")]
    _suspect_n = 0
    for _p in _rv_cands:
        if is_fragment_to_full(_p):
            _p["_295_suspect_morphology"] = True
            _p["_295_suspect_reason"] = "片段→完整(嵌套def风险)"
            _suspect_n += 1
    _selected = select_patch_for_execution(_hist, limit=limit)
    _sel_src = "pilot_whitelist"
    if not _selected:
        # ★第311批 刀1：首批试点白名单为空 ⇒ 走自动分诊（高置信低风险，单批仍 ≤1 条）。
        #   顺序不反：白名单是「先证明能跑通」的最窄边界，分诊是它的上位规则，
        #   白名单有料时优先级更高（确定性优先于统计置信）。
        _selected = select_for_auto_triage(_hist, limit=1)
        _sel_src = "auto_triage"
    # ★324批刀0：选中 ≠ 执行 ≠ 有副作用——三拆，且失败超限者转人工
    _n_selected = len(_selected)
    _blocked: list = []
    _dispatchable: list = []
    # ★326批刀1：一次派单只读一次侧账（传下去，避免每条候选各读一次盘）
    _m326_ledger = _m326_load_fail_ledger()
    for _p in _selected:
        _why = _m324_blocked_reason(_p, _m326_ledger)
        if _why:
            _blocked.append({"patch_id": str(_p.get("id", ""))[:32], "reason": _why})
            _module_logger.info(
                "[324刀0] 选中但转人工（不进沙箱）: patch=%s method=%s reason=%s",
                str(_p.get("id", ""))[:16], str(_p.get("method") or ""), _why)
            continue
        _dispatchable.append(_p)
    _results = []
    for _p in _dispatchable:
        _v = dispatch_and_verify(_sandbox, _p)
        _write_back(_p, _v)
        _results.append(_v)
    # 落盘（原子写；失败只 warning 不影响调度主流程）——有标记或有执行结果即落盘
    if _results or _suspect_n:
        try:
            _tmp = _hp + ".m295.tmp"
            with io.open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(_hist, _f, ensure_ascii=False, indent=2)
            os.replace(_tmp, _hp)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.patch_execution_dispatch::dispatch save history")
    _module_logger.info("[行动面通电] 回扫=%d 可疑=%d 派单=%d 结果=%s",
                        len(_rv_cands), _suspect_n, len(_results),
                        [(r.get("patch_id"), r.get("success")) for r in _results])
    # ★第307批 刀3：定位失败分布（三类分开计数，一眼看出是「图缺」还是「file 字段过时」）
    _module_logger.info("[307刀3] 定位失败分布: no_node=%d file_mismatch=%d no_file=%d",
                        _LOCATE_FAIL_COUNTS[LOCATE_FAIL_NO_NODE],
                        _LOCATE_FAIL_COUNTS[LOCATE_FAIL_FILE_MISMATCH],
                        _LOCATE_FAIL_COUNTS[LOCATE_FAIL_NO_FILE])
    # ★324批刀0：三拆计数（`dispatched` 保留旧口径=已进沙箱执行，兼容既有读法）
    _applied = sum(1 for _r in _results if isinstance(_r, dict) and _r.get("success"))
    _blocked_reason = ""
    if _blocked:
        _blocked_reason = ";" .join(sorted({str(_b.get("reason") or "").split("(")[0]
                                            for _b in _blocked}))
    elif not _n_selected:
        _blocked_reason = _m324_rej_reason() or "no_candidate"
    # ★326批刀1：本拍涉及补丁的持久累计失败次数（选中/被拦都记，便于复盘）
    _m326_fc_trace: dict = {}
    for _p in list(_selected or []):
        try:
            _m326_fc_trace[str(_p.get("id") or "")[:32]] = int(
                _m326_persisted_fail_count(_p, _m326_ledger))
        except Exception as _e:
            silent_exc(_e, where="patch_execution_dispatch::_m326_fc_trace", level="debug")
    _m324_append_trace({
        "ts": time.time(),
        "selected_source": _sel_src,
        "dispatched_selected": _n_selected,
        "dispatched_applied": _applied,
        "dispatched_executed": len(_results),
        "dispatched_verify_failed": len(_results) - _applied,
        "blocked_reason": _blocked_reason,
        "blocked_detail": _blocked[:5],
        "reswept": len(_rv_cands),
        "patch_ids": [str((_r or {}).get("patch_id") or "")[:32] for _r in _results],
        # ★326批刀1：把**持久累计**失败次数写进留痕（重启后复算 cap 的唯一依据）
        "fail_count_persisted": _m326_fc_trace,
        "fail_count_ledger_version": _M326_FAIL_LEDGER_VERSION,
    })
    return {"status": "ok", "reswept": len(_rv_cands), "suspect": _suspect_n,
            "dispatched": len(_results), "results": _results,
            "dispatched_selected": _n_selected,
            "dispatched_applied": _applied,
            "dispatched_verify_failed": len(_results) - _applied,
            "blocked_reason": _blocked_reason,
            "selected_source": _sel_src}


def register_with_daily_schedule(scheduler: Any = None) -> str:
    """把派单挂入 DailyScheduler 每日钩子（单批上限 1 条，每日 1 次）。"""
    if scheduler is None:
        from nucleus.self_awareness.DailyScheduler import get_daily_scheduler
        scheduler = get_daily_scheduler()
    # ★317批刀0：优先挂「节拍钩子」（挂既有 60s 轮询，按 interval 自节流）；
    #   不支持 tick 的调度器退回原日频钩子（行为不变）。
    if hasattr(scheduler, "add_tick_hook"):
        _module_logger.info(
            "[317刀0][分诊节拍] 注册 tick 钩子（默认日频 86400s，"
            "config.PATCH_DISPATCH_INTERVAL_SEC 可加密）")
        return scheduler.add_tick_hook("patch_execution_dispatch_317_tick",
                                       dispatch_on_tick)
    if not hasattr(scheduler, "add_daily_hook"):
        return ""
    return scheduler.add_daily_hook(
        "patch_execution_dispatch_295",
        lambda: dispatch_runtime_verified_patches(limit=1))
