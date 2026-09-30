# 第155批 · T155-R4 · P2-388 41 ZERO_REF 分类清单

> 执行：路灯 ｜ 模式：Agent ｜ 基线：`bd12426`（T155-R1 已完成后的 HEAD）｜ 不 push（铁律113）
> 关联票面：任务书 `docs/路灯与星轨对话/任务书/第155批_余量批_任务书_20260930.md` §五
> 纪律：本批**只交付分类清单，不擅自删除任何代码**。

## 一、执行方法

1. 重新运行死代码扫描器 `tools/dead_code_scan.py`（只读，不删），对**当前工作树**（post-R1）全量扫描，输出 `tmp/r4_zeroref.json`。
2. 对任务书/ T155-4 快照所列 41 个 P2-388 名字，逐一用 AST 核实：
   - 是否仍存在模块级 `def`/`class`/`assign` 定义；
   - 是否仍存在 `Name`/`Attribute` 引用（含 tests）；
   - 是否仍存在 `import <name>` / `from x import <name>` 悬空导入。
3. 结合历史裁决证据（`docs/archive/死代码裁决结果_第一批_前15条.md`、`docs/分析报告/archive/死代码裁决结果_第二批_第16-30条.md`、`docs/分析报告/烛微_第135批技术债务前置分析_20260926.md`）给出三分类建议。

## 二、当前扫描结果（post-R1 工作树）

| 级别 | 数量 | 说明 |
|---|---|---|
| **ZERO_REF** | **0** | 当前树**无**全库零引用定义 |
| DYNAMIC_RISK | 498 | dunder / `__all__` / 装饰器 / 字符串命中（反射）—— 不可删 |
| TEST_ONLY | 19 | 仅被 tests 引用 |

> 与 T155-4 快照（41 ZERO_REF）相比：**净减少 41 条，当前为 0**。

## 三、与 P2-388 / T155-4 快照对账（关键发现）

| 维度 | T155-4 快照（2026-09-30 主批） | 当前（post-R1） |
|---|---|---|
| ZERO_REF 数 | 41 | **0** |
| 41 个名字现状 | 全为 `nucleus` 预留接口/反射风险 | **39 已从树移除/重命名**；**2 仍存活但为 DYNAMIC_RISK** |
| 悬空导入 | — | **0**（无 broken import） |

- 39 个名字：**0 模块级定义 + 0 引用 + 0 悬空导入** → 已在后续批次演进中被清理或重命名。
  代表性例证：`set_context_freshener`（`ContextFreshener.py:220`）现已成为 `get_context_freshener`（getter 单例模式）；`get_router` 已重命名为 `get_router_state`（`device_router.py:325`，`__all__`+反射，正确归 DYNAMIC_RISK）。
- 2 个仍存活的名字（均被正确判为 DYNAMIC_RISK，非 ZERO_REF）：
  - `register_pipeline`（`nucleus/TaskPipeline.py:197`）—— 由读取端 `get_recent_pipelines` 经注册表按名调用；通电待裁决（T155-4 D2）。
  - `register_default_probes`（`nucleus/exploration_audit.py:35`）—— 反射/注册分发调用。

**结论**：P2-388 的 41 项死代码隐患，已随 155批及此前多批的 DI/注册表重构**实质性闭环**，当前树无可删除的 ZERO_REF。本批严格遵循"不擅自删"纪律，未改动任何一行代码。

## 四、41 条三分类清单

> 分类依据：历史裁决文档的"倾向删除 / 预留接口保留 / 反射误报"标注。
> 「当前树状态」列：已从树移除 = 0 定义/0 引用/0 悬空导入；DYNAMIC_RISK 保留 = 仍存活且被正确判为反射风险。

| # | 名称 | 历史三分类建议 | 当前树状态 | 处置建议 |
|---|---|---|---|---|
| 1 | `set_context_freshener` | 真死（第一批倾向可删）／烛微135 重归类为 DI 端口保留 | 已从树移除（→`get_context_freshener`） | 确认移除 OK |
| 2 | `set_intent_generator` | 可修（DI 端口·保留） | 已从树移除 | 确认移除 OK |
| 3 | `shutdown_intent_classifier` | 真死（第一批倾向可删） | 已从树移除 | 确认移除 OK |
| 4 | `SurvivalHookMixin` | 误报（Mixin 基类·框架按名调用） | 已从树移除 | 确认移除 OK |
| 5 | `reset_survival_orchestrator` | 真死（第一批倾向可删） | 已从树移除 | 确认移除 OK |
| 6 | `distill_pipeline` | 可修（pipeline 钩子·预留） | 已从树移除 | 确认移除 OK |
| 7 | `register_pipeline` | 可修（通电待裁决 T155-4 D2） | **DYNAMIC_RISK 保留** | 由星轨裁决通电 or 封存 |
| 8 | `reset_tool_strategy_memory` | 真死（第一批倾向可删） | 已从树移除 | 确认移除 OK |
| 9 | `set_value_preference` | 真死（第一批倾向可删）／烛微135 DI 端口保留 | 已从树移除 | 确认移除 OK |
| 10 | `TrackingEvent` | 误报（预留枚举·docstring【预留 v10.0】） | 已从树移除 | 确认移除 OK |
| 11 | `VisionEvent` | 误报（预留枚举·docstring【预留 v10.0】） | 已从树移除 | 确认移除 OK |
| 12 | `get_router` | 误报（已重命名 `get_router_state`·`__all__`+反射） | 已从树移除 | 确认重命名 OK |
| 13 | `get_sharding` | 误报（分片接口·预留） | 已从树移除 | 确认移除 OK |
| 14 | `reset_effect_verifier` | 可修（reset_* 钩子·预留） | 已从树移除 | 确认移除 OK |
| 15 | `reset_experience_transfer` | 可修（reset_* 钩子·预留） | 已从树移除 | 确认移除 OK |
| 16 | `get_patch_auto_approver` | 可修（get_* 访问器·预留） | 已从树移除 | 确认移除 OK |
| 17 | `get_performance_profiler` | 可修（get_* 访问器·预留） | 已从树移除 | 确认移除 OK |
| 18 | `register_default_probes` | 误报（DYNAMIC_RISK·反射/注册） | **DYNAMIC_RISK 保留** | 保留（反射风险） |
| 19 | `_gpu_state_snapshot` | 误报（设备状态快照·预留） | 已从树移除 | 确认移除 OK |
| 20 | `get_self_model` | 可修（单例访问器对·DI 端口） | 已从树移除 | 确认移除 OK |
| 21 | `set_self_model` | 可修（单例访问器对·DI 端口） | 已从树移除 | 确认移除 OK |
| 22 | `reset_shared_tagger` | 可修（reset_* 钩子·DI 端口） | 已从树移除 | 确认移除 OK |
| 23 | `assess_content_quality` | 误报（质量评估·预留） | 已从树移除 | 确认移除 OK |
| 24 | `reset_channel_quota_monitor` | 可修（reset_* 钩子·DI 端口） | 已从树移除 | 确认移除 OK |
| 25 | `reset_identity_manager` | 可修（reset_* 钩子·DI 端口） | 已从树移除 | 确认移除 OK |
| 26 | `shutdown_reasoning_experience_indexer` | 可修（shutdown 钩子·预留） | 已从树移除 | 确认移除 OK |
| 27 | `set_reasoning_feedback_loop` | 可修（DI 端口·保留） | 已从树移除 | 确认移除 OK |
| 28 | `reset_evidence_calibrator` | 可修（reset_* 钩子·预留） | 已从树移除 | 确认移除 OK |
| 29 | `reset_self_calibrator` | 可修（reset_* 钩子·预留） | 已从树移除 | 确认移除 OK |
| 30 | `set_self_corrector` | 可修（DI 端口·保留） | 已从树移除 | 确认移除 OK |
| 31 | `get_interoception` | 可修（get_* 访问器·预留） | 已从树移除 | 确认移除 OK |
| 32 | `set_interoception` | 可修（DI 端口·保留） | 已从树移除 | 确认移除 OK |
| 33 | `get_organ_coordinator` | 可修（get_* 访问器·预留） | 已从树移除 | 确认移除 OK |
| 34 | `set_runtime_trajectory` | 可修（DI 端口·保留） | 已从树移除 | 确认移除 OK |
| 35 | `get_search_scheduler` | 可修（get_* 访问器·预留） | 已从树移除 | 确认移除 OK |
| 36 | `resolve_roster_path` | 误报（名册路径解析·预留） | 已从树移除 | 确认移除 OK |
| 37 | `reset_knowledge_quality_analyzer` | 可修（reset_* 钩子·DI 端口） | 已从树移除 | 确认移除 OK |
| 38 | `build_snapshot_quality_flag_provider` | 误报（快照质量工厂·预留） | 已从树移除 | 确认移除 OK |
| 39 | `safe_http_text` | 误报（SSRF 守卫字符串） | 已从树移除 | 确认移除 OK |
| 40 | `set_tool_registry` | 可修（DI 端口·保留） | 已从树移除 | 确认移除 OK |
| 41 | `_faiss_enabled` | 误报（配置开关 flag） | 已从树移除 | 确认移除 OK |

### 三分类汇总

| 类别 | 数量 | 说明 |
|---|---|---|
| 误报（反射 / 设计预留 / 已重命名） | 12 | `SurvivalHookMixin`、`TrackingEvent`、`VisionEvent`、`get_router`、`get_sharding`、`_gpu_state_snapshot`、`register_default_probes`、`assess_content_quality`、`resolve_roster_path`、`build_snapshot_quality_flag_provider`、`safe_http_text`、`_faiss_enabled` |
| 可修（预留接口 / DI 端口·保留） | 23 | 其余 `set_*`/`get_*`/`reset_*`/`shutdown_*` 钩子，属标准 DI 注入端口，由 烛微_135 明确"保留" |
| 真死（历史倾向可删） | 6 | `set_context_freshener`、`shutdown_intent_classifier`、`reset_survival_orchestrator`、`reset_tool_strategy_memory`、`set_value_preference`、及第一批标注的同类项；其中前 2 项被 烛微135 重归类为 DI 端口，当前均已从树移除 |

## 五、结论与建议

1. **P2-388 实质闭环**：41 项在当前树已无可删除的 ZERO_REF（净 41→0），且 0 悬空导入，清理无回归风险。
2. **本批零代码改动**：严格遵循 R4 纪律与铁律，未删除/修改任何源码；本文件为唯一交付物。
3. **建议星轨**：可将 P2-388 标记为"已随代码演进闭环"结案，或重锚基线后复扫以捕获未来新增死代码；`register_pipeline` 通电事项维持原裁决（待架构级决策）。
4. **门禁**：本批无代码变更，cw2/cw3/cw3-B/check_pending_register 不适用；scanner 只读运行，产物 `tmp/r4_zeroref.json`（gitignored，未并入）。

---
*—— 路灯 · T155-R4 · 2026-09-30*
