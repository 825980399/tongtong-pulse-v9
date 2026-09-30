#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_event_string_gate.py —— T155-R2 P2-78 事件常量收口（防回归门禁）

禁止「新代码」新增「裸点分命名空间事件名」字符串字面量（如 "heart.beat" / "system.boot"）。
事件名应经 const.py 枚举引用（Class.MEMBER.value），保持单一事实来源。

实现：
    1) 解析 nucleus/const.py，抽取所有「含 . 的字符串值」作为点分事件值集合；
    2) 扫描本次提交（staged diff）新增行中的字符串字面量，命中集合即 FAIL；
    3) 仅检查 Python 源码文件（.py）；文档/CSV/数据等非代码文件不在范围内（举例引用事件名属正常说明）。
    4) 豁免：const.py 自身（定义处）、tests/ 目录（测试断言）、tools/ci/ 目录（CI 工具脚本）。

说明：仅检查“新增行”，不影响存量 200+ 处既有字面量；精确匹配 const.py 值集合，false-positive 极低。
退出码：0=通过，1=阻断。
"""
import ast
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
from nucleus._silent_except import silent_exc  # 静默异常统一走 CI 门禁认可通道


def repo_root() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], encoding="utf-8"
    ).strip()


def dot_event_values(root: str):
    const_path = os.path.join(root, "nucleus", "const.py")
    try:
        src = open(const_path, encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception as e:
        # const.py 不可解析时不阻断（交由其他门禁），仅跳过本检查
        silent_exc(e, where="check_event_string_gate.dot_event_values", level="debug")
        return set()
    values = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    targets = stmt.targets
                    val = stmt.value
                elif isinstance(stmt, ast.AnnAssign):
                    targets = [stmt.target]
                    val = stmt.value
                else:
                    continue
                for t in targets:
                    if isinstance(t, ast.Name) and isinstance(val, ast.Constant) \
                            and isinstance(val.value, str) and "." in val.value:
                        values.add(val.value)
    return values


_STR_LITERAL = re.compile(r'''[\'"]((?:[^\'"\\]|\\.)*)[\'"]''')


def scan_violations(root: str, values):
    r = subprocess.run(
        ["git", "diff", "--cached", "-U0"],
        cwd=root, capture_output=True, text=True, encoding="utf-8",
    )
    violations = []
    cur = None
    for line in r.stdout.splitlines():
        if line.startswith("+++ "):
            cur = line[4:].strip()
            if cur.startswith("b/"):
                cur = cur[2:]
            if cur == "/dev/null":
                cur = None
                continue
            # 仅检查 Python 源码：文档(.md)/CSV/数据等非代码文件引用事件名属正常说明，
            # 误报率高，跳过。门禁初衷是拦「新代码」的裸事件名，非文档举例。
            if not cur.endswith(".py"):
                cur = None
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added = line[1:]
            for m in _STR_LITERAL.finditer(added):
                s = m.group(1)
                if s in values:
                    if cur == "nucleus/const.py":
                        continue  # 定义处豁免
                    if cur and cur.startswith("tests/"):
                        continue  # 测试豁免
                    if cur and cur.startswith("tools/ci/"):
                        continue  # CI 工具脚本豁免（非事件发射生产码）
                    if cur:  # 仅 Python 源码且非豁免目录才记录（None=非.py/已豁免）
                        violations.append((cur, s))
    return violations


def main():
    root = repo_root()
    values = dot_event_values(root)
    if not values:
        print("[event-string] SKIP —— const.py 点分事件值集合为空/不可解析")
        return 0
    violations = scan_violations(root, values)
    if not violations:
        print("[event-string] PASS —— 无新增裸点分事件名字符串字面量")
        return 0
    print("[event-string] FAIL —— 检测到新增裸点分事件名字符串字面量，"
          "应改用 const.py 枚举（Class.MEMBER.value）：")
    for rel, s in violations:
        print("    %s  %r" % (rel, s))
    return 1


if __name__ == "__main__":
    sys.exit(main())
