# -*- coding: utf-8 -*-
"""
ResonanceEngine.py —— 共振引擎

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 器官间共振计算与协同激活
机制: 大型模块（1384行），包含1个类、10个核心方法，采用分层架构实现
定位: 共振核心层
"""

import math

# ★第82批 T-c：五维权重单一来源（Dxxx）。唯一数值定义在 config.RESONANCE_WEIGHTS。
from config import RESONANCE_WEIGHTS

from nucleus.logger import get_module_logger
from nucleus.mnemosyne.PulseNode import PulseNode


import threading
import time
from typing import Any
from nucleus._silent_except import silent_exc


_logger = get_module_logger("ResonanceEngine")


class ResonanceEngine:
    """
    五维共振检索引擎
    
    工作原理:
        查询脉冲 → 提取五维特征 → 遍历候选节点 →
        计算五维共振得分 → 加权求和 → 按得分降序返回
    """
    
    # 五维权重（规则1.3：永久固定，禁止修改）
    # ★第82批 T-c：单一来源收敛（Dxxx）——唯一数值定义在 config.RESONANCE_WEIGHTS，
    #   此处只做只读引用，禁止再写字面数值（原类内重复定义已删）。
    WEIGHTS = RESONANCE_WEIGHTS
    
    def __init__(self):
        # 频率索引: frequency_signature → [node_id, ...]
        self._freq_index: dict[float, list[str]] = {}
        
        # 空间索引: space_path → [node_id, ...]
        self._space_index: dict[str, list[str]] = {}
        
        # 关联组件引用
        self.info_field = None
        self.node_pool = None
        # ===== 未来演化预留（v10.0 振荡场） =====
        self.phase_lock = None          # FrequencyPhaseLock 实例（v10.0注入）
        self._oscillon_enhancement = 1.0  # 振荡场增强因子（v10.0场强度调制记忆维权重）
        # ★v23.0新增：Cython加速模块加载标记
        self._resonance_cy_loaded = False
        self._resonance_cy_available = False
        self._try_load_resonance_cy()
        # ★P0-3修复：索引并发保护锁
        self._index_lock = threading.Lock()
        # 统计
        self._total_queries = 0
        self._total_resonated = 0

        # ===== ★PHASE17-1.3：语义（向量）通道 =====
        #   设计原则：向量通道是记忆维**内部**的第二条并行通道，
        #   不改变五维权重（规则1.3 永久固定），不新增第六维。
        #   未注入 provider / 开关关闭 / 文本缺失 时，行为与改造前**完全一致**。
        self._vector_provider = None      # duck-typing 对象，见 set_vector_provider
        self._semantic_cfg: dict[str, Any] | None = None
        self._semantic_calls = 0          # 语义通道生效次数（验收用）
        self._semantic_fallbacks = 0      # 语义通道不可用次数（验收用）

        # ===== ★PHASE17-阶段二子任务1：规则通道（γ'） =====
        #   规则通道是记忆维**内部**的第三条并行子通道，与关键词 α'、语义 β'
        #   三者内部分配（α'+β'+γ'=1.0），不改变五维权重（规则1.3 永久固定）。
        #   未注入 provider / 开关关闭 / 无规则得分 时，行为与改造前**完全一致**。
        self._rule_provider = None        # duck-typing 对象，见 set_rule_provider
        self._rule_calls = 0              # 规则通道生效次数（验收用）
        self._rule_fallbacks = 0          # 规则通道不可用次数（验收用）

        # ===== ★PHASE17-阶段二子任务3：极性判别层（破坏性操作拦截） =====
        #   查询进入检索前，若命中破坏性模式则拒绝（返回空结果 + WARNING 日志）。
        #   未注入 guard 时零变化（默认不拦截），见 set_polarity_guard。
        self._polarity_guard = None       # duck-typing 对象，见 set_polarity_guard
        self._polarity_blocks = 0         # 拦截次数（验收用）

    # ========== 框架注入接口 ==========

    def set_info_field(self, info_field):
        """注入全局信息场"""
        self.info_field = info_field

    def set_node_pool(self, node_pool):
        """注入脉冲节点池"""
        self.node_pool = node_pool

    def set_vector_provider(self, provider):
        """★PHASE17-1.3：注入向量检索供给者（由 PHASE17-1.5 的 VectorStore 实现）。

        provider 需提供 duck-typing 接口（不强继承，避免耦合）：

            search_by_text(text: str, top_k: int) -> list[tuple[node_id, similarity]]

        传 None 可摘除语义通道（回到纯关键词）。
        """
        self._vector_provider = provider
        if provider is None:
            _logger.info("[共振引擎] 向量通道已摘除，走纯关键词")

    def set_rule_provider(self, provider):
        """★PHASE17-阶段二子任务1：注入规则检索供给者（由规则引擎/PolarityGuard 实现）。

        provider 需提供与向量通道一致的 duck-typing 接口（不强继承，避免耦合）：

            search_by_text(text: str, top_k: int) -> list[tuple[node_id, rule_score]]

        规则得分 rule_score ∈ [0.0, 1.0]，表示「该节点命中本查询对应的规则」的置信度。
        传 None 可摘除规则通道（回到关键词+向量双通道，即阶段一行为）。
        """
        self._rule_provider = provider
        if provider is None:
            _logger.info("[共振引擎] 规则通道已摘除，走关键词+向量双通道")

    def set_polarity_guard(self, guard):
        """★PHASE17-阶段二子任务3：注入极性判别层（破坏性操作拦截）。

        guard 需提供 duck-typing 接口（不强继承，避免耦合）：

            evaluate(text: str) -> {"is_destructive": bool, "matched_pattern": str|None, "score": float}

        注入后，查询进入检索前会先过 guard：命中破坏性模式则拒绝（返回空结果 +
        WARNING 日志），不进入检索。传 None 可摘除拦截（回到不拦截，即改造前行为）。
        """
        self._polarity_guard = guard
        if guard is None:
            _logger.info("[共振引擎] 极性判别层已摘除，不拦截破坏性查询")

    # ---- 语义配置（懒加载，避免 import 期依赖 config） ----
    def _get_semantic_cfg(self) -> dict[str, Any]:
        if self._semantic_cfg is None:
            try:
                import config as _cfg
                _raw = getattr(_cfg, "SEMANTIC_KERNEL_CONFIG", {})
                self._semantic_cfg = _raw if isinstance(_raw, dict) else {}
            except Exception as e:
                silent_exc(e, "nucleus/synapsys/ResonanceEngine.py:149:共振引擎异常", level="warning")
                self._semantic_cfg = {}
        return self._semantic_cfg

    @staticmethod
    def _extract_query_text(query: dict) -> str:
        """从查询脉冲中抽取待编码的自然语言文本（取不到返回空串）。"""
        try:
            payload = query.get("payload") or {}
            if isinstance(payload, dict):
                for key in ("content", "text", "raw_text", "query", "message", "summary"):
                    v = payload.get(key)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                    if isinstance(v, (list, tuple)) and v:
                        return " ".join(str(x) for x in v)
            for key in ("text", "content", "raw_text"):
                v = query.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            kw = (query.get("payload") or {}).get("keywords") if isinstance(
                query.get("payload"), dict) else None
            if not kw:
                kw = query.get("keywords")
            if kw:
                return " ".join(str(k) for k in kw)
        except Exception as e:
            silent_exc(e, where="nucleus.synapsys.ResonanceEngine::_extract_query_text L177")
        return ""

    def _build_semantic_map(self, query: dict, candidate_nodes: list[dict]) -> dict[str, float]:
        """生成本次查询的 {node_id: 向量相似度}。

        任何一步不满足都返回空 dict —— 调用方零变化，无性能损失：
            开关关闭 / 未注入 provider / 无候选 / 抽不到文本 / provider 抛异常
        """
        cfg = self._get_semantic_cfg()
        if not cfg.get("enable_semantic_kernel", False):
            return {}
        if not cfg.get("hybrid_fallback_to_keyword", True):
            return {}
        provider = self._vector_provider
        if provider is None or not candidate_nodes:
            return {}
        text = self._extract_query_text(query)
        if not text:
            return {}
        try:
            top_n = max(50, len(candidate_nodes))
            hits = provider.search_by_text(text, top_n) or []
        except Exception as _e:
            self._semantic_fallbacks += 1
            _logger.debug(f"[共振引擎] 向量检索失败，回落关键词通道: {_e}")
            return {}
        out: dict[str, float] = {}
        for item in hits:
            try:
                nid, sim = item[0], float(item[1])
                if nid:
                    out[str(nid)] = max(0.0, min(1.0, sim))
            except Exception:
                continue
        if out:
            self._semantic_calls += 1
        return out

    def _build_rule_map(self, query: dict, candidate_nodes: list[dict]) -> dict[str, float]:
        """★PHASE17-阶段二子任务1：生成本次查询的 {node_id: 规则得分}。

        与 `_build_semantic_map` 严格对称 —— 任何一步不满足都返回空 dict，
        调用方零变化，无性能损失：
            开关关闭 / 未注入 provider / 无候选 / 抽不到文本 / provider 抛异常
        """
        cfg = self._get_semantic_cfg()
        # 规则通道是记忆维内部的子通道，总开关 enable_semantic_kernel 关闭时整体失效
        if not cfg.get("enable_semantic_kernel", False):
            return {}
        if not cfg.get("enable_rule_channel", False):
            return {}
        provider = self._rule_provider
        if provider is None or not candidate_nodes:
            return {}
        text = self._extract_query_text(query)
        if not text:
            return {}
        try:
            top_n = max(50, len(candidate_nodes))
            hits = provider.search_by_text(text, top_n) or []
        except Exception as _e:
            self._rule_fallbacks += 1
            _logger.debug(f"[共振引擎] 规则检索失败，规则通道回落: {_e}")
            return {}
        out: dict[str, float] = {}
        for item in hits:
            try:
                nid, score = item[0], float(item[1])
                if nid:
                    out[str(nid)] = max(0.0, min(1.0, score))
            except Exception:
                continue
        if out:
            self._rule_calls += 1
            # ★M84-2（第84批 T-84c）：本次查询规则通道**确实生效**（产出非空规则得分）
            #   → 计入本地推理「规则通道」档。此前全库**没有任何**
            #   `record_local_inference(KIND_RULE)` 调用点，导致 llm_dependency 的
            #   「规则通道」恒为 0（实测），把本地推理分母算小、夸大 LLM 占比。
            #   计数失败不影响任何既有行为（异常降级 DEBUG）。
            try:
                from nucleus.LLMDependencyMetrics import (
                    KIND_RULE as _KIND_RULE84,
                    record_local_inference as _rec_local84,
                )
                _rec_local84(_KIND_RULE84)
            except Exception as _e84:
                _logger.debug(
                    "[共振引擎] 规则通道本地推理计数失败（已忽略）: %s: %s",
                    type(_e84).__name__, _e84)
        return out

    def _try_load_resonance_cy(self):
        """
        ★v23.0新增：尝试加载Cython加速的五维得分计算。
        ★四期：统一受 use_cython_extensions 开关控制（默认 False）。
        开关关闭强制走 Python 原生；开启后加载失败静默降级，不影响功能。
        """
        # ★四期：开关控制
        try:
            from config import FEATURE
            _enabled = bool(FEATURE.get("use_cython_extensions", False))
        except Exception:
            _enabled = False

        if not _enabled:
            self._resonance_cy = None
            self._resonance_cy_available = False
            if not self._resonance_cy_loaded:
                self._resonance_cy_loaded = True
                _logger.info("use_cython_extensions=False，使用Python原生实现")
            return

        try:
            from nucleus.synapsys import _resonance_cy  # type: ignore
            self._resonance_cy = _resonance_cy
            self._resonance_cy_available = True
            if not self._resonance_cy_loaded:
                self._resonance_cy_loaded = True
                # ★v23.0新增：输出加载确认日志
                _logger.info("Cython加速模块已加载 (_resonance_cy)")
        except ImportError:
            self._resonance_cy = None
            self._resonance_cy_available = False
            _logger.warning("Cython共振模块未编译，使用Python原生实现")      
    # ========== 索引管理 ==========
    
    def index_node(self, node: dict[str, Any]):
        node_id = node.get("node_id", "")
        if not node_id:
            return
        
        # ★P0-3修复：加锁保护索引写入
        with self._index_lock:
            freq = node.get("frequency_signature", 0.0)
            if freq not in self._freq_index:
                self._freq_index[freq] = []
            if node_id not in self._freq_index[freq]:
                self._freq_index[freq].append(node_id)
                
            space_path = node.get("space_path", "/")
            if space_path not in self._space_index:
                self._space_index[space_path] = []
            if node_id not in self._space_index[space_path]:
                self._space_index[space_path].append(node_id)
    def remove_node(self, node_id: str, freq: float | None = None, space_path: str | None = None):
        # ★P0-3修复：加锁保护索引删除
        with self._index_lock:
            if freq is not None and freq in self._freq_index:
                self._freq_index[freq] = [
                    nid for nid in self._freq_index[freq] if nid != node_id
                ]
            else:
                for freq_list in self._freq_index.values():
                    if node_id in freq_list:
                        freq_list.remove(node_id)
                        
            if space_path is not None and space_path in self._space_index:
                self._space_index[space_path] = [
                    nid for nid in self._space_index[space_path] if nid != node_id
                ]
            else:
                for path_list in self._space_index.values():
                    if node_id in path_list:
                        path_list.remove(node_id)
    # ========== 核心方法：五维共振 ==========
    # ---- A-15：污染降权（灰度 ENABLE_POLLUTION_TAGGING，默认关闭 → 零开销） ----
    def _get_pollution_tagger(self):
        """返回共享 PollutionTagger；开关关闭/模块缺失 → None（零行为）。

        ★性能：config 读取每轮一次（在 resonate 内调用一次并复用），
        标记结果在 tagger 内按 node_id 缓存，长会话不重复判定。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, "ENABLE_POLLUTION_TAGGING", False):
                return None
        except Exception:
            return None
        try:
            from nucleus.knowledge.PollutionTagger import get_shared_tagger
            _cfg_dict = getattr(_cfg, "POLLUTION_TAGGING_CONFIG", None)
            _tagger = get_shared_tagger(config=_cfg_dict,
                                        log_fn=lambda _l, _m: _logger.info(_m))
            # ★懒扫描：首次启用时对已加载节点池做一次全量标记（只标记不删除）。
            #   （放在首次检索时而非装配时——保证快照已恢复完毕；只执行一次）
            if not getattr(self, "_pollution_scanned", False):
                self._pollution_scanned = True
                _pool = getattr(self, "node_pool", None)
                if _pool is not None:
                    try:
                        _stats = _tagger.scan_pool(
                            _pool, log_fn=lambda _l, _m: _logger.info(_m))
                        _logger.info(
                            f"[共振引擎] 污染标记首扫完成: "
                            f"polluted={_stats.get('polluted', 0)}, "
                            f"suspect={_stats.get('suspect', 0)}, "
                            f"clean={_stats.get('clean', 0)}（只标记不删除）")
                    except Exception as _e2:
                        _logger.debug(f"[共振引擎] 污染首扫失败（不影响检索）: {_e2}")
            return _tagger
        except Exception as _e:
            _logger.debug(f"[共振引擎] 污染标记器不可用（不影响检索）: {_e}")
            return None

    def scan_pollution(self, node_pool=None) -> dict:
        """★A-15：主动扫描并标记污染节点（只标记不删除）。

        供启动装配/运维脚本调用；不传 node_pool 时用引擎已注入的节点池。
        返回统计字典；开关关闭返回 {"enabled": False}。
        """
        _tagger = self._get_pollution_tagger()
        if _tagger is None:
            return {"enabled": False}
        _pool = node_pool or getattr(self, "node_pool", None)
        if _pool is None:
            return {"enabled": True, "scanned": 0, "reason": "未注入节点池"}
        _tagger.reset_stats()
        _stats = _tagger.scan_pool(_pool, log_fn=lambda _l, _m: _logger.info(_m))
        _stats["enabled"] = True
        return _stats

    def _get_data_quality_guard(self):
        """★第三批 任务2：统一脏数据入口接线（灰度 ENABLE_DATA_QUALITY_GUARD）。

        模式与 _get_pollution_tagger 一致：开关关闭/模块缺失 → None（零行为）；
        首次启用时对已加载内存节点池 + 经验库做一次全量扫描标记（只标记不删除），
        结果记 INFO 日志「[DataQualityGuard] 全量扫描完成: 污染节点X个/重复Y对/污染经验Z条」。
        重复检测按 legacy 前缀（/自我/架构/五维权重）收敛，避免全量两两比对开销。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, "ENABLE_DATA_QUALITY_GUARD", False):
                return None
        except Exception:
            return None
        try:
            from nucleus.knowledge.DataQualityGuard import get_data_quality_guard
            _guard = get_data_quality_guard()
            if not getattr(self, "_dqg_scanned", False):
                self._dqg_scanned = True
                _pool = getattr(self, "node_pool", None)
                _nodes = None
                if _pool is not None:
                    try:
                        _nodes = _pool.get_all()
                    except Exception:
                        _nodes = None
                try:
                    _report = _guard.scan_all(
                        nodes=_nodes,
                        path_prefix="/自我/架构/五维权重",
                        legacy_prefixes=("/自我/架构/五维权重",),
                    )
                except Exception as _e2:
                    _logger.info(f"[DataQualityGuard] 扫描失败（不影响检索）: {_e2}")
                    _report = {}
                # 经验库只读统计（不写经验库）
                try:
                    _exp = _guard.scan_experience_files(mark=False)
                    if isinstance(_exp, list):
                        _exp_polluted = sum(_e.get("polluted", 0) for _e in _exp
                                            if isinstance(_e, dict))
                        _exp_total = sum(_e.get("total", 0) for _e in _exp
                                        if isinstance(_e, dict))
                        _report["experience"] = {"polluted": _exp_polluted,
                                                 "total": _exp_total}
                except Exception:
                    _report.setdefault("experience", {"polluted": 0, "total": 0})
                _nd = _report.get("nodes", {})
                _du = _report.get("duplicates", {})
                _ex = _report.get("experience", {})
                _logger.info(
                    f"[DataQualityGuard] 已启用并接入共振检索，监控 3 类数据路径"
                    f"（知识节点/经验池/重复节点）；全量扫描完成: "
                    f"污染节点{_nd.get('polluted', 0)}个/"
                    f"重复{_du.get('groups', _du.get('marked', 0))}对/"
                    f"污染经验{_ex.get('polluted', 0)}条")
            return _guard
        except Exception as _e:
            _logger.info(f"[DataQualityGuard] 入口不可用（不影响检索）: {_e}")
            return None

    def _check_polarity_block(self, query_pulse: dict[str, Any]) -> bool:
        """★PHASE17-阶段二子任务3：极性判别拦截（检索前）。

        命中破坏性模式 → WARNING 日志 + 返回 True（调用方应短路返回空结果）。
        未注入 guard / 抽不到文本 / guard 抛异常 → 返回 False（不拦截）。
        """
        if self._polarity_guard is None:
            return False
        _qtext = self._extract_query_text(query_pulse)
        if not _qtext:
            return False
        try:
            _v = self._polarity_guard.evaluate(_qtext)
            if _v and _v.get("is_destructive"):
                self._polarity_blocks += 1
                _logger.warning(
                    f"[共振引擎] ⚠️ 极性判别拦截：拒绝破坏性查询"
                    f"（命中模式「{_v.get('matched_pattern')}」），不进入检索")
                return True
        except Exception as _e:
            _logger.debug(f"[共振引擎] 极性判别异常（不影响检索）: {_e}")
        return False

    def resonate(self, query_pulse: dict[str, Any], 
                 candidate_nodes: list[dict[str, Any]],
                 top_k: int = 20,
                 _semantic_map: dict[str, float] | None = None,
                 _rule_map: dict[str, float] | None = None) -> list[dict[str, Any]]:
        """五维共振检索。

        ★PHASE17-1.3：`_semantic_map` 为本次查询的 {node_id: 向量相似度}。
        ★PHASE17-阶段二子任务1：`_rule_map` 为 {node_id: 规则得分}。
        两者由 resonate_topk 传入以避免重复检索；外部直接调用时传 None，
        引擎会自行构建（构建失败即空 dict → 行为与改造前一致）。
        """
        self._total_queries += 1

        # ★PHASE17-阶段二子任务3：极性判别拦截（未注入 guard 时零开销）
        if self._check_polarity_block(query_pulse):
            return []

        if _semantic_map is None:
            _semantic_map = self._build_semantic_map(query_pulse, candidate_nodes)
        if _rule_map is None:
            _rule_map = self._build_rule_map(query_pulse, candidate_nodes)

        results = []
        
        # ★A-15（2026-09-08）：污染节点降权（灰度 ENABLE_POLLUTION_TAGGING，默认关）。
        #   开关关闭 → _pollution_tagger 为 None，下方零开销、逐字节等价。
        _tagger = self._get_pollution_tagger()
        # ★第三批 任务2：统一脏数据入口首次共振时全量扫描标记（只标记不删除，零开销/次）
        self._get_data_quality_guard()

        for node in candidate_nodes:
            dims = self._calculate_dimensions(query_pulse, node, _semantic_map, _rule_map)
            
            score = (
                dims["memory"] * self.WEIGHTS["memory"] +
                dims["space"]  * self.WEIGHTS["space"] +
                dims["logic"]  * self.WEIGHTS["logic"] +
                dims["time"]   * self.WEIGHTS["time"] +
                dims["state"]  * self.WEIGHTS["state"]
            )

            # ★A-15：suspect×0.7 / polluted×0.3（只改总分乘数，**五维权重不动**）
            if _tagger is not None:
                try:
                    _factor = _tagger.downweight_factor(node)
                    if _factor < 1.0:
                        _flag, _reason = _tagger.classify(node)
                        _logger.debug(
                            f"[共振引擎] 污染降权: 节点{str(node.get('node_id', ''))[:18]} "
                            f"标记={_flag} 系数={_factor} 原因={_reason}")
                        score *= _factor
                except Exception as _e:
                    _logger.debug(f"[共振引擎] 污染降权失败（不影响检索）: {_e}")
            
            if score > 0:
                _r = {
                    "node": node,
                    "score": round(score, 4),
                    "dimensions": dims,
                }
                # ★阶段三子任务3.0：知识时效性派生标记（仅开关开启时计算，不入库、不降权到0）
                if self._knowledge_timeliness_enabled():
                    _r["is_potentially_stale"] = self._is_potentially_stale(node)
                results.append(_r)
        
        results.sort(key=lambda r: r["score"], reverse=True)
        
        self._total_resonated += len(results)

        # ★PHASE17-阶段二子任务4.2：三通道一致性置信度
        #   开关开启且有结果时，附加 confidence（float）+ confidence_detail（可解释分量）。
        #   低置信度 <0.6 只标记 needs_llm_review，不自动改行为（星轨确认的保守策略）。
        if results and self._get_semantic_cfg().get("enable_confidence_calibration", False):
            try:
                _top_score = results[0]["score"]
                _conf = self._calc_confidence(
                    query_pulse, candidate_nodes, _semantic_map, _rule_map, _top_score)
                for _r in results:
                    _r["confidence"] = _conf["confidence"]
                    _r["confidence_detail"] = _conf
            except Exception as _e:
                _logger.debug(f"[共振引擎] 置信度计算失败（不影响检索）: {_e}")

        return results[:top_k]
    def resonate_topk(self, query_pulse: dict[str, Any],
                       candidate_nodes: list[dict[str, Any]],
                       top_k: int = 20) -> list[dict[str, Any]]:
        if not candidate_nodes:
            return []

        # ★PHASE17-阶段二子任务3：极性判别拦截（在粗筛之前，避免浪费计算）
        if self._check_polarity_block(query_pulse):
            return []

        # ★P0-3修复：加锁获取频率索引快照进行粗筛
        _query_freq = query_pulse.get("memory_dim", {}).get("frequency_signature", 0.0)
        if _query_freq > 0 and len(candidate_nodes) > top_k * 2:
            _scored = []
            for _node in candidate_nodes:
                _node_freq = _node.get("frequency_signature", 0.0)
                if _node_freq > 0:
                    _freq_diff = abs(_query_freq - _node_freq)
                    _freq_sim = max(0.0, 1.0 - _freq_diff / max(_query_freq, _node_freq))
                    _scored.append((_node, _freq_sim))
            _scored.sort(key=lambda x: x[1], reverse=True)
            _candidates = [s[0] for s in _scored[:top_k * 2]]
        else:
            _candidates = candidate_nodes

        # ★PHASE17-1.3：语义粗排并入。
        #   原粗筛只看频率签名，会把「频率不相似但语义高度相关」的节点直接筛掉，
        #   导致语义通道在粗排阶段就失效。这里取并集：频率 Top-K ∪ 语义 Top-K×倍数。
        #   语义通道不可用时 _sem_map 为空 dict，下方逻辑整体跳过（零开销、零变化）。
        _sem_map = self._build_semantic_map(query_pulse, candidate_nodes)
        if _sem_map:
            try:
                _mult = int(self._get_semantic_cfg().get(
                    "semantic_candidate_multiplier", 2))
                _sem_top = sorted(_sem_map.items(), key=lambda x: -x[1])[
                    :max(top_k * max(_mult, 1), top_k)]
                _id2node = {n.get("node_id", ""): n for n in candidate_nodes}
                _have = {n.get("node_id", "") for n in _candidates}
                for nid, _sim in _sem_top:
                    if nid and nid not in _have and nid in _id2node:
                        _candidates.append(_id2node[nid])
                        _have.add(nid)
            except Exception as _e:
                _logger.debug(f"[共振引擎] 语义候选并入失败（不影响主流程）: {_e}")

        # ★PHASE17-阶段二子任务1：规则粗排并入（与语义通道对称）。
        #   规则命中的节点若被频率/语义粗筛漏掉，同样会失效；取并集并入候选集。
        #   规则通道不可用时 _rule_map 为空 dict，下方逻辑整体跳过（零开销、零变化）。
        _rule_map = self._build_rule_map(query_pulse, candidate_nodes)
        if _rule_map:
            try:
                _id2node = {n.get("node_id", ""): n for n in candidate_nodes}
                _have = {n.get("node_id", "") for n in _candidates}
                for nid in _rule_map:
                    if nid and nid not in _have and nid in _id2node:
                        _candidates.append(_id2node[nid])
                        _have.add(nid)
            except Exception as _e:
                _logger.debug(f"[共振引擎] 规则候选并入失败（不影响主流程）: {_e}")

        # ★A-7（2026-09-08）：关联图谱候选并入（灰度 ENABLE_KNOWLEDGE_GRAPH_CONSUMPTION）。
        #   肝已建立 624 条关联（因果/类比/层级/语义）但此前 0 消费——
        #   在候选并入层扩展 1~2 跳邻居（召回层消费，**不动五维权重**，宪法红线不触）。
        try:
            _assoc_added = self._expand_by_association(_candidates, top_k)
            if _assoc_added:
                _logger.info(
                    f"[共振引擎] 关联扩展: 本轮并入{_assoc_added}个关联邻居节点")
        except Exception as _e:
            _logger.debug(f"[共振引擎] 关联扩展失败（不影响主流程）: {_e}")

        return self.resonate(query_pulse, _candidates, top_k=top_k,
                             _semantic_map=_sem_map, _rule_map=_rule_map)

    def _expand_by_association(self, candidates: list[dict[str, Any]],
                               top_k: int) -> int:
        """★A-7：经肝的关联图谱扩展候选集（1~2跳邻居并入，去重封顶）。

        数据源：节点池横向联系索引（PulseNodePool.get_related_nodes，肝在压缩/融合时
        经 add_semantic_relation 写入）。返回本轮新并入的邻居数（供日志）。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, 'ENABLE_KNOWLEDGE_GRAPH_CONSUMPTION', False):
                return 0
        except Exception:
            return 0
        _pool = getattr(self, "node_pool", None)
        if _pool is None or not hasattr(_pool, "get_related_nodes"):
            return 0

        _have = {n.get("node_id", "") for n in candidates}
        _added = 0
        _max_added = 30          # 单轮扩展总量上限（防爆炸）
        _max_per_seed = 3        # 每个种子最多扩展邻居数
        _log_budget = 5          # INFO 日志条数预算（防刷屏）
        _second_hop_seeds: list[tuple[str, PulseNode]] = []

        _seeds = list(candidates[:top_k * 2])
        for _seed in _seeds:
            if _added >= _max_added:
                break
            _sid = _seed.get("node_id", "")
            if not _sid:
                continue
            try:
                _related = _pool.get_related_nodes(_sid, limit=_max_per_seed)
            except Exception:
                continue
            for _rn in (_related or []):
                if _added >= _max_added:
                    break
                _rid = getattr(_rn, "node_id", "")
                if not _rid or _rid in _have:
                    continue
                try:
                    candidates.append(_rn.to_dict())
                    _have.add(_rid)
                    _added += 1
                    if _log_budget > 0:
                        _logger.info(
                            f"[共振引擎] 关联扩展: 节点{_sid[:18]}→关联节点{_rid[:18]}"
                            f"（{(_rn.space_path or '/')[:24]}）")
                        _log_budget -= 1
                    if len(_second_hop_seeds) < 5:
                        _second_hop_seeds.append((_rid, _rn))
                except Exception:
                    continue

        # 第二跳（浅层扩展：仅从第一跳前5个邻居再扩一层，仍受总量上限约束）
        for _rid, _rn in _second_hop_seeds:
            if _added >= _max_added:
                break
            try:
                _hop2 = _pool.get_related_nodes(_rid, limit=2)
            except Exception:
                continue
            for _n2 in (_hop2 or []):
                if _added >= _max_added:
                    break
                _nid2 = getattr(_n2, "node_id", "")
                if not _nid2 or _nid2 in _have:
                    continue
                try:
                    candidates.append(_n2.to_dict())
                    _have.add(_nid2)
                    _added += 1
                except Exception:
                    continue
        return _added
    # ========== 五维得分计算 ==========
    
    def _calculate_dimensions(self, query: dict[str, Any], 
                               node: dict[str, Any],
                               semantic_map: dict[str, float] | None = None,
                               rule_map: dict[str, float] | None = None
                               ) -> dict[str, float]:
        """
        计算单个节点的五维共振得分。
        
        Args:
            query: 查询脉冲
            node: 候选知识节点
            semantic_map: ★PHASE17-1.3 {node_id: 向量相似度}，仅作用于记忆维内部
            rule_map: ★PHASE17-阶段二子任务1 {node_id: 规则得分}，仅作用于记忆维内部
            
        Returns:
            五维得分字典 {"memory": 0.8, "space": 0.6, ...}
        """
        dims = {
            "memory": self._calc_memory_dim(query, node, semantic_map, rule_map),
            "space":  self._calc_space_dim(query, node),
            "logic":  self._calc_logic_dim(query, node),
            "time":   self._calc_time_dim(query, node),
            "state":  self._calc_state_dim(query, node),
        }
        return dims
    
    # ---- 记忆维（40%） ----
    def _calc_keyword_score(self, query: dict, node: dict) -> float:
        """纯关键词通道得分（频率签名 + 赫布权重 + 激活次数）。

        ★从 _calc_memory_dim 抽出（阶段二子任务4.2 置信度计算复用），
        与 Cython 版 calc_memory_dim_cy 输出完全一致。
        """
        query_freq = query.get("memory_dim", {}).get("frequency_signature", 0.0)
        node_freq = node.get("frequency_signature", 0.0)
        hebbian = node.get("hebbian_weight", 0.0)
        activation_count = node.get("activation_count", 0)

        if self._resonance_cy_available:
            return self._resonance_cy.calc_memory_dim_cy(
                query_freq, node_freq, hebbian, activation_count
            )

        # Python 原生兜底（与 Cython 版输出完全一致）
        _score = 0.0
        if query_freq > 0 and node_freq > 0:
            freq_diff = abs(query_freq - node_freq)
            freq_sim = max(0.0, 1.0 - freq_diff / 50.0)
            _score += freq_sim * 0.5
        _score += min(hebbian, 1.0) * 0.3
        if activation_count > 0:
            act_score = min(1.0, math.log10(activation_count + 1) / 5.0)
            _score += act_score * 0.2
        return min(_score, 1.0)

    def _calc_memory_dim(self, query: dict, node: dict,
                         semantic_map: dict[str, float] | None = None,
                         rule_map: dict[str, float] | None = None) -> float:
        """记忆维得分。

        ★PHASE17-1.3（星轨 Q3 决策：记忆维**内部分配**，五维权重不动）：

            memory_score = α'·keyword_score + β'·vector_sim      (α'+β'=1.0)
            最终总分仍为 0.40 × memory_score + 0.30·space + ... （规则1.3 未触）

        ★PHASE17-阶段二子任务1（星轨任务书：规则通道 γ' 接入）：

            memory_score = α'·keyword + β'·vector + γ'·rule     (α'+β'+γ'=1.0)
            规则通道关闭 / 该节点无规则得分时，退化为 α'+β' 双通道（行为不变）。

        keyword_score = 原有的 频率签名 + 赫布权重 + 激活次数（本函数上半段算出的 _score）
        vector_sim    = 语义内核给出的向量相似度（由 semantic_map 传入）
        rule_score    = 规则引擎给出的规则得分（由 rule_map 传入）

        ★零变化保证（满足任一即原样返回 _score）：
            开关 enable_semantic_kernel=False / 未注入 provider / 该节点无向量
        """
        # ★v23.0优化：优先使用Cython加速（纯数值部分）→ keyword_score
        _score = self._calc_keyword_score(query, node)
        query_freq = query.get("memory_dim", {}).get("frequency_signature", 0.0)
        node_freq = node.get("frequency_signature", 0.0)

        # ★v23.0保留：频率相位锁定记录（不依赖Cython）
        if query_freq > 0 and node_freq > 0:
            freq_diff = abs(query_freq - node_freq)
            freq_sim = max(0.0, 1.0 - freq_diff / 50.0)
            if self.phase_lock and freq_sim > 0.9:
                node_id = node.get("node_id", "")
                if node_id:
                    self.phase_lock.unlock("recent_query")
                    self.phase_lock.try_lock(
                        source_id="recent_query",
                        source_freq=query_freq,
                        source_phase=0.0,
                        target_id=node_id,
                        target_freq=node_freq,
                        target_phase=0.0,
                    )

        # ===== ★PHASE17-1.3 / 阶段二子任务1：记忆维内部分配 =====
        return self._fuse_memory_channels(_score, node, semantic_map, rule_map)

    def _fuse_memory_channels(self, keyword_score: float, node: dict,
                              semantic_map: dict[str, float] | None,
                              rule_map: dict[str, float] | None = None) -> float:
        """把向量通道 + 规则通道融合进记忆维（记忆维内部多通道加权）。

        ★为何不改权重：五维权重 0.40/0.30/0.15/0.10/0.05 是宪法规则1.3，
        修改属修宪。α'/β'/γ' 作用在**记忆维内部**（关键词 vs 向量 vs 规则通道），
        总权重仍是 0.40，不触宪法。

        二通道模式（enable_rule_channel=False，阶段一行为，逐字节等价）：
            memory = α'·keyword + β'·vector          (α'=0.43, β'=0.57)

        三通道模式（enable_rule_channel=True，阶段二子任务1）：
            memory = α'·keyword + β'·vector + γ'·rule (α'+β'+γ'=1.0)
            任一通道缺席（未编码/无规则得分）时，仅对在场通道的权重归一化到 1.0，
            即「无规则 → 退化为 α'+β'」——与星轨任务书一致，不影响检索。
        """
        cfg = self._get_semantic_cfg()
        if not cfg.get("enable_semantic_kernel", False):
            return keyword_score

        node_id = str(node.get("node_id", "") or "")
        vec_sim = semantic_map.get(node_id) if semantic_map else None

        rule_enabled = bool(cfg.get("enable_rule_channel", False))
        rule_sim = None
        if rule_enabled and rule_map:
            rule_sim = rule_map.get(node_id)
            # ★阶段二子任务4.3：查询级规则得分（PolarityGuard 作 rule_provider 时）。
            #   哨兵键 GLOBAL_RULE_KEY 表示规则得分是查询级、作用于所有候选节点，
            #   节点未显式出现在 rule_map 中也应用该得分。
            if rule_sim is None:
                try:
                    from nucleus.reasoning.PolarityGuard import GLOBAL_RULE_KEY
                    rule_sim = rule_map.get(GLOBAL_RULE_KEY)
                except Exception as e:
                    silent_exc(e, where="nucleus.synapsys.ResonanceEngine::_fuse_memory_channels L858")

        # ---- 规则通道关闭：走阶段一双通道路径（与改造前逐字节等价） ----
        if not rule_enabled:
            if self._vector_provider is None or not semantic_map:
                return keyword_score
            if vec_sim is not None:
                a = float(cfg.get("memory_keyword_ratio", 0.43))
                b = float(cfg.get("memory_vector_ratio", 0.57))
                _sum = a + b
                if _sum <= 0:
                    return keyword_score
                a, b = a / _sum, b / _sum          # 防御：强制 α'+β'=1.0
                return min(1.0, a * keyword_score + b * max(0.0, min(1.0, vec_sim)))

            # 该节点尚未编码（无向量）
            if str(cfg.get("vector_missing_policy", "keyword")) == "zero":
                a = float(cfg.get("memory_keyword_ratio", 0.43))
                return min(1.0, max(0.0, a) * keyword_score)
            return keyword_score   # 默认：回落关键词，不惩罚未编码节点

        # ---- 规则通道开启：三通道融合（在场通道权重归一化到 1.0） ----
        _w = cfg.get("rule_channel_weights") or {}
        wk = float(_w.get("keyword", 0.30))
        wv = float(_w.get("vector", 0.30))
        wr = float(_w.get("rule", 0.40))

        channels: list[tuple[float, float]] = [(keyword_score, wk)]  # 关键词通道恒在场
        if vec_sim is not None:
            channels.append((max(0.0, min(1.0, vec_sim)), wv))
        if rule_sim is not None:
            channels.append((max(0.0, min(1.0, rule_sim)), wr))

        _tot = sum(_wgt for _, _wgt in channels)
        if _tot <= 0:
            return keyword_score
        _fused = sum(_wgt * _s for _s, _wgt in channels) / _tot
        return min(1.0, _fused)

    def _calc_confidence(self, query: dict, candidate_nodes: list[dict],
                         semantic_map: dict[str, float] | None,
                         rule_map: dict[str, float] | None,
                         top_score: float) -> dict[str, Any]:
        """三通道一致性置信度（阶段二子任务4.2）。

        置信度 = 0.5×三通道一致性 + 0.3×最高分绝对值 + 0.2×历史正确推理相似度

        三通道一致性（Jaccard 口径，星轨 2026-09-07 确认）：
          在场通道的 top-1 节点，取最大同簇数 / 在场通道数：
            三通道全一致 = 1.0，两通道一致 = 0.67，各指各的 = 0.33
            规则通道无规则 → 退化两通道（一致=1.0，分歧=0.5）
            单通道 → 中性 0.5（无法判定一致性）

        历史正确推理相似度：初期占位 0.5（config 可配），后续接推理经验库。
        """
        cfg = self._get_semantic_cfg()

        # ---- 1. 收集各在场通道的 top-1 节点 ----
        channel_top1: list[str] = []

        # 关键词通道（恒在场）
        _kw_best_nid = ""
        _kw_best = -1.0
        for _n in candidate_nodes:
            _s = self._calc_keyword_score(query, _n)
            if _s > _kw_best:
                _kw_best = _s
                _kw_best_nid = str(_n.get("node_id", "") or "")
        if _kw_best_nid:
            channel_top1.append(_kw_best_nid)

        # 语义通道（semantic_map 非空时在场）
        if semantic_map:
            _v_best_nid = ""
            _v_best = -1.0
            for _n in candidate_nodes:
                _nid = str(_n.get("node_id", "") or "")
                _v = semantic_map.get(_nid)
                if _v is not None and _v > _v_best:
                    _v_best = _v
                    _v_best_nid = _nid
            if _v_best_nid:
                channel_top1.append(_v_best_nid)

        # 规则通道（rule_map 非空时在场）
        if rule_map:
            _r_best_nid = ""
            _r_best = -1.0
            for _n in candidate_nodes:
                _nid = str(_n.get("node_id", "") or "")
                _v = rule_map.get(_nid)
                if _v is not None and _v > _r_best:
                    _r_best = _v
                    _r_best_nid = _nid
            if _r_best_nid:
                channel_top1.append(_r_best_nid)

        # ---- 2. 一致性（最大同簇数 / 在场通道数）----
        _nch = len(channel_top1)
        if _nch <= 1:
            _consistency = 0.5    # 单通道无法判定，中性
        else:
            _freq: dict[str, int] = {}
            for _nid in channel_top1:
                _freq[_nid] = _freq.get(_nid, 0) + 1
            _consistency = max(_freq.values()) / _nch

        # ---- 3. 历史正确推理相似度 ----
        # ★第九批 B-3（P1-5 接线）：原来是写死的占位值 0.5，等于这一路权重（0.2）
        #   永远是常数，校准形同虚设。现在改为查推理经验库（开启开关时）；
        #   查不到/异常时回退占位值并记 DEBUG，绝不因经验库不可用而让置信度失真。
        _history_sim = float(cfg.get("confidence_history_sim_placeholder", 0.5))
        try:
            import config as _conf_mod
            if getattr(_conf_mod, "ENABLE_CONFIDENCE_EVIDENCE", False):
                _q_text = ""
                if isinstance(query, dict):
                    _q_text = str(query.get("text") or query.get("question")
                                  or query.get("query") or "")
                else:
                    _q_text = str(query or "")
                if _q_text:
                    from nucleus.reasoning.SelfCalibrator import get_evidence_calibrator
                    _history_sim = get_evidence_calibrator().history_similarity(_q_text)
        except Exception as e:
            _logger.debug(f"异常已忽略（保持占位值）: {type(e).__name__}: {e}")  # 不影响主链路

        # ---- 4. 置信度加权 ----
        _w_cons = float(cfg.get("confidence_consistency_weight", 0.5))
        _w_top = float(cfg.get("confidence_top_score_weight", 0.3))
        _w_hist = float(cfg.get("confidence_history_sim_weight", 0.2))
        _top = max(0.0, min(1.0, float(top_score)))
        _conf = _w_cons * _consistency + _w_top * _top + _w_hist * _history_sim
        _conf = max(0.0, min(1.0, _conf))

        # ---- 5. 分级 ----
        _hi = float(cfg.get("confidence_high_threshold", 0.8))
        _lo = float(cfg.get("confidence_low_threshold", 0.6))
        if _conf >= _hi:
            _level = "high"
        elif _conf >= _lo:
            _level = "medium"
        else:
            _level = "low"

        # ★第95批 T-95d：补「置信度守卫」本地推理埋点（此前全库**零调用点**
        #   ⇒ llm_dependency 的「置信度守卫」恒为 0）。语义 = 三通道一致性
        #   置信度判定为**低**（< ``confidence_low_threshold``），即本地检索
        #   结果不足以自持、已标记 ``needs_llm_review`` 的**守卫触发**事件。
        #   本函数在 L558 每查询**只调用一次** ⇒ 每查询至多计 1 次，不放大。
        #   异常只记 DEBUG，绝不影响检索（对齐第84批 M84-2 的埋点风格）。
        if _level == "low":
            try:
                from nucleus.LLMDependencyMetrics import (
                    KIND_GUARD as _m95_kind_guard,
                    record_local_inference as _m95_rec_local,
                )
                _m95_rec_local(_m95_kind_guard)
            except Exception as _e95g:
                _logger.debug(
                    "[共振引擎] 置信度守卫本地推理计数失败（已忽略）: %s: %s",
                    type(_e95g).__name__, _e95g)

        return {
            "confidence": round(_conf, 4),
            "level": _level,
            "consistency": round(_consistency, 4),
            "top_score": round(_top, 4),
            "history_sim": round(_history_sim, 4),
            "channels": _nch,
            "needs_llm_review": _conf < _lo,   # 低置信度标记（只标记，不自动改行为）
        }

    # ---- 空间维（30%） ----
    # ★PHASE17-1.3：路径信息缺失时的**中性分**。
    #   原实现返回常量 0.1 —— 属**退化**：所有无路径节点拿同一个分数，
    #   既不携带信息，又系统性压低其总分（0.30×0.1=0.03）。
    #   "中性" 意味着「无法判断，不引入偏向」，取五维区间的中点 0.5。
    #   ★影响提示：无路径节点总分 +0.12（0.30×0.4），
    #     但因为所有无路径节点是**同量平移**，它们之间的相对排序不变，
    #     只改变「无路径 vs 有路径」的相对位置 —— 由 1.7 黄金评测集复核。
    SPACE_NEUTRAL_SCORE = 0.5

    @staticmethod
    def _normalize_path(path: str) -> str:
        """路径归一化：去掉首尾/重复的斜杠。空串与 "/" 都归一为空串（=无有效路径）。"""
        if not path:
            return ""
        parts = [p for p in str(path).replace("\\", "/").split("/") if p]
        return "/".join(parts)

    def _calc_space_dim(self, query: dict, node: dict) -> float:
        """
        空间维得分：知识树路径匹配度。

        路径前缀匹配越长，共振越强。

        ★PHASE17-1.3 修复（星轨 1.3 决策）：
            1. path 归一化 —— "a/b/"、"a//b"、"/a/b" 视为同一路径（原实现会误判）
            2. path="/" 视为「无有效路径」（原实现 strip("/") 后得到 [""]，
               会拿空串去比对节点路径首段，产生 0 分假信号）
            3. 无路径返回中性分 0.5，而非常量 0.1
        """
        query_path = self._normalize_path(
            (query.get("space_dim") or {}).get("path", ""))
        node_path = self._normalize_path(node.get("space_path", ""))

        if not query_path or not node_path:
            return self.SPACE_NEUTRAL_SCORE   # 无路径信息 → 中性分，不偏不倚

        # 前缀匹配深度（归一化后各段均非空，不会出现空串误匹配）
        query_parts = query_path.split("/")
        node_parts = node_path.split("/")

        match_depth = 0
        for qp, np_ in zip(query_parts, node_parts):
            if qp == np_:
                match_depth += 1
            else:
                break

        # 匹配深度 / 查询路径深度
        if len(query_parts) > 0:
            return min(1.0, match_depth / len(query_parts))

        return self.SPACE_NEUTRAL_SCORE
    
    # ---- 逻辑维（15%） ----
    def _calc_logic_dim(self, query: dict, node: dict) -> float:
        """
        逻辑维得分：意图匹配 + 事件类型匹配。
        """
        score = 0.0
        
        # 事件类型匹配
        query_event = query.get("event_type", "")
        node_trigger = node.get("trigger_reason", "")
        node_source = node.get("source_organ", "")
        
        if query_event and (query_event in node_trigger or query_event in node_source):
            score += 0.6
        
        # 关键词匹配（载荷中的关键词与节点关键词重叠）
        query_keywords = set(query.get("payload", {}).get("keywords", []))
        node_keywords = set(node.get("keywords", []))
        
        if query_keywords and node_keywords:
            overlap = len(query_keywords & node_keywords)
            if overlap > 0:
                keyword_score = min(1.0, overlap / len(query_keywords))
                score += keyword_score * 0.4
        
        return min(score, 1.0)
    
    # ---- 时间维（10%） ----
    def _calc_time_dim(self, query: dict, node: dict) -> float:
        """时间维得分（权重 0.10，宪法规则1.3 永久固定，本方法绝不修改）。

        ★阶段三子任务3.0/3.1：受 ENABLE_KNOWLEDGE_TIMELINESS 灰度开关保护。
            - 关闭：仅激活新鲜度（与原实现逐字节一致，含 Cython 加速），零回退。
            - 开启：0.5*激活新鲜度 + 0.5*来源时效性（time 维内部融合，权重/peak门不动）。
        融合逻辑统一在 Cython 版 calc_time_dim_cy 内完成（Python 兜底层镜像），
        本方法只做"开关路由 + 委派"，避免 Python/Cython 双重融合。
        """
        _enabled = self._knowledge_timeliness_enabled()
        return self._calc_time_dim_base(node, enable_timeliness=_enabled)

    # ---- 阶段三子任务3.0：时间维辅助方法（知识时效性） ----
    def _calc_time_dim_base(self, node: dict, enable_timeliness: bool = False) -> float:
        """时间维核心计算（含 Cython 加速）。

        enable_timeliness=False：仅激活新鲜度（与原实现逐字节一致），零回退。
        enable_timeliness=True：0.5*激活新鲜度 + 0.5*来源时效（与 Cython 版融合逻辑一致）。
        """
        now = time.time()
        last_activated = node.get("last_activated", 0)

        if last_activated <= 0:
            return 0.1

        elapsed = now - last_activated

        # 计算来源年龄（秒），用于时效性融合
        _source_age = 0.0
        if enable_timeliness:
            # ★任务C-4：优先使用网页发布时间 source_timestamp（网页自带），
            #   其次 source_time（RSS published_at 等结构化来源时间），
            #   都没有才回退入库时间——旧行为零变化。
            _src = node.get("source_timestamp", None)
            if not _src or _src <= 0:
                _src = node.get("source_time", None)
            if not _src or _src <= 0:
                _src = node.get("acquired_time", node.get("created_at", 0))
            if _src and _src > 0:
                _source_age = now - _src

        # ★v23.0优化：优先使用Cython加速（融合逻辑在 Cython 内完成）
        if self._resonance_cy_available:
            return self._resonance_cy.calc_time_dim_cy(elapsed, _source_age, enable_timeliness)

        # Python原生兜底（与 Cython 版一致）
        if elapsed < 3600:
            _act = 1.0
        elif elapsed < 86400:
            _act = max(0.3, 1.0 - (elapsed - 3600) / 82800 * 0.7)
        elif elapsed < 604800:
            _act = max(0.1, 0.3 - (elapsed - 86400) / 518400 * 0.2)
        else:
            _act = 0.05

        if not enable_timeliness:
            return _act

        # 融合：0.5*激活 + 0.5*来源时效（与 Cython 版一致）
        _src = self._source_timeliness_seconds(_source_age)
        return min(1.0, 0.5 * _act + 0.5 * _src)

    def _source_timeliness(self, node: dict) -> float:
        """知识来源时效性（分段阈值）。来源越新分越高，下限 0.20（旧知识低但不丢弃）。"""
        _src = node.get("source_time", None)
        if not _src or _src <= 0:
            _src = node.get("acquired_time", node.get("created_at", 0))
        if not _src or _src <= 0:
            return 0.5  # 无来源时间信息，给中性分（不偏向新也不偏向旧）
        return self._source_timeliness_seconds(time.time() - _src)

    def _source_timeliness_seconds(self, source_age_seconds: float) -> float:
        """来源时效性（按秒计的分段阈值），供 Python 兜底层与 Cython 版保持数值一致。"""
        if source_age_seconds < 604800:        # <7天
            return 1.0
        elif source_age_seconds < 2592000:     # <30天
            return 0.85
        elif source_age_seconds < 7776000:     # <90天
            return 0.65
        elif source_age_seconds < 31536000:    # <365天
            return 0.40
        else:                                   # >=365天
            return 0.20

    def _knowledge_timeliness_enabled(self) -> bool:
        """读取顶层灰度开关 ENABLE_KNOWLEDGE_TIMELINESS（延迟导入，避免模块级硬依赖）。"""
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_KNOWLEDGE_TIMELINESS", False))
        except Exception as e:
            silent_exc(e, where="nucleus.synapsys.ResonanceEngine::_knowledge_timeliness_enabled L1203")
            return False

    def _is_potentially_stale(self, node: dict) -> bool:
        """派生标记：入库时间超过 STALE_THRESHOLD_DAYS 视为可能过时。不入库、不降权到 0。"""
        try:
            import config as _cfg
            _thr = float(getattr(_cfg, "STALE_THRESHOLD_DAYS", 365))
        except Exception:
            _thr = 365.0
        _acq = node.get("acquired_time", node.get("created_at", 0))
        if not _acq or _acq <= 0:
            return False
        return (time.time() - _acq) / 86400.0 > _thr
    
    # ---- 状态维（5%） ----
    def _calc_state_dim(self, query: dict, node: dict) -> float:
        importance = node.get("importance", "C")
        
        # ★v23.0优化：优先使用Cython加速
        if self._resonance_cy_available:
            # 将importance字符串映射为int编码
            if importance == "S":
                _code = 3
            elif importance == "A":
                _code = 2
            elif importance == "B":
                _code = 1
            else:
                _code = 0
            return self._resonance_cy.calc_state_dim_cy(_code)
        
        # Python原生兜底
        importance_scores = {
            "S": 1.0,
            "A": 0.8,
            "B": 0.5,
            "C": 0.2,
        }
        return importance_scores.get(importance, 0.2)
    
    # ========== 升级：替代 InfoField 的简单匹配 ==========
    
    def match_condition_advanced(self, pulse: dict[str, Any], 
                                  condition: dict[str, Any]) -> bool:
        """
        升级版条件匹配（替代 InfoField._match_condition 的简单事件匹配）。
        
        在基础事件类型匹配之上，增加五维共振阈值判断。
        当 ResonanceEngine 注入 InfoField 后，InfoField 可调用此方法升级匹配逻辑。
        """
        # 基础事件类型匹配
        event_type = pulse.get("event_type", "")
        event_types = condition.get("event_types", [])
        
        if event_types:
            matched = False
            for pattern in event_types:
                if self._simple_match(event_type, pattern):
                    matched = True
                    break
            if not matched:
                return False
        
        # 优先级阈值
        if pulse.get("priority", 5) < condition.get("min_priority", 0):  # noqa: SIM103
            return False
        
        return True
    
    @staticmethod
    def _simple_match(event_type: str, pattern: str) -> bool:
        """简单通配符匹配"""
        if pattern == "*" or pattern == event_type:
            return True
        pattern_parts = pattern.split(".")
        event_parts = event_type.split(".")
        if len(pattern_parts) != len(event_parts):
            return False
        return all(pp == "*" or pp == ep for pp, ep in zip(pattern_parts, event_parts))

    # ========== 未来演化预留（v10.0 振荡场） ==========
    
    def set_phase_lock(self, phase_lock):
        """
        【预留 v10.0】注入频率-相位锁定机制。
        
        v10.0 中，当查询频率与节点频率锁定时，记忆维得分乘以增强因子。
        频率-相位锁定是振荡场自组织协同的核心机制。
        """
        self.phase_lock = phase_lock
    
    def set_oscillon_enhancement(self, factor: float):
        """
        【预留 v10.0】设置振荡场增强因子。
        
        振荡场强度越高，记忆维共振越强。这模拟了大脑在注意力集中时
        （场强度高），记忆检索更精准的现象。
        
        Args:
            factor: 增强因子 1.0-2.0（1.0=无增强，2.0=最强增强）
        """
        self._oscillon_enhancement = max(1.0, min(2.0, factor))  
    # ========== 统计信息 ==========
    
    def get_freq_index_keys(self) -> list:
        """公开只读访问频率索引键（规则14）"""
        return list(self._freq_index.keys())

    def get_space_index_keys(self) -> list:
        """公开只读访问空间索引键（规则14）"""
        return list(self._space_index.keys())

    def get_stats(self) -> dict[str, Any]:
        """获取共振引擎统计"""
        # ★v23.0新增：相位锁定统计
        _phase_lock_stats = None
        if self.phase_lock:
            _phase_lock_stats = self.phase_lock.get_stats()
        
        return {
            "total_queries": self._total_queries,
            "total_resonated": self._total_resonated,
            "freq_index_size": len(self._freq_index),
            "space_index_size": len(self._space_index),
            "weights": self.WEIGHTS.copy(),
            "has_node_pool": self.node_pool is not None,
            "phase_lock_stats": _phase_lock_stats,  # ★v23.0新增
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== ResonanceEngine 自测 ===")
    
    engine = ResonanceEngine()
    
    # 1. 权重永久固定验证
    print(f"✅ 五维权重: {engine.WEIGHTS}")
    assert sum(engine.WEIGHTS.values()) == 1.0, "权重总和应为1.0"
    print(f"✅ 权重总和: {sum(engine.WEIGHTS.values())}")
    
    # 2. 构建测试节点
    nodes = [
        {
            "node_id": "node_1",
            "value": "脉冲场架构是v8.0的核心设计",
            "keywords": ["脉冲场", "架构", "v8.0"],
            "frequency_signature": 42.5,
            "space_path": "/技术/架构/脉冲场",
            "importance": "S",
            "activation_count": 50,
            "hebbian_weight": 0.9,
            "last_activated": time.time() - 600,  # 10分钟前
            "source_organ": "胃",
            "trigger_reason": "digest.knowledge",
        },
        {
            "node_id": "node_2",
            "value": "Python是一门编程语言",
            "keywords": ["Python", "编程", "语言"],
            "frequency_signature": 15.2,
            "space_path": "/知识/编程/Python",
            "importance": "B",
            "activation_count": 5,
            "hebbian_weight": 0.2,
            "last_activated": time.time() - 86400,  # 1天前
            "source_organ": "双腿",
            "trigger_reason": "scrape.web",
        },
        {
            "node_id": "node_3",
            "value": "小林是曈曈的父亲",
            "keywords": ["小林", "曈曈", "父亲", "新人类"],
            "frequency_signature": 40.0,
            "space_path": "/身份/家庭/小林",
            "importance": "S",
            "activation_count": 100,
            "hebbian_weight": 1.0,
            "last_activated": time.time() - 60,  # 1分钟前
            "source_organ": "内在世界",
            "trigger_reason": "inference.identity",
        },
    ]
    
    # 3. 索引节点
    for node in nodes:
        engine.index_node(node)
    print(f"✅ 频率索引大小: {len(engine._freq_index)}")
    print(f"✅ 空间索引大小: {len(engine._space_index)}")
    
    # 4. 五维共振检索
    query = {
        "event_type": "chat.question",
        "payload": {"keywords": ["脉冲场", "架构"]},
        "memory_dim": {"frequency_signature": 42.0},
        "space_dim": {"path": "/技术/架构/脉冲场"},
    }
    
    results = engine.resonate(query, nodes, top_k=5)
    print(f"✅ 共振结果数: {len(results)}")
    for i, r in enumerate(results):
        print(f"   #{i+1}: {r['node']['value'][:40]}... (score={r['score']})")
    
    # 5. 验证 S 级节点提权
    # node_3（身份节点）即使 space 不匹配，记忆维 + 状态维也应得分
    query2 = {
        "event_type": "chat.question",
        "payload": {"keywords": ["小林"]},
        "memory_dim": {"frequency_signature": 40.0},
        "space_dim": {"path": "/身份"},
    }
    results2 = engine.resonate(query2, nodes, top_k=3)
    print("✅ 身份查询共振结果:")
    for i, r in enumerate(results2):
        print(f"   #{i+1}: {r['node']['value'][:40]}... (score={r['score']})")
    
    # 6. 移除节点
    engine.remove_node("node_2", freq=15.2, space_path="/知识/编程/Python")
    print(f"✅ 移除后频率索引: {len(engine._freq_index)}")
    
    # 7. 统计
    stats = engine.get_stats()
    print(f"✅ 统计: 查询{stats['total_queries']}次 共振{stats['total_resonated']}个节点")
    
    print("=== 自测全部通过 ===")