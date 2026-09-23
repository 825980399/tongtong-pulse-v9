# PHASE18 自我认知引擎 · 阶段二「结果接入」设计 v1.0

**版本**：v1.0
**日期**：2026-09-13
**设计**：路灯、星轨
**状态**：📋 **设计稿**（本批只做设计与接口定义，**不实现任何生产代码**）
**前置**：PHASE18 阶段一（第18~39批）已收官 —— 引擎可每日自动生成画像与报告
**关联债务**：`P2-216`（自我认知结果接入决策/对话）

---

## 一、背景与目标

### 1.1 现状（阶段一收官后）

| 能力 | 状态 | 关键指标 |
|---|---|---|
| 每日调度 | ✅ | `DailyScheduler` 每日 03:00 自动执行 |
| 画像数据模型 | ✅ | `overall_score` / `health_level` / `top_issues` |
| 产出-消费配对 | ✅ | 覆盖率 100.5% / 误报率 0% / 噪声过滤 99.4% |
| 虚假闭环检测 | ✅ | 候选 19 个（含私有 `_load`/`_save` 命名） |
| evolution_health | ✅ | 公共统计接口 `get_evolution_stats()` |
| **结果接入决策/对话** | ❌ | **本设计的目标** |

**核心问题**：引擎的分析结果**只落盘**到 `data/self_awareness/`，
框架的**对话 / 决策 / 进化**三条主链路**完全不知道**自己的"健康状况"。
引擎是"能跑、有洞察"，但**还不能影响行为**。

### 1.2 目标

让曈曈的自我认知**真正影响行为**：

1. 对话中能**有节制地**提及自身状态（不消极、不刷存在感）
2. 决策时考虑自身健康度（低健康度 → 主动降级）
3. 进化优先级由 `top_issues` 驱动（数据驱动"先修什么"）

### 1.3 非目标（本期明确不做）

- ❌ **不做实时分析**：分析结果每日更新一次（有延迟），不得作为实时决策依据
- ❌ **不做自动修复闭环**："自我诊断 → 自动改代码"属阶段三
- ❌ **不改变对话主流程**：接入必须是**旁路 + 可关闭**的

---

## 二、接入场景分析

### 场景 1：对话中主动提及自身状态

**触发**：对话内容与"能力 / 表现 / 学习"相关，且**当前会话尚未提及过**自身状态。

**输出示例**：
> "顺便说一句，我昨天自检时发现**知识质量**那块有点下降（78 分），
>  原因是知识库里有两处冲突没处理。"

**设计要点**：

| 要点 | 取值 | 理由 |
|---|---|---|
| 触发频率上限 | **每会话最多 1 次**，且距上次 ≥ 2 小时 | 防止"刷存在感" |
| 仅提及"非健康"维度 | `health_level ∈ {concerning, critical}` | 健康时无需多言 |
| 禁止表达 | "我有问题" / "我坏了" / "我不行" | 避免消极自述（见 §六） |
| 必须带建议 | 每条状态后附**下一步动作** | 让表达有建设性 |

**风险**：过度自述会污染对话体验 → 用 `SELF_AWARENESS_MENTION_COOLDOWN_SEC`
（默认 7200）硬约束。

---

### 场景 2：决策时考虑自身健康度

**触发**：`PulseController` 接到**重任务**（多步检索 / 长任务 / 高并发）。

**规则**（保守、渐进式）：

| `overall_score` | 等级 | 行为 |
|---|---|---|
| ≥ 70 | healthy / moderate | **无影响**（完全不介入） |
| 50–69 | concerning | 记录 DEBUG 日志；**不改变行为** |
| 30–49 | critical | 对**超长任务**（预计 > 60s）追加**一次**确认提示 |
| < 30 | critical | 主动降级：把任务拆分为更小步骤 |

**设计要点**：

- **禁止断崖式降级**：只影响"任务粒度"，不影响"是否执行"
- 降级动作必须**可观测**（日志 + 事件），便于回溯"为什么这次变慢了"
- 分析结果**有延迟**（每日更新）→ 只用于"趋势性"判断，不用于单次决策

---

### 场景 3：进化优先级排序

**触发**：`SafeEvolutionExecutor` 每轮收集 `issues` 后，排序决定先修哪些。

**规则**：

```text
优先级分数 = 原来的 issue 分数
           + (该 issue 所属维度在 top_issues 中的严重度加成)
```

**维度映射表**（`top_issues.dimension` → 进化 issue 类型）：

| top_issues 维度 | 对应进化 issue 类型 | 加成 |
|---|---|---|
| `code_health` | `issue_type` 含 ruff 规则名 | +30% |
| `fake_loops` | `silent_exception_plain` / `bare_except` | +25% |
| `production_consumption` | 数据文件相关 | +10% |
| `knowledge_health` / `evolution_health` | 无直接对应 | 0 |

**设计要点**：

- 加成是**乘法之后加**（避免小问题被无限放大）
- **上限封顶**：单个 issue 的优先级不超过原始分数的 2 倍
- 加成只在 `top_issues` 的**前 3 条**生效（聚焦最关键问题）

---

### 场景 4：用户询问"你最近怎么样"的数据化回答

**触发**：用户显式询问自身状态（"你最近怎么样" / "你身体好吗" / "自检结果如何"）。

**输出结构**（固定三段）：

```text
① 综合：我最近自检是 <health_level>（<overall_score> / 100）。
② 亮点：表现最好的是 <最高分维度>（<分数>）。
③ 待改进：最该修的是 <top_issues[0]>，我打算 <建议>。
```

**设计要点**：

- 与场景 1 共用数据源，但**不受频率限制**（用户主动问就该答）
- 数据缺失时**如实说"还没有自检数据"**，不得编造
- 回答**不暴露内部文件名 / 行号**（面向用户的表达与内部报告分离）

---

## 三、接口定义

> ★本批仅**定义**，不实现。以下为 Python stub。

### 3.1 `SelfAwarenessEngine` 四个公共接口

```python
class SelfAwarenessEngine:
    """（现有类，阶段二新增下列 4 个公共只读接口）"""

    def get_latest_profile(self) -> SelfAwarenessProfile | None:
        """返回**最新一次**分析产生的画像（而非当前内存态）。

        数据源优先级：
            ① 内存中的最近一次 ``run_all_analyses()`` 结果（当次有效）；
            ② `data/self_awareness/profile_<latest>.json`（跨进程 / 冷启动可用）；
            ③ 都不可用 → ``None``（调用方必须容忍 None）。

        ★只读：绝不触发分析、绝不写盘。
        """
        ...

    def get_top_issues(self, n: int = 5) -> list[dict]:
        """返回最严重的 N 个问题。

        Returns:
            ``[{"dimension": str, "dimension_label": str,
                 "severity": "critical|high|medium|low|info",
                 "original_severity": str,
                 "description": str, "suggestion": str}, ...]``
            无数据时返回 ``[]``（**不返回 None**，便于调用方直接迭代）。
        """
        ...

    def get_health_level(self) -> str:
        """返回健康等级：``healthy`` / ``moderate`` / ``concerning`` /
        ``critical`` / ``unknown``（无数据）。"""
        ...

    def get_dimension_score(self, dimension: str) -> float | None:
        """返回指定维度的 0–100 评分。

        Args:
            dimension: ``code_health`` / ``runtime_health`` / ``knowledge_health`` /
                ``evolution_health`` / ``production_consumption`` /
                ``call_graph_health`` / ``fake_loops``。
        Returns:
            ``float`` 或 ``None``（维度不可用 / 名称非法）。
        """
        ...
```

### 3.2 供接入方使用的辅助接口（同批实现）

```python
class SelfAwarenessEngine:
    def is_fresh(self, max_age_sec: float = 86400) -> bool:
        """画像是否"足够新"（默认 24h 内）。场景 2/3 的使用前置条件。"""
        ...

    def get_public_summary(self) -> dict:
        """面向**用户可见**的摘要（不含文件名/行号/内部路径）。

        Returns:
            ``{"health_level": str, "overall_score": float|None,
               "best_dimension": {"name": str, "score": float} | None,
               "worst_dimension": {"name": str, "score": float} | None,
               "headline_issue": str  # 一句人话，如"知识库有两处冲突"
               }``
        """
        ...
```

### 3.3 配置项（灰度）

| 配置 | 默认 | 说明 |
|---|---|---|
| `ENABLE_SELF_AWARENESS_INFLUENCE_DECISION` | **False** | 总开关（阶段二实施时逐步开启） |
| `SELF_AWARENESS_MENTION_ENABLED` | False | 场景 1：对话主动提及 |
| `SELF_AWARENESS_MENTION_COOLDOWN_SEC` | 7200 | 提及冷却（秒） |
| `SELF_AWARENESS_DECISION_DEGRADE_THRESHOLD` | 50 | 场景 2：触发降级的分数阈值 |
| `SELF_AWARENESS_EVOLUTION_BOOST_ENABLED` | False | 场景 3：top_issues 加成 |
| `SELF_AWARENESS_EVOLUTION_BOOST_MAX` | 0.3 | 场景 3：单条最大加成比例 |

---

## 四、接入点设计

### 4.1 对话系统（`organs/body/PulseMouth.py` + `organs/brain/PulseCortex.py`）

**接入位置**：`PulseCortex` 的回答**后处理**阶段（不进入主 prompt 构造）。

```text
_PulseCortex._maybe_append_self_state(reply) -> reply
    ① 总开关 & 提及开关都开？
    ② 冷却窗口内已提及过？→ 直接返回原回复
    ③ 场景识别：用户显式询问（场景 4）or 话题相关（场景 1）
    ④ 取 get_public_summary() → 渲染一句
    ⑤ 追加到回复**末尾**（不打断主内容）
```

**为什么不在 prompt 里注入**：
- prompt 注入会改变模型输出分布，风险大且难回滚
- 后处理追加可控、可测、可一键关闭

### 4.2 决策系统（`organs/motor/PulseController.py`）

**接入位置**：任务派发**之前**的准入检查。

```text
_PulseController._check_self_awareness_admission(task) -> (allowed, note)
    ① 总开关开？且 is_fresh()？→ 否则放行
    ② get_health_level() → 按 §二·场景2 表决定行为
    ③ 降级动作只改"粒度"，不改"是否执行"
    ④ 记 INFO 日志 + 发一个 `SELF_AWARENESS_DEGRADE` 事件（可观测）
```

### 4.3 进化系统（`nucleus/reasoning/SafeEvolutionExecutor.py`）

**接入位置**：`repair_with_distillation()` 收集 issues 之后的**排序**阶段。

```text
_SafeEvolutionExecutor._boost_by_self_awareness(issues) -> issues
    ① 加成开关开？→ 否则原样返回
    ② 取 get_top_issues(3)
    ③ 按 §二·场景3 的维度映射表加成（封顶 2×）
    ④ 记日志说明"哪些 issue 因何被提前"
```

★**只读红线**：接入方只调 `get_*` 系列，**绝不**让自我认知反向修改分析结果。

---

## 五、灰度策略

**三级灰度**（每级独立开关，默认全关）：

| 级别 | 开关 | 影响面 | 回滚方式 |
|---|---|---|---|
| L1 只读观测 | （无需开关） | 无行为变化，仅日志 | — |
| L2 对话提及 | `SELF_AWARENESS_MENTION_ENABLED` | 仅回复末尾追加 | 关开关即时生效 |
| L3 决策/进化 | `ENABLE_SELF_AWARENESS_INFLUENCE_DECISION` | 影响任务粒度与排序 | 关开关即时生效 |

**推进节奏**：

1. 先开 L1 观测 **≥ 3 天**，确认画像稳定、无假数据
2. 再开 L2（对话提及），观察用户体验
3. 最后开 L3，且**先开进化加成、后开决策降级**

**回滚保障**：所有开关读取失败一律按**关闭**处理（保守优先）。

---

## 六、风险与边界

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| 1 | **消极自述**（"我有问题"）污染对话 | 用户体验下降 | 场景 1 禁用负面自述词；只报"可改进项 + 动作" |
| 2 | **断崖式降级**（健康分低就不干活） | 核心功能不可用 | 只降"粒度"不降"是否执行"；阈值保守（<30） |
| 3 | **数据延迟**（每日更新）被当实时依据 | 决策错配 | `is_fresh()` 前置；超 24h 一律不介入 |
| 4 | **分析结果本身有误**（引擎 bug） | 错误影响决策 | L1 观测期 ≥3 天；接入方对 `None`/异常一律"无影响" |
| 5 | **循环依赖**（引擎 ↔ 决策） | 死锁/递归 | 接入方**只读**；引擎不感知接入方 |
| 6 | **性能开销** | 对话延迟 | `get_latest_profile()` 只读内存/单文件，**禁止**触发 `run_all_analyses()` |
| 7 | **暴露内部信息** | 安全/体验 | 用户可见路径与内部报告**分离**（`get_public_summary`） |

**硬边界（红线）**：

- 接入方**不得**触发任何分析（`run_all_analyses()` 只能由调度器/手动脚本调用）
- 接入方**不得**写入分析结果
- 任何异常一律"降级为无影响"，**绝不**阻断主流程

---

## 七、阶段二实施路线图（3 批）

| 批次 | 主题 | 内容 | 预估 |
|---|---|---|---|
| **第40批** | 接口落地 + L1 观测 | 实现 §三 的 4+2 个公共接口；`config` 全部开关（默认关）；补单测；L1 日志埋点 | 3–4h |
| **第41批** | 对话接入（L2） | 场景 1 + 场景 4；`_maybe_append_self_state`；提及冷却；用户可见摘要渲染 | 3–4h |
| **第42批** | 决策/进化接入（L3） | 场景 2 + 场景 3；降级准入 + 优先级加成；端到端验证 + 效果评估 | 4–6h |

**每批的共同门禁**：`ruff F=0` / `E402` 零新增 / `pytest` 全量 / `verify 43 PASS` /
**开关默认关闭**（零行为变化）。

**收官判据**：连续运行 3 天后，
① `data/self_awareness/` 每日报告正常；
② 对话中出现过自发状态提及且无负面表述；
③ 进化优先级日志中可见 top_issues 加成记录。

---

## 八、兼容性分析

| 维度 | 结论 | 说明 |
|---|---|---|
| 现有对话流程 | ✅ 无侵入 | 后处理追加；开关默认关 |
| 现有决策流程 | ✅ 无侵入 | 准入检查是旁路分支；默认直接放行 |
| 现有进化排序 | ✅ 无侵入 | 排序后加成；开关默认关 |
| `SelfAwarenessEngine` 现有 API | ✅ 只增不改 | 新增 `get_*` 系列，不改动已有方法签名 |
| 画像数据结构 | ✅ 向前兼容 | 复用 `overall_score`/`health_level`/`top_issues` |
| 冷启动（无画像） | ✅ 安全 | 全部 `get_*` 返回 `None`/`[]`/`unknown` |

---

## 九、附录：与阶段一产出的对应关系

| 阶段一产出 | 阶段二用途 |
|---|---|
| `profile_<ts>.json` | `get_latest_profile()` 的冷启动数据源 |
| `overall_score` / `health_level` | 场景 2 降级判定、场景 4 回答 |
| `top_issues`（5 级 severity） | 场景 1 表述、场景 3 加成 |
| `report_<ts>.txt` | （不直接消费，供人工查阅） |
| `daily_schedule` | 保证数据**每日新鲜**（`is_fresh()` 的前提） |

---

**设计结束。本批（第39批）仅产出本文档，不修改任何生产代码。**
