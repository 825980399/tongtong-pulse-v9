# PULSE-DEFECT-20260901-01 修复归档报告

档案编号：PULSE-DEFECT-20260901-01
缺陷：补丁落库缺少「验证失败不落库」保护，LLM 残缺补丁混入审批队列
发现日期：2026-09-01 ｜ 修复日期：2026-09-01
影响模块：`organs/brain/PulseCodeLearner.py`、`nucleus/reasoning/PatchManager.py`

---

## 一、修复结论

| 方案 | 内容 | 状态 |
|------|------|------|
| 方案 A（核心缺陷） | 统一「验证通过才落库」 | ✅ 已修复 |
| 方案 B（完整性护栏） | `verify_in_copy` 第一关增加三关静态护栏 | ✅ 已落地 |

## 二、方案 A 修复详情

**文件**：`organs/brain/PulseCodeLearner.py`（原 2283-2296 行）

**修复前缺陷**：
1. 自动应用分支：`verify_in_copy` 失败仍调用 `save_pending_patch` 落库
2. 总开关关闭分支：跳过验证直接落库

**修复后逻辑**：所有补丁一律先 `verify_in_copy`，通过才落库，失败只记日志丢弃：

```python
_verify_result = _patch_mgr.verify_in_copy(_patch)
if _verify_result.get("passed"):
    if _auto_apply_enabled and isinstance(_risk, int) and _risk <= 1:
        _patch["auto_applied"] = True
        _patch_mgr.save_pending_patch(_patch)
        _auto_applied += 1
    else:
        _patch_mgr.save_pending_patch(_patch)
        _pending_count += 1
else:
    self._log(LogLevel.WARNING, f"补丁验证失败已丢弃: {_patch.get('id','')[:16]}... → {_errs}")
```

**附带修正**：`_auto_applied`/`_pending_count` 计数语义——只有「验证通过 + 自动应用开」计 `_auto_applied`，验证通过但需审批计 `_pending_count`，验证失败不计入任何队列。

## 三、方案 B 完整性护栏详情

**文件**：`nucleus/reasoning/PatchManager.py`

**设计**：护栏集成到 `_verify_in_copy` 开头（验证第一关），所有调用 `verify_in_copy` 的路径（SafeEvolutionExecutor 两条 + PulseCodeLearner 一条）自动获得保护。

**新增方法**：`_check_llm_patch_completeness(patch) -> {complete, reason}`

三关检查：
1. **非空 + 实质差异**：`modified_code` 非空且与原文不同
2. **语法可解析**：`ast.parse(modified_code)` 通过
3. **相似度阈值**：`difflib.SequenceMatcher` 相似度 ≥ 0.5（拦截「仅剩 2%~12%」的严重残缺）

失败时 `stage = "completeness_check_failed"`，直接返回不落库。

**拦截效果**（针对档案 3 个残缺补丁）：
- `patch_llm_1788240506_2815`（相似度 0.78，语法 FAILED）→ 关 2 语法拦截
- `patch_llm_1788243399_b5e3`（相似度 0.24）→ 关 3 相似度拦截
- `patch_llm_1788270021_85c4`（相似度 0.06，仅剩 3 行）→ 关 3 相似度拦截

## 四、验证结果

- ✅ 语法检查：`PulseCodeLearner.py`、`PatchManager.py` 通过
- ✅ 方案 A 逻辑验证：验证失败不落库、计数语义正确
- ✅ 方案 B 三关验证：空修改 / 语法错误 / 严重残缺（相似度 < 0.5）均拦截，正常补丁放行

## 五、配置收编排查结论（本次附带）

1. **`auto_apply_enabled` 总开关**：已在 `config.EVOLUTION_CONFIG` 中（默认 True），且被列入 `_HOT_RELOAD_BLACKLIST`（涉及代码修改权限，禁止热改，需重启生效）——**无需迁移，现状正确**。

2. **散落开关排查**：全框架唯一散落在 config.py 之外的开关常量是 `SURVIVAL_ORCHESTRATOR_ENABLED`（已收编，剩余为兼容引用）。

3. **未收编配置块**：29 个顶层配置块未纳入 `_COVERABLE_CONFIGS`（如 SOCIAL_EMOTIONS、KIDNEY、NARRATIVE_CONFIG 等），这些多为「词表/模式/领域定义」类配置，非「开关类」，是否收编需按需决策（见下）。

## 六、后续建议

- 29 个未收编配置块中，「开关类」与「词表/定义类」应区分对待：
  - **开关类**（如 `EXTERNAL_EXECUTOR.enable_dedup`、`HEART_EMOTION_MODULATION` 等）建议逐步收编
  - **词表/定义类**（如 `SOCIAL_EMOTIONS.word_map`、`RISK_PATTERNS`）改动了也不需要热重载，可暂不处理
- 本次先完成「存续意志」体系的收编（survival_orchestrator + SELF_PRESERVATION），其余按需在后续迭代中推进。
