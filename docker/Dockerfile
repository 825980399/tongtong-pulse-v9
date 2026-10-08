# ===== PulseNet 超算运行镜像 Dockerfile（20261008 星轨版） =====
# 目标：Linux 容器重跑框架，验证运行上限（内存渗漏/节点增长/长稳窗）
# 设计原则：
#   1. 密钥一律运行时注入（-e 环境变量），镜像零密钥
#   2. data/logs/tests/docs 不进镜像（.dockerignore 已排除）
#   3. 剔除 aibot（无 PyPI）/pyaudio（需系统库）/Cython（无 Linux 分支）
#   4. 外脑 API 不可达时走本地降级（config 已配 qwen2 本地兜底）

FROM python:3.11-slim

# 系统依赖：faiss-cpu 需要 libgomp；基础构建工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖层（利用 Docker 缓存：依赖变动少，代码变动多）
COPY docker/requirements-mirror.txt ./requirements-mirror.txt
RUN pip install --no-cache-dir -r requirements-mirror.txt

# 代码层
COPY main.py config.py ./
COPY nucleus/ ./nucleus/
COPY organs/ ./organs/
COPY base/ ./base/
COPY functions/ ./functions/
COPY hardware/ ./hardware/
COPY tools/ ./tools/
COPY pulses/ ./pulses/
COPY somatics/ ./somatics/
COPY utils/ ./utils/

# 默认启动：框架主入口（绝对路径，兼容平台自定义工作目录）
CMD ["python", "/app/main.py"]
