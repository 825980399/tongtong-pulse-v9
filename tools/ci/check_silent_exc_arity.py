#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""silent_exc arity 门禁（E-1 修复回归守卫 / T-自我审计-3 route② 第一发实证弹）。

背景：
  nucleus/_silent_except.py::silent_exc(e, where="", level="debug") 的 `e` 为必填
  位置参数（bc3aaf8 引入的 convert_except_pass.py 模板曾漏 `e`，导致 19 处
  `silent_exc(where=...)` 缺实参 ERROR）。本门禁用 AST 扫全仓，确保：
    1) 每个 `silent_exc(...)` 调用都传入 `e`（位置首参 或 `e=` 关键字）；
    2) `silent_exc(where=...)`（纯 where、缺 e）一律判违规 —— 即 E-1 验收口径
       `grep "silent_exc(where="` 归零 的 AST 化固化。

与 pre-commit 的关系：
  - 轻量 AST 扫描（不跑 pytest），挂 per-commit hook（见 .git/hooks/pre-commit）。
  - 归因明确后，仅报告违规文件:行号，不阻塞非相关提交之外的逻辑。

用法：
  python tools/ci/check_silent_exc_arity.py            # 默认扫描
  python tools/ci/check_silent_exc_arity.py --selftest
"""
import os
import sys
import ast

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 跳过这些目录（非源码 / 易失 / 备份）
_SKIP_DIRS = (".git", "tmp", "node_modules", ".bak", ".workbuddy",
              "__pycache__", ".venv", "venv", "build", "dist")


def _iter_py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地裁剪，避免下钻跳过目录
        dirnames[:] = [d for d in dirnames
                       if not (d in _SKIP_DIRS or d.startswith(".bak"))]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


from nucleus._silent_except import silent_exc  # noqa: E402


def _is_silent_exc_call(node):
    f = node.func
    if isinstance(f, ast.Name) and f.id == "silent_exc":
        return True
    if isinstance(f, ast.Attribute) and f.attr == "silent_exc":
        return True
    return False


def _has_e_arg(call):
    """silent_exc 必填 `e`：位置首参存在 或 关键字 `e=` 存在。"""
    # 位置参数：至少有一个非 *args/**kwargs 的位置实参
    if call.args:
        return True
    for kw in call.keywords:
        if kw.arg == "e":
            return True
    return False


def scan_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except Exception as _e:  # pragma: no cover - 编码/读取异常时跳过该文件
        silent_exc(_e, where="tools.ci.check_silent_exc_arity::scan_file read")
        return []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as _se:  # pragma: no cover - 语法错误文件不纳入（另有 ruff 把关）
        silent_exc(_se, where="tools.ci.check_silent_exc_arity::scan_file parse")
        return []
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_silent_exc_call(node):
            if not _has_e_arg(node):
                rel = os.path.relpath(path, PROJECT_ROOT)
                violations.append((rel, node.lineno))
    return violations


def check():
    all_v = []
    for fp in _iter_py_files(PROJECT_ROOT):
        all_v.extend(scan_file(fp))
    if all_v:
        print("[silent_exc-arity] ❌ 发现 %d 处 silent_exc 缺必填实参 e（E-1 反模式）："
              % len(all_v), file=sys.stderr)
        for rel, ln in sorted(all_v):
            print("   ! %s:%d  silent_exc(...) 缺少 e 实参（应为 silent_exc(e, where=...)）"
                  % (rel, ln), file=sys.stderr)
        print("[silent_exc-arity] 结论：FAIL（须补 e 实参或绑定 except ... as e）", file=sys.stderr)
        return 1
    print("[silent_exc-arity] ✅ PASS（全仓 silent_exc 调用均含必填 e 实参）", file=sys.stderr)
    return 0


def selftest():
    # 自证：构造缺 e 与含 e 两种调用，确认判定正确
    bad = ast.parse("silent_exc(where='x')").body[0].value
    good = ast.parse("silent_exc(e, where='x')").body[0].value
    assert not _has_e_arg(bad), "bad should fail"
    assert _has_e_arg(good), "good should pass"
    print("[selftest] arity 判定自证通过")
    return 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    return check()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
