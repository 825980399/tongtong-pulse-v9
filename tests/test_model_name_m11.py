# -*- coding: utf-8 -*-
"""主线第11批 T1 P2-80：DeepSeek 模型名更新 —— 回归测试。

内部协作者裁决（2026-09-10 20:45）：只改 advanced_model -> deepseek-flash；
default_model 保留 deepseek-v4-flash（旧名仍被官方临时路由到 V4.1 Flash）。

覆盖：
1. config 中 advanced_model 已更新、default_model 按裁决保留
2. 下游 .get("advanced_model", <默认>) 兜底值已同步
3. 全库活跃代码无 deepseek-v4-pro 硬编码残留（注释除外）
4. 旧模型名兜底常量仍可用（避免断路由的兼容保护）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_config_advanced_model_updated():
    import config
    assert config.REMOTE_API_CONFIG["advanced_model"] == "deepseek-flash"
    assert config.REMOTE_API_CHANNELS["advanced_model"] == "deepseek-flash"


def test_config_default_model_preserved():
    """裁决选 2：default_model 保留旧名，等多渠道落地后下批切免费 API。"""
    import config
    assert config.REMOTE_API_CONFIG["default_model"] == "deepseek-v4-flash"


def test_llm_call_config_default_preserved():
    import config
    assert config.LLM_CALL_CONFIG["default_model"] == "deepseek-v4-flash"


def test_downstream_fallback_values_synced():
    """下游 .get("advanced_model", X) 的兜底值应为新名，避免断路由。"""
    targets = [
        "nucleus/evolution/LLMEvolutionEngine.py",
        "nucleus/evolution/SelfReflectionEngine.py",
        "organs/body/PulseLung.py",
    ]
    for rel in targets:
        p = os.path.join(ROOT, rel)
        with open(p, encoding="utf-8") as f:
            src = f.read()
        assert '.get("advanced_model", "deepseek-v4-pro")' not in src, \
            f"{rel} 仍使用旧 advanced 兜底值"
        assert '.get("advanced_model", "deepseek-flash")' in src, \
            f"{rel} 未同步新 advanced 兜底值"


def test_no_active_hardcoded_v4_pro():
    """活跃代码中不应再有 deepseek-v4-pro 的字符串字面量（注释不计）。"""
    import ast
    skip_dirs = {"data", "tmp", "docs", "__pycache__", "tests"}
    hits = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in skip_dirs and not d.startswith(".bak")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, encoding="utf-8") as f:
                    tree = ast.parse(f.read())
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if "deepseek-v4-pro" in node.value:
                        hits.append(f"{fp}:{node.lineno}")
    assert not hits, f"仍有 deepseek-v4-pro 字符串字面量: {hits}"
