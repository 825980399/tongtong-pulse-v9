# 第94批任务书：待审批老化策略 + tokens打通 + 依赖度分层

> 生成：星轨（2026-09-20 深夜）
> 执行：路灯
> 优先级：P0（T-94a）+ P1（T-94b/c）
> 红线：不改 config.py 运行开关（只加新开关）/ 不写 data\knowledge\ / 改前备份 .bak_batch94/

---

## 背景

第93批只读分析完成，三大核心发现：
1. 自学习修复率 2.38%，主因 = 待审批死循环 28.6%
2. LLM 留存管道已落地，但 tokens 恒 0（adapter 不解析 usage）
3. 依赖度 97% 是假的——真实全栈依赖度 12.72%（口径过窄）

本批针对性解决这三个问题。

---

## T-94a（P0）：待审批队列老化策略（直击 28.6% 主因）

### 现状
第93批诊断发现：
- 机制：补丁生成后入 pending，等人工裁决
- 问题：进化循环是分钟级连续运行的，人工裁决速率远低于生成速率
- 后果：pending 只增不减，同一问题每轮重复发现，计入未修复分母

### 改法
1. **pending 队列老化策略**：
   - pending 超过 N 条（建议 20 条）或超过 T 小时（建议 24 小时）未裁决
   - 对满足以下条件的补丁自动放行：
     - source = local_rule（本地规则来源）
     - 非核心文件（排除 organs/brain/PulseInnerWorld.py 等上帝文件）
     - 已通过验证（runtime_verified = true）
     - 风险等级 = 低
2. **开关控制**：`ENABLE_PENDING_QUEUE_AGING`，默认关闭
3. **冷却表调整**：「已有待审批」计时器改为按补丁而非按问题位置

### 验收
- 老化策略用例覆盖：超阈值放行 / 未超阈值不放行
- 自动放行条件校验：local_rule + 非核心 + 已验证
- 默认关闭时零行为变化

---

## T-94b（P1）：LLM tokens 打通（adapter 解析 usage）

### 现状
第93批诊断发现：
- 根因：`openai_compatible_adapter.py:57 parse_response` 只取 content，不解析 usage
- 后果：tokens 恒 0，无法统计真实用量和成本

### 改法
1. **adapter 层**：
   - BaseLLMAdapter 新增可选方法 `extract_usage(response) -> dict | None`
   - OpenAICompatibleAdapter 实现：返回 `{prompt_tokens, completion_tokens, total_tokens}`
   - 不改 parse_response 签名（零回归），只新增方法
2. **调用层**：
   - LLM 调用出口拿到 (text, usage)，把 usage 传给 record()
   - tokens 字段 = usage.total_tokens（向后兼容）
3. **留存器**：
   - record() 新增可选参数 `usage: dict | None = None`
   - 写入新增字段 "usage"

### 验收
- adapter 用例覆盖：有 usage / 无 usage 两种情况
- 向后兼容：无 usage 时 tokens 仍写 0
- 既有调用点零回归

---

## T-94c（P1）：依赖度口径修正（重命名 + 分层报告）

### 现状
第93批诊断发现：
- 口径明确 = 按调用次数 `llm/(llm+local)`
- 缺陷一：分母过窄，排除了 search(774) + digestion(36032)
- 缺陷二：total_requests 命名误导（实际只含 2 类）

### 改法（方案 C + 方案 A）

**方案 C：重命名消除歧义**
1. `total_requests` 重命名为 `answer_requests`（实际 = llm + local）
2. 保留旧字段一个版本做兼容

**方案 A：分层报告（新增指标，不改现有值）**
1. 新增 `overall_llm_share`：`llm_total / (llm_total + local_total + search_total + digestion_total)`
   - 语义：全栈大模型占比
   - 实测值：12.72%
2. 新增 `evolution_local_rule_rate`：本地规则修复数 / 总修复数
   - 语义：自学习闭环的本地化成效
   - 实测值：95.4%

### 验收
- 重命名用例：旧字段兼容 / 新字段存在
- 分层报告用例：新指标公式正确
- 现有指标值不变（零行为变化）

---

## 门禁要求
1. ruff F 全项目 = 0
2. py_compile 全部改动文件通过
3. 相关 pytest 无新增失败
4. 改前备份 .bak_batch94/
5. 交付报告含：根因定位 + diff + 先红后绿证据

## 注意事项
- T-94a 是 P0，直击 28.6% 主因
- T-94b/c 是 P1，指标和数据完善
- 改完不需要立即重启，等本批交付后统一重启验收
