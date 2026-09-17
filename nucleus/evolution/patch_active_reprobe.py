# -*- coding: utf-8 -*-
"""patch_active_reprobe.py —— 补丁验证**主动复现**探针（★主线第51批 T1，P0-2）

背景
----
第46/47批根因分析确认：原补丁验证靠「**历史日志考古**」（在 `pulse.log` 里数
目标文件的错误行数），而日志会被轮转截断 → 补丁时间戳早于日志首行 →
`baseline_errors` 恒为 0 → 「**无法复现**」被当成「**修复有效**」。

本模块把验证方式改为「**从补丁自身的代码对主动复现**」：

```
original_code（修复前）  ──检测器──▶  问题应当**存在**  （复现成功）
modified_code（修复后）  ──检测器──▶  问题应当**消失**  （修复有效）
```

⇒ **不依赖任何日志**，因此不受日志轮转影响。

验证结论分类（任务书 §T1.4）
--------------------------
| 结论 | 含义 |
|---|---|
| ``true_pass`` | **真通过**：修复前复现出问题 + 修复后**完全消失** |
| ``partial_fix`` | **部分修复**：问题数**减少但未清零**（★代码块内可能含其他同类问题） |
| ``false_pass`` | **假通过（无法复现）**：修复前**根本复现不出**该问题 |
| ``ineffective`` | **修复无效**：修复前有问题 + 修复后**未减少** |
| ``verification_failed`` | **验证失败**：代码片段无法解析（无法判定） |
| ``not_applicable`` | 该 ``issue_type`` 无静态检测器（不判通过） |

★关键纪律
--------
* **无法复现 ≠ 修复有效** —— 一律归入 ``false_pass``，**绝不**判为通过；
* 检测器只做**静态代码分析**（AST），不执行被检代码（安全）；
* 本模块**纯函数**、无副作用、无 IO，便于回填与门控测试。

核心接口
--------
* :func:`active_reprobe` —— 单条补丁主动复现（**主入口**）
* :func:`reprobe_batch` —— 批量复现并汇总（含 ``by_verdict`` 分布）
* :func:`true_fix_rate` —— 真实修复率（``strict`` 区分严格/宽松两口径）
* :func:`apply_reprobe` —— 把复现结果原地写入补丁字典
* :func:`verdict_label` —— 结论 → 中文标签（展示层用）
* :func:`detector_for` —— 按 ``issue_type`` 取检测器

使用示例
--------
单条补丁（★注意：示例以省略号表示代码片段，实际传入完整片段）::

    from nucleus.evolution.patch_active_reprobe import active_reprobe
    patch = {
        "issue_type": "silent_exception",
        "original_code": "…except Exception: pass…",       # 修复前
        "modified_code": "…except Exception as e: log(e)…",  # 修复后
    }
    r = active_reprobe(patch)
    r["reprobe_verdict"]        # 'true_pass'
    r["reprobe_baseline_hits"]  # 1（复现成功）
    r["reprobe_after_hits"]     # 0（修复有效）

批量（与回填工具 ``tools/reprobe_patches.py`` 同源）::

    from nucleus.evolution.patch_active_reprobe import (
        reprobe_batch, true_fix_rate)
    summary = reprobe_batch(patches)          # {"total": 60, "by_verdict": {...}}
    true_fix_rate(patches, strict=True)       # 仅完全清零 → 0.8793
    true_fix_rate(patches, strict=False)      # 含部分修复   → 1.0
"""
from __future__ import annotations

import ast
import difflib
import re
import textwrap
from typing import Any, Callable

#: 复现结论（取值）
V_TRUE_PASS: str = "true_pass"
V_FALSE_PASS: str = "false_pass"
V_PARTIAL_FIX: str = "partial_fix"
V_INEFFECTIVE: str = "ineffective"
V_VERIFY_FAILED: str = "verification_failed"
V_NOT_APPLICABLE: str = "not_applicable"

#: 字段名（统一在此定义）
F_REPROBE_VERDICT: str = "reprobe_verdict"
F_REPROBE_ISSUE: str = "reprobe_issue_type"
F_REPROBE_BASELINE: str = "reprobe_baseline_hits"
F_REPROBE_AFTER: str = "reprobe_after_hits"
F_REPROBE_DETAIL: str = "reprobe_detail"
F_REPROBE_VERSION: str = "reprobe_version"

REPROBE_VERSION: int = 1

#: ★第55批 T6.1：diff 区域限定的上下文行数（吸收 parse_snippet 的行号偏移）
REPROBE_DIFF_CONTEXT: int = 3

#: 复现作用域（写入结果，便于回溯判定用的是哪种口径）
S_BLOCK: str = "block"      # 整个代码块（第51批原行为）
S_DIFF: str = "diff"        # ★仅改动区域（第55批新增）
F_REPROBE_SCOPE: str = "reprobe_scope"


def _diff_scope_on() -> bool:
    """★第55批 T6.1 灰度开关（默认开；关 = 回退第51批的全块统计）。"""
    try:
        import config as _cfg

        return bool(getattr(_cfg, "ENABLE_REPROBE_DIFF_SCOPE", True))
    except Exception:
        return True

#: 日志类调用名（用于判断「是否留痕」）
_LOG_CALLS: frozenset[str] = frozenset({
    "debug", "info", "warning", "error", "critical", "exception", "log",
    "print",
})


# ==================== 代码片段解析 ====================

def parse_with_offset(code: Any) -> tuple[ast.Module | None, int]:
    """解析代码片段，并返回**行号偏移量**。

    ★第55批 T6.1：判定改动区域需要把 AST 行号**还原**到原始片段的行号。
      若解析时因缩进问题包了一层 ``def _m51_w():``，AST 行号比原始行号 **+1**。

    Returns:
        ``(tree, offset)`` —— 失败时为 ``(None, 0)``。
    """
    if not isinstance(code, str) or not code.strip():
        return None, 0
    _t = code.replace("\r\n", "\n").replace("\r", "\n")
    _cands = [
        (textwrap.dedent(_t), 0),
        ("def _m51_w():\n" + textwrap.indent(textwrap.dedent(_t), "    "), 1),
        (textwrap.dedent(_t).strip(), 0),
    ]
    for _cand, _off in _cands:
        try:
            return ast.parse(_cand), _off
        except (SyntaxError, ValueError, IndentationError):
            continue
    return None, 0


def parse_snippet(code: Any) -> ast.Module | None:
    """把**代码片段**解析为 AST（容忍缩进：先 dedent，再尝试包进函数）。

    Returns:
        AST 模块对象；无法解析（语法错误）返回 ``None``。
    """
    _tree, _ = parse_with_offset(code)
    return _tree


def diff_line_ranges(original: Any, modified: Any,
                     context: int = REPROBE_DIFF_CONTEXT) -> tuple[set, set]:
    """★第55批 T6.1：算出改动行区间（含上下文）。

    Args:
        original: 修复前代码片段。
        modified: 修复后代码片段。
        context:  改动行前后各扩几行（吸收行号偏移与跨行结构）。

    Returns:
        ``(原片段改动行集合, 新片段改动行集合)``，行号 **1-based**。
        任一为 ``None`` / 无法对齐时返回 ``(set(), set())``（→ 调用方回退全块）。
    """
    if not isinstance(original, str) or not isinstance(modified, str):
        return set(), set()
    _o = original.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    _m = modified.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not _o or not _m:
        return set(), set()

    def _expand(lines: list, idxs: set) -> set:
        _out = set()
        for _i in idxs:
            for _k in range(max(0, _i - context), min(len(lines), _i + context + 1)):
                _out.add(_k + 1)          # 1-based
        return _out

    _sm = difflib.SequenceMatcher(None, _o, _m, autojunk=False)
    _oi: set = set()
    _mi: set = set()
    for _tag, _i1, _i2, _j1, _j2 in _sm.get_opcodes():
        if _tag == "equal":
            continue
        _oi.update(range(_i1, _i2))
        _mi.update(range(_j1, _j2))
    if not _oi and not _mi:
        return set(), set()
    return _expand(_o, _oi), _expand(_m, _mi)


# ==================== 问题检测器 ====================

def _has_trace(node: ast.AST, exc_name: str | None) -> bool:
    """节点内是否有「留痕」（日志调用 / 异常对象引用 / 结果写回）。"""
    for _n in ast.walk(node):
        if isinstance(_n, ast.Assign):
            return True
        if isinstance(_n, ast.Name) and exc_name and _n.id == exc_name:
            return True
        if isinstance(_n, ast.Call):
            _f = _n.func
            _nm = (_f.attr if isinstance(_f, ast.Attribute)
                   else _f.id if isinstance(_f, ast.Name) else "")
            if _nm in _LOG_CALLS:
                return True
        if isinstance(_n, ast.Attribute) and _n.attr == "write":
            return True
    return False


def detect_silent_exception(code: Any) -> list[dict[str, Any]]:
    """检测「静默异常捕获」：``except ...: pass`` 且**无日志记录**。

    与 ``issue_type == "silent_exception"`` 对应（本库 50 条）。
    """
    _tree = parse_snippet(code)
    if _tree is None:
        return []
    _out = []
    for _n in ast.walk(_tree):
        if not isinstance(_n, ast.ExceptHandler):
            continue
        _body = _n.body
        if not _body:
            _out.append({"line": _n.lineno, "why": "空 except 体"})
            continue
        _only_pass = len(_body) == 1 and isinstance(_body[0], ast.Pass)
        if _only_pass:
            _out.append({"line": _n.lineno, "why": "裸 pass（无留痕）"})
            continue
        if not _has_trace(ast.Module(body=_body, type_ignores=[]), _n.name):
            _out.append({"line": _n.lineno, "why": "except 体无日志/无异常引用"})
    return _out


def detect_print_instead_of_log(code: Any) -> list[dict[str, Any]]:
    """检测「用 print 代替日志」（``issue_type == "print_instead_of_log"``）。"""
    _tree = parse_snippet(code)
    if _tree is None:
        return []
    _out = []
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.Call):
            _f = _n.func
            if isinstance(_f, ast.Name) and _f.id == "print":
                _out.append({"line": _n.lineno, "why": "print(...)"})
    return _out


def detect_code_optimization(code: Any) -> list[dict[str, Any]]:
    """``code_optimization`` 无可靠静态判据 → 恒返回空（→ ``not_applicable``）。"""
    return []


#: ``issue_type`` → 检测器
_DETECTORS: dict[str, Callable[[Any], list]] = {
    "silent_exception": detect_silent_exception,
    "print_instead_of_log": detect_print_instead_of_log,
    "code_optimization": detect_code_optimization,
}


def detector_for(issue_type: Any) -> Callable[[Any], list] | None:
    """取 ``issue_type`` 对应的检测器（无 → ``None``）。"""
    if not isinstance(issue_type, str):
        return None
    _fn = _DETECTORS.get(issue_type)
    if _fn is None:
        return None
    # code_optimization 属"无判据"，视为不可用
    if _fn is detect_code_optimization:
        return None
    return _fn


# ==================== 主动复现主流程 ====================

def active_reprobe(patch: dict[str, Any]) -> dict[str, Any]:
    """对单条补丁做**主动复现**验证。

    Args:
        patch: 补丁字典（需含 ``issue_type`` / ``original_code`` / ``modified_code``）。

    Returns:
        ``{reprobe_verdict, reprobe_issue_type, reprobe_baseline_hits,
           reprobe_after_hits, reprobe_detail, reprobe_version}``

    验证流程（任务书 §T1.1）：
        ① 用 ``issue_type`` 选检测器；
        ② ``original_code`` 检测 → **期望检出 ≥1 处**（复现成功）；
        ③ ``modified_code`` 检测 → **期望检出 0 处**（修复有效）。
    """
    _issue = patch.get("issue_type")
    _orig = patch.get("original_code")
    _modi = patch.get("modified_code")

    _det = detector_for(_issue)
    if _det is None:
        return _result(V_NOT_APPLICABLE, _issue, -1, -1,
                       "无对应检测器（issue_type=%s）→ 不判通过" % _issue,
                       _scope_unknown())

    # ① 代码片段必须可解析
    _t_orig, _off_o = parse_with_offset(_orig)
    _t_modi, _off_m = parse_with_offset(_modi)
    if _t_orig is None or _t_modi is None:
        _bad = []
        if _t_orig is None:
            _bad.append("original_code")
        if _t_modi is None:
            _bad.append("modified_code")
        return _result(V_VERIFY_FAILED, _issue, -1, -1,
                       "代码片段无法解析: %s" % ", ".join(_bad))

    # ② 修复前复现
    _base_hits = _det(_orig)
    _after_hits = _det(_modi)

    # ★第55批 T6.1：diff 区域限定 —— 只统计「改动区域」内的命中
    _scope = S_BLOCK
    if _diff_scope_on():
        _so, _sm = diff_line_ranges(_orig, _modi)
        if _so and _sm:
            _b_in = [_h for _h in _base_hits
                     if (_h.get("line", 0) - _off_o) in _so]
            _a_in = [_h for _h in _after_hits
                     if (_h.get("line", 0) - _off_m) in _sm]
            # ★回退保护：改动区域内**原本就没问题** → 说明修复不在该区，
            #   此时若强行限定会把 true_pass 误判为 false_pass → 回退全块
            if _b_in:
                _base_hits, _after_hits, _scope = _b_in, _a_in, S_DIFF
    _nb, _na = len(_base_hits), len(_after_hits)

    if _nb == 0:
        # ★核心修复点：修复前**根本没有该问题** → 无法复现 → 不判通过
        return _result(V_FALSE_PASS, _issue, 0, _na,
                       "★修复前未检出该问题（复现失败）→ 假通过；"
                       "修复后检出 %d 处" % _na, _scope)

    if _na >= _nb:
        _first = _after_hits[0]
        return _result(V_INEFFECTIVE, _issue, _nb, _na,
                       "修复前 %d 处、修复后仍 %d 处（**未减少**；首处在 L%s: %s）"
                       % (_nb, _na, _first.get("line"), _first.get("why")), _scope)

    if _na > 0:
        # ★第51批修正：问题**减少但未清零** → 部分修复
        #   （检测器按整个代码块统计，剩余项可能属其他位置，非本补丁目标）
        return _result(V_PARTIAL_FIX, _issue, _nb, _na,
                       "修复前 %d 处 → 修复后 %d 处（**减少但未清零**，作用域=%s）"
                       % (_nb, _na, _scope), _scope)

    _first_b = _base_hits[0]
    return _result(V_TRUE_PASS, _issue, _nb, 0,
                   "复现成功：修复前 %d 处（首处 L%s: %s）→ 修复后 0 处（作用域=%s）"
                   % (_nb, _first_b.get("line"), _first_b.get("why"), _scope),
                   _scope)


def _result(verdict: str, issue: Any, base: int, after: int,
            detail: str, scope: str = S_BLOCK) -> dict[str, Any]:
    return {
        F_REPROBE_VERDICT: verdict,
        F_REPROBE_ISSUE: issue,
        F_REPROBE_BASELINE: base,
        F_REPROBE_AFTER: after,
        F_REPROBE_DETAIL: detail,
        F_REPROBE_VERSION: REPROBE_VERSION,
        F_REPROBE_SCOPE: scope,            # ★第55批 T6.1：判定所用作用域
    }


def apply_reprobe(patch: dict[str, Any]) -> dict[str, Any]:
    """把复现结果**原地写入**补丁字典（★不删除任何既有字段）。"""
    _r = active_reprobe(patch)
    patch.update(_r)
    return _r


def detector_coverage(patches: list[dict[str, Any]]) -> dict[str, Any]:
    """★第55批 T6.2：检测器覆盖守卫（防静默退化）。

    新增 ``issue_type`` 而忘了配检测器 → 该类型补丁**静默变成 not_applicable**，
    可判定率悄悄下降却无人告警。本函数把缺口**显式暴露**出来，
    供 ReportBus / 门控断言使用。

    Returns:
        ``{total, covered, uncovered_types, uncovered_count, coverage_rate}``
    """
    _by: dict[str, int] = {}
    for _p in patches or []:
        if not isinstance(_p, dict):
            continue
        _k = _p.get("issue_type")
        _key = _k if isinstance(_k, str) else "(缺失/非字符串)"
        _by[_key] = _by.get(_key, 0) + 1

    _cov, _uncov = {}, {}
    for _k, _n in _by.items():
        if detector_for(_k) is None:
            _uncov[_k] = _n
        else:
            _cov[_k] = _n
    _total = sum(_by.values())
    _unc_n = sum(_uncov.values())
    return {
        "total": _total,
        "covered": _total - _unc_n,
        "uncovered_count": _unc_n,
        "covered_types": _cov,
        "uncovered_types": _uncov,
        "coverage_rate": ((_total - _unc_n) / _total) if _total else 0.0,
    }


def _scope_unknown() -> str:
    """``not_applicable`` 时无作用域概念，返回占位值。"""
    return S_BLOCK


def reprobe_batch(patches: list[dict[str, Any]]) -> dict[str, Any]:
    """批量复现（返回汇总统计）。"""
    _rows = []
    for _p in patches:
        if not isinstance(_p, dict):
            continue
        _rows.append({
            "id": _p.get("id"), "file": _p.get("file"),
            "method": _p.get("method"),
            **active_reprobe(_p),
        })
    _by: dict[str, int] = {}
    for _r in _rows:
        _v = _r[F_REPROBE_VERDICT]
        _by[_v] = _by.get(_v, 0) + 1
    return {"total": len(_rows), "by_verdict": _by, "rows": _rows}


#: 结论的中文标签（展示层用）
VERDICT_LABELS: dict[str, str] = {
    V_TRUE_PASS: "真通过",
    V_PARTIAL_FIX: "部分修复",
    V_FALSE_PASS: "假通过(无法复现)",
    V_INEFFECTIVE: "修复无效",
    V_VERIFY_FAILED: "验证失败",
    V_NOT_APPLICABLE: "不适用(无检测器)",
}


def verdict_label(verdict: str) -> str:
    """结论 → 中文标签。"""
    return VERDICT_LABELS.get(verdict, str(verdict))


def true_fix_rate(patches: list[dict[str, Any]],
                  strict: bool = False,
                  force: bool = False) -> float | None:
    """**主动复现口径**的真实修复率。

    Args:
        patches: 补丁列表。
        strict: ``True`` → 仅计 ``true_pass``（问题完全清零）；
                ``False``（默认）→ 计 ``true_pass + partial_fix``（问题已被减少）。
        force:  ★第55批 T6.1 —— ``True`` 则**忽略已回填的旧结论**、现场重算。

                ★背景：``_verdicts`` 默认复用补丁里已回填的 ``reprobe_verdict``。
                  第51批回填的结论（如 7 条 ``partial_fix``）是**按全块统计**得出的，
                  开启 diff 区域限定后若不重算，修复率仍显示旧值 → 改动"看起来没生效"。
                  故提供 ``force`` 让调用方拿**当前口径**的真实值。

    ★分母**排除** ``not_applicable``（无检测器）—— 否则会人为稀释。
    ★无任何可判定样本 → 返回 ``None``（不返回 0.0，避免"0/0=1.0"型虚假满分）。
    """
    _ok = _verdicts(patches, force=force)
    _judgeable = [v for v in _ok if v != V_NOT_APPLICABLE]
    if not _judgeable:
        return None
    _good = {V_TRUE_PASS} if strict else {V_TRUE_PASS, V_PARTIAL_FIX}
    return round(sum(1 for v in _judgeable if v in _good)
                 / float(len(_judgeable)), 4)


def verdict_counts(patches: list[dict[str, Any]],
                   force: bool = False) -> dict[str, int]:
    """按结论汇总（含中文标签键，便于报告展示）。

    Args:
        force: ★第55批 T6.1 —— ``True`` 则忽略已回填结论、现场重算。
    """
    _c: dict[str, int] = {}
    for _v in _verdicts(patches, force=force):
        _c[_v] = _c.get(_v, 0) + 1
    return _c


def _verdicts(patches: list[dict[str, Any]],
              force: bool = False) -> list[str]:
    """取每条补丁的复现结论。

    Args:
        force: ``True`` → **忽略**已回填的 ``reprobe_verdict``，一律现场重算。
               默认 ``False``（复用回填值，与第51批语义一致）。
    """
    _out = []
    for _p in patches:
        if not isinstance(_p, dict):
            continue
        _v = None if force else _p.get(F_REPROBE_VERDICT)
        if _v is None:
            _v = active_reprobe(_p)[F_REPROBE_VERDICT]
        _out.append(_v)
    return _out


# 兼容：允许用正则做静态兜底（不参与主判据，仅供诊断）
_SILENT_RE: re.Pattern = re.compile(r"except\s+[^\n:]+:\s*\n\s*pass\s*(?:\n|$)")


def lint_silent_hint(code: Any) -> int:
    """正则口径的"疑似静默异常"计数（**仅诊断用**，主判据一律走 AST）。"""
    if not isinstance(code, str):
        return 0
    return len(_SILENT_RE.findall(code.replace("\r\n", "\n")))

# _m51_t1_partial