# -*- coding: utf-8 -*-
"""裁决产物版本化导出（360 遗留 · 356 自核查 C1）。

痛点
----
``pending_patches`` / ``applied_355`` / ``rejected_355`` / ``_m356_key_map`` /
``audit_*.json`` 全在 ``data/``，**被 .gitignore 排除**。后果：
  * 「哪条裁决是 FIXED、哪条 WONTFIX」只存在于本机磁盘，换机/清盘即失证；
  * 356B 刀15 对账（7 fixed/21 wontfix/675 open）无法被下批复核，只能信任口头结论。

做法
----
把**裁决类**产物导出一份 git 跟踪副本到
``docs/路灯与星轨对话/交付报告/证据件/裁决产物/``：
  * 小件（<512KB）整份入库；
  * 大件（audit_*.json / merged_rows）只导**摘要**（条目数 + 状态分布 + sha256 + 时间），
    原始档仍在 data/，凭 sha256 可对账——避免把 MB 级运行数据灌进 git。

用法
----
    python tools/export_adjudication_artifacts.py export   # 生成/更新副本 + manifest
    python tools/export_adjudication_artifacts.py check    # 比对副本与源是否漂移（exit≠0 表示有漂移）
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
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

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "裁决产物")

#: (源相对路径, 导出方式) —— full=整份；summary=仅摘要
SOURCES: list[tuple[str, str]] = [
    ("data/patches/pending_patches.json", "full"),
    ("data/patches/applied_355.json", "full"),
    ("data/patches/rejected_355.json", "full"),
    ("data/reports/audit/_m356_key_map.json", "full"),
    ("data/reports/audit/_pool_ledger.json", "full"),
    ("data/reports/audit/audit_20261023.json", "summary"),
    ("data/reports/audit/audit_20261024.json", "summary"),
    ("data/reports/audit/_m356_merged_rows.json", "summary"),
]
_SUMMARY_LIMIT = 512 * 1024


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def _status_counts(obj) -> dict:
    """从最常见的两种形态里抽状态分布：list[dict] 或直接 dict[..rows]。"""
    rows = None
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        for k in ("rows", "issues", "entries", "items"):
            if isinstance(obj.get(k), list):
                rows = obj[k]
                break
    if not isinstance(rows, list):
        return {}
    out: dict[str, int] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        for key in ("status", "closed_reason", "verdict", "gate"):
            if key in r and isinstance(r[key], str):
                out[f"{key}={r[key]}"] = out.get(f"{key}={r[key]}", 0) + 1
                break
    return out


def _summarize(path: str, obj) -> dict:
    n = len(obj) if isinstance(obj, (list, dict)) else 0
    return {
        "_summary": True,
        "source": os.path.relpath(path, _ROOT).replace("\\", "/"),
        "type": type(obj).__name__,
        "size_bytes": os.path.getsize(path),
        "sha256": _sha256(path),
        "entries": n,
        "status_counts": _status_counts(obj),
        "top_keys": list(obj.keys())[:20] if isinstance(obj, dict) else None,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _git_head() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=_ROOT, capture_output=True, text=True, timeout=20)
        return (out.stdout or "").strip()
    except Exception as e:
        # ★纯 return 属静默（铁律176）：git 不可用必须留痕，否则副本「挂哪一笔」无从对账
        silent_exc(e, where="tools/export_adjudication_artifacts::_git_head", level="debug")
        return "unknown"


def export() -> int:
    os.makedirs(_OUT_DIR, exist_ok=True)
    manifest: dict = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_head": _git_head(),
        "policy": "full<512KB 整份入库；大件只导摘要（原始档凭 sha256 对账）",
        "items": [],
    }
    missing = 0
    for rel, kind in SOURCES:
        src = os.path.join(_ROOT, rel)
        item: dict = {"source": rel, "kind": kind, "present": os.path.exists(src)}
        if not os.path.exists(src):
            item["note"] = "源缺失（未导出）"
            missing += 1
            manifest["items"].append(item)
            continue
        size = os.path.getsize(src)
        item.update({"size_bytes": size, "sha256": _sha256(src),
                     "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(os.path.getmtime(src)))})
        obj = None
        if kind == "summary" or size > _SUMMARY_LIMIT:
            try:
                with open(src, encoding="utf-8") as f:
                    obj = json.load(f)
            except Exception as e:
                item["note"] = f"解析失败({type(e).__name__})，仅记 sha256"
                silent_exc(e, where="tools/export_adjudication_artifacts::export", level="debug")
        if obj is not None and (kind == "summary" or size > _SUMMARY_LIMIT):
            payload = _summarize(src, obj)
            item["entries"] = payload.get("entries")
            item["status_counts"] = payload.get("status_counts")
            item["mode"] = "summary"
            out_name = os.path.basename(rel) + ".summary.json"
        else:
            with open(src, "rb") as f:
                payload_bytes = f.read()
            payload = None
            out_name = os.path.basename(rel)
            with open(os.path.join(_OUT_DIR, out_name), "wb") as f:
                f.write(payload_bytes)
            item["mode"] = "full"
        if payload is not None:
            dst = os.path.join(_OUT_DIR, out_name)
            tmp = dst + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, dst)
        item["exported_as"] = out_name
        manifest["items"].append(item)
        print(f"[export] {rel} -> {out_name} ({item.get('mode')}, {size}B)")

    tmp = os.path.join(_OUT_DIR, "manifest.json") + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(_OUT_DIR, "manifest.json"))
    print(f"[export] manifest 已更新；源缺失 {missing} 项")
    return 0


def check() -> int:
    mpath = os.path.join(_OUT_DIR, "manifest.json")
    if not os.path.exists(mpath):
        print("[check] 无 manifest，先跑 export", file=sys.stderr)
        return 2
    with open(mpath, encoding="utf-8") as f:
        mf = json.load(f)
    drift = 0
    for item in mf.get("items", []):
        src = os.path.join(_ROOT, item["source"])
        if not os.path.exists(src):
            print(f"[check] 源缺失 {item['source']}")
            continue
        cur = _sha256(src)
        if cur != item.get("sha256"):
            drift += 1
            print(f"[check] 漂移 {item['source']}: 副本 {str(item.get('sha256'))[:12]} "
                  f"vs 源 {cur[:12]}")
        else:
            print(f"[check] 一致 {item['source']}")
    print(f"[check] 漂移 {drift} 项（manifest 时点 {mf.get('exported_at')}）")
    return 1 if drift else 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    action = sys.argv[1]
    if action == "export":
        return export()
    if action == "check":
        return check()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
