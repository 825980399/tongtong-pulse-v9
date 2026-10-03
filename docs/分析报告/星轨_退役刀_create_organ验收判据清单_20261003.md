# 星轨 · _create_organ 退役★ 验收判据清单 v1.0（2026-10-03）

> 用途：路灯 `_create_organ 退役★`（第 12 刀，P2）交付后，星轨对照本清单逐项验收
> 编制依据：任务书 `第158批_上-A_路灯后续任务推进_20261003.md` §三-5 + 星轨代码实测（main.py / config.py / nucleus/organ_assembler.py / organ_loader.py）
> ★涉停框架：交付前已提前告知小林安排重启验证窗口；装配验证在框架恢复运行后做（D5 不擅自停框架）

---

## 〇、退役刀实质（星轨实测定调，验收前提）

| 事实 | 实测证据 |
|---|---|
| `_create_organ` 本体（main.py:839-863）= **声明式装配底层创建原语**，非退役对象 | 全仓唯一创建实现；`OrganAssembler` / `OrganLoader` 均经 `framework.create_organ()` 转发至它；docstring 已由路灯标注 |
| **真正退役对象 = legacy 硬编码段 `_init_organs_legacy()`（main.py:1354 起）** | 内含 37 处 `_create_organ` 调用；`use_declarative_assembly=True`（config.py:1827 默认）时该段**不执行**（main.py:1158-1161 分支走声明式） |
| QICA = 框架准器官特例 | 位于 `nucleus/qica` 非 `organs/`，不入 ORGAN_META 扫描；Phase 0 显式 `_create_organ(QICA,"QICA")` 创建（organ_assembler.py:41-51）——**退役后必须保留 QICA 创建** |

**验收核心 = legacy 段退役（停用/移除）而非删 `_create_organ` 本体。**

---

## 一、代码验收判据（停框架施工后，交付报告对照）

### A. legacy 段处置（主判据）
- [ ] A1. `_init_organs_legacy()` 已退役（删除 / 停用 / 标注 deprecated 不再走）——至少**移除执行路径**，不再可被 `use_declarative_assembly=False` 调用或已显式废弃
- [ ] A2. legacy 段内 **37 处 `_create_organ` 调用逐一核实**替代路径（每处列：器官名 / 声明式覆盖 / 或准器官保留 / 或确认不再需要）——交付报告附**逐处清单**（38/39 处口径以实际为准，要求路灯给出实测数与任务书 39 的差异说明）
- [ ] A3. **QICA 创建保留**：`self.qica = self._create_organ(QICA, "QICA")` 仍存在（main.py:1211/1461 之一），不可随 legacy 段误删
- [ ] A4. `use_declarative_assembly` 开关语义明确：声明式=True 继续走 `_init_organs_declarative()`；False 回退路径若被一并移除，须在 config.py 开关注释 + 装配分支处**显式标注「legacy 已退役，False 不再可用」**（防未来误开）

### B. 装配完整性（停框架期静态验证）
- [ ] B1. 声明式装配路径完整：`_init_organs_declarative()` 未被破坏，OrganAssembler/OrganLoader 调用链无损
- [ ] B2. **56/56 器官装配断言**：交付报告给出声明式装配实测数（56 声明 + QICA = 57 装配集，与 T-唯一口径件 `organ_declared=57` 一致）
- [ ] B3. 装配具名差集自检（main.py:1166-1187）仍工作：declared vs instantiated 差集 = 预期 `FRAMEWORK_QUASI_ORGANS`（QICA），无「未声明却实例化」异常

### C. 引用面/门禁（静态）
- [ ] C1. 全仓无对 `_init_organs_legacy` 的**其他引用**残留（除被退役处外，grep 全仓含 tests）
- [ ] C2. 门禁全 PASS：ruff F / cw2 / arity / broken-chain / red-baseline（4487±5）/ 回归 tests 全绿
- [ ] C3. 行尾纯 CRLF（loneLF=0、CRCRLF=0）、PII 0、匿名 Tongtong Dev、未 push（铁律113）

---

## 二、装配验证判据（框架恢复运行后，需小林安排窗口）

> 前提：路灯退役刀交付后，由小林在控制台 `python main.py` 启动，星轨/路灯观测

- [ ] V1. 启动成功：`✅ 曈曈 已就绪`，57/57 器官并行启动完成（pulse.log boot 行）
- [ ] V2. 无 `_create_organ` 相关异常：启动期 silent_exc/ERROR 计数 = 0（对照退役前基线）
- [ ] V3. 装配具名差集日志实读命中：声明=56 / 装配=57 / 差集=[QICA] / 未声明异常=[]
- [ ] V4. 功能冒烟（任选 3-5 项）：health_ui 端口 5051 可访问、自我认知/进化/污染报告可产出（ReportBus）、运行时规则引擎 rule_engine 增量档可跑、artifact_registry 可审计
- [ ] V5. 停机验证（Ctrl+C）：优雅关闭、无崩溃诊断/重试风暴（对照 N-4/N-5 基线）

---

## 三、交付报告必含清单（路灯交付时对照）

- [ ] R1. 逐处替代路径清单（37 处 + 口径差异说明：任务书 39 vs 实测 37/38/39）
- [ ] R2. QICA 保留证据（代码位置 + 启动日志命中）
- [ ] R3. legacy 段处置方式声明（删除/停用/标注 + 回退路径影响说明）
- [ ] R4. 门禁结果汇总 + 回归计数
- [ ] R5. 施工包状态声明行（末尾固定格式）
- [ ] R6. 是否已提前告知小林重启验证窗口（★涉停框架铁律）

---

## 四、验收结论档位

| 档位 | 条件 | 处置 |
|---|---|---|
| ✅ 通过 | A1-A4 + B1-B3 + C1-C3 全过；交付报告 R1-R6 齐 | 登记册销案（若在册）+ 总账同步 + 158 收口（装配验证 V1-V5 补做） |
| ⚠️ 附条件通过 | 代码全过，但 V1-V5 待框架恢复后补 | 先销案，V 系列挂 D5 待恢复运行补验 |
| ❌ 退回 | A 主判据任一不过 / 误删 QICA / legacy 残留调用 | 退回路灯补施工，不销案 |

---

*—— 星轨 · _create_organ 退役★ 验收判据清单 v1.0 · 2026-10-03 · 等路灯交付后逐项对照*
