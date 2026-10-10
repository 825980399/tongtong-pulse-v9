# -*- coding: utf-8 -*-
"""
ContextSnapshot.py —— 上下文快照

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 对话上下文的快照保存与恢复
机制: 大型模块（1112行），包含1个类、10个核心方法，采用分层架构实现
定位: 记忆管理层
"""

import hashlib
import json
import os
import tempfile
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus.logger import get_module_logger
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus.mnemosyne.episode_tag import tag_dialog_entry  # ★第181批 刀4：对话记忆情景标签

_module_logger = get_module_logger("ContextSnapshot")


class ContextSnapshot(SilentLogMixin):
    """
    对话上下文持久化管理器。
    
    管理五种上下文数据的独立快照：
    1. conversation_memory —— 对话记忆（按用户分区，隐私保护）
    2. inference_trace —— 推理链（全量持久化，加载时容量保护）
    3. search_experience —— 搜索经验（与肾脏联动遗忘）
    4. learning_goals —— 学习目标与等待队列
    5. code_progress —— 代码理解进度
    """
    def __init__(self, base_dir: str = "data/context"):
        self._base_dir = base_dir
        # ★P1-9修复：RLock允许同一线程在save_all的锁内再调用单类型方法时不会死锁
        self._lock = threading.RLock()
        
        # 各快照文件路径
        self._conversation_path = os.path.join(base_dir, "conversation_memory.json")
        self._inference_trace_path = os.path.join(base_dir, "inference_trace.json")
        self._search_experience_path = os.path.join(base_dir, "search_experience.json")
        self._learning_goals_path = os.path.join(base_dir, "learning_goals.json")
        self._code_progress_path = os.path.join(base_dir, "code_progress.json")
        
        # 容量配置
        self._max_conversation_per_user = 50       # 每个用户最多保留50条对话记忆
        self._max_conversation_total = 500          # 所有用户总计最多500条
        self._max_inference_trace_load = 500         # 加载时最多恢复500条推理链
        self._max_inference_trace_save = 1000        # 保存时最多保留1000条
        self._max_search_experience = 100            # 搜索经验最多100条
        self._max_learning_goals_queue = 3           # 等待队列上限
        
        # 健康度遗忘阈值
        self._conversation_health_threshold = 0.3    # 对话记忆健康度低于此值淘汰
        self._inference_health_threshold = 0.25      # 推理链健康度低于此值淘汰
        self._search_success_threshold = 0.3          # 搜索成功率低于此值且尝试≥3次淘汰
        self._search_min_attempts = 3                 # 最少尝试次数
    
    # 对话记忆持久化
    
    def save_conversation_memory(self, memory_data: dict[str, Any],
                                  self_awareness=None) -> bool:
        """
        保存对话记忆到独立快照。
        """
        # ★P1-9修复：加锁保护整个保存流程
        with self._lock:
            try:
                # 加载已有快照（如果存在）
                existing = self._load_json_safe(self._conversation_path, {"users": {}})
                
                # 获取内存中的对话记忆列表
                memories = memory_data.get("memories", [])
                _module_logger.debug(f"保存入参: 记忆条数={len(memories)}, "
                      f"user_awareness={'已注入' if self_awareness else '未注入'}, "
                      f"用户列表={list({m.get('user_name', '?') for m in memories})}")
                if not memories:
                    # 保护：如果内存为空但磁盘已有数据，不覆盖
                    _existing = self._load_json_safe(self._conversation_path, {"users": {}})
                    _existing_total = sum(
                        len(u.get("memories", [])) for u in _existing.get("users", {}).values()
                    )
                    if _existing_total > 0:
                        return True  # 保留已有数据，不覆盖
                    return True  # 无数据，无需保存
                
                # 按用户分组
                user_groups = {}
                for mem in memories:
                    user_name = mem.get("user_name", "未知")
                    if user_name not in user_groups:
                        user_groups[user_name] = []
                    user_groups[user_name].append(mem)
                
                # 对每个用户做隐私判断并持久化
                for user_name, user_memories in user_groups.items():
                    should_persist = self._should_persist_conversation(
                        user_name, self_awareness
                    )
                    
                    _rel_type = self._get_relationship_type(user_name, self_awareness)
                    _module_logger.debug(f"保存检查: user={user_name}, "
                          f"关系={_rel_type}, 记忆条数={len(user_memories)}, "
                          f"持久化={should_persist}")
                    
                    if not should_persist:
                        continue
                    
                    relationship_type = _rel_type
                    
                    if user_name not in existing["users"]:
                        existing["users"][user_name] = {
                            "relationship_type": relationship_type,
                            "memories": [],
                            "total_count": 0,
                            "last_updated": time.time(),
                        }
                    
                    user_partition = existing["users"][user_name]
                    
                    existing_ids = {
                        m.get("question", "") + str(m.get("timestamp", 0))
                        for m in user_partition["memories"]
                    }
                    
                    for mem in user_memories:
                        mem_id = mem.get("question", "") + str(mem.get("timestamp", 0))
                        if mem_id not in existing_ids:
                            # ★第181批 刀4：批量落盘同样自动打情景标签（同口径）
                            tag_dialog_entry(mem, participants=[user_name])
                            filtered_mem = self._filter_memory_by_privacy(
                                mem, relationship_type
                            )
                            user_partition["memories"].append(filtered_mem)
                            existing_ids.add(mem_id)
                    
                    if len(user_partition["memories"]) > self._max_conversation_per_user:
                        user_partition["memories"].sort(
                            key=lambda m: m.get("timestamp", 0), reverse=True
                        )
                        user_partition["memories"] = user_partition["memories"][
                            :self._max_conversation_per_user
                        ]
                    
                    user_partition["total_count"] = len(user_partition["memories"])
                    user_partition["last_updated"] = time.time()
                    user_partition["relationship_type"] = relationship_type
                
                total_memories = sum(
                    len(u.get("memories", [])) for u in existing["users"].values()
                )
                if total_memories > self._max_conversation_total:
                    self._trim_global_conversation_capacity(existing)
                
                existing["global_config"] = {
                    "max_per_user": self._max_conversation_per_user,
                    "max_total": self._max_conversation_total,
                    "created_at": existing.get("global_config", {}).get(
                        "created_at", time.strftime("%Y-%m-%dT%H:%M:%S")
                    ),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                existing["version"] = "v1.0"
                
                _total_saved = sum(
                    len(u.get("memories", [])) for u in existing.get("users", {}).values()
                )
                if _total_saved == 0:
                    _existing = self._load_json_safe(self._conversation_path, {"users": {}})
                    _existing_total = sum(
                        len(u.get("memories", [])) for u in _existing.get("users", {}).values()
                    )
                    if _existing_total > 0:
                        return True
                return self._atomic_write(self._conversation_path, existing)
                
            except Exception as e:
                _module_logger.error(f"对话记忆保存失败: {e}")
                return False
    def append_conversation_memory(self, memory_entry: dict[str, Any],
                                    self_awareness=None) -> bool:
        """
        实时追加单条对话记忆到持久化文件。
        """
        # ★P1-9修复：加锁保护
        with self._lock:
            try:
                user_name = memory_entry.get("user_name", "未知")
                
                should_persist = self._should_persist_conversation(user_name, self_awareness)
                if not should_persist:
                    return False
                
                existing = self._load_json_safe(self._conversation_path, {"users": {}})
                
                relationship_type = self._get_relationship_type(user_name, self_awareness)
                
                if user_name not in existing["users"]:
                    existing["users"][user_name] = {
                        "relationship_type": relationship_type,
                        "memories": [],
                        "total_count": 0,
                        "last_updated": time.time(),
                    }
                
                user_partition = existing["users"][user_name]
                
                mem_id = memory_entry.get("question", "") + str(memory_entry.get("timestamp", 0))
                for m in user_partition["memories"]:
                    if m.get("question", "") + str(m.get("timestamp", 0)) == mem_id:
                        return True  # 已存在，跳过
                
                # ★第181批 刀4：对话记忆写入前自动打情景标签（幂等，已有则不覆盖）
                tag_dialog_entry(memory_entry, participants=[user_name])
                user_partition["memories"].append(memory_entry)
                
                if len(user_partition["memories"]) > self._max_conversation_per_user:
                    user_partition["memories"].sort(
                        key=lambda m: m.get("timestamp", 0), reverse=True
                    )
                    user_partition["memories"] = user_partition["memories"][
                        :self._max_conversation_per_user
                    ]
                
                user_partition["total_count"] = len(user_partition["memories"])
                user_partition["last_updated"] = time.time()
                user_partition["relationship_type"] = relationship_type
                
                return self._atomic_write(self._conversation_path, existing)
                
            except Exception as e:
                _module_logger.error(f"对话记忆追加失败: {e}")
                return False
    def load_conversation_memory(self, user_name: str | None = None,
                                  self_awareness=None) -> dict[str, Any]:
        """
        加载对话记忆。
        
        Args:
            user_name: 要加载的用户名（None=加载所有用户）
            self_awareness: PulseSelfAwareness实例
        
        Returns:
            {"memories": [...], "user_name": str, "total": int}
        """
        data = self._load_json_safe(self._conversation_path, {"users": {}})
        
        if user_name is None:
            # 加载所有用户的记忆摘要（不含具体内容）
            result = {"users": {}, "total": 0}
            for uname, partition in data.get("users", {}).items():
                result["users"][uname] = {
                    "relationship_type": partition.get("relationship_type", "stranger"),
                    "total_count": partition.get("total_count", 0),
                    "last_updated": partition.get("last_updated", 0),
                }
                result["total"] += partition.get("total_count", 0)
            return result
        
        # 加载特定用户的记忆
        user_partition = data.get("users", {}).get(user_name, {})
        memories = user_partition.get("memories", [])
        
        # 隐私校验：确认请求者有权访问此用户的记忆
        if self_awareness:
            requester_type = self._get_relationship_type(user_name, self_awareness)
            if requester_type in ("stranger", "acquaintance"):
                return {"memories": [], "user_name": user_name, "total": 0,
                        "reason": "隐私保护：访客对话不持久化"}
        
        return {
            "memories": memories,
            "user_name": user_name,
            "total": len(memories),
            "relationship_type": user_partition.get("relationship_type", "stranger"),
        }
    
    def query_conversation(self, user_name: str | None = None,
                           keywords: list[str] | None = None,
                           time_start: float | None = None,
                           time_end: float | None = None,
                           min_importance: float = 0.0,
                           sort_by: str = "timestamp",
                           limit: int = 20) -> list[dict[str, Any]]:
        """
        多维度检索对话记忆。
        
        Args:
            user_name: 按用户过滤（None=所有用户）
            keywords: 按关键词过滤（匹配问题和答案中的关键词）
            time_start: 时间范围起始（Unix时间戳）
            time_end: 时间范围结束
            min_importance: 最低重要性阈值
            sort_by: 排序方式 "timestamp" / "importance" / "relevance"
            limit: 返回上限
        
        Returns:
            匹配的对话记忆列表
        """
        data = self._load_json_safe(self._conversation_path, {"users": {}})
        results = []
        
        # 收集所有匹配的记忆
        for uname, partition in data.get("users", {}).items():
            if user_name and uname != user_name:
                continue
            
            for mem in partition.get("memories", []):
                # 时间范围过滤
                ts = mem.get("timestamp", 0)
                if time_start and ts < time_start:
                    continue
                if time_end and ts > time_end:
                    continue
                
                # 重要性过滤
                importance = mem.get("importance", 0.5)
                if importance < min_importance:
                    continue
                
                # 关键词过滤
                if keywords:
                    question = mem.get("question", "")
                    answer = mem.get("answer_preview", "")
                    mem_kws = mem.get("keywords", [])
                    search_text = question + " " + answer + " " + " ".join(mem_kws)
                    
                    if not any(kw in search_text for kw in keywords):
                        continue
                
                results.append({
                    **mem,
                    "user_name": uname,
                })
        
        # 排序
        if sort_by == "importance":
            results.sort(key=lambda m: m.get("importance", 0.5), reverse=True)
        elif sort_by == "relevance" and keywords:
            # 按关键词命中数量排序
            def relevance_score(m):
                text = m.get("question", "") + " " + m.get("answer_preview", "")
                return sum(1 for kw in keywords if kw in text)
            results.sort(key=relevance_score, reverse=True)
        else:
            # 默认按时间戳倒序
            results.sort(key=lambda m: m.get("timestamp", 0), reverse=True)
        
        return results[:limit]
    
    # 推理链持久化
    
    def save_inference_trace(self, trace_data: list[dict[str, Any]]) -> bool:
        """
        保存推理链到独立快照。
        """
        # ★P1-9修复：加锁保护
        with self._lock:
            if not trace_data:
                return True
            
            try:
                if len(trace_data) > self._max_inference_trace_save:
                    trace_data = trace_data[-self._max_inference_trace_save:]
                
                checksum = self._compute_checksum(trace_data)
                
                snapshot = {
                    "version": "v1.0",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "total_count": len(trace_data),
                    "node_list_checksum": checksum,
                    "traces": self._simplify_traces_for_storage(trace_data),
                }
                
                return self._atomic_write(self._inference_trace_path, snapshot)
                
            except Exception as e:
                _module_logger.error(f"推理链保存失败: {e}")
                return False
    def load_inference_trace(self) -> list[dict[str, Any]]:
        """
        加载推理链（容量保护：最多恢复N条）。
        
        Returns:
            推理链列表
        """
        data = self._load_json_safe(self._inference_trace_path, {"traces": []})
        traces = data.get("traces", [])
        
        # 容量保护
        if len(traces) > self._max_inference_trace_load:
            traces = traces[-self._max_inference_trace_load:]
        
        return traces
    
    def _simplify_traces_for_storage(self, traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """精简推理链用于存储（去除冗余字段，保留核心信息）"""
        simplified = []
        for t in traces:
            simplified.append({
                "timestamp": t.get("timestamp", 0),
                "question": t.get("question", "")[:120],
                "answer": t.get("answer", "")[:120],
                "method": t.get("method", ""),
                "confidence": t.get("confidence", 0),
                "user_name": t.get("user_name", ""),
                "duration": t.get("duration", 0),
                "complexity": t.get("complexity", 0),
                "tuning_hint": t.get("tuning_hint", ""),
            })
        return simplified
    
    # 搜索经验持久化
    
    def save_search_experience(self, experience_data: dict[str, dict[str, Any]]) -> bool:
        """
        保存搜索经验到独立快照。
        """
        # ★P1-9修复：加锁保护
        with self._lock:
            if not experience_data:
                return True
            
            try:
                if len(experience_data) > self._max_search_experience:
                    sorted_keys = sorted(
                        experience_data.keys(),
                        key=lambda k: experience_data[k].get("total_searches", 0),
                        reverse=True
                    )
                    trimmed = {
                        k: experience_data[k]
                        for k in sorted_keys[:self._max_search_experience]
                    }
                    experience_data = trimmed
                
                snapshot = {
                    "version": "v1.0",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "total_entries": len(experience_data),
                    "experiences": self._simplify_experiences_for_storage(experience_data),
                }
                
                return self._atomic_write(self._search_experience_path, snapshot)
                
            except Exception as e:
                _module_logger.error(f"搜索经验保存失败: {e}")
                return False
    def load_search_experience(self) -> dict[str, dict[str, Any]]:
        """
        加载搜索经验。
        
        Returns:
            搜索经验字典
        """
        data = self._load_json_safe(self._search_experience_path, {"experiences": {}})
        return data.get("experiences", {})
    
    def _simplify_experiences_for_storage(self, experiences: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """精简搜索经验用于存储"""
        simplified = {}
        for key, exp in experiences.items():
            simplified[key] = {
                "total_searches": exp.get("total_searches", 0),
                "successful_searches": exp.get("successful_searches", 0),
                "last_result": exp.get("last_result", ""),
                "best_tool": exp.get("best_tool", "inner_world"),
                "content_quality_stats": exp.get("content_quality_stats", {}),
                "avg_content_score": exp.get("avg_content_score", 0.0),
                "last_updated": exp.get("last_updated", time.time()),
            }
        return simplified
    
    # 学习目标持久化
    
    def save_learning_goals(self, active_goal: dict[str, Any] | None,
                            goal_queue: list[dict[str, Any]]) -> bool:
        """
        保存学习目标与等待队列到独立快照。
        """
        # ★P1-9修复：加锁保护
        with self._lock:
            try:
                snapshot = {
                    "version": "v1.0",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "active_goal": self._simplify_goal(active_goal) if active_goal else None,
                    "goal_queue": [
                        self._simplify_goal(g) for g in goal_queue[:self._max_learning_goals_queue]
                    ],
                    "max_queue": self._max_learning_goals_queue,
                }
                
                return self._atomic_write(self._learning_goals_path, snapshot)
                
            except Exception as e:
                _module_logger.error(f"学习目标保存失败: {e}")
                return False
    def load_learning_goals(self) -> dict[str, Any]:
        """
        加载学习目标与等待队列。
        
        Returns:
            {"active_goal": dict/None, "goal_queue": list}
        """
        data = self._load_json_safe(self._learning_goals_path, {})
        return {
            "active_goal": data.get("active_goal"),
            "goal_queue": data.get("goal_queue", []),
        }
    
    def _simplify_goal(self, goal: dict[str, Any]) -> dict[str, Any]:
        """精简学习目标用于存储"""
        return {
            "target_area": goal.get("target_area", ""),
            "reason": goal.get("reason", ""),
            "action": goal.get("action", ""),
            "hint": goal.get("hint", ""),
            "started_at": goal.get("started_at", 0),
            "nodes_at_start": goal.get("nodes_at_start", 0),
            "queued_at": goal.get("queued_at", 0),
            "domain": goal.get("domain", ""),
        }
    
    # 代码理解进度持久化
    
    def save_code_progress(self, progress_data: dict[str, Any]) -> bool:
        """
        保存代码理解进度到独立快照。
        """
        # ★P1-9修复：加锁保护
        with self._lock:
            if not progress_data:
                return True
            
            try:
                snapshot = {
                    "version": "v1.0",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "understood": progress_data.get("understood", 0),
                    "total_methods": progress_data.get("total_methods", 0),
                    "organs_list": progress_data.get("organs_list", []),
                }
                
                return self._atomic_write(self._code_progress_path, snapshot)
                
            except Exception as e:
                _module_logger.error(f"代码进度保存失败: {e}")
                return False
    def load_code_progress(self) -> dict[str, Any]:
        """
        加载代码理解进度。
        
        Returns:
            代码进度字典
        """
        data = self._load_json_safe(self._code_progress_path, {})
        return {
            "understood": data.get("understood", 0),
            "total_methods": data.get("total_methods", 0),
            "organs_list": data.get("organs_list", []),
        }
    
    # 统一保存/加载入口
    
    def save_all(self, inner_world=None, self_awareness=None) -> dict[str, bool]:
        """
        保存所有上下文数据。
        
        Args:
            inner_world: PulseInnerWorld实例（用于获取各类上下文数据）
            self_awareness: PulseSelfAwareness实例（用于隐私判断）
        
        Returns:
            各类型保存结果 {"conversation": True, "inference_trace": True, ...}
        """
        results = {
            "conversation_memory": False,
            "inference_trace": False,
            "search_experience": False,
            "learning_goals": False,
            "code_progress": False,
        }
        
        if inner_world is None:
            return results
        
        with self._lock:
            # 对话记忆
            _memories = inner_world.get_conversation_memory()
            if _memories:
                results["conversation_memory"] = self.save_conversation_memory(
                    {"memories": _memories},
                    self_awareness
                )
            
            # 推理链
            _trace = inner_world.get_inference_trace()
            if _trace:
                results["inference_trace"] = self.save_inference_trace(_trace)
            
            # 搜索经验
            _exp = inner_world.get_search_experience_all()
            if _exp:
                results["search_experience"] = self.save_search_experience(_exp)
            
            # 学习目标
            _goal = inner_world.get_active_learning_goal()
            if _goal:
                results["learning_goals"] = self.save_learning_goals(
                    _goal,
                    inner_world.get_learning_goal_queue()
                )
            
            # 代码理解进度
            _progress = inner_world.get_code_understanding_progress()
            if _progress:
                results["code_progress"] = self.save_code_progress(_progress)
        
        return results
    
    def load_all(self) -> dict[str, Any]:
        """
        加载所有上下文数据。
        
        Returns:
            各类型数据字典
        """
        with self._lock:
            # 加载完整对话记忆（含所有用户的具体记忆内容）
            # 而不是 load_conversation_memory() 无参返回的摘要结构
            _conv_data = self._load_json_safe(
                self._conversation_path, {"users": {}}
            )
            
            return {
                "conversation_memory": _conv_data,
                "inference_trace": self.load_inference_trace(),
                "search_experience": self.load_search_experience(),
                "learning_goals": self.load_learning_goals(),
                "code_progress": self.load_code_progress(),
            }
    # 肾脏联动：健康度评估与遗忘
    
    def assess_context_health(self) -> dict[str, Any]:
        """
        【肾脏联动】统一评估所有上下文数据的健康度。
        
        供PulseKidney._on_purge_check调用。
        
        Returns:
            {
                "conversation_low_health": [...],  # 低健康度对话记忆ID列表
                "inference_low_health": [...],     # 低健康度推理链索引列表
                "search_low_health": [...],         # 低健康度搜索经验key列表
                "summary": "评估摘要"
            }
        """
        results = {
            "conversation_low_health": [],
            "inference_low_health": [],
            "search_low_health": [],
            "summary": "",
        }
        
        # 评估对话记忆健康度
        data = self._load_json_safe(self._conversation_path, {"users": {}})
        for uname, partition in data.get("users", {}).items():
            for mem in partition.get("memories", []):
                health = self._assess_memory_health(mem)
                if health < self._conversation_health_threshold:
                    results["conversation_low_health"].append({
                        "user_name": uname,
                        "question": mem.get("question", "")[:60],
                        "health": round(health, 2),
                        "timestamp": mem.get("timestamp", 0),
                    })
        
        # 评估推理链健康度
        trace_data = self._load_json_safe(self._inference_trace_path, {"traces": []})
        for i, trace in enumerate(trace_data.get("traces", [])):
            health = self._assess_trace_health(trace)
            if health < self._inference_health_threshold:
                results["inference_low_health"].append({
                    "index": i,
                    "question": trace.get("question", "")[:60],
                    "health": round(health, 2),
                    "timestamp": trace.get("timestamp", 0),
                })
        
        # 评估搜索经验健康度
        exp_data = self._load_json_safe(self._search_experience_path, {"experiences": {}})
        for key, exp in exp_data.get("experiences", {}).items():
            success_rate = self._calculate_success_rate(exp)
            total = exp.get("total_searches", 0)
            if success_rate < self._search_success_threshold and total >= self._search_min_attempts:
                results["search_low_health"].append({
                    "key": key,
                    "success_rate": round(success_rate, 2),
                    "total_searches": total,
                })
        
        # 生成摘要
        total_low = (
            len(results["conversation_low_health"]) +
            len(results["inference_low_health"]) +
            len(results["search_low_health"])
        )
        if total_low > 0:
            results["summary"] = (
                f"发现{total_low}个低健康度上下文条目"
                f"（对话{len(results['conversation_low_health'])}条、"
                f"推理链{len(results['inference_low_health'])}条、"
                f"搜索经验{len(results['search_low_health'])}条）"
            )
        else:
            results["summary"] = "所有上下文数据健康度正常"
        
        return results
    
    def forget_low_quality_context(self, health_report: dict[str, Any]) -> dict[str, int]:
        """
        【肾脏联动】批量遗忘低健康度的上下文条目。
        
        Args:
            health_report: assess_context_health的返回结果
        
        Returns:
            各类型遗忘数量
        """
        removed = {
            "conversation": 0,
            "inference_trace": 0,
            "search_experience": 0,
        }
        
        with self._lock:
            # 遗忘低健康度对话记忆
            low_conv = health_report.get("conversation_low_health", [])
            if low_conv:
                removed["conversation"] = self._remove_conversation_memories(low_conv)
            
            # 遗忘低健康度推理链
            low_trace = health_report.get("inference_low_health", [])
            if low_trace:
                removed["inference_trace"] = self._remove_inference_traces(low_trace)
            
            # 遗忘低健康度搜索经验
            low_search = health_report.get("search_low_health", [])
            if low_search:
                removed["search_experience"] = self._remove_search_experiences(low_search)
        
        return removed
    
    # 健康度评估算法
    
    def _assess_memory_health(self, memory: dict[str, Any]) -> float:
        """
        评估单条对话记忆的健康度（0.0-1.0）。
        
        公式：激活频率×0.3 + 关键词丰富度×0.2 + 情感深度×0.3 + 时间衰减×0.2
        """
        score = 0.0
        
        # 1. 激活频率（被检索/引用的次数）
        query_count = memory.get("query_count", 0)
        if query_count >= 3:
            score += 0.3
        elif query_count >= 1:
            score += 0.15
        
        # 2. 关键词丰富度（关键词数量和质量）
        keywords = memory.get("keywords", [])
        if len(keywords) >= 4:
            score += 0.2
        elif len(keywords) >= 2:
            score += 0.1
        
        # 3. 情感深度（情感色彩越强，记忆越有价值）
        emotional_tone = memory.get("emotional_tone", "neutral")
        tone_scores = {
            "positive": 0.3,
            "concerned": 0.25,
            "negative": 0.2,
            "neutral": 0.1,
        }
        score += tone_scores.get(emotional_tone, 0.1)
        
        # 4. 时间衰减（越久远的记忆，健康度越低）
        timestamp = memory.get("timestamp", 0)
        if timestamp > 0:
            age_days = (time.time() - timestamp) / 86400.0
            if age_days < 1:
                score += 0.2
            elif age_days < 7:
                score += 0.15
            elif age_days < 30:
                score += 0.1
            elif age_days < 90:
                score += 0.05
        
        return min(1.0, score)
    
    def _assess_trace_health(self, trace: dict[str, Any]) -> float:
        """
        评估单条推理链的健康度（0.0-1.0）。
        
        公式：置信度×0.5 + 方法多样性×0.2 + 时间衰减×0.3
        """
        score = 0.0
        
        # 1. 置信度
        confidence = trace.get("confidence", 0)
        score += confidence * 0.5
        
        # 2. 方法多样性（非缓存方法加分）
        method = trace.get("method", "")
        if method and "cache" not in method:
            score += 0.2
        elif method and "knowledge" in method:
            score += 0.15
        
        # 3. 时间衰减
        timestamp = trace.get("timestamp", 0)
        if timestamp > 0:
            age_days = (time.time() - timestamp) / 86400.0
            if age_days < 1:
                score += 0.3
            elif age_days < 7:
                score += 0.2
            elif age_days < 30:
                score += 0.1
            elif age_days < 90:
                score += 0.05
        
        return min(1.0, score)
    
    def _calculate_success_rate(self, experience: dict[str, Any]) -> float:
        """计算搜索经验的成功率"""
        total = experience.get("total_searches", 0)
        if total == 0:
            return 0.0
        successful = experience.get("successful_searches", 0)
        return successful / total
    # 遗忘操作
    
    def _remove_conversation_memories(self, low_health_items: list[dict[str, Any]]) -> int:
        """移除低健康度对话记忆"""
        data = self._load_json_safe(self._conversation_path, {"users": {}})
        removed_count = 0
        
        for item in low_health_items:
            user_name = item.get("user_name", "")
            question = item.get("question", "")
            if user_name in data.get("users", {}):
                partition = data["users"][user_name]
                original_count = len(partition.get("memories", []))
                partition["memories"] = [
                    m for m in partition.get("memories", [])
                    if m.get("question", "") != question
                ]
                removed_count += original_count - len(partition["memories"])
                partition["total_count"] = len(partition["memories"])
        
        if removed_count > 0:
            self._atomic_write(self._conversation_path, data)
        
        return removed_count
    
    def _remove_inference_traces(self, low_health_items: list[dict[str, Any]]) -> int:
        """移除低健康度推理链"""
        data = self._load_json_safe(self._inference_trace_path, {"traces": []})
        traces = data.get("traces", [])
        
        # 按索引从大到小排序，避免删除时索引错位
        indices_to_remove = sorted(
            [item.get("index", -1) for item in low_health_items],
            reverse=True
        )
        
        for idx in indices_to_remove:
            if 0 <= idx < len(traces):
                traces.pop(idx)
        
        data["traces"] = traces
        data["total_count"] = len(traces)
        self._atomic_write(self._inference_trace_path, data)
        
        return len(indices_to_remove)
    
    def _remove_search_experiences(self, low_health_items: list[dict[str, Any]]) -> int:
        """移除低健康度搜索经验"""
        data = self._load_json_safe(self._search_experience_path, {"experiences": {}})
        experiences = data.get("experiences", {})
        removed_count = 0
        
        for item in low_health_items:
            key = item.get("key", "")
            if key in experiences:
                del experiences[key]
                removed_count += 1
        
        if removed_count > 0:
            data["total_entries"] = len(experiences)
            self._atomic_write(self._search_experience_path, data)
        
        return removed_count

    # 隐私保护
    
    def _should_persist_conversation(self, user_name: str,
                                      self_awareness=None) -> bool:
        """
        判断是否应该持久化此用户的对话记忆。
        
        规则：
        - blood/family → 持久化
        - partner → 持久化（降级内容）
        - stranger/acquaintance → 不持久化
        - 访客/未知 → 不持久化
        """
        if not user_name or user_name in ("未知", "访客", "用户"):
            return False
        
        relationship_type = self._get_relationship_type(user_name, self_awareness)
        return relationship_type in ("blood", "family", "partner")
    
    def _get_relationship_type(self, user_name: str,
                                 self_awareness=None) -> str:
        """
        获取用户的关系类型。
        
        优先从PulseSelfAwareness获取，失败时返回stranger。
        """
        if self_awareness and hasattr(self_awareness, 'get_persona'):
            persona = self_awareness.get_persona(user_name)
            if persona:
                return persona.get("relationship_type", "stranger")
        
        # 兜底：预置用户判断
        if user_name in ("小林", "路灯"):
            return "blood"
        elif user_name == "<CREATOR_DAUGHTER>":
            return "family"
        elif user_name == "星轨":
            return "partner"
        
        return "stranger"
    
    def _filter_memory_by_privacy(self, memory: dict[str, Any],
                                   relationship_type: str) -> dict[str, Any]:
        """
        根据隐私层级过滤记忆内容。
        
        - blood/family: 完整保留，包括情感细节
        - partner: 保留关键词和摘要，去除情感细节
        """
        if relationship_type in ("blood", "family"):
            return dict(memory)  # 完整保留
        
        # partner: 降级处理
        filtered = dict(memory)
        filtered["emotional_tone"] = "neutral"  # 去除情感色彩
        if "answer_preview" in filtered:
            # 截短回答预览
            filtered["answer_preview"] = filtered["answer_preview"][:80]
        
        return filtered
    # 工具方法
    def _load_json_safe(self, path: str, default: Any) -> Any:
        """安全加载JSON文件，不存在或损坏时返回默认值"""
        if not os.path.exists(path):
            return default
        
        try:
            return safe_read_json(path, default={})
        except (json.JSONDecodeError, Exception):
            return default
    
    def _atomic_write(self, path: str, data: Any) -> bool:
        """原子写入：临时文件+os.replace，保证写入过程不损坏现有数据"""
        try:
            # 确保目录存在
            dir_path = os.path.dirname(path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)
            
            # 写入临时文件
            tmp_fd, tmp_path = tempfile.mkstemp(  # type: ignore[possibly-unbound]
                suffix=".json",
                prefix="ctx_",
                dir=dir_path or "."
            )
            
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # ★P0-1修复：os.replace本身就是原子操作，直接覆盖目标文件
            # 不需要先os.remove——删除和替换之间的时间窗口会导致数据丢失风险
            os.replace(tmp_path, path)  # type: ignore[possibly-unbound]
            
            return True
            
        except Exception as e:
            _module_logger.error(f"原子写入失败({path}): {e}")
            if 'tmp_path' in locals() and os.path.exists(tmp_path):  # type: ignore[possibly-unbound]
                try:
                    os.remove(tmp_path)  # type: ignore[possibly-unbound]
                except Exception as e:
                    self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
            return False
    
    def _compute_checksum(self, data: Any) -> str:
        """计算数据的SHA256校验和"""
        json_str = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest()[:16]
    
    def _trim_global_conversation_capacity(self, data: dict[str, Any]):
        """全局容量保护：超出上限时淘汰最旧的记忆"""
        # 收集所有记忆及其来源
        all_memories = []
        for uname, partition in data.get("users", {}).items():
            for mem in partition.get("memories", []):
                all_memories.append({
                    "user_name": uname,
                    "memory": mem,
                })
        
        # 按时间戳排序，最旧的在前面
        all_memories.sort(key=lambda m: m["memory"].get("timestamp", 0))
        
        # 计算需要淘汰的数量
        total = len(all_memories)
        excess = total - self._max_conversation_total
        if excess <= 0:
            return
        
        # 淘汰最旧的
        to_remove = all_memories[:excess]
        for item in to_remove:
            uname = item["user_name"]
            mem = item["memory"]
            if uname in data.get("users", {}):
                partition = data["users"][uname]
                partition["memories"] = [
                    m for m in partition.get("memories", [])
                    if m.get("question", "") != mem.get("question", "")
                    or m.get("timestamp", 0) != mem.get("timestamp", 0)
                ]
                partition["total_count"] = len(partition["memories"])
    
    def get_stats(self) -> dict[str, Any]:
        """获取各快照的统计信息"""
        conv_data = self._load_json_safe(self._conversation_path, {"users": {}})
        trace_data = self._load_json_safe(self._inference_trace_path, {"traces": []})
        exp_data = self._load_json_safe(self._search_experience_path, {"experiences": {}})
        goals_data = self._load_json_safe(self._learning_goals_path, {})
        code_data = self._load_json_safe(self._code_progress_path, {})
        
        return {
            "conversation_memory": {
                "total_users": len(conv_data.get("users", {})),
                "total_memories": sum(
                    len(u.get("memories", []))
                    for u in conv_data.get("users", {}).values()
                ),
                "file_exists": os.path.exists(self._conversation_path),
            },
            "inference_trace": {
                "total_traces": len(trace_data.get("traces", [])),
                "file_exists": os.path.exists(self._inference_trace_path),
            },
            "search_experience": {
                "total_entries": len(exp_data.get("experiences", {})),
                "file_exists": os.path.exists(self._search_experience_path),
            },
            "learning_goals": {
                "has_active_goal": goals_data.get("active_goal") is not None,
                "queue_size": len(goals_data.get("goal_queue", [])),
                "file_exists": os.path.exists(self._learning_goals_path),
            },
            "code_progress": {
                "understood": code_data.get("understood", 0),
                "total_methods": code_data.get("total_methods", 0),
                "file_exists": os.path.exists(self._code_progress_path),
            },
        }
# ========== 模块级单例 ==========
_context_snapshot: ContextSnapshot | None = None
_context_snapshot_lock = threading.Lock()


def get_context_snapshot() -> ContextSnapshot:
    """获取ContextSnapshot单例"""
    global _context_snapshot
    if _context_snapshot is None:
        with _context_snapshot_lock:
            if _context_snapshot is None:
                _context_snapshot = ContextSnapshot()
    return _context_snapshot


def shutdown_context_snapshot() -> None:
    """★P1: 复位 ContextSnapshot 单例，满足器官零状态（规则4）。"""
    global _context_snapshot
    _inst = _context_snapshot
    _context_snapshot = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.mnemosyne.ContextSnapshot::shutdown_context_snapshot L1119")
