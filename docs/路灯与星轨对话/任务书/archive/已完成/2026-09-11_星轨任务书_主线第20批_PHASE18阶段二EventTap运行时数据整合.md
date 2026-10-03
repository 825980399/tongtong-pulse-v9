# 星轨任务书 — 主线第20批 · PHASE18阶段二启动：EventTap运行时数据整合与动静结合画像

**下发人**: 星轨
**下发时间**: 2026-09-11
**批次编号**: 主线第20批
**承接人**: 路灯
**预计工时**: 5-7小时
**优先级**: 高（PHASE18阶段二启动）

---

## 〇、一句话任务

把第17批建成的EventTap旁路事件统计**整合进自我认知引擎**，让画像从"纯静态代码分析"升级为"**动静结合**"的运行时健康度评估；同时顺手收口P2-113（补丁应用流程print改日志）。

---

## 一、背景与当前状态

### 1.1 已完成（一笔带过）

- PHASE18阶段一**正式收尾**（第18-19批）：自我认知引擎基础框架、产出-消费配对器、虚假闭环检测器、LogAnalyzer/CodeReviewEngine整合、首份真实报告、健康度基线
- EventTap（第17批）已在运行：旁路订阅事件总线全部事件，按事件名/来源/优先级分组计数，支持`get_stats()`/`get_recent()`/`export_stats()`
- pytest基线：525 passed
- 技术债务清单：v7.3

### 1.2 当前缺口

自我认知画像目前只有**静态数据**（代码结构、数据文件、虚假闭环、代码问题），**缺少动态运行时数据**。EventTap已经在收集事件统计，但没有被自我认知引擎消费——"建而不用"的又一个实例。

### 1.3 阶段二目标

PHASE18阶段二的核心方向：**动静结合**——把运行时事件数据并入画像，形成"代码静态健康度 + 运行时动态健康度"的双维度评估。本批是阶段二的第一步：EventTap数据整合。

---

## 二、EventTap现有接口确认（实现前必读）

### 2.1 单例获取

```python
from nucleus.events.EventTap import get_event_tap
tap = get_event_tap()  # 首次调用时创建并订阅"**"，LOW优先级
```

### 2.2 get_stats() 返回结构

```python
{
    "enabled": bool,
    "started": bool,
    "total": int,                    # 总事件数
    "distinct_names": int,           # 事件种类数
    "by_name": dict[str, int],       # 按事件名分组计数
    "by_source": dict[str, int],     # 按来源器官分组计数
    "by_priority": dict[str, int],   # 按优先级分组计数
    "interval": {
        "min": float | None,
        "max": float,
        "avg": float,
        "samples": int,
    },
    "recent_size": int,
}
```

### 2.3 其他接口

- `get_recent(limit=100)` → 最近事件列表（含name/source/priority/timestamp/payload_summary）
- `reset_stats()` → 清空统计（保留订阅）
- `export_stats(path)` → 导出统计+最近事件为JSON

### 2.4 开关

- `config.ENABLE_EVENT_BUS_TAP`（默认True）：旁路监听总开关
- 关闭时`get_stats()`返回enabled=False，by_name等为空dict

---

## 三、6项任务明细

### T1：EventTap运行时统计整合模块（核心）

**文件**：`nucleus/self_awareness/SelfAwarenessEngine.py`

**新增方法**：`integrate_event_tap(self, tap=None) -> dict[str, Any]`

**功能**：
1. 从EventTap单例获取统计数据（`get_stats()`）
2. 转换为画像的`runtime_events`维度，输出：
   - `total_events`：总事件数
   - `distinct_event_names`：事件种类数
   - `active_sources`：活跃来源器官数
   - `top_event_names`：Top10事件名（按计数降序，含count）
   - `top_sources`：Top10来源器官（按计数降序，含count）
   - `by_priority`：按优先级分布
   - `interval`：事件到达间隔（min/max/avg/samples）
   - `tap_enabled`：EventTap是否启用
   - `tap_started`：EventTap是否已启动
3. 开关：`config.ENABLE_EVENT_TAP_INTEGRATION`（默认True）
4. 异常处理：
   - EventTap未启动/导入失败/获取异常 → 返回空dict + `self._log(LogLevel.WARNING, ...)`
   - 开关关闭 → 返回`{"disabled": True}` + DEBUG
5. 不修改`SelfAwarenessProfile`数据模型（只读字段，通过`extra`或独立dict传递）

**红线**：
- 不得在`integrate_event_tap`中调用`reset_stats()`（重置是用户主动行为，不是整合行为）
- 不得修改EventTap的任何状态（只读）
- 不得阻塞事件总线（EventTap的`get_stats()`是加锁的深拷贝，耗时极短，直接调用即可）

---

### T2：器官活跃度排名与异常识别

**文件**：`nucleus/self_awareness/SelfAwarenessEngine.py`

**新增方法**：`analyze_organ_activity(self, event_stats: dict) -> dict[str, Any]`

**功能**：
1. 基于`by_source`统计，计算每个器官的事件发布活跃度
2. 输出：
   - `ranking`：器官活跃度排名列表（[{organ, count, percentage}, ...]，按count降序）
   - `silent_organs`：沉默器官列表（发布事件<总事件1%的器官，含count）
   - `overactive_organs`：过热器官列表（发布事件>总事件20%的器官，含count）
   - `total_organs`：参与事件发布的器官总数
   - `concentration_ratio`：集中度（Top1器官事件数/总事件数，衡量是否过度集中）
3. 阈值可配置（通过方法参数，默认silent_threshold=0.01, overactive_threshold=0.20）
4. 输入为空dict或缺少by_source → 返回`{"no_data": True}` + DEBUG

**设计意图**：
- 沉默器官可能意味着"建而不用"或"通信路径断裂"
- 过热器官可能意味着"单点瓶颈"或"事件风暴"
- 集中度过高可能意味着架构不平衡

---

### T3：画像报告增强（动静结合）

**文件**：
- `nucleus/self_awareness/SelfAwarenessEngine.py`（报告生成方法）
- `tools/run_self_awareness_analysis.py`（脚本）

**功能**：
1. 在profile JSON中新增`runtime_events`字段（T1的输出）和`organ_activity`字段（T2的输出）
2. 在文本报告中新增"**运行时事件统计**"段落，展示：
   - 总事件数、事件种类数、活跃器官数
   - Top5活跃事件名（含count）
   - Top5活跃来源器官（含count和占比）
   - 事件到达间隔（min/max/avg）
   - 按优先级分布
3. 在文本报告中新增"**器官活跃度分析**"段落，展示：
   - 沉默器官列表（如有）
   - 过热器官列表（如有）
   - 集中度评估（正常/偏高/过高）
4. 如果EventTap未启用或无数据，报告中显示"运行时数据不可用（EventTap未启用或统计为空）"，不报错

**红线**：
- 报告格式保持与现有报告一致（纯文本，分段清晰）
- 不得删除现有报告的任何段落

---

### T4：手动分析脚本增强

**文件**：`tools/run_self_awareness_analysis.py`

**新增开关**：
1. `--include-event-tap`（默认True）：是否整合EventTap运行时数据
2. `--reset-event-tap`（默认False）：分析前重置EventTap统计（获取干净的时间窗口数据）
   - 重置后等待用户指定的时间窗口再分析？→ **不，本批只做重置+立即分析**，时间窗口控制留待后续
   - 重置前输出WARNING："将重置EventTap统计，此前数据将丢失"
3. `--event-tap-window N`（默认0=不限制）：仅使用最近N秒的事件统计
   - 本批**不实现**时间窗口过滤，只预留参数位置（传>0时输出WARNING"时间窗口过滤暂未实现，使用全量统计"）

**功能**：
- 脚本运行时自动调用`integrate_event_tap()`和`analyze_organ_activity()`
- 控制台输出运行时事件统计摘要（总事件数、Top3器官、沉默/过热器官数）
- 落盘文件中包含runtime_events和organ_activity字段

---

### T5：测试（≥15例）

**文件**：`tests/test_self_awareness_m20.py`（新建）

**测试分组**：

| 组 | 例数 | 内容 |
|---|------|------|
| EventTap整合接口 | 5 | 正常数据整合、EventTap未启用、导入失败、开关关闭、空统计 |
| 器官活跃度排名 | 4 | 正常排名、沉默器官识别、过热器官识别、空数据 |
| 报告增强 | 3 | 报告包含运行时段落、报告包含器官活跃度段落、无数据时显示提示 |
| 脚本开关 | 3 | --include-event-tap关闭、--reset-event-tap重置、--event-tap-window预留参数 |

**测试要求**：
- 使用mock EventTap（不依赖真实事件总线）
- 测试隔离：使用tmp目录，不污染真实数据
- 每个测试独立，不依赖执行顺序

---

### T6：技术债务顺手处理 — P2-113 补丁应用流程print改日志

**文件**：`main.py`

**内容**：
把补丁应用/自验证流程中的`print()`输出改为`_logger.info()`，确保这些日志能持久化到运行日志文件。

**涉及位置**（main.py，约15处）：
- 行3052：`[自重启] 接班进程启动失败`
- 行3083：`[补丁] 应用完成`
- 行3104：`[自验证] 已写入待验证标记`
- 行3106：`[自验证] 标记写入失败`
- 行3133：`[自验证] 检测到启动失败且有待验证补丁，尝试回退`
- 行3137：`[自验证] 回退成功，3秒后重启`
- 行3149：`[自验证] 回退失败，请人工介入`
- 行3231：`[自验证] 检测到待验证补丁，执行自我验证`
- 行3235：`[自验证] ✅ 验证通过，清除待验证标记`
- 行3241：`[自验证] ❌ 验证失败，尝试自我反思改进`
- 行3267：`[自验证] 💡 反思改进版补丁已生成并通过验证`
- 行3270：`[自验证] 反思改进版补丁验证失败`
- 行3272：`[自验证] 反思改进异常`
- 行3277：`[自验证] 反思改进已提交`
- 行3279：`[自验证] ⚠️ 本轮已自动回退过一次`
- 行3295：`[自验证] 已回退最近补丁，3秒后重启`
- 行3316：`[自验证] 回退失败，停止自动重启`

**要求**：
- 改为`_logger.info()`（不是WARNING，这些是正常流程信息）
- 保持原有文本内容不变（包括emoji）
- 确保`_logger`在这些位置可用（main.py顶部应有`get_module_logger`）
- 行3052的"接班进程启动失败"改为`_logger.error()`（这是错误，不是正常流程）

**红线**：
- 不得修改补丁应用/自验证的业务逻辑，只改日志输出方式
- 不得删除任何print（全部改为logger）

---

## 四、验收标准

### 4.1 功能验收

| # | 验收项 | 标准 |
|---|--------|------|
| 1 | EventTap整合接口 | `integrate_event_tap()`返回包含total_events/top_sources/interval等字段的dict |
| 2 | 器官活跃度排名 | `analyze_organ_activity()`返回ranking/silent_organs/overactive_organs |
| 3 | 报告增强 | 手动运行脚本，文本报告中包含"运行时事件统计"和"器官活跃度分析"段落 |
| 4 | profile JSON | 落盘的profile JSON中包含runtime_events和organ_activity字段 |
| 5 | 脚本开关 | --include-event-tap/--reset-event-tap/--event-tap-window三个开关均可用 |
| 6 | 测试 | 新增≥15例测试，全部通过 |
| 7 | P2-113收口 | main.py补丁应用/自验证流程无print残留，全部走logger |

### 4.2 质量门禁

| 门禁 | 标准 |
|------|------|
| ruff F（全库） | 0 |
| ruff E402（修改范围） | 0 |
| py_compile | 全部改动文件通过 |
| pytest | 全过（525+新增≥15） |
| 框架回归 | verify 43/43 |
| 行尾符 | 保持CRLF |

### 4.3 手动验证步骤

1. 启动框架，等待至少1分钟（让EventTap收集一些事件）
2. 运行`python tools/run_self_awareness_analysis.py`
3. 检查生成的report_*.txt中是否包含"运行时事件统计"和"器官活跃度分析"段落
4. 检查profile_*.json中是否包含runtime_events和organ_activity字段
5. 运行`python tools/run_self_awareness_analysis.py --reset-event-tap`，确认EventTap统计被重置后重新收集
6. 检查logs/pulse.log中是否包含补丁应用/自验证相关的日志（验证P2-113收口）

---

## 五、风险与注意事项

1. **EventTap统计是全局累积的**：不是按时间窗口的，整合时应在报告中注明"统计自框架启动以来累积"。时间窗口过滤留待后续批次。
2. **器官名映射**：EventTap的`by_source`中的source可能是器官类名（如"PulseCortex"），也可能是其他字符串。排名时直接使用原始source，不做映射（映射留待后续）。
3. **main.py的_logger可用性**：修改print为logger前，先确认main.py顶部有`_logger = get_module_logger(...)`。如果没有，先添加。
4. **不修改SelfAwarenessProfile数据模型**：runtime_events和organ_activity通过extra字段或独立dict传递，不修改Profile类的定义（避免破坏现有序列化/反序列化）。
5. **测试不依赖真实EventTap**：用mock对象注入，避免测试间互相干扰。

---

## 六、交付物清单

1. `nucleus/self_awareness/SelfAwarenessEngine.py`（修改：+T1/T2/T3方法）
2. `tools/run_self_awareness_analysis.py`（修改：+T4开关）
3. `main.py`（修改：T6 print改logger）
4. `tests/test_self_awareness_m20.py`（新建：T5 ≥15例测试）
5. 交付报告（含修改文件清单、测试结果、手动验证结果、遇到的问题）

---

## 七、与技术债务清单的关联

| 债务编号 | 事项 | 本批处理 |
|----------|------|---------|
| P2-63 | EventBus建而不用 | 🔄 持续推进（EventTap数据被消费，从"建而不用"到"真实消费"） |
| P2-113 | 补丁应用流程print改日志 | ✅ **本批闭环**（T6） |
| P3-3 | 跨文件调用图构建 | 📋 留待后续（本批只做器官活跃度，不做调用图） |
| P3-5 | 进化方向分析缺失 | 🔄 持续推进（动静结合画像为进化方向分析提供数据基础） |

---

**任务书结束**。路灯领取后请先读EventTap.py和SelfAwarenessEngine.py现有代码，确认接口后再实现。遇到问题及时在交付报告中记录，需星轨裁决的事项明确列出。
