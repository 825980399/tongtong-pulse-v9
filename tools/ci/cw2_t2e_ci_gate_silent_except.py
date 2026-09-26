# -*- coding: utf-8 -*-
"""任务二 d：防回潮 CI 门禁 + pre-commit hook（真实可执行，非伪码）。

规则：
  1. 对 base..HEAD（或工作树）的 diff，新增静默 except handler 数必须 = 0（只降不升 / 名单外新增=0）。
  2. 变更 .py 文件不得含 b"\\r\\r\\n"（CRCRLF 事故正主）。
  3. 变更 .py 文件必须可解析（parse_err=0）。

实现：AST 比较 base 版本与目标版本中「静默 handler」集合，取差集。
退出码：0=通过，1=存在违规（CI fail / pre-commit 阻断）。

用法：
  python -X utf8 cw2_t2e_ci_gate_silent_except.py [--base HEAD] [--target worktree|HEAD]
                                            [--emit-baseline PATH] [--baseline PATH]
"""
import argparse
import ast
import collections
import io
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# 视为「已上报」的函数名（含框架自有 _log）
LOG_FUNCS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal",
             "log", "aibot_log", "log_error", "log_warning", "log_info", "record", "report",
             "notify", "alert", "emit", "_log", "_log_safe", "_log_msg", "_trace", "silent_exc"}


# ---- 第132批 T-132d：静默except豁免白名单（8 处合理自举兜底） ----
# 这些位置是框架有意的静默兜底（PM 的 ImportError 日志模块自举、RWP 的 shutdown 期落盘/回收保护），
# 不应计入"待改造"清单，也不应在未来被重新引入时触发违规。键 = (relpath, 源文件行号)。
LOCATION_WHITELIST = {
    ("nucleus/reasoning/PatchManager.py", 3364),   # except ImportError: pass（日志模块不可用兜底）
    ("nucleus/reasoning/PatchManager.py", 3493),   # except ImportError: pass
    ("nucleus/reasoning/PatchManager.py", 3641),   # except ImportError: pass
    ("nucleus/reasoning/PatchManager.py", 3725),   # except ImportError: pass
    ("nucleus/reasoning/PatchManager.py", 3782),   # except ImportError: pass
    ("nucleus/reasoning/ReasoningWorkerPool.py", 707),   # shutdown 取消在途任务：except Exception: pass
    ("nucleus/reasoning/ReasoningWorkerPool.py", 747),   # shutdown join 子进程：except Exception: pass
    ("nucleus/reasoning/ReasoningWorkerPool.py", 795),   # shutdown 落盘保护(_sd)：except Exception: pass
}


def _log(level, msg):
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
        if (path, node.lineno) in LOCATION_WHITELIST:
            continue
        body = node.body
        allquiet = all(quiet_body(x) for x in body)
        if not allquiet:
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


# ---- 第126批 T-126c 新增：CRCRLF 行尾污染检测 ----
def check_crcrlf(files):
    bad = []
    for rel in files:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        with io.open(p, "rb") as _f:
            _data = _f.read()
        if b"\r\r\n" in _data:
            bad.append(rel)
    return bad


# ---- 第126批 T-126c 新增：生成已知静默except指纹基线（名单） ----
def emit_baseline(path):
    r = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    files = [x for x in r.stdout.splitlines() if x.strip()]
    base = {}
    for rel in files:
        src = git_read_worktree(rel) or ""
        cnt = silent_handlers(src, rel)
        if cnt:
            base[rel] = {"::".join(k): v for k, v in cnt.items()}
    with io.open(path, "w", encoding="utf-8") as _f:
        json.dump(base, _f, ensure_ascii=False, indent=2)
    print(f"BASELINE_EMITTED -> {path}  files={len(files)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--target", default="worktree", choices=["worktree", "HEAD"])
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--emit-baseline", default=None)
    ap.add_argument("--baseline", default=None)
    args = ap.parse_args()

    if args.emit_baseline:
        emit_baseline(args.emit_baseline)
        return 0

    files = changed_files(args.base)
    print("=" * 74)
    print("CI / pre-commit 门禁：防静默except回潮 + CRCRLF")
    print(f"  base={args.base}  target={args.target}  变更 .py 文件数={len(files)}")
    print("=" * 74)
    total_added = 0
    parse_errs = 0
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
        for (fn, tn, shape, bsrc), c in added.items():
            if fn == "::<PARSE_ERROR>::":
                parse_errs += c
            else:
                total_added += c
                per_file.append((rel, c, added))
                if args.verbose:
                    print(f"        {c}x  func={fn}  except {tn}  -> {shape}  body={bsrc[:60]}")

    crcrlf_bad = check_crcrlf(files)
    print("-" * 74)
    ok = True
    if total_added == 0:
        print("  [1][2] 静默except：PASS —— 本次变更未新增（只降不升 / 名单外新增=0）")
    else:
        ok = False
        print(f"  [1][2] 静默except：FAIL —— 新增 {total_added} 处，涉及 {len(per_file)} 文件")
    if parse_errs == 0:
        print("  [3] parse_err：PASS —— 变更 .py 均可解析")
    else:
        ok = False
        print(f"  [3] parse_err：FAIL —— {parse_errs} 个文件解析失败")
    if not crcrlf_bad:
        print("  [4] CRCRLF(双CR行尾)：PASS —— 无 CRCRLF 行尾污染")
    else:
        ok = False
        print(f"  [4] CRCRLF(双CR行尾)：FAIL —— {len(crcrlf_bad)} 文件含双CR：{crcrlf_bad}")
    print("=" * 74)
    print("  结论: PASS" if ok else "  结论: FAIL —— 提交被阻断，请整改后重试")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
