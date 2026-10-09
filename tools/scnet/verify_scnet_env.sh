#!/usr/bin/env bash
# ============================================================
# SCNet 超算 Notebook 环境验证脚本
# 星轨 · 2026-10-09（配套 setup_scnet_env.sh 使用）
#
# 用法：bash tools/scnet/verify_scnet_env.sh
# 输出：每项 PASS/FAIL + 末尾汇总；API Key 只报缺失项不打印值
# ============================================================
set -u

PROJ="${1:-/root/private_data/tongtong-pulse-v9}"
PASS=0
FAIL=0

note() { if [ "$1" = "PASS" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi; printf "  [%s] %s\n" "$1" "$2"; }

cd "$PROJ" 2>/dev/null || { echo "[FATAL] 项目目录不存在: $PROJ"; exit 1; }

echo "=================================================="
echo " SCNet 环境验证 · 项目: $PROJ"
echo "=================================================="

# ---------- 1. 关键 Python 包 import 检查 ----------
echo ""
echo "[1] 关键包导入检查..."
PACKAGES=(cv2 pyaudio sounddevice radon faiss jieba pyarrow orjson chardet torch \
          vosk pytesseract fitz GPUtil requests psutil numpy pillow feedparser \
          cryptography playwright)
for pkg in "${PACKAGES[@]}"; do
  if python3 -c "import $pkg" 2>/dev/null; then
    note PASS "$pkg"
  else
    note FAIL "$pkg（缺失或导入失败）"
  fi
done

# ---------- 2. 0.0.0.0 绑定检查 ----------
echo ""
echo "[2] Web 服务 0.0.0.0 绑定检查..."
if grep -q "('0.0.0.0', self.port)" functions/health_ui.py 2>/dev/null; then
  note PASS "health_ui.py 绑定 0.0.0.0"
else
  note FAIL "health_ui.py 仍绑定 127.0.0.1"
fi
if grep -q "('0.0.0.0', self.port)" functions/web_chat.py 2>/dev/null; then
  note PASS "web_chat.py 绑定 0.0.0.0"
else
  note FAIL "web_chat.py 仍绑定 127.0.0.1"
fi
if grep -q "'0.0.0.0'" functions/web_chat.py 2>/dev/null; then
  note PASS "web_chat.py allowed_hosts 放行 0.0.0.0"
else
  note FAIL "web_chat.py allowed_hosts 未放行"
fi

# ---------- 3. 端口监听检查（运行时用；未启动时提示） ----------
echo ""
echo "[3] 端口监听检查（需框架运行中）..."
if grep -qE ":13BB|:13BC" /proc/net/tcp 2>/dev/null; then
  OUT=$(grep -iE ':13BB|:13BC' /proc/net/tcp | grep -c "^ *[0-9]*: 00000000:13BB\|^ *[0-9]*: 00000000:13BC")
  if [ "$OUT" -ge 1 ]; then
    note PASS "5051/5052 监听 0.0.0.0"
  else
    note FAIL "端口在听但非 0.0.0.0（绑定修改未生效？）"
  fi
else
  note FAIL "5051/5052 未监听（框架未启动或启动失败）"
fi

# ---------- 4. 待审批补丁 ----------
echo ""
echo "[4] 待审批补丁检查..."
PATCHES="data/patches/pending_patches.json"
if [ -f "$PATCHES" ]; then
  CNT=$(python3 -c "import json;print(len(json.load(open('$PATCHES'))))" 2>/dev/null || echo "?")
  if [ "$CNT" = "0" ]; then
    note PASS "pending_patches 为空（${CNT} 个）"
  else
    note FAIL "pending_patches 有 ${CNT} 个（需清空防自动应用）"
  fi
else
  note FAIL "pending_patches.json 不存在"
fi

# ---------- 5. API Key 缺失清单（只报缺失，不打印值） ----------
echo ""
echo "[5] API Key 环境注入检查（只报缺失项）..."
KEYS=(ARK_API_KEY DEEPSEEK_API_KEY ZHIPU_API_KEY WECOM_BOT_ID WECOM_SECRET SILICONFLOW_API_KEY)
MISS=0
for k in "${KEYS[@]}"; do
  if [ -z "${!k:-}" ]; then
    echo "  [MISS] $k 未注入"
    MISS=$((MISS+1))
  fi
done
if [ "$MISS" -eq 0 ]; then
  note PASS "API Key 全部已注入"
else
  note FAIL "API Key 缺失 ${MISS} 项（LLM 渠道将降级本地兜底，框架仍可运行）"
fi

# ---------- 汇总 ----------
echo ""
echo "=================================================="
echo " 汇总: PASS=${PASS}  FAIL=${FAIL}"
if [ "$FAIL" -eq 0 ]; then
  echo " ✅ 环境与本地一致，可启动：python3 main.py"
else
  echo " ⚠️ 有 ${FAIL} 项未通过，参照 setup_scnet_env.sh 补齐后再启动"
fi
echo "=================================================="
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
