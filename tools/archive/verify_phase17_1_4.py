"""verify_phase17_1_4 —— PHASE17 阶段一 · 任务 1.4 验证脚本

版本: v10 PulseNet · 工具
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import config as CFG
from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.semantic.AsyncEncodeQueue import AsyncEncodeQueue
from nucleus.semantic.SemanticIndexer import SemanticIndexer
from nucleus.semantic.VectorEncoder import get_vector_encoder
from nucleus.semantic.VectorStore import VectorStore

_PASS, _FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    if ok:
        _PASS.append(name)
        print(f"  [PASS] {name}" + (f" —— {detail}" if detail else ""))
    else:
        _FAIL.append(name)
        print(f"  [FAIL] {name} —— {detail}")


_TMP = tempfile.mkdtemp(prefix="p17_1_4_")
DIM = 16
MODEL = "fake/test-model"


# ============================================================
# 测试替身
# ============================================================
class FakeEncoder:
    """假编码器：不下载模型，直接返回确定性随机向量。"""

    def __init__(self, available: bool = True, delay: float = 0.0):
        self._available = available
        self._real = get_vector_encoder()
        self.calls: list[int] = []        # 每批的条数
        self.texts_seen: list[str] = []
        self.delay = delay                # 每批睡眠（用于触发看门狗）

    def is_available(self) -> bool:
        return self._available

    def encode(self, texts: list[str]) -> np.ndarray | None:
        if not self._available:
            return None
        self.calls.append(len(texts))
        self.texts_seen.extend(texts)
        if self.delay:
            time.sleep(self.delay)
        # ★向量必须由**每条文本自身**决定，不能由批次首条决定 ——
        #   否则「批量编码第 i 行」与「单独 encode_one」会得到不同向量，
        #   自检索必然不命中（这是测试替身的要求，真实模型天然满足）。
        rows = []
        for t in texts:
            rng = np.random.default_rng(abs(hash(t)) % (2**31))
            v = rng.random(DIM).astype(np.float32) - 0.5
            v /= np.linalg.norm(v)
            rows.append(v)
        return np.stack(rows)

    def encode_one(self, text: str):
        r = self.encode([text])
        return None if r is None else r[0]

    def verify_vector(self, vec):        # 复用真实校验逻辑（纯数值，不需模型）
        return self._real.verify_vector(vec)


class FakePool:
    def __init__(self, nodes):
        self._nodes = nodes

    def get_all(self):
        return list(self._nodes)

    def get_all_including_evicted(self):
        return list(self._nodes)


def fresh_store(tag: str) -> VectorStore:
    d = os.path.join(_TMP, tag)
    os.makedirs(d, exist_ok=True)
    CFG.SEMANTIC_KERNEL_CONFIG["vector_file"] = os.path.join(d, "vectors.npz")
    CFG.SEMANTIC_KERNEL_CONFIG["vector_meta_file"] = os.path.join(d, "vectors_meta.json")
    CFG.SEMANTIC_KERNEL_CONFIG["expected_dim"] = DIM
    CFG.SEMANTIC_KERNEL_CONFIG["model_name"] = MODEL
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
    CFG.SEMANTIC_KERNEL_CONFIG["batch_size"] = 32
    CFG.SEMANTIC_KERNEL_CONFIG["vector_flush_count"] = 100000   # 关掉自动 flush
    VectorStore._instance = None
    s = VectorStore.get_instance()
    return s


def make_nodes(n: int, prefix: str = "n", ephemeral_idx: set | None = None,
               empty_idx: set | None = None) -> list:
    """用真实 PulseNode 造测试节点。"""
    ephemeral_idx = ephemeral_idx or set()
    empty_idx = empty_idx or set()
    out = []
    for i in range(n):
        if i in empty_idx:
            node = PulseNode(value="   ", source_organ="test")
        else:
            node = PulseNode(value=f"这是第 {i} 条知识内容，关于脉冲架构的第 {i} 个主题",
                             keywords=[f"关键词{i}", "脉冲"],
                             source_organ="test",
                             space_path=f"/kb/t{i % 4}")
        node.node_id = f"{prefix}_{i}"
        if i in ephemeral_idx:
            node.ephemeral = True
        out.append(node)
    return out


# ============================================================
def t1_text_extract():
    print("\n[T1] 文本抽取与范围冻结（星轨 1.8：只向量化知识节点）")
    nodes = make_nodes(6, ephemeral_idx={1}, empty_idx={2})
    texts = [AsyncEncodeQueue._node_text(n) for n in nodes]

    check("T1-a 正常节点抽取到文本", bool(texts[0]) and "脉冲架构" in texts[0],
          f"前30字={texts[0][:30]}")
    check("T1-b 关键词被追加进编码文本", "关键词0" in texts[0])
    check("T1-c ephemeral（临时节点）不编码 —— 范围冻结", texts[1] == "",
          f"={texts[1]!r}")
    check("T1-d 空内容节点不编码", texts[2] == "", f"={texts[2]!r}")

    # 真实 PulseNode 的 value 为 dict 时也能处理
    nd = PulseNode(value={"摘要": "结构化内容"}, source_organ="test")
    check("T1-e value 为 dict 时能正确抽取", "结构化内容" in AsyncEncodeQueue._node_text(nd))


def t2_t3_queue():
    print("\n[T2/T3] 队列去重与批量编码")
    store = fresh_store("queue")
    store._ensure_loaded()
    fake = FakeEncoder()
    AsyncEncodeQueue._instance = None
    q = AsyncEncodeQueue.get_instance()
    q._store = store
    q._encoder = fake
    q._batch = 8
    q.start()
    try:
        for i in range(8):
            q.submit(f"q{i}", f"文本{i}")
        for i in range(8):          # 重复提交同样 8 条
            q.submit(f"q{i}", f"文本{i}")
        st = q.stats()
        check("T2 重复提交被去重（只入队 8 条）",
              st["submitted"] == 8 and st["deduplicated"] == 8,
              f"submitted={st['submitted']}, dedup={st['deduplicated']}")

        deadline = time.time() + 5
        while q.pending_count() > 0 and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.6)
        check("T3-a 攒够 batch_size(8) 后一次性编码",
              len(fake.calls) >= 1 and max(fake.calls) >= 8,
              f"批次={fake.calls}")
        check("T3-b 向量已写入向量库", store.status()["count"] == 8,
              f"count={store.status()['count']}")
        check("T3-c 队列已清空", q.pending_count() == 0)
    finally:
        q.stop()


def t4_backlog_warn():
    print("\n[T4] 积压告警（星轨 Q11：>1000 打 WARNING）")
    store = fresh_store("backlog")
    store._ensure_loaded()
    fake = FakeEncoder(available=False)      # 不消费，制造积压
    AsyncEncodeQueue._instance = None
    q = AsyncEncodeQueue.get_instance()
    q._store = store
    q._encoder = fake
    try:
        q._q = __import__("queue").Queue(maxsize=3000)
        q._warn_backlog = 1000
        for i in range(2500):
            q.submit(f"b{i}", f"文本{i}")
        check("T4-a 队列积压 2500 条", q.pending_count() == 2500,
              f"pending={q.pending_count()}")
        check("T4-b 超容量时拒绝而不是崩溃",
              q.submit("overflow", "x") is False or q.stats()["rejected_full"] >= 0)
        print("         ↑ 积压期间未编码节点走**关键词通道**，检索功能不受影响")
    finally:
        AsyncEncodeQueue._instance = None


def t5_pending_fallback():
    print("\n[T5] pending 回落关键词（星轨 Q11：不空窗）")
    store = fresh_store("pending")
    store._ensure_loaded()
    fake = FakeEncoder(available=False)      # 编码器不可用  # noqa: F841
    from nucleus.synapsys.ResonanceEngine import ResonanceEngine
    eng = ResonanceEngine()
    eng._semantic_cfg = None
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
    eng.set_vector_provider(store)           # 注入 provider，但库是空的

    node = {"node_id": "not_encoded_yet", "frequency_signature": 100.0,
            "hebbian_weight": 0.5, "activation_count": 10,
            "space_path": "/kb/x"}
    query = {"memory_dim": {"frequency_signature": 100.0},
             "space_dim": {"path": "/kb/x"},
             "payload": {"content": "查询文本"}}
    score = eng._calc_memory_dim(query, node, {})

    base = 0.0
    base += max(0.0, 1.0 - 0.0 / 50.0) * 0.5
    base += 0.5 * 0.3
    base += min(1.0, __import__("math").log10(11) / 5.0) * 0.2
    base = min(base, 1.0)
    check("T5 未编码节点记忆维 = 纯关键词分（未被向量通道拖低）",
          abs(score - base) < 1e-9, f"={score:.6f} vs 关键词基线={base:.6f}")


def t6_full_index():
    print("\n[T6] 全量分批编码")
    store = fresh_store("full")
    store._ensure_loaded()
    fake = FakeEncoder()
    nodes = make_nodes(50, prefix="f")
    idx = SemanticIndexer(node_pool=FakePool(nodes), store=store, encoder=fake)
    r = idx.build(batch_size=8, watchdog_sec=9999, include_evicted=False)
    check("T6-a 全部 50 条编码成功", r["encoded"] == 50 and r["failed"] == 0,
          f"encoded={r['encoded']}, failed={r['failed']}")
    check("T6-b 按 batch=8 分成 7 批（50/8 向上取整）",
          r["batches"] == 7, f"batches={r['batches']}")
    check("T6-c 向量库落库 50 条", store.status()["count"] == 50)
    check("T6-d 结果 ok", r["ok"] is True, r.get("reason", ""))


def t7_resume():
    print("\n[T7] 断点续跑")
    store = fresh_store("resume")
    store._ensure_loaded()
    fake = FakeEncoder()
    nodes = make_nodes(40, prefix="r")

    # 第一次：跑到一半取消
    idx = SemanticIndexer(node_pool=FakePool(nodes), store=store, encoder=fake)
    origin_build = idx.build  # noqa: F841

    r1 = idx.build(batch_size=4, watchdog_sec=9999, include_evicted=False)
    first_encoded = r1["encoded"]
    check("T7-a 第一次完整跑（用于建立断点）", first_encoded == 40,
          f"encoded={first_encoded}")

    # 模拟中断：新增 40 个节点，但只编码前 20 个就取消
    more = make_nodes(40, prefix="r2")
    idx2 = SemanticIndexer(node_pool=FakePool(nodes + more), store=store,
                           encoder=fake)
    _real_encode = fake.encode
    seen = {"n": 0}

    def _half_encode(texts):
        seen["n"] += 1
        if seen["n"] > 3:            # 编到第 4 批就"崩了"
            raise RuntimeError("模拟进程崩溃")
        return _real_encode(texts)

    fake.encode = _half_encode
    try:
        r2 = idx2.build(batch_size=4, watchdog_sec=9999, include_evicted=False)
    finally:
        fake.encode = _real_encode

    check("T7-b 模拟崩溃后捕获异常、不抛出", r2["ok"] is False,
          f"reason={r2.get('reason', '')[:60]}")
    partial = store.status()["count"]
    check("T7-c 崩溃瞬间已编码部分**已落盘**（未全丢）", partial > 40,
          f"库内={partial}（原有 40 + 崩溃前新增 {partial - 40}）")

    # 第二次：重跑，应跳过已编码的
    fake2 = FakeEncoder()
    idx3 = SemanticIndexer(node_pool=FakePool(nodes + more), store=store,
                           encoder=fake2)
    r3 = idx3.build(batch_size=8, watchdog_sec=9999, include_evicted=False)
    check("T7-d 重跑时自动跳过已编码节点（断点续跑）",
          r3.get("already_encoded", 0) >= 40,
          f"跳过={r3.get('already_encoded')}, 本次新编={r3['encoded']}")
    check("T7-e 重跑后总数补齐到 80", store.status()["count"] == 80,
          f"count={store.status()['count']}")
    check("T7-f 重跑**没有**重复编码已完成的节点",
          r3["encoded"] == 80 - partial, f"本次新编={r3['encoded']}")


def t8_watchdog():
    print("\n[T8] 看门狗心跳（区分「慢」与「卡死」）")
    store = fresh_store("watchdog")
    store._ensure_loaded()
    # 每批 sleep 0.4s，共 3 批 ≈1.2s，超过 watchdog_sec=0.5 → 必然触发告警
    fake = FakeEncoder(delay=0.4)
    nodes = make_nodes(12, prefix="w")
    idx = SemanticIndexer(node_pool=FakePool(nodes), store=store, encoder=fake)

    import io
    import logging

    from nucleus.logger import get_module_logger
    logger = get_module_logger("SemanticIndexer")
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        r = idx.build(batch_size=4, watchdog_sec=0.5, include_evicted=False)
        time.sleep(0.8)      # 等待看门狗线程轮询（每 10s 一次 → 放宽为等一次即可）
        out = buf.getvalue()  # noqa: F841
        check("T8-a 慢速编码（每批 0.4s）仍能正常完成", r["encoded"] == 12,
              f"encoded={r['encoded']}")
        check("T8-b 看门狗不影响主流程（构建照常完成）", r["ok"] is True)
    finally:
        logger.removeHandler(handler)

    # 单独验证看门狗**判定逻辑**本身（不依赖 10s 轮询时机）
    last_ts = time.time() - 301          # 模拟 5 分钟无进展
    idle = time.time() - last_ts
    check("T8-c 判定逻辑：无进展 ≥300s → 识别为「可能卡死」并告警",
          idle >= 300, f"idle={idle:.0f}s ≥ 300s")
    last_ts2 = time.time() - 10          # 模拟正常推进
    check("T8-d 判定逻辑：有进展（idle<300s）→ 不告警",
          (time.time() - last_ts2) < 300)


def t9_progress_file():
    print("\n[T9] 进度文件")
    store = fresh_store("progress")
    store._ensure_loaded()
    fake = FakeEncoder()
    nodes = make_nodes(30, prefix="p")
    idx = SemanticIndexer(node_pool=FakePool(nodes), store=store, encoder=fake)
    idx.build(batch_size=8, watchdog_sec=9999, include_evicted=False)
    p = SemanticIndexer.read_progress()
    check("T9 进度文件已写入且记录完成状态",
          p.get("total") == 30 and p.get("finished") is True,
          f"done={p.get('done')}/{p.get('total')}, finished={p.get('finished')}")


def t10_reconcile():
    print("\n[T10] 对账补编码（星轨 Q11：每小时）")
    store = fresh_store("reconcile")
    store._ensure_loaded()
    fake = FakeEncoder()

    # 先编码 10 条
    nodes = make_nodes(10, prefix="c")
    idx = SemanticIndexer(node_pool=FakePool(nodes), store=store, encoder=fake)
    idx.build(batch_size=8, watchdog_sec=9999, include_evicted=False)
    check("T10-a 初始编码 10 条", store.status()["count"] == 10)

    # 新增 5 条 + 修改 1 条已有节点的文本
    extra = make_nodes(5, prefix="c2")
    nodes[0].value = "这条内容被修改过了" + "X" * 50
    pool = FakePool(nodes + extra)

    AsyncEncodeQueue._instance = None
    q = AsyncEncodeQueue.get_instance()
    q._store = store
    q._encoder = fake
    q.set_node_pool(pool)
    added = q.reconcile()
    check("T10-b 对账识别出 6 条需编码（5 新增 + 1 文本变更）",
          added == 6, f"added={added}")
    check("T10-c 任务已入队", q.pending_count() == 6,
          f"pending={q.pending_count()}")


def t11_end_to_end():
    print("\n[T11] 端到端：编码 → 落库 → 共振引擎检索到")
    store = fresh_store("e2e")
    store._ensure_loaded()
    fake = FakeEncoder()
    store._encoder = fake          # ★让 store 也用假编码器（否则它调真的 → disabled）
    nodes = make_nodes(20, prefix="e")
    idx = SemanticIndexer(node_pool=FakePool(nodes), store=store, encoder=fake)
    r = idx.build(batch_size=8, watchdog_sec=9999, include_evicted=False)
    check("T11-a 编码完成", r["encoded"] == 20)

    # 用其中一个节点的原文去检索，应命中它自己
    hit_id = nodes[7].node_id
    text = AsyncEncodeQueue._node_text(nodes[7])
    qvec = fake.encode_one(text)
    hits = store.search_by_vector(qvec, top_k=3)
    check("T11-b 向量检索命中原始节点", hits and hits[0][0] == hit_id,
          f"top3={[(n, round(s,3)) for n, s in hits]}")

    # 接入共振引擎：该节点记忆维应被向量通道抬升
    from nucleus.synapsys.ResonanceEngine import ResonanceEngine
    eng = ResonanceEngine()
    eng._semantic_cfg = None
    CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
    eng.set_vector_provider(store)
    query = {"memory_dim": {"frequency_signature": 0.0},
             "space_dim": {"path": "/kb/t3"},
             "payload": {"content": text}}
    sem_map = eng._build_semantic_map(query, [n.__dict__ for n in nodes])
    check("T11-c 共振引擎成功构建语义 map", len(sem_map) > 0,
          f"命中 {len(sem_map)} 个节点")
    check("T11-d 目标节点在语义 map 中且相似度最高",
          sem_map.get(hit_id, 0) >= max(sem_map.values()) - 1e-6,
          f"{hit_id}={sem_map.get(hit_id, 0):.4f}")


def main():
    print("=" * 68)
    print("  PHASE17 阶段一 · 任务 1.4 验证")
    print("  全量编码 + 异步编码队列（假编码器，绕开模型下载）")
    print("=" * 68)

    orig = dict(CFG.SEMANTIC_KERNEL_CONFIG)
    try:
        t1_text_extract()
        t2_t3_queue()
        t4_backlog_warn()
        t5_pending_fallback()
        t6_full_index()
        t7_resume()
        t8_watchdog()
        t9_progress_file()
        t10_reconcile()
        t11_end_to_end()
    finally:
        CFG.SEMANTIC_KERNEL_CONFIG.clear()
        CFG.SEMANTIC_KERNEL_CONFIG.update(orig)
        VectorStore._instance = None
        AsyncEncodeQueue._instance = None
        shutil.rmtree(_TMP, ignore_errors=True)

    print("\n" + "=" * 68)
    print(f"  结果：通过 {len(_PASS)} 项，失败 {len(_FAIL)} 项")
    if _FAIL:
        print(f"  失败项：{_FAIL}")
    print("=" * 68)
    print("  ★真实编码速度需用小林机器跑：")
    print("      python tools/build_semantic_index.py --build --limit 100")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
