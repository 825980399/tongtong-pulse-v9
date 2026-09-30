# 第144批 BACKUP MANIFEST

> 生成：路灯 · 2026-09-27
> 备份目录：`.bak_batch144/`（建于首次改动前，git-ignored）

## 备份清单

| 源文件 | 备份路径 | 大小 | mtime |
|---|---|---|---|
| `.gitignore` | `.bak_batch144/.gitignore` | 4,283 B | 2026-09-20 22:08 |
| `config.py` | `.bak_batch144/config.py` | 309,155 B | 2026-09-27 19:39 |
| `nucleus/self_awareness/quality_score_v2.py` | `.bak_batch144/nucleus__self_awareness__quality_score_v2.py` | 20,778 B | 2026-09-26 18:23 |

## 无独立备份但可回溯的文件（git HEAD 基线 = `a199b5e`）
| 文件 | 基线来源 |
|---|---|
| `nucleus/data/DataAccessLayer.py` | `git show a199b5e:...` |
| `nucleus/reasoning/PatchManager.py` | `git show a199b5e:...` |
| `organs/brain/PulseCodeLearner.py` | `git show a199b5e:...` |
| `organs/senses/visual_engines/ocr_engine.py` | `git show a199b5e:...` |
| `tools/export_public.py` | `git show a199b5e:...` |
| `docs/完整进化路线与技术债务清单_v1.0.md` | `git show a199b5e:...` |
| `.git/hooks/pre-commit` | 本地文件（未跟踪，无 git 基线） |

## 工作树临时留存
- `tmp/dz144_doc_worktree_backup.md`：`docs/完整进化路线与技术债务清单_v1.0.md` 清洗前的**工作树副本**快照（含他人污染段，仅作对照，未入库）。

## 回滚方式
```bash
# 单批回滚
git reset --hard a199b5e      # 回到第143批；工作树污染会一并丢失（慎用）
# 仅回滚某文件
git checkout a199b5e -- <file>
```

---

*路灯 · 第144批 · 2026-09-27*
