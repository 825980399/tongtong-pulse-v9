# 框架熟悉分析记录：ChannelQuotaMonitor额度监控与P2-194/P2-246修复

**分析时间**：2026-09-15
**分析人**：星轨（定时修复过程中精读）
**文件**：`nucleus/llm/ChannelQuotaMonitor.py`（410行）、`tests/test_quota_monitor_m32.py`（280行）
**问题**：P2-194（_usage无上限积累已移除渠道）、P2-246（渠道名显示为"ch"，调查未复现）

## 一、文件基本信息

- **路径**：`nucleus/llm/ChannelQuotaMonitor.py`
- **行数**：410行（修复后）
- **功能**：渠道免费额度监控与自动切换
- **核心类**：`ChannelQuotaMonitor`（进程级单例）
- **定位**：LLM渠道治理层

## 二、核心机制

### 2.1 额度统计
- `record_usage(channel_name, prompt_tokens, completion_tokens)`：每次调用后登记token用量
- 从响应体 `usage` 取 `prompt_tokens + completion_tokens` 累加
- 持久化到 `data/channel_quota_usage.json`（进程重启不丢）
- 写盘按30秒间隔节流（`SAVE_MIN_INTERVAL`）

### 2.2 自动策略
- **预警**：剩余 < 20% → WARNING预警（`QUOTA_ALERT_RATIO`）
- **降级**：剩余 < 10% → 优先级 +3（后置）（`QUOTA_DEGRADE_RATIO`）
- **暂停**：剩余 < 5% → 从渠道池剔除（`QUOTA_PAUSE_RATIO`）
- **恢复**：`FORCE_ENABLE_CHANNELS` 白名单可强制放行

### 2.3 日志机制（第37批T4修复P2-228）
- 预警/告警用 WARNING
- 暂停/恢复状态切换用 INFO（`_event_once`，与 `_warn_once` 语义分工）
- 全部经 `_module_logger` 落 `logs/pulse.log`

### 2.4 与熔断独立
- 本模块只依据 token 配额，不感知 circuit_break
- 两者状态互不影响
- 额度暂停的渠道不会被重试（熔断300s后仍会重试）

## 三、P2-194修复：已移除渠道条目清理

### 3.1 问题根因
- `_load()` 从磁盘加载所有历史渠道记录，包括已从配置中移除的渠道
- `save()` 会把所有 `_usage` 中的条目落盘，包括已移除的渠道
- 长期运行且渠道动态增删时，会积累已移除渠道的历史条目
- 导致 `channel_quota_usage.json` 无限增长

### 3.2 修复方案
在 `_load()` 方法中添加清理逻辑：
1. 加载完成后，获取当前配置中的活跃渠道名列表（`_active_channel_names()`）
2. 过滤 `_usage`，只保留在配置中的渠道
3. 同时清理 `_warned` 和 `_events` 中对应的条目
4. 设置 `_dirty = True`，确保下次save落盘清理后的结果
5. 记录DEBUG日志说明清理了多少个渠道

### 3.3 新增方法
`_active_channel_names() -> set[str]`：
- 从 `config.REMOTE_API_CHANNELS.default_channels` 获取所有活跃渠道名
- 异常时返回空set（不清理，保守策略）

### 3.4 测试覆盖
新增 `TestStaleChannelCleanup` 类，4个测试：
1. `test_01_stale_channel_removed_on_load`：验证已移除渠道被清理，活跃渠道保留
2. `test_02_dirty_flag_set_after_cleanup`：验证清理后_dirty=True
3. `test_03_no_stale_no_cleanup`：验证无已移除渠道时不触发清理
4. `test_04_save_after_cleanup_removes_stale_from_disk`：验证清理后save落盘，磁盘文件不再包含已移除渠道

## 四、P2-246调查：渠道名显示为"ch"（未复现）

### 4.1 调查过程
1. 检查 `ChannelQuotaMonitor.py` 中所有日志输出：均使用 `_n` 或 `name`（从 `channel.get("name")` 取），无硬编码"ch"
2. 检查 `PulseLung.py` 调用链：`_call_channel` 中 `_name = channel.get("name", "?")`，正确传递给 `_m32_record_quota_usage`
3. 检查 `config.py` 渠道配置：所有渠道名正常（ark-seed-21-turbo、ark-ds-v4-flash、zhipu、deepseek等）
4. 检查 `data/channel_quota_usage.json`：记录的渠道名正常
5. 全库搜索 `"ch"` 硬编码、`for ch in channels` 直接输出、渠道名映射/缩写逻辑：均未发现
6. 检查 `call_pattern_analyzer.py`：从记录取 `r.get("channel")`，正确

### 4.2 调查结论
**P2-246未复现**。可能的原因：
1. 问题已在之前的批次中修复（如第37批T4修复P2-228时重构了日志输出）
2. 问题描述不精确，"ch"可能是某个特定场景下的临时显示
3. 问题出在已删除/重构的旧代码中

### 4.3 处理方式
将P2-246标记为"未复现/已修复"，记录调查结果，不再作为待修复债务。

## 五、设计意图理解

1. **为什么用文件判重而不是内存状态？**
   - 进程重启后仍能判断当天是否已执行
   - 多进程场景下也能正确判重

2. **为什么额度监控与熔断独立？**
   - 额度是"没钱了"，熔断是"服务不稳定"，两者原因不同
   - 独立判断避免互相干扰：额度耗尽的渠道不应被熔断重试浪费时间

3. **为什么清理在_load而不是save？**
   - 加载时清理一次，后续运行时不需要每次都检查
   - 清理后_dirty=True，自然会在下次save时落盘
   - 不影响record_usage的性能（运行时不做额外检查）

4. **为什么_active_channel_names异常时返回空set？**
   - 保守策略：无法确定活跃渠道时，不清理任何条目
   - 避免误删有用的用量记录

## 六、经验教训

1. **定时任务选择目标前必须验证问题是否真的存在**：P2-246经过全面调查未复现，避免了无效修复
2. **持久化数据需要考虑增长控制**：长期运行的系统，任何无上限的字典/列表都可能成为内存/磁盘问题
3. **清理逻辑要保守**：无法确定时不清理，避免误删有用数据
4. **测试要覆盖清理后的落盘验证**：不仅验证内存状态，还要验证磁盘文件已更新


---

## 七、P2-193修复：FORCE_ENABLE_CHANNELS白名单TTL机制

### 7.1 问题根因

FORCE_ENABLE_CHANNELS 白名单没有TTL（生存时间），如果某渠道额度确已耗尽而白名单长期不清，会持续占用白名单名额。用户可能临时把耗尽渠道加入白名单测试，但忘记移除。

### 7.2 修复方案

1. **新增配置**：FORCE_ENABLE_TTL_HOURS = 0（默认0=永久有效，向后兼容）
2. **新增内存字典**：_force_enable_ts: dict[str, float]，记录每个白名单渠道的首次加入时间
3. **修改_force_enabled()**：
   - TTL=0时直接返回配置中的白名单（永久有效）
   - TTL>0时，过滤掉超过TTL的渠道
   - 过期时调用_event_once记录INFO日志（去重）
   - 无论配置是否为空，都清理不在配置中的渠道的时间戳

### 7.3 重要发现：嵌套锁死锁

**修复过程中发现并解决了一个死锁问题**：
- _force_enabled() 内部使用 with self._lock
- _event_once() 内部也使用 with self._lock
- 	hreading.Lock 是不可重入的，在持有锁的情况下再次获取同一个锁会导致死锁

**解决方案**：将 _event_once 调用移到锁外部。先在锁内收集需要过期的渠道列表，退出锁后再调用 _event_once。

**经验教训**：在使用 	hreading.Lock 的类中，调用其他也使用同一个锁的方法时，必须确保不在锁内部调用，否则会导致死锁。如果需要嵌套调用，应使用 	hreading.RLock（可重入锁）。

### 7.4 测试覆盖

新增 TestForceEnableTTL 类，4个测试：
1. 	est_01_ttl_zero_means_permanent：TTL=0时白名单永久有效
2. 	est_02_ttl_positive_keeps_recent：TTL>0时新加入渠道在TTL内有效
3. 	est_03_ttl_expired_removed：超过TTL的渠道被过滤
4. 	est_04_removed_channel_clears_timestamp：从配置移除后时间戳被清理

### 7.5 设计决策

- **为什么用内存时间戳而不持久化？**：重启后重新计算TTL是合理的——重启相当于重新开始白名单周期，且实现更简单
- **为什么默认TTL=0？**：向后兼容，不改变现有行为
- **为什么用_event_once而不是直接日志？**：避免每轮apply_to_channels都打印过期日志，_event_once去重确保只打印一次


---

## 八、P2-192修复：额度监控本地计数偏差安全系数

### 8.1 问题根因

`ChannelQuotaMonitor` 的用量为**本地累计**：若上游对同一接入点存在其他调用方（白名单外的进程/人工控制台），本地计数会**低于真实消耗**，导致：
- 剩余比例偏高
- 预警/降级/暂停时机偏晚
- 可能在真实额度耗尽后仍继续调用，导致API报错

### 8.2 修复方案

1. **新增配置**：`QUOTA_USAGE_SAFETY_MARGIN = 1.0`（默认1.0=不调整，向后兼容）
2. **新增`safety_margin()`属性**：读取配置项，异常时返回1.0
3. **修改`get_remaining_ratio()`**：用量乘以安全系数 `_used = float(self.get_used(channel_name)) * self.safety_margin()`
4. 设置>1.0（如1.1）可提前预警/降级/暂停，补偿本地计数偏差

### 8.3 设计决策

- **为什么用乘法而不是加法？**：乘法更符合"比例偏差"的语义——本地计数是真实消耗的X%，乘以安全系数相当于放大用量
- **为什么默认1.0？**：向后兼容，不改变现有行为；用户可根据实际偏差调整
- **为什么不直接修改阈值？**：修改阈值（如把暂停阈值从5%提高到10%）会改变所有渠道的行为，而安全系数可以针对本地计数偏差进行补偿，更精确
- **建议值**：如果观察到本地计数比真实消耗低10-20%，建议设置为1.1-1.2

### 8.4 测试覆盖

新增 `TestSafetyMarginP192` 类，5个测试：
1. `test_01_default_safety_margin_is_one`：默认安全系数为1.0
2. `test_02_safety_margin_greater_than_one_reduces_remaining`：安全系数>1.0时剩余比例偏低
3. `test_03_safety_margin_affects_exhausted`：安全系数影响is_exhausted判定
4. `test_04_safety_margin_config_override`：配置项可覆盖默认值
5. `test_05_safety_margin_does_not_affect_unlimited`：安全系数不影响不限量渠道

### 8.5 与P2-193/P2-194的关系

P2-192、P2-193、P2-194都是ChannelQuotaMonitor的改进：
- P2-192：本地计数偏差 → 安全系数补偿
- P2-193：白名单无TTL → 增加TTL机制（第9次修复）
- P2-194：无用条目积累 → 定期清理（第8次修复）

三个问题共同构成了额度监控的完整治理。
