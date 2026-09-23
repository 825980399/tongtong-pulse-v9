# 部署指南：API Key 配置

**生效批次**：主线第26批 T2（P2-163，API Key 明文硬编码治理）
**适用版本**：PulseNet v10（v8.2 及以后）
**最后更新**：2026-09-11

---

## 一、为什么需要这份指南

在此之前，`config.py` 把智谱（zhipu）与火山方舟（doubao）两个渠道的**真实密钥明文写死**为
`os.environ.get("<变量名>", "<真实密钥>")` 的**默认值**：

```python
"api_key": os.environ.get("ZHIPU_API_KEY", "cdc8****....")   # ← 明文写死（已治理）
"api_key": os.environ.get("ARK_API_KEY",   "ark-****....")   # ← 明文写死（已治理）
```

这意味着密钥随源码一起进入版本库、备份目录（`.bak_batchN/`）与交付物，
任何拿到代码的人都能直接使用配额 → **P2-163**。

治理后：

```python
"api_key": os.environ.get("ZHIPU_API_KEY", "")                      # 缺失即空
"api_key": os.environ.get("ARK_API_KEY", "") or os.environ.get("DOUBAO_API_KEY", "")
```

**密钥缺失时不再崩溃**：渠道调用侧已有「配置不完整即跳过」逻辑，会自动轮询下一个渠道。

> ⚠️ **已泄漏的密钥建议尽快轮换**（治理前的两个密钥曾以明文出现在源码与备份中）。

---

## 二、环境变量一览

| 渠道 | 环境变量名 | 说明 |
|---|---|---|
| zhipu（智谱 GLM） | `ZHIPU_API_KEY` | 智谱开放平台密钥 |
| doubao（火山方舟 Ark） | `ARK_API_KEY`（兼容 `DOUBAO_API_KEY`） | 火山方舟 API Key |
| deepseek | `DEEPSEEK_API_KEY` | 同时用于 advanced 渠道 |
| 旧单端点（兼容层） | `TTP_REMOTE_API_KEY` | `REMOTE_API_CONFIG.api_key` |
| NEWAPI 网关 | `NEWAPI_TOKEN` | 可选 |

---

## 三、配置方法

### 3.1 Windows（PowerShell，当前用户永久生效）

```powershell
[System.Environment]::SetEnvironmentVariable("ZHIPU_API_KEY",   "<你的智谱密钥>", "User")
[System.Environment]::SetEnvironmentVariable("ARK_API_KEY",     "<你的方舟密钥>", "User")
[System.Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY","<你的DeepSeek密钥>", "User")
```

★**重新打开终端**（或重启框架）后生效。

### 3.2 Windows（仅当前会话，临时验证用）

```powershell
$env:ZHIPU_API_KEY    = "<你的智谱密钥>"
$env:ARK_API_KEY      = "<你的方舟密钥>"
$env:DEEPSEEK_API_KEY = "<你的DeepSeek密钥>"
python main.py
```

### 3.3 Linux / macOS

```bash
export ZHIPU_API_KEY="<你的智谱密钥>"
export ARK_API_KEY="<你的方舟密钥>"
export DEEPSEEK_API_KEY="<你的DeepSeek密钥>"
```

永久生效：写入 `~/.bashrc` / `~/.zshrc` 后 `source`。

### 3.4 推荐：`.env` 文件（不入库）

项目根目录建 `.env`（**务必确认已在 `.gitignore` 中**）：

```
ZHIPU_API_KEY=...
ARK_API_KEY=...
DEEPSEEK_API_KEY=...
```

启动时加载：

```bash
set -a && source .env && set +a && python main.py
```

---

## 四、启动自检

`config.py` 在**被 import 时**（进程启动）会自动执行 `check_channel_api_keys()`：
- 缺失的渠道记 **WARNING**，格式如下：

```
WARNING config: [API Key 缺失] 渠道 zhipu 未配置密钥 → 该渠道将被自动跳过。
        请设置环境变量：ZHIPU_API_KEY（详见 docs/部署指南_APIKey配置.md）
```

- **不抛异常、不阻塞启动**；
- 也提供 API 自省：

```python
import config
config.check_channel_api_keys()
# → {"missing": [...], "configured": [...], "env_names": {...}}
```

- 测试/CI 场景可用 `PULSE_SKIP_KEY_CHECK=1` 静默该 WARNING。

---

## 五、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 日志出现 `[渠道] xxx 配置不完整，跳过` | 该渠道密钥为空 | 按上文设置对应环境变量并重启 |
| 所有渠道都不可用，回复走本地兜底 | 全部密钥缺失 | 至少配置一个渠道 |
| `HTTP 401 / 403` | 密钥无效或已轮换 | 到厂商控制台确认密钥状态 |
| `HTTP 400 InvalidParameter`（doubao） | 请求体含方舟不支持的字段 | 已由第25批 `CHANNEL_REQUEST_FIELD_POLICY` 修复；若复发请检查该配置 |
| 想临时静默启动检查 | — | 设 `PULSE_SKIP_KEY_CHECK=1` |

---

## 六、安全建议

1. **轮换**治理前明文出现在源码/备份中的两个密钥（zhipu、doubao）；
2. 不要把密钥写进 `config.py`、测试文件、任务书或交付报告；
3. `.bak_batchN/` 备份目录包含历史源码快照 —— **不要随意外发**；
4. 打印日志时一律脱敏（本项目的 `PulseLung._mask_headers()` 与
   `DEBUG_CHANNEL_HTTP_DUMP` 均只输出前若干位 + `****`）；
5. 建议为不同环境（开发/生产）使用不同密钥，便于单独吊销。

---

## 七、模板文件与回归护栏（主线第33批 T1 补充）

### 7.1 `.env.example`

仓库根部提供 **`.env.example`**（只有占位符、不含真实密钥），新部署者照抄即可：

```bash
cp .env.example .env      # .env 已被 .gitignore 忽略
# 填入真实值后导出到环境变量（本项目不强制依赖 python-dotenv）
```

`.gitignore` 中 `.env.*` 通配会连 `.env.example` 一起忽略，因此已为其单独加了
**反向例外** `!.env.example` —— 模板必须入库，否则新部署者无从照抄。

### 7.2 变量清单

| 变量 | 用途 |
|---|---|
| `ZHIPU_API_KEY` | 智谱渠道 |
| `ARK_API_KEY` / `DOUBAO_API_KEY` | 火山方舟（ARK 优先，DOUBAO 为兼容别名） |
| `DEEPSEEK_API_KEY` | DeepSeek 渠道 + `advanced_model` |
| `TTP_REMOTE_API_KEY` | 兼容层远程 API（`REMOTE_API_CONFIG`） |
| `NEWAPI_TOKEN` | newapi 自建网关 |
| `WECOM_BOT_ID` / `WECOM_SECRET` / `WECOM_ADMIN_USERID` | 企业微信机器人 |
| `PULSE_SKIP_KEY_CHECK` | `=1` 静默启动检查（测试/CI） |

### 7.3 回归护栏

`tests/test_api_key_env_m33.py`（12 例）把本指南的承诺固化为**可执行断言**：

- **优先级**：子进程注入环境变量 → 真实加载 `config` → 断言「值发生变化的渠道
  集合非空且等于注入值」（**与渠道名解耦**，渠道池热改也不会失效）；
- **回退**：不设任何密钥 → 所有渠道 `api_key` 必须为空串（证明**不存在明文兜底**）；
- **别名/优先序**：`ARK` 未设时 `DOUBAO` 生效；两者同时设置时 `ARK` 胜出；
- **源码审计**：`config.py` 内不得再出现 `sk-…` / `ark-…` 形态的字符串字面量，
  且每个渠道的 `api_key` 赋值都必须经由 `os.environ.get(..., "")`。

> 只要有人把明文密钥写回 `config.py`，或改动环境变量优先级/回退语义，这 12 例会立刻失败。

