# -*- coding: utf-8 -*-
"""lesson_cooldown.py —— 统一同类错误冷却（第461批）

## 为什么要有这一层（实盘证伪，不是推测）

2026-11-06 09:04 重启后取证：460 已加载，但

* `converge_voided.jsonl` 里 `PulseLiver.py::_compress_group` 的 `no_def` 作废累计
  **59 次**；`failure_experience.jsonl` 的 `voided_no_def` 在 04:44 / 05:54 /
  07:09 / 08:38 连写 4 次 —— 459 的「写经验库 + append_reminder 提醒段」是
  **软保证**，LLM 不读就等于没写；
* 445 刀2 的 `require_full_def` 反馈只接在 `_generate_llm_patch`（单补丁路径），
  实盘作废的 `patch_multi_…` 来自 `generate_multi_file_patch`，**根本没读这份反馈**；
* 460 的冷却只在 `self_verdict.finalize()` 生效，而作废发生在
  `PatchManager._m280_converge_pending()` —— **完全不受约束**。

⇒ 三条路径各写各的，没有一处能真正让「同一个错误」停下来。

## 本模块的契约

* **唯一台账** `data/patches/lesson_cooldown.jsonl`（append-only）；
  作废路径与自裁决路径都写这里，不再各建一套。
* 连续计数按 `(file, method, error_type)`；**冷却生效按 `(file, method)`** ——
  目标已被反复证明改不对，就先整体停手，而不是赌下一次换个错误类型就能对。
* 守卫挂在补丁进入 pending 的**唯一收口**
  （`PatchManager._save_patch_list` 的 pending 分支），一处覆盖 9 个写点，
  并在两条生成路径提前跳过，做到「不再生成」而不只是「不再应用」。
* 失败绝不影响主流程；pytest 护栏：默认生产路径 + pytest ⇒ 不写盘。

## 边界

不替代 284 刀5 的结构判据、不改八闸、不改 447 日限额、不放宽任何闸门。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any

import config
from nucleus._silent_except import silent_exc

SCHEMA = "lesson_cooldown/461.v1"
_DEFAULT_REL = "data/patches/lesson_cooldown.jsonl"


def _cfg(name: str, default: Any) -> Any:
    """★只有 None 才算缺省（`getattr(..., d) or d` 会把 0 当缺省，踩过）。"""
    _v = getattr(config, name, None)
    return default if _v is None else _v


def _on() -> bool:
    return bool(_cfg("ENABLE_M461_LESSON_COOLDOWN", True))


def _guard_on() -> bool:
    return _on() and bool(_cfg("ENABLE_M461_GENERATION_GUARD", True))


def _root(root: str | None = None) -> str:
    if root:
        return str(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _writable(root: str | None) -> bool:
    if root is not None:
        return True
    return "pytest" not in sys.modules


def ledger_path(root: str | None = None) -> str:
    return os.path.join(_root(root), str(_cfg("M461_COOLDOWN_LEDGER_REL", _DEFAULT_REL)))


def _norm_file(file: Any) -> str:
    return str(file or "").replace("\\", "/").lstrip("./")


def target_key(file: Any, method: Any) -> str:
    """冷却生效键 `(file, method)`；sha1 不用内置 hash（跨进程稳定）。"""
    _raw = f"{_norm_file(file)}|{str(method or '').strip()}"
    return hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:16]


def failure_key(file: Any, method: Any, error_type: Any) -> str:
    """连续计数键 `(file, method, error_type)`。"""
    _raw = (f"{_norm_file(file)}|{str(method or '').strip()}|"
            f"{str(error_type or 'other').strip()}")
    return hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:16]


def _note(row: dict, root: str | None = None) -> bool:
    if not _writable(root):
        return False
    _p = ledger_path(root)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="lesson_cooldown::_note", level="debug")
        return False


def _rows(root: str | None = None) -> list[dict]:
    _p = ledger_path(root)
    _out: list[dict] = []
    try:
        if not os.path.isfile(_p):
            return _out
        with open(_p, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _d = json.loads(_line)
                except ValueError as _e:
                    silent_exc(_e, where="lesson_cooldown::_rows bad line", level="debug")
                    continue
                if isinstance(_d, dict):
                    _out.append(_d)
    except Exception as _e:
        silent_exc(_e, where="lesson_cooldown::_rows", level="debug")
    return _out


def record(*, file: str, method: str, error_type: str, reason: str = "",
           source: str = "", root: str | None = None,
           now: float | None = None) -> dict:
    """记一次同类失败；连续达阈值 ⇒ 写冷却。返回可留痕的判定 dict。"""
    _now = float(now if now is not None else time.time())
    _out: dict = {"ok": False, "streak": 0, "cooldown": False,
                  "cooldown_until": 0.0, "target": target_key(file, method),
                  "key": failure_key(file, method, error_type)}
    if not _on():
        _out["why"] = "ENABLE_M461_LESSON_COOLDOWN=False"
        return _out

    _note({"kind": "failure", "at": _now, "schema": SCHEMA,
           "target": _out["target"], "key": _out["key"],
           "file": _norm_file(file), "method": str(method or ""),
           "error_type": str(error_type or "other"),
           "reason": str(reason or "")[:200], "source": str(source or "")}, root=root)

    _streak = 0
    try:
        _fail = [r for r in _rows(root) if r.get("kind") == "failure"]
        _fail.sort(key=lambda r: float(r.get("at") or 0))
        for _r in reversed(_fail):
            if str(_r.get("key") or "") == _out["key"]:
                _streak += 1
            else:
                break
    except Exception as _e:
        silent_exc(_e, where="lesson_cooldown::record streak", level="debug")
        _streak = 1
    _out["streak"] = _streak
    _out["ok"] = True

    _need = int(_cfg("M461_COOLDOWN_STREAK_N", 2))
    if _streak >= _need:
        _until = _now + float(_cfg("M461_COOLDOWN_SEC", 86400.0))
        _out["cooldown"] = True
        _out["cooldown_until"] = _until
        _note({"kind": "cooldown", "at": _now, "schema": SCHEMA,
               "target": _out["target"], "key": _out["key"],
               "file": _norm_file(file), "method": str(method or ""),
               "error_type": str(error_type or "other"),
               "cooldown_until": _until, "streak": _streak}, root=root)
    return _out


def cooldown_until(file: Any, method: Any, *, root: str | None = None,
                   now: float | None = None) -> float:
    """返回该目标当前生效的冷却截止时刻；未冷却 ⇒ 0.0。"""
    _now = float(now if now is not None else time.time())
    _t = target_key(file, method)
    _until = 0.0
    try:
        for _r in _rows(root):
            if _r.get("kind") != "cooldown" or str(_r.get("target") or "") != _t:
                continue
            _u = float(_r.get("cooldown_until") or 0)
            if _u > _now and _u > _until:
                _until = _u
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="lesson_cooldown::cooldown_until", level="debug")
    return _until


def guard_patch(patch: dict, *, root: str | None = None,
                now: float | None = None) -> tuple[bool, str]:
    """生成/入库前的硬闸。返回 `(ok, why)`。"""
    if not _guard_on():
        return True, ""
    if not isinstance(patch, dict) or not patch:
        return True, ""
    _f = patch.get("file") or ""
    _m = patch.get("method") or ""
    if not _f or not _m:
        return True, ""
    _u = cooldown_until(_f, _m, root=root, now=now)
    if _u:
        return False, (f"同类错误冷却中（{_norm_file(_f)}::{_m}，至 {_u:.0f}）"
                       "—— 本轮不再生成/入库")
    return True, "冷却检查通过"


def filter_pending(data: Any, *, root: str | None = None,
                   now: float | None = None) -> tuple[Any, int, list[dict]]:
    """给唯一收口用：过滤掉冷却中的 pending 条目。

    Returns: `(filtered, blocked_count, blocked_rows)`；非 list 原样返回。
    ★只剔除、不改任何保留条目的字段（避免悄悄改写既有契约）。
    """
    if not _guard_on() or not isinstance(data, list):
        return data, 0, []
    _kept: list = []
    _blocked: list[dict] = []
    for _it in data:
        if isinstance(_it, dict):
            _ok, _why = guard_patch(_it, root=root, now=now)
            if not _ok:
                _blocked.append({"id": str(_it.get("id") or ""),
                                 "file": _it.get("file") or "",
                                 "method": _it.get("method") or "",
                                 "why": _why})
                continue
        _kept.append(_it)
    return _kept, len(_blocked), _blocked


def hint_for_prompt(file: Any, method: Any, *, root: str | None = None,
                    now: float | None = None) -> str:
    """软保证补充：冷却中的目标给生成侧一句明确提示（不替代硬闸）。"""
    _u = cooldown_until(file, method, root=root, now=now)
    if not _u:
        return ""
    _m = str(method or "")
    _f = _norm_file(file)
    return (f"注意：{_f}::{_m} "
            "近期同类修改连续失败，已进入冷却期；"
            "本轮不要为它生成补丁，先换目标或补充证据。")
