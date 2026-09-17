# 框架熟悉分析记录：备份脚本与data目录基线声明（P2-208）

**分析时间**：2026-09-15
**分析人**：星轨（定时修复过程中精读）
**文件**：`tools/batch_backup.py`（180行）
**问题**：P2-208 - .bak备份脚本SKIP_DIRS含data/导致无基线

## 一、文件基本信息

- **路径**：`tools/batch_backup.py`
- **行数**：约180行
- **功能**：批次备份工具，遍历项目中的.py文件并复制到.bak_batchN/目录
- **核心导出**：`SKIP_PATH_PREFIXES`、`SKIP_DIR_NAMES`、`BACKUP_DIR_PREFIXES`、`KNOWN_SKIPPED_PY_FILES`、`should_skip_dir()`、`iter_py_files()`、`backup()`、`plan()`

## 二、核心类/函数

### 2.1 should_skip_dir(rel_dir: str) -> bool

**功能**：按相对路径判断该目录是否应跳过备份。

**判断逻辑（三层）**：
1. **路径前缀匹配**（`SKIP_PATH_PREFIXES`）：只在项目根层命中，如 `data/`、`logs/`
2. **目录名匹配**（`SKIP_DIR_NAMES`）：任意层级按名跳过，如 `__pycache__`、`code_backups`、`.git`
3. **备份目录前缀**（`BACKUP_DIR_PREFIXES`）：如 `.bak_batch44`、`.bak_tmp`

**关键设计**：
- 使用相对路径而非裸目录名，避免 `nucleus/data/` 被误跳过（P2-334修复）
- `SKIP_DIR_NAMES` 刻意不含 `data`/`tmp`，避免误伤子目录

### 2.2 iter_py_files(root: str) -> Generator[str]

**功能**：遍历应备份的.py文件，返回相对路径（POSIX分隔符）。

**实现**：使用 `os.walk` + `dns[:] = [...]` 原地修改目录列表，实现剪枝遍历。

### 2.3 SKIP_PATH_PREFIXES

**当前值**：`("data/", "logs/")`

**设计意图**：
- `data/`：运行时数据目录（知识库/经验库/补丁/指标等JSON与缓存），非源码
- `logs/`：运行日志，滚动覆盖，无基线价值

**已知例外**：
- `data/evolution/tests/` 下有6个.py测试文件（约1.5KB/个），属进化引擎配套测试
- 建议后续迁移至 `tests/` 目录统一管理

## 三、与其他模块交互

- **调用方**：批次任务开始前的备份脚本（`tmp/patch_m42_backup.py`等）
- **测试**：`tests/test_batch_backup_m50.py`（18个测试，覆盖should_skip_dir、iter_py_files、真实仓库计划）
- **关联模块**：`tools/audit_utils.py` 也有 `should_skip_dir` 函数（用于代码审计，逻辑类似）

## 四、设计意图理解

1. **为什么跳过data/？**
   - data/主要是运行时数据（JSON、缓存、知识库），不是源码
   - 备份运行时数据会导致备份体积膨胀，且数据频繁变化无基线价值
   - 但data/evolution/tests/下的.py文件是例外，应该被备份

2. **为什么用相对路径前缀而非裸目录名？**
   - P2-334修复：之前用裸目录名 `data` 会误伤 `nucleus/data/` 等子目录
   - 改为相对路径前缀 `data/` 后，只跳过根层的data/，不影响子目录

3. **为什么需要KNOWN_SKIPPED_PY_FILES？**
   - 明确记录被跳过的.py文件，便于审计
   - 不改变跳过行为，但建立了"已知例外"的基线
   - 后续如果这些文件被修改，可以通过对比KNOWN_SKIPPED_PY_FILES发现

## 五、发现的问题

### P2-208：data/目录无基线声明

**问题**：`SKIP_PATH_PREFIXES` 包含 `data/`，但没有明确说明为什么跳过，也没有记录被跳过的.py文件。

**影响**：
- 备份时data/下的.py文件（如data/evolution/tests/）无基线
- 如果这些文件被修改，无法通过备份对比发现
- 新开发者不清楚data/被跳过的原因

**修复**：
1. 在 `SKIP_PATH_PREFIXES` 上方添加详细注释，说明data/和logs/是运行时数据目录
2. 添加 `KNOWN_SKIPPED_PY_FILES` 常量，记录6个已知被跳过的.py文件
3. 在 `__all__` 中导出新常量
4. 建议后续将data/evolution/tests/迁移至tests/目录

## 六、修复记录

**修复时间**：2026-09-15（星轨定时修复第5次）
**修改文件**：`tools/batch_backup.py`
**修改内容**：
- 添加 `SKIP_PATH_PREFIXES` 详细注释（P2-208基线声明）
- 添加 `KNOWN_SKIPPED_PY_FILES` 常量（6个已知例外）
- 更新 `__all__` 导出列表
**验证结果**：
- ruff F类检查：通过
- 导入验证：KNOWN_SKIPPED_PY_FILES有6个文件
- pytest：18个测试全部通过
**不改变现有行为**：should_skip_dir()的返回值不变，仅添加文档和审计常量
