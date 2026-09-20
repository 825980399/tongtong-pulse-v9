# 第91批任务书：日志定位主能力 + LLM补丁语法修复 + 开关配置

> 生成：星轨（2026-09-20 晚上）
> 执行：路灯
> 优先级：P0（T-91a）+ P1（T-91b/c）
> 红线：不改 config.py 运行开关（只加新开关）/ 不写 data\knowledge\ / 改前备份 .bak_batch91/

---

## 背景

第90批交付 + 运行日志分析：
- ✅ T-90a 关0字段契约校验生效
- ✅ T-90b 四条bug修复，覆盖率 33.3% → 80.0%
- ❌ 运行日志显示 LLM补丁语法错误（unindent）
- ❌ 待审批积压 11 个（最老 68.3小时）
- ❌ 修复率仍为 0%

---

## T-91a（P0）：日志调用点定位主能力（T-90b 第二期）

### 现状
T-90b 第一期已修复 4 条 bug，覆盖率从 33.3% 提升到 80.0%。
剩余 15 个不可定位标签：
- 模块名/logger 名型：pulse、pulse.structured_parallel、wecom_chat_bridge、self_inspector 等
- 中文标签别名型：全局学习器、眼睛、胸腺、视觉皮层、触觉、耳朵 等

### 改法
1. **模块名 → 文件映射**：建立 logger 名到文件路径的映射表
2. **标签别名表**：建立中文标签到器官/模块的别名映射
3. **类名大小写归一**：self_inspector → SelfInspector 大小写不敏感匹配

### 验收
- 覆盖率从 80.0% 提升到 95%+
- 剩余不可定位标签 ≤ 3 个

---

## T-91b（P1）：LLM补丁语法错误修复

### 现状
运行日志显示：
`
补丁验证失败: 语法错误: unindent does not match any outer indentation level
`

LLM 生成的补丁有缩进错误。

### 改法
1. 定位 LLM prompt 是否要求保持原缩进
2. 如果 prompt 没要求，补上
3. 如果是 LLM 输出格式问题，考虑后处理自动修复缩进

### 验收
- LLM 补丁语法错误率下降 80%+
- 不再出现 unindent 类语法错误

---

## T-91c（P1）：开关正式写入 config.py

### 现状
三个开关都是"模块内联默认值 + getattr 兜底"：
- ENABLE_M89_PATCH_SIM_THRESHOLD（第89批）
- ENABLE_M90_PATCH_FIELD_CONTRACT（第90批）
- ENABLE_M90_LOG_LOCATE_V2（第90批）

### 改法
将三个开关正式写入 config.py，默认值保持当前（True）。

### 验收
- config.py 中能查到三个开关
- 默认值与当前一致

---

## T-91d（P2）：待审批积压处理 ✅ 已完成

### 状态：星轨已处理，路灯无需执行

### 已完成内容（2026-09-20 晚）
- 11 个待审批补丁全部分析完毕
- ✅ 7 个已自动审批应用（备份在 data\code_backups\backup_20260920_1812xx）
  - PulseKidney.py × 3（silent_exception 加日志）
  - PulseInnerWorld.py（算术计算加日志）
  - PulseLung.py（信号量加日志）
  - PulseInterestModel.py（SQL误报注释）
  - tests/test_lazy_snapshot_m9.py（测试断言修复）
- ❌ 1 个验证失败（PulseKidney.py _m69_kal_query，验证脚本问题非补丁问题）
- ❌ 3 个已删除拒绝
  - PulseLiver.py KAL 调用（LLM 生成方法名错误）
  - PulseStomach.py × 2（local_learning runtime_failed）

### 验收
- ✅ 待审批补丁分析报告（星轨已出）
- ✅ 7 个已应用，3 个已拒绝删除
- 剩余 1 个验证失败项，留待路灯第 92 批处理

---

## 门禁要求
1. ruff F 全项目 = 0
2. py_compile 全部改动文件通过
3. 相关 pytest 无新增失败
4. 改前备份 .bak_batch91/
5. 交付报告含：根因定位 + diff + 先红后绿证据

## 注意事项
- T-91a 是核心：日志定位覆盖率提升后，更多问题能被定位到文件
- T-91b 是运行日志新发现的问题
- 改完不需要立即重启，等本批交付后统一重启验收