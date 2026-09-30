# 第147批 BACKUP_MANIFEST

- 批次：第147批（_on_inference_request 九刀拆分）
- 基线：`9c858ae`（第146批交付）→ 本批 HEAD：`944312e`
- 生成时间：2026-09-28

## 一、备份清单

| 文件 | 备份位置 | 大小 | 备份时机 | 状态 |
|---|---|---|---|---|
| `organs/brain/PulseInnerWorld.py` | `.bak_batch147/organs__brain__PulseInnerWorld.py.bak` | 945541 B | 刀1 首刀改动前 | ✅ 完整 |

## 二、备份缺口声明（如实，未粉饰）

本批核心改动对象 `PulseInnerWorld.py`（唯一的大文件，1864 行 diff）在**刀1 首刀前已完整备份**，覆盖全部 10 刀拆分 + 收尾注释更新。

其余 3 个收尾文件为**小改动**（合计 6+/5-，纯注释/文档/白名单），未单独备份：

| 文件 | 改动性质 | 回滚途径 |
|---|---|---|
| `nucleus/self_inspector.py` | 移除 1 条死豁免 + 2 行注释 | `git show 944312e^:<rel>` |
| `organs/brain/pulse_inner_world_knowledge.py` | 1 处注释改 ctx 字段 | `git show 944312e^:<rel>` |
| `docs/分析报告/技术债务台账_代码实查_20260919.csv` | D059 代码证据列追加基线标注 | `git show 944312e^:<rel>` |

以上 3 文件改动前均为 Clean，回滚可直接 `git show` HEAD 前一个提交取回，风险可接受。

## 三、每刀提交哈希对照（供逐刀回滚）

| 刀 | 提交 | 内容 |
|---|---|---|
| 刀1 | `36d8c1c` | `_ir_finalize` |
| 刀2 | `6f1e65d` | `_ir_build_context` + `__slots__` |
| 刀3 | `4071353` | `_ir_try_explicit_search` + `_ir_run_detectors` |
| 刀4 | `6ebedd0` | `_ir_try_deep_search_pre` + `_ir_try_deep_search_exec` |
| 刀5 | `f8dc210` | `_ir_assemble_knowledge_answer` |
| 刀6 | `22a95a5` | `_ir_dispatch_qica_method` + `_ir_qica_knowledge_retrieve` |
| 刀7-小步1 | `f820539` | `_ir_run_pipeline` |
| 刀7-小步2 | `b062b13` | `_ir_try_derivation` + `_ir_decompose_and_deep_read` |
| 刀7-小步3a | `01fc298` | `_ir_apply_modulations` |
| 刀7-小步3b | `a159d8e` | `_ir_record_failed_domain` + `_ir_try_creative_solution` + `_ir_plan_tools` |
| 收尾 | `944312e` | 基线重锚 + 注释更新 |
