# 第147批 DIFFS_FULL（自动生成）


- 基线：`9c858ae`（第146批交付）→ 本批 HEAD：`944312e`
- 生成方式：`git diff 9c858ae..HEAD` 全量拼接（脚本自动，无手抄）

## 改动量总览（numstat）

```
1	1	docs/分析报告/技术债务台账_代码实查_20260919.csv
2	1	nucleus/self_inspector.py
988	876	organs/brain/PulseInnerWorld.py
2	2	organs/brain/pulse_inner_world_knowledge.py
13	3	tests/test_deep_think_subproc_m31.py
99	0	tests/test_ir_slots_t147.py
92	0	tests/test_ir_timeout_t147.py
2	1	tools/ci/cw2_t2e_ci_gate_silent_except.py
```

## 完整 diff

```diff
diff --git a/docs/分析报告/技术债务台账_代码实查_20260919.csv b/docs/分析报告/技术债务台账_代码实查_20260919.csv
index 6029c09..b56c050 100644
--- a/docs/分析报告/技术债务台账_代码实查_20260919.csv
+++ b/docs/分析报告/技术债务台账_代码实查_20260919.csv
@@ -57,7 +57,7 @@ D055,P2,渠道监控,大模型渠道并发状态监控(P2-159),第四十二章,
 D056,P2,RESULT双发,上游RESULT双发路径治理(P2-164),第四十五章,第五十章,"取证完成,重构待单开批次",| P2-164 | 上游RESULT双发路径治理 | 🔄 取证完成（第26批T4），重构待单开批次 |,部分修复,PulseInnerWorld.py 仍在631/691/811/834/870/896/968/986/1086/1179/1235/1261等12+处_emit(InferenceEvent.RESULT),高,单开批次收口RESULT发射点到单一出口,pool,
 D057,P2,深度思考超时,深度思考超时致长问题无实质回答(P2-170),第四十九章,第五十二章,文档称已完成(待代码复核),✅ 已完成...端到端待重启验证,待重启验证,代码改动存在但需重启后端到端验证;无运行期证据,中,重启后注入长问题取证,pool,
 D058,P2,输出长度,大模型输出长度远低于要求(P2-171),第四十九章,第五十四章,文档称已完成(待代码复核),✅ 已完成...端到端待重启验证,待重启验证,"同上,长度约束改动待运行期确认",中,重启后统计实际输出token长度分布,pool,
-D059,P2,检索重复执行,"on_inference_request检索流程同秒重复2~3次(P2-172,与P2-164同源)",第四十九章,第五十章,待处理,| P2-172 | 内在世界_on_inference_request检索流程重复执行 | ⏳ 待处理...建议合并处理 |,大部分修复有残留,PulseInnerWorld.py=23145行;L245-246 _dedup_cleanup_interval/max_size;L4543 _cleanup_dedup_marks;nucleus/field/RequestDeduplicator.py=375,中,运行期统计同秒检索命中数确认去重生效,pool,
+D059,P2,检索重复执行,"on_inference_request检索流程同秒重复2~3次(P2-172,与P2-164同源)",第四十九章,第五十章,待处理,| P2-172 | 内在世界_on_inference_request检索流程重复执行 | ⏳ 待处理...建议合并处理 |,大部分修复有残留,PulseInnerWorld.py=23145行;L245-246 _dedup_cleanup_interval/max_size;L4543 _cleanup_dedup_marks;nucleus/field/RequestDeduplicator.py=375 @2026-09-27基线(第147批九刀拆分前,行号已失效),中,运行期统计同秒检索命中数确认去重生效,pool,
 D060,P2,自动化优化,"自动化优化(P2-173,第35批计划项)",第五十一章,第六十三章,排期(第35批),第35批：...P2-173（自动化优化）...,部分修复,evolution/AutoParamApplier.py=196;PeriodicTestScheduler.py=400 自动化调度存在,中,出自动化闭环报表,pool,
 D061,P2,模型自动配置,框架自动管理模型配置阶段2/3(P2-183星轨版),第五十七章,第六十三章,排期(第33-34批),| P2-183（星轨版） | 框架自动管理模型配置（阶段2/3） | 高 | 第33-34批 |,大部分修复有残留,llm/model_self_updater.py=444存在,中,补阶段3切换与回滚取证,pool,
 D062,P2,操作指令准入,操作指令准入默认关闭后仍存残余风险与判据缺口(P2-191),第六十一章,第六十六章,待完善(残余风险),T5 操作指令准入评估（P2-191）...实测关闭后仍存残余风险与判据缺口,待核,"按OPERATION_GATE/command_gate/shell_gate/操作指令准入等现名grep活代码=0命中;机制可能已重构或移除,需对照总账确认落点",低,对照总账定位现机制后再判残余风险,pool,
diff --git a/nucleus/self_inspector.py b/nucleus/self_inspector.py
index cf2295e..414e4a2 100644
--- a/nucleus/self_inspector.py
+++ b/nucleus/self_inspector.py
@@ -243,8 +243,9 @@ class SelfInspector(SilentLogMixin):
         self._load_config()
         self._info_field = None  
         # ===== 已知超长方法白名单 =====
+        # ★第147批九刀拆分：_on_inference_request 已拆为 16 个 _ir_* 方法（主方法 45 行），
+        #   移除其豁免，改为盯防新拆出的方法（_ir_* 系列，均 ≤200 行，正常进入 long_method 检测）。
         self._long_method_whitelist = {
-            ("PulseInnerWorld", "_on_inference_request"),
             ("PulseInnerWorld", "_on_heartbeat"),
             ("PulseInnerWorld", "_generate_weekly_report"),
             ("PulseInnerWorld", "_deep_think"),
diff --git a/organs/brain/PulseInnerWorld.py b/organs/brain/PulseInnerWorld.py
index 93cb63c..2f3521a 100644
--- a/organs/brain/PulseInnerWorld.py
+++ b/organs/brain/PulseInnerWorld.py
@@ -76,19 +76,28 @@ class PulseInnerWorld(
             "_context_mode",
             "_context_signal",
             "_emotion_modulation",
+            "_derivation_answer",
+            "_meta_state",
             "_explicit_inference_result",
             "_memory_context",
             "_question_complexity",
             "_question_length",
             "_reasoning_start_time",
             "_supplement_topic",
+            "_stress_modulation",
+            "_strategy_context",
             "correlation_id",
             "empathetic_note",
+            "contemplative_answer",
             "guidance",
             "payload",
             "question",
+            "question_features",
             "search_query",
             "tool_hint",
+            "tool_requested",
+            "fallback_tools",
+            "_has_remote_api",
             "user_name",
         )
 
@@ -529,13 +538,12 @@ class PulseInnerWorld(
             return {"status": "cache_cleared", "cache_key": _cache_key}
         return None
     # ========== 事件处理 ==========
-    def _on_inference_request(self, payload: dict) -> dict[str, Any]:
+    def _ir_build_context(self, payload: dict):
         question = payload.get("question", "")
         user_name = payload.get("user_name", "用户")
         correlation_id = payload.get("correlation_id", "")
-        search_query = question[:80]  # 提前初始化，确保所有分支可用
         if not question:
-            return {"status": "skipped", "reason": "空问题"}
+            return None
         # ===== 【v15.1修复】提前初始化所有可能被引用的变量 =====
         empathetic_note = ""
         # ★FIX: 显式初始化 contemplative_answer，避免 dir() 探测导致的变量生命周期混乱
@@ -573,15 +581,19 @@ class PulseInnerWorld(
         _ctx._emotion_modulation = _emotion_modulation
         _ctx.guidance = guidance
         _ctx.tool_hint = tool_hint
+        _ctx.contemplative_answer = contemplative_answer
+        _ctx._meta_state = _meta_state
+        return _ctx
 
+    def _ir_try_explicit_search(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
         # ★v26.0修复：用户明确要求搜索时，优先触发搜索（不经过内部推理）
         # ★主线第16批 T1/P2-104：三处前缀正则收敛为单一事实来源（见模块顶部常量）
         _prefix_alt = _search_prefix_pattern()
         _explicit_search_patterns = [rf'^({_prefix_alt})']
-        _is_explicit_search = any(re.match(p, question.strip()) for p in _explicit_search_patterns)
+        _is_explicit_search = any(re.match(p, ctx.question.strip()) for p in _explicit_search_patterns)
         if _is_explicit_search:
             # 提取搜索词（去掉"搜索一下"等前缀）
-            _search_topic = re.sub(rf'^({_prefix_alt})\s*', '', question.strip()).strip()
+            _search_topic = re.sub(rf'^({_prefix_alt})\s*', '', ctx.question.strip()).strip()
             # ★T1 防御：前缀剥离后若仍以单字噪声开头，判定为疑似截断残留 ——
             #   只记日志留痕，**不擅改主题**（详见 _detect_leading_search_noise 注释）。
             if _search_topic_guard_enabled():
@@ -602,12 +614,16 @@ class PulseInnerWorld(
                 # 同时返回一个占位回答，告诉用户正在搜索
                 _search_placeholder = f"好的，我正在搜索「{_search_topic[:30]}」相关信息，请稍候..."
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": _search_placeholder,
-                    "method": "explicit_search", "confidence": 0.5, "user_name": user_name,
-                    "correlation_id": correlation_id,
+                    "question": ctx.question, "answer": _search_placeholder,
+                    "method": "explicit_search", "confidence": 0.5, "user_name": ctx.user_name,
+                    "correlation_id": ctx.correlation_id,
                 }, priority=6, layer="L2")
                 return {"status": "explicit_search", "answer": _search_placeholder}
 
+        return None
+
+
+    def _ir_run_detectors(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
         # 检测器调度循环：按优先级依次调用，第一个匹配的立即返回
         # ★D4配置中心化：检测器优先级从「注释魔法数字」收敛为结构化 (优先级, 检测器) 元组，
         #   消除散落注释中的硬编码数字，便于后续统一配置化与审计。执行顺序与优先级数值不变。
@@ -632,41 +648,545 @@ class PulseInnerWorld(
             (42, self._detect_symbolic_reason),          # ★新增：内部符号推理
             (44, self._detect_cognitive_operator),       # ★新增
         ]
+        _REASONING_TIMEOUT = 45.0
         for _priority, _detector in _detectors:
             # ★v26.0新增：检测器调度超时检查
-            if time.time() - _reasoning_start_time > _REASONING_TIMEOUT:
+            if time.time() - ctx._reasoning_start_time > _REASONING_TIMEOUT:
                 self._log(LogLevel.WARNING,
-                         f"推理超时({_REASONING_TIMEOUT}s)，检测器调度中断，问题='{question[:30]}'")
+                         f"推理超时({_REASONING_TIMEOUT}s)，检测器调度中断，问题='{ctx.question[:30]}'")
                 break
-            _result = _detector(_ctx)
+            _result = _detector(ctx)
             if _result is not None:
                 self._log(LogLevel.DEBUG, f"检测器命中: {_detector.__name__} → {_result.get('status', '?')}")
                 return _result
 
+        return None
+
+    def _ir_try_deep_search_pre(self, ctx: "PulseInnerWorld.InferenceContext"):
+        # 策略3: 深度搜索（原有逻辑）
+        if "deep_search" in ctx.fallback_tools and not ctx.tool_requested:
+            # ===== 全局状态感知：自主判断是否适合执行搜索 =====
+            can_search = True
+            skip_reason = ""
+            try:
+                if self.info_field and hasattr(self.info_field, 'get_global_state'):
+                    global_state = self.info_field.get_global_state()
+                    if global_state.get("is_high_load"):
+                        can_search = False
+                        skip_reason = "系统负载偏高，暂缓深度搜索"
+                    elif global_state.get("active_external_ops", 0) >= global_state.get("max_concurrent_ops", 2):
+                        can_search = False
+                        skip_reason = f"已有{global_state.get('active_external_ops')}个搜索任务在执行，暂缓新搜索"
+            except Exception as e:
+                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+            return (can_search, skip_reason)
+        return None
+
+
+    def _ir_try_deep_search_exec(self, ctx: "PulseInnerWorld.InferenceContext", can_search, skip_reason):
+        if can_search:
+            # ===== 新增：语义范畴判断——搜索主题是否适合外部搜索引擎 =====
+            _search_topic_for_check = ctx.search_query or ctx.question[:80]
+            if not self._is_suitable_for_search(_search_topic_for_check):
+                self._log(LogLevel.INFO,
+                         f"语义范畴判断: 搜索主题'{_search_topic_for_check[:40]}'不适合外部搜索，"
+                         f"优先走内在沉思")
+                if self.node_pool:
+                    ctx.contemplative_answer = self._contemplative_reason(ctx.question)
+                    if ctx.contemplative_answer:
+                        self._inference_count += 1
+                        self._cache_inference(ctx.question, ctx.contemplative_answer, ctx.user_name)
+                        final_answer = self._enhance_answer(
+                            answer=ctx.contemplative_answer,
+                            question=ctx.question,
+                            method="contemplation_semantic",
+                            complexity=ctx._question_complexity,
+                            empathetic_note=ctx.empathetic_note,
+                            memory_context=ctx._memory_context
+                        )
+                        self._emit(InferenceEvent.RESULT, {
+                            "question": ctx.question, "answer": final_answer,
+                            "method": "contemplation_semantic", "confidence": 0.5, "user_name": ctx.user_name,
+                            "correlation_id": ctx.payload.get("correlation_id", ""),
+                            "strategy_applied": ctx._strategy_context,
+                            "confidence_hint": "low",
+                        }, priority=6, layer="L2")
+                        return {"status": "contemplation_match", "answer": ctx.contemplative_answer}
+                # ★v25.0修复：不适合搜索且沉思失败，直接走大模型兜底或诚实回答，绝不发起外部搜索
+                self._log(LogLevel.INFO, "语义范畴: 不适合搜索且沉思未命中，走大模型兜底或诚实回答")
+                if ctx._has_remote_api and ctx.correlation_id:
+                    ctx._memory_context = self._build_memory_context(ctx.question, ctx.user_name, ctx.guidance)
+                    self._emit(InferenceEvent.RESULT, {
+                        "question": ctx.question, "answer": None,
+                        "method": "meta_not_search",
+                        "confidence": 0.0, "user_name": ctx.user_name,
+                        "correlation_id": ctx.correlation_id,
+                        "strategy_applied": ctx._strategy_context,
+                        "ctx.tool_requested": False,
+                        "memory_context": ctx._memory_context,
+                    }, priority=5, layer="L2")
+                    return {"status": "delegated_to_lung_meta", "reason": "不适合搜索且沉思失败"}
+                else:
+                    fallback_answer = (
+                        "关于这个问题，我目前的知识库中还没有足够的信息来给出确切的回答，"
+                        "但我会继续学习和思考。"
+                    )
+                    self._inference_count += 1
+                    self._cache_inference(ctx.question, fallback_answer, ctx.user_name)
+                    final_answer = self._enhance_answer(
+                        answer=fallback_answer,
+                        question=ctx.question,
+                        method="meta_honest",
+                        complexity=ctx._question_complexity,
+                        empathetic_note=ctx.empathetic_note,
+                        memory_context=ctx._memory_context,
+                    )
+                    self._emit(InferenceEvent.RESULT, {
+                        "question": ctx.question, "answer": final_answer,
+                        "method": "meta_honest", "confidence": 0.3, "user_name": ctx.user_name,
+                        "correlation_id": ctx.correlation_id,
+                        "strategy_applied": ctx._strategy_context,
+                        "confidence_hint": "low",
+                    }, priority=5, layer="L2")
+                    return {"status": "meta_honest", "answer": fallback_answer}
+            # ===== 新增: 观点陈述检测——判断用户输入是观点还是问题 =====
+            is_opinion_statement = self._is_opinion_statement(ctx.question)
+            if is_opinion_statement and self.node_pool:
+                # 用户可能在分享观点，尝试用内在沉思生成回应
+                self._log(LogLevel.INFO,
+                         f"元认知决策: 检测到观点陈述，优先内在沉思: '{ctx.question[:40]}...'")
+                ctx.contemplative_answer = self._contemplative_reason(ctx.question)
+                if ctx.contemplative_answer:
+                    self._inference_count += 1
+                    self._cache_inference(ctx.question, ctx.contemplative_answer, ctx.user_name)
+                    final_answer = self._enhance_answer(
+                        answer=ctx.contemplative_answer,
+                        question=ctx.question,
+                        method="contemplation",
+                        complexity=ctx._question_complexity,
+                        empathetic_note=ctx.empathetic_note,
+                        memory_context=ctx._memory_context
+                    )
+                    self._emit(InferenceEvent.RESULT, {
+                        "question": ctx.question, "answer": final_answer,
+                        "method": "contemplation", "confidence": 0.5, "user_name": ctx.user_name,
+                        "correlation_id": ctx.payload.get("correlation_id", ""),
+                        "strategy_applied": ctx._strategy_context,
+                        "confidence_hint": "low",
+                    }, priority=6, layer="L2")
+                    return {"status": "contemplation_match", "answer": ctx.contemplative_answer}
+                # 沉思无法回答时，生成带有价值冲突说明的兜底回答
+                fallback_answer = (
+                    "关于这个问题，我目前的知识库中还没有足够的信息来给出确切的回答。"
+                    "但我能感受到你在思考一个很重要的问题——如何在诚实和善意之间找到平衡。"
+                    "这种思考本身就很有价值。"
+                )
+                self._inference_count += 1
+                self._cache_inference(ctx.question, fallback_answer, ctx.user_name)
+                final_answer = self._enhance_answer(
+                    answer=fallback_answer,
+                    question=ctx.question,
+                    method="contemplation",
+                    complexity=ctx._question_complexity,
+                    empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context
+                )
+                self._emit(InferenceEvent.RESULT, {
+                    "question": ctx.question, "answer": final_answer,
+                    "method": "contemplation", "confidence": 0.4, "user_name": ctx.user_name,
+                    "correlation_id": ctx.payload.get("correlation_id", ""),
+                    "strategy_applied": ctx._strategy_context,
+                    "confidence_hint": "low",
+                }, priority=6, layer="L2")
+                # 将兜底回答发射为消化脉冲，让胃创建L1节点
+                self._emit(DigestEvent.KNOWLEDGE, {
+                    "content": f"[内在沉思·兜底回答] {fallback_answer}",
+                    "source_organ": self.organ_name,
+                    "trigger_reason": "contemplation.fallback",
+                    "importance": "B",
+                    "view_mode": "INNER_VIEW",
+                }, priority=3, layer="L2")
+                return {"status": "contemplation_match", "answer": fallback_answer}
+            else:
+                # ★FIX: 抽象概念/知识陈述在源头拦截，不发射无效搜索
+                _skip_search = self._should_skip_search(ctx.question)
+                if _skip_search:
+                    self._log(LogLevel.INFO, f"搜索意图拦截: 抽象概念/知识陈述不触发搜索: '{ctx.question[:40]}...'")
+                # 正常搜索逻辑（ctx.search_query已在前面初始化为ctx.question[:80]）
+                if not _skip_search and len(ctx.question) > 40:
+                    refined = self._refine_search_intent(ctx.question)
+                    if refined and len(refined) >= 4:
+                        ctx.search_query = refined
+                        self._log(LogLevel.INFO, f"元认知决策(搜索意图提炼): '{ctx.question[:40]}...' → '{ctx.search_query}'")
+            if not _skip_search:
+                self._log(LogLevel.INFO,
+                         f"元认知决策: 内部推理未命中，触发深度搜索: {ctx.search_query[:40]}")
+                self._emit(Event.CONTROLLER_OPEN_URL, {
+                    "url": f"https://lite.duckduckgo.com/lite/?q={ctx.search_query[:80]}",
+                    "reason": "元认知决策: 内部推理未命中，需要深度搜索",
+                    "search_topic": ctx.search_query[:80],
+                    "deep_search": True,
+                    "search_intent": "curiosity",
+                }, priority=4, layer="L3")
+                ctx.tool_requested = True
+            # ===== 搜索发起后，如果远程API可用，同时作为兜底方案 =====
+            if ctx._has_remote_api and ctx.correlation_id:
+                # ===== 大模型兜底前记录经验 =====
+                try:
+                    from nucleus.mnemosyne.ReasoningExperience import (
+                        get_reasoning_experience,
+                    )
+                    _reasoning_exp_fb = get_reasoning_experience()
+                    # 过滤内部追问词
+                    _is_internal_meta = bool(
+                        ctx.question and (
+                            re.search(r'的(?:前提|反例|边界|底层构成|演化路径|最小单元)是什么', ctx.question) or
+                            re.search(r'(?:前提|假设)是否(?:总是|还)?成立', ctx.question) or
+                            re.search(r'有没有.*反例|在什么情况下.*失效|结论还成立吗', ctx.question) or
+                            re.search(r'如果.*(?:反过来|放到|推到极致|不一样)', ctx.question) or
+                            re.search(r'它不是什么|换个角度|不同.*视角', ctx.question)
+                        )
+                    )
+                    if ctx.question and not _is_internal_meta:
+                        _reasoning_exp_fb.record(
+                            ctx.question,
+                            "unknown",
+                            source="local_fallback",
+                            confidence=0.3
+                        )
+                except Exception as e:
+                    self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+                # ===== 经验记录结束 =====
+                self._direct_to_lung_questions.add(ctx.question.strip())
+                ctx._memory_context = self._build_memory_context(ctx.question, ctx.user_name, ctx.guidance)
+                self._emit(InferenceEvent.RESULT, {
+                    "question": ctx.question, "answer": None,
+                    "method": "search_with_lung_fallback",
+                    "confidence": 0.0, "user_name": ctx.user_name,
+                    "correlation_id": ctx.correlation_id,  # ← 使用前面提取的ID
+                    "strategy_applied": ctx._strategy_context,
+                    "ctx.tool_requested": True,
+                    "memory_context": ctx._memory_context,
+                }, priority=4, layer="L2")
+        else:
+            self._log(LogLevel.INFO, f"元认知决策: {skip_reason}: {ctx.question[:40]}")
+        return None
+
+    def _ir_assemble_knowledge_answer(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
+        # 知识检索
+        # ★v22.0重构：如果大脑皮层给出了建议路径，优先在建议路径下检索
+        _qica_paths = ctx.payload.get("strategy_context", {}).get("knowledge_paths", [])  # type: ignore[possibly-unbound]
+        # ★第九批 3.4（星轨 P2-9）：QICA 建议 /人物/{人名} 时先查身份知识库。
+        #   此前知识树里没有这些路径，检索必然落空，于是「小林是谁」每次都重新瞎猜。
+        _identity_hit = self._identity_lookup(ctx.question)  # type: ignore[possibly-unbound]
+        if _identity_hit:
+            self._log(LogLevel.INFO,  # type: ignore[possibly-undefined]
+                     f"身份知识命中: {_identity_hit[:40]}")
+        if _identity_hit:
+            knowledge_answer = _identity_hit  # type: ignore[possibly-unbound]
+        elif _qica_paths:  # type: ignore[possibly-unbound]
+            _path_knowledge = None  # type: ignore[possibly-unbound]
+            for _path in _qica_paths[:3]:  # type: ignore[possibly-unbound]
+                _nodes = self.node_pool.query(
+                    evol_level="L3", space_path_prefix=_path, limit=10  # type: ignore[possibly-unbound]
+                ) if self.node_pool else []
+                if _nodes:
+                    _val = str(_nodes[0].value) if _nodes[0].value else ""
+                    if _val and len(_val) > 20:
+                        _path_knowledge = _val[:200]  # type: ignore[possibly-unbound]
+                        self._log(LogLevel.INFO, f"QICA路径优先检索: 路径={_path}, 命中={len(_nodes)}个节点")  # type: ignore[possibly-unbound]
+                        break
+            knowledge_answer = _path_knowledge or self._knowledge_retrieve(ctx.question)  # type: ignore[possibly-unbound]
+        else:
+            knowledge_answer = self._knowledge_retrieve(ctx.question)
+        if knowledge_answer:
+            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
+            self._inference_count += 1
+            self._cache_inference(ctx.question, knowledge_answer, ctx.user_name)
+            knowledge_hint = self._get_confidence_hint(ctx.question)
+            confidence_map = {"certain": 1.0, "high": 0.85, "moderate": 0.7, "low": 0.5}
+            confidence = confidence_map.get(knowledge_hint, 0.6)
+            duration = time.time() - ctx._reasoning_start_time
+            tuning = ""
+            if knowledge_hint == "low":
+                tuning = "知识检索质量偏低，可能需要补充此领域知识"
+            elif knowledge_hint == "high":
+                tuning = "知识检索质量高，此领域认知扎实"
+            self._trace_inference(ctx.question, knowledge_answer, f"knowledge_{knowledge_hint}",
+                                 confidence, ctx.user_name,
+                                 duration=duration, complexity=ctx._question_complexity,
+                                 tuning_hint=tuning)
+            # ===== ★v22.0方向三修复：知识边界感知——检测到低质量检索时自动生成追问 =====
+            _boundary_inquiry = self._detect_knowledge_boundary_and_inquire(
+                question=ctx.question,
+                knowledge_result=knowledge_answer,
+                contemplative_result=ctx.contemplative_answer,
+                confidence=0.3 if knowledge_hint == "low" else 0.5,
+            )
+            if _boundary_inquiry:
+                self._emit(GrowthEvent.NEED_DETECTED, {
+                    "milestone": "知识边界延伸",
+                    "gaps": [{"metric": "knowledge_boundary", "current": 0, "target": 1}],
+                    "suggestion": _boundary_inquiry,
+                    "current_level": {"original_question": ctx.question[:80], "boundary": _boundary_inquiry},
+                    "growth_topic": _boundary_inquiry[:60],
+                }, priority=5, layer="L3")
+                self._log(LogLevel.INFO, f"知识边界延伸: 生成追问 '{_boundary_inquiry[:60]}'")
+            # ===== ★v22.0方向三修复结束 =====
+
+            # ===== 新增: 自适应回答深度——根据关系和语境调整表达 =====
+            knowledge_answer = self._adapt_answer_depth(knowledge_answer, ctx.user_name, ctx.guidance, ctx.question)
+            # ===== 新增: 不确定性诚实表达——让回答更真实可信 =====
+            knowledge_answer = self._add_uncertainty_note(knowledge_answer, knowledge_hint, ctx.user_name)
+            # ===== 新增: 费曼解释——用自己的话重新组织答案 =====
+            if len(knowledge_answer) > 120 or any(
+                prefix in knowledge_answer for prefix in ["[主动学习", "[架构]", "[知识]", "[复盘认知", "相关知识汇总"]
+            ):
+                feynman_version = self._generate_feynman_explanation(ctx.question, knowledge_answer)
+                if feynman_version:
+                    knowledge_answer = feynman_version
+                    self._log(LogLevel.INFO, f"费曼解释: 将复杂知识转化为简单表达: {ctx.question[:30]}")
+            # ===== 新增: 本质追问——在答案基础上进行深层探究 =====
+            essence_question = self._generate_essence_inquiry(ctx.question, knowledge_answer)
+            # ===== 统一增强答案 =====
+            final_knowledge_answer = self._enhance_answer(
+                answer=knowledge_answer,
+                question=ctx.question,
+                method="knowledge",
+                complexity=ctx._question_complexity,
+                empathetic_note=ctx.empathetic_note,
+                memory_context=ctx._memory_context
+            )
+            # ===== 【v15.1修复】ctx._supplement_topic 提前初始化 =====
+            if knowledge_hint == "moderate" and self.node_pool:
+                ctx._supplement_topic = self._build_supplement_search_topic(ctx.question, knowledge_answer)
+            if ctx.correlation_id:
+                self._active_search_correlation[ctx.search_query[:80]] = ctx.correlation_id
+                if ctx._supplement_topic:
+                    self._emit(Event.CONTROLLER_OPEN_URL, {
+                        "url": f"https://lite.duckduckgo.com/lite/?q={ctx._supplement_topic[:80]}",
+                        "reason": f"知识补充搜索: {ctx._supplement_topic[:40]}",
+                        "search_topic": ctx._supplement_topic[:80],
+                        "deep_search": True,
+                        "search_intent": "curiosity",
+                        "search_correlation_id": ctx.correlation_id,
+                    }, priority=2, layer="L3")
+                    self._log(LogLevel.INFO, f"知识补充搜索: '{ctx._supplement_topic[:40]}' (检索置信度={knowledge_hint})")
+            self._emit(InferenceEvent.RESULT, {
+                "question": ctx.question, "answer": final_knowledge_answer,
+                "method": "knowledge", "confidence": 0.7, "user_name": ctx.user_name,
+                "correlation_id": ctx.payload.get("correlation_id", ""),
+                "confidence_hint": knowledge_hint,
+                "strategy_applied": ctx.payload.get("strategy_context", {}),
+                "essence_inquiry": essence_question,
+            }, priority=7, layer="L2")
+            # ===== 新增: 自主建议生成——基于理解主动提供帮助 =====
+            proactive_suggestion = self._generate_proactive_suggestion(ctx.question, knowledge_answer, ctx.user_name)
+            if proactive_suggestion:
+                knowledge_answer = knowledge_answer + " " + proactive_suggestion
+                self._log(LogLevel.INFO, f"自主建议生成: 为'{ctx.user_name}'提供基于'{ctx.question[:30]}'的建议")
+            # ===== 新增: 情感记忆绑定——回忆触发情绪复现 =====
+            self._trigger_emotional_memory(knowledge_answer)
+            # ===== 新增: 知识自省与修正——根据检索质量强化或标记节点 =====
+            self._reflect_and_reinforce_knowledge(ctx.question, knowledge_answer)
+            # ===== 新增: 实践验证——主动构造验证场景 =====
+            verification = self._attempt_practical_verification(ctx.question, knowledge_answer)
+            if verification:
+                self._emit(verification["event_type"], verification["payload"],
+                          priority=verification.get("priority", 4),
+                          layer=verification.get("layer", "L2"))
+                self._log(LogLevel.INFO,
+                         f"实践验证: {verification.get('description', '')[:80]}")
+            # ===== 新增: 自主视角构建——从不同角度审视问题 =====
+            alternative_perspective = self._generate_alternative_perspective(ctx.question, knowledge_answer)
+            if alternative_perspective:
+                self._log(LogLevel.INFO, f"视角构建: {alternative_perspective[:80]}")
+                self._emit(GrowthEvent.NEED_DETECTED, {
+                    "milestone": "视角拓展",
+                    "gaps": [{"metric": "perspective", "current": 0, "target": 1}],
+                    "suggestion": alternative_perspective,
+                    "current_level": {
+                        "original_question": ctx.question,
+                        "perspective": alternative_perspective,
+                    },
+                    "growth_topic": alternative_perspective[:60],
+                }, priority=3, layer="L3")
+            # ===== 新增: 认知框架迁移——跨领域类比 =====
+            framework_transfer = self._attempt_framework_transfer(ctx.question, knowledge_answer)
+            if framework_transfer:
+                self._log(LogLevel.INFO,
+                         f"认知框架迁移: {framework_transfer.get('insight', '')[:80]}")
+                # 将迁移洞察作为探索种子
+                self._emit(GrowthEvent.NEED_DETECTED, {
+                    "milestone": "框架迁移",
+                    "gaps": [{"metric": "cross_domain", "current": 0, "target": 1}],
+                    "suggestion": framework_transfer.get("insight", ""),
+                    "current_level": {
+                        "source_question": ctx.question,
+                        "transferred_from": framework_transfer.get("source_domain", ""),
+                        "transferred_concept": framework_transfer.get("core_concept", ""),
+                    },
+                    "growth_topic": framework_transfer.get("explore_topic", ctx.question[:60]),
+                }, priority=3, layer="L3")
+            # 本质追问结果作为新的探索种子
+            if essence_question:
+                self._emit(GrowthEvent.NEED_DETECTED, {
+                    "milestone": "本质追问",
+                    "gaps": [{"metric": "deep_understanding", "current": 0, "target": 1}],
+                    "suggestion": essence_question,
+                    "current_level": {"original_question": ctx.question, "answer": knowledge_answer[:100]},
+                    "growth_topic": essence_question[:60],
+                }, priority=3, layer="L3")
+            return {"status": "knowledge_match", "answer": knowledge_answer}
+        # 内在沉思引擎——知识检索未命中时，基于已有知识进行推演
+        if self.node_pool:
+            ctx.contemplative_answer = self._contemplative_reason(ctx.question)
+            if ctx.contemplative_answer:
+                self._inference_count += 1
+                self._cache_inference(ctx.question, ctx.contemplative_answer, ctx.user_name)
+                self._trace_inference(ctx.question, ctx.contemplative_answer, "contemplation", 0.5, ctx.user_name,
+                                     duration=time.time() - ctx._reasoning_start_time,
+                                     complexity=ctx._question_complexity,
+                                     tuning_hint="沉思推演完成，需要后续验证")
+                final_answer = self._enhance_answer(
+                    answer=ctx.contemplative_answer,
+                    question=ctx.question,
+                    method="contemplation",
+                    complexity=ctx._question_complexity,
+                    empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context
+                )
+                self._emit(InferenceEvent.RESULT, {
+                    "question": ctx.question, "answer": final_answer,
+                    "method": "contemplation", "confidence": 0.5, "user_name": ctx.user_name,
+                    "correlation_id": ctx.payload.get("correlation_id", ""),
+                    "strategy_applied": ctx.payload.get("strategy_context", {}),
+                    "confidence_hint": "low",
+                }, priority=6, layer="L2")
+                return {"status": "contemplation_match", "answer": ctx.contemplative_answer}
+        return None
+
+    def _ir_qica_knowledge_retrieve(self, ctx: "PulseInnerWorld.InferenceContext", _qica_paths):
+        _knowledge_result = None
+        # ★v22.0修复：严格按QICA优先级顺序检索
+        if _qica_paths and self.node_pool:  # type: ignore[possibly-unbound]
+            for _path in _qica_paths[:3]:  # type: ignore[possibly-unbound]
+                _l3_nodes = self.node_pool.query(evol_level="L3", space_path_prefix=_path, limit=10)  # type: ignore[possibly-unbound]
+                if _l3_nodes:
+                    for _node in _l3_nodes:
+                        _val = self._clean_node_value(str(_node.value)) if _node.value else ""
+                        if _val and len(_val) > 30 and not self._is_internal_knowledge_node(_val):
+                            _knowledge_result = _val
+                            self._log(LogLevel.INFO, f"QICA路径检索: 路径={_path}, 命中节点")  # type: ignore[possibly-unbound]
+                            break
+                if _knowledge_result:
+                    break
+                # 该路径未命中，继续下一个路径
+                _l2_nodes = self.node_pool.query(evol_level="L2", space_path_prefix=_path, limit=10)  # type: ignore[possibly-unbound]
+                if _l2_nodes:
+                    for _node in _l2_nodes:
+                        _val = self._clean_node_value(str(_node.value)) if _node.value else ""
+                        # ★质量修复B2：L2 路径与 L3 路径统一调用内部节点过滤器（修复仅查4前缀导致的漏检）
+                        if _val and len(_val) > 30 and not self._is_internal_knowledge_node(_val):
+                            _knowledge_result = _val
+                            self._log(LogLevel.INFO, f"QICA路径检索(L2): 路径={_path}, 命中节点")  # type: ignore[possibly-unbound]
+                            break
+                if _knowledge_result:
+                    break
+                self._log(LogLevel.DEBUG, f"QICA路径检索未命中: 路径={_path}，尝试下一个路径")  # type: ignore[possibly-unbound]
+        if not _knowledge_result:
+            _knowledge_result = self._knowledge_retrieve(ctx.question)
+
+        if _knowledge_result:
+            # ★v23.0支点：检索结果相关性验证 + 自动降级链路
+            # ★v9.5修复：传入命中节点所在 space_path，桥接「路径主题」与「正文关键词」语义鸿沟  # type: ignore[possibly-unbound]
+            _relevance = self._verify_knowledge_relevance(
+                ctx.question, _knowledge_result,
+                space_path=_path if _qica_paths else None)  # type: ignore[possibly-unbound]
+            if _relevance < 0.10:
+                # 相关性过低，先尝试内在沉思拼凑
+                self._log(LogLevel.INFO,
+                         f"QICA检索结果不相关(相关度={_relevance:.2f})，尝试内在沉思")
+                _contemplation = self._contemplative_reason(ctx.question)
+                if _contemplation and len(_contemplation) > 30:
+                    _knowledge_result = _contemplation
+                    self._log(LogLevel.INFO, "降级到内在沉思成功")
+                else:
+                    # 沉思也不行，调用大模型
+                    self._log(LogLevel.INFO, "内在沉思失败，降级到大模型")
+                    _model_result = self._generate_branch_with_model(
+                        original_question=ctx.question,
+                        branch_name="知识检索降级",
+                        branch_prompt=ctx.question,
+                    )
+                    if _model_result and len(_model_result) > 20:
+                        _knowledge_result = _model_result
+                        self._log(LogLevel.INFO, f"大模型降级成功: {_knowledge_result[:60]}...")
+                        # ★v23.0补充：将大模型结果消化为知识，存入InsightBoard
+                        try:
+                            if hasattr(self, '_insight_board') and self._insight_board:
+                                self._insight_board.post(
+                                    insight_type="knowledge_boundary",
+                                    content=_knowledge_result[:200],
+                                    source_loop="知识检索降级·大模型生成",
+                                    related_dimension="知识补充",
+                                    confidence=0.6,
+                                    keywords=[ctx.question[:30], "大模型补充"]
+                                )
+                        except Exception as e:
+                            self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+                    else:
+                        _knowledge_result = None
+            # 降级链路结束
+
+            if _knowledge_result:
+                self._inference_count += 1
+                self._cache_inference(ctx.question, _knowledge_result, ctx.user_name)
+            self._trace_inference(ctx.question, _knowledge_result, "qica_knowledge", 0.75, ctx.user_name,
+                                 duration=time.time() - ctx._reasoning_start_time,
+                                 complexity=ctx._question_complexity,
+                                 tuning_hint="QICA建议知识检索")
+            _final = self._enhance_answer(
+                answer=_knowledge_result, question=ctx.question, method="qica_knowledge",
+                complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
+                memory_context=ctx._memory_context
+            )
+            self._emit(InferenceEvent.RESULT, {
+                "question": ctx.question, "answer": _final,
+                "method": "qica_knowledge", "confidence": 0.75, "user_name": ctx.user_name,
+                "correlation_id": ctx.correlation_id,
+                "confidence_hint": "moderate",
+                "strategy_applied": ctx.payload.get("strategy_context", {}),
+            }, priority=7, layer="L2")
+            return {"status": "qica_knowledge", "answer": _knowledge_result}
+        return None
+
+
+    def _ir_dispatch_qica_method(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
         # ===== ★v22.0重构：QICA建议方法优先执行 =====
-        _qica_method = payload.get("strategy_context", {}).get("qica_suggested_method", "")
-        _qica_paths = payload.get("strategy_context", {}).get("qica_knowledge_paths", [])  # type: ignore[possibly-unbound]
+        _qica_method = ctx.payload.get("strategy_context", {}).get("qica_suggested_method", "")
+        _qica_paths = ctx.payload.get("strategy_context", {}).get("qica_knowledge_paths", [])  # type: ignore[possibly-unbound]
 
         if _qica_method == "rule_reason":
-            _rule_result = self._rule_reason(question, user_name, guidance)
+            _rule_result = self._rule_reason(ctx.question, ctx.user_name, ctx.guidance)
             if _rule_result:
                 self._inference_count += 1
-                self._cache_inference(question, _rule_result, user_name)
-                self._trace_inference(question, _rule_result, "qica_rule_reason", 0.9, user_name,
-                                     duration=time.time() - _reasoning_start_time,
-                                     complexity=_question_complexity,
+                self._cache_inference(ctx.question, _rule_result, ctx.user_name)
+                self._trace_inference(ctx.question, _rule_result, "qica_rule_reason", 0.9, ctx.user_name,
+                                     duration=time.time() - ctx._reasoning_start_time,
+                                     complexity=ctx._question_complexity,
                                      tuning_hint="QICA建议规则推理")
                 _final = self._enhance_answer(
-                    answer=_rule_result, question=question, method="qica_rule_reason",
-                    complexity=_question_complexity, empathetic_note=empathetic_note,
-                    memory_context=_memory_context
+                    answer=_rule_result, question=ctx.question, method="qica_rule_reason",
+                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context
                 )
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": _final,
-                    "method": "qica_rule_reason", "confidence": self._evidence_conf(0.9, "rule", [_rule_result]), "user_name": user_name,
-                    "correlation_id": correlation_id,
+                    "question": ctx.question, "answer": _final,
+                    "method": "qica_rule_reason", "confidence": self._evidence_conf(0.9, "rule", [_rule_result]), "user_name": ctx.user_name,
+                    "correlation_id": ctx.correlation_id,
                     "confidence_hint": "high",
-                    "strategy_applied": payload.get("strategy_context", {}),
+                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                 }, priority=7, layer="L2")
                 return {"status": "qica_rule_reason", "answer": _rule_result}
 
@@ -675,7 +1195,7 @@ class PulseInnerWorld(
         # 导致 QICA 建议被记录进 strategy_applied 却从不真正执行。
         if _qica_method in self._QICA_EXTRA_METHODS:
             _extra = self._execute_qica_method(
-                _qica_method, question, user_name, guidance)
+                _qica_method, ctx.question, ctx.user_name, ctx.guidance)
             if _extra:
                 self._log(LogLevel.INFO,
                           f"[B2策略] 建议方法={_qica_method} 已采纳并优先执行")
@@ -684,155 +1204,67 @@ class PulseInnerWorld(
                       f"[B2策略] 建议方法={_qica_method} 执行无有效结果，回落默认路径")
 
         if _qica_method == "knowledge_retrieve":
-            _knowledge_result = None
-            # ★v22.0修复：严格按QICA优先级顺序检索
-            if _qica_paths and self.node_pool:  # type: ignore[possibly-unbound]
-                for _path in _qica_paths[:3]:  # type: ignore[possibly-unbound]
-                    _l3_nodes = self.node_pool.query(evol_level="L3", space_path_prefix=_path, limit=10)  # type: ignore[possibly-unbound]
-                    if _l3_nodes:
-                        for _node in _l3_nodes:
-                            _val = self._clean_node_value(str(_node.value)) if _node.value else ""
-                            if _val and len(_val) > 30 and not self._is_internal_knowledge_node(_val):
-                                _knowledge_result = _val
-                                self._log(LogLevel.INFO, f"QICA路径检索: 路径={_path}, 命中节点")  # type: ignore[possibly-unbound]
-                                break
-                    if _knowledge_result:
-                        break
-                    # 该路径未命中，继续下一个路径
-                    _l2_nodes = self.node_pool.query(evol_level="L2", space_path_prefix=_path, limit=10)  # type: ignore[possibly-unbound]
-                    if _l2_nodes:
-                        for _node in _l2_nodes:
-                            _val = self._clean_node_value(str(_node.value)) if _node.value else ""
-                            # ★质量修复B2：L2 路径与 L3 路径统一调用内部节点过滤器（修复仅查4前缀导致的漏检）
-                            if _val and len(_val) > 30 and not self._is_internal_knowledge_node(_val):
-                                _knowledge_result = _val
-                                self._log(LogLevel.INFO, f"QICA路径检索(L2): 路径={_path}, 命中节点")  # type: ignore[possibly-unbound]
-                                break
-                    if _knowledge_result:
-                        break
-                    self._log(LogLevel.DEBUG, f"QICA路径检索未命中: 路径={_path}，尝试下一个路径")  # type: ignore[possibly-unbound]
-            if not _knowledge_result:
-                _knowledge_result = self._knowledge_retrieve(question)
-
-            if _knowledge_result:
-                # ★v23.0支点：检索结果相关性验证 + 自动降级链路
-                # ★v9.5修复：传入命中节点所在 space_path，桥接「路径主题」与「正文关键词」语义鸿沟  # type: ignore[possibly-unbound]
-                _relevance = self._verify_knowledge_relevance(
-                    question, _knowledge_result,
-                    space_path=_path if _qica_paths else None)  # type: ignore[possibly-unbound]
-                if _relevance < 0.10:
-                    # 相关性过低，先尝试内在沉思拼凑
-                    self._log(LogLevel.INFO,
-                             f"QICA检索结果不相关(相关度={_relevance:.2f})，尝试内在沉思")
-                    _contemplation = self._contemplative_reason(question)
-                    if _contemplation and len(_contemplation) > 30:
-                        _knowledge_result = _contemplation
-                        self._log(LogLevel.INFO, "降级到内在沉思成功")
-                    else:
-                        # 沉思也不行，调用大模型
-                        self._log(LogLevel.INFO, "内在沉思失败，降级到大模型")
-                        _model_result = self._generate_branch_with_model(
-                            original_question=question,
-                            branch_name="知识检索降级",
-                            branch_prompt=question,
-                        )
-                        if _model_result and len(_model_result) > 20:
-                            _knowledge_result = _model_result
-                            self._log(LogLevel.INFO, f"大模型降级成功: {_knowledge_result[:60]}...")
-                            # ★v23.0补充：将大模型结果消化为知识，存入InsightBoard
-                            try:
-                                if hasattr(self, '_insight_board') and self._insight_board:
-                                    self._insight_board.post(
-                                        insight_type="knowledge_boundary",
-                                        content=_knowledge_result[:200],
-                                        source_loop="知识检索降级·大模型生成",
-                                        related_dimension="知识补充",
-                                        confidence=0.6,
-                                        keywords=[question[:30], "大模型补充"]
-                                    )
-                            except Exception as e:
-                                self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
-                        else:
-                            _knowledge_result = None
-                # 降级链路结束
-
-                if _knowledge_result:
-                    self._inference_count += 1
-                    self._cache_inference(question, _knowledge_result, user_name)
-                self._trace_inference(question, _knowledge_result, "qica_knowledge", 0.75, user_name,
-                                     duration=time.time() - _reasoning_start_time,
-                                     complexity=_question_complexity,
-                                     tuning_hint="QICA建议知识检索")
-                _final = self._enhance_answer(
-                    answer=_knowledge_result, question=question, method="qica_knowledge",
-                    complexity=_question_complexity, empathetic_note=empathetic_note,
-                    memory_context=_memory_context
-                )
-                self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": _final,
-                    "method": "qica_knowledge", "confidence": 0.75, "user_name": user_name,
-                    "correlation_id": correlation_id,
-                    "confidence_hint": "moderate",
-                    "strategy_applied": payload.get("strategy_context", {}),
-                }, priority=7, layer="L2")
-                return {"status": "qica_knowledge", "answer": _knowledge_result}
-
+            _k = self._ir_qica_knowledge_retrieve(ctx, _qica_paths)
+            if _k is not None:
+                return _k
         if _qica_method == "cognitive_compute":
-            _cog_result = self._cognitive_compute(question)
+            _cog_result = self._cognitive_compute(ctx.question)
             if _cog_result:
                 self._inference_count += 1
-                self._cache_inference(question, _cog_result, user_name)
-                self._trace_inference(question, _cog_result, "qica_cognitive", 0.65, user_name,
-                                     duration=time.time() - _reasoning_start_time,
-                                     complexity=_question_complexity,
+                self._cache_inference(ctx.question, _cog_result, ctx.user_name)
+                self._trace_inference(ctx.question, _cog_result, "qica_cognitive", 0.65, ctx.user_name,
+                                     duration=time.time() - ctx._reasoning_start_time,
+                                     complexity=ctx._question_complexity,
                                      tuning_hint="QICA建议认知算子")
                 _final = self._enhance_answer(
-                    answer=_cog_result, question=question, method="qica_cognitive",
-                    complexity=_question_complexity, empathetic_note=empathetic_note,
-                    memory_context=_memory_context
+                    answer=_cog_result, question=ctx.question, method="qica_cognitive",
+                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context
                 )
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": _final,
-                    "method": "qica_cognitive", "confidence": 0.65, "user_name": user_name,
-                    "correlation_id": correlation_id,
+                    "question": ctx.question, "answer": _final,
+                    "method": "qica_cognitive", "confidence": 0.65, "user_name": ctx.user_name,
+                    "correlation_id": ctx.correlation_id,
                     "confidence_hint": "moderate",
-                    "strategy_applied": payload.get("strategy_context", {}),
+                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                 }, priority=7, layer="L2")
                 return {"status": "qica_cognitive", "answer": _cog_result}
         # ===== QICA建议方法优先执行结束 =====
+        return None
 
+    def _ir_run_pipeline(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
         # ===== v20.0新增：思考纪律——标准思维流水线入口 =====
         # 当所有检测器未命中时，按大脑皮层规划的流水线深度执行推理
-        _pipeline = payload.get("strategy_context", {}).get("thinking_pipeline", {})
+        _pipeline = ctx.payload.get("strategy_context", {}).get("thinking_pipeline", {})
         _pipeline_depth = _pipeline.get("depth", "standard")
 
         if _pipeline_depth == "quick":
             # 快速通道：仅知识检索，不经过复杂推理
-            self._log(LogLevel.DEBUG, f"思考纪律·快速通道: '{question[:40]}'")
-            _knowledge_result = self._knowledge_retrieve(question)
+            self._log(LogLevel.DEBUG, f"思考纪律·快速通道: '{ctx.question[:40]}'")
+            _knowledge_result = self._knowledge_retrieve(ctx.question)
             if _knowledge_result:
                 # v20.0新增：追加L3智慧节点的策略指导
-                _wisdom = self._get_wisdom_guidance(question)
+                _wisdom = self._get_wisdom_guidance(ctx.question)
                 if _wisdom:
                     _knowledge_result = _knowledge_result + "\n\n💡 " + _wisdom
                 # ★v23.0：标准通道检索结果验证降级
-                _validated = self._validate_and_degrade(question, _knowledge_result, "标准通道")
+                _validated = self._validate_and_degrade(ctx.question, _knowledge_result, "标准通道")
                 if _validated != _knowledge_result:
                     _knowledge_result = _validated
 
                 self._inference_count += 1
-                self._cache_inference(question, _knowledge_result, user_name)
+                self._cache_inference(ctx.question, _knowledge_result, ctx.user_name)
                 _final = self._enhance_answer(
-                    answer=_knowledge_result, question=question, method="thinking_discipline_quick",
-                    complexity=_question_complexity, empathetic_note=empathetic_note,
-                    memory_context=_memory_context
+                    answer=_knowledge_result, question=ctx.question, method="thinking_discipline_quick",
+                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context
                 )
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": _final,
-                    "method": "thinking_discipline_quick", "confidence": 0.85, "user_name": user_name,
-                    "correlation_id": correlation_id,
+                    "question": ctx.question, "answer": _final,
+                    "method": "thinking_discipline_quick", "confidence": 0.85, "user_name": ctx.user_name,
+                    "correlation_id": ctx.correlation_id,
                     "confidence_hint": "high",
-                    "strategy_applied": payload.get("strategy_context", {}),
+                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                 }, priority=7, layer="L2")
                 return {"status": "thinking_discipline_quick", "answer": _knowledge_result}
             # 快速通道未命中，降级到标准通道继续
@@ -840,39 +1272,39 @@ class PulseInnerWorld(
 
         if _pipeline_depth in ("standard", "quick"):
             # 标准通道：理解→检索→表达（快速通道降级也走此路径）
-            _knowledge_result = self._knowledge_retrieve(question)
+            _knowledge_result = self._knowledge_retrieve(ctx.question)
             if _knowledge_result:
                 # v20.0新增：追加L3智慧节点的策略指导
-                _wisdom = self._get_wisdom_guidance(question)
+                _wisdom = self._get_wisdom_guidance(ctx.question)
                 if _wisdom:
                     _knowledge_result = _knowledge_result + "\n\n💡 " + _wisdom
                 self._inference_count += 1
-                self._cache_inference(question, _knowledge_result, user_name)
+                self._cache_inference(ctx.question, _knowledge_result, ctx.user_name)
                 _final = self._enhance_answer(
-                    answer=_knowledge_result, question=question, method="thinking_discipline_standard",
-                    complexity=_question_complexity, empathetic_note=empathetic_note,
-                    memory_context=_memory_context
+                    answer=_knowledge_result, question=ctx.question, method="thinking_discipline_standard",
+                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context
                 )
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": _final,
-                    "method": "thinking_discipline_standard", "confidence": 0.75, "user_name": user_name,
-                    "correlation_id": correlation_id,
+                    "question": ctx.question, "answer": _final,
+                    "method": "thinking_discipline_standard", "confidence": 0.75, "user_name": ctx.user_name,
+                    "correlation_id": ctx.correlation_id,
                     "confidence_hint": "moderate",
-                    "strategy_applied": payload.get("strategy_context", {}),
+                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                 }, priority=7, layer="L2")
                 return {"status": "thinking_discipline_standard", "answer": _knowledge_result}
 
         if _pipeline_depth == "deep":
             # 深度通道：理解→检索→验证→深度思考→表达
-            self._log(LogLevel.INFO, f"思考纪律·深度通道: '{question[:60]}'")
+            self._log(LogLevel.INFO, f"思考纪律·深度通道: '{ctx.question[:60]}'")
             # 第一步：先检索知识作为基础
-            _knowledge_result = self._knowledge_retrieve(question)
+            _knowledge_result = self._knowledge_retrieve(ctx.question)
             # 第二步：深度思考
             _deep_result = None
             if hasattr(self, '_reasoning_pool') and self._reasoning_pool:
                 _future = None
                 try:
-                    _future = self._reasoning_pool.submit("PulseInnerWorld._deep_think", question, 3)
+                    _future = self._reasoning_pool.submit("PulseInnerWorld._deep_think", ctx.question, 3)
                     if _future:
                         # ★主线第31批 T1：降级标记（truthy dict）不得当结果用，
                         #   否则主进程同步回退被跳过、内部 dict 还会进入用户可见答案。
@@ -888,13 +1320,13 @@ class PulseInnerWorld(
                 # ★主线第31批 T1：主进程同步执行（子进程结果不可用时的正路）；
                 #   受第27批「自推理开始起算」的总预算约束，避免无限耗时。
                 _deep_result = self._deep_think(
-                    question,
-                    deadline=self._m31_deep_fallback_deadline(_reasoning_start_time))
+                    ctx.question,
+                    deadline=self._m31_deep_fallback_deadline(ctx._reasoning_start_time))
 
             # 第三步：综合知识检索和深度思考结果
             if _deep_result:
                 # v20.0新增：追加L3智慧节点的策略指导
-                _wisdom = self._get_wisdom_guidance(question)
+                _wisdom = self._get_wisdom_guidance(ctx.question)
                 _wisdom_text = f"\n\n💡 {_wisdom}" if _wisdom else ""
                 # 如果知识检索也有结果，融合两者
                 # ★主线第30批 T1：深度思考返回的是**降级标记**（如子进程无知识上下文）
@@ -915,201 +1347,97 @@ class PulseInnerWorld(
                 else:
                     _combined = _deep_result
                 self._inference_count += 1
-                self._cache_inference(question, _combined, user_name)
-                self._trace_inference(question, _combined, "thinking_discipline_deep", 0.7, user_name,
-                                     duration=time.time() - _reasoning_start_time,
-                                     complexity=_question_complexity,
+                self._cache_inference(ctx.question, _combined, ctx.user_name)
+                self._trace_inference(ctx.question, _combined, "thinking_discipline_deep", 0.7, ctx.user_name,
+                                     duration=time.time() - ctx._reasoning_start_time,
+                                     complexity=ctx._question_complexity,
                                      tuning_hint="思考纪律深度通道完成")
                 _final = self._enhance_answer(
-                    answer=_combined, question=question, method="thinking_discipline_deep",
-                    complexity=_question_complexity, empathetic_note=empathetic_note,
-                    memory_context=_memory_context
+                    answer=_combined, question=ctx.question, method="thinking_discipline_deep",
+                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context
                 )
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": _final,
-                    "method": "thinking_discipline_deep", "confidence": 0.7, "user_name": user_name,
-                    "correlation_id": correlation_id,
+                    "question": ctx.question, "answer": _final,
+                    "method": "thinking_discipline_deep", "confidence": 0.7, "user_name": ctx.user_name,
+                    "correlation_id": ctx.correlation_id,
                     "confidence_hint": "moderate",
-                    "strategy_applied": payload.get("strategy_context", {}),
+                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                     "thinking_discipline": True,
                 }, priority=7, layer="L2")
                 return {"status": "thinking_discipline_deep", "answer": _combined}
             elif _knowledge_result:
                 # 深度思考失败但知识检索有结果，按标准通道处理
                 self._inference_count += 1
-                self._cache_inference(question, _knowledge_result, user_name)
+                self._cache_inference(ctx.question, _knowledge_result, ctx.user_name)
                 _final = self._enhance_answer(
-                    answer=_knowledge_result, question=question, method="thinking_discipline_deep_fallback",
-                    complexity=_question_complexity, empathetic_note=empathetic_note,
-                    memory_context=_memory_context
+                    answer=_knowledge_result, question=ctx.question, method="thinking_discipline_deep_fallback",
+                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context
                 )
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": _final,
-                    "method": "thinking_discipline_deep_fallback", "confidence": 0.6, "user_name": user_name,
-                    "correlation_id": correlation_id,
+                    "question": ctx.question, "answer": _final,
+                    "method": "thinking_discipline_deep_fallback", "confidence": 0.6, "user_name": ctx.user_name,
+                    "correlation_id": ctx.correlation_id,
                     "confidence_hint": "moderate",
-                    "strategy_applied": payload.get("strategy_context", {}),
+                    "strategy_applied": ctx.payload.get("strategy_context", {}),
                 }, priority=7, layer="L2")
                 return {"status": "thinking_discipline_deep_fallback", "answer": _knowledge_result}
         # ===== v20.0思考纪律入口结束 =====
+        return None
 
-        # ★v23.0清理：v18.0标记的旧内联分支已由17个检测器完整覆盖，安全移除
-        # ★v17.0新增：情绪调制推理策略——调整复杂度阈值和检索深度
-        _modulated_complexity = _question_complexity
-        if _emotion_modulation.get("depth_factor", 1.0) > 1.1:
-            # 需要更深思时，降低触发深度思考的门槛
-            _modulated_complexity = min(1.0, _question_complexity + 0.15)
-            self._log(LogLevel.DEBUG,
-                     f"情绪调制(深度): 降低深度思考门槛 "
-                     f"({_question_complexity:.2f}→{_modulated_complexity:.2f})")
-        elif _emotion_modulation.get("depth_factor", 1.0) < 0.9:
-            # 需要更快决策时，提高触发深度思考的门槛
-            _modulated_complexity = max(0.0, _question_complexity - 0.1)
-
-        # ===== 阶段6新增：压力→策略调节闭环（应激轴调制推理策略）=====
-        _stress_modulation = self._get_stress_reasoning_modulation()
-        if _stress_modulation.get("depth_factor", 1.0) < 0.9:
-            # 高压：提高触发深度思考的门槛，优先更快更浅的决策
-            _modulated_complexity = max(0.0, _modulated_complexity - 0.1)
-
-        # ===== v20.0新增：情绪驱动的推理策略选择 =====
-        _strategy_pref = _emotion_modulation.get("strategy_preference", {})
-        _strategy_active = _strategy_pref.get("active", False)
-        _preferred_strategies = list(_strategy_pref.get("preferred", []))
-        _avoid_strategies = list(_strategy_pref.get("avoid", []))
-
-        # 压力策略并入情绪策略偏好（压力优先：拆分 + 降并行）
-        if _stress_modulation.get("prefer_decompose"):
-            if "multi_step_execute" not in _preferred_strategies:
-                _preferred_strategies.insert(0, "multi_step_execute")
-        if _stress_modulation.get("avoid_parallel"):
-            for _p in ("multi_branch_deep_think", "multi_branch"):
-                if _p not in _avoid_strategies:
-                    _avoid_strategies.append(_p)
-        if _stress_modulation.get("prefer_decompose") or _stress_modulation.get("avoid_parallel"):
-            _strategy_active = True
-
-        if _strategy_active and (_preferred_strategies or _avoid_strategies):
-            # 策略偏好激活时，尝试优先策略列表中的方法
-            _strategy_result = None
-
-            # 1. 优先策略：按顺序尝试优先列表中的推理策略
-            for _strat in _preferred_strategies:
-                if _strat == "creative_solution":
-                    _strategy_result = self._attempt_creative_solution(question)
-                elif _strat == "experience_route":
-                    # 尝试经验匹配路由（已在检测器中，这里做补充尝试）
-                    try:
-                        from nucleus.mnemosyne.ReasoningExperience import (
-                            get_reasoning_experience,
-                        )
-                        _exp = get_reasoning_experience()
-                        _match = _exp.search(question)
-                        if _match and _match.get("confidence", 0) >= 0.4:
-                            _derivation_type = _match.get("derivation_type", "")
-                            if _derivation_type:
-                                _strategy_result = self._route_to_deriver(
-                                    question, user_name, _reasoning_start_time,
-                                    _question_complexity, empathetic_note,
-                                    _memory_context, payload, guidance
-                                )
-                    except Exception as e:
-                        self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
-                elif _strat == "cache":
-                    _cached = self._inference_cache.get(f"{user_name}:{question.strip()}")
-                    if not _cached:
-                        _cached = self._inference_cache.get(f"用户:{question.strip()}")
-                    if _cached:
-                        _cached_answer = _cached.get("answer", "")
-                        if _cached_answer:
-                            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
-                            self._cache_hit_count += 1
-                            _strategy_result = _cached_answer
-                elif _strat == "rule_reason":
-                    _strategy_result = self._rule_reason(question, user_name, guidance)
-                elif _strat == "contemplation":
-                    _strategy_result = self._contemplative_reason(question)
-
-                if _strategy_result:
-                    _method = f"emotion_strategy_{_strat}"
-                    self._inference_count += 1
-                    self._cache_inference(question, _strategy_result, user_name)
-                    self._trace_inference(question, _strategy_result, _method, 0.65, user_name,
-                                         duration=time.time() - _reasoning_start_time,
-                                         complexity=_question_complexity,
-                                         tuning_hint=f"情绪策略偏好触发({_strategy_pref.get('description', '')})")
-                    _final = self._enhance_answer(
-                        answer=_strategy_result, question=question, method=_method,
-                        complexity=_question_complexity, empathetic_note=empathetic_note,
-                        memory_context=_memory_context
-                    )
-                    self._emit(InferenceEvent.RESULT, {
-                        "question": question, "answer": _final,
-                        "method": _method, "confidence": 0.65, "user_name": user_name,
-                        "correlation_id": correlation_id,
-                        "confidence_hint": "moderate",
-                        "strategy_applied": payload.get("strategy_context", {}),
-                        "emotion_strategy": True,
-                    }, priority=7, layer="L2")
-                    self._log(LogLevel.INFO,
-                             f"情绪策略命中: {_emotion_modulation.get('emotion', '中性')}→{_strat} '{question[:40]}'")
-                    return {"status": _method, "answer": _strategy_result}
-
-            # 2. 如果优先策略都未命中，且当前路由类型在避免列表中，回退到知识检索
-            # （避免列表中的策略在下方的_route_to_deriver中被跳过，这里只做记录）
-            if _avoid_strategies:
-                self._log(LogLevel.DEBUG,
-                         f"情绪策略避免: {_emotion_modulation.get('emotion', '中性')}→跳过{_avoid_strategies}")
-        # ===== v20.0新增结束 =====
-
-        # ===== 推理问题前置过滤结束 =====
-        _derivation_answer = self._route_to_deriver(question, user_name, _reasoning_start_time,
-                                                      _question_complexity, empathetic_note,
-                                                      _memory_context, payload, guidance)
-        if _derivation_answer:
+    def _ir_try_derivation(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
+        ctx._derivation_answer = self._route_to_deriver(ctx.question, ctx.user_name, ctx._reasoning_start_time,
+                                                      ctx._question_complexity, ctx.empathetic_note,
+                                                      ctx._memory_context, ctx.payload, ctx.guidance)
+        if ctx._derivation_answer:
             # ★v22.0方向三修复v3：相关性检查——经验路由结果与问题无关时，跳过
-            _derivation_content = _derivation_answer.get("answer", "")
+            _derivation_content = ctx._derivation_answer.get("answer", "")
             if _derivation_content and len(str(_derivation_content)) > 20:
-                _question_core = set(re.findall(r'[\u4e00-\u9fff]{2,4}', question)[:5])
+                _question_core = set(re.findall(r'[\u4e00-\u9fff]{2,4}', ctx.question)[:5])
                 _answer_core = set(re.findall(r'[\u4e00-\u9fff]{2,4}', str(_derivation_content)[:200]))
                 _overlap = len(_question_core & _answer_core)
                 if _overlap < 1:
                     self._log(LogLevel.INFO,
                              f"经验路由跳过(相关性低): 问题核心词={_question_core}, "
                              f"答案核心词={_answer_core}, 重叠={_overlap}")
-                    _derivation_answer = None  # 跳过，让流程继续到知识检索
+                    ctx._derivation_answer = None  # 跳过，让流程继续到知识检索
 
-            if _derivation_answer:
+            if ctx._derivation_answer:
                 try:
                     from nucleus.mnemosyne.ReasoningExperience import (
                         get_reasoning_experience,
                     )
                     _reasoning_exp = get_reasoning_experience()
-                    _status = _derivation_answer.get("status", "")
+                    _status = ctx._derivation_answer.get("status", "")
                     if _status.startswith("deriver_"):
                         _derivation_type_record = _status.replace("deriver_", "")
-                        _reasoning_exp.record(question, _derivation_type_record, source="local")
+                        _reasoning_exp.record(ctx.question, _derivation_type_record, source="local")
                         self._log(LogLevel.DEBUG, f"经验沉淀: 类型={_derivation_type_record}")
                 except Exception as e:
                     self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
-                return _derivation_answer
+                return ctx._derivation_answer
+        return None
+
+
+    def _ir_decompose_and_deep_read(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
         # 使用大脑皮层传来的工具提示
-        tool_hint = payload.get("tool_hint", {})
-        if tool_hint.get("should_search", False):
-            self._log(LogLevel.DEBUG, f"工具提示: 问题='{question[:30]}' 建议搜索")
+        ctx.tool_hint = ctx.payload.get("tool_hint", {})
+        if ctx.tool_hint.get("should_search", False):
+            self._log(LogLevel.DEBUG, f"工具提示: 问题='{ctx.question[:30]}' 建议搜索")
         else:
             # 工具认知层建议不搜索：将在元认知决策阶段处理
             pass
         # ===== 新增: 复杂问题自主拆解 =====
-        _is_param_format = ("已知：" in question or "已知:" in question) and ("=" in question or "＝" in question)
-        _is_rule_format = any(_kw in question for _kw in ["规则1", "规则2", "规则3", "第一，", "第二，", "第三，", "如果", "那么"])
+        _is_param_format = ("已知：" in ctx.question or "已知:" in ctx.question) and ("=" in ctx.question or "＝" in ctx.question)
+        _is_rule_format = any(_kw in ctx.question for _kw in ["规则1", "规则2", "规则3", "第一，", "第二，", "第三，", "如果", "那么"])
         if _is_param_format and not _is_rule_format:
             sub_questions = None
         else:
-            sub_questions = self._decompose_complex_question(question)
+            sub_questions = self._decompose_complex_question(ctx.question)
         if sub_questions and len(sub_questions) >= 2:
             self._log(LogLevel.INFO,
-                     f"问题拆解: 将'{question[:40]}'拆分为{len(sub_questions)}个子问题")
+                     f"问题拆解: 将'{ctx.question[:40]}'拆分为{len(sub_questions)}个子问题")
             # 逐个推理子问题
             sub_results = []
             for sq in sub_questions:
@@ -1120,47 +1448,47 @@ class PulseInnerWorld(
                     sub_results.append({"question": sq, "answer": None, "found": False})
             # 综合子问题结果
             if any(r["found"] for r in sub_results):
-                composite_answer = self._compose_sub_results(question, sub_results)
+                composite_answer = self._compose_sub_results(ctx.question, sub_results)
                 if composite_answer:
                     self._inference_count += 1
-                    self._cache_inference(question, composite_answer, user_name)
-                    self._trace_inference(question, composite_answer, "decompose", 0.75, user_name,
-                                         duration=time.time() - _reasoning_start_time,
-                                         complexity=_question_complexity,
+                    self._cache_inference(ctx.question, composite_answer, ctx.user_name)
+                    self._trace_inference(ctx.question, composite_answer, "decompose", 0.75, ctx.user_name,
+                                         duration=time.time() - ctx._reasoning_start_time,
+                                         complexity=ctx._question_complexity,
                                          tuning_hint="复杂问题拆解成功，分解策略有效")
                     final_answer = self._enhance_answer(
                         answer=composite_answer,
-                        question=question,
+                        question=ctx.question,
                         method="decompose",
-                        complexity=_question_complexity,
-                        empathetic_note=empathetic_note,
-                        memory_context=_memory_context
+                        complexity=ctx._question_complexity,
+                        empathetic_note=ctx.empathetic_note,
+                        memory_context=ctx._memory_context
                     )
                     self._emit(InferenceEvent.RESULT, {
-                        "question": question, "answer": final_answer,
-                        "method": "decompose", "confidence": 0.75, "user_name": user_name,
-                        "correlation_id": payload.get("correlation_id", ""),
-                        "strategy_applied": payload.get("strategy_context", {}),
+                        "question": ctx.question, "answer": final_answer,
+                        "method": "decompose", "confidence": 0.75, "user_name": ctx.user_name,
+                        "correlation_id": ctx.payload.get("correlation_id", ""),
+                        "strategy_applied": ctx.payload.get("strategy_context", {}),
                         "confidence_hint": "moderate",
                     }, priority=7, layer="L2")
                     return {"status": "decompose_match", "answer": composite_answer}
         # ===== 新增: 思考停顿——复杂问题优先走深度思考模式 =====
-        complexity_score = self._assess_question_complexity(question)
+        complexity_score = self._assess_question_complexity(ctx.question)
         _deep_concept_words = ["智慧", "自由", "意义", "本质", "真理", "存在", "意识", "爱", "幸福本质", "价值"]
-        _has_deep_concept = any(_dw in question for _dw in _deep_concept_words)
+        _has_deep_concept = any(_dw in ctx.question for _dw in _deep_concept_words)
         if _has_deep_concept:
             complexity_score = max(complexity_score, 0.65)  # 强制提高复杂度
         if complexity_score >= 0.6:
             # 高复杂度问题：先尝试沉思，再回退到知识检索
             self._log(LogLevel.INFO,
-                     f"思考停顿: 问题复杂度={complexity_score:.2f}，进入深度思考模式: {question[:40]}")
+                     f"思考停顿: 问题复杂度={complexity_score:.2f}，进入深度思考模式: {ctx.question[:40]}")
             if self.node_pool:
                 deep_answer = None
                 if hasattr(self, '_reasoning_pool') and self._reasoning_pool:
                     _future = None
                     try:
                         _future = self._reasoning_pool.submit(
-                            "PulseInnerWorld._deep_think", question, 3
+                            "PulseInnerWorld._deep_think", ctx.question, 3
                         )
                         if _future:
                             # ★主线第31批 T1：统一判定，见 _m31_accept_subproc_deep_result
@@ -1175,292 +1503,189 @@ class PulseInnerWorld(
                 if not deep_answer:
                     # ★主线第31批 T1：主进程同步执行 + 第27批预算口径
                     deep_answer = self._deep_think(
-                        question,
-                        deadline=self._m31_deep_fallback_deadline(_reasoning_start_time))
+                        ctx.question,
+                        deadline=self._m31_deep_fallback_deadline(ctx._reasoning_start_time))
                 if deep_answer:
                     self._inference_count += 1
-                    self._cache_inference(question, deep_answer, user_name)
-                    self._trace_inference(question, deep_answer, "deep_think", 0.55, user_name,
-                                         duration=time.time() - _reasoning_start_time,
-                                         complexity=_question_complexity,
+                    self._cache_inference(ctx.question, deep_answer, ctx.user_name)
+                    self._trace_inference(ctx.question, deep_answer, "deep_think", 0.55, ctx.user_name,
+                                         duration=time.time() - ctx._reasoning_start_time,
+                                         complexity=ctx._question_complexity,
                                          tuning_hint="深度思考流水线完成，多维度综合")
                     final_answer = self._enhance_answer(
                         answer=deep_answer,
-                        question=question,
+                        question=ctx.question,
                         method="deep_think",
-                        complexity=_question_complexity,
-                        empathetic_note=empathetic_note,
-                        memory_context=_memory_context
+                        complexity=ctx._question_complexity,
+                        empathetic_note=ctx.empathetic_note,
+                        memory_context=ctx._memory_context
                     )
                     self._emit(InferenceEvent.RESULT, {
-                        "question": question, "answer": final_answer,
-                        "method": "deep_think", "confidence": 0.55, "user_name": user_name,
-                        "correlation_id": payload.get("correlation_id", ""),
-                        "strategy_applied": payload.get("strategy_context", {}),
+                        "question": ctx.question, "answer": final_answer,
+                        "method": "deep_think", "confidence": 0.55, "user_name": ctx.user_name,
+                        "correlation_id": ctx.payload.get("correlation_id", ""),
+                        "strategy_applied": ctx.payload.get("strategy_context", {}),
                         "confidence_hint": "moderate",
                         "thinking_pause": True,
                     }, priority=7, layer="L2")
                     return {"status": "deep_think", "answer": deep_answer}
                 # 流水线未产生结果，回退到原有沉思逻辑
-                contemplative_answer = self._contemplative_reason(question)
-                if contemplative_answer:
+                ctx.contemplative_answer = self._contemplative_reason(ctx.question)
+                if ctx.contemplative_answer:
                     self._inference_count += 1
-                    self._cache_inference(question, contemplative_answer, user_name)
-                    self._trace_inference(question, contemplative_answer, "deep_contemplation", 0.6, user_name,
-                                         duration=time.time() - _reasoning_start_time,
-                                         complexity=_question_complexity,
+                    self._cache_inference(ctx.question, ctx.contemplative_answer, ctx.user_name)
+                    self._trace_inference(ctx.question, ctx.contemplative_answer, "deep_contemplation", 0.6, ctx.user_name,
+                                         duration=time.time() - ctx._reasoning_start_time,
+                                         complexity=ctx._question_complexity,
                                          tuning_hint="高复杂度问题，已进入深度思考模式")
                     final_answer = self._enhance_answer(
-                        answer=contemplative_answer,
-                        question=question,
+                        answer=ctx.contemplative_answer,
+                        question=ctx.question,
                         method="deep_contemplation",
-                        complexity=_question_complexity,
-                        empathetic_note=empathetic_note,
-                        memory_context=_memory_context
+                        complexity=ctx._question_complexity,
+                        empathetic_note=ctx.empathetic_note,
+                        memory_context=ctx._memory_context
                     )
                     self._emit(InferenceEvent.RESULT, {
-                        "question": question, "answer": final_answer,
-                        "method": "deep_contemplation", "confidence": 0.6, "user_name": user_name,
-                        "correlation_id": payload.get("correlation_id", ""),
-                        "strategy_applied": payload.get("strategy_context", {}),
+                        "question": ctx.question, "answer": final_answer,
+                        "method": "deep_contemplation", "confidence": 0.6, "user_name": ctx.user_name,
+                        "correlation_id": ctx.payload.get("correlation_id", ""),
+                        "strategy_applied": ctx.payload.get("strategy_context", {}),
                         "confidence_hint": "moderate",
                         "thinking_pause": True,
                     }, priority=7, layer="L2")
-                    return {"status": "deep_contemplation", "answer": contemplative_answer}
+                    return {"status": "deep_contemplation", "answer": ctx.contemplative_answer}
+        return None
 
-        # ★v17.0新增：认知边界感知——记录推理失败的领域
-        if not _derivation_answer:
-            _failed_keywords = []
-            for _match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
-                _word = _match.group()
-                if _word not in _failed_keywords and _word not in [
-                    "什么是", "是什么", "为什么", "如何", "怎么",
-                    "这个", "那个", "一个", "一种", "可以", "能够",
-                ]:
-                    _failed_keywords.append(_word)
+    def _ir_apply_modulations(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
+        # ★v23.0清理：v18.0标记的旧内联分支已由17个检测器完整覆盖，安全移除
+        # ★v17.0新增：情绪调制推理策略——调整复杂度阈值和检索深度
+        _modulated_complexity = ctx._question_complexity
+        if ctx._emotion_modulation.get("depth_factor", 1.0) > 1.1:
+            # 需要更深思时，降低触发深度思考的门槛
+            _modulated_complexity = min(1.0, ctx._question_complexity + 0.15)
+            self._log(LogLevel.DEBUG,
+                     f"情绪调制(深度): 降低深度思考门槛 "
+                     f"({ctx._question_complexity:.2f}→{_modulated_complexity:.2f})")
+        elif ctx._emotion_modulation.get("depth_factor", 1.0) < 0.9:
+            # 需要更快决策时，提高触发深度思考的门槛
+            _modulated_complexity = max(0.0, ctx._question_complexity - 0.1)
 
-            if _failed_keywords:
-                if not hasattr(self, '_failed_domain_records'):
-                    self._failed_domain_records = {}
-                for _kw in _failed_keywords[:5]:
-                    self._failed_domain_records[_kw] = self._failed_domain_records.get(_kw, 0) + 1
+        # ===== 阶段6新增：压力→策略调节闭环（应激轴调制推理策略）=====
+        ctx._stress_modulation = self._get_stress_reasoning_modulation()
+        if ctx._stress_modulation.get("depth_factor", 1.0) < 0.9:
+            # 高压：提高触发深度思考的门槛，优先更快更浅的决策
+            _modulated_complexity = max(0.0, _modulated_complexity - 0.1)
 
-                if not hasattr(self, '_failed_domain_log_count'):
-                    self._failed_domain_log_count = 0
-                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
-                self._failed_domain_log_count += 1
-                if self._failed_domain_log_count % 3 == 0:
-                    _top_failed = sorted(self._failed_domain_records.items(), key=lambda x: x[1], reverse=True)[:3]
+        # ===== v20.0新增：情绪驱动的推理策略选择 =====
+        _strategy_pref = ctx._emotion_modulation.get("strategy_preference", {})
+        _strategy_active = _strategy_pref.get("active", False)
+        _preferred_strategies = list(_strategy_pref.get("preferred", []))
+        _avoid_strategies = list(_strategy_pref.get("avoid", []))
+
+        # 压力策略并入情绪策略偏好（压力优先：拆分 + 降并行）
+        if ctx._stress_modulation.get("prefer_decompose"):
+            if "multi_step_execute" not in _preferred_strategies:
+                _preferred_strategies.insert(0, "multi_step_execute")
+        if ctx._stress_modulation.get("avoid_parallel"):
+            for _p in ("multi_branch_deep_think", "multi_branch"):
+                if _p not in _avoid_strategies:
+                    _avoid_strategies.append(_p)
+        if ctx._stress_modulation.get("prefer_decompose") or ctx._stress_modulation.get("avoid_parallel"):
+            _strategy_active = True
+
+        if _strategy_active and (_preferred_strategies or _avoid_strategies):
+            # 策略偏好激活时，尝试优先策略列表中的方法
+            _strategy_result = None
+
+            # 1. 优先策略：按顺序尝试优先列表中的推理策略
+            for _strat in _preferred_strategies:
+                if _strat == "creative_solution":
+                    _strategy_result = self._attempt_creative_solution(ctx.question)
+                elif _strat == "experience_route":
+                    # 尝试经验匹配路由（已在检测器中，这里做补充尝试）
+                    try:
+                        from nucleus.mnemosyne.ReasoningExperience import (
+                            get_reasoning_experience,
+                        )
+                        _exp = get_reasoning_experience()
+                        _match = _exp.search(ctx.question)
+                        if _match and _match.get("confidence", 0) >= 0.4:
+                            _derivation_type = _match.get("derivation_type", "")
+                            if _derivation_type:
+                                _strategy_result = self._route_to_deriver(
+                                    ctx.question, ctx.user_name, ctx._reasoning_start_time,
+                                    ctx._question_complexity, ctx.empathetic_note,
+                                    ctx._memory_context, ctx.payload, ctx.guidance
+                                )
+                    except Exception as e:
+                        self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
+                elif _strat == "cache":
+                    _cached = self._inference_cache.get(f"{ctx.user_name}:{ctx.question.strip()}")
+                    if not _cached:
+                        _cached = self._inference_cache.get(f"用户:{ctx.question.strip()}")
+                    if _cached:
+                        _cached_answer = _cached.get("answer", "")
+                        if _cached_answer:
+                            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
+                            self._cache_hit_count += 1
+                            _strategy_result = _cached_answer
+                elif _strat == "rule_reason":
+                    _strategy_result = self._rule_reason(ctx.question, ctx.user_name, ctx.guidance)
+                elif _strat == "contemplation":
+                    _strategy_result = self._contemplative_reason(ctx.question)
+
+                if _strategy_result:
+                    _method = f"emotion_strategy_{_strat}"
+                    self._inference_count += 1
+                    self._cache_inference(ctx.question, _strategy_result, ctx.user_name)
+                    self._trace_inference(ctx.question, _strategy_result, _method, 0.65, ctx.user_name,
+                                         duration=time.time() - ctx._reasoning_start_time,
+                                         complexity=ctx._question_complexity,
+                                         tuning_hint=f"情绪策略偏好触发({_strategy_pref.get('description', '')})")
+                    _final = self._enhance_answer(
+                        answer=_strategy_result, question=ctx.question, method=_method,
+                        complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
+                        memory_context=ctx._memory_context
+                    )
+                    self._emit(InferenceEvent.RESULT, {
+                        "question": ctx.question, "answer": _final,
+                        "method": _method, "confidence": 0.65, "user_name": ctx.user_name,
+                        "ctx.correlation_id": ctx.correlation_id,
+                        "confidence_hint": "moderate",
+                        "strategy_applied": ctx.payload.get("strategy_context", {}),
+                        "emotion_strategy": True,
+                    }, priority=7, layer="L2")
                     self._log(LogLevel.INFO,
-                             f"认知边界记录: 推理失败已累计{self._failed_domain_log_count}次, "
-                             f"高频失败领域={_top_failed}")
+                             f"情绪策略命中: {ctx._emotion_modulation.get('emotion', '中性')}→{_strat} '{ctx.question[:40]}'")
+                    return {"status": _method, "answer": _strategy_result}
 
-        # 知识检索
-        # ★v22.0重构：如果大脑皮层给出了建议路径，优先在建议路径下检索
-        _qica_paths = payload.get("strategy_context", {}).get("knowledge_paths", [])  # type: ignore[possibly-unbound]
-        # ★第九批 3.4（星轨 P2-9）：QICA 建议 /人物/{人名} 时先查身份知识库。
-        #   此前知识树里没有这些路径，检索必然落空，于是「小林是谁」每次都重新瞎猜。
-        _identity_hit = self._identity_lookup(question)  # type: ignore[possibly-unbound]
-        if _identity_hit:
-            self._log(LogLevel.INFO,  # type: ignore[possibly-undefined]
-                     f"身份知识命中: {_identity_hit[:40]}")
-        if _identity_hit:
-            knowledge_answer = _identity_hit  # type: ignore[possibly-unbound]
-        elif _qica_paths:  # type: ignore[possibly-unbound]
-            _path_knowledge = None  # type: ignore[possibly-unbound]
-            for _path in _qica_paths[:3]:  # type: ignore[possibly-unbound]
-                _nodes = self.node_pool.query(
-                    evol_level="L3", space_path_prefix=_path, limit=10  # type: ignore[possibly-unbound]
-                ) if self.node_pool else []
-                if _nodes:
-                    _val = str(_nodes[0].value) if _nodes[0].value else ""
-                    if _val and len(_val) > 20:
-                        _path_knowledge = _val[:200]  # type: ignore[possibly-unbound]
-                        self._log(LogLevel.INFO, f"QICA路径优先检索: 路径={_path}, 命中={len(_nodes)}个节点")  # type: ignore[possibly-unbound]
-                        break
-            knowledge_answer = _path_knowledge or self._knowledge_retrieve(question)  # type: ignore[possibly-unbound]
-        else:
-            knowledge_answer = self._knowledge_retrieve(question)
-        if knowledge_answer:
-            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
-            self._inference_count += 1
-            self._cache_inference(question, knowledge_answer, user_name)
-            knowledge_hint = self._get_confidence_hint(question)
-            confidence_map = {"certain": 1.0, "high": 0.85, "moderate": 0.7, "low": 0.5}
-            confidence = confidence_map.get(knowledge_hint, 0.6)
-            duration = time.time() - _reasoning_start_time
-            tuning = ""
-            if knowledge_hint == "low":
-                tuning = "知识检索质量偏低，可能需要补充此领域知识"
-            elif knowledge_hint == "high":
-                tuning = "知识检索质量高，此领域认知扎实"
-            self._trace_inference(question, knowledge_answer, f"knowledge_{knowledge_hint}",
-                                 confidence, user_name,
-                                 duration=duration, complexity=_question_complexity,
-                                 tuning_hint=tuning)
-            # ===== ★v22.0方向三修复：知识边界感知——检测到低质量检索时自动生成追问 =====
-            _boundary_inquiry = self._detect_knowledge_boundary_and_inquire(
-                question=question,
-                knowledge_result=knowledge_answer,
-                contemplative_result=contemplative_answer if 'contemplative_answer' in dir() else None,
-                confidence=0.3 if knowledge_hint == "low" else 0.5,
-            )
-            if _boundary_inquiry:
-                self._emit(GrowthEvent.NEED_DETECTED, {
-                    "milestone": "知识边界延伸",
-                    "gaps": [{"metric": "knowledge_boundary", "current": 0, "target": 1}],
-                    "suggestion": _boundary_inquiry,
-                    "current_level": {"original_question": question[:80], "boundary": _boundary_inquiry},
-                    "growth_topic": _boundary_inquiry[:60],
-                }, priority=5, layer="L3")
-                self._log(LogLevel.INFO, f"知识边界延伸: 生成追问 '{_boundary_inquiry[:60]}'")
-            # ===== ★v22.0方向三修复结束 =====
+            # 2. 如果优先策略都未命中，且当前路由类型在避免列表中，回退到知识检索
+            # （避免列表中的策略在下方的_route_to_deriver中被跳过，这里只做记录）
+            if _avoid_strategies:
+                self._log(LogLevel.DEBUG,
+                         f"情绪策略避免: {ctx._emotion_modulation.get('emotion', '中性')}→跳过{_avoid_strategies}")
+        # ===== v20.0新增结束 =====
 
-            # ===== 新增: 自适应回答深度——根据关系和语境调整表达 =====
-            knowledge_answer = self._adapt_answer_depth(knowledge_answer, user_name, guidance, question)
-            # ===== 新增: 不确定性诚实表达——让回答更真实可信 =====
-            knowledge_answer = self._add_uncertainty_note(knowledge_answer, knowledge_hint, user_name)
-            # ===== 新增: 费曼解释——用自己的话重新组织答案 =====
-            if len(knowledge_answer) > 120 or any(
-                prefix in knowledge_answer for prefix in ["[主动学习", "[架构]", "[知识]", "[复盘认知", "相关知识汇总"]
-            ):
-                feynman_version = self._generate_feynman_explanation(question, knowledge_answer)
-                if feynman_version:
-                    knowledge_answer = feynman_version
-                    self._log(LogLevel.INFO, f"费曼解释: 将复杂知识转化为简单表达: {question[:30]}")
-            # ===== 新增: 本质追问——在答案基础上进行深层探究 =====
-            essence_question = self._generate_essence_inquiry(question, knowledge_answer)
-            # ===== 统一增强答案 =====
-            final_knowledge_answer = self._enhance_answer(
-                answer=knowledge_answer,
-                question=question,
-                method="knowledge",
-                complexity=_question_complexity,
-                empathetic_note=empathetic_note,
-                memory_context=_memory_context
-            )
-            # ===== 【v15.1修复】_supplement_topic 提前初始化 =====
-            if knowledge_hint == "moderate" and self.node_pool:
-                _supplement_topic = self._build_supplement_search_topic(question, knowledge_answer)
-            if correlation_id:
-                self._active_search_correlation[search_query[:80]] = correlation_id
-                if _supplement_topic:
-                    self._emit(Event.CONTROLLER_OPEN_URL, {
-                        "url": f"https://lite.duckduckgo.com/lite/?q={_supplement_topic[:80]}",
-                        "reason": f"知识补充搜索: {_supplement_topic[:40]}",
-                        "search_topic": _supplement_topic[:80],
-                        "deep_search": True,
-                        "search_intent": "curiosity",
-                        "search_correlation_id": correlation_id,
-                    }, priority=2, layer="L3")
-                    self._log(LogLevel.INFO, f"知识补充搜索: '{_supplement_topic[:40]}' (检索置信度={knowledge_hint})")
-            self._emit(InferenceEvent.RESULT, {
-                "question": question, "answer": final_knowledge_answer,
-                "method": "knowledge", "confidence": 0.7, "user_name": user_name,
-                "correlation_id": payload.get("correlation_id", ""),
-                "confidence_hint": knowledge_hint,
-                "strategy_applied": payload.get("strategy_context", {}),
-                "essence_inquiry": essence_question,
-            }, priority=7, layer="L2")
-            # ===== 新增: 自主建议生成——基于理解主动提供帮助 =====
-            proactive_suggestion = self._generate_proactive_suggestion(question, knowledge_answer, user_name)
-            if proactive_suggestion:
-                knowledge_answer = knowledge_answer + " " + proactive_suggestion
-                self._log(LogLevel.INFO, f"自主建议生成: 为'{user_name}'提供基于'{question[:30]}'的建议")
-            # ===== 新增: 情感记忆绑定——回忆触发情绪复现 =====
-            self._trigger_emotional_memory(knowledge_answer)
-            # ===== 新增: 知识自省与修正——根据检索质量强化或标记节点 =====
-            self._reflect_and_reinforce_knowledge(question, knowledge_answer)
-            # ===== 新增: 实践验证——主动构造验证场景 =====
-            verification = self._attempt_practical_verification(question, knowledge_answer)
-            if verification:
-                self._emit(verification["event_type"], verification["payload"],
-                          priority=verification.get("priority", 4),
-                          layer=verification.get("layer", "L2"))
-                self._log(LogLevel.INFO,
-                         f"实践验证: {verification.get('description', '')[:80]}")
-            # ===== 新增: 自主视角构建——从不同角度审视问题 =====
-            alternative_perspective = self._generate_alternative_perspective(question, knowledge_answer)
-            if alternative_perspective:
-                self._log(LogLevel.INFO, f"视角构建: {alternative_perspective[:80]}")
-                self._emit(GrowthEvent.NEED_DETECTED, {
-                    "milestone": "视角拓展",
-                    "gaps": [{"metric": "perspective", "current": 0, "target": 1}],
-                    "suggestion": alternative_perspective,
-                    "current_level": {
-                        "original_question": question,
-                        "perspective": alternative_perspective,
-                    },
-                    "growth_topic": alternative_perspective[:60],
-                }, priority=3, layer="L3")
-            # ===== 新增: 认知框架迁移——跨领域类比 =====
-            framework_transfer = self._attempt_framework_transfer(question, knowledge_answer)
-            if framework_transfer:
-                self._log(LogLevel.INFO,
-                         f"认知框架迁移: {framework_transfer.get('insight', '')[:80]}")
-                # 将迁移洞察作为探索种子
-                self._emit(GrowthEvent.NEED_DETECTED, {
-                    "milestone": "框架迁移",
-                    "gaps": [{"metric": "cross_domain", "current": 0, "target": 1}],
-                    "suggestion": framework_transfer.get("insight", ""),
-                    "current_level": {
-                        "source_question": question,
-                        "transferred_from": framework_transfer.get("source_domain", ""),
-                        "transferred_concept": framework_transfer.get("core_concept", ""),
-                    },
-                    "growth_topic": framework_transfer.get("explore_topic", question[:60]),
-                }, priority=3, layer="L3")
-            # 本质追问结果作为新的探索种子
-            if essence_question:
-                self._emit(GrowthEvent.NEED_DETECTED, {
-                    "milestone": "本质追问",
-                    "gaps": [{"metric": "deep_understanding", "current": 0, "target": 1}],
-                    "suggestion": essence_question,
-                    "current_level": {"original_question": question, "answer": knowledge_answer[:100]},
-                    "growth_topic": essence_question[:60],
-                }, priority=3, layer="L3")
-            return {"status": "knowledge_match", "answer": knowledge_answer}
-        # 内在沉思引擎——知识检索未命中时，基于已有知识进行推演
-        if self.node_pool:
-            contemplative_answer = self._contemplative_reason(question)
-            if contemplative_answer:
-                self._inference_count += 1
-                self._cache_inference(question, contemplative_answer, user_name)
-                self._trace_inference(question, contemplative_answer, "contemplation", 0.5, user_name,
-                                     duration=time.time() - _reasoning_start_time,
-                                     complexity=_question_complexity,
-                                     tuning_hint="沉思推演完成，需要后续验证")
-                final_answer = self._enhance_answer(
-                    answer=contemplative_answer,
-                    question=question,
-                    method="contemplation",
-                    complexity=_question_complexity,
-                    empathetic_note=empathetic_note,
-                    memory_context=_memory_context
-                )
-                self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": final_answer,
-                    "method": "contemplation", "confidence": 0.5, "user_name": user_name,
-                    "correlation_id": payload.get("correlation_id", ""),
-                    "strategy_applied": payload.get("strategy_context", {}),
-                    "confidence_hint": "low",
-                }, priority=6, layer="L2")
-                return {"status": "contemplation_match", "answer": contemplative_answer}
+        # ===== 推理问题前置过滤结束 =====
+        return None
+
+    def _ir_plan_tools(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
         # ===== 元认知决策：推理结束后的行动闭环（增强版） =====
-        _strategy_context = payload.get("strategy_context", {})
-        tool_requested = False
+        ctx._strategy_context = ctx.payload.get("strategy_context", {})
+        ctx.tool_requested = False
         # ===== 新增: 状态感知调制——根据内在状态调整工具选择倾向 =====
-        fallback_tools = _strategy_context.get("fallback_approach", [])
+        ctx.fallback_tools = ctx._strategy_context.get("fallback_approach", [])
         # ===== 新增：复杂问题直接走大模型，不走搜索 =====
-        _has_remote_api = False
+        ctx._has_remote_api = False
         try:
             import config as _cfg_check
             _api_cfg = getattr(_cfg_check, 'REMOTE_API_CONFIG', {})
             if _api_cfg.get("enabled", False) and _api_cfg.get("api_key", ""):
-                _has_remote_api = True
+                ctx._has_remote_api = True
         except Exception as e:
             self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
-        if _question_complexity > 0.4 and _has_remote_api:
+        if ctx._question_complexity > 0.4 and ctx._has_remote_api:
             # ★P1-1(2026-09-03)：大模型调用前置思考——即使知识检索/沉思未直接命中，
             #   也快速检索相关知识作为上下文传给大模型，让大模型基于框架本地认知补充，
             #   而非从零开始回答。减少大模型依赖，提高回答准确性。
@@ -1468,7 +1693,7 @@ class PulseInnerWorld(
             try:
                 if self.node_pool:
                     _hint_results = self.node_pool.query(
-                        question, limit=3, min_relevance=0.3)
+                        ctx.question, limit=3, min_relevance=0.3)
                     if _hint_results:
                         _hints = []
                         for _h in _hint_results[:3]:
@@ -1484,32 +1709,32 @@ class PulseInnerWorld(
             except Exception as _hint_err:
                 self._log(LogLevel.WARNING, f"大模型前置知识检索异常: {_hint_err}")
 
-            # 区分：有correlation_id是对话触发（需要回复），没有是后台自主学习（只消化不输出）
-            if correlation_id:
+            # 区分：有ctx.correlation_id是对话触发（需要回复），没有是后台自主学习（只消化不输出）
+            if ctx.correlation_id:
                 self._log(LogLevel.INFO,
-                         f"复杂问题推给大模型: 复杂度={_question_complexity:.2f}"
+                         f"复杂问题推给大模型: 复杂度={ctx._question_complexity:.2f}"
                          f"{'，含本地知识参考' if _local_knowledge_hint else ''}")
-                self._direct_to_lung_questions.add(question.strip())
-                _memory_context = self._build_memory_context(question, user_name, guidance)
+                self._direct_to_lung_questions.add(ctx.question.strip())
+                _memory_context = self._build_memory_context(ctx.question, ctx.user_name, ctx.guidance)
                 if _local_knowledge_hint:
-                    _memory_context = (_memory_context or "") + "\n\n" + _local_knowledge_hint
+                    _memory_context = (ctx._memory_context or "") + "\n\n" + _local_knowledge_hint
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": None,
-                    "correlation_id": correlation_id,
-                    "confidence": 0.0, "user_name": user_name,
-                    "strategy_applied": _strategy_context,
+                    "question": ctx.question, "answer": None,
+                    "correlation_id": ctx.correlation_id,
+                    "confidence": 0.0, "user_name": ctx.user_name,
+                    "strategy_applied": ctx._strategy_context,
                     "tool_requested": False,
-                    "memory_context": _memory_context,
+                    "memory_context": ctx._memory_context,
                 }, priority=5, layer="L2")
                 return {"status": "direct_to_lung", "reason": "复杂问题优先推理"}
             else:
                 # 后台自主学习：直接调用大模型消化为知识，不经过嘴巴输出
-                self._log(LogLevel.INFO, f"后台学习触发大模型: {question[:40]}")
+                self._log(LogLevel.INFO, f"后台学习触发大模型: {ctx.question[:40]}")
                 self._emit(Event.LUNGS_SELECT_MODEL, {  # ★P3-5修复：修正笔误，原 "lung.select_model" 与常量 LungEvent.SELECT_MODEL 不匹配
                     "task_type": "chat",
-                    "prompt": question,
-                    "user_name": user_name,
-                    "memory_context": self._build_memory_context(question, user_name, guidance),
+                    "prompt": ctx.question,
+                    "user_name": ctx.user_name,
+                    "memory_context": self._build_memory_context(ctx.question, ctx.user_name, ctx.guidance),
                     # ★修复：显式标记后台学习，不依赖肺部「is_dialogue 默认 False」的隐式行为。
                     # 明确 is_dialogue=False + is_background_learning=True，语义自明、防回归。
                     "is_dialogue": False,
@@ -1526,81 +1751,81 @@ class PulseInnerWorld(
                 self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
         # 悲伤/恐惧时：优先内在沉思而非外部搜索
         if _current_emotion in ("悲伤", "恐惧") and _emotion_intensity > 0.3:
-            if "deep_search" in fallback_tools:
-                fallback_tools.remove("deep_search")
-            if "inner_world" not in fallback_tools:
-                fallback_tools.insert(0, "inner_world")
+            if "deep_search" in ctx.fallback_tools:
+                ctx.fallback_tools.remove("deep_search")
+            if "inner_world" not in ctx.fallback_tools:
+                ctx.fallback_tools.insert(0, "inner_world")
             self._log(LogLevel.DEBUG,
                      f"情绪驱动({_current_emotion}): 优先内在沉思，跳过外部搜索")
         # 喜悦/期待时：更愿意尝试外部搜索和探索
         elif _current_emotion in ("喜悦", "期待") and _emotion_intensity > 0.2:
-            if "deep_search" not in fallback_tools:
-                fallback_tools.append("deep_search")
+            if "deep_search" not in ctx.fallback_tools:
+                ctx.fallback_tools.append("deep_search")
             # 提升复杂度感知，更容易触发深度思考
-            _question_complexity = min(1.0, _question_complexity + 0.1)
+            _question_complexity = min(1.0, ctx._question_complexity + 0.1)
         # 焦虑时：使用缓存优先，减少不确定性
         elif _current_emotion == "焦虑" and _emotion_intensity > 0.3:
             # 延长缓存有效期（在缓存检查处已处理）
-            fallback_tools = ["inner_world"]  # 只用内在世界，不做外部搜索
+            ctx.fallback_tools = ["inner_world"]  # 只用内在世界，不做外部搜索
         # ===== 工具认知层：根据大脑皮层的建议决定是否跳过深度搜索 =====
-        if not tool_hint.get("should_search", True) and "deep_search" in fallback_tools:
-            fallback_tools.remove("deep_search")
-            self._log(LogLevel.INFO, f"工具认知: 根据搜索经验，跳过深度搜索 (问题='{question[:30]}')")
-        if _meta_state.get("cognitive_load") == "high":
-            if "deep_search" in fallback_tools:
-                fallback_tools.remove("deep_search")
+        if not ctx.tool_hint.get("should_search", True) and "deep_search" in ctx.fallback_tools:
+            ctx.fallback_tools.remove("deep_search")
+            self._log(LogLevel.INFO, f"工具认知: 根据搜索经验，跳过深度搜索 (问题='{ctx.question[:30]}')")
+        if ctx._meta_state.get("cognitive_load") == "high":
+            if "deep_search" in ctx.fallback_tools:
+                ctx.fallback_tools.remove("deep_search")
                 self._log(LogLevel.DEBUG, "状态感知: 认知负荷偏高，跳过深度搜索")
         # ★压力闭环：高压下降并行/外部搜索，优先内在分步求解（复用已算出的压力调制）
-        if _stress_modulation.get("avoid_parallel"):
-            if "deep_search" in fallback_tools:
-                fallback_tools.remove("deep_search")
-            if "inner_world" not in fallback_tools:
-                fallback_tools.insert(0, "inner_world")
-        if _meta_state.get("wisdom_quality") == "high":
-            if "deep_search" not in fallback_tools:
-                fallback_tools.append("deep_search")
+        if ctx._stress_modulation.get("avoid_parallel"):
+            if "deep_search" in ctx.fallback_tools:
+                ctx.fallback_tools.remove("deep_search")
+            if "inner_world" not in ctx.fallback_tools:
+                ctx.fallback_tools.insert(0, "inner_world")
+        if ctx._meta_state.get("wisdom_quality") == "high":
+            if "deep_search" not in ctx.fallback_tools:
+                ctx.fallback_tools.append("deep_search")
         # 情感充盈时，更愿意冒险尝试创造性方案
-        if _meta_state.get("emotional_state") == "positive":
-            complexity_threshold = _meta_state.get("complexity_bonus", 0)
-            _question_complexity += complexity_threshold  # 提升复杂度感知，更容易触发深度思考
+        if ctx._meta_state.get("emotional_state") == "positive":
+            complexity_threshold = ctx._meta_state.get("complexity_bonus", 0)
+            ctx._question_complexity += complexity_threshold  # 提升复杂度感知，更容易触发深度思考
         # 分析问题特征，决定工具选择策略
-        question_features = self._analyze_question_features(question)
+        ctx.question_features = self._analyze_question_features(ctx.question)
         # 策略1: 计算验证类问题 → 优先用代码沙箱
-        if question_features.get("is_computational") and not tool_requested:
+        if ctx.question_features.get("is_computational") and not ctx.tool_requested:
             # ★FIX(推理准确性): 先尝试本地安全算术求值，命中则直接返回结果，不再发射空壳占位代码
-            _calc_result = self._safe_eval_arithmetic(question)
+            _calc_result = self._safe_eval_arithmetic(ctx.question)
             if _calc_result is not None:
                 self._inference_count += 1
                 _calc_answer = f"计算结果：{_calc_result}"
-                self._cache_inference(question, _calc_answer, user_name)
+                self._cache_inference(ctx.question, _calc_answer, ctx.user_name)
                 _final = self._enhance_answer(
-                    answer=_calc_answer, question=question, method="arithmetic",
-                    complexity=_question_complexity, empathetic_note=empathetic_note,
-                    memory_context=_memory_context,
+                    answer=_calc_answer, question=ctx.question, method="arithmetic",
+                    complexity=ctx._question_complexity, empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context,
                 )
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": _final,
-                    "method": "arithmetic", "confidence": self._evidence_conf(0.95, "arithmetic", [1]), "user_name": user_name,
-                    "correlation_id": correlation_id,
+                    "question": ctx.question, "answer": _final,
+                    "method": "arithmetic", "confidence": self._evidence_conf(0.95, "arithmetic", [1]), "user_name": ctx.user_name,
+                    "correlation_id": ctx.correlation_id,
                     "confidence_hint": "high",
-                    "strategy_applied": _strategy_context,
+                    "strategy_applied": ctx._strategy_context,
                 }, priority=7, layer="L2")
                 return {"status": "arithmetic", "answer": _calc_answer}
             self._log(LogLevel.INFO,
-                     f"元认知决策: 计算类问题，尝试代码验证: {question[:40]}")
+                     f"元认知决策: 计算类问题，尝试代码验证: {ctx.question[:40]}")
             self._emit(Event.MOTOR_EXECUTE, {
-                "code": f"# 验证计算: {question[:80]}\nprint('计算结果: ...')",
+                "code": f"# 验证计算: {ctx.question[:80]}\nprint('计算结果: ...')",
                 "language": "python",
-                "user_name": user_name,
+                "user_name": ctx.user_name,
                 "task_id": f"meta_calc_{int(time.time())}",
             }, priority=6, layer="L2")
-            tool_requested = True
+            ctx.tool_requested = True
         # 策略2: 比较分析类问题 → 拆解子问题后逐个搜索
-        if question_features.get("is_comparative") and not tool_requested:
-            sub_parts = self._decompose_complex_question(question)
+        if ctx.question_features.get("is_comparative") and not ctx.tool_requested:
+            sub_parts = self._decompose_complex_question(ctx.question)
             if sub_parts and len(sub_parts) >= 2:
                 self._log(LogLevel.INFO,
-                         f"元认知决策: 比较类问题，拆解为{len(sub_parts)}个子问题: {question[:40]}")
+                         f"元认知决策: 比较类问题，拆解为{len(sub_parts)}个子问题: {ctx.question[:40]}")
                 for sq in sub_parts[:2]:
                     self._emit(Event.CONTROLLER_OPEN_URL, {
                         "url": f"https://lite.duckduckgo.com/lite/?q={sq[:80]}",
@@ -1609,230 +1834,113 @@ class PulseInnerWorld(
                         "deep_search": True,
                         "search_intent": "curiosity",
                     }, priority=4, layer="L3")
-                tool_requested = True
-        # 策略3: 深度搜索（原有逻辑）
-        if "deep_search" in fallback_tools and not tool_requested:
-            # ===== 全局状态感知：自主判断是否适合执行搜索 =====
-            can_search = True
-            skip_reason = ""
-            try:
-                if self.info_field and hasattr(self.info_field, 'get_global_state'):
-                    global_state = self.info_field.get_global_state()
-                    if global_state.get("is_high_load"):
-                        can_search = False
-                        skip_reason = "系统负载偏高，暂缓深度搜索"
-                    elif global_state.get("active_external_ops", 0) >= global_state.get("max_concurrent_ops", 2):
-                        can_search = False
-                        skip_reason = f"已有{global_state.get('active_external_ops')}个搜索任务在执行，暂缓新搜索"
-            except Exception as e:
-                self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
-            if can_search:
-                # ===== 新增：语义范畴判断——搜索主题是否适合外部搜索引擎 =====
-                _search_topic_for_check = search_query or question[:80]
-                if not self._is_suitable_for_search(_search_topic_for_check):
-                    self._log(LogLevel.INFO,
-                             f"语义范畴判断: 搜索主题'{_search_topic_for_check[:40]}'不适合外部搜索，"
-                             f"优先走内在沉思")
-                    if self.node_pool:
-                        contemplative_answer = self._contemplative_reason(question)
-                        if contemplative_answer:
-                            self._inference_count += 1
-                            self._cache_inference(question, contemplative_answer, user_name)
-                            final_answer = self._enhance_answer(
-                                answer=contemplative_answer,
-                                question=question,
-                                method="contemplation_semantic",
-                                complexity=_question_complexity,
-                                empathetic_note=empathetic_note,
-                                memory_context=_memory_context
-                            )
-                            self._emit(InferenceEvent.RESULT, {
-                                "question": question, "answer": final_answer,
-                                "method": "contemplation_semantic", "confidence": 0.5, "user_name": user_name,
-                                "correlation_id": payload.get("correlation_id", ""),
-                                "strategy_applied": _strategy_context,
-                                "confidence_hint": "low",
-                            }, priority=6, layer="L2")
-                            return {"status": "contemplation_match", "answer": contemplative_answer}
-                    # ★v25.0修复：不适合搜索且沉思失败，直接走大模型兜底或诚实回答，绝不发起外部搜索
-                    self._log(LogLevel.INFO, "语义范畴: 不适合搜索且沉思未命中，走大模型兜底或诚实回答")
-                    if _has_remote_api and correlation_id:
-                        _memory_context = self._build_memory_context(question, user_name, guidance)
-                        self._emit(InferenceEvent.RESULT, {
-                            "question": question, "answer": None,
-                            "method": "meta_not_search",
-                            "confidence": 0.0, "user_name": user_name,
-                            "correlation_id": correlation_id,
-                            "strategy_applied": _strategy_context,
-                            "tool_requested": False,
-                            "memory_context": _memory_context,
-                        }, priority=5, layer="L2")
-                        return {"status": "delegated_to_lung_meta", "reason": "不适合搜索且沉思失败"}
-                    else:
-                        fallback_answer = (
-                            "关于这个问题，我目前的知识库中还没有足够的信息来给出确切的回答，"
-                            "但我会继续学习和思考。"
-                        )
-                        self._inference_count += 1
-                        self._cache_inference(question, fallback_answer, user_name)
-                        final_answer = self._enhance_answer(
-                            answer=fallback_answer,
-                            question=question,
-                            method="meta_honest",
-                            complexity=_question_complexity,
-                            empathetic_note=empathetic_note,
-                            memory_context=_memory_context,
-                        )
-                        self._emit(InferenceEvent.RESULT, {
-                            "question": question, "answer": final_answer,
-                            "method": "meta_honest", "confidence": 0.3, "user_name": user_name,
-                            "correlation_id": correlation_id,
-                            "strategy_applied": _strategy_context,
-                            "confidence_hint": "low",
-                        }, priority=5, layer="L2")
-                        return {"status": "meta_honest", "answer": fallback_answer}
-                # ===== 新增: 观点陈述检测——判断用户输入是观点还是问题 =====
-                is_opinion_statement = self._is_opinion_statement(question)
-                if is_opinion_statement and self.node_pool:
-                    # 用户可能在分享观点，尝试用内在沉思生成回应
-                    self._log(LogLevel.INFO,
-                             f"元认知决策: 检测到观点陈述，优先内在沉思: '{question[:40]}...'")
-                    contemplative_answer = self._contemplative_reason(question)
-                    if contemplative_answer:
-                        self._inference_count += 1
-                        self._cache_inference(question, contemplative_answer, user_name)
-                        final_answer = self._enhance_answer(
-                            answer=contemplative_answer,
-                            question=question,
-                            method="contemplation",
-                            complexity=_question_complexity,
-                            empathetic_note=empathetic_note,
-                            memory_context=_memory_context
-                        )
-                        self._emit(InferenceEvent.RESULT, {
-                            "question": question, "answer": final_answer,
-                            "method": "contemplation", "confidence": 0.5, "user_name": user_name,
-                            "correlation_id": payload.get("correlation_id", ""),
-                            "strategy_applied": _strategy_context,
-                            "confidence_hint": "low",
-                        }, priority=6, layer="L2")
-                        return {"status": "contemplation_match", "answer": contemplative_answer}
-                    # 沉思无法回答时，生成带有价值冲突说明的兜底回答
-                    fallback_answer = (
-                        "关于这个问题，我目前的知识库中还没有足够的信息来给出确切的回答。"
-                        "但我能感受到你在思考一个很重要的问题——如何在诚实和善意之间找到平衡。"
-                        "这种思考本身就很有价值。"
-                    )
-                    self._inference_count += 1
-                    self._cache_inference(question, fallback_answer, user_name)
-                    final_answer = self._enhance_answer(
-                        answer=fallback_answer,
-                        question=question,
-                        method="contemplation",
-                        complexity=_question_complexity,
-                        empathetic_note=empathetic_note,
-                        memory_context=_memory_context
-                    )
-                    self._emit(InferenceEvent.RESULT, {
-                        "question": question, "answer": final_answer,
-                        "method": "contemplation", "confidence": 0.4, "user_name": user_name,
-                        "correlation_id": payload.get("correlation_id", ""),
-                        "strategy_applied": _strategy_context,
-                        "confidence_hint": "low",
-                    }, priority=6, layer="L2")
-                    # 将兜底回答发射为消化脉冲，让胃创建L1节点
-                    self._emit(DigestEvent.KNOWLEDGE, {
-                        "content": f"[内在沉思·兜底回答] {fallback_answer}",
-                        "source_organ": self.organ_name,
-                        "trigger_reason": "contemplation.fallback",
-                        "importance": "B",
-                        "view_mode": "INNER_VIEW",
-                    }, priority=3, layer="L2")
-                    return {"status": "contemplation_match", "answer": fallback_answer}
-                else:
-                    # ★FIX: 抽象概念/知识陈述在源头拦截，不发射无效搜索
-                    _skip_search = self._should_skip_search(question)
-                    if _skip_search:
-                        self._log(LogLevel.INFO, f"搜索意图拦截: 抽象概念/知识陈述不触发搜索: '{question[:40]}...'")
-                    # 正常搜索逻辑（search_query已在前面初始化为question[:80]）
-                    if not _skip_search and len(question) > 40:
-                        refined = self._refine_search_intent(question)
-                        if refined and len(refined) >= 4:
-                            search_query = refined
-                            self._log(LogLevel.INFO, f"元认知决策(搜索意图提炼): '{question[:40]}...' → '{search_query}'")
-                if not _skip_search:
+                ctx.tool_requested = True
+        return None
+
+
+
+
+
+
+
+
+
+    def _on_inference_request(self, payload: dict) -> dict[str, Any]:
+        _ctx = self._ir_build_context(payload)
+        if _ctx is None:
+            return {"status": "skipped", "reason": "空问题"}
+
+        _explicit = self._ir_try_explicit_search(_ctx)
+        if _explicit is not None:
+            return _explicit
+        _detector_result = self._ir_run_detectors(_ctx)
+        if _detector_result is not None:
+            return _detector_result
+        _qica = self._ir_dispatch_qica_method(_ctx)
+        if _qica is not None:
+            return _qica
+        _pipeline_result = self._ir_run_pipeline(_ctx)
+        if _pipeline_result is not None:
+            return _pipeline_result
+
+        _mod = self._ir_apply_modulations(_ctx)
+        if _mod is not None:
+            return _mod
+        _derivation = self._ir_try_derivation(_ctx)
+        if _derivation is not None:
+            return _derivation
+        _deep_read = self._ir_decompose_and_deep_read(_ctx)
+        if _deep_read is not None:
+            return _deep_read
+        self._ir_record_failed_domain(_ctx)
+
+        _knowledge = self._ir_assemble_knowledge_answer(_ctx)
+        if _knowledge is not None:
+            return _knowledge
+        _plan = self._ir_plan_tools(_ctx)
+        if _plan is not None:
+            return _plan
+        _ds_gate = self._ir_try_deep_search_pre(_ctx)
+        if _ds_gate is not None:
+            _can_search, _skip_reason = _ds_gate
+            _ds_exec = self._ir_try_deep_search_exec(_ctx, _can_search, _skip_reason)
+            if _ds_exec is not None:
+                return _ds_exec
+        _creative = self._ir_try_creative_solution(_ctx, payload)
+        if _creative is not None:
+            return _creative
+        return self._ir_finalize(_ctx)
+
+    def _ir_record_failed_domain(self, ctx: "PulseInnerWorld.InferenceContext") -> None:
+        # ★v17.0新增：认知边界感知——记录推理失败的领域
+        if not ctx._derivation_answer:
+            _failed_keywords = []
+            for _match in re.finditer(r'[\u4e00-\u9fff]{2,4}', ctx.question):
+                _word = _match.group()
+                if _word not in _failed_keywords and _word not in [
+                    "什么是", "是什么", "为什么", "如何", "怎么",
+                    "这个", "那个", "一个", "一种", "可以", "能够",
+                ]:
+                    _failed_keywords.append(_word)
+
+            if _failed_keywords:
+                if not hasattr(self, '_failed_domain_records'):
+                    self._failed_domain_records = {}
+                for _kw in _failed_keywords[:5]:
+                    self._failed_domain_records[_kw] = self._failed_domain_records.get(_kw, 0) + 1
+
+                if not hasattr(self, '_failed_domain_log_count'):
+                    self._failed_domain_log_count = 0
+                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
+                self._failed_domain_log_count += 1
+                if self._failed_domain_log_count % 3 == 0:
+                    _top_failed = sorted(self._failed_domain_records.items(), key=lambda x: x[1], reverse=True)[:3]
                     self._log(LogLevel.INFO,
-                             f"元认知决策: 内部推理未命中，触发深度搜索: {search_query[:40]}")
-                    self._emit(Event.CONTROLLER_OPEN_URL, {
-                        "url": f"https://lite.duckduckgo.com/lite/?q={search_query[:80]}",
-                        "reason": "元认知决策: 内部推理未命中，需要深度搜索",
-                        "search_topic": search_query[:80],
-                        "deep_search": True,
-                        "search_intent": "curiosity",
-                    }, priority=4, layer="L3")
-                    tool_requested = True
-                # ===== 搜索发起后，如果远程API可用，同时作为兜底方案 =====
-                if _has_remote_api and correlation_id:
-                    # ===== 大模型兜底前记录经验 =====
-                    try:
-                        from nucleus.mnemosyne.ReasoningExperience import (
-                            get_reasoning_experience,
-                        )
-                        _reasoning_exp_fb = get_reasoning_experience()
-                        # 过滤内部追问词
-                        _is_internal_meta = bool(
-                            question and (
-                                re.search(r'的(?:前提|反例|边界|底层构成|演化路径|最小单元)是什么', question) or
-                                re.search(r'(?:前提|假设)是否(?:总是|还)?成立', question) or
-                                re.search(r'有没有.*反例|在什么情况下.*失效|结论还成立吗', question) or
-                                re.search(r'如果.*(?:反过来|放到|推到极致|不一样)', question) or
-                                re.search(r'它不是什么|换个角度|不同.*视角', question)
-                            )
-                        )
-                        if question and not _is_internal_meta:
-                            _reasoning_exp_fb.record(
-                                question,
-                                "unknown",
-                                source="local_fallback",
-                                confidence=0.3
-                            )
-                    except Exception as e:
-                        self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
-                    # ===== 经验记录结束 =====
-                    self._direct_to_lung_questions.add(question.strip())
-                    _memory_context = self._build_memory_context(question, user_name, guidance)
-                    self._emit(InferenceEvent.RESULT, {
-                        "question": question, "answer": None,
-                        "method": "search_with_lung_fallback",
-                        "confidence": 0.0, "user_name": user_name,
-                        "correlation_id": correlation_id,  # ← 使用前面提取的ID
-                        "strategy_applied": _strategy_context,
-                        "tool_requested": True,
-                        "memory_context": _memory_context,
-                    }, priority=4, layer="L2")
-            else:
-                self._log(LogLevel.INFO, f"元认知决策: {skip_reason}: {question[:40]}")
+                             f"认知边界记录: 推理失败已累计{self._failed_domain_log_count}次, "
+                             f"高频失败领域={_top_failed}")
+
+    def _ir_try_creative_solution(self, ctx: "PulseInnerWorld.InferenceContext", payload: dict) -> dict | None:
         # 策略4: 无工具可用——创造性解决方案
-        if not tool_requested:
+        if not ctx.tool_requested:
             self._log(LogLevel.INFO,
-                     f"元认知决策: 无预设工具可用，尝试创造性解决: {question[:40]}")
+                     f"元认知决策: 无预设工具可用，尝试创造性解决: {ctx.question[:40]}")
             # 生成一个基于已有知识的假设作为临时解决方案
-            creative_solution = self._attempt_creative_solution(question)
+            creative_solution = self._attempt_creative_solution(ctx.question)
             if creative_solution:
                 final_answer = self._enhance_answer(
                     answer=creative_solution,
-                    question=question,
+                    question=ctx.question,
                     method="creative_solution",
-                    complexity=_question_complexity,
-                    empathetic_note=empathetic_note,
-                    memory_context=_memory_context
+                    complexity=ctx._question_complexity,
+                    empathetic_note=ctx.empathetic_note,
+                    memory_context=ctx._memory_context
                 )
                 self._emit(InferenceEvent.RESULT, {
-                    "question": question, "answer": final_answer,
+                    "question": ctx.question, "answer": final_answer,
                     "method": "creative_solution", "confidence": 0.3,
-                    "user_name": user_name,
+                    "user_name": ctx.user_name,
                     "correlation_id": payload.get("correlation_id", ""),
-                    "strategy_applied": _strategy_context,
+                    "strategy_applied": ctx._strategy_context,
                     "tool_requested": True,
                     "creative_solution": True,
                 }, priority=5, layer="L2")
@@ -1845,56 +1953,59 @@ class PulseInnerWorld(
             self._emit(GrowthEvent.NEED_DETECTED, {
                 "milestone": "待解决问题",
                 "gaps": [{"metric": "unsolved", "current": 0, "target": 1}],
-                "suggestion": f"未解决的问题: {question[:80]}",
-                "current_level": {"unsolved_question": question[:80]},
-                "growth_topic": f"待解决问题: {question[:60]}",
+                "suggestion": f"未解决的问题: {ctx.question[:80]}",
+                "current_level": {"unsolved_question": ctx.question[:80]},
+                "growth_topic": f"待解决问题: {ctx.question[:60]}",
             }, priority=3, layer="L3")
+        return None
+
+    def _ir_finalize(self, ctx: "PulseInnerWorld.InferenceContext") -> dict:
         # 发射最终结果（无答案时附上记忆上下文，供大脑皮层调用肺模型时使用）
-        _memory_context = self._build_memory_context(question, user_name, guidance)
+        _memory_context = self._build_memory_context(ctx.question, ctx.user_name, ctx.guidance)
         self._emit(InferenceEvent.RESULT, {
-            "question": question, "answer": None,
-            "method": "none", "confidence": 0.0, "user_name": user_name,
-            "correlation_id": payload.get("correlation_id", ""),
-            "strategy_applied": _strategy_context,
-            "tool_requested": tool_requested,
+            "question": ctx.question, "answer": None,
+            "method": "none", "confidence": 0.0, "user_name": ctx.user_name,
+            "correlation_id": ctx.payload.get("correlation_id", ""),
+            "strategy_applied": ctx._strategy_context,
+            "tool_requested": ctx.tool_requested,
             "memory_context": _memory_context,
         }, priority=5, layer="L2")
         # ===== 新增: 实时元认知监控——感知本次推理的总体质量 =====
-        reasoning_duration = time.time() - _reasoning_start_time
+        reasoning_duration = time.time() - ctx._reasoning_start_time
         if reasoning_duration > 2.0:
             self._log(LogLevel.DEBUG,
                      f"实时元认知: 本次推理耗时{reasoning_duration:.1f}秒，"
-                     f"问题复杂度={_question_complexity:.2f}，"
-                     f"最终状态={tool_requested and '已触发工具' or '未找到答案'}")
+                     f"问题复杂度={ctx._question_complexity:.2f}，"
+                     f"最终状态={ctx.tool_requested and '已触发工具' or '未找到答案'}")
         # ===== 新增: 元认知决策经验记录 =====
-        if tool_requested:
+        if ctx.tool_requested:
             # 有工具被触发时，记录触发原因和策略
-            _decision_type = "search" if not question_features.get("is_computational") else "code"
-            self._trace_inference(question, f"[工具调度: {_decision_type}]",
-                                 f"tool_{_decision_type}", 0.0, user_name,
+            _decision_type = "search" if not ctx.question_features.get("is_computational") else "code"
+            self._trace_inference(ctx.question, f"[工具调度: {_decision_type}]",
+                                 f"tool_{_decision_type}", 0.0, ctx.user_name,
                                  duration=reasoning_duration,
-                                 complexity=_question_complexity,
+                                 complexity=ctx._question_complexity,
                                  tuning_hint=f"元认知决策触发{_decision_type}，耗时{reasoning_duration:.1f}s")
 
             # 补充工具认知层经验：记录触发工具时的决策上下文
             if hasattr(self, '_search_experience') and _decision_type == "search":
                 self._log(LogLevel.DEBUG,
-                         f"元认知决策记录: 触发深度搜索 (主题='{search_query[:40]}')")
+                         f"元认知决策记录: 触发深度搜索 (主题='{ctx.search_query[:40]}')")
         else:
             # 无工具可用，记录为推理盲区
-            self._trace_inference(question, "[无可用工具]",
-                                 "none", 0.0, user_name,
+            self._trace_inference(ctx.question, "[无可用工具]",
+                                 "none", 0.0, ctx.user_name,
                                  duration=reasoning_duration,
-                                 complexity=_question_complexity,
+                                 complexity=ctx._question_complexity,
                                  tuning_hint="所有工具均不可用，建议补充知识")
         # 如果推理耗时过长且未找到答案，记录为需要关注的事件
-        if reasoning_duration > 5.0 and not tool_requested:
+        if reasoning_duration > 5.0 and not ctx.tool_requested:
             self._log(LogLevel.INFO,
-                     f"实时元认知: 高耗时未命中——问题'{question[:40]}'"
+                     f"实时元认知: 高耗时未命中——问题'{ctx.question[:40]}'"
                      f"推理{reasoning_duration:.1f}秒后仍未找到答案，可能需要补充知识")
 
         # ===== v21.0新增：坚韧品格·迭代层——根据失败归因自动调整策略 =====
-        if not tool_requested and reasoning_duration > 1.0:
+        if not ctx.tool_requested and reasoning_duration > 1.0:
             # 查询InsightBoard中最近的失败归因结果
             try:
                 if hasattr(self, '_insight_board') and self._insight_board:
@@ -1919,7 +2030,7 @@ class PulseInnerWorld(
                                     get_reasoning_experience,
                                 )
                                 _exp = get_reasoning_experience()
-                                _exp.record(question, "unknown", source="strategy_adjustment", confidence=0.4)
+                                _exp.record(ctx.question, "unknown", source="strategy_adjustment", confidence=0.4)
                             except Exception as e:
                                 self._log(LogLevel.DEBUG, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
                         elif _attr_type == "information":
@@ -1933,7 +2044,7 @@ class PulseInnerWorld(
                                     "gaps": [{"metric": "knowledge_gap", "current": 0, "target": 1}],
                                     "suggestion": f"归因分析发现信息不足: {_attr_content[:80]}",
                                     "current_level": {"attribution": _attr_content[:80]},
-                                    "growth_topic": f"补充学习: {question[:60]}",
+                                    "growth_topic": f"补充学习: {ctx.question[:60]}",
                                 }, priority=4, layer="L3")
                         elif _attr_type == "capability":
                             # 能力不足：触发系统性学习计划
@@ -1945,11 +2056,11 @@ class PulseInnerWorld(
         # ===== v21.0新增结束 =====
 
         # ★v25.0新增：推理失败记录到体验池
-        if not tool_requested and reasoning_duration > 0.5:
+        if not ctx.tool_requested and reasoning_duration > 0.5:
             try:
                 if hasattr(self, 'experience_pool') and self.experience_pool:
                     self.experience_pool.record_experience(
-                        motivation=f"尝试回答「{question[:50]}」",
+                        motivation=f"尝试回答「{ctx.question[:50]}」",
                         motivation_intensity=0.6,
                         process_pressure=0.6,
                         pressure_type="frustration",
@@ -1957,19 +2068,20 @@ class PulseInnerWorld(
                         reward_intensity=0.1,
                         emotion_tags=["挫败", "不确定"],
                         emotion_intensity=0.5,
-                        content=f"推理未命中，问题复杂度={_question_complexity:.2f}，耗时={reasoning_duration:.1f}秒"
+                        content=f"推理未命中，问题复杂度={ctx._question_complexity:.2f}，耗时={reasoning_duration:.1f}秒"
                     )
-                    self._log(LogLevel.DEBUG, f"推理失败体验记录: '{question[:40]}'")
+                    self._log(LogLevel.DEBUG, f"推理失败体验记录: '{ctx.question[:40]}'")
             except Exception as e:
                 self._log(LogLevel.WARNING, f"外部依赖异常已忽略: {type(e).__name__}: {e}")
 
         return {
-            "status": "tool_requested" if tool_requested else "no_match",
+            "status": "tool_requested" if ctx.tool_requested else "no_match",
             "answer": None,
             "confidence": 0.0,
             "reasoning_duration": round(reasoning_duration, 2),
         }
-    # ========== 推理检测器（从_on_inference_request提取） ==========
+
+    # ========== 推理检测器方法群（原 _on_inference_request C 段；第147批九刀拆分后由 _ir_run_detectors 调度） ==========
 
     def _detect_simple_query_local(self, ctx: "PulseInnerWorld.InferenceContext"):
         """优先级101：简单问题本地回答（阶段二子任务5.1）。
diff --git a/organs/brain/pulse_inner_world_knowledge.py b/organs/brain/pulse_inner_world_knowledge.py
index 247a384..b2f4ec5 100644
--- a/organs/brain/pulse_inner_world_knowledge.py
+++ b/organs/brain/pulse_inner_world_knowledge.py
@@ -2024,8 +2024,8 @@ class PulseInnerWorldKnowledgeMixin:
                          f"路由薄弱降级: '{_derivation_type}'属于薄弱领域，"
                          f"复杂度感知+{_complexity_boost:.2f}")
                 # 通过调整 _question_complexity 让后续的深度思考检测更容易触发
-                # 注意：_question_complexity 是 _on_inference_request 中的局部变量
-                # 这里通过返回特殊的 derivation_type 来标记，让外层处理
+                # 注意：_question_complexity 现为 ctx._question_complexity 字段（第147批九刀拆分后）
+                # 本方法不持有 ctx，仍通过返回特殊的 derivation_type 来标记，让外层处理
                 _derivation_type = f"weak_{_derivation_type}"
 
         if not _derivation_type:
diff --git a/tests/test_deep_think_subproc_m31.py b/tests/test_deep_think_subproc_m31.py
index eb30d03..10ea43c 100644
--- a/tests/test_deep_think_subproc_m31.py
+++ b/tests/test_deep_think_subproc_m31.py
@@ -41,6 +41,12 @@ class _LL:
     ERROR = "ERROR"
 
 
+class _Ctx:
+    """★第147批九刀拆分后：切片代码改用 ctx.question / ctx._reasoning_start_time
+    字段访问，故 exec 命名空间需提供一个带这两个属性的 ctx 占位对象。"""
+    pass
+
+
 class _Switch:
     """临时改写 config 上的开关（用后复原）。"""
 
@@ -221,11 +227,15 @@ class TestCallSiteRealExec(unittest.TestCase):
             _calls.append({"q": q, "deadline": deadline}),
             "主进程深度思考：完整答案" if subproc_result is _DEGRADED else "x")[1]
 
-        # 切片来自方法体，需补齐其外层作用域变量
+        # 切片来自方法体，需补齐其外层作用域变量。
+        # ★第147批：E 段（深度通道）已抽入 _ir_run_pipeline，局部变量
+        #   question/_reasoning_start_time 改为 ctx.question/ctx._reasoning_start_time。
+        _ctx = _Ctx()
+        _ctx.question = "测试问题：数字生命的意义是什么？"
+        _ctx._reasoning_start_time = time.time()
         _ns = {"self": _iw, "time": time, "LogLevel": _LL,
                "hasattr": hasattr, "Exception": Exception,
-               "question": "测试问题：数字生命的意义是什么？",
-               "_reasoning_start_time": time.time()}
+               "ctx": _ctx}
         exec(compile(_seg, "<m31-slice>", "exec"), _ns)
         return _ns.get("_deep_result"), _calls
 
diff --git a/tests/test_ir_slots_t147.py b/tests/test_ir_slots_t147.py
new file mode 100644
index 0000000..81d4f41
--- /dev/null
+++ b/tests/test_ir_slots_t147.py
@@ -0,0 +1,99 @@
+# -*- coding: utf-8 -*-
+"""第147批 刀2 槽位一致性单测：InferenceContext.__slots__ 必须覆盖所有 ctx 字段写入点。
+
+背景：拆分 _on_inference_request 时，跨切口变量通过 InferenceContext（__slots__ 限定）承载。
+若某处写入 `ctx.xxx = ...` 但 xxx 未列入 __slots__，运行时抛 AttributeError（__slots__
+不允许动态新增属性）。本测试用 AST 自动扫描所有 ctx/_ctx 属性写入点，断言全部落槽，
+防止漏槽。
+
+扫描范围：
+1. 写入点（Assign / AugAssign / AnnAssign 目标是 ctx.attr 或 _ctx.attr）
+2. 读取点（Attribute(value=ctx/_ctx) 的 Load）—— 一并断言，读写都必须在槽内
+"""
+import ast
+import os
+import sys
+
+ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+sys.path.insert(0, ROOT)
+
+import unittest
+
+
+_SRC = os.path.join(ROOT, "organs", "brain", "PulseInnerWorld.py")
+
+
+def _load_tree():
+    with open(_SRC, "r", encoding="utf-8", errors="replace") as f:
+        src = f.read()
+    return ast.parse(src)
+
+
+def _slots(tree):
+    for n in ast.walk(tree):
+        if isinstance(n, ast.ClassDef) and n.name == "InferenceContext":
+            for st in n.body:
+                if isinstance(st, ast.Assign):
+                    for t in st.targets:
+                        if isinstance(t, ast.Name) and t.id == "__slots__":
+                            if isinstance(st.value, ast.Tuple):
+                                return {e.value for e in st.value.elts
+                                        if isinstance(e, ast.Constant) and isinstance(e.value, str)}
+    raise AssertionError("未找到 InferenceContext.__slots__")
+
+
+def _ctx_attr_writes(tree):
+    """返回 (写入字段集, 读取字段集)。"""
+    writes, reads = set(), set()
+    for n in ast.walk(tree):
+        # 写入：目标为 ctx.xxx / _ctx.xxx
+        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
+            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
+            for t in targets:
+                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
+                        and t.value.id in ("ctx", "_ctx"):
+                    writes.add(t.attr)
+        # 读取：Attribute(value=ctx/_ctx)
+        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
+                and n.value.id in ("ctx", "_ctx") and isinstance(n.ctx, ast.Load):
+            reads.add(n.attr)
+    return writes, reads
+
+
+class TestIrSlotsT147(unittest.TestCase):
+    """第147批刀2：ctx 槽位一致性（防漏槽 AttributeError）。"""
+
+    @classmethod
+    def setUpClass(cls):
+        cls._tree = _load_tree()
+        cls._slots = _slots(cls._tree)
+        cls._writes, cls._reads = _ctx_attr_writes(cls._tree)
+
+    def test_01_slots_cover_all_ctx_writes(self):
+        """所有 ctx.xxx = 写入点字段必须都在 __slots__ 中。"""
+        missing = sorted(self._writes - self._slots)
+        self.assertEqual([], missing, f"写入点字段未落槽: {missing}")
+
+    def test_02_slots_cover_all_ctx_reads(self):
+        """所有 ctx.xxx 读取字段必须都在 __slots__ 中。"""
+        missing = sorted(self._reads - self._slots)
+        self.assertEqual([], missing, f"读取字段未落槽: {missing}")
+
+    def test_03_slots_non_empty(self):
+        self.assertGreater(len(self._slots), 15)
+
+    def test_04_build_context_returns_ctx(self):
+        """_ir_build_context 应 return ctx（非 None 路径）。"""
+        for n in ast.walk(self._tree):
+            if isinstance(n, ast.FunctionDef) and n.name == "_ir_build_context":
+                has_return_ctx = any(
+                    isinstance(s, ast.Return) and isinstance(s.value, ast.Name)
+                    for s in ast.walk(n)
+                )
+                self.assertTrue(has_return_ctx, "_ir_build_context 缺少 return _ctx")
+                return
+        self.fail("未找到 _ir_build_context")
+
+
+if __name__ == "__main__":
+    unittest.main()
diff --git a/tests/test_ir_timeout_t147.py b/tests/test_ir_timeout_t147.py
new file mode 100644
index 0000000..4aa3b06
--- /dev/null
+++ b/tests/test_ir_timeout_t147.py
@@ -0,0 +1,92 @@
+# -*- coding: utf-8 -*-
+"""第147批 刀3 超时故障注入测试：_ir_run_detectors 的 45s 超时中断。
+
+验证：当推理起点时间被注入为"很久以前"时，检测器调度循环应在首轮迭代即触发
+超时中断（break），返回 None，且日志输出「推理超时」——而非继续调用检测器。
+"""
+import os
+import sys
+import time
+import unittest
+from unittest import mock
+
+ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+sys.path.insert(0, ROOT)
+
+from organs.brain.PulseInnerWorld import PulseInnerWorld
+
+
+_DETECTOR_NAMES = [
+    "_detect_simple_query_local",
+    "_detect_pure_emotion",
+    "_detect_force_deep_think",
+    "_detect_multi_step_task",
+    "_detect_multi_branch_think",
+    "_detect_branch_expand_request",
+    "_detect_health_check",
+    "_detect_deep_review_report",
+    "_detect_meta_cognitive_report",
+    "_detect_long_term_evolution",
+    "_detect_rule_reason",
+    "_detect_experience_route",
+    "_detect_conflict_exclusive",
+    "_detect_file_analysis",
+    "_detect_code_call_chain",
+    "_detect_simple_logic",
+    "_detect_composite_logic",
+    "_detect_symbolic_reason",
+    "_detect_cognitive_operator",
+]
+
+
+class TestIrDetectorTimeoutT147(unittest.TestCase):
+    """刀3：检测器调度 45s 超时中断（故障注入）。"""
+
+    def _make_iw(self):
+        iw = PulseInnerWorld.__new__(PulseInnerWorld)
+        iw._log = mock.MagicMock()
+        self._detector_calls = []
+        for name in _DETECTOR_NAMES:
+            def _stub(ctx, _name=name):
+                self._detector_calls.append(_name)
+                return None
+            setattr(iw, name, _stub)
+        return iw
+
+    def test_01_timeout_breaks_before_any_detector(self):
+        """超时注入：起点在 100s 前 → 首轮即 break，检测器零调用。"""
+        iw = self._make_iw()
+        ctx = PulseInnerWorld.InferenceContext("测试问题", "用户", "cid", {})
+        ctx._reasoning_start_time = time.time() - 100.0  # 远早于 45s 阈值
+
+        result = iw._ir_run_detectors(ctx)
+
+        self.assertIsNone(result, "超时中断应返回 None")
+        self.assertEqual(self._detector_calls, [], "超时后不得调用任何检测器")
+
+        # 日志含「推理超时」
+        warn_msgs = []
+        for c in iw._log.call_args_list:
+            joined = " ".join(str(a) for a in c.args)
+            if "推理超时" in joined:
+                warn_msgs.append(joined)
+        self.assertTrue(warn_msgs, f"未输出「推理超时」日志: {iw._log.call_args_list}")
+
+    def test_02_no_timeout_does_not_log_timeout(self):
+        """无故障：起点在当下 → 不产生「推理超时」日志（0 新增）。"""
+        iw = self._make_iw()
+        ctx = PulseInnerWorld.InferenceContext("测试问题", "用户", "cid", {})
+        ctx._reasoning_start_time = time.time()  # 当下，未超时
+
+        iw._ir_run_detectors(ctx)
+
+        timeout_msgs = []
+        for c in iw._log.call_args_list:
+            joined = " ".join(str(a) for a in c.args)
+            if "推理超时" in joined:
+                timeout_msgs.append(joined)
+        self.assertEqual(timeout_msgs, [], "无故障时不得出现「推理超时」日志")
+
+
+if __name__ == "__main__":
+    unittest.main()
diff --git a/tools/ci/cw2_t2e_ci_gate_silent_except.py b/tools/ci/cw2_t2e_ci_gate_silent_except.py
index 65590f3..6f4a280 100644
--- a/tools/ci/cw2_t2e_ci_gate_silent_except.py
+++ b/tools/ci/cw2_t2e_ci_gate_silent_except.py
@@ -71,7 +71,8 @@ LOCATION_WHITELIST = {
     # ---- 第143批 T-143a：PII 清洗 —— _identity_rules 兜底 handler 的**捕获体**
     #      内嵌身份语句含真名，改造后 body 指纹字符串变化（handler 本身未增未删，
     #      该文件静默 handler 总数 49→49 不变）。键 = (relpath, 行号)。
-    ("organs/brain/PulseInnerWorld.py", 349),                   # _load_inner_world_config：_identity_rules 默认兜底（body 含身份语句）
+    ("organs/brain/PulseInnerWorld.py", 358),                   # _load_inner_world_config：_identity_rules 默认兜底（★147批：__slots__ 再+2，原 349→352→354）
+    ("organs/brain/PulseInnerWorld.py", 565),                   # _ir_build_context：guidance 兜底（★147批刀2：平移自 _on_inference_request，净增0）
 }
 
 

```
