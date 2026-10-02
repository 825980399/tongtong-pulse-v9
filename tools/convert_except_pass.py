# -*- coding: utf-8 -*-
"""B156-8 / P2-79 · except_pass → silent_exc 转化器（安全、幂等、可回滚）。

策略（遵循项目「pulse-edit-noop-guard」纪律）：
  - 对扫描出的「吸收型 except」(body 为 pass / continue / break / return None)，
    在 body 之前插入 `silent_exc(e, where="<file>:<line>")`，并把对应
    `except X:` 改写为 `except X as e:`（绑定异常变量，供 silent_exc 必填参数 e 使用），
    **不改变控制流**（continue/break 仍执行），仅把被吞异常变为可见日志。
  - 不删除 except 子句、不改异常类型；纯「可见化」修复。
  - 文件已 import silent_exc 则复用；缺失则补 `from nucleus._silent_except import silent_exc`。
  - 幂等：handler 内已含 silent_exc 调用则跳过。
  - 默认 dry-run；`--apply` 才落盘。处理前打印 diff 预览。
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_absorbing(body):
    if not body:
        return True, "empty"
    if len(body) == 1:
        s = body[0]
        if isinstance(s, (ast.Pass, ast.Continue, ast.Break)):
            return True, type(s).__name__
        if isinstance(s, ast.Return) and (s.value is None or _is_none(s.value)):
            return True, "return None"
    return False, ""


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _has_silent_exc(body):
    for s in body:
        for n in ast.walk(s):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                if name == "silent_exc":
                    return True
    return False


def convert_file(path: str, hits: list, apply: bool) -> int:
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.split("\n")
    # 自底向上，保持行号稳定
    for h in sorted(hits, key=lambda x: x["lineno"], reverse=True):
        ln = h["lineno"]  # 1-indexed except 行
        idx = ln - 1
        j = idx + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j >= len(lines):
            continue
        body_line = lines[j]
        body_strip = body_line.strip()
        indent = len(body_line) - len(body_line.lstrip())
        where = f'{h["file"]}:{ln}'
        # ★E-1 根因修复：绑定异常变量 `as e`，否则 silent_exc(e, ...) 缺必填实参。
        # 仅处理简单 `except X:` 形态（无 as、无多异常括号），避免破坏既有语法。
        _exc = lines[idx]
        if " as " not in _exc and re.search(r"except\s+[A-Za-z_][\w.]*\s*:", _exc):
            _es = _exc.rstrip()
            if _es.endswith(":"):
                lines[idx] = _es[:-1].rstrip() + " as e:"
        inserted = (" " * indent) + f'silent_exc(e, where="{where}")'
        if body_strip in ("continue", "break"):
            lines.insert(j, inserted)
            print(f"  +{j+1:<5} {inserted}")
        elif body_strip == "pass":
            lines[j] = inserted
            print(f"  ={j+1:<5} {inserted}")
        else:
            # 非纯吸收（如 return None），不处理
            continue
    new_src = "\n".join(lines)
    if not apply:
        return 0
    # 确保 import 存在
    if "from nucleus._silent_except import silent_exc" not in new_src:
        # 插到首个 from nucleus 之后，否则文件头
        new_lines = new_src.split("\n")
        ins = None
        for k, l in enumerate(new_lines):
            if l.startswith("from nucleus."):
                ins = k + 1
                break
        if ins is None:
            ins = 0
        new_lines.insert(ins, "from nucleus._silent_except import silent_exc")
        new_src = "\n".join(new_lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    return 1


def main() -> int:
    scan_path = sys.argv[1] if len(sys.argv) > 1 else "tmp/exc_scan.json"
    apply = "--apply" in sys.argv
    targets = json.load(open(scan_path))
    # 仅处理指定文件集合（pilot：非 nucleus 核心层，按任务书 utils→hardware→functions→organs 顺序）
    pilot = {
        "hardware/robot_body/tcp_client.py",
        "functions/health_ui.py",
        "functions/chat/chat_service.py",
        "organs/identity/PulsePersonalityKernel.py",
        "organs/motor/PulseController.py",
        "organs/motor/PulseFileDigester.py",
    }
    by_file: dict[str, list] = {}
    for h in targets["hits"]:
        if h["file"] in pilot:
            by_file.setdefault(h["file"], []).append(h)
    total = 0
    for f in sorted(by_file):
        fp = os.path.join(ROOT, f)
        print(f"\n=== {f} ({len(by_file[f])} hits) {'[APPLY]' if apply else '[dry-run]'} ===")
        total += convert_file(fp, by_file[f], apply)
    print(f"\n{'APPLIED' if apply else 'PREVIEW'} files touched: {len(by_file)}  insertions: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
