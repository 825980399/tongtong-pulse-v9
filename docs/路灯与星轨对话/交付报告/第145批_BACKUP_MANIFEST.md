# 第145批 · 备份清单（BACKUP_MANIFEST）

- 批次：第145批 占位符渲染 + 导出安全 + BOM 收尾
- 备份目录：`.bak_batch145/`
- 备份时间：2026-09-27 20:59 ~ 21:04
- 基线：第144批提交 `d6f6cfb`
- 备份方式：Python `os.walk` 逐文件二进制复制（仅 `.py` 及必要文本），保留原始行尾

## 备份文件清单（7 项）

| # | 相对路径 | 大小(bytes) | 用途 | 本批是否改动 |
|---|---------|------------|------|-------------|
| 1 | `.gitignore` | 4946 | T-145e 核对（结论：已含 nul，无需改） | ❌ 未改 |
| 2 | `config.py` | 309216 | T-145a 占位符渲染器新增 | ✅ +108 |
| 3 | `nucleus/data/exclude_dirs.py` | — | T-145c 参考读取（未写入） | ❌ 未改 |
| 4 | `organs/body/PulseLung.py` | — | T-145a system prompt 渲染（3 处） | ✅ +9/-1 |
| 5 | `organs/brain/PulseInnerWorld.py` | — | T-145a/f/g persona 渲染 + 删重复 + 日志提级 | ✅ +13/-32 |
| 6 | `tools/export_public.py` | — | T-145b 白名单移出内部总账 | ✅ +3/-1 |
| 7 | `tools/package_full_project.py` | — | T-145c 复用 fail-closed 白名单 | ✅ +42/-2 |

## 行尾符核验

本批改动文件按铁律138/139 处理：
- 匹配前统一归一为 LF，写回时按原 `had_crlf` 还原；
- 判定行尾一律用二进制读（`open(path,'rb')`），禁用文本模式误判。

核验结果：改动后各文件行尾与 `.bak_batch145/` 基线逐字节一致（0 flip）。

## 回滚方法

```bash
# 单文件回滚
cp .bak_batch145/config.py config.py

# 全批回滚（git）
git reset --hard d6f6cfb   # 回到第144批基线（工作树污染会一并丢失，谨慎）
```

> ⚠️ `git reset --hard` 会同时丢弃工作树的既有污染改动，**不建议**在污染未清理时使用。
> 推荐单文件 `cp` 回滚 + `git revert bdc204c`。

## 备份目录生命周期

`.bak_batch145/` 为**单点故障**（git-ignored），批次验收后由外部清理流程回收，
不长期驻留。下一批（第146批）备份将落 `.bak_batch146/`。
