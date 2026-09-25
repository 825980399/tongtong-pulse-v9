# 第116批 备份清单 BACKUP_MANIFEST

> 备份根: `.bak_batch116/`  |  生成时间: 2026-09-24（115 批收尾后承接，24 日凌晨完成）
> 备份均在**首次改动前**落盘（`tmp/patch_m116_backup.py`，`os.walk` 只拷 .py）。

## 1. 代码备份（改前快照 → 用于 DIFFS_FULL）

| # | 备份文件 | 字节 | 对应任务 |
|---|---|---:|---|
| 1 | `.bak_batch116/nucleus/reasoning/SafeEvolutionExecutor.py` | 328358 | T-116a①③ / T-116b① |
| 2 | `.bak_batch116/nucleus/reasoning/PatchManager.py` | 209654 | T-116f |
| 3 | `.bak_batch116/nucleus/evolution/PatchAutoApprover.py` | 26370 | T-116c⑤ |
| 4 | `.bak_batch116/tools/adjudicate_patch.py` | 8634 | T-116c①②③④ / T-116b② |
| 5 | `.bak_batch116/tools/check_patch_consistency.py` | 13519 | T-116b③ |

> 备份目录内另有**扁平副本**（`nucleus__reasoning__SafeEvolutionExecutor.py` 等，路径 `/`→`__`），
> 为早期备份脚本两种布局混用所致；DIFF 生成以 `_manifest.txt` 的 5 个相对路径为准，两种布局内容一致。

## 2. 当前文件（改后）字节对照

| 文件 | 改前 | 改后 | 增量 |
|---|---:|---:|---:|
| nucleus/reasoning/SafeEvolutionExecutor.py | 328358 | 330041 | +1683 |
| nucleus/reasoning/PatchManager.py | 209654 | 210066 | +412 |
| nucleus/evolution/PatchAutoApprover.py | 26370 | 26851 | +481 |
| tools/adjudicate_patch.py | 8634 | 16382 | +7748 |
| tools/check_patch_consistency.py | 13519 | 14578 | +1059 |

## 3. 账本快照（事后补做 — 见交付报告「遗留说明·P2」）

| 文件 | 字节 | 说明 |
|---|---:|---|
| `.bak_batch116/data_patches_after/pending_patches.json` | 113414 | 写后态（16→15 条） |
| `.bak_batch116/data_patches_after/patch_history.json` | 735454 | 写后态（74 条，C6 回填落此处） |
| `.bak_batch116/data_patches_after/patch_history_obsolete.json` | 191274 | 写后态（21→22 条，含 ebbf） |

> ⚠️ 本批**数据侧写盘未取改前快照**（114 批曾做、本批漏做）。上表为**写后**快照，仅可用于对照，
> 不可用于回滚到写前态。回滚能力依赖：`fix-c6`（可逆回填，幂等）+ `keep --source obsolete`
> （可把 ebbf 从归档账标回保留）。

## 4. 新增文件（无改前快照）

- `tests/test_m116_c6_semantics.py` — 全新单测，13 用例（T-116b/c/f 语义门禁）

## 5. 回滚方式

```bash
# 代码回滚（逐文件）
cp .bak_batch116/nucleus/reasoning/SafeEvolutionExecutor.py  nucleus/reasoning/SafeEvolutionExecutor.py
cp .bak_batch116/nucleus/reasoning/PatchManager.py            nucleus/reasoning/PatchManager.py
cp .bak_batch116/nucleus/evolution/PatchAutoApprover.py       nucleus/evolution/PatchAutoApprover.py
cp .bak_batch116/tools/adjudicate_patch.py                    tools/adjudicate_patch.py
cp .bak_batch116/tools/check_patch_consistency.py              tools/check_patch_consistency.py
```

> 回滚后需重启框架生效（红线 2）。账本侧不随代码回滚自动复原。
