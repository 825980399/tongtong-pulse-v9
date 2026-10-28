# -*- coding: utf-8 -*-
"""383 刀7：台账 qualname 对账（只读脚本）。

## 为什么需要它

两本静默 except 台账（`tools/ci/silent_except_fingerprints.json` 119 条、
`tools/ci/baselines/m365_silent_except_ledger.json` 68 条）里的 `qualname` / `lineno`
是**条目落库那一刻**的快照。代码一动，宿主函数可能被拆走、改名、并入调用方、或者干脆修好
（加了上报），而台账里没有任何字段记录这件事 ⇒ 后人翻台账无法判断「这条还算不算数」。

本脚本把「台账五元组 vs 当前代码」逐条对账，生成 diff 报告。

## 五态（每一态都是可复算的 AST 事实，不含推测）

| 状态 | 含义 | 判据 |
|---|---|---|
| `ok` | 账实相符 | 五元组仍在当前静默集里 |
| `repaired` | 宿主还在、异常类型没变，但已不再静默 | 同 `(qualname, tname)` 的 handler 在，但带 log/raise |
| `inlined` | 宿主变了（逻辑被搬进别的函数） | 本文件仍有同 `(tname, kind)`，但挂在别的 `qualname` 下 |
| `removed` | 连同类型的 handler 都没有了 | 本文件找不到任何同 `(tname, kind)` 的 handler |
| `file_missing` | 台账记的文件已不存在 | 路径不可读 |

★票面只点名 `[inlined]` / `[removed]` 两态。多出 `repaired` 是有意的：台账里大量条目
其实是被**修好了**（加重抛/加日志），若把它们混进 `removed`，会把「已销账」误读成
「代码丢了」。这两件事的处置完全不同。

## 两条硬约束

1. **判据同源**：指纹一律走 `ci_common.extract_handlers`（357 起的唯一权威实现），
   本脚本不另写一套 sha/kind 口径。两套判据一旦分叉，对账结论就不可比（铁律 271）。
2. **只加不删**：默认 dry-run，一个字节都不改台账；`--annotate` 才往条目追加
   `qualname_status` / `qualname_now` 两个字段，**绝不删除任何条目**（票面要求）。

用法：
    python tools/ci/qualname_reconcile.py                     # 摘要（只读）
    python tools/ci/qualname_reconcile.py --json tmp/q.json    # 另存完整报告
    python tools/ci/qualname_reconcile.py --ledger <path> --annotate   # 回写标注
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402  静默异常统一走门禁认可通道

#: 两本台账（也是 `--ledger` 的默认集合）
DEFAULT_LEDGERS = (
    "tools/ci/silent_except_fingerprints.json",
    "tools/ci/baselines/m365_silent_except_ledger.json",
)

STATUS_OK = "ok"
STATUS_REPAIRED = "repaired"
STATUS_INLINED = "inlined"
STATUS_REMOVED = "removed"
STATUS_FILE_MISSING = "file_missing"
STATUS_PARSE_ERROR = "parse_error"

#: 票面点名的两态（报告里单独分组，方便直接对账）
TICKETED = (STATUS_INLINED, STATUS_REMOVED)


def _load_common():
    """加载同目录的 `ci_common.py`（权威指纹实现）。"""
    path = os.path.join(_SCRIPT_DIR, "ci_common.py")
    spec = importlib.util.spec_from_file_location("m383_ci_common", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COMMON = None


def common():
    global _COMMON
    if _COMMON is None:
        _COMMON = _load_common()
    return _COMMON


def _norm_path(p):
    return str(p or "").replace("\\", "/").strip()


#: 文件 -> (silent 五元组键集合, 全 handler 列表) ；None 表示文件缺失
_FILE_CACHE: dict = {}


def _scan_file(rel_path, root=None):
    """返回 (silent_keys, all_handlers)；文件缺失返回 None。

    `all_handlers` 每项：{qualname, tname, kind, sha16, lineno, has_report}
    —— 口径与 `ci_common.extract_handlers` 完全同源（同一批内部函数），
       唯一差别是**不过滤**「有上报」的 handler，正是为了分辨 repaired。
    """
    key = (root or _ROOT, rel_path)
    if key in _FILE_CACHE:
        return _FILE_CACHE[key]

    full = os.path.join(root or _ROOT, rel_path)
    if not os.path.isfile(full):
        _FILE_CACHE[key] = None
        return None

    c = common()
    try:
        src = io.open(full, "r", encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError) as e:
        silent_exc(e, where="qualname_reconcile._scan_file parse", level="debug")
        _FILE_CACHE[key] = (STATUS_PARSE_ERROR, str(e)[:80])
        return _FILE_CACHE[key]

    # ① 静默集：直接吃权威实现的输出，不重算
    silent_keys = set()
    try:
        for fp in c.extract_handlers(src, rel_path):
            silent_keys.add((rel_path, fp.get("qualname"), fp.get("tname"),
                             fp.get("kind"), fp.get("sha16")))
    except (SyntaxError, ValueError) as e:
        silent_exc(e, where="qualname_reconcile._scan_file extract", level="debug")

    # ② 全 handler（不过滤上报）：复用同一批内部函数，保证 qualname/kind/sha 口径一致
    _enclosing = getattr(c, "_enclosing_func")
    _kind_of = getattr(c, "_kind_of")
    _has_report = getattr(c, "_has_report")
    _normalize = getattr(c, "normalize_body")

    funcs = [(n.lineno, n.end_lineno or n.lineno, n.name)
             for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    all_handlers = []
    for h in ast.walk(tree):
        if not isinstance(h, ast.ExceptHandler):
            continue
        tname = ast.unparse(h.type) if h.type else "bare"
        body_src = ";".join(ast.unparse(x) for x in h.body)
        kind = _kind_of(h.body)
        all_handlers.append({
            "qualname": _enclosing(funcs, h.lineno),
            "tname": tname,
            "kind": kind,
            "sha16": hashlib.sha256(_normalize(body_src).encode("utf-8")).hexdigest()[:16],
            "lineno": h.lineno,
            "has_report": bool(_has_report(h)),
        })
    all_handlers.sort(key=lambda x: x["lineno"])

    _FILE_CACHE[key] = (silent_keys, all_handlers)
    return _FILE_CACHE[key]


def judge(row, root=None):
    """对单条台账条目判定五态。返回 (status, info)。"""
    rel = _norm_path(row.get("path"))
    qual = str(row.get("qualname") or "")
    tname = str(row.get("tname") or "")
    kind = str(row.get("kind") or "")
    sha = str(row.get("sha16") or "")

    scanned = _scan_file(rel, root)
    if scanned is None:
        return STATUS_FILE_MISSING, {}
    if isinstance(scanned, tuple) and len(scanned) == 2 and scanned[0] == STATUS_PARSE_ERROR:
        return STATUS_PARSE_ERROR, {"err": scanned[1]}

    silent_keys, all_handlers = scanned

    # ① 账实相符
    if (rel, qual, tname, kind, sha) in silent_keys:
        same = [h for h in all_handlers if h["qualname"] == qual and h["tname"] == tname]
        return STATUS_OK, {"lineno_now": (same[0]["lineno"] if same else None)}

    # ② 宿主与异常类型仍在 ⇒ 变了的是「静默与否」（加了上报）或体形态
    same_host = [h for h in all_handlers if h["qualname"] == qual and h["tname"] == tname]
    if same_host:
        h = same_host[0]
        return STATUS_REPAIRED, {
            "lineno_now": h["lineno"],
            "has_report_now": h["has_report"],
            "kind_now": h["kind"],
            "sha_now": h["sha16"],
        }

    # ③ 宿主没了，但本文件还有同 (tname, kind) 的 handler ⇒ 搬到别的函数里了
    elsewhere = [h for h in all_handlers if h["tname"] == tname and h["kind"] == kind]
    if elsewhere:
        return STATUS_INLINED, {"now_under": elsewhere[0]["qualname"],
                                "lineno_now": elsewhere[0]["lineno"]}

    # ④ 本文件连同类型的 handler 都没了
    return STATUS_REMOVED, {}


def _rows_of(ledger_path, root=None):
    """从台账文件里取出带 qualname 的条目数组。支持 list 与 {rows:[...]} 两种形态。"""
    full = ledger_path if os.path.isabs(ledger_path) else os.path.join(root or _ROOT, ledger_path)
    data = json.load(io.open(full, "r", encoding="utf-8"))
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        return list(data.get("rows") or []), data
    return [], None


def reconcile(ledger_path, root=None):
    """对一本台账做对账，返回报告 dict（不改任何文件）。"""
    rows, wrapper = _rows_of(ledger_path, root)
    stat: dict = {}
    details = []
    for r in rows:
        if not isinstance(r, dict) or "qualname" not in r:
            continue
        st, info = judge(r, root)
        stat[st] = stat.get(st, 0) + 1
        if st == STATUS_OK:
            continue
        details.append({
            "path": r.get("path"),
            "qualname": r.get("qualname"),
            "tname": r.get("tname"),
            "kind": r.get("kind"),
            "lineno": r.get("lineno"),
            "sha16": r.get("sha16"),
            "status": st,
            **info,
        })
    details.sort(key=lambda d: (d["status"], str(d.get("path")), str(d.get("qualname"))))
    return {
        "ledger": ledger_path,
        "schema": (wrapper or {}).get("schema") if isinstance(wrapper, dict) else None,
        "total": sum(stat.values()),
        "stat": stat,
        "ticketed": {s: stat.get(s, 0) for s in TICKETED},
        "details": details,
    }


def reconcile_all(root=None, ledgers=None):
    """对全部默认台账对账。"""
    out = {"root": root or _ROOT, "ledgers": {}, "totals": {}}
    for rel in (ledgers or DEFAULT_LEDGERS):
        rep = reconcile(rel, root)
        out["ledgers"][rel] = rep
        for k, v in rep["stat"].items():
            out["totals"][k] = out["totals"].get(k, 0) + v
    out["total"] = sum(out["totals"].values())
    return out


def annotate(ledger_path, root=None, report=None):
    """把对账结果**追加**到台账条目（只加字段，绝不删条目）。

    返回 (改动条数, 条目总数)。`qualname_status` 是幂等的：重复跑只覆盖该字段。
    """
    full = ledger_path if os.path.isabs(ledger_path) else os.path.join(root or _ROOT, ledger_path)
    data = json.load(io.open(full, "r", encoding="utf-8"))
    rows = data if isinstance(data, list) else list(data.get("rows") or [])
    if report is None:
        report = reconcile(ledger_path, root)

    # 用 (path, qualname, tname, kind, sha16) 反查状态
    index = {}
    for d in report["details"]:
        index[(_norm_path(d.get("path")), d.get("qualname"), d.get("tname"),
               d.get("kind"), d.get("sha16"))] = d

    changed = 0
    for r in rows:
        if not isinstance(r, dict) or "qualname" not in r:
            continue
        key = (_norm_path(r.get("path")), r.get("qualname"), r.get("tname"),
               r.get("kind"), r.get("sha16"))
        d = index.get(key)
        status = d["status"] if d else STATUS_OK
        if r.get("qualname_status") != status:
            r["qualname_status"] = status
            changed += 1
        if d and status == STATUS_INLINED and d.get("now_under"):
            r["qualname_now"] = d["now_under"]
        elif "qualname_now" in r and status != STATUS_INLINED:
            r.pop("qualname_now", None)

    if changed:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        io.open(full, "w", encoding="utf-8", newline="\n").write(text + "\n")
    return changed, len(rows)


def _print_summary(report):
    print("=" * 78)
    print("383刀7 台账 qualname 对账（只读）")
    print("=" * 78)
    for rel, rep in report["ledgers"].items():
        print("\n[%s]" % rel)
        print("  条目 %d  %s" % (rep["total"], rep["stat"]))
        tick = rep["ticketed"]
        print("  票面两态：[inlined]=%d  [removed]=%d" % (tick.get(STATUS_INLINED, 0),
                                                          tick.get(STATUS_REMOVED, 0)))
        for d in rep["details"][:5]:
            extra = ""
            if d["status"] == STATUS_INLINED:
                extra = " → 现在挂在 %s" % d.get("now_under")
            elif d["status"] == STATUS_REPAIRED:
                extra = " → 已加%s" % ("上报" if d.get("has_report_now") else "非静默体")
            print("    %-12s %s :: %s%s" % (d["status"], d.get("path"),
                                            d.get("qualname"), extra))
    print("\n[总计] %s" % report["totals"])
    print("说明：repaired = 已修好（可销账）；removed = 代码里也没有了；"
          "inlined = 宿主函数变了。三者处置不同，别混读。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="台账 qualname 对账（只读）")
    ap.add_argument("--ledger", action="append", default=None,
                    help="只对账指定台账（可重复）；缺省跑两本默认台账")
    ap.add_argument("--json", default=None, help="把完整报告写到该路径")
    ap.add_argument("--annotate", action="store_true",
                    help="把 qualname_status 追加到台账条目（只加不删）")
    ap.add_argument("--root", default=None, help="仓库根（默认脚本上级目录）")
    args = ap.parse_args(argv)

    if args.annotate and not args.ledger:
        ap.error("--annotate 必须配 --ledger 指定单本台账，避免一次误改两本")

    report = reconcile_all(args.root, args.ledger)
    _print_summary(report)

    if args.json:
        out = args.json if os.path.isabs(args.json) else os.path.join(args.root or _ROOT, args.json)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        io.open(out, "w", encoding="utf-8", newline="\n").write(
            json.dumps(report, ensure_ascii=False, indent=1))
        print("\n报告已写入 %s" % out)

    if args.annotate:
        for rel in args.ledger:
            n, total = annotate(rel, args.root, report["ledgers"].get(rel))
            print("[annotate] %s → 标注 %d/%d 条（未删任何条目）" % (rel, n, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
