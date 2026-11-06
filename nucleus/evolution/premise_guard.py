# -*- coding: utf-8 -*-
"""premise_guard.py —— 生成前的 T0 前提核实（第462批）

## 补的是什么

457 批把「假设生成连续 3 次 8s 超时堵死主链」推翻（完成日志计数 0，
真凶是 Playwright `page_load=8000` 与空答案出口）；459 批把「台账在
data/patches/ 且只有 20 条」推翻（真实在 data/reports/、229 条）。两次靠的都是
**动手之前先证伪前提**。闭环内此前没有这个角色：issue 从日志 / 静态扫描 /
代码审查 / 运行期派单进来，直接进生成，没人问「这个问题现在还成立吗」。

## 判据（不重复造轮子）

1. 结构：目标文件是否存在、method 是否还能在 AST 里找到、`original_code`
   是否还出现在当前文件里 —— 任一明确失效 ⇒ `stale`。
2. 语义：**复用 446 刀1 的检测器注册表**对当前源码跑一次；
   同一套判据说「命中 0」⇒ 问题已不在 ⇒ `stale`；「命中 >=1」⇒ `confirmed`。
   没有对应检测器 ⇒ 只给 `unknown`（不猜、不冒充通过）。
3. 三态：`confirmed`（可生成）/ `stale`（前提失效，剔除并留痕）/
   `unknown`（测不了，保留但标注 —— 无法判定 ≠ 失败，铁律同型）。

## 边界

纯读 + append-only 留痕；不改源码、不改 issue 对象、不碰 pending 写口。
pytest 护栏：默认生产路径 + pytest ⇒ 不写盘。
"""
from __future__ import annotations

import ast
import json
import os
import sys
import time
from typing import Any

import config
from nucleus._silent_except import silent_exc

SCHEMA = "premise_guard/462.v1"
P_CONFIRMED = "confirmed"
P_STALE = "stale"
P_UNKNOWN = "unknown"
_DEFAULT_REL = "data/reports/evolution/premise_trace.jsonl"


def _cfg(name: str, default: Any) -> Any:
    _v = getattr(config, name, None)
    return default if _v is None else _v


def _on() -> bool:
    return bool(_cfg("ENABLE_M462_PREMISE_GUARD", True))


def _root(root: str | None = None) -> str:
    if root:
        return str(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _writable(root: str | None) -> bool:
    if root is not None:
        return True
    return "pytest" not in sys.modules


def trace_path(root: str | None = None) -> str:
    return os.path.join(_root(root), str(_cfg("M462_PREMISE_TRACE_REL", _DEFAULT_REL)))


def note(row: dict, root: str | None = None) -> bool:
    if not _writable(root):
        return False
    _p = trace_path(root)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="premise_guard::note", level="debug")
        return False


def _abs_file(issue: dict, root: str | None) -> str:
    _f = str((issue or {}).get("file") or "").replace("\\", "/").lstrip("./")
    if not _f:
        return ""
    return _f if os.path.isabs(_f) else os.path.join(_root(root), _f)


def _method_exists(src: str, method: str) -> bool | None:
    """True/False = 明确查到/查不到；None = 源码不可解析（不可判定）。"""
    try:
        _tree = ast.parse(src)
    except SyntaxError as _e:
        silent_exc(_e, where="premise_guard::_method_exists parse", level="debug")
        return None
    except Exception as _e:
        silent_exc(_e, where="premise_guard::_method_exists other", level="debug")
        return None
    for _n in ast.walk(_tree):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == method:
            return True
    return False


def verify_premise(issue: dict, *, root: str | None = None,
                   now: float | None = None) -> dict:
    """三态前提核实。返回判定 dict（不修改 issue）。"""
    _now = float(now if now is not None else time.time())
    _out: dict = {"premise": P_UNKNOWN, "why": "", "checks": {},
                  "hits": None, "file": str((issue or {}).get("file") or ""),
                  "method": str((issue or {}).get("method") or ""),
                  "issue_type": str((issue or {}).get("issue_type")
                                    or (issue or {}).get("type") or ""),
                  "at": _now, "schema": SCHEMA}
    if not _on():
        _out["why"] = "ENABLE_M462_PREMISE_GUARD=False ⇒ 不核实"
        return _out
    if not isinstance(issue, dict) or not issue:
        _out["why"] = "空问题单"
        return _out

    _checks = _out["checks"]
    _abs = _abs_file(issue, root)
    if not _abs:
        _out["why"] = "无 file 字段 ⇒ 不可判定（保留但不冒充通过）"
        return _out
    if not os.path.isfile(_abs):
        _out["premise"] = P_STALE
        _out["why"] = "目标文件不存在 ⇒ 前提失效"
        _checks["file_exists"] = False
        return _out
    _checks["file_exists"] = True

    try:
        with open(_abs, "r", encoding="utf-8") as _f:
            _src = _f.read()
    except Exception as _e:
        silent_exc(_e, where="premise_guard::verify_premise read", level="debug")
        _out["why"] = f"源码读取失败 ⇒ 不可判定: {type(_e).__name__}"
        return _out

    _method = str(issue.get("method") or "").strip()
    if _method:
        _m = _method_exists(_src, _method)
        _checks["method_exists"] = _m
        if _m is False:
            _out["premise"] = P_STALE
            _out["why"] = (f"方法 {_method} 已不在源码中（生成侧可能虚构了方法名）"
                           "⇒ 前提失效，补丁无处可打")
            return _out

    _orig = str(issue.get("original_code") or "").strip()
    if _orig:
        _norm = "".join(_orig.split())
        _present = _norm in "".join(_src.split())
        _checks["original_code_present"] = _present
        if not _present:
            _out["premise"] = P_STALE
            _out["why"] = "原始代码片段已不在当前文件 ⇒ 前提失效（代码已被改动）"
            return _out

    # ★语义反查：复用 446 刀1 的检测器，优先对 issue 自带的 original_code 片段跑
    #   （与静态复现探针同口径）；没有片段才退到整文件源码。
    # ★★462 首轮实测：检测器是按**代码片段**设计的，拿整文件跑会误判 ——
    #   `non_atomic_write` 对 PulseMetricsCollector.py 整文件命中 0，但该问题
    #   确实存在（07:45 实盘样本）。故「命中 0」只能判 unknown，**绝不能判 stale**，
    #   否则会把真问题全部误杀、闭环全灭。只有结构层面明确失效才判 stale。
    try:
        from nucleus.evolution.patch_active_reprobe import detector_hits
        # ★★462 二轮实测：`original_code` **绝不能 strip** —— 首行前导空格是
        #   相对缩进的一部分，strip 掉会让包装后的片段 IndentationError。
        #   判空用 strip 的结果，送探针用原始串（与 self_verdict 同口径）。
        _raw_full = str(issue.get("original_code") or "")
        _has_snippet = bool(_raw_full.strip())
        _target = _raw_full if _has_snippet else _src
        _scope = "snippet" if _has_snippet else "whole_file"
        # ★462 首轮单测实测：缩进片段直接送 detector_hits 会让 parse_with_offset
        #   抛 IndentationError ⇒ 命中 0 ⇒ 误判 unknown。必须与复现探针同口径，
        #   先做片段归一化包装（函数内惰性导入，避免循环导入）。
        if _scope == "snippet":
            try:
                from nucleus.evolution.self_verdict import normalize_snippet
                _target, _ = normalize_snippet(_target)
            except Exception as _e:
                silent_exc(_e, where="premise_guard::verify_premise normalize",
                           level="debug")
        _hits = detector_hits(_out["issue_type"], _target)
        _n = len(_hits) if isinstance(_hits, list) else 0
        _out["hits"] = _n
        _out["scope"] = _scope
        _checks["detector_hits_current"] = _n
        if _n > 0:
            _out["premise"] = P_CONFIRMED
            _out["why"] = f"{_scope} 仍检出 {_n} 处 ⇒ 前提成立"
        else:
            _out["premise"] = P_UNKNOWN
            _out["why"] = (f"{_scope} 检出 0 处 ⇒ 不可判定（检测器作用域可能不适配，"
                           "不冒充『问题已消失』）")
        return _out
    except Exception as _e:
        silent_exc(_e, where="premise_guard::verify_premise detector", level="debug")
        _out["why"] = "检测器不可用 ⇒ 不可判定（不猜）"
        return _out


def filter_issues(issues: Any, *, root: str | None = None,
                  now: float | None = None) -> tuple[Any, int, list[dict]]:
    """给问题源出口用：剔除 `stale`，保留 `confirmed` / `unknown`。

    Returns: `(kept, blocked_count, blocked_rows)`；非 list 原样返回。
    ★只剔除、不改保留条目的字段（避免悄悄改写既有契约）。
    """
    # ★462 隔离护栏：pytest + 默认生产路径 ⇒ 不做核实（既有用例构造的
    #   issue 常指向不存在的文件，若照常核实会被判 stale 而误删）。
    if not _on() or not isinstance(issues, list) or not _writable(root):
        return issues, 0, []
    _kept: list = []
    _blocked: list[dict] = []
    for _it in issues:
        if isinstance(_it, dict):
            _v = verify_premise(_it, root=root, now=now)
            if _v["premise"] == P_STALE:
                _blocked.append({"file": _v["file"], "method": _v["method"],
                                 "issue_type": _v["issue_type"], "why": _v["why"]})
                note({"kind": "premise", "at": _v["at"], "schema": SCHEMA,
                      "premise": P_STALE, **{k: _v[k] for k in
                                             ("file", "method", "issue_type", "why", "checks")}},
                     root=root)
                continue
        _kept.append(_it)
    return _kept, len(_blocked), _blocked
