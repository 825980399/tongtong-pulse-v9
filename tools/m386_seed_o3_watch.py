#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""386刀1：破零观察位**人工补位**工具（从问题池挑 1 个 O3 候选写进 pending）

背景
----
384刀2 依规清零 pending 时，把破零观察位的**唯一**候选一起删了 ⇒
`watch_candidate` 恒返 `not_in_pool`（那是正常返回值、不报警）⇒ 破零静默失明。
385刀1 修的是「不再静默」（多值 + 自愈 + 缺失告警），但自愈走到
`no_candidate` —— pending 8 条全是 runtime_verified 的 llm 补丁，**0 个 O3**。

本工具做 385 留下的那一半：**人工补一个 O3 候选进 pending**。

★关键事实（386 T0 实测，不要凭票面照抄）
  * O3 id 必须按 `_m343_o3_stable_id(file, method, "dead_code_candidate",
    "335.cg.v1")` 复算 —— 已用三条历史条目验证**逐字一致**：
      o3_b31b31fee790d6e9 (audit_problem_pool.restore_pool)
      o3_49e50495d5e24d5a (audit_problem_pool.plan_target_drift)
      o3_59cd0eef8e125186 (audit_problem_pool.pool_summary)
    ★池里 `issue_type` 是 `dead_code`，但**生成 id 必须用 `dead_code_candidate`**：
      用 dead_code 会算出 o3_3d645ca55fb4d777 ⇒ 与 O3 开闸入池路径不同 ⇒
      重演 343刀1 修掉的「同题不同 id」坑。
  * **派单不改源码**：`dispatch_o3_approved` docstring 明写
    「沙箱 `motor.execute` 隔离执行（绝不 apply 到源码）、单批硬上限 1」。
    ⇒ 补位的风险是「耗一次派单额度」，不是「代码被改」。
  * **准入字段不在这里写**：`o3_auto_dispatch_ready` 的合规写入口是
    `approve_o3_patch`；存量补登记走 377 的 `tools/backfill_o3_dispatch_ready.py`。
    本工具**只落"未审批"形态**（`requires_confirmation=True`、无 ready 字段），
    准入回填请用既有工具单独做，两步可分别回滚。

候选筛选（本工具不硬编码结果，每次重算）
  ① 池里 `issue_type=dead_code` 且 `status=open`
  ② 文件存在、文件里确有 `def <method>`
  ③ 排除核心件（base/ config.py main.py）与超长文件（>3000 行 = GOD_FILE）
  ④ 全库调用点 ≤1（只有定义处）—— 用 `git grep`，排除 tests/.bak/tmp
  ⑤ 不在失败台账 `_m326_load_fail_ledger` 里（否则派单一上来就被 cap 挡）

用法::

    python tools/m386_seed_o3_watch.py                       # dry-run（默认，绝不写盘）
    python tools/m386_seed_o3_watch.py --list                # 只列候选
    python tools/m386_seed_o3_watch.py --apply --allow-prod   # 写（备份 + sha256 核对）
    python tools/m386_seed_o3_watch.py --file X --method Y    # 指定候选
    python tools/m386_seed_o3_watch.py --report out.json

★安全约束
  * 默认 dry-run；写生产 `data/` 需 `--apply` **且** `--allow-prod`（双重显式）；
  * 写前整文件备份到 `.bak_batch386/` 并核对 sha256，不一致即中止；
  * 幂等：同 id 已存在 ⇒ 不重复追加（返 `exists`）；
  * 写后**重读盘**，用 `zero_break_judge.watch_candidate` 与
    `_m339_o3_dispatch_ready` 复算，逐条给三态（不口头声称）。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

PENDING = os.path.join("data", "patches", "pending_patches.json")
BACKUP_DIR = os.path.join(".bak_batch386")

#: 默认候选（386 T0 体检选出；**每次仍会重算校验**，不盲信）
DEFAULT_FILE = "nucleus/runtime/OrganCoordinator.py"
DEFAULT_METHOD = "report_ready"

CORE_FILES = ("base/", "config.py", "main.py")
GOD_FILE_LINES = 3000

MARK = "★386刀1"


def _sha256(path: str) -> str:
    try:
        with open(path, "rb") as _f:
            return hashlib.sha256(_f.read()).hexdigest()[:16]
    except Exception as _e:
        silent_exc(_e, where="m386_seed._sha256", level="debug")
        return ""


def _read_json(path: str):
    with io.open(path, encoding="utf-8") as _f:
        return json.load(_f)


def o3_id(file_norm: str, method: str) -> str:
    """★按 O3 开闸入池**同口径**复算 id（已用 3 条历史条目验证一致）。"""
    from nucleus.evolution.patch_execution_dispatch import _m343_o3_stable_id
    return _m343_o3_stable_id(file_norm, method, "dead_code_candidate", "335.cg.v1")


def call_sites(method: str) -> int:
    """全库 `method(` 出现次数（排除 tests/.bak/tmp/文档）。"""
    import subprocess
    try:
        _o = subprocess.run(["git", "grep", "-n", "--", "%s(" % method],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="ignore")
        _lines = [l for l in (_o.stdout or "").splitlines() if l]
    except Exception as _e:
        silent_exc(_e, where="m386_seed.call_sites", level="debug")
        return -1
    _hit = []
    for _l in _lines:
        if "/tests/" in _l or ".bak" in _l or "/tmp/" in _l:
            continue
        if "code_backups" in _l or "/docs/" in _l or ".json:" in _l:
            continue
        _hit.append(_l)
    return len(_hit)


def pick_candidates(pool_entries: list) -> list:
    """按五条筛选规则重算候选（每次都算，不缓存结果）。"""
    _ledger = {}
    try:
        from nucleus.evolution.patch_execution_dispatch import _m326_load_fail_ledger
        _ledger = _m326_load_fail_ledger() or {}
    except Exception as _e:
        silent_exc(_e, where="m386_seed.pick_candidates ledger", level="debug")
    _led_keys = set()
    if isinstance(_ledger, dict):
        _led_keys |= {str(k) for k in _ledger}
        for _v in _ledger.values():
            if isinstance(_v, dict):
                _led_keys.add(str(_v.get("patch_id") or _v.get("id") or ""))

    _out = []
    for _e in (pool_entries or []):
        if not isinstance(_e, dict):
            continue
        if str(_e.get("issue_type")) != "dead_code":
            continue
        if str(_e.get("status")) != "open":
            continue
        _f = str(_e.get("file") or "")
        _m = str(_e.get("method") or "")
        if not _f or not _m:
            continue
        if any(_f.startswith(c) or _f == c for c in CORE_FILES):
            continue
        if not os.path.isfile(_f):
            continue
        try:
            _src = io.open(_f, encoding="utf-8", errors="ignore").read()
        except Exception as _re:
            silent_exc(_re, where="m386_seed.pick_candidates read", level="debug")
            continue
        if ("def %s" % _m) not in _src:
            continue
        _lines = _src.count("\n") + 1
        if _lines > GOD_FILE_LINES:
            continue
        _n = call_sites(_m)
        if _n > 1:
            continue
        _oid = o3_id(_f, _m)
        if _oid in _led_keys:
            continue
        _out.append({"file": _f, "method": _m, "line": int(_e.get("line") or 0),
                     "dedup_key": str(_e.get("dedup_key") or ""),
                     "severity": str(_e.get("severity") or ""),
                     "o3_id": _oid, "calls": _n, "lines": _lines,
                     "pool_desc": str(_e.get("desc") or "")})
    return _out


def build_entry(file_norm: str, method: str, *, line: int,
                dedup_key: str, pool_desc: str, now: float) -> dict:
    """条目字段 —— **照 384刀2 备份里被清掉的原观察位**的形态，不自己编字段名。

    两条样本：`o3_b31b31fee790d6e9`（已批，带 ready 字段）与
    `o3_49e50495d5e24d5a`（未批）。本工具落**未批**形态。
    """
    return {
        "id": o3_id(file_norm, method),
        "file": file_norm,
        "method": method,
        "line": line,
        "issue_type": "dead_code_candidate",
        "description": ("O3 开闸入池：方法 %s 图内三层无入边且图外无证据 ⇒ 待人工确认是否删除"
                        % method),
        "o3_marker": "335.cg.v1",
        "o3_dead_code": True,
        "o3_rule": "O3_OPEN_GATE_335",
        "intake_reason": "o3_open_gate_consumer",
        "source": "o3_open_gate",
        "source_process": "live",
        "risk_level": "高",
        "trust_score": 0,
        "status": "pending",
        "requires_confirmation": True,
        "created_at": now,
        "generated_at": now,
        "saved_at": now,
        "o3_intake_ts": int(now),
        "id_scheme": "343.stable.sha1",
        "dedup_key": dedup_key or None,
        # ===== 本刀留痕：让后人一眼看出这是**人工补位**，不是开闸自然入池 =====
        "seeded_by": "%s/m386_seed_o3_watch" % MARK,
        "seed_at": now,
        "seed_reason": ("384刀2 清零 pending 后破零观察位无候选（385刀1 自愈走 "
                        "no_candidate）⇒ 386 人工补 1 个 O3 候选恢复读数"),
        "seed_pool_desc": pool_desc or None,
    }


def verify(pending_rows: list, patch_id: str, project_root: str) -> dict:
    """写前/写后都用**同一套真实判据**复算（三态明确）。"""
    from nucleus.evolution import zero_break_judge as zb
    from nucleus.evolution.patch_execution_dispatch import _m339_o3_dispatch_ready
    _w = zb.watch_candidate(patch_id, pending_rows=pending_rows, project_root=project_root)
    _row = None
    for _r in (pending_rows or []):
        if isinstance(_r, dict) and str(_r.get("id") or "") == patch_id:
            _row = _r
            break
    _ok, _why = _m339_o3_dispatch_ready(_row or {})
    return {
        "in_pending": _w.get("in_pending"),
        "verdict": _w.get("verdict"),
        "missing": _w.get("missing"),
        "selected": _w.get("selected"),
        "dispatch_ready": _ok,
        "dispatch_why": _why,
    }


def main() -> int:
    _ap = argparse.ArgumentParser(description="386刀1 破零观察位人工补位")
    _ap.add_argument("--file", default=DEFAULT_FILE)
    _ap.add_argument("--method", default=DEFAULT_METHOD)
    _ap.add_argument("--list", action="store_true", help="只列候选，不写")
    _ap.add_argument("--apply", action="store_true")
    _ap.add_argument("--allow-prod", action="store_true")
    _ap.add_argument("--target", default=PENDING)
    _ap.add_argument("--report", default=None)
    _a = _ap.parse_args()

    try:
        from nucleus import audit_problem_pool as ap
        _pool = ap.load_pool()
    except Exception as _e:
        silent_exc(_e, where="m386_seed.load_pool", level="warning")
        print("[0] 问题池读不到 ⇒ 无法挑候选")
        return 2
    _es = _pool.get("entries") or []
    _cands = pick_candidates(_es)
    print("[1] 池内条目 %d；按五条规则筛选后候选 %d" % (len(_es), len(_cands)))

    if _a.list:
        print("\n%-46s %-30s %-5s %-6s %s" % ("file", "method", "calls", "lines", "o3_id"))
        for _c in _cands[:15]:
            print("%-46s %-30s %-5d %-6d %s" % (_c["file"][:46], _c["method"][:30],
                                                _c["calls"], _c["lines"], _c["o3_id"]))
        return 0

    _hit = [c for c in _cands if c["file"] == _a.file and c["method"] == _a.method]
    if not _hit:
        print("[2] 指定候选不在筛选结果里 ⇒ 中止（不写盘）")
        print("    指定: %s :: %s" % (_a.file, _a.method))
        print("    提示：它可能已被 claimed / 文件不存在 / 有调用点 / 在失败台账。"
              "用 --list 看当前合格候选。")
        return 2
    _cand = _hit[0]
    _pid = _cand["o3_id"]
    print("[2] 选定候选: %s :: %s ⇒ %s（池 dedup=%s, severity=%s, 调用点=%d）"
          % (_cand["file"], _cand["method"], _pid, _cand["dedup_key"],
             _cand["severity"], _cand["calls"]))

    _rows = _read_json(_a.target) if os.path.isfile(_a.target) else []
    if not isinstance(_rows, list):
        print("[3] pending 顶层不是 list ⇒ 中止")
        return 2
    _exist = [r for r in _rows if isinstance(r, dict) and str(r.get("id") or "") == _pid]
    if _exist:
        print("[3] 该 id 已在 pending（幂等）⇒ 不重复追加")
        _rep = {"state": "exists", "patch_id": _pid,
                "verify": verify(_rows, _pid, _ROOT)}
        print("    当前判定:", json.dumps(_rep["verify"], ensure_ascii=False))
        if _a.report:
            with open(_a.report, "w", encoding="utf-8", newline="\n") as _f:
                json.dump(_rep, _f, ensure_ascii=False, indent=1)
        return 0

    _entry = build_entry(_cand["file"], _cand["method"], line=_cand["line"],
                         dedup_key=_cand["dedup_key"], pool_desc=_cand["pool_desc"],
                         now=time.time())
    _new = list(_rows) + [_entry]
    _rep = {"state": "dry-run", "patch_id": _pid, "candidate": _cand,
            "entry": _entry,
            "verify_before": verify(_rows, _pid, _ROOT),
            "verify_after": verify(_new, _pid, _ROOT)}
    print("[3] 补位前判定:", json.dumps(_rep["verify_before"], ensure_ascii=False))
    print("    补位后判定:", json.dumps(_rep["verify_after"], ensure_ascii=False))

    if not _a.apply:
        print("\n[4] dry-run：未写盘。加 --apply --allow-prod 执行。")
        if _a.report:
            with open(_a.report, "w", encoding="utf-8", newline="\n") as _f:
                json.dump(_rep, _f, ensure_ascii=False, indent=1)
        return 0

    if "data" + os.sep in _a.target and not _a.allow_prod:
        print("[4] 目标在生产 data/ 下 ⇒ 需 --allow-prod 显式授权（双重显式）")
        return 2

    os.makedirs(BACKUP_DIR, exist_ok=True)
    _bak = os.path.join(BACKUP_DIR, "pending_before_386_d1.json")
    _src_sha = _sha256(_a.target)
    try:
        with open(_a.target, "rb") as _fi, open(_bak, "wb") as _fo:
            _fo.write(_fi.read())
    except Exception as _e:
        silent_exc(_e, where="m386_seed.backup", level="warning")
        print("[4] 备份失败 ⇒ 中止")
        return 2
    if _sha256(_bak) != _src_sha:
        print("[4] 备份 sha256 不一致 ⇒ 中止（原文件未动）")
        return 2
    print("[4] 备份 %s（sha256[:16]=%s 已核对）" % (_bak, _src_sha))

    _tmp = _a.target + ".tmp386"
    with open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
        json.dump(_new, _f, ensure_ascii=False, indent=2)
    os.replace(_tmp, _a.target)

    _back = _read_json(_a.target)
    _rep["state"] = "applied"
    _rep["rows_before"] = len(_rows)
    _rep["rows_after"] = len(_back)
    _rep["verify_reread"] = verify(_back, _pid, _ROOT)
    print("[5] 已写盘：%d → %d 条" % (len(_rows), len(_back)))
    print("    重读判定:", json.dumps(_rep["verify_reread"], ensure_ascii=False))
    if _rep["verify_reread"]["in_pending"] is not True:
        print("    ★写后仍未在 pending ⇒ 判据有问题，请回滚检查")
    if _a.report:
        with open(_a.report, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_rep, _f, ensure_ascii=False, indent=1)
        print("[6] 报告:", _a.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
