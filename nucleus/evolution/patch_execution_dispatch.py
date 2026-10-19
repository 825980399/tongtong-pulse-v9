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
    _module_logger.info(
        "[317刀0][分诊节拍] 触发自动分诊 interval=%.0fs dispatched=%s "
        "hard_cap=1 blast_cap=1（安全闸未放宽）",
        _iv, _r.get("dispatched") if isinstance(_r, dict) else "?")
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
    _out.sort(key=lambda p: (-float(p.get("trust_score", 0) or 0),))
    _module_logger.info(
        "[311刀1] 自动分诊池: 命中 %d 条（拒收 status=%d risk=%d 形态=%d 置信=%d "
        "生命周期=%d 牵连=%d），单批派 %d 条",
        len(_out), _rej["status"], _rej["risk"], _rej["morph"], _rej["trust"],
        _rej["lifecycle"], _rej["blast"], min(len(_out), _lim))
    return _out[:_lim]


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
    if not _selected:
        # ★第311批 刀1：首批试点白名单为空 ⇒ 走自动分诊（高置信低风险，单批仍 ≤1 条）。
        #   顺序不反：白名单是「先证明能跑通」的最窄边界，分诊是它的上位规则，
        #   白名单有料时优先级更高（确定性优先于统计置信）。
        _selected = select_for_auto_triage(_hist, limit=1)
    _results = []
    for _p in _selected:
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
    return {"status": "ok", "reswept": len(_rv_cands), "suspect": _suspect_n,
            "dispatched": len(_results), "results": _results}


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
