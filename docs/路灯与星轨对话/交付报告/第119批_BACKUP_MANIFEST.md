# 第119批 · 备份清单（BACKUP MANIFEST）

> 生成时刻：2026-09-24（路灯交付，主线第119批）
> 备份根目录：`.bak_batch119/`（改前快照，git-ignored，单点故障，勿依赖其长期留存）
> 恢复方式：Python `shutil.copyfile`（Git Bash `>nul` 在此环境报 Permission denied，禁用）

## 一、改前快照（3 目标文件，T0 建立）

| 相对路径 | 字节 | SHA-256 | 行尾 | BOM |
|---|---:|---|---|---|
| `organs/senses/PulseVisualCortex.py` | 44400 | `70eb22acff6aaf4e7e02e6ee095cf7b6f7a14518a7cc3c62952832a7c853db64` | CRLF | 否 |
| `nucleus/reasoning/SafeEvolutionExecutor.py` | 334764 | `fbb4abfbe95f266d12243127346612c419cd2a11999ccde366e0b74bb444a6ea` | CRLF | 否 |
| `docs/分析报告/技术债务台账_代码实查_20260919.csv` | 84131 | `c9f1bbf6dc07578bcbe35f1d2edd7dda5c197bcb13e3b3c0de3f7e831e57b364` | CRLF | 是(UTF-8) |

> 行尾保全核验（字节级）：3 文件改后均与改前**同 CRLF/BOM 形态**，补丁脚本采用「去 CRLF→替换→还原 CRLF」「CSV 去 BOM→检测 CRLF→还原」策略，无行尾污染（详见 §三）。

## 二、备份范围说明（重要）

T0 备份**仅覆盖 3 目标文件**（VC.py / SE.py / CSV）。以下两项为 3 目标文件改动后的**派生同步**，未纳入 `.bak_batch119/`，原因：其改动是台账口径刷新，非新增功能点，回滚时与 CSV 同步回滚即可。

- `docs/完整进化路线与技术债务清单_v1.0.md` §1.2：双轨表刷新（pool 39/159/1、site 6/2/0），纯文档。
- `tools/check_debt_ledger.py`：`TARGET` 常量刷新至新基线（只读工具，无写风险）。

如须完整回滚，请同步还原上述两文件至第118批末状态（git show HEAD 或人工对照交付报告 §1.2 旧值）。

## 三、补丁行尾保全策略（铁律115/116/131 合规）

1. `.py` 文件：`raw.decode("utf-8-sig")` → 检测 `\r\n` → `text.replace("\r\n","\n")` → 唯一性 `assert cnt==1` → 替换 → 还原 CRLF → `wb` 写回。
2. `.csv` 文件：去 BOM → 检测 CRLF → 逻辑行替换 → 还原 CRLF → BOM 写回。
3. 幂等：每个 old 串均 `content.count(old)==1` 断言，重复/缺失即中止，杜绝静默 no-op（Edit 工具大括号陷阱规避）。
4. 备份取基线：`git show HEAD:<path>` 优先；本批 .git 损坏，改用 T0 落盘快照 `.bak_batch119/`。

## 四、门禁可恢复性

- VC.py / SE.py 改动均经 `py_compile` ✅，下次框架重启生效。
- CSV 改动经 `tools/check_debt_ledger.py` 校验 ✅ `EXIT=0`（双轨计数一致）。
- 任一处异常可用 `shutil.copyfile(.bak_batch119/<rel>, <rel>)` 即时回滚。
