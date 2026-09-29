# -*- coding: utf-8 -*-
"""
PatternPrewarmConsumer.py —— 模式预热消费者

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 预存模式的消费与激活
机制: 基于PatternPrewarmConsumer类实现，包含10个核心方法
定位: 学习激活层
"""

from __future__ import annotations

import threading
import time
from typing import Any
from collections.abc import Callable


# 常见周期事件 → 相关知识线索（关键词 / 主题）。高频事件优先覆盖。
# 这是确定性的默认解析表，不依赖外部知识库即可工作，保证链路在默认配置下
# 就有「实际效果」。
EVENT_KNOWLEDGE_HINTS: dict[str, list[str]] = {
    "heart.beat": ["心跳", "生命维持", "系统健康", "运行状态", "存活检测"],
    "system.boot": ["启动", "初始化", "配置加载", "引导", "自举"],
    "system.alarm": ["告警", "异常", "熔断", "故障处理", "降级"],
    "system.error": ["错误", "异常捕获", "堆栈", "恢复"],
    "ears.heard": ["听觉", "语音识别", "输入理解", "语义解析", "聆听"],
    "chat.message": ["对话", "回复生成", "意图理解", "交互", "会话"],
    "mouth.speak": ["表达", "语音合成", "输出", "反馈", "发声"],
    "knowledge.written": ["知识写入", "记忆固化", "经验沉淀", "归档"],
    "curiosity.tick": ["好奇", "自主探索", "兴趣演化", "发散"],
    "reflection.insight": ["反思", "洞察", "元认知", "总结"],
    "interest.changed": ["兴趣", "偏好", "关注点", "演化"],
}


class PatternPrewarmConsumer:
    """把 StreamMiner 的 prewarm 事件预热为知识线索缓存。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # event_type -> 预热条目
        self._preload_cache: dict[str, dict[str, Any]] = {}
        self._total_preloaded = 0
        self._total_served = 0
        self._last_preload_ts = 0.0

        # 可插拔解析器（默认用 EVENT_KNOWLEDGE_HINTS）
        self._resolver: Callable[[str], list[str]] | None = None
        # 可插拔向量检索（接入真实知识库 → 候选节点 id）
        self._vector_resolver: Callable[[str], list[str]] | None = None

    # ---- 可插拔钩子（用于接入真实知识库检索）----

    def set_hint_resolver(self, resolver: Callable[[str], list[str]] | None) -> None:
        """覆盖默认的关键词线索解析（event_type -> 关键词列表）。"""
        self._resolver = resolver

    def set_vector_resolver(self, resolver: Callable[[str], list[str]] | None) -> None:
        """接入真实向量/知识库检索：event_type -> 候选知识节点 id 列表。"""
        self._vector_resolver = resolver

    # ---- 内部解析 ----

    def _resolve_hints(self, event_type: str) -> list[str]:
        if self._resolver is not None:
            try:
                _r = self._resolver(event_type)
                if _r:
                    return list(_r)
            except Exception:
                pass
        return list(EVENT_KNOWLEDGE_HINTS.get(event_type, []))

    # ---- 核心消费入口 ----

    def on_prewarm(self, prewarm_list: list[dict]) -> int:
        """消费一批 prewarm 事件，预热知识线索缓存。

        Args:
            prewarm_list: StreamMiner.consume()["prewarm"] 结构，
                          [{event_type, predicted_ts, in_sec, regularity}, ...]
        Returns:
            实际预热（写入缓存）的事件数。
        """
        if not prewarm_list:
            return 0
        _loaded = 0
        with self._lock:
            for _u in prewarm_list:
                if not isinstance(_u, dict):
                    continue
                _et = _u.get("event_type")
                if not _et:
                    continue
                _hints = self._resolve_hints(_et)
                _entry: dict[str, Any] = {
                    "event_type": _et,
                    "hints": _hints,
                    "in_sec": _u.get("in_sec"),
                    "predicted_ts": _u.get("predicted_ts"),
                    "regularity": _u.get("regularity", 0.0),
                    "preloaded_at": time.time(),
                    "candidate_node_ids": [],
                }
                # 若接入了向量检索，补充候选节点 id（真实知识库预热）
                if self._vector_resolver is not None and _hints:
                    try:
                        _entry["candidate_node_ids"] = list(
                            self._vector_resolver(_et)
                        )
                    except Exception:
                        _entry["candidate_node_ids"] = []
                self._preload_cache[_et] = _entry
                self._total_preloaded += 1
                _loaded += 1
            if _loaded:
                self._last_preload_ts = time.time()
        return _loaded

    def consume_payload(self, payload: dict) -> int:
        """适配 InfoField 发射的单个 `pattern_prewarm` 脉冲载荷。"""
        if not isinstance(payload, dict):
            return 0
        _et = payload.get("predicted_event")
        if not _et:
            return 0
        return self.on_prewarm([{
            "event_type": _et,
            "in_sec": payload.get("in_sec"),
            "predicted_ts": payload.get("predicted_ts"),
            "regularity": payload.get("regularity", 0.0),
        }])

    # ---- 预加载查询（事件到达时消费）----

    def get_preloaded(self, event_type: str) -> dict | None:
        """查询某事件是否已预热；返回预热条目或 None。"""
        with self._lock:
            return self._preload_cache.get(event_type)

    def serve_on_arrival(self, event_type: str) -> dict | None:
        """事件实际到达时调用：返回预加载线索并记一次命中。

        返回非 None 即证明「预加载被真正消费」，是链路打通的可测证据。
        """
        with self._lock:
            _e = self._preload_cache.get(event_type)
            if _e is not None:
                self._total_served += 1
                return _e
            return None

    def clear(self) -> None:
        """清空缓存与统计（测试 / 重置用）。"""
        with self._lock:
            self._preload_cache.clear()
            self._total_preloaded = 0
            self._total_served = 0
            self._last_preload_ts = 0.0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_preloaded": self._total_preloaded,
                "total_served": self._total_served,
                "cached_events": len(self._preload_cache),
                "last_preload_ts": self._last_preload_ts,
            }


# ---- 模块级单例：InfoField 与测试共享同一份预热缓存 ----

_singleton: PatternPrewarmConsumer | None = None
_singleton_lock = threading.Lock()


def get_pattern_prewarm_consumer() -> PatternPrewarmConsumer:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = PatternPrewarmConsumer()
    return _singleton
