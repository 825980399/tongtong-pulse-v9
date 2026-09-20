# -*- coding: utf-8 -*-
"""第71批 T5：节点全量导入工具 —— 将节点（含关联网络）同步到 Neo4j。

支持三种模式：
- full        全量导入（重置 checkpoint 后导入，受 --limit 限制）
- incremental 增量导入（只导入 checkpoint 中不存在的"新增"节点）
- resume      断点续传（读取已有 checkpoint，跳过已导入，继续未完成批次）

默认走 **mock 后端**（`--mock` 或 Neo4j 不可用时）：把"本应写入的内容"
记录到本地 JSON，便于在无真实数据库环境下验证导入逻辑、断点续传与去重正确性。
真实后端复用 ``Neo4jStore`` 的接口（``add_node`` / ``add_relationship``），
由 ``ENABLE_NEO4J_GRAPH_STORE`` + ``ENABLE_NEO4J_DUAL_WRITE`` 控制是否真正写库。

设计原则（与双写一致）：
- 失败安全：单个节点写入异常被捕获并计入失败统计，不影响整体进度。
- 可重入：同一节点重复导入幂等（checkpoint + MERGE）。
- 不写生产数据：mock 模式只落本地临时 JSON。

用法示例：
    python tools/import_nodes_to_neo4j.py --source sample_nodes.json --mode full --mock
    python tools/import_nodes_to_neo4j.py --source sample_nodes.json --mode resume --checkpoint .bak_batch71/import_ckpt.json
    python tools/import_nodes_to_neo4j.py --source pool --mode incremental --require-real
"""
import argparse
import json
import os
import sys
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_LOGGER = logging.getLogger("import_nodes_to_neo4j")
if not _LOGGER.handlers:
    _LOGGER.addHandler(logging.StreamHandler())
    _LOGGER.setLevel(logging.INFO)

REL_TYPE_DEFAULT = "RELATED"


# ===== 统计 =====
class ImportStats:
    """导入过程计数器。"""

    def __init__(self):
        self.total_in_source = 0
        self.processed = 0
        self.imported = 0
        self.skipped = 0
        self.relationships = 0
        self.failed = 0
        self.elapsed_ms = 0.0

    def as_dict(self):
        return {
            "total_in_source": self.total_in_source,
            "processed": self.processed,
            "imported": self.imported,
            "skipped": self.skipped,
            "relationships": self.relationships,
            "failed": self.failed,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


# ===== 写入后端（鸭子类型，复用 Neo4jStore 接口）=====
class MockWriter:
    """本地 mock 后端：把"会写入的内容"存进内存，用于无库环境验证。"""

    def __init__(self):
        self.nodes = {}
        self.rels = []
        self.flushed = 0

    def add_node(self, node_id, properties):
        self.nodes[node_id] = dict(properties)
        return True

    def add_relationship(self, from_id, to_id, rel_type, properties=None):
        self.rels.append((from_id, to_id, rel_type, dict(properties or {})))
        return True

    def flush(self):
        self.flushed += 1
        return True

    def get_stats(self):
        return {"nodes": len(self.nodes), "relationships": len(self.rels),
                "flushed": self.flushed}

    def reset(self):
        self.nodes = {}
        self.rels = []
        self.flushed = 0


class Neo4jWriter:
    """真实后端：包装 Neo4jStore。不可用时抛出清晰异常。"""

    def __init__(self):
        from nucleus.graph_store.neo4j_store import get_neo4j_store
        self._store = get_neo4j_store()
        if not self._store.is_available():
            raise RuntimeError(
                "Neo4j 不可用（驱动未安装或开关关闭）。"
                "请确认 ENABLE_NEO4J_GRAPH_STORE/ENABLE_NEO4J_DUAL_WRITE=True "
                "且已安装 neo4j 驱动，或改用 --mock。")
        self.flushed = 0

    def add_node(self, node_id, properties):
        return self._store.add_node(node_id, properties)

    def add_relationship(self, from_id, to_id, rel_type, properties=None):
        return self._store.add_relationship(from_id, to_id, rel_type, properties or {})

    def flush(self):
        self.flushed += 1
        return True

    def get_stats(self):
        return self._store.get_stats()


def build_writer(use_mock, require_real):
    """依据参数构建写入后端；mock 优先且安全降级。"""
    if require_real:
        return Neo4jWriter()
    if use_mock:
        return MockWriter()
    # 非 mock：尝试真实后端，失败则降级 mock（调用方已被告知）
    try:
        return Neo4jWriter()
    except RuntimeError as e:
        _LOGGER.warning("真实后端不可用，降级为 mock：%s", e)
        return MockWriter()


# ===== checkpoint =====
def load_checkpoint(path):
    """读取 checkpoint，返回已导入 node_id 集合与元信息。"""
    if not path or not os.path.isfile(path):
        return set(), {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("imported", [])), data.get("meta", {})
    except Exception as e:
        _LOGGER.warning("checkpoint 读取失败，视为空：%s", e)
        return set(), {}


def save_checkpoint(path, imported_ids, meta):
    """落盘 checkpoint。"""
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = {"imported": sorted(imported_ids), "meta": meta,
            "saved_at": time.time()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===== 节点来源 =====
def node_to_properties(node):
    """把节点 dict 转换为 Neo4j 节点属性（仅存查询需要的元数据）。"""
    props = {}
    for key in ("node_id", "evol_level", "space_path", "keywords"):
        if key in node and node[key] is not None:
            props[key] = node[key]
    value = node.get("value")
    if isinstance(value, str):
        # 大字段截断，避免图库膨胀（设计文档要求 value 仍留节点池）
        props["value_preview"] = value[:200]
    return props


def _linked_id(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("node_id") or item.get("id")
    return None


def collect_pool_nodes(limit=None):
    """从运行中的节点池收集节点（需 live 框架注入池）；无池时返回空。"""
    try:
        from nucleus.knowledge_access_layer import get_kal
        kal = get_kal()
        pool = getattr(kal, "_node_pool", None)
        if pool is None:
            return []
        out = []
        if hasattr(pool, "get_all_nodes"):
            for n in pool.get_all_nodes():
                out.append(n.to_dict() if hasattr(n, "to_dict") else dict(n))
                if limit and len(out) >= limit:
                    break
        return out
    except Exception as e:
        _LOGGER.warning("从节点池收集失败：%s", e)
        return []


def collect_nodes(source, limit=None, node_provider=None):
    """按来源收集节点列表。

    - source 为 .json 路径：从文件加载节点数组
    - source == "pool"：从 live 节点池或注入的 node_provider 收集
    - 其它：视为 JSON 字符串直接解析
    """
    if node_provider is not None:
        nodes = list(node_provider())
    elif isinstance(source, (list, tuple)):
        nodes = list(source)
    elif isinstance(source, str) and source.endswith(".json") and os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as f:
            nodes = json.load(f)
        if isinstance(nodes, dict) and "nodes" in nodes:
            nodes = nodes["nodes"]
    elif source == "pool":
        nodes = collect_pool_nodes(limit=limit)
    else:
        nodes = json.loads(source)
    if limit is not None:
        nodes = nodes[:limit]
    return nodes


# ===== 核心导入 =====
def import_nodes(nodes, writer, checkpoint_path=None, mode="full",
                 dry_run=False, logger=None):
    """执行导入，返回 ImportStats。幂等、可重入、失败安全。

    mode:
      full         : 重置 checkpoint，导入全部（受 limit）
      incremental  : 跳过 checkpoint 已有节点（仅新增）
      resume       : 跳过 checkpoint 已有节点（继续未完成批次）
    """
    log = logger or _LOGGER
    stats = ImportStats()
    stats.total_in_source = len(nodes)

    imported_ids, meta = set(), {}
    if mode in ("incremental", "resume") and checkpoint_path:
        imported_ids, meta = load_checkpoint(checkpoint_path)
    elif mode == "full":
        # full 重置 checkpoint
        imported_ids = set()
        if checkpoint_path and os.path.isfile(checkpoint_path):
            try:
                os.remove(checkpoint_path)
            except Exception:
                pass

    for node in nodes:
        node_id = node.get("node_id") or node.get("id")
        if not node_id:
            stats.failed += 1
            continue
        stats.processed += 1
        if node_id in imported_ids:
            stats.skipped += 1
            continue
        if dry_run:
            stats.imported += 1
            continue
        try:
            writer.add_node(node_id, node_to_properties(node))
            linked = node.get("linked_nodes") or []
            for item in linked:
                tid = _linked_id(item)
                if not tid or tid == node_id:
                    continue
                writer.add_relationship(node_id, tid, REL_TYPE_DEFAULT)
                stats.relationships += 1
            imported_ids.add(node_id)
            stats.imported += 1
        except Exception as e:
            stats.failed += 1
            log.warning("节点 %s 导入失败：%s", node_id, e)

    if not dry_run:
        try:
            writer.flush()
        except Exception:
            pass
        if checkpoint_path:
            save_checkpoint(checkpoint_path, imported_ids, meta)

    return stats


# ===== CLI =====
def main(argv=None):
    parser = argparse.ArgumentParser(description="节点全量导入工具 (Neo4j)")
    parser.add_argument("--source", default="pool",
                        help="节点来源：pool / .json 文件路径 / JSON 字符串")
    parser.add_argument("--mode", default="full",
                        choices=["full", "incremental", "resume"])
    parser.add_argument("--checkpoint", default=None,
                        help="断点续传 checkpoint 文件路径")
    parser.add_argument("--limit", type=int, default=None, help="最大导入节点数")
    parser.add_argument("--mock", action="store_true", default=True,
                        help="使用 mock 后端（默认开）")
    parser.add_argument("--no-mock", dest="mock", action="store_false",
                        help="尝试真实 Neo4j 后端")
    parser.add_argument("--require-real", action="store_true",
                        help="强制真实后端，不可用时直接报错退出")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    parser.add_argument("--json-out", default=None, help="结果 JSON 输出路径")
    parser.add_argument("--report", action="store_true", default=True,
                        help="打印汇总报告")
    args = parser.parse_args(argv)

    t0 = time.time()
    try:
        writer = build_writer(args.mock, args.require_real)
    except RuntimeError as e:
        _LOGGER.error(str(e))
        return 2

    try:
        nodes = collect_nodes(args.source, limit=args.limit)
    except Exception as e:
        _LOGGER.error("节点收集失败：%s", e)
        return 2

    if not nodes:
        _LOGGER.warning("来源为空，无节点可导入（live 池未注入时请用 --source 指定 JSON 文件）。")
        return 0

    stats = import_nodes(nodes, writer, checkpoint_path=args.checkpoint,
                         mode=args.mode, dry_run=args.dry_run)
    stats.elapsed_ms = (time.time() - t0) * 1000.0

    result = stats.as_dict()
    result["mode"] = args.mode
    result["backend"] = "mock" if isinstance(writer, MockWriter) else "neo4j"
    result["source"] = args.source if isinstance(args.source, str) else "<in-memory>"

    if args.report:
        _LOGGER.info("导入完成：%s", json.dumps(result, ensure_ascii=False))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        _LOGGER.info("结果已写入 %s", args.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
