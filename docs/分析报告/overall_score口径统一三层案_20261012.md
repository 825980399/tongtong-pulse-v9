# overall_score 口径统一三层案（第196批 刀3 · 纯文档件）

- **编制**：路灯 ｜ **日期**：2026-10-12
- **依据**：第196批施工任务书 §3 + 烛微196前置分析 §3（口径统一三层案）
- **定位**：**纯文档件，零代码、零停窗**。代码面迁移（engine 降级改造 + 消费点切换）推 197。

---

## 背景

全仓 `overall_score` / `health_level` 存在**三套并行口径**，命名与边界互不统一，导致历史曲线不可比、消费面混乱。实测分布：

| 口径 | 位置 | 分数/等级方案 | 标签语言 | 状态 |
|------|------|--------------|----------|------|
| **v2（主标准）** | `nucleus/self_awareness/quality_score_v2.py` | 5 维加权（module_effectiveness 0.30 / issue_severity 0.25 / test_coverage 0.20 / static_health 0.15 / data_integrity 0.10），**证据驱动、可证伪、不惩罚「不知道」** | 英文 | 现行主入口 `evaluate_v2` |
| **v1（旧引擎）** | `nucleus/self_awareness/SelfAwarenessEngine.py:1599 _health_level` + `compute_overall_score` | 可用维度加权平均（<2 维度→None） | 英文 | 仍在 PulseCortex 等消费 |
| **遗留手工** | `organs/brain/PulseInnerWorld.py:11121` | 手工逐项加减分（趋势好+5/坏-8…） | 中文 | 遗留，与 v1/v2 不兼容 |

两口径消费面**互不相交**：`tools/export_lfi.py:162` 声明 `overall_score: False`（LFI 不接 overall_score）；v2 仅写 `data/self_awareness/quality_trend.jsonl` 趋势文件；遗留手工评分仅活在 PulseInnerWorld 内部。统一迁移面比「担心中」更小。

---

## 三层案

### ① 分数口径：v2 为主，engine 降为「维度级诊断」

- **v2（`quality_score_v2.evaluate_v2`）定为唯一综合分主标准**：只认可量化的运行数据（消费率/修复率/覆盖率/问题数），每个维度给出 `formula + data_source + raw` 可证伪。
- **v1 `SelfAwarenessEngine.compute_overall_score` 降级为「维度级诊断」**：保留其逐维度明细（`reasoning_precision` / `code_health` 等），`overall_score` 仅作为聚合视图之一，**不再作为对外主综合分**。其 `health_level` 边界（见②）待迁移对齐 v2。
- **遗留手工评分（PulseInnerWorld）标记废弃**：不纳入 v2 体系，随重构逐步移除。
- 迁移动作（推 197）：消费点（`PulseCortex` 质量闸、PulseInnerWorld 深度审视）统一改读 v2；engine 的 `overall_score` 聚合保留但仅用于诊断回填。

### ② 分级带口径：统一命名与边界一张表

现状三套边界冲突（**v1 与 v2 边界不同**）：

| 等级 | v1 `_health_level` | v2 `quality_level` | 遗留手工（PulseInnerWorld） |
|------|--------------------|--------------------|------------------------------|
| 顶级 | healthy ≥ 80 | healthy ≥ 90 | 优秀 ≥ 90 |
| 次高 | moderate 60–79 | attention 70–89 | 良好 ≥ 75 |
| 中 | concerning 40–59 | warning 40–69 | 一般 ≥ 60 |
| 低 | critical < 40 | critical < 40 | 需要关注 < 60 |
| 未知 | unknown（非数值） | unknown（None） | — |

**统一方案（以 v2 为基准，英文标签，消除 v1 偏移）**：

| 统一等级 | 分数边界 | 含义 |
|----------|----------|------|
| `healthy` | ≥ 90 | 健康 |
| `attention` | 70–89 | 需关注（趋势/单维偏弱） |
| `warning` | 40–69 | 警告（多维权重偏低） |
| `critical` | < 40 | 危急（存在未修高优问题） |
| `unknown` | None / 非数值 | 数据不足，不惩罚 |

- v1 `_health_level`（moderate 60–79 / concerning 40–59）**迁移对齐**上表（60–69 并入 warning，70–79 并入 attention）。
- 遗留中文标签（优秀/良好/一般/需要关注）**统一替换为英文**，消除中英混用。
- 统一边界表落地位置（推 197）：新建 `nucleus/self_awareness/health_bands.py` 单一真源，v1/v2/消费方全部引用。

### ③ 断代标注：统一生效日起，两口径历史曲线不得跨口径对比

- **统一生效日**：自 **2026-10-12** 起，v2 为唯一综合分口径；此前历史视为 v1/遗留口径代。
- **历史曲线隔离**：`data/self_awareness/quality_trend.jsonl`（v2）与任何遗留引擎曲线**不得跨口径拼接对比**；历史数据保留但每条记录标注 `caliber_version`（v1 / legacy / v2）。
- **迁移窗口**：生效日前为过渡期，双写（v2 主 + 旧引擎诊断回填），过渡期结束后旧引擎 `overall_score` 聚合仅作诊断、不再对外发布。

---

## 验收口径（本刀纯文档）

- 三层案完整：① 分数口径（v2 为主 / engine 降级维度级诊断）② 分级带口径（统一命名与边界一张表）③ 断代标注（统一生效日 + 历史曲线不跨口径）✅
- 零代码、零停窗、不影响既有消费面（两口径消费面互不相交，统一迁移推 197）✅
- 代码面迁移（engine 降级改造 + 消费点切换 + `health_bands.py` 单一真源）属「不排 196」清单，推 197 执行。

*路灯 · overall_score 口径统一三层案 · 2026-10-12 · 纯文档件*
