# 第90批任务书：字段契约修复 + 日志定位bug修复

> 生成：星轨（2026-09-20 下午）
> 执行：路灯
> 优先级：P0（T-90a）+ P1（T-90b）
> 红线：不改 config.py 开关 / 不写 data\knowledge\ / 改前备份 .bak_batch90/

---

## 背景

第89批重要发现：
- ★ 主链路不传 original_code，_verify_in_copy 关3形同虚设
- 补丁库 [0.3,0.5) 命中 0 条，降阈值历史收益=0
- T-89b 设计：日志调用点定位覆盖率 77.4%，附带查出 4 条既有 bug

---

## T-90a（P0）：_llm_patch 补 original_code 字段

### 现状
主链路 SafeEvolutionExecutor.repair_with_distillation 构造 _llm_patch 时，
根本没有传 original_code / modified_code 字段
（_generate_patch 返回的键是 pplied_strategy / modified_code / diff_summary / …）。

⇒ _verify_in_copy 关3 读取到的是空字符串，完整性关形同虚设。

### 改法
1. 找到主链路构造 _llm_patch 的位置
2. 补上 original_code 字段（值 = 素材提取时的 _snippet）
3. 确认 modified_code 字段是否也需要补
4. 先红后绿：改前确认 original_code 为空，改后确认非空

### 验收
- _verify_in_copy 关3 能读到真实 original_code
- 补丁验证不再形同虚设

---

## T-90b（P1）：日志定位 4 条既有 bug 修复（T-89b 第一期）

### 现状
T-89b 设计文档查出 4 条既有 bug：

1. LogAnalyzer.py:199-210 —— 调用定位时 message 还是空串（填充在其后两行），
   且 _locate_attempted 使定位只试一次永不重试
2. self_inspector.py:1382-1392 1c —— 只用中文器官名索引（243个），
   而真实日志标签多数是类名（PatchManager/InfoField/…）⇒ 恒不命中；
   第87批建好的全项目类索引（1007类）没接上来
3. 中文标签匹配写的是前缀关系，「胃」vs「脉冲驱动胃」不命中（需改包含）
4. LogAnalyzer.py:226 取 Traceback 第一帧 = 调用层而非致错点

### 改法
按 T-89b 设计文档分期：
- 本期只修 4 条 bug（低风险）
- 不改主能力（日志调用点定位），主能力放到第91批

### 验收
- 4 条 bug 全部修复
- 日志定位覆盖率提升（从当前水平到 50%+）

---

## 门禁要求
1. ruff F 全项目 = 0
2. py_compile 全部改动文件通过
3. 相关 pytest 无新增失败
4. 改前备份 .bak_batch90/
5. 交付报告含：根因定位 + diff + 先红后绿证据

## 注意事项
- T-90a 是核心：字段契约修好后，完整性关才真正生效
- T-90b 是 T-89b 第一期，只修 bug 不上新能力
- 改完不需要立即重启，等本批交付后统一重启验收