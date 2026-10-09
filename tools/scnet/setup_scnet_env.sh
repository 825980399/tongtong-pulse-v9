#!/usr/bin/env bash
# ============================================================
# SCNet 超算 Notebook 一键环境准备脚本
# 星轨 · 2026-10-09（基于 2610091220315544 实测沉淀）
#
# 用途：在新建 SCNet Notebook 实例后，一键补齐与本地一致的环境，
#       使「除了 API Key 环境注入外，与本地无差别」。
# 幂等：可重复执行，已完成步骤自动跳过。
#
# 用法：
#   bash setup_scnet_env.sh                          # 全量（含 apt 系统库）
#   bash setup_scnet_env.sh --skip-apt               # 跳过系统库（已装过时）
#   bash setup_scnet_env.sh --with-cython            # 额外尝试 Cython 编译
#
# 前置：项目已上传到 /root/private_data/tongtong-pulse-v9
# ============================================================
set -u

PROJ="${1:-/root/private_data/tongtong-pulse-v9}"
PY_SRC="https://pypi.tuna.tsinghua.edu.cn/simple"
SKIP_APT=0
WITH_CYTHON=0
for _a in "$@"; do
  case "$_a" in
    --skip-apt)   SKIP_APT=1 ;;
    --with-cython) WITH_CYTHON=1 ;;
  esac
done

echo "=================================================="
echo " SCNet 环境准备 · 项目: $PROJ"
echo "=================================================="

cd "$PROJ" || { echo "[FAIL] 项目目录不存在: $PROJ"; exit 1; }

# ---------- 0. 系统库（apt，容错：失败不中断） ----------
if [ "$SKIP_APT" -eq 0 ]; then
  echo ""
  echo "[1/6] 安装系统库（OpenCV/PyAudio/OCR 依赖）..."
  apt-get update -qq 2>/dev/null || echo "  (apt update 失败，跳过)"
  # opencv-python / mediapipe 需要 libGL; pyaudio 需要 portaudio; tesseract 需要 OCR 引擎
  apt-get install -y -qq \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    portaudio19-dev libsndfile1 \
    tesseract-ocr 2>/dev/null || echo "  (部分系统库安装失败，继续)"
else
  echo ""
  echo "[1/6] 跳过系统库（--skip-apt）"
fi

# ---------- 1. Python 依赖（排除 aibot） ----------
echo ""
echo "[2/6] 安装 Python 依赖（requirements.txt，排除 aibot）..."
# aibot 为第162批刀4 已知声明：pip 无此包（企业微信 SDK 需线下获取），排除避免整段失败
sed '/^aibot[[:space:]]*#/d; /^aibot[[:space:]]*$/d' requirements.txt > /tmp/req_pulsenet.txt
if pip install -r /tmp/req_pulsenet.txt -i "$PY_SRC" 2>&1 | tail -5 | grep -qiE "error|failed"; then
  echo "  [WARN] 部分依赖安装报错，请查看完整输出。继续补装 radon..."
else
  echo "  依赖安装完成。"
fi

# ---------- 2. radon（自检复杂度分析依赖） ----------
echo ""
echo "[3/6] 补装 radon（self_inspector 依赖）..."
pip install radon -i "$PY_SRC" 2>&1 | tail -2

# ---------- 3. playwright 浏览器 ----------
echo ""
echo "[4/6] playwright chromium 检查..."
if python3 -c "import playwright; print('playwright OK')" 2>/dev/null; then
  python3 -m playwright install chromium 2>&1 | tail -2 || echo "  [WARN] chromium 安装失败（浏览器自动化不可用，其余不受影响）"
else
  echo "  [WARN] playwright 未安装，跳过浏览器安装"
fi

# ---------- 4. 0.0.0.0 绑定（SCNet 公网访问必需，幂等） ----------
echo ""
echo "[5/6] Web 服务绑定 0.0.0.0（SCNet 访问自定义服务必需）..."
F1="functions/health_ui.py"
F2="functions/web_chat.py"
if grep -q "('0.0.0.0', self.port)" "$F1" 2>/dev/null; then
  echo "  health_ui.py:1749 已绑定 0.0.0.0 ✓"
else
  sed -i "s/ThreadingHTTPServer(('127.0.0.1', self.port), HealthHandler)/ThreadingHTTPServer(('0.0.0.0', self.port), HealthHandler)/" "$F1"
  echo "  health_ui.py:1749 → 0.0.0.0（已修改）"
fi
if grep -q "('0.0.0.0', self.port)" "$F2" 2>/dev/null; then
  echo "  web_chat.py:638 已绑定 0.0.0.0 ✓"
else
  sed -i "s/ThreadingHTTPServer(('127.0.0.1', self.port), WebChatHandler)/ThreadingHTTPServer(('0.0.0.0', self.port), WebChatHandler)/" "$F2"
  echo "  web_chat.py:638 → 0.0.0.0（已修改）"
fi
if grep -q "'0.0.0.0'" "$F2" 2>/dev/null; then
  echo "  web_chat.py:397 allowed_hosts 已含 0.0.0.0 ✓"
else
  sed -i "s/allowed_hosts = ('127.0.0.1', 'localhost')/allowed_hosts = ('0.0.0.0', '127.0.0.1', 'localhost')/" "$F2"
  echo "  web_chat.py:397 allowed_hosts → 增加 0.0.0.0（已修改）"
fi

# ---------- 5. 清空待审批补丁（防旧补丁自动应用污染观测） ----------
echo ""
echo "[6/6] 清空待审批补丁（带备份）..."
PATCHES="data/patches/pending_patches.json"
if [ -f "$PATCHES" ]; then
  TS=$(date +%Y%m%d_%H%M%S)
  cp "$PATCHES" "tmp/pending_patches_scnet_backup_${TS}.json" 2>/dev/null && echo "  备份: tmp/pending_patches_scnet_backup_${TS}.json"
  python3 -c "import json; json.dump([], open('$PATCHES','w'))" && echo "  pending_patches.json 已清空 ✓"
else
  echo "  无 pending_patches.json，跳过"
fi

# ---------- 6. Cython（可选，探测到脚本才编译） ----------
if [ "$WITH_CYTHON" -eq 1 ]; then
  echo ""
  echo "[可选] Cython 编译尝试..."
  if [ -f setup_cython.py ]; then
    python3 setup_cython.py build_ext --inplace 2>&1 | tail -5
  else
    echo "  [已知问题] 主仓无 setup_cython.py（仅归档 .bak_batch163 内有），编译链路已断，跳过。"
  fi
fi

echo ""
echo "=================================================="
echo " 环境准备完成。下一步："
echo "  1) bash tools/scnet/verify_scnet_env.sh   # 验证环境"
echo "  2) 注入 API Key（环境变量或 data/config_override.json）"
echo "  3) python3 main.py                        # 前台启动"
echo "=================================================="
