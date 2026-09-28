#!/usr/bin/env python
"""build_semantic_index —— 全量语义索引构建工具（PHASE17 阶段一 · 任务 1.4）

版本: v10 PulseNet · 工具
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_nodes(include_l1: bool = True):
    """加载逻辑全量节点：parquet/JSON 全量 + L1 独立快照（按 node_id 去重）。"""
    from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot

    snap = PulseSnapshot()
    nodes = []
    try:
        nodes = snap.load() or []
        print(f"  · 主快照加载 {len(nodes)} 个节点")
    except Exception as _e:
        print(f"  ! 主快照加载失败: {type(_e).__name__}: {_e}")

    if include_l1:
        try:
            l1 = snap.load_l1() or []
            if l1:
                seen = {getattr(n, "node_id", "") for n in nodes}
                added = [n for n in l1
                         if getattr(n, "node_id", "") not in seen]
                print(f"  · L1 独立快照 {len(l1)} 个，去重后新增 {len(added)} 个")
                nodes = nodes + added
        except Exception as _e:
            print(f"  ! L1 快照加载失败: {type(_e).__name__}: {_e}")

    return nodes


def _build_pool(nodes):
    """把节点装进一个轻量池对象，供 SemanticIndexer 使用。"""
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    pool = PulseNodePool()
    try:
        pool.load_batch(nodes)
    except Exception as _e:
        print(f"  ! 节点装载失败（改用直连模式）: {type(_e).__name__}: {_e}")
    return pool


def cmd_dry_run(args) -> int:
    print("=" * 66)
    print("  规模预估（不编码、不下载模型）")
    print("=" * 66)
    import config as CFG
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True

    nodes = _load_nodes(include_l1=not args.no_l1)
    print(f"\n  逻辑全量节点数：{len(nodes)}")

    from nucleus.semantic.AsyncEncodeQueue import AsyncEncodeQueue
    from nucleus.semantic.SemanticIndexer import SemanticIndexer
    from nucleus.semantic.VectorStore import VectorStore

    VectorStore._instance = None
    store = VectorStore.get_instance()
    store._ensure_loaded()
    idx = SemanticIndexer(node_pool=_build_pool(nodes), store=store)  # noqa: F841

    to_encode, stat = [], {}  # noqa: F841
    for nd in nodes:
        nid = getattr(nd, "node_id", "") or ""
        if not nid:
            continue
        if getattr(nd, "ephemeral", False):
            continue
        text = AsyncEncodeQueue._node_text(nd)
        if not text:
            continue
        if store.needs_encode(nid, text):
            to_encode.append((nid, text))

    chars = sum(len(t) for _n, t in to_encode)
    print(f"  已有向量：{store.status()['count']}")
    print(f"  待编码　：{len(to_encode)}")
    print(f"  文本总量：{chars:,} 字符（约 {chars/1024/1024:.1f} MB）")

    speed = args.speed          # 条/秒，默认按保守值估算
    if to_encode:
        est = len(to_encode) / speed
        print(f"\n  按 {speed} 条/秒估算：约 {est/60:.1f} 分钟 "
              f"（{est/3600:.1f} 小时）")
        print("  ★ 实际速度请用 --build --limit 100 实测后换算，")
        print("    首次运行还需加上约 90MB 模型下载时间。")
    print("=" * 66)
    return 0


def cmd_build(args) -> int:
    print("=" * 66)
    print("  全量语义索引构建")
    print("=" * 66)
    import config as CFG
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
    if CFG.SEMANTIC_KERNEL_CONFIG.get("batch_size") != args.batch:
        CFG.SEMANTIC_KERNEL_CONFIG["batch_size"] = args.batch

    from nucleus.semantic.SemanticIndexer import SemanticIndexer
    from nucleus.semantic.VectorEncoder import get_vector_encoder
    from nucleus.semantic.VectorStore import VectorStore

    VectorStore._instance = None
    store = VectorStore.get_instance()
    store._ensure_loaded()
    print(f"  向量库现有：{store.status()['count']} 条")

    enc = get_vector_encoder()
    # 等待编码器后台加载完成（模型已缓存到本地，通常几秒内就绪；首次下载最多等120秒）
    _wait_start = time.time()
    while not enc.is_available() and time.time() - _wait_start < 120:
        _st = enc.status().get("state", "?")
        print(f"  等待编码器就绪…（当前状态：{_st}，已等 {int(time.time()-_wait_start)}s）", end="\r")
        time.sleep(1)
    print()
    if not enc.is_available():
        print("\n  ⚠ 编码器不可用 —— 通常是模型未下载或网络不通。")
        print(f"     状态：{enc.status().get('state')} | "
              f"{enc.status().get('reason', '')[:120]}")
        print("     框架照常运行，检索走关键词通道。")
        print("     解决后重跑本命令即可。")
        return 2
    print(f"  ✅ 编码器就绪：{enc.status().get('reason', '')[:80]}")

    nodes = _load_nodes(include_l1=not args.no_l1)
    if args.limit and args.limit > 0:
        # 只取前 N 个**待编码**的，避免全被已编码的占满额度
        kept, cnt = [], 0
        from nucleus.semantic.AsyncEncodeQueue import AsyncEncodeQueue
        for nd in nodes:
            if cnt >= args.limit:
                break
            nid = getattr(nd, "node_id", "") or ""
            text = AsyncEncodeQueue._node_text(nd)
            if nid and text and store.needs_encode(nid, text):
                kept.append(nd)
                cnt += 1
        print(f"  --limit {args.limit}：本次只编码 {len(kept)} 个待编码节点")
        nodes = kept

    idx = SemanticIndexer(node_pool=_build_pool(nodes), store=store,
                          encoder=enc)

    print(f"\n  开始编码（batch={args.batch}, 看门狗={args.watchdog}s）…")
    print("  中断随时可以 Ctrl+C，已编码部分会落盘，重跑自动续跑。\n")

    t0 = time.time()
    r = idx.build(batch_size=args.batch, watchdog_sec=args.watchdog,
                  include_evicted=False)   # 节点已由本脚本全量装载
    el = time.time() - t0

    print("\n" + "=" * 66)
    print("  构建结果")
    print("=" * 66)
    for k in ("ok", "encoded", "failed", "batches", "cancelled",
              "total_nodes", "already_encoded", "skipped_ephemeral",
              "skipped_empty", "reason"):
        if k in r:
            print(f"    {k:20s} = {r[k]}")
    print(f"    {'elapsed_sec':20s} = {el:.1f}")
    if r.get("encoded"):
        print(f"    实际速度              = {r['encoded']/el:.1f} 条/秒")
        remain = max(0, r.get("to_encode", 0) - r["encoded"])
        if remain:
            print(f"    剩余 {remain} 条，预计还需 {remain/(r['encoded']/el)/60:.1f} 分钟")
    st = store.status()
    print(f"    向量库现有            = {st['count']} 条 "
          f"（{st['matrix_bytes']/1024/1024:.1f} MB）")
    print("=" * 66)
    return 0 if r.get("ok") else 1


def cmd_status(args) -> int:
    print("=" * 66)
    print("  语义内核状态")
    print("=" * 66)
    import config as CFG
    cfg = CFG.SEMANTIC_KERNEL_CONFIG
    print(f"  开关 enable_semantic_kernel = {cfg.get('enable_semantic_kernel')}")
    print(f"  模型 model_name             = {cfg.get('model_name')}")
    print(f"  维度 expected_dim           = {cfg.get('expected_dim')}")
    print(f"  α'/β'                       = {cfg.get('memory_keyword_ratio')} / "
          f"{cfg.get('memory_vector_ratio')}")

    try:
        from nucleus.semantic.VectorEncoder import get_vector_encoder
        s = get_vector_encoder().status()
        print(f"\n  编码器状态 = {s['state']}")
        print(f"    {s['reason'][:150]}")
    except Exception as _e:
        print(f"\n  编码器状态读取失败: {_e}")

    try:
        from nucleus.semantic.VectorStore import VectorStore
        VectorStore._instance = None
        st = VectorStore.get_instance().status()
        print(f"\n  向量库：{st['count']} 条（槽位 {st['slots']}，"
              f"待压缩 {st['pending_compact']}）")
        print(f"    文件：{st['vec_file']}")
        print(f"    存在：{st['vec_file_exists']} | "
              f"体积：{st['matrix_bytes']/1024/1024:.1f} MB")
        if st["load_error"]:
            print(f"    加载错误：{st['load_error']}")
    except Exception as _e:
        print(f"\n  向量库状态读取失败: {_e}")

    try:
        from nucleus.semantic.SemanticIndexer import SemanticIndexer
        p = SemanticIndexer.read_progress()
        if p:
            print(f"\n  上次进度：{p.get('done')}/{p.get('total')} "
                  f"（{'已完成' if p.get('finished') else '未完成'}）"
                  f" 速度 {p.get('speed', 0):.1f} 条/秒")
    except Exception:
        pass
    print("=" * 66)
    return 0


def cmd_reconcile(args) -> int:
    print("=" * 66)
    print("  对账补编码")
    print("=" * 66)
    import config as CFG
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
    from nucleus.semantic.AsyncEncodeQueue import AsyncEncodeQueue
    from nucleus.semantic.VectorStore import VectorStore

    VectorStore._instance = None
    store = VectorStore.get_instance()
    store._ensure_loaded()

    # 直接同步补齐（不启后台线程，脚本跑完即结束）
    nodes = _load_nodes(include_l1=not args.no_l1)
    q = AsyncEncodeQueue.get_instance()
    added = 0
    for nd in nodes:
        nid = getattr(nd, "node_id", "") or ""
        text = AsyncEncodeQueue._node_text(nd)
        if nid and text and store.needs_encode(nid, text):
            q.submit(nid, text)
            added += 1
    print(f"  待补编码：{added} 条")

    if added == 0:
        print("  无需补编码。")
        return 0

    enc = q._ensure_deps()[1]
    if not enc.is_available():
        print("  ⚠ 编码器不可用，无法补编码（节点继续走关键词通道）")
        return 2

    q.start()
    print("  后台编码中…（Ctrl+C 可中断）")
    try:
        while q.pending_count() > 0:
            time.sleep(1.0)
            pend = q.pending_count()
            s = q.stats()
            print(f"\r    剩余 {pend:6d} | 已编码 {s['encoded']:6d} | "
                  f"失败 {s['failed']:4d}   ", end="", flush=True)
    except KeyboardInterrupt:
        print("\n  已中断，正在保存…")
    finally:
        q.stop()
    print(f"\n  完成。向量库现有 {store.status()['count']} 条")
    return 0


def cmd_reset(args) -> int:
    from nucleus.semantic.VectorStore import VectorStore
    VectorStore._instance = None
    st = VectorStore.get_instance().status()
    if not args.yes:
        print(f"  将删除：{st['vec_file']} 与 {st['meta_file']}")
        print(f"  当前 {st['count']} 条向量将被清空，重跑 --build 可重建。")
        print("  确认请加 --yes")
        return 1
    removed = 0
    for p in (st["vec_file"], st["meta_file"]):
        try:
            if os.path.exists(p):
                os.remove(p)
                removed += 1
        except Exception as _e:
            print(f"  删除失败 {p}: {_e}")
    print(f"  已删除 {removed} 个文件。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="PHASE17 全量语义索引构建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="只统计规模，不编码")
    ap.add_argument("--build", action="store_true", help="执行全量编码")
    ap.add_argument("--status", action="store_true", help="查看状态")
    ap.add_argument("--reconcile", action="store_true", help="对账补编码")
    ap.add_argument("--reset", action="store_true", help="清空向量库")
    ap.add_argument("--yes", action="store_true", help="危险操作确认")
    ap.add_argument("--batch", type=int, default=32, help="批大小（默认32）")
    ap.add_argument("--limit", type=int, default=0, help="本次最多编码 N 条")
    ap.add_argument("--watchdog", type=float, default=300.0,
                    help="看门狗心跳秒数（默认300）")
    ap.add_argument("--speed", type=float, default=30.0,
                    help="dry-run 估算用的速度（条/秒，默认30）")
    ap.add_argument("--no-l1", action="store_true", help="不含 L1 冷存节点")
    args = ap.parse_args()

    if args.reset:
        return cmd_reset(args)
    if args.status:
        return cmd_status(args)
    if args.dry_run:
        return cmd_dry_run(args)
    if args.reconcile:
        return cmd_reconcile(args)
    if args.build:
        return cmd_build(args)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
