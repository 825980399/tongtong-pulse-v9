# -*- coding: utf-8 -*-
"""
KnowledgeQualityScorer.py —— 知识质量评分器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 对知识节点进行多维度质量评分
机制: 基于KnowledgeQualityScorer类实现，包含9个核心方法
定位: 知识治理层
"""

from __future__ import annotations

import re
from typing import Any



class KnowledgeQualityScorer:
    """统一知识质量评分器。"""

    def __init__(self) -> None:
        # 各维度权重（总和100）
        self.weights = {
            "info_density": 25,
            "keyword_quality": 25,
            "source_trust": 20,
            "length_appropriateness": 15,
            "classification_quality": 15,
        }
        # 无意义词（用于关键词质量评估）
        self._noise_words = {
            "的", "了", "是", "在", "有", "和", "与", "或", "也", "都",
            "就", "还", "会", "要", "能", "可以", "应该", "这个", "那个",
            "什么", "怎么", "如何", "为什么", "一个", "一种", "一些",
        }

    def score(
        self,
        content: str,
        keywords: list[str] | None = None,
        source: str = "",
        space_path: str = "",
        source_trust: float = 50.0,
    ) -> dict[str, Any]:
        """计算知识质量综合评分。

        Args:
            content: 知识内容文本
            keywords: 关键词列表
            source: 来源（如"搜索"、"对话"、"代码学习"）
            space_path: 分类路径
            source_trust: 来源信任分（0-100）

        Returns:
            {
                "total_score": 0-100,
                "level": "优秀"/"良好"/"一般"/"较差",
                "dimensions": {各维度得分},
                "is_temporary": bool（是否应标记为临时节点）,
                "suggestion": str（改进建议）,
            }
        """
        _content = content or ""
        _keywords = keywords or []

        # 维度1：信息密度
        _density_score = self._score_info_density(_content)

        # 维度2：关键词质量
        _keyword_score = self._score_keyword_quality(_keywords)

        # 维度3：来源信任
        _trust_score = self._score_source_trust(source, source_trust)

        # 维度4：内容长度
        _length_score = self._score_length(_content)

        # 维度5：分类质量
        _class_score = self._score_classification(space_path)

        # 加权总分
        _total = round(
            _density_score * self.weights["info_density"] / 100
            + _keyword_score * self.weights["keyword_quality"] / 100
            + _trust_score * self.weights["source_trust"] / 100
            + _length_score * self.weights["length_appropriateness"] / 100
            + _class_score * self.weights["classification_quality"] / 100,
            1,
        )

        # 等级
        if _total >= 80:
            _level = "优秀"
        elif _total >= 60:
            _level = "良好"
        elif _total >= 40:
            _level = "一般"
        else:
            _level = "较差"

        # 是否临时节点（总分<30或信息密度极低）
        _is_temporary = _total < 30 or _density_score < 20

        # 改进建议
        _suggestion = self._generate_suggestion(
            _density_score, _keyword_score, _trust_score, _length_score, _class_score
        )

        return {
            "total_score": _total,
            "level": _level,
            "dimensions": {
                "info_density": _density_score,
                "keyword_quality": _keyword_score,
                "source_trust": _trust_score,
                "length_appropriateness": _length_score,
                "classification_quality": _class_score,
            },
            "is_temporary": _is_temporary,
            "suggestion": _suggestion,
        }

    def _score_info_density(self, content: str) -> float:
        """信息密度评分：有效中文字符占比。"""
        if not content:
            return 0.0
        _chinese = len(re.findall(r'[\u4e00-\u9fff]', content))
        _total = len(content)
        if _total == 0:
            return 0.0
        _density = _chinese / _total
        # 密度>50%得满分，<10%得0分
        return round(min(100, max(0, (_density - 0.1) / 0.4 * 100)), 1)

    def _score_keyword_quality(self, keywords: list[str]) -> float:
        """关键词质量评分：有意义关键词占比。"""
        if not keywords:
            return 20.0  # 无关键词给基础分
        _meaningful = 0
        for _kw in keywords:
            if _kw and _kw not in self._noise_words and len(_kw) >= 2:
                _meaningful += 1
        _ratio = _meaningful / len(keywords)
        return round(_ratio * 100, 1)

    def _score_source_trust(self, source: str, trust: float) -> float:
        """来源信任评分。"""
        _base = max(0, min(100, trust))
        # 搜索来源额外扣分（网络内容可信度低）
        if "搜索" in source or "search" in source.lower():
            _base *= 0.8
        # 代码学习来源加分（内部生成，可信度高）
        elif "代码" in source or "code" in source.lower():
            _base = min(100, _base * 1.1)
        return round(_base, 1)

    def _score_length(self, content: str) -> float:
        """内容长度评分：50-500字为最佳。"""
        _len = len(content)
        if _len < 10:
            return 10.0
        if _len < 50:
            return round((_len - 10) / 40 * 60 + 10, 1)
        if _len <= 500:
            return 100.0
        if _len <= 2000:
            return round(100 - (_len - 500) / 1500 * 30, 1)
        return 50.0  # 过长扣分但不低于50

    def _score_classification(self, space_path: str) -> float:
        """分类质量评分：未分类扣分。"""
        if not space_path or space_path == "/" or "未分类" in space_path:
            return 20.0
        _parts = [p for p in space_path.strip("/").split("/") if p]
        if len(_parts) >= 2:
            return 100.0
        if len(_parts) == 1:
            return 60.0
        return 30.0

    def _generate_suggestion(
        self, density: float, keyword: float, trust: float,
        length: float, classification: float,
    ) -> str:
        """生成改进建议。"""
        _issues = []
        if density < 40:
            _issues.append("信息密度低")
        if keyword < 40:
            _issues.append("关键词质量差")
        if trust < 40:
            _issues.append("来源信任度低")
        if length < 40:
            _issues.append("内容长度不当")
        if classification < 40:
            _issues.append("分类不明确")
        if _issues:
            return f"质量短板: {', '.join(_issues)}"
        return "质量良好"


# ========== 单例 ==========

_quality_scorer: KnowledgeQualityScorer | None = None


def get_quality_scorer() -> KnowledgeQualityScorer:
    """获取知识质量评分器单例。"""
    global _quality_scorer
    if _quality_scorer is None:
        _quality_scorer = KnowledgeQualityScorer()
    return _quality_scorer


if __name__ == "__main__":
    scorer = get_quality_scorer()
    # 测试
    r1 = scorer.score(
        content="电力工程预算需要考虑材料成本、人工成本和机械使用费。",
        keywords=["电力", "工程", "预算", "成本"],
        source="对话",
        space_path="/技术/电力工程/预算",
        source_trust=80,
    )
    print(f"高质量内容: {r1['total_score']}分 ({r1['level']}) - {r1['suggestion']}")

    r2 = scorer.score(
        content="的了是在有",
        keywords=["的", "了"],
        source="搜索",
        space_path="/未分类",
        source_trust=20,
    )
    print(f"低质量内容: {r2['total_score']}分 ({r2['level']}) - 临时={r2['is_temporary']} - {r2['suggestion']}")
