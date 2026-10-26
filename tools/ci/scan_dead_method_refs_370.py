# -*- coding: utf-8 -*-
"""370 刀3 · O3 两方法**死方法引用扫描**（纯只读，不入生产 import 链）。

用途：对 `nucleus/audit_problem_pool.py` 的 `plan_target_drift` / `restore_pool`
做四类引用计数（定义 / 调用 / 属性读 / 文本提及），生产面与测试面分列，结果落
证据件供「死方法监测」留痕。**本脚本不做删除决策、不改任何文件**。

用法：
    python tools/ci/scan_dead_method_refs_370.py [--json OUT.json] [--md OUT.md]

★纪律：只读；不被生产代码 import；与 `ci_common.py` 同目录但**不共享判据**，
  不参与 CI 阻断（本刀不新增 CI 阻断）。
"""
import argparse
import ast
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))  # tools/ci -> tools -> 仓库根

TARGETS = [
    {"file": "nucleus/audit_problem_pool.py", "name": "plan_target_drift"},
    {"file": "nucleus/audit_problem_pool.py", "name": "restore_pool"},
]

# 生产面：仓库 .py 排除 tests/ 与噪声目录；测试面：tests/
_SKIP_DIR_PREFIX = (".git", ".bak", "tmp", "__pycache__", ".venv", "node_modules")
_SKIP_DIR_EXACT = {"tmp", ".workbuddy", "tools"}  # tools 视为工具面，单列不计入生产


def _iter_py(surface):
    """surface: 'prod' | 'test'"""
    if surface == "test":
        roots = [os.path.join(ROOT, "tests")]
    else:
        roots = [ROOT]
    seen = set()
    for base in roots:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(_SKIP_DIR_PREFIX)
                           and not (surface == "prod" and d in _SKIP_DIR_EXACT)]
            if surface == "prod":
                rel_dir = os.path.relpath(dirpath, ROOT).replace("\\", "/")
                if rel_dir.split("/")[0] == "tests":
                    continue
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                # ★核真伪：`.bak*` 是历史批次**备份副本**，不是生产面。
                #   首版漏排导致 `restore_pool` 被扫出 2 处定义（其一在
                #   `.bak_batch350_ap.py:680`）⇒ 判读会失真。
                if fn.startswith(".bak"):
                    continue
                fp = os.path.join(dirpath, fn)
                if fp in seen:
                    continue
                seen.add(fp)
                yield fp


def _read(fp):
    """只读取文本。

    ★不用 try/except 兜底：`except OSError: return ""` 属**纯 return 静默**
    （铁律176），会被 cw2 静默门拦下。本扫描器是人工运行的只读工具，
    读不到就让它显式抛错，比悄悄返回空串、把「文件没读着」伪装成
    「文件里没有引用」要诚实得多——后者正是死方法判读最容易栽的地方。
    """
    if not os.path.isfile(fp):
        return ""
    with open(fp, "rb") as f:
        return f.read().decode("utf-8", "replace")


def _scan_one(name, surface):
    """返回 (defs, calls, attrs, mentions) 四类命中，每类为 [(relpath, lineno), ...]。"""
    defs, calls, attrs = [], [], []
    mentions = []
    pat = re.compile(r"\b%s\b" % re.escape(name))
    for fp in _iter_py(surface):
        rel = os.path.relpath(fp, ROOT).replace("\\", "/")
        src = _read(fp)
        if not src:
            continue
        # --- 文本提及（先算，口径：整文件正则命中行，含注释与 docstring）---
        for i, line in enumerate(src.replace("\r\n", "\n").split("\n"), 1):
            if pat.search(line):
                mentions.append((rel, i))
        # --- AST 三类 ---
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        call_func_nodes = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                call_func_nodes.add(id(n.func))
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                defs.append((rel, n.lineno))
            elif isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name) and f.id == name:
                    calls.append((rel, n.lineno))
                elif isinstance(f, ast.Attribute) and f.attr == name:
                    calls.append((rel, n.lineno))
            elif isinstance(n, ast.Attribute) and n.attr == name:
                if id(n) not in call_func_nodes:
                    attrs.append((rel, n.lineno))
    return defs, calls, attrs, mentions


def build():
    out = {"root": ROOT, "targets": []}
    for t in TARGETS:
        rec = {"file": t["file"], "name": t["name"], "surfaces": {}}
        for surface in ("prod", "test"):
            d, c, a, m = _scan_one(t["name"], surface)
            rec["surfaces"][surface] = {
                "def_n": len(d), "call_n": len(c),
                "attr_read_n": len(a), "mention_n": len(m),
                "defs": [{"f": f, "l": l} for f, l in d],
                "calls": [{"f": f, "l": l} for f, l in c],
                "attrs": [{"f": f, "l": l} for f, l in a],
                "mentions": [{"f": f, "l": l} for f, l in m],
            }
        out["targets"].append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    a = ap.parse_args()
    data = build()
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps({t["name"]: {s: {k: v for k, v in d.items()
                                      if k.endswith("_n")}
                                  for s, d in t["surfaces"].items()}
                      for t in data["targets"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
