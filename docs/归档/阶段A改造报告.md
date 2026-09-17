# 阶段 A 改造报告：LLM 主导补丁生成 + tools/ 定期测试纳入检测体系

> 版本：v28.0 阶段A
> 目标：缩小与「世界顶级自主迭代智能体」的核心差距——从「6类规则自修复」扩展到「开放域 LLM 修复」，并把 tools/ 测试工具纳入框架定期检测。
> 状态：核心改造完成，待本地运行验证

---

## 一、本阶段解决的两个问题

基于《自主迭代能力差距分析报告》定位的核心差距，本阶段落地两个改造：

1. **LLM 主导补丁生成**：`_generate_patch` 规则 miss 时不再 `return None`，而是回退到 LLM 做开放域语义修复。
2. **tools/ 定期测试**：把散落的测试工具纳入框架检测体系，作为健康度「行为层」验证。

---

## 二、改造一：LLM 主导补丁生成

### 断点与修复

**断点**：`SafeEvolutionExecutor._generate_patch` 的 `else: return None`——遇到规则表未覆盖的第 7 类问题，直接放弃。

**修复**：新增 `_generate_llm_patch` 方法，规则 miss 时回退到 LLM。

```python
# 改造前
else:
    return None  # 通用问题，暂不自动修复

# 改造后
else:
    return self._generate_llm_patch(plan, self_inspector, original_code, file_path, method_name)
```

### `_generate_llm_patch` 设计

1. 从 `plan` 构造 issue，调用 `_call_llm_for_repair` 生成 LLM 修复。
2. 清理 LLM 输出的 markdown 代码块，与原文对比（无实质变化则放弃）。
3. 补丁标记 `repair_source="llm"`、`confidence="medium"`（与本地规则 `high` 区分）。
4. 无 API key 时优雅降级返回 `None`（不崩溃、不阻塞）。

### 安全门槛（关键设计）

LLM 补丁风险高于本地规则补丁，因此增加**额外信任门槛**：

```python
# LLM 补丁需满足 auto_apply_min_trust 才自动批准
_is_llm_patch = patch.get("repair_source") == "llm"
if _is_llm_patch and patch.get("trust_score", 0) < _min_trust:
    patch["status"] = "verified"  # 信任不足，降级待人工审批
else:
    patch["status"] = "approved"  # 本地规则补丁不受此门槛限制
```

**验证结果**：4 个用例全部正确——LLM 低信任(30)→待审批，LLM 高信任(60/90)→批准，本地规则(30)→不受门槛限制直接批准。

---

## 三、改造二：tools/ 定期测试纳入检测体系

### 新增 `PeriodicTestScheduler`

自动扫描 `tools/` 目录，按脚本名前缀分类：

| 分类 | 前缀 | 策略 | 数量 |
|------|------|------|------|
| 轻量验证 | `verify_*` / `test_*` | 每轮都跑（超时60s） | 5 |
| 重负载 | `stress_test_*` / `benchmark_*` | 低频跑（每6轮一次，超时300s） | 4 |
| 运维工具 | `clean_*` / `import_*` / `check_*` / `find_*` / `deep_*` / `gray_*` | 不自动跑 | 7 |

### 接入心跳机制

在 `PulseCodeLearner._on_heartbeat` 中，每 100 次心跳触发一轮轻量测试：

```python
self._test_run_interval = 100  # 每100次心跳跑一轮轻量测试

def _maybe_run_periodic_tests(self):
    if self._heartbeat_count % self._test_run_interval != 0:
        return
    # 通过 info_field 异步提交，避免阻塞心跳主线程
    ...
```

### 测试结果归档

结果归档到 `data/evolution/test_runs/test_run_YYYYMMDD_HHMMSS.json`，形成可追溯的测试历史。失败时记录失败脚本并告警。

---

## 四、验证结果

| 验证项 | 结果 |
|--------|------|
| `SafeEvolutionExecutor` 语法编译 | ✅ 通过 |
| `_generate_llm_patch` 方法存在 + `_generate_patch` 接入 | ✅ 通过 |
| 无 API key 时优雅降级返回 None | ✅ 通过 |
| LLM 补丁信任门槛逻辑（4 用例） | ✅ 4/4 正确 |
| `PeriodicTestScheduler` 脚本分类（5轻量/4重载/7运维） | ✅ 正确 |
| 实际跑一轮轻量测试 | ✅ 5/5 通过 |
| `PulseCodeLearner` 实例化 + 调度器注入 | ✅ 通过 |
| 定期测试触发（心跳=100） | ✅ 通过，归档正常 |
| `verify_patch_coverage.py` 回归测试 | ✅ 12/12 通过 |
| 完整导入链（evolution 子系统 7 模块） | ✅ 通过 |

---

## 五、能力差距缩小情况

| 差距维度 | 阶段A前 | 阶段A后 |
|---------|--------|---------|
| 问题发现 | 6类硬编码规则 | 6类规则 + LLM开放域 |
| 补丁生成 | 规则 miss 即放弃 | 规则 miss 回退 LLM |
| 测试能力 | 仅进化时跑2脚本 | 定期跑5轻量+低频4重载 |

**尚未解决（留给阶段B/C）**：跨文件修改、多步规划、失败后自我反思。

---

## 六、涉及文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `nucleus/reasoning/SafeEvolutionExecutor.py` | 修改 | 新增 `_generate_llm_patch` + 规则 miss 回退 + LLM 信任门槛 |
| `nucleus/evolution/PeriodicTestScheduler.py` | 新建 | 定期测试调度器 |
| `nucleus/evolution/__init__.py` | 修改 | 导出新模块 |
| `organs/brain/PulseCodeLearner.py` | 修改 | 注入调度器 + 心跳触发定期测试 |

---

## 七、待办与注意事项

1. **本地运行验证**（待执行）：需配置 `TTP_REMOTE_API_KEY` 环境变量后，LLM 补丁才能真正生成。未配置时优雅降级（仅规则修复生效）。
2. **LLM 补丁质量依赖大模型**：`deepseek-v4-flash` 是轻量模型，复杂修复质量可能有限，后续可考虑用 `advanced_model`（deepseek-v4-pro）做复杂修复。
3. **定期测试频率**：当前 100 次心跳一轮（约 50 分钟，因心跳 30s），如觉得频繁可调大 `_test_run_interval`。
4. **资产空间同步**（待执行）：本地改动尚未对比替换资产空间对应文件。
