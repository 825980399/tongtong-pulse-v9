# -*- coding: utf-8 -*-
"""数据治理：.corrupted 清理 + 知识备份轮转（主线第41批 T2 / P1-264）。

背景
----
* ``data/`` 下 1274 个 ``.corrupted`` 文件（实测**全部**是
  ``data/corrupted/bad2.json.<时间戳>.corrupted`` —— 同一损坏样本的历史快照，
  合计仅 **0.2 MB**；全库**无**对应的非损坏版本）。
* ``data/knowledge/`` 下知识快照备份无有效上限：``PulseSnapshot._max_backups``
  原为**硬编码 5** → 实测堆积 5 份 × 438 MB ≈ **2.19 GB**，且每 ~10 分钟仍在新增。
  （生成侧已在本批改为读 ``config.KNOWLEDGE_BACKUP_KEEP``，默认 3。）

处置策略（**可恢复优先**）
------------------------
1. ``.corrupted``
   * **有**对应非损坏版本 → 删除（冗余副本）。
   * **无**对应版本 → 移入 ``data/_quarantine/corrupted/``（保留
     ``config.CORRUPTED_QUARANTINE_DAYS`` 天，附 ``manifest.json`` 便于恢复）。
2. 知识备份
   * 按 mtime 倒序保留最新 ``config.KNOWLEDGE_BACKUP_KEEP``（默认 3）份**原样**；
   * 更旧的 → **gzip 压缩**到 ``data/_archive/knowledge_backups/``
     （JSON 压缩比高 → 真实释放磁盘空间，且内容仍可恢复）。

★幂等：源文件不存在 / 目标 .gz 已存在 → 跳过。
★``--dry-run``：只报告，不改动任何文件。

用法
----
    python tools/data_governance_m41.py --dry-run
    python tools/data_governance_m41.py
"""
from __future__ import annotations

import gzip
import io
import json
import os
import re
import shutil
import sys
import time
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_DATA = os.path.join(_ROOT, "data")
_QUAR = os.path.join(_DATA, "_quarantine", "corrupted")
_ARCH = os.path.join(_DATA, "_archive", "knowledge_backups")
_MANIFEST = os.path.join(_DATA, "_quarantine", "manifest.json")
_TS_SUFFIX = re.compile(r"^(?P<origin>.+)\.\d{8}_\d{6}$")


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def origin_name(fname: str) -> str:
    """``bad2.json.20260910_174016.corrupted`` → ``bad2.json``。"""
    _base = fname[: -len(".corrupted")] if fname.endswith(".corrupted") else fname
    _m = _TS_SUFFIX.match(_base)
    return _m.group("origin") if _m else _base


def scan_corrupted(root: str | None = None) -> list[tuple[str, str, int]]:
    """返回 [(绝对路径, 原文件名, 字节)]。

    ★root=None 时**运行时**读模块级 _DATA —— 不可用默认参数
    (root: str = _DATA)：默认参数在**函数定义时**求值，测试重定向
    _gov._DATA 后仍指向旧路径。
    """
    _root = root if root is not None else _DATA
    _out = []
    _skip = ("_quarantine", "_archive")
    for dp, dn, fn in os.walk(_root):
        # ★必须检查**路径任一段**：只比对 basename 会漏掉 _quarantine/corrupted/
        #   这类子目录 → 隔离后的文件被重复扫描，破坏幂等（实测踩到）。
        _parts = os.path.relpath(dp, _root).split(os.sep)
        if any(_s in _parts for _s in _skip):
            continue
        for f in fn:
            if f.endswith(".corrupted"):
                p = os.path.join(dp, f)
                try:
                    _out.append((p, origin_name(f), os.path.getsize(p)))
                except OSError:
                    continue
    return _out


def govern_corrupted(dry: bool = False) -> dict:
    """.corrupted 治理：有副本→删；无副本→隔离。"""
    _items = scan_corrupted()
    _del = _quar = 0
    _sz_del = _sz_q = 0
    _moved = []
    for _p, _origin, _sz in _items:
        _counterpart = os.path.join(os.path.dirname(_p), _origin)
        if os.path.isfile(_counterpart):
            if not dry:
                try:
                    os.remove(_p)
                except OSError:
                    continue
            _del += 1
            _sz_del += _sz
        else:
            if not dry:
                if not os.path.isdir(_QUAR):
                    os.makedirs(_QUAR, exist_ok=True)
                _dst = os.path.join(_QUAR, os.path.basename(_p))
                try:
                    # ★优先 os.replace（同盘原子 rename；跨盘才回退 shutil.move）
                    os.replace(_p, _dst)
                except OSError:
                    try:
                        shutil.move(_p, _dst)
                    except OSError:
                        continue
                _moved.append({"file": os.path.basename(_p),
                               "origin": _origin,
                               "size": _sz,
                               "at": time.time()})
            _quar += 1
            _sz_q += _sz
    if not dry and _moved:
        _old = []
        if os.path.isfile(_MANIFEST):
            try:
                _old = json.load(io.open(_MANIFEST, encoding="utf-8")) or []
            except Exception:
                _old = []
        with io.open(_MANIFEST, "w", encoding="utf-8") as f:
            json.dump(_old + _moved, f, ensure_ascii=False, indent=1)
    return {"total": len(_items), "deleted": _del, "quarantined": _quar,
            "freed_bytes": _sz_del, "quarantine_bytes": _sz_q}


def scan_knowledge_backups(kdir: str | None = None) -> list[tuple[float, str, int]]:
    """knowledge 目录下的 ``*.bak``（按 mtime 倒序）。"""
    _d = kdir or os.path.join(_DATA, "knowledge")
    _rows = []
    if not os.path.isdir(_d):
        return _rows
    for f in os.listdir(_d):
        if ".bak" not in f:
            continue
        p = os.path.join(_d, f)
        if not os.path.isfile(p):
            continue
        try:
            _rows.append((os.path.getmtime(p), p, os.path.getsize(p)))
        except OSError:
            continue
    _rows.sort(reverse=True)
    return _rows


def _gzip_file(src: str, dst: str, chunk: int = 4 * 1024 * 1024) -> bool:
    """流式压缩（大文件不整读内存）。"""
    try:
        with open(src, "rb") as _fi, gzip.open(dst, "wb", compresslevel=6) as _fo:
            while True:
                _b = _fi.read(chunk)
                if not _b:
                    break
                _fo.write(_b)
        return True
    except Exception as e:
        print("  [WARN] 压缩失败 %s: %s: %s" % (os.path.basename(src),
                                               type(e).__name__, e))
        return False


def govern_backups(dry: bool = False) -> dict:
    """知识备份轮转：留最新 K 份；更旧的 gzip 归档。"""
    _keep = int(_cfg("KNOWLEDGE_BACKUP_KEEP", 3) or 3)
    _keep = max(1, _keep)
    _rows = scan_knowledge_backups()
    _archived = 0
    _freed = 0
    _after = 0
    for _i, (_mt, _p, _sz) in enumerate(_rows):
        if _i < _keep:
            continue
        _dst = os.path.join(_ARCH, os.path.basename(_p) + ".gz")
        if os.path.isfile(_dst):
            continue                       # 已归档（幂等）
        if dry:
            _archived += 1
            continue
        if not os.path.isdir(_ARCH):
            os.makedirs(_ARCH, exist_ok=True)
        if _gzip_file(_p, _dst):
            try:
                _after = os.path.getsize(_dst)
            except OSError:
                _after = 0
            try:
                os.remove(_p)
            except OSError:
                pass
            _archived += 1
            _freed += max(0, _sz - _after)
    return {"keep": _keep, "total": len(_rows), "archived": _archived,
            "freed_bytes": _freed}


def main(argv: list[str]) -> int:
    _dry = "--dry-run" in argv
    print("=" * 74)
    print("数据治理报告（第41批 T2 / P1-264）%s" % ("[DRY-RUN]" if _dry else ""))
    print("=" * 74)

    _c = govern_corrupted(dry=_dry)
    print("[1] .corrupted")
    print("    扫描总数     : %d" % _c["total"])
    print("    删除（有副本）: %d  (%.2f MB)" % (_c["deleted"],
                                              _c["freed_bytes"] / 1024.0 / 1024.0))
    print("    隔离（无副本）: %d  (%.2f MB) → %s"
          % (_c["quarantined"], _c["quarantine_bytes"] / 1024.0 / 1024.0,
             os.path.relpath(_QUAR, _ROOT)))

    _b = govern_backups(dry=_dry)
    print()
    print("[2] 知识备份轮转（保留最新 %d 份）" % _b["keep"])
    print("    现有备份数   : %d" % _b["total"])
    print("    压缩归档     : %d 份" % _b["archived"])
    print("    释放空间     : %.1f MB" % (_b["freed_bytes"] / 1024.0 / 1024.0))
    print("    归档目录     : %s" % os.path.relpath(_ARCH, _ROOT))

    print()
    _tot = (_c["freed_bytes"] + _b["freed_bytes"]) / 1024.0 / 1024.0
    print("合计释放空间   : %.1f MB" % _tot)
    if _dry:
        print()
        print("[DRY-RUN] 未改动任何文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
