# PyMuPDF（fitz）AGPL-3.0 开源合规豁免声明

- 落盘日期：2026-10-08
- 关联批次：第 170 批 C4（T-PyMuPDF开源合规-1）
- 关联票号：`T-PyMuPDF开源合规-1`（已入 `tools/ci/baselines/pending_register_baseline.json` 待裁决登记册）
- 维护方：路灯（施工）/ 星轨（裁决）
- 状态：**标注豁免 + 立票跟踪**（未做代码替换，替换决策待星轨裁定）

---

## 1. 背景

`requirements.txt:43` 声明了 `PyMuPDF>=1.23.0,<2.0.0`（导入名 `fitz`），用于 PDF 文字提取。
PyMuPDF 的上游许可证为 **AGPL-3.0**（强 copyleft），在「分发 / 网络服务」场景下对衍生作品有
传染性开源义务，属开源合规重点关注对象。

## 2. 实际使用情况（T0 实测核实）

- 唯一生产调用点：`organs/senses/visual_engines/pdf_engine.py`
  - `L39`：`import fitz  # PyMuPDF`（位于 `try:` 体内）
  - `L41`：`_doc = fitz.open(file_path)`
  - `L48`：`_page.get_text()` 逐页抽取文字
  - `L61-62`：`except ImportError` 降级 → 返回 `error="PyMuPDF(fitz)未安装..."`
- 调用形态：**动态导入 + ImportError 兜底**，即该依赖为**可选功能依赖**，缺失时 PDF 提取
  优雅降级（返回 error 字段），不影响主链路。
- 全仓范围（organs/ / nucleus/ / tools/ / tests/）检索 `fitz|PyMuPDF|pymupdf`：
  除 `pdf_engine.py` 与 CI 对账门禁（`check_requirements_declared_imports.py` 别名映射）、
  既有单测外，无其它运行期调用。

## 3. 风险研判

| 维度 | 评估 |
|------|------|
| 许可证 | AGPL-3.0（强 copyleft） |
| 触发条件 | 仅在「分发本仓库 / 以网络服务形式提供本仓库能力」时产生衍生义务 |
| 当前耦合度 | 低：仅 1 个可选功能模块，已 ImportError 降级，非核心链路 |
| 替换可行性 | 高：`get_text()` 纯文字抽取可迁移至 pypdf（BSD-2-Clause）/ pdfminer.six（MIT） |
| 替换阻塞点 | ① 抽取质量（mupdf 版面还原优于 pypdf，复杂 PDF 需实测）；② 本环境未安装 pypdf，无法就地验证 |

## 4. 处置决定（本刀）

采用任务书给定的「**标注豁免 + 立票跟踪**」分支（非代码替换）：

1. `requirements.txt:43` 追加 AGPL 豁免标注，注明票号与声明文档路径；
2. 本声明落盘，作为合规留痕；
3. 票号 `T-PyMuPDF开源合规-1` 已在待裁决登记册跟踪，状态维持「待星轨裁定」。

## 5. 缓减措施（已具备）

- 导入已 `try/except ImportError` 包裹，缺失即降级，无硬崩溃风险；
- CI 门禁 `check_requirements_declared_imports.py` 已建立 `pymupdf → fitz` 别名映射，
  依赖声明与导入名一致，无「未声明硬缺口」。

## 6. 待星轨裁定的后续动作（建议，非本刀施工范围）

> 以下为建议项，是否执行由星轨裁定；本刀不做代码改动。

- **方案 A（彻底去 AGPL）**：将 `pdf_engine.py` 的 `fitz` 调用替换为 `pypdf`，
  并把 `requirements.txt` 的 `PyMuPDF` 改为 `pypdf`；需先在部署环境安装 pypdf 并做
  抽取质量端到端对比（≥ 既有用例抽样）。
- **方案 B（维持豁免）**：维持 PyMuPDF，本声明 + 票号跟踪长期有效；
  若曈曈后续以「分发 / SaaS」形态对外，则须重新评估 AGPL 义务或切换方案 A。

## 7. 验收对照

- [x] `requirements.txt` 已标注 AGPL 豁免（保留依赖，附票号与声明路径）
- [x] 合规声明落盘（本文件）
- [x] 文档更新（本声明 + requirements 注释）
- [ ] 代码替换（待星轨裁定方案 A/B，非本刀范围）

> 注：本刀未满足「requirements.txt 无 AGPL 依赖」分支，而是满足并列的
> 「合规声明落盘」分支，符合任务书验收「无 AGPL 依赖 **或** 合规声明落盘」之「或」条款。
