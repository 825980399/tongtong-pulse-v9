# -*- coding: utf-8 -*-
"""424 刀2：知识库 L1 计数「多口径对账」。

★为什么会有「三方对不上」（T0 实测，2026-11-02）
------------------------------------------------
同一句「L1 有多少条」，框架里有 4 个**互不等价**的口径，谁都没错，但谁都不一样：

| 口径 | 取值位置 | 谓词 | 生产实测 |
|---|---|---|---|
| A 热池（权威） | `PulseNodePool.get_stats()["evol_distribution"]["L1"]` | hot+warm+cold **全部**，**含** `ephemeral` | **578** |
| B L1 快照·声明 | `pulse_l1_snapshot.json["l1_count"]` | 写盘时算好的数 | **562** |
| C L1 快照·实际 | `len(pulse_l1_snapshot.json["nodes"])` | `get_all_including_evicted()` 中 `L1 and not ephemeral` | **562** |
| D 冷存·索引 | `cold.index.json` 条目数 | 已归档的冷节点（全是 L1） | **5536** |
| E 冷存·Parquet | `cold/*.parquet` 去重 `node_id` | 同上 | **5536** |

⇒ **578 与 562 差的 16 条，是 `ephemeral`（瞬时感知节点）**：
  `get_stats()` 不过滤 ephemeral，`_save_l1_locked()` 过滤。
⇒ **5536 与 578 不是「矛盾」**：冷存是**已归档**集合，与热存本就是两个集合，
  放在一起比当然「差一个数量级」。真该查的是**重叠**（同一 id 不该既热又冷）。

本模块把四件事分清：
  1. **口径写死**：每个数都带 `predicate` 说明，`describe()` 可打印；
  2. **同口径才比**：池（非 ephemeral）↔ 文件条数 必须相等；
  3. **自洽性**：文件里的 `l1_count` 字段 ↔ `len(nodes)` 必须相等；
  4. **冷热重叠**：热 L1 的 id 不该出现在冷存里（真重复存储）。

落盘钩子只跑「便宜的两项」（不读 60MB parquet）；全量对账由
`reconcile(root, full=True)` 或 CLI 触发。

灰度：`config.ENABLE_M424_KNOWLEDGE_COUNT_RECONCILE`（缺键按 True）。

版本：424.1
"""

from __future__ import annotations

import json
import logging
import os

from nucleus._silent_except import silent_exc

VERSION = "424.1"
SWITCH = "ENABLE_M424_KNOWLEDGE_COUNT_RECONCILE"
LOG_TAG = "[424刀2]"

L1_SNAPSHOT_REL = "data/knowledge/pulse_l1_snapshot.json"
COLD_INDEX_REL = "data/knowledge/cold.index.json"
COLD_DIR_REL = "data/knowledge/cold"
RUNTIME_STATE_REL = "data/runtime_state.json"

# 口径清单（供 describe() 与报告引用；改谓词必须同步改这里）
PREDICATES = {
    "pool_l1_all": "node_pool.get_stats()['evol_distribution']['L1']（含 ephemeral）",
    "pool_l1_persistable": "hot+warm+cold 中 evol_level=='L1' 且 not ephemeral（= 落盘谓词）",
    "file_declared": "pulse_l1_snapshot.json 的 l1_count 字段（写盘时算的）",
    "file_nodes": "pulse_l1_snapshot.json 的 len(nodes)（真写进去的）",
    "cold_index": "cold.index.json 条目数（已归档冷节点）",
    "cold_parquet": "cold/*.parquet 去重 node_id 数（已归档冷节点）",
    "runtime_declared": "runtime_state.json 里 evolution.knowledge_evolution.l1_count（跨进程快照，仅供对照）",
}


def enabled() -> bool:
    """灰度开关；读不到按开启（对账只读 + 只打日志，失败无副作用）。"""
    try:
        import config as _c
        return bool(getattr(_c, SWITCH, True))
    except (ImportError, AttributeError) as _e:
        silent_exc(_e, where="knowledge_count_reconcile.enabled[424刀2]", level="debug")
        return True


def describe() -> dict:
    """返回口径说明（不读任何文件）。"""
    return {"version": VERSION, "switch": SWITCH, "enabled": enabled(),
            "predicates": dict(PREDICATES)}


def _m424_read_json(path):
    """读 JSON；读不到返回 None（**与「空文件」区分**：空 dict 也返回 {}）。"""
    try:
        with open(path, encoding="utf-8-sig") as _f:
            return json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="knowledge_count_reconcile._m424_read_json[424刀2]", level="debug")
        return None


# ---------------------------------------------------------------- 各口径的计数器
def count_pool_l1(node_pool) -> dict:
    """热池口径。★谓词必须与 `PulseSnapshot._save_l1_locked` 逐字一致。

    `get_all_including_evicted()` 会把冷层/已淘汰的也算进来 —— 这正是落盘用的那个集合，
    所以这里也用它，否则两个数天然对不上。
    """
    out = {"pool_l1_all": None, "pool_l1_persistable": None, "ephemeral_l1": 0,
           "pool_ids": None}
    if node_pool is None:
        return out
    try:
        stats = node_pool.get_stats()
        out["pool_l1_all"] = int(stats.get("evol_distribution", {}).get("L1", 0) or 0)
    except (AttributeError, TypeError, ValueError) as _e:
        silent_exc(_e, where="knowledge_count_reconcile.count_pool_l1(stats)[424刀2]")
    try:
        nodes = node_pool.get_all_including_evicted()
    except (AttributeError, TypeError) as _e:
        silent_exc(_e, where="knowledge_count_reconcile.count_pool_l1(get_all)[424刀2]")
        return out
    _l1 = [n for n in nodes if getattr(n, "evol_level", None) == "L1"]
    _persist = [n for n in _l1 if not getattr(n, "ephemeral", False)]
    out["pool_l1_persistable"] = len(_persist)
    out["ephemeral_l1"] = len(_l1) - len(_persist)
    out["pool_ids"] = {str(getattr(n, "node_id", "") or getattr(n, "id", "")) for n in _persist}
    out["pool_ids"].discard("")
    return out


def count_l1_file(root: str = ".") -> dict:
    """L1 快照文件口径：声明数 vs 实际条数（自洽性）。"""
    out = {"path": os.path.join(root, L1_SNAPSHOT_REL), "exists": False,
           "file_declared": None, "file_nodes": None, "node_ids": None, "created_at": None}
    if not os.path.isfile(out["path"]):
        return out
    out["exists"] = True
    doc = _m424_read_json(out["path"])
    if not isinstance(doc, dict):
        return out
    out["created_at"] = doc.get("created_at")
    try:
        out["file_declared"] = int(doc.get("l1_count"))
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="knowledge_count_reconcile.count_l1_file(declared)[424刀2]", level="debug")
    _nodes = doc.get("nodes")
    if isinstance(_nodes, list):
        out["file_nodes"] = len(_nodes)
        _ids = set()
        for _n in _nodes:
            if isinstance(_n, dict):
                _k = _n.get("node_id") or _n.get("id")
                if _k:
                    _ids.add(str(_k))
        out["node_ids"] = _ids
    return out


def count_cold(root: str = ".", with_parquet: bool = True) -> dict:
    """冷存口径：索引条目数 + Parquet 去重 node_id 数（可选，读 parquet 较慢）。"""
    out = {"index": None, "index_levels": None, "parquet": None, "parquet_levels": None,
           "index_ids": None}
    idx = _m424_read_json(os.path.join(root, COLD_INDEX_REL))
    if isinstance(idx, dict) and idx:
        out["index"] = len(idx)
        _lv = {}
        for _v in idx.values():
            _k = str(_v.get("level", "L1") if isinstance(_v, dict) else _v)
            _lv[_k] = _lv.get(_k, 0) + 1
        out["index_levels"] = _lv
        out["index_ids"] = set(idx.keys())
    if not with_parquet:
        return out
    cold_dir = os.path.join(root, COLD_DIR_REL)
    if not os.path.isdir(cold_dir):
        return out
    try:
        import pyarrow.parquet as _pq
    except Exception as _e:
        silent_exc(_e, where="knowledge_count_reconcile.count_cold(pyarrow)[424刀2]")
        return out
    _ids, _lv2 = {}, {}
    for _root, _dn, _fns in os.walk(cold_dir):
        for _fn in _fns:
            if not _fn.endswith(".parquet"):
                continue
            try:
                _t = _pq.read_table(os.path.join(_root, _fn), columns=["node_id", "evol_level"])
            except Exception as _e:
                silent_exc(_e, where="knowledge_count_reconcile.count_cold(read)[424刀2]")
                continue
            for _r in _t.to_pylist():
                _nid = str(_r.get("node_id", "") or "")
                if _nid and _nid not in _ids:
                    _ids[_nid] = str(_r.get("evol_level", "") or "")
                    _lv2[_ids[_nid]] = _lv2.get(_ids[_nid], 0) + 1
    out["parquet"] = len(_ids)
    out["parquet_levels"] = _lv2
    out["parquet_ids"] = set(_ids.keys())
    return out


def count_runtime_declared(root: str = ".") -> int | None:
    """runtime_state 里声明的 l1_count（跨进程快照，仅供对照，不参与判定）。"""
    doc = _m424_read_json(os.path.join(root, RUNTIME_STATE_REL))
    if not isinstance(doc, dict):
        return None
    try:
        return int(doc.get("evolution", {}).get("knowledge_evolution", {}).get("l1_count"))
    except (TypeError, ValueError, AttributeError) as _e:
        silent_exc(_e, where="knowledge_count_reconcile.count_runtime_declared[424刀2]", level="debug")
        return None


# ---------------------------------------------------------------- 对账
def reconcile(root: str = ".", node_pool=None, wrote_count: int | None = None,
              full: bool = False, at_write: bool = False) -> dict:
    """对账入口。**绝不抛异常**；真不一致时打 WARNING。

    Args:
        root: 项目根（测试传隔离根，绝不写死生产路径）。
        node_pool: 热池；`at_write=True` 时由调用方传入即为权威口径。
        wrote_count: 刚落盘的条数（`len(snapshot["nodes"])`），用于「写完立刻回读校验」。
        full: 是否连冷存一起对（要读 parquet，慢）。
        at_write: 落盘钩子模式 —— 只做便宜的两项，且不读 parquet。

    Returns:
        dict：``status`` ∈ ``ok`` / ``disabled`` / ``no_data`` / ``mismatch``；
        ``diffs`` 为逐项差异（空 list = 一致）；``counts`` 为各口径原始读数。
    """
    res = {"version": VERSION, "status": "ok", "diffs": [], "counts": {},
           "checks": [], "at_write": bool(at_write)}
    if not enabled():
        res["status"] = "disabled"
        return res

    try:
        _pool = count_pool_l1(node_pool)
        _file = count_l1_file(root)
        res["counts"].update({
            "pool_l1_all": _pool["pool_l1_all"],
            "pool_l1_persistable": _pool["pool_l1_persistable"],
            "ephemeral_l1": _pool["ephemeral_l1"],
            "file_declared": _file["file_declared"],
            "file_nodes": _file["file_nodes"],
            "l1_file_exists": _file["exists"],
            "l1_file_created_at": _file["created_at"],
        })

        # ① 文件自洽：声明数 == 实际条数
        if _file["exists"] and _file["file_declared"] is not None and _file["file_nodes"] is not None:
            res["checks"].append("l1_file_self_consistent")
            if _file["file_declared"] != _file["file_nodes"]:
                res["diffs"].append({
                    "code": "l1_file_self_inconsistent",
                    "detail": f"l1_count 字段={_file['file_declared']} 但 nodes={_file['file_nodes']}",
                })

        # ② 同口径相等：热池（非 ephemeral）== 文件条数
        if wrote_count is not None and _file["file_nodes"] is not None:
            res["checks"].append("wrote_vs_file")
            if int(wrote_count) != _file["file_nodes"]:
                res["diffs"].append({
                    "code": "wrote_not_persisted",
                    "detail": f"本应写入 {wrote_count} 条，文件里只有 {_file['file_nodes']} 条",
                })
        if _pool["pool_l1_persistable"] is not None and _file["file_nodes"] is not None:
            res["checks"].append("pool_vs_file")
            if _pool["pool_l1_persistable"] != _file["file_nodes"]:
                # 落盘后同一时刻读，两个数必须相等；不等说明中途有节点增删（正常）或写漏（异常）
                res["diffs"].append({
                    "code": "pool_file_mismatch",
                    "detail": (f"热池可落盘 L1={_pool['pool_l1_persistable']}，"
                               f"文件 L1={_file['file_nodes']}，差 "
                               f"{_pool['pool_l1_persistable'] - _file['file_nodes']}"),
                    "benign_if": "落盘与对账之间池子有增删时会出现，属正常竞态",
                })

        # ③ 冷存自洽 + 冷热不重叠（只在 full 模式做，读 parquet 慢）
        if full and not at_write:
            _cold = count_cold(root, with_parquet=True)
            res["counts"].update({
                "cold_index": _cold["index"], "cold_index_levels": _cold["index_levels"],
                "cold_parquet": _cold["parquet"], "cold_parquet_levels": _cold["parquet_levels"],
                "runtime_declared": count_runtime_declared(root),
            })
            if _cold["index"] is not None and _cold["parquet"] is not None:
                res["checks"].append("cold_index_vs_parquet")
                if _cold["index"] != _cold["parquet"]:
                    res["diffs"].append({
                        "code": "cold_index_parquet_mismatch",
                        "detail": f"索引 {_cold['index']} ≠ Parquet {_cold['parquet']}",
                    })
            _cold_ids = _cold.get("parquet_ids") or _cold.get("index_ids")
            _hot_ids = _pool.get("pool_ids") or _file.get("node_ids")
            if _cold_ids and _hot_ids:
                _both = _cold_ids & _hot_ids
                res["checks"].append("cold_hot_disjoint")
                res["counts"]["cold_hot_overlap"] = len(_both)
                if _both:
                    res["diffs"].append({
                        "code": "cold_hot_overlap",
                        "detail": f"{len(_both)} 个 node_id 同时存在于冷存与热存：{sorted(_both)[:5]}",
                    })
        else:
            res["counts"]["runtime_declared"] = count_runtime_declared(root)

        if res["diffs"]:
            res["status"] = "mismatch"
    except Exception as _e:
        silent_exc(_e, where="knowledge_count_reconcile.reconcile[424刀2]", level="warning")
        res["status"] = "error"
        res["error"] = f"{type(_e).__name__}: {_e}"
        return res

    if res["status"] == "mismatch":
        for _d in res["diffs"]:
            logging.getLogger("pulse").warning(
                "%s 计数对账不一致 code=%s %s", LOG_TAG, _d["code"], _d["detail"])
    elif not res["checks"]:
        res["status"] = "no_data"
    return res


def format_report(res: dict) -> str:
    """把对账结果排成人读文本（CLI / 报告用）。"""
    c = res.get("counts", {})
    lines = [
        f"{LOG_TAG} 知识库 L1 计数对账 v{res.get('version')}  status={res.get('status')}",
        "  口径（每个数各自成立，别跨口径比）：",
        f"    热池·含瞬时 (pool_l1_all)        = {c.get('pool_l1_all')}",
        (f"    热池·可落盘 (pool_l1_persistable)= {c.get('pool_l1_persistable')}"
         f"   其中 ephemeral={c.get('ephemeral_l1')}"),
        (f"    L1 文件·声明 (file_declared)     = {c.get('file_declared')}"
         f"   @{c.get('l1_file_created_at')}"),
        f"    L1 文件·实际 (file_nodes)        = {c.get('file_nodes')}",
        f"    冷存·索引   (cold_index)         = {c.get('cold_index')}  {c.get('cold_index_levels')}",
        f"    冷存·Parquet(cold_parquet)       = {c.get('cold_parquet')}  {c.get('cold_parquet_levels')}",
        f"    运行时声明  (runtime_declared)   = {c.get('runtime_declared')}  （跨进程，仅供对照）",
        f"  已做的检查：{res.get('checks') or '（无）'}",
    ]
    if res.get("diffs"):
        lines.append("  ★不一致：")
        for d in res["diffs"]:
            lines.append(f"    - {d['code']}: {d['detail']}")
    else:
        lines.append("  ✓ 无不一致")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - 手工对账入口
    import sys as _sys
    _root = _sys.argv[1] if len(_sys.argv) > 1 else "."
    print(format_report(reconcile(_root, full=True)))
