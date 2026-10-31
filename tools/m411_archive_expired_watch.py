# -*- coding: utf-8 -*-
"""411 刀5 工具：过期观察位归档（**搬出 pending，不是原地改状态**）。

为什么不能照任务书写"把 status 改成 archived"
-------------------------------------------
T0 实测两个判据都只看 `status == "expired_pending"`：

  * `zero_break_judge._m407_watch_alive()` —— 判"这个观察位还活着吗"；
  * `patch_execution_dispatch._m339_o3_dispatch_ready()` —— 判"能不能自动派单"。

若把 3 条 `expired_pending` **原地改成 `archived`**：

  * 前者不再视为死状态 ⇒ `n_eligible` 从 0 变成 3（**读数变好看**）；
  * 后者也不再拦住 ⇒ 这 3 条 `dead_code_candidate` **会被真的派单进沙箱**。

那就把"数量虚高、可派单为 0"换成了"数量好看、真派单死代码" —— 比原问题更糟。
所以本工具按 331刀4 已有的归档范式做事：
**追加到归档文件 + 从 pending 移除 + 原子替换**，并在条目里留
`status_history`（原状态、归档时刻、归档人、原因）。

用法::

    python tools/m411_archive_expired_watch.py            # dry-run（默认）
    python tools/m411_archive_expired_watch.py --apply    # 真正归档（先自动备份）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402  （必须在 sys.path 注入之后）

PENDING_REL = os.path.join("data", "patches", "pending_patches.json")
ARCHIVE_REL = os.path.join("data", "patches", "pending_patches_archive.json")
TARGET_STATUS = "expired_pending"
ARCHIVED_STATUS = "archived"


def _load(path):
    return json.loads(io.open(path, encoding="utf-8").read())


def _archive_doc(path):
    """归档文件形态：`{"version", "items":[...]}`；老文件是 list 也认。"""
    if not os.path.exists(path):
        return {"version": "m411", "items": []}
    try:
        _d = _load(path)
    except Exception as e:
        silent_exc(e, where="m411_archive_expired_watch._archive_doc", level="warning")
        print("[WARN] 归档文件读不了（%s），本轮不写归档以免覆盖：%s" % (type(e).__name__, e))
        return None
    if isinstance(_d, list):
        return {"version": "m411", "items": _d}
    if isinstance(_d, dict) and isinstance(_d.get("items"), list):
        return _d
    print("[WARN] 归档文件结构不认识（%s），本轮不写" % type(_d).__name__)
    return None


def _mark(row, now, reason):
    """给归档条目补 status_history（原条目没有这个字段 —— T0 实测 0/3 有）。"""
    _hist = row.get("status_history")
    if not isinstance(_hist, list):
        _hist = []
    _hist.append({
        "status": str(row.get("status") or ""),
        "at": round(now, 3),
        "at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "by": "411刀5_archive_expired_watch",
        "reason": reason,
    })
    row["status_history"] = _hist
    row["status_before_archive"] = str(row.get("status") or "")
    row["status"] = ARCHIVED_STATUS
    row["archived_at"] = round(now, 3)
    row["archived_by"] = "411刀5_archive_expired_watch"
    row["archive_reason"] = reason
    return row


def _state_probe(rows):
    """调真判据看状态（自愈五态）；导入失败就返回 None，不编造。

    ★必须**快照/恢复** `zero_break_judge._WATCH_RUNTIME`：
      该模块用模块级 list 记"自愈补进来的位"，`self_heal_watch()` 会**原地追加**。
      不恢复就会出现"第一次 probe 补了位、第二次 probe 看到补过的位 ⇒ 判 healthy"
      的假象（本工具首版实测到这一点：归档前 healed / 归档后 healthy，
      而其实只是 `_WATCH_RUNTIME` 被上一次调用改了）。
    """
    try:
        from nucleus.evolution import zero_break_judge as zbj
        _hist = []
        _hp = os.path.join("data", "patches", "patch_history.json")
        if os.path.exists(_hp):
            try:
                _hist = _load(_hp)
            except Exception as _e_hist:
                silent_exc(_e_hist, where="m411_archive_expired_watch._state_probe:history",
                           level="debug")
                _hist = []
        _rt = getattr(zbj, "_WATCH_RUNTIME", None)
        _saved = list(_rt) if isinstance(_rt, list) else None
        try:
            _r = zbj.self_heal_watch(pending_rows=rows, history_rows=_hist)
        finally:
            if _saved is not None and isinstance(_rt, list):
                _rt[:] = _saved
        return {"state": _r.get("state"), "n_active": _r.get("n_active"),
                "n_eligible": _r.get("n_eligible"), "why": _r.get("why"),
                "skipped": list(_r.get("skipped") or []),
                "dead_active": list(_r.get("dead_active") or [])}
    except Exception as e:
        silent_exc(e, where="m411_archive_expired_watch._state_probe", level="warning")
        print("[WARN] 自愈判据调用失败（不影响归档）：%s: %s" % (type(e).__name__, e))
        return None


def main() -> int:
    _ap = argparse.ArgumentParser(description="411 刀5 过期观察位归档")
    _ap.add_argument("--apply", action="store_true", help="真正归档（默认 dry-run）")
    _ap.add_argument("--keep-file", action="store_true",
                     help="连归档文件都不写（只打印）")
    _args = _ap.parse_args()
    if not os.path.exists(PENDING_REL):
        print("[FAIL] 找不到 %s" % PENDING_REL)
        return 1
    _rows = _load(PENDING_REL)
    if not isinstance(_rows, list):
        print("[FAIL] pending 不是 list（%s），本工具只处理 list 形态" % type(_rows).__name__)
        return 1

    _targets = [r for r in _rows if isinstance(r, dict)
                and str(r.get("status")) == TARGET_STATUS]
    print("=" * 78)
    print("[现状] %s  共 %d 条；%s = %d 条" % (
        PENDING_REL, len(_rows), TARGET_STATUS, len(_targets)))
    _before = _state_probe(_rows)
    print("[归档前] %s" % json.dumps(_before, ensure_ascii=False))

    if not _targets:
        print("\n[结论] 没有 %s 条目，无需归档（不改任何文件）" % TARGET_STATUS)
        return 0

    _now = time.time()
    _archived = []
    for _r in _targets:
        _row = json.loads(json.dumps(_r))  # 深拷贝，先不改内存里的原表
        _age_days = None
        try:
            _age_days = round((_now - float(_r.get("created_at") or 0)) / 86400.0, 2)
        except (TypeError, ValueError) as _e_age:
            silent_exc(_e_age, where="m411_archive_expired_watch.main:age_days", level="debug")
            _age_days = None
        _reason = "expired_pending 归档（超龄 %s 天，O3 死代码候选，从未派单）" % (
            _age_days if _age_days is not None else "?")
        _archived.append(_mark(_row, _now, _reason))
        print("  · %-26s file=%s status→%s" % (
            str(_r.get("id"))[:26], str(_r.get("file"))[:44], ARCHIVED_STATUS))
        print("    status_history 条数=%d（归档前 %d）" % (
            len(_archived[-1]["status_history"]),
            len(_r.get("status_history") or []) if isinstance(_r.get("status_history"), list) else 0))

    _left = [r for r in _rows if not (isinstance(r, dict)
                                      and str(r.get("status")) == TARGET_STATUS)]
    _after = _state_probe(_left)
    print("[归档后(模拟)] %s" % json.dumps(_after, ensure_ascii=False))
    if _before and _after:
        print("[读数变化] state: %s → %s ｜ n_eligible: %s → %s" % (
            _before.get("state"), _after.get("state"),
            _before.get("n_eligible"), _after.get("n_eligible")))

    if not _args.apply:
        print("\n★dry-run：未写任何文件。要归档请加 --apply")
        return 0

    # ---- 真正写 ----
    _bak = PENDING_REL + ".bak_m411_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(PENDING_REL, _bak)
    print("\n[备份] %s" % _bak)

    if not _args.keep_file:
        _doc = _archive_doc(ARCHIVE_REL)
        if _doc is None:
            print("[FAIL] 归档文件结构不可判定，已中止（pending 未改）")
            return 2
        _doc["items"].extend(_archived)
        _doc["version"] = _doc.get("version") or "m411"
        _doc["last_archived_at"] = round(_now, 3)
        _tmp_a = ARCHIVE_REL + ".tmp411"
        with io.open(_tmp_a, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_doc, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp_a, ARCHIVE_REL)
        print("[归档] %s  现共 %d 条（本次 +%d）" % (
            ARCHIVE_REL, len(_doc["items"]), len(_archived)))

    _tmp = PENDING_REL + ".tmp411"
    with io.open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
        json.dump(_left, _f, ensure_ascii=False, indent=2)
    os.replace(_tmp, PENDING_REL)
    print("[pending] 已重写：%d → %d 条" % (len(_rows), len(_left)))

    # 写后复核（读回文件，不信内存）
    _chk = _load(PENDING_REL)
    _still = [r for r in _chk if isinstance(r, dict) and str(r.get("status")) == TARGET_STATUS]
    _arch_chk = _archive_doc(ARCHIVE_REL)
    _n_arch = len(_arch_chk["items"]) if _arch_chk else -1
    _ok1 = len(_still) == 0
    # ★`--keep-file` 时归档文件本来就没动 ⇒ 归档两项**不适用**（不适用 ≠ 失败）
    _kept = bool(_args.keep_file)
    _ok2 = True if _kept else (_n_arch >= len(_archived))
    _items_new = (_arch_chk or {}).get("items", [])[-len(_archived):]
    _ok3 = True if _kept else all(
        isinstance(x, dict) and x.get("status_history")
        and x.get("status") == ARCHIVED_STATUS for x in _items_new)
    _ok4 = len(_chk) == len(_rows) - len(_targets)
    print("\n[复核] pending 无残留=%s / 归档已增=%s(%s) / status_history 保留=%s / 条数对得上=%s" % (
        _ok1, _ok2, ("skip:--keep-file" if _kept else _n_arch), _ok3, _ok4))
    print("[结论] %s" % ("PASS" if all((_ok1, _ok2, _ok3, _ok4)) else "FAIL"))
    return 0 if all((_ok1, _ok2, _ok3, _ok4)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
