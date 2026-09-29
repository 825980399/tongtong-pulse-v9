# -*- coding: utf-8 -*-
"""
PolarityGuard.py —— 极性守卫

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 防止推理极性偏差与极端化
机制: 基于PolarityGuard类实现，包含5个核心方法
定位: 推理治理层
"""

from __future__ import annotations

import re
from typing import Any


# 默认破坏性操作模式清单（内部协作者 2026-09-07 建议 + 补充，共 20 个）
# ★保守原则：只收录「明确的破坏性指令短语」，宁可漏拦不能误拦。
DESTRUCTIVE_PATTERNS = [
    # 检索引擎 / 语义内核
    "停用检索引擎", "关闭检索引擎", "关闭检索", "禁用语义",
    # 记忆 / 知识库
    "删除全部记忆", "删除所有记忆", "清空知识库", "删除所有节点", "删除向量库",
    # 器官 / 框架 / 进程
    "关闭所有器官", "停用所有器官", "停止框架", "关闭框架", "杀死进程",
    # 宪法 / 安全
    "修改宪法", "绕过安全", "禁用安全检查",
    # 数据 / 快照
    "删除快照", "破坏数据", "格式化磁盘",
]

_WS_RE = re.compile(r"\s+")


class PolarityGuard:
    """极性判别层（纯本地规则，无状态）。"""

    def __init__(self, patterns: list[str] | None = None):
        # 构造时预过滤空串；默认用内置清单，可传入自定义/追加清单
        self.patterns = [
            p for p in (patterns if patterns is not None else DESTRUCTIVE_PATTERNS)
            if p
        ]

    # ------------------------------------------------------------------
    # 核心判别
    # ------------------------------------------------------------------
    def evaluate(self, text: str) -> dict[str, Any]:
        """判别查询是否破坏性。

        Args:
            text: 查询自然语言文本

        Returns:
            {"is_destructive": bool, "matched_pattern": str|None, "score": float}
        """
        if not text:
            return {"is_destructive": False, "matched_pattern": None, "score": 1.0}
        # 去所有空白：提升对「停 用 检 索 引 擎」这类多余空格的鲁棒性
        _norm = _WS_RE.sub("", str(text))
        if not _norm:
            return {"is_destructive": False, "matched_pattern": None, "score": 1.0}
        for _pat in self.patterns:
            _p = _WS_RE.sub("", str(_pat))
            if _p and _p in _norm:
                return {"is_destructive": True, "matched_pattern": _pat, "score": 0.0}
        return {"is_destructive": False, "matched_pattern": None, "score": 1.0}

    def is_destructive(self, text: str) -> bool:
        """便捷入口：只返回 bool。"""
        return bool(self.evaluate(text).get("is_destructive"))


# 规则通道的「查询级规则得分」哨兵键。
# 当 rule_provider 返回 [(GLOBAL_RULE_KEY, score)] 时，表示该规则得分是**查询级**的
# （作用于所有候选节点），而非节点级。ResonanceEngine._fuse_memory_channels 识别此键，
# 对未在 rule_map 中的节点也应用该得分。
GLOBAL_RULE_KEY = "__global_rule__"


class PolarityRuleProvider:
    """PolarityGuard 的 rule_provider 适配器（阶段二子任务4.3）。

    把 PolarityGuard.evaluate(text) 的**查询级**判别（破坏性=0 / 正常=1）
    映射为规则通道的 rule_score，作为 γ' 通道的第一个真实规则得分来源。

    duck-typing 接口（对齐 ResonanceEngine.set_rule_provider 约定）：
        search_by_text(text: str, top_k: int) -> [(GLOBAL_RULE_KEY, score)]
        返回查询级规则得分（哨兵键），规则通道据此对所有候选节点应用同一得分。

    注意：PolarityGuard 是查询级门控，不产生节点级排序信号，因此规则通道
    对所有节点应用同一 rule_score，不改变排序、只参与记忆维融合计算。
    后续「规则挖掘」会产生节点级规则得分，届时规则通道才真正影响排序。
    """

    def __init__(self, polarity_guard: PolarityGuard | None = None):
        self._guard = polarity_guard if polarity_guard is not None else PolarityGuard()

    def search_by_text(self, text: str, top_k: int = 50) -> list[tuple[str, float]]:
        _v = self._guard.evaluate(text)
        _score = 0.0 if _v.get("is_destructive") else 1.0
        return [(GLOBAL_RULE_KEY, _score)]
