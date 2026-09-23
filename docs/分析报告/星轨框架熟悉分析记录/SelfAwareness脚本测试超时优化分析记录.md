# 框架熟悉分析记录：SelfAwareness脚本测试超时优化（P2-243）

**分析时间**：2026-09-15
**分析人**：星轨（定时修复过程中精读）
**文件**：`tests/test_self_awareness_m20.py`（340行）、`tools/run_self_awareness_analysis.py`（347行）
**问题**：P2-243 - TestScriptSwitches单类191s超Bash默认120s超时

## 一、问题根因

**TestScriptSwitches类**有3个测试方法，每个都调用 `_run()` 方法：
1. `test_include_event_tap_off`：验证 `--no-include-event-tap` 开关
2. `test_reset_event_tap`：验证 `--reset-event-tap` 开关
3. `test_event_tap_window_reserved`：验证 `--event-tap-window` 开关

**`_run()` 方法**会：
1. 导入 `run_self_awareness_analysis` 脚本
2. 调用 `_script.main(argv)` 运行完整的自我认知分析
3. `main()` 内部调用 `_engine.run_all_analyses(scope="all")` —— **这是最耗时的部分**

**`run_all_analyses` 做什么**：
- 运行所有已注册的分析器（production_consumption、fake_loops、log_analyzer、code_review等）
- 每个分析器都需要扫描全项目代码（数百个文件，数万行代码）
- 即使使用 `--skip-code-review`，仍然运行其他分析器
- 单次运行约60秒，3个测试就是180秒

## 二、修复方案

**采用方案：mock `run_all_analyses` 返回空profile（不修改生产代码）**

在 `TestScriptSwitches._run()` 方法中添加：
```python
mock.patch.object(SelfAwarenessEngine, "run_all_analyses",
                  return_value=SelfAwarenessProfile())
```

**为什么这个方案是正确的**：
1. TestScriptSwitches的测试目标是**验证命令行开关的解析和行为**，不是验证分析器的正确性
2. 分析器的正确性由其他测试类（TestIntegrateEventTap、TestOrganActivity等）覆盖
3. mock `run_all_analyses` 后，仍然验证：
   - 命令行参数解析（argparse）
   - EventTap重置逻辑
   - 分析器注册列表（`list_analyzers()`）
   - 报告生成和落盘
   - 退出码

**不采用的方案**：
- ❌ 按类拆分：只是把问题分散，没有解决根本原因
- ❌ 增加超时配置：没有解决测试慢的问题，只是容忍了慢
- ❌ 删除这些测试：会丢失命令行开关的覆盖

## 三、验证结果

| 指标 | 修复前 | 修复后 | 改善 |
|------|--------|--------|------|
| TestScriptSwitches单类耗时 | 191.37s | 75.59s | **-60%** |
| 是否超Bash默认120s | ✅ 超时 | ❌ 不超时 | 已解决 |
| 完整文件测试数 | 21 | 21 | 不变 |
| 完整文件耗时 | ~200s | 76.34s | -62% |
| ruff F类检查 | 通过 | 通过 | 不变 |

**剩余耗时来源**（75秒/3测试 ≈ 25秒/测试）：
1. 每次测试重新导入 `run_self_awareness_analysis` 模块（约5秒）
2. `_build_engine` 注册分析器（约3秒）
3. `analyze_organ_activity` 运行（约2秒）
4. 报告生成和落盘（约5秒）
5. pytest框架开销（约10秒）

这些是脚本集成测试的固有开销，可接受。

## 四、修改内容

**文件**：`tests/test_self_awareness_m20.py`

修改 `TestScriptSwitches._run()` 方法：
- 添加 `mock.patch.object(SelfAwarenessEngine, "run_all_analyses", return_value=SelfAwarenessProfile())`
- 更新docstring说明优化原因（P2-243）
- 不修改任何生产代码

## 五、设计意图理解

1. **为什么脚本测试要运行完整main()？**
   - 集成测试需要验证命令行开关到行为的完整链路
   - 只测试argparse解析不能验证开关是否真正影响了行为

2. **为什么mock run_all_analyses是安全的？**
   - run_all_analyses的正确性由单元测试覆盖（TestIntegrateEventTap等）
   - 脚本测试只需要验证"开关是否正确传递给引擎"
   - mock后仍然验证了分析器注册列表（证明开关影响了注册）

3. **为什么不mock整个_build_engine？**
   - 需要验证 `list_analyzers()` 的输出（测试中检查"已注册分析器"）
   - 需要验证EventTap重置逻辑（`reset_event_tap`调用）
   - 只mock最耗时的run_all_analyses，保留其他集成验证

## 六、经验教训

1. **集成测试要区分"验证开关"和"验证功能"**：验证开关时可以mock耗时的功能部分
2. **测试超时问题要找根因**：不是简单增加超时，而是找到耗时的真正来源
3. **mock要精确**：只mock最耗时的部分，保留尽可能多的集成验证
4. **SelfAwarenessProfile所有字段有默认值**：可以直接 `SelfAwarenessProfile()` 创建空对象作为mock返回值
