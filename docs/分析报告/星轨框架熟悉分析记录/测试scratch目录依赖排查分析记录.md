# 框架熟悉分析记录：测试scratch目录依赖全库排查（P2-377扩展）

**分析时间**：2026-09-15
**分析人**：星轨（定时修复过程中全库排查）
**范围**：tests/ 目录下所有 .py 测试文件
**问题**：P2-377 - 测试依赖git-ignored scratch目录（.bak_batch49）→ 被清理即失效；已修1处，需全库排查同类

## 一、排查方法

1. 搜索所有测试文件中对 `.bak_batch*`、`scratch`、`tmp_dir`、`temp_dir`、`.tmp`、`code_backups` 的引用
2. 分类分析：哪些是真正依赖git-ignored目录（被清理会失败），哪些是安全的
3. 运行相关测试验证

## 二、排查结果分类

### 2.1 已修复的主要问题（第53批）

**test_tool_wiring_m49.py**：
- 原问题：核心测试从 `.bak_batch49` 基线读取排除集，目录被清理后测试失效
- 第53批修复：基线改为固化常量（`_BASE49_FLE_EXCLUDE`、`_BASE49_PCM_EXCLUDE`、`_BASE49_CGA_EXCLUDE`）
- 残留引用：`test_16_bak_corroboration_when_available`（第113-130行）
  - 这是"额外佐证"测试，`.bak_batch49`存在时与固化常量逐字比对
  - 目录不存在时 `skipTest(".bak_batch49 已被清理（scratch），固化常量已独立生效")`
  - **安全：不会失败**

### 2.2 安全的测试模式

**模式A：自己创建自己清理的scratch目录（安全）**
- `test_call_graph_m21.py`：`_SCRATCH = tmp/_callgraph_m21`，模块级收尾删除
- `test_event_bus_m14.py`：`_SCRATCH_DIR`，setup/teardown管理
- `test_event_tap_m17.py`：`_SCRATCH`，自己创建
- `test_lazy_snapshot_m9.py`：`_SCRATCH_DIR`，每次生成前清空
- `test_self_awareness_m18/m19/m20.py`：`_SCRATCH`，模块级收尾删除
- `test_snapshot_checksum_m13.py`：`_T3_SCRATCH_DIR`，自己管理
- `test_timeout_quality_m27.py`：`_JSON_TMP_DIR`，自己管理
- **特点**：使用 `tmp/` 下的子目录，测试开始时创建，结束时清理，不依赖外部目录

**模式B：使用tempfile.mkdtemp()（最安全）**
- `test_experience_pool_persistence_m54.py`
- `test_log_governance_m37.py`
- `test_patch_approve_write_check_m53.py`
- `test_patch_auto_approve_p1_m54.py`
- `test_patch_self_apply_m53.py`
- **特点**：使用系统临时目录，完全隔离，不依赖项目内任何目录

**模式C：只测试目录名字符串匹配（安全）**
- `test_batch_backup_m50.py`：测试 `should_skip_dir(".bak_batch49")` 是否返回True，不依赖实际目录
- `test_unified_exclude_dirs_m47.py`：测试排除列表是否包含目录名，不依赖实际目录

**模式D：有skipTest/return保护的生产状态检查（安全）**
- `test_tmp_backup_migration_m47.py`：
  - `test_30_old_location_gone`：`.tmp_backup`不存在时return（通过），存在时fail（说明迁移未完成）
  - `test_31_new_location_exists`：`.bak_tmp`不存在时skipTest
  - `test_32_migrated_snapshot_readable`：`.bak_tmp`不存在时skipTest

### 2.3 排查结论

**全库未发现"依赖git-ignored目录且被清理会失败"的测试。**

所有对git-ignored目录的引用都有妥善处理：
- 核心测试已改为固化常量（不依赖外部目录）
- 残留的佐证测试在目录不存在时skipTest
- 生产状态检查在目录不存在时return或skipTest
- 其他测试使用自己创建的scratch目录或tempfile

## 三、验证结果

运行4个相关测试文件：
- `test_tool_wiring_m49.py`：通过（1个skip，.bak_batch49不存在）
- `test_batch_backup_m50.py`：全部通过
- `test_tmp_backup_migration_m47.py`：通过（2个skip，.bak_tmp不存在）
- `test_unified_exclude_dirs_m47.py`：全部通过
- **总计：65 passed, 3 skipped（全部是安全跳过，非失败）**

## 四、P2-377状态更新

- **主要问题**：第53批已修复（test_tool_wiring_m49.py基线改为固化常量）
- **全库排查**：本次完成，确认无其他同类问题
- **结论**：P2-377 ✅ 已修复（第53批修复 + 本次全库排查确认）

## 五、经验教训

1. **测试不应依赖git-ignored目录**：git-ignored目录可能被随时清理，依赖它们的测试会不稳定
2. **正确的测试临时目录模式**：
   - 优先使用 `tempfile.mkdtemp()`（系统临时目录，完全隔离）
   - 其次使用 `tmp/` 下的子目录，自己创建自己清理
   - 绝对不要依赖 `.bak_batch*`、`.tmp_backup` 等可能被清理的目录
3. **佐证测试应有skip保护**：如果需要对比历史基线，应在基线不存在时skipTest，而不是fail
4. **固化常量优于外部文件**：对于排除集、配置基线等不常变化的数据，直接固化为常量比从外部文件读取更稳定
