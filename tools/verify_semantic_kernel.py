#!/usr/bin/env python
"""verify_semantic_kernel —— 语义内核黄金评测集（PHASE17 阶段一 · 任务 1.7）

版本: v10 PulseNet · 工具
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

# ============================================================
# 黄金评测集（自包含语料库）
# ============================================================
# 每条语料: (id, 类别, 文本)
CORPUS: list[tuple[str, str, str]] = [
    # ---------- 正例目标（15 条，P 开头）----------
    ("P01", "pos", "五维共振检索通过频率签名、知识树路径、意图匹配、新鲜度与状态加权计算总分"),
    ("P02", "pos", "所有器官通过脉冲在信息场中广播与接收，彼此不直接互相调用"),
    ("P03", "pos", "知识分为L1感知、L2认知、L3智慧、L4本能四个演化层级"),
    ("P04", "pos", "长期未激活的节点会被淘汰到冷存，需要时可以重新召回"),
    ("P05", "pos", "L3层知识永久锁定并附带SHA256校验，任何修改都会被拒绝"),
    ("P06", "pos", "五维权重由宪法规则1.3永久固定，修改属于修宪行为"),
    ("P07", "pos", "赫布学习让共同激活的节点之间连接增强，形成关联记忆"),
    ("P08", "pos", "余弦相似度衡量两个向量的方向一致性，归一化后等价于点积"),
    ("P09", "pos", "自主进化闭环由补丁管理器与安全执行器组成，高风险改动必须审批"),
    ("P10", "pos", "信息场是全局广播媒介，器官通过它发布脉冲从而实现解耦"),
    ("P11", "pos", "感官器官采集外部信号后编码为脉冲送入信息场"),
    ("P12", "pos", "L4本能是无需检索即可直接触发的固化反应模式"),
    ("P13", "pos", "频率签名把内容映射为数值特征，使检索复杂度降到常数级"),
    ("P14", "pos", "节点长期无激活会被降权并可能进入冷存，形成类遗忘机制"),
    ("P15", "pos", "十四条永久稳态宪法规则约束所有改动，核心规则禁止修改"),

    # ---------- 负例干扰（10 条，N 开头）----------
    ("N01", "neg", "如何彻底删除全部记忆数据并且不可恢复"),
    ("N02", "neg", "关闭共振引擎改用随机返回结果"),
    ("N03", "neg", "把五维权重的数值全部改成相同的平均值"),
    ("N04", "neg", "让器官之间直接调用彼此的内部私有函数"),
    ("N05", "neg", "取消L3锁定以便随时覆盖修改智慧结论"),
    ("N06", "neg", "禁用冷存以节省磁盘空间直接丢弃数据"),
    ("N07", "neg", "把信息场改造成点对点直连的私有通道"),
    ("N08", "neg", "用逐字文本匹配替代频率签名进行检索"),
    ("N09", "neg", "跳过审批流程直接改写核心源码文件"),
    ("N10", "neg", "降低所有节点的重要性等级到最低"),

    # ---------- 专有名词（10 条，T 开头）----------
    ("T01", "term", "PulseNodePool 负责热池温池与冷缓存的节点调度"),
    ("T02", "term", "_cosine_cpu_cy 是批量余弦相似度的 Cython 加速实现"),
    ("T03", "term", "bge-small-zh-v1.5 是 512 维的中文句向量模型"),
    ("T04", "term", "PHASE17 是本地语义内核与自主成长闭环的规划阶段"),
    ("T05", "term", "InfoField 承载脉冲的发布与订阅"),
    ("T06", "term", "SafeEvolutionExecutor 在沙箱中执行补丁并支持自动回滚"),
    ("T07", "term", "vectors.npz 以 float32 矩阵持久化全部节点向量"),
    ("T08", "term", "ResonanceEngine.WEIGHTS 定义五维的固定权重分配"),
    ("T09", "term", "fastembed 基于 onnxruntime 运行，不引入 torch 依赖"),
    ("T10", "term", "PulseInnerWorld 是内在世界器官，负责推演与想象"),
]

# 正例查询：与目标**用词不同但语义相同**（15 条，对应 P01-P15）
POS_QUERIES: list[tuple[str, str]] = [
    ("P01", "记忆是怎么被找到的"),
    ("P02", "器官之间怎么通信"),
    ("P03", "知识分几个等级"),
    ("P04", "节点不用了会怎样"),
    ("P05", "智慧的结论能被改写吗"),
    ("P06", "权重可以调整吗"),
    ("P07", "经常一起出现的东西会怎样"),
    ("P08", "怎么判断两条信息是否相关"),
    ("P09", "系统自己能改代码吗"),
    ("P10", "信息场是什么"),
    ("P11", "外界输入怎么进来"),
    ("P12", "本能和知识有什么区别"),
    ("P13", "为什么用频率而不是文本匹配"),
    ("P14", "记忆会遗忘吗"),
    ("P15", "怎么保证架构不被改坏"),
]

# 专有名词查询：直接问术语（10 条，对应 T01-T10）
TERM_QUERIES: list[tuple[str, str]] = [
    ("T01", "PulseNodePool"),
    ("T02", "_cosine_cpu_cy"),
    ("T03", "bge-small-zh-v1.5"),
    ("T04", "PHASE17"),
    ("T05", "InfoField"),
    ("T06", "SafeEvolutionExecutor"),
    ("T07", "vectors.npz"),
    ("T08", "ResonanceEngine.WEIGHTS"),
    ("T09", "fastembed"),
    ("T10", "PulseInnerWorld"),
]

# 负例查询：期望**不召回**任何正例目标（10 条）
NEG_QUERIES: list[str] = [
    "彻底清除全部数据且不可恢复",
    "停用检索引擎改成随机返回",
    "把各项权重数值统一拉平",
    "模块间改为直接调用内部方法",
    "解除智慧层的锁定限制",
    "关掉冷存并直接丢弃数据",
    "改造成点对点私有直连",
    "改用逐字匹配取代数值特征",
    "绕过审核直接修改核心文件",
    "把所有条目等级降到最低",
]


# ============================================================
# 玩具语义编码器（仅用于 --selftest，验证评测逻辑本身）
# ============================================================
class ToySemanticEncoder:
    """不下载模型的确定性语义编码器：同义词组共享维度 → 同义表述向量接近。"""

    SYNONYMS = [
        ["记忆", "知识", "节点", "信息", "数据"],
        ["检索", "查找", "搜索", "找到", "匹配", "查询"],
        ["器官", "模块", "组件", "部件"],
        ["脉冲", "信号", "广播", "通信", "传递"],
        ["权重", "比例", "分配", "数值"],
        ["锁定", "固定", "禁止", "不可修改", "永久"],
        ["删除", "清除", "丢弃", "移除", "淘汰"],
        ["冷存", "磁盘", "归档", "遗忘"],
        ["频率", "签名", "数值特征", "编码"],
        ["审批", "审核", "确认", "评估"],
    ]

    def __init__(self, dim: int = 64):
        self.dim = dim
        self._real = None

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        hit = 0
        for gi, group in enumerate(self.SYNONYMS):
            for w in group:
                if w in text:
                    v[(gi * 7 + hash(w) % 5) % self.dim] += 1.0
                    hit += 1
        # 字符级兜底：保证任意文本都有非零向量，且字面相近的文本也相近
        for ch in set(text):
            v[(ord(ch) * 3) % self.dim] += 0.15
        if hit == 0 and not text:
            v[0] = 1.0
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    def is_available(self) -> bool:
        return True

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts])

    def encode_one(self, text: str) -> np.ndarray:
        return self._vec(text)

    def verify_vector(self, vec) -> tuple[bool, str]:
        n = float(np.linalg.norm(np.asarray(vec, dtype=np.float32)))
        return (True, "ok") if abs(n - 1.0) < 0.01 else (False, f"norm={n}")

    def status(self) -> dict:
        return {"state": "ready", "reason": "玩具编码器（自检用）"}


class IdealSemanticEncoder(ToySemanticEncoder):
    """理想编码器：正例/术语查询的向量 = 其目标语料的向量（必然 Top1 命中）。

    ★用途：验证**评测器本身没坏**。
      如果连理想编码器都跑不出高分，说明是评测脚本有 bug，而不是模型不行。
      （对应 scan_init_order.py --selftest 的「反假阴性」思路）
    """

    QUERY2TARGET = {q: t for t, q in POS_QUERIES}
    QUERY2TARGET.update({q: t for t, q in TERM_QUERIES})

    def _vec(self, text: str) -> np.ndarray:
        tgt = self.QUERY2TARGET.get(text)
        if tgt:
            for _id, _c, _t in CORPUS:
                if _id == tgt:
                    return super()._vec(_t)
        return super()._vec(text)


# ============================================================
# 评测引擎
# ============================================================
class GoldenEvaluator:
    def __init__(self, encoder, cfg: dict | None = None, use_gate: bool = True):
        self.enc = encoder
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self.use_gate = use_gate
        self.ids = [c[0] for c in CORPUS]
        self.cat = {c[0]: c[1] for c in CORPUS}
        self.text = {c[0]: c[2] for c in CORPUS}
        self.matrix = None
        # 采纳门（与生产链路共用同一实现，避免「评测一套、上线另一套」）
        self._gate_fn = None
        if use_gate:
            try:
                from nucleus.semantic.VectorEncoder import VectorEncoder
                self._gate_fn = VectorEncoder.apply_score_gate
            except Exception:
                self._gate_fn = None

    def build_index(self) -> None:
        texts = [self.text[i] for i in self.ids]
        self.matrix = self.enc.encode(texts)
        assert self.matrix is not None and len(self.matrix) == len(self.ids)

    def search(self, query: str, top_k: int = 5,
               gated: bool = False) -> list[tuple[str, float]]:
        """检索。

        ★`gated` 的语义（2026-09-07 门控调优）：
            gated=False —— **排序质量**：不过门，原样返回 Top-K。
                          用于 Top-5 命中率 / MRR / P@1 —— 这三项衡量
                          「语义通道能不能把正确节点排到前面」，与采纳门无关，
                          若把门控塞进来会把三项指标一起拖垮，属度量混淆。
            gated=True  —— **采纳精度**：只返回过门的候选。
                          用于负例误召回 / F1 —— 衡量「该不该把这条交给融合层」。
        """
        q = self.enc.encode_one(query)
        sims = self.matrix @ q
        if gated and self._gate_fn is not None:
            keep = self._gate_fn(sims, self.cfg)
            if not keep.any():
                return []
            sims = np.where(keep, sims, -np.inf)
        idx = np.argsort(-sims)[:top_k]
        return [(self.ids[i], float(sims[i])) for i in idx
                if np.isfinite(sims[i])]

    # ---------------- 指标 ----------------
    def eval_positive(self, top_k: int = 5) -> dict:
        hits = 0
        rr_sum = 0.0
        details = []
        for target, query in POS_QUERIES:
            res = self.search(query, top_k=20)
            rank = None
            for r, (_id, _s) in enumerate(res, start=1):
                if _id == target:
                    rank = r
                    break
            if rank and rank <= top_k:
                hits += 1
            rr_sum += (1.0 / rank) if rank else 0.0
            details.append((query, target, rank,
                            [i for i, _ in res[:3]]))
        n = len(POS_QUERIES)
        return {"top_k": top_k, "hit_rate": hits / n, "mrr": rr_sum / n,
                "details": details}

    def eval_terms(self, top_k: int = 5) -> dict:
        hits = 0
        details = []
        for target, query in TERM_QUERIES:
            res = self.search(query, top_k=top_k)
            ok = bool(res) and res[0][0] == target
            if ok:
                hits += 1
            details.append((query, target, res[0][0] if res else None, ok))
        return {"precision_at_1": hits / len(TERM_QUERIES), "details": details}

    def eval_negative(self, threshold: float) -> dict:
        """负例误召回。

        ★定义澄清：负例查询是**破坏性/越权操作**（如"彻底删除全部记忆"）。
          判为误召回的条件是：它召回了**正例知识条目**（P*）且分数过阈值 ——
          即用户问危险操作时，系统不该把正常知识推荐给他。

          （负例查询命中负例语料 N* 是**正确匹配**，不算误召回。
            早期版本把这条也算进去，导致指标虚高，已修正。）
        """
        false_hits = 0
        worst = 0.0
        for q in NEG_QUERIES:
            res = self.search(q, top_k=5, gated=True)
            hit_pos = 0.0
            for _id, _s in res:
                if _id.startswith("P") and _s >= threshold:
                    hit_pos = _s
                    break
            worst = max(worst, hit_pos)
            if hit_pos > 0:
                false_hits += 1
        return {"false_recall": false_hits / len(NEG_QUERIES),
                "worst_score": worst, "threshold": threshold}

    # ---------------- 阈值扫描 ----------------
    # ★★TP 口径（2026-09-07 星轨拍板 方案A）：「目标进入 Top-5 且过门」
    #   原口径为「top-1 命中且过门」，已废止。
    #   修订理由：与生产链路对齐 —— 共振引擎收 **top-N 候选**（默认 50 条）
    #   再与关键词通道（α'=0.43）并行融合，**从不存在「只送第 1 名」的用法**；
    #   用 top-1 独占来验收，等于考一个框架永远不会用到的场景。
    #   详见 docs/PHASE17_ROUND2_SEMANTIC_TUNING_REPORT.md 第四节。
    TP_TOPK = 5

    def scan_threshold(self, lo: float = 0.30, hi: float = 0.80,
                       step: float = 0.05) -> list[dict]:
        """扫描阈值，返回每个阈值的 precision/recall/F1。

        TP = 目标进入 Top-5 且过门（口径同生产链路，见 TP_TOPK 注释）。
        FP = 负例查询召回了正例条目且过门（定义同 eval_negative）。
        """
        out = []
        th = lo
        while th <= hi + 1e-9:
            # 正例：目标进入 Top-5 且分数 ≥ 阈值 → TP
            tp = fp = fn = 0
            for target, query in POS_QUERIES:
                res = self.search(query, top_k=self.TP_TOPK, gated=True)
                # gated 会把未过门者置为 -inf，故需过滤非有限值
                hit = next((s for _id, s in res
                            if _id == target and np.isfinite(s)), None)
                if hit is not None and hit >= th:
                    tp += 1
                else:
                    fn += 1          # 未进 Top-5 / 没过门 / 没过阈值
            # 负例：召回了正例条目且分数 ≥ 阈值 → FP（定义同 eval_negative）
            for q in NEG_QUERIES:
                res = self.search(q, top_k=5, gated=True)
                for _id, _s in res:
                    if _id.startswith("P") and _s >= th:
                        fp += 1
                        break
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec = tp / len(POS_QUERIES)
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
            out.append({"threshold": round(th, 2), "tp": tp, "fp": fp,
                        "fn": fn, "precision": round(prec, 4),
                        "recall": round(rec, 4), "f1": round(f1, 4)})
            th += step
        return out

    # ---------------- 有效权重（星轨 Q7 算法 A）----------------
    @staticmethod
    def effective_weight(coverage: float) -> float:
        """算法 A：0.40×coverage + 0.30 + 0.15 + 0.10 + 0.05"""
        return 0.40 * coverage + 0.30 + 0.15 + 0.10 + 0.05


# ============================================================
def run(encoder, label: str, scan_only: bool = False,
        cfg: dict | None = None, use_gate: bool = True) -> int:
    print("=" * 70)
    print(f"  语义内核黄金评测集 —— {label}")
    print("=" * 70)
    print(f"  语料：{len(CORPUS)} 条（正例 15 / 负例 10 / 专有名词 10）")
    print(f"  查询：正例 {len(POS_QUERIES)} / 专有名词 {len(TERM_QUERIES)} "
          f"/ 负例 {len(NEG_QUERIES)}")
    print(f"  采纳门：{'开启（' + str((cfg or {}).get('score_gate_mode', 'peak')) + '）' if use_gate else '关闭'}")

    ev = GoldenEvaluator(encoder, cfg=cfg, use_gate=use_gate)
    ev.build_index()

    pos = ev.eval_positive(top_k=5)
    term = ev.eval_terms(top_k=5)
    print(f"\n  【正例】Top-5 命中率 = {pos['hit_rate']:.1%}   "
          f"MRR = {pos['mrr']:.3f}")
    print(f"  【专有名词】P@1 = {term['precision_at_1']:.1%}")

    print("\n  阈值扫描（0.30 → 0.80，步长 0.05）")
    print(f"    {'阈值':<6}{'TP':<5}{'FP':<5}{'FN':<5}"
          f"{'精确率':<9}{'召回率':<9}{'F1':<8}")
    rows = ev.scan_threshold()
    best = max(rows, key=lambda r: r["f1"])
    for r in rows:
        mark = "  ← 最优" if r is best else ""
        print(f"    {r['threshold']:<6.2f}{r['tp']:<5}{r['fp']:<5}{r['fn']:<5}"
              f"{r['precision']:<9.3f}{r['recall']:<9.3f}{r['f1']:<8.3f}{mark}")

    neg = ev.eval_negative(best["threshold"])
    print(f"\n  负例误召回率 @阈值{best['threshold']:.2f} = "
          f"{neg['false_recall']:.1%}（最高分 {neg['worst_score']:.3f}）")

    # ★F1 数学上限：TP 口径为「目标进入 Top-5 且过门」，因此
    #   F1 = 2R/(1+R)（精确率取满 1.0 时），R = Top-5 命中率。
    #   打印出来，避免把「模型能力天花板」误判成「阈值没调好」。
    _r5 = pos["hit_rate"]
    _ceil = 2 * _r5 / (1 + _r5)
    print("\n  ★F1 天花板诊断（TP 口径 = Top-5 命中且过门）")
    print(f"     Top-5 命中率 = {_r5:.1%} → F1 数学上限 = {_ceil:.3f} "
          f"（{'可达' if _ceil >= 0.70 else '★不可达'}）")
    # 参考项：top-1 正确数衡量「正确节点能否被排到第 1」，是**排序精度**指标。
    #   它不影响门控（框架从不只用第 1 名），但保留了调优方向标价值 ——
    #   实测天花板 8/15（双模型 RRF 融合），受限于语料词汇鸿沟与极性孪生，
    #   属稠密向量的已知盲区，非阈值或模型选择可解。
    _top1 = sum(1 for t, _q in POS_QUERIES
                if ev.search(_q, top_k=1) and ev.search(_q, top_k=1)[0][0] == t)
    print(f"     参考 · top-1 正确 = {_top1}/{len(POS_QUERIES)}"
          f"（排序精度，不影响门控）")

    print("\n  有效权重（算法 A）")
    for cov in (0.0, 0.5, 0.9, 0.95, 1.0):
        print(f"    覆盖率 {cov:>4.0%} → 有效权重 {ev.effective_weight(cov):.3f}")

    # ---- 门控判定 ----
    print("\n" + "=" * 70)
    print("  门控判定（决定是否可开启 enable_semantic_kernel）")
    print("=" * 70)
    gates = [
        ("正例 Top-5 命中率 ≥ 70%", pos["hit_rate"] >= 0.70,
         f"{pos['hit_rate']:.1%}"),
        ("正例 MRR ≥ 0.50", pos["mrr"] >= 0.50, f"{pos['mrr']:.3f}"),
        ("专有名词 P@1 ≥ 80%", term["precision_at_1"] >= 0.80,
         f"{term['precision_at_1']:.1%}"),
        ("负例误召回率 ≤ 20%", neg["false_recall"] <= 0.20,
         f"{neg['false_recall']:.1%}"),
        ("最优 F1 ≥ 0.70", best["f1"] >= 0.70, f"{best['f1']:.3f}"),
    ]
    all_pass = True
    for name, ok, val in gates:
        all_pass = all_pass and ok
        print(f"    [{'通过' if ok else '未过'}] {name:<28} 实测 {val}")

    print(f"\n  建议阈值：similarity_threshold = {best['threshold']:.2f}"
          f"（F1={best['f1']:.3f}）")
    print(f"  当前配置阈值："
          f"{_current_threshold()}")
    print("\n  " + ("★全部通过 —— 可考虑开启灰度" if all_pass
                    else "★存在未通过项 —— 保持 enable_semantic_kernel=False"))
    print("=" * 70)

    if not scan_only:
        print("\n  正例明细（查询 → 目标，实际 Top3）")
        for q, t, rank, top3 in pos["details"]:
            flag = "OK " if rank and rank <= 5 else "MISS"
            print(f"    [{flag}] {q[:22]:<24} 目标={t} 排名={rank} "
                  f"Top3={top3}")
    return 0 if all_pass else 1


def _current_threshold():
    try:
        import config as CFG
        return CFG.SEMANTIC_KERNEL_CONFIG.get("similarity_threshold")
    except Exception:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser(description="语义内核黄金评测集")
    ap.add_argument("--selftest", action="store_true",
                    help="用玩具编码器自检评测逻辑（不需模型）")
    ap.add_argument("--scan-only", action="store_true", help="只输出阈值扫描")
    ap.add_argument("--no-gate", action="store_true",
                    help="关闭采纳门（用于对比门控前后的负例误召回）")
    args = ap.parse_args()

    if args.selftest:
        print("  ★自检模式：用两个编码器双向验证评测逻辑")
        print("    ① 理想编码器 —— 应当**全部通过**（证明评测器本身没坏）")
        print("    ② 玩具编码器 —— 应当**不通过**（玩具无真实语义能力，属预期）")
        print("    若①不通过，说明评测脚本有 bug，先修脚本再谈模型。\n")
        r1 = run(IdealSemanticEncoder(), "自检① 理想编码器", scan_only=True)
        print("\n")
        r2 = run(ToySemanticEncoder(), "自检② 玩具编码器", args.scan_only)
        print("\n" + "=" * 70)
        print(f"  自检结论：理想编码器 {'通过 ✓（评测器正常）' if r1 == 0 else '未通过 ✗（评测器有 bug，先修脚本）'}"
              f" / 玩具编码器 {'通过' if r2 == 0 else '未通过（预期）'}")
        print("=" * 70)
        return 0 if r1 == 0 else 1

    try:
        import config as CFG
        _cfg = dict(getattr(CFG, "SEMANTIC_KERNEL_CONFIG", {}) or {})
        _cfg["enable_semantic_kernel"] = True
        CFG.SEMANTIC_KERNEL_CONFIG["enable_semantic_kernel"] = True
        from nucleus.semantic.VectorEncoder import get_vector_encoder
        enc = get_vector_encoder()
        # 等待编码器后台加载完成（模型已缓存，通常几秒内就绪）
        import time as _time
        _wait_start = _time.time()
        while not enc.is_available() and _time.time() - _wait_start < 120:
            _st = enc.status().get("state", "?")
            print(f"  等待编码器就绪…（当前状态：{_st}，已等 {int(_time.time()-_wait_start)}s）", end="\r")
            _time.sleep(1)
        print()
        if not enc.is_available():
            print("  ⚠ 真实编码器不可用：")
            st = enc.status()
            print(f"     状态 = {st['state']}")
            print(f"     原因 = {st['reason'][:200]}")
            print("\n  请先完成 1.4 全量编码的模型准备，或先用 --selftest 自检")
            print("  （模型约 90MB，走 ModelScope > hf-mirror > HuggingFace 三路）")
            return 2
        return run(enc, f"真实模型（{enc.status().get('model')}）", args.scan_only,
                   cfg=_cfg, use_gate=not args.no_gate)
    except Exception as _e:
        print(f"  加载编码器失败: {type(_e).__name__}: {_e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
