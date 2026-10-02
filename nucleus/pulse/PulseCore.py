# -*- coding: utf-8 -*-
"""
PulseCore.py —— 脉冲核心

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 脉冲信号生成与传播核心引擎
机制: 基于PulseCore类实现，包含10个核心方法
定位: 脉冲核心层
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


# ★暂缓项1：脉冲结构 TypedDict 契约（编译期检查，零运行时开销）
from nucleus.pulse_types import Pulse
from nucleus.const import  Event, ChatEvent, EarEvent, HeartEvent, InterestEvent, KnowledgeEvent, MouthEvent, ReflectionEvent, SubconsciousEvent, SystemEvent, VascularEvent
from nucleus._silent_except import silent_exc

# 层级默认映射表（当 emit 未指定 layer 时自动推断）
_DEFAULT_LAYER_MAP = {
    HeartEvent.BEAT: "L0",
    HeartEvent.ALIVE: "L0",
    SystemEvent.BOOT: "L0",
    SystemEvent.STOP: "L0",
    SystemEvent.ALARM: "L0",
    SystemEvent.ERROR: "L0",
    VascularEvent.SILENT_ORGAN: "L0",
    Event.CHAT_MESSAGE: "L1",
    ChatEvent.INITIATIVE: "L1",
    MouthEvent.SPEAK: "L1",
    MouthEvent.REPLY: "L1",
    EarEvent.HEARD: "L1",
    EarEvent.INTENT_DETECTED: "L1",
    ReflectionEvent.INSIGHT: "L1",
    InterestEvent.CHANGED: "L1",
    KnowledgeEvent.WRITTEN: "L2",
    KnowledgeEvent.COMPRESSED: "L2",
    KnowledgeEvent.RAW: "L2",
    Event.DIGEST_KNOWLEDGE: "L2",
    "purge.check": "L3",
    "purge.result": "L3",
    Event.CURIOSITY_TICK: "L3",
    SubconsciousEvent.EXPLORE: "L3",
}


class PulseCore:
    """
    脉冲核心引擎（v9.5 终极完整版）
    
    管理所有脉冲的完整生命周期。
    脉冲的实际路由由 InfoField 按 layer 异步分发。
    """
    
    def __init__(self, max_completed_fingerprints: int = 5000):
        self._completed_fingerprints: OrderedDict = OrderedDict()
        self._max_fingerprints = max_completed_fingerprints
        
        self._execution_lock = threading.Lock()
        
        # 全局统计
        self._total_emitted = 0
        self._total_completed = 0
        self._total_timeout = 0
        self._total_duplicated = 0
        
        # v9.5 新增: 按 layer 的发射统计
        self._layer_stats: dict[str, int] = {"L0": 0, "L1": 0, "L2": 0, "L3": 0}
        self._layer_stats_lock = threading.Lock()
        # ★7-1/P1-13：_total_emitted 参与 pulse_id 生成（:151），
        #   属「读-改-写后再读」的标识计数器而非纯统计：并发下两个脉冲
        #   可能自增后读到同一个值，序号不再单调。
        #   单独一把锁，不与 _execution_lock 混用——既避免扩大临界区，
        #   也避免与既有锁块嵌套（threading.Lock 不可重入，嵌套即死锁）。
        self._counter_lock = threading.Lock()
        
        self.info_field = None
        self.oscillon_field = None
        self._classical_mode = True
        
        # v9.5: 是否启用自动 layer 推断
        self._auto_layer = True
        
    # ========== 框架注入接口 ==========
    
    def set_info_field(self, info_field):
        self.info_field = info_field
    def set_oscillon_field(self, oscillon_field):
        self.oscillon_field = oscillon_field
        self._classical_mode = False
    def is_classical_mode(self) -> bool:
        return self._classical_mode
        
    # ========== 脉冲创建（v9.5 终极版） ==========
    
    def emit(self, source_organ: str, event_type: str, 
             payload: dict | None = None, priority: int = 5,
             ttl_ns: int = 5_000_000_000,
             layer: str | None = None,
             intent: str | None = None) -> Pulse:
        """
        创建一个脉冲（v9.5: 原生支持 layer）。
        
        Args:
            source_organ: 来源器官名称
            event_type:  事件类型（如 HeartEvent.BEAT）
            payload:     载荷数据
            priority:    优先级 0-10
            ttl_ns:      有效期（纳秒），默认 5 秒
            layer:       脉冲层级（v9.5 新增）。
                          - 'L0' 生命线（心跳/熔断/告警）
                          - 'L1' 实时交互（对话/意图）
                          - 'L2' 认知思考（知识/推理）
                          - 'L3' 后台自主（清理/演化）
                          - None 时自动推断（默认规则 + 兜底 L1）
        Returns:
            完整的 Pulse 字典
            
        """
        with self._counter_lock:
            self._total_emitted += 1
            # 立刻取回本次自增后的序号供下方 pulse_id 使用。
            # 若出了锁再读 self._total_emitted，可能读到别的线程已改写的
            # 值，两个脉冲就会拿到相同序号——那样加锁也就失去意义。
            _emit_seq = self._total_emitted
        
        # 自动推断 layer（如果未显式指定）
        if layer is None and self._auto_layer:
            layer = self._infer_layer(event_type)
        elif layer is None:
            layer = "L1"
        
        # 更新 layer 统计
        with self._layer_stats_lock:
            if layer in self._layer_stats:
                self._layer_stats[layer] += 1
            else:
                self._layer_stats[layer] = 1
        
        pulse_id = (
            f"pulse:{source_organ}:{event_type}:"
            f"{time.time_ns()}:{_emit_seq}"
        )
        
        pulse: Pulse = {
            "pulse_id": pulse_id,
            "source_organ": source_organ,
            "event_type": event_type,
            "priority": priority,
            "layer": layer,                       # v9.5: 层级标记
            "intent": intent or "",               # P1预埋落地: 脉冲意图标记
            "timestamp_ns": time.time_ns(),
            # 五维信息（预留）
            "time_dim": {"created_at": time.time()},
            "space_dim": {"path": ""},
            "state_dim": {"state": "active"},
            "logic_dim": {"call_chain": [source_organ]},
            "memory_dim": {"frequency_signature": 0.0, "linked_nodes": [], "hebbian_weight": 0.0},
            # 载荷与生命周期
            "payload": payload or {},
            "ttl_ns": ttl_ns,
            "status": "created",
        }
        return pulse
    
    def _infer_layer(self, event_type: str) -> str:
        """
        根据事件类型推断默认层级（v9.5 新增）。
        如果无匹配则返回 'L1'。
        """
        if event_type in _DEFAULT_LAYER_MAP:
            return _DEFAULT_LAYER_MAP[event_type]
        return "L1"
    
    def set_auto_layer(self, enabled: bool):
        """启用/禁用自动 layer 推断"""
        self._auto_layer = enabled
    
    # ========== 脉冲分发（保留兼容，调度由 InfoField 主导） ==========
    
    def dispatch(self, pulse: dict[str, Any], 
                 handler: Callable | None = None,
                 timeout_ns: int = 3_000_000_000) -> dict[str, Any]:
        """
        分发一个脉冲（v9.5: 保留原有同步分发逻辑）。
        实际业务调度由 InfoField 按 layer 异步分发，此方法作为兜底或测试用。
        注意：timeout_ns 为「事后审计阈值」——Python 无法中断运行中的 handler，
        超时仅标记状态并计入统计，不会真正中止执行。
        """
        pulse_id = pulse.get("pulse_id", "unknown")

        # ★INFRA-1修复: 幂等键改为「业务指纹」而非唯一 pulse_id，
        #   使真正重复的业务事件能被去重；无业务载荷时回退 pulse_id（保持原行为）
        dedup_key = self._business_fingerprint(pulse) if pulse.get("payload") else pulse_id

        if self._is_duplicate(dedup_key):
            with self._counter_lock:
                self._total_duplicated += 1
            pulse["status"] = "duplicate"
            return {"status": "duplicate", "pulse_id": pulse_id, "reason": "已执行过"}

        if handler is None:
            self._mark_completed(dedup_key)
            pulse["status"] = "completed"
            return {"status": "completed", "pulse_id": pulse_id, "note": "无处理函数"}
        
        start_ns = time.time_ns()
        try:
            result = handler(pulse)
            elapsed_ns = time.time_ns() - start_ns
            if elapsed_ns > timeout_ns:
                with self._counter_lock:
                    self._total_timeout += 1
                pulse["status"] = "timeout"
                self._mark_completed(dedup_key)
                return {"status": "timeout", "pulse_id": pulse_id, "elapsed_ms": elapsed_ns / 1_000_000}
            
            self._mark_completed(dedup_key)
            with self._counter_lock:
                self._total_completed += 1
            pulse["status"] = "completed"
            return {"status": "completed", "pulse_id": pulse_id, "result": result, "elapsed_ms": elapsed_ns / 1_000_000}
        except Exception as e:
            elapsed_ns = time.time_ns() - start_ns
            with self._counter_lock:
                self._total_timeout += 1
            pulse["status"] = "error"
            self._mark_completed(dedup_key)
            return {"status": "error", "pulse_id": pulse_id, "error": str(e), "elapsed_ms": elapsed_ns / 1_000_000}
    
    # ========== 并发竞争消解 ==========
    
    def sort_by_priority(self, pulses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(pulses, key=lambda p: (-p.get("priority", 5), p.get("timestamp_ns", 0)))
    
    def destroy(self, pulse: dict[str, Any]):
        pulse["status"] = "destroyed"
        pulse["payload"] = None
    
    # ========== 幂等保护 ==========

    def business_fingerprint(self, pulse: dict[str, Any]) -> str:
        """公开封装 _business_fingerprint，供信息场调用（规则14）"""
        return self._business_fingerprint(pulse)

    def _business_fingerprint(self, pulse: dict[str, Any]) -> str:
        """返回脉冲的业务级指纹（稳定去重键）。

        代表同一业务事件的不同脉冲（相同来源/类型/目标/意图/载荷）
        会折叠为同一指纹，从而实现真正的重复事件去重；
        与原先基于唯一 pulse_id 的去重相比，不会再因 time.time_ns()
        与 _total_emitted 而永远不命中。
        """
        import hashlib
        import json
        payload = pulse.get("payload") or {}
        payload_key = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        source = pulse.get("source_organ", "")
        event_type = pulse.get("event_type", "")
        target = pulse.get("target_organ", "") or pulse.get("target", "")
        intent = pulse.get("intent", "") or ""
        base = f"{source}|{event_type}|{target}|{intent}|{payload_key}"
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]
        return f"biz:{digest}"

    def _is_duplicate(self, pulse_id: str) -> bool:
        with self._execution_lock:
            return pulse_id in self._completed_fingerprints
    
    def notify_completed(self, pulse: dict[str, Any] | None = None) -> None:
        """★P0修复：公开完成通知接口，供 InfoField 分发完成后调用。

        原 _total_completed 只在未被实际调用的 execute() 中增加，
        导致运行日志显示"发射9857 完成0"。
        """
        try:
            if pulse and pulse.get("payload"):
                _key = self._business_fingerprint(pulse)
            else:
                _key = pulse.get("pulse_id", "unknown") if pulse else "unknown"
            with self._execution_lock:
                self._completed_fingerprints[_key] = time.time()
                while len(self._completed_fingerprints) > self._max_fingerprints:
                    self._completed_fingerprints.popitem(last=False)
                self._total_completed += 1
        except Exception as e:
            silent_exc(e, where="nucleus.pulse.PulseCore::notify_completed L294")

    def _mark_completed(self, pulse_id: str):
        with self._execution_lock:
            self._completed_fingerprints[pulse_id] = time.time()
            while len(self._completed_fingerprints) > self._max_fingerprints:
                self._completed_fingerprints.popitem(last=False)
    
    def _clean_expired_fingerprints(self, ttl_seconds: float = 5.0):
        now = time.time()
        with self._execution_lock:
            expired = [pid for pid, ts in self._completed_fingerprints.items() if now - ts > ttl_seconds]
            for pid in expired:
                del self._completed_fingerprints[pid]
    
    # ========== 统计信息（v9.5 增强） ==========
    
    def get_stats(self) -> dict[str, Any]:
        with self._layer_stats_lock:
            layer_counts = dict(self._layer_stats)
        return {
            "total_emitted": self._total_emitted,
            "total_completed": self._total_completed,
            "total_timeout": self._total_timeout,
            "total_duplicated": self._total_duplicated,
            "cached_fingerprints": len(self._completed_fingerprints),
            "layer_stats": layer_counts,              # v9.5 新增
            "auto_layer": self._auto_layer,           # v9.5 新增
            "classical_mode": self._classical_mode,
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== PulseCore v9.5 终极版自测 ===\n")
    
    core = PulseCore()
    
    # 1. layer 显式指定
    p0 = core.emit("心脏", HeartEvent.BEAT, priority=7, layer="L0")
    assert p0["layer"] == "L0", "显式 layer 失败"
    print(f"1. 显式 L0: layer={p0['layer']} ✅")
    
    # 2. 自动推断 layer
    p1 = core.emit("胃", Event.DIGEST_KNOWLEDGE)
    assert p1["layer"] == "L2", f"自动推断失败: {p1['layer']}"
    print(f"2. 自动推断 L2: event=digest.knowledge, layer={p1['layer']} ✅")
    
    p2 = core.emit("耳朵", Event.CHAT_MESSAGE)
    assert p2["layer"] == "L1", f"自动推断失败: {p2['layer']}"
    print(f"3. 自动推断 L1: event=chat.message, layer={p2['layer']} ✅")
    
    p3 = core.emit("潜意识", Event.CURIOSITY_TICK)
    assert p3["layer"] == "L3", f"自动推断失败: {p3['layer']}"
    print(f"4. 自动推断 L3: event=curiosity.tick, layer={p3['layer']} ✅")
    
    # 未知事件默认 L1
    p4 = core.emit("未知", Event.UNKNOWN_EVENT)
    assert p4["layer"] == "L1", f"未知事件默认失败: {p4['layer']}"
    print(f"5. 默认 L1: event=unknown.event, layer={p4['layer']} ✅")
    
    # 6. 分层统计
    stats = core.get_stats()
    print(f"6. 分层统计: {stats['layer_stats']}")
    
    # 7. 幂等保护
    def handler(p): return {"ok": True}
    r1 = core.dispatch(p0, handler=handler)
    r2 = core.dispatch(p0, handler=handler)
    assert r2["status"] == "duplicate", "幂等保护失效"
    print(f"7. 幂等保护: {r2['status']} ✅")
    
    print(f"\n8. 总览: 发射{stats['total_emitted']} 完成{stats['total_completed']} 超时{stats['total_timeout']} 重复{stats['total_duplicated']}")
    print("=== 自测全部通过 ===")