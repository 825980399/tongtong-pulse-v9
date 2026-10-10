# -*- coding: utf-8 -*-
"""
ReasoningExperience.py —— 推理经验

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 推理过程经验的记录与复用
机制: 基于ReasoningExperience类实现，包含10个核心方法
定位: 记忆推理层
"""
import json
import os
import re
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.data.DataAccessLayer import safe_write_json  # T-112a：复用硬化写通道
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


class ReasoningExperience(SilentLogMixin):
    """
    推理经验管理器。
    
    存储"问题结构特征 → 推理类型"的映射经验。
    当路由层无法确定推理类型时，在经验库中搜索相似问题结构。
    """
    
    def __init__(self, data_dir: str = "data/context"):
        self._data_dir = data_dir
        self._file_path = os.path.join(data_dir, "reasoning_experience.json")
        self._experiences: list[dict[str, Any]] = []
        self._save_lock = threading.RLock()  # T-112a：保存可重入锁，防止并发/递归保存互相覆盖
        self._max_experiences = 200
        self._min_confidence = 0.5  # 最低匹配置信度
        
        # ★v23.0新增：特征提取缓存
        self._feature_cache: dict[str, dict[str, Any]] = {}  # question→features
        self._feature_cache_max = 100  # 最多缓存100个问题的特征
        self._feature_cache_ttl = 300  # 缓存有效期5分钟
        
        # ★v23.0新增：特征权重提取为类常量，便于校准
        self.FEATURE_WEIGHTS = {
            "has_sequence_numbering": 1.5,
            "has_arrow_symbol": 2.0,
            "has_state_description": 1.0,
            "has_comparison_request": 1.5,
            "has_sample_list": 1.5,
            "has_analogy_signal": 2.0,
            "has_conflict_signal": 2.0,
            "has_time_evolution": 1.5,
            "has_meta_request": 1.5,
            "semicolon_count": 0.8,  # ★v23.0新增：利用数值特征
            "length": 0.5,           # ★v23.0新增：利用长度特征
            "question_type_hint": 2.0,
        }
        self._load()
    # ========== 持久化 ==========
    def _load(self):
        """从文件加载经验库"""
        if not os.path.exists(self._file_path):
            return
        try:
            with open(self._file_path, encoding='utf-8') as f:
                data = json.load(f)
                self._experiences = data.get("experiences", [])
                if len(self._experiences) > self._max_experiences:
                    self._experiences = self._experiences[-self._max_experiences:]
        except Exception as e:
            print(f"[WARNING] ReasoningExperience.py:65: {type(e).__name__}: {e}")
            self._experiences = []
    
    def _save(self):
        """保存经验库到文件（原子写入，可重入锁保护）"""
        with self._save_lock:
            try:
                dir_path = os.path.dirname(self._file_path)
                if dir_path and not os.path.exists(dir_path):
                    os.makedirs(dir_path, exist_ok=True)
                
                data = {
                    "version": "v1.0",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "total_count": len(self._experiences),
                    "experiences": self._experiences,
                }
                
                # 原子写入（经安全写通道：路径锁 + 退避重试 + 唯一 tmp + 失败清理）
                if not safe_write_json(self._file_path, data):
                    self._log(LogLevel.WARNING, "reasoning_experience.json 写入失败（需关注）")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
    # ========== 问题结构特征提取 ==========
    def extract_structural_features(self, question: str) -> dict[str, Any]:
        """
        从问题中提取结构特征（不依赖具体词汇）。
        ★v23.0优化：增加特征缓存，避免同一问题重复计算。
        """
        # ★v23.0新增：缓存检查
        _cache_key = question.strip()[:200]
        if _cache_key in self._feature_cache:
            _cached = self._feature_cache[_cache_key]
            if time.time() - _cached.get("_cached_at", 0) < self._feature_cache_ttl:
                return {k: v for k, v in _cached.items() if k != "_cached_at"}
        
        features = {
            "has_sequence_numbering": bool(
                re.search(r'(?:规则\s*\d+|第\s*[一二三四五六七八九十\d]+|^\s*[一二三四五六七八九十]\s*[、，,])', question, re.MULTILINE)
            ),
            "has_arrow_symbol": bool(re.search(r'(?:→|->|=>)', question)),
            "has_state_description": bool(
                re.search(r'\d+\s*(?:条|个|次|小时|分钟|点|%|分|天|周)', question)
            ),
            "has_comparison_request": bool(
                re.search(r'(?:是否|会不会|能否|请判断|是否满足|是否包含)', question)
            ),
            "has_sample_list": bool(
                re.search(r'(?:一|二|三|四|五)[、，,]\s*\S.+?(?:二|三|四|五)[、，,]\s*\S', question)
            ),
            "has_analogy_signal": bool(
                re.search(r'(?:类比|映射到|对应|维度.*映射|将.*类比)', question)
            ),
            "has_conflict_signal": bool(
                re.search(r'(?:冲突|矛盾|节点\s*[AB]|信任\s*\d+)', question)
            ),
            "has_time_evolution": bool(
                re.search(r'(?:连续.*运行|长期.*推演|演化.*推演|运行\s*\d+\s*天)', question)
            ),
            "has_meta_request": bool(
                re.search(r'(?:思考模式|认知策略|深度分析.*自己|分析.*推理.*方式)', question)
            ),
            "semicolon_count": question.count('；') + question.count(';'),
            "length": len(question),
            "question_type_hint": self._extract_question_type(question),
        }
        # ★v23.0新增：写入缓存
        if len(self._feature_cache) >= self._feature_cache_max:
            _oldest_key = min(
                self._feature_cache.keys(),
                key=lambda k: self._feature_cache[k].get("_cached_at", 0)
            )
            del self._feature_cache[_oldest_key]
        _cached_features = dict(features)
        _cached_features["_cached_at"] = time.time()
        self._feature_cache[_cache_key] = _cached_features
        return features
    
    def _extract_question_type(self, question: str) -> str:
        """提取疑问词类型"""
        if re.search(r'请问.*结论|请问.*推导|请问.*推理', question):
            return "conclusion_request"
        if re.search(r'请判断|是否.*满足|是否.*生成', question):
            return "judgment_request"
        if re.search(r'归纳|提炼.*规律|总结.*共同', question):
            return "induction_request"
        if re.search(r'推演|接下来|会发生|预测', question):
            return "projection_request"
        if re.search(r'复盘|回放|全流程|还原.*推导', question):
            return "replay_request"
        if re.search(r'深度分析|反思|元认知', question):
            return "meta_request"
        if re.search(r'标准化处理|冲突.*处理', question):
            return "conflict_request"
        return "general"
    # ========== 经验检索 ==========
    def search(self, question: str) -> dict[str, Any] | None:
        """
        在经验库中搜索与当前问题结构最相似的历史经验。
        
        Returns:
            {"derivation_type": str, "confidence": float, "source": str, "matched_example": str}
            如果无匹配返回None
        """
        current_features = self.extract_structural_features(question)
        
        best_match = None
        best_score = 0.0
        
        for exp in self._experiences:
            exp_features = exp.get("features", {})
            if not exp_features:
                continue
            
            score = self._calculate_feature_similarity(current_features, exp_features)
            
            # 加权：大模型确认的经验权重更高
            source = exp.get("source", "local")
            if source == "remote_api":
                score *= 1.2
            elif source == "remote_api_confirmed":
                score *= 1.5
            
            # 时间衰减：越近的经验越可信
            age_days = (time.time() - exp.get("timestamp", 0)) / 86400.0
            if age_days > 0:
                score *= max(0.7, 1.0 - age_days * 0.01)
            
            if score > best_score:
                best_score = score
                best_match = exp
        
        if best_match and best_score >= self._min_confidence:
            return {
                "derivation_type": best_match.get("derivation_type", ""),
                "confidence": round(best_score, 2),
                "source": best_match.get("source", "unknown"),
                "matched_example": best_match.get("example_question", "")[:80],
                "features_matched": self._get_matched_features(current_features, best_match.get("features", {})),
            }
        
        return None
    
    def _calculate_feature_similarity(self, current: dict[str, Any], stored: dict[str, Any]) -> float:
        """计算两个特征集的相似度（0.0-1.0）"""
        if not stored:
            return 0.0
        
        total_weight = 0.0
        matched_weight = 0.0
        
        # 各特征权重（使用类常量，支持统一校准）
        weights = self.FEATURE_WEIGHTS
        
        for feature, weight in weights.items():
            total_weight += weight
            current_val = current.get(feature)
            stored_val = stored.get(feature)
            
            if current_val is not None and stored_val is not None:
                if current_val == stored_val:
                    matched_weight += weight
                # 部分匹配
                elif isinstance(current_val, (int, float)) and isinstance(stored_val, (int, float)):
                    # 数值特征（如semicolon_count）：接近度计算
                    diff_ratio = abs(current_val - stored_val) / max(1, max(current_val, stored_val))
                    if diff_ratio < 0.3:
                        matched_weight += weight * (1 - diff_ratio)
        
        if total_weight == 0:
            return 0.0
        
        return matched_weight / total_weight
    
    def _get_matched_features(self, current: dict[str, Any], stored: dict[str, Any]) -> list[str]:
        """获取匹配的特征名列表（用于日志）"""
        matched = []
        for key in current:
            if key in stored and current[key] == stored[key]:
                matched.append(key)
        return matched[:5]
    
    # ========== 经验记录 ==========
    
    def record(self, question: str, derivation_type: str, source: str = "local",
               confidence: float = 0.7):
        """
        记录一次推理经验。
        
        Args:
            question: 用户问题
            derivation_type: 确定的推理类型（deductive/inductive/analogical...）
            source: 经验来源（local=本地推测, remote_api=大模型判断）
            confidence: 置信度
        """
        features = self.extract_structural_features(question)
        
        # 检查是否已存在高度相似的经验
        existing = self.search(question)
        if existing and existing.get("confidence", 0) >= 0.85:
            # 更新已有经验的来源和置信度
            for exp in self._experiences:
                exp_features = exp.get("features", {})
                if self._calculate_feature_similarity(features, exp_features) >= 0.85:
                    if source == "remote_api" and exp.get("source", "") in ("local", "remote_api") or source == "remote_api_confirmed":
                        exp["source"] = "remote_api_confirmed"
                        exp["derivation_type"] = derivation_type
                        exp["timestamp"] = time.time()
                    self._save()
                    return
        
        # ★P0-2修复：容量保护——先预检再追加，避免新经验被截断淘汰
        # 预检容量：超出上限时先淘汰一条最旧经验，为新经验腾出空间
        if len(self._experiences) >= self._max_experiences:
            # 优先淘汰时间最久、来源为local的经验
            self._experiences.sort(
                key=lambda e: (
                    0 if e.get("source", "") in ("remote_api", "remote_api_confirmed") else 1,
                    e.get("timestamp", 0)
                )
            )
            # 移除最旧的一条（排序后索引0是最优先淘汰的）
            _removed = self._experiences.pop(0)
        
        # 新增经验（此时容量已确保有余地）
        exp = {
            "features": features,
            "derivation_type": derivation_type,
            "source": source,
            "confidence": confidence,
            "timestamp": time.time(),
            "example_question": question[:200],
        }
        self._experiences.append(exp)
        
        self._save()
    
    def get_stats(self) -> dict[str, Any]:
        """获取经验库统计"""
        sources = {}
        for exp in self._experiences:
            src = exp.get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1
        
        types = {}
        for exp in self._experiences:
            t = exp.get("derivation_type", "unknown")
            types[t] = types.get(t, 0) + 1
        
        return {
            "total_experiences": len(self._experiences),
            "source_distribution": sources,
            "type_distribution": types,
            "max_capacity": self._max_experiences,
        }

# 模块级单例

_reasoning_experience: ReasoningExperience | None = None
_reasoning_experience_lock = threading.Lock()

def get_reasoning_experience() -> ReasoningExperience:
    """获取ReasoningExperience单例"""
    global _reasoning_experience
    if _reasoning_experience is None:
        with _reasoning_experience_lock:
            if _reasoning_experience is None:
                _reasoning_experience = ReasoningExperience()
    return _reasoning_experience


def shutdown_reasoning_experience() -> None:
    """★P1: 复位 ReasoningExperience 单例，满足器官零状态（规则4）。"""
    global _reasoning_experience
    _inst = _reasoning_experience
    _reasoning_experience = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.mnemosyne.ReasoningExperience::shutdown_reasoning_experience L358")
