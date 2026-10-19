# 第98批任务书：SSRF 防护 + 孤儿脉冲 + PulseLegs 复核

> 生成：星轨（2026-09-21 下午）
> 执行：路灯
> 优先级：P0（T-98a）+ P1（T-98b/c）
> 红线：不改 config.py 运行开关（只加新开关）/ 不写 data\knowledge\ / 改前备份 .bak_batch98/
> 是否需要停框架：❌ 不需要，下次重启生效

---

## 背景

第97批交付完成，重启后框架运行正常。本批针对性解决运行日志分析发现的三个问题：
1. SSRF 防护误拦火山渠道（导致渠道池失败率 33%）
2. 孤儿脉冲积累（代码学习 digest.knowledge 事件无人接收）
3. PulseLegs 假成功复核（烛微新发现）

---

## T-98a（P0）：SSRF 防护误拦火山渠道

### 现状
运行日志发现：
- 火山方舟域名 `ark.cn-beijing.volces.com` 被解析到**内网 IP** `192.168.50.86`
- SSRF 防护认为是非公网地址，直接拦截
- 导致多个火山渠道全部不可用：
  - ark-ds-v4.1-flash
  - ark-glm-5.3-flash
  - ark-seed-21-pro
  - ark-ds-v4-pro
  - ark-seed-21-turbo
  - ark-seed-evolving
- 渠道失败率 33%（最近1小时）
- 只有 zhipu（智谱）和 ark-glm-5.2 能用

### 改法
1. **定位 SSRF 防护代码**：
   - 找到 SSRF 防护的实现位置
   - 理解为什么会把火山域名解析到内网 IP
2. **方案选择（三选一）**：
   - **方案A（推荐）**：SSRF 防护白名单加火山方舟域名/IP
   - **方案B**：排查 DNS 解析问题，为什么火山域名被解析到内网
   - **方案C**：SSRF 防护对 API 域名豁免
3. **验证**：
   - 火山渠道调用不再被 SSRF 防护拦截
   - 渠道池成功率提升
   - 既有单测全绿

### 验收
- 火山渠道调用正常，不再被 SSRF 防护拦截
- 渠道池成功率从 67% 提升到 90%+
- 既有单测全绿

---

## T-98b（P1）：孤儿脉冲（代码学习 digest.knowledge 无人接收）

### 现状
运行日志分析发现：
- `pulse_orphans.json` 已满 100 条
- **88%** 来自代码学习器官发射的 `digest.knowledge` 事件
- 错误类型：「孤儿脉冲: 发射后无器官接收」
- 其他少量孤儿：能量代谢、叙事自我、心脏等

### 改法
1. **定位问题**：
   - 代码学习在哪里发射 `digest.knowledge` 事件
   - 为什么没有器官订阅这个事件
2. **方案选择（三选一）**：
   - **方案A（推荐）**：加消费方——让肝/记忆系统订阅 `digest.knowledge` 事件
   - **方案B**：不发射——代码学习不要走事件总线，直接内部调用
   - **方案C**：降级为 DEBUG 日志——不再记录为孤儿脉冲
3. **清理历史孤儿**：
   - 重置 `pulse_orphans.json`（或清空）
4. **验证**：
   - 孤儿脉冲不再积累
   - 既有单测全绿

### 验收
- `digest.knowledge` 事件有消费方，不再是孤儿
- 孤儿脉冲不再持续积累
- 既有单测全绿

---

## T-98c（P1）：PulseLegs 假成功复核

### 现状
烛微校准报告新发现：
- 烛微探针命中的 2 条假成功：
  - PulseLegs._write_learn_log（09-11）
  - PulseInnerWorld._safe_eval_arithmetic（09-20）
- 路灯 T-96c 修正的 2 条：
  - PulseInnerWorld._safe_eval_arithmetic
  - PulseLung._get_channel_semaphore
- 差异：**PulseLegs._write_learn_log 至今 effect_verified=True 未修正**

### 改法
1. **复核 PulseLegs._write_learn_log**：
   - 用烛微的探针算法（modified_code 新增行不在磁盘 且 original_code 在场）
   - 确认是否确实是假成功
2. **如果是假成功**：
   - 补一条更正（effect_verified: True → False）
   - 写更正留痕
3. **验证**：
   - 复核结果准确
   - 既有单测全绿

### 验收
- PulseLegs._write_learn_log 是否为假成功，已确认
- 如果是假成功，已补更正
- 既有单测全绿

---

## 门禁要求
1. ruff F 全项目 = 0
2. py_compile 全部改动文件通过
3. 相关 pytest 无新增失败
4. 改前备份 .bak_batch98/
5. 交付报告含：根因定位 + diff + 先红后绿证据

## 注意事项
- T-98a 是 P0，直击渠道池失败率高的问题
- T-98b 是孤儿脉冲积累问题
- T-98c 是烛微新发现的假成功
- 改完不需要立即重启，等本批交付后统一重启验收
- 框架正在运行中，不需要停止
- 烛微第2期审计报告还没出来，等出来后再追加任务
