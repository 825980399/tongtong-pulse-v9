# -*- coding: utf-8 -*-
"""
OrganCoordinator.py —— 器官协调器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 多器官协作协调与冲突解决
机制: 基于OrganCoordinator类实现，包含10个核心方法
定位: 器官管理层

⚠️ @deprecated (157-D C-3 自我感知三件 / Q157-3 已裁):
    本模块为「自我感知三件套」之一，与 SelfModel / Interoception 同属冗余未接线能力——
    全仓无任何外部 import / 实例化（仅模块内自引用单例）。决策：③ 弃用标注封存（原定②删除或③，
    为规避删除导致的潜在测试/动态引用断裂，选③标注）。复活须走专门批，禁止新代码 import 本模块。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Self

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底



class OrganCoordinator(SilentLogMixin):
    """器官协调器单例"""

    _instance: OrganCoordinator | None = None
    _lock = threading.Lock()

    def __new__(cls) -> Self:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._tasks: dict[str, dict[str, Any]] = {}
        self._task_lock = threading.Lock()
        self._listeners: dict[str, list[Callable]] = {}
        self._listener_lock = threading.Lock()

    # ========== 协同任务管理 ==========

    def create_task(
        self,
        task_id: str,
        participant_organs: list[str],
        timeout_seconds: float = 30.0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """创建协同任务

        Args:
            task_id: 任务唯一标识
            participant_organs: 参与器官列表
            timeout_seconds: 超时时间（秒）
            metadata: 任务元数据

        Returns:
            True=创建成功, False=任务已存在
        """
        with self._task_lock:
            if task_id in self._tasks:
                return False
            self._tasks[task_id] = {
                "participants": {o: "pending" for o in participant_organs},
                "created_at": time.time(),
                "timeout": timeout_seconds,
                "metadata": metadata or {},
                "status": "waiting",
                "results": {},
            }
        return True

    def report_ready(self, task_id: str, organ_name: str, result: Any = None) -> bool:
        """器官报告就绪/完成

        Args:
            task_id: 任务ID
            organ_name: 器官名
            result: 该器官的处理结果

        Returns:
            True=报告成功, False=任务不存在或器官未参与
        """
        with self._task_lock:
            task = self._tasks.get(task_id)
            if not task or organ_name not in task["participants"]:
                return False
            task["participants"][organ_name] = "ready"
            if result is not None:
                task["results"][organ_name] = result
            # 检查是否所有参与器官都就绪
            if all(s == "ready" for s in task["participants"].values()):
                task["status"] = "completed"
                self._notify_task_completed(task_id, task)
        return True

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        """获取任务状态"""
        with self._task_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return {
                "status": task["status"],
                "ready_count": sum(1 for s in task["participants"].values() if s == "ready"),
                "total_count": len(task["participants"]),
                "pending_organs": [o for o, s in task["participants"].items() if s == "pending"],
                "results": task["results"],
                "metadata": task["metadata"],
                "elapsed": time.time() - task["created_at"],
            }

    def wait_for_task(
        self, task_id: str, timeout: float | None = None
    ) -> dict[str, Any] | None:
        """阻塞等待任务完成（带超时）"""
        deadline = time.time() + (timeout or 30.0)
        while time.time() < deadline:
            status = self.get_task_status(task_id)
            if not status or status["status"] == "completed":
                return status
            time.sleep(0.1)
        return self.get_task_status(task_id)

    def cancel_task(self, task_id: str, reason: str = "cancelled") -> bool:
        """取消任务"""
        with self._task_lock:
            task = self._tasks.pop(task_id, None)
            if not task:
                return False
            task["status"] = "cancelled"
            task["cancel_reason"] = reason
        return True

    def cleanup_expired(self) -> int:
        """清理超时任务，返回清理数量"""
        now = time.time()
        expired = []
        with self._task_lock:
            for tid, task in self._tasks.items():
                if task["status"] == "waiting" and now - task["created_at"] > task["timeout"]:
                    expired.append(tid)
            for tid in expired:
                del self._tasks[tid]
        return len(expired)

    # ========== 事件监听 ==========

    def on_task_completed(self, task_id_prefix: str, callback: Callable) -> None:
        """注册任务完成监听器

        Args:
            task_id_prefix: 任务ID前缀（空字符串=监听所有）
            callback: 回调函数 callback(task_id, status_dict)
        """
        with self._listener_lock:
            self._listeners.setdefault(task_id_prefix, []).append(callback)

    def _notify_task_completed(self, task_id: str, task: dict) -> None:
        """通知任务完成（调用方需持有 _task_lock）"""
        status = {
            "status": "completed",
            "results": task["results"],
            "metadata": task["metadata"],
            "participants": list(task["participants"].keys()),
        }
        with self._listener_lock:
            for prefix, callbacks in self._listeners.items():
                if task_id.startswith(prefix):
                    for cb in callbacks:
                        try:
                            cb(task_id, status)
                        except Exception as e:
                            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ========== 统计 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取协调器统计"""
        with self._task_lock:
            waiting = sum(1 for t in self._tasks.values() if t["status"] == "waiting")
            completed = sum(1 for t in self._tasks.values() if t["status"] == "completed")
        return {
            "total_tasks": len(self._tasks),
            "waiting": waiting,
            "completed": completed,
            "listeners": sum(len(v) for v in self._listeners.values()),
        }


