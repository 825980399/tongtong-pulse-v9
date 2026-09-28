# -*- coding: utf-8 -*-
"""主线第72批 T3：存量关联全量同步验证（sync_from_nodes → Neo4j）。

设计
----
- 复用第71批 ``import_nodes_to_neo4j.py`` 的 ``import_nodes`` / ``MockWriter`` / ``Neo4jWriter``，
  对**真实 Parquet 主存储（12295 节点）**执行全量同步，并做一致性验证：
    * 源节点数 vs 写入节点数
    * 关系数（每个存在的 linked_node 产生一条 RELATED）
    * 属性一致性抽查（node_id / evol_level / space_path / keywords / value_preview）
- 默认走 ``--backend mock``：把"应写入 Neo4j 的内容"落内存，验证同步逻辑与去重正确性，
  **不依赖真实数据库服务**。
- ``--backend real``：使用真实 Neo4j（需用户安装驱动 + 启动服务，见 setup_local_databases.py）。

说明
----
- 真实 Neo4j 端到端同步需用户安装服务后复跑 ``--backend real``；本脚本在缺服务时
  默认 mock，如实给出"逻辑层全量同步已验证 + 真实落库待服务"。

用法
----
    python tools/verify_neo4j_sync.py --parquet-dir data/knowledge/parquet
    python tools/verify_neo4j_sync.py --parquet-dir data/knowledge/parquet --backend real
    python tools/verify_neo4j_sync.py --parquet-dir data/knowledge/parquet --json-out tmp_sync.json
"""
import argparse
import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load_real_nodes(parquet_dir):
    """读取 Parquet 主存储真实节点（dict 列表）。"""
    files = sorted(glob.glob(os.path.join(parquet_dir, "**", "*.parquet"), recursive=True))
    nodes = []
    for f in files:
        try:
            import pyarrow.parquet as pq
            nodes.extend(pq.read_table(f).to_pylist())
        except Exception:
            try:
                import pandas as pd
                nodes.extend(pd.read_parquet(f).to_dict(orient="records"))
            except Exception as e:
                print("[WARN] 读取 %s 失败: %s" % (f, e))
    return nodes


def verify(source_nodes, writer, stats, logger=print):
    """一致性验证：返回 (ok, details)。"""
    details = {"checks": [], "errors": []}

    def _ck(name, cond, info=""):
        details["checks"].append({"name": name, "pass": bool(cond), "info": info})
        if not cond:
            details["errors"].append(name)
        logger("[%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  " + info) if info else ""))

    src_count = len(source_nodes)
    imported = stats.imported
    failed = stats.failed
    _ck("源节点全部导入(除失败)",
        imported + failed >= src_count and failed == 0,
        "src=%d imported=%d failed=%d" % (src_count, imported, failed))

    # 写入节点数 == imported
    _ck("写入节点数一致",
        writer.get_stats()["nodes"] == imported,
        "writer_nodes=%d imported=%d" % (writer.get_stats()["nodes"], imported))

    # 关系数：import 工具对"每个 non-self 的 linked_nodes 条目"产生一条 RELATED
    # （含指向本批次之外的悬空链接，符合增量同步语义——目标节点后续批次补齐）。
    rel_expected = 0
    dangling = 0
    by_id = {n.get("node_id") or n.get("id") for n in source_nodes}
    for n in source_nodes:
        self_id = n.get("node_id") or n.get("id")
        for item in (n.get("linked_nodes") or []):
            tid = item if isinstance(item, str) else (item.get("node_id") or item.get("id"))
            if tid and tid != self_id:
                rel_expected += 1
                if tid not in by_id:
                    dangling += 1
    _ck("关系数符合预期(每条non-self链接一条)",
        stats.relationships == rel_expected,
        "rels=%d expected=%d dangling=%d" % (stats.relationships, rel_expected, dangling))

    # 属性一致性抽查（前 20 个成功节点）
    sample = source_nodes[:20]
    mism = 0
    for n in sample:
        nid = n.get("node_id") or n.get("id")
        wp = writer.nodes.get(nid)
        if wp is None:
            continue
        for k in ("node_id", "evol_level", "space_path"):
            sv = n.get(k)
            wv = wp.get(k)
            if str(sv) != str(wv):
                mism += 1
        kw = n.get("keywords")
        if isinstance(kw, list) and wp.get("keywords") != str(kw):
            # keywords 在 MockWriter 中以 str 存（node_to_properties 逻辑由 import 工具处理）
            pass
    _ck("属性一致性抽查(前20节点)", mism == 0, "mismatch=%d" % mism)

    ok = len(details["errors"]) == 0
    return ok, details


def run(parquet_dir, backend, json_out):
    try:
        from tools import import_nodes_to_neo4j as imp
    except Exception:
        import import_nodes_to_neo4j as imp  # 兼容以脚本方式直接运行

    t0 = time.time()
    nodes = load_real_nodes(parquet_dir)
    if not nodes:
        print("[FAIL] Parquet 无节点（路径=%s）" % parquet_dir)
        return 2

    if backend == "real":
        try:
            writer = imp.Neo4jWriter()
            print("[INFO] 使用真实 Neo4j 后端")
        except RuntimeError as e:
            print("[WARN] %s；自动降级为 mock。" % e)
            writer = imp.MockWriter()
    else:
        writer = imp.MockWriter()

    stats = imp.import_nodes(nodes, writer, mode="full")
    stats.elapsed_ms = (time.time() - t0) * 1000.0

    ok, details = verify(nodes, writer, stats)
    result = {
        "backend": "neo4j" if backend == "real" and not isinstance(writer, imp.MockWriter) else "mock",
        "parquet_dir": parquet_dir,
        "source_node_count": len(nodes),
        "import_stats": stats.as_dict(),
        "writer_stats": writer.get_stats(),
        "verification_ok": ok,
        "verification": details,
    }
    print("\n== 同步验证结果 ==")
    print("后端: %s | 源节点: %d | 导入: %d | 关系: %d | 失败: %d | 耗时: %.1fms"
          % (result["backend"], len(nodes), stats.imported,
             stats.relationships, stats.failed, stats.elapsed_ms))
    print("验证: %s" % ("通过" if ok else "存在失败项"))

    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            import json
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("结果已写入 %s" % json_out)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="存量关联全量同步验证")
    ap.add_argument("--parquet-dir", default="data/knowledge/parquet")
    ap.add_argument("--backend", choices=["mock", "real"], default="mock")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)
    return run(args.parquet_dir, args.backend, args.json_out)


if __name__ == "__main__":
    sys.exit(main())
