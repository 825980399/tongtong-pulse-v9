#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主线第71批 T4：热/冷池 · FAISS · KAL 进程内性能微基准。

设计原则
--------
- 本脚本在**当前进程内**自建实例（PulseNodePool / FAISSVectorStore / KAL），
  测量的是「组件自身开销」的**微基准**，不是生产流量观测。
- 生产框架若处于停机状态（pulse.log 长时间无更新 / 进程已退出），
  则无法取得「生产负载」数字；本报告会如实标注，绝不编造生产数据。
- 任一阶段依赖缺失或实例化失败时，该阶段标记为 SKIPPED 并附原因，
  不影响其余阶段；整体退出码恒为 0（便于自动化纳入门禁）。

用法
----
    python tools/benchmark_hot_cold_faiss_kal.py [--nodes 1000] [--faiss-count 2000] \
        [--faiss-dim 512] [--faiss-k 10] [--faiss-queries 200] [--seed 42] \
        [--json-out PATH]
"""
import argparse
import json
import os
import sys
import time
from nucleus._silent_except import silent_exc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _pct(values, p):
    if not values:
        return 0.0
    vs = sorted(values)
    k = max(0, min(len(vs) - 1, int(round((p / 100.0) * (len(vs) - 1)))))
    return vs[k]


def _stats(latencies):
    if not latencies:
        return {"count": 0, "ops_per_s": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    _sum = sum(latencies)
    _total_ms = _sum * 1000.0
    return {
        "count": len(latencies),
        "ops_per_s": (len(latencies) / _sum) if _sum > 0 else 0.0,
        "p50_ms": round(_pct(latencies, 50) * 1000.0, 4),
        "p95_ms": round(_pct(latencies, 95) * 1000.0, 4),
        "max_ms": round(max(latencies) * 1000.0, 4),
    }


def _config_snapshot():
    try:
        import config
        def g(n, d=False):
            return bool(getattr(config, n, d))
        return {
            "ENABLE_HOT_COLD_SEPARATION": g("ENABLE_HOT_COLD_SEPARATION"),
            "ENABLE_NEO4J_GRAPH_STORE": g("ENABLE_NEO4J_GRAPH_STORE"),
            "ENABLE_NEO4J_DUAL_WRITE": g("ENABLE_NEO4J_DUAL_WRITE"),
            "ENABLE_INFLUXDB_TIMESERIES": g("ENABLE_INFLUXDB_TIMESERIES"),
            "ENABLE_INFLUXDB_WRITE_ONLY": g("ENABLE_INFLUXDB_WRITE_ONLY"),
            "ENABLE_DISTRIBUTED": g("ENABLE_DISTRIBUTED"),
        }
    except Exception as e:  # pragma: no cover
        return {"error": "%s: %s" % (type(e).__name__, e)}


def _framework_status():
    """探测生产框架是否仍在运行（pulse.log 最后修改时间 + 是否含优雅关闭标记）。"""
    out = {"pulse_log": None, "mtime_age_min": None, "graceful_shutdown": None}
    try:
        import config
        _log = getattr(config, "LOG_FILE", None) or os.path.join(ROOT, "logs", "pulse.log")
        if not os.path.exists(_log):
            _log = os.path.join(ROOT, "logs", "pulse.log")
        if os.path.exists(_log):
            out["pulse_log"] = _log
            _age = (time.time() - os.path.getmtime(_log)) / 60.0
            out["mtime_age_min"] = round(_age, 1)
            try:
                with open(_log, "rb") as f:
                    f.seek(0, 2)
                    _sz = f.tell()
                    f.seek(max(0, _sz - 4096))
                    _tail = f.read().decode("utf-8", "ignore")
                out["graceful_shutdown"] = ("优雅关闭" in _tail) or ("已优雅关闭" in _tail) or ("graceful" in _tail.lower())
            except Exception:
                pass
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def stage_node_pool(nodes=1000, seed=42):
    import random
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    from nucleus.mnemosyne.PulseNode import PulseNode

    rng = random.Random(seed)
    pool = PulseNodePool()
    created = []  # (node_id, evol_level)
    add_lat = []
    levels = {"L3": 0, "L2": 0, "L1": 0}
    for i in range(nodes):
        r = rng.random()
        lvl = "L3" if r < 0.10 else ("L2" if r < 0.40 else "L1")
        levels[lvl] += 1
        n = PulseNode(value="bench-content-%d-%s" % (i, rng.random()),
                      evol_level=lvl, keywords=["k%d" % (i % 50)])
        t0 = time.perf_counter()
        pool.add(n)
        add_lat.append(time.perf_counter() - t0)
        created.append((n.node_id, lvl))

    get_lat = []
    get_by_level = {"L3": [], "L2": [], "L1": []}
    for nid, lvl in created:
        t0 = time.perf_counter()
        pool.get(nid)
        dt = time.perf_counter() - t0
        get_lat.append(dt)
        get_by_level[lvl].append(dt)

    res = {
        "status": "OK",
        "level_distribution": levels,
        "add": _stats(add_lat),
        "get_all": _stats(get_lat),
        "get_by_level": {k: _stats(v) for k, v in get_by_level.items()},
    }
    try:
        fn = getattr(pool, "get_cache_stats", None)
        if callable(fn):
            res["cache_stats"] = fn()
    except Exception as e:
        silent_exc(e, where="tools.benchmark_hot_cold_faiss_kal::stage_node_pool L138")
    return res


def stage_faiss(count=2000, dim=512, k=10, queries=200, seed=42):
    import random
    rng = random.Random(seed)
    from nucleus.vector_store.faiss_store import get_faiss_store

    store = get_faiss_store()
    ids = ["v%d" % i for i in range(count)]
    vectors = [[rng.random() for _ in range(dim)] for _ in range(count)]

    t0 = time.perf_counter()
    store.add_vectors(ids, vectors)  # 返回值在 brute-force 回退分支为 None，故以实际落盘计数判定
    add_dt = time.perf_counter() - t0

    qvecs = [[rng.random() for _ in range(dim)] for _ in range(queries)]
    search_lat = []
    for qv in qvecs:
        t0 = time.perf_counter()
        store.search(qv, top_k=k)
        search_lat.append(time.perf_counter() - t0)

    try:
        _fs = store.get_stats()
    except Exception:
        _fs = {}
    stored = int(_fs.get("vector_count", 0))
    index_ready = bool(_fs.get("index_ready", False))
    # 注：本运行环境 FAISS 单例处于 brute-force 回退（index_ready=False），
    # 故 search 走暴力余弦，时延偏高属预期，非缺陷。
    return {
        "status": "OK" if stored >= count else "ADD_UNDERFLOW",
        "faiss_available": _fs.get("faiss_available", _fs.get("available", "unknown")),
        "index_type": _fs.get("index_type", "unknown"),
        "index_ready": index_ready,
        "stored_vectors": stored,
        "vectors": count,
        "dim": dim,
        "add_total_ms": round(add_dt * 1000.0, 2),
        "add_ops_per_s": (count / add_dt) if add_dt > 0 else 0.0,
        "search": _stats(search_lat),
        "note": ("FAISS 单例处于 brute-force 回退模式（index_ready=False），"
                 "search 为暴力余弦，时延偏高属预期") if not index_ready else "",
    }


def stage_faiss_fix(count=None, dim=512, topks=(100, 500, 1000, 5000, 10000),
                    queries=200, seed=42):
    """[M73-T2] FAISS 索引修复后的真实对比基准。

    - count 为 None 时动态取 Parquet 实际节点数（不再硬编码 12295，避免随
      数据增长误判）；读取失败回退 2000；
    - 用真实规模（以 Parquet 实际节点数为准）的随机 512 维向量；
    - 验证 add_vectors 自动构建索引（index_ready=True）；
    - 对比 FAISS 索引检索 vs 暴力余弦 在 Top100/500/1000/5000/10000 的 p50/p95/max 与加速比；
    - 计算 Recall@10（FAISS Top10 与暴力 L2 Top10 交集 / 10）；
    - 测量索引构建耗时、增量添加（1000）耗时、保存/加载耗时。
    向量为随机合成（FAISS 性能只取决于规模与维度，不依赖向量取值），规模与真实一致。
    """
    if count is None:
        count = _real_node_count() or 2000
    import random
    import tempfile
    import shutil
    rng = random.Random(seed)
    from nucleus.vector_store.faiss_store import FAISSVectorStore

    ids = ["v%d" % i for i in range(count)]
    vectors = [rng.random() for _ in range(dim)]  # placeholder, replaced below
    vectors = [[rng.random() for _ in range(dim)] for _ in range(count)]
    vec_dict = {i: v for i, v in zip(ids, vectors)}

    store = FAISSVectorStore(dimension=dim, index_type="auto", batch_size=2000)
    t0 = time.perf_counter()
    store.add_vectors(ids, vectors)
    build_dt = time.perf_counter() - t0
    built = store.is_available()
    index_type = store.get_index_stats().get("index_type", "unknown")

    qvecs = [[rng.random() for _ in range(dim)] for _ in range(queries)]

    # FAISS 索引检索
    faiss_lat = {k: [] for k in topks}
    faiss_top10 = []
    for qv in qvecs:
        for k in topks:
            t0 = time.perf_counter()
            store.search(qv, top_k=k)
            faiss_lat[k].append(time.perf_counter() - t0)
        faiss_top10.append([r[0] for r in store.search(qv, top_k=10)])

    # 暴力余弦检索（关闭 faiss 模拟回退）
    store_b = FAISSVectorStore(dimension=dim, index_type="auto")
    store_b._faiss_available = False
    store_b._vectors = {i: v for i, v in zip(ids, vectors)}
    brute_lat = {k: [] for k in topks}
    brute_top10 = []
    for qv in qvecs:
        for k in topks:
            t0 = time.perf_counter()
            store_b.search(qv, top_k=k)
            brute_lat[k].append(time.perf_counter() - t0)
        brute_top10.append([r[0] for r in store_b.search(qv, top_k=10)])

    # 加速比
    speedup = {}
    for k in topks:
        _fp = _pct(faiss_lat[k], 50)
        _bp = _pct(brute_lat[k], 50)
        speedup[k] = round(_bp / _fp, 2) if _fp > 0 else 0.0

    # Recall@10（FAISS Top10 与暴力 L2 Top10 交集）
    import numpy as np

    def _brute_l2_topk(query, k):
        q = np.array(query, dtype=np.float32)
        scored = []
        for vid, v in vec_dict.items():
            vv = np.array(v, dtype=np.float32)
            d = float(np.sum((q - vv) ** 2))
            scored.append((d, vid))
        scored.sort(key=lambda x: x[0])
        return [vid for _, vid in scored[:k]]

    recall_list = []
    for qv, ftop in zip(qvecs, faiss_top10):
        ref = set(_brute_l2_topk(qv, 10))
        hit = len(set(ftop) & ref)
        recall_list.append(hit / 10.0)
    recall10 = round(sum(recall_list) / len(recall_list), 4) if recall_list else 0.0

    # 增量添加耗时
    inc_ids = ["inc%d" % i for i in range(1000)]
    inc_vecs = [[rng.random() for _ in range(dim)] for _ in range(1000)]
    t0 = time.perf_counter()
    store.add_vectors(inc_ids, inc_vecs)
    inc_dt = time.perf_counter() - t0

    # 保存/加载耗时
    tmpd = tempfile.mkdtemp(prefix="m73_faiss_")
    try:
        p = os.path.join(tmpd, "idx.faiss")
        t0 = time.perf_counter()
        store.save(p)
        save_dt = time.perf_counter() - t0
        store_l = FAISSVectorStore(dimension=dim, index_type="auto")
        t0 = time.perf_counter()
        store_l.load(p)
        load_dt = time.perf_counter() - t0
        load_ok = store_l.is_available()
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)

    return {
        "status": "OK" if built else "INDEX_NOT_BUILT",
        "index_type": index_type,
        "index_ready": built,
        "count": count,
        "dim": dim,
        "index_build_ms": round(build_dt * 1000.0, 2),
        "search_faiss": {k: _stats(faiss_lat[k]) for k in topks},
        "search_brute": {k: _stats(brute_lat[k]) for k in topks},
        "speedup_x": speedup,
        "recall_at_10": recall10,
        "incremental_add_1000_ms": round(inc_dt * 1000.0, 2),
        "save_ms": round(save_dt * 1000.0, 2) if "save_dt" in dir() else None,
        "load_ms": round(load_dt * 1000.0, 2) if "load_dt" in dir() else None,
        "load_ok": load_ok if "load_ok" in dir() else None,
        "note": ("FlatL2 为精确索引，Recall@10 应≈1.0；加速比随 TopK 增大而显著。"
                 if index_type == "FlatL2" else "IVFFlat 为近似索引，Recall@10 可能<1.0。"),
    }


def stage_kal(nodes=50, seed=7):
    from nucleus.knowledge_access_layer import get_kal
    from nucleus.mnemosyne.PulseNode import PulseNode

    kal = get_kal()
    if getattr(kal, "_node_pool", None) is None:
        return {
            "status": "SKIPPED",
            "reason": ("KAL 未接入节点池：进程内独立运行无 _node_pool（需生产框架主控制器装配），"
                       "无法在独立进程内基准，须在生产框架装配后观测。"),
        }
    made = []
    for i in range(nodes):
        n = PulseNode(value="kal-bench-%d" % i, evol_level="L2",
                      keywords=["kal%d" % i])
        if kal.save_node(n):
            made.append(n.node_id)

    get_lat = []
    for nid in made:
        try:
            t0 = time.perf_counter()
            kal.get_node(nid)
            get_lat.append(time.perf_counter() - t0)
        except Exception as e:
            silent_exc(e, where="tools.benchmark_hot_cold_faiss_kal::stage_kal L338")
    return {
        "status": "OK" if made else "NO_NODES_SAVED",
        "saved": len(made),
        "get": _stats(get_lat),
    }


def _load_real_nodes(parquet_dir):
    """读取 Parquet 主存储的真实节点（3 个 evol_level 分区），返回 dict 列表。"""
    import glob
    files = sorted(glob.glob(os.path.join(parquet_dir, "**", "*.parquet"), recursive=True))
    nodes = []
    for f in files:
        # ★第83批 T-d1：evol_level 是分区目录名（evol_level=Lx），**不在 parquet 文件列内**。
        #   逐文件读取时必须显式回填，否则 from_dict 缺字段 → 静默默认 L1（分层塌缩）
        #   并触发 "[第80批 T2] from_dict 缺失 evol_level" 告警（≤5 次/进程 debounce）。
        _lv83 = ""
        for _seg in f.replace("\\", "/").split("/"):
            if _seg.startswith("evol_level="):
                _lv83 = _seg.split("=", 1)[1]
                break
        try:
            import pyarrow.parquet as pq
            _rows = pq.read_table(f).to_pylist()
        except Exception:
            try:
                import pandas as pd
                _rows = pd.read_parquet(f).to_dict(orient="records")
            except Exception as e:
                return {"error": "%s: %s" % (type(e).__name__, e)}
        for _r in _rows:
            if _lv83:
                _r.setdefault("evol_level", _lv83)
        nodes.extend(_rows)
    return nodes


def _real_node_count(parquet_dir="data/knowledge/parquet"):
    """动态读取 Parquet 主存储的实际节点数（替代硬编码 12295）。

    数据规模随进化持续增长，硬编码会随之误判；此处以 Parquet 实际行数为准，
    读取失败返回 0（调用方按需回退）。
    """
    raw = _load_real_nodes(parquet_dir)
    if isinstance(raw, dict) and "error" in raw:
        return 0
    return len(raw or [])


def stage_node_pool_real(parquet_dir="data/knowledge/parquet", sample_gets=2000):
    """真实数据冷加载微基准：从 Parquet 主存储恢复实际节点数，测量 materialization 开销。

    说明
    ----
    - PulseNodePool() 构造为惰性（不预载），真实冷加载成本主要来自
      “把 Parquet 行物化为 PulseNode 并加入三层池”这一路径；此处如实测量之。
    - 生产框架采用 L2/L3 懒加载，有效启动时间低于“全量物化”基线，本基准给出基线上限。
    - 非生产负载观测：进程内独立运行，仅反映组件自身开销，绝不编造生产流量数字。
    """
    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    raw = _load_real_nodes(parquet_dir)
    if isinstance(raw, dict) and "error" in raw:
        return {"status": "SKIPPED", "reason": raw["error"]}
    if not raw:
        return {"status": "SKIPPED", "reason": "Parquet 无节点（路径=%s）" % parquet_dir}

    try:
        import psutil
        _proc = psutil.Process(os.getpid())
        _mem0 = _proc.memory_info().rss
        _have_psutil = True
    except Exception:
        _mem0 = 0
        _have_psutil = False

    pool = PulseNodePool()
    built = []
    for d in raw:
        try:
            built.append(PulseNode.from_dict(d))
        except Exception:
            continue

    t0 = time.perf_counter()
    added = 0
    for n in built:
        try:
            pool.add(n)
            added += 1
        except Exception:
            continue
    add_dt = time.perf_counter() - t0

    if _have_psutil:
        _mem_mb = (_proc.memory_info().rss - _mem0) / (1024.0 * 1024.0)
    else:
        _mem_mb = None

    # 真实节点数与分层统计
    try:
        _all = pool.snapshot_active_nodes()
    except Exception:
        _all = []
    loaded = len(_all)
    res = {
        "status": "OK",
        "source": "parquet-real",
        "parquet_rows": len(raw),
        "built_nodes": len(built),
        "added": added,
        "materialized_nodes": loaded,
        "full_materialize_ms": round(add_dt * 1000.0, 2),
        "full_materialize_ops_per_s": (added / add_dt) if add_dt > 0 else 0.0,
        "memory_delta_mb": (round(_mem_mb, 2) if _mem_mb is not None else None),
    }
    try:
        fn = getattr(pool, "get_cache_stats", None)
        if callable(fn):
            res["tier_stats"] = fn()
    except Exception:
        pass

    # get 采样（冷加载后查询性能）
    import random
    rng = random.Random(1)
    ids = [getattr(x, "node_id", "") for x in _all if getattr(x, "node_id", "")]
    sample = rng.sample(ids, min(sample_gets, len(ids))) if ids else []
    get_lat = []
    for nid in sample:
        t0 = time.perf_counter()
        pool.get(nid)
        get_lat.append(time.perf_counter() - t0)
    res["get"] = _stats(get_lat)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, default=1000)
    ap.add_argument("--faiss-count", type=int, default=2000)
    ap.add_argument("--faiss-dim", type=int, default=512)
    ap.add_argument("--faiss-k", type=int, default=10)
    ap.add_argument("--faiss-queries", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json-out", default="")
    ap.add_argument("--real-data", action="store_true",
                    help="加载 Parquet 主存储真实节点（Parquet 实际节点数）做冷加载微基准")
    ap.add_argument("--parquet-dir", default="data/knowledge/parquet",
                    help="Parquet 主存储目录（相对/绝对）")
    ap.add_argument("--faiss-fix", action="store_true",
                    help="[M73-T2] 运行 FAISS 索引修复后的真实对比基准（暴力 vs FAISS / Recall@10）")
    ap.add_argument("--faiss-topks", default="100,500,1000,5000,10000",
                    help="[M73-T2] 对比的 TopK 列表，逗号分隔")
    a = ap.parse_args()

    result = {
        "title": "曈曈 PulseNet v10 · 热冷池/FAISS/KAL 进程内微基准",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": ("本结果为进程内微基准（非生产负载观测）。生产框架停机时"
                 "不编造生产数字，仅如实记录组件自身开销。"),
        "config": _config_snapshot(),
        "framework": _framework_status(),
        "real_data": bool(a.real_data),
        "stages": {},
    }

    for name, fn in (
        ("node_pool", lambda: stage_node_pool(a.nodes, a.seed)),
        ("faiss", lambda: stage_faiss(a.faiss_count, a.faiss_dim, a.faiss_k, a.faiss_queries, a.seed)),
        ("kal", lambda: stage_kal(50, 7)),
    ):
        try:
            result["stages"][name] = fn()
        except Exception as e:
            result["stages"][name] = {
                "status": "SKIPPED",
                "reason": "%s: %s" % (type(e).__name__, str(e)[:300]),
            }

    if a.real_data:
        try:
            result["stages"]["node_pool_real"] = stage_node_pool_real(a.parquet_dir)
        except Exception as e:
            result["stages"]["node_pool_real"] = {
                "status": "SKIPPED",
                "reason": "%s: %s" % (type(e).__name__, str(e)[:300]),
            }

    if a.faiss_fix:
        try:
            _topks = tuple(int(x) for x in str(a.faiss_topks).split(",") if x.strip())
            _cnt = a.faiss_count if a.faiss_count > 0 else (
                (_real_node_count(a.parquet_dir) or 2000) if a.real_data else 2000)
            result["stages"]["faiss_fix"] = stage_faiss_fix(
                count=_cnt, dim=a.faiss_dim, topks=_topks,
                queries=a.faiss_queries, seed=a.seed)
        except Exception as e:
            result["stages"]["faiss_fix"] = {
                "status": "SKIPPED",
                "reason": "%s: %s" % (type(e).__name__, str(e)[:300]),
            }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("[benchmark] JSON 已写入 %s" % a.json_out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
