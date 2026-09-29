# -*- coding: utf-8 -*-
"""
ContradictionDetector.py —— 矛盾检测器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 检测知识与推理中的矛盾
机制: 基于ContradictionDetector类实现，包含7个核心方法
定位: 推理治理层
"""

from __future__ import annotations

import re
from typing import Any


# 完整否定词对（原 knowledge_noise_filter.NEGATION_PAIRS，15 对）
NEGATION_PAIRS = [
    ("是", "不是"), ("可以", "不可以"), ("能", "不能"),
    ("正确", "错误"), ("真", "假"), ("有", "没有"),
    ("存在", "不存在"), ("有效", "无效"), ("成功", "失败"),
    ("支持", "不支持"), ("允许", "禁止"), ("开启", "关闭"),
    ("增加", "减少"), ("上升", "下降"), ("提高", "降低"),
]

# 肝的矛盾对立词子集（原 PulseLiver._OPPOSITE_PAIRS，6 对）
OPPOSITE_PAIRS = (
    ("是", "不是"), ("可以", "不可以"), ("能", "不能"),
    ("正确", "错误"), ("真", "假"), ("有", "没有"),
)

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,6}")


class ContradictionDetector:
    """通用矛盾检测原语（纯静态方法，无状态，可被任意模块直接调用）。"""

    # ------------------------------------------------------------------
    # 底层原语
    # ------------------------------------------------------------------

    @staticmethod
    def _common_cjk_words(val_a: str, val_b: str, limit: int = 5) -> set[str]:
        """两段文本前 limit 个中文词（2~6 字）的交集。"""
        words_a = set(_CJK_RE.findall(val_a)[:limit])
        words_b = set(_CJK_RE.findall(val_b)[:limit])
        return words_a & words_b

    @staticmethod
    def has_opposition(val_a: str, val_b: str, pairs=None) -> bool:
        """纯对立词检测：一个含正面词、另一个含对应负面词，不要求共同概念词。

        原 PulseLiver._detect_contradictions 的 opposition 判定即此逻辑
        （词对 + 外部关键词重叠过滤），抽取后行为严格等价。
        ★此处不做 lower()，与原肝实现逐字等价（词对均为中文，大小写
        对中文匹配无影响，保持原样最稳妥）。
        """
        if not val_a or not val_b:
            return False
        _pairs = pairs if pairs is not None else NEGATION_PAIRS
        return any(pos in val_a and neg in val_b or neg in val_a and pos in val_b for pos, neg in _pairs)

    # ------------------------------------------------------------------
    # 三种矛盾类型
    # ------------------------------------------------------------------

    @staticmethod
    def detect_semantic(val_a: str, val_b: str, pairs=None) -> bool:
        """语义矛盾：否定词对 + 至少 1 个共同中文概念词。

        等价于原 knowledge_noise_filter.detect_value_contradiction：
        先 lower，再查对立词（正/反两向），命中则要求前 5 个中文词有交集。
        """
        if not val_a or not val_b:
            return False
        _pairs = pairs if pairs is not None else NEGATION_PAIRS
        va = val_a.lower()
        vb = val_b.lower()
        for pos, neg in _pairs:
            if (pos in va and neg in vb) or (neg in va and pos in vb):
                if len(ContradictionDetector._common_cjk_words(val_a, val_b)) >= 1:
                    return True
        return False

    @staticmethod
    def detect_numeric(val_a: str, val_b: str) -> bool:
        """数值矛盾：两值都含数值、数值集合不同、且去掉数值后仍有共同概念词。

        约束「共同概念词」是为了排除不同时间的测量值（如「温度 30 度」
        vs「温度 50 度」可能是先后两次读数，不是矛盾），只有同一概念下
        冲突的数值断言才算矛盾。
        """
        if not val_a or not val_b:
            return False
        nums_a = _NUM_RE.findall(val_a)
        nums_b = _NUM_RE.findall(val_b)
        if not nums_a or not nums_b:
            return False
        if set(nums_a) == set(nums_b):
            return False
        text_a = _NUM_RE.sub("", val_a)
        text_b = _NUM_RE.sub("", val_b)
        return len(ContradictionDetector._common_cjk_words(text_a, text_b)) >= 1

    @staticmethod
    def detect_path(path_a: str, path_b: str,
                    val_a: str, val_b: str, pairs=None) -> bool:
        """路径矛盾：两节点位于同一路径或互为父子路径，且值语义对立。

        同一知识域下出现对立声明，比跨域更可能是真矛盾。
        """
        if not path_a or not path_b:
            return False
        pa = path_a.rstrip("/")
        pb = path_b.rstrip("/")
        same_or_child = (
            pa == pb
            or pa.startswith(pb + "/")
            or pb.startswith(pa + "/")
        )
        if not same_or_child:
            return False
        return ContradictionDetector.detect_semantic(val_a, val_b, pairs=pairs)

    # ------------------------------------------------------------------
    # 聚合入口（规则通道使用）
    # ------------------------------------------------------------------

    @staticmethod
    def detect(node_a: Any, node_b: Any) -> dict | None:
        """通用入口：检测两个知识节点是否矛盾。

        依次尝试数值矛盾 → 语义矛盾 → 路径矛盾，命中即返回详情。

        Args:
            node_a / node_b: PulseNode 实例，或任意含 value / space_path
                            属性的对象（宽松取值，缺省按空处理）。

        Returns:
            {
                "type": "numeric" | "semantic" | "path",
                "value_a": str, "value_b": str,
                "path_a": str, "path_b": str,
            }
            无矛盾返回 None。
        """
        val_a = ContradictionDetector._value_of(node_a)
        val_b = ContradictionDetector._value_of(node_b)
        path_a = getattr(node_a, "space_path", "") or ""
        path_b = getattr(node_b, "space_path", "") or ""

        if ContradictionDetector.detect_numeric(val_a, val_b):
            return {
                "type": "numeric",
                "value_a": val_a[:100],
                "value_b": val_b[:100],
                "path_a": path_a,
                "path_b": path_b,
            }
        if ContradictionDetector.detect_semantic(val_a, val_b):
            return {
                "type": "semantic",
                "value_a": val_a[:100],
                "value_b": val_b[:100],
                "path_a": path_a,
                "path_b": path_b,
            }
        if ContradictionDetector.detect_path(path_a, path_b, val_a, val_b):
            return {
                "type": "path",
                "value_a": val_a[:100],
                "value_b": val_b[:100],
                "path_a": path_a,
                "path_b": path_b,
            }
        return None

    @staticmethod
    def _value_of(node: Any) -> str:
        """取节点 value 并归一为字符串（对齐肝的 str(value) 处理）。"""
        if node is None:
            return ""
        v = getattr(node, "value", "")
        return v if isinstance(v, str) else str(v)
