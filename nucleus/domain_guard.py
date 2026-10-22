# -*- coding: utf-8 -*-
"""★344补3：A-17 三类出口域过滤 —— 隔离产物不得进生产。

**病根**（烛微 §五-⑤ 实证 · 挂账63）：
`data/reports/memory/top_types_20261022_194451.json` 的 `proc_gen=pid15780@...`
是**验证进程**（当时活体是 pid22576），却写进了生产目录；
`pending_patches` 里另有 `file=tmp/p342_root/d1_iso.py` 历史条目（隔离产物进生产池）。
**同一份数据被生产与验证两条路写，事后读的人分不清哪份是真活体。**

**三类出口**（任务书原话）：
  ① 日志                  —— 343 已做（`nucleus/evolution/LogAnalyzer` 的 `[343域过滤]`）
  ② pending/history 补丁池 —— 本模块 `guard_patch_entry`（结构性拒收隔离路径）
  ③ 自观测产物目录         —— 本模块 `guard_artifact_dir`（隔离声明在就改道）

**口径边界（重要，别把判据放宽成"看起来像"）**：
  · ②是**结构性判定**：只认明确属于隔离的路径形态（`tmp/`、隔离根、备份件后缀），
    不看文件名里有没有"iso"字样（铁律153：禁从字符串关键字猜意图）；
  · ③只认**显式隔离声明**（环境变量 `PULSE_ISOLATION_ROOT` / `PULSE_AUDIT_POOL_DIR`）。
    **为什么不按 proc_gen 判域**：活体代次目前没有落盘 oracle
    （`data/monitor/health_snapshot.json` 顶层无 `proc_gen`），
    拿不到真值就不能拿它当判据（铁律156/166：读不到就如实说读不到，不猜）。
    ⇒ "按 proc_gen 判域"列为移交项，需先补活体代次落盘。
"""
from __future__ import annotations

import os
from typing import Any

from nucleus.logger import get_module_logger

_module_logger = get_module_logger("nucleus.domain_guard")

#: 隔离根声明类环境变量（任一存在 ⇒ 本进程属隔离/验证进程）
ISOLATION_ENV_KEYS = (
    "PULSE_ISOLATION_ROOT",
    "PULSE_AUDIT_POOL_DIR",
    "PULSE_ISOLATED",
)

#: 明确的隔离路径前缀（相对仓库根；一律用正斜杠归一后比较）
ISOLATION_PATH_PREFIXES = ("tmp/", "tests/_probe_", "data/code_backups/")

#: 隔离件/备份件后缀特征（结构特征，不是内容关键字）
ISOLATION_NAME_MARKERS = (".bak_before_", ".restored", ".tmp335", ".tmp")

#: 一次性统计（供计数留痕行使用；不落盘、不参与判据）
STATS = {"patch_rejected": 0, "artifact_rerouted": 0}


def isolation_declared() -> str:
    """返回**命中的隔离声明**（env 名=值），无声明返回空串。"""
    for _k in ISOLATION_ENV_KEYS:
        _v = os.environ.get(_k, "").strip()
        if _v:
            return "%s=%s" % (_k, _v)
    return ""


def norm_path(p: Any) -> str:
    """仓库内路径归一：反斜杠→正斜杠、去首尾空白、去 `./` 前缀。"""
    _s = str(p or "").strip().replace("\\", "/")
    while _s.startswith("./"):
        _s = _s[2:]
    return _s


def is_isolation_path(p: Any) -> tuple[bool, str]:
    """该路径是否属**隔离产物**。返回 `(是, 原因)`。

    只做结构性判定（前缀/后缀），不看内容关键字。
    """
    _n = norm_path(p)
    if not _n:
        return False, ""
    for _pre in ISOLATION_PATH_PREFIXES:
        if _n.startswith(_pre):
            return True, "prefix:%s" % _pre
    for _mk in ISOLATION_NAME_MARKERS:
        if _mk in _n:
            return True, "marker:%s" % _mk
    return False, ""


def guard_patch_entry(entry: dict, trace: Any = None) -> tuple[bool, str]:
    """②补丁池出口：隔离产物条目**拒收**。返回 `(允许入库, 原因)`。

    允许为空 `file`（有些补丁只带 method），只有"路径明确属隔离"才拒。
    """
    if not isinstance(entry, dict):
        return True, ""
    _bad, _why = is_isolation_path(entry.get("file"))
    if not _bad:
        return True, ""
    STATS["patch_rejected"] += 1
    _msg = ("[343域过滤] 补丁池拒收隔离产物 file=%s why=%s patch=%s"
            % (norm_path(entry.get("file")), _why,
               str(entry.get("id") or "")[:24]))
    if callable(trace):
        try:
            trace("%s", _msg)
        except Exception as _te:  # noqa: BLE001 —— 留痕失败不得改变判定
            _module_logger.debug("域过滤留痕失败(补丁池): %s: %s", type(_te).__name__, _te)
    return False, _why


def guard_artifact_dir(path: str, trace: Any = None) -> tuple[str, str]:
    """③自观测产物出口：隔离进程**改道**到隔离根，返回 `(实际目录, 原因)`。

    只有存在显式隔离声明时才改道；生产进程原样返回（不引入不可证判据）。
    """
    _dir = str(path or "")
    _decl = isolation_declared()
    if not _decl:
        return _dir, ""
    _root = ""
    for _k in ISOLATION_ENV_KEYS:
        _v = os.environ.get(_k, "").strip()
        if _v:
            _root = _v if os.path.isabs(_v) else os.path.abspath(_v)
            break
    # `PULSE_AUDIT_POOL_DIR` 指向的是池目录本身，产物要落到其父级下的 reports
    if _root and os.path.basename(_root.rstrip("\\/")).lower() in ("audit", "pool"):
        _root = os.path.dirname(_root)
    _new = os.path.join(_root or os.path.abspath("."), "_m344_isolated_reports")
    STATS["artifact_rerouted"] += 1
    _msg = "[343域过滤] 产物改道 %s ⇒ %s 声明=%s" % (_dir, _new, _decl)
    if callable(trace):
        try:
            trace("%s", _msg)
        except Exception as _te:  # noqa: BLE001
            _module_logger.debug("域过滤留痕失败(产物改道): %s: %s", type(_te).__name__, _te)
    return _new, "isolation_declared"


def purge_pool_entries(rows: list, trace: Any = None) -> dict:
    """把已进池的隔离产物条目**清理出去**（一次性处置，幂等）。

    Returns: `{kept, removed, removed_ids, reasons}`。
    """
    _kept: list = []
    _removed: list = []
    _reasons: dict[str, int] = {}
    for _r in (rows or []):
        _d = _r if isinstance(_r, dict) else {}
        _ok, _why = guard_patch_entry(_d, trace=trace)
        if _ok:
            _kept.append(_r)
            continue
        # 非 dict 行拿不到 id：记占位而不是丢掉（"清了几条"必须可数）
        _rid = str(_d.get("id") or "")[:24] or "<non-dict>"
        _removed.append(_rid)
        _reasons[_why] = _reasons.get(_why, 0) + 1
    return {"kept": len(_kept), "removed": len(_removed),
            "removed_ids": _removed, "reasons": _reasons}


def domain_filter_stats() -> dict:
    """计数行用的只读摘要（本轮进程内累计）。"""
    return dict(STATS)


def reset_stats() -> None:
    """清计数（隔离验证用）。"""
    STATS["patch_rejected"] = 0
    STATS["artifact_rerouted"] = 0
