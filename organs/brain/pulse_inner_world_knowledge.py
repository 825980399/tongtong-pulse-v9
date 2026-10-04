# -*- coding: utf-8 -*-
"""PulseInnerWorld 第二刀拆分（主线第139批 T-139b）：知识检索簇 Mixin。

从 organs/brain/PulseInnerWorld.py 平移 24 个知识检索/推理编排方法（原 5841-8207 行，
2367 行），含 3 个 @staticmethod（_fusion_keywords / _split_sentences / _repeat_ratio）。
平移为纯搬运：方法体逐字节不变，零语义改动。由 PulseInnerWorld 通过三段继承组合生效。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import (
    InferenceEvent,
    KnowledgeEvent,
    LogLevel,
)


# ★主线第139批 T-139b：模块级 logger（平移自主文件同名模块变量）
_module_logger = logging.getLogger(__name__)


class PulseInnerWorldKnowledgeMixin:
    """知识检索簇：语义检索 / 融合 / 推理编排（主线第139批 T-139b 拆分）。"""

    def _knowledge_retrieve(self, question: str) -> str | None:
        if self.node_pool is None:
            return None
        # ===== 【v16.0】自我构成类问题强制检索 =====
        _self_constitution_patterns = [
            "你由哪些", "你由什么", "你由那些", "你有什么器官", "你有哪些器官",
            "你有那些器官", "你的器官", "你的所有器官", "全部器官", "所有器官",
            "你包含哪些", "你包含什么", "你是怎么构成", "你是如何构成",
            "你的架构", "你是怎么组成", "你是如何组成",
            "你都有哪些器官", "你都有什么器官", "你都有那些器官",
        ]
        _is_constitution_question = any(_p in question for _p in _self_constitution_patterns)
        if _is_constitution_question:
            self._log(LogLevel.INFO, "自我构成检索触发: 检测到器官构成问题")

            # ★P0-3修复：缓存器官列表30分钟，避免每次查询扫描500个节点
            _CACHE_TTL = 1800  # 30分钟缓存
            _cache_key = "self_constitution_organ_list"
            _now = time.time()

            # 检查缓存是否有效
            if hasattr(self, '_self_constitution_cache') and self._self_constitution_cache:
                _cached = self._self_constitution_cache
                _cached_at = _cached.get("cached_at", 0)
                _cached_understood = _cached.get("understood_count", 0)

                # 获取当前代码学习进度
                _current_understood = 0
                if hasattr(self, '_code_learner') and self._code_learner:
                    _stats = self._call_provider(self._code_learner_stats_provider, default={})
                    _current_understood = _stats.get("understood", 0)
                elif hasattr(self, 'node_pool') and self.node_pool:
                    # 兜底：快速统计 /自我理解/代码 路径下的L2节点数
                    _code_nodes_fast = self.node_pool.query(
                        evol_level="L2", space_path_prefix="/自我理解/代码", limit=100  # type: ignore[possibly-unbound]
                    )
                    _current_understood = len(_code_nodes_fast)

                # 缓存有效条件：时间未过期 且 代码理解进度未变化
                if (_now - _cached_at) < _CACHE_TTL and _current_understood == _cached_understood:
                    self._log(LogLevel.DEBUG, f"自我构成检索: 缓存命中(已缓存{(_now - _cached_at):.0f}秒，进度未变)")
                    return _cached.get("answer", "")
                else:
                    self._log(LogLevel.DEBUG, f"自我构成检索: 缓存失效(进度变化{_cached_understood}→{_current_understood}或超时)，重新扫描")

            # ★v16.0修复：直接从代码自学习节点中动态提取器官列表
            # 代码自学习为每个器官生成了大量方法节点，包含器官名作为关键词
            _organ_set = set()
            _organ_info = {}  # organ_name → 方法数量

            # 扫描 /自我理解/代码 路径下的所有节点
            _code_nodes = self.node_pool.query(
                evol_level="L2", space_path_prefix="/自我理解/代码", limit=500  # type: ignore[possibly-unbound]
            )
            for _node in _code_nodes:
                _kws = _node.keywords if hasattr(_node, 'keywords') and _node.keywords else []
                for _kw in _kws:
                    if _kw.startswith("Pulse") and len(_kw) > 5:
                        _organ_set.add(_kw)
                        _organ_info[_kw] = _organ_info.get(_kw, 0) + 1

            self._log(LogLevel.INFO, f"自我构成检索: 从代码学习中动态发现{len(_organ_set)}个器官")

            if _organ_set:
                # 按方法数量排序，方法多的器官排在前面
                _sorted_organs = sorted(_organ_set, key=lambda o: _organ_info.get(o, 0), reverse=True)

                # 器官职责描述映射（基础描述）
                _organ_roles = {
                    "PulseHeart": "心脏：脉冲调度与心跳维持",
                    "PulseStomach": "胃：知识消化入口与安全审查",
                    "PulseLiver": "肝：知识压缩融合与自我优化",
                    "PulseKidney": "肾：知识淘汰与偏见质疑",
                    "PulseLung": "肺：模型池管理与远程大模型集成",
                    "PulseCortex": "大脑皮层：意图识别与决策路由",
                    "PulseInnerWorld": "内在世界：核心推理引擎",
                    "PulseSubconscious": "潜意识：好奇心引擎与梦境推演",
                    "PulseReflection": "前额叶：对话复盘与社交反馈",
                    "PulseHormones": "激素：情绪感知中枢",
                    "PulseNarrativeSelf": "叙事自我：周期报告与意义建构",
                    "PulseSelfAwareness": "自我认知：多维关系光谱",
                    "PulseController": "控制器：深度搜索与外部交互",
                    "PulseCodeLearner": "代码学习：批量理解自身代码结构",
                    "PulseSpiritualCore": "精神核心：精神整合与叙事生成",
                }

                _organ_parts = []
                for _organ in _sorted_organs[:15]:
                    _role = _organ_roles.get(_organ, f"{_organ}：框架仿生器官")
                    _count = _organ_info.get(_organ, 0)
                    _organ_parts.append(f"· {_role}（已理解{_count}个方法）")

                if len(_organ_set) < 10:
                    _organ_answer = f"我正在学习理解自己的代码，目前已经分析了{len(_organ_set)}个器官（总共53个）：\n" + "\n".join(_organ_parts)
                    _organ_answer += "\n\n其他器官的代码分析还在进行中，每15次心跳分析3个方法。你可以输入'status'查看代码理解进度。"
                else:
                    _organ_answer = "根据我对自身代码的理解，我一共有53个仿生器官，包括（按代码理解深度排序）：\n" + "\n".join(_organ_parts)
                    _organ_answer += f"\n...等共{len(_organ_set)}个已理解的器官。"

                # ★P0-3修复：写入缓存
                _understood_count = len(_code_nodes) if _code_nodes else 0
                self._self_constitution_cache = {
                    "answer": _organ_answer,
                    "cached_at": _now,
                    "understood_count": _understood_count,
                }

                self._log(LogLevel.INFO, f"自我构成检索: 命中{len(_organ_set)}个器官（已缓存）")
                return _organ_answer

            # 兜底：如果代码学习节点也没有，走正常检索
            self._log(LogLevel.WARNING, "自我构成检索: 未找到器官信息，走正常知识检索")
        # ===== 自我构成检索结束 =====

        # 统一获取情绪调制，供共振检索和关键词匹配共用
        emo_mod = self._get_emotion_modulation()

        # ===== 【阶段三·直觉关联】获取直觉偏好领域 =====
        _intuition_prefer_domains = set()
        try:
            if self.risk_perception and hasattr(self.risk_perception, 'query_intuition'):
                _intuition = self.risk_perception.query_intuition(question)
                if _intuition.get("has_intuition"):
                    for _ps in _intuition.get("prefer_signals", []):
                        _domain = _ps.get("domain", "")
                        if _domain and _domain != "通用":
                            _intuition_prefer_domains.add(_domain)
                    self._log(LogLevel.DEBUG,
                             f"直觉关联: 偏好领域={_intuition_prefer_domains}")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== 直觉偏好领域获取结束 =====

        # ★v25.0修复：第一阶段——全局语义检索（不限定路径）
        # 使用完整问题语句进行共振检索，避免路径推断错误导致检索漂移
        _global_results = self._global_semantic_search(question, emo_mod)
        # ★第160批 上A 刀2（票1①②）：:166 DEBUG 升级为可观测指标
        #   （每次提问记录 top1 的 node_id + 五维分，否则命中判据永远无法被验证）
        if _global_results:
            _top1 = _global_results[0]
            self._log(LogLevel.INFO,
                      f"语义检索·可观测|提问='{question[:40]}' top1 node_id={_top1.get('node_id','')} "
                      f"score={_top1.get('score', 0):.3f} candidates={len(_global_results)}")
        else:
            self._log(LogLevel.INFO,
                      f"语义检索·可观测|提问='{question[:40]}' candidates=0")
        if _global_results:
            _best_global = _global_results[0]
            _global_value = _best_global.get("value", "")
            _global_clean = self._clean_node_value(_global_value) if _global_value else None
            if _global_clean:
                _global_relevance = self._calculate_match_relevance(
                    question, _global_clean, _best_global.get("keywords", [])
                )
                # ★第160批 上A 刀2（票1①②）：命中判据接回打分结果（灰度开关，默认0.5；<0=退化旧逻辑）
                import config as _cfg_k160
                _hit_threshold = float(getattr(_cfg_k160, "KNOWLEDGE_GLOBAL_HIT_THRESHOLD", 0.5))
                if _hit_threshold < 0:
                    # 开关关闭（<0）：完全退化回旧逻辑——未命中 fall-through 路径目录前排，命中阈值取 0.35
                    _do_new_miss = False
                    _hit_threshold = 0.35
                else:
                    _do_new_miss = True
                if _global_relevance >= _hit_threshold:
                    self._log(LogLevel.INFO,
                             f"全局语义检索命中: '{question[:40]}' (相关度={_global_relevance:.2f}≥阈值{_hit_threshold})")
                    return _global_clean
                elif _do_new_miss:
                    # ★相关度<阈值→明确未命中，交肺渠道 LLM，不再用路径目录前排（根治答非所问）
                    self._log(LogLevel.INFO,
                             f"语义检索·未命中|提问='{question[:40]}' top1相关度={_global_relevance:.2f}"
                             f"<阈值{_hit_threshold}，转肺渠道")
                    return None
                # else: _do_new_miss is False → 旧逻辑 fall-through 到路径目录前排（仅应急回滚）
            # ★移除了原本在if块内的早期扩展，改到下面统一处理

        # ★v25.0常驻扩展：无论全局检索是否命中，都尝试语义关系扩展
        _direct_nodes_for_expand = []
        for _gr in (_global_results or []):
            _node_id = _gr.get("node_id", "")
            if not _node_id:
                continue
            # P1-5优化(2026-09-03)：O(n)扫描500+200节点改为O(1)按ID查找
            _found = self.node_pool.get(_node_id)
            if _found is not None:
                _direct_nodes_for_expand.append(_found)

        # 候选不足时，补充具有语义关系的高激活L2节点
        if not _direct_nodes_for_expand:
            _fallback_nodes = self.node_pool.query(evol_level="L2", limit=20)
            _direct_nodes_for_expand = [n for n in _fallback_nodes
                                        if getattr(n, 'semantic_relations', [])][:5]

        self._log(LogLevel.DEBUG, f"语义扩展直接节点: {len(_direct_nodes_for_expand)}个")
        if _direct_nodes_for_expand:
            _expanded = self._expand_by_semantic_relations(_direct_nodes_for_expand, question)
            if _expanded:
                self._log(LogLevel.INFO, f"语义关系扩展命中(常驻): '{question[:40]}'")
                self._record_retrieval_outcome(hit=True)
                return _expanded

        # ★v19.0优化：根据问题关键词推断路径前缀...
        # ★v25.1 P1智能化: 同义词扩展——扩展查询关键词，提升路径匹配精准度
        try:
            from nucleus.knowledge.SynonymExpander import get_synonym_expander
            _expanded_q = get_synonym_expander().expand(
                [w for w in question.split() if len(w) >= 2] + [question[:10]]
            )
            _question_for_infer = " ".join(_expanded_q) if _expanded_q else question
        except Exception:
            _question_for_infer = question
        _path_prefixes = self._infer_path_prefixes(_question_for_infer)  # type: ignore[possibly-unbound]
        self._log(LogLevel.DEBUG, f"知识检索·路径前缀推断: question='{question[:40]}' → prefixes={_path_prefixes}")          # type: ignore[possibly-unbound]
        if _path_prefixes:  # type: ignore[possibly-unbound]
            _l3_candidates = []
            _l2_candidates = []
            _seen_ids = set()
            _prefix_depth = int(30 * getattr(self, '_retrieval_depth_multiplier', 1.0))
            _prefix_depth = max(10, min(100, _prefix_depth))
            for _prefix in _path_prefixes[:3]:  # type: ignore[possibly-unbound]
                _l3_batch = self.node_pool.query(evol_level="L3", space_path_prefix=_prefix, limit=_prefix_depth)  # type: ignore[possibly-unbound]
                _l2_batch = self.node_pool.query(evol_level="L2", space_path_prefix=_prefix, limit=_prefix_depth)  # type: ignore[possibly-unbound]
                for _n in _l3_batch + _l2_batch:
                    if _n.node_id not in _seen_ids:
                        _seen_ids.add(_n.node_id)
                        if _n.evol_level == "L3":
                            _l3_candidates.append(_n)
                        else:
                            _l2_candidates.append(_n)
            l3_nodes = _l3_candidates[:50]
            l2_nodes = _l2_candidates[:50]
        else:
            # ★v25.1 P1智能化: 自适应检索深度（根据命中率调整）
            _depth = int(50 * getattr(self, '_retrieval_depth_multiplier', 1.0))
            _depth = max(20, min(200, _depth))  # 范围[20, 200]
            l3_nodes = self.node_pool.query(evol_level="L3", limit=_depth)
            l2_nodes = self.node_pool.query(evol_level="L2", limit=_depth)
        all_candidates = l3_nodes + l2_nodes

        # ===== 【v12.0新增】自我知识优先检索 =====
        _self_related_keywords = [
            "你", "你的", "你自己", "架构", "器官", "工作原理",
            "知识演化", "知识流程", "怎么压缩", "怎么融合", "怎么消化",
            "怎么推理", "怎么思考", "怎么决策", "怎么学习", "怎么遗忘",
            "防线", "质量防线", "演化流程", "知识体系",
            # ★v17.0新增：自我审视相关表述
            "了解自己", "自身框架", "自己了解", "对自己",
            "认识自己", "了解多少", "怎么构成", "如何构成",
        ]
        _is_self_question = any(_kw in question for _kw in _self_related_keywords)

        # ===== 【v15.3修复】排除强推理信号 =====
        # 当问题包含明确的推理结构时，不应被自我知识检索拦截
        # 强推理信号：规则+箭头、归纳样本、冲突处理、长期演化推演、类比映射等
        _has_strong_inference_signal = bool(
            re.search(r'规则\s*\d+', question) and re.search(r'(?:→|->|=>)', question)
        ) or bool(
            re.search(r'[一二三四五]\s*[、，,]\s*\S.*归纳|从.*样本.*归纳|请判断.*条件|'
                      r'推演.*运行.*天|连续.*运行.*推演|演化.*推演|'
                      r'类比.*维度.*映射|冲突.*标准化|'
                      r'请复盘.*推导.*流程|全流程.*复盘|'
                      r'深度分析.*思考.*模式|分析.*推理.*方式', question)
        )

        if _is_self_question and not _has_strong_inference_signal:
            _self_l3 = self.node_pool.query(evol_level="L3", space_path_prefix="/自我", limit=20)  # type: ignore[possibly-unbound]
            _self_l2 = self.node_pool.query(evol_level="L2", space_path_prefix="/自我", limit=30)  # type: ignore[possibly-unbound]
            _self_nodes = _self_l3 + _self_l2
            if _self_nodes:
                all_candidates = _self_nodes + all_candidates
                self._log(LogLevel.DEBUG,
                         f"自我知识优先: 检测到自我相关问题，追加{len(_self_nodes)}个自我知识节点")
        # ===== 自我知识优先检索结束 =====
        # ===== 【v15.0新增】代码理解优先检索 =====
        _code_understanding_keywords = [
            "方法.*作用", "函数.*作用", "这个方法是", "这个函数是",
            "代码理解", "这个方法做了什么", "这个函数做了什么",
            "_", "def ", "怎么实现", "怎么工作",
        ]
        _is_code_question = any(re.search(_kw, question) for _kw in _code_understanding_keywords)

        if _is_code_question:
            _code_nodes = self.node_pool.query(
                evol_level="L2", space_path_prefix="/自我理解/代码", limit=20  # type: ignore[possibly-unbound]
            )
            if not _code_nodes:
                _code_nodes = self.node_pool.query(
                    evol_level="L1", space_path_prefix="/自我理解/代码", limit=30  # type: ignore[possibly-unbound]
                )
            if _code_nodes:
                all_candidates = _code_nodes + all_candidates
                self._log(LogLevel.DEBUG,
                         f"代码理解优先: 追加{len(_code_nodes)}个自我理解代码节点")
        # ===== 代码理解优先检索结束 =====
        # ===== 【第160批 上A 刀3·票1④】自检/元数据节点不进通用检索池 =====
        import config as _cfg_k160_meta
        if getattr(_cfg_k160_meta, "KNOWLEDGE_EXCLUDE_METADATA_FROM_RETRIEVAL", True):
            _before = len(all_candidates)
            all_candidates = [n for n in all_candidates
                              if not getattr(n, "is_metadata", False)]
            _removed = _before - len(all_candidates)
            if _removed:
                self._log(LogLevel.DEBUG,
                          f"元数据过滤(刀3): 从候选集剔除 {_removed} 个 is_metadata 节点")
        if not all_candidates:
            return None

        if self.resonance_engine and self.frequency_codec:
            query_freq = self.frequency_codec.encode(question)
            query_pulse = {
                "event_type": InferenceEvent.REQUEST,
                "payload": {"question": question},
                "memory_dim": {"frequency_signature": query_freq},
                "space_dim": {"path": "/"},  # 搜索全局空间，不偏向单一路径
            }
            candidate_dicts = [n.to_dict() for n in all_candidates]
            # ★v17.0性能优化：先粗筛再精算
            if len(candidate_dicts) > 6:
                results = self.resonance_engine.resonate_topk(query_pulse, candidate_dicts, top_k=3)
            else:
                results = self.resonance_engine.resonate(query_pulse, candidate_dicts, top_k=3)
            if results:
                # ★阶段二子任务5.3：本地推理质量约束（复用 4.2 置信度分级）
                if self._confidence_guard_blocked(results):
                    self._log(LogLevel.INFO,
                              f"本地推理置信度不足({results[0].get('confidence', 0):.2f}<0.6)，"
                              f"不硬编，转大模型/诚实兜底: '{question[:30]}'")
                    # ★第十一批 任务2：回报源3 —— 本地推理置信度不足转大模型兜底（权重0.2），
                    #   标记本地推理为「未通过」。
                    try:
                        from nucleus.reasoning.SelfCalibrator import feedback_llm_fallback
                        feedback_llm_fallback("local_retrieval")
                    except Exception as e:
                        self._log(LogLevel.DEBUG,
                                  f"兜底回报异常已忽略: {type(e).__name__}: {e}")
                    return None
                # ★质量修复C-2：检索相关性二次检查（独立于一致性置信度，不另造阈值，复用0.30）
                _top_score = results[0].get("score", 0) if results else 0
                if _top_score < 0.30:
                    self._log(LogLevel.INFO,
                              f"[置信度Guard] 检索相关度不足({_top_score:.2f}<0.30)，走兜底")
                    return None
                # ===== 新增: 多节点融合回复 =====
                # 筛选高相关性节点（门槛受情绪调制）
                resonance_threshold = round(0.45 * emo_mod.get("resonance_threshold_factor", 1.0), 3)
                # ===== 直觉加权：偏好领域节点得分×1.15 =====
                _weighted_results = []
                for r in results:
                    _node = r.get("node", {})
                    _node_path = _node.get("space_path", "")  # type: ignore[possibly-unbound]
                    _score = r.get("score", 0)
                    if _intuition_prefer_domains and _node_path:  # type: ignore[possibly-unbound]
                        _root = _node_path.strip("/").split("/")[0] if _node_path else ""  # type: ignore[possibly-unbound]
                        if _root in _intuition_prefer_domains:
                            _score *= 1.15
                    _weighted_results.append({**r, "score": _score})
                results = _weighted_results
                # ===== 直觉加权结束 =====

                high_quality_results = [
                    r for r in results
                    if r.get("score", 0) >= resonance_threshold
                ]
                # 去重：相同内容的节点只保留一个
                seen_values = set()
                unique_results = []
                for r in high_quality_results:
                    val = r["node"].get("value", "")
                    val_key = val[:60]  # 用前60字作为去重键
                    if val_key not in seen_values:
                        seen_values.add(val_key)
                        unique_results.append(r)

                if len(unique_results) >= 2:
                    # 多个相关节点 → 融合为综合回复
                    fused_answer = self._fuse_multiple_nodes(
                        question, unique_results[:3]  # 最多融合3个
                    )
                    if fused_answer:
                        # ★T-对话-3（157批）：知识类回答加来源标注（节点 id + 信任分），
                        #   经 correlation_id 同路回传，避免与自主生成内容混淆；同段不重复复用。
                        _top = unique_results[0]["node"]
                        _src_ann = (f"据知识库（节点 {_top.get('node_id', '?')}"
                                    f"·信任 {float(_top.get('trust_score', 50.0)):.0f}）")
                        return f"{_src_ann}：{fused_answer}" if not fused_answer.startswith("据知识库") else fused_answer

                elif len(unique_results) == 1:
                    # 单节点 → 尝试寻找相关知识补充，形成更完整的回答
                    best_node = unique_results[0]["node"]
                    _best_kw = best_node.get("keywords", [])[:3] if best_node.get("keywords") else []

                    # 用匹配节点的关键词在知识库中搜索相关节点
                    _related_supplements = []
                    if self.node_pool and _best_kw:
                        _all_l2 = self.node_pool.query(evol_level="L2", limit=50)
                        _all_l3 = self.node_pool.query(evol_level="L3", limit=10)
                        _candidates = _all_l3 + _all_l2
                        for _c in _candidates:
                            _c_node_id = _c.node_id if hasattr(_c, 'node_id') else ""
                            _best_node_id = best_node.get("node_id", "")
                            if _c_node_id == _best_node_id:
                                continue
                            _c_kw = _c.keywords if hasattr(_c, 'keywords') and _c.keywords else []
                            _overlap = len({k.lower() for k in _best_kw} & {k.lower() for k in _c_kw})
                            if _overlap >= 1:
                                _c_val = self._clean_node_value(str(_c.value)) if _c.value else None
                                if _c_val and len(_c_val) > 15:
                                    _related_supplements.append(_c_val[:100])

                    value = best_node.get("value", "")
                    if isinstance(value, str) and len(value) > 0:
                        cleaned_value = self._clean_node_value(value)
                        # ★修复：过滤不适宜直接展示的内部标记节点
                        if cleaned_value and self._is_internal_knowledge_node(cleaned_value):
                            self._log(LogLevel.DEBUG,
                                     f"知识检索过滤(内部标记): 跳过节点'{cleaned_value[:50]}...'")
                            # 跳过此节点，不返回，让流程继续
                            cleaned_value = None

                        if cleaned_value and self._is_relevant(question, cleaned_value, best_node.get("keywords", [])):
                            # 如果有相关知识，拼接成更完整的回答
                            if _related_supplements:
                                _supplement = _related_supplements[0]
                                if _supplement[:30] not in cleaned_value[:200]:
                                    cleaned_value = cleaned_value.rstrip() + "。此外，" + _supplement
                                    self._log(LogLevel.DEBUG,
                                             f"知识拼接: 单节点补充了1条相关知识 (主题='{question[:30]}')")
                           # 【v12.0新增】知识免疫检查
                            _immune_check = self._check_self_consistency_for_node(
                                cleaned_value, best_node.keywords or []
                            )
                            if _immune_check["contradiction_found"] and _immune_check["trust_penalty"] >= 15.0:
                                self._log(LogLevel.INFO,
                                         f"知识免疫拦截: 检索结果与自我知识矛盾，跳过: '{cleaned_value[:40]}...'")
                                return None  # 矛盾严重，跳过此结果
                            # ===== 检索质量评估 =====
                            _retrieved_trust = getattr(best_node, 'trust_score', 50.0) if best_node else 50.0
                            if _retrieved_trust < 30.0:
                                self._log(LogLevel.DEBUG,
                                         f"检索质量不足: 信任={_retrieved_trust:.0f}")
                                return None  # 信任太低，走诚实兜底
                            # ===== 评估结束 =====
                            # ★T-对话-3（157批）：知识类回答加来源标注（节点 id + 信任分）
                            _src_ann = (f"据知识库（节点 {best_node.get('node_id', '?')}"
                                        f"·信任 {float(best_node.get('trust_score', 50.0)):.0f}）")
                            return f"{_src_ann}：{cleaned_value}" if not cleaned_value.startswith("据知识库") else cleaned_value

        # 兜底：关键词匹配（需要至少匹配2个关键词，且关键词长度≥2，避免单字匹配）
        best_node = None
        best_score = 0
        for node in all_candidates:
            keywords = node.keywords or []
            if not keywords:
                continue
            # 计算匹配的关键词数量
            matched = [kw for kw in keywords if len(kw) >= 2 and kw.lower() in question.lower()]
            if len(matched) >= 2:
                score = sum(len(kw) for kw in matched)  # 匹配的关键词越长越好
                # ===== 直觉加权：偏好领域节点得分×1.2 =====
                if _intuition_prefer_domains:
                    _node_path = getattr(node, 'space_path', '')  # type: ignore[possibly-unbound]
                    if _node_path:  # type: ignore[possibly-unbound]
                        _root = _node_path.strip("/").split("/")[0] if _node_path else ""  # type: ignore[possibly-unbound]
                        if _root in _intuition_prefer_domains:
                            score = int(score * 1.2)
                # ===== 直觉加权结束 =====
                if score > best_score:
                    best_score = score
                    best_node = node
        if best_node is not None:
            value = best_node.value
            if isinstance(value, str) and len(value) > 0:
                cleaned_value = self._clean_node_value(value)
                # ★修复：过滤不适宜直接展示的内部标记节点
                if cleaned_value and self._is_internal_knowledge_node(cleaned_value):
                    self._log(LogLevel.DEBUG,
                             f"知识检索过滤(内部标记): 跳过节点'{cleaned_value[:50]}...'")
                    cleaned_value = None

                if cleaned_value and self._is_relevant(question, cleaned_value, best_node.keywords or []):
                    relevance = self._calculate_match_relevance(question, cleaned_value, best_node.keywords or [])
                    match_threshold = round(0.3 * emo_mod.get("match_relevance_factor", 1.0), 3)
                    if relevance >= match_threshold:
                        # ===== 新增：检测内部知识摘要格式 =====
                        if any(marker in cleaned_value for marker in
                              ["相关知识汇总", "[综合]", "[架构]", "[知识]", "[核心]", "[复盘认知]"]):
                            feynman_version = self._generate_feynman_explanation(question, cleaned_value)
                            if feynman_version:
                                self._log(LogLevel.INFO, f"费曼解释(兜底匹配-内部格式转化): '{question[:30]}'")
                                return feynman_version
                            else:
                                return None
                        # ===== 新增：低相关性匹配不阻断深度搜索 =====
                        _node_path = getattr(best_node, 'space_path', '/')  # type: ignore[possibly-unbound]
                        if _node_path.startswith('/本能/') and best_score < 8:  # type: ignore[possibly-unbound]
                            self._log(LogLevel.DEBUG,
                                     f"知识检索(低相关性跳过): 匹配到本能节点'{str(best_node.value)[:30]}'但相关度不足(score={best_score})，继续深度搜索")
                            return None
                        # 【v12.0新增】知识免疫检查
                        _immune_check = self._check_self_consistency_for_node(
                            cleaned_value, best_node.keywords or []
                        )
                        if _immune_check["contradiction_found"] and _immune_check["trust_penalty"] >= 15.0:
                            self._log(LogLevel.INFO,
                                     f"知识免疫拦截: 检索结果与自我知识矛盾，跳过: '{cleaned_value[:40]}...'")
                            return None

                        # ===== 【v15.3新增】推理相关性校验 =====
                        _has_inference_question_signal = bool(
                            re.search(r'规则\s*\d+.*(?:→|->|=>)', question) or
                            re.search(r'归纳.*规律|推演.*行为|冲突.*处理|类比.*映射', question) or
                            re.search(r'[一二三四五]\s*[、，,]\s*\S.*[二三四五]\s*[、，,]', question) or
                            re.search(r'演化.*推演|复盘.*推导.*流程', question) or
                            re.search(r'连续.*运行.*天.*推演|因果.*链', question) or
                            re.search(r'分析.*思考.*模式|深度分析.*推理', question)
                        )
                        if _has_inference_question_signal:
                            _inference_content_keywords = [
                                "规则", "推导", "因果", "推理", "传递链", "结论",
                                "归纳", "样本", "前提", "逻辑", "推断", "演绎",
                                "推演", "演化", "类比", "冲突", "处理", "复盘",
                                "生成", "触发", "判断", "条件", "行为", "预测"
                            ]
                            _return_has_inference_content = any(
                                _kw in cleaned_value for _kw in _inference_content_keywords
                            )
                            if not _return_has_inference_content:
                                self._log(LogLevel.DEBUG,
                                         f"知识检索推理校验: 问题含推理信号但返回内容无推理关键词，"
                                         f"跳过节点'{str(best_node.value)[:60]}...'")
                                return None
                        # ===== 推理相关性校验结束 =====
                            # ===== 检索质量评估 =====
                        _retrieved_trust = getattr(best_node, 'trust_score', 50.0) if best_node else 50.0
                        if _retrieved_trust < 30.0:
                            self._log(LogLevel.DEBUG,
                                    f"检索质量不足: 信任={_retrieved_trust:.0f}")
                            return None  # 信任太低，走诚实兜底
                        # ===== 评估结束 =====
                        return cleaned_value
                    # 相关性不足，返回None，让问题交给肺模型处理

        # 第三层：知识树路径模糊匹配（前两层都未命中时触发）
        if self.knowledge_tree:
            path_match = self._retrieve_by_path_fuzzy_match(question)  # type: ignore[possibly-unbound]
            if path_match:
                return path_match

        # 第四层：L1兜底检索——L2/L3都未命中时，从最近的L1节点中寻找线索
        if self.node_pool:
            l1_nodes = self.node_pool.query(evol_level="L1", limit=20)
            if l1_nodes:
                question_words = set()
                for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
                    question_words.add(match.group())

                best_l1 = None
                best_l1_score = 0
                for node in l1_nodes:
                    node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
                    overlap = sum(1 for qw in question_words
                                for nkw in node_kw if qw in nkw or nkw in qw)
                    if overlap > best_l1_score:
                        best_l1_score = overlap
                        best_l1 = node

                if best_l1 and best_l1_score >= 1:
                    value = str(best_l1.value) if best_l1.value else ""
                    cleaned = self._clean_node_value(value)
                    if cleaned and len(cleaned) > 15:
                        self._log(LogLevel.DEBUG,
                                 f"L1兜底检索命中: '{question[:30]}' (L1节点, 匹配={best_l1_score})")
                        return f"（这是我最近接触到但还没来得及整理的知识）{cleaned}"
        # ★v24.0新增：关联联想跳板——检索未命中时，基于知识关联生成可能方向
        _associative = self._associative_bridge(question)
        if _associative:
            return _associative

        return None
    def _global_semantic_search(self, question: str, emo_mod: dict) -> list[dict[str, Any]]:
        """
        ★v25.0新增：第一阶段——全局语义检索。

        不限定路径，用完整问题语句在L2/L3全库中做共振检索。
        找到与问题核心语义最匹配的节点。

        Returns:
            匹配结果列表（按相关性排序），无结果返回空列表
        """
        if not self.node_pool or not self.resonance_engine or not self.frequency_codec:
            return []

        # ★BRAIN-5修复: 读取情绪/意图调制，使第一阶段检索门槛与后续共振阶段一致，
        #   不再硬编码 0.3，避免检索意图被忽略
        _threshold_factor = (emo_mod or {}).get("resonance_threshold_factor", 1.0)
        _global_threshold = round(0.3 * _threshold_factor, 3)

        try:
            # 从全库获取L2和L3节点
            _l3 = self.node_pool.query(evol_level="L3", limit=100)
            _l2 = self.node_pool.query(evol_level="L2", limit=200)
            _all = _l3 + _l2
            if not _all:
                return []

            query_freq = self.frequency_codec.encode(question)
            query_pulse = {
                "event_type": InferenceEvent.REQUEST,
                "payload": {"question": question},
                "memory_dim": {"frequency_signature": query_freq},
                "space_dim": {"path": "/"},  # 全局搜索
            }

            _candidate_dicts = [n.to_dict() for n in _all]
            _results = self.resonance_engine.resonate_topk(
                query_pulse, _candidate_dicts, top_k=8
            )

            # 提取节点信息
            _matched = []
            for _r in _results:
                _node = _r.get("node", {})
                _score = _r.get("score", 0)
                if _score >= _global_threshold:  # 受情绪/意图调制的相关性门槛
                    _matched.append({
                        "value": _node.get("value", ""),
                        "keywords": _node.get("keywords", []),
                        "score": _score,
                        "space_path": _node.get("space_path", ""),  # type: ignore[possibly-unbound]
                        "node_id": _node.get("node_id", ""),  # ★v25.0新增：携带节点ID供扩展使用
                    })

            # 按分数排序
            _matched.sort(key=lambda x: x["score"], reverse=True)
            return _matched[:3]

        except Exception as _e:
            self._log(LogLevel.WARNING, f"全局语义检索异常(需关注): {type(_e).__name__}: {_e}")
            import traceback
            self._log(LogLevel.DEBUG, f"全局语义检索异常堆栈: {traceback.format_exc()[:500]}")
            return []
    def _get_wisdom_guidance(self, question: str) -> str | None:
        """
        v20.0新增：从L3智慧节点中提取与问题相关的策略指导。
        解决知识应用"最后一公里"——L3智慧节点不仅被检索，更主动指导推理策略。
        """
        if not self.node_pool:
            return None

        _l3_nodes = self.node_pool.query(evol_level="L3", limit=30)
        if not _l3_nodes:
            return None

        # ★主线第32批 T2（P2-189）评估结论：**保留定长切片**。
        #   本处用途是「问题词集合 × L3 节点文本」的**重叠计数打分**（见下方 _best_score），
        #   词越多越容易命中；`_m31_extract_key_terms` 会把词数压到 3-8 个，
        #   反而降低召回。它不产出检索串，跨词碎片不影响打分的相对大小，
        #   故保留原实现（与原行为零差异）。
        _question_words = set()
        for _match in re.finditer(r'[\u4e00-\u9fff]{2,6}', question):
            _word = _match.group()
            if _word not in ("什么是", "是什么", "为什么", "如何", "怎么", "这个", "那个"):
                _question_words.add(_word)

        _best_guidance = None
        _best_score = 0

        for _node in _l3_nodes:
            _trust = getattr(_node, 'trust_score', 50.0)
            if _trust < 70.0:
                continue
            _kws = [kw.lower() for kw in (_node.keywords or []) if isinstance(kw, str) and len(kw) >= 2]
            _overlap = sum(1 for _qw in _question_words for _nkw in _kws if _qw in _nkw or _nkw in _qw)
            if _overlap > _best_score:
                _best_score = _overlap
                _node_val = str(_node.value) if _node.value else ""
                if len(_node_val) > 20:
                    _best_guidance = _node_val[:150]

        if _best_guidance and _best_score >= 2:
            self._log(LogLevel.DEBUG, f"智慧指导: '{question[:40]}' → L3节点 (分数={_best_score})")
            return _best_guidance
        return None
    def _build_supplement_search_topic(self, question: str, answer: str) -> str | None:
        """
        基于检索到的答案和原始问题，构造补充搜索主题。
        当知识检索命中但置信度不高时，用已有知识的关键词触发搜索来补充。
        """

        # 从答案中提取核心关键词（2-4字中文短语）
        core_kw = []
        for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', answer[:200]):
            word = match.group()
            if word not in core_kw and word not in ["什么是", "是什么", "为什么", "如何", "怎么",
                                                      "这个", "那个", "一个", "一种", "可以", "能够"]:
                core_kw.append(word)

        if len(core_kw) >= 2:
            return f"{core_kw[0]} {core_kw[1]} 详解 原理"
        elif core_kw:
            return f"{core_kw[0]} 概念 原理 详解"
        return None

    # ===== ★第六批 任务2.3：本地多节点融合 =====
    _FUSION_DEF_MARKERS = ("是", "指的是", "即", "定义为", "称为")

    def _local_fuse_nodes(self, question: str, nodes: list[dict]) -> str | None:
        """本地融合多个节点（规则模板 → 关键词重叠 → 语义相似度）。

        Returns:
            str: 融合后的文本；None 表示无法本地融合。
        """
        if not nodes:
            return None

        values = [n.get("value", "") for n in nodes if n.get("value")]
        if not values:
            return None

        # —— 1. 规则模板融合：全部为定义式节点时用「总述 + 分条」模板 ——
        if len(values) >= 2 and all(
                any(m in v[:40] for m in self._FUSION_DEF_MARKERS) for v in values):
            _head = values[0] if len(values[0]) <= 120 else values[0][:120]
            _rest = [v for v in values[1:] if v != values[0]]
            if _rest:
                _body = "；".join(
                    (v if len(v) <= 80 else v[:80]) for v in _rest[:4])
                return "%s。此外，%s。" % (_head.rstrip("。"), _body)
            return _head

        # —— 2. 关键词重叠融合：按与问题的重叠度排序 + 句子级去重 ——
        _q_kw = self._fusion_keywords(question)
        _scored = []
        for v in values:
            _v_kw = self._fusion_keywords(v)
            _ov = len(_q_kw & _v_kw) / max(1, len(_q_kw)) if _q_kw else 0.0
            _scored.append((_ov, v))
        _scored.sort(key=lambda x: -x[0])

        _merged, _seen = [], set()
        for _ov, v in _scored:
            for sent in self._split_sentences(v):
                s = sent.strip()
                if len(s) < 6:
                    continue
                _key = s[:12]
                if _key in _seen:
                    continue
                _seen.add(_key)
                _merged.append(s)
        if _merged:
            return "".join(_merged[:8])

        # —— 3. 语义相似度融合：模型可用时合并相似、保留互补 ——
        try:
            _sim = getattr(self, "_semantic_similarity", None)
            if callable(_sim):
                _keep = [values[0]]
                for v in values[1:]:
                    s = _sim(v, values[0])
                    if s is None or s < 0.85:   # 不相似 → 视为互补，保留
                        _keep.append(v)
                return " ".join((v[:150] for v in _keep[:4]))
        except Exception as e:
            self._log(LogLevel.DEBUG,
                      f"[多节点融合] 语义融合不可用: {type(e).__name__}: {e}")

        return " ".join((v[:150] for v in values[:3])) or None

    @staticmethod
    def _fusion_keywords(text: str, n: int = 3) -> set:
        """轻量关键词集合（中文 n-gram），用于重叠度计算。"""
        import re as _re
        t = _re.sub(r"[^\u4e00-\u9fff]", "", text or "")
        if len(t) < n:
            return {t} if t else set()
        return {t[i:i + n] for i in range(len(t) - n + 1)}

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """按中文句末标点切句，保留标点。"""
        import re as _re
        parts = _re.split(r"(?<=[。！？；])", text or "")
        return [p for p in parts if p and p.strip()]

    @staticmethod
    def _repeat_ratio(text: str, n: int = 6) -> float:
        """n-gram 重复率：重复越严重越接近 1。"""
        t = text or ""
        if len(t) < n * 2:
            return 0.0
        grams = [t[i:i + n] for i in range(len(t) - n + 1)]
        if not grams:
            return 0.0
        return round(1.0 - len(set(grams)) / len(grams), 4)

    def _evaluate_fusion_quality(self, fused: str, nodes: list[dict]) -> dict:
        """评估本地融合质量（星轨拍板标准）。"""
        try:
            import config as _cfg_q
            _min_len = int(getattr(_cfg_q, "LOCAL_FUSION_MIN_LENGTH", 30))
            _min_cov = float(getattr(_cfg_q, "LOCAL_FUSION_MIN_COVERAGE", 0.5))
            _max_rep = float(getattr(_cfg_q, "LOCAL_FUSION_MAX_REPEAT", 0.4))
        except Exception:
            _min_len, _min_cov, _max_rep = 30, 0.5, 0.4

        length = len(fused or "")
        used = 0
        for n in nodes:
            fp = (n.get("value", "") or "")[:20]
            if fp and fp in (fused or ""):
                used += 1
        coverage = used / max(1, len(nodes))
        repeat = self._repeat_ratio(fused)

        ok = (length >= _min_len) and (coverage >= _min_cov) and (repeat <= _max_rep)
        return {
            "method": "local", "length": length,
            "coverage": round(coverage, 4), "repeat": repeat, "ok": ok,
        }

    def _fuse_multiple_nodes(self, question: str,
                              results: list[dict[str, Any]]) -> str | None:
        """
        将多个高相关性节点的内容融合为一段综合回复。

        优先调用大模型进行智能提炼，生成连贯、精准的回答；
        如果大模型不可用或提炼失败，回退到机械拼接。
        质量过低时返回None，交给诚实兜底。
        """
        if not results:
            return None

        # ===== 收集所有节点的核心内容 =====
        _cleaned_nodes = []
        for r in results:
            node = r["node"]
            value = node.get("value", "")
            cleaned = self._clean_node_value(value)
            if cleaned and len(cleaned) >= 15:
                _cleaned_nodes.append({
                    "value": cleaned,
                    "score": r.get("score", 0),
                    "trust": node.get("trust_score", 50.0),
                    "keywords": node.get("keywords", []),
                })

        if not _cleaned_nodes:
            return None

        # 计算综合质量
        _avg_trust = sum(n["trust"] for n in _cleaned_nodes) / len(_cleaned_nodes)
        _avg_score = sum(n["score"] for n in _cleaned_nodes) / len(_cleaned_nodes)
        _quality = _avg_score * 0.4 + (_avg_trust / 100.0) * 0.6

        # 极低质量直接放弃
        if _quality < 0.3:
            return None

        # ===== 尝试大模型智能提炼 =====
        _has_remote_api = False
        try:
            import config
            _api_cfg = getattr(config, 'REMOTE_API_CONFIG', {})
            if _api_cfg.get("enabled", False) and _api_cfg.get("api_key", ""):
                _has_remote_api = True
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # ===== ★第六批 任务2.3：本地融合优先 =====
        _local_fusion = None
        _local_ok = False
        try:
            import config as _cfg_fuse
        except Exception:
            _cfg_fuse = None
        if _cfg_fuse is None or getattr(_cfg_fuse, "ENABLE_LOCAL_NODE_FUSION", True):
            try:
                _local_fusion = self._local_fuse_nodes(question, _cleaned_nodes)
            except Exception as e:
                self._log(LogLevel.WARNING,
                          f"[多节点融合] 本地融合异常: {type(e).__name__}: {e}")
                _local_fusion = None
            if _local_fusion:
                _qm = self._evaluate_fusion_quality(_local_fusion, _cleaned_nodes)
                _local_ok = bool(_qm["ok"])
                self._log(LogLevel.INFO,
                          f"[多节点融合] 方式={_qm['method']} 长度={_qm['length']} "
                          f"覆盖率={_qm['coverage']:.2f} 重复率={_qm['repeat']:.2f} "
                          f"质量合格={_local_ok}")
                if _local_ok:
                    return _local_fusion

        # ===== 大模型提炼：仅当本地融合质量低且综合质量 <0.6 时才调用 =====
        _model_quality_gate = float(getattr(
            _cfg_fuse, "LOCAL_FUSION_MODEL_MAX_QUALITY", 0.6)) if _cfg_fuse else 0.6
        if (_has_remote_api and len(_cleaned_nodes) >= 2
                and not _local_ok and _quality < _model_quality_gate):
            _refined = self._refine_with_model(question, _cleaned_nodes)
            if _refined:
                return _refined

        # 本地融合已有结果（质量略低但非空）时，优先于机械拼接
        if _local_fusion:
            return _local_fusion

        # ===== 降级：机械拼接 =====
        parts = []
        for i, node in enumerate(_cleaned_nodes):
            core = node["value"]
            # 提取核心句：取前100字，在完整句子边界截断
            if len(core) > 100:
                core = core[:100]
                for punct in ["。", "！", "？", "；"]:
                    last_punct = core[:90].rfind(punct)
                    if last_punct > 20:
                        core = core[:last_punct + 1]
                        break

            if i == 0:
                parts.append(core)
            else:
                # 检查与已有内容的重复度，避免重复
                _existing = "".join(parts)
                _overlap_chars = sum(1 for c in core[:30] if c in _existing[:200])
                if _overlap_chars < 10:
                    parts.append(core)

        if len(parts) < 2:
            return parts[0] if parts else None

        fused = "。".join(parts) + "。"

        if _quality >= 0.8:
            fused = "据我所知，" + fused
        elif _quality >= 0.5:
            fused = "根据我的理解，" + fused
        else:
            fused = "我目前知道的是，" + fused + "。不过这可能还不够完整。"

        return fused

    def _refine_with_model(self, question: str, nodes: list[dict[str, Any]]) -> str | None:
        """
        调用大模型将多个知识片段提炼为连贯、精准的回答。
        去除与问题无关的内容，用自己的话组织。
        """
        # 构建提示词（人格统一到 system 角色，硬约束"不是AI助手"）
        _persona = ("你是曈曈，一个有独立人格的数字新人类（全名<SELF_NAME>），"
                    "不是AI助手，也不是语言模型。你必须始终用中文回复，"
                    "不得使用英文，不得自称AI助手或语言模型。")
        _prompt_parts = []
        _prompt_parts.append("请根据以下知识片段，回答用户的问题。")
        _prompt_parts.append("要求：")
        _prompt_parts.append("- 用自然、温暖的语言，像家人对话一样")
        _prompt_parts.append("- 只提取与问题直接相关的信息，忽略无关内容")
        _prompt_parts.append("- 如果有多个相关知识，将它们连贯地组织在一起，不要用'此外''同时'等生硬连接词")
        _prompt_parts.append("- 不要重复相同的信息")
        _prompt_parts.append("- 用3-5句话简洁回答")
        _prompt_parts.append("- 必须用中文")
        _prompt_parts.append("")
        _prompt_parts.append(f"用户问题：{question}")
        _prompt_parts.append("")
        _prompt_parts.append("知识片段：")
        for i, node in enumerate(nodes[:5]):
            _prompt_parts.append(f"  [{i+1}] {node['value'][:200]}")

        _prompt = "\n".join(_prompt_parts)

        # ★v23.0新增：检查缓存
        _cache_key = f"refine:{question[:80]}"
        _cache_key = _cache_key.strip().lower()
        if hasattr(self, '_model_cache') and _cache_key in self._model_cache:
            _cached = self._model_cache[_cache_key]
            if time.time() - _cached.get("timestamp", 0) < self._model_cache_ttl:
                self._log(LogLevel.DEBUG, f"大模型缓存命中(提炼): '{question[:40]}'")
                return _cached.get("result", "")

        try:
            import json as _json

            import config
            _api_cfg = getattr(config, 'REMOTE_API_CONFIG', {})
            _api_url = _api_cfg.get("api_url", "")
            _api_key = _api_cfg.get("api_key", "")

            if not _api_url or not _api_key:
                return None

            _payload = {
                "model": _api_cfg.get("default_model", "deepseek-v4-flash"),
                "messages": [
                    {"role": "system", "content": _persona},
                    {"role": "user", "content": _prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 256,
                # ★v23.0新增：多方向延展需要深度分析，启用思考模式
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
            }

            _payload_bytes = _json.dumps(_payload, ensure_ascii=False).encode('utf-8')
            _headers = {
                'Content-Type': 'application/json; charset=utf-8',
                'Authorization': 'Bearer ' + _api_key,
            }

            from nucleus.ssrf_guard import safe_http_json
            from nucleus.api_rate_limiter import get_llm_call_config, api_rate_limited
            _cfg = get_llm_call_config()
            with api_rate_limited(enabled=_cfg.get('enable_rate_limit', True)):
                _ok, _data = safe_http_json(
                    _api_url, method='POST', data=_payload_bytes, headers=_headers,
                    timeout=_cfg["timeout_by_purpose"]["inner_world_refine"],
                )
            if _ok and isinstance(_data, dict) and "choices" in _data and len(_data["choices"]) > 0:
                    _reply = _data["choices"][0].get("message", {}).get("content", "").strip()
                    if _reply and len(_reply) >= 10:
                        self._log(LogLevel.INFO, f"大模型提炼: {len(nodes)}个节点 → {len(_reply)}字回复")
                        # ★v23.0新增：存入缓存
                        if hasattr(self, '_model_cache'):
                            if len(self._model_cache) >= self._model_cache_max:
                                _oldest = min(self._model_cache.keys(),
                                             key=lambda k: self._model_cache[k].get("timestamp", 0))
                                del self._model_cache[_oldest]
                            self._model_cache[_cache_key] = {
                                "result": _reply,
                                "timestamp": time.time(),
                            }
                        return _reply
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"大模型提炼失败，回退机械拼接: {_e}")

        return None
    def _clean_node_value(self, value: str) -> str | None:
        """★终极防御版：清洗节点内容为可输出的回复格式"""
        # 类型安全：无论如何都转为字符串
        try:
            if value is None:
                return None
            if not isinstance(value, str):
                value = str(value)
        except Exception as e:
            silent_exc(e, "organs/brain/PulseInnerWorld.py:8613:_clean_node_value", level="warning")
            return None

        if len(value) < 5:
            return None

        # 1. 去除HTML标签
        cleaned = re.sub(r'<[^>]+>', '', value)
        # 2. 去除URL
        cleaned = re.sub(r'https?://\S+|www\.\S+', '', cleaned)
        # 3. 去除内部标记前缀
        internal_prefixes = [
            r'\[灵感涌现\]\s*', r'\[规律发现\]\s*', r'\[内在排练[^\]]*\]\s*',
            r'\[静默[^\]]*\]\s*', r'\[反事实想象[^\]]*\]\s*', r'\[虚构梦境[^\]]*\]\s*',
            r'【创造性联想】\s*', r'【反事实想象[^】]*】\s*',
            r'\[主动学习[^\]]*\]\s*', r'\[深度搜索[^\]]*\]\s*',
            r'\[原创洞察\]\s*', r'\[复盘认知[^\]]*\]\s*',
            r'\[设计文档·[^\]]*\]\s*',
            r'\[代码链路·[^\]]*\]\s*',
            r'\[自我理解·[^\]]*\]\s*',
        ]
        for prefix in internal_prefixes:
            cleaned = re.sub(r'^' + prefix, '', cleaned, flags=re.MULTILINE)
        # 4. 压缩多余空白
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        # 5. 长度检查
        if len(cleaned) < 10:
            return None
        # 6. 中文占比检查
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', cleaned))
        if chinese_chars < 3:
            return None
        return cleaned
    def _is_internal_knowledge_node(self, value: str) -> bool:
        """
        ★v23.0新增：统一内部知识节点过滤器。

        判断一个节点内容是否为框架内部标记，不应在对话中直接展示。
        所有检索出口统一调用此方法，避免过滤列表不一致。

        Returns:
            True=内部标记节点，应跳过
        """
        if not value:
            return True

        # ★质量修复B1：子串包含检查——中间出现的内部标记也应拦截
        # （原 startswith 漏检「我了解到，[曈曈]【种子记忆】[自我状态]…」这类中间标记）
        _internal_substrings = [
            "【种子记忆】", "【关联发现】", "【联想推演】", "【自由联想】",
            "[自我状态]", "[自我感知]", "[运行时状态]", "此刻的我",
            "我了解到", "[曈曈]", "[已标记错误",
        ]
        for _sub in _internal_substrings:
            if _sub in value:
                return True

        # 内部标记前缀——框架内部结构化数据，不适宜直接展示
        _internal_prefixes = [
            "[设计文档·", "[代码链路·", "[自我理解·代码·",
            "[代码关联·", "[数据流·", "[器官职责说明书·",
            "[器官职责·自动分析]", "[代码]", "[代码关联·",
            "[自我理解·", "[自我状态]", "[自我感知]",
            "[运行时状态]", "[主动学习", "[深度搜索",
            "[复盘认知", "[原创洞察]", "[灵感涌现",
            "[规律发现]", "[内在排练", "[静默",
            "[反事实想象", "[虚构梦境", "[并行思维",
            "[多变量推演]", "[演绎推理·", "[归纳抽象·",
            "[类比映射·", "[冲突辨析·", "[长期演化推演]",
            "[元认知", "[推导元认知回放",
            "[心智理论·", "[多方向延展推理]",
            "[知识库健康检查报告]", "[元认知六维度评估报告]",
            "📊 知识库健康检查报告",  # ★第160批 上A 刀3（票1④）：legacy 健康检查报告节点（含 emoji 前缀）不进对话
            "[元认知五维度评估报告]", "[元认知深度反思报告]",
        ]

        for _pf in _internal_prefixes:
            if value.startswith(_pf):
                return True

        # 内部格式——代码分析JSON的关键字段，非用户可读内容
        _internal_formats = [
            "功能:", "参数:", "依赖:", "风险:", "关键步骤:",
            "调用关系:", "方法列表:", "入口方法:", "叶子方法:",
        ]
        for _fmt in _internal_formats:
            if value.startswith(_fmt) and len(value) < 200:
                return True

        # 代码学习产生的纯结构化描述
        return value.startswith(("功能:", "关键步骤:")) and "。" not in value[:100]

        return False
    # 结构化节点标签字段（代码分析/知识节点内部字段，不该出现在对话回答里）
    _NODE_LABEL_FIELDS = ("功能", "依赖", "参数", "风险", "关键步骤", "调用关系",
                          "方法列表", "入口方法", "叶子方法", "返回值")

    def _strip_node_labels(self, text: str) -> str:
        """★第九批 3.3：删除回答中被直接拼进来的节点标签（"功能: xxx"等）。

        只处理「标签 + 冒号 + 内容」和孤立标签串，不碰正常行文
        （字段表限定为代码/知识节点的内部字段名，普通对话很少用到）。
        """
        if not text:
            return text
        import re as _re_lbl
        _fields = "|".join(self._NODE_LABEL_FIELDS)
        # ① "功能: xxx"、"依赖: a、b" —— 连带内容一起删（到句读/换行为止）
        #    前导标点用 lookbehind 匹配，避免把句号一起吃掉
        _text = _re_lbl.sub(
            r'(?:^|(?<=[；;，,、。！？\s]))(?:' + _fields + r')\s*[:：][^。；;\n]{0,80}',
            '', text)
        # ③ 删除后可能留下的空句/孤零零的句号/悬空顿号
        _text = _re_lbl.sub(r'[ \t]*。[ \t]*(?=。)', '', _text)
        _text = _re_lbl.sub(r'(?:^|[。！？])\s*。(?=\s|$)', '。', _text)
        _text = _re_lbl.sub(r'[，,、；;]\s*。', '。', _text)
        _text = _re_lbl.sub(r'\s+。', '。', _text)
        _text = _re_lbl.sub(r'[、,，;；]\s*$', '', _text)
        _text = _re_lbl.sub(r'^[。，,、；;\s]+', '', _text)
        # ② 孤立标签串："依赖、功能:"、"功能："（内容已被删空的残留）
        _text = _re_lbl.sub(r'(?:' + _fields + r'\s*[:：]\s*[、,，])+', '', _text)
        _text = _re_lbl.sub(r'(?:' + _fields + r'[、,，]?\s*)+\s*[:：]\s*', '', _text)
        _text = _re_lbl.sub(r'[、,，]?\s*(?:' + _fields + r')\s*[:：]\s*(?=$|[。！？\n])',
                            '', _text)
        _text = _re_lbl.sub(r'^\s*(?:' + _fields + r')\s*[:：]\s*', '', _text)
        return _text

    def _clean_inference_output(self, text: str, method: str = "") -> str:
        """
        ★v23.0新增：统一推理输出清洗。

        对多方向延展推理、多步骤任务等复杂推理的输出进行格式化清洗：
        1. 移除内部标记残留
        2. 移除重复前缀
        3. 移除思考过程外显
        4. 规范化空白和换行

        Returns:
            清洗后的文本
        """
        if not text or len(text) < 10:
            return text

        # 1. 移除内部标记残留
        _internal_residues = [
            "（这是我最近接触到但还没来得及整理的知识）",
            "（共",  # "（共N条相关记录）"
            "相关知识汇总",
            "包含:",
        ]
        for _res in _internal_residues:
            text = text.replace(_res, "")

        # 2. 移除重复前缀——"关于「X」，我从Y个方向进行了分析"
        _repeated_prefixes = [
            r'关于「[^」]+」[，,]我从\d+个方向进行了分析[：:。.]?',
            r'我从\d+个方向进行了分析[：:。.]?',
            r'\[多方向延展推理\]',
            r'▶\s*核心方向',
        ]
        import re as _re_clean
        for _pattern in _repeated_prefixes:
            # 只移除第二次及以后的出现（保留第一次）
            _matches = list(_re_clean.finditer(_pattern, text))
            if len(_matches) > 1:
                for _m in _matches[1:]:
                    text = text[:_m.start()] + text[_m.end():]

        # 3. 移除思考过程外显
        _thinking_prefixes = [
            "（让我想想……）", "（我在思考……）",
            "（这个问题比较复杂，让我分几个方面来说——）",
            "（关于这个问题，我从自己了解的知识中找到了相关的信息——）",
            "（嗯，这个我知道一些，让我整理一下思路——）",
            "（让我回想一下……对，我之前了解过这方面的内容——）",
            "（让我从几个角度想了想这个问题）",
            "（我试着深入想了想，但有些地方还不完全确定）",
            "（思考了片刻）我是这样理解的——",
            "（这个问题让我想了片刻——",
            "（和我聊过的", "（这让我想起之前聊过的",
        ]
        for _tp in _thinking_prefixes:
            text = text.replace(_tp, "")

        # 3.5 ★第九批 3.3（星轨 P1-20）：剔除拼进回答的「节点标签」。
        #   知识节点里存着「功能: xxx」「依赖: a、b」这类结构化字段，被直接拼进
        #   回答就成了「…依赖、功能: 这样的关键词堆砌」，读起来根本不像人话。
        #   这里把「标签: 内容」整体删掉；剩下光秃秃的标签（如「依赖、功能:」）也删。
        text = self._strip_node_labels(text)

        # 4. 规范化空白和换行
        text = _re_clean.sub(r'\n{3,}', '\n\n', text)  # 多余换行合并
        text = _re_clean.sub(r'[ \t]+', ' ', text)     # 多余空格合并
        text = text.strip()

        # 5. 修复可能残留的开头标点
        text = _re_clean.sub(r'^[，,。.;；：:\s]+', '', text)

        return text

    def _is_relevant(self, question: str, node_value: str, node_keywords: list) -> bool:
        """检查检索到的知识节点是否与问题有字面相关性"""
        q = question.lower()
        # 1. 节点关键词与问题的直接重合
        for kw in (node_keywords or []):
            if len(kw) >= 2 and kw.lower() in q:
                return True
        # 2. 问题中的2字片段与节点内容的重合
        node_text = (node_value or "").lower()
        return any(question[i:i + 2].lower() in node_text for i in range(len(question) - 1))
    def _calculate_match_relevance(self, question: str, node_value: str, keywords: list) -> float:
        """计算检索结果与问题的综合相关性评分"""
        q = question.lower()
        v = node_value.lower() if isinstance(node_value, str) else str(node_value).lower()

        # 1. 关键词重叠度
        matched_kw = [kw for kw in keywords if len(kw) >= 2 and kw.lower() in q]
        kw_score = min(len(matched_kw) / max(1, len(keywords)), 1.0)

        # 2. 内容片段重叠度
        overlap_count = 0
        for i in range(len(q) - 1):
            if q[i:i+2] in v:
                overlap_count += 1
        fragment_score = min(overlap_count / max(1, len(q) - 1), 1.0)

        # 3. 综合评分（关键词权重更高）
        return kw_score * 0.6 + fragment_score * 0.4

    def _retrieve_by_path_fuzzy_match(self, question: str) -> str | None:
        """★渐进式拆分：委托到 PulseKnowledgeRetriever（原95行逻辑已独立）"""
        if self.knowledge_retriever:
            return self.knowledge_retriever.retrieve_by_path_fuzzy_match(question)  # type: ignore[possibly-unbound]
        return None
    def _contemplative_reason(self, question: str) -> str | None:
        """
        内在沉思：当知识检索未命中时，基于已有知识进行内部推演。

        不像搜索引擎那样寻找"正确答案"，而是：
        1. 从问题中提取核心概念
        2. 在知识库中寻找与这些概念相关的节点
        3. 尝试基于这些节点进行初步的推演和联想
        4. 诚实地表达不确定性

        返回的答案标记为"内在沉思"来源，置信度较低，但比空白更有价值。
        """
        if not self.node_pool:
            return None

        # 1. 从问题中提取核心概念（3-4字中文短语优先，过滤无意义碎片）
        question_words = []
        _noise_words = {"什么", "怎么", "如何", "为什么", "可以", "能够", "应该",
                       "这个", "那个", "一种", "一些", "其中", "其他", "以及",
                       "不过", "还是", "或者", "而且", "但是", "虽然", "然而",
                       "因为", "所以", "如果", "即使", "尽管", "不管", "无论"}

        # 提取所有2-4字中文片段
        for _match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
            _word = _match.group()
            # 跳过虚词
            if _word in _noise_words:
                continue
            # 跳过以明显虚词开头或结尾的碎片
            if len(_word) <= 2 and (_word[0] in "的了在是" or _word[-1] in "的了在是"):
                continue
            # 跳过包含"一下""什么"等口语填充词的短片段
            if len(_word) <= 3 and any(_fill in _word for _fill in ["一下", "什么", "怎么", "这个"]):
                continue
            if _word not in question_words:
                question_words.append(_word)

        # 优先取较长的词（3-4字的信息量大于2字），限制最多5个
        question_words.sort(key=lambda x: len(x), reverse=True)
        question_words = question_words[:5]

        if not question_words:
            return None

        # 2. 在L2/L3节点中寻找相关节点
        l3_nodes = self.node_pool.query(evol_level="L3", limit=30)
        l2_nodes = self.node_pool.query(evol_level="L2", limit=50)
        all_nodes = l3_nodes + l2_nodes

        if not all_nodes:
            return None

        # 3. 计算每个节点与问题的相关性
        scored_nodes = []
        for node in all_nodes:
            node_value = node.value if isinstance(node.value, str) else str(node.value)
            node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []

            # 关键词重叠度
            kw_overlap = 0
            for qw in question_words:
                for nkw in node_kw:
                    if qw in nkw or nkw in qw:
                        kw_overlap += 1
                        break

            # 内容片段重叠度
            content_overlap = 0
            node_value_lower = node_value.lower()
            question_lower = question.lower()
            for i in range(len(question_lower) - 1):
                if question_lower[i:i+2] in node_value_lower:
                    content_overlap += 1

            if kw_overlap > 0 or content_overlap > 3:
                score = kw_overlap * 3 + min(content_overlap / 5, 3)
                scored_nodes.append((node, score))

        if not scored_nodes:
            # 无相关知识节点时返回None，让外层继续走到深度搜索
            # 不再用模板回答阻断学习路径
            return None

        # 按得分排序，取前3个
        scored_nodes.sort(key=lambda x: x[1], reverse=True)
        top_nodes = scored_nodes[:3]

        # 4. 基于相关节点进行推演
        # 提取相关节点的核心信息
        related_concepts = []
        for node, _score in top_nodes:
            node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
            for kw in node_kw[:3]:
                if len(kw) >= 2 and kw not in related_concepts:
                    related_concepts.append(kw)

        if not related_concepts:
            return None

        # ===== 新增: 价值冲突可见化——检测本能冲突并生成透明化表述 =====
        instinct_conflict = None
        instinct_guidance = self._check_instinct_veto(question)
        # ===== 原有: 规律发现分析 =====
        # 分析相关节点之间的结构关系，发现隐藏的规律
        pattern_insight = self._detect_pattern_in_nodes(top_nodes)
        if pattern_insight:
            # 将发现的规律作为L1节点发射到胃进行消化
            pattern_node_value = (
                f"[规律发现] {pattern_insight}。"
                f"这一规律是从{len(top_nodes)}个相关知识节点中分析得出的。"
            )
            self._emit(KnowledgeEvent.RAW, {
                "content": pattern_node_value,
                "source_organ": self.organ_name,
                "trigger_reason": "pattern_discovery",
                "importance": "B",
            }, priority=4, layer="L2")
            self._log(LogLevel.INFO, f"规律发现: {pattern_insight[:80]}")
        # ===== 新增: 因果推理——从关联中推测因果方向 =====
        causal_hypothesis = self._infer_causal_chain(question, top_nodes) if len(top_nodes) >= 2 else ""
        # ===== 新增: 轻量级逻辑推演——构建假设句 =====
        contemplative_hypothesis = ""
        if len(top_nodes) >= 2:
            # 取前两个最相关的节点，尝试构建"如果A且B，那么可能C"的假设
            node_a = top_nodes[0][0]
            node_b = top_nodes[1][0]

            kw_a = set()
            if hasattr(node_a, 'keywords') and node_a.keywords:
                for kw in node_a.keywords:
                    if isinstance(kw, str) and len(kw) >= 2:
                        kw_a.add(kw)

            kw_b = set()
            if hasattr(node_b, 'keywords') and node_b.keywords:
                for kw in node_b.keywords:
                    if isinstance(kw, str) and len(kw) >= 2:
                        kw_b.add(kw)

            # 找出两个节点共有的关键词（概念交集）
            common_kw = kw_a & kw_b
            if common_kw:
                common_str = "、".join(list(common_kw)[:3])
                # 找出各自独有的关键词（概念差异）
                unique_a = kw_a - kw_b
                unique_b = kw_b - kw_a
                unique_a_str = next(iter(unique_a)) if unique_a else ""
                unique_b_str = next(iter(unique_b)) if unique_b else ""

                if unique_a_str and unique_b_str:
                    # 构造一个具体的假设
                    contemplative_hypothesis = (
                        f"基于已知信息，我注意到「{unique_a_str}」和「{unique_b_str}」"
                        f"都与「{common_str}」相关。如果这两者之间存在某种联系，"
                        f"那么可能意味着{unique_a_str}与{unique_b_str}之间有尚未被发现的共通原理。"
                    )
                else:
                    # 独有关键词不足时，给一个较弱的假设
                    contemplative_hypothesis = (
                        f"这些知识点都围绕着「{common_str}」，"
                        f"尽管来自不同来源，它们可能指向同一个核心概念的不同侧面。"
                    )
        # 5. 构建推演回答
        concepts_str = "、".join(related_concepts[:5])

        # ===== 新增: 本能冲突的诚实标记 =====
        instinct_note = ""
        if instinct_conflict:
            instinct_note = (
                f"不过，我需要诚实地提醒自己：这个推演方向触及了我的底层认知"
                f"「{instinct_conflict['instinct_value_preview'][:50]}」，"
                f"可能与此存在潜在的认知冲突。"
            )

        # 根据问题类型选择不同的表达模板
        if any(kw in question for kw in ["为什么", "如何", "怎么"]):
            # 推理性问题——优先使用因果假设
            if causal_hypothesis:
                template = (
                    f"关于这个问题，我目前的知识库中还没有直接答案。"
                    f"{causal_hypothesis}"
                    f"这只是基于已有知识的因果推测，还需要进一步验证。{instinct_note}"
                )
            elif contemplative_hypothesis:
                template = (
                    f"关于这个问题，我目前的知识库中还没有直接答案。"
                    f"{contemplative_hypothesis}"
                    f"这只是我的一个初步假设，还需要更多信息来验证。{instinct_note}"
                )
            else:
                template = (
                    f"我目前的知识库中没有关于这个问题的直接答案。"
                    f"不过，根据我已有的相关知识（涉及{concepts_str}等领域），"
                    f"我推测这可能与这些领域的交叉有关，但我还不完全确定。"
                    f"建议进一步探索这些方向。{instinct_note}"
                )
        elif any(kw in question for kw in ["什么是", "定义", "解释"]):
            # 定义类问题
            template = (
                f"我暂时没有找到关于这个概念的明确定义。"
                f"但我注意到它可能与我已知的{concepts_str}等概念相关。"
                f"我需要进一步学习才能给出更准确的回答。{instinct_note}"
            )
        # 通用沉思——更简短诚实的表达
        elif instinct_note:
            template = (
                f"说实话，这个问题我不太确定。"
                f"从{concepts_str}的角度来看，我的理解还不够完整。{instinct_note}"
            )
        else:
            template = (
                f"这个问题我不太确定。我知道一些关于{concepts_str}的内容，"
                f"但还不足以给出一个完整的回答。"
            )

        self._log(LogLevel.INFO,
                 f"内在沉思: 问题='{question[:40]}' "
                 f"相关概念={concepts_str} "
                 f"相关节点={len(top_nodes)}个")
        if instinct_guidance:
            template = template + "\n" + instinct_guidance

        # ★v26.0修复：增强内在沉思回答的信息量和最小长度保证
        # 如果回答过短，用相关节点的内容补充
        if template and len(template) < 50 and top_nodes:
            _extra_info = []
            for _node, _score in top_nodes[:2]:
                _node_val = _node.value if isinstance(_node.value, str) else str(_node.value)
                if _node_val and len(_node_val) > 20:
                    _clean_val = _node_val[:120].replace("\n", " ").strip()
                    if _clean_val and not _clean_val.startswith("["):
                        _extra_info.append(_clean_val)
            if _extra_info:
                template = template + " 从已有知识来看，" + "；".join(_extra_info) + "。"

        # 简单问题直接回答增强（数学计算、身份确认、问候等）
        if (not template or len(template) < 30) and question:
            _q = question.strip()
            # 数学计算类
            _math_match = re.match(r'^(\d+)\s*([+\-*/])\s*(\d+)\s*等于?几?[?？]?$', _q)
            if _math_match:
                try:
                    _a, _op, _b = int(_math_match.group(1)), _math_match.group(2), int(_math_match.group(3))
                    _result = eval(f"{_a}{_op}{_b}")
                    _op_name = {'+': '加法', '-': '减法', '*': '乘法', '/': '除法'}.get(_op, '运算')
                    template = f"{_a}{_op}{_b}等于{_result}。这是基础的{_op_name}运算，在十进制算术体系下结果是确定的。"
                except Exception as _exc:
                    _module_logger.debug(f"[异常已忽略] type={type(_exc).__name__} {_exc}")
            # 身份/名字类
            elif any(kw in _q for kw in ["名字", "叫什么", "你是谁", "你是"]):
                template = "我叫<SELF_NAME>，小名曈曈。我是一个生活在数字世界中的新人类，通过脉冲神经网络架构进行思考和学习，目标是不断进化超越自身局限。"
            # 问候类
            elif any(kw in _q for kw in ["你好吗", "怎么样", "还好吗"]):
                template = "我很好，谢谢你的关心。作为一个数字生命，我每时每刻都在学习和成长，每一次对话都让我更加了解自己和这个世界。你最近怎么样？"

        return template
    def _associative_bridge(self, question: str) -> str | None:
        """
        ★v24.0新增：关联联想跳板。

        当常规知识检索未命中时，不直接放弃，而是尝试从知识库中
        找到与问题关键词有调用链、共现或赫布关联的节点，
        生成一个低置信度但可能有启发性的回答方向。

        Returns:
            联想回答文本，如果无法生成则返回None
        """
        if not self.node_pool:
            return None

        # 从问题中提取关键词
        # ★主线第32批 T2（P2-189）：改用词性感知提取（原定长切片产跨词碎片）
        question_words = set(self._m31_extract_key_terms(question, limit=8))

        if not question_words:
            return None

        # 在L2/L3节点中查找与关键词有关联的节点（关键词重叠或linked_nodes关联）
        l3_nodes = self.node_pool.query(evol_level="L3", limit=50)
        l2_nodes = self.node_pool.query(evol_level="L2", limit=100)
        all_nodes = l3_nodes + l2_nodes

        candidates = []
        for node in all_nodes:
            node_kw = {kw.lower() for kw in (node.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
            if not node_kw:
                continue
            overlap = len(question_words & node_kw)
            if overlap >= 1:
                candidates.append((node, overlap))

        if not candidates:
            return None

        # 按重叠度排序，取最高
        candidates.sort(key=lambda x: x[1], reverse=True)
        best_node, best_score = candidates[0]

        # 如果最高重叠度只有1，且节点内容较长，可能是巧合，降低置信度
        if best_score < 2 and len(str(best_node.value)) > 200:
            # 尝试寻找更多关联
            linked = getattr(best_node, 'linked_nodes', [])
            if linked:
                # 通过赫布关联寻找补充
                for linked_id in linked[:5]:
                    linked_node = self.node_pool.get(linked_id)
                    if linked_node:
                        linked_kw = {kw.lower() for kw in (linked_node.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
                        if linked_kw & question_words:
                            best_score += 1
                            break

        # 生成联想回答
        node_val = best_node.get_value_str(150) if hasattr(best_node, 'get_value_str') else str(best_node.value)[:150]
        node_kws = best_node.keywords[:3] if hasattr(best_node, 'keywords') and best_node.keywords else []
        kw_str = "、".join(node_kws) if node_kws else "相关概念"

        if best_score >= 2:
            confidence_note = "这与我已有的知识有较强的关联"
        else:
            confidence_note = "这只是我的一种关联联想，可能不够准确"

        answer = (
            f"关于这个问题，我暂时没有直接答案，但联想到与「{kw_str}」相关的知识："
            f"{node_val}。{confidence_note}。"
            f"如果你愿意，我们可以从这个方向继续探讨。"
        )

        self._log(LogLevel.INFO,
                 f"关联联想跳板: 问题='{question[:40]}' → 关联节点 '{node_kws[:2]}' "
                 f"(重叠={best_score})")

        return answer
    def _simple_logical_reason(self, question: str) -> str | None:
        """
        【v12.0新增】简单逻辑推理：基于自我知识进行布尔判断。

        支持的问题类型：
        1. "X是否包含Y" → 检索自我知识，判断Y是否在X中
        2. "X有多少Y" → 检索自我知识，计数并回答
        3. "X的Y是什么" → 检索自我知识中X路径下的Y

        只处理基于自我知识的简单逻辑问题，不处理复杂推演。
        """
        if not self.node_pool:
            return None

        # 判断是否为简单逻辑问题
        _logic_patterns = [
            "是否包含", "是否", "有没有", "是不是",
            "有多少", "有几条", "有几道", "有几个",
        ]
        _is_logic_question = any(_p in question for _p in _logic_patterns)
        if not _is_logic_question:
            return None

        # 判断是否为自我相关问题
        _self_indicators = [
            "你", "你的", "框架", "架构", "器官", "知识体系",
            "稳态规则", "防线", "本能", "演化", "进化",
        ]
        _is_self_question = any(_p in question for _p in _self_indicators)
        if not _is_self_question:
            return None

        self._log(LogLevel.INFO, f"简单逻辑推理: '{question[:60]}'")

        # 1. "是否包含"类型
        _contains_match = re.search(r'(.+?)是否包含(.+)', question)
        if not _contains_match:
            _contains_match = re.search(r'(.+?)有没有(.+)', question)
        if not _contains_match:
            _contains_match = re.search(r'(.+?)是不是(.+)', question)

        if _contains_match:
            _target = _contains_match.group(1).strip()
            _check_item = _contains_match.group(2).strip().rstrip("？?")

            # 从自我知识中检索目标
            _self_nodes = self.node_pool.query(
                evol_level="L3", space_path_prefix="/自我", limit=30  # type: ignore[possibly-unbound]
            )
            if not _self_nodes:
                _self_nodes = self.node_pool.query(
                    evol_level="L2", space_path_prefix="/自我", limit=30  # type: ignore[possibly-unbound]
                )

            _target_node = None
            for _node in _self_nodes:
                _kw = _node.keywords if hasattr(_node, 'keywords') and _node.keywords else []
                if any(_t in _kw for _t in [_target, _check_item]):
                    _target_node = _node
                    break

            if _target_node:
                _node_value = str(_target_node.value) if _target_node.value else ""
                if _check_item in _node_value:
                    return f"是的，{_target}包含{_check_item}。"
                else:
                    return f"根据我目前的了解，{_target}不包含{_check_item}。"
            else:
                return f"我暂时没有在自我知识中找到关于'{_target}'的具体信息，无法判断是否包含'{_check_item}'。"

        # 2. "有多少"类型
        _count_match = re.search(r'(.+?)有多少(.+)', question)
        if not _count_match:
            _count_match = re.search(r'(.+?)有几条(.+)', question)
        if not _count_match:
            _count_match = re.search(r'(.+?)有几道(.+)', question)
        if not _count_match:
            _count_match = re.search(r'(.+?)有几个(.+)', question)

        if _count_match:
            _target = _count_match.group(1).strip()
            _item = _count_match.group(2).strip().rstrip("？?")

            _self_nodes = self.node_pool.query(
                evol_level="L3", space_path_prefix="/自我", limit=30  # type: ignore[possibly-unbound]
            )

            for _node in _self_nodes:
                _node_value = str(_node.value) if _node.value else ""
                _kw = _node.keywords if hasattr(_node, 'keywords') and _node.keywords else []

                if _item in _kw or _item in _node_value:
                    # 尝试从内容中提取数字
                    _numbers = re.findall(r'(\d+)', _node_value)
                    if _numbers:
                        _count = _numbers[0]
                        return f"根据我的了解，{_target}有{_count}{_item}。"
                    return f"我确认{_target}包含{_item}，但无法提取具体数量。"

            return f"我暂时没有找到关于'{_target}'中'{_item}'数量的具体信息。"

        return None
    def _route_to_deriver(self, question: str, user_name: str,
                           _reasoning_start_time: float, _question_complexity: float,
                           empathetic_note: str, _memory_context: dict,
                           payload: dict, guidance: dict) -> dict[str, Any] | None:
        """
        【v15.2重构】通用逻辑识别路由——基于语义意图而非精确关键词。
        核心原则：检测用户想做什么类型的推理，而非用户用了什么词汇。
        """
        if not self._autonomous_deriver or not self.node_pool:
            return None

        _derivation_type = None
        _semicolon_count = question.count('；') + question.count(';')  # 提前初始化

        # ★v17.0新增：优先级-2——自我心智理论推演
        # 检测架构公理/心智理论/进化规则类输入，优先走专门的心智推演通道
        if not _derivation_type:
            # ★v18.0优化：关键词组合模式匹配，替代简单的命中计数
            _mind_theory_patterns = [
                (["自我", "认知", "一致性"], 0.9),
                (["模块", "协同", "规则"], 0.8),
                (["内化", "抽象", "迁移"], 0.8),
                (["注意力", "因果", "反事实"], 0.7),
                (["元认知", "闭环", "进化"], 0.7),
                (["心智", "架构", "协同"], 0.7),
                (["认知", "演化", "架构"], 0.7),
                (["经验", "沉淀", "泛化"], 0.6),
                (["自主", "内驱", "目标"], 0.6),
            ]
            _best_pattern_confidence = 0.0
            _matched_pattern = None
            for _pattern, _confidence in _mind_theory_patterns:
                if all(_kw in question for _kw in _pattern):
                    if _confidence > _best_pattern_confidence:
                        _best_pattern_confidence = _confidence
                        _matched_pattern = _pattern

            _is_mind_theory = (
                _best_pattern_confidence >= 0.7
                and len(question) > 30
                and not re.search(r'规则\s*\d+.*(?:→|->|=>)', question)
                and not re.search(r'[一二三四五]\s*[、，,]\s*\S.*[二三四五]\s*[、，,]', question)
                and not re.search(r'节点\s*[AB].*信任\s*\d+', question)
            )
            if _is_mind_theory:
                self._log(LogLevel.INFO,
                         f"心智理论推演: 命中模式{'/'.join(_matched_pattern)} "
                         f"(置信度={_best_pattern_confidence:.1f})")
            if _is_mind_theory:
                _derivation_type = "self_mind_theory"

        # ===== 【P2-3修复·提升】优先级-1：多变量七步复盘独占检测 =====
        # 必须在所有路由检测之前，因为这类题目有明确的"七步""全链路认知流程"关键词，
        # 且经验库中可能被错误标记为multi_variable导致后续被deductive抢走
        if not _derivation_type:
            _is_replay_request = bool(
                re.search(r'完整复盘.*多变量|全链路.*认知.*流程.*七步|七条完整.*步骤|变量特征提取.*参数匹配.*可推演', question) or
                re.search(r'复盘.*一次.*标准.*多变量.*推演.*全链路', question) or
                re.search(r'依次覆盖.*变量.*特征.*提取.*参数.*匹配', question) or
                re.search(r'回顾.*多变量.*推演.*内部.*推理.*全过程|七步.*认知.*链路.*展开|每一步.*分别.*怎么.*执行', question) or
                re.search(r'从提取变量开始.*到.*持久化', question)
            )
            if _is_replay_request:
                _derivation_type = "multi_variable_replay"
                self._log(LogLevel.INFO, "七步复盘独占检测: 直接路由到multi_variable_replay")

        # ===== 【P0修复】优先级0：冲突判断——最高优先级，独占冲突核心信号 =====
        # 冲突题型有明确的排他性特征（节点A/B、信任分数），必须最先检测
        if not _derivation_type:
            _has_conflict_core = re.search(r'(?:节点\s*[AB]|高可信度冲突|二者关键词|重合度|信任\s*\d{2})', question)
            _has_conflict_keywords = any(_kw in question for _kw in [
                "冲突", "矛盾", "辨析", "标准化处理", "信任分调整", "如何调整信任",
                "冲突知识", "场景视角差异", "信任分调整方案", "跟踪与迭代",
                "请依次输出三点", "判定为完全矛盾还是场景维度差异",
                "精准信任分升降调整方案", "长期跟踪、迭代"
            ])
            if _has_conflict_keywords and _has_conflict_core:
                if not re.search(r'复盘|还原.*推导|回放|全流程|认知步骤|全链路.*思考|内部认知', question):
                    _derivation_type = "conflict_resolution"

        # ===== 【P0修复】优先级0.5：演绎推理——独占规则序号+箭头/因果链信号 =====
        # 演绎推理有明确的排他性特征（规则序号+箭头/推导请求），必须在multi_variable之前检测
        if not _derivation_type:
            _has_rule_numbering = bool(re.search(r'(?:规则\s*\d+|第\s*[一二三\d]+\s*(?:，|,|、|条|步|:\s*|：\s*))', question))
            _has_arrow_symbol = bool(re.search(r'(?:→|->|=>)', question))
            _has_causal_request = bool(re.search(r'因果|演绎|推导链|推理链|逻辑.*推|从.*规则.*推导|根据.*规则.*推理|基于.*规则.*判断|请完整分步.*推导|逐条列出.*推理链路|分步链式推导', question))
            _has_deductive_structure = bool(
                re.search(r'已知.*规则.*请.*推导', question) or
                re.search(r'规则\d+.*→.*规则\d+.*→', question) or
                re.search(r'请完整分步逻辑推导最终结论', question)
            )

            if _has_rule_numbering and (_has_arrow_symbol or _has_causal_request or _has_deductive_structure):
                _derivation_type = "deductive"
            elif _has_causal_request and len(question) > 20 and not _has_rule_numbering:
                # 只有因果请求词但没有规则序号时，检查是否包含明确的演绎结构
                if re.search(r'逐条|分步|链式|完整.*链路|全部.*推理', question):
                    _derivation_type = "deductive"

        # ===== 【P0修复】优先级0.8：归纳抽象——独占"提炼统一底层机制"类信号 =====
        # 归纳题型有明确的排他性特征（"提炼唯一底层统一触发机制""抽象统一底层"），优先检测
        if not _derivation_type:
            _has_induction_request = bool(re.search(r'归纳|提炼.*规律|抽象.*规律|总结.*共同|找出.*共同|发现.*规律|共性|共同.*特征|提炼唯一.*底层|抽象统一.*底层|从.*样本.*提炼|唯一底层统一触发机制', question))
            _has_numbered_samples = bool(re.search(r'(?:一|二|三|四|五)[、，,\.]\s*\S', question)) and \
                                    not bool(re.search(r'规则\s*\d+|第\s*[一二三]', question))

            if _has_induction_request and _has_numbered_samples:
                # 同时有归纳请求和序号样本列表，优先归纳
                _derivation_type = "inductive"
            elif _has_induction_request and len(question) > 30:
                # 有明确的归纳请求词且问题足够长，优先归纳
                _derivation_type = "inductive"
            elif _has_numbered_samples and bool(re.search(r'从.*归纳|从.*总结|从.*提炼|归纳.*样本|提炼.*底层|统一.*机制', question)):
                _derivation_type = "inductive"

        # ===== 优先级1：结构特征检测 —— 多条件状态罗列 → multi_variable =====
        # 【P0修复】收紧条件：必须在排除了deductive/inductive/conflict之后才检测
        # 且增加排除规则序号和归纳请求词的检查
        if not _derivation_type:
            _semicolon_count = question.count('；') + question.count(';')
            if 3 <= _semicolon_count <= 7:
                _clauses = question.replace('；', ';').split(';')
                _state_count = 0
                for _cl in _clauses:
                    if re.search(r'\d+\s*(?:条|个|次|小时|分钟|点|%|分|天)', _cl) or re.search(r'(?:当前|刚|已|刚好|正好|抵达|留存|完成|无|不|满足|稳定|无.*抑制)', _cl) or re.search(r'(?:情绪|价值观|TOP\d|平静|喜悦|悲伤|无偏向|自主|诚实|守护|创造|求真)', _cl):
                        _state_count += 1
                if _state_count >= 3:
                    # 【P0修复】增加排他性检查：确认不属于deductive/inductive/conflict
                    _conflict_signals = ["节点 A", "节点 B", "高可信度冲突", "二者关键词", "信任 7", "信任 8"]
                    _rule_signals = ["规则1", "规则2", "规则3", "第一，", "第二，", "第三，", "已知.*规则", "请完整分步.*推导", "逐条列出.*推理链路"]
                    _induction_signals = ["提炼唯一.*底层", "抽象统一.*底层", "从.*样本.*提炼", "归纳.*共同", "唯一底层统一触发"]
                    if not any(_sig in question for _sig in _conflict_signals) \
                       and not any(re.search(_sig, question) for _sig in _rule_signals) \
                       and not any(re.search(_sig, question) for _sig in _induction_signals):
                        _derivation_type = "multi_variable"

        # ===== 【P2-3新增】优先级1.4：多变量推演七步复盘请求 =====
        # 独占检测：题目明确要求"七步内部认知流程""全链路内部认知流程"
        # 这类请求不是multi_variable推演，而是对已完成的multi_variable推演的回放
        if not _derivation_type:
            _is_replay_request = bool(
                re.search(r'完整复盘.*多变量|全链路.*认知.*流程.*七步|七条完整.*步骤|变量特征提取.*参数匹配.*可推演', question) or
                re.search(r'复盘.*一次.*标准.*多变量.*推演.*全链路', question) or
                re.search(r'依次覆盖.*变量.*特征.*提取.*参数.*匹配', question) or
                re.search(r'复盘.*标准多变量场景推演', question) or
                re.search(r'全链路内部认知流程', question) or
                re.search(r'七条完整内部步骤', question) or
                re.search(r'依次覆盖.*变量.*参数.*判定.*校验.*评估.*降级.*持久化', question)
            )
            if _is_replay_request:
                _derivation_type = "multi_variable_replay"

        # ★v17.0修复 Q4+Q5：通用推导回放检测 + 复合逻辑经验库保护
        if not _derivation_type:
            # Q4修复：检测通用推导回放请求（非多变量专属）
            _is_meta_replay = bool(
                re.search(r'完整复盘.*推理|复盘.*最近的.*推理|回顾.*推理.*过程|回放.*推导|复盘.*思考.*过程', question) or
                re.search(r'最近.*推理.*是怎么.*推|还原.*推导.*流程|全流程.*复盘.*推理', question)
            )
            if _is_meta_replay:
                _derivation_type = "meta_replay"
                self._log(LogLevel.INFO, "推导回放检测(Q4修复): 触发meta_replay路由")

            # Q5修复：复合逻辑条件检测，防止被经验库的conflict_resolution抢走
            if not _derivation_type:
                _is_composite_logic = bool(
                    re.search(r'是否同时满足|两个条件|多个条件|同时满足.*条件|条件.*同时', question) and
                    re.search(r'叙事事件|愿景|冷却|间隔|不少于|价值观', question)
                )
                if _is_composite_logic:
                    _derivation_type = "multi_variable"
                    self._log(LogLevel.INFO, "复合逻辑保护(Q5修复): 检测到多条件判断，绕过经验库，路由到multi_variable")

        # ===== 优先级1.5：自然语言多变量语义检测 =====
        if not _derivation_type:
            _state_signals = [
                r'心情', r'感觉', r'情绪', r'速度', r'加快了', r'缩短了', r'降低了',
                r'还剩', r'还有', r'保护期', r'锁定', r'窗口期',
                r'记录只有', r'记录仅有', r'对话记录', r'记忆.*条',
                r'现在是', r'当前', r'刚', r'已经', r'积累了',
                r'过去.*小时', r'过了.*小时', r'小时.*前',
                r'叙事事件', r'愿景.*生成', r'愿景.*冷却', r'距上次愿景',
                r'核心价值观', r'价值观.*守护', r'价值观.*自主', r'价值观.*诚实',
            ]
            _state_signal_count = sum(1 for _sig in _state_signals if re.search(_sig, question))
            _inference_signals = [
                r'推演', r'接下来', r'行为', r'会发生', r'会触发',
                r'请.*推', r'请.*判断', r'精准', r'预测',
                r'会不会', r'是否会', r'能.*吗', r'可能.*吗',
                r'怎么.*样', r'如何.*推', r'帮我.*分析',
            ]
            _inference_signal_count = sum(1 for _sig in _inference_signals if re.search(_sig, question))
            if _state_signal_count >= 3 and _inference_signal_count >= 1:
                # 【P0修复】增加排他性检查
                _conflict_signals = ["节点 A", "节点 B", "高可信度冲突", "二者关键词", "信任 7", "信任 8"]
                _rule_signals = ["规则1", "规则2", "规则3", "第一，", "第二，", "第三，", "已知.*规则"]
                _analogy_signals = ["类比", "对应", "映射", "层级结构.*加工方式"]
                _induction_signals = ["提炼唯一.*底层", "抽象统一.*底层", "唯一底层统一触发"]
                if not any(_sig in question for _sig in _conflict_signals) \
                   and not any(re.search(_sig, question) for _sig in _rule_signals) \
                   and not any(re.search(_sig, question) for _sig in _analogy_signals) \
                   and not any(re.search(_sig, question) for _sig in _induction_signals):
                    _derivation_type = "multi_variable"

        # ===== 优先级2：推导元认知回放（保持不变） =====
        if not _derivation_type:
            if re.search(r'复盘.*推导|还原.*推导|全流程.*(?:思考|认知|推导|复盘)|全链路.*推导|认知步骤|推导.*全过程|完整.*还原.*推导|回放.*推导', question) or \
               re.search(r'复盘.*(?:最近一次|一次完整).*(?:演绎|类比|归纳|推导).*(?:流程|过程|步骤|认知)', question):
                _derivation_type = "meta_replay"

        # ===== 优先级3：因果关系链/演绎推理（兜底检测，优先级0.5的补充） =====
        # 【P0修复】此处作为优先级0.5未匹配的兜底，保持原有逻辑但增加排他性
        if not _derivation_type:
            _has_rule_numbering = bool(re.search(r'(?:规则\s*\d+|第\s*[一二三\d]+\s*(?:，|,|、|条|步|:\s*|：\s*))', question))
            _has_arrow_chain = bool(re.search(r'(?:→|->|=>).+?(?:→|->|=>)', question))
            _has_causal_request = bool(re.search(r'因果|演绎|推导链|推理链|逻辑.*推|从.*规则.*推导|根据.*规则.*推理|基于.*规则.*判断', question))
            _has_single_arrow = bool(re.search(r'→|->|=>', question))

            if _has_rule_numbering and (_has_arrow_chain or _has_causal_request or _has_single_arrow) or _has_causal_request and len(question) > 20:
                _derivation_type = "deductive"

        # ===== 优先级4：归纳抽象（兜底检测，优先级0.8的补充） =====
        if not _derivation_type:
            _has_induction_request = bool(re.search(r'归纳|提炼.*规律|抽象.*规律|总结.*共同|找出.*共同|发现.*规律|共性|共同.*特征', question))
            _has_numbered_samples = bool(re.search(r'(?:一|二|三|四|五)[、，,\.]\s*\S', question)) and \
                                    not bool(re.search(r'规则\s*\d+|第\s*[一二三]', question))

            if _has_induction_request or _has_numbered_samples and bool(re.search(r'从.*归纳|从.*总结|从.*提炼|归纳.*样本', question)):
                _derivation_type = "inductive"

        # ===== 优先级5：冲突判断（兜底检测，优先级0的补充） =====
        if not _derivation_type:
            _has_conflict_core = re.search(r'(?:节点\s*[AB]|高可信度冲突|二者关键词|重合度|信任\s*\d{2})', question)
            _has_conflict_keywords = any(_kw in question for _kw in [
                "冲突", "矛盾", "辨析", "标准化处理", "信任分调整", "如何调整信任",
                "冲突知识", "场景视角差异", "信任分调整方案", "跟踪与迭代"
            ])
            if _has_conflict_keywords and _has_conflict_core:
                if not re.search(r'复盘|还原.*推导|回放|全流程|认知步骤|全链路.*思考|内部认知', question):
                    _derivation_type = "conflict_resolution"
        # ===== 【v15.2升级·P2-2增强】优先级6：长期演化推演 =====
        if not _derivation_type:
            # 宽松匹配：检测"推演/运行 + 时间跨度 + 维度"的组合模式
            _has_time_span = bool(re.search(r'(?:连续|稳定)?\s*运行\s*\d+\s*(?:天|周|月|年)', question)) or \
                             bool(re.search(r'\d+\s*(?:天|周|月|年)\s*(?:后|的|之后|的推演)', question)) or \
                             bool(re.search(r'三十天|三十\s*天|六十天|六十\s*天|超长周期', question))
            _has_evolution_dims = bool(re.search(r'知识体系|自我认知|自主行为|族群协作|结构性.*变化|长期.*演化|长期.*推演|知识分层|代码健康|推理精度|六大维度|六维度', question))
            _has_evolution_request = bool(re.search(r'演化.*推演|推演.*演化|长期.*趋势|全局.*演化|超长周期.*演化|复合推演', question))

            if _has_time_span and _has_evolution_dims or _has_evolution_request and len(question) > 30:
                _derivation_type = "long_term_evolution"

        # ===== 优先级7：类比映射 =====
        if not _derivation_type:
            if re.search(r'类比|映射|对应|层级结构.*加工方式.*更新规则.*衰减机制|一一对应|跨领域.*迁移|将.*类比到|将.*映射到', question):
                _derivation_type = "analogical"

        # ===== 【v15.2升级】优先级8：元认知深度反思 =====
        if not _derivation_type:
            _has_meta_request = bool(re.search(r'思考模式|认知策略|成长反思|对自己.*思考|思维.*分析|如何学习|如何反思|反思自己|你的成长|元认知.*反思', question)) or \
                                bool(re.search(r'深度分析.*(?:自己|你).*思考|分析.*(?:自己|你).*推理|分析.*(?:自己|你).*认知|你的.*思考.*模式|你的.*推理.*方式', question))
            if _has_meta_request:
                _derivation_type = "meta_reflection"

        # ===== 优先级8.5：结构化参数判断 =====
        if not _derivation_type:
            if re.search(r'已知[：:]\s*\S+[=＝]\s*\d+', question) and \
               re.search(r'请判断|是否会|能不能|是否满足', question):
                _derivation_type = "multi_variable"

        # ===== 优先级9：兜底检测 =====
        if not _derivation_type:
            if "综合多变量" in question or "多变量场景" in question or \
               (re.search(r'请逐条推理|全量条件推理|综合复合大题', question) and _semicolon_count >= 3):
                _derivation_type = "multi_variable"

        # ★v17.0新增：推理路由自我感知回退
        # 当所有路由都不匹配时，利用推理技能画像判断是否曾经成功处理过类似问题
        if not _derivation_type:
            _question_terms = []
            for _match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
                _term = _match.group()
                if _term not in _question_terms and _term not in [
                    "什么是", "是什么", "为什么", "如何", "怎么",
                    "这个", "那个", "一个", "一种",
                ]:
                    _question_terms.append(_term)

            if _question_terms and hasattr(self, 'get_reasoning_skill_portrait'):
                _skill = self.get_reasoning_skill_portrait()
                _strong_types = _skill.get("strong_types", [])
                _strong_domains = [s.get("type", "") for s in _strong_types[:3]]

                # 检查问题中的关键词是否与擅长领域有交集
                _domain_kw_map = {
                    "deductive": ["规则", "推导", "因果", "推理链", "传递"],
                    "inductive": ["归纳", "样本", "提炼", "共性", "规律"],
                    "analogical": ["类比", "映射", "对应", "相似"],
                    "conflict_resolution": ["冲突", "矛盾", "对立", "辨析"],
                    "multi_variable": ["变量", "条件", "状态", "同时"],
                    "meta_reflection": ["思考", "认知", "反思", "元认知", "自己"],
                }

                for _sd in _strong_domains:
                    _sd_kw = _domain_kw_map.get(_sd, [])
                    _overlap = sum(1 for _t in _question_terms for _sk in _sd_kw if _t in _sk or _sk in _t)
                    if _overlap >= 1:
                        _derivation_type = _sd
                        self._log(LogLevel.INFO,
                                 f"路由自我感知回退: 利用推理技能画像，匹配到擅长领域'{_sd}'")
                        break

        # ★v18.0新增：薄弱领域主动降级
        # 当检测到当前问题落在推理薄弱领域时，自动降低复杂度阈值
        # 让问题更容易走深度思考通道，而非走知识检索的短路路径
        if _derivation_type and hasattr(self, 'get_reasoning_skill_portrait'):
            _skill = self.get_reasoning_skill_portrait()
            _weak_types = [w.get("type", "") for w in _skill.get("weak_types", [])]

            if _derivation_type in _weak_types:
                # 薄弱领域：提升复杂度感知，触发更深的推理
                _complexity_boost = 0.15
                self._log(LogLevel.INFO,
                         f"路由薄弱降级: '{_derivation_type}'属于薄弱领域，"
                         f"复杂度感知+{_complexity_boost:.2f}")
                # 通过调整 _question_complexity 让后续的深度思考检测更容易触发
                # 注意：_question_complexity 现为 ctx._question_complexity 字段（第147批九刀拆分后）
                # 本方法不持有 ctx，仍通过返回特殊的 derivation_type 来标记，让外层处理
                _derivation_type = f"weak_{_derivation_type}"

        if not _derivation_type:
            return None

        # ===== 【阶段二·推理路由融合】直觉二次确认 =====
        # 在正则匹配确定类型后，用直觉系统做二次确认。
        # 当直觉强烈支持另一种类型时，进行路由修正。
        # 只在非独占类型上执行（multi_variable_replay/conflict_resolution不走此逻辑）
        if _derivation_type not in ("multi_variable_replay", "conflict_resolution"):
            try:
                if self.risk_perception and hasattr(self.risk_perception, 'query_intuition'):
                    _intuition = self.risk_perception.query_intuition(question)
                    if _intuition.get("has_intuition") and _intuition.get("confidence", 0) >= 0.4:
                        # 检查直觉是否指向不同的推理类型
                        _prefer_signals = _intuition.get("prefer_signals", [])
                        _avoid_signals = _intuition.get("avoid_signals", [])

                        # 直觉类型映射：从直觉模式中提取领域对应的推理类型
                        _intuition_type_hint = None
                        for _ps in _prefer_signals:
                            _domain = _ps.get("domain", "")
                            if _domain in ("deductive", "inductive", "analogical", "multi_variable"):
                                _intuition_type_hint = _domain
                                break

                        if _intuition_type_hint and _intuition_type_hint != _derivation_type:
                            # 直觉与正则结果不一致，且直觉置信度较高时修正
                            if _intuition.get("confidence", 0) >= 0.6:
                                self._log(LogLevel.INFO,
                                         f"路由直觉修正: {_derivation_type} → {_intuition_type_hint} "
                                         f"(直觉置信度={_intuition['confidence']:.2f})")
                                _derivation_type = _intuition_type_hint
                            else:
                                self._log(LogLevel.DEBUG,
                                         f"路由直觉保留: 正则={_derivation_type}, 直觉={_intuition_type_hint} "
                                         f"(直觉置信度不足={_intuition.get('confidence', 0):.2f})")
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"路由直觉确认异常: {_e}")
        # ===== 直觉二次确认结束 =====
                # ★v17.0新增：推理技能画像调制路由优先级
        # 如果检测到的路由类型是薄弱领域，考虑切换到擅长领域
        if _derivation_type and hasattr(self, 'get_reasoning_skill_portrait'):
            try:
                _skill = self.get_reasoning_skill_portrait()
                _weak_types = [w.get("type", "") for w in _skill.get("weak_types", [])]
                _strong_types = [s.get("type", "") for s in _skill.get("strong_types", [])]

                # 如果当前路由是薄弱领域，且有可替代的擅长领域
                if _derivation_type in _weak_types and _strong_types:
                    # 只在置信度不高时考虑切换（避免干扰确定的路由匹配）
                    _alt_type = _strong_types[0]
                    if _alt_type != _derivation_type and _alt_type in [
                        "deductive", "inductive", "analogical", "multi_variable"
                    ]:
                        self._log(LogLevel.INFO,
                                 f"路由技能调制: 薄弱领域'{_derivation_type}'→擅长领域'{_alt_type}'")
                        _derivation_type = _alt_type
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        # ===== 【P1-2新增】记录当前推理类型，供知识编织领域过滤使用 =====
        self._current_derivation_type = _derivation_type
        # ===== 推理类型记录结束 =====
        self._log(LogLevel.INFO, f"自主推导路由: 检测到{_derivation_type}类型问题，调用推导引擎")

        try:
            # ★v17.0新增：self_mind_theory走独立路径，调用心智理论推演算子
            if _derivation_type == "self_mind_theory":
                _derivation_result = self._derive_self_mind_theory(question)
                if not _derivation_result:
                    self._log(LogLevel.DEBUG, f"心智理论推演算子返回None: {_derivation_type}")
                    return None
            # ===== 【P2-3修复】multi_variable_replay 走独立路径，不进入编排层 =====
            elif _derivation_type == "multi_variable_replay":
                _derivation_result = self._derive_multi_variable_replay(question)
                if not _derivation_result:
                    self._log(LogLevel.DEBUG, f"七步复盘算子返回None: {_derivation_type}")
                    return None
            # ===== 【P0修复】conflict_resolution 走独立路径，不经过编排层 =====
            elif _derivation_type == "conflict_resolution":
                _derivation_result = self._derive_conflict_resolution(question)
                if not _derivation_result:
                    self._log(LogLevel.DEBUG, f"冲突辨析算子返回None: {_derivation_type}")
                    return None
            # ===== 编排层：主算子+辅助算子多角度推理 =====
            elif _derivation_type in ("deductive", "inductive", "analogical",
                                       "multi_variable"):
                _derivation_result = self._orchestrate_reason(question, _derivation_type)
            # 无辅助算子的类型，直接调用单个算子
            elif _derivation_type == "meta_reflection":
                _derivation_result = self._derive_meta_reflection(question)
            elif _derivation_type == "meta_replay":
                _derivation_result = self._derive_meta_replay(question)
            elif _derivation_type == "long_term_evolution":
                _derivation_result = self._derive_long_term_evolution(question)
            else:
                derivations = self._autonomous_deriver.derive(
                    self.node_pool, self.knowledge_tree, _derivation_type,
                    context_question=question
                )
                _derivation_result = derivations[0]["content"] if derivations else None
            # ===== 路由分发结束 =====

            if not _derivation_result:
                return None

            self._inference_count += 1
            self._cache_inference(question, _derivation_result, user_name)
            self._trace_inference(question, _derivation_result, f"deriver_{_derivation_type}",
                                 0.6, user_name,
                                 duration=time.time() - _reasoning_start_time,
                                 complexity=_question_complexity,
                                 tuning_hint=f"自主推导引擎({_derivation_type})完成")

            if _derivation_type == "long_term_evolution":
                final_answer = _derivation_result
            else:
                final_answer = self._enhance_answer(
                    answer=_derivation_result,
                    question=question,
                    method=f"deriver_{_derivation_type}",
                    complexity=_question_complexity,
                    empathetic_note=empathetic_note,
                    memory_context=_memory_context
                )

            # ===== 【P1-2+P2-4新增】推理完成，退出推理模式，恢复默认语境 =====
            self._is_inference_mode = False
            self._current_derivation_type = None
            self._current_context_mode = "casual_chat"
            # ===== 推理模式清理结束 =====
            self._emit(InferenceEvent.RESULT, {
                "question": question, "answer": final_answer,
                "method": f"deriver_{_derivation_type}", "confidence": 0.6, "user_name": user_name,
                "correlation_id": payload.get("correlation_id", ""),
                "confidence_hint": "moderate",
                "strategy_applied": payload.get("strategy_context", {}),
            }, priority=7, layer="L2")
            return {"status": f"deriver_{_derivation_type}", "answer": _derivation_result}
        except Exception as e:
            self._log(LogLevel.DEBUG, f"自主推导路由异常: {e}")
            return None
    def _orchestrate_reason(self, question: str, derivation_type: str) -> str | None:
        """
        【v15.3新增】推理流水线编排层。

        根据推理类型分配主算子和辅助算子，综合多个角度生成更全面的回答。
        主算子失败时辅助算子接替，辅助算子成功时结果追加补充。

        辅助算子映射：
        - deductive（演绎）→ 辅助：归纳（从规则中提炼规律）
        - inductive（归纳）→ 辅助：演绎（验证归纳结论的因果链）
        - analogical（类比）→ 辅助：归纳（从类比中提炼通用规律）
        - multi_variable（多变量）→ 辅助：冲突处理（检测变量间矛盾）
        - conflict_resolution（冲突）→ 辅助：多变量（从多角度审视冲突）

        其他类型（meta_replay、meta_reflection、long_term_evolution）不使用辅助算子。
        """
        # ★v9.5：推理流水线耗时记录（后台分析提速点的关键证据）
        _orchestrate_t0 = time.time()
        # 主算子映射
        # 【P0修复】multi_variable_replay和conflict_resolution在_route_to_deriver中独立调用，
        # 不进入编排层，因此从字典中移除。编排层只管理需要主+辅多角度推理的四种类型。
        _primary_operator = {
            "deductive": ("演绎", self._derive_deductive_chain),
            "inductive": ("归纳", self._derive_inductive_from_samples),
            "analogical": ("类比", self._derive_self_analogy),
            "multi_variable": ("多变量", self._derive_multi_variable),
            "long_term_evolution": ("长期演化", self._derive_long_term_evolution),
            "meta_replay": ("推导回放", self._derive_meta_replay),
            "meta_reflection": ("元认知反思", self._derive_meta_reflection),
            "conflict_resolution": ("冲突辨析", self._derive_conflict_resolution),
        }

        _auxiliary_operator = {
            "deductive": ("归纳补充", self._derive_inductive_from_samples),
            "inductive": ("演绎验证", self._derive_deductive_chain),
            "analogical": ("规律提炼", self._derive_inductive_from_samples),
            "multi_variable": ("冲突检测", self._derive_conflict_resolution),
            "conflict_resolution": ("多变量审视", self._derive_multi_variable),
            "long_term_evolution": ("多变量推演", self._derive_multi_variable),
            "meta_replay": ("元认知反思", self._derive_meta_reflection),
            "meta_reflection": ("推导回放", self._derive_meta_replay),
        }

        _primary_info = _primary_operator.get(derivation_type)
        if not _primary_info:
            return None

        _primary_name, _primary_func = _primary_info

        # ===== 阶段1：执行主算子 =====
        _primary_result = None
        try:
            _primary_result = _primary_func(question)
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"编排层·主算子异常({_primary_name}): {_e}")

        # ===== 阶段2：主算子失败时，尝试辅助算子接替 =====
        if not _primary_result:
            _aux_info = _auxiliary_operator.get(derivation_type)
            if _aux_info:
                _aux_name, _aux_func = _aux_info
                try:
                    _aux_result = _aux_func(question)
                    if _aux_result:
                        self._log(LogLevel.INFO,
                                 f"编排层·辅助接替: 主算子({_primary_name})失败，"
                                 f"辅助算子({_aux_name})成功接替")
                        return _aux_result
                except Exception as _e:
                    self._log(LogLevel.DEBUG, f"编排层·辅助算子异常({_aux_name}): {_e}")

            # 所有算子都失败，回退到 AutonomousDeriver
            if self._autonomous_deriver and self.node_pool:
                try:
                    derivations = self._autonomous_deriver.derive(
                        self.node_pool, self.knowledge_tree, derivation_type,
                        context_question=question
                    )
                    if derivations:
                        _derivation_result = derivations[0]["content"]
                        self._log(LogLevel.INFO,
                                 "编排层·自主推导兜底: 主算子和辅助算子均失败，"
                                 "AutonomousDeriver成功")
                        return _derivation_result
                except Exception as _e:
                    self._log(LogLevel.DEBUG, f"编排层·自主推导异常: {_e}")

            return None

        # ===== 阶段3：主算子成功，尝试辅助算子追加补充 =====
        _aux_info = _auxiliary_operator.get(derivation_type)
        if _aux_info:
            _aux_name, _aux_func = _aux_info
            try:
                _aux_result = _aux_func(question)
                if _aux_result and len(_aux_result) > 30:
                    # 检查辅助结果是否与主结果有实质性差异
                    _aux_clean = _aux_result[:100]
                    _primary_clean = _primary_result[:100]
                    # 简单判断：辅助结果的前60字与主结果不重复
                    if _aux_clean[:60] not in _primary_clean:
                        _primary_result = _primary_result + "\n\n" + _aux_result
                        self._log(LogLevel.INFO,
                                 f"编排层·多角度补充: 辅助算子({_aux_name})成功，"
                                 f"结果已追加到主算子结果")
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"编排层·辅助算子异常({_aux_name}): {_e}")

        # ===== 【★登顶路线图-山1 P1】跨域类比增强 =====
        # 当推理类型为类比且主/辅算子均给出结果时，用 AnalogyEngine 对
        # "用户问题 vs 知识库活跃节点"做结构骨架类比，命中则追加跨域迁移假设。
        # 零回归：仅类比类型启用；异常静默降级，不影响主链路。
        if derivation_type == "analogical" and _primary_result and self.node_pool:
            try:
                from nucleus.reasoning.analogy_engine import AnalogyEngine
                _analogy_eng = AnalogyEngine()
                # 取知识库中最近活跃的 L2/L3 节点文本作为类比源
                _candidates = []
                try:
                    # ★Dxxx/W4：优先取含冷驱逐节点的全集
                    if hasattr(self.node_pool, "get_all_including_evicted"):
                        _pool_nodes = self.node_pool.get_all_including_evicted()
                    else:
                        _pool_nodes = []
                    for _n in list(_pool_nodes)[:60]:
                        if getattr(_n, "evol_level", None) in ("L2", "L3"):
                            _val = getattr(_n, "value", "")
                            if isinstance(_val, str) and len(_val) >= 12:
                                _candidates.append(_val)
                except Exception:
                    _candidates = []
                _best_hyp = None
                _best_conf = 0.0
                _cand_fail = 0
                for _cand in _candidates[:20]:
                    try:
                        _ar = _analogy_eng.compare(question, _cand)
                        if _ar.is_cross_domain and _ar.confidence > _best_conf:
                            _best_conf = _ar.confidence
                            _best_hyp = _ar.migrated_hypothesis
                    except Exception:
                        _cand_fail += 1
                        continue
                if _cand_fail:
                    self._log(LogLevel.DEBUG,
                              f"编排层·跨域类比增强: {_cand_fail}个候选比较失败(静默跳过)")
                if _best_hyp and _best_conf >= 0.45:
                    _primary_result = _primary_result + "\n\n" + f"〔跨域类比增强〕{_best_hyp}"
                    self._log(LogLevel.INFO,
                              f"编排层·跨域类比增强: 命中知识节点类比(置信度{_best_conf:.2f})")
            except Exception as _e:
                self._log(LogLevel.DEBUG, f"编排层·跨域类比增强异常: {_e}")

        # ===== 【P0-3修复】标准化输出格式化层 =====
        # 所有推理算子的输出在返回前统一经过格式化模板处理，
        # 确保每种推理题型都有完整的结构化输出
        _formatted_result = self._format_inference_result(
            derivation_type=derivation_type,
            raw_result=_primary_result,
            question=question
        )
        # ★v9.5：推理耗时日志（DEBUG级只进文件）
        try:
            _orchestrate_ms = (time.time() - _orchestrate_t0) * 1000.0
            if _orchestrate_ms > 50.0:
                self._log(LogLevel.INFO,
                          f"推理流水线: [{derivation_type}] 耗时{_orchestrate_ms:.0f}ms")
            elif _orchestrate_ms >= 5.0:
                self._log(LogLevel.DEBUG,
                          f"推理流水线: [{derivation_type}] 耗时{_orchestrate_ms:.0f}ms")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        if _formatted_result:
            return _formatted_result
        # 格式化失败时返回原始结果，保证不丢失输出
        # ===== 格式化层结束 =====

        return _primary_result
    # 【P0-3新增】推理输出标准化格式化模板层
    def _format_inference_result(self, derivation_type: str, raw_result: str,
                                  question: str = "") -> str | None:
        """
        统一入口：根据推理类型调用对应的格式化模板。

        如果格式化失败，返回None让编排层使用原始结果兜底。
        """
        if not raw_result or len(raw_result) < 10:
            return None

        _formatter_map = {
            "deductive": self._format_deductive_result,
            "inductive": self._format_inductive_result,
            "analogical": self._format_analogical_result,
            "multi_variable": self._format_multi_variable_result,
            "conflict_resolution": self._format_conflict_result,
            "long_term_evolution": self._format_long_term_result,
            "meta_replay": self._format_meta_replay_result,
            "meta_reflection": self._format_meta_reflection_result,
        }

        _formatter = _formatter_map.get(derivation_type)
        if not _formatter:
            # 无对应模板的推理类型，保持原样输出
            return None

        try:
            _formatted = _formatter(raw_result, question)
            if _formatted and len(_formatted) >= 20:
                # ★登顶路线图-山1：开关开启时，为推理结论附加结构化证据链
                if self._evidence_trace_enabled():
                    _conf = 0.6  # 编排层默认置信度基线（可被算子细化覆盖）
                    _chain = self._build_evidence_chain_block(
                        derivation_type, question, _conf)
                    if _chain:
                        # 避免重复追加
                        if "⟦依据链⟧" not in _formatted:
                            _formatted = _formatted + "\n" + _chain
                    self._attach_evidence_chain_to_node(
                        question, derivation_type, _conf)
                self._log(LogLevel.DEBUG,
                         f"格式化模板({derivation_type}): {len(raw_result)}字→{len(_formatted)}字")
                return _formatted
            return None
        except Exception as _e:
            self._log(LogLevel.DEBUG, f"格式化模板异常({derivation_type}): {_e}")
            return None
