# -*- coding: utf-8 -*-
"""utils/backup_retention.py —— 437 刀2：历史备份文件的保留策略与清理。

【为什么需要（实测盘面，2026-11-03 20:5x）】
`data/` 共 8.0G，其中 `.bak` 系列 **22 个文件 / 1373.9 MB**（占 16.8%）。三个巨型：

    768.5 MB  data/knowledge/pulse_knowledge_snapshot.json.20261103_064554.bak
    463.3 MB  data/knowledge/evidence_chain_freeze_20260923/…20260923_093831.bak
    141.4 MB  data/_archive/pulse_pairs_20261102.jsonl.trim.bak

其余 19 个合计 ~0.6 MB（435 批已把 `_keep_backup` 改成「小文件只留 1 代」）。

【口径】
本模块只认三种**明确的备份后缀**，别的一律不碰：
    ``<base>.bak``          gen 0
    ``<base>.bak.<N>``      gen N（N≥1，`_keep_backup` 的旧轮转位）
    ``<base>.trim.bak``     gen 0（`pulse_tracer` 裁切前备份）
两把尺子任一命中即「可删」：
    ① **代数**：同一 base 已有 gen 0 ⇒ 其余 gen≥keep_generations 的删掉（「只留最近 1 代」）
    ② **年龄**：mtime 早于 max_age_days 天 ⇒ 可删

【保护名单（宁可不删）】
    · `evidence_chain_freeze*` —— 证据链冻结仓，删了不可再生
    · `_archive`              —— 人工归档目录，去留由人决定
    · `.git` / `node_modules` —— 不遍历
    · 只处理**普通文件**，不跟随符号链接；root 之外的路径一律拒绝（commonpath 校验）

【默认只扫不删】
`sweep(dry_run=True)` 是默认值；真删必须显式 `dry_run=False`，
且每次都会把清单落到 `data/monitor/backup_sweep_last.json` 便于事后追溯。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time

_log = logging.getLogger("pulse.module.backup_retention")

# 保护：目录名命中即整棵跳过
PROTECT_DIR_MARKERS = ("evidence_chain_freeze", "_archive", ".git", "node_modules")
# 不遍历的顶层
SKIP_TOP_DIRS = (".git", "node_modules", "__pycache__", ".venv", "venv")

_ROT_RE = re.compile(r"^(?P<base>.+)\.bak\.(?P<gen>\d+)$")
_TRIM_SUFFIX = ".trim.bak"
_BAK_SUFFIX = ".bak"

_MANIFEST_REL = os.path.join("monitor", "backup_sweep_last.json")
_STAMP_REL = os.path.join("monitor", "backup_sweep_stamp.json")


# ---------------------------------------------------------------- 分类
def classify_backup(filename: str) -> tuple[str, int] | None:
    """判断文件名是否属于备份，返回 ``(base_key, generation)``；不是备份返回 None。

    只认三种后缀（见模块头）。`base_key` 是**同源文件的分组键**：
    ``a.json.bak`` / ``a.json.bak.1`` / ``a.json.bak.2`` → ``a.json``；
    ``p.jsonl.trim.bak`` → ``p.jsonl``；
    ``s.json.20261103_064554.bak`` → ``s.json.20261103_064554``（时间戳式，各自独立一代）。
    """
    if not filename:
        return None
    # ★顺序要紧：`x.bak.1` 不以 ".bak" 结尾，必须先判轮转位再判普通 .bak
    _m = _ROT_RE.match(filename)
    if _m:
        return _m.group("base"), int(_m.group("gen"))
    if not filename.endswith(_BAK_SUFFIX):
        return None
    if filename.endswith(_TRIM_SUFFIX):
        return filename[: -len(_TRIM_SUFFIX)], 0
    return filename[: -len(_BAK_SUFFIX)], 0


def _is_protected(rel_parts: tuple[str, ...]) -> bool:
    """相对路径的**目录**部分命中保护标记 ⇒ True（文件名本身不算）。"""
    for _part in rel_parts[:-1]:
        for _mk in PROTECT_DIR_MARKERS:
            if _mk in _part:
                return True
    return False


# ---------------------------------------------------------------- 扫描
def scan(root: str = "data", max_age_days: float = 7.0,
         keep_generations: int = 1, now: float | None = None) -> dict:
    """只读扫描：返回可删 / 保留 / 跳过三张清单。**不删任何文件**。

    Returns:
        dict::

            {"root","scanned","total_bytes","deletable":[{path,size,age_days,reason,base,gen}],
             "kept":[...], "skipped":[{path,why}], "deletable_bytes"}
    """
    _now = time.time() if now is None else float(now)
    _root = os.path.abspath(root)
    _max_age_sec = max(0.0, float(max_age_days)) * 86400.0
    _keep = max(1, int(keep_generations))

    if not os.path.isdir(_root):
        return {"root": _root, "scanned": 0, "total_bytes": 0, "deletable": [],
                "kept": [], "skipped": [], "deletable_bytes": 0,
                "why": "root 不存在"}

    _groups: dict[str, list[dict]] = {}
    _skipped: list[dict] = []
    _scanned = 0
    _total_bytes = 0

    for _dirpath, _dirnames, _filenames in os.walk(_root):
        _dirnames[:] = [d for d in _dirnames
                        if d not in SKIP_TOP_DIRS
                        and not any(_mk in d for _mk in PROTECT_DIR_MARKERS)]
        for _fn in _filenames:
            _cls = classify_backup(_fn)
            if _cls is None:
                continue
            _full = os.path.join(_dirpath, _fn)
            _rel = os.path.relpath(_full, _root)
            if os.path.islink(_full) or not os.path.isfile(_full):
                _skipped.append({"path": _rel, "why": "不是普通文件"})
                continue
            try:
                _st = os.stat(_full)
            except OSError as _e:
                _skipped.append({"path": _rel, "why": f"stat 失败: {_e}"})
                continue
            _scanned += 1
            _total_bytes += int(_st.st_size)
            _age_days = max(0.0, (_now - float(_st.st_mtime)) / 86400.0)
            _base, _gen = _cls
            # 分组键带目录：不同目录的同名文件互不干扰
            _key = os.path.normcase(os.path.join(_dirpath, _base))
            _groups.setdefault(_key, []).append({
                "path": _full, "rel": _rel, "size": int(_st.st_size),
                "mtime": float(_st.st_mtime), "age_days": _age_days,
                "base": _base, "gen": _gen,
            })

    _deletable: list[dict] = []
    _kept: list[dict] = []
    for _items in _groups.values():
        _gens = {it["gen"] for it in _items}
        _has_gen0 = 0 in _gens
        for _it in _items:
            _reasons = []
            if _has_gen0 and _it["gen"] >= _keep:
                _reasons.append(f"多代备份（gen={_it['gen']}，保留 {_keep} 代）")
            if _it["age_days"] > float(max_age_days):
                _reasons.append(f"超过 {max_age_days:g} 天（{_it['age_days']:.1f} 天）")
            _out = {
                "path": _it["path"], "rel": _it["rel"], "size": _it["size"],
                "age_days": round(_it["age_days"], 2), "base": _it["base"],
                "gen": _it["gen"], "reason": " + ".join(_reasons),
            }
            if _reasons:
                _deletable.append(_out)
            else:
                _out["reason"] = "未超龄且是最新一代"
                _kept.append(_out)

    _deletable.sort(key=lambda x: -x["size"])
    _kept.sort(key=lambda x: -x["size"])
    return {
        "root": _root,
        "scanned": _scanned,
        "total_bytes": _total_bytes,
        "deletable": _deletable,
        "kept": _kept,
        "skipped": _skipped,
        "deletable_bytes": sum(x["size"] for x in _deletable),
        "generated_at": _now,
    }


# ---------------------------------------------------------------- 清理
def sweep(root: str = "data", max_age_days: float = 7.0,
          keep_generations: int = 1, dry_run: bool = True,
          now: float | None = None) -> dict:
    """按 `scan()` 的清单执行删除。``dry_run=True``（默认）只返回清单不动盘。"""
    _rep = scan(root, max_age_days=max_age_days,
                keep_generations=keep_generations, now=now)
    _root = _rep["root"]
    _deleted: list[dict] = []
    _failed: list[dict] = []

    if not dry_run:
        for _it in _rep["deletable"]:
            _p = _it["path"]
            # 双保险：解析后必须仍在 root 内，且仍不是符号链接
            try:
                _rp = os.path.realpath(_p)
                if os.path.commonpath([os.path.realpath(_root), _rp]) != os.path.realpath(_root):
                    _failed.append({"rel": _it["rel"], "why": "越出 root，拒绝删除"})
                    continue
                if os.path.islink(_p):
                    _failed.append({"rel": _it["rel"], "why": "符号链接，拒绝删除"})
                    continue
                os.remove(_p)
                _deleted.append(_it)
            except OSError as _e:
                _failed.append({"rel": _it["rel"], "why": f"{type(_e).__name__}: {_e}"})

    _out = dict(_rep)
    _out["dry_run"] = bool(dry_run)
    _out["deleted"] = _deleted
    _out["failed"] = _failed
    _out["deleted_bytes"] = sum(x["size"] for x in _deleted) if _deleted else 0
    return _out


def _write_manifest(report: dict, root: str) -> None:
    """把最近一次清理清单落到 `data/monitor/backup_sweep_last.json`（失败只记日志）。"""
    try:
        _path = os.path.join(os.path.abspath(root), _MANIFEST_REL)
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        _slim = {
            "generated_at": report.get("generated_at"),
            "dry_run": report.get("dry_run"),
            "scanned": report.get("scanned"),
            "total_bytes": report.get("total_bytes"),
            "deletable_bytes": report.get("deletable_bytes"),
            "deleted_bytes": report.get("deleted_bytes"),
            "deleted": [{"rel": d["rel"], "size": d["size"], "reason": d["reason"]}
                        for d in report.get("deleted", [])],
            "kept": [{"rel": d["rel"], "size": d["size"]} for d in report.get("kept", [])],
            "failed": report.get("failed", []),
        }
        with open(_path, "w", encoding="utf-8") as _f:
            json.dump(_slim, _f, ensure_ascii=False, indent=1)
    except OSError as _e:
        _log.debug("[437刀2] 写清单失败: %s: %s", type(_e).__name__, _e)


# ---------------------------------------------------------------- 运行时挂载
def _cfg(name: str, default):
    try:
        _c = sys.modules.get("config")
        if _c is None:
            return default
        return getattr(_c, name, default)
    except Exception as _e:
        _log.debug("[437刀2] 读 config.%s 失败: %s", name, _e)
        return default


def maybe_sweep_once(root: str = "data") -> dict:
    """按 `M437_BACKUP_SWEEP_MIN_INTERVAL_HOURS` 节流地跑一次**真删**清理。

    节流戳：`data/monitor/backup_sweep_stamp.json`。间隔内直接返回 ``{"skipped": True}``。
    """
    if not bool(_cfg("ENABLE_M437_BACKUP_RETENTION", True)):
        return {"skipped": True, "why": "ENABLE_M437_BACKUP_RETENTION=False"}
    if not bool(_cfg("M437_BACKUP_SWEEP_ENABLED", True)):
        return {"skipped": True, "why": "M437_BACKUP_SWEEP_ENABLED=False"}

    _root = os.path.abspath(root)
    _stamp = os.path.join(_root, _STAMP_REL)
    _interval_h = float(_cfg("M437_BACKUP_SWEEP_MIN_INTERVAL_HOURS", 24) or 24) * 3600.0
    _now = time.time()
    try:
        if os.path.isfile(_stamp):
            with open(_stamp, encoding="utf-8") as _f:
                _last = float(json.load(_f).get("last_sweep_at") or 0.0)
            if _now - _last < _interval_h:
                return {"skipped": True,
                        "why": f"距上次 {( _now - _last) / 3600.0:.1f}h < {_interval_h / 3600.0:.0f}h"}
    except (OSError, ValueError, TypeError) as _e:
        _log.debug("[437刀2] 读节流戳失败（照常执行）: %s: %s", type(_e).__name__, _e)

    _max_age = float(_cfg("M437_BACKUP_MAX_AGE_DAYS", 7) or 7)
    _keep = int(_cfg("M437_BACKUP_KEEP_GENERATIONS", 1) or 1)
    _rep = sweep(_root, max_age_days=_max_age, keep_generations=_keep, dry_run=False)
    _write_manifest(_rep, _root)

    try:
        os.makedirs(os.path.dirname(_stamp), exist_ok=True)
        with open(_stamp, "w", encoding="utf-8") as _f:
            json.dump({"last_sweep_at": _now,
                       "deleted": len(_rep.get("deleted", [])),
                       "deleted_bytes": _rep.get("deleted_bytes", 0)}, _f)
    except OSError as _e:
        _log.debug("[437刀2] 写节流戳失败: %s: %s", type(_e).__name__, _e)

    if _rep.get("deleted"):
        _log.info("[437刀2] 备份清理: 删 %d 个 / 回收 %.2f MB（可删清单 %.2f MB）",
                  len(_rep["deleted"]), _rep["deleted_bytes"] / 1048576.0,
                  _rep["deletable_bytes"] / 1048576.0)
    else:
        _log.info("[437刀2] 备份清理: 无需删除（扫描 %d 个备份 / %.2f MB，全部在保留策略内）",
                  _rep.get("scanned", 0), _rep.get("total_bytes", 0) / 1048576.0)
    return _rep


def start_sweeper_thread(root: str = "data", delay_sec: float = 20.0) -> threading.Thread:
    """起一个**低优先级守护线程**，延迟 `delay_sec` 后跑一次清理。

    为什么要延迟：启动窗口内 L0/L1 池正在建、模型正在加载，不要在这时候抢 IO。
    """
    def _run() -> None:
        try:
            time.sleep(max(0.0, float(delay_sec)))
            maybe_sweep_once(root)
        except Exception as _e:
            _log.warning("[437刀2] 备份清理线程异常: %s: %s", type(_e).__name__, _e)

    _t = threading.Thread(target=_run, name="M437BackupSweep", daemon=True)
    _t.start()
    return _t


# ---------------------------------------------------------------- CLI
def _main(argv: list[str] | None = None) -> int:
    _ap = argparse.ArgumentParser(description="437刀2 备份保留策略：先扫清单，再删")
    _ap.add_argument("--root", default="data", help="扫描根目录（默认 data）")
    _ap.add_argument("--max-age-days", type=float, default=7.0)
    _ap.add_argument("--keep-generations", type=int, default=1)
    _ap.add_argument("--apply", action="store_true", help="真删（默认只打印清单）")
    _args = _ap.parse_args(argv)

    _rep = sweep(_args.root, max_age_days=_args.max_age_days,
                 keep_generations=_args.keep_generations, dry_run=not _args.apply)
    if _args.apply:
        _write_manifest(_rep, _args.root)   # 真删就留清单，便于事后追溯
    print("=" * 78)
    print(f"root={_rep['root']}  扫描备份 {_rep['scanned']} 个 / "
          f"{_rep['total_bytes'] / 1048576.0:.1f} MB  "
          f"可删 {len(_rep['deletable'])} 个 / {_rep['deletable_bytes'] / 1048576.0:.1f} MB")
    for _it in _rep["deletable"]:
        print(f"  [删] {_it['size'] / 1048576.0:10.2f} MB  "
              f"{_it['rel']:<58} {_it['reason']}")
    for _it in _rep["kept"]:
        print(f"  [留] {_it['size'] / 1048576.0:10.2f} MB  "
              f"{_it['rel']:<58} {_it['reason']}")
    if _rep["dry_run"]:
        print("（dry-run：未删除任何文件；加 --apply 才真删）")
    else:
        print(f"已删 {len(_rep['deleted'])} 个 / "
              f"{_rep['deleted_bytes'] / 1048576.0:.2f} MB；"
              f"失败 {len(_rep['failed'])} 个")
        for _f in _rep["failed"]:
            print(f"  [失败] {_f['rel']}: {_f['why']}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
