# -*- coding: utf-8 -*-
"""ast_release.py —— AST 可释放面释放点（344 批刀5）

## 病根（344 批 T0 实测复核，铁律109）

343 刀4 把「AST 常驻」评为**可释放档**（Name 76101↔83430 波动、dict 30.1万→32.6万），
但只出评估、未给释放点；心跳已接线（`[343心跳]`）而 `candidates_offered` 恒 0
⇒ **只决策不释放**。

344 T0 追加实测（`tmp/p344_root/d5_steady_state.json`）澄清了病因：
在**干净测法**下 `del tree` 后 AST 对象**完全回收**（6 轮稳态 residual=0）。
⇒ AST 本身不泄漏，泄漏风险在**「谁把树多吊了一会儿」**：

  * 解析函数里 `_tree` 在 `visit()` 之后**仍然是局部变量**，后续几十行都在持着它；
  * 异常路径下 `traceback → frame → 局部变量 → 整棵树`，只要异常对象被日志/账本
    长期引用，整棵树就长期活着（长跑进程里这是真实的长期持有）。

## 本模块的两件事

1. **用完即删（生产主路径）**：`note_ast_face_done()` —— 扫描侧 `del tree` 之后记账，
   **不持有任何树**（不引入新的长持面，这是本刀最重要的安全约束）。
2. **释放原语（决策链执行面）**：`release_ast_faces()` —— 心跳 G2/G3 档位时调用，
   清池 + `gc.collect(2)` + **前后实测** AST 活体计数，产出
   `[344释放] ast_cleanup nodes=<n> heap=<before>→<after>`（带 proc_gen）。

## 测法纪律（本刀被自己污染过两次，写下来防复发）

`gc.get_objects()` 返回的 **list 本身就是一组强引用**：任何对 AST 节点建 list/dict/
采样副本的"诊断"都会把对象吊住，使 `del tree` 失效，读数退化成「恒等于 held」。
⇒ 本模块所有计数一律走**内联生成器**（`sum(1 for o in gc.get_objects() if ...)`），
   绝不对 AST 建容器。隔离脚本 `d5_residual_diag.py` v1/v2 的 `released: 0` 即此坑。

## 口径（铁律155：归因计数 ≠ 分母）

* `nodes`（留痕主口径）= **实测**：`ast_live_count()` 释放前 − 释放后；
* `accounting`（留痕辅口径）= **账本**：池内登记时 `ast.walk` 数出的节点数之和；
* `faces` = 池内面数；`faces_done` = 自上次释放以来「用完即删」的面数（记账，不持有）。
* 实测取不到 ⇒ `nodes=unavailable`，**不填 0**（"没读到"≠"是 0"）。

## 安全阀

registry 若无限长持，**它自己就成了新的泄漏源**。
⇒ `MAX_PENDING_FACES` / `MAX_PENDING_NODES` 双上限，超限按最旧（LRU）立即释放并留痕
   `[344释放] ast_evict`。释放路径绝不因容量问题静默丢弃。
"""

import ast as _ast
import gc
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ast_release")

#: 版本戳（口径变更必须升版，便于报表对表）
AST_RELEASE_VERSION = "344.ast.rel.v1"

#: 待释放面硬上限（防 registry 自体成泄漏源）
MAX_PENDING_FACES = 64
MAX_PENDING_NODES = 400_000

#: 登记池：handle -> {"tree", "nodes", "source", "proc_gen", "ts"}
_PENDING: dict[str, dict] = {}
_PENDING_NODES = 0
_SEQ = 0

_STATS: dict[str, Any] = {
    "registered": 0,
    "evicted": 0,
    "release_calls": 0,
    "nodes_released": 0,
    "faces_done": 0,
}


# ---------------------------------------------------------------- 代次
def _m344_pg() -> str:
    """取代次标识。**不新开 import 依赖**：先用已加载模块，取不到记 unknown（不填 0）。"""
    _m = sys.modules.get("nucleus.proc_gen")
    if _m is not None:
        try:
            return str(_m.proc_gen_str() or "unknown")
        except Exception as _e:
            silent_exc(_e, where="ast_release::_m344_pg", level="debug")
    return "unknown"


# ---------------------------------------------------------------- 实测读数
def ast_live_count() -> int | None:
    """当前进程 AST 活体对象数（**内联生成器，不建容器**）。

    Returns:
        int，或 None（`gc.get_objects()` 不可用）。None ≠ 0。
    """
    try:
        return sum(1 for _o in gc.get_objects() if isinstance(_o, _ast.AST))
    except Exception as _e:  # noqa: BLE001
        silent_exc(_e, where="ast_release::ast_live_count", level="debug")
        return None


# ---------------------------------------------------------------- 容量阀
def _m344_evict_oldest(need_faces: int, need_nodes: int) -> list:
    """按最旧优先驱逐，直到再放得下 (need_faces, need_nodes)。返回被驱逐的 handle。"""
    global _PENDING_NODES
    _drop: list = []
    if need_faces <= 0 and need_nodes <= 0:
        return _drop
    while _PENDING and (len(_PENDING) + need_faces > MAX_PENDING_FACES
                        or _PENDING_NODES + need_nodes > MAX_PENDING_NODES):
        _oldest = min(_PENDING.items(), key=lambda kv: kv[1].get("ts", 0.0))[0]
        _ent = _PENDING.pop(_oldest, None)
        if _ent is None:
            break
        _PENDING_NODES -= int(_ent.get("nodes") or 0)
        _ent.pop("tree", None)
        _drop.append(_oldest)
        _STATS["evicted"] += 1
    if _drop:
        # 容量驱逐是**设计内**行为，但必须可追溯（不是静默丢）
        _module_logger.info(
            "[344释放] ast_evict 容量阀驱逐 %d 面 剩余面=%d 剩余节点=%d 上限=%d/%d proc_gen=%s",
            len(_drop), len(_PENDING), _PENDING_NODES,
            MAX_PENDING_FACES, MAX_PENDING_NODES, _m344_pg())
    return _drop


# ---------------------------------------------------------------- 登记 / 记账
def register_ast_face(tree: Any, *, source: str = "",
                      proc_gen: str | None = None) -> str | None:
    """登记一个**待释放的 AST 面**（转移所有权；调用方交树后不应再用）。

    适用面：扫描侧在异常兜底路径上把手上还活着的树交出来，
    以及隔离验收脚本构造 AST 面（对应任务书验收①）。

    Returns:
        handle 字符串；`tree is None` ⇒ None（不登记空面）。
    """
    global _PENDING_NODES, _SEQ
    if tree is None:
        return None
    try:
        _n = sum(1 for _ in _ast.walk(tree))
    except Exception as _e:  # noqa: BLE001
        # 走不通的树照样要能释放 ⇒ 节点数记 0 但面照样登记（节点数只是账本辅口径）
        silent_exc(_e, where="ast_release::register_ast_face walk", level="debug")
        _n = 0
    _m344_evict_oldest(1, int(_n))
    _SEQ += 1
    _h = "astf_%d_%d" % (int(time.time() * 1000), _SEQ)
    _PENDING[_h] = {"tree": tree, "nodes": int(_n), "source": str(source or ""),
                    "proc_gen": str(proc_gen or _m344_pg()), "ts": time.time()}
    _PENDING_NODES += int(_n)
    _STATS["registered"] += 1
    return _h


def note_ast_face_done(nodes: int | None = None, *, source: str = "") -> None:
    """扫描侧**已 del 树**之后记账（生产主路径）。

    只累加计数，**不持有任何对象** —— 这是本刀"不引入新长持面"的落地形式。
    `nodes` 只作观测读数（面规模），不参与释放动作。
    """
    _STATS["faces_done"] += 1
    if nodes is not None:
        _STATS["nodes_done"] = int(_STATS.get("nodes_done", 0)) + int(nodes)
    if source:
        _STATS["last_source"] = source


def pending_stats() -> dict:
    """池现状（心跳报 `candidates_offered` 用：**池内真实面数**，不是 0 常量）。"""
    return {
        "version": AST_RELEASE_VERSION,
        "faces": len(_PENDING),
        "nodes": int(_PENDING_NODES),
        "max_faces": MAX_PENDING_FACES,
        "max_nodes": MAX_PENDING_NODES,
        "faces_done": int(_STATS["faces_done"]),
        "nodes_done": int(_STATS.get("nodes_done", 0)),
        "stats": dict(_STATS),
        "proc_gen": _m344_pg(),
    }


# ---------------------------------------------------------------- 释放执行面
def release_ast_faces(*, tier: str = "", heap_before: float | None = None,
                      heap_probe=None, proc_gen: str | None = None,
                      gc_collect: bool = True, where: str = "") -> dict:
    """执行一次 AST 释放（心跳 G2/G3 档位调用）。

    Args:
        tier: 触发档位（`G2_SOFT_RELEASE` / `G3_COLD_DEMOTE`），只作留痕。
        heap_before: 释放前 heap（MB）；None ⇒ 留痕记 `unavailable`（不填 0）。
        heap_probe: 可选 `() -> float | None`，释放后调一次取 heap 后值。
        proc_gen: 代次；None ⇒ 自行取（取不到 unknown）。
        gc_collect: 是否 `gc.collect(2)`（默认 True）。
        where: 留痕后缀（心跳侧带 beat/every）。

    Returns:
        `{version, tier, faces, accounting_nodes, measured_nodes, nodes,
          heap_before, heap_after, gc_collected, verdict, proc_gen, stats}`
        `nodes` = 实测下降（取不到 ⇒ None）；`measured_nodes` 与 `accounting_nodes`
        并列，谁缺谁就 None —— 两者不互相冒充。
    """
    _pg = str(proc_gen or _m344_pg())
    global _PENDING_NODES
    _before = ast_live_count()
    _faces = len(_PENDING)
    _acct = int(_PENDING_NODES)

    # 断引用：逐个 pop 并清掉 tree 键（handle 顺序不敏感，全清）
    _dropped = 0
    for _h in list(_PENDING.keys()):
        _ent = _PENDING.pop(_h, None)
        if _ent is not None:
            _ent.pop("tree", None)
            _dropped += 1
    _PENDING_NODES = 0

    _gc_done = False
    if gc_collect:
        try:
            gc.collect(2)
            _gc_done = True
        except Exception as _e:  # noqa: BLE001
            silent_exc(_e, where="ast_release::release_ast_faces gc", level="warning")
            _module_logger.warning(
                "[344释放] gc.collect(2) 失败（不静默）: %s: %s",
                type(_e).__name__, _e)

    _after = ast_live_count()
    _meas: int | None = None
    if _before is not None and _after is not None:
        _meas = max(0, int(_before) - int(_after))

    _heap_after = None
    if heap_probe is not None:
        try:
            _heap_after = heap_probe()
        except Exception as _e:  # noqa: BLE001
            silent_exc(_e, where="ast_release::release_ast_faces heap_probe", level="debug")
            _heap_after = None

    _STATS["release_calls"] += 1
    if _meas:
        _STATS["nodes_released"] += int(_meas)

    _module_logger.info(
        "[344释放] ast_cleanup nodes=%s accounting=%d faces=%d heap=%s→%s "
        "tier=%s gc=%s live=%s→%s proc_gen=%s %s",
        ("%d" % _meas) if _meas is not None else "unavailable",
        _acct, _dropped,
        ("%.1fMB" % heap_before) if isinstance(heap_before, (int, float)) else "unavailable",
        ("%.1fMB" % _heap_after) if isinstance(_heap_after, (int, float)) else "unavailable",
        tier or "-", "yes" if _gc_done else "no",
        _before if _before is not None else "unavailable",
        _after if _after is not None else "unavailable",
        _pg, where or "",
    )

    return {
        "version": AST_RELEASE_VERSION,
        "tier": tier,
        "faces": _dropped,
        "accounting_nodes": _acct,
        "measured_nodes": _meas,
        # `nodes` 是留痕主口径 = 实测；缺实测就 None（**不拿账本冒充实测**）
        "nodes": _meas,
        "heap_before": heap_before,
        "heap_after": _heap_after,
        "gc_collected": _gc_done,
        "live_before": _before,
        "live_after": _after,
        "proc_gen": _pg,
        "verdict": ("released" if (_dropped or (_meas or 0) > 0) else "noop_pool_empty"),
        "stats": dict(_STATS),
    }


def reset_stats() -> None:
    """仅测试/隔离用：清池 + 归零统计。**生产不得调用**（会把账冲平）。"""
    global _PENDING_NODES
    for _h in list(_PENDING.keys()):
        _ent = _PENDING.pop(_h, None)
        if _ent is not None:
            _ent.pop("tree", None)
    _PENDING_NODES = 0
    for _k in list(_STATS.keys()):
        _STATS[_k] = 0
