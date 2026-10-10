# -*- coding: utf-8 -*-
"""
第102批 数据治理工具（D160 悬空引用 / D161 孤儿向量 / D165 语义关系冗余）

用法：
    python tools/m102_data_governance.py --scan [--out tmp/m102_scan.json]
    python tools/m102_data_governance.py --apply a      # 清理存量悬空边
    python tools/m102_data_governance.py --apply b      # 清理存量孤儿向量
    python tools/m102_data_governance.py --apply c      # linked_nodes 冗余投影移除（先合并零丢失）
    python tools/m102_data_governance.py --verify       # 治理后完整性校验

安全设计：
  1. 只读取证（--scan / --verify）默认不写任何文件；
  2. --apply 前必须已有 .bak_batch102/data_knowledge_full_* 全量备份（本工具会检查）；
  3. 每个目标文件先写 .tmp 再 os.replace 原子替换，崩溃不留半截文件；
  4. 开关关闭时（ENABLE_M102_* = False）本工具仍可用（它只做一次性存量治理），
     但**运行时增量防护**依赖那几个开关。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

from nucleus._silent_except import silent_exc

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_DIR = os.path.join(_PROJECT_ROOT, "data", "knowledge")
MAIN_SNAPSHOT = os.path.join(KNOWLEDGE_DIR, "pulse_knowledge_snapshot.json")
L1_SNAPSHOT = os.path.join(KNOWLEDGE_DIR, "pulse_l1_snapshot.json")
L1_SNAPSHOT_INDENT = 2          # 与 PulseSnapshot._save_l1_locked 一致
MAIN_SNAPSHOT_INDENT = None     # 与 _atomic_write_with_rotation(compact=True) 一致
MAIN_SNAPSHOT_SEP = None        # ★往返实测：磁盘=默认带空格分隔符（生产 json.dump 口径）。
                                #   load->dump 默认格式 = 522.40MB vs 原 522.39MB（差 +0.012MB，0.002%）；
                                #   若改用紧凑 (",", ":") 会变成 490.67MB（−31.7MB/−6.5%），
                                #   那是「改格式」不是「减数据」，会污染体积对照，故不改。
PARQUET_DIR = os.path.join(KNOWLEDGE_DIR, "parquet")
COLD_INDEX = os.path.join(KNOWLEDGE_DIR, "cold.index.json")
VECTORS_NPZ = os.path.join(KNOWLEDGE_DIR, "vectors.npz")
VECTORS_META = os.path.join(KNOWLEDGE_DIR, "vectors_meta.json")
BACKUP_ROOT = os.path.join(_PROJECT_ROOT, ".bak_batch102")

_M102_MERGE_SOURCE = "m102_merge"
_M102_MERGE_REL = "cooccurrence"


def _atomic_json(path, data, indent=None, separators=None):
    """原子写 JSON（ensure_ascii=False）。

    ★格式对齐（第102批实测）：磁盘上 pulse_knowledge_snapshot.json 是**紧凑无空格**格式
    （{"version":"v9.5",...}），而生产 _atomic_write_with_rotation 用的是默认带空格分隔符。
    为保证「治理前后体积差」只反映**数据减少**、不被序列化格式变化污染，
    重写主快照时显式传 separators=(",", ":") 复刻磁盘现有格式。
    """
    tmp = path + ".m102.tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        if separators is None:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        else:
            json.dump(data, f, ensure_ascii=False, indent=indent, separators=separators)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_parquet(path, rows, schema):
    import pyarrow as pa
    import pyarrow.parquet as pq
    cols = {}
    for field in schema:
        nm = field.name
        cols[nm] = pa.array([r.get(nm) for r in rows], type=field.type)
    new_t = pa.table(cols, schema=schema)
    tmp = path + ".m102.tmp"
    pq.write_table(new_t, tmp)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 数据加载
def load_all_nodes():
    main = json.load(io.open(MAIN_SNAPSHOT, encoding="utf-8", errors="replace"))
    l1 = json.load(io.open(L1_SNAPSHOT, encoding="utf-8", errors="replace"))
    return main, l1


def collect_node_ids(main, l1, parquet_ids=None, cold_ids=None):
    ids = set()
    for n in (main.get("nodes") or []):
        if isinstance(n, dict) and n.get("node_id"):
            ids.add(str(n["node_id"]))
    for n in (l1.get("nodes") or []):
        if isinstance(n, dict) and n.get("node_id"):
            ids.add(str(n["node_id"]))
    for x in (parquet_ids or ()):
        ids.add(str(x))
    for x in (cold_ids or ()):
        ids.add(str(x))
    return ids


def load_parquet_files():
    """返回 [(分区目录名, 文件名, 绝对路径)]。"""
    files = []
    if not os.path.isdir(PARQUET_DIR):
        return files
    for lv in sorted(os.listdir(PARQUET_DIR)):
        d = os.path.join(PARQUET_DIR, lv)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".parquet"):
                files.append((lv, f, os.path.join(d, f)))
    return files


def load_parquet_ids(files):
    import pyarrow.parquet as pq
    ids = set()
    for _lv, _f, fp in files:
        t = pq.read_table(fp)
        if "node_id" in t.column_names:
            for r in t.select(["node_id"]).to_pylist():
                if r.get("node_id"):
                    ids.add(str(r["node_id"]))
    return ids


def load_cold_ids():
    ids = set()
    cold_dir = os.path.join(KNOWLEDGE_DIR, "cold")
    if os.path.isdir(cold_dir):
        import pyarrow.parquet as pq
        for f in sorted(os.listdir(cold_dir)):
            if f.endswith(".parquet"):
                t = pq.read_table(os.path.join(cold_dir, f))
                if "node_id" in t.column_names:
                    for r in t.select(["node_id"]).to_pylist():
                        if r.get("node_id"):
                            ids.add(str(r["node_id"]))
    if os.path.isfile(COLD_INDEX):
        try:
            d = json.load(io.open(COLD_INDEX, encoding="utf-8", errors="replace"))
            if isinstance(d, dict):
                ids |= set(str(k) for k in d.keys())
        except Exception as e:
            silent_exc(e, "m102_data_governance:147:冷索引读取异常", level="warning")
    return ids


def _sem_pairs(sr):
    """返回 [(target_str, 原条目)]，兼容 list[dict] / list[str]。"""
    out = []
    for it in (sr or []):
        tg = it.get("target_node_id") if isinstance(it, dict) else it
        if tg is None:
            continue
        out.append((str(tg), it))
    return out


def _norm_sem(r):
    """parquet 行里 semantic_relations 可能是 JSON 字符串；返回 (list, 是否字符串存储)。"""
    sr = r.get("semantic_relations")
    if isinstance(sr, str):
        try:
            return (json.loads(sr) if sr else []), True
        except Exception:
            return [], True
    if isinstance(sr, list):
        return sr, False
    return [], False


def scan_edges(nodes, ids):
    st = sd = lt = ld = 0
    for n in nodes:
        if not isinstance(n, dict):
            continue
        for tg, _it in _sem_pairs(n.get("semantic_relations")):
            st += 1
            if tg not in ids:
                sd += 1
        for x in (n.get("linked_nodes") or []):
            if x is None:
                continue
            lt += 1
            if str(x) not in ids:
                ld += 1
    return st, sd, lt, ld


def parquet_row_edges(rows, ids):
    st = sd = lt = ld = 0
    for r in rows:
        sr, _ = _norm_sem(r)
        for tg, _it in _sem_pairs(sr):
            st += 1
            if tg not in ids:
                sd += 1
        for x in (r.get("linked_nodes") or []):
            if x is None:
                continue
            lt += 1
            if str(x) not in ids:
                ld += 1
    return st, sd, lt, ld


# ---------------------------------------------------------------- 扫描
def do_scan(out_path=None):
    t0 = time.time()
    main, l1 = load_all_nodes()
    pq_files = load_parquet_files()
    pq_ids = load_parquet_ids(pq_files)
    cold_ids = load_cold_ids()
    ids = collect_node_ids(main, l1, pq_ids, cold_ids)

    main_nodes = main.get("nodes") or []
    l1_nodes = l1.get("nodes") or []
    m = scan_edges(main_nodes, ids)
    l = scan_edges(l1_nodes, ids)

    import pyarrow.parquet as pq
    pq_stats = []
    for lv, f, fp in pq_files:
        t = pq.read_table(fp)
        pq_stats.append((lv, f, t.num_rows) + parquet_row_edges(t.to_pylist(), ids))

    meta = json.load(io.open(VECTORS_META, encoding="utf-8", errors="replace"))
    vids = [str(x) for x in (meta.get("node_ids") or [])]
    orphan = [x for x in vids if x not in ids]
    novec = [x for x in ids if x not in set(vids)]

    dangling = (m[1] + l[1] + m[3] + l[3]
                + sum(x[4] + x[6] for x in pq_stats))
    rep = {
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "node_id_universe": len(ids),
        "main_json": {
            "file": os.path.relpath(MAIN_SNAPSHOT, _PROJECT_ROOT),
            "bytes": os.path.getsize(MAIN_SNAPSHOT),
            "nodes": len(main_nodes),
            "sem_total": m[0], "sem_dangling": m[1],
            "linked_total": m[2], "linked_dangling": m[3],
        },
        "l1_json": {
            "file": os.path.relpath(L1_SNAPSHOT, _PROJECT_ROOT),
            "bytes": os.path.getsize(L1_SNAPSHOT),
            "nodes": len(l1_nodes),
            "sem_total": l[0], "sem_dangling": l[1],
            "linked_total": l[2], "linked_dangling": l[3],
        },
        "parquet": [
            {"level": lv, "file": f, "rows": rows,
             "sem_total": st, "sem_dangling": sd,
             "linked_total": lt, "linked_dangling": ld}
            for (lv, f, rows, st, sd, lt, ld) in pq_stats
        ],
        "vectors": {
            "npz_bytes": os.path.getsize(VECTORS_NPZ),
            "meta_bytes": os.path.getsize(VECTORS_META),
            "count": len(vids),
            "orphan": len(orphan),
            "orphan_pct": round(100.0 * len(orphan) / max(1, len(vids)), 2),
            "nodes_without_vector": len(novec),
        },
        "dangling_total": dangling,
        "elapsed_sec": round(time.time() - t0, 1),
    }

    inter = union = sem_only = ln_only = 0
    for n in main_nodes:
        if not isinstance(n, dict):
            continue
        stg = set(tg for tg, _ in _sem_pairs(n.get("semantic_relations")) if tg in ids)
        ltg = set(str(x) for x in (n.get("linked_nodes") or []) if x and str(x) in ids)
        inter += len(stg & ltg)
        union += len(stg | ltg)
        sem_only += len(stg - ltg)
        ln_only += len(ltg - stg)
    rep["redundancy"] = {
        "jaccard": round(inter / max(1, union), 4),
        "inter": inter, "union": union,
        "sem_only_non_dangling": sem_only,
        "linked_only_non_dangling": ln_only,
    }

    print("=" * 72)
    print("第102批 数据治理扫描（只读）  %s" % rep["scanned_at"])
    print("=" * 72)
    print("节点 ID 全集        : %d" % rep["node_id_universe"])
    print("主快照              : %s  %.2f MB / %d 节点" % (
        rep["main_json"]["file"], rep["main_json"]["bytes"] / 2 ** 20,
        rep["main_json"]["nodes"]))
    print("  sem    %d 条，悬空 %d" % (rep["main_json"]["sem_total"], rep["main_json"]["sem_dangling"]))
    print("  linked %d 条，悬空 %d" % (rep["main_json"]["linked_total"], rep["main_json"]["linked_dangling"]))
    print("L1 快照             : %s  %.2f MB / %d 节点" % (
        rep["l1_json"]["file"], rep["l1_json"]["bytes"] / 2 ** 20,
        rep["l1_json"]["nodes"]))
    print("  sem    %d 条，悬空 %d" % (rep["l1_json"]["sem_total"], rep["l1_json"]["sem_dangling"]))
    print("  linked %d 条，悬空 %d" % (rep["l1_json"]["linked_total"], rep["l1_json"]["linked_dangling"]))
    for p in rep["parquet"]:
        print("Parquet %-14s: %d 行  悬空 sem=%d linked=%d" % (
            p["level"], p["rows"], p["sem_dangling"], p["linked_dangling"]))
    print("向量                : %d 条，孤儿 %d (%.2f%%)，无向量节点 %d" % (
        rep["vectors"]["count"], rep["vectors"]["orphan"],
        rep["vectors"]["orphan_pct"], rep["vectors"]["nodes_without_vector"]))
    print("冗余度(Jaccard)     : %.4f  (sem独有 %d / linked独有 %d)" % (
        rep["redundancy"]["jaccard"], rep["redundancy"]["sem_only_non_dangling"],
        rep["redundancy"]["linked_only_non_dangling"]))
    print("-" * 72)
    print("悬空边合计          : %d" % rep["dangling_total"])
    print("耗时 %.1fs" % rep["elapsed_sec"])

    if out_path:
        d = os.path.dirname(out_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with io.open(out_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print("报告已写入: %s" % out_path)
    return rep


# ---------------------------------------------------------------- 治理
def _require_backup():
    if not os.path.isdir(BACKUP_ROOT):
        print("!! 未发现 .bak_batch102/ 备份目录，拒绝执行 --apply")
        return False
    subs = [x for x in os.listdir(BACKUP_ROOT) if x.startswith("data_knowledge_full")]
    if not subs:
        print("!! 未发现 data/knowledge 全量备份，拒绝执行 --apply")
        return False
    print("备份校验通过: %s" % subs[0])
    return True


def apply_a():
    """T-102a：清理存量悬空边（主 JSON / L1 JSON / parquet 分区）。"""
    if not _require_backup():
        return 1
    main, l1 = load_all_nodes()
    pq_files = load_parquet_files()
    pq_ids = load_parquet_ids(pq_files)
    cold_ids = load_cold_ids()
    ids = collect_node_ids(main, l1, pq_ids, cold_ids)

    before = os.path.getsize(MAIN_SNAPSHOT)
    total_sem = total_ln = 0

    for name, data, indent, path in (
            ("main", main, MAIN_SNAPSHOT_INDENT, MAIN_SNAPSHOT),
            ("l1", l1, L1_SNAPSHOT_INDENT, L1_SNAPSHOT)):
        srm = lrm = 0
        for n in (data.get("nodes") or []):
            if not isinstance(n, dict):
                continue
            sr = n.get("semantic_relations")
            if isinstance(sr, list) and sr:
                pairs = _sem_pairs(sr)
                srm += len([1 for tg, _ in pairs if tg not in ids])
                keep = [it for (tg, it) in pairs if tg in ids]
                if len(keep) != len(sr):
                    n["semantic_relations"] = keep
            ln = n.get("linked_nodes")
            if isinstance(ln, list) and ln:
                keep2 = [x for x in ln if x is not None and str(x) in ids]
                lrm += len(ln) - len(keep2)
                if len(keep2) != len(ln):
                    n["linked_nodes"] = keep2
        total_sem += srm
        total_ln += lrm
        _atomic_json(path, data, indent, MAIN_SNAPSHOT_SEP if name == "main" else None)
        print("  %-5s 清理 sem=%d linked=%d" % (name, srm, lrm))

    import pyarrow.parquet as pq
    for lv, f, fp in pq_files:
        t = pq.read_table(fp)
        rows = t.to_pylist()
        srm = lrm = 0
        for r in rows:
            sr, as_str = _norm_sem(r)
            if sr:
                pairs = _sem_pairs(sr)
                srm += len([1 for tg, _ in pairs if tg not in ids])
                keep = [it for (tg, it) in pairs if tg in ids]
                if len(keep) != len(sr):
                    r["semantic_relations"] = json.dumps(keep, ensure_ascii=False) if as_str else keep
            ln = r.get("linked_nodes")
            if isinstance(ln, list) and ln:
                keep2 = [x for x in ln if x is not None and str(x) in ids]
                lrm += len(ln) - len(keep2)
                if len(keep2) != len(ln):
                    r["linked_nodes"] = keep2
        total_sem += srm
        total_ln += lrm
        _atomic_parquet(fp, rows, t.schema)
        print("  %-5s 清理 sem=%d linked=%d" % (lv, srm, lrm))

    after = os.path.getsize(MAIN_SNAPSHOT)
    print("T-102a 完成: sem 悬空清理 %d 条 / linked 悬空清理 %d 条；"
          "主快照 %.2f MB -> %.2f MB (-%.2f MB)" % (
              total_sem, total_ln, before / 2 ** 20, after / 2 ** 20,
              (before - after) / 2 ** 20))
    return 0


def apply_b():
    """T-102b：清理存量孤儿向量（npz + meta 同步）。"""
    if not _require_backup():
        return 1
    import numpy as np

    main, l1 = load_all_nodes()
    pq_files = load_parquet_files()
    pq_ids = load_parquet_ids(pq_files)
    cold_ids = load_cold_ids()
    ids = collect_node_ids(main, l1, pq_ids, cold_ids)

    meta = json.load(io.open(VECTORS_META, encoding="utf-8", errors="replace"))
    vids = [str(x) for x in (meta.get("node_ids") or [])]
    keep_idx = [i for i, x in enumerate(vids) if x in ids]
    removed = len(vids) - len(keep_idx)
    if removed == 0:
        print("T-102b: 无孤儿向量，跳过")
        return 0

    # ★坑：np.load 返回的 NpzFile 是懒加载且**持有文件句柄**，不 close 的话
    #   Windows 上 os.replace 覆盖该文件会抛 [WinError 5] 拒绝访问（第102批实测）。
    data = np.load(VECTORS_NPZ)
    try:
        key = "matrix" if "matrix" in data.files else data.files[0]
        mat = np.ascontiguousarray(data[key])   # 拷贝进内存，脱离 npz 句柄
    finally:
        data.close()
    new_mat = mat[keep_idx]
    new_ids = [vids[i] for i in keep_idx]
    h_of = meta.get("text_hash") or {}
    u_of = meta.get("updated") or {}
    meta["node_ids"] = new_ids
    meta["count"] = len(new_ids)
    meta["text_hash"] = {k: v for k, v in h_of.items() if str(k) in ids}
    meta["updated"] = {k: v for k, v in u_of.items() if str(k) in ids}
    meta["saved_at"] = time.time()
    meta["m102_reaped"] = removed

    before_npz = os.path.getsize(VECTORS_NPZ)
    before_meta = os.path.getsize(VECTORS_META)
    tmp_npz = VECTORS_NPZ + ".m102.tmp.npz"
    try:
        np.savez(tmp_npz, **{key: new_mat})
        # 目标只读属性会导致 WinError 5，先解除
        try:
            if not os.access(VECTORS_NPZ, os.W_OK):
                os.chmod(VECTORS_NPZ, 0o666)
        except Exception as e:
            silent_exc(e, where="tools.m102_data_governance::apply_b L459")
        os.replace(tmp_npz, VECTORS_NPZ)
    except Exception:
        if os.path.exists(tmp_npz):
            try:
                os.remove(tmp_npz)
            except Exception as e:
                silent_exc(e, where="tools.m102_data_governance::apply_b L466")
        raise
    _atomic_json(VECTORS_META, meta)
    print("T-102b 完成: 孤儿向量移除 %d 条 (%d -> %d)；npz %.2f MB -> %.2f MB；"
          "meta %.2f MB -> %.2f MB" % (
              removed, len(vids), len(new_ids),
              before_npz / 2 ** 20, os.path.getsize(VECTORS_NPZ) / 2 ** 20,
              before_meta / 2 ** 20, os.path.getsize(VECTORS_META) / 2 ** 20))
    return 0


def apply_c():
    """T-102c：linked_nodes 冗余投影移除（先把 linked 独有边零丢失并入 sem）。"""
    if not _require_backup():
        return 1
    _now = time.time()
    main, l1 = load_all_nodes()
    pq_files = load_parquet_files()
    pq_ids = load_parquet_ids(pq_files)
    cold_ids = load_cold_ids()
    ids = collect_node_ids(main, l1, pq_ids, cold_ids)

    merged = emptied = 0

    def _merge_node(n):
        nonlocal merged, emptied
        if not isinstance(n, dict):
            return
        sr = n.get("semantic_relations")
        if not isinstance(sr, list):
            sr = []
        have = set(tg for tg, _ in _sem_pairs(sr))
        ln = n.get("linked_nodes") or []
        for x in ln:
            tg = str(x)
            if tg not in ids or tg in have:
                continue
            sr.append({
                "target_node_id": tg,
                "relation_type": _M102_MERGE_REL,
                "weight": 0.5,
                "source": _M102_MERGE_SOURCE,
                "established_at": _now,
                "updated_at": _now,
            })
            have.add(tg)
            merged += 1
        n["semantic_relations"] = [it for (tg, it) in _sem_pairs(sr) if tg in ids]
        emptied += len(ln)
        n["linked_nodes"] = []

    for n in (main.get("nodes") or []):
        _merge_node(n)
    for n in (l1.get("nodes") or []):
        _merge_node(n)

    before = os.path.getsize(MAIN_SNAPSHOT)
    _atomic_json(MAIN_SNAPSHOT, main, MAIN_SNAPSHOT_INDENT)
    _atomic_json(L1_SNAPSHOT, l1, L1_SNAPSHOT_INDENT)

    import pyarrow.parquet as pq
    for lv, f, fp in pq_files:
        t = pq.read_table(fp)
        rows = t.to_pylist()
        for r in rows:
            sr, as_str = _norm_sem(r)
            have = set(tg for tg, _ in _sem_pairs(sr))
            for x in (r.get("linked_nodes") or []):
                tg = str(x)
                if tg not in ids or tg in have:
                    continue
                sr.append({
                    "target_node_id": tg,
                    "relation_type": _M102_MERGE_REL,
                    "weight": 0.5,
                    "source": _M102_MERGE_SOURCE,
                    "established_at": _now,
                    "updated_at": _now,
                })
                have.add(tg)
                merged += 1
            sr = [it for (tg, it) in _sem_pairs(sr) if tg in ids]
            r["semantic_relations"] = json.dumps(sr, ensure_ascii=False) if as_str else sr
            emptied += len(r.get("linked_nodes") or [])
            r["linked_nodes"] = []
        _atomic_parquet(fp, rows, t.schema)
        print("  %-5s 分区已去冗余" % lv)

    after = os.path.getsize(MAIN_SNAPSHOT)
    print("T-102c 完成: linked 独有边并入 sem %d 条；linked_nodes 清空 %d 条；"
          "主快照 %.2f MB -> %.2f MB (-%.2f MB)" % (
              merged, emptied, before / 2 ** 20, after / 2 ** 20,
              (before - after) / 2 ** 20))
    return 0


def do_verify():
    rep = do_scan()
    dang = rep["dangling_total"]
    orph = rep["vectors"]["orphan"]
    print()
    print("=" * 72)
    print("完整性校验")
    print("=" * 72)
    ok = True
    print("悬空边            : %d   %s" % (dang, "OK" if dang == 0 else "REMAIN"))
    print("孤儿向量          : %d   %s" % (orph, "OK" if orph == 0 else "REMAIN"))
    if dang:
        ok = False
    if orph:
        ok = False
    print()
    print("VERIFY_RESULT=%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser(description="第102批 数据治理工具")
    ap.add_argument("--scan", action="store_true", help="只读取证")
    ap.add_argument("--apply", choices=["a", "b", "c"], help="执行治理")
    ap.add_argument("--verify", action="store_true", help="治理后完整性校验")
    ap.add_argument("--out", default="", help="--scan 报告落盘路径")
    args = ap.parse_args()

    if args.scan:
        do_scan(args.out or None)
        return 0
    if args.verify:
        return do_verify()
    if args.apply == "a":
        return apply_a()
    if args.apply == "b":
        return apply_b()
    if args.apply == "c":
        return apply_c()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
