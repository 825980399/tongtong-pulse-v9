# 第130批 任务书：W7三件式独窗 + git push匿名化

> 派发：星轨　｜　执行：路灯　｜　前置分析：烛微第130批
> 模式：⚠️ **需停框架执行**（改config.py+PulseNodePool.py+VC.py）
> 红线：不改config.py其他运行开关、不碰data/knowledge/、改前备份

---

## 0. 本批定位

**W7独窗：影子观察启动+十五票装载。**
本批四个方向：坏补丁紧急清理+W7翻值三件式+git push匿名化+hash映射表。

---

## 1. T-130a（P0）：坏补丁紧急清理

### 背景
刚才框架停止时，自动弹出并应用了一个坏补丁——和昨晚事故一模一样：LLM把`DataAccessLayer.py`的`safe_read_json`函数定义行删了！

已手动回滚，但需要彻底清理：
1. pending_patches.json里所有approved状态的补丁全部清零
2. 确认没有其他坏补丁排队
3. 验证DataAccessLayer.py完整

### 操作
1. 检查`data/patches/pending_patches.json`，把所有非rejected状态的补丁全部标记为rejected
2. 确认`nucleus/data/DataAccessLayer.py`完整（`safe_read_json`函数定义在第129行）
3. 备份当前pending_patches.json

### 验收
- pending_patches.json里approved状态补丁数=0
- safe_read_json函数完整存在
- DataAccessLayer.py py_compile过

---

## 2. T-130b（P1）：W7三件式独窗

### 背景
W6验收通过，现在翻DIRECT启动影子观察。
烛微建议：三件式一次重启（翻值+闭环行修+两except转warning）。

### 三件内容

#### ① 翻值单行票
- config.py:1548 `ENABLE_FACE_WELCOME_DIRECT=False` → `True`
- SHADOW保持True=只记不跳零行为变更
- 为什么不能走env（已证伪：0命中）

#### ② 闭环行补l3两字段
- PulseNodePool :1963-1966 f-string 追加 `, L3候选{l3_downgrade_candidates}, 降级{l3_downgraded}`
- 修烛微第130批发现的P1新缺陷（candidates读数无日志载体）

#### ③ 两处except转warning
- 执行段:1789/:1967 两处`except:continue`转warning
- 闭环首跑期最需要可见性的位置仍静默

### 验收
- py_compile过
- m95单测39 passed + 4 skipped
- 翻值后：影子行开始记录
- 闭环行输出含L3候选/降级字段

---

## 2. T-130b（P1）：git push匿名化

### 背景
本地15票未推，作者都是QQ邮箱+真名，需要匿名化后推双远端。

### 操作步骤
1. `git branch pre-rebase-keep`（备份枝，回滚用）
2. `git rebase -i 8d9b95f --exec 'git commit --amend --author="Tongtong Dev <dev@users.noreply.gitee.com>" --no-edit'`
3. 验证：`git log --format='%an %ae' 8d9b95f..HEAD` 全绿（全是Tongtong Dev）
4. 生成hash映射表（新旧commit对照）
5. `git push origin master`
6. `git push openi master`
7. `git push origin refs/archive/old-102`

### 验收
- 双远端tips=最新HEAD
- 作者全部=Tongtong Dev <dev@users.noreply.gitee.com>
- hash映射表已生成

---

## 3. T-130c（P2）：hash映射表commit

### 操作
- 生成`docs\git_commit_hash_mapping.md`（15票新旧对照）
- commit入库

### commit message
`docs: git rebase匿名化hash映射表`

---

## 4. 门禁要求

| 门禁 | 标准 |
|---|---|
| ruff F | =0 |
| py_compile | 改动文件全过 |
| m95单测 | 39 passed + 4 skipped |
| 静默except CI | PASS（新增=0） |
| 行尾保全 | .py CRLF不变 |

---

## 5. 交付要求

1. W7三件式DIFF
2. git rebase匿名化操作记录
3. hash映射表
4. git push结果
5. 门禁结果

---

*星轨 · 2026-09-26 · 第130批*
