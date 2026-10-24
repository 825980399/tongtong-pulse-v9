# -*- coding: utf-8 -*-
"""全库 `if __name__ == "__main__"` 自测块盘点（354 批遗留，360 复核权威清单）。

354 只移了 `base/BasePulseOrgan.py` 一处，其余「另立批评估」一直没做，
期间**还在新增**。本工具给出可复核的权威清单，供后续逐处裁决：
    * 自测块留在生产模块里 ⇒ 被 import 时不执行，但会拖慢解析、且容易被
      当成「有可执行入口」（误导演化/拓扑分析把 main 分支算成可达代码）；
    * 真正需要挪走的是「块内有写盘 / 起线程 / 网络」这类有副作用的自测块。

口径（★铁律 219：判据必须限定生产面）：
    只扫 `config.py` / `main.py` / `organs/**` / `nucleus/**`；
    排除 `tests/`、`tmp/`、`data/`、`.bak*`、`tools/`、`.aionclaw-tmp/`。

用法：
    python tools/scan_main_blocks.py                 # 打印摘要
    python tools/scan_main_blocks.py --out <file.md> # 落 markdown 清单
"""
from __future__ import annotations

import argparse
import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRODUCTION_ROOTS = ("organs", "nucleus")
_PRODUCTION_FILES = ("config.py", "main.py")
_EXCLUDE_PARTS = (os.sep + "tests" + os.sep, os.sep + "tmp" + os.sep,
                  os.sep + "data" + os.sep, os.sep + "tools" + os.sep,
                  ".bak", os.sep + ".aionclaw-tmp" + os.sep,
                  "__pycache__", os.sep + ".git" + os.sep)

#: 高危：写盘 / 起线程 / 起进程 / 网络 / 删除 —— 挪走前必须先看
_HIGH_RISK_CALLS = {"open", "system", "Popen", "run", "urlopen", "post",
                    "Thread", "start", "remove", "rmtree", "mkdir", "makedirs",
                    "execute", "save", "write"}
#: 仅打印/读类：中优先级
_LOW_RISK_CALLS = {"print", "get", "sleep", "loads", "load"}


def _is_production(rel: str) -> bool:
    if any(p in rel for p in _EXCLUDE_PARTS):
        return False
    if rel in _PRODUCTION_FILES:
        return True
    return rel.startswith(_PRODUCTION_ROOTS)


def _block_side_effects(block: list[ast.stmt]) -> dict[str, list[str]]:
    """★铁律 168（先造判别力）：副作用分**高危 / 仅打印**两级。

    若把所有含 `print` 的块都算「有副作用」，111 块里 109 块命中 ⇒ 判别力为零，
    清单等于没排优先级。分开后才能真正指出「先处理哪几块」。
    """
    high: list[str] = []
    low: list[str] = []
    for node in ast.walk(ast.Module(body=block, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if not isinstance(name, str):
            continue
        if name in _HIGH_RISK_CALLS and name not in high:
            high.append(name)
        elif name in _LOW_RISK_CALLS and name not in low:
            low.append(name)
    return {"high": high, "low": low}


def scan(root: str = _ROOT) -> list[dict]:
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "tmp", "data", "tools")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if not _is_production(rel):
                continue
            try:
                with open(full, "rb") as f:
                    src = f.read().decode("utf-8", "replace")
                tree = ast.parse(src)
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.If):
                    continue
                test = node.test
                ok = False
                if isinstance(test, ast.Compare):
                    left = test.left
                    if isinstance(left, ast.Name) and left.id == "__name__":
                        ok = True
                if not ok:
                    continue
                end = getattr(node, "end_lineno", node.lineno) or node.lineno
                lines = end - node.lineno + 1
                out.append({
                    "file": rel,
                    "lineno": node.lineno,
                    "lines": lines,
                    "side_effects": _block_side_effects(node.body),
                    "total_file_lines": src.count("\n") + 1,
                })
    out.sort(key=lambda d: (-d["lines"], d["file"]))
    return out


def render(items: list[dict]) -> str:
    high = [i for i in items if i["side_effects"]["high"]]
    low = [i for i in items if not i["side_effects"]["high"] and i["side_effects"]["low"]]
    pure = [i for i in items if not i["side_effects"]["high"] and not i["side_effects"]["low"]]
    lines: list[str] = []
    lines.append("# `__main__` 自测块盘点（360 复核 · 权威清单）\n")
    lines.append("- 扫描口径：`config.py` / `main.py` / `organs/**` / `nucleus/**`"
                 "（排除 tests/tmp/data/tools/.bak/.aionclaw-tmp）")
    lines.append(f"- 自测块总数：**{len(items)}**；"
                 f"高危（写盘/起线程/起进程/网络/删除）：**{len(high)}**；"
                 f"仅打印：**{len(low)}**；无副作用：**{len(pure)}**")
    lines.append(f"- 自测块总行数：{sum(i['lines'] for i in items)} 行\n")
    lines.append("## 高危（优先评估：挪走或加开关）\n")
    lines.append("| 文件 | 行号 | 块行数 | 高危调用 |")
    lines.append("|---|---|---|---|")
    for i in high:
        lines.append(f"| {i['file']} | {i['lineno']} | {i['lines']} | "
                     f"{', '.join(i['side_effects']['high'])} |")
    lines.append("\n## 仅打印（中优先级）\n")
    lines.append("| 文件 | 行号 | 块行数 | 打印类调用 |")
    lines.append("|---|---|---|---|")
    for i in low:
        lines.append(f"| {i['file']} | {i['lineno']} | {i['lines']} | "
                     f"{', '.join(i['side_effects']['low'])} |")
    lines.append("\n## 无副作用（可观察，低优先级）\n")
    lines.append("| 文件 | 行号 | 块行数 | 文件总行数 |")
    lines.append("|---|---|---|---|")
    for i in items:
        if i["side_effects"]:
            continue
        lines.append(f"| {i['file']} | {i['lineno']} | {i['lines']} | {i['total_file_lines']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描生产面 __main__ 自测块")
    ap.add_argument("--out", default="", help="markdown 输出路径")
    ap.add_argument("--root", default=_ROOT)
    args = ap.parse_args()
    items = scan(args.root)
    md = render(items)
    if args.out:
        tmp = args.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(md)
        os.replace(tmp, args.out)
        print(f"[scan_main_blocks] {len(items)} 块 → {args.out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
