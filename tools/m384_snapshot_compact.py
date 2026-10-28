# -*- coding: utf-8 -*-
"""384 刀3：知识快照冷备压缩（.bak → .bak.gz，热档保持纯 JSON）。

## 为什么热档不能压

`lazy_snapshot` 走 mmap 直接映射主文件（`pulse_knowledge_snapshot.json`），
压缩后 mmap 就不可用 ⇒ **只压 `.bak` 冷备，主文件一个字节不动**。

## 安全顺序（每步过了才走下一步）

1. `gzip` 压缩到 `<原>.gz`（不覆盖、不删原文件）
2. **流式验证**：解压字节数 == 原文件字节数、首字节 `{`、末字节 `}`
3. 验证通过 ⇒ 删原 `.bak`；**不通过 ⇒ 删坏 gz、保留原 .bak、立即中止**
4. 按 mtime 倒序保留最新 `--keep` 个备份（与 `PulseSnapshot._cleanup_old_backups`
   同款判据：文件名含 `.bak`，按 mtime 倒序，取前 N 个留下）
5. 打印压缩前后大小对比

## 纪律

· 没有 `--apply` 时**只打印计划**，不动任何文件
· 任何一个文件压缩失败 ⇒ 整体中止，已成功的不回滚（它们已经验证过了）
· 不动 `evidence_chain_freeze_*` 目录（冻结证据，语义不同）

用法：
    python tools/m384_snapshot_compact.py                  # dry-run
    python tools/m384_snapshot_compact.py --apply          # 执行
    python tools/m384_snapshot_compact.py --apply --keep 1 # 显式指定保留份数
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

KDIR = os.path.join(_ROOT, "data", "knowledge")
BASE = "pulse_knowledge_snapshot.json"
CHUNK = 4 << 20

#: 与 `PulseSnapshot._cleanup_old_backups` 同款判据（文件名含 `.bak`，mtime 倒序）
def list_backups(kdir: str = KDIR, base: str = BASE) -> list:
    out = []
    for _n in os.listdir(kdir):
        if _n.startswith(base) and ".bak" in _n:
            _p = os.path.join(kdir, _n)
            if os.path.isfile(_p):
                out.append((os.path.getmtime(_p), _p))
    out.sort(reverse=True)
    return out


def gz_path_of(p: str) -> str:
    return p if p.endswith(".gz") else p + ".gz"


def compress_one(src: str) -> dict:
    """压一个文件并验证。返回 {ok, gz, src_size, gz_size, why}。"""
    dst = gz_path_of(src)
    r = {"src": os.path.basename(src), "gz": os.path.basename(dst),
         "src_size": os.path.getsize(src), "ok": False, "why": "", "gz_size": 0}

    if os.path.exists(dst):
        r["why"] = "目标已存在，拒绝覆盖"
        return r

    try:
        with io.open(src, "rb") as _fi, gzip.open(dst, "wb", compresslevel=6) as _fo:
            shutil.copyfileobj(_fi, _fo, CHUNK)
        # 保留原 mtime（轮转按 mtime 排序，不保留会让顺序乱）
        _st = os.stat(src)
        os.utime(dst, (_st.st_atime, _st.st_mtime))
    except OSError as _e:
        silent_exc(_e, where="m384_snapshot_compact.compress_one", level="warning")
        r["why"] = "压缩失败: %s" % _e
        return r

    r["gz_size"] = os.path.getsize(dst)
    # ---- 流式验证：字节数 + 首尾字符 ----
    _n = 0
    _first = b""
    _tail = b""
    try:
        with gzip.open(dst, "rb") as _g:
            while True:
                _b = _g.read(CHUNK)
                if not _b:
                    break
                if not _first:
                    _first = _b[:1]
                _n += len(_b)
                _tail = (_tail + _b)[-16:]
    except OSError as _e:
        silent_exc(_e, where="m384_snapshot_compact.verify", level="warning")
        r["why"] = "解压失败: %s" % _e
        return r

    if _n != r["src_size"]:
        r["why"] = "解压字节数 %d != 原文件 %d" % (_n, r["src_size"])
        return r
    if _first != b"{":
        r["why"] = "首字节不是 { （实际 %r）" % _first
        return r
    if not _tail.strip().endswith(b"}"):
        r["why"] = "末字节不是 } （实际尾部 %r）" % _tail[-8:]
        return r

    r["ok"] = True
    return r


def main(argv=None) -> int:
    _ap = argparse.ArgumentParser(description="384刀3 快照冷备压缩（.bak → .bak.gz）")
    _ap.add_argument("--apply", action="store_true", help="真压（默认只打印计划）")
    _ap.add_argument("--keep", type=int, default=None,
                     help="压缩后保留几份冷备（默认读 config.KNOWLEDGE_BACKUP_KEEP）")
    _ap.add_argument("--dir", default=KDIR)
    _args = _ap.parse_args(argv)

    if not os.path.isdir(_args.dir):
        print("[错误] 目录不存在: %s" % _args.dir)
        return 2

    _keep = _args.keep
    if _keep is None:
        try:
            import config as _c
            _keep = int(getattr(_c, "KNOWLEDGE_BACKUP_KEEP", 1) or 1)
        except (ImportError, TypeError, ValueError) as _e:
            silent_exc(_e, where="m384_snapshot_compact.keep", level="debug")
            _keep = 1
    _keep = max(1, int(_keep))

    _bk = list_backups(_args.dir)
    _plain = [(m, p) for m, p in _bk if not p.endswith(".gz")]
    _gz = [(m, p) for m, p in _bk if p.endswith(".gz")]

    print("=" * 78)
    print("384刀3 快照冷备压缩（%s）" % ("APPLY" if _args.apply else "DRY-RUN"))
    print("=" * 78)
    print("目录：%s" % _args.dir)
    print("保留份数（config.KNOWLEDGE_BACKUP_KEEP）= %d" % _keep)
    print("现有 .bak（未压缩）：%d 个｜.bak.gz：%d 个" % (len(_plain), len(_gz)))
    for _m, _p in _bk:
        print("  %8.1f MB  %s" % (os.path.getsize(_p) / 1048576.0, os.path.basename(_p)))

    if not _plain:
        print("\n没有待压缩的 .bak（已全部压缩过或无备份）。")
        return 0

    if not _args.apply:
        _tot = sum(os.path.getsize(p) for _m, p in _plain)
        print("\n[dry-run] 将压缩 %d 个文件，合计 %.1f MB；预计压后约 %.0f-%.0f MB。"
              % (len(_plain), _tot / 1048576.0,
                 _tot / 1048576.0 * 0.06, _tot / 1048576.0 * 0.12))
        print("           加 --apply 才执行（每个文件压后立刻验证，通过才删原文件）。")
        return 0

    # ---- 压缩 + 验证 + 删原文件 ----
    _rows = []
    for _m, _p in sorted(_plain, key=lambda x: x[0], reverse=True):
        _r = compress_one(_p)
        _rows.append(_r)
        print("\n[压缩] %s" % _r["src"])
        print("       %.1f MB → %.1f MB（%.1f%%）"
              % (_r["src_size"] / 1048576.0, _r["gz_size"] / 1048576.0,
                 (_r["gz_size"] * 100.0 / _r["src_size"]) if _r["src_size"] else 0))
        if not _r["ok"]:
            print("       **验证失败**：%s ⇒ 保留原文件，删除坏 gz，中止后续" % _r["why"])
            _bad = gz_path_of(_p)
            if os.path.exists(_bad):
                try:
                    os.remove(_bad)
                except OSError as _e:
                    silent_exc(_e, where="m384_snapshot_compact.remove_bad", level="warning")
            break
        os.remove(_p)
        print("       验证通过（解压字节数一致 / 首尾花括号正确）⇒ 已删原 .bak")

    # ---- 按保留份数裁剪 ----
    _after = list_backups(_args.dir)
    _drop = _after[_keep:]
    if _drop:
        print("\n[轮转] 保留最新 %d 份，将删除 %d 份更旧的：" % (_keep, len(_drop)))
        for _m, _p in _drop:
            print("  - %8.1f MB  %s" % (os.path.getsize(_p) / 1048576.0, os.path.basename(_p)))
            try:
                os.remove(_p)
            except OSError as _e:
                silent_exc(_e, where="m384_snapshot_compact.rotate", level="warning")
                print("     删除失败（已跳过）：%s" % _e)

    # ---- 汇总 ----
    print("\n" + "=" * 78)
    print("压缩结果")
    print("=" * 78)
    _s0 = sum(r["src_size"] for r in _rows if r["ok"])
    _s1 = sum(r["gz_size"] for r in _rows if r["ok"])
    print("已压 %d 个：%.1f MB → %.1f MB，省 %.1f MB（压缩率 %.1f%%）"
          % (sum(1 for r in _rows if r["ok"]), _s0 / 1048576.0, _s1 / 1048576.0,
             (_s0 - _s1) / 1048576.0, (_s1 * 100.0 / _s0) if _s0 else 0))
    print("当前备份清单：")
    for _m, _p in list_backups(_args.dir):
        print("  %8.1f MB  %s" % (os.path.getsize(_p) / 1048576.0, os.path.basename(_p)))
    _okall = all(r["ok"] for r in _rows)
    return 0 if _okall else 5


if __name__ == "__main__":
    sys.exit(main())
