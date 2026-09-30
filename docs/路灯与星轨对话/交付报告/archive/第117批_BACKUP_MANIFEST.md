> ⚠️ 文件映射：本文档撰写时 R4 验收清单文件名为 `docs/验收/R4验收清单_B1-B23.md`，已于**第121批**更名为 `docs/验收/R4运行时验收总单.md`（去号化 + 版本表 v1.2）。



# 第117批 备份清单 BACKUP_MANIFEST

> 备份根: `.bak_batch117/`  |  生成时间: 2026-09-24 凌晨
> 备份均在**首次改动前**落盘（`tmp/patch_m117_backup.py`）。
> 命名规则：扁平名 `rel.replace("/", "__")`（与 116 批一致），另存 `_manifest.txt` / `_lineendings.txt`。

## 1. 代码备份（改前快照 → 用于 DIFFS_FULL）

| # | 备份文件 | 改前字节 | 当前字节 | 对应任务 |
|---|---|---:|---:|---|
| 1 | `.bak_batch117/utils__pulse_tracer.py` | 5328 | 9024 | T-117a 方案A+方案B |
| 2 | `.bak_batch117/base__BasePulseOrgan.py` | 29426 | 29795 | T-117a 方案B（删两处同步 flush） |
| 3 | `.bak_batch117/nucleus__reasoning__SafeEvolutionExecutor.py` | 330041 | 334764 | T-117b / T-117d① |
| 4 | `.bak_batch117/nucleus__logger.py` | 31363 | 33333 | T-117d②（smoke 日志器） |
| 5 | `.bak_batch117/tests__test_t115d_dummy_skip_ask.py` | 2634 | 3304 | T-117d②（合成指纹改走 smoke） |

## 2. ★账本改前快照（116 批漏做，本批补上）

| 文件 | 字节 | 说明 |
|---|---:|---|
| `.bak_batch117/data_patches/pending_patches.json` | 113414 | **写前**态（15 条） |
| `.bak_batch117/data_patches/patch_history.json` | 735454 | 写前态（74 条） |
| `.bak_batch117/data_patches/patch_history_obsolete.json` | 191274 | 写前态（22 条） |

> T-117e 对账本做了 1 次写盘（cd22 → obsolete）。写前快照已备，**可回滚**。

## 3. 行尾基线（`_lineendings.txt`）

| 文件 | 备份 | 当前 | 结论 |
|---|---|---|---|
| utils/pulse_tracer.py | LF | LF | 未翻转 |
| base/BasePulseOrgan.py | LF | LF | 未翻转 |
| nucleus/reasoning/SafeEvolutionExecutor.py | **CRLF** | **CRLF** | 未翻转 |
| nucleus/logger.py | LF | LF | 未翻转 |
| tests/test_t115d_dummy_skip_ask.py | LF | LF | 未翻转 |

## 4. 新增文件（无改前快照，删即可回滚）

| 文件 | 字节 | 说明 |
|---|---:|---|
| `tests/test_m117_tracer_flush.py` | 9921 | T-117a 竞态门禁 9 用例（含头判尾更正控） |
| `tests/test_m117_ratchet_and_smoke.py` | 7681 | T-117b 棘轮自愈 + T-117d② 冒烟隔离 9 用例 |
| `tests/test_m117_path_norm.py` | 2991 | T-117d① 路径形制归一 5 用例 |
| `docs/验收/R4验收清单_B1-B23.md` | 9725 | T-117c R4 验收总单 |

## 5. 回滚方式

```bash
# 代码回滚（逐文件）
cp .bak_batch117/utils__pulse_tracer.py                            utils/pulse_tracer.py
cp .bak_batch117/base__BasePulseOrgan.py                           base/BasePulseOrgan.py
cp .bak_batch117/nucleus__reasoning__SafeEvolutionExecutor.py      nucleus/reasoning/SafeEvolutionExecutor.py
cp .bak_batch117/nucleus__logger.py                                nucleus/logger.py
cp .bak_batch117/tests__test_t115d_dummy_skip_ask.py               tests/test_t115d_dummy_skip_ask.py

# 账本回滚（T-117e：cd22 回到 pending）
cp .bak_batch117/data_patches/pending_patches.json           data/patches/pending_patches.json
cp .bak_batch117/data_patches/patch_history.json             data/patches/patch_history.json
cp .bak_batch117/data_patches/patch_history_obsolete.json    data/patches/patch_history_obsolete.json
```

> 回滚后需重启框架生效（红线 2）。
