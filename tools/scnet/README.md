# SCNet 超算一键部署工具（tools/scnet/）

> 星轨 · 2026-10-09 · 基于 SCNet Notebook 实例 2610091220315544 实测沉淀
> 目标：**新开 Notebook 后跑一个脚本，环境即与本地一致（仅 API Key 留待运行时注入）**

## 文件说明

| 文件 | 用途 |
|---|---|
| `setup_scnet_env.sh` | 一键环境准备（幂等，可重复执行） |
| `verify_scnet_env.sh` | 环境验证（输出 PASS/FAIL 汇总，可作启动前检查） |

## 标准流程（新 Notebook 实例）

```bash
# 0. 确认项目已上传并解压到 /root/private_data/tongtong-pulse-v9
cd /root/private_data/tongtong-pulse-v9

# 1. 一键准备环境（系统库 + 依赖 + 绑定 + 清补丁，约 3-5 分钟）
bash tools/scnet/setup_scnet_env.sh

# 2. 验证环境（应全部 PASS）
bash tools/scnet/verify_scnet_env.sh

# 3. 注入 API Key（二选一）
#    方式A：环境变量
export DEEPSEEK_API_KEY=xxx ARK_API_KEY=xxx ZHIPU_API_KEY=xxx
#    方式B：data/config_override.json（推荐，持久化，参照 docs/部署指南_APIKey配置.md）

# 4. 前台启动（勿用 nohup，JupyterLab 终端是 tty，前台才正常）
python3 main.py

# 5. 验证服务（无 ss 命令，用替代法）
grep -iE ':13BB|:13BC' /proc/net/tcp   # 00000000:13BB = 0.0.0.0:5051 ✅
```

## setup_scnet_env.sh 做了什么

| 步骤 | 内容 | 依据（本次实测） |
|---|---|---|
| 1/6 系统库 | libgl1/libglib2.0-0/libsm6/libxext6/libxrender1（OpenCV/mediapipe）、portaudio19-dev（PyAudio）、libsndfile1（sounddevice）、tesseract-ocr（OCR） | OpenCV/PyAudio 曾报"未安装"实为缺系统库 |
| 2/6 Python 依赖 | requirements.txt **排除 aibot 行**后全量安装（清华源） | aibot 是 162批刀4 已知声明，pip 无此包；`sed` 排除避免整段失败 |
| 3/6 radon | 补装（self_inspector 复杂度分析依赖） | 启动日志曾报 ModuleNotFoundError |
| 4/6 playwright | chromium 浏览器安装 | 控制器/无头浏览器需要 |
| 5/6 绑定 | health_ui.py:1749 / web_chat.py:638 / :397 改 0.0.0.0（幂等） | 「访问自定义服务」检测不到 127.0.0.1 服务 |
| 6/6 补丁 | 备份后清空 pending_patches.json | 防旧 data/ 带入的补丁自动应用污染观测 |
| 可选 Cython | `--with-cython` 时探测 setup_cython.py 编译 | **主仓无该文件**（仅归档 .bak_batch163 有），已知断链，跳过 |

## 已知遗留问题（影响超算，待本地处理）

| # | 问题 | 影响 | 建议处置 |
|---|---|---|---|
| 1 | 主仓无 setup_cython.py，Cython 编译链路断 | 性能降 3 倍（Python 回退） | 本地立票：定位编译脚本真实路径或补回 |
| 2 | Ollama 自动启动 `Popen()` 收到意外参数 `timeout` | 超算无 Ollama，无实际影响 | 本地立小票给路灯修（改用 `Popen(...).wait(timeout=...)`） |
| 3 | aibot 包 pip 无（企业微信 SDK） | 仅企业版「新器」不可用 | 按 162批刀4 声明：部署时从企业微信开放平台线下获取 |
| 4 | nvidia-smi 缺失 | 硬件探测降级 | 预期（DCU/DTK 环境）；可排期加 rocm 探测分支 |

## 安全说明

- 0.0.0.0 绑定暴露公网，**仅限测试环境**；本地保持 127.0.0.1
- 镜像路线若启用，应在 Dockerfile 中通过环境变量控制绑定地址（默认内网）
- verify 脚本只报 API Key **缺失项**，不打印任何值
