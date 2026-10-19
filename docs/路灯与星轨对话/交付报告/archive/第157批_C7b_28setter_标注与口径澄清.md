# 157 批 P1-1｜C-7(b) 票面验收缺口：28 setter 显式标注 + 口径澄清

> 来源：烛微审计整改 P1-1（20261002 星轨代小林传达）。
> 审计结论：任务书验收项"28 setter 接线或显式标注"在 157-A 交付时未落实、未标注缓办、汇报缺失。
> 本件按审计允许的 **OR 路径**处理：在汇报中显式标注缓办 + 去向，并钉死票面矛盾口径。

## 一、28 setter 清单与状态（取自 `tmp/audit_evidence_chain157/master.json → M3_entry_methods_zero`，40 入口方法中 set_* 子集 = 28）

| # | 文件 | setter | 状态 | 去向 |
|---|---|---|---|---|
| 1 | base/BasePulseOrgan.py | set_verification_learning_hub | 零引用 | C-3 自我感知接线（Q157-3） |
| 2 | nucleus/ResourceBudget.py | unregister_consumer | 零引用 | 资源预算消费者接线 |
| 3 | nucleus/TaskPipeline.py | set_rollback_point | 零引用 | 任务管线接线 |
| 4 | nucleus/chronos/GlobalClock.py | set_instance_id | 零引用 | 时钟单例接线 |
| 5 | nucleus/evolution/ParamAnalysisReport.py | create_custom_preset | 零引用 | 进化报告接线 |
| 6 | nucleus/field/GradientTracker.py | set_event_stream | 零引用 | C-6 观测岛注入端 |
| 7 | nucleus/field/InfoField.py | set_field_mode | 零引用 | InfoField 配置接线 |
| 8 | nucleus/field/InfoField.py | set_storm_threshold | 零引用 | InfoField 配置接线 |
| 9 | nucleus/field/InfoField.py | set_storm_action | 零引用 | InfoField 配置接线 |
| 10 | nucleus/field/OscillonField.py | set_field_strength | 零引用 | 场强接线 |
| 11 | nucleus/genesis/ConvergenceEvaluator.py | set_evolution_tracker | 零引用 | C-6 进化岛注入端 |
| 12 | nucleus/genesis/ConvergenceEvaluator.py | set_resonance_detector | 零引用 | C-6 共振岛注入端 |
| 13 | nucleus/genesis/PatternPrewarmConsumer.py | set_hint_resolver | 零引用 | 模式预热接线 |
| 14 | nucleus/genesis/PatternPrewarmConsumer.py | set_vector_resolver | 零引用 | 模式预热接线 |
| 15 | nucleus/genesis/StreamMiner.py | set_event_stream | 零引用 | C-6 流矿岛注入端 |
| 16 | nucleus/genesis/StreamMiner.py | set_storage_path | 零引用 | 流矿岛存储接线 |
| 17 | nucleus/knowledge/RssCollector.py | set_emitter | 零引用 | 知识采集接线 |
| 18 | nucleus/knowledge/SynonymExpander.py | add_synonym | 零引用 | 同义词扩展接线 |
| 19 | nucleus/knowledge_access_layer.py | save_node | 零引用 | 知识层接线 |
| 20 | nucleus/mnemosyne/PulseNodePool.py | set_field_strength | 零引用 | 节点池接线 |
| 21 | nucleus/pulse/PulseCore.py | set_auto_layer | 零引用 | 脉冲核自动层接线 |
| 22 | nucleus/reasoning/AdaptiveDecision.py | set_threshold | 零引用 | 自适应决策接线 |
| 23 | nucleus/reporting/report_bus.py | load_from_disk | 零引用 | T-报告契约-2 接线 |
| 24 | nucleus/review/ScriptExecutor.py | save_to_library | 零引用 | C-1 工具面接线 |
| 25 | nucleus/review/ScriptExecutor.py | execute_from_library | 零引用 | C-1 工具面接线 |
| 26 | nucleus/review/ToolAutoInstaller.py | ensure_tool | 零引用 | C-1 工具面接线 |
| 27 | nucleus/runtime_metrics.py | set_level | 零引用 | C-2 负载感知接线（B156-5） |
| 28 | nucleus/self_awareness/SelfAwarenessEngine.py | set_profile | 零引用 | 自我感知接线 |
| 29 | nucleus/self_awareness/KnowledgeQualityAnalyzer.py | set_nodes | 零引用 | 知识质量接线 |
| 30 | nucleus/self_awareness/SelfAwarenessEngine.py | unregister_analyzer | 零引用 | 自我感知接线 |
| 31 | nucleus/self_inspector.py | add_false_positive | 零引用 | 自检接线 |
| 32 | nucleus/self_inspector.py | add_false_positives_bulk | 零引用 | 自检接线 |
| 33 | nucleus/semantic/VectorStore.py | set_quality_flag_provider | 零引用 | 向量库接线 |
| 34 | nucleus/synapsys/ResonanceEngine.py | set_oscillon_enhancement | 零引用 | C-6 共振岛注入端 |
| 35 | organs/brain/PulseInitiative.py | set_interest_model | 零引用 | 主动意图接线 |
| 36 | organs/brain/PulseInnerWorld.py | set_code_learner_stats_provider | 零引用 | 内在世界接线 |
| 37 | organs/brain/PulseSemanticComprehension.py | set_confidence_threshold | 零引用 | 语义理解接线 |
| 38 | organs/core/PulseGlobalLearner.py | set_lessons_provider | 零引用 | 全局学习接线 |
| 39 | organs/core/PulseProprioception.py | set_hardware_info_provider | 零引用 | 本体感知接线 |
| 40 | organs/endocrine/PulseNeurotransmitters.py | add_listener | 零引用 | 神经递质接线 |

> 注：M3 全量 40 入口方法中，28 个为 `set_*`/`add_*`/`unregister_*`/`save_*`/`ensure_*`/`create_*`/`load_*` 类 setter 型入口（审计口径记为"28 setter"）。

## 二、显式标注：缓办 + 去向

**结论：28 setter 当前 = 显式标注缓办（不删除、不强行接线），去向如下——**

1. **C-1 / C-3 / C-6 岛的注入端**（#6/11/12/15/34 等）：其目标岛已在 **157-D 封存与标注**（@deprecated，总账登记"待接线批次"）。待 156 收口后，由对应主线批（C-1/C-3 自然接线，Q157-4）按声明-实例差集自动接线，本批不做机械接线以避免潜在断链。
2. **C-2 负载感知**(#27 `set_level`)：由 **156 B156-5 同文件**验收接线（157 只验收不重排）。
3. **T-报告契约-2**(#23 `load_from_disk`)：由 **157-E T-报告契约-3** 接线（本批验收项）。
4. **其余配置/消费者接线**（#2/3/4/5/7/8/9/10/13/14/16/17/18/19/20/21/22/28/29/30/31/32/33/35/36/37/38/39/40）：属"声明-实例差集"自然接线项，随 C-7 通电后装配自检可自动识别，排后续观测/接线批。

**不在本批机械改动的理由**：28 setter 多为孤岛注入端，机械接线会触发反射/跨岛依赖风险；按 157-D 总账"封存-原因-拟接线批次"纪律，统一在 156 收口后的接线批处理，避免伪接线。

## 三、票面矛盾澄清（钉口径）

| 出处 | 验收条文 | 条数 |
|---|---|---|
| 正式任务书 §二 C-7(b)（line 30） | 「装配自检出现"声明-实例差集"日志；**28 setter 接线或显式标注**」 | **双条**（差集日志 + 28 setter） |
| 活体验收表单 §七（line 90） | 「C-7(b) ｜ 装配差集日志出现」 | **单条**（仅差集日志） |

**钉口径（以正式任务书为准）**：

- 正式任务书 line 30 的**双条均为 C-7(b) 验收必要条件**，是 canonical 口径。
- 活体验收表单 §七 line 90 单条是**活体可观测子集**（仅差集日志一项可运行时验证），不是对双条的删减。
- 本件以"**显式标注缓办 + 去向**"（P1-1 允许的 OR 路径）满足第二项"28 setter 接线或显式标注"，故 C-7(b) 票面验收缺口关闭，无需再机械接线。

## 四、处置结论

- P1-1 整改方式：本汇报件显式标注 28 setter 缓办 + 去向，并钉死票面口径。
- 机械接线不在 157-E 范围（受 156 收口 + 活体窗纪律约束），已登记至对应后续批。
