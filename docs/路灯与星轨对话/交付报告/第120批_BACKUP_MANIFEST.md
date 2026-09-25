# 第120批 备份清单与行尾核验 (BACKUP_MANIFEST)
> 生成: 2026-09-24 16:51:19 路灯

## 一、改前快照 (.bak_batch120/，sha256 + 行尾)

| 文件 | 备份 sha256 | 备份大小 | 备份行尾 | 当前 sha256 | 当前行尾 |
|---|---|---|---|---|---|
| docs/分析报告/技术债务台账_代码实查_20260919.csv | `46e56771de81a10f…` | 83063 | MIXED(CRLF=208,loneLF=5,BOM=True) | `94d22dd3a476142d…` | MIXED(CRLF=212,loneLF=5,BOM=True) |
| tools/check_debt_ledger.py | `de24e730e94deeab…` | 3522 | LF | `ca59f1960fb8d08d…` | CRLF |
| nucleus/mnemosyne/PulseNode.py | `106657d6190771e8…` | 43514 | LF | `460da07c27caef6e…` | CRLF |

> 完整 sha256 见各文件下方。

CSV  备份 sha256 = 46e56771de81a10f1c6999d1a569ed0d6ff773c890ad5c530974b4b6b72f4efb
CSV  当前 sha256 = 94d22dd3a476142dc3e0ebedbc93b17f97768c34d0e2b540800b9ead03cbbea2
ledger 备份 sha256 = de24e730e94deeabf7fb20d4b436218a702a7835d0c25e6162cf4496387acd7b
ledger 当前 sha256 = ca59f1960fb8d08d298848bd1c60f384bd039e600ac9bf0b2110e728b99d86aa
PulseNode 备份 sha256 = 106657d6190771e80d468b4c213cada2f1a104a7661f5c1f96822af36e5d274e
PulseNode 当前 sha256 = 460da07c27caef6e0553a344a249efc5075ef389672ad3f83eb376887d854977

## 二、新增/其它文件 sha256 + 行尾

| 文件 | 当前 sha256 | 行尾 | 说明 |
|---|---|---|---|
| docs/完整进化路线与技术债务清单_v1.0.md | `65607e371b39c44cad9425915754b58620748ef492aba860da4a6787cd9dc61a` | CRLF | T-120c 改 §1.2（基线在 git HEAD） |
| docs/台账/销账SOP_v1.md | `d136a44c02259b2c181388bb147fa473a9498367b27b40f0d0ef1ff0a20a1064` | CRLF | 新建（T-120d） |

## 三、git_evidence 备份（T-120f 改前，铁律113 保全）

路径 `.bak_batch120/git_evidence/`，含 pack_*/logs_*/refs_*/HEAD_* 四组时间戳副本：
- `HEAD_20260924_163855`
- `logs_20260924_163854`
- `pack_20260924_163854`
- `refs_20260924_163855`

## 四、行尾保全结论

- CSV：BOM+CRLF 保全（212 行 CRLF，5 处 loneLF 系引号内多行字段，合法）
- MD：CRLF 无 BOM 保全（与仓库 .md 约定一致）
- PulseNode.py / check_debt_ledger.py：工作副本 CRLF（索引/HEAD 为 LF，autocrlf 呈现；本次未翻转行尾）
- 销账SOP_v1.md：新建时初为 LF，已转 CRLF 以对齐仓库 .md 约定（门禁合规修正）
