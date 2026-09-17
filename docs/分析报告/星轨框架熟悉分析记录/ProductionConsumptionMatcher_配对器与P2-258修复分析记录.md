# 框架熟悉分析记录：ProductionConsumptionMatcher产出-消费配对器与P2-258修复

**分析时间**：2026-09-15
**分析人**：星轨（定时修复过程中精读）
**文件**：`nucleus/self_awareness/ProductionConsumptionMatcher.py`（约1170行）、`tests/test_matcher_disk_m37.py`（约370行）
**问题**：P2-258（data根下13个运行态文件未纳入白名单）

## 一、文件基本信息

- **路径**：`nucleus/self_awareness/ProductionConsumptionMatcher.py`
- **职责**：用纯静态AST扫描找出项目中"谁写了数据文件、谁读了数据文件"，标记「疑似无消费」（写了没人读）与「疑似无产出」（读了没人写）
- **定位**：PHASE18 地基之一，自我认知引擎的核心组件
- **红线**：纯静态分析，绝不触发任何运行时保存/加载，不import被扫描模块、不执行其代码

## 二、核心机制

### 2.1 双通道扫描

1. **源码通道**：遍历项目中.py文件（排除tests/tmp/.bak*/__pycache__），对每个调用点判定产出或消费，建立「文件 → 产出方/消费方」映射
2. **磁盘枚举通道**（第37批T2新增）：按扩展名遍历磁盘上的数据文件，与源码通道求并集

### 2.2 排除白名单体系

| 白名单类型 | 配置项 | 默认值 | 作用 |
|-----------|--------|--------|------|
| 总开关 | `ENABLE_PRODUCTION_CONSUMPTION_EXCLUDE` | True | 关闭时零排除 |
| 自我观察目录 | `_SELF_OBSERVE_DEFAULT` | `data/self_awareness` | 引擎自身产物 |
| 目录白名单 | `PRODUCTION_CONSUMPTION_EXCLUDE_DIRS` | 21个运行态目录 | 归档/测试/探针/运行态目录 |
| 扩展名白名单 | `PRODUCTION_CONSUMPTION_EXCLUDE_EXTENSIONS` | `.md/.txt/.log` | 文档/日志类 |
| **文件白名单**（P2-258新增） | `PRODUCTION_CONSUMPTION_EXCLUDE_FILES` | None（用默认14个文件） | data根下运行态文件 |

### 2.3 豁免原则（第39批T1裁决）

**扩展名/目录/文件白名单不作用于源码产出方命中的路径**：
- 无源码产出方 → 排除（标记为噪声）
- 有源码产出方 → 豁免（标记为`*_exempted`，保留在「有效无消费」中，是真问题）

这个原则避免了`logs/pulse_crash.log`这类"写了没人读"的真问题被噪声白名单吞掉。

## 三、P2-258问题根因

### 3.1 问题描述

data根下有13个运行态JSON文件（如`channel_quota_usage.json`、`config_override.json`、`runtime_state.json`等），这些文件：
- 是框架运行时自动生成的
- "源码未引用"是常态而非缺陷
- 没有子目录可依，目录白名单无法覆盖
- 扩展名是.json，不在扩展名白名单中

结果：这些文件被配对器标记为"未排除的磁盘独有"，贡献了噪声。

### 3.2 修复方案

1. **新增`_EXCLUDE_FILES_DEFAULT`常量**：列出14个data根下的运行态文件
2. **新增`_exclude_files()`函数**：支持配置覆盖（`PRODUCTION_CONSUMPTION_EXCLUDE_FILES`）
3. **修改`exclude_reason()`**：在目录白名单检查之后、扩展名白名单检查之前，增加文件级白名单检查
4. **遵循豁免原则**：有源码产出方的文件标记为`file_whitelist_exempted`，无产出方的标记为`file_whitelist`
5. **新增`_EXCLUDE_SUGGEST`条目**：`file_whitelist`和`file_whitelist_exempted`的建议文案
6. **config.py新增配置项**：`PRODUCTION_CONSUMPTION_EXCLUDE_FILES = None`（None表示用默认值）

### 3.3 设计决策

- **为什么用硬编码文件列表而不是"data/*.json"通配？**：精确控制，避免误排除用户自定义的data根下文件
- **为什么文件白名单检查在扩展名白名单之前？**：文件白名单更具体，应优先匹配；且.json不在扩展名白名单中，顺序不影响结果
- **为什么默认值是None而不是空列表？**：None表示"未配置，用代码内默认值"；空列表表示"明确配置为空，不排除任何文件"

## 四、测试覆盖

新增`TestFileWhitelistP258`类，6个测试：

| 测试 | 验证内容 |
|------|----------|
| test_01_data_root_runtime_file_excluded | data根下运行态文件（无产出方）被排除 |
| test_02_data_root_runtime_file_with_producer_exempted | 有产出方时豁免（保留为真问题） |
| test_03_non_whitelist_file_not_excluded | 非白名单文件不被排除 |
| test_04_all_default_whitelist_files_excluded | 默认白名单中所有文件都被排除 |
| test_05_config_override_works | 配置项可覆盖默认值 |
| test_06_suggestion_text_exists | 建议文案存在 |

## 五、与其他模块的交互

- **config.py**：提供配置项（开关、目录白名单、扩展名白名单、文件白名单）
- **nucleus/data/exclude_dirs.py**：提供源码扫描排除目录（`SOURCE_SCAN_DIRS`）和磁盘扫描排除目录（`DISK_SCAN_EXCLUDED`）
- **自我认知引擎**：消费配对器的输出，生成自认知报告
- **ReportBus**（第55批T2待接入）：配对器的报告将通过ReportBus接入自认知闭环

## 六、发现的其他问题

1. **白名单维护成本**：data根下新增运行态文件时，需要手动更新`_EXCLUDE_FILES_DEFAULT`。未来可考虑自动发现机制（如扫描data根下的.json文件并自动排除）。
2. **配置覆盖语义**：`PRODUCTION_CONSUMPTION_EXCLUDE_FILES`为非空列表时会完全覆盖默认值，而不是追加。这与目录白名单的行为一致，但用户可能期望追加。
3. **.bak文件未排除**：data根下的`tool_strategy_memory.json.bak`等备份文件不在白名单中，但它们是备份文件，应该被排除。不过这些文件可能会被`.bak`前缀排除规则覆盖（`_EXCLUDE_DIR_PREFIXES = (".bak", ".")`），需要验证。

## 七、修复记录

- **修复时间**：2026-09-15（定时任务第10次触发）
- **修改文件**：
  - `nucleus/self_awareness/ProductionConsumptionMatcher.py`：新增`_EXCLUDE_FILES_DEFAULT`、`_exclude_files()`、修改`exclude_reason()`、新增`_EXCLUDE_SUGGEST`条目
  - `config.py`：新增`PRODUCTION_CONSUMPTION_EXCLUDE_FILES = None`
  - `tests/test_matcher_disk_m37.py`：新增`TestFileWhitelistP258`类（6个测试）
- **验证结果**：ruff通过，33个测试全部通过（原27 + 新增6）
- **git提交**：待提交
