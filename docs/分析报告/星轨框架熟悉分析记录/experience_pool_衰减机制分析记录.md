# 框架熟悉分析记录：experience_pool.py 衰减机制与容量保护

**分析时间**：2026-09-15
**分析人**：星轨（定时修复过程中精读）
**文件**：`nucleus/mnemosyne/experience_pool.py`（约900行，35.6KB）

## 一、文件基本信息

- **用途**：经验池管理，存储和管理框架运行过程中产生的经验记录
- **核心类**：ExperiencePool（经验池）
- **关键机制**：容量保护、情绪衰减、摘要压缩、污染治理

## 二、核心机制理解

### 2.1 容量保护（_trim_capacity）
- **触发时机**：每次写入经验后调用
- **功能**：当经验数量超过容量上限时，移除多余的已摘要经验
- **位置**：experience_pool.py:212附近
- **第49批增强**：在容量保护中接入了check_decay()调用

### 2.2 情绪衰减检查（check_decay）
- **定义位置**：experience_pool.py:374
- **功能**：定期执行情绪衰减检查，对非高强度情绪体验执行衰减
- **衰减阈值**：情绪强度低于0.05时自动调用_summarize_experience()进行摘要压缩
- **检查间隔**：_decay_check_interval（3600秒/1小时）
- **★历史问题（P2-331）**：定义完整但全库无生产调用方，导致情绪强度<0.05的完整体验永远不会被自动压缩
- **★第49批修复**：接入_trim_capacity()，每次写入后触发（内部由3600s限流，不增热路径开销）

### 2.3 灰度开关（_m49_decay_on_trim_on）
- **位置**：experience_pool.py:269附近
- **功能**：控制是否在_trim_capacity中调用check_decay
- **关闭行为**：回到check_decay从不被调用的旧行为
- **设计意图**：允许回滚到修复前的状态，便于对比和问题定位

### 2.4 锁安全设计
- **锁类型**：threading.RLock()（可重入锁）
- **关键设计**：在持锁的record_experience内调用_trim_capacity，再调用check_decay不会死锁
- **原因**：RLock允许同一线程多次获取锁，而普通Lock会死锁

### 2.5 摘要压缩（_summarize_experience）
- **位置**：experience_pool.py:241附近
- **功能**：将完整体验压缩为摘要，保留raw_summary
- **★与SERP污染的关系**：83.1%的污染源在维护侧check_decay()的_summarize_experience()
- **★P0-3止血**：必须先改摘要机制（保留raw_summary），再清洗，否则清洗会再生

## 三、与其他模块的交互

- **写入方**：多个器官在产生经验时调用record_experience()
- **读取方**：L3检索层、自认知模块、决策模块
- **维护方**：_trim_capacity（容量保护）、check_decay（衰减检查）、_summarize_experience（摘要压缩）
- **测试方**：test_log_decay_robustness_m49.py、test_experience_summary_hemostasis_m47.py

## 四、发现的问题/待确认项

1. **★文档漂移已修正**：P2-331 check_decay死代码已在第49批修复，但技术债务清单规划表仍列为待修复（本次定时任务已修正）
2. **SERP污染根因**：83.1%的污染来自check_decay()调用的_summarize_experience()，需要先改摘要机制再清洗
3. **灰度开关命名**：_m49_decay_on_trim_on()使用批次号命名，长期看可能需要更通用的命名方式
4. **衰减间隔配置**：_decay_check_interval=3600秒是否合理，需要根据实际运行数据调整

## 五、本次分析记录

- **原计划**：决策check_decay()是修复还是下线
- **实际发现**：已在第49批修复（接入_trim_capacity），技术债务清单存在文档漂移
- **成果**：修正文档漂移，避免重复修复；深入理解了experience_pool的衰减机制和容量保护设计
