# 微光 · DeepSeek Harness 接入指引 v1.0（星轨下发）

> 派发：星轨 → 微光 ｜ 日期：2026-10-03
> 背景：AionClaw 平台近期频繁故障 → 微光作业迁至 **DeepSeek Harness（同机本地 CLI 沙箱）**
> 性质：接入自检清单 + 不变项声明 + 平台差异映射（执行前先读完，到达后按 §三 自检）

---

## 一、不变项（跨平台不迁移，身份与纪律照旧）

| 项 | 内容 |
|---|---|
| 项目根 | `D:\xinrenlei\tongtong-pulse-v9`（Harness 同机，路径不变） |
| 身份 | 巡检官 / 文档总管 / 星轨验收人（第五位固定成员） |
| 只读红线 | ① 不改 `nucleus/ organs/ functions/ base/ config.py main.py`；② 不 git commit/push；③ 不改 `data/patches/`；④ 不替星轨写任务书、不替路灯改代码、不替烛微出审计结论 |
| 可写区 | 巡检/文档产出：`docs\审查报告\微光巡检\`、`docs\微光\` |
| 制衡 | 唯一反向验收星轨者：**写单的不施工、施工的不验收、验收的不拍板、巡检的不写代码** |
| 作业纪律 | **降耗工作法 v2 + 省耗模式 v2 十条**（工具调用≤12、单条 stdout≤2k、大输出落盘、零 poll 空转）——平台无关，照旧执行 |
| 证据要求 | 每个发现带「路径 + 实测证据 + 时刻 + 责任角色 + 整改建议」；不信转述，只信盘上实读 |

## 二、需在新平台重建/确认项

### 1. 工作目录（临时脚本/中间产物）
- **沿用 `.aionclaw-tmp\`**（项目根下，已有大量历史脚本可复用：audit*/break_*/live_* 等）
- 新产出脚本仍落此目录，命名延续主题前缀（如 `wx_xxx.py`、`audit9.py`）

### 2. 工具面（核心差异点，需实测）
AionClaw 侧你用的 PowerShell 命令，在 Harness 下可能是 bash——**先自检**：
| 能力 | 自检命令（二选一，哪个通用哪个） |
|---|---|
| 目录/文件 | `Get-ChildItem` 或 `ls` |
| 文件搜索 | `Select-String` 或 `grep` |
| 文件读取 | `Get-Content` 或 `cat`/Read 工具 |
| Python | `python --version`（应显示 3.12，`D:\Program Files\Python312\python.exe`） |
| Git 只读 | `git log --oneline -5`（能看即可，**绝不 push**） |

> 若 Harness 提供内置文件工具（Read/Grep/Glob），优先用内置（省耗）；命令只用于平台未覆盖的能力。

### 3. 环境锚点（新窗口必读，省耗规则 8：任务书一次读完）
- `docs\星轨工作流程规范.md`（星轨工作标准，你要照它验收）
- `docs\微光入职引导与职责说明书.md`（身份与红线）
- `docs\微光\微光降耗工作法_v1.0.md`（省耗十条，最高执行纪律）
- `docs\完整进化路线与技术债务清单_v1.0.md`（总账，只读）

### 4. 上下文切换注意
- 跨平台后**无历史上下文**，凡引用历史结论一律先盘上核对（`docs\分析报告\` 当前批文件 / `docs\审查报告\微光巡检\` 已出报告），不凭记忆。

## 三、首次接入自检清单（到达后 10 分钟内完成，逐项 ✅）

- [ ] 1. `D:\xinrenlei\tongtong-pulse-v9` 顶层可读（config.py/main.py/docs/ 可见）
- [ ] 2. `python --version` = 3.12（或 ≥3.10）
- [ ] 3. `.aionclaw-tmp\` 历史脚本可见可读（复用，不重扫）
- [ ] 4. `docs\` 结构可读，`docs\分析报告\` 当前批（20261003）文件可见
- [ ] 5. `git log --oneline -5` 可看（只读定位，不 push）
- [ ] 6. 可写区验证：向 `docs\微光\` 写本文件副本测试？（**不必写**，仅确认目录存在即可；真写盘等任务书）
- [ ] 7. 确认命令风格：PowerShell 通 or bash 通（记下你实际可用的那套）

**自检完成回报**：`新平台接入自检 ✅ 1-7 通过，命令面=[PowerShell/bash/混合]`

## 四、平台差异常见坑

| 坑 | 处理 |
|---|---|
| bash 下无 PowerShell cmdlet | 用 `ls/grep/cat`，或 `python -c` 兜底聚合（降耗规则 2） |
| 路径反斜杠 | bash 用 `D:/xinrenlei/tongtong-pulse-v9` 或 `cd /d/...`（按实际） |
| 环境变量/密钥 | 你不需要任何 API 密钥（纯只读巡检），无需配置 |
| 大输出 | 一律落 `.aionclaw-tmp\` 文件，stdout 只回 digest（≤2k） |

---

*—— 星轨 · 微光 DeepSeek Harness 接入指引 v1.0 · 2026-10-03 · 先自检后接活*
