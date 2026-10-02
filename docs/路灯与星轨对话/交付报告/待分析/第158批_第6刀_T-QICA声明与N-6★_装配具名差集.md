# 第158批 · 第6刀 T-QICA声明-1 + N-6★（P2）· 装配具名差集交付报告

> 触发：路灯_触发指令_E1修复与3新票核验与活体余票_20261002.md（星轨 2026-10-02 晚）
> 性质：P2 声明核对 + 活体余票（一次性领取第 6 刀，T-QICA声明-1 与 N-6★ 同刀收口）
> 代码提交：`e3d09b8`（nucleus/organ_assembler.py + main.py，精确暂存零并入他方遗留）

## 一、现象与根因

- **首报（装配差集）**：声明集（ORGAN_META 扫描 `organs/`，共 56 个）= 56；运行期装配集（`self.organs` 实际实例）= 57；具名差集 `多=['QICA']`。
- **根因**：QICA 位于 `nucleus/qica/QICA.py`（非 `organs/` 注册表），不被 `OrganLoader.scan_organs_directory()` 扫描，故不计入 ORGAN_META 声明集；但 `main.py` 在 legacy（:1406）与声明式（:1178）两条路径的 Phase 0 均通过 `self._create_organ(QICA, "QICA")` 显式实例化为器官并纳管于 `self.organs`。`FRAMEWORK_COMPONENTS`（organ_assembler.py:33）已将其列为组件符号，但缺少「准器官」身份的正式声明，导致差集 `['QICA']` 在日志中呈现为未解释的异常偏差。
- **为何不直接补进 ORGAN_META 扫描**：`main._create_organ`（:839）**非幂等**——每次 `organ_class(name)` 重建并覆盖 `self.organs[name]`。若把 QICA 补进 `organs/` 扫描，声明式装配路径会先经 Phase 0 实例化 QICA（已注入 `node_pool`/`knowledge_tree`），再被 `OrganAssembler.assemble()` 二次实例化覆盖，触发重复创建并可能丢失已注入依赖。故采用「显式常量声明 + 具名差集自检」方案，既统一计数口径又避免重复实例化。
- **目标（路灯触发指令第 6 刀）**：① 补 QICA 的器官身份声明；② 器官计数口径统一以 ORGAN_META=56 为基准；③ 装配输出具名差集。

## 二、修复内容

1. **`nucleus/organ_assembler.py`**：
   - 新增模块级常量 `FRAMEWORK_QUASI_ORGANS = frozenset({"QICA"})`，附注释说明其「框架准器官」身份与不入 ORGAN_META 扫描的架构原因；同步更新 `FRAMEWORK_COMPONENTS` 中 `"qica"` 的注释，指向该常量。
   - 新增方法 `OrganAssembler.diff_declared_vs_instantiated(instantiated_names)`，计算「声明集(ORGAN_META)」vs「装配集(self.organs)」具名差集，返回：`declared_count`（基线）/ `instantiated_count` / `named_diff` / `quasi_organ_extra`（差集中属框架准器官者，预期偏差）/ `undeclared_instantiated`（差集中非框架准器官者，真实缺陷）。
2. **`main.py`**：`_init_organs_with_feature()` 装配完成后台日志（原仅输出器官数）新增「装配具名差集」自检——统一以 ORGAN_META 扫描数（基线 56）为计数基准，运行期 `OrganLoader.scan_organs_directory()` + `OrganAssembler.diff_declared_vs_instantiated(set(self.organs.keys()))` 计算并输出差集；若 `undeclared_instantiated` 非空则升级 WARNING 提示「需补 ORGAN_META 或归入 FRAMEWORK_QUASI_ORGANS」。两条装配路径（legacy / 声明式）均覆盖。异常分支经 `silent_exc` 降级（不影响启动主流程）。

## 三、验收

| 项 | 结果 |
|---|---|
| ruff F（系统 0.16.5，`check --select F`） | ✅ All checks passed |
| cw2 静默except / CRCRLF | ✅ PASS（变更 .py 未新增静默 handler，无 CRCRLF） |
| arity 门禁 | ✅ PASS |
| deprecated-import / event-string | ✅ PASS |
| red_baseline collect | ✅ PASS（4487，基线 4484±5） |
| broken-chain 断链 | ✅ PASS（island=1 / entry=102 / set=35 均=基线，零增量） |
| cw3-B ctx 陈旧读 | ✅ PASS（0） |
| 待裁决登记册 | ✅ PASS（152 条，到期未裁 0） |
| 探针实证：声明集(ORGAN_META) | ✅ 56（扫描 `organs/` 读 ORGAN_META） |
| 探针实证：diff 逻辑（声明 56 + QICA 装配 57） | ✅ `named_diff=['QICA']` `quasi_organ_extra=['QICA']` `undeclared_instantiated=[]` |
| 探针实证：异常注入（伪造未声明器官） | ✅ 正确落入 `undeclared_instantiated`（真缺陷检测有效） |
| 行尾 | ✅ 两文件全 CRLF（0 LF-only / 0 CRCRLF） |

## 四、提交

- 代码修复：`e3d09b8`（nucleus/organ_assembler.py + main.py，精确暂存零并入他方遗留）

## 五、157 余票状态声明行

`157余票状态：C-8接线[已触发·8a73ab4] / T-框架-2[已触发・验收登记・verify rc=0] / T-基础-1[已触发・见提交 eb328e8] / 报告契约-3[已触发・见提交 92cd205] / E-1[已触发・见提交 10eb02f+cd2fa8b] / T-落盘路径泄漏-1[已核对・无缺陷(施工噪声)・见提交 25117cb] / T-向量库元数据污染-1[已核对・无缺陷(施工噪声)・见提交 453cf9f] / T-QICA声明-1[已触发・见提交 e3d09b8] / N-8[已触发・见提交 3b07346] / N-4[待活体窗] / N-5[待活体窗] / 空转#1/#4/#5/#9[待活体窗] / N-6★[已触发・见提交 e3d09b8] / N-9[待]`

> 纪律：逐刀独立提交、精确暂存零并入他方 7 个 docs `M` + 1 个 `test_lazy_snapshot_m9.py` `M`（非我产）；未 push（铁律113）；匿名 `Tongtong Dev`；PII 0。
