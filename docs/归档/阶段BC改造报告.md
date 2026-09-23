# 阶段 B/C 改造报告：跨文件多步规划 + 失败后自我反思

> 版本：v29.0 阶段B+C
> 目标：完成「世界顶级自主迭代智能体」三阶段升级路线的最后两步——架构级重构能力（B）与迭代式进化深度（C）。
> 状态：核心改造完成，待本地运行验证

---

## 一、本阶段解决的问题

基于《自主迭代能力差距分析报告》的三阶段路线图，阶段 A 已解决「覆盖面」差距（LLM 主导补丁），本阶段解决剩下两个：

| 阶段 | 差距 | 解决能力 |
|------|------|---------|
| B | 跨文件修改、多步规划 | `LLMEvolutionEngine` |
| C | 失败后自我反思 | `SelfReflectionEngine` |

---

## 二、改造一：LLMEvolutionEngine（阶段B）

### 新增 `nucleus/evolution/LLMEvolutionEngine.py`

统一承载「跨文件补丁包」和「多步规划」两大能力：

**能力1：跨文件补丁包 `generate_multi_file_patch`**
- 给 LLM 投喂**调用链上下文**（`called_methods` + `called_by`，来自既有的 `get_call_chain`）。
- 读取调用方代码，让 LLM 在改接口/方法签名时**同步迁移所有调用方**。
- LLM 输出结构化 JSON 补丁包（多文件多方法），严格解析校验。

**能力2：多步规划 `plan_multi_step`**
- 让 LLM 把复杂重构拆成多个有依赖关系的步骤，每步含「目标 + 验证标准 + 依赖前序步骤」。
- 每步独立验证，前一步通过才执行下一步。

### 安全设计
- 跨文件补丁 `trust_score=50`（保守），`repair_source="llm_multi_file"`。
- 用 `advanced_model`（deepseek-v4-pro）做复杂重构（轻量模型不够）。
- 无 API key 时优雅降级返回空列表。

### 验证结果
- JSON 补丁包解析：2 补丁正确提取（含跨文件调用方迁移）
- 多步计划解析：2 步正确提取（含依赖关系）
- 无 API key 优雅降级：返回空列表，不崩溃

---

## 三、改造二：SelfReflectionEngine（阶段C）

### 新增 `nucleus/evolution/SelfReflectionEngine.py`

验证失败时不再只是「回退停止」，而是把失败现场喂回 LLM 做根因分析 + 改进：

**核心方法 `reflect_and_improve`**：
- 输入：失败补丁（含 original/modified/diff_summary）+ 三关验证详情 + 健康度变化。
- LLM 分析失败根因，生成改进版补丁。
- 改进版补丁标记 `repair_source="llm_reflection"`、`confidence="low"`、`trust_score=40`（反思产物风险最高，需最严格验证）。

### SelfVerifier 增强

新增 `run_verification_detailed`，返回三关验证的**详细结果**（每关通过情况 + 失败详情），供反思引擎使用。原 `run_verification` 保留兼容（内部委托给 detailed）。

### main.py 接入

验证失败链路改造：

```
验证失败 → 尝试反思改进（SelfReflectionEngine）
    ├─ 改进版补丁通过验证 → 提交待应用（不立即回退，迭代式进化）
    └─ 反思无效/不可用 → 走原自动回退流程（rollback_last）
```

---

## 四、能力差距最终对照

| 差距维度 | 阶段A前 | 阶段A后 | 阶段B+C后 |
|---------|--------|---------|----------|
| 问题发现 | 6类规则 | +LLM开放域 | 同左 |
| 补丁生成 | 规则miss即放弃 | LLM单文件 | +跨文件补丁包 |
| 跨文件修改 | ❌ | ❌ | ✅ 改接口同步迁移调用方 |
| 多步规划 | ❌ | ❌ | ✅ 复杂任务拆解 |
| 自我反思 | ❌ | ❌ | ✅ 失败分析+改进+重试 |

**至此，三阶段路线图全部落地。** 曈曈从「规则驱动自修复」跨越到「LLM 驱动的开放域智能进化」。

---

## 五、验证结果

| 验证项 | 结果 |
|--------|------|
| 全部改动文件语法编译 | ✅ 通过 |
| 完整导入链（evolution 9 模块 + SelfVerifier） | ✅ 通过 |
| `LLMEvolutionEngine` JSON 解析（跨文件补丁 + 多步计划） | ✅ 通过 |
| `SelfReflectionEngine` 反思补丁解析 | ✅ 通过 |
| 无 API key 优雅降级（两个引擎） | ✅ 通过 |
| `run_verification_detailed` 兼容性 | ✅ 通过 |
| `verify_patch_coverage.py` 回归测试 | ✅ 12/12 通过 |
| `main.py` 可导入 | ✅ 通过 |

---

## 六、涉及文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `nucleus/evolution/LLMEvolutionEngine.py` | 新建 | 跨文件补丁 + 多步规划 |
| `nucleus/evolution/SelfReflectionEngine.py` | 新建 | 失败后自我反思 |
| `nucleus/reasoning/SelfVerifier.py` | 修改 | 新增 run_verification_detailed |
| `nucleus/reasoning/SafeEvolutionExecutor.py` | 修改 | 单文件失败标注跨文件升级点 |
| `main.py` | 修改 | 验证失败接入反思改进 |
| `nucleus/evolution/__init__.py` | 修改 | 导出新模块 |

---

## 七、待办与注意事项

1. **本地运行验证**（待执行）：需配置 `TTP_REMOTE_API_KEY` 后，B/C 能力才能真正生效（跨文件补丁、反思改进都依赖 LLM）。未配置时优雅降级。
2. **反思重试上限**：当前反思改进只尝试一次（改进版补丁验证失败则回退）。未来可加「最多反思 N 次」的循环，但需防死循环。
3. **跨文件补丁的信任门槛**：当前 `trust_score=50` 低于 `auto_apply_min_trust=60`，意味着跨文件补丁默认**不会自动批准**（需人工审批），这是有意的保守设计。若要放开，可调低 `auto_apply_min_trust` 或提高跨文件补丁的信任分。
4. **资产空间同步**（待执行）：本地改动尚未对比替换资产空间对应文件。
