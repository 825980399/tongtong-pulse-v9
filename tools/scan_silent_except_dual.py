# -*- coding: utf-8 -*-
"""364 A5 前置：CI 静默 except 门禁「新旧判据」并行全量对比（只出证据，不切判据）。

背景：363 刀 D4 / 364 刀 A5 要求把门禁指纹从四元组升级为五元组
`(path, qualname, nth, tname, kind, body_sha16)`，并补齐 continue/break 形态。
切换会改写全部已知豁免（40 条）的 identity ⇒ 任务书要求「先跑新旧判据全量对比
验证窗口，差异表落证据件后再切判据」。本工具即该验证窗口的静态实现：

  - 旧判据：本文件 extract_handlers_legacy（**371A5 起的冻结副本**）。
    ★原因：ci_common.extract_handlers 已于 371A5 第 2 笔升级为五元组并纳入 cont/break，
      若仍直接 import，本工具的「新旧对比」会退化成「新 vs 新」而失去意义。
  - 新判据：本文件 extract_handlers_v2（追加 cont/break 形态 + kind 字段 + tname 入 identity）

对比口径：
  - 同一 handler 的可比键 = (path, qualname, nth, sha16)（与 tname/kind 无关）
  - added   = 新判据命中、旧判据未命中（★真正的新增拦截，即 continue/break 形态漏网）
  - removed = 旧判据命中、新判据未命中（应为 0，非 0 即新判据实现有偏差）
  - baseline_migration = 40 条已知豁免按 (path, qualname, nth, lineno) 在新判据下重定位，
    给出「旧四元组 → 新五元组」的一一映射，供切换时机械重写基线。

tier（A/B/C）为**启发式建议档**，不是裁决结论：
  - A 必改：qualname 命中闭环数据轮转/写盘/账本关键词
  - B 可豁免：可选依赖类捕获（ImportError/ModuleNotFoundError）或配置读取兜底
  - C 合理静默：其余，需人工补元数据（理由/责任人/批次）

用法：
  python -X utf8 tools/scan_silent_except_dual.py [--scope prod|full]
                                                  [--out-json PATH] [--out-md PATH]
"""
import argparse
import ast
import hashlib
import json
import logging
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))  # _HERE=tools/ ⇒ 上一级即仓库根
sys.path.insert(0, os.path.join(_HERE, "ci"))  # ci_common 位于 tools/ci/
from ci_common import (  # noqa: E402  —— 旧判据（权威）
    _enclosing_func,
    _has_report,
    _quiet_body,
    handler_identity,
    normalize_body,
)

BASELINE = os.path.join(_HERE, "ci", "silent_except_fingerprints.json")

_LOG = logging.getLogger("ci.dual_judge")

# 生产面（任务书口径）：config.py / main.py / organs/** / nucleus/**
PROD_ROOTS = ("config.py", "main.py", "organs/", "nucleus/")
EXCL_PREFIX = ("tests/", "tmp/", "data/", ".bak")

ROTATION_KEYS = (
    "save", "persist", "write", "ledger", "rotate", "flush", "commit",
    "enqueue", "append", "dump", "record", "snapshot", "cleanup", "release",
    "queue", "rotate_log", "checkpoint",
)
OPTIONAL_DEP_TNAMES = ("ImportError", "ModuleNotFoundError")
CFG_HINT_KEYS = ("cfg", "config", "setting", "default", "feature", "flag")
# 「控制流型」捕获：循环轮询/超时等待的标准写法（如 `except queue.Empty: continue`），
# 语义上是控制流而非吞异常，不应判违规。仅作统计提示，是否豁免由裁决决定。
CONTROL_FLOW_TNAMES = ("queue.Empty", "Empty", "TimeoutError", "asyncio.TimeoutError", "TimeoutExpired")


def _kind_of(body):
    """判定 handler 体形态；返回 None 表示「不算静默」。

    kind ∈ pass / ret / assign / cont / break（前三类＝旧判据既有，后两类＝A5 新增）。
    """
    n = len(body)
    if n == 0:
        return None
    if all(_quiet_body(x) for x in body):
        return "pass"
    rets = [x for x in body if isinstance(x, ast.Return)]
    asg = [x for x in body if isinstance(x, (ast.Assign, ast.AugAssign, ast.AnnAssign))]
    conts = [x for x in body if isinstance(x, ast.Continue)]
    brks = [x for x in body if isinstance(x, ast.Break)]
    # —— 旧判据既有形态 ——
    if rets and len(rets) == n:
        return "ret"
    if rets and len(rets) + len(asg) == n:
        return "ret"
    if asg and len(asg) == n:
        return "assign"
    # —— A5 新增形态：continue / break（含与 assign 混合覆盖全部）——
    if conts and (len(conts) == n or len(conts) + len(asg) == n):
        return "cont"
    if brks and (len(brks) == n or len(brks) + len(asg) == n):
        return "break"
    return None


def _tier_of(fp):
    """启发式建议档（A/B/C），仅供裁决参考，非终裁。"""
    q = (fp.get("qualname") or "").lower()
    kind = fp.get("kind")
    tname = fp.get("tname")
    if any(k in q for k in ROTATION_KEYS):
        return "A"
    if tname in OPTIONAL_DEP_TNAMES:
        return "B"
    if kind in ("pass", "assign", "ret") and any(k in q for k in CFG_HINT_KEYS):
        return "B"
    return "C"


def extract_handlers_legacy(src, path="<unknown>"):
    """★371A5 冻结副本：切换前的旧判据（四元组、只认 pass/ret/assign）。

    与 `ci_common.extract_handlers`（现已升级为五元组 + cont/break）解耦，
    使本工具的新旧对比长期可复跑、口径不随主门禁漂移。
    ★生产代码与门禁**不再调用**本函数，仅供对比与历史复核。
    """
    out = []
    tree = ast.parse(src)
    funcs = [(n.lineno, n.end_lineno or n.lineno, n.name)
             for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    handlers.sort(key=lambda n: n.lineno)
    scope_count = {}
    for h in handlers:
        if _has_report(h):
            continue
        qualname = _enclosing_func(funcs, h.lineno)
        nth = scope_count.get(qualname, 0)
        scope_count[qualname] = nth + 1
        body = h.body
        allquiet = all(_quiet_body(x) for x in body)
        if not allquiet:
            rets = [x for x in body if isinstance(x, ast.Return)]
            asg = [x for x in body if isinstance(x, (ast.Assign, ast.AugAssign))]
            if not (len(rets) == len(body) or len(rets) + len(asg) == len(body)):
                continue
        tname = ast.unparse(h.type) if h.type else "bare"
        body_src = ";".join(ast.unparse(x) for x in body)
        out.append({
            "path": path.replace("\\", "/"),
            "qualname": qualname,
            "nth": nth,
            "sha16": hashlib.sha256(normalize_body(body_src).encode("utf-8")).hexdigest()[:16],
            "tname": tname,
            "has_log": False,
            "has_reraise": False,
            # 旧判据没有 kind 概念；统一记 pass，仅为了让指纹形状与新版一致
            "kind": "pass",
            "lineno": h.lineno,
        })
    return out


def extract_handlers_v2(src, path="<unknown>"):
    """新判据：与旧判据同过滤口径（_has_report / nth 计数一致），追加 cont/break。"""
    out = []
    tree = ast.parse(src)
    funcs = [(n.lineno, n.end_lineno or n.lineno, n.name)
             for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    handlers.sort(key=lambda n: n.lineno)
    scope_count = {}
    for h in handlers:
        if _has_report(h):
            continue
        qualname = _enclosing_func(funcs, h.lineno)
        nth = scope_count.get(qualname, 0)
        scope_count[qualname] = nth + 1
        kind = _kind_of(h.body)
        if kind is None:
            continue
        tname = ast.unparse(h.type) if h.type else "bare"
        body_src = ";".join(ast.unparse(x) for x in h.body)
        fp = {
            "path": path.replace("\\", "/"),
            "qualname": qualname,
            "nth": nth,
            "tname": tname,
            "kind": kind,
            "sha16": hashlib.sha256(normalize_body(body_src).encode("utf-8")).hexdigest()[:16],
            "lineno": h.lineno,
            "body": normalize_body(body_src)[:120],
        }
        fp["tier"] = _tier_of(fp)
        out.append(fp)
    return out


def _v2_identity(fp):
    return (fp["path"], fp["qualname"], fp["nth"], fp["tname"], fp["kind"], fp["sha16"])


def _cmp_key(fp):
    """跨判据可比键（不含 tname/kind）：同一 handler 的两副面孔。"""
    return (fp["path"], fp["qualname"], fp["nth"], fp["sha16"])


def git_ls_files_py():
    r = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    return [x for x in r.stdout.splitlines() if x.strip()]


def in_prod(rel):
    p = rel.replace("\\", "/")
    if p.startswith(EXCL_PREFIX) or "/." in p:
        return False
    return p in ("config.py", "main.py") or p.startswith(("organs/", "nucleus/"))


def read_src(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def scan(scope="prod"):
    files = git_ls_files_py()
    if scope == "prod":
        files = [f for f in files if in_prod(f)]
    old, new, parse_err = [], [], []
    for rel in files:
        src = read_src(rel)
        if not src:
            continue
        try:
            old.extend(extract_handlers_legacy(src, rel))
        except SyntaxError as _e:
            parse_err.append((rel, "legacy", str(_e)))
            _LOG.warning("旧判据解析失败 %s: %s", rel, _e)
        try:
            new.extend(extract_handlers_v2(src, rel))
        except SyntaxError as _e:
            parse_err.append((rel, "v2", str(_e)))
            _LOG.warning("新判据解析失败 %s: %s", rel, _e)
    return files, old, new, parse_err


def load_baseline():
    if not os.path.exists(BASELINE):
        _LOG.error("缺失豁免基线 %s", BASELINE)
        return []
    with open(BASELINE, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="prod", choices=["prod", "full"])
    ap.add_argument("--out-json", default="")
    ap.add_argument("--out-md", default="")
    args = ap.parse_args()

    files, old, new, parse_err = scan(args.scope)
    new_keys = set(_cmp_key(x) for x in new)
    # ★371A5：added/removed 一律用「可比键」_cmp_key（四元组 path,qualname,nth,sha16）。
    #   曾误用 handler_identity —— 它现已升为五元组（含 tname+kind、去 nth），
    #   4 元 tuple 与 5 元 tuple 永不相等 ⇒ added 会被算成「全部 new」。口径必须分开：
    #   「跨判据可比」用 _cmp_key，「同一判据内身份」才用 handler_identity。
    old_cmp = set(_cmp_key(x) for x in old)

    added = [x for x in new if _cmp_key(x) not in old_cmp]
    removed = [x for x in old if _cmp_key(x) not in new_keys]

    # —— 40 条已知豁免的迁移映射：按 (path, qualname, nth, sha16) 重定位 ——
    #    ★行号不做匹配键（基线 lineno 为历史快照，现仓已漂移；sha16=body 归一化 hash 稳定）
    base = load_baseline()
    new_by_key = {}
    for x in new:
        new_by_key.setdefault(_cmp_key(x), []).append(x)
    migration = []
    for b in base:
        key = (b["path"], b["qualname"], b["nth"], b["sha16"])
        cand = new_by_key.get(key, [])
        if len(cand) == 1:
            v2 = cand[0]
            migration.append({
                "old": list(handler_identity(b)),
                "new": list(_v2_identity(v2)),
                "tier": v2["tier"],
                "kind": v2["kind"],
                "lineno_baseline": b.get("lineno"),
                "lineno_head": v2["lineno"],
                "lineno_drift": (v2["lineno"] - b.get("lineno")) if isinstance(b.get("lineno"), int) else None,
                "matched": True,
            })
        else:
            migration.append({
                "old": list(key),
                "new": None,
                "tier": None,
                "kind": None,
                "lineno_baseline": b.get("lineno"),
                "lineno_head": None,
                "lineno_drift": None,
                "matched": False,
                "cand_count": len(cand),
                "note": "现仓未命中（handler 已被改造/删除/body 变更）",
            })

    def _by(kind_key, seq):
        d = {}
        for x in seq:
            d[x.get(kind_key, "?")] = d.get(x.get(kind_key, "?"), 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))

    tier_new = _by("tier", new)
    kind_new = _by("kind", new)
    kind_added = _by("kind", added)
    tier_added = _by("tier", added)

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": args.scope,
        "files_scanned": len(files),
        "parse_err": [{"file": a, "judge": b, "err": c} for a, b, c in parse_err],
        "legacy_hits": len(old),
        "new_hits": len(new),
        "new_by_kind": kind_new,
        "new_by_tier": tier_new,
        "added": [{
            "path": x["path"], "qualname": x["qualname"], "nth": x["nth"],
            "tname": x["tname"], "kind": x["kind"], "sha16": x["sha16"],
            "lineno": x["lineno"], "tier": x["tier"], "body": x["body"],
        } for x in added],
        "added_by_kind": kind_added,
        "added_by_tier": tier_added,
        "added_control_flow": [{
            "path": x["path"], "qualname": x["qualname"], "nth": x["nth"],
            "tname": x["tname"], "kind": x["kind"], "lineno": x["lineno"],
        } for x in added if x["tname"] in CONTROL_FLOW_TNAMES and x["kind"] in ("cont", "break")],
        "added_control_flow_count": sum(
            1 for x in added if x["tname"] in CONTROL_FLOW_TNAMES and x["kind"] in ("cont", "break")),
        "removed_count": len(removed),
        "removed_sample": [list(handler_identity(x)) for x in removed[:20]],
        "baseline_total": len(base),
        "baseline_matched": sum(1 for m in migration if m["matched"]),
        "baseline_unmatched": sum(1 for m in migration if not m["matched"]),
        "baseline_migration": [{k: (list(v) if isinstance(v, tuple) else v) for k, v in m.items()}
                               for m in migration],
    }

    if args.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("added", "baseline_migration")}, ensure_ascii=False, indent=2))
    print(f"[added] {len(added)} 条（详见 --out-json）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
