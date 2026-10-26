# -*- coding: utf-8 -*-
"""CI 共用：静默 except handler 指纹化（Dxxx-4，解决行号漂移 + 新文件盲区）。

唯一标识一个「有意静默兜底」handler 的稳定键（★371A5 起为五元组）：
    (path, qualname, tname, kind, body_sha256[:16])

- path: 相对仓库根的 POSIX 路径
- qualname: 直接 enclosing 函数名；模块级为 "<module>"
- tname: except 的异常类型文本（`bare` 表示裸 except）
- kind: handler 体形态 ∈ pass / ret / assign / cont / break
  （前三种为旧判据既有；**cont / break 为 A5 新增**——「这一条跳过」「整个循环退出」
   同样属于静默吞异常，旧判据完全查不到）
- body_sha16: handler 体归一化后的 sha256 前 16 位

★为什么去掉 nth（D8）：旧四元组含「该函数内第几个 handler」序号，一旦同一函数里
前面的 handler 被修好（不再静默），后面 handler 的序号整体前移 ⇒ 身份键变化 ⇒
被误报成「新增静默」。改用 tname+kind 区分「同名同体不同异常类型/形态」后，
序号不再参与身份，D8 假新增消失。

附加字段（行号仅作备注，不参与匹配）：nth / has_log / has_reraise / lineno。

本模块刻意不含任何「静默 except」——CI 脚本自身也必须过门禁。
"""
import ast
import hashlib

# ★371A5：判据版本。改判据口径 / 重建豁免表**必须**同步升此值。
#   守卫测试 test_m369_blade4_silent_trace::test_judge_and_fingerprint_files_untouched
#   据此区分「受控升级」与「偷偷改判据」：文件无改动⇒不校验；有改动⇒必须离开冻结基线。
JUDGE_VERSION = "A5-five-tuple-no-nth"
# 冻结基线：369 刀4「只留痕不切判据」时四元组判据的版本哨兵（不得再赋给 JUDGE_VERSION）
FROZEN_JUDGE_VERSION = "four-tuple-v1"

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


def _kind_of(body):
    """判定 handler 体形态；返回 None 表示「不算静默」。

    kind ∈ pass / ret / assign / cont / break（前三类＝旧判据既有，后两类＝A5 新增）。
    ★口径与 `tools/scan_silent_except_dual.py::_kind_of` 完全同款（364 前置对比件），
      不得各自演化——两边一旦分叉，对比结论就不再可比。
    """
    n = len(body)
    if n == 0:
        return None
    if all(_quiet_body(x) for x in body):
        return "pass"
    rets = [x for x in body if isinstance(x, ast.Return)]
    asg = [x for x in body if isinstance(x, (ast.Assign, ast.AugAssign, ast.AnnAssign))]
    conts = [x for x in body if isinstance(x, ast.Continue)]
    brks = [x for x in body if isinstance(x, ast.Break)]
    # —— 旧判据既有形态 ——
    if rets and len(rets) == n:
        return "ret"
    if rets and len(rets) + len(asg) == n:
        return "ret"
    if asg and len(asg) == n:
        return "assign"
    # —— A5 新增形态：continue / break（含与 assign 混合覆盖全部）——
    if conts and (len(conts) == n or len(conts) + len(asg) == n):
        return "cont"
    if brks and (len(brks) == n or len(brks) + len(asg) == n):
        return "break"
    return None


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


def handler_fingerprint(path, qualname, nth, body_src, tname, has_log, has_reraise,
                        kind="pass"):
    """构造一枚 handler 指纹。

    `kind` 缺省 "pass" 以兼容旧调用方（旧口径只有 pass/ret/assign 三形态）；
    新判据一律显式传入 `_kind_of()` 的结果。
    """
    return {
        "path": path.replace("\\", "/"),
        "qualname": qualname,
        "nth": nth,
        "sha16": hashlib.sha256(normalize_body(body_src).encode("utf-8")).hexdigest()[:16],
        "tname": tname,
        "kind": kind,
        "has_log": has_log,
        "has_reraise": has_reraise,
    }


def handler_identity(fp):
    """用于集合成员判定的稳定键（不含行号、不含 nth —— ★371A5 去 D8）。

    旧键含 nth：同函数内前面的静默 handler 修好后，后面的序号前移 ⇒ 身份变化 ⇒
    被误报「新增静默」。现改由 tname + kind 承担区分职责，序号彻底退出身份。
    """
    return (fp["path"], fp["qualname"], fp["tname"], fp.get("kind", "pass"), fp["sha16"])


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

    静默定义（★371A5 起扩容）：
      - ExceptHandler 且不含 Raise、不含 LOG_FUNCS 调用（silent_exc 视为已上报）；
      - 体形态 kind ∈ pass（pass/.../单行串）／ret（return 系）／assign（纯赋值）／
        **cont（continue 系）／break（break 系）**——后两者为 A5 新增形态。
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
        kind = _kind_of(h.body)
        if kind is None:
            continue
        tname = ast.unparse(h.type) if h.type else "bare"
        body_src = ";".join(ast.unparse(x) for x in h.body)
        fp = handler_fingerprint(path, qualname, nth, body_src, tname, False, False, kind=kind)
        fp["lineno"] = h.lineno
        out.append(fp)
    return out
