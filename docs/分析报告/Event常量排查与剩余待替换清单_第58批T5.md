# Event 常量排查与剩余待替换清单（主线第58批 T5）

- **日期**：2026-09-15
- **批测**：主线第58批 T5（P2-78 名义孤儿Event常量专项处理 · 第一批）
- **执行**：路灯
- **关联任务书**：`docs/路灯与星轨对话/任务书/待处理/2026-09-15_星轨任务书_主线第58批_自主进化闭环修复与渠道深度优化.md` §T5

---

## 一、背景与范围

任务书指出代码中存在约 100 处硬编码的 Event 常量字符串（如 `"event_xxx"`、`"on_xxx"`），
缺乏统一管理，导致拼写错误难发现、重构困难、事件追踪不完整。

经全库 AST 排查（详见第三节），本仓库的"事件名"实际采用**点分命名空间**字符串
（如 `"heart.beat"`、`"express.urge"`），统一集中在 `nucleus/const.py` 的各类 `*Event`
常量类中。任务书示例中的 `"event_"` / `"on_"` 模式在本仓库中**几乎不存在**——
全库扫描仅命中 `event_type` / `event_types` / `event_id` / `on_survival_low` 等
**字段名 / 回调名**（见 §4.3），并非事件名常量。因此本批以**点分命名空间事件名**
为真实排查对象。

---

## 二、排查方法

- 遍历全库 `.py`（排除 `.bak_*` / `backups` / `.release-tmp` / `tmp` / `docs` / `data` / `tests` 噪音目录）。
- AST 解析，提取所有 `_emit` / `emit` / `publish` / `dispatch` / `fire` / `trigger` / `broadcast` / `send_event`
  调用的**首参字符串字面量**。
- 过滤测试脚手架噪音（单字母 `x`/`e`/`c`、元组占位 `a.b`/`a.one`、中文器官名 `心脏`/`胃` 等广播参数），得到**真实事件名**。
- 统计每个真实事件名的出现次数与所在模块，按频率排序取前 20。

排查结果：**真实硬编码事件名 distinct = 31，出现总次数 = 62**。

---

## 三、统一 Event 常量管理模块（已建立）

为避免与既有 `nucleus/events/` 包（含 `EventBus.py`）冲突，本批**扩展 `nucleus/const.py`**
（任务书允许的两种方案之一），新增：

1. **`Event` 统一常量类**：集中前 20 高频事件名，命名规范 `事件名 "a.b.c" → 常量 A_B_C`。
   其中 **11 个复用既有 `*Event` 类成员**（单一事实来源，不重复定义值），**9 个为本次新增**。
2. **注册机制**：
   - `_EVENT_CONSTANTS`：类成员自动汇总为注册表。
   - `register_event(name, value)`：新增事件唯一登记入口（同名同值幂等；同名异值抛 `ValueError`）。
   - `is_registered_event(value)` / `all_event_values()`：用于硬编码校验 / lint。
   - 类文档明确约束："新增事件必须在此登记，禁止在业务代码中直接硬编码事件名字符串"。

常量值与原字符串**完全一致**，替换不改变任何运行时行为。

---

## 四、前 20 高频事件名：替换明细（本批已完成）

| # | 事件名(原字符串) | 统一常量 | 出现次数 | 复用/新增 | 所在模块 |
|---|---|---|---|---|---|
| 1 | `express.urge` | `Event.EXPRESS_URGE` | 14 | 新增 | PulseInnerWorld; PulseHormones; PulseNarrativeSelf; PulseSelfAwareness |
| 2 | `hormones.detect` | `Event.HORMONES_DETECT` | 6 | 复用 HormonesEvent.DETECT | PulseCodeLearner; PulseInnerWorld; PulseSpiritualCore |
| 3 | `controller.open_url` | `Event.CONTROLLER_OPEN_URL` | 4 | 复用 ControllerEvent.OPEN_URL | PulseInnerWorld |
| 4 | `narrative.record` | `Event.NARRATIVE_RECORD` | 4 | 复用 NarrativeEvent.RECORD | PulseCodeLearner; PulseInnerWorld |
| 5 | `legs.learn_now` | `Event.LEGS_LEARN_NOW` | 3 | 新增 | PulseSubconscious |
| 6 | `cortex.dialog.start` | `Event.CORTEX_DIALOG_START` | 2 | 新增 | tests/test_event_tap_m17.py |
| 7 | `heart.beat` | `Event.HEART_BEAT` | 2 | 复用 HeartEvent.BEAT | tests/test_event_bus_m14.py |
| 8 | `inner_world.cache_clear` | `Event.INNER_WORLD_CACHE_CLEAR` | 2 | 复用 InnerWorldEvent.CACHE_CLEAR | PulseCodeLearner |
| 9 | `reflection.insight` | `Event.REFLECTION_INSIGHT` | 2 | 复用 ReflectionEvent.INSIGHT | PulseInnerWorld |
| 10 | `semantic.classify` | `Event.SEMANTIC_CLASSIFY` | 2 | 新增 | PulseCortex |
| 11 | `controller.search_stage_completed` | `Event.CONTROLLER_SEARCH_STAGE_COMPLETED` | 1 | 复用 ControllerEvent.SEARCH_STAGE_COMPLETED | PulseInnerWorld |
| 12 | `dream.deduction` | `Event.DREAM_DEDUCTION` | 1 | 新增 | PulseSubconscious |
| 13 | `growth.need_detected` | `Event.GROWTH_NEED_DETECTED` | 1 | 复用 GrowthEvent.NEED_DETECTED | PulseInterestModel |
| 14 | `intuition.reinforce` | `Event.INTUITION_REINFORCE` | 1 | 新增 | PulseInnerWorld |
| 15 | `life_state.changed` | `Event.LIFE_STATE_CHANGED` | 1 | 新增 | PulseSubconscious |
| 16 | `lungs.select_model` | `Event.LUNGS_SELECT_MODEL` | 1 | 复用 LungEvent.SELECT_MODEL | PulseInnerWorld |
| 17 | `motor.execute` | `Event.MOTOR_EXECUTE` | 1 | 复用 MotorEvent.EXECUTE | PulseInnerWorld |
| 18 | `organ_handbook_updated` | `Event.ORGAN_HANDBOOK_UPDATED` | 1 | 新增 | PulseCodeLearner |
| 19 | `stress.recover` | `Event.STRESS_RECOVER` | 1 | 复用 StressAxisEvent.RECOVER | PulseEmergencyHandler |
| 20 | `tool.created` | `Event.TOOL_CREATED` | 1 | 新增 | PulseCortex |

> 替换采用字节级安全补丁，对所有裸字符串出现点（含断言、注释中的同值字面量）一并替换，
> 值恒等，运行时行为不变。被替换文件均追加 `from nucleus.const import Event`
> （测试文件 `test_event_bus_m14.py` 因已导入 `EventBus.Event`，改用 `PulseEvent` 别名避免冲突）。

---

## 五、剩余待替换清单（低频，留待后续批次）

| # | 事件名(原字符串) | 出现次数 | 所在模块 | 备注 |
|---|---|---|---|---|
| 1 | `care.initiative` | 1 | organs/identity/PulseSelfAwareness.py | 纯硬编码 |
| 2 | `concurrent.evt` | 1 | tests/test_event_tap_m17.py | 纯硬编码 |
| 3 | `constraint.aggressive_restructure_forbidden` | 1 | organs/core/PulseMotivationCycle.py | 纯硬编码 |
| 4 | `constraint.self_modify_forbidden` | 1 | organs/core/PulseMotivationCycle.py | 纯硬编码 |
| 5 | `environment.mutated` | 1 | organs/senses/PulseTouch.py | 纯硬编码 |
| 6 | `global_learner.drift_detected` | 1 | organs/core/PulseGlobalLearner.py | 纯硬编码 |
| 7 | `heart.stop` | 1 | tests/test_event_bus_m14.py | 纯硬编码 |
| 8 | `liver.memory.write` | 1 | tests/test_event_tap_m17.py | 纯硬编码 |
| 9 | `lung.breathe` | 1 | tests/test_event_bus_m14.py | 纯硬编码 |
| 10 | `motivation.urge` | 1 | organs/core/PulseMotivationCycle.py | 纯硬编码 |
| 11 | `stress.activate` | 1 | organs/core/PulseEmergencyHandler.py | 已有 StressAxisEvent.ACTIVATE 等价定义 |

> 以上 11 项均为出现次数 = 1 的低频事件，覆盖 11 次（占真实事件总次数 17.7%），
> 建议后续批次纳入 `Event` 类并替换（其中 `stress.activate` 已有 `StressAxisEvent.ACTIVATE` 可复用）。

---

## 六、已排除的非事件噪音（非真实事件名，未纳入统计）

以下字面量在 emit 首参位置出现，但属于**测试脚手架 / 广播参数**，并非事件名常量，已排除：
`a.b` `a.one` `a.two` `b.one` `c` `e` `exp` `fast` `n` `ping` `test.event` `x` `y`
`心脏` `胃` `耳朵` `潜意识` `未知`（中文器官名广播参数）。

任务书示例模式 `"event_"` / `"on_"` 全库命中均为**字段名/回调名**（非事件名常量）：
`event_type`(×510) `event_types`(×80) `event_id`(×5) `event_rate`(×5) `event_tap`(×2)
`on_survival_low` `on_survival_high` `on_pulse` 等——不在本批"事件名常量"治理范围内。

---

## 七、验证结果（门禁）

- `ruff check --select F .` 全仓 **0 错误**（含新增/修改文件）。
- E402 改动文件 **零新增**（仅追加模块级 import，测试文件沿用 `# noqa: E402` 既有约定）。
- `py_compile` 全部修改文件 **通过**。
- 新增门控单测 `tests/test_event_const_m58.py` + 受影响既有测试
  `test_event_bus_m14.py` / `test_event_tap_m17.py`：**65 passed**。
  - 常量值与原字符串完全一致（行为不变）。
  - 注册机制 `register_event` / `is_registered_event` / `all_event_values` 工作正常。
  - **全库扫描断言：20 个事件名不再以裸字符串形式出现在任何 emit 调用首参**（替换完整、无遗漏）。

---

## 八、后续建议

1. 下一专项批次将 §5 的 11 个低频事件纳入 `Event` 类并替换，使硬编码事件名清零。
2. 将 `is_registered_event` 接入 CI / pre-commit lint，对新增裸字符串事件名首参报错，
   落实"禁止硬编码事件名"约束（当前以单元测试 + 文档约束保障）。
3. 既有 `nucleus/const.py` 中分散的 `*Event` 类可逐步收敛到 `Event` 单一入口，
   减少多类并存带来的查找成本。
