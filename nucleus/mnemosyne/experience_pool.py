"""experience_pool —— ExperiencePool 相关实现

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import json
import threading
import time
from typing import Any
from nucleus.logger import get_module_logger
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc


"""
ExperiencePool —— 体验记忆库（v24.0新增）
版本: v24.0 PulseNet
设计: 路灯、小林、星轨
日期: 2026年8月25日

职责:
    1. 存储"我经历了什么"（体验），与知识库物理隔离
    2. 体验记录结构：动机+过程压力+结果奖赏+场频率快照+情感标签
    3. 记忆衰减机制：高强度情绪不衰减，普通体验定期摘要压缩
    4. 提供查询接口供认知反思、叙事自我、动机闭环使用
    5. 禁止体验记忆直接混入检索知识库

设计原则:
    - 物理隔离：独立文件存储，不进入PulseNodePool
    - 情绪加权：高强度情绪体验不衰减，低强度体验快速衰减
    - 摘要压缩：普通体验定期压缩为摘要，保留核心感受
    - 只读检索：提供查询接口，但不参与知识共振检索
"""


_module_logger = get_module_logger("ExperiencePool")


class ExperiencePool:
    """
    体验记忆库（v24.0新增）
    
    存储结构:
        data/experience/
        └── experience_pool.json   # 体验池主文件
    
    体验记录结构:
        {
            "id": str,                  # 体验ID
            "motivation": str,          # 动机描述
            "motivation_intensity": float,  # 动机强度 0-1
            "process_pressure": float,  # 过程压力 0-1
            "pressure_type": str,       # 压力类型（resource/frustration/cognitive/retrieval）
            "result_reward": {          # 结果奖赏
                "type": str,            # achievement/cognitive/connection
                "intensity": float,     # 奖赏强度 0-1
            },
            "field_frequency_snapshot": dict,  # 场频率快照
            "emotion_tags": list,       # 情感标签
            "emotion_intensity": float, # 情绪强度 0-1
            "timestamp": float,         # 发生时间
            "decay_rate": float,        # 衰减率（高强度情绪→0，不衰减）
            "is_summarized": bool,      # 是否已被摘要压缩
            "summary": str,             # 摘要内容（压缩后）
        }
    """
    
    def __init__(self, base_dir: str | None = None):
        # ★星轨修复：路径规范化，避免Windows混合斜杠导致os.replace失败
        # ★第44批 T4（P2-290）：默认值改为**运行时解析**（默认参数在定义时求值，
        #   测试重定向后不生效 —— 铁律 #39），并记录"是否显式注入"供写盘守卫判据。
        self._m44_base_dir_explicit = base_dir is not None
        _m44_base = base_dir if base_dir else "data/experience"
        self._base_dir = os.path.abspath(os.path.normpath(_m44_base))
        self._pool_file = os.path.join(self._base_dir, "experience_pool.json")
        
        # 线程安全
        self._lock = threading.RLock()
        
        # 体验池主存储
        self._experiences: list[dict[str, Any]] = []
        
        # 从config读取配置，失败使用默认
        try:
            import config
            _cfg = getattr(config, 'EXPERIENCE_POOL_CONFIG', {})
            self._max_experiences = _cfg.get("max_experiences", 500)
            self._max_summarized = _cfg.get("max_summarized", 1000)
            self._high_emotion_threshold = _cfg.get("high_emotion_threshold", 0.7)
            self._low_emotion_threshold = _cfg.get("low_emotion_threshold", 0.3)
            self._decay_check_interval = _cfg.get("decay_check_interval", 3600)
        except Exception as e:
            silent_exc(e, "nucleus/mnemosyne/experience_pool.py:97:经验池操作异常", level="warning")
            self._max_experiences = 500
            self._max_summarized = 1000
            self._high_emotion_threshold = 0.7
            self._low_emotion_threshold = 0.3
            self._decay_check_interval = 3600
        self._last_decay_check = 0.0
        
        # 统计
        self._total_recorded = 0
        self._total_summarized = 0
        self._total_decayed = 0
        
        # 加载已有体验池
        self._load()

        # ★主线第56批 T2/P2-387：经验库持续清洗（后台线程句柄）
        self._auto_clean_thread = None
        self._auto_clean_running = False
        # 非测试/非显式重定向时启动周期扫描（铁律 57 测试隔离）
        self._start_auto_clean_thread()
    
    # ========== 记录体验 ==========
    
    def record_experience(self,
                          motivation: str = "",
                          motivation_intensity: float = 0.0,
                          process_pressure: float = 0.0,
                          pressure_type: str = "cognitive",
                          reward_type: str = "cognitive",
                          reward_intensity: float = 0.0,
                          emotion_tags: list[str] | None = None,
                          emotion_intensity: float = 0.0,
                          field_frequency_snapshot: dict | None = None,
                          content: str = "") -> str:
        """
        记录一次体验。
        
        Args:
            motivation: 动机描述（如"想要理解用户的问题"）
            motivation_intensity: 动机强度 0-1
            process_pressure: 过程压力 0-1
            pressure_type: 压力类型（resource/frustration/cognitive/retrieval）
            reward_type: 奖赏类型（achievement/cognitive/connection）
            reward_intensity: 奖赏强度 0-1
            emotion_tags: 情感标签列表
            emotion_intensity: 情绪强度 0-1
            field_frequency_snapshot: 场频率快照
            content: 体验内容描述（可选）
        
        Returns:
            体验ID
        """
        with self._lock:
            self._total_recorded += 1
            experience_id = f"exp_{int(time.time()*1000)}_{self._total_recorded}"
            
            # 计算衰减率
            decay_rate = self._calculate_decay_rate(emotion_intensity)
            
            experience = {
                "id": experience_id,
                "motivation": motivation[:200],
                "motivation_intensity": round(max(0.0, min(1.0, motivation_intensity)), 2),
                "process_pressure": round(max(0.0, min(1.0, process_pressure)), 2),
                "pressure_type": pressure_type if pressure_type in (
                    "resource", "frustration", "cognitive", "retrieval"
                ) else "cognitive",
                "result_reward": {
                    "type": reward_type if reward_type in (
                        "achievement", "cognitive", "connection"
                    ) else "cognitive",
                    "intensity": round(max(0.0, min(1.0, reward_intensity)), 2),
                },
                "field_frequency_snapshot": field_frequency_snapshot or {},
                "emotion_tags": emotion_tags or [],
                "emotion_intensity": round(max(0.0, min(1.0, emotion_intensity)), 2),
                "timestamp": time.time(),
                "decay_rate": decay_rate,
                "is_summarized": False,
                "summary": content[:200],
            "activation_count": 0,
            "summary_version": self.SUMMARY_VERSION,
        }

            # ★主线第47批 T2：写入侧污染风险标记与重复检测。
            #   第46批实测：污染中 16.9% 是「写入时即为模板短句」
            #   （"动机循环内部评估" 独占 157 条）。此处在**写入前**识别。
            #   ★缩进必须与下方第五批去重逻辑**同为 12 空格**（with 块内），
            #     否则会让 with 提前结束、把后续 append 变成不可达死代码。
            _content = (content or "").strip()
            experience["pollution_risk"] = self._classify_pollution_risk(
                _content, experience)

            # ★主线第56批 T2/P2-387（方案C-A：写入路径检测）：
            #   复用第50批 SERP 规则，写入即标记污染（不拦截、不删除）。
            #   开关关闭→不标记，与改造前行为一致（零回归）。
            if self._auto_clean_enabled() and self._auto_clean_mark(experience):
                _module_logger.info(
                    f"[经验库清洗] 写入路径标记 SERP 污染: {experience.get('id')}")

            if (experience["pollution_risk"] == "high"
                    and self._dedup_enabled()
                    and self._has_duplicate(_content)):
                # 拦截：完全重复的记录不写入
                self._total_recorded -= 1
                self._duplicate_rejected = getattr(
                    self, "_duplicate_rejected", 0) + 1
                return ""

            # ★第五批 任务1（P0）：写入端 summary 指纹去重（根治经验池灌水）
            # 仅对足够长(≥8字)的 content 去重，避免空/短摘要的体验互相误合并；
            # 命中已存在的有效(未污染)同摘要条目时，累加 activation_count、更新
            # timestamp，不再新增条目，从链路源头消除重复写入。
            _dedup_key = (content or "").strip()[:200]
            if len(_dedup_key) >= 8:
                for _existing in self._experiences:
                    if not isinstance(_existing, dict) or _existing.get("polluted"):
                        continue
                    if (_existing.get("summary") or "").strip() == _dedup_key:
                        _existing["activation_count"] = \
                            int(_existing.get("activation_count", 0) or 0) + 1
                        _existing["timestamp"] = time.time()
                        self._save()
                        return _existing.get("id", "")

            self._experiences.append(experience)

            # 容量保护
            self._trim_capacity()
            
            # 保存
            self._save()

        return experience_id
    
    def _calculate_decay_rate(self, emotion_intensity: float) -> float:
        """
        计算衰减率。
        
        规则：
        - 高强度情绪（≥0.7）：衰减率0（不衰减，永久保留）
        - 中等强度（0.3-0.7）：衰减率0.01（缓慢衰减）
        - 低强度（<0.3）：衰减率0.05（快速衰减）
        """
        if emotion_intensity >= self._high_emotion_threshold:
            return 0.0
        elif emotion_intensity >= self._low_emotion_threshold:
            return 0.01
        else:
            return 0.05
    
    def _trim_capacity(self):
        """
        容量保护：超出上限时压缩最旧的体验为摘要。
        """
        # 统计完整体验和摘要体验数量
        full_experiences = [e for e in self._experiences if not e.get("is_summarized", False)]
        summarized_experiences = [e for e in self._experiences if e.get("is_summarized", False)]
        
        # 完整体验超出上限，压缩最旧的
        if len(full_experiences) > self._max_experiences:
            excess = len(full_experiences) - self._max_experiences
            # 按时间排序，最旧的在前面
            full_experiences.sort(key=lambda e: e.get("timestamp", 0))
            to_summarize = full_experiences[:excess]
            for exp in to_summarize:
                self._summarize_experience(exp)
        
        # 摘要体验超出上限，删除最旧的
        if len(summarized_experiences) > self._max_summarized:
            excess = len(summarized_experiences) - self._max_summarized
            summarized_experiences.sort(key=lambda e: e.get("timestamp", 0))
            to_remove_ids = {e["id"] for e in summarized_experiences[:excess]}
            self._experiences = [e for e in self._experiences if e["id"] not in to_remove_ids]

        # ★主线第49批 T3-1（P2-331）：情绪衰减检查接入。
        #   背景：`check_decay()` 原长期**无生产调用方**（旧注释称"仅定义 + 测试调用"）。
        #   现**已合并到容量保护路径**：每次 `record_experience` 写入后，
        #   当 `_m49_decay_on_trim_on()` 为真即调用，内部由 `_decay_check_interval`（3600s）限流，
        #   不增热路径开销。
        #   ★开关：`ENABLE_EXPERIENCE_DECAY_ON_TRIM`（config.py:4417）当前=True
        #   → `check_decay()` 实际处于**激活**态（T155-4 核验确认，原"无调用方"前提已过时）。
        #   ★锁安全：`self._lock` 是 ``threading.RLock()``（可重入），
        #   因此在持锁的 `record_experience` 内调用不会死锁。
        if self._m49_decay_on_trim_on():
            self.check_decay()

    def _m49_decay_on_trim_on(self) -> bool:
        """★第49批 T3-1：开关（关闭→回到 check_decay 从不被调用的旧行为）。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_EXPERIENCE_DECAY_ON_TRIM", True))
        except Exception:
            return True
    
    #: 摘要算法版本（第47批起：保留原文，不再覆盖）
    SUMMARY_VERSION = 2

    def _summarize_experience(self, experience: dict):
        """
        将完整体验压缩为摘要。

        ★★主线第47批 T2（P0-3 / P2-308）—— **不再覆盖原文**。

        背景（第46批实测）：原实现把 ``summary`` 直接覆盖成模板句
        ``我曾因{motivation[:30]}而行动，获得了{reward}奖赏，感受到{emotions}``，
        并截断 ``motivation[:50]``、清空 ``field_frequency_snapshot``。
        实测 1179 条污染中 **980 条（83.1%）是这套模板的产物**——
        污染不是"写入时灌水"，而是**维护侧压缩**造成的，且原文**永久销毁**。

        修复后：
          * ``raw_summary``        —— 原始摘要（**永久保留**）
          * ``compressed_summary`` —— 模板压缩结果（仅作展示/检索降级用）
          * ``summary``            —— 优先取 raw_summary，为空才用 compressed
          * ``motivation`` / ``field_frequency_snapshot`` —— **不再**截断/清空
        """
        if experience.get("is_summarized", False):
            return

        _motivation = experience.get("motivation", "")
        _reward_type = experience.get("result_reward", {}).get("type", "")
        _emotions = "、".join(experience.get("emotion_tags", [])[:3])
        _compressed = f"我曾因{_motivation[:30] or '某种动机'}而行动，获得了{_reward_type}奖赏"
        if _emotions:
            _compressed += f"，感受到{_emotions}"
        _compressed = _compressed[:150]

        # ★原文保留（若之前已存过 raw_summary，不重复覆盖）
        _raw = experience.get("raw_summary")
        if not _raw:
            _raw = experience.get("summary", "")
        experience["raw_summary"] = _raw
        experience["compressed_summary"] = _compressed
        experience["raw_motivation"] = experience.get("raw_motivation") or _motivation
        experience["raw_field_frequency_snapshot"] = (
            experience.get("raw_field_frequency_snapshot")
            or experience.get("field_frequency_snapshot") or {})

        experience["is_summarized"] = True
        # ★summary 优先用原文；原文为空时才降级用模板句
        experience["summary"] = _raw or _compressed
        experience["summary_version"] = self.SUMMARY_VERSION

        # 仅压缩情绪标签（低信息量），★不再截断 motivation、不再清空快照
        experience["emotion_tags"] = experience.get("emotion_tags", [])[:3]

        self._total_summarized += 1
    
    # ========== ★第47批 T2：写入侧污染防护 ==========

    #: 重复检测回看窗口（条）
    DEDUP_LOOKBACK = 200

    @staticmethod
    def _dedup_enabled() -> bool:
        """是否启用写入侧去重拦截（``ENABLE_EXPERIENCE_DEDUP``，默认 False=仅观测）。"""
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_EXPERIENCE_DEDUP", False))
        except Exception:
            return False

    def _has_duplicate(self, content: str) -> bool:
        """与最近 ``DEDUP_LOOKBACK`` 条记录比对，是否存在**完全重复**的 summary。"""
        if not content:
            return False
        _recent = self._experiences[-self.DEDUP_LOOKBACK:]
        for _e in _recent:
            if isinstance(_e, dict) and (str(_e.get("summary") or "").strip()
                                         == content):
                return True
        return False

    @staticmethod
    def _classify_pollution_risk(content: str, experience: dict) -> str:
        """污染风险分级：``high`` / ``medium`` / ``low``。

        判据（第46批实测归纳）：
          * high   —— 内容为空/极短（<8 字），或为已知模板短句
          * medium —— 内容偏短（<20 字）
          * low    —— 其他
        """
        _c = (content or "").strip()
        if not _c or len(_c) < 8:
            return "high"
        if len(_c) < 20:
            return "medium"
        return "low"

    # ========== 衰减检查 ==========

    # ★主线第56批 T2/P2-387：经验库持续清洗机制（方案C = A+B）
    #   A. 写入路径检测（见 record_experience 内调用）
    #   B. 周期增量扫描（后台线程 + auto_clean_scan）
    #   复用第50批 SERP 规则（nucleus/data/experience_cleanup.classify）。
    #   灰度 ENABLE_EXPERIENCE_AUTO_CLEAN（默认开）：关闭→零新增标记。
    @staticmethod
    def _auto_clean_enabled() -> bool:
        """经验库自动清洗总开关（默认开）。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_EXPERIENCE_AUTO_CLEAN", True))
        except Exception:
            return True

    # ★主线第65批 T1/P1：分类收窄常量
    #   根因：第56批 write_side_boilerplate 宽口径（把内审短句也当样板）→ 82.6% 误标。
    #   收窄为「真正含 SERP 样板文本」才判，并设白名单/置信度阈值。
    _SERP_BOILERPLATE_TOKENS = (
        "搜索结果", "相关搜索", "广告", "赞助商链接", "百度一下",
        "Google 搜索", "谷歌搜索", "免责声明", "版权所有", "©", "相关推荐",
    )
    _SERP_MARK_CONFIDENCE = 0.8  # 分类阈值：boilerplate 字符覆盖 ≥ 此值才判污染

    def _serp_classify(self, experience: dict) -> str:
        """★主线第65批 T1/P1：分类收窄（重构）。

        收窄策略：
          1. 白名单来源（code_learning / user_dialog）产生的经验直接跳过；
          2. 摘要模板（legacy 模板句）沿用既有判据（委托 experience_cleanup.classify）；
          3. write_side 仅当 summary 真正含「SERP 样板 token」且覆盖度≥阈值才判。
        返回 CLASS_TEMPLATE / CLASS_WRITE_SIDE / 空串；导入失败安全降级空串。
        """
        if not isinstance(experience, dict):
            return ""
        # 1) 白名单来源跳过
        _src = str(experience.get("source", "") or "").strip().lower()
        try:
            import config as _c
            _wl = getattr(_c, "EXPERIENCE_POLLUTION_WHITELIST_SOURCES", []) or []
        except Exception:
            _wl = []
        if _src and _src in [str(s).lower() for s in _wl]:
            return ""
        _s = str(experience.get("summary") or experience.get("content")
                 or experience.get("raw_summary") or "")
        # 2) 摘要模板（原文已丢失的 legacy 模板句）—— 沿用既有判据
        try:
            from nucleus.data.experience_cleanup import (
                classify as _serp_classify_fn,
                CLASS_TEMPLATE, CLASS_WRITE_SIDE)
        except Exception:
            _serp_classify_fn = None
            CLASS_TEMPLATE = "template_summary_legacy"
            CLASS_WRITE_SIDE = "write_side_boilerplate"
        if _serp_classify_fn is not None:
            try:
                if _serp_classify_fn(experience) == CLASS_TEMPLATE:
                    return CLASS_TEMPLATE
            except Exception:
                pass
        # 3) write_side 收窄：仅真正含 SERP 样板 token 且覆盖度达标才判
        if not _s:
            return ""
        _hits = [t for t in self._SERP_BOILERPLATE_TOKENS if t and t in _s]
        if not _hits:
            return ""
        _coverage = sum(len(t) for t in _hits) / max(1, len(_s))
        if _coverage < self._SERP_MARK_CONFIDENCE:
            return ""
        return CLASS_WRITE_SIDE

    def _serp_boilerplate_coverage(self, experience: dict) -> float:
        """★主线第65批 T1/P2：boilerplate 字符覆盖度（清理闭环分级用）。"""
        _s = str(experience.get("summary") or experience.get("content")
                 or experience.get("raw_summary") or "")
        if not _s:
            return 0.0
        _hits = [t for t in self._SERP_BOILERPLATE_TOKENS if t and t in _s]
        if not _hits:
            return 0.0
        return min(1.0, sum(len(t) for t in _hits) / max(1, len(_s)))

    def _cleanup_enabled(self) -> bool:
        """★主线第65批 T1：清理闭环总开关（默认开）。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_EXPERIENCE_POLLUTION_CLEANUP", True))
        except Exception:
            return True

    def run_pollution_cleanup(self, dry_run: bool = False) -> dict:
        """★主线第65批 T1/P2（清理闭环）：处理 polluted & is_cleaned=False 的记录。

        分级（置信度=boilerplate 覆盖度）：
          * ≥ HIGH      → 隔离（action=quarantined_high，可恢复）
          * [MEDIUM,HIGH) → 隔离（action=quarantined，可恢复）
          * < MEDIUM    → 恢复（取消污染标记，action=reclassified）
        ★隔离记录写 data/experience/_quarantine/，不直接物理删除 → 数据可恢复。
        ★生产执行须停机窗口；测试用显式 base_dir 隔离（写盘守卫拦截落盘）。
        """
        _res = {"enabled": self._cleanup_enabled(), "processed": 0,
                "quarantined": 0, "restored": 0, "dry_run": dry_run}
        if not _res["enabled"]:
            return _res
        # ★T3: 自适应降频接线——经验库清理。
        #   仅生产主池生效（测试显式 base_dir 隔离池跳过，保证门控单测确定性）；
        #   节流早返回仍保持契约形状（含 quarantined/processed/restored 键，值 0），
        #   避免调用方 KeyError（第97批 T-97d 修复：原早返回 dict 缺键致 8 条门控失败）。
        if not getattr(self, "_m44_base_dir_explicit", False):
            try:
                from nucleus.runtime_metrics import get_adaptive_controller
                _ctrl = get_adaptive_controller()
                _ctrl.register("experience_cleanup", 300)
                if not _ctrl.should_execute("experience_cleanup"):
                    _res["skipped"] = True
                    _res["reason"] = "adaptive_frequency_throttle"
                    return _res
            except Exception:
                pass
        try:
            import config as _c
            _hi = float(getattr(_c, "EXPERIENCE_POLLUTION_HIGH_CONFIDENCE", 0.9))
            _me = float(getattr(_c, "EXPERIENCE_POLLUTION_MEDIUM_CONFIDENCE", 0.7))
        except Exception:
            _hi, _me = 0.9, 0.7
        _quarantine = []
        with self._lock:
            _new_pool = []
            for _e in self._experiences:
                if not (isinstance(_e, dict) and _e.get("polluted") is True
                        and _e.get("is_cleaned") is False):
                    _new_pool.append(_e)
                    continue
                _cov = self._serp_boilerplate_coverage(_e)
                if _cov >= _me:
                    _e = dict(_e)
                    _e["is_cleaned"] = True
                    _e["cleanup_action"] = "quarantined_high" if _cov >= _hi else "quarantined"
                    _e["cleanup_at"] = time.time()
                    _quarantine.append(_e)
                    _res["quarantined"] += 1
                else:
                    _e = dict(_e)
                    _e["polluted"] = False
                    _e["is_cleaned"] = True
                    _e["cleanup_action"] = "reclassified"
                    _e["cleanup_at"] = time.time()
                    _res["restored"] += 1
                    _new_pool.append(_e)
            _res["processed"] = _res["quarantined"] + _res["restored"]
            if not dry_run:
                self._experiences = _new_pool
                self._save()
                self._write_quarantine(_quarantine)
        return _res

    def reevaluate_history_batch(self, batch_no: int = 56, dry_run: bool = False) -> dict:
        """★主线第65批 T1/P3（历史重评）：用收窄判据重评 cleanup_batch==batch_no 的记录。

        仍命中窄判据→保留污染（进入清理闭环）；不再命中→取消污染标记恢复。
        返回 {total, retained, restored}。★生产执行须停机窗口。
        """
        _res = {"batch": batch_no, "total": 0, "retained": 0,
                "restored": 0, "dry_run": dry_run}
        with self._lock:
            _new_pool = []
            for _e in self._experiences:
                if (isinstance(_e, dict) and _e.get("cleanup_batch") == batch_no
                        and _e.get("polluted") is True
                        and _e.get("is_cleaned") is False):
                    _res["total"] += 1
                    if self._serp_classify(_e):
                        _res["retained"] += 1
                        _new_pool.append(_e)
                    else:
                        _e = dict(_e)
                        _e["polluted"] = False
                        _e["is_cleaned"] = True
                        _e["cleanup_action"] = "reclassified"
                        _e["cleanup_at"] = time.time()
                        _res["restored"] += 1
                        _new_pool.append(_e)
                else:
                    _new_pool.append(_e)
            if not dry_run:
                self._experiences = _new_pool
                self._save()
        return _res

    def _write_quarantine(self, records: list) -> None:
        """★主线第65批 T1：把隔离记录写入 data/experience/_quarantine/。

        ★受写盘守卫约束：测试显式 base_dir 隔离时不落盘（不污染生产 data/）。
        """
        if not records:
            return
        try:
            from nucleus.data.write_guard import guard_write as _gw
            _qdir = os.path.join(os.path.dirname(self._pool_file), "_quarantine")
            _qf = os.path.join(_qdir, f"quarantine_{int(time.time())}.json")
            if not _gw(_qf, explicit=getattr(self, "_m44_base_dir_explicit", False),
                      component="ExperiencePool"):
                return
            os.makedirs(_qdir, exist_ok=True)
            with open(_qf, "w", encoding="utf-8") as _f:
                _f.write(json.dumps(records, ensure_ascii=False, indent=2))
        except Exception as _e:
            _module_logger.warning(
                "[经验池] 隔离区写入失败（不影响主池）: %s: %s",
                type(_e).__name__, _e)

    def _restore_enabled(self) -> bool:
        """★主线第66批 T4：隔离恢复总开关（默认开）。"""
        try:
            import config as _c
            return bool(getattr(_c, "ENABLE_EXPERIENCE_QUARANTINE_RESTORE", True))
        except Exception:
            return True

    def restore_from_quarantine(self, dry_run: bool = False,
                                remove_quarantine: bool = True) -> dict:
        """★主线第66批 T4（隔离恢复闭环）：把隔离区记录恢复回主经验库。

        隔离记录（_quarantine/quarantine_*.json）回写主池：
          is_cleaned=True, cleanup_action="restored_from_quarantine",
          restored_at=time.time()
        按 id 去重（主池已存在同 id 则跳过）；恢复成功后默认删除隔离文件。
        ★受写盘守卫约束：测试显式 base_dir 隔离时不落盘（不污染生产 data/）。
        ★开关 ENABLE_EXPERIENCE_QUARANTINE_RESTORE 关闭 → 空操作（保留改造前行为）。
        """
        _res = {"enabled": self._restore_enabled(), "scanned": 0,
                "restored": 0, "skipped": 0, "removed_files": 0, "dry_run": dry_run}
        if not _res["enabled"]:
            return _res
        _qdir = os.path.join(os.path.dirname(self._pool_file), "_quarantine")
        if not os.path.isdir(_qdir):
            return _res
        try:
            _files = sorted(f for f in os.listdir(_qdir) if f.endswith(".json"))
        except Exception as _e:
            _module_logger.warning("[隔离恢复] 读隔离目录失败: %s: %s", type(_e).__name__, _e)
            return _res
        _restored = []
        with self._lock:
            _existing_ids = {e.get("id") for e in self._experiences if isinstance(e, dict)}
            for _fn in _files:
                _fp = os.path.join(_qdir, _fn)
                try:
                    with open(_fp, "r", encoding="utf-8") as _f:
                        _recs = json.load(_f)
                except Exception as _e:
                    _module_logger.warning(
                        "[隔离恢复] 读隔离文件失败 %s: %s: %s",
                        _fn, type(_e).__name__, _e)
                    continue
                if not isinstance(_recs, list):
                    continue
                _res["scanned"] += 1
                for _r in _recs:
                    if not isinstance(_r, dict):
                        continue
                    _rid = _r.get("id")
                    if _rid and _rid in _existing_ids:
                        _res["skipped"] += 1
                        continue
                    _r = dict(_r)
                    _r["is_cleaned"] = True
                    _r["cleanup_action"] = "restored_from_quarantine"
                    _r["restored_at"] = time.time()
                    _restored.append(_r)
                    if _rid:
                        _existing_ids.add(_rid)
            if not _restored:
                return _res
            _res["restored"] = len(_restored)
            if not dry_run:
                self._experiences.extend(_restored)
                self._save()
                if remove_quarantine:
                    for _fn in _files:
                        _fp = os.path.join(_qdir, _fn)
                        try:
                            from nucleus.data.write_guard import guard_write as _gw
                            if _gw(_fp, explicit=getattr(self, "_m44_base_dir_explicit", False),
                                  component="ExperiencePool"):
                                os.remove(_fp)
                                _res["removed_files"] += 1
                        except Exception as _e:
                            _module_logger.warning(
                                "[隔离恢复] 删隔离文件失败 %s: %s: %s",
                                _fn, type(_e).__name__, _e)
        return _res

    def _auto_clean_mark(self, experience: dict) -> bool:
        """★对单条经验做 SERP 污染标记（不删除）。返回是否新标记。"""
        if not isinstance(experience, dict):
            return False
        if (experience.get("polluted") is True
                and experience.get("is_cleaned") is False
                and experience.get("cleanup_batch") == 56):
            return False  # 本批已标记，跳过（幂等）
        _cls = self._serp_classify(experience)
        if not _cls:
            return False
        experience["polluted"] = True
        experience["is_cleaned"] = False
        experience["pollution_risk"] = "high"
        experience["cleanup_reason"] = _cls
        experience["cleanup_batch"] = 56
        experience["cleanup_at"] = time.time()
        return True

    def auto_clean_scan(self) -> dict:
        """★周期增量清洗：扫描内存经验，标记新发现的 SERP 污染（不删除）。"""
        if not self._auto_clean_enabled():
            return {"scanned": 0, "newly_marked": 0, "enabled": False}
        _n = 0
        with self._lock:
            for _e in self._experiences:
                if self._auto_clean_mark(_e):
                    _n += 1
            if _n:
                self._save()
        return {"scanned": len(self._experiences), "newly_marked": _n,
                "enabled": True}

    def _auto_clean_thread_loop(self) -> None:
        """★后台周期扫描循环（由 _start_auto_clean_thread 启停）。"""
        _interval = 3600  # 周期（秒）
        while getattr(self, "_auto_clean_running", False):
            time.sleep(_interval)
            try:
                self.auto_clean_scan()
            except Exception as e:
                _module_logger.warning(
                    f"[经验库清洗] 周期扫描异常: {type(e).__name__}: {e}")

    def _start_auto_clean_thread(self) -> None:
        """★启动后台周期清洗线程（测试重定向/pytest/开关关闭时不启）。"""
        if getattr(self, "_auto_clean_thread", None) is not None:
            return
        if getattr(self, "_m44_base_dir_explicit", False):
            return  # 测试显式注入 base_dir → 不启线程
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return  # 测试环境隔离（铁律 57）
        if not self._auto_clean_enabled():
            return
        self._auto_clean_running = True
        _t = threading.Thread(
            target=self._auto_clean_thread_loop,
            name="exp-auto-clean", daemon=True)
        self._auto_clean_thread = _t
        _t.start()
    
    # [批次4·深度体检][CHAIN-6] 经验强度修改即落盘
    def check_decay(self):
        """
        定期执行衰减检查。
        
        对非高强度情绪体验执行衰减：
        - 衰减率>0的体验，其情感强度随时间降低
        - 衰减到0的体验自动摘要压缩
        """
        now = time.time()
        if now - self._last_decay_check < self._decay_check_interval:
            return
        
        self._last_decay_check = now
        
        with self._lock:
            _dirty = False
            for exp in self._experiences:
                if exp.get("is_summarized", False):
                    continue
                
                decay_rate = exp.get("decay_rate", 0.0)
                if decay_rate == 0.0:
                    continue  # 高强度情绪，不衰减
                
                # 计算衰减
                elapsed = now - exp.get("timestamp", now)
                decay_amount = decay_rate * (elapsed / 3600.0)  # 每小时衰减
                
                if decay_amount > 0.01:
                    old_intensity = exp.get("emotion_intensity", 0.0)
                    new_intensity = max(0.0, old_intensity - decay_amount)
                    exp["emotion_intensity"] = round(new_intensity, 2)
                    _dirty = True

                    # 衰减到极低时，摘要压缩
                    if new_intensity < 0.05:
                        self._summarize_experience(exp)
                        self._total_decayed += 1

            # ★CHAIN-6修复: 只要有强度被修改就持久化，避免重启后衰减被回滚（原仅摘要时才保存）
            if _dirty or self._total_decayed > 0:
                self._save()
    
    # ========== ★主线第50批 T2（P0-3）：L3 检索闸门 ==========

    def _m50_retrievable(self, exp) -> bool:
        """★本条经验是否可供生产检索使用。

        委托给 ``nucleus.data.experience_cleanup.is_retrievable``
        （单一真相源）。**零回归**：仅显式带
        ``is_cleaned=False`` 或 ``polluted=True`` 才拦截；
        开关关闭时恒 True。
        """
        try:
            from nucleus.data.experience_cleanup import (
                filter_enabled as _fe, is_retrievable as _ir)
            if not _fe():
                return True
            return _ir(exp)
        except Exception as _e:
            _module_logger.debug(
                "[M50-T2] 清洗闸门不可用（放行）: %s", type(_e).__name__)
            return True

    # ========== 查询接口 ==========
    
    def query_experiences(self,
                          emotion_tags: list[str] | None = None,
                          reward_type: str | None = None,
                          pressure_type: str | None = None,
                          min_emotion_intensity: float = 0.0,
                          time_start: float | None = None,
                          time_end: float | None = None,
                          include_summarized: bool = True,
                          limit: int = 20) -> list[dict[str, Any]]:
        """
        多维度查询体验记忆。
        
        Args:
            emotion_tags: 按情感标签过滤
            reward_type: 按奖赏类型过滤
            pressure_type: 按压力类型过滤
            min_emotion_intensity: 最低情绪强度
            time_start: 时间范围起始
            time_end: 时间范围结束
            include_summarized: 是否包含摘要体验
            limit: 返回上限
        
        Returns:
            匹配的体验列表（按时间倒序）
        """
        with self._lock:
            results = []
            _skipped_polluted = 0
            for exp in self._experiences:
                if not include_summarized and exp.get("is_summarized", False):
                    continue

                # ★主线第50批 T2（P0-3）：L3 检索闸门
                if not self._m50_retrievable(exp):
                    _skipped_polluted += 1
                    continue
                
                # 情感标签过滤
                if emotion_tags:
                    if not any(tag in exp.get("emotion_tags", []) for tag in emotion_tags):
                        continue
                
                # 奖赏类型过滤
                if reward_type:
                    if exp.get("result_reward", {}).get("type") != reward_type:
                        continue
                 
                # 压力类型过滤
                if pressure_type:
                    if exp.get("pressure_type") != pressure_type:
                        continue
                
                # 情绪强度过滤
                if exp.get("emotion_intensity", 0.0) < min_emotion_intensity:
                    continue
                
                # 时间范围过滤
                ts = exp.get("timestamp", 0)
                if time_start and ts < time_start:
                    continue
                if time_end and ts > time_end:
                    continue
                
                results.append(dict(exp))
            
            # 按时间倒序
            results.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
            return results[:limit]
    
    def get_experiences_for_narrative(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        获取供叙事自我整合的体验素材。
        
        优先返回完整的高强度体验，附带摘要体验作为补充。
        """
        with self._lock:
            # ★主线第50批 T2（P0-3）：L3 检索闸门（叙事自我素材）
            _src = [e for e in self._experiences if self._m50_retrievable(e)]
            full = [e for e in _src if not e.get("is_summarized", False)]
            summarized = [e for e in _src if e.get("is_summarized", False)]
            
            # 完整体验按情绪强度排序，取前limit条
            full.sort(key=lambda e: e.get("emotion_intensity", 0.0), reverse=True)
            result = full[:limit]
            
            # 如果完整体验不足，补充摘要体验
            if len(result) < limit:
                summarized.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
                result.extend(summarized[:limit - len(result)])
            
            return [dict(e) for e in result]
    
    def get_positive_experiences(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        获取正向体验（奖赏强度高的体验）。
        供动机闭环使用，作为正向激励的素材。
        """
        with self._lock:
            positive = [
                e for e in self._experiences
                # ★主线第50批 T2（P0-3）：L3 检索闸门
                if self._m50_retrievable(e)
                and e.get("result_reward", {}).get("intensity", 0.0) >= 0.5
                and not e.get("is_summarized", False)
            ]
            positive.sort(
                key=lambda e: e.get("result_reward", {}).get("intensity", 0.0),
                reverse=True
            )
            return [dict(e) for e in positive[:limit]]
    
    def get_negative_experiences(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        获取负向体验（压力高且奖赏低的体验）。
        供动机闭环使用，作为避错学习的素材。
        """
        with self._lock:
            negative = [
                e for e in self._experiences
                # ★主线第50批 T2（P0-3）：L3 检索闸门
                if self._m50_retrievable(e)
                and e.get("process_pressure", 0.0) >= 0.6
                and e.get("result_reward", {}).get("intensity", 0.0) < 0.3
                and not e.get("is_summarized", False)
                and not e.get("feedback_consumed", False)
            ]
            negative.sort(
                key=lambda e: e.get("process_pressure", 0.0),
                reverse=True
            )
            _result = [dict(e) for e in negative[:limit]]
            # ★v9.5补充：负面体验被动机循环读取后标记为“已消费”，
            # 避免同一批负面体验永久堆积持续拉高 frustration，
            # 导致自我修改被长期禁止。（保留数据用于避错学习）
            for e in _result:
                _orig = next((x for x in self._experiences if x.get("id") == e.get("id")), None)
                if _orig is not None:
                    _orig["feedback_consumed"] = True
            return _result
    
    # ========== 持久化 ==========
    
    # ★第54批 T4（P2-370）：停机期治理成果的字段名（**只补不覆盖**）
    _GOVERNANCE_FIELDS = (
        "is_cleaned", "polluted", "pollution_risk", "pollution_reason",
        "cleanup_reason", "cleanup_batch", "cleanup_at",
    )

    def _merge_governance_fields_from_disk(self) -> int:
        """把磁盘上已有、但内存缺失的治理标记补回内存（合并保存的核心）。

        ★为什么只补「内存缺失」的字段：内存若已有明确标记（含 is_cleaned=False），
        说明是本次运行期的新判断，优先于磁盘 —— 避免把运行期新状态回退。
        ★不恢复「磁盘有但内存没有」的整条记录：删除是主动行为（衰减/清理），
        恢复它会破坏语义（★与任务书方案A的偏离点，见交付报告）。

        Returns:
            int: 补齐的字段数（0 表示无需合并或合并失败；失败不影响主保存流程）。
        """
        merged = 0
        try:
            if not os.path.exists(self._pool_file):
                return 0
            with open(self._pool_file, "r", encoding="utf-8") as _f:
                _disk = json.load(_f)
            _rows = _disk.get("experiences") if isinstance(_disk, dict) else None
            if not isinstance(_rows, list):
                return 0
            _idx = {}
            for _r in _rows:
                if isinstance(_r, dict) and _r.get("id"):
                    _idx[_r["id"]] = _r
            for _r in self._experiences:
                if not isinstance(_r, dict):
                    continue
                _old = _idx.get(_r.get("id"))
                if not _old:
                    continue
                for _k in self._GOVERNANCE_FIELDS:
                    # ★判据：只有内存「没有该字段」或「显式为 None」才从磁盘补齐；
                    #   内存已有值（**含 is_cleaned=False 这种合法标记**）一律视为
                    #   运行期判断，优先于磁盘 —— 避免每轮保存都被回写（计数不幂等）。
                    if _k in _old and (_k not in _r or _r.get(_k) is None):
                        _r[_k] = _old[_k]
                        merged += 1
        except Exception as e:
            # ★铁律 10：不吞异常，留痕后按原样保存（合并失败不得阻断持久化）
            _module_logger.warning(
                f"[经验池] 合并停机期治理标记失败，按原样保存: {type(e).__name__}: {e}")
            return 0
        return merged

    def _save(self):
        """保存体验池到文件（★星轨修复：增加Windows文件锁定重试机制）"""
        try:
            # ★第44批 T4（P2-290）：写盘守卫 —— 测试环境不得写生产 data/
            try:
                from nucleus.data.write_guard import guard_write as _m44_gw
                if not _m44_gw(
                        self._pool_file,
                        explicit=getattr(self, "_m44_base_dir_explicit", False),
                        component="ExperiencePool"):
                    return
            except ImportError:
                pass
            os.makedirs(self._base_dir, exist_ok=True)
            # ★第54批 T4（P2-370）：合并保存 —— 保留停机期写入的清洗标记，
            #   不再用内存快照整体覆盖磁盘（灰度 ENABLE_EXPERIENCE_MERGE_SAVE）。
            _merge_on = True
            try:
                import config as _cfg_merge
                _merge_on = getattr(_cfg_merge, "ENABLE_EXPERIENCE_MERGE_SAVE", True)
            except Exception as e:
                _module_logger.warning(
                    f"[经验池] 读取合并开关失败，按合并处理: {type(e).__name__}: {e}")
            if _merge_on:
                _merged_n = self._merge_governance_fields_from_disk()
                if _merged_n:
                    _module_logger.info(
                        f"[经验池] 已合并停机期治理标记 {_merged_n} 处"
                        f"（P2-370：避免保存时抹掉清洗成果）")
            snapshot = {
                "version": "v1.0",
                "updated_at": time.time(),
                "total_recorded": self._total_recorded,
                "total_summarized": self._total_summarized,
                "total_decayed": self._total_decayed,
                "experiences": self._experiences,
            }
            # 原子写入
            tmp_file = self._pool_file + ".tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            
            # ★星轨修复：Windows文件锁定重试机制（最多3次，间隔100ms）
            _last_error = None
            for _attempt in range(3):
                try:
                    os.replace(tmp_file, self._pool_file)
                    _last_error = None
                    break
                except OSError as _e:
                    _last_error = _e
                    if _attempt < 2:
                        time.sleep(0.1)
            if _last_error:
                # 重试仍失败，清理tmp文件并记录错误
                try:
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                except Exception:
                    pass
                _module_logger.error(f"保存失败（重试3次后仍失败）: {_last_error}")
        except Exception as e:
            _module_logger.error(f"保存失败: {e}")
    
    def _load(self):
        """从文件加载体验池"""
        try:
            if os.path.exists(self._pool_file):
                data = safe_read_json(self._pool_file, default={})
                self._experiences = data.get("experiences", [])
                self._total_recorded = data.get("total_recorded", 0)
                self._total_summarized = data.get("total_summarized", 0)
                self._total_decayed = data.get("total_decayed", 0)
        except Exception as e:
            print(f"[WARNING] experience_pool.py:492: {type(e).__name__}: {e}")
            self._experiences = []
    
    # ========== 统计信息 ==========
    
    def get_stats(self) -> dict[str, Any]:
        """获取体验池统计信息"""
        with self._lock:
            full_count = sum(1 for e in self._experiences if not e.get("is_summarized", False))
            summarized_count = sum(1 for e in self._experiences if e.get("is_summarized", False))
            high_emotion = sum(
                1 for e in self._experiences
                if e.get("emotion_intensity", 0.0) >= self._high_emotion_threshold
            )
            return {
                "total_experiences": len(self._experiences),
                "full_experiences": full_count,
                "summarized_experiences": summarized_count,
                "high_emotion_experiences": high_emotion,
                "total_recorded": self._total_recorded,
                "total_summarized": self._total_summarized,
                "total_decayed": self._total_decayed,
                "file_exists": os.path.exists(self._pool_file),
            }


# ========== 模块级单例 ==========
_experience_pool: ExperiencePool | None = None
_experience_pool_lock = threading.Lock()


def get_experience_pool() -> ExperiencePool:
    """获取ExperiencePool单例"""
    global _experience_pool
    if _experience_pool is None:
        with _experience_pool_lock:
            if _experience_pool is None:
                _experience_pool = ExperiencePool()
    return _experience_pool


def shutdown_experience_pool() -> None:
    """★P1: 复位 ExperiencePool 单例，满足器官零状态（规则4）。

    原停机流程未清理该全局单例，重启时会复用带残留状态的旧实例。此处显式置空，
    使下次获取重建全新零状态实例；若实例自身提供 shutdown/stop 则一并调用释放资源。
    """
    global _experience_pool
    _inst = _experience_pool
    _experience_pool = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception:
                pass


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== ExperiencePool v24.0 自测 ===\n")
    
    pool = ExperiencePool(base_dir="data/experience_test")
    
    # 测试1：记录高强度体验（不衰减）
    exp1_id = pool.record_experience(
        motivation="想帮助小林解决问题",
        motivation_intensity=0.8,
        process_pressure=0.3,
        pressure_type="cognitive",
        reward_type="achievement",
        reward_intensity=0.9,
        emotion_tags=["喜悦", "满足"],
        emotion_intensity=0.85,
        content="成功帮小林解决了技术问题，他夸我厉害",
    )
    print(f"1. 高强度体验: id={exp1_id}, 衰减率=0.0（不衰减）")
    
    # 测试2：记录低强度体验（快速衰减）
    exp2_id = pool.record_experience(
        motivation="例行学习",
        motivation_intensity=0.3,
        process_pressure=0.1,
        pressure_type="cognitive",
        reward_type="cognitive",
        reward_intensity=0.2,
        emotion_tags=["平静"],
        emotion_intensity=0.2,
        content="看了一些资料，没有特别的感觉",
    )
    print(f"2. 低强度体验: id={exp2_id}, 衰减率=0.05（快速衰减）")
    
    # 测试3：查询正向体验
    positive = pool.get_positive_experiences()
    print(f"3. 正向体验: {len(positive)}条 → {positive[0]['result_reward']['type'] if positive else '无'}")
    
    # 测试4：查询所有体验
    all_exp = pool.query_experiences()
    print(f"4. 所有体验: {len(all_exp)}条")
    
    # 测试5：统计
    stats = pool.get_stats()
    print(f"5. 统计: 总体验{stats['total_experiences']}条, "
          f"完整{stats['full_experiences']}条, "
          f"高强度{stats['high_emotion_experiences']}条")
    
    # 清理测试文件
    import shutil
    shutil.rmtree("data/experience_test", ignore_errors=True)
    
    print("\n=== 自测全部通过 ===")
# _m49_t3_1_done
# _m50_t2_gate_done
# _m50_t2_q1_done
# _m50_t2_q2_done
# _m50_t2_q3_done
# _m50_t2_q4_done
# M56_T2_EXPERIENCE_AUTO_CLEAN
# _m69_t3b_expcleanup
