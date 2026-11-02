# -*- coding: utf-8 -*-
"""知识快照「冷档」gzip 压缩 / 还原工具（360 遗留闭合）。

背景
----
354/356 两批都提出「主快照 gzip」（实测 718.6MB → 71.1MB，回载全等），
但**一直没落盘**。360 复核找到真因：

    ``nucleus/mnemosyne/lazy_snapshot.py`` 用 **mmap 随机访问**主快照
    （按节点字节区间惰性 decode），gzip 是流格式、不支持随机定位，
    ⇒ 主档一旦压缩，惰性加载整条链路失效（这是架构级冲突，不是开关问题）。

但**轮转冷档**（``pulse_knowledge_snapshot.json.<ts>.bak``）没有这个约束：
它只在「主档损坏 → ``_load_from_backup()``」时被**整体**读一次（``safe_read_json``）。
因此本工具只压缩冷档，主档**永不碰**（代码里三重保险）。

前置（已随本工具同批落地）
--------------------------
* ``DataAccessLayer.safe_read_json`` 已透明支持 ``.gz`` ⇒ ``_load_from_backup``
  能直接读压缩后的备份（文件名含 ``.bak`` 子串，原有过滤逻辑天然命中）。
* ``PulseMetricsCollector._count_snapshot_backups`` 已把 ``*.bak.gz`` 计入。

用法
----
    python tools/snapshot_cold_gzip.py scan
    python tools/snapshot_cold_gzip.py compress --all            # 压全部冷档
    python tools/snapshot_cold_gzip.py compress --file <path>    # 压单个
    python tools/snapshot_cold_gzip.py restore  --file <path.gz> # 还原（解压回原路径）

安全契约
--------
1. 压缩 = 一遍读（边算原档 sha256 边写 ``.gz.tmp``）→ 解压重算 sha256 比对
   → 比对通过才 ``os.replace`` 转正 → 再删明文（``--keep-plain`` 可保留）。
2. 任一步失败：``.gz.tmp`` 删除，明文**原样保留**，退出码非 0。
3. 主档（无 ``.bak`` 段 / 就是 ``pulse_knowledge_snapshot.json``）直接拒绝。
4. 已存在同名 ``.gz`` 时跳过（幂等）。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import sys
import time

_M360_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _M360_ROOT not in sys.path:
    sys.path.insert(0, _M360_ROOT)
try:
    from nucleus._silent_except import silent_exc
except Exception:  # 兜底：独立脚本在缺包环境下也要留痕，不静默吞掉
    def silent_exc(_e, where: str = "", level: str = "debug") -> None:
        print(f"[silent] {where}: {type(_e).__name__}: {_e}", file=sys.stderr)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KNOWLEDGE_DIR = os.path.join(_PROJECT_ROOT, "data", "knowledge")
#: ★主档白名单：这些文件**永不**压缩（lazy_snapshot 用 mmap 惰性读）
_NEVER_TOUCH = {
    "pulse_knowledge_snapshot.json",
    "pulse_l1_snapshot.json",
    "vectors_meta.json",
    "cold.index.json",
}
_CHUNK = 4 * 1024 * 1024


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def _is_cold_backup(path: str) -> bool:
    """冷档判据：主档名 + 时间戳 + .bak 结尾，且不是主档本身、不是已压缩档。"""
    name = os.path.basename(path)
    if name in _NEVER_TOUCH:
        return False
    if name.endswith(".gz"):
        return False
    if ".bak" not in name:
        return False
    return name.startswith("pulse_knowledge_snapshot.json")


def _sha256(path: str, gz: bool = False) -> tuple[str, int]:
    """流式算 sha256 + 字节数（内存恒定 _CHUNK）。"""
    _h = hashlib.sha256()
    _n = 0
    _opener = gzip.open if gz else open
    with _opener(path, "rb") as f:  # type: ignore[operator]
        while True:
            _b = f.read(_CHUNK)
            if not _b:
                break
            _h.update(_b)
            _n += len(_b)
    return _h.hexdigest(), _n


def scan(directory: str = _KNOWLEDGE_DIR) -> list[dict]:
    out: list[dict] = []
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        p = os.path.join(directory, name)
        if not os.path.isfile(p):
            continue
        if not _is_cold_backup(p):
            continue
        out.append({
            "path": p,
            "name": name,
            "size": os.path.getsize(p),
            "gz_exists": os.path.exists(p + ".gz"),
            "mtime": os.path.getmtime(p),
        })
    return out


def compress(path: str, level: int = 6, keep_plain: bool = False,
             dry_run: bool = False) -> dict:
    """压缩单个冷档。返回 {path, ok, stage, plain_bytes, gz_bytes, ratio, saved, msg}。"""
    res: dict = {"path": path, "ok": False, "stage": "init", "plain_bytes": 0,
                 "gz_bytes": 0, "ratio": 0.0, "saved": 0, "msg": ""}
    if not os.path.isfile(path):
        res["stage"], res["msg"] = "absent", "文件不存在"
        return res
    if not _is_cold_backup(path):
        res["stage"], res["msg"] = "refused", "非冷档（主档/已压缩/非备份）"
        return res
    gz_path = path + ".gz"
    if os.path.exists(gz_path):
        res["stage"], res["msg"] = "skipped", "已存在同名 .gz（幂等跳过）"
        res["ok"] = True
        return res

    res["plain_bytes"] = os.path.getsize(path)
    if dry_run:
        res["stage"], res["msg"] = "dry_run", "仅评估未写盘"
        res["ok"] = True
        return res

    tmp_gz = gz_path + ".tmp"
    t0 = time.time()
    # ---- 一遍读：边算原档 sha256，边写 .gz.tmp ----
    try:
        _h = hashlib.sha256()
        with open(path, "rb") as src, gzip.open(tmp_gz, "wb", compresslevel=level) as dst:
            while True:
                _b = src.read(_CHUNK)
                if not _b:
                    break
                _h.update(_b)
                dst.write(_b)
        plain_sha, plain_n = _h.hexdigest(), res["plain_bytes"]
    except Exception as e:
        res["stage"], res["msg"] = "compress_failed", f"{type(e).__name__}: {e}"
        _silent_remove(tmp_gz)
        return res

    # ---- 校验：解压回读，sha256 必须与明文逐字节一致 ----
    try:
        gz_sha, gz_n = _sha256(tmp_gz, gz=True)
    except Exception as e:
        res["stage"], res["msg"] = "verify_failed", f"{type(e).__name__}: {e}"
        _silent_remove(tmp_gz)
        return res
    if gz_sha != plain_sha or gz_n != plain_n:
        res["stage"] = "verify_mismatch"
        res["msg"] = f"回读不一致 sha {gz_sha[:12]} vs {plain_sha[:12]}, {gz_n} vs {plain_n}"
        _silent_remove(tmp_gz)
        return res

    # ---- 转正 + 删明文 ----
    try:
        os.replace(tmp_gz, gz_path)
    except Exception as e:
        res["stage"], res["msg"] = "replace_failed", f"{type(e).__name__}: {e}"
        _silent_remove(tmp_gz)
        return res
    res["gz_bytes"] = os.path.getsize(gz_path)
    res["ratio"] = (res["gz_bytes"] / res["plain_bytes"]) if res["plain_bytes"] else 0.0
    res["saved"] = res["plain_bytes"] - res["gz_bytes"]
    res["stage"] = "ok"
    res["ok"] = True
    res["msg"] = (f"{_human(res['plain_bytes'])}→{_human(res['gz_bytes'])} "
                  f"(ratio {res['ratio']:.3f}, {time.time() - t0:.1f}s)")
    if not keep_plain:
        try:
            os.remove(path)
            res["msg"] += "，明文已删"
        except Exception as e:
            res["msg"] += f"（明文删除失败：{type(e).__name__}: {e}）"
            silent_exc(e, where="tools/snapshot_cold_gzip::compress(rm_plain)", level="debug")
    else:
        res["msg"] += "，按 --keep-plain 保留明文"
    return res


def restore(gz_path: str) -> dict:
    """把 .gz 还原成明文（回滚通道）。"""
    res: dict = {"path": gz_path, "ok": False, "stage": "init", "msg": ""}
    if not gz_path.endswith(".gz") or not os.path.isfile(gz_path):
        res["stage"], res["msg"] = "absent", "不是 .gz 或文件不存在"
        return res
    plain = gz_path[:-3]
    if os.path.exists(plain):
        res["stage"], res["msg"] = "skipped", "明文档已存在（避免覆盖）"
        res["ok"] = True
        return res
    tmp = plain + ".tmp"
    try:
        with gzip.open(gz_path, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, _CHUNK)
        os.replace(tmp, plain)
    except Exception as e:
        res["stage"], res["msg"] = "restore_failed", f"{type(e).__name__}: {e}"
        _silent_remove(tmp)
        return res
    res["ok"], res["stage"] = True, "ok"
    res["msg"] = f"已还原 {plain}（{_human(os.path.getsize(plain))}）"
    return res


def _silent_remove(p: str) -> None:
    """删临时文件。★函数名带 silent，但**不能真静默**（铁律176）：
    删不掉会让 .tmp 残留污染目录，下一轮 compress 会撞已存在的临时档。"""
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception as e:
        silent_exc(e, where="tools/snapshot_cold_gzip::_silent_remove", level="debug")


def main() -> int:
    ap = argparse.ArgumentParser(description="知识快照冷档 gzip 压缩/还原")
    ap.add_argument("action", choices=("scan", "compress", "restore"))
    ap.add_argument("--file", default="", help="单个目标文件")
    ap.add_argument("--all", action="store_true", help="对全部冷档生效")
    ap.add_argument("--level", type=int, default=6, help="gzip 压缩级别(1-9)")
    ap.add_argument("--keep-plain", action="store_true", help="压缩后保留明文档")
    ap.add_argument("--dry-run", action="store_true", help="只评估不写盘")
    ap.add_argument("--dir", default=_KNOWLEDGE_DIR, help="快照目录")
    args = ap.parse_args()

    if args.action == "scan":
        items = scan(args.dir)
        if not items:
            print("[scan] 无可压缩冷档")
            return 0
        total = 0
        for it in items:
            total += it["size"]
            flag = "（已有.gz）" if it["gz_exists"] else ""
            print(f"[scan] {it['name']}  {_human(it['size'])}{flag}")
        print(f"[scan] 合计 {len(items)} 份 / {_human(total)}")
        return 0

    if args.action == "compress":
        if args.file:
            targets = [args.file]
        elif args.all:
            targets = [it["path"] for it in scan(args.dir) if not it["gz_exists"]]
        else:
            print("[compress] 需指定 --file 或 --all", file=sys.stderr)
            return 2
        if not targets:
            print("[compress] 无待压缩目标（全部已压缩或目录为空）")
            return 0
        ok_n = 0
        saved_total = 0
        for t in targets:
            r = compress(t, level=args.level, keep_plain=args.keep_plain,
                         dry_run=args.dry_run)
            print(f"[compress] {os.path.basename(t)} -> {r['stage']}: {r['msg']}")
            if r["ok"]:
                ok_n += 1
                saved_total += r.get("saved", 0)
        print(f"[compress] 成功 {ok_n}/{len(targets)}，"
              f"释放 {_human(saved_total)}（dry_run={args.dry_run}）")
        return 0 if ok_n == len(targets) else 1

    if args.action == "restore":
        if not args.file:
            print("[restore] 需指定 --file <xxx.gz>", file=sys.stderr)
            return 2
        r = restore(args.file)
        print(f"[restore] {r['stage']}: {r['msg']}")
        return 0 if r["ok"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
