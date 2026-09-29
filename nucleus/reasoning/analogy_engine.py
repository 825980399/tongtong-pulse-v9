# -*- coding: utf-8 -*-
"""
analogy_engine.py —— 类比引擎

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 类比推理与相似性迁移
机制: 基于Skeleton类实现，包含10个核心方法
定位: 推理核心层
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any



@dataclass
class Skeleton:
    """文本片段的结构骨架。"""
    text: str
    triplets: list[tuple[str, str, str]] = field(default_factory=list)   # (主语, 谓语, 宾语)
    causal: list[tuple[str, str]] = field(default_factory=list)          # (因, 果)
    conditionals: list[tuple[str, str]] = field(default_factory=list)    # (条件, 结果)
    quantifiers: list[tuple[str, str, str]] = field(default_factory=list)  # (量词, 对象, 程度/数值)
    verbs: list[str] = field(default_factory=list)                       # 核心动词
    roles: list[str] = field(default_factory=list)                       # 语义角色（主语/宾语）

    def as_dict(self) -> dict[str, Any]:
        return {
            "triplets": self.triplets,
            "causal": self.causal,
            "conditionals": self.conditionals,
            "quantifiers": self.quantifiers,
            "verbs": self.verbs,
            "roles": self.roles,
        }


@dataclass
class AnalogyResult:
    """类比迁移结果。"""
    source_text: str
    target_text: str
    similarity: float            # 0~1 结构相似度
    shared_structure: list[str]  # 共享的结构特征描述
    migrated_hypothesis: str     # 迁移生成的可检验假设
    confidence: float            # 综合置信度
    is_cross_domain: bool        # 是否跨领域

    def to_dict(self) -> dict[str, Any]:
        return {
            "similarity": round(self.similarity, 4),
            "shared_structure": self.shared_structure,
            "migrated_hypothesis": self.migrated_hypothesis,
            "confidence": round(self.confidence, 4),
            "is_cross_domain": self.is_cross_domain,
            "evidence_skeleton": {
                "source": self.source_text[:60],
                "target": self.target_text[:60],
            },
        }


# 中文语义动词表（作为骨架识别的锚点）
_CAUSAL_WORDS = ["导致", "引起", "使", "造成", "因为", "所以", "因此", "于是", "带来"]
_CONDITIONAL_WORDS = ["如果", "若", "只要", "只有", "当", "一旦", "则", "就"]
_QUANTIFIER_PAT = re.compile(r"(越.*越|越多|越少|更快|更慢|更强|更弱|更高|更低|越大|越小|更多|更少)")
_TRIPLET_PAT = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,12}?)((?:是|有|包含|包括|拥有|具备|属于|具有|构成|分为)([\u4e00-\u9fa5A-Za-z0-9]{2,20}))")


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。；;！!？?]", text) if s.strip()]


class AnalogyEngine:
    """跨域类比迁移引擎。"""

    def __init__(self) -> None:
        pass

    # ========== 骨架提取 ==========

    def extract_skeleton(self, text: str) -> Skeleton:
        """提取文本片段的结构骨架（确定性规则，可复现）。"""
        sk = Skeleton(text=text)
        sentences = _split_sentences(text)
        for sent in sentences:
            # 因果链（含"越A越B"量变因果：如"电压越高，电流越大"）
            # 方式一："X越A，越B" 结构（量变因果）
            _mm = re.match(r"^(.{1,12}?)越(.{1,6}?)[，,]\s*越(.{1,12})$", sent.strip())
            if _mm:
                _cond, _mid, _res = _mm.group(1), _mm.group(2), _mm.group(3)
                sk.causal.append((f"{_cond}越{_mid}", f"越{_res}"))
                sk.conditionals.append((f"{_cond}越{_mid}", f"越{_res}"))
            # 方式二：常规因果词
            for w in _CAUSAL_WORDS:
                if w in sent:
                    parts = sent.split(w, 1)
                    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                        sk.causal.append((parts[0].strip(), parts[1].strip()))
            # 条件结构
            for w in _CONDITIONAL_WORDS:
                if w in sent:
                    parts = sent.split(w, 1)
                    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                        sk.conditionals.append((parts[0].strip(), parts[1].strip()))
            # 方式三："…时，…"（时间/情境条件）——如"资源充足时，团队活力增强"
            _tm = re.match(r"^(.{1,14}?)时[，,]\s*(.+)$", sent.strip())
            if _tm:
                sk.conditionals.append((_tm.group(1), _tm.group(2)))
            # 数量关系
            for m in _QUANTIFIER_PAT.finditer(sent):
                sk.quantifiers.append((m.group(1), sent[max(0, m.start() - 10):m.start()].strip(), ""))
            # 主谓宾三元组
            for m in _TRIPLET_PAT.finditer(sent):
                sk.triplets.append((m.group(1), m.group(2), m.group(3)))
                sk.roles.append(m.group(1))
                sk.roles.append(m.group(3))
        # 核心动词（去除停用词）
        _stop = {"是", "有", "包含", "包括", "拥有", "具备", "属于", "具有", "构成", "分为", "当", "如果"}
        for _, v, _ in sk.triplets:
            if v not in _stop:
                sk.verbs.append(v)
        # 从因果/条件中也提取动词（首个动词）
        for a, b in sk.causal + sk.conditionals:
            _vm = re.search(r"[\u4e00-\u9fa5]{2,6}(?=了|着|过|$)", b)
            if _vm and _vm.group(0) not in _stop:
                sk.verbs.append(_vm.group(0))
        return sk

    # ========== 结构相似度 ==========

    def _jaccard(self, a: list[str], b: list[str]) -> float:
        """Jaccard 相似度。"""
        if not a and not b:
            return 0.0
        sa, sb = set(a), set(b)
        if not sa and not sb:
            return 0.0
        inter = len(sa & sb)
        union = len(sa | sb)
        return inter / union if union else 0.0

    @staticmethod
    def _abstract_pair(pair: tuple[str, str]) -> str:
        """把因果/条件对抽象为结构模板（具体词 → 占位符），捕捉"形状"而非"内容"。

        策略：只保留"量词类别 + 变化方向"，领域名词全部替换为占位符 X/Y。

        例：("电压越高", "电流越大") → "X量高→Y量大"
            ("水压越高", "水流越大") → "X量高→Y量大"  （与上相同 → 结构相似）
            ("电阻增大", "电流减小") → "X增→Y减"
        """
        a, b = pair
        # 量词类别识别
        _up = ["越高", "越强", "越快", "越大", "越多", "越深", "越强", "越高"]
        _down = ["越少", "越慢", "越低", "越弱", "越小", "越浅"]
        _inc = ["增大", "升高", "提高", "增强", "上升", "增加", "加快", "变高", "变多", "增强"]
        _dec = ["减小", "降低", "下降", "减弱", "减少", "变低", "变少", "变慢", "变弱", "降低"]

        def _ab(x: str) -> str:
            for k in _up:
                if k in x:
                    return "X量高"
            for k in _down:
                if k in x:
                    return "X量低"
            for k in _inc:
                if k in x:
                    return "X增"
            for k in _dec:
                if k in x:
                    return "X减"
            return "X"
        return f"{_ab(a)}→{_ab(b)}"

    def compare(self, text_a: str, text_b: str) -> AnalogyResult:
        """
        比较两个文本片段的结构相似度，判定是否构成跨域类比。

        相似度计算：结构特征（因果/条件/量词）经"抽象化"后做加权 Jaccard。
        抽象化把"电压越高→电流越大"与"水压越高→水流越大"映射为同一模板
        "量高→量大"，从而识别跨域结构相似。
        跨域判定：结构相似度 >= 阈值 且 内容词重叠低（形状相似、内容不同）。
        """
        sk_a = self.extract_skeleton(text_a)
        sk_b = self.extract_skeleton(text_b)

        # 结构特征：抽象化为模板后比较
        def _abs_list(pairs) -> list[str]:
            return [self._abstract_pair(p) for p in pairs]

        sims = {
            "causal": self._jaccard(_abs_list(sk_a.causal), _abs_list(sk_b.causal)),
            "conditional": self._jaccard(_abs_list(sk_a.conditionals), _abs_list(sk_b.conditionals)),
            "quantifier": self._jaccard(sk_a.quantifiers, sk_b.quantifiers),
            "triplet": self._jaccard(sk_a.triplets, sk_b.triplets),
            "verb": self._jaccard(sk_a.verbs, sk_b.verbs),
        }
        # 权重：因果/条件最体现结构，量词次之，三元组与动词再弱一些
        weights = {
            "causal": 0.35,
            "conditional": 0.25,
            "quantifier": 0.15,
            "triplet": 0.15,
            "verb": 0.10,
        }
        structural_sim = sum(sims[k] * weights[k] for k in weights)

        # 内容相似度：语义名词的重叠度（内容越不同越像"跨域"）
        # 用 2~4 字词块提取语义名词，并剔除含量词/虚词字的块（越/增/减/高/低/大/小等）。
        # "电压/电流" vs "水压/水流" → 名词 Jaccard 低 → 领域不同 → 跨域。
        _STOP_WORDS = {"导致", "引起", "使", "因为", "所以", "因此", "如果", "则", "就", "当",
                       "被", "更", "越", "时", "了", "的", "以及", "并且", "然后", "随之",
                       "进一步", "过度", "充足", "持续", "下降", "提高"}
        _QTY_CHARS = set("越增减高低大强弱快慢升降冷热多少深浅宽窄长短轻重")
        def _nounset(t: str) -> set[str]:
            _out = set()
            for w in re.findall(r"[\u4e00-\u9fa5]{2,4}", t):
                if w in _STOP_WORDS:
                    continue
                if any(c in _QTY_CHARS for c in w):
                    continue
                _out.add(w)
            return _out
        content_sim = self._jaccard(sorted(_nounset(text_a)), sorted(_nounset(text_b)))

        # 跨域判定：结构相似（加权分数 或 任一核心结构特征强映射）+ 内容不重叠
        _max_feature = max(sims.values()) if sims else 0.0
        _structural_pass = (structural_sim >= 0.30) or (_max_feature >= 0.60)
        is_cross_domain = _structural_pass and (content_sim <= 0.45)

        # 置信度：结构相似度高则置信度高，内容重叠高则置信度降低（更可能是同领域直述）
        confidence = min(0.95, structural_sim * (1.0 - 0.4 * content_sim) + 0.15)

        # 共享结构描述
        shared = []
        for k in ("causal", "conditional", "quantifier", "triplet", "verb"):
            if sims[k] >= 0.5:
                _name = {"causal": "因果链", "conditional": "条件结构", "quantifier": "量变关系",
                         "triplet": "主谓宾关系", "verb": "核心动作"}[k]
                shared.append(f"{_name}(相似度{sims[k]:.2f})")

        hypothesis = self._build_hypothesis(sk_a, sk_b, is_cross_domain)

        return AnalogyResult(
            source_text=text_a,
            target_text=text_b,
            similarity=round(structural_sim, 4),
            shared_structure=shared,
            migrated_hypothesis=hypothesis,
            confidence=round(confidence, 4),
            is_cross_domain=is_cross_domain,
        )

    def _build_hypothesis(self, sk_a: Skeleton, sk_b: Skeleton, cross: bool) -> str:
        """根据骨架差异生成迁移假设。"""
        if not cross:
            return "两段结构相似度不足，或属于同领域直述，暂不生成跨域迁移假设。"
        # 从 B 的因果/条件骨架中提取"规则形式"，映射回 A 领域
        _rule_b = ""
        if sk_b.causal:
            cause, effect = sk_b.causal[0]
            _rule_b = f"当{cause[:20]}时→{effect[:20]}"
        elif sk_b.conditionals:
            cond, res = sk_b.conditionals[0]
            _rule_b = f"若{cond[:20]}则{res[:20]}"
        if _rule_b:
            return f"跨域类比假设：源领域规则「{_rule_b}」的因果/条件结构，可迁移到目标领域验证其是否同样成立。"
        return "跨域类比假设：两段结构骨架一致，建议在目标领域按源领域规律设计验证实验。"
