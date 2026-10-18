# -*- coding: utf-8 -*-
"""
InsightBoard.py —— 洞察看板

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架运行指标可视化与洞察展示
机制: 基于InsightBoard类实现，包含10个核心方法
定位: 监控展示层
"""

import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc


class InsightBoard:
    """
    闭环间洞察共享黑板。
    
    八个核心闭环：
    1. 知识演化闭环（肝→内在世界→潜意识）
    2. 工具认知闭环（内在世界→大脑皮层→内在世界）
    3. 深度思考闭环（内在世界内部）
    4. 社交关系闭环（前额叶→自我认知→大脑皮层）
    5. 自我审视闭环（内在世界心跳驱动）
    6. 代码学习闭环（内在世界→SelfInspector→胃消化）
    7. 认知策略闭环（内在世界→自我认知→潜意识→双腿）
    8. 搜索反馈闭环（内在世界→潜意识）
    """

    def __init__(self, max_entries: int = 100):
        """
        Args:
            max_entries: 最大洞察条目数
        """
        self._entries: list[dict[str, Any]] = []
        self._max_entries = max_entries
        self._lock = threading.Lock()

        # 洞察有效期配置（秒）
        # ★v23.0补全：覆盖框架中实际使用的全部洞察类型
        self._ttl_config = {
            # ★P3-5修复：删除 6 个 TTL 幽灵 key（knowledge_growth/tool_effectiveness/
            #   cross_domain_discovery/cognitive_pattern/relation_change/growth_milestone），
            #   全库无任何 post() 写入也无 query() 读取，属死配置。
            "weak_area_detected": 3600,     # 薄弱领域：1小时
            "search_quality_warning": 1800, # 搜索质量警告：30分钟
            "innovation_insight": 7200,     # 创新洞察：2小时
            # ── v20.0-v22.0已使用的洞察类型 ──
            "spiritual_narrative": 7200,    # 精神叙事：2小时
            "temporal_self_insight": 10800, # 时间自我感知：3小时
            "behavioral_anomaly": 7200,     # 行为模式偏离：2小时
            "knowledge_association": 14400, # 知识关联图谱：4小时
            "existential_state": 3600,      # 存续状态感知：1小时
            "failure_attribution": 7200,    # 失败归因：2小时
            "first_person_experience": 7200,# 第一人称主体感：2小时
            "knowledge_boundary": 14400,    # 知识边界：4小时
            "emotion_attribution": 7200,    # 情绪归因：2小时
            "deep_self_review": 21600,      # 深度自我审视：6小时
            "startup_health": 7200,         # 启动健康检查：2小时
            "code_risk": 21600,             # 代码风险：6小时
            "code_health_improvement": 21600, # 代码健康改善：6小时
            "conversation_highlight": 86400,# 对话高光记忆：24小时
            "organized_memories": 21600,    # 记忆组织摘要：6小时
            "knowledge_contradiction": 21600, # 知识矛盾：6小时
            "eureka_moment": 7200,          # 顿悟时刻：2小时
            "evolution_plan": 21600,        # 进化方案：6小时
            "code_patch": 21600,            # 安全补丁：6小时
            # ── v23.0新增 ──
            "boundary_reinforcement": 21600, # 边界加固：6小时
            "model_quality_feedback": 7200,  # 大模型质量反馈：2小时
            "comprehensive_diagnosis": 21600,  # ★v23.0新增：综合诊断：6小时
            "modification_suggestion": 43200,  # ★v23.0新增：修改建议：12小时
        }

    def post(self, insight_type: str, content: str, source_loop: str,
             related_dimension: str = "", confidence: float = 0.5,
             keywords: list[str] | None = None) -> str:
        """
        发布一条洞察到黑板。
        
        Args:
            insight_type: 洞察类型（见_ttl_config）
            content: 洞察内容
            source_loop: 来源闭环名称
            related_dimension: 相关维度/领域
            confidence: 置信度 0.0-1.0
            keywords: 关联关键词
        
        Returns:
            洞察条目ID
        """
        entry = {
            "id": f"insight_{int(time.time() * 1000)}_{len(self._entries)}",
            "type": insight_type,
            "content": content,
            "source_loop": source_loop,
            "related_dimension": related_dimension,
            "confidence": confidence,
            "keywords": keywords or [],
            "posted_at": time.time(),
            "ttl": self._ttl_config.get(insight_type, 3600),
            "query_count": 0,
        }

        with self._lock:
            self._entries.append(entry)

            # 容量保护：超出上限时移除最旧的条目
            if len(self._entries) > self._max_entries:
                # 优先移除过期条目
                self._cleanup_expired()
                # 如果仍然超限，按时间排序后只保留最新的max_entries条
                if len(self._entries) > self._max_entries:
                    self._entries.sort(key=lambda e: e["posted_at"], reverse=True)
                    self._entries = self._entries[:self._max_entries]

        return entry["id"]

    def query(self, insight_type: str | None = None, related_dimension: str | None = None,
              source_loop: str | None = None, min_confidence: float = 0.0,
              max_age_seconds: float | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """
        查询与条件匹配的洞察。
        
        Args:
            insight_type: 洞察类型（可选）
            related_dimension: 相关维度（可选，支持部分匹配）
            source_loop: 来源闭环（可选）
            min_confidence: 最低置信度
            max_age_seconds: 最大时效（秒）
            limit: 返回上限
        
        Returns:
            匹配的洞察条目列表，按时效性和置信度排序
        """
        now = time.time()
        results = []

        with self._lock:
            for entry in self._entries:
                # 过滤过期条目
                age = now - entry["posted_at"]
                effective_ttl = max_age_seconds or entry["ttl"]
                if age > effective_ttl:
                    continue

                # 按类型过滤
                if insight_type and entry["type"] != insight_type:
                    continue

                # 按来源闭环过滤
                if source_loop and entry["source_loop"] != source_loop:
                    continue

                # 按相关维度过滤（部分匹配）
                if related_dimension and related_dimension not in entry.get("related_dimension", ""):
                    continue

                # 按置信度过滤
                if entry.get("confidence", 0) < min_confidence:
                    continue

                # 计算综合权重：时效性×0.4 + 置信度×0.6
                freshness = max(0, 1.0 - age / effective_ttl)
                weight = freshness * 0.4 + entry.get("confidence", 0.5) * 0.6

                results.append({
                    **entry,
                    "age_seconds": round(age, 1),
                    "weight": round(weight, 2),
                })

        # 按权重降序排序
        results.sort(key=lambda r: r["weight"], reverse=True)

        # 更新查询计数
        for r in results[:limit]:
            for entry in self._entries:
                if entry["id"] == r["id"]:
                    entry["query_count"] += 1
                    break

        return results[:limit]

    def get_latest(self, insight_type: str, source_loop: str | None = None) -> dict[str, Any] | None:
        """
        获取指定类型的最新洞察。
        
        Args:
            insight_type: 洞察类型
            source_loop: 来源闭环（可选）
        
        Returns:
            最新的一条洞察，如果不存在则返回None
        """
        results = self.query(
            insight_type=insight_type,
            source_loop=source_loop,
            limit=1
        )
        return results[0] if results else None

    def has_recent(self, insight_type: str, related_dimension: str | None = None,
                   max_age_seconds: float | None = None) -> bool:
        """
        检查是否有指定类型的近期洞察。
        
        Args:
            insight_type: 洞察类型
            related_dimension: 相关维度（可选）
            max_age_seconds: 最大时效（秒）
        
        Returns:
            是否存在匹配的近期洞察
        """
        results = self.query(
            insight_type=insight_type,
            related_dimension=related_dimension,
            max_age_seconds=max_age_seconds,
            limit=1
        )
        return len(results) > 0

    def _cleanup_expired(self):
        """清理所有过期条目"""
        now = time.time()
        self._entries = [
            e for e in self._entries
            if now - e["posted_at"] < e["ttl"] * 2  # 保留两倍TTL内的条目
        ]

    def cleanup(self):
        """手动触发清理"""
        with self._lock:
            self._cleanup_expired()

    def get_stats(self) -> dict[str, Any]:
        """获取黑板统计信息"""
        with self._lock:
            now = time.time()
            active = sum(1 for e in self._entries
                        if now - e["posted_at"] < e["ttl"])
            type_dist = {}
            for e in self._entries:
                t = e["type"]
                type_dist[t] = type_dist.get(t, 0) + 1
            return {
                "total_entries": len(self._entries),
                "active_entries": active,
                "expired_entries": len(self._entries) - active,
                "type_distribution": type_dist,
            }

    def _m290_score_entry(self, entry: dict[str, Any]) -> float:
        """★第290批 刀2：洞察/想象评分 = confidence × relevance × novelty。

        - confidence：entry["confidence"]（0-1，post 时已有）
        - relevance：启发式代理——有 keywords 或 related_dimension 时偏高
        - novelty：现成 VectorEncoder 相似度反推（此处用保守代理，见下方注释）

        返回 [0,1] 分值，供 Top-N 落盘排序。纯函数、无副作用。
        """
        try:
            _conf = float(entry.get("confidence", 0.5) or 0.5)
        except (TypeError, ValueError):
            _conf = 0.5
        _conf = max(0.0, min(1.0, _conf))
        _kw = entry.get("keywords") or []
        _dim = entry.get("related_dimension") or ""
        _rel = 0.6 + (0.2 if _kw else 0.0) + (0.2 if _dim else 0.0)
        _rel = max(0.0, min(1.0, _rel))
        # ★novelty：VectorEncoder/ExperienceRetriever 集成需编码全量知识语料，属下一批；
        #   此处以内容长度作极简代理（更长通常承载更完整的新联想），钳制到 [0.5,1.0]
        #   避免把「短但新颖」的洞察打残。真正的新颖度由后续批接 VectorEncoder 覆盖。
        try:
            _len = len(str(entry.get("content", "") or ""))
        except Exception:
            _len = 0
        _novelty = 0.5 + 0.5 * min(_len / 500.0, 1.0)
        _novelty = max(0.0, min(1.0, _novelty))
        return round(_conf * _rel * _novelty, 4)

    def _m290_top_n(self) -> int:
        """★第290批 刀2：日拍落盘 Top-N（防噪声淹盘）。默认 5，可从 config.INSIGHT_TOP_N_DUMP 覆盖。"""
        try:
            import config as _cfg
            return max(1, int(getattr(_cfg, "INSIGHT_TOP_N_DUMP", 5) or 5))
        except Exception:
            return 5

    def dump_to_disk(self, path: str) -> int:
        """★286批刀2：把当前黑板内容落盘为 JSON（供每日03:00日拍，滚动保留7份）。

        原子写（tmp + os.replace）。★第290批 刀2：每条带 score 且按分排序，
        仅保留 Top-N 防噪声淹盘。返回实际落盘条目数。
        """
        import json as _json
        import os as _os
        with self._lock:
            _entries = [dict(e) for e in self._entries]
        _scored = []
        for _e in _entries:
            _e = dict(_e)
            _e["score"] = self._m290_score_entry(_e)
            _scored.append(_e)
        _scored.sort(key=lambda e: e.get("score", 0.0), reverse=True)
        _top_n = self._m290_top_n()
        _kept = _scored[:_top_n]
        _payload = {
            "dumped_at": time.time(),
            "entry_count": len(_entries),
            "top_n": _top_n,
            "entries": _kept,
        }
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        _tmp = path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            _json.dump(_payload, _f, ensure_ascii=False, indent=2)
        _os.replace(_tmp, path)
        return len(_kept)


# ========== 模块级单例 ==========
_insight_board: InsightBoard | None = None
_insight_board_lock = threading.Lock()


def get_insight_board() -> InsightBoard:
    """获取InsightBoard单例"""
    global _insight_board
    if _insight_board is None:
        with _insight_board_lock:
            if _insight_board is None:
                _insight_board = InsightBoard()
    return _insight_board


def shutdown_insight_board() -> None:
    """★P0批次3：复位 InsightBoard 单例，满足器官零状态（规则4）。

    原停机流程未清理该全局单例，重启时会复用带残留黑板数据的旧实例。
    此处显式置空，使下次获取重建全新零状态实例。
    """
    global _insight_board
    _inst = _insight_board
    _insight_board = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.InsightBoard::shutdown_insight_board L289")
