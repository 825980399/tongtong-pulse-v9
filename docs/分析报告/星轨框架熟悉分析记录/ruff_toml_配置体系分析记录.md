# 框架熟悉分析记录：ruff.toml配置体系

**分析时间**：2026-09-15
**分析人**：星轨（定时修复过程中精读）
**文件**：`ruff.toml`（51行，2.1KB）

## 一、文件基本信息

- **用途**：ruff静态代码检查工具的项目级配置文件
- **建立时间**：2026-09-02
- **最近调整**：2026-09-10（第2批，星轨裁决line-length 88→120）
- **本次修改**：2026-09-15（星轨定时修复，新增.release-tmp排除）

## 二、核心配置项理解

### 2.1 line-length = 120
- **背景**：默认88对中文注释不友好
- **裁决**：星轨2026-09-10第2批裁决，方案A（调基线，不为过ruff改动业务代码）
- **影响**：允许更长的行，减少中文注释的换行

### 2.2 exclude（顶层配置项）
- **注意**：exclude与line-length同为顶层配置项，写在lint段下不生效
- **排除项**：
  - `data/code_backups`：历史版本只读存档，重复告警干扰统计
  - `tmp/patch_*`/`tmp/scan_*`/`tmp/check_*`：诊断施工临时脚本（第4批实测+30条）
  - `.bak*`：备份文件
  - `.release-tmp`/`.release-tmp*/`：发布临时副本（本次新增，1297文件/25.8MB）
- **设计细节**：tmp/下的test_*.py是正式回归测试，必须继续受检，故按前缀精确排除，不整目录排除tmp/

### 2.3 [lint] ignore（8项设计内忽略）
| 规则 | 忽略原因 |
|------|----------|
| BLE001 | 盲捕获Exception，外围模块/硬件探测/器官边界防御性编程 |
| S110 | try-except-pass，安全探测类失败时静默降级 |
| S112 | try-except-continue，批量外围任务容错继续 |
| N999 | （未在注释中说明，需后续确认） |
| SIM102 | （未在注释中说明，需后续确认） |
| B005 | strip-with-multi-characters，PulseInnerWorld按字符集剥离 |
| UP009 | utf8-encoding-declaration，项目头部规范强制要求 |
| E702 | （未在注释中说明，需后续确认） |

### 2.4 [lint.per-file-ignores]
- `organs/motor/PulseController.py` = ["E402"]
- `organs/motor/PulseLegs.py` = ["E402"]
- **原因**：budget_guard装饰器必须在类定义与后续import之前就位，强行上移import有加载时序风险

## 三、与其他模块的交互

- **.gitignore**：.release-tmp已在gitignore中排除，但ruff需要单独配置exclude
- **CI/CD**：ruff检查可能在发布流程中运行，排除.release-tmp避免发布时误报
- **健康诊断**：self_inspector可能调用ruff统计代码问题数，排除后统计更准确

## 四、发现的问题/待确认项

1. **N999/SIM102/E702的忽略原因未在注释中说明**：建议后续补充注释，或确认是否仍需忽略
2. **exclude列表可能需要定期审计**：新增临时目录时需同步更新exclude
3. **.release-tmp目录的生成机制**：需确认是哪个脚本/流程生成的，是否可以在生成后自动清理

## 五、本次修复记录

- **问题**：.release-tmp未在ruff exclude中，导致重复扫描1297个文件
- **修复**：在exclude列表中添加.release-tmp和.release-tmp*/，并添加注释说明
- **验证**：语法错误测试文件验证排除生效，全库F类检查0错误
