# -*- coding: utf-8 -*-
"""169批 C6（T-快照 retention 核查）：**只读诊断**，不删任何文件。

结论（T0 实测 2026-10-07）
--------------------------
* ``data/knowledge`` 顶层受管备份 **3 件**（= 上限 KNOWLEDGE_BACKUP_KEEP=3），未超；
* ``evidence_chain_freeze_20260923/`` 子目录另有 **2 件**（463.1 + 463.3 MB）；
* 递归合计 **5 件 / 2497.6 MB**。

「5 > 3」成因
-------------
``PulseSnapshot._cleanup_old_backups`` 只做 ``os.listdir(snapshot_dir)``
（**仅顶层、不递归**），判据 ``fname.startswith(base_name) and ".bak" in fname``
⇒ 子目录内的副本**从不进入匹配集**，自然不受保留上限约束。

本刀产出
--------
* ``max_backups`` / 受管实际份数 / 命名匹配集；
* 未受管（子目录）副本清单与体积；
* 是否超额 + 超额归因。

★**不做任何删除**（删除动作归停窗段步骤 5，需停机态执行）。

用法::

    python tools/snapshot_retention_audit.py
    python tools/snapshot_retention_audit.py --dir data/knowledge --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_DIR = os.path.join("data", "knowledge")
DEFAULT_BASE_NAME = "pulse_knowledge_snapshot.json"
DEFAULT_KEEP = 3

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
# ★第169批 C2：统一归一入口（内委托 safe_relpath，跨盘降级绝对路径 + 分隔符归一）
from nucleus.data.path_utils import normalize_relpath  # noqa: E402


def _keep_from_config() -> int:
    """保留份数：config.KNOWLEDGE_BACKUP_KEEP（默认 3）。"""
    try:
        sys.path.insert(0, _PROJECT_ROOT)
        import config as _cfg
        return int(getattr(_cfg, "KNOWLEDGE_BACKUP_KEEP", DEFAULT_KEEP)
                   or DEFAULT_KEEP)
    except Exception as _e:
        sys.stderr.write("[snapshot_retention_audit] 上限读取失败，按默认 %d: %s: %s\n"
                         % (DEFAULT_KEEP, type(_e).__name__, _e))
        return DEFAULT_KEEP


def _matches_managed(fname: str, base_name: str) -> bool:
    """★与 ``_cleanup_old_backups`` **逐字同源**的匹配判据。

    ``fname.startswith(base_name) and ".bak" in fname``
    —— 审计必须与受管逻辑同一口径，否则「命名匹配集」不可信。
    """
    return fname.startswith(base_name) and ".bak" in fname


def scan_retention(directory: str | None = None,
                   base_name: str = DEFAULT_BASE_NAME,
                   keep: int | None = None,
                   root: str | None = None) -> dict:
    """只读扫描快照备份保留现状。

    Returns:
        ``{"dir", "base_name", "max_backups", "managed_count",
           "managed": [{"name","size","mtime"}], "unmanaged_count",
           "unmanaged": [{"path","size","mtime"}], "total_bytes",
           "over_limit", "over_by", "cause"}``
    """
    _root = root or _PROJECT_ROOT
    _dir = directory or DEFAULT_DIR
    if not os.path.isabs(_dir):
        _dir = os.path.join(_root, _dir)
    _keep = _keep_from_config() if keep is None else int(keep)

    _managed: list[dict] = []
    _unmanaged: list[dict] = []

    if os.path.isdir(_dir):
        for _fn in sorted(os.listdir(_dir)):
            _p = os.path.join(_dir, _fn)
            if not os.path.isfile(_p):
                continue
            if _matches_managed(_fn, base_name):
                _managed.append({
                    "name": _fn,
                    "size": os.path.getsize(_p),
                    "mtime": os.path.getmtime(_p),
                })
        # 子目录内的副本（_cleanup_old_backups 不递归，故不受管）
        for _dp, _dns, _fns in os.walk(_dir):
            if os.path.abspath(_dp) == os.path.abspath(_dir):
                continue
            for _fn in sorted(_fns):
                if not _fn.endswith(".bak"):
                    continue
                _p = os.path.join(_dp, _fn)
                if not os.path.isfile(_p):
                    continue
                _unmanaged.append({
                    "path": normalize_relpath(_p, _root),
                    "size": os.path.getsize(_p),
                    "mtime": os.path.getmtime(_p),
                })

    _managed.sort(key=lambda x: -x["mtime"])
    _total = sum(x["size"] for x in _managed) + sum(x["size"] for x in _unmanaged)
    _over = max(0, len(_managed) - _keep)
    _cause = ""
    if _over:
        _cause = "受管目录内实际 %d 件 > 上限 %d（清理未生效或上限被改小）" % (
            len(_managed), _keep)
    elif _unmanaged:
        _cause = ("受管目录未超（%d/%d）；超额来自**子目录副本** —— "
                  "_cleanup_old_backups 仅 listdir 顶层、不递归，子目录副本不归管"
                  % (len(_managed), _keep))
    else:
        _cause = "受管目录 %d/%d，且无子目录副本 —— 无超额" % (len(_managed), _keep)

    return {
        "dir": normalize_relpath(_dir, _root),
        "base_name": base_name,
        "max_backups": _keep,
        "managed_count": len(_managed),
        "managed": _managed,
        "unmanaged_count": len(_unmanaged),
        "unmanaged": _unmanaged,
        "total_bytes": _total,
        "total_mb": round(_total / 1048576.0, 1),
        "over_limit": bool(_over or _unmanaged),
        "over_by": _over,
        "cause": _cause,
    }


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(
        description="快照备份保留现状只读审计（不删任何文件）")
    _ap.add_argument("--dir", default=None, help="快照目录，默认 data/knowledge")
    _ap.add_argument("--base-name", default=DEFAULT_BASE_NAME,
                     help="快照基名（受管匹配前缀）")
    _ap.add_argument("--keep", type=int, default=None, help="覆盖保留上限")
    _ap.add_argument("--json", dest="json_out", default=None,
                     help="结果落盘 JSON（仍不删任何快照）")
    args = _ap.parse_args(argv)

    _r = scan_retention(directory=args.dir, base_name=args.base_name,
                        keep=args.keep)
    print("[快照保留审计] 目录=%s 上限=%d" % (_r["dir"], _r["max_backups"]))
    print("  受管(顶层, 匹配 %s* 且含 .bak): %d 件"
          % (_r["base_name"], _r["managed_count"]))
    for _m in _r["managed"]:
        print("     - %-52s %.1f MB" % (_m["name"], _m["size"] / 1048576.0))
    print("  未受管(子目录 .bak): %d 件" % _r["unmanaged_count"])
    for _u in _r["unmanaged"]:
        print("     - %-52s %.1f MB" % (_u["path"], _u["size"] / 1048576.0))
    print("  合计 %.1f MB | 超额=%s (受管超出 %d 件)" % (
        _r["total_mb"], _r["over_limit"], _r["over_by"]))
    print("  归因: {}".format(_r["cause"]))

    if args.json_out:
        _out = args.json_out
        if not os.path.isabs(_out):
            _out = os.path.join(_PROJECT_ROOT, _out)
        _d = os.path.dirname(_out)
        if _d:
            os.makedirs(_d, exist_ok=True)
        with open(_out, "w", encoding="utf-8") as _f:
            json.dump(_r, _f, ensure_ascii=False, indent=2)
        print("  已写出: {}".format(_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
