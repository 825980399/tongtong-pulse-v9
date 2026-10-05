# -*- coding: utf-8 -*-
"""第160批 下下 刀4：独立调用 PulseSnapshot.m160_rebuild_parquet_from_nodes。

默认 dry_run（只统计不写盘）；--apply 实际重刷 Parquet 主存储。
禁止在 cleanup_alias_placeholder_nodes.py 中另写一份 Parquet 逻辑——统一走本工具/本函数。

用法：
    python tools/rebuild_parquet_from_snapshot.py                  # dry-run 统计
    python tools/rebuild_parquet_from_snapshot.py --apply          # 实际重刷
    python tools/rebuild_parquet_from_snapshot.py --apply --force  # 忽略停框架守卫
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

_DEFAULT_SNAPSHOT = os.path.join(_ROOT, "data", "knowledge",
                                 "pulse_knowledge_snapshot.json")


def _framework_looks_running(snapshot: str, threshold_sec: int = 600) -> bool:
    """停框架守卫：快照近期被写入，或存在大内存主框架 python 进程。"""
    try:
        if os.path.exists(snapshot) and (time.time() - os.path.getmtime(snapshot) < threshold_sec):
            return True
    except OSError as _e:
        silent_exc(_e, where="rebuild_parquet_from_snapshot::_framework_looks_running")
    try:
        import psutil
        for _p in psutil.process_iter(["name", "memory_info"]):
            try:
                if str(_p.info.get("name", "")).lower().startswith("python"):
                    _mi = _p.info.get("memory_info")
                    if _mi is not None and _mi.rss > 1_000_000_000:
                        return True
            except Exception as _pe:
                silent_exc(_pe, where="rebuild_parquet_from_snapshot::process_iter")
    except Exception as _e:
        silent_exc(_e, where="rebuild_parquet_from_snapshot::_framework_looks_running psutil")
    return False


def _load_nodes(snapshot: str):
    from nucleus.mnemosyne.PulseNode import PulseNode
    with io.open(snapshot, "r", encoding="utf-8") as _f:
        _data = json.load(_f)
    _nodes = []
    for _nd in _data.get("nodes", []):
        try:
            _nodes.append(PulseNode.from_dict(_nd))
        except Exception as _e:
            silent_exc(_e, where="rebuild_parquet_from_snapshot::_load_nodes")
    return _nodes


def main() -> int:
    _ap = argparse.ArgumentParser(
        description="第160批下下 刀4：Parquet 主存储原子重刷")
    _ap.add_argument("--snapshot", default=_DEFAULT_SNAPSHOT,
                     help="待重建快照（默认主快照 pulse_knowledge_snapshot.json）")
    _ap.add_argument("--apply", action="store_true",
                     help="实际写盘（默认 dry-run 统计）")
    _ap.add_argument("--force", action="store_true",
                     help="即使框架疑似运行也强制写盘（不推荐）")
    _args = _ap.parse_args()

    if not os.path.exists(_args.snapshot):
        print(f"[错误] 快照不存在: {_args.snapshot}")
        return 2

    from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot
    _nodes = _load_nodes(_args.snapshot)
    print(f"[加载] {_args.snapshot} → {len(_nodes)} 节点")

    _snap = PulseSnapshot(snapshot_path=_args.snapshot)

    if not _args.apply:
        _report = _snap.m160_rebuild_parquet_from_nodes(dry_run=True, nodes=_nodes)
        print(f"[dry-run] {_report}")
        return 0

    # ---- 落盘前停框架守卫 ----
    if _framework_looks_running(_args.snapshot) and not _args.force:
        print("\n[拒绝] 检测到框架疑似仍在运行（快照近期被写入或存在主框架进程）。")
        print("       请先停止框架，否则重建会被下一次保存覆盖。如需强制加 --force。")
        return 3

    _report = _snap.m160_rebuild_parquet_from_nodes(dry_run=False, nodes=_nodes)
    print(f"[结果] {_report}")
    if _report.get("status") == "rebuilt":
        print("[完成] Parquet 主存储已原子重刷。"
              "建议随后写 JSON 快照以保一致（写序：Parquet 先、JSON 后）。")
        return 0
    print(f"[失败] 重建未成功：{_report}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
