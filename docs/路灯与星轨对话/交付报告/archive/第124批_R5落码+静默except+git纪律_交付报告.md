# 第 124 批交付报告 · R5 落码 + 静默 except 首批 7 处 + git 纪律

- **日期**：2026-09-25
- **批号**：主线第 124 批（T-124a / T-124b / T-124c）
- **提交基准**：`HEAD=8d9b95f`（提交前）→ 9 个新提交落在 `8d9b95f..HEAD`
- **配套文件**：`第124批_R5落码_DIFF.md`、`第124批_静默except_DIFF.md`、`第124批_DIFFS_FULL.md`、`第124批_T0前提核实与偏差清单.md`

---

## 0. 概述

| 方向 | 优先级 | 状态 | 结论 |
|---|---|---|---|
| T-124a R5 人脸持久化落码 | P0 | ✅ 完成 | 整文件覆盖 `organs/senses/PulseVisualCortex.py`，schema/挂点/隐私方法/env 覆盖齐备 |
| T-124b 静默 except 首批 7 处 | P1 | ✅ 完成 | 7 处 `except:pass` → `silent_exc(...level="warning")`；helper 加 `level` 尾参；CI 门禁补 `silent_exc` |
| T-124c git 一批一 commit 纪律 | P2 | ✅ 完成 | 116~123 legacy 收口 + 本批 124 + 收尾 chore，共 9 票；125 批文件刻意排除 |

全部五项门禁通过（见 §5④）。

---

## §5① R5 落码 DIFF

**交付物**：`第124批_R5落码_DIFF.md`（仅 `PulseVisualCortex.py` 的 124 提交差异，277 行）。完整差异见 `第124批_DIFFS_FULL.md`。

### 落码要点
- **整文件覆盖** `organs/senses/PulseVisualCortex.py`（953 → 1120 行，CRLF 保全）。
- **Schema**：`faces{name → {encoding128, enrolled_at, source, hits, tolerance_override}}`，不装库也能写、装库（face_recognition 1.3.0，第 122 批已装）后直接生效。
- **挂点**：`_face_roster_path` / `_save_face_roster` / load 三段；`_DIRTY_FACE_KEYS` 三处共用（脏键过滤）。
- **隐私方法**（`B_PRIVACY` 由第 122 批审计证据并入）：`_forget_face` + `_list_roster`（**不回显 encoding**，只回 name/enrolled_at/hits）。
- **环境变量覆盖**：`TONGTONG_FACE_ROSTER` 可整体覆盖花名册（便于测试/部署解耦）。
- **门禁自愈**：R5 新增的 2 处静默 handler（`_tol_or_none` 的 `except (TypeError,ValueError)`、`numpy` 兜底 `except ImportError`）均补 DEBUG 级日志，N7 门禁零新增（见 §5④）。

### 验收（T-124a）
- `py_compile` OK；离线 import VC 不炸。
- 桩测 `tests/test_r5_face_roster.py` **5/5 passed**（env 覆盖 / 落盘脏键过滤 / 只回元数据 / forget 四处一致 / 往返等价）。
- `ruff F=0`。

---

## §5② 静默 except 首批 7 处 DIFF

**交付物**：`第124批_静默except_DIFF.md`（main/logger/PulseMetricsCollector/_silent_except/cw2 gate 的 124 提交差异，272 行）。

### 7 处改造清单（改前 `except Exception: pass` → 改后 `silent_exc(e, "...", level="warning")`）

| # | 文件 | 当前行 | 语义标识 |
|---|---|---|---|
| 1 | `main.py` | 1193 | `_resolve_component` |
| 2 | `main.py` | 1316 | `注入肺实例降级` |
| 3 | `main.py` | 3702 | `假死探测取handled` |
| 4 | `main.py` | 3722 | `崩溃日志轮转 disable` |
| 5 | `main.py` | 3728 | `崩溃日志轮转 rename` |
| 6 | `nucleus/logger.py` | 508 | `轮转冷却读` |
| 7 | `organs/core/PulseMetricsCollector.py` | 430 | `快照统计` |

> 注：`main.py:3422` 已存在 `silent_exc(e, "main.py:3328")`（早期批次，默认 `level="debug"`），非本批 7 处之一。

### 2 处支撑改动
- `nucleus/_silent_except.py`：`silent_exc(e, where="")` 新增尾参 `level: str = "debug"`（向后兼容），按白名单走 `logging.getLogger("pulse").<level>(...)`。
- `tools/ci/cw2_t2e_ci_gate_silent_except.py`：`LOG_FUNCS` 增加 `"silent_exc"` —— 因为 `silent_exc` 内部即走 logging，本质已是 reported，此前未列入导致"改用 silent_exc 仍计为新增静默 except"的误报。

### 验收（T-124b）
- CI 门禁 `cw2_t2e_ci_gate_silent_except.py`：**PASS（新增静默 except = 0）**。
- `tests/test_m78_silent_except.py`：5 passed；`tests/test_m95_followups.py`：43 passed。
- `ruff F=0`；`py_compile` OK。

---

## §5③ git 9 票提交记录

**结构说明（与任务书偏差）**：任务书列 `116/117/118/119+120/121/122/123/docs/chore`。本批实际为 **9 票**——
1. 任务书"docs"独立票 → 改为**各批 docs 内联进对应批次提交**（每票自包含、可独立审查）；
2. 任务书"119+120"合并 → 改为"**120+121**"合并（120/121 均为小批，合并更紧凑；119 独立含其补丁交付）；
3. 新增"**124**"本批票 + "**chore**"收尾票。

总票数仍为 9，与任务书一致。

| # | SHA | 批号 | 说明 |
|---|---|---|---|
| 1 | `ae390ba` | 116 | 交付报告/测试/前置分析 收口 |
| 2 | `d1a39a1` | 117 | tracer flush 修复 + R4 验收 + 棘轮修复 收口（+补归 `base/BasePulseOrgan.py`、`tests/test_t115d_dummy_skip_ask.py`） |
| 3 | `2b5a499` | 118 | face R2 急救 + 裸 logging + 账本三件套 收口（+补归 `organs/senses/PulseEars.py`、`tests/test_m95_followups.py`） |
| 4 | `ca8469e` | 119 | 装库前置硬化 + 池票首批清账 收口 |
| 5 | `a877bee` | 120+121 | SOP 成文 / 票号校准 / D040 / 账面收尾 收口 |
| 6 | `b666843` | 122 | 活体窗装库 + C4 断 5 Parquet 修复 收口 |
| 7 | `1a2ff00` | 123 | DAL 止血 + 补丁冻结 + W6 准备 收口 |
| 8 | 见 `git log` | 124 | **本批**：R5 落码 + 静默 except 首批 7 处 + T0 报告（本交付报告与 3 份 DIFF 同属此票） |
| 9 | 见 `git log` | chore | 收尾删除 legacy 残留产物（`dz_claim_scan.json` / `tmp_verify_influx.py`） |

> 注：上表 1–7 行为稳定祖先提交 SHA（不随后续 amend 改写）；第 8/9 行（124 / chore）因本交付报告本身纳入 124 提交，其精确 SHA 以 `git --no-pager log --oneline 8d9b95f..HEAD` 实时输出为准。

**红线核查**：
- ✅ 不含 `data/knowledge/`（各票均显式列文件，chore 未用 `git add -A`，`.gitignore` 已覆盖）。
- ⚠️ **未 push gitee**：依铁律 113（本地 `.git` 此前损坏、无共享远程引用、共享远程风险），提交只落本地，待星轨手动修复仓库关联后推送。
- ⛔ **刻意排除**：`docs/分析报告/烛微_第125批技术债务前置分析_20260925.md` 与 `docs/路灯与星轨对话/任务书/烛微_第125批技术债务前置分析_任务书.md`（未来 125 批，保留未跟踪）。

---

## §5④ 门禁结果（五项全过）

| 门禁项 | 结果 | 说明 |
|---|---|---|
| `ruff F=0` | ✅ PASS | 全仓 `ruff check --select F` —— All checks passed |
| `py_compile` | ✅ PASS | 6 改文件 + 2 测试文件均编译通过 |
| `m95`（test_m95_followups） | ✅ 43 passed | 即任务书"m95 43 passed"口径 |
| R5 桩测 | ✅ 5/5 passed | `tests/test_r5_face_roster.py` |
| `.py CRLF 不变` | ✅ PASS | 6 改文件 CRLF 保全；`main.py` 唯一 1 处 LF-only 行（`# _m51_t3_main`）经比对与 `.bak_batch124` 备份一致，为历史遗留、非本批引入 |

附加：N7 静默 except CI 门禁 **PASS（本次变更新增 = 0）**；`tests/test_m78_silent_except.py` 5 passed。

---

## §6 T0 前提核实偏差与裁决（摘要）

开工前逐条实测任务书前提，发现 8 项偏差（详见 `第124批_T0前提核实与偏差清单.md`），关键裁决：
1. 文件名 `_silent_exc` → 实为 `_silent_except`；`MetricsCollector` → `organs/core/PulseMetricsCollector.py`；路径 `organs/brain/` → `organs/senses/`。
2. B_PRIVACY（`_list_faces`/`_forget_face`）在第 122 批审计证据中**未并入** proposed，本批补并入。
3. CI 门禁 `LOG_FUNCS` 不含 `silent_exc` 会导致"改用 silent_exc 仍误判为新增静默 except"——已加 `"silent_exc"` 修复。
4. "AST 517 / m95 43 passed" 为陈旧上游口径；本批以实测 delta 报告（m95 实测 43 passed 仍成立，test_m78 实有 5 测试）。
5. R5 文件工作副本为 LF，整文件覆盖时按 CRLF 字节转换保全。

---

## §7 交付物清单

| 文件 | 内容 |
|---|---|
| `第124批_R5落码+静默except+git纪律_交付报告.md` | 本报告 |
| `第124批_R5落码_DIFF.md` | R5 落码差异（VC.py，277 行） |
| `第124批_静默except_DIFF.md` | 静默 except 7 处差异（272 行） |
| `第124批_DIFFS_FULL.md` | 本批 124 提交全量差异（1099 行） |
| `第124批_T0前提核实与偏差清单.md` | T0 实测偏差与裁决 |
| `tests/test_r5_face_roster.py` | R5 桩测（已提交入 124 票） |

---

## §8 Push 状态

- 本地 9 提交已完成，**未推送 gitee**（铁律 113：本地 `.git` 此前损坏、共享远程风险）。
- 待星轨手动修复仓库关联（`git remote` / `fetch`）后，执行 `git push origin master` 推送 `8d9b95f..HEAD`。
