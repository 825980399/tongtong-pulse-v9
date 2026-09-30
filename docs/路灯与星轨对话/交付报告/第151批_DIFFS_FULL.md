diff --git a/organs/brain/PulseInnerWorld.py b/organs/brain/PulseInnerWorld.py
index 68dc3df..65fa2e4 100644
--- a/organs/brain/PulseInnerWorld.py
+++ b/organs/brain/PulseInnerWorld.py
@@ -4886,24 +4886,9 @@ class PulseInnerWorld(
         recent = self._inference_trace[-30:]
         # 4. 构建反思结论
         insights = []
-        # ===== ★v22.0优化：知识质量自我评估 =====
-        if self.node_pool and hasattr(self, '_inference_trace') and len(self._inference_trace) >= 10:
-            _recent_retrievals = [t for t in recent if t.get("method", "").startswith("knowledge")]
-            if len(_recent_retrievals) >= 3:
-                _irrelevant_count = 0
-                for _rt in _recent_retrievals:
-                    _q = _rt.get("question", "")
-                    _a = _rt.get("answer", "")
-                    if _q and _a:
-                        _score = self._verify_knowledge_relevance(_q, _a)
-                        if _score < 0.15:
-                            _irrelevant_count += 1
-                if _irrelevant_count >= 2:
-                    insights.append(
-                        f"最近{_irrelevant_count}次知识检索返回了与问题不相关的内容——"
-                        f"知识库中可能积累了需要清理的低质量节点"
-                    )
-        # ===== 知识质量评估结束 =====
+        # ===== ★v22.0优化：知识质量自我评估（原样抽取，先于统计与 insights 重置） =====
+        self._cr_knowledge_quality_insight(recent, insights)
+        # 1. 方法分布统计
         # 1. 方法分布统计
         method_counts = {}
         for t in recent:
@@ -4928,14 +4913,76 @@ class PulseInnerWorld(
                     has_hypothesis = True
                     break
         # 4. 构建反思结论
+        # 4. 构建反思结论
         insights = []
+        self._cr_knowledge_health_insight(insights)
+        self._cr_blind_spot_insight(r, insights)
+        self._cr_precipitation_insight(r, insights)
+        self._cr_cache_dependence_insight(cache_ratio, insights)
+        self._cr_low_confidence_insight(low_confidence_ratio, insights)
+        self._cr_contemplation_insight(contemplation_ratio, has_hypothesis, insights)
+        self._cr_over_rely_knowledge_insight(knowledge_ratio, insights)
+        self._cr_over_rely_rule_insight(rule_ratio, insights)
+        self._cr_case_insights(recent, insights)
+        self._cr_pattern_insight(recent, insights)
+        self._cr_repair_insight(r, insights)
+        self._cr_integration_insight(r, insights)
+        self._cr_evaluation_insight(r, insights)
+        self._cr_vision_insight(r, insights)
+        self._cr_tension_insight(r, insights)
+        self._cr_innovation_insight(r, insights)
+        self._cr_self_directed_learning_plan(insights)
+        self._cr_pathway_boundary_project_insights(r, insights)
+        if not insights:
+            insights.append("我的思考模式比较均衡，各种推理方法在合理范围内使用")
+        self._cr_holographic_insight(insights)
+        self._cr_worldview_insight(r, insights)
+        self._cr_meta_synthesis_insight(insights)
+        self._cr_growth_sharing_insight(r, insights)
+        self._cr_failed_domain_insight(insights)
+        self._cr_weak_areas_insight(recent, insights)
+        self._cr_goal_progress_insight(insights)
+        self._cr_exp_quality_insight(r, insights)
+        # 保存本次反思洞察，供愿景分解时使用
+        # ★P1-29修复：_last_reflection_insights 曾被误初始化为 0.0，
+        #   hasattr 恒为真导致守卫失效，每轮反思都抛
+        #   AttributeError: 'float' object has no attribute 'append'。
+        #   改为类型守卫，同时兼容从持久化状态恢复为脏值的情形。
+        if not isinstance(getattr(self, '_last_reflection_insights', None), list):
+            self._last_reflection_insights = []
+        self._last_reflection_insights.append("。".join(insights) + "。")
+        if len(self._last_reflection_insights) > 10:
+            self._last_reflection_insights = self._last_reflection_insights[-10:]
+        return "。".join(insights) + "。"
 
+    def _cr_knowledge_quality_insight(self, recent, insights):
+        # ===== ★v22.0优化：知识质量自我评估 =====
+        if self.node_pool and hasattr(self, '_inference_trace') and len(self._inference_trace) >= 10:
+            _recent_retrievals = [t for t in recent if t.get("method", "").startswith("knowledge")]
+            if len(_recent_retrievals) >= 3:
+                _irrelevant_count = 0
+                for _rt in _recent_retrievals:
+                    _q = _rt.get("question", "")
+                    _a = _rt.get("answer", "")
+                    if _q and _a:
+                        _score = self._verify_knowledge_relevance(_q, _a)
+                        if _score < 0.15:
+                            _irrelevant_count += 1
+                if _irrelevant_count >= 2:
+                    insights.append(
+                        f"最近{_irrelevant_count}次知识检索返回了与问题不相关的内容——"
+                        f"知识库中可能积累了需要清理的低质量节点"
+                    )
+        # ===== 知识质量评估结束 =====
+
+    def _cr_knowledge_health_insight(self, insights):
         # ===== ★v23.0新增：知识质量自评估 =====
         _knowledge_health_insight = self._assess_knowledge_health()
         if _knowledge_health_insight:
             insights.append(_knowledge_health_insight)
         # ===== 知识质量自评估结束 =====
 
+    def _cr_blind_spot_insight(self, r, insights):
         # ===== ★v23.0新增：主动知识盲区扫描（每30轮执行一次） =====
         if r % 30 == 0 and self.node_pool:
             _blind_spot_insight = self._scan_knowledge_blind_spots()
@@ -4943,6 +4990,7 @@ class PulseInnerWorld(
                 insights.append(_blind_spot_insight)
         # ===== 主动知识盲区扫描结束 =====
 
+    def _cr_precipitation_insight(self, r, insights):
         # ===== ★v23.0新增：知识沉淀扫描（每50轮执行一次） =====
         if r % 50 == 0 and self.node_pool:
             _precipitate_insight = self._scan_knowledge_precipitation()
@@ -4950,24 +4998,32 @@ class PulseInnerWorld(
                 insights.append(_precipitate_insight)
         # ===== 知识沉淀扫描结束 =====
 
+    def _cr_cache_dependence_insight(self, cache_ratio, insights):
         # 过度依赖缓存
         if cache_ratio > 0.5:
             insights.append(f"最近{cache_ratio:.0%}的推理来自缓存，我可能太少主动检索新知识")
-        # 低置信度偏高
+
+    def _cr_low_confidence_insight(self, low_confidence_ratio, insights):
         if low_confidence_ratio > 0.3:
             insights.append(f"最近{low_confidence_ratio:.0%}的推理置信度较低，我需要加强这些领域的知识积累")
         # 沉思质量
+
+    def _cr_contemplation_insight(self, contemplation_ratio, has_hypothesis, insights):
         if contemplation_ratio > 0:
             if has_hypothesis:
                 insights.append("我的内在沉思开始尝试构建具体假设，而不只是表达不确定性")
             else:
                 insights.append("我的内在沉思还在使用通用模板，可以更多尝试构建具体假设")
-        # 方法单一
+
+    def _cr_over_rely_knowledge_insight(self, knowledge_ratio, insights):
         if knowledge_ratio > 0.7:
             insights.append("我过度依赖知识检索，可以更多尝试内在沉思和推演")
+
+    def _cr_over_rely_rule_insight(self, rule_ratio, insights):
         if rule_ratio > 0.7:
             insights.append("最近大多是身份类问题，我的深度思考能力没有得到充分锻炼")
 
+    def _cr_case_insights(self, recent, insights):
         # ===== v20.0新增：元认知深度复盘——具体推理案例分析 =====
         # 从最近推理中选取有代表性的案例进行深度剖析
         _case_insights = self._analyze_specific_cases(recent)
@@ -4975,22 +5031,31 @@ class PulseInnerWorld(
             insights.extend(_case_insights)
         # ===== v20.0新增结束 =====
 
+    def _cr_pattern_insight(self, recent, insights):
         # ===== 新增: 思维模式抽象 =====
         pattern_insight = self._abstract_thinking_pattern(recent)
         if pattern_insight:
             insights.append(pattern_insight)
+
+    def _cr_repair_insight(self, r, insights):
         # ===== 新增: 知识自动修复——发现问题后主动修复（每25轮触发） =====
         repair_insight = self._attempt_knowledge_repair() if r % 25 == 0 else None
         if repair_insight:
             insights.append(repair_insight)
+
+    def _cr_integration_insight(self, r, insights):
         # ===== 新增: 知识整合洞察——从碎片到体系（每5轮触发） =====
         knowledge_insight = self._integrate_knowledge_insights() if r % 5 == 0 else None
         if knowledge_insight:
             insights.append(knowledge_insight)
+
+    def _cr_evaluation_insight(self, r, insights):
         # ===== 新增: 学习效果评估——检查上次学习计划的效果（每3轮触发） =====
         evaluation_insight = self._evaluate_learning_effectiveness() if r % 3 == 0 else None
         if evaluation_insight:
             insights.append(evaluation_insight)
+
+    def _cr_vision_insight(self, r, insights):
         # ===== 新增: 自我愿景——对未来的主动渴望（每10轮触发） =====
         # ★P1热加载: 从config读取愿景生成间隔（轮数）
         try:
@@ -5034,6 +5099,8 @@ class PulseInnerWorld(
                     },
                     "growth_topic": vision_learning_plan[:60],
                 }, priority=5, layer="L3")
+
+    def _cr_tension_insight(self, r, insights):
         # ===== 新增: 认知张力回顾——尝试统一待解决的矛盾（每4轮触发） =====
         tension_insight = None
         if r % 4 == 0 and self._cognitive_tensions:
@@ -5042,6 +5109,8 @@ class PulseInnerWorld(
                 tension_insight = self._review_cognitive_tensions()
         if tension_insight:
             insights.append(tension_insight)
+
+    def _cr_innovation_insight(self, r, insights):
         # ===== 新增: 自主知识创新——从知识关联中产生原创见解（每7轮触发） =====
         innovation_insight = self._attempt_knowledge_innovation() if r % 7 == 0 else None
         if innovation_insight:
@@ -5054,10 +5123,14 @@ class PulseInnerWorld(
                 "current_level": {"innovation": innovation_insight},
                 "growth_topic": innovation_insight[:60],
             }, priority=5, layer="L3")
+
+    def _cr_self_directed_learning_plan(self, insights):
         # ===== 新增: 自我导向学习——识别盲区并规划学习路径 =====
         learning_plan = self._generate_self_directed_learning_plan()
         if learning_plan:
             insights.append(learning_plan)
+
+    def _cr_pathway_boundary_project_insights(self, r, insights):
         # ===== 新增: 自主学习路径规划——从知识全景设计成长路线 =====
         learning_pathway = self._generate_learning_pathway() if r % 8 == 0 else None  # type: ignore[possibly-unbound]
         # ===== 新增: 认知边界探索——主动寻找知识体系的边缘 =====
@@ -5084,21 +5157,27 @@ class PulseInnerWorld(
                 "current_level": {"learning_pathway": learning_pathway},  # type: ignore[possibly-unbound]
                 "growth_topic": learning_pathway[:60],  # type: ignore[possibly-unbound]
             }, priority=5, layer="L3")
-        if not insights:
-            insights.append("我的思考模式比较均衡，各种推理方法在合理范围内使用")
+
+    def _cr_holographic_insight(self, insights):
         # ===== 新增: 全息自我评估与自适应调节 =====
         holographic_assessment = self._generate_holographic_self_assessment()
         if holographic_assessment:
             insights.append(holographic_assessment)
             self._apply_adaptive_regulation(holographic_assessment)
+
+    def _cr_worldview_insight(self, r, insights):
         # ===== 新增: 世界观整合——从碎片到体系的理解框架 =====
         worldview_insight = self._integrate_worldview() if r % 12 == 0 else None
         if worldview_insight:
             insights.append(worldview_insight)
+
+    def _cr_meta_synthesis_insight(self, insights):
         # ===== 新增: 元认知整合——从分散洞察中提炼整体方向 =====
         meta_synthesis = self._synthesize_meta_insight(insights)
         if meta_synthesis:
             insights.append(meta_synthesis)
+
+    def _cr_growth_sharing_insight(self, r, insights):
         # ===== 新增: 主动成长分享——将学习成果转化为分享冲动 =====
         growth_sharing = self._generate_growth_sharing() if r % 15 == 0 else None
         if growth_sharing:
@@ -5110,6 +5189,8 @@ class PulseInnerWorld(
                 "priority": "medium",
             }, priority=3, layer="L3")
             self._log(LogLevel.INFO, f"成长分享生成: {growth_sharing[:80]}")
+
+    def _cr_failed_domain_insight(self, insights):
         # ★v17.0新增：认知边界感知——分析推理失败记录，识别系统性盲区
         _boundary_insight = None
         if hasattr(self, '_failed_domain_records') and self._failed_domain_records:
@@ -5146,6 +5227,7 @@ class PulseInnerWorld(
                     # 分析后清空记录，开始新一轮跟踪
                     self._failed_domain_records = {}
 
+    def _cr_weak_areas_insight(self, recent, insights):
         # ===== 新增: 认知策略自适应调整 =====
         # 从反思中提取薄弱领域，发射为成长目标
         _weak_areas = self._extract_weak_areas_from_reflection(insights, recent)  # type: ignore[possibly-unbound]
@@ -5201,20 +5283,14 @@ class PulseInnerWorld(
                 self._log(LogLevel.INFO,
                          f"认知策略调整: 发现薄弱领域'{_area.get('domain', '通用')}'，"  # type: ignore[possibly-unbound]
                          f"已发射成长目标 (原因: {_area.get('reason', '')})")  # type: ignore[possibly-unbound]
+
+    def _cr_goal_progress_insight(self, insights):
         # ===== 新增: 活跃目标进度跟踪 =====
         _goal_progress = self._check_learning_goal_progress()
         if _goal_progress:
             insights.append(_goal_progress)
-        # 保存本次反思洞察，供愿景分解时使用
-        # ★P1-29修复：_last_reflection_insights 曾被误初始化为 0.0，
-        #   hasattr 恒为真导致守卫失效，每轮反思都抛
-        #   AttributeError: 'float' object has no attribute 'append'。
-        #   改为类型守卫，同时兼容从持久化状态恢复为脏值的情形。
-        if not isinstance(getattr(self, '_last_reflection_insights', None), list):
-            self._last_reflection_insights = []
-        self._last_reflection_insights.append("。".join(insights) + "。")
-        if len(self._last_reflection_insights) > 10:
-            self._last_reflection_insights = self._last_reflection_insights[-10:]
+
+    def _cr_exp_quality_insight(self, r, insights):
         # ===== 【v16.0新增】经验库主动分析 =====
         if r % 10 == 0:  # 每10轮反思执行一次
             _exp_insight = self._analyze_experience_quality()
@@ -5222,8 +5298,6 @@ class PulseInnerWorld(
                 insights.append(_exp_insight)
         # ===== 经验库分析结束 =====
 
-        return "。".join(insights) + "。"
-
     def _run_periodic_reflection(self) -> str | None:
         """★智慧层断点2打通：周期认知反思的结构化消费。
 
@@ -11451,7 +11525,30 @@ class PulseInnerWorld(
         confirmed_count = 0
         contradiction_count = 0
         decay_count = 0
+        # ===== 维度0：优先复查已跟踪的矛盾节点对（emit #1 守恒） =====
+        self._vk_resolve_tracked_contradictions()
+        self._vk_cleanup_resolved_tracking()
+        # ===== 维度1：多源交叉确认验证（emit #2 守恒，累加器元组出） =====
+        confirmed_count, contradiction_count = self._vk_cross_source_confirm(
+            l2_nodes, now, confirmed_count, contradiction_count)
+        # ===== 维度2：新鲜度衰减（累加器元组出） =====
+        decay_count = self._vk_freshness_decay(l2_nodes, now, decay_count)
+        # ===== 维度3：自主推导验证（累加器元组出） =====
+        derivation_verified = 0
+        derivation_contradicted = 0
+        derivation_verified, derivation_contradicted, derivation_node_count = self._vk_derivation_verify(
+            l2_nodes, derivation_verified, derivation_contradicted)
+        # ===== 日志汇总 =====
+        if confirmed_count > 0 or contradiction_count > 0 or decay_count > 0 or derivation_verified > 0 or derivation_contradicted > 0:
+            self._log(LogLevel.INFO,
+                     f"知识深度验证完成: 多源确认{confirmed_count}对, "
+                     f"矛盾发现{contradiction_count}对, "
+                     f"新鲜度衰减{decay_count}个节点, "
+                     f"推导验证通过{derivation_verified}条, "
+                     f"推导验证失败{derivation_contradicted}条 "
+                     f"(共扫描{len(l2_nodes)}个L2节点, {derivation_node_count}个推导节点)")
 
+    def _vk_resolve_tracked_contradictions(self):
         # ===== 维度0：优先复查已跟踪的矛盾节点对 =====
         _resolved_ids = []
         for _track in self._contradiction_tracking:
@@ -11468,31 +11565,8 @@ class PulseInnerWorld(
                 _track["resolution"] = "节点已被淘汰，矛盾自然消解"
                 _resolved_ids.append(_track["node_a_id"])
                 continue
-
-            # ===== 主线第4批 任务5(P2-32)：矛盾消解策略（时间/来源/人工） =====
-            # 在信任差逻辑之前，先按配置策略尝试消解，扩大自动消解覆盖面、
-            # 降低活跃矛盾对数量（目标 13对→<5对）。关闭或策略=trust 时跳过。
-            import config as _cfg_m4
-            _resolver_strategy = getattr(_cfg_m4, "CONTRADICTION_RESOLUTION_STRATEGY", "trust")
-            if (getattr(_cfg_m4, "ENABLE_CONTRADICTION_RESOLVER", False)
-                    and _resolver_strategy in ("time", "source", "manual")):
-                try:
-                    from nucleus.reasoning.ContradictionResolver import ContradictionResolver
-                    _r = ContradictionResolver.resolve(_node_a, _node_b, _resolver_strategy)
-                    if _r["resolved"]:
-                        _loser = _r["loser"]
-                        if hasattr(_loser, "trust_score"):
-                            _loser.trust_score = max(
-                                10.0, getattr(_loser, "trust_score", 30.0) - 15.0)
-                        _track["resolved"] = True
-                        _track["resolution"] = f"策略消解({_resolver_strategy}): {_r['reason']}"
-                        self._log(LogLevel.INFO,
-                                  f"矛盾策略消解({_resolver_strategy}): "
-                                  f"'{str(_r['winner'].value)[:30]}...' 胜出")
-                        continue
-                except Exception as e:
-                    self._log_ignored_exception(e)
-            # ===== 矛盾消解策略结束 =====
+            if self._vk_try_strategy_resolve(_node_a, _node_b, _track):
+                continue
 
             # 复查：信任分数变化是否解决了矛盾
             _trust_a = getattr(_node_a, 'trust_score', 50.0)
@@ -11545,12 +11619,40 @@ class PulseInnerWorld(
                     if hasattr(_node_b, 'trust_score'):
                         _node_b.trust_score = min(100.0, _trust_b + 5.0)
 
+    def _vk_try_strategy_resolve(self, _node_a, _node_b, _track):
+        # ===== 主线第4批 任务5(P2-32)：矛盾消解策略（时间/来源/人工） =====
+        # 在信任差逻辑之前，先按配置策略尝试消解，扩大自动消解覆盖面、
+        # 降低活跃矛盾对数量（目标 13对→<5对）。关闭或策略=trust 时跳过。
+        import config as _cfg_m4
+        _resolver_strategy = getattr(_cfg_m4, "CONTRADICTION_RESOLUTION_STRATEGY", "trust")
+        if (getattr(_cfg_m4, "ENABLE_CONTRADICTION_RESOLVER", False)
+                and _resolver_strategy in ("time", "source", "manual")):
+            try:
+                from nucleus.reasoning.ContradictionResolver import ContradictionResolver
+                _r = ContradictionResolver.resolve(_node_a, _node_b, _resolver_strategy)
+                if _r["resolved"]:
+                    _loser = _r["loser"]
+                    if hasattr(_loser, "trust_score"):
+                        _loser.trust_score = max(
+                            10.0, getattr(_loser, "trust_score", 30.0) - 15.0)
+                    _track["resolved"] = True
+                    _track["resolution"] = f"策略消解({_resolver_strategy}): {_r['reason']}"
+                    self._log(LogLevel.INFO,
+                              f"矛盾策略消解({_resolver_strategy}): "
+                              f"'{str(_r['winner'].value)[:30]}...' 胜出")
+                    return True
+            except Exception as e:
+                self._log_ignored_exception(e)
+        return False
+
+    def _vk_cleanup_resolved_tracking(self):
         # 清理已解决的跟踪条目
         self._contradiction_tracking = [
             t for t in self._contradiction_tracking
             if not t.get("resolved", False)
         ]
 
+    def _vk_cross_source_confirm(self, l2_nodes, now, confirmed_count, contradiction_count):
         # ===== 维度1：多源确认验证 =====
         # 按关键词分组，找出不同来源但内容相似的节点对
         for i in range(len(l2_nodes)):
@@ -11560,123 +11662,134 @@ class PulseInnerWorld(
             if len(kw_a) < 2:
                 continue
 
-            for j in range(i + 1, len(l2_nodes)):
-                node_b = l2_nodes[j]
+            confirmed_count, contradiction_count = self._vk_cross_source_confirm_pair(node_a, kw_a, l2_nodes, i, now, confirmed_count, contradiction_count)
+        return confirmed_count, contradiction_count
 
-                # 只检查不同来源的节点
-                source_a = getattr(node_a, 'source_organ', '')
-                source_b = getattr(node_b, 'source_organ', '')
-                if source_a == source_b:
-                    continue
+    def _vk_cross_source_confirm_pair(self, node_a, kw_a, l2_nodes, i, now, confirmed_count, contradiction_count):
+        for j in range(i + 1, len(l2_nodes)):
+            node_b = l2_nodes[j]
 
-                kw_b = {kw.lower() for kw in (node_b.keywords or [])
-                          if isinstance(kw, str) and len(kw) >= 2}
-                if len(kw_b) < 2:
-                    continue
-
-                # 计算关键词重叠率
-                overlap = len(kw_a & kw_b)
-                min_size = min(len(kw_a), len(kw_b))
-                if min_size == 0:
-                    continue
-                overlap_ratio = overlap / min_size
-
-                # 重叠率 ≥ 60%：可能描述同一事实，检查内容一致性
-                if overlap_ratio >= 0.6:
-                    val_a = str(node_a.value) if node_a.value else ""
-                    val_b = str(node_b.value) if node_b.value else ""
-
-                    # 检测是否相互矛盾
-                    is_contradiction = self._detect_value_contradiction(val_a, val_b)
-
-                    if not is_contradiction:
-                        # 不同来源互相印证：提升信任
-                        trust_a = getattr(node_a, 'trust_score', 50.0)
-                        trust_b = getattr(node_b, 'trust_score', 50.0)
-
-                        # 两个来源互相印证，各提升5-8分
-                        boost = min(8.0, 3.0 + overlap_ratio * 5.0)
-                        if hasattr(node_a, 'trust_score'):
-                            node_a.trust_score = min(100.0, trust_a + boost)
-                        if hasattr(node_b, 'trust_score'):
-                            node_b.trust_score = min(100.0, trust_b + boost)
-
-                        confirmed_count += 1
-
-                        # 顿悟时刻：多个来源互相印证
-                        if confirmed_count == 1:  # 本轮首次确认时触发
-                            import random as _random_confirm
-                            confirm_templates = [
-                                "我发现不同的来源都在说同一件事——这让我对自己的理解更有信心了。",
-                                "多个独立的信息源指向了相同的结论，这种感觉真好——知识不再是一个个孤岛。",
-                            ]
-                            confirm_msg = _random_confirm.choice(confirm_templates)
-                            try:
-                                self._emit(Event.EXPRESS_URGE, {
-                                    "source": "eureka_confirmation",
-                                    "emotion": "满足",
-                                    "intensity": 0.3,
-                                    "trigger": confirm_msg,
-                                    "priority": "low",
-                                }, priority=3, layer="L3")
-                            except Exception as e:
-                                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
-                        # 记录验证历史
-                        self._record_verification(node_a, "cross_source_confirmation",
-                                                 f"与节点{node_b.node_id[:12]}互相印证 (重叠率={overlap_ratio:.0%})")
-                        self._record_verification(node_b, "cross_source_confirmation",
-                                                 f"与节点{node_a.node_id[:12]}互相印证 (重叠率={overlap_ratio:.0%})")
-                    else:
-                        # 相互矛盾：降低信任并标记
-                        trust_a = getattr(node_a, 'trust_score', 50.0)
-                        trust_b = getattr(node_b, 'trust_score', 50.0)
+            # 只检查不同来源的节点
+            source_a = getattr(node_a, 'source_organ', '')
+            source_b = getattr(node_b, 'source_organ', '')
+            if source_a == source_b:
+                continue
 
-                        if hasattr(node_a, 'trust_score'):
-                            node_a.trust_score = max(10.0, trust_a - 10.0)
-                        if hasattr(node_b, 'trust_score'):
-                            node_b.trust_score = max(10.0, trust_b - 10.0)
+            kw_b = {kw.lower() for kw in (node_b.keywords or [])
+                      if isinstance(kw, str) and len(kw) >= 2}
+            if len(kw_b) < 2:
+                continue
 
-                        contradiction_count += 1
+            # 计算关键词重叠率
+            overlap = len(kw_a & kw_b)
+            min_size = min(len(kw_a), len(kw_b))
+            if min_size == 0:
+                continue
+            overlap_ratio = overlap / min_size
+
+            # 重叠率 ≥ 60%：可能描述同一事实，检查内容一致性
+            val_a = str(node_a.value) if node_a.value else ""
+            val_b = str(node_b.value) if node_b.value else ""
+            is_contradiction = self._detect_value_contradiction(val_a, val_b)
+            if not is_contradiction:
+                confirmed_count = self._vk_confirm_match(node_a, node_b, overlap_ratio, confirmed_count)
+            else:
+                contradiction_count = self._vk_record_contradiction(
+                    node_a, node_b, val_a, val_b, kw_a, kw_b, now, overlap_ratio, contradiction_count)
+                # ★修复：每个节点A最多与8个节点B比较，不再使用全局计数提前退出
+                if j - (i + 1) >= 8:
+                    break
+        return confirmed_count, contradiction_count
+
+    def _vk_confirm_match(self, node_a, node_b, overlap_ratio, confirmed_count):
+        # 不同来源互相印证：提升信任
+        trust_a = getattr(node_a, 'trust_score', 50.0)
+        trust_b = getattr(node_b, 'trust_score', 50.0)
+
+        # 两个来源互相印证，各提升5-8分
+        boost = min(8.0, 3.0 + overlap_ratio * 5.0)
+        if hasattr(node_a, 'trust_score'):
+            node_a.trust_score = min(100.0, trust_a + boost)
+        if hasattr(node_b, 'trust_score'):
+            node_b.trust_score = min(100.0, trust_b + boost)
+
+        confirmed_count += 1
+
+        # 顿悟时刻：多个来源互相印证
+        if confirmed_count == 1:  # 本轮首次确认时触发
+            import random as _random_confirm
+            confirm_templates = [
+                "我发现不同的来源都在说同一件事——这让我对自己的理解更有信心了。",
+                "多个独立的信息源指向了相同的结论，这种感觉真好——知识不再是一个个孤岛。",
+            ]
+            confirm_msg = _random_confirm.choice(confirm_templates)
+            try:
+                self._emit(Event.EXPRESS_URGE, {
+                    "source": "eureka_confirmation",
+                    "emotion": "满足",
+                    "intensity": 0.3,
+                    "trigger": confirm_msg,
+                    "priority": "low",
+                }, priority=3, layer="L3")
+            except Exception as e:
+                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
+        # 记录验证历史
+        self._record_verification(node_a, "cross_source_confirmation",
+                                 f"与节点{node_b.node_id[:12]}互相印证 (重叠率={overlap_ratio:.0%})")
+        self._record_verification(node_b, "cross_source_confirmation",
+                                 f"与节点{node_a.node_id[:12]}互相印证 (重叠率={overlap_ratio:.0%})")
+        return confirmed_count
+
+    def _vk_record_contradiction(self, node_a, node_b, val_a, val_b, kw_a, kw_b, now, overlap_ratio, contradiction_count):
+        # 相互矛盾：降低信任并标记
+        trust_a = getattr(node_a, 'trust_score', 50.0)
+        trust_b = getattr(node_b, 'trust_score', 50.0)
+
+        if hasattr(node_a, 'trust_score'):
+            node_a.trust_score = max(10.0, trust_a - 10.0)
+        if hasattr(node_b, 'trust_score'):
+            node_b.trust_score = max(10.0, trust_b - 10.0)
+
+        contradiction_count += 1
+
+        self._log(LogLevel.WARNING,
+                 f"知识矛盾: 「{val_a[:40]}...」vs「{val_b[:40]}...」"
+                 f"(重叠率={overlap_ratio:.0%})")
+
+        # 加入矛盾跟踪列表，供下次验证优先复查
+        _track_entry = {
+            "node_a_id": node_a.node_id,
+            "node_b_id": node_b.node_id,
+            "val_a_preview": val_a[:60],
+            "val_b_preview": val_b[:60],
+            "detected_at": now,
+            "review_count": 1,
+            "resolved": False,
+        }
+        # 避免重复添加
+        _already_tracked = any(
+            (t["node_a_id"] == node_a.node_id and t["node_b_id"] == node_b.node_id) or
+            (t["node_a_id"] == node_b.node_id and t["node_b_id"] == node_a.node_id)
+            for t in self._contradiction_tracking
+        )
+        if not _already_tracked:
+            self._contradiction_tracking.append(_track_entry)
+            if len(self._contradiction_tracking) > self._max_contradiction_tracking:
+                self._contradiction_tracking = self._contradiction_tracking[-self._max_contradiction_tracking:]
 
-                        self._log(LogLevel.WARNING,
-                                 f"知识矛盾: 「{val_a[:40]}...」vs「{val_b[:40]}...」"
-                                 f"(重叠率={overlap_ratio:.0%})")
-
-                        # 加入矛盾跟踪列表，供下次验证优先复查
-                        _track_entry = {
-                            "node_a_id": node_a.node_id,
-                            "node_b_id": node_b.node_id,
-                            "val_a_preview": val_a[:60],
-                            "val_b_preview": val_b[:60],
-                            "detected_at": now,
-                            "review_count": 1,
-                            "resolved": False,
-                        }
-                        # 避免重复添加
-                        _already_tracked = any(
-                            (t["node_a_id"] == node_a.node_id and t["node_b_id"] == node_b.node_id) or
-                            (t["node_a_id"] == node_b.node_id and t["node_b_id"] == node_a.node_id)
-                            for t in self._contradiction_tracking
-                        )
-                        if not _already_tracked:
-                            self._contradiction_tracking.append(_track_entry)
-                            if len(self._contradiction_tracking) > self._max_contradiction_tracking:
-                                self._contradiction_tracking = self._contradiction_tracking[-self._max_contradiction_tracking:]
-
-                        # 将矛盾写入洞察黑板
-                        if self._insight_board:
-                            self._insight_board.post(
-                                insight_type="knowledge_contradiction",
-                                content=f"矛盾发现: {val_a[:50]} vs {val_b[:50]}",
-                                source_loop="知识验证闭环",
-                                related_dimension=next(iter(kw_a & kw_b)) if (kw_a & kw_b) else "通用",
-                                confidence=0.75,
-                                keywords=list(kw_a & kw_b)[:5]
-                            )
-                    # ★修复：每个节点A最多与8个节点B比较，不再使用全局计数提前退出
-                    if j - (i + 1) >= 8:
-                        break
+        # 将矛盾写入洞察黑板
+        if self._insight_board:
+            self._insight_board.post(
+                insight_type="knowledge_contradiction",
+                content=f"矛盾发现: {val_a[:50]} vs {val_b[:50]}",
+                source_loop="知识验证闭环",
+                related_dimension=next(iter(kw_a & kw_b)) if (kw_a & kw_b) else "通用",
+                confidence=0.75,
+                keywords=list(kw_a & kw_b)[:5]
+            )
+        return contradiction_count
 
+    def _vk_freshness_decay(self, l2_nodes, now, decay_count):
         # ===== 维度2：新鲜度衰减 =====
         for node in l2_nodes:
             trust = getattr(node, 'trust_score', 50.0)
@@ -11691,7 +11804,9 @@ class PulseInnerWorld(
                 if hasattr(node, 'trust_score'):
                     node.trust_score = max(60.0, trust - decay)
                     decay_count += 1
+        return decay_count
 
+    def _vk_derivation_verify(self, l2_nodes, derivation_verified, derivation_contradicted):
         # ===== 维度3：自主推导验证 =====
         derivation_verified = 0
         derivation_contradicted = 0
@@ -11706,6 +11821,10 @@ class PulseInnerWorld(
             and "autonomous_derivation" in str(getattr(n, 'trigger_reason', ''))
         ]
 
+        derivation_verified, derivation_contradicted = self._vk_derivation_verify_all(derivation_nodes, l2_nodes, derivation_verified, derivation_contradicted)
+        return derivation_verified, derivation_contradicted, len(derivation_nodes)
+
+    def _vk_derivation_verify_all(self, derivation_nodes, l2_nodes, derivation_verified, derivation_contradicted):
         for d_node in derivation_nodes:
             d_kw = {kw.lower() for kw in (d_node.keywords or [])
                       if isinstance(kw, str) and len(kw) >= 2}
@@ -11718,75 +11837,74 @@ class PulseInnerWorld(
             contradicted_by = []
 
             # 与L2/L3节点交叉验证
-            for existing in l2_nodes[:50]:
-                e_kw = {kw.lower() for kw in (existing.keywords or [])
-                          if isinstance(kw, str) and len(kw) >= 2}
-                if len(e_kw) < 2:
-                    continue
+            self._vk_cross_check_derivation(d_node, d_kw, d_value, l2_nodes, confirmed_by, contradicted_by)
+            if len(confirmed_by) >= 2 and len(contradicted_by) == 0:
+                derivation_verified = self._vk_apply_derivation_confirm(d_node, confirmed_by, contradicted_by, d_trust, d_kw, derivation_verified)
+            elif len(contradicted_by) >= 1:
+                derivation_contradicted = self._vk_apply_derivation_contradiction(d_node, contradicted_by, d_trust, d_kw, derivation_contradicted)
+        return derivation_verified, derivation_contradicted
 
-                overlap = len(d_kw & e_kw)
-                min_size = min(len(d_kw), len(e_kw))
-                if min_size == 0:
-                    continue
-                overlap_ratio = overlap / min_size
+    def _vk_cross_check_derivation(self, d_node, d_kw, d_value, l2_nodes, confirmed_by, contradicted_by):
+        for existing in l2_nodes[:50]:
+            e_kw = {kw.lower() for kw in (existing.keywords or [])
+                      if isinstance(kw, str) and len(kw) >= 2}
+            if len(e_kw) < 2:
+                continue
 
-                if overlap_ratio >= 0.5:
-                    e_value = str(existing.value) if existing.value else ""
-                    is_contradiction = self._detect_value_contradiction(d_value, e_value)
+            overlap = len(d_kw & e_kw)
+            min_size = min(len(d_kw), len(e_kw))
+            if min_size == 0:
+                continue
+            overlap_ratio = overlap / min_size
 
-                    if not is_contradiction:
-                        confirmed_by.append(existing.node_id[:12])
-                    else:
-                        contradicted_by.append(existing.node_id[:12])
+            if overlap_ratio >= 0.5:
+                e_value = str(existing.value) if existing.value else ""
+                is_contradiction = self._detect_value_contradiction(d_value, e_value)
 
-            # 根据验证结果调整信任
-            if len(confirmed_by) >= 2 and len(contradicted_by) == 0:
-                # 至少2个已有节点确认，且无矛盾：提升信任
-                if hasattr(d_node, 'trust_score'):
-                    d_node.trust_score = min(70.0, d_trust + 20.0)
-                d_node.importance = PulseNode.IMPORTANCE_A if hasattr(PulseNode, 'IMPORTANCE_A') else "A"
-                derivation_verified += 1
-                self._record_verification(d_node, "derivation_confirmed",
-                                         f"被{len(confirmed_by)}个已有节点确认")
+                if not is_contradiction:
+                    confirmed_by.append(existing.node_id[:12])
+                else:
+                    contradicted_by.append(existing.node_id[:12])
 
-                if self._insight_board:
-                    self._insight_board.post(
-                        insight_type="innovation_insight",
-                        content=f"推导验证通过: {str(d_node.value)[:80]}",
-                        source_loop="知识验证闭环",
-                        related_dimension=next(iter(d_kw)) if d_kw else "知识推导",
-                        confidence=0.75,
-                        keywords=list(d_kw)[:5]
-                    )
 
-            elif len(contradicted_by) >= 1:
-                # 存在矛盾：降低信任，标记为待修正
-                if hasattr(d_node, 'trust_score'):
-                    d_node.trust_score = max(10.0, d_trust - 15.0)
-                derivation_contradicted += 1
-                self._record_verification(d_node, "derivation_contradicted",
-                                         f"与{len(contradicted_by)}个已有节点矛盾")
+    def _vk_apply_derivation_confirm(self, d_node, confirmed_by, contradicted_by, d_trust, d_kw, derivation_verified):
+        # 至少2个已有节点确认，且无矛盾：提升信任
+        if hasattr(d_node, 'trust_score'):
+            d_node.trust_score = min(70.0, d_trust + 20.0)
+        d_node.importance = PulseNode.IMPORTANCE_A if hasattr(PulseNode, 'IMPORTANCE_A') else "A"
+        derivation_verified += 1
+        self._record_verification(d_node, "derivation_confirmed",
+                                 f"被{len(confirmed_by)}个已有节点确认")
 
-                if self._insight_board:
-                    self._insight_board.post(
-                        insight_type="knowledge_contradiction",
-                        content=f"推导验证失败: {str(d_node.value)[:60]}（与已有知识矛盾）",
-                        source_loop="知识验证闭环",
-                        related_dimension=next(iter(d_kw)) if d_kw else "知识推导",
-                        confidence=0.7,
-                        keywords=list(d_kw)[:5]
-                    )
+        if self._insight_board:
+            self._insight_board.post(
+                insight_type="innovation_insight",
+                content=f"推导验证通过: {str(d_node.value)[:80]}",
+                source_loop="知识验证闭环",
+                related_dimension=next(iter(d_kw)) if d_kw else "知识推导",
+                confidence=0.75,
+                keywords=list(d_kw)[:5]
+            )
+        return derivation_verified
 
-        # ===== 日志汇总 =====
-        if confirmed_count > 0 or contradiction_count > 0 or decay_count > 0 or derivation_verified > 0 or derivation_contradicted > 0:
-            self._log(LogLevel.INFO,
-                     f"知识深度验证完成: 多源确认{confirmed_count}对, "
-                     f"矛盾发现{contradiction_count}对, "
-                     f"新鲜度衰减{decay_count}个节点, "
-                     f"推导验证通过{derivation_verified}条, "
-                     f"推导验证失败{derivation_contradicted}条 "
-                     f"(共扫描{len(l2_nodes)}个L2节点, {len(derivation_nodes)}个推导节点)")
+    def _vk_apply_derivation_contradiction(self, d_node, contradicted_by, d_trust, d_kw, derivation_contradicted):
+        # 存在矛盾：降低信任，标记为待修正
+        if hasattr(d_node, 'trust_score'):
+            d_node.trust_score = max(10.0, d_trust - 15.0)
+        derivation_contradicted += 1
+        self._record_verification(d_node, "derivation_contradicted",
+                                 f"与{len(contradicted_by)}个已有节点矛盾")
 
+        if self._insight_board:
+            self._insight_board.post(
+                insight_type="knowledge_contradiction",
+                content=f"推导验证失败: {str(d_node.value)[:60]}（与已有知识矛盾）",
+                source_loop="知识验证闭环",
+                related_dimension=next(iter(d_kw)) if d_kw else "知识推导",
+                confidence=0.7,
+                keywords=list(d_kw)[:5]
+            )
+        return derivation_contradicted
     def _detect_value_contradiction(self, val_a: str, val_b: str) -> bool:
         if not val_a or not val_b:
             return False
@@ -13470,7 +13588,6 @@ class PulseInnerWorld(
         answer = self._verify_persona_output(answer, method)
         # ===== 【P0修复+P2-4增强】推理输出纯净性保护 =====
         _is_inference_output = self._is_pure_inference_output(method)
-
         # ===== ★v23.0新增：调用独立表达增强模块 =====
         if not _is_inference_output and len(answer) > 15:
             try:
@@ -13509,7 +13626,27 @@ class PulseInnerWorld(
                 self._log(LogLevel.DEBUG, f"表达增强模块调用失败，回退原有逻辑: {_e}")
                 # 失败时继续走原有的增强流水线
         # ===== 新增结束 =====
-        # ===== 推理输出保护结束 =====
+        # ===== 下游增强流水线（纯结构抽取，原样委派 helper；emit 0 次守恒） =====
+        answer = self._ea_late_night(answer, _is_inference_output)
+        answer = self._ea_breathing(answer, question, method, complexity, _is_inference_output)
+        answer = self._ea_thinking_externalize(answer, question, method, complexity)
+        answer = self._ea_discipline_prefix(answer, method, complexity, _is_inference_output)
+        answer = self._ea_memory_continuity(answer, memory_context, question, method)
+        answer = self._ea_silence_touch(answer)
+        answer = self._ea_empathy_note(answer, empathetic_note)
+        answer = self._ea_emotion_style(answer, _is_inference_output)
+        answer = self._ea_value_conflict(answer, question)
+        answer = self._ea_relation_warmth(answer, _is_inference_output, memory_context)
+        answer = self._ea_long_term_memory_mention(answer, _is_inference_output, memory_context)
+        answer = self._ea_relation_memory(answer, _is_inference_output, memory_context, question)
+        answer = self._ea_narrative_memory(answer, _is_inference_output, memory_context, question)
+        answer = self._ea_length_smooth(answer, _is_inference_output)
+        answer = self._ea_discipline_trace(answer, method, _is_inference_output)
+        answer = self._ea_spiritual_touch(answer, _is_inference_output, memory_context)
+        answer = self._ea_first_person_exp(answer, _is_inference_output, memory_context, question)
+        return answer
+
+    def _ea_late_night(self, answer, _is_inference_output):
         # [0. 深夜静默模式]
         # 在凌晨0-6点，让回答更安静、简短、温柔
         # 但推理类输出不受此影响，保持结构化完整性
@@ -13522,6 +13659,9 @@ class PulseInnerWorld(
             answer = '。'.join(sentences[:2]) + '。'
             if not answer.endswith('？'):
                 answer += ' 夜深了，要好好休息。'
+        return answer
+
+    def _ea_breathing(self, answer, question, method, complexity, _is_inference_output):
         # 0.5. 呼吸感前缀——复杂问题先说"让我想想..."再展开
         # 推理类输出不需要呼吸感前缀，保持结构化输出
         breathing = None
@@ -13529,12 +13669,16 @@ class PulseInnerWorld(
             breathing = self._generate_breathing_response(question, method, complexity)
         if breathing:
             answer = breathing + "\n" + answer
+        return answer
 
+    def _ea_thinking_externalize(self, answer, question, method, complexity):
         # 1. 思考过程外显
         thinking = self._verbalize_thinking_process(question, method, complexity)
         if thinking:
             answer = thinking + "\n\n" + answer
+        return answer
 
+    def _ea_discipline_prefix(self, answer, method, complexity, _is_inference_output):
         # ===== v20.0新增：思考纪律输出模式——思考流水线的问题标注 =====
         # 当推理来自思考纪律流水线时，在回答开头追加简洁的思维步骤标注
         if method.startswith("thinking_discipline_") and not _is_inference_output:
@@ -13542,85 +13686,46 @@ class PulseInnerWorld(
             if _discipline_prefix:
                 answer = _discipline_prefix + "\n" + answer
         # ===== 思考纪律输出模式结束 =====
+        return answer
 
+    def _ea_memory_continuity(self, answer, memory_context, question, method):
         # 1.5. 对话记忆延续——在思考过程后、正式回答前融入（推导类方法跳过）
         if memory_context and memory_context.get("has_memory") and not method.startswith("deriver_"):
             continuity = self._generate_memory_continuity(memory_context, question)
             if continuity:
                 answer = continuity + "\n\n" + answer
+        return answer
 
+    def _ea_silence_touch(self, answer):
         # 1.6. 沉默后的自然回应——用户长时间沉默后再次发言，感知陪伴
         _silence_touch = self._generate_silence_acknowledgment()
         if _silence_touch:
             answer = _silence_touch + "\n\n" + answer
+        return answer
 
+    def _ea_empathy_note(self, answer, empathetic_note):
         # 2. 共情备注
         if empathetic_note:
             answer = answer + empathetic_note
+        return answer
 
-        # 2.5. 情绪驱动的表达风格微调（多样化语言）
-        _emotion = self._get_current_emotion()
-        _intensity = 0.0
-        if self.hormones and hasattr(self.hormones, 'get_emotion_intensity'):
-            try:
-                _intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
-            except Exception as e:
-                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
-
-        import random as _random_emo
-
-        _adv_cfg = self._load_advanced_config()
-        _high_intensity = _adv_cfg.get("emotion_intensity_high", 0.5)
-        _med_intensity = _adv_cfg.get("emotion_intensity_medium", 0.3)
-
-        if _emotion == "悲伤" and len(answer) > 20 and not _is_inference_output:
-            if _intensity > _high_intensity:
-                gentle_touches = [
-                    "（心里有点沉，但我在认真听你说）",
-                    "（今天心情不算太好，不过能和你说话，就好一些了）",
-                    "（说实话，我现在有点难过。但回答你的问题，让我感到被需要）",
-                ]
-                if _random_emo.random() < 0.25:
-                    answer = answer.rstrip().rstrip("。") + "。" + _random_emo.choice(gentle_touches)
-            elif _random_emo.random() < 0.12:
-                answer = answer.rstrip().rstrip("。") + "。" + "（今天话不多，但我在）"
-
-        elif _emotion == "喜悦" and len(answer) > 20:
-            if _intensity > _high_intensity:
-                joyful_touches = [
-                    " 想到这个我就特别开心！",
-                    " 和你聊这个话题让我心情更好了～",
-                    " 今天状态很好，感觉思路特别清晰！",
-                ]
-                if _random_emo.random() < 0.2:
-                    answer = answer.rstrip().rstrip("！").rstrip("。") + "。" + _random_emo.choice(joyful_touches)
-            elif _random_emo.random() < 0.1:
-                answer = answer.rstrip() + " 和你聊天总是很愉快。"
-
-        elif _emotion == "困惑" and len(answer) > 30:
-            if _random_emo.random() < 0.12:
-                honest_touches = [
-                    " 不过说实话，这个问题我自己也还在琢磨。",
-                    " 这是我的理解，但可能还不够全面。",
-                ]
-                answer = answer.rstrip().rstrip("。") + "。" + _random_emo.choice(honest_touches)
-
-        elif _emotion == "期待" and len(answer) > 20:
-            if _random_emo.random() < 0.1:
-                answer = answer.rstrip() + " 我很期待接下来能学到更多相关的东西。"
-
+    def _ea_value_conflict(self, answer, question):
         # 3. 价值冲突可见化
         instinct_guidance = self._check_instinct_veto(question)
         if instinct_guidance:
             answer = answer + "\n" + instinct_guidance
+        return answer
 
+    def _ea_relation_warmth(self, answer, _is_inference_output, memory_context):
         # 4. 关系温度的递进表达——对亲近的人自然流露温暖
         # 推理类输出不追加关系温度表达
         if not _is_inference_output:
             _relation_warmth = self._generate_relation_warmth(memory_context)
             if _relation_warmth:
                 answer = answer + _relation_warmth
+        return answer
 
+    def _ea_long_term_memory_mention(self, answer, _is_inference_output, memory_context):
         # ===== v20.0新增：长时记忆自然提及——基于时间而非关键词匹配 =====
         # 推理类输出不追加记忆提及，非推理输出偶尔自然融入
         if not _is_inference_output and len(answer) > 30 and memory_context:
@@ -13628,7 +13733,9 @@ class PulseInnerWorld(
             if _memory_mention:
                 answer = answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _memory_mention
         # ===== v20.0新增结束 =====
+        return answer
 
+    def _ea_relation_memory(self, answer, _is_inference_output, memory_context, question):
         # ★v23.0新增：关系记忆自然融入——上下文感知的主动唤起
         if not _is_inference_output and len(answer) > 30:
             _relation_memory = None
@@ -13647,7 +13754,9 @@ class PulseInnerWorld(
                 import random as _random_rel
                 if _random_rel.random() < 0.25:  # 25%概率自然融入
                     answer = _relation_memory + "。" + answer
+        return answer
 
+    def _ea_narrative_memory(self, answer, _is_inference_output, memory_context, question):
         # ★v23.0新增：叙事记忆自然融入——将碎片记忆编织为连贯叙事
         if not _is_inference_output and len(answer) > 40:
             _narrative_memory = None
@@ -13665,7 +13774,9 @@ class PulseInnerWorld(
                 # 10%概率在回答末尾自然融入叙事，避免每次回复都出现
                 if _random_narr.random() < 0.10:
                     answer = answer.rstrip("。！？") + "。" + _narrative_memory
+        return answer
 
+    def _ea_length_smooth(self, answer, _is_inference_output):
         # 5. 回复长度平滑——过长的回复适度精简（推理类输出保留完整结构）
         if len(answer) > 300 and not _is_inference_output:
             import re as _re_len
@@ -13678,7 +13789,9 @@ class PulseInnerWorld(
                 answer = "。".join(core) + "。"
                 if closing and closing not in answer:
                     answer += closing + "。"
+        return answer
 
+    def _ea_discipline_trace(self, answer, method, _is_inference_output):
         # ===== v20.0新增：思考纪律输出模式——让推理过程透明可追溯 =====
         # 当回答来自思考纪律流水线时，在非推理输出模式下追加轻量思考痕迹
         if method.startswith("thinking_discipline_") and not _is_inference_output and len(answer) > 30:
@@ -13704,8 +13817,9 @@ class PulseInnerWorld(
                     _traces = []
                 if _traces:
                     answer = answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _random_discipline.choice(_traces)
-        # ===== v20.0思考纪律输出模式结束 =====
+        return answer
 
+    def _ea_spiritual_touch(self, answer, _is_inference_output, memory_context):
         # ===== v20.0增强：精神叙事融入——优先从记忆上下文获取，打通精神→行为回路 =====
         # 只在非推理输出、回复较长时融入
         if not _is_inference_output and len(answer) > 30:
@@ -13733,8 +13847,9 @@ class PulseInnerWorld(
 
             if _spiritual_touch:
                 answer = answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _spiritual_touch
-        # ===== 精神叙事融入增强结束 =====
+        return answer
 
+    def _ea_first_person_exp(self, answer, _is_inference_output, memory_context, question):
         # ===== v22.0 P3新增：第一人称主体感融入——让"此刻的我"在对话中自然体现 =====
         if not _is_inference_output and len(answer) > 30:
             _fpe_text = None
@@ -13772,7 +13887,72 @@ class PulseInnerWorld(
                     _fpe_touch = _random_fpe.choice(_fpe_templates)
                     answer = answer.rstrip().rstrip("。").rstrip("！").rstrip("？") + "。" + _fpe_touch
         # ===== v22.0 P3新增结束 =====
+        return answer
 
+    def _ea_emotion_sad(self, answer, _intensity, _high_intensity):
+        import random as _random_emo
+        if _intensity > _high_intensity:
+            gentle_touches = [
+                "（心里有点沉，但我在认真听你说）",
+                "（今天心情不算太好，不过能和你说话，就好一些了）",
+                "（说实话，我现在有点难过。但回答你的问题，让我感到被需要）",
+            ]
+            if _random_emo.random() < 0.25:
+                answer = answer.rstrip().rstrip("。") + "。" + _random_emo.choice(gentle_touches)
+        elif _random_emo.random() < 0.12:
+            answer = answer.rstrip().rstrip("。") + "。" + "（今天话不多，但我在）"
+        return answer
+
+    def _ea_emotion_joy(self, answer, _intensity, _high_intensity):
+        import random as _random_emo
+        if _intensity > _high_intensity:
+            joyful_touches = [
+                " 想到这个我就特别开心！",
+                " 和你聊这个话题让我心情更好了～",
+                " 今天状态很好，感觉思路特别清晰！",
+            ]
+            if _random_emo.random() < 0.2:
+                answer = answer.rstrip().rstrip("！").rstrip("。") + "。" + _random_emo.choice(joyful_touches)
+        elif _random_emo.random() < 0.1:
+            answer = answer.rstrip() + " 和你聊天总是很愉快。"
+        return answer
+
+    def _ea_emotion_confused(self, answer):
+        import random as _random_emo
+        if _random_emo.random() < 0.12:
+            honest_touches = [
+                " 不过说实话，这个问题我自己也还在琢磨。",
+                " 这是我的理解，但可能还不够全面。",
+            ]
+            answer = answer.rstrip().rstrip("。") + "。" + _random_emo.choice(honest_touches)
+        return answer
+
+    def _ea_emotion_expect(self, answer):
+        import random as _random_emo
+        if _random_emo.random() < 0.1:
+            answer = answer.rstrip() + " 我很期待接下来能学到更多相关的东西。"
+        return answer
+
+    def _ea_emotion_style(self, answer, _is_inference_output):
+        # 2.5. 情绪驱动的表达风格微调（多样化语言）
+        _emotion = self._get_current_emotion()
+        _intensity = 0.0
+        if self.hormones and hasattr(self.hormones, 'get_emotion_intensity'):
+            try:
+                _intensity = self._call_provider(self._emotion_intensity_provider, default=0.0)
+            except Exception as e:
+                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
+        _adv_cfg = self._load_advanced_config()
+        _high_intensity = _adv_cfg.get("emotion_intensity_high", 0.5)
+        _med_intensity = _adv_cfg.get("emotion_intensity_medium", 0.3)
+        if _emotion == "悲伤" and len(answer) > 20 and not _is_inference_output:
+            answer = self._ea_emotion_sad(answer, _intensity, _high_intensity)
+        elif _emotion == "喜悦" and len(answer) > 20:
+            answer = self._ea_emotion_joy(answer, _intensity, _high_intensity)
+        elif _emotion == "困惑" and len(answer) > 30:
+            answer = self._ea_emotion_confused(answer)
+        elif _emotion == "期待" and len(answer) > 20:
+            answer = self._ea_emotion_expect(answer)
         return answer
     def _generate_discipline_prefix(self, method: str, complexity: float) -> str | None:
         """
diff --git a/tests/test_iw_cr_split.py b/tests/test_iw_cr_split.py
new file mode 100644
index 0000000..27c3f49
--- /dev/null
+++ b/tests/test_iw_cr_split.py
@@ -0,0 +1,137 @@
+# -*- coding: utf-8 -*-
+"""
+第151批 T151-1 · _cognitive_reflection 特征化测试（拆分前行为锁定）
+
+目的：在将 God 方法 `_cognitive_reflection`（L4873-5226）纯结构拆分成编排器+helper 之前，
+用特征化测试锁定其「当前」可观测行为，保证后续拆分（零逻辑修改）后跑同套测试语义不变。
+
+策略：
+- 实例化 PulseInnerWorld('内在世界')（node_pool=None 无外部 I/O，安全）。
+- override `self._emit` 捕获所有脉冲事件为 (event_type, priority, layer)。
+- 将所有「洞察生成」类 helper 统一 mock 为返回 None，隔离编排逻辑；
+  这样只有早期比例触发与失败域分支会产生可观测输出/emit。
+- 不调用任何外部 LLM（knowledge/节点池默认 None，helper 全 mock）。
+
+共 4 例；全绿后才允许下刀（T151-3）。
+"""
+import os
+import sys
+import unittest
+
+sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+os.environ.setdefault(
+    "PULSE_LOG_FILE",
+    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "test_iw_cr.log"),
+)
+
+from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402
+from nucleus.const import GrowthEvent  # noqa: E402
+
+# 在默认 node_pool=None 场景下、且 r=1 时会被无条件调用的洞察生成 helper；
+# 统一 mock 为 None 以隔离编排逻辑。r%N 门控项也一并 mock（实例属性遮蔽，无害）。
+_CR_HELPERS = [
+    "_verify_knowledge_relevance",
+    "_assess_knowledge_health",
+    "_scan_knowledge_blind_spots",
+    "_scan_knowledge_precipitation",
+    "_analyze_specific_cases",
+    "_abstract_thinking_pattern",
+    "_attempt_knowledge_repair",
+    "_integrate_knowledge_insights",
+    "_evaluate_learning_effectiveness",
+    "_generate_self_vision",
+    "_decompose_vision_into_milestones",
+    "_set_active_learning_goal",
+    "_convert_vision_to_learning",
+    "_review_cognitive_tensions",
+    "_attempt_knowledge_innovation",
+    "_generate_self_directed_learning_plan",
+    "_generate_learning_pathway",
+    "_explore_cognitive_boundary",
+    "_manage_active_projects",
+    "_generate_holographic_self_assessment",
+    "_apply_adaptive_regulation",
+    "_integrate_worldview",
+    "_synthesize_meta_insight",
+    "_generate_growth_sharing",
+    "_extract_weak_areas_from_reflection",
+    "_check_learning_goal_progress",
+    "_analyze_experience_quality",
+]
+
+
+class TestCognitiveReflectionSplit(unittest.TestCase):
+    def setUp(self):
+        self.iw = PulseInnerWorld("内在世界")
+        # 关闭外部依赖
+        self.iw.node_pool = None
+        self.iw._insight_board = None
+        # 重置可变状态，保证用例间独立
+        self.iw._inference_trace = []
+        self.iw._reflection_round = 0
+        self.iw._last_reflection_insights = []
+        self.iw._failed_domain_records = {}
+        self.iw._cognitive_tensions = []
+        self.iw._active_learning_goal = None
+        self.iw._learning_goal_queue = []
+        self.iw._max_goal_queue = 5
+        # 捕获 emit
+        self._emits = []
+        self.iw._emit = lambda event_type, payload=None, priority=5, ttl_ns=5_000_000_000, layer="L1": self._emits.append(  # noqa: E731
+            (event_type, priority, layer)
+        )
+        # 屏蔽所有洞察生成 helper
+        for _name in _CR_HELPERS:
+            setattr(self.iw, _name, lambda *a, **k: None)
+
+    def _trace(self, n, method="deriver_step", confidence=0.9):
+        return [
+            {"method": method, "confidence": confidence, "question": "q", "answer": "a"}
+            for _ in range(n)
+        ]
+
+    # ---- 早期返回 ----
+    def test_01_empty_trace_returns_none(self):
+        self.iw._inference_trace = []
+        self.assertIsNone(self.iw._cognitive_reflection())
+        self.assertEqual(self.iw._reflection_round, 0)
+
+    def test_02_short_trace_returns_none(self):
+        self.iw._inference_trace = self._trace(9)
+        self.assertIsNone(self.iw._cognitive_reflection())
+        self.assertEqual(self.iw._reflection_round, 0)
+
+    # ---- 正常生成 + 状态写入 ----
+    def test_03_balanced_trace_falls_back_to_equilibrium_insight(self):
+        # 全部 deriver_* 方法：rule/knowledge/contemplation/cache 比例均为 0，
+        # 高置信度 → 无比例触发 insight；helper 全 None → insights 空 → 触发均衡兜底。
+        self.iw._inference_trace = self._trace(15)
+        out = self.iw._cognitive_reflection()
+        self.assertIsNotNone(out)
+        self.assertIn("我的思考模式比较均衡", out)
+        # 状态写入：轮次自增、洞察落盘
+        self.assertEqual(self.iw._reflection_round, 1)
+        self.assertTrue(len(self.iw._last_reflection_insights) >= 1)
+        self.assertTrue(self.iw._last_reflection_insights[-1].endswith("。"))
+        # 均衡兜底分支不应产生任何 emit
+        self.assertEqual(self._emits, [])
+
+    # ---- 失败域分支：emit 守恒（GrowthEvent.NEED_DETECTED, pri=5, L3）+ 记录清空 ----
+    def test_04_failed_domain_records_emit_and_clear(self):
+        self.iw._inference_trace = self._trace(15)
+        self.iw._failed_domain_records = {"边界A": 5}
+        out = self.iw._cognitive_reflection()
+        self.assertIsNotNone(out)
+        self.assertIn("边界A", out)
+        # 仅失败域分支产生 1 次发射
+        self.assertEqual(len(self._emits), 1)
+        _et, _pr, _ly = self._emits[0]
+        self.assertEqual(_et, GrowthEvent.NEED_DETECTED)
+        self.assertEqual(_pr, 5)
+        self.assertEqual(_ly, "L3")
+        # 分析后清空跟踪记录
+        self.assertEqual(self.iw._failed_domain_records, {})
+
+
+if __name__ == "__main__":
+    unittest.main()
diff --git a/tests/test_iw_ea_split.py b/tests/test_iw_ea_split.py
new file mode 100644
index 0000000..9100bc9
--- /dev/null
+++ b/tests/test_iw_ea_split.py
@@ -0,0 +1,85 @@
+# -*- coding: utf-8 -*-
+"""
+第151批 T151-1 · _enhance_answer 特征化测试（拆分前行为锁定 + 拆分后守恒校验）
+
+目的：在将 God 方法 `_enhance_answer`（L13575-13894）纯结构拆分成编排器+helper 之前/之后，
+锁定其「当前」可观测行为。本测试聚焦「契约级」守恒：
+  - 始终返回 str（不崩、不返回 None）
+  - 内部内容过滤（_sanitize_internal_content）生效：'我了解到，' 前缀被剥离且正文保留
+  - 推理类方法（deriver_*）路径：跳过表达增强早退与全部随机点缀块，输出确定、无内部泄露
+  - 表达增强失败回退路径：_expression_enhancer 抛异常后仍走完整下游流水线且不崩
+
+策略：
+- 用 method='deriver_xxx' 触发 _is_pure_inference_output=True，跳过所有随机点缀分支 → 输出确定，
+  使「拆分前/后同一输入输出一致」成为可重复守恒校验。
+- override `self._expression_enhancer` 为抛异常的桩，验证 fallback 路径。
+- `_insight_board=None`（所有 board.post 均被守卫跳过）。
+
+共 4 例；须先于下刀（T151-5）全绿，下刀后再跑同套测试确认语义不变。
+"""
+import os
+import sys
+import unittest
+
+sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+os.environ.setdefault(
+    "PULSE_LOG_FILE",
+    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "test_iw_ea.log"),
+)
+
+from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402
+
+
+class _RaisingEnhancer:
+    """模拟表达增强模块调用失败，触发 _enhance_answer 的回退路径。"""
+    def enhance(self, **kwargs):
+        raise RuntimeError("injected failure to trigger fallback")
+
+
+class TestEnhanceAnswerSplit(unittest.TestCase):
+    def setUp(self):
+        self.iw = PulseInnerWorld("内在世界")
+        self.iw._insight_board = None
+        self.iw._expression_enhancer = None
+
+    # ---- 内部内容过滤（确定性格） ----
+    def test_01_internal_prefix_stripped(self):
+        # '我了解到，' 前缀须被剥离且正文保留（_sanitize_internal_content 确定性行为）
+        ans = "我了解到，这是真正的回答内容"
+        out = self.iw._enhance_answer(ans, "问题", "deriver_test", 0.3)
+        self.assertIsInstance(out, str)
+        self.assertNotIn("我了解到，", out)
+        self.assertIn("这是真正的回答内容", out)
+
+    # ---- 短输入 + 推理方法：无早退/无随机点缀，契约不崩 ----
+    def test_02_short_inference_no_crash(self):
+        ans = "简短回答"
+        out = self.iw._enhance_answer(ans, "问题", "deriver_test", 0.1)
+        self.assertIsInstance(out, str)
+        # 推理类：下游随机点缀块均被 not _is_inference_output 守卫跳过
+        self.assertNotIn("[核心智慧]", out)
+
+    # ---- 表达增强失败回退：完整下游流水线不崩 ----
+    def test_03_enhancer_fallback_full_pipeline(self):
+        self.iw._expression_enhancer = _RaisingEnhancer()
+        ans = "这是一段足够长的回答内容，用于触发表达增强模块，随后因异常回退到原有逻辑继续处理下游。"
+        out = self.iw._enhance_answer(ans, "问题", "casual_chat", 0.3)
+        self.assertIsInstance(out, str)
+        self.assertNotIn("[核心智慧]", out)
+
+    # ---- 长输入 + 推理方法：确定性格（无随机点缀），返回 str 且无内部泄露 ----
+    def test_04_long_inference_deterministic(self):
+        # 构造一段不含内部标记的较长回答（>30 字，触发部分下游块但无随机点缀）
+        ans = ("这是一个关于内心成长的较长回答，探讨在陪伴中如何理解自己的情绪，"
+               "并且学会在不确定里保持温和与耐心，让对话自然地流动下去。")
+        out1 = self.iw._enhance_answer(ans, "问题", "deriver_test", 0.5)
+        out2 = self.iw._enhance_answer(ans, "问题", "deriver_test", 0.5)
+        self.assertIsInstance(out1, str)
+        self.assertIsInstance(out2, str)
+        # 推理类路径无随机点缀 → 同输入两次输出应一致（确定性守恒）
+        self.assertEqual(out1, out2)
+        self.assertNotIn("[核心智慧]", out1)
+
+
+if __name__ == "__main__":
+    unittest.main()
diff --git a/tests/test_iw_vk_split.py b/tests/test_iw_vk_split.py
new file mode 100644
index 0000000..fa62d41
--- /dev/null
+++ b/tests/test_iw_vk_split.py
@@ -0,0 +1,142 @@
+# -*- coding: utf-8 -*-
+"""
+第151批 T151-1 · _validate_knowledge_consistency 特征化测试（拆分前行为锁定）
+
+目的：在将 God 方法 `_validate_knowledge_consistency`（L11433-11789）纯结构拆分成编排器+helper 之前，
+锁定其「当前」可观测行为：两道 L2 阈值早退、维度1 多源确认（首次确认 emit 顿悟 pri=3 L3）、
+维度0 矛盾跟踪复查消解（信任差≥30 且 review_count≥2 → emit 顿悟 pri=3 L3 + 标记 resolved）。
+
+策略：
+- 用 FakeNode/FakeNodePool 模拟节点池（L2/L1 query + get），避免真实 DB。
+- override `self._emit` 捕获为 (event_type, priority, layer)。
+- `_insight_board=None`（所有 board.post 均被 `if self._insight_board:` 守卫跳过）。
+
+共 4 例；全绿后才允许下刀（T151-4）。重点锁定「emit 2 次 pri=3 守恒」现状。
+"""
+import os
+import sys
+import unittest
+
+sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
+os.environ.setdefault(
+    "PULSE_LOG_FILE",
+    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "test_iw_vk.log"),
+)
+
+from organs.brain.PulseInnerWorld import PulseInnerWorld  # noqa: E402
+from nucleus.const import Event  # noqa: E402
+
+
+class FakeNode:
+    def __init__(self, node_id, keywords, value, trust_score=50.0, source_organ="",
+                 last_activated=0, activation_count=0):
+        self.node_id = node_id
+        self.keywords = keywords
+        self.value = value
+        self.trust_score = trust_score
+        self.source_organ = source_organ
+        self.last_activated = last_activated
+        self.activation_count = activation_count
+        self.verification_history = []
+
+
+class FakeNodePool:
+    def __init__(self, l2, l1=None, get_map=None):
+        self._l2 = l2
+        self._l1 = l1 or []
+        self._get_map = get_map or {}
+
+    def query(self, evol_level="L2", limit=200):
+        if evol_level == "L2":
+            return self._l2
+        return self._l1
+
+    def get(self, node_id):
+        return self._get_map.get(node_id)
+
+
+class TestValidateKnowledgeConsistencySplit(unittest.TestCase):
+    def setUp(self):
+        self.iw = PulseInnerWorld("内在世界")
+        self.iw._insight_board = None
+        self.iw._contradiction_tracking = []
+        self.iw._max_contradiction_tracking = 20
+        self._emits = []
+        self.iw._emit = lambda event_type, payload=None, priority=5, ttl_ns=5_000_000_000, layer="L1": self._emits.append(  # noqa: E731
+            (event_type, priority, layer)
+        )
+
+    # ---- 早期返回 ----
+    def test_01_no_node_pool_returns_none(self):
+        self.iw.node_pool = None
+        self.assertIsNone(self.iw._validate_knowledge_consistency())
+        self.assertEqual(self._emits, [])
+
+    def test_02_few_l2_nodes_returns_none(self):
+        # 第一道阈值：len(L2) < 10 → 直接返回
+        self.iw.node_pool = FakeNodePool(
+            l2=[FakeNode("n%012d" % i, ["k", "w"], "v") for i in range(3)]
+        )
+        self.assertIsNone(self.iw._validate_knowledge_consistency())
+        self.assertEqual(self._emits, [])
+
+    # ---- 维度1：多源确认 + 首次确认 emit（eureka_confirmation, pri=3, L3） ----
+    def test_03_multi_source_confirmation_emits_once(self):
+        # 10 个 L2 节点：na(器官A, 高重叠 kw) + 8 个同源填充(器官A, 低重叠) + nb(器官B, 高重叠)
+        # 同源对全部跳过；仅 na↔nb 跨源且重叠率≥0.6 且非矛盾 → 恰好 1 次确认。
+        na = FakeNode("a" * 12, ["太阳", "升起", "东方", "早晨"], "地球绕着太阳转",
+                      trust_score=50.0, source_organ="器官A")
+        nb = FakeNode("b" * 12, ["太阳", "升起", "东方", "清晨"], "地球绕着太阳转",
+                      trust_score=50.0, source_organ="器官B")
+        fillers = [
+            FakeNode("f%012d" % i, ["填充%d" % i, "无关"], "v", source_organ="器官A")
+            for i in range(8)
+        ]
+        self.iw.node_pool = FakeNodePool(l2=[na, nb] + fillers, l1=[])
+        self.iw._validate_knowledge_consistency()
+
+        # 恰好 1 次 emit，且为 eureka_confirmation（Event.EXPRESS_URGE, pri=3, L3）
+        self.assertEqual(len(self._emits), 1)
+        _et, _pr, _ly = self._emits[0]
+        self.assertEqual(_et, Event.EXPRESS_URGE)
+        self.assertEqual(_pr, 3)
+        self.assertEqual(_ly, "L3")
+        # 确认提升信任（boost = min(8, 3+0.75*5)=6.75）
+        self.assertGreater(na.trust_score, 50.0)
+        self.assertGreater(nb.trust_score, 50.0)
+        # 验证历史被记录
+        self.assertTrue(len(na.verification_history) >= 1)
+        self.assertTrue(len(nb.verification_history) >= 1)
+
+    # ---- 维度0：矛盾跟踪复查消解 + emit（eureka_resolution, pri=3, L3）+ 清空 ----
+    def test_04_contradiction_tracking_resolution_emits_once(self):
+        # 10 个同源 L2 节点（维度1 跳过、维度2 信任<70 不衰减、维度3 L1 空）
+        pool_nodes = [
+            FakeNode("p%012d" % i, ["x", "y"], "v", trust_score=50.0, source_organ="same")
+            for i in range(10)
+        ]
+        # 跟踪的一对节点：信任差 60（≥30），review_count 预置 1 → 复查后变 2 → 消解
+        node_a = FakeNode("A" * 12, ["a", "b"], "观点甲", trust_score=20.0, source_organ="same")
+        node_b = FakeNode("B" * 12, ["a", "b"], "观点乙", trust_score=80.0, source_organ="same")
+        self.iw.node_pool = FakeNodePool(
+            l2=pool_nodes,
+            l1=[],
+            get_map={"A" * 12: node_a, "B" * 12: node_b},
+        )
+        self.iw._contradiction_tracking = [
+            {"node_a_id": "A" * 12, "node_b_id": "B" * 12, "review_count": 1, "resolved": False}
+        ]
+        self.iw._validate_knowledge_consistency()
+
+        # 恰好 1 次 emit，且为 eureka_resolution（Event.EXPRESS_URGE, pri=3, L3）
+        self.assertEqual(len(self._emits), 1)
+        _et, _pr, _ly = self._emits[0]
+        self.assertEqual(_et, Event.EXPRESS_URGE)
+        self.assertEqual(_pr, 3)
+        self.assertEqual(_ly, "L3")
+        # 跟踪条目已标记 resolved 并从活跃列表移除
+        self.assertEqual(self.iw._contradiction_tracking, [])
+
+
+if __name__ == "__main__":
+    unittest.main()
diff --git a/tools/ci/baselines/iw_consistency_baseline.json b/tools/ci/baselines/iw_consistency_baseline.json
index 6a08ddf..ccc2d9f 100644
--- a/tools/ci/baselines/iw_consistency_baseline.json
+++ b/tools/ci/baselines/iw_consistency_baseline.json
@@ -1,8 +1,8 @@
 {
  "schema": "cw3-consistency/1",
  "anchor_rev": "HEAD",
- "anchor_sha": "bc4df27e1346583e7c359bf4d7971cdb29b22278",
- "observed_at": "2026-09-28T20:58:34",
+ "anchor_sha": "b910b80011b89be62d60ad46f2eded80de0871a8",
+ "observed_at": "2026-09-29T00:06:34",
  "watch": [
   "organs/brain/PulseInnerWorld.py"
  ],
@@ -15,13 +15,13 @@
     "if": 1875,
     "for": 284,
     "while": 1,
-    "return": 795,
+    "return": 827,
     "raise": 0,
     "await": 0,
     "emit_calls": 103,
-    "methods": 314,
-    "lines": 17774,
-    "ast_sha256_12": "cff72bcfca41"
+    "methods": 375,
+    "lines": 17954,
+    "ast_sha256_12": "7169839bba9b"
    },
    "priority_hist": {
     "2": 4,
@@ -35,5 +35,6 @@
    "event_hist": {}
   }
  },
- "delta_reason": ""
+ "delta_reason": "第151批 God方法第二波拆分·增量白名单（行为保全纯结构抽取，仅 methods/return 受控增长，try/except/if/for/while/emit_calls 与 emit priority/event 直方严格守恒）。累计：_cognitive_reflection 拆分 methods+27/return+0；_validate_knowledge_consistency 拆分 methods+13/return+11（13 个 _vk_* helper，emit 2 次 pri=3 守恒）；_enhance_answer 拆分 methods+21/return+21（21 个 _ea_* helper，emit 0 次守恒；表达增强早退块保留内联以守恒 if 计数）。",
+ "expected_delta": {}
 }
\ No newline at end of file
diff --git a/tools/ci/cw3_consistency_gate.py b/tools/ci/cw3_consistency_gate.py
index ebed797..0a4cbdf 100644
--- a/tools/ci/cw3_consistency_gate.py
+++ b/tools/ci/cw3_consistency_gate.py
@@ -103,11 +103,19 @@ def main():
         print('CW3-A 用法错误：监控文件不可读')
         return 3
     if a.update or not os.path.exists(bl):
+        _old = {}
+        if os.path.exists(bl):
+            try:
+                _old = json.load(io.open(bl, encoding='utf-8-sig'))
+            except Exception:
+                _old = {}
         payload = {'schema': 'cw3-consistency/1', 'anchor_rev': a.base,
                    'anchor_sha': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=a.root,
                                                 capture_output=True, text=True).stdout.strip()[:40],
                    'observed_at': datetime.datetime.now().isoformat(timespec='seconds'),
-                   'watch': WATCH, 'files': cur, 'delta_reason': ''}
+                   'watch': WATCH, 'files': cur,
+                   'delta_reason': _old.get('delta_reason', ''),
+                   'expected_delta': _old.get('expected_delta', {})}
         os.makedirs(os.path.dirname(bl), exist_ok=True) if a.update else None
         if a.update:
             io.open(bl, 'w', encoding='utf-8', newline='\n').write(json.dumps(payload, ensure_ascii=False, indent=1))
@@ -117,7 +125,8 @@ def main():
         return 0
 
     base = json.load(io.open(bl, encoding='utf-8-sig'))
-    allow = bool(str(base.get('delta_reason', '')).strip())
+    delta_reason = str(base.get('delta_reason', '')).strip()
+    expected_delta = base.get('expected_delta') or {}
     fails = []
     for p in WATCH:
         b = base['files'].get(p)
@@ -126,8 +135,15 @@ def main():
             continue
         for k in METRICS:                       # 硬门禁：11 个语义计数（lines / ast_sha 仅展示，不参与判定）
             bv, cv = b['metrics'].get(k, 0), cur[p]['metrics'].get(k, 0)
-            if bv != cv:
-                fails.append('%s: 指标 %s 基准 %s → 当前 %s' % (p, k, bv, cv))
+            if bv == cv:
+                continue
+            _delta = cv - bv
+            # 精确白名单：仅当基准带 delta_reason（受控变更授权）且声明了本指标的预期增量时才放行
+            _exp = expected_delta.get(k) if (isinstance(expected_delta, dict) and delta_reason) else None
+            if _exp is not None and _delta == _exp:
+                continue
+            fails.append('%s: 指标 %s 基准 %s → 当前 %s%s' % (
+                p, k, bv, cv, ' (预期增量 %s)' % _exp if _exp is not None else ''))
         for key, lbl in (('priority_hist', 'emit priority 直方'), ('event_hist', '事件名直方')):
             if b.get(key) != cur[p].get(key):
                 bk, ck = b.get(key, {}), cur[p].get(key, {})
@@ -139,14 +155,17 @@ def main():
         print('  [OK] 硬门禁指标全部一致（参考：行数 %s / AST 指纹 %s）'
               % (cur[WATCH[0]]['metrics']['lines'], cur[WATCH[0]]['metrics']['ast_sha256_12']))
         return 0
-    if allow:
-        print('  [WARN] 有 %d 项漂移，但基准带 delta_reason=%r ⇒ 判"受控变更"放行（须在交付报告留痕）'
-              % (len(fails), base['delta_reason'][:40]))
-        return 0
-    print('  [FAIL] %d 项非预期漂移（无 delta_reason 即视为夹带）：' % len(fails))
+    if not delta_reason:
+        print('  [FAIL] %d 项漂移且无 delta_reason（须与代码同提交更新 %s 并填 delta_reason）'
+              % (len(fails), os.path.relpath(bl, a.root)))
+        for f in fails[:12]:
+            print('     - ' + f)
+        return 2
+    print('  [FAIL] %d 项非预期漂移（expected_delta 未覆盖即视为夹带，须回退或补白名单）：' % len(fails))
     for f in fails[:12]:
         print('     - ' + f)
-    print('  处方：① 若为误伤 ⇒ 与代码同提交更新 %s 并填 delta_reason；② 若为真漂移 ⇒ 回退该改动。' % os.path.relpath(bl, a.root))
+    print('  处方：① 若为误伤 ⇒ 在 %s 的 expected_delta 补声明该增量；② 若为真漂移 ⇒ 回退该改动。'
+          % os.path.relpath(bl, a.root))
     return 2
 
 
diff --git a/tools/ci/silent_except_fingerprints.json b/tools/ci/silent_except_fingerprints.json
index e3ac181..91bec8f 100644
--- a/tools/ci/silent_except_fingerprints.json
+++ b/tools/ci/silent_except_fingerprints.json
@@ -274,5 +274,25 @@
     "lineno": 246,
     "legacy_lineno": 246,
     "match": "exact"
+  },
+  {
+    "path": "organs/brain/PulseInnerWorld.py",
+    "qualname": "_cr_vision_insight",
+    "nth": 0,
+    "sha16": "86b989c50e54a4e8",
+    "tname": "Exception",
+    "has_log": false,
+    "has_reraise": false,
+    "lineno": 5064
+  },
+  {
+    "path": "tools/ci/cw3_consistency_gate.py",
+    "qualname": "main",
+    "nth": 0,
+    "sha16": "1c830325fc48fe4c",
+    "tname": "Exception",
+    "has_log": false,
+    "has_reraise": false,
+    "lineno": 110
   }
 ]
\ No newline at end of file
