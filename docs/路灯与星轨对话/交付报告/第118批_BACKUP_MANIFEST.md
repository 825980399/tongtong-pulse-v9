> ⚠️ 文件映射：本文档撰写时 R4 验收清单文件名为 `docs/验收/R4验收清单_B1-B23.md`，已于**第121批**更名为 `docs/验收/R4运行时验收总单.md`（去号化 + 版本表 v1.2）。



# 第118批 备份清单（BACKUP_MANIFEST）

> 备份目录：`.bak_batch118/`（11 个改前快照，sha256 校验，生成于 2026-09-24 08:43:58）
> 用途：任何一行需回滚，直接 `copy .bak_batch118/<rel> <rel>` 即可（注意 CRLF 保全：备份与当前换行一致，覆盖写不翻行尾）。
> 纪律：本批未动 `data/knowledge/`、未动 `config.py` 运行开关、未停框架；改 .py 下次重启生效。

| # | 文件 | 大小(字节) | sha256(前16) | 任务 |
|---|---|---:|---|---|
| 1 | main.py | 224857 | 5a367536f41c9107 | T-118b 裸logging→pulse树 |
| 2 | organs/senses/PulseVisualCortex.py | 44367 | 65b036b11a098e9e | T-118a face R2 层1 |
| 3 | organs/identity/PulseSelfAwareness.py | 151579 | f0bdac42acf4e507 | T-118a face R2 层1 |
| 4 | organs/senses/PulseEars.py | 26668 | 7d93d6782660f82d | T-118a face R2 层1 |
| 5 | organs/body/PulseHeart.py | 41057 | 1f9c0e13ff39a961 | T-118a face R2 层1 |
| 6 | functions/chat/chat_service.py | 50629 | cbe5d5d95eb20dea | T-118a face R2 层1 |
| 7 | functions/web_chat.py | 25507 | ed0d814d0a73d491 | T-118a face R2 层1 |
| 8 | tests/test_m95_followups.py | 29218 | 534c1ad0ac7af5e2 | T-118d① m95 期望键同步 |
| 9 | docs/分析报告/技术债务台账_代码实查_20260919.csv | 76430 | d5b66460e5732b52 | T-118c 账本三件套 |
| 10 | docs/完整进化路线与技术债务清单_v1.0.md | 1223829 | d0abf61f5a49b6b6 | T-118c 账本三件套 |
| 11 | docs/验收/R4验收清单_B1-B23.md | 9725 | 7c0ec2af8c5a2353 | T-118d② R4 B16重定/B24顺延 |

## 行尾（CRLF）保全结论（门禁实测）

| 文件 | 改前 CRLF | 改后 CRLF | 一致 |
|---|---|---|---|
| main.py | 是 | 是 | ✅ |
| PulseVisualCortex.py | 是 | 是 | ✅ |
| PulseSelfAwareness.py | 是 | 是 | ✅ |
| PulseEars.py | 否 | 否 | ✅ |
| PulseHeart.py | 否 | 否 | ✅ |
| chat_service.py | 是 | 是 | ✅ |
| web_chat.py | 否 | 否 | ✅ |
| test_m95_followups.py | 否 | 否 | ✅ |
| 技术债务台账…csv | 是(BOM) | 是(BOM) | ✅ |
| 完整进化路线…md | 是 | 是 | ✅ |
| R4验收清单…md | 否 | 否 | ✅ |

> 字节级补丁脚本全程 `decode(utf-8-sig)` → 检测 CRLF → `\n` 替换 → 唯一性 assert → 幂等 SKIP → 按需还原 CRLF → `write(encoding="utf-8")`，故无一行翻行尾。

## 回滚命令示例（如需）

```bat
copy /Y .bak_batch118\main.py main.py
copy /Y .bak_batch118\organs\identity\PulseSelfAwareness.py organs\identity\PulseSelfAwareness.py
rem …其余同理，逐一覆盖，勿批量 rd
```
