#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tmp/ 备份与依赖扫描工具（主线第46批 T4，P2-300/301/307）

两部分能力：

1. **tmp 备份**（P2-300）
   - ``snapshot()`` 把 ``tmp/`` 中的**关键文件**快照到 ``.tmp_backup/<YYYYMMDD_HHMMSS>/``
   - ``prune(keep=5)`` 保留最近 N 份，自动清理旧备份
   - ``restore(ts)`` 一键恢复指定备份
   - 与 ``.bak_batchNN/`` 协同：后者备份**源码**，本工具备份 **tmp**

2. **测试依赖扫描**（P2-307）
   - ``scan_test_dependencies()`` 扫描 tests/ 中对 tmp 文件的三类依赖：
     import / subprocess / open
   - 清理前必须排除这些文件 —— 第45批误删 3 个测试依赖文件的直接教训

用法::

    python tools/tmp_backup.py snapshot            # 建快照
    python tools/tmp_backup.py list                # 列备份
    python tools/tmp_backup.py restore <ts>        # 恢复
    python tools/tmp_backup.py prune --keep 5      # 保留最近 5 份
    python tools/tmp_backup.py deps                # 输出测试依赖的 tmp 文件清单
    python tools/tmp_backup.py deps --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(ROOT, "tmp")
#: ★第47批 T4（P2-300）：默认落点改为 ``.bak_tmp/`` ——
#:   可被既有 ``.bak*`` 规则自动排除，无需为全库扫描类测试逐个打补丁。
BACKUP_ROOT = os.path.join(ROOT, ".bak_tmp")
#: 第46批旧落点（不自动被排除 → 需迁移）
LEGACY_BACKUP_ROOT = os.path.join(ROOT, ".tmp_backup")

#: 需要备份的文件名形态（第45批教训：测试会 import/subprocess 这些脚本）
KEEP_PATTERNS = (
    re.compile(r"^(?:patch|scan|check|update|verify|record|run|backfill|calibrate)_[\w\-]+\.py$"),
    re.compile(r"^test_[\w\-]+\.py$"),                 # tmp/test_isolation.py 之外的测试桩
    re.compile(r"^_(?:m\d+|event|im)[\w\-]*\.(?:py|json|log)$"),
    re.compile(r"^[\w\-]+\.py$"),                      # 兜底：所有 .py 都值得留
)
#: 明确不备份的形态
SKIP_PATTERNS = (
    re.compile(r"^_m\d{2}_collect\d?\.txt$"),          # 收集用 scratch
    re.compile(r"^\.pytest_cache"),
)
TS_FMT = "%Y%m%d_%H%M%S"
MANIFEST = "MANIFEST.json"


# ==================== 基础 ====================

# ★第48批 T2（P2-320）：委托给统一实现 ``nucleus.data.path_utils.safe_relpath``。
#   本文件原先自带的 ``_rel()`` 是第46批的本地补丁；现统一到权威实现，
#   便于其他模块复用（避免每处各写一遍 try/except）。
from nucleus._silent_except import silent_exc
from nucleus.data.path_utils import safe_relpath as _safe_relpath  # noqa: E402


def _rel(path: str, root: str = ROOT) -> str:
    """相对路径（★跨盘安全）—— 等价于 ``os.path.relpath`` + 跨盘降级。"""
    return _safe_relpath(path, root)


def _sha256(path: str) -> str:
    _h = hashlib.sha256()
    with open(path, "rb") as _f:
        for _chunk in iter(lambda: _f.read(65536), b""):
            _h.update(_chunk)
    return _h.hexdigest()


def _should_keep(name: str) -> bool:
    if any(p.match(name) for p in SKIP_PATTERNS):
        return False
    return any(p.match(name) for p in KEEP_PATTERNS)


def iter_tmp_files(tmp_dir: str = TMP_DIR):
    """遍历 tmp/ 下**直接子文件**（不递归子目录，避免把隔离目录整个搬走）。"""
    if not os.path.isdir(tmp_dir):
        return
    for _n in sorted(os.listdir(tmp_dir)):
        _fp = os.path.join(tmp_dir, _n)
        if os.path.isfile(_fp) and _should_keep(_n):
            yield _n, _fp


def iter_tmp_dirs(tmp_dir: str = TMP_DIR):
    """遍历 tmp/ 下的直接子目录（只备份**空目录或极小目录**的结构信息）。"""
    if not os.path.isdir(tmp_dir):
        return
    for _n in sorted(os.listdir(tmp_dir)):
        _fp = os.path.join(tmp_dir, _n)
        if os.path.isdir(_fp):
            yield _n, _fp


# ==================== 备份 ====================

def snapshot(tmp_dir: str = TMP_DIR, backup_root: str = BACKUP_ROOT,
             label: str = "") -> dict:
    """创建 tmp/ 快照。返回快照报告（含 sha256 清单）。"""
    _ts = time.strftime(TS_FMT)
    _dst = os.path.join(backup_root, _ts)
    if os.path.isdir(_dst):        # 同秒冲突 → 加后缀
        _dst = "%s_%06d" % (_dst, int(time.time() * 1000) % 1000000)
    os.makedirs(_dst, exist_ok=True)

    _files = []
    for _n, _fp in iter_tmp_files(tmp_dir):
        try:
            _d = os.path.join(_dst, _n)
            shutil.copy2(_fp, _d)
            _files.append({"name": _n, "bytes": os.path.getsize(_fp),
                           "sha256": _sha256(_fp)})
        except (OSError, IOError) as _e:
            _files.append({"name": _n, "error": "%s: %s" % (type(_e).__name__, _e)})

    _dirs = [_n for _n, _ in iter_tmp_dirs(tmp_dir)]
    _rep = {
        "snapshot": os.path.basename(_dst),
        "created_at": time.time(),
        "created_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "label": label,
        "tmp_dir": _rel(tmp_dir),
        "files": _files,
        "file_count": len(_files),
        "dirs_observed": _dirs,
        "dir_count": len(_dirs),
    }
    with io.open(os.path.join(_dst, MANIFEST), "w", encoding="utf-8") as _f:
        _f.write(json.dumps(_rep, ensure_ascii=False, indent=2))
    return _rep


def list_backups(backup_root: str = BACKUP_ROOT) -> list:
    """列出全部备份（按时间倒序）。"""
    if not os.path.isdir(backup_root):
        return []
    _out = []
    for _n in sorted(os.listdir(backup_root), reverse=True):
        _d = os.path.join(backup_root, _n)
        if not os.path.isdir(_d):
            continue
        _mf = os.path.join(_d, MANIFEST)
        _info = {"snapshot": _n, "path": _d}
        if os.path.isfile(_mf):
            try:
                _j = json.load(io.open(_mf, encoding="utf-8"))
                _info.update({"created_str": _j.get("created_str"),
                              "file_count": _j.get("file_count"),
                              "label": _j.get("label", "")})
            except (ValueError, OSError) as e:
                silent_exc(e, where="tools.tmp_backup::list_backups L165")
        _info["bytes"] = _dir_size(_d)
        _out.append(_info)
    return _out


def _dir_size(path: str) -> int:
    _t = 0
    for _dp, _dns, _fns in os.walk(path):
        for _f in _fns:
            try:
                _t += os.path.getsize(os.path.join(_dp, _f))
            except OSError as e:
                silent_exc(e, where="tools.tmp_backup::_dir_size L178")
    return _t


def prune(keep: int = 5, backup_root: str = BACKUP_ROOT) -> dict:
    """保留最近 keep 份，删除更旧的。"""
    _all = list_backups(backup_root)
    _old = _all[keep:]
    _removed = []
    for _b in _old:
        try:
            shutil.rmtree(_b["path"])
            _removed.append(_b["snapshot"])
        except OSError as _e:
            _removed.append({"snapshot": _b["snapshot"],
                             "error": "%s: %s" % (type(_e).__name__, _e)})
    return {"keep": keep, "total": len(_all), "removed": _removed,
            "remaining": [x["snapshot"] for x in _all[:keep]]}


def restore(snapshot_id: str, tmp_dir: str = TMP_DIR,
            backup_root: str = BACKUP_ROOT, overwrite: bool = True) -> dict:
    """从指定备份恢复文件到 tmp/（默认覆盖）。"""
    _src = os.path.join(backup_root, snapshot_id)
    if not os.path.isdir(_src):
        return {"ok": False, "error": "备份不存在: %s" % snapshot_id}
    _mf = os.path.join(_src, MANIFEST)
    _manifest = None
    if os.path.isfile(_mf):
        try:
            _manifest = json.load(io.open(_mf, encoding="utf-8"))
        except (ValueError, OSError) as e:
            silent_exc(e, where="tools.tmp_backup::restore L210")

    os.makedirs(tmp_dir, exist_ok=True)
    _restored, _skipped, _errors = [], [], []
    for _n in sorted(os.listdir(_src)):
        if _n == MANIFEST:
            continue
        _s = os.path.join(_src, _n)
        if not os.path.isfile(_s):
            continue
        _d = os.path.join(tmp_dir, _n)
        if os.path.isfile(_d) and not overwrite:
            _skipped.append(_n)
            continue
        try:
            shutil.copy2(_s, _d)
            _restored.append({"name": _n, "sha256": _sha256(_d)})
        except (OSError, IOError) as _e:
            _errors.append({"name": _n, "error": "%s: %s" % (type(_e).__name__, _e)})

    # 校验：与 MANIFEST 记录的 sha256 比对
    _mismatch = []
    if _manifest:
        _want = {f["name"]: f.get("sha256") for f in _manifest.get("files", [])}
        for _r in _restored:
            _w = _want.get(_r["name"])
            if _w and _w != _r["sha256"]:
                _mismatch.append(_r["name"])
    return {"ok": not _errors and not _mismatch,
            "snapshot": snapshot_id,
            "restored": [x["name"] for x in _restored],
            "restored_count": len(_restored),
            "skipped": _skipped, "errors": _errors, "sha_mismatch": _mismatch}


# ==================== 测试依赖扫描（P2-307） ====================

#: 项目模块名（tmp 中的同名文件不应被误判为"测试依赖"）
def project_module_names(root: str = ROOT) -> set:
    _names = set()
    for _sub in ("nucleus", "tools", "organs", "functions", "utils",
                 "somatics", "hardware", "base", "tests"):
        _d = os.path.join(root, _sub)
        if not os.path.isdir(_d):
            continue
        for _dp, _dns, _fns in os.walk(_d):
            _dns[:] = [x for x in _dns if x != "__pycache__"]
            for _f in _fns:
                if _f.endswith(".py"):
                    _names.add(_f)
    for _f in os.listdir(root):
        if _f.endswith(".py"):
            _names.add(_f)
    return _names


_SCRIPT_NAME = re.compile(r"^(?:patch|scan|check|update|verify|record|run|"
                          r"backfill|calibrate)_[\w\-]+\.py$")


def scan_test_dependencies(tests_dir: str | None = None,
                           tmp_dir: str = TMP_DIR) -> dict:
    """扫描 tests/ 中对 tmp 文件的依赖，返回应排除清单。

    三类依赖：
      * import   —— ``sys.path.insert(0, tmp)`` + ``import xxx``
      * subprocess —— ``python tmp/xxx.py``
      * open     —— ``open("tmp/xxx")``
    """
    tests_dir = tests_dir or os.path.join(ROOT, "tests")
    _mods = project_module_names()

    _py_names = set()
    if os.path.isdir(tmp_dir):
        _py_names = {n for n in os.listdir(tmp_dir)
                     if n.endswith(".py") and os.path.isfile(os.path.join(tmp_dir, n))}
    #: 模块名（去 .py）—— ``import patch_t2_m9`` 这种**不带后缀**的形态也要能识别
    _stems = {n[:-3]: n for n in _py_names}

    _imports, _subs, _opens = {}, {}, {}
    for _fn in sorted(os.listdir(tests_dir)):
        if not _fn.endswith(".py"):
            continue
        _t = io.open(os.path.join(tests_dir, _fn), encoding="utf-8",
                     errors="replace").read()
        # ★放宽：``"tmp"`` / ``"tmp/..."`` / ``tmp\...`` / ``_TMP`` 均视为引用了 tmp
        _uses_tmp = (bool(re.search(r"""["']tmp[/\\"']""", _t))
                     or bool(re.search(r"""[/\\]tmp[/\\]""", _t))
                     or "_TMP" in _t or "_proj_tmp" in _t)
        if not _uses_tmp:
            continue

        # 候选 A：`"xxx.py"` 字面量（★允许路径分隔符，取 basename ——
        #           ``"tmp/scan_orphan_event_m10.py"`` 这种形态必须能捕获）
        _cands = set()
        for _m in re.finditer(r"""["']([\w\-.\\/]+\.py)["']""", _t):
            _cands.add(os.path.basename(_m.group(1).replace("\\", "/")))
        _cands = {c for c in _cands if c in _py_names and c not in _mods}
        # 候选 B：`import xxx`（无 .py 后缀）→ 反查 tmp 中的同名模块
        _imp_cands = set()
        for _m in re.finditer(r"^\s*import\s+([A-Za-z_]\w*)\s*$", _t, re.M):
            _n = _m.group(1)
            if _n in _stems and _stems[_n] not in _mods:
                _imp_cands.add(_stems[_n])
        # 候选 C：`from xxx import ...`
        for _m in re.finditer(r"^\s*from\s+([A-Za-z_]\w*)\s+import\s", _t, re.M):
            _n = _m.group(1)
            if _n in _stems and _stems[_n] not in _mods:
                _imp_cands.add(_stems[_n])

        _all = _cands | _imp_cands
        if not _all:
            continue
        if _imp_cands or (re.search(r"^\s*import\s+\w+", _t, re.M) and "sys.path" in _t):
            _imports[_fn] = sorted(_all)
        if "subprocess" in _t:
            _subs[_fn] = sorted(_all)
        if re.search(r"""open\s*\(\s*["'][^"']*tmp""", _t) or \
                re.search(r"""["']tmp[/\\][\w\-]+\.py["']""", _t):
            _opens[_fn] = sorted(_all)

    _protected = set()
    for _d in (_imports, _subs, _opens):
        for _v in _d.values():
            _protected.update(_v)
    return {
        "tests_dir": _rel(tests_dir),
        "tmp_dir": _rel(tmp_dir),
        "by_import": _imports,
        "by_subprocess": _subs,
        "by_open": _opens,
        "protected": sorted(_protected),
        "protected_count": len(_protected),
    }


def migrate_legacy(legacy_root: str = LEGACY_BACKUP_ROOT,
                   backup_root: str = BACKUP_ROOT) -> dict:
    """把第46批旧落点 ``.tmp_backup/`` 迁移到 ``.bak_tmp/``。

    ★**移动而非删除**：先校验每个快照的 MANIFEST 可解析，
      再逐个 ``shutil.move``；全部成功后才删除空的旧目录。
      任一环节失败 → 停止并保留现场（不丢数据）。

    Returns:
        ``{moved, skipped, errors, legacy_removed, from, to}``
    """
    _out: dict = {"from": _rel(legacy_root), "to": _rel(backup_root),
                  "moved": [], "skipped": [], "errors": [],
                  "legacy_removed": False}
    if not os.path.isdir(legacy_root):
        _out["skipped"].append("旧目录不存在，无需迁移")
        return _out
    os.makedirs(backup_root, exist_ok=True)

    for _n in sorted(os.listdir(legacy_root)):
        _s = os.path.join(legacy_root, _n)
        if not os.path.isdir(_s):
            continue
        _d = os.path.join(backup_root, _n)
        if os.path.isdir(_d):
            _out["skipped"].append("%s（目标已存在）" % _n)
            continue
        # 校验：MANIFEST 可解析（存在时）
        _mf = os.path.join(_s, MANIFEST)
        if os.path.isfile(_mf):
            try:
                json.load(io.open(_mf, encoding="utf-8"))
            except ValueError as _e:
                _out["errors"].append("%s MANIFEST 损坏: %s" % (_n, _e))
                continue
        try:
            shutil.move(_s, _d)
            _out["moved"].append(_n)
        except (OSError, shutil.Error) as _e:
            _out["errors"].append("%s: %s: %s" % (_n, type(_e).__name__, _e))

    # 仅当全部成功且旧目录为空时才删除
    if not _out["errors"]:
        try:
            _left = [x for x in os.listdir(legacy_root)]
            if not _left:
                os.rmdir(legacy_root)
                _out["legacy_removed"] = True
            else:
                _out["skipped"].append("旧目录仍残留 %d 项，保留" % len(_left))
        except OSError as _e:
            _out["errors"].append("删除旧目录失败: %s" % _e)
    return _out


def is_protected(name: str, deps: dict | None = None) -> bool:
    """该文件是否受保护（被测试依赖 → 清理时必须排除）。"""
    if deps is None:
        deps = scan_test_dependencies()
    return name in deps.get("protected", [])


# ==================== CLI ====================

def main() -> int:
    _ap = argparse.ArgumentParser(description="tmp 备份与测试依赖扫描")
    _sub = _ap.add_subparsers(dest="cmd", required=True)

    _sub.add_parser("snapshot", help="创建 tmp 快照").add_argument(
        "--label", default="", help="快照标签（如 批次号）")
    _sub.add_parser("list", help="列出备份")

    _p = _sub.add_parser("restore", help="恢复指定备份")
    _p.add_argument("snapshot_id")
    _p.add_argument("--no-overwrite", action="store_true")

    _p = _sub.add_parser("prune", help="保留最近 N 份")
    _p.add_argument("--keep", type=int, default=5)

    _p = _sub.add_parser("migrate", help="把旧落点 .tmp_backup 迁移到 .bak_tmp")

    _p = _sub.add_parser("deps", help="扫描测试依赖的 tmp 文件")
    _p.add_argument("--json", default="", help="输出 JSON 路径")

    _ns = _ap.parse_args()

    if _ns.cmd == "migrate":
        _r = migrate_legacy()
        print("[migrate] %s → %s" % (_r["from"], _r["to"]))
        print("   移动 %d 个: %s" % (len(_r["moved"]), _r["moved"]))
        if _r["skipped"]:
            print("   跳过:", _r["skipped"])
        if _r["errors"]:
            print("   ★错误:", _r["errors"])
        print("   旧目录已删除:", _r["legacy_removed"])

    if _ns.cmd == "snapshot":
        _r = snapshot(label=_ns.label)
        print("[snapshot] %s  文件 %d 个  目录 %d 个  →  %s"
              % (_r["snapshot"], _r["file_count"], _r["dir_count"],
                 os.path.join(".tmp_backup", _r["snapshot"])))
    elif _ns.cmd == "list":
        for _b in list_backups():
            print("  %-18s %6.1f KB  文件 %-4s  %s  %s"
                  % (_b["snapshot"], _b["bytes"] / 1024.0,
                     _b.get("file_count", "?"), _b.get("created_str", ""),
                     _b.get("label", "")))
    elif _ns.cmd == "restore":
        _r = restore(_ns.snapshot_id, overwrite=not _ns.no_overwrite)
        print("[restore] ok=%s  恢复 %d 个%s"
              % (_r["ok"], _r.get("restored_count", 0),
                 "  sha不匹配: %s" % _r["sha_mismatch"] if _r["sha_mismatch"] else ""))
        if _r.get("errors"):
            print("   errors:", _r["errors"])
    elif _ns.cmd == "prune":
        _r = prune(keep=_ns.keep)
        print("[prune] 保留 %d 份，删除 %d 份: %s"
              % (_r["keep"], len(_r["removed"]), _r["removed"]))
    elif _ns.cmd == "deps":
        _r = scan_test_dependencies()
        print("[deps] 受保护的 tmp 文件 %d 个:" % _r["protected_count"])
        for _n in _r["protected"]:
            print("   ", _n)
        for _k in ("by_import", "by_subprocess", "by_open"):
            if _r[_k]:
                print("  %s: %s" % (_k, _r[_k]))
        if _ns.json:
            with io.open(_ns.json, "w", encoding="utf-8") as _f:
                _f.write(json.dumps(_r, ensure_ascii=False, indent=2))
            print("  →", _ns.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
