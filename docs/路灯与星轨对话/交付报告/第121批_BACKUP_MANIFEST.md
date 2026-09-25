# 第121批 BACKUP_MANIFEST（账单收尾 + SOP三件套 + 装库准备）

> 生成时间：2026-09-24 ｜ 备份目录：`.bak_batch121/` ｜ 纪律：改前首次改动前取快照


## §1 备份前快照（5 个改前文件 + _manifest.txt）

| 备份文件名（扁平名） | 原相对路径 | 大小(B) | sha256 | CRLF | bareLF | BOM | 行尾 |
|---|---|---|---|---|---|---|---|
| `docs__分析报告__技术债务台账_代码实查_20260919.csv` | `docs/分析报告/技术债务台账_代码实查_20260919.csv` | 83895 | `94d22dd3a476142dc3e0ebedbc93b17f97768c34d0e2b540800b9ead03cbbea2` | 212 | 5 | 是 | MIXED |
| `tools__check_debt_ledger.py` | `tools/check_debt_ledger.py` | 3928 | `ca59f1960fb8d08d298848bd1c60f384bd039e600ac9bf0b2110e728b99d86aa` | 96 | 0 | 否 | CRLF |
| `nucleus__mnemosyne__PulseNode.py` | `nucleus/mnemosyne/PulseNode.py` | 44941 | `460da07c27caef6e0553a344a249efc5075ef389672ad3f83eb376887d854977` | 954 | 0 | 否 | CRLF |
| `docs__台账__销账SOP_v1.md` | `docs/台账/销账SOP_v1.md` | 4685 | `d136a44c02259b2c181388bb147fa473a9498367b27b40f0d0ef1ff0a20a1064` | 73 | 0 | 否 | CRLF |
| `docs__验收__R4验收清单_B1-B23.md` | `docs/验收/R4运行时验收总单.md` | 10009 | `6b889700415ff69a1c5e93d80117902ba7e45b89755bf4dac52aaad2b9b6b2a6` | 0 | 108 | 否 | LF |

## §2 回滚：从备份恢复（逐文件，按扁平名还原）

```bash
cd D:/xinrenlei/tongtong-pulse-v9
cp ".bak_batch121/docs__分析报告__技术债务台账_代码实查_20260919.csv" "docs/分析报告/技术债务台账_代码实查_20260919.csv"
cp ".bak_batch121/tools__check_debt_ledger.py" "tools/check_debt_ledger.py"
cp ".bak_batch121/nucleus__mnemosyne__PulseNode.py" "nucleus/mnemosyne/PulseNode.py"
cp ".bak_batch121/docs__台账__销账SOP_v1.md" "docs/台账/销账SOP_v1.md"
cp ".bak_batch121/docs__验收__R4验收清单_B1-B23.md" "docs/验收/R4运行时验收总单.md"
```

## §3 历史文件副本（`refs/`，12 份，改后态存档，非改前基线）

> 说明：12 份历史文件在 T-121b E3 中**加了一行映射头**（纯增量、不改正文）。
> `refs/` 存的是加头后的副本，用于审计；**无改前基线**（改动仅为顶部一行 front-matter，可凭本报告 §映射头文本 手工逆操作）。

| 备份文件 | 原相对路径 | 大小(B) | sha256 |
|---|---|---|---|
| `docs__分析报告__烛微_第117批技术债务前置分析_20260923.md` | `docs/分析报告/烛微_第117批技术债务前置分析_20260923.md` | 24775 | `93aeec69b07c1a72eee3b6d26cf3ba1d2634941dacee487aeee89ca9890c647d` |
| `docs__分析报告__烛微_第118批技术债务前置分析_20260924.md` | `docs/分析报告/烛微_第118批技术债务前置分析_20260924.md` | 20047 | `77993e210873bfcaf70200a6aca4e8c49090e76ecf020dd334558e48cb3c5d8c` |
| `docs__分析报告__烛微_第119批技术债务前置分析_20260924.md` | `docs/分析报告/烛微_第119批技术债务前置分析_20260924.md` | 17888 | `d85079e1845c1e7e75065983a148c0172c974618a1134f4c5d0f58f62786900f` |
| `docs__分析报告__烛微_第120批技术债务前置分析_20260924.md` | `docs/分析报告/烛微_第120批技术债务前置分析_20260924.md` | 18646 | `39472aaf630e16ac192f33e14097ea05f5e054fd1e339a3f260cba811cd4c4ae` |
| `docs__路灯与星轨对话__交付报告__待分析__2026-09-24_路灯交付_主线第117批_交付报告.md` | `docs/路灯与星轨对话/交付报告/待分析/2026-09-24_路灯交付_主线第117批_交付报告.md` | 14449 | `832dcb7fe0a210f908f3d9b888632a2ebcc78c24b2ad1ceecd4802b72697d154` |
| `docs__路灯与星轨对话__交付报告__第117批_BACKUP_MANIFEST.md` | `docs/路灯与星轨对话/交付报告/第117批_BACKUP_MANIFEST.md` | 3231 | `b5c976d2dd3209b84b94554ef729e0250898d1de811c4e9bbd414ae3726d76e4` |
| `docs__路灯与星轨对话__交付报告__第118批_BACKUP_MANIFEST.md` | `docs/路灯与星轨对话/交付报告/第118批_BACKUP_MANIFEST.md` | 2557 | `0b588d2dd987f96c1321f2103a908563ae17436fc1fd2eece69ff5e64e2ed9b5` |
| `docs__路灯与星轨对话__任务书__烛微_第117批技术债务前置分析_任务书.md` | `docs/路灯与星轨对话/任务书/烛微_第117批技术债务前置分析_任务书.md` | 6954 | `f015c31c25388192102ddaa00876a6fe8c9f859eea95af5419e4cafb94e4f7f6` |
| `docs__路灯与星轨对话__任务书__烛微_第118批技术债务前置分析_任务书.md` | `docs/路灯与星轨对话/任务书/烛微_第118批技术债务前置分析_任务书.md` | 5124 | `716cc2770f34ea2bdf659c42df56fb5f451f553a837622427147d8896479d43c` |
| `docs__路灯与星轨对话__任务书__烛微_第119批技术债务前置分析_任务书.md` | `docs/路灯与星轨对话/任务书/烛微_第119批技术债务前置分析_任务书.md` | 4665 | `76ca58a6a50fae8bf1f983cfc72d23aec025e61cc4590c4b929806c8a4e09992` |
| `docs__路灯与星轨对话__任务书__第117批_tracer flush修复+R4验收+棘轮修复_任务书.md` | `docs/路灯与星轨对话/任务书/第117批_tracer flush修复+R4验收+棘轮修复_任务书.md` | 4507 | `4b4195aa5ad378a18badd13571326e90770362fb68ef8c100ccae43cacd86782` |
| `docs__路灯与星轨对话__任务书__第118批_face R2急救+裸logging+账本三件套_任务书.md` | `docs/路灯与星轨对话/任务书/第118批_face R2急救+裸logging+账本三件套_任务书.md` | 4497 | `a1117827986b72b67c8cd4aab4f611f593a70650038e07928cbaca31eb6e7674` |

## §4 行尾保全声明（门禁之一）

| 文件 | 改前行尾 | 改后行尾 | CRLF 数(改前→改后) | bareLF | BOM | 结论 |
|---|---|---|---|---|---|---|
| `docs/分析报告/技术债务台账_代码实查_20260919.csv` | MIXED | MIXED | 212→212 | 5 | 是 | 保全 |
| `tools/check_debt_ledger.py` | CRLF | CRLF | 96→106 | 0 | 否 | 保全 |
| `nucleus/mnemosyne/PulseNode.py` | CRLF | CRLF | 954→954 | 0 | 否 | 保全 |
| `docs/台账/销账SOP_v1.md` | CRLF | CRLF | 73→83 | 0 | 否 | 保全 |
| `docs/验收/R4运行时验收总单.md` | LF | LF | 0→0 | 118 | 否 | 保全 |
| `docs/台账/装库准备包_v1.md`（新建） | — | CRLF | —→57 | 0 | 否 | 新建 CRLF |

> 说明：技术债务台账 CSV 的 `bareLF=5` 为**字段内合法引号换行**（quoted newline），
> 非行尾翻转；BOM(utf-8-sig) 与 CRLF 保全。R4 验收总单（更名文件）保持 LF 原样。

## §5 当前工作区改动文件 sha256（交付核对）

| 文件 | 大小(B) | sha256 | 行尾 |
|---|---|---|---|
| `docs/分析报告/技术债务台账_代码实查_20260919.csv` | 84799 | `9eeec6f2a2a1a6f80a99f1c8418362689370670de91df6a015f52c477b09d200` | MIXED |
| `tools/check_debt_ledger.py` | 4551 | `e692b0061e7f1185ea415ec6c4f0840bf61ccffe8ccda0cbc72358af054cf842` | CRLF |
| `nucleus/mnemosyne/PulseNode.py` | 45009 | `996020c4c6f02eb572bd644fb2ff476ee03dd8af62f06a21ef70bd8b5a773323` | CRLF |
| `docs/台账/销账SOP_v1.md` | 5766 | `d0479d5ffc658d37bc0e02d94a8aabba827745c0fc6b505112b57f5c9cf0845a` | CRLF |
| `docs/验收/R4运行时验收总单.md` | 11305 | `046498d62ed30121b209e2200c86746654638ae452317fe1999c93740a62d9d7` | LF |
| `docs/台账/装库准备包_v1.md` | 3329 | `82b70fe97be5c8acef7359bfe6e1bb2486a7ec06495b9c7d440c7b89b6b73235` | CRLF |
