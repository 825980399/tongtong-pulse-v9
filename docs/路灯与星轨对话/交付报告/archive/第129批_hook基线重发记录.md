# 第129批 · T-129c hook 基线重发记录

## 操作
重跑静默 except 扫描，覆盖 `tools/ci/baselines/silent_except_baseline.json`。

```
python -X utf8 tools/ci/cw2_t2e_ci_gate_silent_except.py --emit-baseline tools/ci/baselines/silent_except_baseline.json
```

## 结果对比

| 项 | 旧基线（备份） | 新基线（重发） |
|---|---|---|
| 覆盖文件数 | 308 | 303 |
| 静默点总数 | 1449 | 1416 |

> ⚠️ **任务书偏差**：任务书称「新基线=456 个静默点」，实测重扫 = **1416**（旧基线亦为 1449）。
> 结论：原任务书的 456 为失真数字（疑似与某子集/历史误记混淆）；本批按真实工作树重发，基线已校正为准确值。
> 旧基线与新基线差异 = 净减 33 点（含批3 拟清的 25 指纹实际仍在代码→此次一并校正），文件净减 5 个（已删除/改名文件出榜）。

## CI 门禁结果（base=HEAD vs target=worktree）
```
$ python -X utf8 tools/ci/cw2_t2e_ci_gate_silent_except.py --base HEAD --target worktree
  [1][2] 静默except：PASS —— 本次变更未新增（只降不升 / 名单外新增=0）
  [3] parse_err：PASS —— 变更 .py 均可解析
  [4] CRCRLF(双CR行尾)：PASS —— 无 CRCRLF 行尾污染
  结论: PASS
```
- 本次变更 .py 文件数 = 2（PatchManager.py / VC.py），均未新增静默 except → **新增=0，CI PASS**。
- 注：经全库 grep，无任何代码读取 `silent_except_baseline.json`（该文件为纯记录/基线快照），CI 差集逻辑不依赖其计数。
