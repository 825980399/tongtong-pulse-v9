# -*- coding: utf-8 -*-
"""存量 `verifiable_rate` 重算机制 · ★374 刀4（票 `T-存量重算机制-1`）

## 问题

`verifiable_rate = 可判定补丁数 / 总补丁数`，而"可判定"取决于每条补丁的
`problem_fixed` 是否已派生。派生只在**两个时刻**发生：

* 补丁刚应用完（`PatchManager` 写入）；
* 人工跑 `tools/backfill_patch_verification_split.py`。

于是存量的 84 条历史补丁**永远停在 `problem_fixed=None`**：哪怕后来探针/基线
给出了新证据，也没有任何东西回去把字段重算一遍 ⇒ 比率冻结在 0.2143，
「具身有眼 0.5+」的承诺无法兑现。**不是判据太严，是没人再算一次。**

## 机制（本模块）

`recompute()`：读 `data/patches/patch_history.json` → 用既有纯函数
`patch_verification_split.backfill(apply=True)` 重新派生 → **只有真变了才落盘**
（原子写 + 写盘守卫）。

* **定时/事件触发**：由 `main.py` 主节拍调用 `maybe_recompute()`（6h 一次，
  模块级节流；重启后首个节拍会算一次，正好覆盖"停机期间来的新证据"）。
* **读多写少**：无变化即 `state="no_change"`，一次都不写；有变化才写一次。
* **不碰活体**：只读写 `patch_history.json`（历史补丁档），**不碰
  `pending_patches.json`**（那是派单活体，另有单写者）。
* **不改目标值**：0.5+ 的产品目标不在这里，本模块只负责让比率随证据滚动。
* **不改判据**：`backfill()` / `split_verification()` 一行未动，本模块只是它的
  **调度者**。
"""
from __future__ import annotations

import copy
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.data.write_guard import guard_write
from nucleus.evolution.patch_verification_split import (
    F_EFFECTIVENESS,
    F_GRANULARITY,
    F_NO_REGRESSION,
    F_PF_PARTIAL,
    F_PF_REASON,
    F_PROBLEM_FIXED,
    F_SPLIT_VERSION,
    F_VERIFIED,
    backfill,
)
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("VerifiableRecompute")

#: 历史补丁档相对路径（唯一数据源；**不是** pending_patches.json）
HISTORY_REL = os.path.join("data", "patches", "patch_history.json")

#: 重算间隔（秒）。6h 的取法：证据（探针/基线对比）本身按日级产生，
#: 更密的频率只是反复读同一份 5MB JSON + 反复原子写，收益为零。
#: 这是**节流常量**，不是 config 开关（任务书：本批不新增开关）。
RECOMPUTE_MIN_INTERVAL_SEC = 6 * 3600

#: `backfill()` 写入的第 8 个派生字段（人读理由；`backfill` 里是裸字符串）
_F_SPLIT_REASON = "verification_split_reason"

#: 参与"是否变化"比较的字段 = **`backfill()` 会写的全部派生字段**。
#: 少收一个就会「库里字段是旧的、指纹却判没变」⇒ 永远不修，且这种遗漏完全静默
#: （任务书原话：存量停在旧值不是判据太严，是没人再算一次）。
#: 这些字段全是**存储输入的确定性派生**，不是活体读数，适合做变化指纹。
#: ★例外说明：`verified` 是**自指**的——它既是派生输出，又是 `no_regression`
#:   的 legacy 输入（`verification.passed` 缺失时 `split_verification` 回退读它）。
#:   仍要收进指纹，因为它是"存量写着 `verified=True` 却从没真验证过"的唯一痕迹源；
#:   自指只影响**第一次**（`bool(bool(x))` 是不动点），不存在每拍漂移的写盘循环
#:   ——`test_12b` 就是钉这件事的。
_DERIVED_FIELDS = (F_NO_REGRESSION, F_PROBLEM_FIXED, F_PF_REASON, F_PF_PARTIAL,
                   F_GRANULARITY, F_EFFECTIVENESS, F_VERIFIED, F_SPLIT_VERSION,
                   _F_SPLIT_REASON)

_LAST_TS = 0.0


def history_path(root: str | None = None) -> str:
    _root = str(root) if root else os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(_root, HISTORY_REL)


def _load(path: str) -> tuple[Any, list | None]:
    """返回 `(文档, 补丁列表)`；结构不认识返回 `(None, None)`（不猜）。"""
    try:
        with open(path, encoding="utf-8") as _f:
            _doc = json.load(_f)
    except Exception as _e:
        silent_exc(_e, where="verifiable_recompute::_load", level="warning")
        return None, None
    if isinstance(_doc, list):
        return _doc, _doc
    if isinstance(_doc, dict):
        for _k in ("patches", "records", "items", "data"):
            if isinstance(_doc.get(_k), list):
                return _doc, _doc[_k]
    return None, None


def _eol_of(path: str) -> str:
    """探测原文件行尾（铁律138/229：改写数据档必须按原行尾回写）。

    真档 `patch_history.json` 是 CRLF；若统一按 `os.linesep` 写，跨平台会把
    整档换行翻一遍——即使内容一字未改也造出全文件 diff。
    """
    try:
        with open(path, "rb") as _f:
            _head = _f.read(4096)
        return "\r\n" if b"\r\n" in _head else "\n"
    except Exception as _e:
        # 读不到行尾不该阻断重算，但**必须留痕**（纯 return 也是静默 except，铁律176）
        silent_exc(_e, where="verifiable_recompute::_eol_of", level="debug")
        return os.linesep


def _atomic_write(path: str, doc: Any) -> bool:
    """同目录临时文件 + `os.replace`（失败不留半截文件；行尾守恒）。"""
    _tmp = path + ".tmp_m374"
    try:
        with open(_tmp, "w", encoding="utf-8", newline=_eol_of(path)) as _f:
            json.dump(doc, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp, path)
        return True
    except Exception as _e:
        silent_exc(_e, where="verifiable_recompute::_atomic_write", level="warning")
        try:
            if os.path.exists(_tmp):
                os.remove(_tmp)
        except Exception as _e2:
            silent_exc(_e2, where="verifiable_recompute::_atomic_write cleanup",
                       level="debug")
        return False


def _fingerprint(patches: list) -> list:
    """派生字段指纹（用于判"是否真变了"）。"""
    return [tuple(p.get(_k) for _k in _DERIVED_FIELDS)
            for p in patches if isinstance(p, dict)]


def recompute(*, root: str | None = None, path: str | None = None,
              now: float | None = None, dry_run: bool = False,
              force: bool = False) -> dict:
    """重算存量派生字段，让 `verifiable_rate` 随证据滚动。

    Returns: `{state, path, total, changed, verifiable_count, verifiable_rate,
               real_fix_rate, reason}`
      `state` ∈ `ok` / `no_change` / `no_history` / `bad_structure` /
                `throttled` / `dry_run` / `write_rejected` / `write_failed`
    """
    global _LAST_TS
    _now = float(now if now is not None else time.time())
    _p = path or history_path(root)
    _rep: dict[str, Any] = {"state": "no_history", "path": _p, "total": 0,
                            "changed": 0, "verifiable_count": 0,
                            "verifiable_rate": None, "real_fix_rate": None,
                            "reason": ""}
    if not force and _LAST_TS > 0 and (_now - _LAST_TS) < RECOMPUTE_MIN_INTERVAL_SEC:
        _rep["state"] = "throttled"
        _rep["reason"] = f"距上次 {_now - _LAST_TS:.0f}s < {RECOMPUTE_MIN_INTERVAL_SEC}s"
        return _rep
    if not os.path.isfile(_p):
        _rep["reason"] = "patch_history 不存在（无非可算）"
        return _rep
    _doc, _patches = _load(_p)
    if _patches is None:
        _rep["state"] = "bad_structure"
        _rep["reason"] = "结构不认识（既非 list 也非含 patches/records/items/data 的 dict）"
        return _rep

    _rep["total"] = len(_patches)
    _before = _fingerprint(_patches)
    # ★先在**深拷贝**上派生：dry_run 也要如实报"会变多少条"。
    #   直接对原列表 `apply=False` 的话，派生字段根本没变 ⇒ changed 恒 0（假读数）。
    _work = copy.deepcopy(_patches)
    _stats = backfill(_work, apply=True)
    _after = _fingerprint(_work)
    _changed = sum(1 for _a, _b in zip(_before, _after) if _a != _b)
    _rep["changed"] = _changed
    _rep["verifiable_count"] = int(_stats.get("verifiable_count", 0) or 0)
    _rep["verifiable_rate"] = _stats.get("verifiable_rate")
    _rep["real_fix_rate"] = _stats.get("real_fix_rate")

    if dry_run:
        _rep["state"] = "dry_run"
        _rep["reason"] = f"预演：{_changed} 条会变"
        return _rep
    if _changed <= 0:
        _rep["state"] = "no_change"
        _rep["reason"] = "派生字段已是最新（不写盘）"
        _LAST_TS = _now
        return _rep
    if not guard_write(_p, component="VerifiableRecompute"):
        _rep["state"] = "write_rejected"
        _rep["reason"] = "写盘守卫拒绝（测试/隔离环境）"
        return _rep
    # 正式写：把派生结果应用到真实列表（与拷贝上算出的一致，逐字段同源）
    backfill(_patches, apply=True)
    if not _atomic_write(_p, _doc):
        _rep["state"] = "write_failed"
        _rep["reason"] = "原子写失败"
        return _rep
    _LAST_TS = _now
    _rep["state"] = "ok"
    _module_logger.info(
        "[374刀4][存量重算] total=%d changed=%d verifiable=%d rate=%s real_fix=%s",
        _rep["total"], _rep["changed"], _rep["verifiable_count"],
        _rep["verifiable_rate"], _rep["real_fix_rate"])
    return _rep


def maybe_recompute(project_root: str | None = None, now: float | None = None) -> dict:
    """主节拍入口（6h 节流；异常不外泄——观测不得阻断主流程）。"""
    try:
        return recompute(root=project_root, now=now)
    except Exception as _e:
        silent_exc(_e, where="verifiable_recompute::maybe_recompute", level="warning")
        return {"state": "error", "reason": f"{type(_e).__name__}: {_e}"}


def reset_recompute_throttle() -> None:
    """★测试专用：清节流时间戳。"""
    global _LAST_TS
    _LAST_TS = 0.0
