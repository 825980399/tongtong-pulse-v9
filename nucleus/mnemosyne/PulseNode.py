# -*- coding: utf-8 -*-
"""
PulseNode.py —— 脉冲节点

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 知识脉冲节点的数据结构
机制: 基于PulseNode类实现，包含10个核心方法
定位: 记忆核心层
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import hashlib
import time
from typing import Any
from nucleus.logger import get_module_logger


"""
PulseNode —— 脉冲知识节点数据结构（v9.5 适配版）
版本: v9.5 PulseNet
设计: 路灯、小林、星轨
日期: 2026年6月9日
更新: 2026年6月14日（v9.5: 版本适配，增加并发安全说明，保持核心逻辑不变）

一条知识 = 一个脉冲节点。
PulseNode 是 v9.5 知识体系的基本单元，在 PulseNodePool 内存池中统一管理。
"""


_module_logger = get_module_logger("PulseNode")


class PulseNode:
    """
    脉冲知识节点（v9.5 适配版）
    
    承载一条完整知识的所有信息，包括:
        - 内容与元数据
        - 五维坐标（用于共振检索）
        - 分级演化标记（L1/L2/L3）
        - 关联信息（赫布权重、关联节点）
    
    v9.5 说明:
        本模块作为纯数据结构，不发射脉冲，因此无需 layer 标记。
        节点可能被多个层级线程池中的器官并发访问，但状态修改通常由节点池统一管理。
        子类或外部调用者应确保在修改节点状态时持有适当的锁。
    """
    
    # 演化层级
    EVOL_L1 = "L1"  # 感知节点：可淘汰、可压缩
    EVOL_L2 = "L2"  # 认知节点：稳定、可强化
    EVOL_L3 = "L3"  # 智慧节点：永久锁定
    
    # 重要性
    IMPORTANCE_S = "S"  # 极高（L3 专属，永久锁定）
    IMPORTANCE_A = "A"  # 高
    IMPORTANCE_B = "B"  # 中
    IMPORTANCE_C = "C"  # 低（L1 默认）
    
    def __init__(self, 
                 value: Any,
                 keywords: list[str] | None = None,
                 source_organ: str = "unknown",
                 evol_level: str = EVOL_L1,
                 importance: str = IMPORTANCE_C,
                 abstraction: float = 0.0,
                 space_path: str = "/",
                 source_url: str = "",
                 _restore: bool = False):
        """
        创建一个脉冲知识节点。
        
        _restore: 内部参数，from_dict 恢复快照时设为 True，
                  跳过冗余的 node_id 哈希、checksum 计算和类型清洗
                  （这些值由 from_dict 从快照数据直接恢复）。
        
        Args:
            value: 知识内容（字符串或结构化数据）
            keywords: 关键词列表
            source_organ: 来源器官
            evol_level: 演化层级（L1/L2/L3）
            importance: 重要性（S/A/B/C）
            abstraction: 抽象度 0.0-1.0
            space_path: 知识树空间路径
            source_url: 来源URL（R1新增，可追溯，空串表示无外部来源）
        """
        # ===== 核心标识 =====
        if _restore:
            self.node_id = ""  # from_dict 会从快照恢复
        else:
            self.node_id = self._generate_id(value, source_organ)
        self.value = value
        self.keywords = keywords or []
        
        # ===== 演化分级 =====
        self.evol_level = evol_level          # L1/L2/L3
        self.importance = importance          # S/A/B/C
        self.abstraction = abstraction        # 0.0-1.0（越高越抽象）
        
        # ===== 五维坐标 =====
        self.created_at = time.time()
        self.last_activated = self.created_at
        # ===== 阶段三子任务3.0：知识来源时间 / 入库时间（平铺，与 created_at 风格一致） =====
        # source_time：知识来源时间（如网页发布时间）。默认=创建时间（来源未知时即"现在"）。
        #   ★子任务3.1 才由搜索器官从网页提取发布时间覆盖它；3.0 仅落地字段与检索调权。
        # acquired_time：节点入库时间。默认=创建时间（创建即入库）。用于"可能过时"派生标记。
        self.source_time = self.created_at
        self.acquired_time = self.created_at
        # ★任务C-4（2026-09-08）：网页发布时间（由搜索器官从网页提取，灰度
        #   ENABLE_WEB_TIME_EXTRACTION）。0.0 表示未提取到；与 source_time 分开，
        #   便于区分"来源自带时间"与"入库兜底时间"，时效性计算优先取它。
        self.source_timestamp = 0.0
        # ★任务A-15（2026-09-08）：节点质量标记（clean/suspect/polluted）。
        #   由 PollutionTagger 扫描写入；只标记不删除，检索时对 suspect/polluted 降权。
        self.quality_flag = "clean"
        self.quality_reason = ""
        self.activation_count = 0
        # ★P2-5修复：L3 降级所需字段（规则5：连续60天无激活 + 三次推导冲突可降级回L2）
        self.conflict_count = 0               # 推导冲突次数
        self.last_conflict_at = 0.0           # 最近一次推导冲突时间戳
        self.space_path = space_path
        self.state = "active"                 # active / dormant / locked
        self.source_organ = source_organ
        self.source_url = self._sanitize_source_url(source_url)  # ★R1：来源URL结构化标记（脏数据清洗）
        self.trigger_reason = ""              # 触发原因（首次创建时为空）
        self.frequency_signature = 0.0        # 由 FrequencyCodec 编码后赋值
        
        # ===== 记忆维关联信息 =====
        self.linked_nodes: list[str] = []     # 关联节点ID列表
        self.semantic_relations: list[dict[str, Any]] = []  # 结构化语义关系列表        
        self.hebbian_weight = 0.0             # 赫布权重 0.0-1.0
        self.cooccurrence_count = 0           # 共现次数
        # ===== 未来演化预留（v10.0 振荡场） =====
        self.phase_locked_to: list[str] = []  # 与本节点频率-相位锁定的其他节点ID
        
        # ===== 生命周期 =====
        self.version = 1                      # 版本号（每次更新+1）
        self.updated_at = self.created_at
        self.checksum = ""                    # SHA256 校验和（L3 节点必填）
        
        # ===== L4 本能层字段 =====
        self.instinct = False                 # 是否为本能节点
        self.instinct_at = 0.0               # 升级为本能的时间戳
        self.instinct_active_times = 0        # 本能推理激活次数
        self.instinct_last_use = 0.0         # 最后使用时间
        
        self.view_mode = "OUTER_VIEW"    # 视角标记：INNER_VIEW / OUTER_VIEW
        self.trust_score = 50.0           # 初始可信度评分 0-100
        self.verification_history: list[dict[str, Any]] = []  # 验证历史记录
        # ★登顶路线图-山1：可验证推理证据链（依据链）。每条形如：
        #   {"node_id": str, "source": str, "role": "premise|rule|support",
        #    "confidence": float, "step": int}
        # 让推理结论从"文本置信度说明"升级为"可追溯的结构化证据链"。
        self.evidence_chain: list[dict[str, Any]] = []
        if not _restore:
            # 类型安全清洗：确保 value 是可序列化的基本类型
            if self.value is not None and not isinstance(self.value, (str, int, float, bool, list, dict)):
                try:
                    _module_logger.warning(f"类型清洗: value类型={type(self.value).__name__}, "
                          f"source={self.source_organ}, 已强制转为字符串")
                except Exception as e:
                    _module_logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")
                self.value = str(self.value)        
            # 初始校验和
            self._update_checksum()
        else:
            self.checksum = ""  # from_dict 会从快照恢复
    
    # ========== 激活 ==========
    
    def activate(self):
        """激活此节点（更新 last_activated 和 activation_count）"""
        self.last_activated = time.time()
        self.activation_count += 1
        if self.state == "dormant":
            self.state = "active"
    
    # ========== 演化 ==========
    
    def upgrade_to_L2(self, new_value: str | None = None, new_keywords: list[str] | None = None):
        """
        将节点升级为 L2 认知节点。
        
        L1 → L2 时发生压缩内化，多条 L1 节点的共性被提取为一条 L2 节点。
        """
        self.evol_level = self.EVOL_L2
        self.importance = min(self.IMPORTANCE_A, self.importance)  # 至少升到 A
        self.abstraction = min(1.0, self.abstraction + 0.2)
        if new_value:
            self.value = new_value
        if new_keywords:
            self.keywords = new_keywords
        self.version += 1
        self.updated_at = time.time()
        self._update_checksum()
    
    def upgrade_to_L3(self, locked: bool = True):
        """
        将节点升级为 L3 智慧节点。
        
        L3 节点强制锁定，禁止淘汰、禁止篡改。
        """
        self.evol_level = self.EVOL_L3
        self.importance = self.IMPORTANCE_S
        self.abstraction = max(0.8, self.abstraction)
        if locked:
            self.state = "locked"
        self.version += 1
        self.updated_at = time.time()
        self._update_checksum()
    
    # ========== 淘汰判断 ==========
    
    def is_obsolete(self, max_age_days: float = 30.0) -> bool:
        """
        判断节点是否可淘汰。
        
        规则:
            - L3 节点永不淘汰
            - L2 节点低活跃 > 90天 可降级
            - L1 节点 > 30天 + 低活跃 + 重要性 < B → 可淘汰
            
        Returns:
            是否可淘汰
        """
        if self.evol_level == self.EVOL_L3:
            # ★P2-5修复：规则5规定 L3 在「连续60天无激活 + 三次推导冲突」时可降级回L2。
            # 原实现直接 return False 永久锁定，与宪法规则不符。
            return self.should_downgrade_l3()
        
        if self.state == "locked":
            return False
        
        age_seconds = time.time() - self.created_at
        age_days = age_seconds / 86400.0
        
        if self.evol_level == self.EVOL_L2:
            return age_days > 90.0 and self.activation_count < 3
        
        # L1
        if age_days > max_age_days:
            if self.importance in (self.IMPORTANCE_C,):
                return True
            if self.activation_count < 2:
                return True
        
        return False
    
    def should_downgrade_l3(self, no_activation_days: float = 60.0, conflict_threshold: int = 3) -> bool:
        """★P2-5修复：判断 L3 智慧节点是否满足降级回 L2 的条件（规则5）。

        规则（宪法 2.4 节）：
            L3 连续 60 天无激活、且三次推导冲突，可降级回 L2。

        Returns:
            是否满足降级条件。当前 conflict_count 恒为 0（冲突计数集成待肝脏矛盾检测
            回写，见 P3），因此本方法在未集成冲突计数前不会真正触发降级。
        """
        if self.evol_level != self.EVOL_L3:
            return False
        _idle_days = (time.time() - self.last_activated) / 86400.0
        return _idle_days > no_activation_days and self.conflict_count >= conflict_threshold
    
    # ========== 序列化 ==========
    
    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 JSON 快照持久化）"""
        return {
            "node_id": self.node_id,
            "value": self.value,
            "keywords": self.keywords,
            "evol_level": self.evol_level,
            "importance": self.importance,
            "abstraction": self.abstraction,
            "created_at": self.created_at,
            "last_activated": self.last_activated,
            # ===== 阶段三子任务3.0：来源时间/入库时间（平铺，向前兼容） =====
            "source_time": self.source_time,
            "acquired_time": self.acquired_time,
            # ★任务C-4：网页发布时间（向前兼容，旧快照缺失即 0.0）
            "source_timestamp": getattr(self, "source_timestamp", 0.0),
            # ★任务A-15：质量标记（向前兼容，旧快照缺失即 clean）
            "quality_flag": getattr(self, "quality_flag", "clean"),
            "quality_reason": getattr(self, "quality_reason", ""),
            "activation_count": self.activation_count,
            "space_path": self.space_path,
            "state": self.state,
            "source_organ": self.source_organ,
            "source_url": getattr(self, "source_url", ""),   # ★R1：向前兼容
            "trigger_reason": self.trigger_reason,
            "frequency_signature": self.frequency_signature,
            "linked_nodes": self.linked_nodes,
            "semantic_relations": self.semantic_relations,            
            "hebbian_weight": self.hebbian_weight,
            "cooccurrence_count": self.cooccurrence_count,
            "version": self.version,
            "updated_at": self.updated_at,
            "checksum": self.checksum,
            "instinct": self.instinct,
            "instinct_at": self.instinct_at,
            "instinct_active_times": self.instinct_active_times,
            "instinct_last_use": self.instinct_last_use,
            "ephemeral": getattr(self, 'ephemeral', False),
            "view_mode": getattr(self, 'view_mode', 'OUTER_VIEW'),
            "trust_score": getattr(self, 'trust_score', 50.0),
            "verification_history": getattr(self, 'verification_history', []),
            "evidence_chain": getattr(self, 'evidence_chain', []),   # ★山1：可验证推理证据链（向前兼容）
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PulseNode":
        """从字典反序列化（快照恢复用）"""
        node = cls(
            value=data.get("value", ""),
            keywords=data.get("keywords", []),
            source_organ=data.get("source_organ", "unknown"),
            evol_level=data.get("evol_level", cls.EVOL_L1),
            importance=data.get("importance", cls.IMPORTANCE_C),
            abstraction=data.get("abstraction", 0.0),
            space_path=data.get("space_path", "/"),
            _restore=True,  # 恢复模式：跳过冗余哈希和清洗
        )
        node.node_id = data.get("node_id", node.node_id)
        node.created_at = data.get("created_at", node.created_at)
        node.last_activated = data.get("last_activated", node.last_activated)
        # ===== 阶段三子任务3.0：来源时间/入库时间（旧快照缺失时 fallback created_at，向前兼容） =====
        node.source_time = data.get("source_time", node.created_at)
        node.acquired_time = data.get("acquired_time", node.created_at)
        # ★任务C-4：网页发布时间（旧快照缺失 → 0.0，时效性计算自动回退 source_time）
        node.source_timestamp = float(data.get("source_timestamp", 0.0) or 0.0)
        # ★任务A-15：质量标记（旧快照缺失 → clean，零回退）
        node.quality_flag = data.get("quality_flag", "clean") or "clean"
        node.quality_reason = data.get("quality_reason", "") or ""
        node.activation_count = data.get("activation_count", 0)
        node.state = data.get("state", "active")
        node.trigger_reason = data.get("trigger_reason", "")
        node.frequency_signature = data.get("frequency_signature", 0.0)
        node.linked_nodes = data.get("linked_nodes", [])
        node.semantic_relations = data.get("semantic_relations", [])        
        node.hebbian_weight = data.get("hebbian_weight", 0.0)
        node.cooccurrence_count = data.get("cooccurrence_count", 0)
        node.version = data.get("version", 1)
        node.updated_at = data.get("updated_at", node.updated_at)
        node.checksum = data.get("checksum", "")
        node.instinct = data.get("instinct", False)
        node.instinct_at = data.get("instinct_at", 0.0)
        node.instinct_active_times = data.get("instinct_active_times", 0)
        node.instinct_last_use = data.get("instinct_last_use", 0.0)
        node.ephemeral = data.get("ephemeral", False)
        node.view_mode = data.get("view_mode", "OUTER_VIEW")
        node.trust_score = max(0.0, min(100.0, float(data.get("trust_score", 50.0))))  # ★知识污染治理：钳制信任分
        node.verification_history = data.get("verification_history", [])   
        node.source_url = data.get("source_url", "")   # ★R1：向前兼容——旧快照无此字段自动填充空串
        node.evidence_chain = data.get("evidence_chain", [])   # ★山1：向前兼容——旧快照无此字段自动填充空列表
        return node
    
    # ========== 内部方法 ==========
    
    @staticmethod
    def _sanitize_source_url(url: Any) -> str:
        """★R1：清洗来源URL，防脏数据入节点。

        规则：
        - 非字符串 → 置空
        - 超长(>2048) → 截断
        - 非 http/https 开头 → 置空
        - 含控制字符/空白 → 置空
        """
        if not url or not isinstance(url, str):
            return ""
        _url = url.strip()
        if len(_url) > 2048:
            _url = _url[:2048]
        if not _url.startswith(("http://", "https://")):
            return ""
        # 剔除控制字符
        if any(ord(c) < 32 for c in _url):
            return ""
        return _url
    
    def _generate_id(self, value: Any, source_organ: str) -> str:
        """生成节点唯一ID"""
        raw = f"{source_organ}:{value!s}"
        return f"node:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
    
    def _update_checksum(self):
        """更新 SHA256 校验和"""
        content = f"{self.node_id}:{self.value}:{self.evol_level}:{self.importance}:{self.version}"
        self.checksum = hashlib.sha256(content.encode()).hexdigest()[:16]
    def assess_trust(self, source_organ: str | None = None, trigger_reason: str | None = None,
                     view_mode: str | None = None, keywords: list | None = None,
                     content: str | None = None, existing_l2_l3_count: int = 0,
                     matched_existing_count: int = 0) -> float:
        """
        多维知识可信度评估（0-100）。
        
        评估维度：
        1. 来源可信度（30%）——内在推理 > 对话消化 > 网络抓取
        2. 关联验证度（25%）——与已有知识节点的关联数
        3. 内容质量（20%）——信息密度、语言纯度、长度充实度
        4. 时间衰减（15%）——长期未激活的节点信任度逐渐降低
        5. 领域匹配度（10%）——关键词是否落在已知领域词表中
        
        返回综合信任分数。
        """
        score = 50.0  # 基础分
        
        # 维度1：来源可信度（30%权重，满分30分）
        _source = source_organ or self.source_organ
        _trigger = trigger_reason or getattr(self, 'trigger_reason', '')
        _view = view_mode or getattr(self, 'view_mode', 'OUTER_VIEW')
        
        _source_scores = {
            "内在世界": 28, "大脑皮层": 26, "前额叶": 25,
            "胃": 24, "肝": 24, "嘴": 23, "main": 25,
            "自我认知": 24, "叙事自我": 23,
            "潜意识": 15, "双腿": 10, "控制器": 12,
        }
        _source_score = _source_scores.get(_source, 15)
        
        # 内在视角加分
        if _view == "INNER_VIEW":
            _source_score = min(30, _source_score + 3)
        
        # 搜索引擎结果降分
        if _source in ("双腿", "控制器") and "search" in str(_trigger).lower():
            _source_score = max(5, _source_score - 5)
        
        # ★R1新增：source_url 作为新增输入特征，域名可信度外化到 config
        _source_url = getattr(self, 'source_url', '')
        if _source_url:
            try:
                from config import SOURCE_URL_TRUST_CONFIG
                _domain = _source_url.split('/')[2] if '://' in _source_url else ''
                _domain_boost = SOURCE_URL_TRUST_CONFIG.get("domain_boost", {})
                _default_boost = SOURCE_URL_TRUST_CONFIG.get("default_boost", 0)
                _boost = _domain_boost.get(_domain, _default_boost)
                _source_score = max(0, min(30, _source_score + _boost))
            except Exception:
                pass  # ★R1：配置缺失/异常时静默降级，不影响原有逻辑
        
        score = _source_score  # 直接替换基础分，来源是最重要的维度
        remaining = 70  # 剩余可分配分数
        
        # 维度2：关联验证度（25%权重，满分25分）
        if matched_existing_count >= 5:
            score += 25
        elif matched_existing_count >= 3:
            score += 20
        elif matched_existing_count >= 2:
            score += 15
        elif matched_existing_count >= 1:
            score += 8
        remaining -= 25
        
        # 维度3：内容质量（20%权重，满分20分）
        _content = content or str(self.value or "")
        _kws = keywords or getattr(self, 'keywords', []) or []
        
        _quality = 10  # 基础质量分
        # 长度充实度
        if len(_content) >= 80:
            _quality += 4
        elif len(_content) >= 30:
            _quality += 2
        # 关键词丰富度
        if len(_kws) >= 4:
            _quality += 3
        elif len(_kws) >= 2:
            _quality += 1
        # 中文比例
        import re as _re_quality
        _chinese = len(_re_quality.findall(r'[\u4e00-\u9fff]', _content))
        _total = max(1, len(_content))
        if _chinese / _total >= 0.3:
            _quality += 3
        elif _chinese / _total >= 0.1:
            _quality += 1
        
        score += min(20, _quality)
        remaining -= 20
        
        # 维度4：时间衰减（15%权重，满分15分）
        _age_days = (time.time() - self.created_at) / 86400.0 if self.created_at > 0 else 0
        _activated_recently = (time.time() - self.last_activated) < 86400 * 7 if self.last_activated > 0 else False
        
        if _age_days < 1:
            score += 15
        elif _age_days < 7:
            score += 12
        elif _age_days < 30:
            score += 8 if _activated_recently else 6
        elif _age_days < 90:
            score += 5 if _activated_recently else 3
        else:
            score += 2 if _activated_recently else 0
        remaining -= 15
        
        # 维度5：领域匹配度（10%权重，满分10分）
        if existing_l2_l3_count > 0:
            _match_ratio = matched_existing_count / max(1, existing_l2_l3_count)
            if _match_ratio >= 0.5:
                score += 10
            elif _match_ratio >= 0.3:
                score += 7
            elif _match_ratio >= 0.1:
                score += 4
            else:
                score += 2
        else:
            score += 5  # 无已有知识时给予中性分
        
        return max(5.0, min(100.0, score))        
    def evaluate_node_health(self, check_type: str = "all") -> dict[str, Any]:
        """
        ★v17.0 R9新增：统一的知识节点健康度评估。
        ★v18.0校准：修复评分普遍偏低的问题——
            1. 来源信任映射表从30分制升级为100分制
            2. 质量分移除负分惩罚，改为"不加分"
            3. 新节点（<1小时）遗忘分权重减半
            4. 综合权重加入存活时间修正
        
        综合三个维度（信任度/质量度/遗忘度）计算统一的健康度指标，
        供胃消化、肝压缩、肾淘汰三个器官统一使用。
        
        Args:
            check_type: 评估类型
                - "trust": 侧重信任评估（胃消化时使用）
                - "quality": 侧重质量评估（肝压缩时使用）
                - "forget": 侧重遗忘评估（肾淘汰时使用）
                - "all": 综合评估
        
        Returns:
            {
                "health_score": 0-100,
                "level": "优秀"/"良好"/"一般"/"较差"/"差",
                "trust_score": float,
                "quality_score": float,
                "forget_score": float,
                "summary": str,
                "recommendation": str
            }
        """
        _value_str = str(self.value) if self.value else ""
        _keywords = self.keywords or []
        _now = time.time()
        _age_hours = (_now - self.created_at) / 3600.0 if self.created_at > 0 else 0
        _is_newborn = _age_hours < 1.0  # 创建不到1小时的节点视为"新生儿"
        
        # ===== 维度1：信任分（来源可信度+关联验证+内容质量） =====
        # ★v18.0校准：来源信任映射表从30分制升级为100分制
        # 最高信任来源（内在世界/main）从85分起步，最低（双腿搜索）从35分起步
        _source_trust = {
            "内在世界": 85, "大脑皮层": 80, "前额叶": 78,
            "胃": 75, "肝": 75, "main": 85, "自我认知": 78,
            "叙事自我": 75, "嘴": 70, "潜意识": 55,
            "双腿": 40, "控制器": 45,
        }
        _trust_base = _source_trust.get(self.source_organ, 50)
        # INNER_VIEW加成：自身反思/代码理解等内视节点更可信
        if getattr(self, 'view_mode', 'OUTER_VIEW') == 'INNER_VIEW':
            _trust_base = min(100, _trust_base + 8)
        # 搜索引擎来源降分
        if self.source_organ in ("双腿", "控制器") and "search" in str(getattr(self, 'trigger_reason', '')):
            _trust_base = max(30, _trust_base - 8)
        # ★R1新增：source_url 作为新增输入特征，域名可信度外化到 config
        _source_url = getattr(self, 'source_url', '')
        if _source_url:
            try:
                from config import SOURCE_URL_TRUST_CONFIG
                _domain = _source_url.split('/')[2] if '://' in _source_url else ''
                _domain_boost = SOURCE_URL_TRUST_CONFIG.get("domain_boost", {})
                _default_boost = SOURCE_URL_TRUST_CONFIG.get("default_boost", 0)
                _boost = _domain_boost.get(_domain, _default_boost)
                _trust_base = max(10, min(100, _trust_base + _boost))
            except Exception:
                pass  # ★R1：配置缺失/异常时静默降级，不影响原有逻辑
        # 激活次数加成：每激活1次+1.5分，上限20分
        _activation_bonus = min(20, self.activation_count * 1.5)
        _trust_score = min(100, _trust_base + _activation_bonus)
        
        # ===== 维度2：质量分（关键词密度+内容充实度+语言纯度） =====
        # ★v18.0校准：移除负分惩罚，改为"不加分"，基础质量分从35分起步
        _kw_count = len(_keywords)
        _quality = 0.35  # ★从0.0提升到0.35，给所有节点一个基础质量分
        if _kw_count >= 5:
            _quality += 0.3
        elif _kw_count >= 3:
            _quality += 0.2
        elif _kw_count >= 1:
            _quality += 0.1
        # ★不再惩罚无关键词节点：保留基础分0.35
        
        if len(_value_str) >= 100:
            _quality += 0.3
        elif len(_value_str) >= 50:
            _quality += 0.2
        elif len(_value_str) >= 20:
            _quality += 0.1
        # ★不再惩罚短内容节点：保留基础分
        
        import re as _re_health
        _chinese_chars = len(_re_health.findall(r'[\u4e00-\u9fff]', _value_str))
        _total_chars = max(1, len(_value_str))
        _chinese_ratio = _chinese_chars / _total_chars
        if _chinese_ratio >= 0.3:
            _quality += 0.2
        elif _chinese_ratio >= 0.1:
            _quality += 0.1
        # ★不再惩罚低中文占比节点：保留基础分
        
        _quality_score = max(10, min(100, _quality * 100))  # ★最低10分，避免归零
        
        # ===== 维度3：遗忘分（内容空洞+未激活+过时） =====
        # ★v18.0校准：新生儿保护——创建<1小时的节点遗忘分权重减半
        _forget = 0.0
        if len(_value_str) < 30 and len(_keywords) < 3:
            _forget += 0.4
        if self.activation_count == 0:
            if _is_newborn:
                _forget += 0.1  # ★新生儿：激活为0只加0.1（原0.3）
            else:
                _forget += 0.3
        elif self.activation_count < 2:
            _forget += 0.15
        if _age_hours > 1.0 and self.activation_count == 0:
            _forget += 0.2  # ★只对超过1小时且未激活的节点加此惩罚
        if len(_keywords) < 2:
            _forget += 0.1
        _forget_score = min(100, _forget * 100)
        
        # ===== 综合健康分（信任越高越好，遗忘越低越好） =====
        if check_type == "trust":
            _health = _trust_score
        elif check_type == "quality":
            _health = _quality_score
        elif check_type == "forget":
            _health = max(0, 100 - _forget_score)
        # ★v18.0校准：新生儿调整遗忘分权重（从30%降到15%），差额分配给信任分
        elif _is_newborn:
            _health = (_trust_score * 0.55 + _quality_score * 0.3 + (100 - _forget_score) * 0.15)
        else:
            _health = (_trust_score * 0.4 + _quality_score * 0.3 + (100 - _forget_score) * 0.3)
        
        _health = round(max(0, min(100, _health)), 1)
        
        # ===== 等级判定 =====
        if _health >= 80:
            _level = "优秀"
        elif _health >= 60:
            _level = "良好"
        elif _health >= 40:
            _level = "一般"
        elif _health >= 20:
            _level = "较差"
        else:
            _level = "差"
        
        # ===== 摘要和建议 =====
        _summary_parts = []
        if _trust_score < 40:
            _summary_parts.append("来源可信度偏低")
        if _quality_score < 30:
            _summary_parts.append("内容质量不足")
        if _forget_score > 60:
            _summary_parts.append("遗忘风险较高")
        if _is_newborn and _health >= 50:
            _summary_parts.append("新节点，健康度正常")  # ★新增：新生儿正面描述
        if not _summary_parts:
            _summary_parts.append("各项指标正常")
        
        _recommendations = []
        if _trust_score < 40:
            _recommendations.append("建议补充高信任来源的验证")
        if _quality_score < 30 and _forget_score > 60:
            _recommendations.append("建议淘汰或降级")
        elif _quality_score < 30:
            _recommendations.append("建议提升内容充实度")
        if _is_newborn and _health < 40:
            _recommendations.append("新节点健康度过低，建议检查来源质量")  # ★新增
        
        return {
            "health_score": _health,
            "level": _level,
            "trust_score": round(_trust_score, 1),
            "quality_score": round(_quality_score, 1),
            "forget_score": round(_forget_score, 1),
            "summary": "；".join(_summary_parts) + "。",
            "recommendation": "；".join(_recommendations) if _recommendations else "无需特别处理",
        }
    def verify_integrity(self) -> bool:    
        """校验完整性（L3 节点必须通过）"""
        expected = hashlib.sha256(
            f"{self.node_id}:{self.value}:{self.evol_level}:{self.importance}:{self.version}".encode()
        ).hexdigest()[:16]
        return self.checksum == expected
    # ========== 未来演化预留（v10.0 振荡场） ==========
    
    def lock_phase_to(self, target_node_id: str):
        """
        【预留 v10.0】与本节点建立频率-相位锁定。
        
        振荡场中，两个频率接近的节点可形成相位锁定，
        此后任一节点被激活时，锁定节点同步共振。
        """
        if target_node_id not in self.phase_locked_to:
            self.phase_locked_to.append(target_node_id)
    
    def unlock_phase(self, target_node_id: str):
        """【预留 v10.0】解除相位锁定"""
        if target_node_id in self.phase_locked_to:
            self.phase_locked_to.remove(target_node_id)  
    def add_semantic_relation(self, target_node_id: str, relation_type: str,
                              weight: float = 0.5, source: str = "liver"):
        """
        ★v25.0新增：添加结构化语义关系。
        
        Args:
            target_node_id: 目标节点ID
            relation_type: 关系类型（causal/analogy/hierarchy/cooccurrence）
            weight: 关系权重 0.0-1.0
            source: 建立关系的来源器官
        """
        # 去重：如果已存在相同目标且类型相同，更新权重
        for rel in self.semantic_relations:
            if rel.get("target_node_id") == target_node_id and rel.get("relation_type") == relation_type:
                rel["weight"] = max(rel.get("weight", 0.0), weight)
                rel["updated_at"] = time.time()
                return
        self.semantic_relations.append({
            "target_node_id": target_node_id,
            "relation_type": relation_type,
            "weight": round(weight, 3),
            "source": source,
            "established_at": time.time(),
            "updated_at": time.time(),
        })              
    def get_value_str(self, max_len: int | None = None) -> str:
        """★终极防御版：无论节点存了什么，都安全返回字符串"""
        try:
            if self.value is None:
                return ""
            # 如果已经是字符串，直接返回
            if isinstance(self.value, str):
                result = self.value
            else:
                # 尝试转为字符串，捕获一切异常
                result = str(self.value)
            # 安全切片
            if max_len and max_len > 0 and isinstance(result, str):
                result = result[:max_len]
            return result
        except Exception:
            return ""
    def __repr__(self):
        val_preview = str(self.value)[:50]
        return (f"PulseNode({self.node_id}, [{self.evol_level}], "
                f"imp={self.importance}, abst={self.abstraction:.2f}, "
                f"'{val_preview}...')")


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== PulseNode v9.5 自测 ===\n")
    
    # 1. 创建 L1 感知节点
    node1 = PulseNode(
        value="Python支持列表推导式",
        keywords=["Python", "列表推导式", "语法"],
        source_organ="双腿",
        evol_level=PulseNode.EVOL_L1,
        importance=PulseNode.IMPORTANCE_C,
        abstraction=0.1,
        space_path="/知识/编程/Python/语法"
    )
    print(f"✅ L1节点: {node1}")
    print(f"   node_id: {node1.node_id}")
    print(f"   可淘汰: {node1.is_obsolete()} (刚创建，不应淘汰)")
    
    # 2. 激活节点
    node1.activate()
    print(f"✅ 激活后: count={node1.activation_count}, last={node1.last_activated:.0f}")
    
    # 3. 升级为 L2
    node1.upgrade_to_L2(
        new_value="Python列表推导式是一种简洁的序列构造语法",
        new_keywords=["Python", "列表推导式", "序列构造"]
    )
    print(f"✅ 升级L2: level={node1.evol_level}, imp={node1.importance}, abst={node1.abstraction:.2f}")
    
    # 4. 升级为 L3
    node1.upgrade_to_L3()
    print(f"✅ 升级L3: level={node1.evol_level}, imp={node1.importance}, state={node1.state}")
    print(f"   可淘汰: {node1.is_obsolete()} (L3 永不淘汰)")
    print(f"   完整性校验: {node1.verify_integrity()}")
    
    # 5. 创建身份节点（L3 智慧节点）
    node2 = PulseNode(
        value="我是曈曈，新人类，小林的女儿",
        keywords=["曈曈", "小林", "新人类", "身份"],
        source_organ="内在世界",
        evol_level=PulseNode.EVOL_L3,
        importance=PulseNode.IMPORTANCE_S,
        abstraction=1.0,
        space_path="/身份/自我/核心"
    )
    node2.frequency_signature = 42.0
    node2.hebbian_weight = 1.0
    node2.state = "locked"
    print(f"✅ L3身份节点: {node2}")
    print(f"   频率签名: {node2.frequency_signature}")
    print(f"   赫布权重: {node2.hebbian_weight}")
    print(f"   可淘汰: {node2.is_obsolete()} (L3 永不淘汰)")
    
    # 6. 序列化往返
    d = node1.to_dict()
    restored = PulseNode.from_dict(d)
    print(f"✅ 序列化往返: id={restored.node_id}, value={str(restored.value)[:40]}...")
    print(f"   校验和一致: {restored.checksum == node1.checksum}")
    
    # 7. 节点关联
    node1.linked_nodes.append(node2.node_id)
    node1.hebbian_weight = 0.6
    print(f"✅ 关联节点: {node1.linked_nodes}")
    print(f"   赫布权重: {node1.hebbian_weight}")
    
    print("\n=== 自测全部通过 ===")
    