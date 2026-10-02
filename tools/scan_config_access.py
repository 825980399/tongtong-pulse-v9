# -*- coding: utf-8 -*-
"""B156-8 / P2-65 · config 访问点扫描与分类。

用途：为「config.py 拆分」提供真实数据底座。扫描全仓 `config.<ATTR>` 访问点，
按属性名语义前缀归类，输出每类命中数与涉及文件数，供拆分方案（按域分包）决策。

不修改任何代码，纯只读扫描。
"""
from __future__ import annotations

import ast
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDE_DIRS = {"tmp", ".git", "__pycache__", "node_modules", ".venv", "venv"}


def _skip_dir(d: str) -> bool:
    if d in EXCLUDE_DIRS:
        return True
    # 备份目录 .bak_batch* 单点故障，非工作树，跳过
    if d.startswith(".bak_batch"):
        return True
    return False


def _is_config_attr(node: ast.AST):
    """返回 config 属性名（若 node 是 config.<NAME> 形式）。"""
    if isinstance(node, ast.Attribute):
        val = node.value
        if isinstance(val, ast.Name) and val.id == "config":
            return node.attr
        if isinstance(val, ast.Attribute) and isinstance(val.value, ast.Name) and val.value.id == "config":
            return val.attr + "." + node.attr
    return None


def _categorize(attr: str) -> str:
    """按语义前缀归类，未知归为 OTHER。"""
    a = attr.upper()
    if a.startswith("ENABLE_"):
        return "ENABLE_*（功能开关）"
    if a.startswith(("PATH", "DIR", "FILE", "ROOT", "HOME", "WORK")):
        return "路径/PATH_*（文件系统）"
    if a.startswith(("MODEL", "LLM", "EMBED", "VECTOR", "EMBEDDING")):
        return "模型/LLM_*（推理配置）"
    if a.startswith(("REDIS", "INFLUX", "POSTGRES", "DB_", "MQTT", "KAFKA")):
        return "存储/消息中间件"
    if a.startswith(("LOG", "LOGGER")):
        return "日志/LOG_*"
    if a.startswith(("API", "URL", "HOST", "PORT", "TOKEN", "KEY", "SECRET")):
        return "网络/密钥/API_*"
    if a.startswith(("TIMEOUT", "RETRY", "INTERVAL", "RATE", "THRESH", "LIMIT", "MAX", "MIN")):
        return "阈值/限流/TIMEOUT_*"
    if a.startswith(("FEATURE", "FLAG", "DEBUG", "VERBOSE", "TEST")):
        return "特性/调试/FEATURE_*"
    if a.startswith(("PROMPT", "TEMPLATE", "SYSTEM")):
        return "提示词/PROMPT_*"
    if "ORGAN" in a or "CHANNEL" in a or "QICA" in a:
        return "器官/通道/QICA"
    return "OTHER（未归类）"


def scan() -> dict:
    cat_counter: Counter = Counter()
    cat_files: dict[str, set] = defaultdict(set)
    file_counter: Counter = Counter()
    total = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, ROOT).replace("\\", "/")
            if rel.startswith("tmp/"):
                continue
            try:
                src = open(fp, encoding="utf-8", errors="ignore").read()
                tree = ast.parse(src, filename=fp)
            except Exception:
                continue
            seen = set()
            for n in ast.walk(tree):
                attr = _is_config_attr(n)
                if attr and attr not in seen:
                    seen.add(attr)
                    cat = _categorize(attr)
                    cat_counter[cat] += 1
                    cat_files[cat].add(rel)
                    file_counter[rel] += 1
                    total += 1
    return {
        "total_access_points": total,
        "by_category": [
            {"category": c, "count": cnt, "files": len(cat_files[c])}
            for c, cnt in cat_counter.most_common()
        ],
        "top_files": [
            {"file": f, "count": cnt} for f, cnt in file_counter.most_common(25)
        ],
    }


def main() -> int:
    res = scan()
    out = sys.argv[1] if len(sys.argv) > 1 else None
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"written -> {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
