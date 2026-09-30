# 第126批 任务书：冷却闸补双处 + git commit第10票 + 静默except hook

> 派发：星轨　｜　执行：路灯　｜　前置分析：烛微第126批
> 模式：框架已停，改代码后重启生效
> 红线：不改config.py运行开关、不碰data/knowledge/、改前备份

---

## 0. 本批定位

**W6前最后一批收尾。**
本批三个方向：补两处冷却闸（防止WARNING刷屏）+ git commit第125批改动 + 静默except防再长hook。

---

## 1. T-126a（P1）：冷却闸补两处

### 背景
烛微发现首批7处里有两处没补冷却闸，会导致WARNING刷屏：
- main:3701 假死探测：在while+sleep(10)环内，持续失败态=**8640条/日WARNING**
- MC:429 快照统计：每心跳一拍（最快3s）同级WARNING

### 改造内容
- 两处都加300s冷却闸（同一文件60s内只打一次WARNING）
- 复用_logger的限流模式
- 参考：safe_read_json的_last_warning_time限流

### 验收
- py_compile过
- 离线测试：连续触发只打一次WARNING
- CI门禁PASS

---

## 2. T-126b（P2）：git commit第125批（第10票）

### 背景
第125批改动还没提交，W6重启前先commit，防止在制品被回滚吞掉。

### 提交内容
- T-125a R1保险丝修复（PulseNodePool.py）
- T-125b D040写侧A'（PulseLiver.py）
- T-125c 静默except批2（7个文件）
- 第125批交付报告+3份DIFF

### commit message
`第125批 R1保险丝修复+D040写侧A'+静默except批2 交付（T-125a/b/c）`

### 红线
- 不含data/knowledge/
- 不含.bak_batch*/
- 只提交代码和docs

---

## 3. T-126c（P1）：静默except防再长hook

### 背景
"清完又长回来"模式已被实证（Pool今天+2）。
烛微定稿：hook案——复用CI gate结构指纹。

### 改造内容
1. 新建pre-commit hook：`.git/hooks/pre-commit`
2. 跑`cw2_t2e_ci_gate_silent_except.py`
3. 四断言：
   - parse_err=0
   - 只降不升（静默except数不增加）
   - 名单外新增=0
   - **`\r\r\n`=0**（CRCRLF事故正主）
4. baseline文件从tmp迁到`tools/ci/baselines/`

### 验收
- hook能拦住新增静默except
- hook能拦住CRCRLF行尾
- 现有代码全过

---

## 4. 门禁要求

| 门禁 | 标准 |
|---|---|
| ruff F | =0 |
| py_compile | 改动文件全过 |
| m95单测 | 43 passed |
| 静默except CI | PASS（新增=0） |
| 行尾保全 | .py CRLF不变 |

---

## 5. 交付要求

1. 冷却闸两处DIFF
2. git commit记录
3. hook文件+baseline迁移
4. 门禁结果

---

*星轨 · 2026-09-25 · 第126批*
