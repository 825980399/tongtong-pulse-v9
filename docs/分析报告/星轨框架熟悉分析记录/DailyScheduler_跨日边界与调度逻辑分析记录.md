# 框架熟悉分析记录：DailyScheduler跨日边界与P2-232测试补充

**分析时间**：2026-09-15
**分析人**：星轨（定时修复过程中精读）
**文件**：`nucleus/self_awareness/DailyScheduler.py`（545行）
**问题**：P2-232 - 调度跨日边界无显式测试（23:59启动、次日03:00触发行为正确但无测试）

## 一、文件基本信息

- **路径**：`nucleus/self_awareness/DailyScheduler.py`
- **行数**：545行
- **功能**：自我认知引擎每日低频调度器，在指定时间自动运行全量分析并生成报告
- **核心类**：`SelfAwarenessDailyScheduler`
- **调度方式**：进程内daemon线程 + 文件判重（report_<YYYYMMDD>_*.txt）

## 二、核心类/函数

### 2.1 SelfAwarenessDailyScheduler

**构造参数**：
- `engine`：自我认知引擎实例
- `now_fn`：时间函数（可注入，测试用），默认 `datetime.now`
- `check_interval`：轮询间隔（秒）
- `output_dir`：输出目录
- `project_root`：项目根目录

### 2.2 核心调度逻辑

**_due(now) -> bool**：
- 比较 `(now.hour, now.minute) >= (schedule_hour(), schedule_minute())`
- **只比较时分，不比较日期**
- 调度点03:00时：03:00~23:59返回True，00:00~02:59返回False

**_today_done(now) -> bool**：
- 检查输出目录是否存在 `report_<YYYYMMDD>_*.txt`
- 使用 `now.strftime("%Y%m%d")` 作为日期前缀
- 跨日后自动使用新日期检查
- 目录不存在/读取失败 → 视为"未执行"（宁可多跑，不可漏跑）

**_loop()**：
- 后台daemon线程循环
- `if _due(now) and not _today_done(now): run_once()`
- 异常不退出线程（调度层兜底）

### 2.3 run_once()

- 执行全量分析 + 画像落盘 + 报告落盘
- 返回dict：status/elapsed_ms/profile_path/report_path/dimensions/error
- **防御**：pytest环境不得写生产目录（_m37_guard）

## 三、跨日边界行为分析

**调度点03:00时的完整跨日流程**：

| 时间 | _due | _today_done | 行为 |
|------|------|-------------|------|
| 当天23:59（有报告） | True | True | 跳过（当天已完成） |
| 当天23:59（无报告） | True | False | 补跑当天 |
| 次日00:00 | False | - | 不执行（未到点） |
| 次日02:59 | False | - | 不执行（未到点） |
| 次日03:00 | True | False | 正常执行 |

**关键设计**：
1. `_due`只比较时分 → 23:59属于"已过调度点"的补跑窗口
2. `_today_done`按日期检查 → 跨日后自动切换，昨天的报告不阻塞今天
3. 两者结合实现"每日一次"的调度语义

## 四、P2-232修复内容

**问题**：跨日边界行为正确但无显式测试。

**修复**：在 `tests/test_sa_schedule_m37.py` 中新增 `TestDayBoundary` 类，包含7个测试：

| 测试 | 验证内容 |
|------|----------|
| test_60_due_at_2359_is_true | 23:59时_due=True（补跑窗口） |
| test_61_due_at_midnight_is_false | 次日00:00时_due=False |
| test_62_due_at_0259_is_false | 次日02:59时_due=False |
| test_63_cross_day_today_done_uses_new_date | 跨日后_today_done使用新日期 |
| test_64_cross_day_full_flow_skip_then_run | 完整跨日流程：跳过→不执行→执行 |
| test_65_2359_without_report_triggers_makeup_run | 23:59无报告时补跑当天 |
| test_66_schedule_time_near_midnight_boundary | 调度点附近边界（02:59/03:00） |

**技术要点**：
- test_65使用 `now_fn=lambda: _fixed` 注入固定时间，确保 `run_once()` 生成的报告文件名使用注入日期
- 所有测试使用 `tempfile.mkdtemp()` 创建临时输出目录，不污染生产环境

## 五、验证结果

| 验证项 | 结果 |
|--------|------|
| ruff F类检查 | ✅ All checks passed |
| TestDayBoundary类 | ✅ 7 passed in 0.15s |
| 完整test_sa_schedule_m37.py | ✅ 34 passed in 0.55s（原27+新增7） |

## 六、设计意图理解

1. **为什么用文件判重而不是内存状态？**
   - 进程重启后仍能判断当天是否已执行
   - 多进程场景下也能正确判重
   - 文件系统是最可靠的跨进程状态

2. **为什么_due只比较时分？**
   - 简化逻辑，不需要处理日期边界
   - 配合_today_done的日期检查，自然实现"每日一次"
   - 23:59启动时如果当天没跑，会立即补跑（容错设计）

3. **为什么宁可多跑不可漏跑？**
   - 自我认知分析是幂等的（重复运行不会产生副作用）
   - 漏跑会导致当天没有分析报告，影响自我认知连续性
   - 多跑一次的成本很低（几分钟的分析时间）

## 七、发现的其他问题

无。DailyScheduler的跨日边界设计是正确的，只是缺少测试覆盖。P2-232修复后，跨日边界行为已有完整的测试保障。
