#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""O3 自动派单准入字段回填工具（主线第377批 刀1，P1）

背景
----
376 刀2 定位「破零 executed_ge_1 恒 0」的两层锁时发现：观察目标
``o3_b31b31fee790d6e9``（`dead_code_candidate` / ``nucleus/audit_problem_pool.py`` /
``restore_pool``）是 **365 前入池的存量条目**，只带 ``evidence_tier`` 等 28 个字段，
既没有 ``o3_auto_dispatch_ready``，``requires_confirmation`` 也仍是 ``True``
⇒ ``_m339_o3_dispatch_ready`` 永远返回 ``not_confirmed``，人工四项审核也批不动。

本工具把「准入所需字段」按**显式补丁 id 白名单**回填，不扫全池、不改破零主链路逻辑、
不动 ``status``（不手动 set_status，等 live 下一拍自动分诊）。

★关键事实（377 T0 实测，不要凭票面照抄）
  * ``_m339_o3_dispatch_ready`` 是**双条件**：``o3_auto_dispatch_ready is True``
    **且** ``requires_confirmation is False``。只补前者会卡在
    ``still_requires_confirmation`` —— 票面只提了一个字段，本工具补两个。
  * 票面点名的「参照条」``o3_59cd0eef8e125186`` 自己也没有 ``o3_auto_dispatch_ready``
    ⇒ 参照不了，按判据函数本身取真值（两个字段分别给 ``True`` / ``False``）。

用法::

    python tools/backfill_o3_dispatch_ready.py                        # dry-run（默认，绝不写盘）
    python tools/backfill_o3_dispatch_ready.py --apply --allow-prod    # 执行（自动备份）
    python tools/backfill_o3_dispatch_ready.py --patch-id <id> --apply --allow-prod
    python tools/backfill_o3_dispatch_ready.py --report <out.json>     # 另存 JSON 报告

★安全约束
  * **默认 dry-run**，不加 ``--apply`` 绝不写盘；
  * 写生产 ``data/`` 需 ``--apply`` **且** ``--allow-prod``（双重显式）——本工具是运维
    手术工具，与「测试环境一律不得写生产 data/」的写盘守卫同向，不提供隐式放行；
  * 写前整文件备份到 ``data/_archive/o3_dispatch_ready/<ts>/``（带 sha256 MANIFEST）；
  * 落盘保持原文件行尾（CRLF/LF 按字节判定）与 ``indent=2 / ensure_ascii=False`` 口径，
    未改动条目的字节不变（铁律138/213）；
  * 写后**重读盘**并用派单闸复算判定，逐条给 ``ok`` / 拒收原因（三态明确，不口头声称）。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.data.path_utils import safe_relpath as _safe_relpath

DEFAULT_TARGET = os.path.join("data", "patches", "pending_patches.json")
ARCHIVE_ROOT = os.path.join("data", "_archive")

#: ★351 刀3 起的破零观察位（`zero_break_judge.WATCH_IDS`），默认只动这一条
DEFAULT_PATCH_IDS: tuple[str, ...] = ("o3_b31b31fee790d6e9",)

#: 回填写入的字段（两个是判据需要的，三个是留痕）
FILL_READY = "o3_auto_dispatch_ready"
FILL_CONFIRM = "requires_confirmation"
FILL_MARK = "o3_auto_dispatch_ready_by"
FILL_AT = "o3_auto_dispatch_ready_at"
FILL_AT_STR = "o3_auto_dispatch_ready_at_str"
FILL_NOTE = "o3_auto_dispatch_ready_note"

#: 落盘序列化口径（377 T0 实测：与原文件 67764 字节逐字节一致）
_DUMP_KW = {"ensure_ascii": False, "indent": 2}


def _gate():
    """惰性取派单闸判据与 O3 识别常量（唯一真源，不在本工具里复制一份）。"""
    from nucleus.evolution.patch_execution_dispatch import (
        _M339_O3_ISSUE_TYPES,
        _M339_O3_SOURCES,
        _m339_o3_dispatch_ready,
    )
    return _m339_o3_dispatch_ready, _M339_O3_SOURCES, _M339_O3_ISSUE_TYPES


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as _f:
        return _f.read()


def _uses_crlf(raw: bytes) -> bool:
    """按字节判定行尾（铁律213）：有 ``\\r\\n`` 就按 CRLF 回写。"""
    return raw.count(b"\r\n") > 0


def _dumps_like(raw: bytes, doc: Any) -> bytes:
    """按原文件口径序列化，保持行尾不变。"""
    _s = json.dumps(doc, **_DUMP_KW)
    if _uses_crlf(raw):
        _s = _s.replace("\r\n", "\n").replace("\n", "\r\n")
    return _s.encode("utf-8")


def _load(path: str) -> tuple[Any, list, bytes]:
    """返回 ``(文档对象, 条目列表, 原始字节)``。"""
    if not os.path.isfile(path):
        raise SystemExit(f"[回填] 目标不存在: {path}")
    _raw = _read_bytes(path)
    _doc = json.loads(_raw.decode("utf-8"))
    if isinstance(_doc, list):
        return _doc, _doc, _raw
    if isinstance(_doc, dict):
        for _k in ("patches", "records", "items", "data"):
            if isinstance(_doc.get(_k), list):
                return _doc, _doc[_k], _raw
    raise SystemExit(f"[回填] 无法识别结构: {path}")


def _item_id(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("patch_id") or item.get("id") or "")


def _is_o3(item: Any) -> bool:
    """与派单闸同口径的 O3 识别（``_m339_o3_dispatch_ready`` 第一段）。"""
    if not isinstance(item, dict):
        return False
    _ok, _srcs, _types = _gate()
    return (item.get("o3_dead_code") is True
            or str(item.get("source") or "") in _srcs
            or str(item.get("issue_type") or "") in _types)


def _skip_reason(item: Any) -> str:
    """不满足回填前提时返回原因；可回填返回空串。"""
    if not isinstance(item, dict):
        return "not_dict"
    if not _is_o3(item):
        return "not_o3"
    if item.get("applied") is True:
        return "applied"
    if item.get("pending_cleared") is True:
        return "pending_cleared"
    if str(item.get("status") or "") != "pending":
        return "status_not_pending"
    return ""


def _already(item: dict) -> bool:
    return (item.get(FILL_READY) is True and item.get(FILL_CONFIRM) is False)


def _apply_fields(item: dict, now: float, note: str) -> dict:
    """就地回填，返回改动前的原值（供报告）。"""
    _before = {k: item.get(k) for k in (FILL_READY, FILL_CONFIRM)}
    item[FILL_READY] = True
    item[FILL_CONFIRM] = False
    item[FILL_MARK] = "backfill_377_d1"
    item[FILL_AT] = now
    item[FILL_AT_STR] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    item[FILL_NOTE] = note
    return _before


def _backup(path: str, archive_root: str) -> dict:
    """整文件归档备份（带 sha256 MANIFEST）。"""
    import shutil

    _ts = time.strftime("%Y%m%d_%H%M%S")
    _d = os.path.join(archive_root, "o3_dispatch_ready", _ts)
    os.makedirs(_d, exist_ok=True)
    _dst = os.path.join(_d, os.path.basename(path))
    shutil.copy2(path, _dst)
    _h = _sha256_bytes(_read_bytes(path))
    with open(os.path.join(_d, "MANIFEST.json"), "w", encoding="utf-8") as _f:
        _f.write(json.dumps({
            "source": _safe_relpath(path, ROOT),
            "backup": _safe_relpath(_dst, ROOT),
            "sha256": _h,
            "bytes": os.path.getsize(path),
            "created_at": time.time(),
            "created_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2))
    return {"dir": _safe_relpath(_d, ROOT),
            "backup": _safe_relpath(_dst, ROOT), "sha256": _h}


def _write_atomic(path: str, data: bytes) -> None:
    """同目录临时文件 + ``os.replace``，避免 live 进程读到半截 JSON。"""
    _tmp = path + ".tmp377"
    with open(_tmp, "wb") as _f:
        _f.write(data)
        _f.flush()
        os.fsync(_f.fileno())
    os.replace(_tmp, path)


def _prod_default(path: Any) -> bool:
    from nucleus.data.write_guard import is_production_data_path
    return is_production_data_path(path)


def build_report(target: str = DEFAULT_TARGET,
                 patch_ids: tuple[str, ...] = DEFAULT_PATCH_IDS,
                 apply: bool = False,
                 allow_prod: bool = False,
                 do_backup: bool = True,
                 archive_root: str = ARCHIVE_ROOT,
                 prod_check: Any = None,
                 note: str = "") -> dict:
    """勘查 / 执行回填。``apply=False`` 时**绝不**写盘。"""
    _gate_fn, _srcs, _types = _gate()
    _prod = prod_check or _prod_default
    _note = note or "377刀1 存量补字段（破零观察位；未走 approve_o3_patch 四项审核闸，人工拍板见交付报告）"

    _doc, _items, _raw = _load(target)
    _before_sha = _sha256_bytes(_raw)
    _idx = {_item_id(_it): _i for _i, _it in enumerate(_items) if _item_id(_it)}
    _snap_other = {_item_id(_it): _sha256_bytes(
        json.dumps(_it, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        for _it in _items if _item_id(_it)}

    _now = time.time()
    _rows: list[dict] = []
    _touched: list[dict] = []
    for _pid in patch_ids:
        _i = _idx.get(_pid)
        if _i is None:
            _rows.append({"patch_id": _pid, "state": "skipped",
                          "reason": "not_in_pool", "gate_before": None,
                          "gate_after": None})
            continue
        _it = _items[_i]
        _why = _skip_reason(_it)
        _gb = _gate_fn(_it)
        if _why:
            _rows.append({"patch_id": _pid, "state": "skipped", "reason": _why,
                          "gate_before": {"ok": _gb[0], "why": _gb[1]},
                          "gate_after": None})
            continue
        if _already(_it):
            _rows.append({"patch_id": _pid, "state": "already", "reason": "fields_present",
                          "gate_before": {"ok": _gb[0], "why": _gb[1]},
                          "gate_after": {"ok": _gb[0], "why": _gb[1]}})
            continue
        # 在**副本**上预演，dry-run 也能给出写后读数
        _prev = copy.deepcopy(_it)
        _old = _apply_fields(_prev, _now, _note)
        _ga = _gate_fn(_prev)
        _rows.append({"patch_id": _pid, "state": "filled",
                      "reason": "", "before_values": _old,
                      "gate_before": {"ok": _gb[0], "why": _gb[1]},
                      "gate_after": {"ok": _ga[0], "why": _ga[1]}})
        if apply:
            _apply_fields(_it, _now, _note)
            _touched.append({"patch_id": _pid, "index": _i})

    _rep: dict = {
        "schema": "o3_dispatch_ready_backfill/377.v1",
        "target": _safe_relpath(target, ROOT),
        "patch_ids": list(patch_ids),
        "dry_run": not apply,
        "applied": False,
        "rows": _rows,
        "changed_ids": [r["patch_id"] for r in _rows if r["state"] == "filled"],
        "already_ids": [r["patch_id"] for r in _rows if r["state"] == "already"],
        "skipped_ids": [r["patch_id"] for r in _rows if r["state"] == "skipped"],
        "sha256_before": _before_sha,
        "bytes_before": len(_raw),
        "crlf": _uses_crlf(_raw),
        "o3_sources": list(_srcs),
        "o3_issue_types": list(_types),
    }

    if not apply:
        _rep["note"] = "dry-run：未写盘（加 --apply --allow-prod 执行）"
        _rep["untouched_ok"] = True
        return _rep

    # ---- 写盘前双重显式授权 ----
    if _prod(target) and not allow_prod:
        _rep["error"] = "目标在生产 data/ 路径下，需 --allow-prod 显式授权（双重显式）"
        return _rep
    if not _rep["changed_ids"]:
        _rep["note"] = "无需回填：无可写条目（already/skipped），未写盘"
        _rep["untouched_ok"] = True
        return _rep

    _bk = _backup(target, archive_root) if do_backup else None
    _new_raw = _dumps_like(_raw, _doc)
    _write_atomic(target, _new_raw)

    # ---- 写后重读盘复核 ----
    _doc2, _items2, _raw2 = _load(target)
    _idx2 = {_item_id(_it): _it for _it in _items2 if _item_id(_it)}
    for _r in _rows:
        if _r["state"] != "filled":
            continue
        _it2 = _idx2.get(_r["patch_id"])
        _g2 = _gate_fn(_it2) if _it2 is not None else (False, "not_in_pool_after_write")
        _r["gate_persisted"] = {"ok": _g2[0], "why": _g2[1]}
    _snap2 = {_item_id(_it): _sha256_bytes(
        json.dumps(_it, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        for _it in _items2 if _item_id(_it)}
    _changed_set = set(_rep["changed_ids"])
    _leak = sorted(_k for _k in _snap_other
                   if _snap_other[_k] != _snap2.get(_k) and _k not in _changed_set)

    _rep.update({
        "applied": True,
        "backup": _bk,
        "sha256_after": _sha256_bytes(_raw2),
        "bytes_after": len(_raw2),
        "untouched_ok": not _leak,
        "untouched_leaked": _leak,
        "reload_count": len(_items2),
    })
    return _rep


def _print_report(rep: dict, ns: Any) -> None:
    print("=" * 74)
    print("O3 自动派单准入字段回填" + ("（DRY-RUN）" if rep.get("dry_run") else "（已执行）"))
    print("=" * 74)
    print("目标: %s" % rep["target"])
    print("行尾: %s   字节 %d" % ("CRLF" if rep.get("crlf") else "LF",
                                  rep.get("bytes_before") or 0))
    print("O3 识别: sources=%s issue_types=%s"
          % (rep.get("o3_sources"), rep.get("o3_issue_types")))
    print()
    for _r in rep["rows"]:
        _gb = _r.get("gate_before") or {}
        _ga = _r.get("gate_after") or {}
        print("[%s] %-24s state=%-8s %s"
              % ("√" if _r["state"] == "filled" else "·",
                 _r["patch_id"], _r["state"], _r.get("reason") or ""))
        print("     闸前: ok=%-5s why=%-28s" % (_gb.get("ok"), _gb.get("why")))
        if _ga:
            print("     闸后: ok=%-5s why=%-28s" % (_ga.get("ok"), _ga.get("why")))
        if _r.get("gate_persisted"):
            _gp = _r["gate_persisted"]
            print("     落盘复核: ok=%-5s why=%s" % (_gp.get("ok"), _gp.get("why")))
        if _r.get("before_values"):
            print("     原值: %s" % (_r["before_values"],))
    print()
    if rep.get("error"):
        print("[!] " + rep["error"])
        return
    if rep.get("dry_run"):
        print("[6] dry-run：未写盘。加 --apply --allow-prod 执行。")
        return
    if not rep.get("applied"):
        print("[6] " + str(rep.get("note") or "未写盘"))
        return
    print("[6] 已写入；未改条目数=%d 泄漏=%s"
          % (0 if rep.get("untouched_ok") else len(rep.get("untouched_leaked") or []),
             rep.get("untouched_leaked") or "无"))
    print("    备份: %s" % ((rep.get("backup") or {}).get("dir") or "(未备份)"))
    print("    sha256 %s → %s"
          % (str(rep.get("sha256_before"))[:16], str(rep.get("sha256_after"))[:16]))
    print("    字节 %s → %s" % (rep.get("bytes_before"), rep.get("bytes_after")))


def main() -> int:
    _ap = argparse.ArgumentParser(description="O3 自动派单准入字段回填（377批刀1）")
    _ap.add_argument("--target", default=DEFAULT_TARGET)
    _ap.add_argument("--patch-id", action="append", default=None,
                     help="可重复；缺省用 DEFAULT_PATCH_IDS（破零观察位）")
    _ap.add_argument("--apply", action="store_true", help="执行写入（默认 dry-run）")
    _ap.add_argument("--allow-prod", action="store_true",
                     help="写生产 data/ 的显式授权（--apply 时必需）")
    _ap.add_argument("--no-backup", action="store_true")
    _ap.add_argument("--report", default="", help="JSON 报告输出路径")
    _ns = _ap.parse_args()

    _ids = tuple(_ns.patch_id) if _ns.patch_id else DEFAULT_PATCH_IDS
    _r = build_report(_ns.target, _ids, apply=_ns.apply,
                      allow_prod=_ns.allow_prod,
                      do_backup=not _ns.no_backup)
    _print_report(_r, _ns)

    if _ns.report:
        _d = os.path.dirname(os.path.abspath(_ns.report))
        if _d and not os.path.isdir(_d):
            os.makedirs(_d, exist_ok=True)
        with open(_ns.report, "w", encoding="utf-8") as _f:
            _f.write(json.dumps(_r, ensure_ascii=False, indent=2))
        print()
        print("报告已写出 → %s" % _ns.report)
    if _r.get("error"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# _m377_d1_backfill
