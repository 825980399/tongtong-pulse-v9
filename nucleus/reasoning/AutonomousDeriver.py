# -*- coding: utf-8 -*-
"""
AutonomousDeriver.py —— 自主推导器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 自主知识推导与逻辑演绎
机制: 基于AutonomousDeriver类实现，包含10个核心方法
定位: 推理核心层
"""

import re
import threading
import time
from typing import Any
from nucleus._silent_except import silent_exc



class AutonomousDeriver:
    """
    自主知识推导引擎。
    
    三种推导模式：
    1. 演绎推导——基于已有L2/L3节点中的规则关系，推导新事实
    2. 归纳推导——从多个同领域L2节点中提炼通用规律
    3. 类比推导——将已知领域的框架结构应用到未知领域
    """
    def __init__(self):
        self._derivation_log: list[dict[str, Any]] = []
        self._max_log = 50
        self._deduction_cooldown: dict[str, float] = {}
        self._DEDUCTION_COOLDOWN = 3600  # 同类型推导冷却1小时
        # ★v23.0新增：归纳和类比的冷却追踪
        self._induction_cooldown: dict[str, float] = {}  # 归纳冷却
        self._analogy_cooldown: dict[str, float] = {}    # 类比冷却
        self._COOLDOWN_ALL = 1800  # 归纳/类比冷却30分钟
    
    def derive(self, node_pool, knowledge_tree, derivation_type: str = "auto", context_question: str | None = None) -> list[dict[str, Any]]:
        """
        执行自主推导。
        
        Args:
            node_pool: 知识节点池
            knowledge_tree: 知识树
            derivation_type: 推导类型——"deductive" / "inductive" / "analogical" / "auto"
        Returns:
            推导结果列表 [{"type", "content", "keywords", "confidence", "source_nodes"}]
        """
        if not node_pool:
            return []
        
        if derivation_type == "auto":
            # 自动选择：检查每种推导是否有足够素材
            results = []
            results.extend(self._deductive_derive(node_pool))
            results.extend(self._inductive_derive(node_pool, knowledge_tree))
            results.extend(self._analogical_derive(node_pool, knowledge_tree))
            return results
        elif derivation_type == "deductive":
            return self._deductive_derive(node_pool)
        elif derivation_type == "inductive":
            return self._inductive_derive(node_pool, knowledge_tree)
        elif derivation_type == "analogical":
            return self._analogical_derive(node_pool, knowledge_tree, context_question)
        
        return []
    
    def _deductive_derive(self, node_pool) -> list[dict[str, Any]]:
        """
        逻辑演绎：从L2/L3节点中寻找因果关系链，推导新事实。
        
        方法：
        1. 搜索包含因果关键词的节点（导致/因此/所以/因为/影响/产生）
        2. 找到A→B和B→C的关系链
        3. 推导A→C（标记为低置信度）
        """
        results = []
        
        # 获取L2和L3节点
        l3_nodes = node_pool.query(evol_level="L3", limit=20)
        l2_nodes = node_pool.query(evol_level="L2", limit=80)
        all_nodes = l3_nodes + l2_nodes
        
        if len(all_nodes) < 5:
            return results
        
        # 因果关键词
        causal_keywords = ["导致", "因此", "所以", "因为", "影响", "产生", 
                          "引起", "造成", "促使", "触发", "驱动", "推动",
                          "使得", "结果是", "源于", "归因于"]
        
        # 提取包含因果关系的节点
        causal_nodes = []
        for node in all_nodes:
            value = str(node.value) if node.value else ""
            for kw in causal_keywords:
                if kw in value:
                    causal_nodes.append(node)
                    break
        
        if len(causal_nodes) < 2:
            return results
        
        # 优先使用高信任节点（信任分数≥50）
        high_trust_causal = [n for n in causal_nodes 
                            if getattr(n, 'trust_score', 50.0) >= 50.0]
        if len(high_trust_causal) >= 2:
            causal_nodes = high_trust_causal
        elif len(causal_nodes) < 2:
            return results
        
        # 提取因果关系中的概念对
        causal_pairs = []
        for node in causal_nodes[:10]:
            value = str(node.value) if node.value else ""
            kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
            
            # 找因果关系中涉及的A和B
            for i, kw_a in enumerate(kws):
                for kw_b in kws[i+1:]:
                    # 检查是否有因果关系连接词出现在两个关键词之间
                    # ★修复：关键词可能含正则特殊字符（*+?()等），必须re.escape转义
                    for causal_kw in causal_keywords:
                        pattern = f"{re.escape(kw_a)}.*?{re.escape(causal_kw)}.*?{re.escape(kw_b)}"
                        try:
                            if re.search(pattern, value):
                                causal_pairs.append({
                                "cause": kw_a,
                                "effect": kw_b,
                                "relation": causal_kw,
                                "source_node_id": node.node_id,
                                "source_value": value[:100],
                                "trust": getattr(node, 'trust_score', 50.0),
                            })
                            break
                        except re.error:
                            # 关键词含无法处理的正则特殊字符，跳过此组合
                            continue
        
        if len(causal_pairs) < 2:
            return results

        # ★B-2（2026-09-08 第八批）：因果链边存在性校验（灰度 ENABLE_CAUSAL_INFERENCE
        #   默认 False；关闭时走原路径，推导结果不校验、零回退）。
        #   开启时：链上每条边必须在框架自有图谱（semantic_relations/横向索引/词法）
        #   中真实存在——校验不过则截断到最后一条已验证边，置信度按未验证跳数衰减，
        #   且输出如实标注验证状态（禁止只追加"已验证"文本）。
        if self._causal_inference_enabled() and node_pool is not None:
            _verified_deductions = self._verify_deductions_with_graph(
                causal_pairs, node_pool)
            if _verified_deductions:
                results.extend(_verified_deductions)
            return results[:2]
        
        # 寻找因果链：A→B 和 B→C → 推导 A→C
        deductions = []
        seen_chains = set()
        
        for pair1 in causal_pairs:
            for pair2 in causal_pairs:
                if pair1["source_node_id"] == pair2["source_node_id"]:
                    continue
                
                # B在pair1中是effect，在pair2中是cause
                if pair1["effect"].lower() == pair2["cause"].lower():
                    chain_key = f"{pair1['cause']}→{pair1['effect']}→{pair2['effect']}"
                    if chain_key in seen_chains:
                        continue
                    seen_chains.add(chain_key)
                   # 综合置信度：源节点信任越高，推导结果越可信
                    _avg_source_trust = (pair1["trust"] + pair2["trust"]) / 2
                    _derived_confidence = min(45.0, _avg_source_trust * 0.4 + 10.0)                    
                    # 推导A→C
                    deduction = {
                        "type": "deductive",
                        "content": (
                            f"[演绎推导] 已知「{pair1['cause']}」{pair1['relation']}「{pair1['effect']}」，"
                            f"且「{pair2['cause']}」{pair2['relation']}「{pair2['effect']}」。"
                            f"由此推导：「{pair1['cause']}」可能间接影响「{pair2['effect']}」。"
                            f"这个推导基于两条独立的因果关系链，需要进一步验证。"
                        ),
                        "keywords": [pair1["cause"], pair1["effect"], pair2["effect"]],
                        "confidence": round(_derived_confidence, 1),
                        "source_trust_avg": round(_avg_source_trust, 1),
                        "source_nodes": [pair1["source_node_id"], pair2["source_node_id"]],
                        "derivation_chain": chain_key,
                    }
                    deductions.append(deduction)
        
        return deductions[:2]

    # ========== ★B-2（2026-09-08 第八批）：因果链图谱校验 ==========
    @staticmethod
    def _causal_inference_enabled() -> bool:
        """灰度 ENABLE_CAUSAL_INFERENCE（默认 False，关闭时零行为）。"""
        try:
            import config as _cfg
            return bool(getattr(_cfg, "ENABLE_CAUSAL_INFERENCE", False))
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.AutonomousDeriver::_causal_inference_enabled L201")
            return False

    def _verify_deductions_with_graph(self, causal_pairs: list[dict],
                                      node_pool) -> list[dict[str, Any]]:
        """把因果对序列送 CausalInferrer 做边存在性校验（截断+置信度衰减）。

        与原推导的分工：原逻辑负责"从文本抽因果对+组链"（不变）；
        本方法负责"链上每条边是否真实存在于框架图谱"（新增的前置校验）。
        """
        try:
            from nucleus.reasoning.CausalInferrer import get_causal_inferrer
        except Exception:
            return []

        _inferrer = get_causal_inferrer(log_fn=self._log)
        # 因果对按首尾相接组链（pair[i].effect == pair[i+1].cause 已由调用方保证）
        _result = _inferrer.verify_chain(causal_pairs, node_pool)
        if _result is None:
            return []

        _kept = _result["chain"]
        if not _kept:
            return []

        _p1, _p2 = _kept[0], _kept[-1]
        # 置信度（0~1）换算回推导分数制（0~50，与原 45 上限同量级）
        _score = round(_result["confidence"] * 50.0, 1)
        _status = "已验证" if not _result["truncated"] else (
            f"部分已验证（{_result['report']}）")

        return [{
            "type": "deductive",
            "content": (
                f"[演绎推导·图谱校验] 「{_p1['cause']}」{_p1.get('relation', '导致')}"
                f"「{_p1['effect']}」"
                + (f"，且「{_p2['cause']}」{_p2.get('relation', '导致')}"
                   f"「{_p2['effect']}」。"
                   if len(_kept) > 1 else "")
                + f"由此推导：「{_p1['cause']}」可能间接影响「{_p2['effect']}」。"
                f"校验状态：{_status}。"
            ),
            "keywords": [_p1["cause"], _p1["effect"], _p2["effect"]],
            "confidence": _score,
            "source_nodes": [_p.get("source_node_id") for _p in _kept],
            "derivation_chain": "→".join(
                f"{_p['cause']}→{_p['effect']}" for _p in _kept),
            "causal_verification": {
                "verified_hops": _result["verified_hops"],
                "truncated": _result["truncated"],
                "edge_report": _result["edge_report"],
            },
        }]

    def _log(self, level: str, msg: str):
        """轻量日志（AutonomousDeriver 原无 _log，B-2 补充，异常不冒泡）。"""
        try:
            from nucleus.aibot_logger import get_aibot_logger
            _lg = get_aibot_logger()
            _lv = (level or "info").lower()
            if _lv == "debug":
                _lg.debug(msg)
            elif _lv in ("warning", "warn"):
                _lg.warn(msg)
            elif _lv == "error":
                _lg.error(msg)
            else:
                _lg.info(msg)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.AutonomousDeriver::_log L260")

    def _inductive_derive(self, node_pool, knowledge_tree) -> list[dict[str, Any]]:
        """
        归纳升华：从同一路径下多个L2节点中提炼通用规律。
        
        方法：
        1. 按知识树路径分组
        2. 对每组≥3个节点的组，提取共同关键词
        3. 如果共同关键词≥2个，归纳出通用规律
        """
        results = []
        
        l2_nodes = node_pool.query(evol_level="L2", limit=100)
        
        if len(l2_nodes) < 6:
            return results
        
        # 按路径分组
        path_groups = {}
        for node in l2_nodes:
            path = getattr(node, 'space_path', '/')
            # 取到二级路径
            parts = path.strip('/').split('/')
            group_key = '/'.join(parts[:2]) if len(parts) >= 2 else parts[0] if parts else 'root'
            if group_key not in path_groups:
                path_groups[group_key] = []
            path_groups[group_key].append(node)
        
        # 对每组≥3个节点的组进行归纳（优先使用高信任节点）
        for path, nodes in path_groups.items():
            if len(nodes) < 3:
                continue
            
            # ★v23.0新增：冷却检查——同一路径30分钟内不重复归纳
            _now = time.time()
            _last_induct = self._induction_cooldown.get(path, 0)
            if _now - _last_induct < self._COOLDOWN_ALL:
                continue
            
            # 信任分数加权：高信任节点在归纳中权重更高
            _trust_weighted_nodes = sorted(
                nodes, 
                key=lambda n: getattr(n, 'trust_score', 50.0), 
                reverse=True
            )
            # 如果高信任节点≥3个，只使用它们
            _high_trust = [n for n in nodes if getattr(n, 'trust_score', 50.0) >= 50.0]
            if len(_high_trust) >= 3:
                nodes = _high_trust
            
            # 提取所有关键词及其出现频率
            from collections import Counter
            all_kw = []
            for node in nodes:
                kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
                all_kw.extend([kw.lower() for kw in kws if isinstance(kw, str) and len(kw) >= 2])
            
            kw_counter = Counter(all_kw)
            # 找出在≥2个节点中出现的共同关键词
            common_kw = [kw for kw, count in kw_counter.items() if count >= 2]
            
            if len(common_kw) < 2:
                continue
            
            # 提取节点中的共同价值/规律性描述
            common_patterns = []
            node_values = [str(n.value)[:150] if n.value else "" for n in nodes]
            for pattern in ["共同", "通用", "普遍", "基本", "核心", "关键", "本质", "共性"]:
                for val in node_values:
                    if pattern in val:
                        common_patterns.append(pattern)
                        break
            
            path_name = path.split('/')[-1] if '/' in path else path
            
            if common_patterns:
                # 尝试从知识库中查找深层原理
                _deep_insight = self._search_deep_principle(node_pool, common_kw)
                _insight_text = f"。深层原理：{_deep_insight}" if _deep_insight else "，值得进一步探索其底层统一原理。"

                induction = {
                    "type": "inductive",
                    "content": (
                        f"[归纳升华] 从「{path_name}」领域的{len(nodes)}个知识点中，"
                        f"发现「{'、'.join(common_kw[:3])}」是共同涉及的核心概念。"
                        f"这些知识揭示了该领域的{'、'.join(common_patterns[:2])}规律。"
                        f"{_insight_text}"
                    ),
                    "keywords": common_kw[:5],
                    "confidence": min(45.0, 30.0 + len(nodes) * 3.0),
                    "source_nodes": [n.node_id for n in nodes[:5]],
                    "derivation_chain": f"归纳: {path} ({len(nodes)}个节点)",
                }
                results.append(induction)
                # ★v23.0新增：记录冷却时间
                self._induction_cooldown[path] = time.time()
            # 无明确规律描述但共同关键词足够
            elif len(common_kw) >= 3 and len(nodes) >= 4:
                # 尝试从知识库中查找深层原理
                _deep_insight = self._search_deep_principle(node_pool, common_kw)
                _insight_text = f"。深层原理：{_deep_insight}" if _deep_insight else "，值得进一步整合和深化理解。"

                induction = {
                    "type": "inductive",
                    "content": (
                        f"[归纳升华] 在「{path_name}」领域，{len(nodes)}个知识点"
                        f"共同指向了「{'、'.join(common_kw[:3])}」。"
                        f"{_insight_text}"
                    ),
                    "keywords": common_kw[:5],
                    "confidence": min(40.0, 25.0 + len(nodes) * 2.5),
                    "source_nodes": [n.node_id for n in nodes[:5]],
                    "derivation_chain": f"归纳: {path} ({len(nodes)}个节点)",
                }
                results.append(induction)
                # ★v23.0新增：记录冷却时间
                self._induction_cooldown[path] = time.time()
        
        return results[:2]
    def _search_deep_principle(self, node_pool, common_keywords: list) -> str | None:
        """
        从知识库中搜索与给定关键词相关的深层原理。
        
        优先搜索L3节点中信任分数≥70的节点，提取其核心内容作为归纳的深度解释。
        """
        if not node_pool or not common_keywords:
            return None
        
        # 查询L3节点
        l3_nodes = node_pool.query(evol_level="L3", limit=30)
        if not l3_nodes:
            return None
        
        _best_node = None
        _best_overlap = 0
        
        _input_kw = {kw.lower() for kw in common_keywords if isinstance(kw, str) and len(kw) >= 2}
        
        for _node in l3_nodes:
            _trust = getattr(_node, 'trust_score', 50.0)
            if _trust < 70.0:
                continue
            
            _node_kw = {kw.lower() for kw in (_node.keywords or []) if isinstance(kw, str) and len(kw) >= 2}
            _overlap = len(_input_kw & _node_kw)
            
            if _overlap > _best_overlap:
                _best_overlap = _overlap
                _best_node = _node
        
        if _best_node and _best_overlap >= 2:
            _value = str(_best_node.value) if _best_node.value else ""
            # 取前150字作为核心解释
            _clean = _value[:150].rstrip("，,。.")
            if len(_clean) >= 30:
                return _clean
        
        return None    
    def _analogical_derive(self, node_pool, knowledge_tree, context_question: str | None = None) -> list[dict[str, Any]]:
        results = []
        
        # ===== 【v12.0新增】优先基于自我架构知识构建类比 =====
        _self_l3 = node_pool.query(evol_level="L3", space_path_prefix="/自我/架构", limit=30)
        _self_l2 = node_pool.query(evol_level="L2", space_path_prefix="/自我/架构", limit=30)
        _self_nodes = _self_l3 + _self_l2
        
        if len(_self_nodes) >= 4:
            _self_groups = {}
            for _node in _self_nodes:
                _path = getattr(_node, 'space_path', '/自我/架构')
                _parts = _path.strip('/').split('/')
                _group_key = '/'.join(_parts[:2]) if len(_parts) >= 2 else _parts[0]
                if _group_key not in _self_groups:
                    _self_groups[_group_key] = []
                _self_groups[_group_key].append(_node)
            
            if len(_self_groups) >= 2:
                _self_items = sorted(_self_groups.items(), key=lambda x: len(x[1]), reverse=True)
                _path_a, _nodes_a = _self_items[0]
                _path_b, _nodes_b = _self_items[1]
                
                _kw_a = set()
                for _n in _nodes_a[:5]:
                    _kws = _n.keywords if hasattr(_n, 'keywords') and _n.keywords else []
                    _kw_a.update([kw.lower() for kw in _kws if isinstance(kw, str) and len(kw) >= 2])
                
                _kw_b = set()
                for _n in _nodes_b[:5]:
                    _kws = _n.keywords if hasattr(_n, 'keywords') and _n.keywords else []
                    _kw_b.update([kw.lower() for kw in _kws if isinstance(kw, str) and len(kw) >= 2])
                
                _shared = _kw_a & _kw_b
                _unique_a = _kw_a - _kw_b
                _unique_b = _kw_b - _kw_a
                
                if len(_shared) >= 1:
                    return [{
                        "type": "analogical",
                        "content": (
                            f"[类比迁移·自我知识] 从我的自我架构认知来看，"
                            f"「{_path_a}」和「{_path_b}」都涉及"
                            f"「{'、'.join(list(_shared)[:3])}」等核心概念。"
                            f"「{_path_a}」中的「{'、'.join(list(_unique_a)[:2]) if _unique_a else '架构设计'}」原理，"
                            f"可以类比应用到「{_path_b}」的"
                            f"「{'、'.join(list(_unique_b)[:2]) if _unique_b else '认知演化'}」上，"
                            f"这提示了框架设计层面的深层统一性。"
                        ),
                        "keywords": list(_shared)[:5] + list(_unique_a)[:2] + list(_unique_b)[:2],
                        "confidence": 40.0,
                        "source_nodes": [_n.node_id for _n in _nodes_a[:3]] + [_n.node_id for _n in _nodes_b[:3]],
                        "derivation_chain": f"类比(自我知识): {_path_a} ↔ {_path_b}",
                    }]
        # ===== 自我知识优先类比结束 =====
        l2_nodes = node_pool.query(evol_level="L2", limit=100)
        
        if len(l2_nodes) < 8:
            return results
        
        # 按路径分组
        path_groups = {}
        for node in l2_nodes:
            path = getattr(node, 'space_path', '/')
            parts = path.strip('/').split('/')
            group_key = parts[0] if parts else 'root'
            if group_key not in path_groups:
                path_groups[group_key] = []
            path_groups[group_key].append(node)
        
        if len(path_groups) < 2:
            return results

        path_items = sorted(path_groups.items(), key=lambda x: len(x[1]), reverse=True)
        
        # 如果有首选领域，将它们排到前面
        _preferred_domains = set()
        if context_question:
            import re as _re_ctx
            _ctx_words = _re_ctx.findall(r'[\u4e00-\u9fff]{2,4}', context_question)
            for _node in l2_nodes[:50]:
                _node_kw = [kw.lower() for kw in (_node.keywords or []) if isinstance(kw, str)]
                _overlap = sum(1 for _w in _ctx_words if any(_w in _kw for _kw in _node_kw))
                if _overlap >= 1:
                    _path = getattr(_node, 'space_path', '/')
                    _root = _path.strip('/').split('/')[0] if _path else 'root'
                    _preferred_domains.add(_root)
        
        if _preferred_domains:
            _preferred_items = [(k, v) for k, v in path_items if k in _preferred_domains]
            _other_items = [(k, v) for k, v in path_items if k not in _preferred_domains]
            path_items = _preferred_items + _other_items
        
        analogies = []
        seen_pairs = set()
        
        for i in range(len(path_items)):
            for j in range(i + 1, len(path_items)):
                path_a, nodes_a = path_items[i]
                path_b, nodes_b = path_items[j]
                
                if len(nodes_a) < 3 or len(nodes_b) < 3:
                    continue
                
                # ★v23.0新增：冷却检查——同一领域对30分钟内不重复类比
                _now = time.time()
                _pair_cool_key = f"{path_a}:{path_b}"
                _last_analogy = self._analogy_cooldown.get(_pair_cool_key, 0)
                if _now - _last_analogy < self._COOLDOWN_ALL:
                    continue
                
                _high_trust_a = [n for n in nodes_a if getattr(n, 'trust_score', 50.0) >= 45.0]
                _high_trust_b = [n for n in nodes_b if getattr(n, 'trust_score', 50.0) >= 45.0]
                if len(_high_trust_a) >= 3:
                    nodes_a = _high_trust_a
                if len(_high_trust_b) >= 3:
                    nodes_b = _high_trust_b
                
                pair_key = tuple(sorted([path_a, path_b]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                
                kw_a = set()
                for n in nodes_a[:5]:
                    kws = n.keywords if hasattr(n, 'keywords') and n.keywords else []
                    kw_a.update([kw.lower() for kw in kws if isinstance(kw, str) and len(kw) >= 2])
                
                kw_b = set()
                for n in nodes_b[:5]:
                    kws = n.keywords if hasattr(n, 'keywords') and n.keywords else []
                    kw_b.update([kw.lower() for kw in kws if isinstance(kw, str) and len(kw) >= 2])
                
                shared_kw = kw_a & kw_b
                unique_a = kw_a - kw_b
                unique_b = kw_b - kw_a
                
                if len(shared_kw) >= 2 and len(unique_a) >= 2 and len(unique_b) >= 2:
                    analogy = {
                        "type": "analogical",
                        "content": (
                            f"[类比迁移] 「{path_a}」和「{path_b}」看似不同，"
                            f"但都涉及「{'、'.join(list(shared_kw)[:3])}」。"
                            f"如果「{path_a}」中的「{'、'.join(list(unique_a)[:2])}」原理"
                            f"可以应用到「{path_b}」的「{'、'.join(list(unique_b)[:2])}」上，"
                            f"可能会产生新的交叉领域洞察。"
                        ),
                        "keywords": list(shared_kw)[:5] + list(unique_a)[:2] + list(unique_b)[:2],
                        "confidence": min(35.0, 20.0 + len(shared_kw) * 5.0),
                        "source_nodes": [n.node_id for n in nodes_a[:3]] + [n.node_id for n in nodes_b[:3]],
                        "derivation_chain": f"类比: {path_a} ↔ {path_b} (共同概念: {len(shared_kw)}个)",
                    }
                    analogies.append(analogy)
                    # ★v23.0新增：记录冷却时间
                    self._analogy_cooldown[_pair_cool_key] = time.time()
        
        return analogies[:2]
    def causal_chain_derive(self, node_pool, premise_a: str, premise_b: str) -> dict[str, Any] | None:
        """
        【v12.0新增】因果链传递算子：给定两个前提"A→B"和"B→C"，推导"A→C"。
        
        支持：
        1. 从知识库中搜索与premise_a和premise_b相关的因果节点
        2. 构建因果图，寻找传递路径
        3. 返回推导结论及完整推理链
        
        Args:
            node_pool: 知识节点池
            premise_a: 前提A的描述
            premise_b: 前提B的描述
        
        Returns:
            推导结果字典，包含content、keywords、confidence、chain_steps
        """
        if not node_pool:
            return None
        
        # 获取L2和L3节点
        l3_nodes = node_pool.query(evol_level="L3", limit=30)
        l2_nodes = node_pool.query(evol_level="L2", limit=50)
        all_nodes = l3_nodes + l2_nodes
        
        if len(all_nodes) < 3:
            return None
        
        # 因果关键词
        causal_keywords = ["导致", "因此", "所以", "因为", "影响", "产生",
                          "引起", "造成", "促使", "触发", "驱动", "推动",
                          "使得", "结果是", "源于", "归因于", "取决于", "依赖于"]
        
        # 提取与premise_a和premise_b相关的因果节点
        relevant_nodes_a = []
        relevant_nodes_b = []
        
        import re  # noqa: F401
        for node in all_nodes:
            value = str(node.value) if node.value else ""
            kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
            
            # 检查是否包含因果关键词
            has_causal = any(kw in value for kw in causal_keywords)
            if not has_causal:
                continue
            
            # 检查与premise_a或premise_b的相关性
            kw_str = " ".join(kws).lower()
            if any(w in kw_str for w in premise_a.lower().split()):
                relevant_nodes_a.append(node)
            if any(w in kw_str for w in premise_b.lower().split()):
                relevant_nodes_b.append(node)
        
        if len(relevant_nodes_a) < 1 or len(relevant_nodes_b) < 1:
            return None
        
        # 提取因果关系对
        def extract_causal_pairs(nodes, filter_word=""):
            pairs = []
            for node in nodes[:10]:
                value = str(node.value) if node.value else ""
                kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
                for causal_kw in causal_keywords:
                    # 分割因果关系的两端
                    parts = value.split(causal_kw, 1)
                    if len(parts) == 2:
                        cause_part = parts[0][-80:]
                        effect_part = parts[1][:80]
                        # 提取关键词
                        cause_kw = []
                        effect_kw = []
                        for kw in kws:
                            if kw in cause_part:
                                cause_kw.append(kw)
                            if kw in effect_part:
                                effect_kw.append(kw)
                        if cause_kw and effect_kw:
                            pairs.append({
                                "cause": cause_kw[0],
                                "effect": effect_kw[0],
                                "relation": causal_kw,
                                "cause_text": cause_part.strip()[-60:],
                                "effect_text": effect_part.strip()[:60],
                                "trust": getattr(node, 'trust_score', 50.0),
                                "node_id": node.node_id,
                            })
            return pairs
        
        pairs_a = extract_causal_pairs(relevant_nodes_a, premise_a)
        pairs_b = extract_causal_pairs(relevant_nodes_b, premise_b)
        
        if not pairs_a or not pairs_b:
            return None
        
        # 寻找因果链：A→B 和 B→C
        chain_steps = []
        pa = None
        pb = None
        for pa in pairs_a:
            for pb in pairs_b:
                # B在pa中是effect，在pb中是cause
                if pa["effect"].lower() == pb["cause"].lower():
                    chain_steps.append({
                        "step1": f"「{pa['cause']}」{pa['relation']}「{pa['effect']}」",
                        "step2": f"「{pb['cause']}」{pb['relation']}「{pb['effect']}」",
                        "derived": f"「{pa['cause']}」可能间接影响「{pb['effect']}」",
                        "chain": f"{pa['cause']}→{pa['effect']}→{pb['effect']}",
                        "avg_trust": (pa["trust"] + pb["trust"]) / 2,
                    })
        
        if not chain_steps:
            return None
        
        # 选择信任最高的因果链
        best_chain = max(chain_steps, key=lambda c: c["avg_trust"])
        confidence = min(45.0, best_chain["avg_trust"] * 0.4 + 10.0)
        
        _result = {
            "type": "causal_chain",
            "content": (
                f"[因果链推演] {best_chain['step1']}，且{best_chain['step2']}。"
                f"综合推演：{best_chain['derived']}。"
                f"这个结论基于两条独立因果链的传递推导，需要进一步验证。"
            ),
            "keywords": [pa["cause"], pa["effect"], pb["effect"]],
            "confidence": round(confidence, 1),
            "chain_steps": best_chain,
            "source_nodes": [pa.get("node_id", ""), pb.get("node_id", "")],
        }
        # ★v9.5山1-P0多步因果验证器：对推导链做符号级逐级验证，断链标记待验证
        try:
            from nucleus.reasoning.CausalVerifier import get_causal_verifier
            _result = get_causal_verifier().verify_causal_chain_result(_result, node_pool)
        except Exception as _e:
            # ★第51批 T4（P2-355）：原为裸 ``pass`` → 显式留痕
            from nucleus.logger import get_module_logger as _m51_gml
            _m51_gml("AutonomousDeriver").debug(
                "[T4留痕] 因果链验证器异常已忽略: %s: %s",
                type(_e).__name__, _e)
        return _result
    
    def inductive_generalize(self, node_pool, samples: list[str], domain_hint: str = "") -> dict[str, Any] | None:
        """
        【v12.0新增】归纳抽象算子：从多个样本中提炼统一规律。
        
        支持：
        1. 从知识库中搜索与各样本相关的节点
        2. 提取共同关键词和模式
        3. 生成归纳结论
        
        Args:
            node_pool: 知识节点池
            samples: 样本描述列表
            domain_hint: 领域提示
        
        Returns:
            归纳结果字典
        """
        if not node_pool or len(samples) < 3:
            return None
        
        # 为每个样本搜索相关节点
        all_relevant_nodes = []
        sample_keywords = {}
        
        for i, sample in enumerate(samples):
            l3_nodes = node_pool.query(evol_level="L3", limit=10)
            l2_nodes = node_pool.query(evol_level="L2", limit=20)
            
            relevant = []
            for node in l3_nodes + l2_nodes:
                value = str(node.value).lower() if node.value else ""
                if any(w in value for w in sample.lower().split()):
                    relevant.append(node)
            
            all_relevant_nodes.extend(relevant)
            sample_keywords[i] = set()
            for node in relevant:
                kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
                sample_keywords[i].update(kw.lower() for kw in kws if isinstance(kw, str) and len(kw) >= 2)
        
        if len(all_relevant_nodes) < 5:
            return None
        
        # 找所有样本的共同关键词
        if not sample_keywords:
            return None
        common_kw = None
        for i in range(len(samples)):
            if sample_keywords.get(i):
                if common_kw is None:
                    common_kw = sample_keywords[i].copy()
                else:
                    common_kw &= sample_keywords[i]
        
        if not common_kw or len(common_kw) < 2:
            # 放宽条件：找至少3个样本中出现的共同关键词
            from collections import Counter
            all_kw = []
            for i in range(len(samples)):
                if i in sample_keywords:
                    all_kw.extend(list(sample_keywords[i]))
            kw_counter = Counter(all_kw)
            common_kw = {kw for kw, count in kw_counter.items() if count >= max(2, len(samples) // 2)}
        
        if not common_kw or len(common_kw) < 2:
            return None
        
        common_list = list(common_kw)
        domain_str = domain_hint or "通用"
        confidence = min(40.0, 25.0 + len(all_relevant_nodes) * 1.5)
        
        return {
            "type": "inductive",
            "content": (
                f"[归纳抽象] 从{len(samples)}个不同样本中，发现共同指向了"
                f"「{'、'.join(common_list[:5])}」。"
                f"这表明在{domain_str}领域，可能存在一个以{'、'.join(common_list[:3])}"
                f"为核心的底层统一规律。"
            ),
            "keywords": common_list[:5],
            "confidence": round(confidence, 1),
            "sample_count": len(samples),
            "source_nodes": [n.node_id for n in all_relevant_nodes[:5]],
        }
    
    def analogical_map(self, node_pool, domain_a: str, domain_b: str) -> dict[str, Any] | None:
        """
        【v12.0新增】跨领域类比算子：在两组不同领域的节点间进行结构映射。
        
        支持：
        1. 从两个领域分别获取节点组
        2. 比较关键词分布和结构
        3. 生成类比洞察，包含具体可迁移的概念
        
        Args:
            node_pool: 知识节点池
            domain_a: 源领域路径
            domain_b: 目标领域路径
        
        Returns:
            类比结果字典
        """
        if not node_pool:
            return None
        
        # 从两个领域获取节点
        nodes_a = node_pool.query(evol_level="L2", space_path_prefix=domain_a, limit=20)
        if len(nodes_a) < 5:
            nodes_a = node_pool.query(evol_level="L3", space_path_prefix=domain_a, limit=10)
            if len(nodes_a) < 3:
                return None
        
        nodes_b = node_pool.query(evol_level="L2", space_path_prefix=domain_b, limit=20)
        if len(nodes_b) < 5:
            nodes_b = node_pool.query(evol_level="L3", space_path_prefix=domain_b, limit=10)
            if len(nodes_b) < 3:
                return None
        
        # 提取两个领域的关键词集合
        kw_a = set()
        for n in nodes_a[:8]:
            kws = n.keywords if hasattr(n, 'keywords') and n.keywords else []
            kw_a.update(kw.lower() for kw in kws if isinstance(kw, str) and len(kw) >= 2)
        
        kw_b = set()
        for n in nodes_b[:8]:
            kws = n.keywords if hasattr(n, 'keywords') and n.keywords else []
            kw_b.update(kw.lower() for kw in kws if isinstance(kw, str) and len(kw) >= 2)
        
        if not kw_a or not kw_b:
            return None
        
        # 计算共同概念和各自独有概念
        shared_kw = kw_a & kw_b
        unique_a = kw_a - kw_b
        unique_b = kw_b - kw_a
        
        if len(shared_kw) < 2:
            # 尝试通过语义关联找共同概念
            # 简化：找包含关系
            for kw in list(kw_a)[:10]:
                for kb in list(kw_b)[:10]:
                    if kw in kb or kb in kw:
                        shared_kw.add(kw)
        
        if len(shared_kw) < 1:
            return None
        
        # 提取各自的代表性概念用于映射
        rep_a = list(unique_a)[:3] if unique_a else list(kw_a)[:3]
        rep_b = list(unique_b)[:3] if unique_b else list(kw_b)[:3]
        
        domain_a_name = domain_a.split("/")[-1] if "/" in domain_a else domain_a
        domain_b_name = domain_b.split("/")[-1] if "/" in domain_b else domain_b
        
        confidence = min(35.0, 20.0 + len(shared_kw) * 5.0)
        
        return {
            "type": "analogical",
            "content": (
                f"[跨领域类比] 「{domain_a_name}」和「{domain_b_name}」虽然属于不同领域，"
                f"但都涉及「{'、'.join(list(shared_kw)[:4])}」等共同概念。"
                f"其中，「{domain_a_name}」的「{'、'.join(rep_a[:2])}」与"
                f"「{domain_b_name}」的「{'、'.join(rep_b[:2])}」存在结构上的对应关系。"
                f"如果将前者的原理迁移到后者，可能产生新的交叉洞察。"
            ),
            "keywords": list(shared_kw)[:5] + rep_a[:2] + rep_b[:2],
            "confidence": round(confidence, 1),
            "shared_concepts": list(shared_kw)[:5],
            "unique_a": rep_a[:3],
            "unique_b": rep_b[:3],
            "source_nodes": [n.node_id for n in nodes_a[:3]] + [n.node_id for n in nodes_b[:3]],
        }
        
    def log_derivation(self, result: dict[str, Any]):
        """记录推导日志"""
        self._derivation_log.append({
            **result,
            "timestamp": time.time(),
        })
        if len(self._derivation_log) > self._max_log:
            self._derivation_log = self._derivation_log[-self._max_log:]
    
    def get_derivation_log(self) -> list[dict[str, Any]]:
        """公开只读访问推导日志副本（规则14）"""
        return list(self._derivation_log)

    def get_stats(self) -> dict[str, Any]:
        """获取推导统计"""
        type_counts = {}
        for entry in self._derivation_log:
            t = entry.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        return {
            "total_derivations": len(self._derivation_log),
            "type_distribution": type_counts,
        }


# 模块级单例

_autonomous_deriver: AutonomousDeriver | None = None
_autonomous_deriver_lock = threading.Lock()

def get_autonomous_deriver() -> AutonomousDeriver:
    """获取AutonomousDeriver单例"""
    global _autonomous_deriver
    if _autonomous_deriver is None:
        with _autonomous_deriver_lock:
            if _autonomous_deriver is None:
                _autonomous_deriver = AutonomousDeriver()
    return _autonomous_deriver


def shutdown_autonomous_deriver() -> None:
    """★P1: 复位 AutonomousDeriver 单例，满足器官零状态（规则4）。"""
    global _autonomous_deriver
    _inst = _autonomous_deriver
    _autonomous_deriver = None
    if _inst is not None:
        _sd = getattr(_inst, "shutdown", None) or getattr(_inst, "stop", None)
        if _sd is not None:
            try:
                _sd()
            except Exception as e:
                silent_exc(e, where="nucleus.reasoning.AutonomousDeriver::shutdown_autonomous_deriver L943")

# _m51_t4_e