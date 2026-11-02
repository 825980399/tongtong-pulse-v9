# -*- coding: utf-8 -*-
"""426 刀2：`effect_verified` 历史 None 回填（有证据才填，没证据标待验证）。

背景
----
`data/patches/patch_history.json` 里 `applied=True` 但 `effect_verified` 缺失/None
的补丁共 **36** 条（426 T0 实测）。这些补丁**不是没验过**，而是验证结果记录在
别的字段里没回写：

  · `reprobe_verdict == "true_pass"`  → 29 条，`reprobe_baseline_hits=1`、
    `reprobe_after_hits=0`，`detail` 写明「复现成功：修复前 1 处 → 修复后 0 处」
  · `reprobe_verdict == "partial_fix"` → 7 条，减少但未清零（如 6→4、20→7）

回填判据（★不瞎填）
------------------
1. 只有 **同时满足** `reprobe_verdict=true_pass` + `reprobe_baseline_hits>=1`
   + `reprobe_after_hits==0` 才填 `True`。
   `baseline_hits>=1` 是**判别力门槛**（铁律 35）：基线本来 0 处命中时
   "修复后也是 0" 不能证明任何事。
2. `partial_fix` **不填 True 也不填 False**：它确实改对了一部分，填 False 是
   另一种失真。按任务书「没证据的标成待验证」，保留 `effect_verified=None`
   并写 `effect_verify_pending_reason`。
3. 其它任何形态（没有 reprobe 字段/判据不全）同样按待验证处理。

用法
----
    python tools/m426_backfill_effect_verified.py            # 干跑（默认）
    python tools/m426_backfill_effect_verified.py --write    # 落盘
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc

HISTORY_REL = os.path.join("data", "patches", "patch_history.json")
VERIFY_REL = os.path.join("data", "evolution", "effect_verify.json")
BATCH = "426"


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for _c in iter(lambda: f.read(1 << 20), b""):
            h.update(_c)
    return h.hexdigest()


def _read_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _eol(path: str) -> str:
    raw = Path(path).read_bytes()
    _crlf = raw.count(b"\r\n")
    _bare = raw.count(b"\n") - _crlf
    return "\r\n" if (_crlf and not _bare) else "\n"


def _write_json(path: str, obj, indent: int = 2) -> None:
    """原子写 + 按原行尾回写（铁律 1）。"""
    _tmp = path + ".tmp426"
    _txt = json.dumps(obj, ensure_ascii=False, indent=indent)
    _txt = _txt.replace("\n", _eol(path))
    with open(_tmp, "wb") as f:
        f.write(_txt.encode("utf-8"))
    os.replace(_tmp, path)


def classify(patch: dict) -> tuple[str, str]:
    """返回 (kind, 证据短句)。kind ∈ {fill_true, pending}。"""
    _rv = str(patch.get("reprobe_verdict") or "")
    _b = patch.get("reprobe_baseline_hits")
    _a = patch.get("reprobe_after_hits")
    try:
        _bi = int(_b)
        _ai = int(_a)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="m426_backfill_effect_verified.classify")
        _bi = _ai = -1
    if _rv == "true_pass" and _bi >= 1 and _ai == 0:
        return "fill_true", f"reprobe: 复现成功 {_bi} 处 → 修复后 0 处（true_pass）"
    if _rv == "partial_fix":
        return "pending", f"reprobe: {_b} → {_a} 处，减少但未清零（partial_fix）"
    return "pending", f"无可用复现证据（reprobe_verdict={_rv!r}, {_b}→{_a}）"


def build(history: list) -> dict:
    _rows = [p for p in history if isinstance(p, dict)]
    _targets = [p for p in _rows
                if p.get("applied") is True and p.get("effect_verified") is None
                and not p.get("effect_verified_batch")]
    _filled, _pending, _lines = [], [], []
    _now = time.strftime("%Y-%m-%d %H:%M:%S")
    for _p in _targets:
        _kind, _why = classify(_p)
        _pid = str(_p.get("id") or "")
        if _kind == "fill_true":
            _p["effect_verified"] = True
            _filled.append(_pid)
        else:
            _pending.append(_pid)
            _p["effect_verify_pending_reason"] = _why
        _p["effect_verified_batch"] = BATCH
        _p["effect_verified_source"] = "reprobe_verdict"
        _p["effect_verified_at"] = _now
        _p["effect_verified_evidence"] = _why
        _lines.append((_pid, _kind, _why, str(_p.get("file") or "")))
    _remain = [str(p.get("id")) for p in _rows
               if p.get("applied") is True and p.get("effect_verified") is None]
    return {"targets": len(_targets), "filled": _filled, "pending": _pending,
            "remain_none": _remain, "lines": _lines, "at": _now}


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--write", action="store_true", help="真正落盘（默认干跑）")
    _ap.add_argument("--root", default=_ROOT)
    _args = _ap.parse_args(argv)

    _hp = os.path.join(_args.root, HISTORY_REL)
    _vp = os.path.join(_args.root, VERIFY_REL)
    if not os.path.exists(_hp):
        print(f"找不到 {_hp}")
        return 2

    _h = _read_json(_hp)
    if not isinstance(_h, list):
        print(f"patch_history 不是 list：{type(_h).__name__}")
        return 2

    _before_sha = _sha(_hp)
    _r = build(_h)
    print(f"目标（applied=True 且 effect_verified=None）：{_r['targets']}")
    print(f"  回填 True : {len(_r['filled'])}")
    print(f"  标待验证  : {len(_r['pending'])}")
    print(f"  仍为 None : {len(_r['remain_none'])}")
    for _pid, _kind, _why, _f in _r["lines"]:
        print(f"   {_kind:<9} {_pid:<26} {_why[:46]:<46} {_f[:52]}")

    if not _args.write:
        print("\n[干跑] 未写盘。加 --write 落盘。")
        return 0

    if _r["targets"] == 0:
        print("\n无可回填项（幂等），不写盘。")
        return 0

    # 落盘前再确认：期间生产进程若写过，重读重算（最多 3 次）
    for _try in range(3):
        if _sha(_hp) != _before_sha:
            print(f"[重试{_try + 1}] patch_history 期间被写过，重读重算")
            _h = _read_json(_hp)
            _before_sha = _sha(_hp)
            _r = build(_h)
            if _r["targets"] == 0:
                print("重读后已无可回填项。")
                return 0
        _write_json(_hp, _h)
        break

    # 写后校验：重新读盘，确认每一个填充目标真的变成 True
    _chk = _read_json(_hp)
    _by_id = {str(p.get("id")): p for p in _chk if isinstance(p, dict)}
    _bad = [i for i in _r["filled"] if (_by_id.get(i) or {}).get("effect_verified") is not True]
    if _bad:
        print(f"写后校验失败：{len(_bad)} 条未变成 True（前 3：{_bad[:3]}）")
        return 3

    # effect_verify.json 追加一份批次台账（不动 issues/verdicts 结构）
    try:
        if os.path.exists(_vp):
            _v = _read_json(_vp)
            if isinstance(_v, dict):
                _v["backfill_" + BATCH] = {
                    "at": _r["at"],
                    "filled_true": len(_r["filled"]),
                    "pending": len(_r["pending"]),
                    "remaining_none": len(_r["remain_none"]),
                    "source": "reprobe_verdict(true_pass & baseline>=1 & after==0)",
                    "pending_ids": _r["pending"],
                }
                _write_json(_vp, _v)
                print(f"台账已写：{VERIFY_REL} -> backfill_{BATCH}")
    except Exception as _e:
        print(f"台账写入失败（不影响主回填）：{type(_e).__name__}: {_e}")

    print(f"\n落盘完成：sha {_before_sha[:12]} -> {_sha(_hp)[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
