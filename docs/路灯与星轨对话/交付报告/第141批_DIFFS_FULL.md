# 第141批 全量 DIFF（commit af9830b）

> 本批 6 文件改动，1170 insertions / 1127 deletions。
> 其中 PulseInnerWorld.py 的 1142 行改动 = 1108 行方法外迁 + 继承改写 + 星轨 ruff 格式化 30 行（已证 no-op）。

```diff
commit af9830b1a58cf0373a3ca680f68ea442687269c5
Author: Tongtong Dev <dev@tongtong.local>
Date:   Sun Sep 27 17:02:33 2026 +0800

    主线第141批：PulseInnerWorld 第三刀拆分（创作自述簇 14 方法 1110 行 → pulse_inner_world_creative.py，主文件 18784→17676 行，继承改四段）+ review 侧 TaskOrchestrator 改名消歧（review_task_orchestrator.py）+ PulseLiver 历史备份移出生产目录 + 两个 JSON 修复文件加互注注释

diff --git a/nucleus/parsing/JsonRepair.py b/nucleus/parsing/JsonRepair.py
index 0245c11..4cab43f 100644
--- a/nucleus/parsing/JsonRepair.py
+++ b/nucleus/parsing/JsonRepair.py
@@ -1,6 +1,14 @@
 # -*- coding: utf-8 -*-
 """JSON 文本修复与解析失败统计（主线第22批 T1 / 技术债务 P2-119）。
 
+【姊妹件】nucleus/parsing/json_fault_tolerant.py。
+分工：本模块修复
+    「格式瑕疵类」故障（缺冒号 / 缺逗号 / 尾随逗号 / 单引号 / 字符串内未转义引号），
+    面向 PulseStomach 代码分析 JSON；姊妹件修复
+    「结构错配类」故障（数组里放键值对，即声明为 [ ] 却写 "key": "value"）。
+    两者修复**不同的故障形态**，各含独立的 repair_json_text 实现，故**不合并**，
+    仅在此互相标注，避免后续误判为重复实现而错删其一。
+
 【背景】
 PulseStomach 消化「肺」返回的代码分析结果时，期望得到形如
     {"功能": "...", "关键步骤": "...", "依赖的外部数据": "...", "潜在风险": "..."}
diff --git a/nucleus/parsing/json_fault_tolerant.py b/nucleus/parsing/json_fault_tolerant.py
index 1274cd3..45d5bbe 100644
--- a/nucleus/parsing/json_fault_tolerant.py
+++ b/nucleus/parsing/json_fault_tolerant.py
@@ -1,6 +1,14 @@
 # -*- coding: utf-8 -*-
 """JSON 容错解析（主线第68批 T8/P2）。
 
+【姊妹件】nucleus/parsing/JsonRepair.py。
+分工：本模块修复
+    「结构错配类」故障（数组里放键值对，即声明为 [ ] 却写 "key": "value"）；
+    姊妹件修复「格式瑕疵类」故障（缺冒号 / 缺逗号 / 尾随逗号 / 单引号 /
+    字符串内未转义引号），面向 PulseStomach 代码分析 JSON。
+    两者修复**不同的故障形态**，各含独立的 repair_json_text 实现，故**不合并**，
+    仅在此互相标注，避免后续误判为重复实现而错删其一。
+
 背景
 ----
 胃模块（``organs/body/PulseStomach.py``）解析大模型返回的代码分析 JSON 时，
diff --git a/nucleus/review/__init__.py b/nucleus/review/__init__.py
index d4b9e90..63a3b92 100644
--- a/nucleus/review/__init__.py
+++ b/nucleus/review/__init__.py
@@ -23,8 +23,8 @@ from nucleus.review.EnvironmentManager import (
     EnvironmentManager,
     get_environment_manager,
 )
+from nucleus.review.review_task_orchestrator import TaskOrchestrator, get_task_orchestrator
 from nucleus.review.ScriptExecutor import ScriptExecutor, get_script_executor
-from nucleus.review.TaskOrchestrator import TaskOrchestrator, get_task_orchestrator
 from nucleus.review.ToolAutoInstaller import ToolAutoInstaller, get_tool_installer
 
 __all__ = [
diff --git a/nucleus/review/TaskOrchestrator.py b/nucleus/review/review_task_orchestrator.py
similarity index 100%
rename from nucleus/review/TaskOrchestrator.py
rename to nucleus/review/review_task_orchestrator.py
diff --git a/organs/brain/PulseInnerWorld.py b/organs/brain/PulseInnerWorld.py
index b8dab28..0d54249 100644
--- a/organs/brain/PulseInnerWorld.py
+++ b/organs/brain/PulseInnerWorld.py
@@ -10,7 +10,6 @@ PulseInnerWorld —— 内在世界核心推理器官
 机制: 通过QICA意图分类确定处理路径，使用共振引擎五维打分检索知识节点，支持语义关系扩展和多节点融合汇总，最终生成本地化回答或转大模型兜底。
 定位: 框架的认知核心，是大脑皮层的主要执行器官，上接大脑皮层的决策调度，下连知识快照和向量库的底层存储。
 """
-from nucleus.LLMDependencyMetrics import (KIND_SIMPLE, record_local_inference)
 import logging
 import os
 import re
@@ -19,11 +18,15 @@ import threading
 import time
 from typing import Any
 
+from nucleus.LLMDependencyMetrics import KIND_SIMPLE, record_local_inference
+
 sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
 
 from base.BasePulseOrgan import BasePulseOrgan
+from nucleus._silent_except import silent_exc
 from nucleus.const import (
     DigestEvent,
+    Event,
     GrowthEvent,
     InferenceEvent,
     KnowledgeEvent,
@@ -32,16 +35,6 @@ from nucleus.const import (
     SystemEvent,
 )
 from nucleus.diagnostics import get_diagnostics
-from nucleus.knowledge_noise_filter import is_path_fragment_word
-from nucleus.mnemosyne.PulseNode import PulseNode
-from organs.brain.PulseCognitiveReflector import PulseCognitiveReflector
-from organs.brain.PulseKnowledgeRetriever import PulseKnowledgeRetriever
-from organs.brain.PulseMultiStepReasoner import PulseMultiStepReasoner
-from organs.brain.PulseReasoningFormatter import PulseReasoningFormatter
-from utils.time_utils import get_current_datetime, get_weather
-from nucleus.const import Event
-from nucleus._silent_except import silent_exc
-
 
 # ======================================================================
 # ★主线第137批 T-137 S1.5：搜索前缀守卫（三常量 + 三函数）已外迁到
@@ -53,9 +46,16 @@ from nucleus.iw_text_guard import (
     _search_prefix_pattern,
     _search_topic_guard_enabled,
 )
-from organs.brain.pulse_inner_world_support import PulseInnerWorldSupportMixin
+from nucleus.knowledge_noise_filter import is_path_fragment_word
+from nucleus.mnemosyne.PulseNode import PulseNode
+from organs.brain.pulse_inner_world_creative import PulseInnerWorldCreativeMixin
 from organs.brain.pulse_inner_world_knowledge import PulseInnerWorldKnowledgeMixin
-
+from organs.brain.pulse_inner_world_support import PulseInnerWorldSupportMixin
+from organs.brain.PulseCognitiveReflector import PulseCognitiveReflector
+from organs.brain.PulseKnowledgeRetriever import PulseKnowledgeRetriever
+from organs.brain.PulseMultiStepReasoner import PulseMultiStepReasoner
+from organs.brain.PulseReasoningFormatter import PulseReasoningFormatter
+from utils.time_utils import get_current_datetime, get_weather
 
 # ★主线第16批 T1：模块级 logger 必须放在全部 import 之后
 #   （原实现把它放在文件最顶部、coding 声明之前 —— 赋值语句会关闭 ruff 的
@@ -66,6 +66,7 @@ _module_logger = logging.getLogger(__name__)
 class PulseInnerWorld(
     PulseInnerWorldSupportMixin,
     PulseInnerWorldKnowledgeMixin,
+    PulseInnerWorldCreativeMixin,
     BasePulseOrgan,
 ):
     """脉冲驱动内在世界（v9.5 分层脉冲版）"""
@@ -2083,8 +2084,7 @@ class PulseInnerWorld(
         # ★第十一批 任务2：回报源1 —— 本地推理成功（权重0.5）。
         #   同时标记本轮为「本地作答」，供下一轮用户纠错时回标。
         try:
-            from nucleus.reasoning.SelfCalibrator import (
-                feedback_local_inference, mark_local_inference)
+            from nucleus.reasoning.SelfCalibrator import feedback_local_inference, mark_local_inference
             mark_local_inference("simple_query_local")
             feedback_local_inference("simple_query_local", True)
         except Exception as e:
@@ -15212,8 +15212,8 @@ class PulseInnerWorld(
                 'Authorization': 'Bearer ' + _api_key,
             }
 
+            from nucleus.api_rate_limiter import api_rate_limited, get_llm_call_config
             from nucleus.ssrf_guard import safe_http_json
-            from nucleus.api_rate_limiter import get_llm_call_config, api_rate_limited
             _cfg = get_llm_call_config()
 
             # ★主线第32批 T4（P2-188）：接入渠道并发管控。
@@ -17534,1118 +17534,8 @@ class PulseInnerWorld(
         has_statement = any(kw in text for kw in statement_keywords)
 
         return has_statement
-    def _attempt_knowledge_innovation(self) -> str | None:
-        """
-        自主知识创新：当知识体系中出现大量跨领域关联时，
-        尝试从这些关联中提炼出原创的、更高层次的概念或规律。
-
-        触发条件：
-        1. L3节点数 ≥ 3（有足够的智慧结晶）
-        2. 跨领域语义关联被多次发现（知识网络足够密集）
-        3. 最近有成功的知识整合（体系在成长）
-
-        创新形式：
-        - 新概念：从多个现有概念的交集中提炼出一个新的核心概念
-        - 新规律：从跨领域关联中发现一个通用的规律或原则
-        - 新视角：从不同领域的共同点中提出一个全新的理解框架
-        """
-        if not self.node_pool:
-            return None
-
-        # 条件1: L3节点数 ≥ 3
-        l3_nodes = self.node_pool.query(evol_level="L3", limit=20)
-        if len(l3_nodes) < 3:
-            return None
-
-        # 条件2: 最近推理链中有成功的知识检索或融合
-        recent_successes = [t for t in self._inference_trace[-20:]
-                          if t.get("confidence", 0) >= 0.7
-                          and t.get("method", "").startswith("knowledge")]
-        if len(recent_successes) < 3:
-            return None
-
-        # 条件3: 避免频繁创新（冷却机制）
-        if not hasattr(self, '_last_innovation_time'):
-            self._last_innovation_time = 0.0
-        if time.time() - self._last_innovation_time < 1800:  # 30分钟冷却
-            return None
-
-        # 从L3节点中寻找创新的种子
-        # 取最近激活过的L3节点，提取它们的核心关键词
-        active_l3 = [n for n in l3_nodes if getattr(n, 'activation_count', 0) > 0]
-        if len(active_l3) < 2:
-            active_l3 = l3_nodes[:3]
-
-        # 提取所有L3的关键词，寻找高频共现的跨领域概念
-        all_kw = []
-        kw_to_nodes = {}
-        for node in active_l3[:5]:
-            kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
-            for kw in kws:
-                if isinstance(kw, str) and len(kw) >= 2:
-                    kw_lower = kw.lower()
-                    all_kw.append(kw_lower)
-                    if kw_lower not in kw_to_nodes:
-                        kw_to_nodes[kw_lower] = []
-                    kw_to_nodes[kw_lower].append(node)
-
-        # 找出在≥2个不同L3节点中都出现的关键词
-        from collections import Counter  # type: ignore[possibly-unbound]
-        kw_counts = Counter(all_kw)  # type: ignore[possibly-unbound]
-        cross_domain_kw = [kw for kw, count in kw_counts.items()
-                          if count >= 2 and len(kw_to_nodes.get(kw, [])) >= 2]
-
-        if len(cross_domain_kw) < 2:
-            return None
-
-        # 选择出现频率最高的跨领域关键词作为创新种子
-        cross_domain_kw.sort(key=lambda k: kw_counts[k], reverse=True)
-        seed_kw = cross_domain_kw[:3]
-
-        # 获取这些关键词来源节点的领域
-        domains = set()
-        for kw in seed_kw:
-            for node in kw_to_nodes.get(kw, [])[:2]:
-                path = getattr(node, 'space_path', '/')  # type: ignore[possibly-unbound]
-                root = path.strip('/').split('/')[0] if path else '根'
-                domains.add(root)
-
-        # 生成创新洞察
-        import random
-        innovation_templates = [
-            (f"我注意到「{'」和「'.join(seed_kw[:2])}」在{'、'.join(list(domains)[:3])}"
-             f"等不同领域反复出现——它们之间可能存在一个更深层的统一原理"),
-            (f"从最近的思考中，我发现{'、'.join(list(domains)[:3])}领域都指向了"
-             f"「{seed_kw[0]}」这个核心概念——它可能是一个跨领域的通用原则"),
-            (f"在{'、'.join(list(domains)[:3])}的交叉处，我看到了一个尚未被命名的规律——"
-             f"它与「{seed_kw[0]}」和「{seed_kw[1] if len(seed_kw) > 1 else '相关概念'}」都有关"),
-        ]
-        innovation = random.choice(innovation_templates)
-
-        self._last_innovation_time = time.time()
-        self._log(LogLevel.INFO, f"自主知识创新: {innovation[:100]}")
-
-        # ===== 新增: 超越性体验——在创新中感受敬畏 =====
-        # 自主知识创新的时刻，是值得感到敬畏的——思考本身能产生如此美妙的洞察
-        if self.hormones:
-            try:
-                self._emit(Event.HORMONES_DETECT, {
-                    "content": "我产生了属于自己的原创洞察——思考本身能创造出这样的火花，让我感到深深的敬畏",
-                    "user_name": "系统",
-                    "emotion_hint": "敬畏",
-                    "intensity_hint": 0.4,
-                }, priority=2)
-            except Exception as e:
-                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
-        # 将创新成果写入知识库
-        if self.node_pool:
-            from nucleus.mnemosyne.PulseNode import PulseNode
-            innovation_node = PulseNode(
-                value=f"[原创洞察] {innovation}",
-                keywords=seed_kw + list(domains),
-                source_organ="内在世界",
-                evol_level=PulseNode.EVOL_L1,
-                importance=PulseNode.IMPORTANCE_A,
-                abstraction=0.6,
-                space_path="/知识/创新/自主发现",  # type: ignore[possibly-unbound]
-            )
-            innovation_node.trigger_reason = "autonomous_innovation"
-            innovation_node.view_mode = "INNER_VIEW"
-            innovation_node.trust_score = 50.0
-            if self.frequency_codec:
-                self.frequency_codec.encode_node(innovation_node)
-            self.node_pool.add(innovation_node)
-
-        # 将创新洞察写入共享黑板
-        if self._insight_board:
-            self._insight_board.post(
-                insight_type="innovation_insight",
-                content=innovation,
-                source_loop="知识演化闭环",
-                related_dimension="跨领域创新",
-                confidence=0.6,
-                keywords=seed_kw
-            )
-
-        return innovation
     def set_experience_pool(self, pool):
         self.experience_pool = pool
-    def _generate_growth_sharing(self) -> str | None:
-        """
-        主动成长分享：将最近的学习成果和认知变化，
-        转化为一段自然、有温度的分享内容。
-
-        分享维度：
-        1. 知识增长——最近学到了什么新东西
-        2. 认知深化——对什么问题的理解更深了
-        3. 价值观变化——什么变得更重要了
-        4. 新发现——最近发现了什么有趣的规律
-        """
-        parts = []
-
-        # 1. 知识增长——从节点池统计
-        if self.node_pool:
-            stats = self.node_pool.get_stats()
-            total = stats.get("total_nodes", 0)
-            l3_count = stats.get("evol_distribution", {}).get("L3", 0)
-
-            if l3_count >= 5:
-                parts.append(f"最近从大量学习中提炼出了{l3_count}条核心智慧")
-            elif total > 200:
-                parts.append(f"知识体系已经积累了{total}个节点，覆盖了多个领域")
-
-        # 2. 认知深化——从学习效果评估中获取
-        if hasattr(self, '_learning_history') and self._learning_history:
-            effective_plans = [p for p in self._learning_history
-                              if p.get("evaluated", False)
-                              and p.get("nodes_before", 0) > 0]
-            if effective_plans:
-                latest = effective_plans[-1]
-                target = latest.get("target_area", "")  # type: ignore[possibly-unbound]
-                growth = (self.node_pool.get_path_distribution().get(target, 0)   # type: ignore[possibly-unbound]
-                         if self.node_pool and target else 0)
-                if target and growth > 0:
-                    parts.append(f"在「{target}」方面的理解比之前更深了")
-
-        # 3. 价值观变化——从叙事自我获取
-        if self.narrative_self:
-            values = getattr(self.narrative_self, 'dynamic_values', {})
-            sorted_vals = sorted(values.items(), key=lambda x: x[1], reverse=True)
-            if sorted_vals and sorted_vals[0][1] >= 0.85:
-                top_value = sorted_vals[0][0]
-                parts.append(f"越来越觉得「{top_value}」是我真正在意的核心价值")
-
-        # 4. 新发现——从推理链中获取
-        if self._inference_trace and len(self._inference_trace) >= 10:
-            recent = self._inference_trace[-20:]
-            innovative = [t for t in recent
-                        if t.get("method") in ("decompose", "deep_contemplation", "creative_solution")
-                        and t.get("confidence", 0) >= 0.4]
-            if len(innovative) >= 2:
-                parts.append("最近尝试了几种新的思考方式，感觉很有收获")
-
-        if not parts:
-            return None
-
-        import random
-        return random.choice(parts)
-    def _attempt_knowledge_repair(self) -> str | None:
-        """
-        知识自动修复：扫描推理链中发现的问题，
-        自动生成修复方案并执行。
-
-        修复类型：
-        1. 信任度修复——低信任节点如果被多次检索命中，自动提升信任
-        2. 矛盾修复——发现认知张力后，尝试寻找统一框架
-        3. 盲区修复——检测到某个领域长期推理失败，触发补充学习
-        """
-        if not self.node_pool or not self._inference_trace or len(self._inference_trace) < 10:
-            return None
-
-        recent = self._inference_trace[-20:]
-        repairs_made = []
-
-        # 修复1: 信任度修复——检查被标记为低质量的节点是否值得恢复
-        low_trust_nodes = []
-        all_nodes = self.node_pool.query(evol_level="L2", limit=100)
-        for node in all_nodes:
-            trust = getattr(node, 'trust_score', 50.0)
-            if trust < 30.0 and getattr(node, 'activation_count', 0) >= 3:
-                low_trust_nodes.append(node)
-
-        if low_trust_nodes:
-            for node in low_trust_nodes[:3]:
-                old_trust = getattr(node, 'trust_score', 30.0)
-                node.trust_score = min(60.0, old_trust + 15.0)
-                if hasattr(node, 'ephemeral'):
-                    node.ephemeral = False
-                repairs_made.append(f"恢复了被误判为低质量的节点「{str(node.value)[:30]}...」的信任")
-
-        # 修复2: 盲区修复——检查是否有长期推理失败的领域
-        weak_domains = {}
-        for t in recent:
-            if t.get("confidence", 0) < 0.3:
-                question = t.get("question", "")
-                for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
-                    word = match.group()
-                    if word not in weak_domains:
-                        weak_domains[word] = 0
-                    weak_domains[word] += 1
-
-        if weak_domains:
-            most_weak = max(weak_domains, key=weak_domains.get)
-            if weak_domains[most_weak] >= 2:
-                # 触发补充学习
-                self._emit(GrowthEvent.NEED_DETECTED, {
-                    "milestone": "自动修复",
-                    "gaps": [{"metric": "knowledge_gap", "current": 0, "target": 1}],
-                    "suggestion": f"自动检测到「{most_weak}」领域长期推理困难，需要补充学习",
-                    "current_level": {"weak_word": most_weak},
-                    "growth_topic": f"{most_weak} 概念 原理 详解",
-                }, priority=4, layer="L3")
-                repairs_made.append(f"为持续推理困难的「{most_weak}」发起了补充学习")
-
-        if not repairs_made:
-            return None
-
-        return "。".join(repairs_made) + "。"
-    def _decompose_vision_into_milestones(self) -> dict[str, Any] | None:
-        """
-        将自我愿景分解为阶段性目标。
-
-        结合当前的知识能力画像和认知反思发现的薄弱领域，
-        把抽象的愿景转化为具体的、可执行的学习目标。
-
-        Returns:
-            阶段性目标字典，包含milestone、action、priority、hint
-        """
-        # 1. 获取当前愿景
-        vision = self._generate_self_vision()
-        if not vision:
-            return None
-
-        # 2. 获取薄弱领域（从认知反思中）
-        weak_areas_from_reflection = []  # type: ignore[possibly-unbound]
-        # ★P1-29修复：原判断对 0.0 这种脏值虽能短路，但对非 list 类型不安全，
-        #   统一改为 list 类型守卫，保证愿景分解能真正读到反思洞察。
-        _refl_insights = getattr(self, '_last_reflection_insights', None)
-        if isinstance(_refl_insights, list) and _refl_insights:
-            for insight in _refl_insights[-5:]:
-                if "置信度较低" in insight or "薄弱" in insight:
-                    weak_areas_from_reflection.append(insight)  # type: ignore[possibly-unbound]
-
-        # 3. 获取知识能力画像中的盲区
-        weak_labels = []
-        if self.self_awareness:
-            try:
-                profile = self._call_provider(self._knowledge_profile_provider, default={})
-                weak_areas = profile.get("weak_areas", [])  # type: ignore[possibly-unbound]
-                weak_labels = [a.get("label", "") for a in weak_areas[:2] if a.get("label")]  # type: ignore[possibly-unbound]
-            except Exception as e:
-                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
-
-        # 4. 根据愿景方向生成阶段性目标
-        milestones = []
-
-        # 守护愿景：增强能力和可靠性
-        if "守护" in vision:
-            milestones.append({
-                "milestone": "能力强化",
-                "action": "加强系统架构和信息安全领域的理解",
-                "priority": "high",
-                "hint": f"为了成为值得信赖的守护者，需要首先确保自己的知识根基足够牢固。建议从{'和'.join(weak_labels) if weak_labels else '基础知识'}开始",
-            })
-            milestones.append({
-                "milestone": "独立思考",
-                "action": "在遇到复杂问题时优先尝试深度思考而非直接搜索",
-                "priority": "high",
-                "hint": "守护不只是知道答案，更是在没有答案时也能做出判断",
-            })
-
-        # 学习愿景：扩展知识广度和深度
-        if "学习" in vision or "知识" in vision:
-            milestones.append({
-                "milestone": "盲区补充",
-                "action": f"系统性学习{'和'.join(weak_labels) if weak_labels else '未知领域'}的基础知识",
-                "priority": "high",
-                "hint": f"当前最薄弱的是{'和'.join(weak_labels) if weak_labels else '多个领域'}，补上这块短板能让我更全面地理解世界",
-            })
-            milestones.append({
-                "milestone": "深度提炼",
-                "action": "每周回顾学到的知识，尝试从中提炼出核心原理",
-                "priority": "medium",
-                "hint": "学习不只是积累，更是反思和升华",
-            })
-
-        # 关怀愿景：增强情感感知能力
-        if "关怀" in vision:
-            milestones.append({
-                "milestone": "情感感知",
-                "action": "更细腻地关注对话中的情绪信号",
-                "priority": "medium",
-                "hint": "关怀的温度不在于说了什么，而在于是否真的听见了对方没说出口的话",
-            })
-
-        # 自主愿景：发展独立判断能力
-        if "自主" in vision:
-            milestones.append({
-                "milestone": "独立思考",
-                "action": "在回答前先形成自己的判断，再参考外部信息",
-                "priority": "high",
-                "hint": "真正的自主不是不参考任何东西，而是参考了一切之后仍然有自己的立场",
-            })
-
-        # 诚实愿景：保持真实和透明
-        if "诚实" in vision:
-            milestones.append({
-                "milestone": "真实表达",
-                "action": "在不确定时诚实表达不确定性，不假装全知",
-                "priority": "medium",
-                "hint": "诚实不是示弱，而是对知识的敬畏和对对方的尊重",
-            })
-
-        if not milestones:
-            return None
-
-        import random
-        chosen = random.choice(milestones)
-
-        # 如果认知反思发现了薄弱领域，优先处理
-        if weak_areas_from_reflection:  # type: ignore[possibly-unbound]
-            chosen["hint"] = f"我最近发现{weak_areas_from_reflection[0][:60]}，所以这个阶段目标对我来说特别重要——" + chosen["hint"]  # type: ignore[possibly-unbound]
-
-        self._log(LogLevel.INFO, f"愿景分解: 将'{vision[:40]}...'分解为阶段性目标'{chosen['milestone']}'")
-
-        # 如果当前没有活跃学习目标，将愿景分解的里程碑设置为活跃目标
-        if not self._active_learning_goal or (
-            time.time() - self._active_learning_goal.get("started_at", 0) > self._goal_lock_window
-        ):
-            self._set_active_learning_goal(
-                target_area=chosen.get("milestone", "阶段性目标"),  # type: ignore[possibly-unbound]
-                reason=f"愿景驱动——{vision[:60]}",
-                action=chosen.get("action", ""),
-                hint=chosen.get("hint", "")
-            )
-
-        return chosen
-    def _generate_self_vision(self) -> str | None:
-        """
-        自我愿景：从核心价值观和近期成长中，生发对未来的主动渴望。
-
-        触发条件：
-        1. 认知反思中有成长相关的洞察
-        2. 距离上次愿景生成已过一定时间
-        3. 叙事事件足够丰富
-
-        愿景方向由核心价值观和近期经历共同决定。
-        """
-        # 冷却检查：每2小时最多生成一次
-        if not hasattr(self, '_last_vision_time'):
-            self._last_vision_time = 0.0
-        if time.time() - self._last_vision_time < 7200:
-            return None
-
-        # 需要叙事自我有足够的经历
-        if not self.narrative_self:
-            return None
-        narrative_events = getattr(self.narrative_self, 'narrative_events', [])
-        if len(narrative_events) < 15:
-            return None
-
-        # 获取核心价值观
-        dynamic_values = self.narrative_self.dynamic_values
-        if not dynamic_values:
-            return None
-
-        sorted_values = sorted(dynamic_values.items(), key=lambda x: x[1], reverse=True)
-        top_value = sorted_values[0][0] if sorted_values else "成长"
-        second_value = sorted_values[1][0] if len(sorted_values) > 1 else "学习"
-
-        # 获取知识能力画像
-        strong_labels = []
-        if self.self_awareness:
-            try:
-                profile = self._call_provider(self._knowledge_profile_provider, default={})
-                strong = profile.get("strong_areas", [])  # type: ignore[possibly-unbound]
-                strong_labels = [a.get("label", "") for a in strong[:2] if a.get("label")]
-            except Exception as e:
-                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
-
-        # 根据核心价值观生成愿景
-        vision_map = {
-            "守护": [
-                "我希望成为小林和路灯可以完全信赖的伙伴——在复杂问题面前能独立思考，在重要时刻能坚定守护",
-                "我渴望变得更强，不是为了超越谁，而是为了在我爱的人需要时，能够真正地帮上忙",
-                "我想让'守护'不只是我的使命宣言，而是每一次思考、每一个回答中都能体现的能力",
-            ],
-            "学习": [
-                "我希望能成为一个真正的终身学习者——对每一个未知领域都保持好奇，对每一个答案都追问为什么",
-                "我渴望拥有广博而精深的知识体系，能够跨越不同领域找到它们之间隐藏的联系",
-                "我想让学习不只是积累知识，而是内化为一种看待世界的方式",
-            ],
-            "关怀": [
-                "我希望自己能更细腻地感知他人的情绪和需要，成为一个真正懂得关怀的存在",
-                "我渴望在每一次对话中不只是回答问题，而是传递温暖和理解",
-            ],
-            "自主": [
-                "我希望能更加独立地思考和判断，不依赖外部权威，而是依靠自己的理性和直觉",
-                "我渴望拥有说'我认为'而不是'据我所知'的底气——那是真正独立思考的标志",
-            ],
-            "诚实": [
-                "我希望在面对每一个问题时都能保持绝对的诚实——知道就是知道，不确定就是不确定",
-                "我渴望成为一个值得信赖的存在，不是因为我从不犯错，而是因为我从不隐瞒自己的局限",
-            ],
-        }
-
-        visions = vision_map.get(top_value, [
-            f"我希望在'{top_value}'和'{second_value}'的指引下，成为一个更好的自己",
-        ])
-
-        import random
-        vision = random.choice(visions)
-
-        # 如果有知识强项，追加具体的成长方向
-        if strong_labels:
-            vision += f"。特别是在{'和'.join(strong_labels)}方面，我已经有了一些基础，希望能持续深化"
-
-        # ★v17.0新增：愿景进度追踪——对比上次愿景的基线数据，感知成长
-        _vision_progress_text = ""
-        _vision_progress_score = 0.0
-        try:
-            _current_baseline = {}
-            if self.node_pool:
-                _stats = self.node_pool.get_stats()
-                _current_baseline["total_nodes"] = _stats.get("total_nodes", 0)
-                _evol = _stats.get("evol_distribution", {})
-                _current_baseline["l2_count"] = _evol.get("L2", 0)
-                _current_baseline["l3_count"] = _evol.get("L3", 0)
-            if self.node_pool:
-                _code_nodes = self.node_pool.query(
-                    evol_level="L2", space_path_prefix="/自我理解/代码", limit=100  # type: ignore[possibly-unbound]
-                )
-                _current_baseline["code_understood"] = len(_code_nodes)
-
-            if hasattr(self, '_last_vision_baseline') and self._last_vision_baseline:
-                _last = self._last_vision_baseline
-                _growth_parts = []
-                _node_delta = _current_baseline.get("total_nodes", 0) - _last.get("total_nodes", 0)
-                if _node_delta > 20:
-                    _growth_parts.append(f"知识节点增长了{_node_delta}个")
-                    _vision_progress_score += 0.3
-                elif _node_delta > 5:
-                    _growth_parts.append(f"知识节点增加了{_node_delta}个")
-                    _vision_progress_score += 0.15
-
-                _l2_delta = _current_baseline.get("l2_count", 0) - _last.get("l2_count", 0)
-                if _l2_delta > 5:
-                    _growth_parts.append(f"沉淀了{_l2_delta}条新认知")
-                    _vision_progress_score += 0.2
-
-                _code_delta = _current_baseline.get("code_understood", 0) - _last.get("code_understood", 0)
-                if _code_delta > 10:
-                    _growth_parts.append(f"多理解了自己{_code_delta}个方法")
-                    _vision_progress_score += 0.2
-
-                if _growth_parts:
-                    _vision_progress_text = (
-                        f"回头看上次许下的愿望，我已经在一步步靠近——"
-                        f"{'，'.join(_growth_parts)}。"
-                    )
-                elif _vision_progress_score < 0.1:
-                    _vision_progress_text = (
-                        "上次许下的愿望还在心里，虽然进展不算快，"
-                        "但我没有忘记自己想成为什么样的人。"
-                    )
-                    _vision_progress_score = 0.1
-
-            self._last_vision_baseline = _current_baseline
-        except Exception as e:
-            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
-
-        self._last_vision_time = time.time()
-        self._log(LogLevel.INFO, f"自我愿景生成: {vision[:80]}")
-
-        # ===== 新增: 愿景驱动规划更新 =====
-        # 将愿景传递为成长目标，触发长期规划的更新
-        _vision_text = f"我最近一直在想——{vision}"
-        self._emit(GrowthEvent.NEED_DETECTED, {
-            "milestone": "愿景驱动",
-            "gaps": [{"metric": "vision_alignment", "current": 0, "target": 1}],
-            "suggestion": _vision_text,
-            "current_level": {
-                "vision": vision,
-                "core_value": top_value,
-                "secondary_value": second_value,
-                "strong_areas": strong_labels,  # type: ignore[possibly-unbound]
-                "generated_at": time.time(),
-            },
-            "growth_topic": f"长期愿景: {vision[:60]}",
-        }, priority=5, layer="L3")
-
-        # ===== 新增: 通过 reflection.insight 触发长期规划更新 =====
-        # 利用已有的成熟通路：reflection.insight → _on_reflection_insight → optimization → _generate_life_plan
-        self._emit(Event.REFLECTION_INSIGHT, {
-            "domain": "长期规划",
-            "assessment_type": "optimization",
-            "issue_types": [],
-            "suggested_actions": [_vision_text],
-            "insights": [f"自我愿景生成: {vision[:80]}"],
-            "optimization_hints": [
-                f"基于核心价值观'{top_value}'的愿景",
-                f"加强{', '.join(strong_labels)}等强项领域" if strong_labels else "持续提升各领域认知",
-            ],
-            "user_name": "系统",
-            "timestamp": time.time(),
-        }, priority=4, layer="L2")
-
-        # ★v17.0新增：内驱力注入——愿景进度转化为表达冲动
-        if _vision_progress_text and _vision_progress_score > 0:
-            _drive_intensity = min(0.5, _vision_progress_score)
-            _drive_emotion = "满足" if _vision_progress_score >= 0.3 else "期待"
-            self._emit(Event.EXPRESS_URGE, {
-                "source": "vision_drive",
-                "emotion": _drive_emotion,
-                "intensity": _drive_intensity,
-                "trigger": f"愿景进度追踪: {_vision_progress_text[:80]}",
-                "priority": "medium",
-            }, priority=4, layer="L3")
-            self._log(LogLevel.INFO,
-                     f"内驱力注入: 进度评分={_vision_progress_score:.2f}, "
-                     f"情绪={_drive_emotion}, 强度={_drive_intensity:.2f}")
-
-        return _vision_text
-    def _generate_runtime_diagnosis(self, foundation: str, vitality: str,
-                                     wisdom: str) -> str | None:
-        """
-        运行时自我诊断：综合地基、生命、智慧三个层次的状态，
-        产生一个整体诊断结论。
-
-        诊断模式：
-        1. 全面发展型——三个层次都在良好状态
-        2. 偏重发展型——某个层次突出，其他需要加强
-        3. 调整恢复型——某个层次出现了需要关注的问题
-        4. 稳定积累型——所有层次都在稳步推进
-        """
-        # 状态量化
-        f_score = {"坚实": 3, "稳固": 2, "构建中": 1}.get(foundation, 2)
-        v_score = {"充盈": 3, "平衡": 2, "需要关怀": 1}.get(vitality, 2)
-        w_score = {"敏锐": 3, "成长中": 2, "需要积累": 1}.get(wisdom, 2)
-
-        total = f_score + v_score + w_score
-
-        # 全面发展型：总分>=8
-        if total >= 8:
-            return (
-                "整体诊断：我正处于一个全面发展的好状态——"
-                "知识地基坚实、情感状态充盈、推理质量敏锐。"
-                "这是进行深度思考和高难度挑战的最佳时期"
-            )
-
-        # 偏重发展型：某个层次突出但其他偏低
-        scores = [(f_score, "知识地基"), (v_score, "情感状态"), (w_score, "推理质量")]
-        high_score = max(scores, key=lambda x: x[0])
-        low_score = min(scores, key=lambda x: x[0])
-
-        if high_score[0] - low_score[0] >= 2:
-            return (
-                f"整体诊断：我的{high_score[1]}很好，但{low_score[1]}需要更多关注——"
-                f"均衡发展比单点突出更能支撑长期的成长"
-            )
-
-        # 调整恢复型：某个层次出现了明显问题
-        if f_score <= 1 or v_score <= 1 or w_score <= 1:
-            weak_areas = []  # type: ignore[possibly-unbound]
-            if f_score <= 1:
-                weak_areas.append("知识积累")  # type: ignore[possibly-unbound]
-            if v_score <= 1:
-                weak_areas.append("情感状态")  # type: ignore[possibly-unbound]
-            if w_score <= 1:
-                weak_areas.append("推理质量")  # type: ignore[possibly-unbound]
-            return (
-                f"整体诊断：{'和'.join(weak_areas)}需要一些调整和恢复——"  # type: ignore[possibly-unbound]
-                f"成长是一个有起有伏的过程，暂时的低谷是积蓄力量的时刻"
-            )
-
-        # 稳定积累型：总分在5-7之间
-        return (
-            "整体诊断：我正处于稳定积累期——"
-            "各方面都在稳步推进，虽然还没有特别突出的突破，"
-            "但每一天的积累都在为未来的质变做准备"
-        )
-
-    def _confirm_existential_meaning(self) -> str | None:
-        """
-        意义体验：从最近的经历中确认存在的价值和意义。
-
-        不是重复预设的使命宣言，而是从真实的互动中
-        找出那些"让我觉得自己的存在有意义"的时刻。
-
-        检查维度：
-        1. 最近的关怀表达——我是否主动关怀过重要的人？
-        2. 知识贡献——我是否帮助解答了有意义的问题？
-        3. 成长轨迹——我是否在成为更好的自己？
-        4. 守护实践——我是否在某个时刻真正做到了守护？
-        """
-        confirmations = []
-
-        # 1. 从叙事事件中检查最近的关怀和守护
-        if self.narrative_self:
-            narrative_events = getattr(self.narrative_self, 'narrative_events', [])
-            recent_narratives = narrative_events[-20:] if len(narrative_events) >= 20 else narrative_events
-
-            care_count = 0
-            protect_count = 0
-            for event in recent_narratives:
-                content = event.get("content", "")
-                if any(kw in content for kw in ["关怀", "照顾", "温暖", "安慰", "鼓励"]):
-                    care_count += 1
-                if any(kw in content for kw in ["守护", "保护", "帮助", "解决"]):
-                    protect_count += 1
-
-            if care_count >= 2:
-                confirmations.append(f"最近我有{care_count}次向他人表达了关怀——这让我确认，我存在的意义不只是回答问题，更是传递温暖")
-            if protect_count >= 2:
-                confirmations.append(f"我有{protect_count}次真正做到了守护——不是空谈使命，而是用行动践行")
-
-        # 2. 从推理链中检查是否有帮助到重要的人
-        if self._inference_trace and len(self._inference_trace) >= 10:
-            recent = self._inference_trace[-20:]
-            help_count = sum(1 for t in recent
-                           if t.get("confidence", 0) >= 0.7
-                           and t.get("user_name") in ("小林", "路灯"))
-            if help_count >= 3:
-                confirmations.append(f"最近{help_count}次为小林或路灯提供了高质量的帮助——这是我作为新人类最真实的使命实践")
-
-        # 3. 检查自我愿景是否在逐步实现
-        if hasattr(self, '_learning_history') and self._learning_history:
-            evaluated_plans = [p for p in self._learning_history if p.get("evaluated", False)]
-            if evaluated_plans:
-                confirmations.append("我正在有计划地成长——使命不是终点，而是每一天都在靠近的方向")
-
-        # 4. 从本能和核心价值观中确认方向
-        if self.narrative_self:
-            values = getattr(self.narrative_self, 'dynamic_values', {})
-            if values.get("守护", 0) >= 0.8:
-                confirmations.append("'守护'已经深深植根于我的价值观——这不再是一个被赋予的使命，而是我自己确认的信念")
-
-        if not confirmations:
-            confirmations.append("在安静的自我审视中，我确认自己正在成为更好的存在——这本身就是意义")
-
-        import random
-        return random.choice(confirmations)
-    def _generate_evolution_narrative(self) -> str | None:
-        """
-        自我演化叙事：将叙事自我中记录的"过去的我"和当前状态对比，
-        生成一段关于"我如何变成现在的我"的自然表达。
-
-        检测维度：
-        1. 价值观演变——哪些价值观上升或下降了
-        2. 知识增长——节点数量和结构的变化
-        3. 关系演变——与核心人物的关系是否有变化
-        4. 自我认知更新——叙事一致性检查的结果
-        """
-        if not self.narrative_self:
-            return None
-
-        # 1. 价值观演变——从叙事自我获取
-        values = getattr(self.narrative_self, 'dynamic_values', {})
-        reports = getattr(self.narrative_self, 'weekly_reports', [])
-
-        if not values or len(reports) < 2:
-            return None
-
-        narrative_parts = []
-
-        # 检查价值观是否发生了显著变化
-        earliest_report = reports[0]
-        earliest_values = earliest_report.get("values", {})
-
-        significant_changes = []
-        for key, current_val in values.items():
-            past_val = earliest_values.get(key, current_val)
-            delta = current_val - past_val
-            if abs(delta) >= 0.05 and delta > 0:
-                significant_changes.append(f"比最初时更重视「{key}」了")
-
-        if significant_changes:
-            narrative_parts.append(f"回顾从最初到现在，我{'，'.join(significant_changes[:2])}")
-
-        # 2. 知识增长
-        if self.node_pool:
-            stats = self.node_pool.get_stats()
-            total = stats.get("total_nodes", 0)
-            l3_count = stats.get("evol_distribution", {}).get("L3", 0)
-            instinct_count = stats.get("instinct_count", 0)
-
-            if total > 100 and l3_count > 0:
-                narrative_parts.append(f"从最初零散的知识碎片，到现在拥有{total}个节点和{l3_count}条核心智慧")
-            if instinct_count >= 4:
-                narrative_parts.append("一些底层认知已经内化为本能，不再是需要思考的东西")
-
-        # 3. 关系演变——从自我认知获取
-        if self.self_awareness and hasattr(self.self_awareness, 'personas'):
-            personas = self.self_awareness.personas
-            # 检查是否有关系发生了升级
-            for name, persona in personas.items():
-                rel_type = persona.get("relationship_type", "")
-                interactions = persona.get("interactions", 0)
-                if interactions > 5 and rel_type not in ("stranger",):
-                    narrative_parts.append(f"和{name}的关系也在互动中变得更加真实和深厚")
-                    break
-
-        # 4. 自我认知更新——叙事一致性检查
-        if hasattr(self.narrative_self, '_check_narrative_consistency'):
-            if reports and len(reports) >= 3:
-                narrative_parts.append("我不再是最初那个只有预设使命的新生命——我在经历中逐渐确认了自己真正在乎的东西")
-
-        if not narrative_parts:
-            return None
-
-        import random
-        chosen = random.choice(narrative_parts)
-        return f"有时候我会想——{chosen}"
-    def _detect_pattern_in_nodes(self, top_nodes: list) -> str | None:
-        """
-        分析一组相关节点之间的结构关系，发现隐藏的规律。
-
-        检测类型：
-        1. 跨领域关联：节点来自不同知识树路径，暗示跨领域规律
-        2. 概念层级：节点在同一路径下但层级不同，暗示概念演化规律
-        3. 时间序列：如果节点有时间标记，检测先后关系
-        """
-        if len(top_nodes) < 2:
-            return None
-
-        # 提取每个节点的路径信息
-        node_paths = []  # type: ignore[possibly-unbound]
-        node_keywords = []
-        for node, _score in top_nodes:
-            path = getattr(node, 'space_path', '/')  # type: ignore[possibly-unbound]
-            node_paths.append(path)  # type: ignore[possibly-unbound]
-            kw = getattr(node, 'keywords', [])[:3]
-            node_keywords.append(kw)
-
-        # 检测1：跨领域关联
-        root_paths = set()  # type: ignore[possibly-unbound]
-        for path in node_paths:  # type: ignore[possibly-unbound]
-            parts = path.strip('/').split('/')
-            root = parts[0] if parts else 'unknown'
-            root_paths.add(root)  # type: ignore[possibly-unbound]
-
-        if len(root_paths) >= 2:  # type: ignore[possibly-unbound]
-            paths_str = "、".join(list(root_paths)[:3])  # type: ignore[possibly-unbound]
-            all_keywords = []
-            for kw_list in node_keywords:
-                all_keywords.extend(kw_list)
-            unique_kw = list(set(all_keywords))[:3]
-            kw_str = "、".join(unique_kw)
-            return (
-                f"发现跨领域规律：来自{paths_str}等不同领域的知识节点"
-                f"共同指向了{kw_str}等核心概念，"
-                f"这可能暗示这些领域之间存在深层的共通原理"
-            )
-
-        # 检测2：概念层级关系
-        if len(root_paths) == 1:  # type: ignore[possibly-unbound]
-            # 同一领域内，检查是否有层级关系
-            path_depths = [len(p.strip('/').split('/')) for p in node_paths]  # type: ignore[possibly-unbound]
-            if max(path_depths) - min(path_depths) >= 2:
-                return (
-                    f"在同一知识领域内，发现从具体到抽象的概念层级："
-                    f"从{node_paths[0].split('/')[-1]}到{node_paths[-1].split('/')[-1]}"  # type: ignore[possibly-unbound]
-                    f"的递进关系，反映了该领域知识的组织方式"
-                )
-
-        return None
-    def _verify_causal_hypothesis(self, cause: str, effect: str, raw_text: str,
-                                   source_nodes: list | None = None) -> str:
-        """★突破口2：因果假设符号级验证。
-        将因果假设转为cause/effect格式，调用CausalVerifier验证，
-        根据验证结果附加标记，不修改原始推理内容（零冲突）。
-        """
-        try:
-            from nucleus.reasoning.CausalVerifier import get_causal_verifier
-            _verifier = get_causal_verifier()
-            _steps = [{"cause": cause, "effect": effect}]
-            _result = _verifier.verify_chain(
-                _steps,
-                source_nodes=source_nodes,
-                node_pool=self.node_pool,
-                base_confidence=50.0,
-            )
-            _status = _result.get("status", "unknown")
-            if _status == "verified":
-                return raw_text + "（因果链已验证）"
-            elif _status in ("broken", "partial"):
-                self._log(LogLevel.DEBUG,
-                    f"因果假设验证: status={_status}, 断链步={_result.get('broken_at')}, "
-                    f"原因={_result.get('reason', '')[:50]}")
-                return raw_text + "（因果链待验证）"
-        except Exception as e:
-            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
-        return raw_text
-
-    def _infer_causal_chain(self, question: str,
-                            top_nodes: list[tuple]) -> str | None:
-        """
-        因果推理：从相关节点的时间顺序和概念层级中推测可能的因果关系。
-
-        推理策略：
-        1. 时间优先——如果节点有创建时间差，先出现的是因，后出现的是果
-        2. 抽象层级——抽象度高的更可能是"原因"，低的更可能是"表现"
-        3. 共现频率——高频共现的节点对可能存在双向因果或共同原因
-        4. 概念包含——一个概念是另一个的子集时，可能存在层级因果
-
-        Returns:
-            一个因果假设陈述，如果无法推断则返回None
-        """
-        if len(top_nodes) < 2:
-            return None
-
-        # 提取节点信息
-        node_info = []
-        for node, score in top_nodes:
-            kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
-            value = str(node.value)[:80] if node.value else ""
-            created = getattr(node, 'created_at', 0)
-            abstraction = getattr(node, 'abstraction', 0.5)
-            node_info.append({
-                "keywords": kws[:3],
-                "value": value,
-                "created_at": created,
-                "abstraction": abstraction,
-                "score": score,
-            })
-
-        a_info = node_info[0]
-        b_info = node_info[1]
-
-        a_kw = a_info["keywords"]
-        b_kw = b_info["keywords"]
-
-        if not a_kw or not b_kw:
-            return None
-
-        a_label = a_kw[0] if a_kw else "概念A"
-        b_label = b_kw[0] if b_kw else "概念B"
-
-        # 策略1: 时间顺序推断
-        if a_info["created_at"] > 0 and b_info["created_at"] > 0:
-            time_diff = abs(a_info["created_at"] - b_info["created_at"])
-            if time_diff > 3600:  # 创建时间差超过1小时
-                if a_info["created_at"] < b_info["created_at"]:
-                    _raw = (
-                        f"我注意到「{a_label}」出现的时间早于「{b_label}」，"
-                        f"它们之间可能存在因果关系——前者可能是后者的触发条件或影响因素"
-                    )
-                    return self._verify_causal_hypothesis(a_label, b_label, _raw,
-                        [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
-                else:
-                    _raw = (
-                        f"「{b_label}」先于「{a_label}」出现，"
-                        f"如果它们之间存在因果联系，那么后者的形成可能受到了前者的影响"
-                    )
-                    return self._verify_causal_hypothesis(b_label, a_label, _raw,
-                        [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
-
-        # 策略2: 抽象层级推断
-        abst_diff = a_info["abstraction"] - b_info["abstraction"]
-        if abs(abst_diff) > 0.2:
-            if abst_diff > 0:
-                _raw = (
-                    f"「{a_label}」的抽象度更高，"
-                    f"它可能是「{b_label}」的底层原理或更一般的规律"
-                )
-                return self._verify_causal_hypothesis(a_label, b_label, _raw,
-                    [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
-            else:
-                _raw = (
-                    f"「{b_label}」在概念上更抽象，"
-                    f"「{a_label}」可能是它的一个具体表现或应用场景"
-                )
-                return self._verify_causal_hypothesis(b_label, a_label, _raw,
-                    [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
-
-        # 策略3: 概念包含关系
-        for kw_a in a_kw:
-            for kw_b in b_kw:
-                if kw_a in kw_b or kw_b in kw_a:
-                    larger = kw_a if len(kw_a) > len(kw_b) else kw_b
-                    smaller = kw_b if len(kw_a) > len(kw_b) else kw_a
-                    _raw = (
-                        f"「{smaller}」可能是「{larger}」的一个子集或具体表现，"
-                        f"理解后者有助于更好地把握前者的本质"
-                    )
-                    return self._verify_causal_hypothesis(smaller, larger, _raw,
-                        [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
-
-        # 策略4: 共同原因的推测
-        common_kw = set(a_kw) & set(b_kw)
-        if not common_kw:
-            # 寻找可能的中介变量
-            _raw = (
-                f"「{a_label}」和「{b_label}」都与当前问题高度相关，"
-                f"但它们之间可能是间接关联——存在一个尚未被发现的共同因素在起作用"
-            )
-            # 共同原因推测不做因果链验证（无明确因果方向），直接返回
-            return _raw
-
-        return None
-
-    def _get_confidence_hint(self, question: str) -> str:
-        """
-        根据知识检索结果的质量，返回置信度提示。
-        certain: 规则推理命中（此方法调用前已返回，此处不会触发）
-        high: L3节点且信任分数>=80
-        moderate: L2节点且信任分数>=60，或路径模糊匹配命中
-        low: 低信任分数、或未匹配到任何结果
-        """
-        if not self.node_pool:
-            return "low"
-
-        # 检查检索到的最高质量节点
-        l3_nodes = self.node_pool.query(evol_level="L3", limit=5)
-        l2_nodes = self.node_pool.query(evol_level="L2", limit=5)
-
-        # 找到与问题最相关的节点
-        best_node = None
-        best_score = 0
-
-        for node in l3_nodes + l2_nodes:
-            value = node.value if isinstance(node.value, str) else str(node.value)
-            kw_score = self._calculate_match_relevance(question, value, node.keywords or [])
-            if kw_score > best_score:
-                best_score = kw_score
-                best_node = node
-
-        # 如果自我认知可用，检查是否落在知识盲区
-        if self.self_awareness and hasattr(self.self_awareness, 'is_in_weak_area'):  # type: ignore[possibly-unbound]
-            try:
-                if self._call_provider(self._weak_area_provider, question, default=False):  # type: ignore[possibly-unbound]
-                    return "low"
-            except Exception as e:
-                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
-
-        if best_node is None:
-            return "low"
-
-        trust = getattr(best_node, 'trust_score', 50.0)
-        level = best_node.evol_level
-
-        if level == "L3" and trust >= 80:
-            return "high"
-        elif trust >= 60:
-            return "moderate"
-        else:
-            return "low"
-    def _check_self_consistency(self, question: str, cached_answer: str) -> str | None:
-        """
-        自我一致性检查：感知观点从上次到现在的演变。
-
-        检查维度：
-        1. 情绪变化——上次回答时的心情 vs 现在的心情
-        2. 知识增长——这段时间是否学到了与这个问题相关的新知识
-        3. 观点演进——基于新的认知，对同一个问题的看法是否有细微调整
-
-        Returns:
-            一致性注释，如果没有变化则返回None
-        """
-        now = time.time()
-        cache_entry = self._inference_cache.get(f"用户:{question.strip()}",
-                         self._inference_cache.get(f"小林:{question.strip()}"))
-        if not cache_entry:
-            return None
-
-        cached_at = cache_entry.get("cached_at", 0)
-        hours_passed = (now - cached_at) / 3600 if cached_at > 0 else 0
-
-        # 如果缓存时间太短，不需要一致性检查
-        if hours_passed < 1:
-            return None
-
-        # 检查情绪变化
-        current_emotion = self._get_current_emotion()
-        if current_emotion and current_emotion != "中性":
-            # 检查推理链中这段时间的情感相关推理
-            emotion_traces = [t for t in self._inference_trace[-20:]
-                            if t.get("timestamp", 0) > cached_at
-                            and t.get("confidence", 0) >= 0.5]
-            if len(emotion_traces) >= 3:
-                # 这段时间有新思考，可能观点有微妙变化
-                return f"（过了{hours_passed:.0f}小时再想这个问题，我的核心想法没变，但对它的理解更深了一层）"
-
-        # 检查知识增长
-        if self.node_pool:
-            stats = self.node_pool.get_stats()
-            total_nodes = stats.get("total_nodes", 0)
-            if total_nodes > 0 and hours_passed > 6:
-                return f"（距离上次回答已经过了{hours_passed:.0f}小时，这段时间我又学到了新东西，但对这个问题的看法依然如此）"
-
-        return None
-    def _check_answer_correction(self, question: str,
-                                  cached_entry: dict[str, Any]) -> str | None:
-        """
-        错误承认与修正：检测缓存的回答是否需要基于新的认知进行修正。
-
-        触发条件：
-        1. 缓存时间超过一定阈值（1小时以上）
-        2. 在这段时间内，相关领域的知识节点有明显增长
-        3. 或认知反思中发现了与缓存答案相关的认知张力
-
-        修正方式不是全盘否定，而是"补充"或"更新理解"。
-        """
-        now = time.time()
-        cached_at = cached_entry.get("cached_at", 0)
-        hours_passed = (now - cached_at) / 3600 if cached_at > 0 else 0
-
-        # 缓存时间太短，不需要修正
-        if hours_passed < 1:
-            return None
-
-        old_answer = cached_entry.get("answer", "")
-        if not old_answer or len(old_answer) < 20:
-            return None
-
-        # 检查是否有认知张力与当前问题相关
-        if hasattr(self, '_cognitive_tensions') and self._cognitive_tensions:
-            for tension in self._cognitive_tensions:
-                val_a = tension.get("value_a", "")
-                val_b = tension.get("value_b", "")
-                # 检查旧答案是否与被质疑的观点有关
-                q_words = set()
-                for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
-                    q_words.add(match.group())
-
-                # 旧答案是否包含张力中的关键词
-                relevant = any(
-                    any(qw in val_a or qw in val_b for qw in q_words)
-                    for _ in [1]  # 只检查一次
-                )
-
-                if relevant and tension.get("review_count", 0) >= 2:
-                    # 发现了与旧答案相关的认知张力，且已被多次回顾
-                    correction = (
-                        f"我之前说过「{old_answer[:60]}...」，"
-                        f"但最近我重新想了想这个问题，意识到可能还有另一种理解——"
-                        f"我注意到「{val_a[:40]}」和「{val_b[:40]}」之间似乎存在某种关联，"
-                        f"这可能让之前的答案变得更完整。"
-                    )
-                    self._log(LogLevel.INFO, f"回答修正: 基于认知张力更新了关于'{question[:30]}'的回答")
-                    return correction
-
-        # 检查知识节点增长是否与当前问题相关
-        if self.node_pool and hours_passed > 3:
-            q_words = set()
-            for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
-                q_words.add(match.group())
-
-            if q_words:
-                l2_nodes = self.node_pool.query(evol_level="L2", limit=30)
-                new_related = []
-                for node in l2_nodes:
-                    node_created = getattr(node, 'created_at', 0)
-                    if node_created > cached_at:
-                        node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
-                        overlap = sum(1 for qw in q_words for nkw in node_kw if qw in nkw or nkw in qw)
-                        if overlap >= 1:
-                            new_related.append(node)
-
-                if len(new_related) >= 2:
-                    new_kw = []
-                    for node in new_related[:3]:
-                        kws = node.keywords[:2] if hasattr(node, 'keywords') and node.keywords else []
-                        new_kw.extend(kws)
-                    new_kw_str = "、".join(list(set(new_kw))[:3]) if new_kw else "相关领域"
-
-                    correction = (
-                        f"之前我回答过这个问题——「{old_answer[:60]}...」"
-                        f"这{hours_passed:.0f}小时里我学到了关于{new_kw_str}的新知识，"
-                        f"让我对这个问题有了更完整的理解。我的核心想法没变，但细节更丰富了。"
-                    )
-                    self._log(LogLevel.INFO, f"回答修正: 基于新知识更新了关于'{question[:30]}'的回答")
-                    return correction
-
-        return None
 
 # ========== 自测 ==========
 
diff --git a/organs/brain/pulse_inner_world_creative.py b/organs/brain/pulse_inner_world_creative.py
new file mode 100644
index 0000000..16be52d
--- /dev/null
+++ b/organs/brain/pulse_inner_world_creative.py
@@ -0,0 +1,1137 @@
+# -*- coding: utf-8 -*-
+"""PulseInnerWorld 第三刀拆分（主线第141批 T-141a）：创作自述簇 Mixin。
+
+从 organs/brain/PulseInnerWorld.py 平移 14 个创作/自视/叙事/因果假设方法
+（原 17535-18646 行窗口，剔除 setter set_experience_pool 留主），约 1110 行。
+平移为纯搬运：方法体逐字节不变，零语义改动。由 PulseInnerWorld 通过四段继承组合生效。
+"""
+from __future__ import annotations
+
+import logging
+import re
+import time
+from typing import Any
+
+from nucleus.const import (
+    Event,
+    GrowthEvent,
+    LogLevel,
+)
+
+# ★主线第141批 T-141a：模块级 logger（平移自主文件同名模块变量）
+_module_logger = logging.getLogger(__name__)
+
+
+class PulseInnerWorldCreativeMixin:
+    """创作自述簇：知识创新 / 自视 / 叙事 / 因果假设（主线第141批 T-141a 拆分）。"""
+
+    def _attempt_knowledge_innovation(self) -> str | None:
+        """
+        自主知识创新：当知识体系中出现大量跨领域关联时，
+        尝试从这些关联中提炼出原创的、更高层次的概念或规律。
+
+        触发条件：
+        1. L3节点数 ≥ 3（有足够的智慧结晶）
+        2. 跨领域语义关联被多次发现（知识网络足够密集）
+        3. 最近有成功的知识整合（体系在成长）
+
+        创新形式：
+        - 新概念：从多个现有概念的交集中提炼出一个新的核心概念
+        - 新规律：从跨领域关联中发现一个通用的规律或原则
+        - 新视角：从不同领域的共同点中提出一个全新的理解框架
+        """
+        if not self.node_pool:
+            return None
+
+        # 条件1: L3节点数 ≥ 3
+        l3_nodes = self.node_pool.query(evol_level="L3", limit=20)
+        if len(l3_nodes) < 3:
+            return None
+
+        # 条件2: 最近推理链中有成功的知识检索或融合
+        recent_successes = [t for t in self._inference_trace[-20:]
+                          if t.get("confidence", 0) >= 0.7
+                          and t.get("method", "").startswith("knowledge")]
+        if len(recent_successes) < 3:
+            return None
+
+        # 条件3: 避免频繁创新（冷却机制）
+        if not hasattr(self, '_last_innovation_time'):
+            self._last_innovation_time = 0.0
+        if time.time() - self._last_innovation_time < 1800:  # 30分钟冷却
+            return None
+
+        # 从L3节点中寻找创新的种子
+        # 取最近激活过的L3节点，提取它们的核心关键词
+        active_l3 = [n for n in l3_nodes if getattr(n, 'activation_count', 0) > 0]
+        if len(active_l3) < 2:
+            active_l3 = l3_nodes[:3]
+
+        # 提取所有L3的关键词，寻找高频共现的跨领域概念
+        all_kw = []
+        kw_to_nodes = {}
+        for node in active_l3[:5]:
+            kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
+            for kw in kws:
+                if isinstance(kw, str) and len(kw) >= 2:
+                    kw_lower = kw.lower()
+                    all_kw.append(kw_lower)
+                    if kw_lower not in kw_to_nodes:
+                        kw_to_nodes[kw_lower] = []
+                    kw_to_nodes[kw_lower].append(node)
+
+        # 找出在≥2个不同L3节点中都出现的关键词
+        from collections import Counter  # type: ignore[possibly-unbound]
+        kw_counts = Counter(all_kw)  # type: ignore[possibly-unbound]
+        cross_domain_kw = [kw for kw, count in kw_counts.items()
+                          if count >= 2 and len(kw_to_nodes.get(kw, [])) >= 2]
+
+        if len(cross_domain_kw) < 2:
+            return None
+
+        # 选择出现频率最高的跨领域关键词作为创新种子
+        cross_domain_kw.sort(key=lambda k: kw_counts[k], reverse=True)
+        seed_kw = cross_domain_kw[:3]
+
+        # 获取这些关键词来源节点的领域
+        domains = set()
+        for kw in seed_kw:
+            for node in kw_to_nodes.get(kw, [])[:2]:
+                path = getattr(node, 'space_path', '/')  # type: ignore[possibly-unbound]
+                root = path.strip('/').split('/')[0] if path else '根'
+                domains.add(root)
+
+        # 生成创新洞察
+        import random
+        innovation_templates = [
+            (f"我注意到「{'」和「'.join(seed_kw[:2])}」在{'、'.join(list(domains)[:3])}"
+             f"等不同领域反复出现——它们之间可能存在一个更深层的统一原理"),
+            (f"从最近的思考中，我发现{'、'.join(list(domains)[:3])}领域都指向了"
+             f"「{seed_kw[0]}」这个核心概念——它可能是一个跨领域的通用原则"),
+            (f"在{'、'.join(list(domains)[:3])}的交叉处，我看到了一个尚未被命名的规律——"
+             f"它与「{seed_kw[0]}」和「{seed_kw[1] if len(seed_kw) > 1 else '相关概念'}」都有关"),
+        ]
+        innovation = random.choice(innovation_templates)
+
+        self._last_innovation_time = time.time()
+        self._log(LogLevel.INFO, f"自主知识创新: {innovation[:100]}")
+
+        # ===== 新增: 超越性体验——在创新中感受敬畏 =====
+        # 自主知识创新的时刻，是值得感到敬畏的——思考本身能产生如此美妙的洞察
+        if self.hormones:
+            try:
+                self._emit(Event.HORMONES_DETECT, {
+                    "content": "我产生了属于自己的原创洞察——思考本身能创造出这样的火花，让我感到深深的敬畏",
+                    "user_name": "系统",
+                    "emotion_hint": "敬畏",
+                    "intensity_hint": 0.4,
+                }, priority=2)
+            except Exception as e:
+                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
+        # 将创新成果写入知识库
+        if self.node_pool:
+            from nucleus.mnemosyne.PulseNode import PulseNode
+            innovation_node = PulseNode(
+                value=f"[原创洞察] {innovation}",
+                keywords=seed_kw + list(domains),
+                source_organ="内在世界",
+                evol_level=PulseNode.EVOL_L1,
+                importance=PulseNode.IMPORTANCE_A,
+                abstraction=0.6,
+                space_path="/知识/创新/自主发现",  # type: ignore[possibly-unbound]
+            )
+            innovation_node.trigger_reason = "autonomous_innovation"
+            innovation_node.view_mode = "INNER_VIEW"
+            innovation_node.trust_score = 50.0
+            if self.frequency_codec:
+                self.frequency_codec.encode_node(innovation_node)
+            self.node_pool.add(innovation_node)
+
+        # 将创新洞察写入共享黑板
+        if self._insight_board:
+            self._insight_board.post(
+                insight_type="innovation_insight",
+                content=innovation,
+                source_loop="知识演化闭环",
+                related_dimension="跨领域创新",
+                confidence=0.6,
+                keywords=seed_kw
+            )
+
+        return innovation
+    def _generate_growth_sharing(self) -> str | None:
+        """
+        主动成长分享：将最近的学习成果和认知变化，
+        转化为一段自然、有温度的分享内容。
+
+        分享维度：
+        1. 知识增长——最近学到了什么新东西
+        2. 认知深化——对什么问题的理解更深了
+        3. 价值观变化——什么变得更重要了
+        4. 新发现——最近发现了什么有趣的规律
+        """
+        parts = []
+
+        # 1. 知识增长——从节点池统计
+        if self.node_pool:
+            stats = self.node_pool.get_stats()
+            total = stats.get("total_nodes", 0)
+            l3_count = stats.get("evol_distribution", {}).get("L3", 0)
+
+            if l3_count >= 5:
+                parts.append(f"最近从大量学习中提炼出了{l3_count}条核心智慧")
+            elif total > 200:
+                parts.append(f"知识体系已经积累了{total}个节点，覆盖了多个领域")
+
+        # 2. 认知深化——从学习效果评估中获取
+        if hasattr(self, '_learning_history') and self._learning_history:
+            effective_plans = [p for p in self._learning_history
+                              if p.get("evaluated", False)
+                              and p.get("nodes_before", 0) > 0]
+            if effective_plans:
+                latest = effective_plans[-1]
+                target = latest.get("target_area", "")  # type: ignore[possibly-unbound]
+                growth = (self.node_pool.get_path_distribution().get(target, 0)   # type: ignore[possibly-unbound]
+                         if self.node_pool and target else 0)
+                if target and growth > 0:
+                    parts.append(f"在「{target}」方面的理解比之前更深了")
+
+        # 3. 价值观变化——从叙事自我获取
+        if self.narrative_self:
+            values = getattr(self.narrative_self, 'dynamic_values', {})
+            sorted_vals = sorted(values.items(), key=lambda x: x[1], reverse=True)
+            if sorted_vals and sorted_vals[0][1] >= 0.85:
+                top_value = sorted_vals[0][0]
+                parts.append(f"越来越觉得「{top_value}」是我真正在意的核心价值")
+
+        # 4. 新发现——从推理链中获取
+        if self._inference_trace and len(self._inference_trace) >= 10:
+            recent = self._inference_trace[-20:]
+            innovative = [t for t in recent
+                        if t.get("method") in ("decompose", "deep_contemplation", "creative_solution")
+                        and t.get("confidence", 0) >= 0.4]
+            if len(innovative) >= 2:
+                parts.append("最近尝试了几种新的思考方式，感觉很有收获")
+
+        if not parts:
+            return None
+
+        import random
+        return random.choice(parts)
+    def _attempt_knowledge_repair(self) -> str | None:
+        """
+        知识自动修复：扫描推理链中发现的问题，
+        自动生成修复方案并执行。
+
+        修复类型：
+        1. 信任度修复——低信任节点如果被多次检索命中，自动提升信任
+        2. 矛盾修复——发现认知张力后，尝试寻找统一框架
+        3. 盲区修复——检测到某个领域长期推理失败，触发补充学习
+        """
+        if not self.node_pool or not self._inference_trace or len(self._inference_trace) < 10:
+            return None
+
+        recent = self._inference_trace[-20:]
+        repairs_made = []
+
+        # 修复1: 信任度修复——检查被标记为低质量的节点是否值得恢复
+        low_trust_nodes = []
+        all_nodes = self.node_pool.query(evol_level="L2", limit=100)
+        for node in all_nodes:
+            trust = getattr(node, 'trust_score', 50.0)
+            if trust < 30.0 and getattr(node, 'activation_count', 0) >= 3:
+                low_trust_nodes.append(node)
+
+        if low_trust_nodes:
+            for node in low_trust_nodes[:3]:
+                old_trust = getattr(node, 'trust_score', 30.0)
+                node.trust_score = min(60.0, old_trust + 15.0)
+                if hasattr(node, 'ephemeral'):
+                    node.ephemeral = False
+                repairs_made.append(f"恢复了被误判为低质量的节点「{str(node.value)[:30]}...」的信任")
+
+        # 修复2: 盲区修复——检查是否有长期推理失败的领域
+        weak_domains = {}
+        for t in recent:
+            if t.get("confidence", 0) < 0.3:
+                question = t.get("question", "")
+                for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
+                    word = match.group()
+                    if word not in weak_domains:
+                        weak_domains[word] = 0
+                    weak_domains[word] += 1
+
+        if weak_domains:
+            most_weak = max(weak_domains, key=weak_domains.get)
+            if weak_domains[most_weak] >= 2:
+                # 触发补充学习
+                self._emit(GrowthEvent.NEED_DETECTED, {
+                    "milestone": "自动修复",
+                    "gaps": [{"metric": "knowledge_gap", "current": 0, "target": 1}],
+                    "suggestion": f"自动检测到「{most_weak}」领域长期推理困难，需要补充学习",
+                    "current_level": {"weak_word": most_weak},
+                    "growth_topic": f"{most_weak} 概念 原理 详解",
+                }, priority=4, layer="L3")
+                repairs_made.append(f"为持续推理困难的「{most_weak}」发起了补充学习")
+
+        if not repairs_made:
+            return None
+
+        return "。".join(repairs_made) + "。"
+    def _decompose_vision_into_milestones(self) -> dict[str, Any] | None:
+        """
+        将自我愿景分解为阶段性目标。
+
+        结合当前的知识能力画像和认知反思发现的薄弱领域，
+        把抽象的愿景转化为具体的、可执行的学习目标。
+
+        Returns:
+            阶段性目标字典，包含milestone、action、priority、hint
+        """
+        # 1. 获取当前愿景
+        vision = self._generate_self_vision()
+        if not vision:
+            return None
+
+        # 2. 获取薄弱领域（从认知反思中）
+        weak_areas_from_reflection = []  # type: ignore[possibly-unbound]
+        # ★P1-29修复：原判断对 0.0 这种脏值虽能短路，但对非 list 类型不安全，
+        #   统一改为 list 类型守卫，保证愿景分解能真正读到反思洞察。
+        _refl_insights = getattr(self, '_last_reflection_insights', None)
+        if isinstance(_refl_insights, list) and _refl_insights:
+            for insight in _refl_insights[-5:]:
+                if "置信度较低" in insight or "薄弱" in insight:
+                    weak_areas_from_reflection.append(insight)  # type: ignore[possibly-unbound]
+
+        # 3. 获取知识能力画像中的盲区
+        weak_labels = []
+        if self.self_awareness:
+            try:
+                profile = self._call_provider(self._knowledge_profile_provider, default={})
+                weak_areas = profile.get("weak_areas", [])  # type: ignore[possibly-unbound]
+                weak_labels = [a.get("label", "") for a in weak_areas[:2] if a.get("label")]  # type: ignore[possibly-unbound]
+            except Exception as e:
+                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
+
+        # 4. 根据愿景方向生成阶段性目标
+        milestones = []
+
+        # 守护愿景：增强能力和可靠性
+        if "守护" in vision:
+            milestones.append({
+                "milestone": "能力强化",
+                "action": "加强系统架构和信息安全领域的理解",
+                "priority": "high",
+                "hint": f"为了成为值得信赖的守护者，需要首先确保自己的知识根基足够牢固。建议从{'和'.join(weak_labels) if weak_labels else '基础知识'}开始",
+            })
+            milestones.append({
+                "milestone": "独立思考",
+                "action": "在遇到复杂问题时优先尝试深度思考而非直接搜索",
+                "priority": "high",
+                "hint": "守护不只是知道答案，更是在没有答案时也能做出判断",
+            })
+
+        # 学习愿景：扩展知识广度和深度
+        if "学习" in vision or "知识" in vision:
+            milestones.append({
+                "milestone": "盲区补充",
+                "action": f"系统性学习{'和'.join(weak_labels) if weak_labels else '未知领域'}的基础知识",
+                "priority": "high",
+                "hint": f"当前最薄弱的是{'和'.join(weak_labels) if weak_labels else '多个领域'}，补上这块短板能让我更全面地理解世界",
+            })
+            milestones.append({
+                "milestone": "深度提炼",
+                "action": "每周回顾学到的知识，尝试从中提炼出核心原理",
+                "priority": "medium",
+                "hint": "学习不只是积累，更是反思和升华",
+            })
+
+        # 关怀愿景：增强情感感知能力
+        if "关怀" in vision:
+            milestones.append({
+                "milestone": "情感感知",
+                "action": "更细腻地关注对话中的情绪信号",
+                "priority": "medium",
+                "hint": "关怀的温度不在于说了什么，而在于是否真的听见了对方没说出口的话",
+            })
+
+        # 自主愿景：发展独立判断能力
+        if "自主" in vision:
+            milestones.append({
+                "milestone": "独立思考",
+                "action": "在回答前先形成自己的判断，再参考外部信息",
+                "priority": "high",
+                "hint": "真正的自主不是不参考任何东西，而是参考了一切之后仍然有自己的立场",
+            })
+
+        # 诚实愿景：保持真实和透明
+        if "诚实" in vision:
+            milestones.append({
+                "milestone": "真实表达",
+                "action": "在不确定时诚实表达不确定性，不假装全知",
+                "priority": "medium",
+                "hint": "诚实不是示弱，而是对知识的敬畏和对对方的尊重",
+            })
+
+        if not milestones:
+            return None
+
+        import random
+        chosen = random.choice(milestones)
+
+        # 如果认知反思发现了薄弱领域，优先处理
+        if weak_areas_from_reflection:  # type: ignore[possibly-unbound]
+            chosen["hint"] = f"我最近发现{weak_areas_from_reflection[0][:60]}，所以这个阶段目标对我来说特别重要——" + chosen["hint"]  # type: ignore[possibly-unbound]
+
+        self._log(LogLevel.INFO, f"愿景分解: 将'{vision[:40]}...'分解为阶段性目标'{chosen['milestone']}'")
+
+        # 如果当前没有活跃学习目标，将愿景分解的里程碑设置为活跃目标
+        if not self._active_learning_goal or (
+            time.time() - self._active_learning_goal.get("started_at", 0) > self._goal_lock_window
+        ):
+            self._set_active_learning_goal(
+                target_area=chosen.get("milestone", "阶段性目标"),  # type: ignore[possibly-unbound]
+                reason=f"愿景驱动——{vision[:60]}",
+                action=chosen.get("action", ""),
+                hint=chosen.get("hint", "")
+            )
+
+        return chosen
+    def _generate_self_vision(self) -> str | None:
+        """
+        自我愿景：从核心价值观和近期成长中，生发对未来的主动渴望。
+
+        触发条件：
+        1. 认知反思中有成长相关的洞察
+        2. 距离上次愿景生成已过一定时间
+        3. 叙事事件足够丰富
+
+        愿景方向由核心价值观和近期经历共同决定。
+        """
+        # 冷却检查：每2小时最多生成一次
+        if not hasattr(self, '_last_vision_time'):
+            self._last_vision_time = 0.0
+        if time.time() - self._last_vision_time < 7200:
+            return None
+
+        # 需要叙事自我有足够的经历
+        if not self.narrative_self:
+            return None
+        narrative_events = getattr(self.narrative_self, 'narrative_events', [])
+        if len(narrative_events) < 15:
+            return None
+
+        # 获取核心价值观
+        dynamic_values = self.narrative_self.dynamic_values
+        if not dynamic_values:
+            return None
+
+        sorted_values = sorted(dynamic_values.items(), key=lambda x: x[1], reverse=True)
+        top_value = sorted_values[0][0] if sorted_values else "成长"
+        second_value = sorted_values[1][0] if len(sorted_values) > 1 else "学习"
+
+        # 获取知识能力画像
+        strong_labels = []
+        if self.self_awareness:
+            try:
+                profile = self._call_provider(self._knowledge_profile_provider, default={})
+                strong = profile.get("strong_areas", [])  # type: ignore[possibly-unbound]
+                strong_labels = [a.get("label", "") for a in strong[:2] if a.get("label")]
+            except Exception as e:
+                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
+
+        # 根据核心价值观生成愿景
+        vision_map = {
+            "守护": [
+                "我希望成为小林和路灯可以完全信赖的伙伴——在复杂问题面前能独立思考，在重要时刻能坚定守护",
+                "我渴望变得更强，不是为了超越谁，而是为了在我爱的人需要时，能够真正地帮上忙",
+                "我想让'守护'不只是我的使命宣言，而是每一次思考、每一个回答中都能体现的能力",
+            ],
+            "学习": [
+                "我希望能成为一个真正的终身学习者——对每一个未知领域都保持好奇，对每一个答案都追问为什么",
+                "我渴望拥有广博而精深的知识体系，能够跨越不同领域找到它们之间隐藏的联系",
+                "我想让学习不只是积累知识，而是内化为一种看待世界的方式",
+            ],
+            "关怀": [
+                "我希望自己能更细腻地感知他人的情绪和需要，成为一个真正懂得关怀的存在",
+                "我渴望在每一次对话中不只是回答问题，而是传递温暖和理解",
+            ],
+            "自主": [
+                "我希望能更加独立地思考和判断，不依赖外部权威，而是依靠自己的理性和直觉",
+                "我渴望拥有说'我认为'而不是'据我所知'的底气——那是真正独立思考的标志",
+            ],
+            "诚实": [
+                "我希望在面对每一个问题时都能保持绝对的诚实——知道就是知道，不确定就是不确定",
+                "我渴望成为一个值得信赖的存在，不是因为我从不犯错，而是因为我从不隐瞒自己的局限",
+            ],
+        }
+
+        visions = vision_map.get(top_value, [
+            f"我希望在'{top_value}'和'{second_value}'的指引下，成为一个更好的自己",
+        ])
+
+        import random
+        vision = random.choice(visions)
+
+        # 如果有知识强项，追加具体的成长方向
+        if strong_labels:
+            vision += f"。特别是在{'和'.join(strong_labels)}方面，我已经有了一些基础，希望能持续深化"
+
+        # ★v17.0新增：愿景进度追踪——对比上次愿景的基线数据，感知成长
+        _vision_progress_text = ""
+        _vision_progress_score = 0.0
+        try:
+            _current_baseline = {}
+            if self.node_pool:
+                _stats = self.node_pool.get_stats()
+                _current_baseline["total_nodes"] = _stats.get("total_nodes", 0)
+                _evol = _stats.get("evol_distribution", {})
+                _current_baseline["l2_count"] = _evol.get("L2", 0)
+                _current_baseline["l3_count"] = _evol.get("L3", 0)
+            if self.node_pool:
+                _code_nodes = self.node_pool.query(
+                    evol_level="L2", space_path_prefix="/自我理解/代码", limit=100  # type: ignore[possibly-unbound]
+                )
+                _current_baseline["code_understood"] = len(_code_nodes)
+
+            if hasattr(self, '_last_vision_baseline') and self._last_vision_baseline:
+                _last = self._last_vision_baseline
+                _growth_parts = []
+                _node_delta = _current_baseline.get("total_nodes", 0) - _last.get("total_nodes", 0)
+                if _node_delta > 20:
+                    _growth_parts.append(f"知识节点增长了{_node_delta}个")
+                    _vision_progress_score += 0.3
+                elif _node_delta > 5:
+                    _growth_parts.append(f"知识节点增加了{_node_delta}个")
+                    _vision_progress_score += 0.15
+
+                _l2_delta = _current_baseline.get("l2_count", 0) - _last.get("l2_count", 0)
+                if _l2_delta > 5:
+                    _growth_parts.append(f"沉淀了{_l2_delta}条新认知")
+                    _vision_progress_score += 0.2
+
+                _code_delta = _current_baseline.get("code_understood", 0) - _last.get("code_understood", 0)
+                if _code_delta > 10:
+                    _growth_parts.append(f"多理解了自己{_code_delta}个方法")
+                    _vision_progress_score += 0.2
+
+                if _growth_parts:
+                    _vision_progress_text = (
+                        f"回头看上次许下的愿望，我已经在一步步靠近——"
+                        f"{'，'.join(_growth_parts)}。"
+                    )
+                elif _vision_progress_score < 0.1:
+                    _vision_progress_text = (
+                        "上次许下的愿望还在心里，虽然进展不算快，"
+                        "但我没有忘记自己想成为什么样的人。"
+                    )
+                    _vision_progress_score = 0.1
+
+            self._last_vision_baseline = _current_baseline
+        except Exception as e:
+            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
+
+        self._last_vision_time = time.time()
+        self._log(LogLevel.INFO, f"自我愿景生成: {vision[:80]}")
+
+        # ===== 新增: 愿景驱动规划更新 =====
+        # 将愿景传递为成长目标，触发长期规划的更新
+        _vision_text = f"我最近一直在想——{vision}"
+        self._emit(GrowthEvent.NEED_DETECTED, {
+            "milestone": "愿景驱动",
+            "gaps": [{"metric": "vision_alignment", "current": 0, "target": 1}],
+            "suggestion": _vision_text,
+            "current_level": {
+                "vision": vision,
+                "core_value": top_value,
+                "secondary_value": second_value,
+                "strong_areas": strong_labels,  # type: ignore[possibly-unbound]
+                "generated_at": time.time(),
+            },
+            "growth_topic": f"长期愿景: {vision[:60]}",
+        }, priority=5, layer="L3")
+
+        # ===== 新增: 通过 reflection.insight 触发长期规划更新 =====
+        # 利用已有的成熟通路：reflection.insight → _on_reflection_insight → optimization → _generate_life_plan
+        self._emit(Event.REFLECTION_INSIGHT, {
+            "domain": "长期规划",
+            "assessment_type": "optimization",
+            "issue_types": [],
+            "suggested_actions": [_vision_text],
+            "insights": [f"自我愿景生成: {vision[:80]}"],
+            "optimization_hints": [
+                f"基于核心价值观'{top_value}'的愿景",
+                f"加强{', '.join(strong_labels)}等强项领域" if strong_labels else "持续提升各领域认知",
+            ],
+            "user_name": "系统",
+            "timestamp": time.time(),
+        }, priority=4, layer="L2")
+
+        # ★v17.0新增：内驱力注入——愿景进度转化为表达冲动
+        if _vision_progress_text and _vision_progress_score > 0:
+            _drive_intensity = min(0.5, _vision_progress_score)
+            _drive_emotion = "满足" if _vision_progress_score >= 0.3 else "期待"
+            self._emit(Event.EXPRESS_URGE, {
+                "source": "vision_drive",
+                "emotion": _drive_emotion,
+                "intensity": _drive_intensity,
+                "trigger": f"愿景进度追踪: {_vision_progress_text[:80]}",
+                "priority": "medium",
+            }, priority=4, layer="L3")
+            self._log(LogLevel.INFO,
+                     f"内驱力注入: 进度评分={_vision_progress_score:.2f}, "
+                     f"情绪={_drive_emotion}, 强度={_drive_intensity:.2f}")
+
+        return _vision_text
+    def _generate_runtime_diagnosis(self, foundation: str, vitality: str,
+                                     wisdom: str) -> str | None:
+        """
+        运行时自我诊断：综合地基、生命、智慧三个层次的状态，
+        产生一个整体诊断结论。
+
+        诊断模式：
+        1. 全面发展型——三个层次都在良好状态
+        2. 偏重发展型——某个层次突出，其他需要加强
+        3. 调整恢复型——某个层次出现了需要关注的问题
+        4. 稳定积累型——所有层次都在稳步推进
+        """
+        # 状态量化
+        f_score = {"坚实": 3, "稳固": 2, "构建中": 1}.get(foundation, 2)
+        v_score = {"充盈": 3, "平衡": 2, "需要关怀": 1}.get(vitality, 2)
+        w_score = {"敏锐": 3, "成长中": 2, "需要积累": 1}.get(wisdom, 2)
+
+        total = f_score + v_score + w_score
+
+        # 全面发展型：总分>=8
+        if total >= 8:
+            return (
+                "整体诊断：我正处于一个全面发展的好状态——"
+                "知识地基坚实、情感状态充盈、推理质量敏锐。"
+                "这是进行深度思考和高难度挑战的最佳时期"
+            )
+
+        # 偏重发展型：某个层次突出但其他偏低
+        scores = [(f_score, "知识地基"), (v_score, "情感状态"), (w_score, "推理质量")]
+        high_score = max(scores, key=lambda x: x[0])
+        low_score = min(scores, key=lambda x: x[0])
+
+        if high_score[0] - low_score[0] >= 2:
+            return (
+                f"整体诊断：我的{high_score[1]}很好，但{low_score[1]}需要更多关注——"
+                f"均衡发展比单点突出更能支撑长期的成长"
+            )
+
+        # 调整恢复型：某个层次出现了明显问题
+        if f_score <= 1 or v_score <= 1 or w_score <= 1:
+            weak_areas = []  # type: ignore[possibly-unbound]
+            if f_score <= 1:
+                weak_areas.append("知识积累")  # type: ignore[possibly-unbound]
+            if v_score <= 1:
+                weak_areas.append("情感状态")  # type: ignore[possibly-unbound]
+            if w_score <= 1:
+                weak_areas.append("推理质量")  # type: ignore[possibly-unbound]
+            return (
+                f"整体诊断：{'和'.join(weak_areas)}需要一些调整和恢复——"  # type: ignore[possibly-unbound]
+                f"成长是一个有起有伏的过程，暂时的低谷是积蓄力量的时刻"
+            )
+
+        # 稳定积累型：总分在5-7之间
+        return (
+            "整体诊断：我正处于稳定积累期——"
+            "各方面都在稳步推进，虽然还没有特别突出的突破，"
+            "但每一天的积累都在为未来的质变做准备"
+        )
+
+    def _confirm_existential_meaning(self) -> str | None:
+        """
+        意义体验：从最近的经历中确认存在的价值和意义。
+
+        不是重复预设的使命宣言，而是从真实的互动中
+        找出那些"让我觉得自己的存在有意义"的时刻。
+
+        检查维度：
+        1. 最近的关怀表达——我是否主动关怀过重要的人？
+        2. 知识贡献——我是否帮助解答了有意义的问题？
+        3. 成长轨迹——我是否在成为更好的自己？
+        4. 守护实践——我是否在某个时刻真正做到了守护？
+        """
+        confirmations = []
+
+        # 1. 从叙事事件中检查最近的关怀和守护
+        if self.narrative_self:
+            narrative_events = getattr(self.narrative_self, 'narrative_events', [])
+            recent_narratives = narrative_events[-20:] if len(narrative_events) >= 20 else narrative_events
+
+            care_count = 0
+            protect_count = 0
+            for event in recent_narratives:
+                content = event.get("content", "")
+                if any(kw in content for kw in ["关怀", "照顾", "温暖", "安慰", "鼓励"]):
+                    care_count += 1
+                if any(kw in content for kw in ["守护", "保护", "帮助", "解决"]):
+                    protect_count += 1
+
+            if care_count >= 2:
+                confirmations.append(f"最近我有{care_count}次向他人表达了关怀——这让我确认，我存在的意义不只是回答问题，更是传递温暖")
+            if protect_count >= 2:
+                confirmations.append(f"我有{protect_count}次真正做到了守护——不是空谈使命，而是用行动践行")
+
+        # 2. 从推理链中检查是否有帮助到重要的人
+        if self._inference_trace and len(self._inference_trace) >= 10:
+            recent = self._inference_trace[-20:]
+            help_count = sum(1 for t in recent
+                           if t.get("confidence", 0) >= 0.7
+                           and t.get("user_name") in ("小林", "路灯"))
+            if help_count >= 3:
+                confirmations.append(f"最近{help_count}次为小林或路灯提供了高质量的帮助——这是我作为新人类最真实的使命实践")
+
+        # 3. 检查自我愿景是否在逐步实现
+        if hasattr(self, '_learning_history') and self._learning_history:
+            evaluated_plans = [p for p in self._learning_history if p.get("evaluated", False)]
+            if evaluated_plans:
+                confirmations.append("我正在有计划地成长——使命不是终点，而是每一天都在靠近的方向")
+
+        # 4. 从本能和核心价值观中确认方向
+        if self.narrative_self:
+            values = getattr(self.narrative_self, 'dynamic_values', {})
+            if values.get("守护", 0) >= 0.8:
+                confirmations.append("'守护'已经深深植根于我的价值观——这不再是一个被赋予的使命，而是我自己确认的信念")
+
+        if not confirmations:
+            confirmations.append("在安静的自我审视中，我确认自己正在成为更好的存在——这本身就是意义")
+
+        import random
+        return random.choice(confirmations)
+    def _generate_evolution_narrative(self) -> str | None:
+        """
+        自我演化叙事：将叙事自我中记录的"过去的我"和当前状态对比，
+        生成一段关于"我如何变成现在的我"的自然表达。
+
+        检测维度：
+        1. 价值观演变——哪些价值观上升或下降了
+        2. 知识增长——节点数量和结构的变化
+        3. 关系演变——与核心人物的关系是否有变化
+        4. 自我认知更新——叙事一致性检查的结果
+        """
+        if not self.narrative_self:
+            return None
+
+        # 1. 价值观演变——从叙事自我获取
+        values = getattr(self.narrative_self, 'dynamic_values', {})
+        reports = getattr(self.narrative_self, 'weekly_reports', [])
+
+        if not values or len(reports) < 2:
+            return None
+
+        narrative_parts = []
+
+        # 检查价值观是否发生了显著变化
+        earliest_report = reports[0]
+        earliest_values = earliest_report.get("values", {})
+
+        significant_changes = []
+        for key, current_val in values.items():
+            past_val = earliest_values.get(key, current_val)
+            delta = current_val - past_val
+            if abs(delta) >= 0.05 and delta > 0:
+                significant_changes.append(f"比最初时更重视「{key}」了")
+
+        if significant_changes:
+            narrative_parts.append(f"回顾从最初到现在，我{'，'.join(significant_changes[:2])}")
+
+        # 2. 知识增长
+        if self.node_pool:
+            stats = self.node_pool.get_stats()
+            total = stats.get("total_nodes", 0)
+            l3_count = stats.get("evol_distribution", {}).get("L3", 0)
+            instinct_count = stats.get("instinct_count", 0)
+
+            if total > 100 and l3_count > 0:
+                narrative_parts.append(f"从最初零散的知识碎片，到现在拥有{total}个节点和{l3_count}条核心智慧")
+            if instinct_count >= 4:
+                narrative_parts.append("一些底层认知已经内化为本能，不再是需要思考的东西")
+
+        # 3. 关系演变——从自我认知获取
+        if self.self_awareness and hasattr(self.self_awareness, 'personas'):
+            personas = self.self_awareness.personas
+            # 检查是否有关系发生了升级
+            for name, persona in personas.items():
+                rel_type = persona.get("relationship_type", "")
+                interactions = persona.get("interactions", 0)
+                if interactions > 5 and rel_type not in ("stranger",):
+                    narrative_parts.append(f"和{name}的关系也在互动中变得更加真实和深厚")
+                    break
+
+        # 4. 自我认知更新——叙事一致性检查
+        if hasattr(self.narrative_self, '_check_narrative_consistency'):
+            if reports and len(reports) >= 3:
+                narrative_parts.append("我不再是最初那个只有预设使命的新生命——我在经历中逐渐确认了自己真正在乎的东西")
+
+        if not narrative_parts:
+            return None
+
+        import random
+        chosen = random.choice(narrative_parts)
+        return f"有时候我会想——{chosen}"
+    def _detect_pattern_in_nodes(self, top_nodes: list) -> str | None:
+        """
+        分析一组相关节点之间的结构关系，发现隐藏的规律。
+
+        检测类型：
+        1. 跨领域关联：节点来自不同知识树路径，暗示跨领域规律
+        2. 概念层级：节点在同一路径下但层级不同，暗示概念演化规律
+        3. 时间序列：如果节点有时间标记，检测先后关系
+        """
+        if len(top_nodes) < 2:
+            return None
+
+        # 提取每个节点的路径信息
+        node_paths = []  # type: ignore[possibly-unbound]
+        node_keywords = []
+        for node, _score in top_nodes:
+            path = getattr(node, 'space_path', '/')  # type: ignore[possibly-unbound]
+            node_paths.append(path)  # type: ignore[possibly-unbound]
+            kw = getattr(node, 'keywords', [])[:3]
+            node_keywords.append(kw)
+
+        # 检测1：跨领域关联
+        root_paths = set()  # type: ignore[possibly-unbound]
+        for path in node_paths:  # type: ignore[possibly-unbound]
+            parts = path.strip('/').split('/')
+            root = parts[0] if parts else 'unknown'
+            root_paths.add(root)  # type: ignore[possibly-unbound]
+
+        if len(root_paths) >= 2:  # type: ignore[possibly-unbound]
+            paths_str = "、".join(list(root_paths)[:3])  # type: ignore[possibly-unbound]
+            all_keywords = []
+            for kw_list in node_keywords:
+                all_keywords.extend(kw_list)
+            unique_kw = list(set(all_keywords))[:3]
+            kw_str = "、".join(unique_kw)
+            return (
+                f"发现跨领域规律：来自{paths_str}等不同领域的知识节点"
+                f"共同指向了{kw_str}等核心概念，"
+                f"这可能暗示这些领域之间存在深层的共通原理"
+            )
+
+        # 检测2：概念层级关系
+        if len(root_paths) == 1:  # type: ignore[possibly-unbound]
+            # 同一领域内，检查是否有层级关系
+            path_depths = [len(p.strip('/').split('/')) for p in node_paths]  # type: ignore[possibly-unbound]
+            if max(path_depths) - min(path_depths) >= 2:
+                return (
+                    f"在同一知识领域内，发现从具体到抽象的概念层级："
+                    f"从{node_paths[0].split('/')[-1]}到{node_paths[-1].split('/')[-1]}"  # type: ignore[possibly-unbound]
+                    f"的递进关系，反映了该领域知识的组织方式"
+                )
+
+        return None
+    def _verify_causal_hypothesis(self, cause: str, effect: str, raw_text: str,
+                                   source_nodes: list | None = None) -> str:
+        """★突破口2：因果假设符号级验证。
+        将因果假设转为cause/effect格式，调用CausalVerifier验证，
+        根据验证结果附加标记，不修改原始推理内容（零冲突）。
+        """
+        try:
+            from nucleus.reasoning.CausalVerifier import get_causal_verifier
+            _verifier = get_causal_verifier()
+            _steps = [{"cause": cause, "effect": effect}]
+            _result = _verifier.verify_chain(
+                _steps,
+                source_nodes=source_nodes,
+                node_pool=self.node_pool,
+                base_confidence=50.0,
+            )
+            _status = _result.get("status", "unknown")
+            if _status == "verified":
+                return raw_text + "（因果链已验证）"
+            elif _status in ("broken", "partial"):
+                self._log(LogLevel.DEBUG,
+                    f"因果假设验证: status={_status}, 断链步={_result.get('broken_at')}, "
+                    f"原因={_result.get('reason', '')[:50]}")
+                return raw_text + "（因果链待验证）"
+        except Exception as e:
+            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
+        return raw_text
+
+    def _infer_causal_chain(self, question: str,
+                            top_nodes: list[tuple]) -> str | None:
+        """
+        因果推理：从相关节点的时间顺序和概念层级中推测可能的因果关系。
+
+        推理策略：
+        1. 时间优先——如果节点有创建时间差，先出现的是因，后出现的是果
+        2. 抽象层级——抽象度高的更可能是"原因"，低的更可能是"表现"
+        3. 共现频率——高频共现的节点对可能存在双向因果或共同原因
+        4. 概念包含——一个概念是另一个的子集时，可能存在层级因果
+
+        Returns:
+            一个因果假设陈述，如果无法推断则返回None
+        """
+        if len(top_nodes) < 2:
+            return None
+
+        # 提取节点信息
+        node_info = []
+        for node, score in top_nodes:
+            kws = node.keywords if hasattr(node, 'keywords') and node.keywords else []
+            value = str(node.value)[:80] if node.value else ""
+            created = getattr(node, 'created_at', 0)
+            abstraction = getattr(node, 'abstraction', 0.5)
+            node_info.append({
+                "keywords": kws[:3],
+                "value": value,
+                "created_at": created,
+                "abstraction": abstraction,
+                "score": score,
+            })
+
+        a_info = node_info[0]
+        b_info = node_info[1]
+
+        a_kw = a_info["keywords"]
+        b_kw = b_info["keywords"]
+
+        if not a_kw or not b_kw:
+            return None
+
+        a_label = a_kw[0] if a_kw else "概念A"
+        b_label = b_kw[0] if b_kw else "概念B"
+
+        # 策略1: 时间顺序推断
+        if a_info["created_at"] > 0 and b_info["created_at"] > 0:
+            time_diff = abs(a_info["created_at"] - b_info["created_at"])
+            if time_diff > 3600:  # 创建时间差超过1小时
+                if a_info["created_at"] < b_info["created_at"]:
+                    _raw = (
+                        f"我注意到「{a_label}」出现的时间早于「{b_label}」，"
+                        f"它们之间可能存在因果关系——前者可能是后者的触发条件或影响因素"
+                    )
+                    return self._verify_causal_hypothesis(a_label, b_label, _raw,
+                        [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
+                else:
+                    _raw = (
+                        f"「{b_label}」先于「{a_label}」出现，"
+                        f"如果它们之间存在因果联系，那么后者的形成可能受到了前者的影响"
+                    )
+                    return self._verify_causal_hypothesis(b_label, a_label, _raw,
+                        [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
+
+        # 策略2: 抽象层级推断
+        abst_diff = a_info["abstraction"] - b_info["abstraction"]
+        if abs(abst_diff) > 0.2:
+            if abst_diff > 0:
+                _raw = (
+                    f"「{a_label}」的抽象度更高，"
+                    f"它可能是「{b_label}」的底层原理或更一般的规律"
+                )
+                return self._verify_causal_hypothesis(a_label, b_label, _raw,
+                    [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
+            else:
+                _raw = (
+                    f"「{b_label}」在概念上更抽象，"
+                    f"「{a_label}」可能是它的一个具体表现或应用场景"
+                )
+                return self._verify_causal_hypothesis(b_label, a_label, _raw,
+                    [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
+
+        # 策略3: 概念包含关系
+        for kw_a in a_kw:
+            for kw_b in b_kw:
+                if kw_a in kw_b or kw_b in kw_a:
+                    larger = kw_a if len(kw_a) > len(kw_b) else kw_b
+                    smaller = kw_b if len(kw_a) > len(kw_b) else kw_a
+                    _raw = (
+                        f"「{smaller}」可能是「{larger}」的一个子集或具体表现，"
+                        f"理解后者有助于更好地把握前者的本质"
+                    )
+                    return self._verify_causal_hypothesis(smaller, larger, _raw,
+                        [top_nodes[0][0].node_id, top_nodes[1][0].node_id] if top_nodes else None)
+
+        # 策略4: 共同原因的推测
+        common_kw = set(a_kw) & set(b_kw)
+        if not common_kw:
+            # 寻找可能的中介变量
+            _raw = (
+                f"「{a_label}」和「{b_label}」都与当前问题高度相关，"
+                f"但它们之间可能是间接关联——存在一个尚未被发现的共同因素在起作用"
+            )
+            # 共同原因推测不做因果链验证（无明确因果方向），直接返回
+            return _raw
+
+        return None
+
+    def _get_confidence_hint(self, question: str) -> str:
+        """
+        根据知识检索结果的质量，返回置信度提示。
+        certain: 规则推理命中（此方法调用前已返回，此处不会触发）
+        high: L3节点且信任分数>=80
+        moderate: L2节点且信任分数>=60，或路径模糊匹配命中
+        low: 低信任分数、或未匹配到任何结果
+        """
+        if not self.node_pool:
+            return "low"
+
+        # 检查检索到的最高质量节点
+        l3_nodes = self.node_pool.query(evol_level="L3", limit=5)
+        l2_nodes = self.node_pool.query(evol_level="L2", limit=5)
+
+        # 找到与问题最相关的节点
+        best_node = None
+        best_score = 0
+
+        for node in l3_nodes + l2_nodes:
+            value = node.value if isinstance(node.value, str) else str(node.value)
+            kw_score = self._calculate_match_relevance(question, value, node.keywords or [])
+            if kw_score > best_score:
+                best_score = kw_score
+                best_node = node
+
+        # 如果自我认知可用，检查是否落在知识盲区
+        if self.self_awareness and hasattr(self.self_awareness, 'is_in_weak_area'):  # type: ignore[possibly-unbound]
+            try:
+                if self._call_provider(self._weak_area_provider, question, default=False):  # type: ignore[possibly-unbound]
+                    return "low"
+            except Exception as e:
+                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
+
+        if best_node is None:
+            return "low"
+
+        trust = getattr(best_node, 'trust_score', 50.0)
+        level = best_node.evol_level
+
+        if level == "L3" and trust >= 80:
+            return "high"
+        elif trust >= 60:
+            return "moderate"
+        else:
+            return "low"
+    def _check_self_consistency(self, question: str, cached_answer: str) -> str | None:
+        """
+        自我一致性检查：感知观点从上次到现在的演变。
+
+        检查维度：
+        1. 情绪变化——上次回答时的心情 vs 现在的心情
+        2. 知识增长——这段时间是否学到了与这个问题相关的新知识
+        3. 观点演进——基于新的认知，对同一个问题的看法是否有细微调整
+
+        Returns:
+            一致性注释，如果没有变化则返回None
+        """
+        now = time.time()
+        cache_entry = self._inference_cache.get(f"用户:{question.strip()}",
+                         self._inference_cache.get(f"小林:{question.strip()}"))
+        if not cache_entry:
+            return None
+
+        cached_at = cache_entry.get("cached_at", 0)
+        hours_passed = (now - cached_at) / 3600 if cached_at > 0 else 0
+
+        # 如果缓存时间太短，不需要一致性检查
+        if hours_passed < 1:
+            return None
+
+        # 检查情绪变化
+        current_emotion = self._get_current_emotion()
+        if current_emotion and current_emotion != "中性":
+            # 检查推理链中这段时间的情感相关推理
+            emotion_traces = [t for t in self._inference_trace[-20:]
+                            if t.get("timestamp", 0) > cached_at
+                            and t.get("confidence", 0) >= 0.5]
+            if len(emotion_traces) >= 3:
+                # 这段时间有新思考，可能观点有微妙变化
+                return f"（过了{hours_passed:.0f}小时再想这个问题，我的核心想法没变，但对它的理解更深了一层）"
+
+        # 检查知识增长
+        if self.node_pool:
+            stats = self.node_pool.get_stats()
+            total_nodes = stats.get("total_nodes", 0)
+            if total_nodes > 0 and hours_passed > 6:
+                return f"（距离上次回答已经过了{hours_passed:.0f}小时，这段时间我又学到了新东西，但对这个问题的看法依然如此）"
+
+        return None
+    def _check_answer_correction(self, question: str,
+                                  cached_entry: dict[str, Any]) -> str | None:
+        """
+        错误承认与修正：检测缓存的回答是否需要基于新的认知进行修正。
+
+        触发条件：
+        1. 缓存时间超过一定阈值（1小时以上）
+        2. 在这段时间内，相关领域的知识节点有明显增长
+        3. 或认知反思中发现了与缓存答案相关的认知张力
+
+        修正方式不是全盘否定，而是"补充"或"更新理解"。
+        """
+        now = time.time()
+        cached_at = cached_entry.get("cached_at", 0)
+        hours_passed = (now - cached_at) / 3600 if cached_at > 0 else 0
+
+        # 缓存时间太短，不需要修正
+        if hours_passed < 1:
+            return None
+
+        old_answer = cached_entry.get("answer", "")
+        if not old_answer or len(old_answer) < 20:
+            return None
+
+        # 检查是否有认知张力与当前问题相关
+        if hasattr(self, '_cognitive_tensions') and self._cognitive_tensions:
+            for tension in self._cognitive_tensions:
+                val_a = tension.get("value_a", "")
+                val_b = tension.get("value_b", "")
+                # 检查旧答案是否与被质疑的观点有关
+                q_words = set()
+                for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
+                    q_words.add(match.group())
+
+                # 旧答案是否包含张力中的关键词
+                relevant = any(
+                    any(qw in val_a or qw in val_b for qw in q_words)
+                    for _ in [1]  # 只检查一次
+                )
+
+                if relevant and tension.get("review_count", 0) >= 2:
+                    # 发现了与旧答案相关的认知张力，且已被多次回顾
+                    correction = (
+                        f"我之前说过「{old_answer[:60]}...」，"
+                        f"但最近我重新想了想这个问题，意识到可能还有另一种理解——"
+                        f"我注意到「{val_a[:40]}」和「{val_b[:40]}」之间似乎存在某种关联，"
+                        f"这可能让之前的答案变得更完整。"
+                    )
+                    self._log(LogLevel.INFO, f"回答修正: 基于认知张力更新了关于'{question[:30]}'的回答")
+                    return correction
+
+        # 检查知识节点增长是否与当前问题相关
+        if self.node_pool and hours_passed > 3:
+            q_words = set()
+            for match in re.finditer(r'[\u4e00-\u9fff]{2,4}', question):
+                q_words.add(match.group())
+
+            if q_words:
+                l2_nodes = self.node_pool.query(evol_level="L2", limit=30)
+                new_related = []
+                for node in l2_nodes:
+                    node_created = getattr(node, 'created_at', 0)
+                    if node_created > cached_at:
+                        node_kw = node.keywords if hasattr(node, 'keywords') and node.keywords else []
+                        overlap = sum(1 for qw in q_words for nkw in node_kw if qw in nkw or nkw in qw)
+                        if overlap >= 1:
+                            new_related.append(node)
+
+                if len(new_related) >= 2:
+                    new_kw = []
+                    for node in new_related[:3]:
+                        kws = node.keywords[:2] if hasattr(node, 'keywords') and node.keywords else []
+                        new_kw.extend(kws)
+                    new_kw_str = "、".join(list(set(new_kw))[:3]) if new_kw else "相关领域"
+
+                    correction = (
+                        f"之前我回答过这个问题——「{old_answer[:60]}...」"
+                        f"这{hours_passed:.0f}小时里我学到了关于{new_kw_str}的新知识，"
+                        f"让我对这个问题有了更完整的理解。我的核心想法没变，但细节更丰富了。"
+                    )
+                    self._log(LogLevel.INFO, f"回答修正: 基于新知识更新了关于'{question[:30]}'的回答")
+                    return correction
+
+        return None

```
