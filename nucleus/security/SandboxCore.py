# -*- coding: utf-8 -*-
"""
SandboxCore.py —— 沙箱核心

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 安全沙箱执行环境核心
机制: 基于SandboxCore类实现，包含4个核心方法
定位: 安全执行层
"""

import threading
import time
from typing import Any



class SandboxCore:
    """
    P2-2: 三级安全沙箱核心引擎（v9.5 适配版）
    
    三级纵深防御:
        L1 (皮肤):    关键词模式匹配，快速拦截明显威胁
        L2 (沙箱):    隔离执行环境，限制资源访问
        L3 (伦理):    内容语义审查，价值冲突裁决
    
    v9.5说明:
        本模块作为安全服务提供方，不主动发射脉冲，因此无需 layer 标记。
        实际安全拦截脉冲（SecurityEvent.*）由各防御器官（皮肤/沙箱/伦理）按分层规则发射。
    """
    
    def __init__(self, max_history: int = 100):
        self._lock = threading.Lock()
        self._event_history: list[dict[str, Any]] = []
        self._max_history = max_history
        self._blocked_count = 0
        self._passed_count = 0
        
        # 安全态势评分 (0.0-1.0, 越高越危险)
        self._threat_level = 0.0
    
    def adjudicate(self, content: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        统一安全裁决入口（v24.0实现实际审查）。
        当前实现基础危险词检测，未来可接入三级防御器官。
        
        返回:
            {"verdict": "blocked"|"passed", "level": "L1"|"L2"|"L3", "reason": str, "threat_score": float}
        """
        if not content:
            self.record_event("adjudicate", "sandbox_core", "passed", "空内容", 0.0)
            return {"verdict": "passed", "level": "L1", "reason": "空内容", "threat_score": 0.0}

        # 从config读取危险词表，失败使用兜底
        try:
            import config
            forbidden_keywords = getattr(config, 'ETHICS_CONFIG', {}).get('forbidden_keywords', [])
        except Exception:
            forbidden_keywords = []

        content_lower = content.lower()
        for kw in forbidden_keywords:
            if kw in content_lower:
                self.record_event("adjudicate", "sandbox_core", "blocked",
                                  f"包含禁止内容: {kw}", threat_score=0.9)
                return {"verdict": "blocked", "level": "L3",
                        "reason": f"包含禁止内容: {kw}", "threat_score": 0.9}

        # 通过
        self.record_event("adjudicate", "sandbox_core", "passed",
                          "安全内容", threat_score=0.1)
        return {"verdict": "passed", "level": "L1",
                "reason": "安全内容", "threat_score": 0.1}
    
    def record_event(self, event_type: str, source: str, verdict: str, 
                     reason: str = "", threat_score: float = 0.0, details: dict | None = None):
        """记录安全事件到历史"""
        with self._lock:
            event = {
                "timestamp": time.time(),
                "event_type": event_type,
                "source": source,
                "verdict": verdict,
                "reason": reason,
                "threat_score": threat_score,
                "details": details or {},
            }
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history.pop(0)
            
            if verdict == "blocked":
                self._blocked_count += 1
                self._threat_level = min(1.0, self._threat_level + 0.1)
            else:
                self._passed_count += 1
                self._threat_level = max(0.0, self._threat_level - 0.02)
    
    def get_stats(self) -> dict[str, Any]:
        """获取安全统计"""
        with self._lock:
            return {
                "blocked_count": self._blocked_count,
                "passed_count": self._passed_count,
                "threat_level": round(self._threat_level, 3),
                "history_size": len(self._event_history),
                "recent_events": self._event_history[-5:],
            }