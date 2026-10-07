# PulseInnerWorld 架构与维护指南（陌生人可接管视角）

> 文档归属：第 164 批 B1（T-God文件拆分-1）交付物之一。
> 适用范围：`organs/brain/PulseInnerWorld.py` 及其拆分出的 mixin / 委托类。
> 阅读对象：首次接手本仓库、需要"在不破坏行为的前提下改东西"的维护者。

---

## 1. 这是什么

`PulseInnerWorld`（内在世界）是曈曈 PulseNet v10 的**认知核心器官**——负责知识检索、语义扩展、多节点融合、本地推理回答，是"大脑皮层的主要执行器官"。它上接大脑决策调度（`PulseCortex`），下连知识快照与向量库底层存储。

**关键事实**：它是一个**巨型类（God Class）**，历史上通过多次批次（137/139/141/164-B1）把内聚方法簇逐步抽离成 mixin 与委托类，但主文件仍然很大（截至 164 批 B1 约 1.7 万行）。本指南告诉你**当前结构是什么、改东西时该去哪、绝对不能做什么**。

---

## 2. 当前拆分结构

`PulseInnerWorld` 通过**多重继承（MRO）**组合多个 mixin，并**委托**给若干独立类。方法调用一律走 `self.xxx`，由 Python MRO 解析到正确实现——所以"方法在哪个文件"对调用方透明。

```
PulseInnerWorld(
    PulseInnerWorldSupportMixin,      # 支撑/工具/配置/生命周期
    PulseInnerWorldKnowledgeMixin,    # 知识检索/融合/一致性校验
    PulseInnerWorldCreativeMixin,     # 创意生成/深度思考/多步推理
    PulseInnerWorldEmotionAugMixin,   # 第164批B1新增：情感增强/回答润色（纯文本变换）
    BasePulseOrgan,                   # 框架基类（必须放在最后）
)
```

### 2.1 Mixin 文件清单（方法簇）

| 文件 | 职责 | 典型方法 |
|------|------|----------|
| `organs/brain/pulse_inner_world_support.py` | 支撑/工具/配置/生命周期/身份记忆 | `set_*`, `on_pulse`, `_init_knowledge_retriever`, `_find_and_weave_best_match` |
| `organs/brain/pulse_inner_world_knowledge.py` | 知识检索/融合/一致性校验/矛盾追踪 | `_ir_*`(推理入口), `_vk_*`(知识校验), `_validate_knowledge_consistency` |
| `organs/brain/pulse_inner_world_creative.py` | 创意生成/深度思考/多步推理/分支 | `_deep_think`, `_multi_branch_deep_think`, `_multi_step_execute` |
| `organs/brain/pulse_inner_world_emotion_aug.py` | **情感增强/回答润色**（第164批B1迁入） | `_enhance_answer`, `_ea_*`(表达增强流水线), `_generate_discipline_prefix`, `_get_spiritual_touch`, `_generate_breathing_response`, `_generate_silence_acknowledgment`, `_verbalize_thinking_process`, `_add_uncertainty_note` |

### 2.2 委托类（被 PulseInnerWorld 持有/调用，非继承）

这些是**独立类**，被主类在 `__init__`/初始化方法中实例化后委托调用（不是 mixin）：

| 类 | 文件 | 职责 |
|----|------|------|
| `PulseCognitiveReflector` | `organs/brain/PulseCognitiveReflector.py` | 认知反思/洞察生成（`_cr_*` 系列内部调用） |
| `PulseKnowledgeRetriever` | `organs/brain/PulseKnowledgeRetriever.py` | 知识检索执行 |
| `PulseMultiStepReasoner` | `organs/brain/PulseMultiStepReasoner.py` | 多步推理执行 |
| `PulseReasoningFormatter` | `organs/brain/PulseReasoningFormatter.py` | 推理结果格式化 |
| `PulseExpression` | `organs/brain/PulseExpression.py` | 表达增强独立模块（`_enhance_answer` 内延迟实例化） |

### 2.3 主文件当前保留的内容

`PulseInnerWorld.py` 主文件仍包含：类声明 + `InferenceContext` 内部类（`__slots__` 承载推理共享状态）+ 尚未抽离的大量方法（推理入口 `_on_inference_request`/`_ir_*`、认知反思 `_cognitive_reflection`/`_cr_*`、诊断 `_comprehensive_self_diagnosis`、矛盾校验 `_vk_*`、对话记忆 `_weave_memory_narrative` 等）。

---

## 3. 陌生人维护指南（必读）

### 3.1 要加一个"回答润色/表达增强"类方法 → 去 `pulse_inner_world_emotion_aug.py`

例如想新增一种情绪化表达风格，把方法写成 `def _ea_xxx(self, answer, ...)` 放进 `PulseInnerWorldEmotionAugMixin`，并在 `_enhance_answer` 的下游流水线里 `answer = self._ea_xxx(answer, ...)` 串接即可。**不要**把这类方法写回主文件。

### 3.2 要加"知识检索/融合"方法 → 去 `pulse_inner_world_knowledge.py`

### 3.3 要加"创意/深度思考/多步"方法 → 去 `pulse_inner_world_creative.py`

### 3.4 通用铁律（违反会被 CI 门禁拦截）

1. **继承顺序**：`BasePulseOrgan` 必须永远是最后一个基类；新增 mixin 插在它之前。
2. **MRO 透明性**：mixin 内只通过 `self.*` 调用，不要引入模块级循环依赖（mixin 文件顶部只允许 `import time`/`typing`/`nucleus.const`/`utils.time_utils` 这类叶子依赖）。
3. **禁止新增静默 `try/except`**：所有异常捕获**必须**带 `self._log(LogLevel.WARNING, "异常已忽略（需关注）: ...")` 这类日志（即"logged except"），否则 cw2 静默except 门禁会拦截提交。
4. **行尾符（CRLF）**：所有 `.py` 必须用 CRLF。改动后务必 `git diff --cached` 确认 diff 只含真实改动（避免 loneLF 暴涨）。
5. **不要碰 `config.py`**：新开关一律用模块级常量（如 `OFFLINE_SURVIVAL_ENABLED`），不要去 `config.py` 加项。
6. **节点零注册**：mixin 内的纯文本/格式化方法**不得**注册知识节点，否则 `collect` 红基线门禁会漂移。

### 3.5 如何验证你没改坏行为

```bash
# 1) 编译
python -m py_compile organs/brain/PulseInnerWorld.py organs/brain/pulse_inner_world_emotion_aug.py
# 2) 导入冒烟（确认 MRO 可达）
python -c "from organs.brain.PulseInnerWorld import PulseInnerWorld as P; \
           from organs.brain.pulse_inner_world_emotion_aug import PulseInnerWorldEmotionAugMixin as M; \
           assert issubclass(P, M) and hasattr(P, '_enhance_answer')"
# 3) 提交时 pre-commit 自动跑 cw2 + 5 静态门 + collect + S4 + cw3-B + 登记册
```

---

## 4. 历史拆分脉络

| 批次 | 动作 |
|------|------|
| 137 | IW 首刀拆分（建立 mixin 雏形） |
| 139 | IW 第二刀拆分 |
| 141 | IW 第三刀拆分 + 改名消歧 + 清理备份 |
| 164-B1 | 从主文件再抽离"情感增强/回答润色"簇（13603–14237 行，28 个方法）到 `pulse_inner_world_emotion_aug.py`，行为零变更 |

> 设计原则：**继续拆分 ≠ 重写**。每次只把内聚、低耦合的方法簇物理迁到独立文件，方法签名、`self.*` 调用方式、MRO 接入点完全不变，从而把"巨型类"逐步降解为"可独立 import、职责清晰"的模块集合。
