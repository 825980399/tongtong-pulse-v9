# -*- coding: utf-8 -*-
"""B156-8 / P2-79 · except_pass（吞异常）扫描。

用途：定位「吸收型 except」——except 子句体为空或仅 `pass`/`continue`/`break`/`return None`，
即把异常静默吞掉、可能掩盖真实错误的代码点。为 except_pass 续推（utils→hardware→
functions→organs→nucleus）提供真实清单与按层分布。

不修改任何代码，纯只读扫描。输出 JSON：命中列表 + 按顶层包分布。
"""
from __future__ import annotations

import ast
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDE_DIRS = {"tmp", ".git", "__pycache__", "node_modules", ".venv", "venv"}


def _skip_dir(d: str) -> bool:
    if d in EXCLUDE_DIRS:
        return True
    if d.startswith(".bak_batch"):
        return True
    return False

# 视为「吸收体」的语句类型
_ABSORB_STMTS = (ast.Pass, ast.Continue, ast.Break)


def _body_is_absorbing(body) -> tuple[bool, str]:
    """判断 except 体是否为纯吸收（空 / 仅 pass / 仅 continue|break / 仅 return None）。"""
    if not body:
        return True, "empty"
    if len(body) == 1:
        stmt = body[0]
        if isinstance(stmt, _ABSORB_STMTS):
            return True, type(stmt).__name__
        if isinstance(stmt, ast.Return) and (stmt.value is None or _is_none_const(stmt.value)):
            return True, "return None"
    return False, ""


def _is_none_const(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _layer(rel: str) -> str:
    parts = rel.split("/")
    if rel.startswith("tests/"):
        return "tests"
    if rel.startswith("tools/"):
        return "tools"
    if rel.startswith("nucleus/"):
        sub = parts[1] if len(parts) > 1 else ""
        return "nucleus/" + sub
    if rel.startswith("utils/"):
        return "utils"
    if rel.startswith("hardware/"):
        return "hardware"
    if rel.startswith("functions/"):
        return "functions"
    if rel.startswith("organs/"):
        return "organs"
    return "other"


def scan() -> dict:
    hits = []
    layer_counter: Counter = Counter()
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
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                ok, kind = _body_is_absorbing(node.body)
                if not ok:
                    continue
                # 跳过已用 silent_exc 的（吸收体里含 silent_exc 调用不算裸吞）
                if _contains_silent_exc(node.body):
                    continue
                ex_type = "bare" if node.type is None else ast.unparse(node.type)
                hits.append({
                    "file": rel,
                    "lineno": node.lineno,
                    "except_type": ex_type,
                    "body_kind": kind,
                    "layer": _layer(rel),
                })
                layer_counter[_layer(rel)] += 1
    return {
        "total_absorbs": len(hits),
        "by_layer": [{"layer": l, "count": c} for l, c in layer_counter.most_common()],
        "hits": hits,
    }


def _contains_silent_exc(body) -> bool:
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Call):
                f = n.func
                name = None
                if isinstance(f, ast.Name):
                    name = f.id
                elif isinstance(f, ast.Attribute):
                    name = f.attr
                if name == "silent_exc":
                    return True
    return False


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
