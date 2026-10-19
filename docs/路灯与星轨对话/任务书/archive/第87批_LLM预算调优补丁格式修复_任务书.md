# 第87批任务书：LLM修复预算调优 + 补丁格式修复 + 验证学习枢纽刷屏

> 生成：星轨（2026-09-20 上午）
> 执行：路灯
> 优先级：P0（T-87a/b）+ P2（T-87c）
> 红线：不改 config.py 开关 / 不写 data\knowledge\ / 改前备份 .bak_batch87/

---

## T-87a（P0）：LLM修复 max_tokens 继续上调

### 现状
第86批已将 _LLM_REPAIR_MAX_TOKENS 从 1500 提至 8192，但实测仍不够：

`
[LLM修复] 模型返回 content 为空（finish_reason=length, completion_tokens=8191, reasoning_content=24524字, max_tokens=8192）
——疑似推理预算被耗尽，请上调 _LLM_REPAIR_MAX_TOKENS
`

推理模型（deepseek-v4-flash）的 reasoning_content 约 24000-25000 字，8192 tokens 全部被推理吃光，content 仍为空。

### 改法
1. SafeEvolutionExecutor.py:44 _LLM_REPAIR_MAX_TOKENS = 8192 → **16384**
2. 同步确认 _LLM_REPAIR_MIN_TIMEOUT = 120 是否够用（推理+生成可能需要更长时间，必要时提至 180s）
3. 先红后绿：改前复跑 test_llm_repair_path_m86.py 确认 8192 时的失败模式，改后确认 16384 时 content 非空

### 验收
- 日志不再出现 inish_reason=length + completion_tokens=8191
- 出现 inish_reason=stop + content 非空
- [自主修复] 恢复打印，修复率脱离 0%

---

## T-87b（P0）：PatchManager 补丁格式问题

### 现状
LLM 返回的补丁被 PatchManager 拒绝：

`
[PatchManager] WARNING: [补丁验证] 路径沙箱拒绝: 补丁缺少 file 字段
`

### 定位要求
1. 找到 LLM 生成补丁的代码位置（SafeEvolutionExecutor 或 PatchManager）
2. 确认 LLM 返回的补丁 JSON 结构是什么样的
3. 确认 PatchManager 期望的 ile 字段应该是什么格式
4. 判断是 LLM prompt 没要求 file 字段，还是解析时丢了

### 改法方向（定位后定）
- 如果是 prompt 问题：在 LLM prompt 中明确要求返回 ile 字段
- 如果是解析问题：在解析层补默认值或做格式校验
- 最小改动原则，不重构整个补丁生成流程

### 验收
- 不再出现 路径沙箱拒绝: 补丁缺少 file 字段
- LLM 返回的补丁能正常进入待审批队列

---

## T-87c（P2）：verification_learning_hub DEBUG刷屏（第86批遗漏）

### 现状

ucleus\mnemosyne\verification_learning_hub.py:170-171 的 DEBUG 日志刷屏：

`python
self._log(LogLevel.DEBUG,
    f"[验证决策校验] {organ}/{task_type} 置信度={confidence:.2f} ...")
`

### 改法
将这条 DEBUG 日志降级（或改为采样打印，如每 100 次打印一次），不影响调试但减少刷屏。

### 验收
- 运行日志中 [验证决策校验] 出现频率下降 90% 以上
- 不影响功能逻辑

---

## 门禁要求
1. ruff F 全项目 = 0
2. py_compile 全部改动文件通过
3. 相关 pytest 无新增失败
4. 改前备份 .bak_batch87/
5. 交付报告含：根因定位 + diff + 先红后绿证据

## 注意事项
- T-87a 是核心：LLM修复路径现在能跑通但预算不够，这是修复率 0% 的直接原因
- T-87b 是下游：预算够了之后补丁格式问题会更明显，一起修
- 改完不需要立即重启，等本批交付后统一重启验收

---

## 交付结论（路灯 · 2026-09-20）

**结果：3/3 子任务落地，五项门禁全绿。**

交付报告：`交付报告/已分析/2026-09-20_主线第87批_LLM预算调优补丁格式修复_交付报告.md`

| 子任务 | 任务书判断 | 实测 | 落地（文件:行号） |
|---|---|---|---|
| **T-87a** | 「8192 仍不够」 | ✅ **成立**（日志逐字确证：`completion_tokens=8191` / `reasoning_content≈24524字` / `finish_reason=length`，最近 09-20 09:02:42） | `SafeEvolutionExecutor.py:44` `16384`、`:49` `180` |
| **T-87b** | 「prompt 没要求 file / 解析丢了」 | ❌ **两者均不成立** → 真因：日志类问题的 `issue["file"]` 本就是空串（`resolve_organ_file` 只覆盖 `organs/`，对 `PulseSnapshot`/`PatchManager`/`InfoField` 等非器官标签恒返 None） | 二级反查 `SafeEvolutionExecutor.py:1240`、file 必填校验 `:1469`、告警补上下文 `PatchManager.py:776` |
| **T-87c** | 「降级 DEBUG」 | ⚠️ 该日志**已是 DEBUG 级** → 改**采样** | `verification_learning_hub.py:30`（常量）、`:45`（计数）、`:186`（判定），噪声 −98.8% |

**先红后绿**：新增门控 `tests/test_llm_budget_patch_format_m87.py`：`8 failed, 2 passed` → **`10 passed`**。

**门禁**：ruff F 全库 **0** ｜ py_compile **全过** ｜ 导入冒烟 **4/4** ｜
分层回归 **394 passed / 1 skipped / 0 failed**（片A 128、片B 131+1skip、片C 135）｜
`verify_phase17_1_5` **43 PASS / 0 FAIL** ｜ 行尾复核：主源码 CRLF 保持、新测试 LF。

**备份**：`.bak_batch87/` 3 文件 / 444,725 B / `COPY_FAIL=0`。
改动量（`difflib` vs 备份）：**+110 / −14**。

**任务书偏差（第 31 次记录）**：① T-87a 点名用于先红的 `test_llm_repair_path_m86.py`
在 8192 下**本就通过**（只断言 `>= 4096`），无法承载"先红"；② T-87b 的
「LLM 返回的补丁 JSON 结构」前提不成立（LLM 只返回代码片段，补丁 dict 由框架构造）；
③ T-87b 猜测的两种原因均不成立；④ T-87c 该日志已是 DEBUG 级。

**越界改动（显式标注，可回退）**：顺手修掉 `SafeEvolutionExecutor.py` 内 **2 处既有**
静默 `except: pass`（源自第 85 批 `_m85_conservative_fix`；`.bak_batch87` 复算确认改前即 2 处，
仅行号平移 +54）→ 使 `tests/test_robustness_m7.py::test_core_files_no_silent_except_pass`
由红转绿。修法为零行为变化的"补 DEBUG 留痕"。

**待星轨裁决**：① 新增默认开启的灰度开关 `ENABLE_NONORGAN_FILE_RESOLVE`
（走 `getattr` 兜底、未写入 `config.py`）是否要求默认 `False`？
② 是否把 `_file` 一并回填（可换取真实代码片段作 LLM 素材，代价是 PHASE13 两项统计口径变化）？
③ 16384 是否足够无法离线验证 —— 若重启后仍见 `finish_reason=length`，
建议下一批改 prompt 策略或引入 `reasoning_effort` 控制。

⚠️ **本批全部为 `.py` 改动 → 需重启框架验收**（运行期验收清单见交付报告 §9）。
