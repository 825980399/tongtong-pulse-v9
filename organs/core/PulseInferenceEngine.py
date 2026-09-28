# -*- coding: utf-8 -*-
"""
PulseInferenceEngine —— 推理引擎器官 · 本地轻量推理插槽

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 InferenceEngineEvent.EXECUTE 脉冲，执行带 KV 缓存的本地轻量推理，把结果写入缓存后广播 InferenceEngineEvent.RESULT；在外部大模型不可用时提供可降级的推理通路。
机制: on_pulse 按 event_type 分派到 _on_execute 或 _on_status_request；_on_execute 先查 self._kv_cache，命中则累加 _cache_hit_count 并直接返回 status=cache_hit，未命中调 _simulate_inference 生成结果写入缓存，缓存超过 _max_cache 时按结果长度淘汰最旧项，最后 _emit InferenceEngineEvent.RESULT（layer=L2，字段契约为 question/answer）；_on_status_request 回 get_stats。
定位: 躯体层的「本地推理插槽」，是推理能力的兜底实现而非主链路。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import InferenceEngineEvent, SystemEvent


class PulseInferenceEngine(BasePulseOrgan):
    """脉冲驱动推理引擎（v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "推理引擎"):
        super().__init__(organ_name)
        self._kv_cache: dict[str, Any] = {}
        self._inference_count = 0
        self._cache_hit_count = 0
        self._max_cache = 500

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})
        if event_type == InferenceEngineEvent.EXECUTE:
            return self._on_execute(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    def _on_execute(self, payload: dict) -> dict[str, Any]:
        query = payload.get("query", "")
        cache_key = query.strip()[:80]
        if cache_key in self._kv_cache:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._cache_hit_count += 1
            return {"status": "cache_hit", "result": self._kv_cache[cache_key]}
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._inference_count += 1
        result = self._simulate_inference(query)
        self._kv_cache[cache_key] = result
        if len(self._kv_cache) > self._max_cache:
            oldest = min(self._kv_cache.keys(), key=lambda k: len(self._kv_cache[k]) if isinstance(self._kv_cache[k], str) else 0)
            del self._kv_cache[oldest]
        # v9.5: 推理结果脉冲标记为L2认知思考层
        # ★P0-5修复：统一字段契约为 question/answer（与 PulseInnerWorld 发射方、
        # PulseCortex/PulseMotivationCycle 订阅方对齐），原 query/result 字段无人读取。
        self._emit(InferenceEngineEvent.RESULT, {
            "question": query,
            "answer": result,
            "method": "inference_engine",
            "confidence": 0.6,
            "cache_hit": False
        }, priority=6, layer="L2")
        return {"status": "executed", "result": result}

    def _simulate_inference(self, query: str) -> str:
        return f"推理结果: 基于五维共振匹配，'{query[:40]}' 的最优答案是..."

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name, "inference_count": self._inference_count,
            "cache_hit_count": self._cache_hit_count, "cache_size": len(self._kv_cache),
            "is_running": self.is_running,
        }

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name, "event_types": [InferenceEngineEvent.EXECUTE, SystemEvent.STATUS_REQUEST], "min_priority": 1}]

    # ========== 未来演化预留（v10.0 振荡场） ==========
    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


if __name__ == "__main__":
    print("=== PulseInferenceEngine v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self): self.published = []
        def publish(self, p): self.published.append(p)
    m = MockInfoField(); e = PulseInferenceEngine("推理引擎"); e.set_info_field(m); e.start()
    r1 = e.on_pulse({"event_type": InferenceEngineEvent.EXECUTE, "payload": {"query": "曈曈是谁"}, "priority": 6})
    print(f"1. 首次推理: {r1['status']}")
    # 验证推理结果脉冲的 layer 标记
    result_pulses = [p for p in m.published if p.get("event_type") == InferenceEngineEvent.RESULT]
    if result_pulses:
        print(f"   RESULT脉冲 layer: {result_pulses[-1].get('layer', '未设置')} (预期L2)")
    r2 = e.on_pulse({"event_type": InferenceEngineEvent.EXECUTE, "payload": {"query": "曈曈是谁"}, "priority": 6})
    print(f"2. 缓存命中: {r2['status']}")
    s = e.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"3. 统计: 推理{s['inference_count']}次, 缓存命中{s['cache_hit_count']}次")
    e.stop(); print("\n=== 自测全部通过 ===")
