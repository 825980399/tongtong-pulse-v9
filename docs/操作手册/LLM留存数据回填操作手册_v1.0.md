# LLM 留存数据回填操作手册 v1.0

> **批次**：主线第45批 T4（P1-294）　**编制**：路灯　**日期**：2026-09-13
> **工具**：`tools/backfill_llm_trace_fields.py`　**上游**：第44批 T1（P1-285 / P1-286）
> **验证报告**：`docs/分析报告/m45_回填工具验证报告.json`（ALL OK）
> **★本批未执行生产回填** —— 本手册供**停机窗口**执行时使用。

---

## 一、这个工具解决什么问题

第43批 T1 数据质量评估实测 `data/llm_traces/calls_*.jsonl`：

| 问题 | 规模 | 后果 |
|---|---|---|
| `prompt_version` 缺失 | **100%** | 无法按提示词版本回溯效果差异 |
| `failed` 记录的 `error` 为空 | **111/111** | 失败不可诊断、重复试错 |

第44批 T1 已修复**生成侧**（新记录自带 `prompt_version` 与失败 `error`），
但**历史记录**仍是空的 → 回填工具负责把这批历史补成可分析形态。

---

## 二、★前置条件（强制执行）

| # | 条件 | 检查方式 |
|---|---|---|
| **1** | **框架必须已停止运行** | 见下方「停机确认」 |
| **2** | 磁盘可用空间 ≥ 留存目录大小的 **3 倍** | 回填会同时保留「归档备份」+「单文件 .bak」+「新文件」 |
| **3** | 有权限写 `data/llm_traces/` 与 `data/_archive/` | — |

### ★停机确认（必做，第44批血的教训）

```bash
# 用 Python 探针（勿用 tail/head —— 本环境 coreutils 不可用）
python -c "import os,datetime,io; p='logs/pulse.log'; print(os.path.getsize(p), datetime.datetime.fromtimestamp(os.path.getmtime(p))); print(''.join(io.open(p,encoding='utf-8',errors='replace').readlines()[-5:]))"
```

**判据**：`logs/pulse.log` 的 mtime **距今超过 5 分钟** → 视为已停机。
若仍在增长 → **立即中止**：回填会与在写进程竞争，导致数据丢失。

---

## 三、执行步骤（五步，逐步验证）

### 步骤 0：备份（手工兜底，可选但推荐）

工具的 `--apply` 会自动做「整目录归档备份」，但**手抄一份**更稳妥：

```bash
python -c "
import shutil, datetime
d = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
shutil.copytree('data/llm_traces', 'data/_archive/llm_traces_manual_%s' % d)
print('manual backup ->', d)
"
```

### 步骤 1：dry-run（**只读**，不写任何文件）

```bash
python tools/backfill_llm_trace_fields.py --dir data/llm_traces
```

**期望输出**：
```
[回填] dir=...\data\llm_traces apply=False
  - calls_20260913.jsonl 记录=NNN prompt_version补=NNN error补=NN
[回填] 合计 记录=NNN prompt_version补=NNN error补=NN
[回填] dry-run（未写盘）。加 --apply 执行。
```

★ **记录下这三个数字**，步骤 4 要用它们核对。

### 步骤 2：生成「回填方案报告」（**仍然不写盘**）

```bash
python tools/backfill_llm_trace_fields.py --report docs/分析报告/m45_回填执行报告.json
```

人工审阅报告里的 `files[].samples`（前 3 条样例）确认补的值符合预期。

### 步骤 3：执行回填（`--apply`，**此时才写盘**）

```bash
python tools/backfill_llm_trace_fields.py --apply \
    --report docs/分析报告/m45_回填执行报告.json
```

**执行时会发生什么（按顺序）**：
1. **整目录归档备份** → `data/_archive/llm_traces_backup_YYYYMMDD_HHMMSS/`
   （含 `BACKUP_MANIFEST.json`，逐文件 size + sha256 前 16 位）
2. 每个 `calls_*.jsonl` 复制一份 `<file>.bak_batch44`（已存在则复用）
3. 逐行重写 `calls_*.jsonl`：只补 `prompt_version` / `error` 两个字段

**期望输出（新增两行）**：
```
[回填] 归档备份 → ...\data\_archive\llm_traces_backup_20260913_HHMMSS（N 文件 / X.XX MB，校验=OK）
```

★ **若 `校验=MISMATCH`** → 立即停止并向星轨上报（备份不完整，禁止继续）。

### 步骤 4：验证（**必须做**）

```bash
python -c "
import json, io, glob
from collections import Counter
recs = []
for fp in sorted(glob.glob('data/llm_traces/calls_*.jsonl')):
    for ln in io.open(fp, encoding='utf-8', errors='replace'):
        ln = ln.strip()
        if ln:
            try: recs.append(json.loads(ln))
            except Exception: pass
print('总记录', len(recs))
print('prompt_version 分布:', dict(Counter(r.get('prompt_version') or '<empty>' for r in recs)))
print('failed 且 error 为空:', sum(1 for r in recs if r.get('status') != 'success' and not (r.get('error') or '').strip()))
"
```

**验收判据**：

| 项 | 期望 |
|---|---|
| `prompt_version` 为空的数量 | **0**（全部为 `unknown` 或具体版本号） |
| `failed` 且 `error` 为空的数量 | **0** |
| 总记录数 | 与步骤 1 的 dry-run 数字**一致**（若期间框架启动会变） |
| 业务字段 | `prompt` / `response` / `status` / `ts` **逐条未变** |

### 步骤 5：重启框架并观察

重启后确认新记录正常写入（`prompt_version` 不再是 `<empty>`），
并检查 `data/llm_traces/quality_report.json` 的「数据纯度」是否改善。

---

## 四、回滚方案

**回滚是一条命令** —— 用归档备份覆盖回去：

```bash
python -c "
import shutil, os, glob
baks = sorted(glob.glob('data/_archive/llm_traces_backup_*'))
src = baks[-1]                      # 最近一次归档备份
print('rollback from:', src)
for f in os.listdir(src):
    if f == 'BACKUP_MANIFEST.json':
        continue
    shutil.copy2(os.path.join(src, f), os.path.join('data/llm_traces', f))
print('rollback done')
"
```

**三级回滚**：

| 级别 | 场景 | 动作 |
|---|---|---|
| L1 | 只回滚个别文件 | 用 `<file>.bak_batch44` 覆盖回单个文件 |
| L2 | 全量回滚 | 用 `data/_archive/llm_traces_backup_*` 整目录覆盖 |
| L3 | 备份也损坏 | 用步骤 0 的手工备份 |

★ **回填是"补两个字段"的幂等操作** —— 重复执行不会累积改动（已在门控测试中验证），
因此"回滚"通常不是必须的：出了问题再跑一次即可回到一致状态。

---

## 五、验证清单（执行后逐项打勾）

- [ ] 步骤 2 的 dry-run 数字已记录
- [ ] `data/_archive/llm_traces_backup_*/BACKUP_MANIFEST.json` 存在且 `mismatch` 为空
- [ ] `<file>.bak_batch44` 已生成
- [ ] 验证脚本：`prompt_version` 为空数 = **0**
- [ ] 验证脚本：failed 且 error 为空数 = **0**
- [ ] 业务字段（prompt/response/status/ts）逐条未变
- [ ] 框架重启后新记录 `prompt_version` 正常
- [ ] 报告已写入 `docs/分析报告/`

---

## 六、常见问题（FAQ）

| 症状 | 真因 | 处理 |
|---|---|---|
| `KeyError: 'total'` / `'total_prompt_version_fill'` | 工具版本过旧（第44批初版有聚合口径 bug） | 升级到第45批版本（已修复） |
| `校验=MISMATCH` | 备份期间磁盘满 / 文件被外部持有 | 释放空间后重跑；**不要继续** |
| 执行后记录数变少 | 文件被外部重写（框架/编辑器） | 用归档备份回滚；确认已停机后重做 |
| `prompt_version` 仍是空 | 该字段名拼写不符 / 文件不是 `calls_*.jsonl` | 检查 `--dir` 是否指向正确目录 |
| 第二次 `--apply` 报告补 0 条 | **正常** —— 幂等生效 | 无需处理 |
| dry-run 两次数字不同 | 框架仍在运行（文件在增长） | **立即停止**，检出并停掉框架 |

---

## 七、参数速查

| 参数 | 默认 | 说明 |
|---|---|---|
| `--dir` | `config.LLM_TRACE_DIR` | 留存目录 |
| `--apply` | 关 | 关 = dry-run（**默认**）；开 = 写盘 |
| `--version` | `unknown` | 回填到 `prompt_version` 的值 |
| `--report` | 无 | 报告 JSON 输出路径（dry-run / apply 均可） |
| `--archive-root` | `data/_archive` | 整目录归档根 |
| `--no-backup` | 关 | 跳过整目录归档备份（**不推荐**） |

---

## 八、安全设计要点（为什么可以放心用）

| 设计 | 作用 |
|---|---|
| **默认 dry-run** | 不写盘是默认行为，写盘必须显式 `--apply` |
| **两层备份** | 整目录归档（带 sha256）+ 单文件 `.bak_batch44` |
| **只补两个字段** | 不触碰 `prompt` / `response` / `status` / `ts` |
| **幂等** | 重复执行不累积改动（门控测试已证） |
| **坏行容忍** | 非法 JSON / 非 dict 行**原样保留**，只计入 `bad_lines` |
| **`error` 派生规则** | failed 且 `response` 非空 → 取 `response` 前 200 字符并加 `backfilled_from_response:` 前缀；否则写 `(backfilled: source had no error detail)` |
| **不删除任何数据** | 归档 = 复制；`prompt_version` 只填空值不覆盖已有值 |

---

**手册结束。执行前请再确认一次：框架已停止（`logs/pulse.log` mtime > 5 分钟）。**
