# -*- coding: utf-8 -*-
"""CI 共用：静默 except handler 指纹化（Dxxx-4，解决行号漂移 + 新文件盲区）。

唯一标识一个「有意静默兜底」handler 的稳定键：
    (path, qualname, nth_in_function, body_sha256[:16])

- path: 相对仓库根的 POSIX 路径
- qualname: 直接 enclosing 函数名；模块级为 "<module>"
- nth_in_function: 该函数/模块内第几个 ExceptHandler（0-based，按源码序）
- body_sha16: handler 体归一化后的 sha256 前 16 位
  （纯 `pass` / `...` / 单行字符串体 hash 会互撞，故必须配 nth 区分）

附加字段（行号仅作备注，不参与匹配）：tname / has_log / has_reraise。

本模块刻意不含任何「静默 except」——CI 脚本自身也必须过门禁。
"""
import ast
import hashlib

# 视为「已上报」的函数名（含框架自有 _log）；命中即不算静默
LOG_FUNCS = {
    "debug", "info", "warning", "warn", "error", "exception", "critical", "fatal",
    "log", "aibot_log", "log_error", "log_warning", "log_info", "record", "report",
    "notify", "alert", "emit", "_log", "_log_safe", "_log_msg", "_trace", "silent_exc",
}


def normalize_body(src):
    """归一化 handler 体文本：折叠空白、统一换行，使同义体 hash 稳定。"""
    return " ".join(src.split())


def _quiet_body(n):
    if isinstance(n, ast.Pass):
        return True
    if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
        return n.value.value is Ellipsis or isinstance(n.value.value, str)
    return False


def _has_report(h):
    """handler 体是否含 Raise 或 LOG_FUNCS 调用（有上报/重抛即不算静默）。"""
    for n in ast.walk(h):
        if isinstance(n, ast.Raise):
            return True
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if nm in LOG_FUNCS:
                return True
    return False


def handler_fingerprint(path, qualname, nth, body_src, tname, has_log, has_reraise):
    return {
        "path": path.replace("\\", "/"),
        "qualname": qualname,
        "nth": nth,
        "sha16": hashlib.sha256(normalize_body(body_src).encode("utf-8")).hexdigest()[:16],
        "tname": tname,
        "has_log": has_log,
        "has_reraise": has_reraise,
    }


def handler_identity(fp):
    """用于集合成员判定的稳定键（不含行号，不含附加字段）。"""
    return (fp["path"], fp["qualname"], fp["nth"], fp["sha16"])


def _enclosing_func(funcs, line):
    """返回包含 line 的最内层函数名（最小跨度优先），否则 <module>。"""
    best = "<module>"
    best_span = None
    for a, b, nm in funcs:
        if a <= line <= b:
            span = b - a
            if best_span is None or span < best_span:
                best = nm
                best_span = span
    return best


def extract_handlers(src, path="<unknown>"):
    """解析源码，返回所有「静默 handler」的指纹 list（已过滤有上报/重抛的）。

    静默定义（与历史门禁一致）：
      - ExceptHandler 且不含 Raise、不含 LOG_FUNCS 调用；
      - 体为「纯静默」（pass / ... / 单行串）或「纯 return」或「return+assign 覆盖全部」。
    返回的每枚指纹含调试字段 "lineno"（不参与 handler_identity）。
    """
    out = []
    tree = ast.parse(src)
    funcs = [(n.lineno, n.end_lineno or n.lineno, n.name)
             for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    handlers.sort(key=lambda n: n.lineno)
    scope_count = {}
    for h in handlers:
        if _has_report(h):
            continue
        qualname = _enclosing_func(funcs, h.lineno)
        nth = scope_count.get(qualname, 0)
        scope_count[qualname] = nth + 1
        body = h.body
        allquiet = all(_quiet_body(x) for x in body)
        if not allquiet:
            rets = [x for x in body if isinstance(x, ast.Return)]
            asg = [x for x in body if isinstance(x, (ast.Assign, ast.AugAssign))]
            if not (len(rets) == len(body) or len(rets) + len(asg) == len(body)):
                continue
        tname = ast.unparse(h.type) if h.type else "bare"
        body_src = ";".join(ast.unparse(x) for x in body)
        fp = handler_fingerprint(path, qualname, nth, body_src, tname, False, False)
        fp["lineno"] = h.lineno
        out.append(fp)
    return out
