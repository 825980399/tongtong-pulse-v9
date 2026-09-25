# -*- coding: utf-8 -*-
"""任务二 d：防回潮 CI 门禁（真实可执行，非伪码）。

规则：对 base..HEAD（或工作树）的 diff，新增静默 except handler 数必须 = 0。
实现：AST 比较 base 版本与目标版本中「静默 handler」集合，取差集。
退出码：0=通过，1=存在新增（CI fail）。

用法：
  python -X utf8 cw2_t2e_ci_gate_silent_except.py [--base HEAD] [--target worktree|HEAD]
"""
import argparse
import ast
import collections
import io
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# 视为「已上报」的函数名（含框架自有 _log）
LOG_FUNCS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal",
             "log", "aibot_log", "log_error", "log_warning", "log_info", "record", "report",
             "notify", "alert", "emit", "_log", "_log_safe", "_log_msg", "_trace", "silent_exc"}


def _log(level, msg):
    """★T-101e：门禁脚本自身也须满足「无静默 except」规则——所有异常分支显式上报。

    ``_log`` 在 LOG_FUNCS 集合内，调用它即可让 gate 的 ``has_report`` 判定为非静默，
    从而避免「门禁脚本自己触发自己的 FAIL」。
    """
    sys.stderr.write(f"[ci_gate_silent_except][{level}] {msg}\n")


def quiet_body(n):
    if isinstance(n, ast.Pass):
        return True
    if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
        return n.value.value is Ellipsis or isinstance(n.value.value, str)
    return False


def has_report(h):
    for n in ast.walk(h):
        if isinstance(n, ast.Raise):
            return True
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if nm in LOG_FUNCS:
                return True
    return False


def silent_handlers(src, path="<unknown>"):
    """返回该源码中所有「静默」except handler 的特征指纹集合。

    指纹 = (函数名, 捕获类型, 体形态, 规范化体源码)
    用结构指纹而非行号，避免仅仅上下移动代码就误报为「新增」。
    """
    out = collections.Counter()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        _log("error", f"源码解析失败，跳过指纹提取: {type(e).__name__}: {e}")
        out[("::<PARSE_ERROR>::", str(e)[:80], "", "")] += 1
        return out
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append((node.lineno, node.end_lineno or node.lineno, node.name))

    def encf(line):
        best = "<module>"
        for a, b, nm in funcs:
            if a <= line <= b:
                best = nm
        return best

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if has_report(node):
            continue
        body = node.body
        allquiet = all(quiet_body(x) for x in body)
        if not allquiet:
            # 只有「体完全静默」或「纯 return/赋值」才纳入门禁，避免误伤
            rets = [x for x in body if isinstance(x, ast.Return)]
            asg = [x for x in body if isinstance(x, (ast.Assign, ast.AugAssign))]
            if not (len(rets) == len(body) or len(rets) + len(asg) == len(body)):
                continue
        tname = ast.unparse(node.type) if node.type else "bare"
        shape = "pass" if allquiet else "return_or_assign"
        try:
            body_src = ";".join(ast.unparse(x) for x in body)
        except Exception:
            _log("warning", "handler 体反解析失败，指纹体记为 '?'")
            body_src = "?"
        out[(encf(node.lineno), tname, shape, body_src)] += 1
    return out


def git_show(ref, relpath):
    r = subprocess.run(["git", "show", f"{ref}:{relpath}"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else None


def git_read_worktree(relpath):
    p = os.path.join(ROOT, relpath)
    if not os.path.exists(p):
        return None
    return io.open(p, encoding="utf-8", errors="replace").read()


def changed_files(base):
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=ACMR", base, "--", "*.py"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    lst = [x for x in r.stdout.splitlines() if x.strip()]
    # 处理中文/特殊路径：git 可能加引号
    out = []
    for x in lst:
        if x.startswith('"') and x.endswith('"'):
            try:
                x = x[1:-1].encode().decode("unicode_escape").encode("latin1").decode("utf-8")
            except Exception:
                _log("warning", "中文/特殊路径反解失败，保留原串")
                pass
        out.append(x)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--target", default="worktree", choices=["worktree", "HEAD"])
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    files = changed_files(args.base)
    print("=" * 74)
    print("CI 门禁：新增静默 except 必须为 0")
    print(f"  base={args.base}  target={args.target}  变更 .py 文件数={len(files)}")
    print("=" * 74)
    total_added = 0
    per_file = []
    for rel in files:
        old = git_show(args.base, rel)
        new = git_read_worktree(rel) if args.target == "worktree" else git_show("HEAD", rel)
        if new is None:
            continue
        if old is None:
            old = ""
        so, sn = silent_handlers(old, rel), silent_handlers(new, rel)
        added = sn - so          # Counter 差集：新有而旧无（含重数）
        n = sum(added.values())
        if n:
            total_added += n
            per_file.append((rel, n, added))
            print(f"  [FAIL] {rel}: 新增静默 except {n} 处")
            if args.verbose:
                for (fn, tn, shape, bsrc), c in added.most_common(8):
                    print(f"        {c}x  func={fn}  except {tn}  -> {shape}  body={bsrc[:60]}")
    print("-" * 74)
    if total_added == 0:
        print("  结论: PASS —— 本次变更未新增静默 except")
    else:
        print(f"  结论: FAIL —— 新增静默 except 合计 {total_added} 处，涉及 {len(per_file)} 文件")
        print("  整改要求: 每处须改为「带节流 DEBUG 日志」或补 `# intentional:` 说明后")
        print("              在本文件白名单登记（见 cw2_t2_fix_templates.md §白名单）")
    sys.exit(0 if total_added == 0 else 1)


if __name__ == "__main__":
    main()
