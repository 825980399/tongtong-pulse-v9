# -*- coding: utf-8 -*-
"""B156-9 风格批 · 幻影技术债证伪扫描。

任务书断言：风格面仅「380/182 except_pass」一处真实面；「裸 except 35」「裸 noqa 21」
为幻影（已证伪为 0）。本工具以 AST/正则实测复核：

  - 裸 `except:`（catch-all，无异常类型）在**项目源码**中的数量（排除 scratch 目录）；
  - 裸 `# noqa`（无错误码）的数量。

不修改任何代码，纯只读扫描。输出 JSON：bare_except / bare_noqa 命中列表 + 计数。
scratch 目录（tmp / .aionclaw-tmp / .bak_batch* / __pycache__ 等）一律排除，
避免把一次性探针/审计脚本误算进项目技术债。
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDE_DIRS = {"tmp", ".git", "__pycache__", "node_modules", ".venv", "venv", ".aionclaw-tmp"}


def _skip(d: str) -> bool:
    if d in EXCLUDE_DIRS:
        return True
    if d.startswith(".bak_batch"):
        return True
    return False


def scan() -> dict:
    bare_except: list[str] = []
    bare_noqa: list[str] = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if not _skip(d)]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel.startswith("tmp/") or rel.startswith(".aionclaw-tmp/"):
                continue
            try:
                src = open(p, encoding="utf-8", errors="ignore").read()
                tree = ast.parse(src, filename=p)
            except Exception:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.ExceptHandler) and n.type is None:
                    bare_except.append(f"{rel}:{n.lineno}")
            for i, line in enumerate(src.split("\n"), 1):
                if re.search(r"#\s*noqa\s*$", line):
                    bare_noqa.append(f"{rel}:{i}")
    return {
        "bare_except": bare_except,
        "bare_except_count": len(bare_except),
        "bare_noqa": bare_noqa,
        "bare_noqa_count": len(bare_noqa),
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
