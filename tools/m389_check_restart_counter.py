#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""389刀4：重启计数桶 **只读核实 / 幂等重置** 工具。

T0 核实（2026-10-29 18:xx）
--------------------------
票面写「计数器已经记了 1 次（今天下午自动重启过一次）」，实测：

    data/patches/restart_count.txt        = "0"     （mtime 2026-10-29 14:08）
    data/patches/restart_blocked_at.txt   = 旧值    （mtime 2026-10-03 14:53）

⇒ **票面前提不成立**：计数当前已是 0，无需重置（本工具默认 dry-run，不写盘）。

这个桶是什么（别改错东西）
--------------------------
`restart_count.txt` 不是"内存自动重启计数器"，而是
**补丁自动应用 + 内存自动重启 共用的棘轮计数桶**（176批段2 明写"共用"）：

* `PatchManager._max_restart_count = 3`（实例属性）
* `counter > 3` ⇒ `memory_restart_bucket_state().locked = True`，
  锁 `MEMORY_RESTART_COOLDOWN_HOURS`(=6.0, 387刀1) 小时；
  冷却期满后**放行一次并重置棘轮**（`apply_all_pending` 里那条分支）。
* 内存重启走 `bump_restart_counter_for_memory_restart()` —— 与补丁重启**同一个计数**。

★由此有个**设计风险**（本刀不擅自改，留裁决）：
  4 次内存重启 ⇒ 补丁自动应用被锁 6h；反之补丁重启也会消耗内存重启的额度。
  如果要分开，是"两个桶"的设计变更，得单独成批（会动 `apply_all_pending` 与
  `memory_restart_bucket_state` 两处判定）。

用法::

    python tools/m389_check_restart_counter.py                  # dry-run（默认，只读）
    python tools/m389_check_restart_counter.py --json           # 机器可读
    python tools/m389_check_restart_counter.py --reset --allow-prod   # 写 0（双重显式）

★为什么重置还要 `--allow-prod`：`data/` 是生产目录，写盘守卫默认拒非框架进程
  （铁律198）。本工具不绕过守卫 —— 它显式声明 `explicit=True` **且**要求
  `--allow-prod`，两层都过才写；写前打印将改动的内容。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

#: 与 `PatchManager._max_restart_count` 同值（默认 3；本工具**不 import** 那个重模块）
DEFAULT_MAX_RESTART_COUNT = 3
DEFAULT_COOLDOWN_HOURS = 6.0


def counter_path(patch_dir: str | None = None) -> str:
    return os.path.join(patch_dir or os.path.join(_ROOT, "data", "patches"),
                        "restart_count.txt")


def blocked_at_path(patch_dir: str | None = None) -> str:
    return os.path.join(patch_dir or os.path.join(_ROOT, "data", "patches"),
                        "restart_blocked_at.txt")


def read_counter(path: str) -> tuple:
    """`(值, 状态)`；状态三态 `ok` / `absent` / `unreadable`。

    ★不存在与读不动**分开报**：前者是"从没记过"（按 0 处理是设计内），
      后者是"有文件但读不了"（按 0 处理等于**放宽**棘轮，必须可见）。
    """
    if not os.path.isfile(path):
        return 0, "absent"
    try:
        with open(path, encoding="utf-8") as _f:
            return int(_f.read().strip() or 0), "ok"
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="m389.read_counter", level="warning")
        return 0, "unreadable"


def read_blocked_at(path: str) -> float:
    try:
        with open(path, encoding="utf-8") as _f:
            return float(_f.read().strip() or 0)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="m389.read_blocked_at", level="warning")
        return 0.0


def bucket_state(counter: int, blocked_at: float, cooldown_h: float,
                 max_n: int = DEFAULT_MAX_RESTART_COUNT,
                 now: float | None = None) -> dict:
    """复刻 `PatchManager.memory_restart_bucket_state()` 的判定（**只读口径**）。

    判定只有一条：`counter > max_n` 才可能锁；锁定时再看冷却是否期满。
    `blocked_at <= 0` 且已超限 ⇒ 视为"刚锁死"，剩余 = 整个冷却窗口。
    """
    _now = time.time() if now is None else float(now)
    _cooldown_sec = float(cooldown_h) * 3600.0
    _elapsed = (_now - blocked_at) if blocked_at > 0 else float("inf")
    _locked = False
    # ★389刀4：未锁定时 `remain_sec = 0`。原实现初值是"整个冷却窗口"，
    #   于是"未锁 + 剩余6小时"同时出现，读起来像"再等 6 小时才解锁" —— 误导。
    _remain = 0
    if int(counter) > int(max_n):
        if blocked_at > 0 and _elapsed >= _cooldown_sec:
            _locked = False
            _remain = 0
        else:
            _locked = True
            _remain = (int(_cooldown_sec - _elapsed) if blocked_at > 0
                       else int(_cooldown_sec))
    return {"counter": int(counter), "max": int(max_n),
            "cooldown_h": float(cooldown_h), "cooldown_sec": int(_cooldown_sec),
            "locked": bool(_locked), "remain_sec": max(0, _remain),
            "elapsed_sec": (None if _elapsed == float("inf") else int(_elapsed))}


def reset_counter(path: str, *, allow_prod: bool = False,
                  explicit: bool = True) -> dict:
    """把计数写回 0（**幂等**）。返回 `{ok, changed, before, after, reason}`。

    三层保护：① 必须 `allow_prod`；② 必须过写盘守卫（`explicit=True` 才放行
    非框架进程）；③ 写后回读核对。**不动** `restart_blocked_at.txt` ——
    那是冷却起点，清掉等于把已锁死的棘轮提前放行（比"不重置"更危险）。
    """
    _out = {"path": path, "ok": False, "changed": False,
            "before": None, "after": None, "reason": ""}
    _before, _st = read_counter(path)
    _out["before"] = _before
    if _st == "unreadable":
        _out["reason"] = "unreadable_before_reset"
        return _out
    if not allow_prod:
        _out["reason"] = "need_allow_prod"
        return _out
    if _before == 0:
        _out.update({"ok": True, "changed": False, "after": 0,
                     "reason": "already_zero"})
        return _out
    try:
        from nucleus.data.write_guard import guard_write as _gw
        if not _gw(path, explicit=explicit, component="m389.reset_restart_counter"):
            _out["reason"] = "denied_by_write_guard"
            return _out
    except ImportError as _ie:
        # 守卫不可用 ⇒ 本工具仍要能跑（它是诊断工具，不是写盘闸门）
        silent_exc(_ie, where="m389.reset_restart_counter guard", level="debug")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as _f:
            _f.write("0")
    except OSError as _e:
        silent_exc(_e, where="m389.reset_counter write", level="warning")
        _out["reason"] = "write_failed:%s" % type(_e).__name__
        return _out
    _after, _st2 = read_counter(path)
    _out["after"] = _after
    _out["ok"] = (_st2 == "ok" and _after == 0)
    _out["changed"] = (_out["ok"] and _before != 0)
    _out["reason"] = "reset" if _out["ok"] else "verify_failed"
    return _out


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="389刀4 重启计数桶核实/重置")
    _ap.add_argument("--patch-dir", default="", help="默认 <repo>/data/patches")
    _ap.add_argument("--json", action="store_true", help="机器可读输出")
    _ap.add_argument("--reset", action="store_true", help="写回 0（需 --allow-prod）")
    _ap.add_argument("--allow-prod", action="store_true")
    _a = _ap.parse_args(argv)

    _dir = _a.patch_dir or None
    _cp = counter_path(_dir)
    _bp = blocked_at_path(_dir)
    _counter, _cstate = read_counter(_cp)
    _blocked = read_blocked_at(_bp)
    _cooldown = DEFAULT_COOLDOWN_HOURS
    try:
        import config as _cfg
        _cooldown = float(getattr(_cfg, "MEMORY_RESTART_COOLDOWN_HOURS",
                                  DEFAULT_COOLDOWN_HOURS))
    except Exception as _ce:
        silent_exc(_ce, where="m389.main cooldown read", level="debug")
    _st = bucket_state(_counter, _blocked, _cooldown)

    _view = {"counter_file": _cp, "counter": _counter, "counter_state": _cstate,
             "blocked_at_file": _bp, "blocked_at": _blocked,
             "cooldown_h": _cooldown, "bucket": _st}
    if _a.json:
        print(json.dumps(_view, ensure_ascii=False, indent=2))
    else:
        print("=" * 68)
        print("389刀4 重启计数桶（内存重启与补丁重启**共用**）")
        print("=" * 68)
        print("  计数文件 : %s" % _cp)
        print("  计数     : %s（状态=%s）" % (_counter, _cstate))
        print("  锁死起点 : %s（%s）" % (
            _blocked,
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_blocked))
            if _blocked > 0 else "无"))
        print("  上限/冷却: %d 次 / %.1fh" % (_st["max"], _st["cooldown_h"]))
        print("  当前状态 : %s%s" % (
            "锁定" if _st["locked"] else "未锁",
            "（剩余 %d 秒）" % _st["remain_sec"] if _st["locked"] else ""))
        if _cstate == "unreadable":
            print("  ★计数文件存在但读不动 —— 按 0 处理等于放宽棘轮，先修文件")
        if _counter > _st["max"]:
            print("  ★已超上限：内存重启会按冷却窗口被判锁定，属设计内")

    if not _a.reset:
        return 0
    _r = reset_counter(_cp, allow_prod=bool(_a.allow_prod))
    print("\n[reset] %s" % json.dumps(_r, ensure_ascii=False))
    if not _r["ok"]:
        if _r["reason"] == "need_allow_prod":
            print("  ⇒ 目标在生产 data/ 下：确认后加 --allow-prod 重跑")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
