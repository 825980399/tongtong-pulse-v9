# 部署指南：API Key 与运行环境变量配置

本文仅说明如何用一个 `.env` 文件（或等价的环境变量）完成最小部署，**不包含任何内部流程、批次号或人名**。

曈曈 PulseNet 是**研究原型**，所有大模型渠道都是**可选**的：未配置任何 Key 时框架仍可启动，仅对应能力自动降级。

---

## 一、最小启动

```bash
# 1) 安装依赖（Python 3.12，Windows 为主要验证环境）
pip install -r requirements.txt

# 2) 准备环境变量
cp .env.example .env        # .env 已被 .gitignore 忽略，绝不会入库

# 3) 编辑 .env，按需填入下方任意渠道 Key（可留空）

# 4) 启动
python main.py
#    浏览器打开 http://localhost:5051 查看健康面板
```

> 本项目**不强制**依赖 `python-dotenv`。如需自动从 `.env` 加载，请自行
> `pip install python-dotenv` 并在入口处 `from dotenv import load_dotenv; load_dotenv()`。
> 也可在启动前手动导出环境变量（如 PowerShell：`$env:ZHIPU_API_KEY="xxx"`）。

---

## 二、环境变量一览（示例占位符，真实值请自行填写）

| 变量 | 用途 | 是否必填 |
|---|---|---|
| `ZHIPU_API_KEY` | 智谱渠道（免费，优先使用） | 否 |
| `ARK_API_KEY` | 火山方舟 / 豆包（免费额度；与下者二选一） | 否 |
| `DOUBAO_API_KEY` | 豆包兼容别名（若两者都设，优先取 `ARK_API_KEY`） | 否 |
| `DEEPSEEK_API_KEY` | DeepSeek（付费 + 高级推理） | 否 |
| `TTP_REMOTE_API_KEY` | 兼容层远程 API（可选） | 否 |
| `NEWAPI_TOKEN` | newapi 自建网关令牌（仅启用网关时使用） | 否 |
| `WECOM_BOT_ID` / `WECOM_SECRET` / `WECOM_ADMIN_USERID` | 企业微信机器人凭证（环境变量优先于本地配置） | 否 |
| `PULSE_SKIP_KEY_CHECK` | `=1` 静默「API Key 缺失」启动检查（测试 / CI） | 否 |
| `PULSE_DUP_TRACE` / `PULSE_HTTP_DUMP` / `PULSE_REQUEST_DEDUP` | 调试开关（默认关闭） | 否 |

> 仅填你实际要用的渠道即可。未设置的渠道会被自动跳过，不影响其余功能。

---

## 三、安全须知

- **绝不把真实密钥提交入库**。`SECRET` / `TOKEN` / `API_KEY` 类内容只存在于本地 `.env`，
  该文件已被 `.gitignore` 忽略。
- 若历史上任何版本曾把密钥明文写入源码或文档，**视为已泄露，必须到对应平台轮转（rotate）**。
- 发布包由 `tools/export_public.py` 导出，导出时会自动复扫自身与全部文件，命中敏感信息即阻断。
- 漏洞与安全相关事项请见仓库根目录 `SECURITY.md`；外部贡献流程见 `CONTRIBUTING.md`。

---

## 四、常见问题

- **启动报「缺少 API Key」？** 属正常提示。配置至少一个渠道 Key 即可消除；或设
  `PULSE_SKIP_KEY_CHECK=1` 跳过启动检查（仅建议测试 / CI 使用）。
- **想用本地模型 / 兼容 OpenAI 的网关？** 通过 `TTP_REMOTE_API_KEY` 与对应渠道配置接入，
  具体字段见 `.env.example` 注释。
- **更多架构与演示步骤**：见仓库根目录 `README.md` 与 `docs/demo-quickstart.md`。

*本文档为公开发布内容，不含任何内部标识。*
