# -*- coding: utf-8 -*-
"""
SynonymExpander.py —— 同义词扩展器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 查询词的同义词扩展与语义增强
机制: 基于SynonymExpander类实现，包含9个核心方法
定位: 知识检索层
"""

from __future__ import annotations

from typing import Any



class SynonymExpander:
    """同义词扩展器。"""

    # 内置同义词表（领域通用词对）
    _BUILTIN_SYNONYMS: dict[str, list[str]] = {
        # 电力/电气领域
        "电力": ["电气", "电能", "供电", "电网"],
        "电气": ["电力", "电能", "供电"],
        "工程": ["施工", "建设", "项目", "建造"],
        "施工": ["工程", "建设", "建造", "作业"],
        "造价": ["预算", "成本", "计价", "概算"],
        "预算": ["造价", "成本", "计价"],
        "安装": ["装配", "架设", "敷设"],
        "电缆": ["线缆", "导线", "线路"],
        "变压器": ["变电", "配变", "主变"],
        "断路器": ["开关", "断电器"],
        "配电": ["供电", "输电", "变电"],
        # 编程/技术领域
        "编程": ["代码", "开发", "程序", "编码"],
        "代码": ["编程", "程序", "源码", "脚本"],
        "算法": ["逻辑", "方法", "策略"],
        "架构": ["结构", "设计", "框架", "体系"],
        "框架": ["架构", "结构", "系统"],
        "调试": ["排错", "修复", "debug"],
        "性能": ["效率", "速度", "优化"],
        "优化": ["改进", "提升", "增强", "性能"],
        # 知识/认知领域
        "知识": ["认知", "信息", "内容", "经验"],
        "认知": ["知识", "理解", "意识"],
        "理解": ["认知", "领悟", "掌握"],
        "推理": ["逻辑", "推断", "思考"],
        "思考": ["推理", "思维", "冥想"],
        "学习": ["掌握", "习得", "训练"],
        "记忆": ["存储", "回忆", "留存"],
        # 通用概念
        "问题": ["缺陷", "错误", "bug", "故障"],
        "错误": ["问题", "缺陷", "异常", "bug"],
        "修复": ["解决", "修补", "修正", "fix"],
        "解决": ["修复", "处理", "应对"],
        "分析": ["研究", "解析", "拆解"],
        "设计": ["规划", "构思", "架构"],
        "测试": ["验证", "检验", "检查"],
        "验证": ["测试", "确认", "校验"],
        "质量": ["品质", "水准", "水平"],
        "效率": ["性能", "速度", "效能"],
        "安全": ["防护", "保障", "稳定"],
        "稳定": ["安全", "可靠", "健壮"],
    }

    def __init__(self) -> None:
        self._synonyms: dict[str, list[str]] = {}
        self._load_builtin()

    def _load_builtin(self) -> None:
        """加载内置同义词表，建立双向映射。"""
        for _word, _syns in self._BUILTIN_SYNONYMS.items():
            if _word not in self._synonyms:
                self._synonyms[_word] = []
            for _s in _syns:
                if _s not in self._synonyms[_word]:
                    self._synonyms[_word].append(_s)
                # 反向映射
                if _s not in self._synonyms:
                    self._synonyms[_s] = []
                if _word not in self._synonyms[_s]:
                    self._synonyms[_s].append(_word)

    def add_synonym(self, word: str, synonym: str) -> None:
        """动态添加同义词对。"""
        if word not in self._synonyms:
            self._synonyms[word] = []
        if synonym not in self._synonyms[word]:
            self._synonyms[word].append(synonym)
        if synonym not in self._synonyms:
            self._synonyms[synonym] = []
        if word not in self._synonyms[synonym]:
            self._synonyms[synonym].append(word)

    def expand(self, keywords: list[str], max_per_word: int = 3) -> list[str]:
        """扩展关键词列表，返回原词+同义词（去重）。

        Args:
            keywords: 原始关键词列表
            max_per_word: 每个词最多扩展的同义词数

        Returns:
            扩展后的关键词列表（原词在前，同义词在后）
        """
        if not keywords:
            return []

        _result = list(keywords)
        _seen = set(_result)

        for _kw in keywords:
            _kw_lower = _kw.lower()
            _syns = self._synonyms.get(_kw_lower, [])
            for _s in _syns[:max_per_word]:
                if _s not in _seen:
                    _result.append(_s)
                    _seen.add(_s)

        return _result

    def get_synonyms(self, word: str) -> list[str]:
        """获取单个词的同义词。"""
        return self._synonyms.get(word.lower(), [])

    def has_synonym(self, word: str) -> bool:
        """检查词是否有同义词。"""
        return word.lower() in self._synonyms

    def match_score(self, query_words: list[str], target_text: str) -> float:
        """计算查询词与目标文本的匹配度（含同义词扩展）。

        Args:
            query_words: 查询关键词列表
            target_text: 目标文本（如路径名）

        Returns:
            匹配分数（0-1，越高越匹配）
        """
        if not query_words or not target_text:
            return 0.0

        _expanded = self.expand(query_words)
        _target_lower = target_text.lower()
        _matched = 0
        _total = len(_expanded)

        for _w in _expanded:
            if _w.lower() in _target_lower:
                _matched += 1

        return _matched / _total if _total > 0 else 0.0

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。"""
        return {
            "total_words": len(self._synonyms),
            "total_pairs": sum(len(v) for v in self._synonyms.values()) // 2,
        }


# ========== 单例 ==========

_synonym_expander: SynonymExpander | None = None


def get_synonym_expander() -> SynonymExpander:
    """获取同义词扩展器单例。"""
    global _synonym_expander
    if _synonym_expander is None:
        _synonym_expander = SynonymExpander()
    return _synonym_expander


if __name__ == "__main__":
    exp = get_synonym_expander()
    print(f"同义词表: {exp.get_stats()}")
    print(f"扩展['电力', '工程']: {exp.expand(['电力', '工程'])}")
    print(f"扩展['造价', '预算']: {exp.expand(['造价', '预算'])}")
    print(f"匹配度: {exp.match_score(['电力', '工程'], '/技术/电气工程/施工'):.2f}")
