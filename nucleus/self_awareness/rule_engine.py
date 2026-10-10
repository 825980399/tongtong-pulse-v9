# -*- coding: utf-8 -*-
"""★第158批 上-A T-自我审计-1（P1）：运行时规则引擎（增量档）。

定位
----
把「自我审计」从**人工翻代码**变成**可执行的规则集**：规则带元数据
（编号 / 描述 / 严重级 / 检查器），引擎按**增量档**只跑变更文件，出分级报告。

设计取舍（与既有门禁的边界）
--------------------------
* 本引擎**不替代** pre-commit 门禁（cw2 / arity / red-baseline 等）——那些是
  **提交前红绿门禁**；本件是**运行时自我审计**（定时跑、留痕、出趋势），
  二者互补不重叠。
* 规则**只读**：不改任何生产代码、不发网络请求。发现的问题只**报告**，
  修复由人工/专门批次决定（符合「执行方不做裁决」）。

增量档
------
默认只取 **git 变更文件**（``HEAD~1..HEAD`` 的 .py + 工作树未提交变更），
避免每轮全仓扫描的成本；``full=True`` 时回退全量。变更集为空即**跳过**
并如实标记 ``skipped_empty_diff``（不假装"检查了 0 个文件 = 全部通过"）。

规则元数据（TT001 风格）
------------------------
每条规则含 ``id`` / ``title`` / ``severity`` / ``scope`` / ``checker``，
``severity`` ∈ ``error`` / ``warn`` / ``info``，与 T-规则生命周期-1 的
「规则签名」配套（规则集本身可被指纹校验，防意外修改）。
"""
from __future__ import annotations

import hashlib
import inspect
import io
import logging
import os
import subprocess
import sys
import time
from typing import Any

from nucleus._silent_except import silent_exc

__all__ = [
    "SEVERITIES",
    "RULE_REGISTRY",
    "engine_version",
    "rule_sha16",
    "rule_signatures",
    "changed_files",
    "run_rules",
    "latest_report_path",
]

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_LOG = logging.getLogger("rule_engine")

#: 严重级（由重到轻）。
SEVERITIES = ("error", "warn", "info")


# ------------------------------------------------------------------ 规则实现
def _iter_lines(path):
    """读文件并按 CRLF 归一，返回 (行号, 行文本) 迭代器。"""
    with io.open(path, encoding="utf-8", errors="replace") as _f:
        for _i, _line in enumerate(_f.read().replace("\r\n", "\n").split("\n"), 1):
            yield _i, _line


def check_silent_except(path) -> list[dict]:
    """TT001：裸 ``except ...: pass``（静默吞异常）检出。"""
    _hits = []
    try:
        _lines = [(_i, _l.rstrip()) for _i, _l in _iter_lines(path)]
    except OSError as _e:
        silent_exc(_e, where="nucleus.self_awareness.rule_engine::check_silent_except",
                   level="debug")
        return _hits
    for _k in range(len(_lines) - 1):
        _i, _s = _lines[_k]
        _nxt = _lines[_k + 1][1].strip()
        if (_s.lstrip().startswith("except") and _s.rstrip().endswith(":")
                and _nxt in ("pass", "...")):
            _hits.append({"line": _i, "text": _s.strip()[:100]})
    return _hits


def check_lone_lf(path) -> list[dict]:
    """TT002：孤立 LF（lone LF）检出——本仓要求 CRLF。"""
    _hits = []
    try:
        with open(path, "rb") as _f:
            _d = _f.read()
    except OSError as _e:
        silent_exc(_e, where="nucleus.self_awareness.rule_engine::check_lone_lf",
                   level="debug")
        return _hits
    _crlf = _d.count(b"\r\n")
    _lone = _d.count(b"\n") - _crlf
    if _lone > 0:
        _hits.append({"line": 0, "text": "loneLF=%d（应为 0，仓要求 CRLF）" % _lone})
    if b"\r\r\n" in _d:
        _hits.append({"line": 0, "text": "CRCRLF 行尾污染"})
    return _hits


def check_todo_marker(path) -> list[dict]:
    """TT003：遗留 TODO/FIXME/XXX 标记（info 级，仅提示不阻断）。"""
    _hits = []
    try:
        for _i, _line in _iter_lines(path):
            if any(_k in _line for _k in ("TODO", "FIXME", "XXX")):
                _hits.append({"line": _i, "text": _line.strip()[:100]})
    except OSError as _e:
        silent_exc(_e, where="nucleus.self_awareness.rule_engine::check_todo_marker",
                   level="debug")
    return _hits


#: 规则注册表（元数据 + 检查器）——规则集本体，可被 :func:`rule_signatures` 指纹化。
RULE_REGISTRY: dict[str, dict[str, Any]] = {
    "TT001": {
        "title": "静默 except（裸 except: pass）",
        "severity": "error",
        "scope": "py",
        "checker": check_silent_except,
    },
    "TT002": {
        "title": "行尾不合规（loneLF / CRCRLF）",
        "severity": "error",
        "scope": "any",
        "checker": check_lone_lf,
    },
    "TT003": {
        "title": "遗留 TODO/FIXME/XXX 标记",
        "severity": "info",
        "scope": "py",
        "checker": check_todo_marker,
    },
}


def engine_version() -> str:
    """引擎版本。"""
    return "158A-T1-1"


def rule_sha16(title, severity, scope, checker_src):
    """★第159批上A 刀2（T-规则生命周期-1）统一规则签名口径。

    与 ``tools/ci/verify_fingerprint_gate.py::fp_rule_sha16`` 逐字节一致
    （同源归一：``" ".join((src or "").split())``），确保「改函数体→红、
    改缩进/挪行→不红」在引擎侧与 CI 门禁侧判定一致。

    归一逻辑与 ``tools/ci/ci_common.py::normalize_body`` 同源（折叠全部空白
    为单空格），故改检测逻辑函数体 → 归一串变 → 指纹变红；仅改缩进/换行/
    挪行 → 归一串不变 → 指纹不红。
    """
    _body = " ".join((checker_src or "").split())
    _raw = "%s|%s|%s|%s" % (title, severity, scope, _body)
    return hashlib.sha256(_raw.encode("utf-8")).hexdigest()[:16]


def rule_signatures() -> dict[str, str]:
    """★规则集指纹（T-规则生命周期-1 配套，第159批上A 刀2 口径统一）。

    旧口径仅拼接 ``title|severity|scope``，检测逻辑函数体变更不可见（缺陷）。
    新口径 = ``sha16(title|severity|scope|normalize_body(checker_src))``：
    改函数体 → 指纹变红；改缩进/挪行 → 归一后不变 → 不红。
    """
    _out: dict[str, str] = {}
    for _rid, _r in sorted(RULE_REGISTRY.items()):
        _checker = _r.get("checker")
        _src = ""
        if callable(_checker):
            try:
                _src = inspect.getsource(_checker)
            except (OSError, TypeError) as _e:
                silent_exc(_e, where="nucleus.self_awareness.rule_engine::rule_signatures",
                           level="debug")
        _out[_rid] = rule_sha16(_r["title"], _r["severity"], _r["scope"], _src)
    return _out


# ------------------------------------------------------------------ 增量档
def changed_files(base="HEAD~1", target="HEAD", root=None) -> list[str]:
    """取增量档文件集（``base..target`` 的 .py + 工作树未提交变更）。

    Returns:
        相对仓库根的路径列表（已去重、已过滤 .py）。失败返回 ``[]``。
    """
    _root = root or _PROJECT_ROOT
    _out: list[str] = []
    try:
        _r = subprocess.run(
            ["git", "diff", "--name-only", "%s..%s" % (base, target)],
            cwd=_root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60, check=False)
        if _r.returncode == 0:
            _out.extend([x.strip() for x in _r.stdout.splitlines() if x.strip()])
    except Exception as _e:
        silent_exc(_e, where="nucleus.self_awareness.rule_engine::changed_files diff",
                   level="debug")
        _LOG.warning("增量档读取失败(git diff %s..%s): %s: %s",
                     base, target, type(_e).__name__, _e)
    try:
        _r2 = subprocess.run(["git", "diff", "--name-only", "HEAD"],
                             cwd=_root, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60,
                             check=False)
        if _r2.returncode == 0:
            _out.extend([x.strip() for x in _r2.stdout.splitlines() if x.strip()])
    except Exception as _e:
        silent_exc(_e, where="nucleus.self_awareness.rule_engine::changed_files worktree",
                   level="debug")
        _LOG.warning("增量档读取失败(git diff HEAD 工作树): %s: %s",
                     type(_e).__name__, _e)
    return sorted({x for x in _out if x.endswith(".py")
                   and os.path.isfile(os.path.join(_root, x))})


# ------------------------------------------------------------------ 执行
def run_rules(files=None, full=False, root=None) -> dict[str, Any]:
    """执行规则集。

    Args:
        files: 指定文件集（相对路径）；``None`` 且 ``full=False`` → 走**增量档**。
        full: True → 全量扫描（较慢，日常不建议）。
        root: 仓库根（测试可注入）。

    Returns:
        ``{scanned, skipped, by_severity, findings, rule_signatures, ran_at,
        mode, engine_version}``。
    """
    _root = root or _PROJECT_ROOT
    if full:
        _mode = "full"
        _files = []
        for _dp, _dns, _fns in os.walk(_root):
            _dns[:] = [d for d in _dns
                       if not d.startswith(".bak") and d not in
                       ("__pycache__", ".git", "tmp", "node_modules", ".venv", "venv")]
            for _fn in _fns:
                if _fn.endswith(".py"):
                    _files.append(os.path.relpath(os.path.join(_dp, _fn), _root)
                                  .replace("\\", "/"))
        _files = sorted(_files)
    elif files is not None:
        _mode = "explicit"
        _files = [f for f in files if str(f).endswith(".py")
                  and os.path.isfile(os.path.join(_root, str(f)))]
    else:
        _mode = "incremental"
        _files = changed_files(root=_root)

    _findings: list[dict] = []
    for _rel in _files:
        _abs = os.path.join(_root, _rel)
        for _rid, _r in sorted(RULE_REGISTRY.items()):
            try:
                _hits = _r["checker"](_abs) or []
            except Exception as _e:
                # ★规则自身出错不得中断整轮：留痕并计入 info
                silent_exc(_e, where="nucleus.self_awareness.rule_engine::run_rules:%s" % _rid,
                           level="warning")
                _findings.append({"rule": _rid, "file": _rel, "line": 0,
                                  "severity": "info", "text": "规则执行异常: %s" % type(_e).__name__})
                continue
            for _h in _hits:
                _findings.append({
                    "rule": _rid, "file": _rel,
                    "line": _h.get("line", 0),
                    "severity": _r["severity"],
                    "text": _h.get("text", ""),
                })

    _by_sev = {s: 0 for s in SEVERITIES}
    for _f in _findings:
        _by_sev[_f["severity"]] = _by_sev.get(_f["severity"], 0) + 1

    return {
        "engine_version": engine_version(),
        "mode": _mode,
        "scanned": len(_files),
        "skipped": (None if _files else "empty_diff_or_no_files"),
        "by_severity": _by_sev,
        "findings": _findings,
        "rule_signatures": rule_signatures(),
        "ran_at": time.time(),
    }


def latest_report_path() -> str:
    """报告落盘路径（``data/self_awareness/rule_reports/rule_<ts>.json``）。"""
    return os.path.join(_PROJECT_ROOT, "data", "self_awareness", "rule_reports",
                        "rule_%s.json" % time.strftime("%Y%m%d_%H%M%S"))


def _is_test_env() -> bool:
    try:
        return ("pytest" in sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception as e:
        silent_exc(e, where="nucleus.self_awareness.rule_engine::_is_test_env")
        return False
