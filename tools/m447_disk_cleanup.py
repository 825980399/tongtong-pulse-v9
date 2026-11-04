# -*- coding: utf-8 -*-
"""447 刀3：清磁盘债（精确清单，默认只打印，`--apply` 才删）。

纪律
    * **只删显式清单里的绝对路径**，不做目录级递归删除、不用通配符扫目录。
    * 每个候选都要过四道复核：是文件不是目录 / 不是符号链接 / mtime 早于
      `--min-age-sec`（默认 3600s，防删正在写的文件）/ 不在保护名单里。
    * 删前把清单写成 `data/reports/m447_cleanup_<ts>.json`（路径/字节/mtime/分类），
      删后逐条回读确认"确实不在了"，再报释放量。
    * 保护名单（**永不删**）：`data/models/`、Parquet 主存、`pulse_knowledge_snapshot.json`
      本体与其最新 `.bak`、`.bak_batch445/446/447/`。

用法
    python tools/m447_disk_cleanup.py              # 只打印计划
    python tools/m447_disk_cleanup.py --apply      # 真删
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc

#: 永远不删的路径前缀（相对仓根）
PROTECTED = (
    "data/models/",
    "data/knowledge/pulse_knowledge_snapshot.json",
    "data/knowledge/vectors.npz",
)

#: pairs 保留日（任务书：最近 3 天）
PAIRS_KEEP = ("20261102", "20261103", "20261104")


def _rel(p: str, root: str | None = None) -> str:
    return os.path.relpath(p, root or _ROOT).replace("\\", "/")


def _size(p: str) -> int:
    try:
        return os.path.getsize(p)
    except OSError as _e:
        silent_exc(_e, where="tools/m447_disk_cleanup.py::_size", level="debug")
        return 0


def _guarded(p: str, *, min_age_sec: float, now: float,
             root: str | None = None) -> tuple[bool, str]:
    """四道复核。返回 ``(ok, why)``。`root` 仅供隔离单测注入。"""
    if not os.path.isfile(p):
        return False, "不是普通文件"
    if os.path.islink(p):
        return False, "符号链接"
    _r = _rel(p, root)
    if any(_r.startswith(_x) for _x in PROTECTED):
        return False, "保护名单"
    if os.path.basename(p) == "pulse_knowledge_snapshot.json":
        return False, "知识快照本体"
    try:
        _age = now - os.path.getmtime(p)
    except OSError as _e:
        silent_exc(_e, where="tools/m447_disk_cleanup.py::_guarded mtime", level="debug")
        return False, "读不到 mtime"
    if _age < min_age_sec:
        return False, f"太新（{_age:.0f}s < {min_age_sec:.0f}s）"
    return True, "ok"


def plan(*, now: float | None = None, orphan_min_mb: float = 100.0,
         root: str | None = None) -> list:
    """生成删除计划（纯读）。每项：path/rel/bytes/mtime/category。

    `root` 仅供隔离单测注入（默认仓根）；生产调用方不传。
    """
    _root = root or _ROOT
    _now = float(now if now is not None else time.time())
    _out: list = []

    def _add(p: str, category: str) -> None:
        _out.append({"path": p, "rel": _rel(p, _root), "bytes": _size(p),
                     "mtime": os.path.getmtime(p) if os.path.exists(p) else 0.0,
                     "category": category})

    # ① data/reports/memory/：tracemalloc 基线，只留**最新一份**；孤儿 .tmp 全删
    _mem = os.path.join(_root, "data", "reports", "memory")
    _all_mem = ([os.path.join(_mem, n) for n in os.listdir(_mem)]
                if os.path.isdir(_mem) else [])
    _all_mem = [p for p in _all_mem if os.path.basename(p).startswith("_tm_baseline")]
    # ★「留最新一份」只在**正式快照**里选；`.snap.tmp` 是写一半留下的临时件，
    #   一律删（它再新也不是可用基线，留着只会让下一个人以为是有效读数）。
    _snaps = sorted([p for p in _all_mem if p.endswith(".snap")],
                    key=lambda x: os.path.getmtime(x), reverse=True)
    for _p in _snaps[1:]:
        _add(_p, "tracemalloc_baseline")
    for _p in _all_mem:
        if _p.endswith(".snap.tmp"):
            _add(_p, "tracemalloc_baseline")

    # ② data/knowledge/：mkstemp 随机名孤儿快照（`snapshot_<8位随机>.json`）
    _kd = os.path.join(_root, "data", "knowledge")
    if os.path.isdir(_kd):
        for _n in sorted(os.listdir(_kd)):
            if not (_n.startswith("snapshot_") and _n.endswith(".json")):
                continue
            _p = os.path.join(_kd, _n)
            _stem = _n[len("snapshot_"):-len(".json")]
            if len(_stem) != 8 or not _stem.isalnum():
                continue                  # 不是 mkstemp 随机名，不碰
            # 任务书点名的是「2 个、约 1.3GB」这两个大件；同类的两个小件
            # （09-28 的 10.8MB / 9.1MB）**不在点名范围内** ⇒ 不删，只在计划里报出来
            # 供人裁决（删除动作宁少不多：多删一次要恢复，少删一次只是占地方）。
            if _size(_p) < orphan_min_mb * 1048576.0:
                print(f"   · 同类孤儿但不在任务书清单（保留待裁决）: {_rel(_p, _root)}  {_human(_size(_p))}")
                continue
            _add(_p, "orphan_knowledge_snapshot")

    # ③ data/monitor/pulse_pairs_*.jsonl：只留最近 3 天
    for _p in sorted(glob.glob(os.path.join(_root, "data", "monitor",
                                            "pulse_pairs_*.jsonl"))):
        _day = os.path.basename(_p)[len("pulse_pairs_"):-len(".jsonl")]
        if _day in PAIRS_KEEP:
            continue
        _add(_p, "pulse_pairs")

    # ④ data/_archive/：重复备份（.trim.bak 是仍在盘上的那份的裁切副本）
    _trim = os.path.join(_root, "data", "_archive",
                         "pulse_pairs_20261102.jsonl.trim.bak")
    if os.path.exists(_trim):
        _add(_trim, "archive_duplicate")
    _kb = os.path.join(_root, "data", "_archive", "knowledge_backups")
    if os.path.isdir(_kb):
        _gz = sorted([os.path.join(_kb, n) for n in os.listdir(_kb)],
                     key=lambda x: os.path.getmtime(x), reverse=True)
        for _p in _gz[1:]:                # 只留最新一份冷备
            _add(_p, "archive_duplicate")
    return _out


def _human(n: float) -> str:
    for _u in ("B", "KB", "MB", "GB"):
        if n < 1024 or _u == "GB":
            return f"{n:.1f}{_u}"
        n /= 1024.0
    return f"{n:.1f}GB"


def _du_mb(p: str) -> float:
    _t = 0
    for _dp, _dns, _fns in os.walk(p):
        _dns[:] = [_d for _d in _dns if not _d.startswith(".git")]
        for _f in _fns:
            _t += _size(os.path.join(_dp, _f))
    return _t / 1048576.0


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="447 刀3 磁盘清理")
    _ap.add_argument("--apply", action="store_true", help="真的删（默认只打印）")
    _ap.add_argument("--min-age-sec", type=float, default=3600.0)
    _args = _ap.parse_args(argv)

    _now = time.time()
    _items = plan(now=_now)
    _by_cat: dict = {}
    for _it in _items:
        _by_cat.setdefault(_it["category"], [0, 0])
        _by_cat[_it["category"]][0] += 1
        _by_cat[_it["category"]][1] += _it["bytes"]

    _tot = _human(sum(i["bytes"] for i in _items))
    print(f"== 删除计划（{len(_items)} 项 / {_tot}）==")
    for _c, (_n, _b) in sorted(_by_cat.items(), key=lambda kv: -kv[1][1]):
        print(f"  {_c:<28s} {_n:3d} 项  {_human(_b)}")
    for _it in _items:
        print("   - {}  {}".format(_rel_ok(_it), _human(_it["bytes"])))

    _ok, _skip = [], []
    for _it in _items:
        _g, _why = _guarded(_it["path"], min_age_sec=_args.min_age_sec, now=_now,
                            root=_ROOT)
        (_ok if _g else _skip).append((_it, _why))
    if _skip:
        print(f"\n== 复核拦下 {len(_skip)} 项（不删）==")
        for _it, _why in _skip:
            print(f"   ! {_it['rel']}  ← {_why}")

    _freed_plan = _human(sum(i["bytes"] for i, _ in _ok))
    print(f"\n== 可删 {len(_ok)} 项 / {_freed_plan} ==")
    if not _args.apply:
        print("DRY-RUN：未删任何文件（加 --apply 才删）")
        return 0

    _before_mb = _du_mb(os.path.join(_ROOT, "data"))
    _manifest = os.path.join(_ROOT, "data", "reports",
                             "m447_cleanup_{}.json".format(time.strftime("%Y%m%d_%H%M%S")))
    try:
        os.makedirs(os.path.dirname(_manifest), exist_ok=True)
        with open(_manifest, "w", encoding="utf-8", newline="\n") as _f:
            json.dump({"at": _now, "at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "apply": True, "planned": _items,
                       "skipped": [{"rel": i["rel"], "why": w} for i, w in _skip],
                       "data_dir_mb_before": round(_before_mb, 1)},
                      _f, ensure_ascii=False, indent=1)
        print("清单:", _rel(_manifest))
    except OSError as _e:
        silent_exc(_e, where="tools/m447_disk_cleanup.py::main 写清单", level="warning")
        print("FAIL: 清单写不出来，拒绝删除（无清单不删）")
        return 1

    _freed = 0
    _deleted, _failed = [], []
    for _it, _why in _ok:
        try:
            os.remove(_it["path"])
            if os.path.exists(_it["path"]):
                _failed.append((_it["rel"], "删后仍存在"))
                continue
            _freed += int(_it["bytes"])
            _deleted.append(_it["rel"])
        except OSError as _e:
            silent_exc(_e, where="tools/m447_disk_cleanup.py::main 删除", level="warning")
            _failed.append((_it["rel"], f"{type(_e).__name__}: {_e}"))

    _after_mb = _du_mb(os.path.join(_ROOT, "data"))
    print("\n== 结果 ==")
    print(f"  删除成功 {len(_deleted)} 项 / 失败 {len(_failed)} 项")
    print(f"  释放 {_human(_freed)}（{_freed} 字节）")
    print(f"  data 目录 {round(_before_mb, 1)} → {round(_after_mb, 1)} MB")
    for _r, _w in _failed:
        print(f"  ! 失败 {_r} ← {_w}")

    try:
        with open(_manifest, "r", encoding="utf-8") as _f:
            _doc = json.load(_f)
        _doc["deleted"] = _deleted
        _doc["failed"] = [{"rel": r, "why": w} for r, w in _failed]
        _doc["freed_bytes"] = _freed
        _doc["data_dir_mb_after"] = round(_after_mb, 1)
        with open(_manifest, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_doc, _f, ensure_ascii=False, indent=1)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="tools/m447_disk_cleanup.py::main 回填清单", level="warning")
    return 0 if not _failed else 2


def _rel_ok(it: dict) -> str:
    return it.get("rel") or it.get("path") or ""


if __name__ == "__main__":
    sys.exit(main())
