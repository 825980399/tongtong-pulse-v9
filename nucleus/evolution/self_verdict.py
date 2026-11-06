# -*- coding: utf-8 -*-
"""self_verdict.py —— 自裁决闭环（第460批）

授权来源：2026-11-06 星轨对话裁决「闭环内的裁决角色让曈曈自己裁决」。
**本模块推翻了 459 之后路灯建议的『保留人工裁决闸』**：安全感不再由"有人拦"
提供，而是由三件可验证的事提供：

  ① 证据准入门 —— 审批前统一调用既有静态复现探针，回填八闸一直拿不到的
     `reprobe_verdict` / `problem_gone` / `baseline_errors`；
     **不可判定 ⇒ 不放行**（`false_pass` 绝不猜成通过，也不罚成失败）。
  ② 真自动回退 —— 改前按字节快照（含行尾与 sha256），应用后验证失败则
     **真的写回**（447 批注释写了"失败自动回滚"，函数里却没有一次 rollback）。
  ③ 同类错误冷却 —— 失败按结构化 `error_type` 进既有失败经验库（452 刀6 已把
     `append_reminder` 接进生成 prompt，是软保证）；同一 (dedup_key, error_type)
     连续失败达 N 次则进冷却，冷却期内不再生成补丁（硬保证）。

边界（务必遵守）：
  · **不新增第二个状态写口**：应用仍由 `approval_bridge` →
    `PatchManager.approve_patch_by_id()` 执行；本模块只做前置 `prepare()` 与
    后置 `finalize()`，不改 patch 原对象（一律返回副本）。
  · **不改八闸判据**：`approval_bridge.decision()` 一个字不动。
  · **不 import 任何执行器**，与 approval_bridge 的硬边界一致。
  · 默认开关 True（按裁决生效）；关掉 ⇒ `prepare()` 返回 `admitted=False`，
    调用方逐字节回到 460 前行为，可用来 A/B 复现旧现场。

两段式：
    prep = prepare(patch, ...)              # 准入 → 证据 → 三态裁决 → 快照
    res  = maybe_approve_scoped(prep["patch"], pm)   # 既有唯一写口
    fin  = finalize(prep, res, pm, ...)     # 留痕 → 验证 → 失败真回退 → 经验
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import time
from typing import Any

import config
from nucleus._silent_except import silent_exc

SCHEMA = "self_verdict/460.v1"

#: 三态裁决字面量（测试按这些字面量做探针，勿改）
V_APPLY = "apply"
V_REJECT = "reject"
V_INCONCLUSIVE = "inconclusive"

#: 证据结论 → 裁决依据（与 375 刀4 的 `_M375_PROBLEM_GONE_MAP` 同口径）
_EVIDENCE_TRUE_PASS = "true_pass"
_EVIDENCE_PARTIAL = "partial_fix"

_DEFAULT_SNAPSHOT_REL = "data/patches/self_verdict_snapshots"
_DEFAULT_LEDGER_REL = "data/patches/self_verdict_ledger.jsonl"


def _cfg(name: str, default: Any) -> Any:
    """★只有 None 才算缺省：`getattr(cfg, X, d) or d` 会把 0 当缺省（踩过）。"""
    _v = getattr(config, name, None)
    return default if _v is None else _v


def _on() -> bool:
    return bool(_cfg("ENABLE_M460_SELF_VERDICT", True))


def _root(root: str | None = None) -> str:
    if root:
        return str(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _writable(root: str | None) -> bool:
    """★pytest 护栏：显式传 root（隔离目录）照常写；默认生产路径 + pytest ⇒ 不写。

    446 批实测：pytest 会经生产代码路径写默认留痕文件，一次回归灌 8 行假判定。
    """
    if root is not None:
        return True
    return "pytest" not in sys.modules


def ledger_path(root: str | None = None) -> str:
    return os.path.join(_root(root), str(_cfg("M460_LEDGER_REL", _DEFAULT_LEDGER_REL)))


def snapshot_dir(root: str | None = None) -> str:
    return os.path.join(_root(root), str(_cfg("M460_SNAPSHOT_DIR_REL", _DEFAULT_SNAPSHOT_REL)))


def _target_files(patch: dict) -> list[str]:
    """与 `approval_bridge.target_files` 同口径（`files` 优先，其次单 `file`）。

    不直接 import，避免 approval_bridge → self_verdict 的循环导入。
    """
    _files = (patch or {}).get("files")
    if isinstance(_files, (list, tuple, set)):
        _out = [str(x) for x in _files if str(x).strip()]
        if _out:
            return _out
    _one = str((patch or {}).get("file") or "").strip()
    return [_one] if _one else []


def dedup_key(patch: dict) -> str:
    """复用 `audit_problem_pool.make_dedup_key`（sha1，弃行号、路径归一）。

    拿不到时用同口径本地兜底 —— 绝不退化成内置 `hash()`（跨进程不稳定）。
    """
    _f = str((patch or {}).get("file") or "").replace("\\", "/").lstrip("./")
    _m = str((patch or {}).get("method") or "").strip()
    _t = str((patch or {}).get("issue_type") or "").strip()
    try:
        from nucleus.audit_problem_pool import make_dedup_key as _mk
        return str(_mk(_f, _m, _t))
    except Exception as _e:
        silent_exc(_e, where="self_verdict::dedup_key fallback", level="debug")
    _raw = f"{_f}|{_m}|{_t}"
    return hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:16]


def note_ledger(row: dict, *, root: str | None = None) -> bool:
    """append-only 留痕。**失败绝不影响主流程**（日志不决定返回值）。"""
    if not _writable(root):
        return False
    _p = ledger_path(root)
    try:
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as _e:
        silent_exc(_e, where="self_verdict::note_ledger", level="debug")
        return False


def _read_ledger(root: str | None = None) -> list[dict]:
    _p = ledger_path(root)
    _rows: list[dict] = []
    try:
        if not os.path.isfile(_p):
            return _rows
        with open(_p, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _d = json.loads(_line)
                except ValueError as _e:
                    silent_exc(_e, where="self_verdict::_read_ledger bad line", level="debug")
                    continue
                if isinstance(_d, dict):
                    _rows.append(_d)
    except Exception as _e:
        silent_exc(_e, where="self_verdict::_read_ledger", level="debug")
    return _rows


# ==================== ① 问题单准入 ====================

def make_ticket(patch: dict, *, root: str | None = None,
                now: float | None = None) -> dict:
    """问题单准入：**证据 + 去重 + 聚合窗口**。

    Returns: `{"admitted", "ticket", "merged", "hits", "why", "cooldown"}`
    """
    _now = float(now if now is not None else time.time())
    _out: dict = {"admitted": False, "ticket": None, "merged": False,
                  "hits": 0, "why": "", "cooldown": False}
    if not isinstance(patch, dict) or not patch:
        _out["why"] = "空问题单"
        return _out

    _key = dedup_key(patch)
    _files = _target_files(patch)
    _has_locator = bool(_files) or bool(str(patch.get("method") or "").strip())
    _has_code = bool(patch.get("original_code")) and bool(patch.get("modified_code"))
    if not _has_locator and not _has_code:
        _out["why"] = "无定位证据（file/method 与 original_code 均缺）"
        return _out

    _agg = float(_cfg("M460_TICKET_AGGREGATE_SEC", 900.0))
    _hits = 0
    for _row in _read_ledger(root):
        if str(_row.get("dedup_key") or "") != _key:
            continue
        if _row.get("kind") not in ("ticket", "verdict"):
            continue
        try:
            _ts = float(_row.get("at") or 0)
        except (TypeError, ValueError) as _e:
            silent_exc(_e, where="self_verdict::make_ticket ts", level="debug")
            continue
        if 0 < _now - _ts <= _agg:
            _hits += 1
    _out["hits"] = _hits
    _out["merged"] = _hits > 0

    _cd = cooldown_active(_key, root=root, now=_now)
    if _cd:
        _out["why"] = f"同类错误冷却中（至 {_cd:.0f}）"
        _out["cooldown"] = True
        return _out

    _out["ticket"] = {
        "dedup_key": _key,
        "patch_id": str(patch.get("id") or ""),
        "file": _files[0] if _files else str(patch.get("file") or ""),
        "method": str(patch.get("method") or ""),
        "issue_type": str(patch.get("issue_type") or ""),
        "evidence": "code_pair" if _has_code else "locator",
        "at": _now,
        "hits_in_window": _hits,
    }
    _out["admitted"] = True
    _out["why"] = "聚合窗口内重复" if _hits else "准入通过"
    return _out


# ==================== ② 证据生产（接既有静态探针） ====================

def normalize_snippet(code: Any) -> tuple[str, bool]:
    """★461 刀2：把「方法体片段」包装成可解析函数体，供静态复现探针使用。

    实盘 05:14 样本 `original_code` 是 199 字符的**方法体**（没有 `def`），
    `active_reprobe` 解析不到完整函数 ⇒ `false_pass` ⇒ `baseline_errors=0`
    ⇒ 八闸 baseline_positive 永假。检测器其实早在 446 刀1 就覆盖了
    `periodic_task_no_reentry`，卡的是**输入形态**不是判据缺失。

    ★只包装「无 def 且顶层解析失败」的片段；已有 def 但语法错的一律原样返回
      （不猜、不替生成器背锅）。返回 `(code, wrapped)`。
    """
    if not isinstance(code, str) or not code.strip():
        return str(code or ""), False
    try:
        ast.parse(code)
        return code, False
    except SyntaxError as _e:
        silent_exc(_e, where="self_verdict::normalize_snippet parse", level="debug")
    except Exception as _e:
        silent_exc(_e, where="self_verdict::normalize_snippet other", level="debug")
        return code, False
    if re.search(r"^\s*def\s", code, re.MULTILINE):
        return code, False
    _body = "\n".join(("    " + _ln if _ln.strip() else "")
                       for _ln in code.splitlines())
    return "def _m461_probe():\n" + _body + "\n", True


def collect_evidence(patch: dict) -> dict:
    """审批前统一调用既有探针并**回填副本**，这是 460 批最关键的一刀。

    既有零件（都不新建）：
      · `patch_active_reprobe.active_reprobe()` —— 静态 AST 复现，不执行代码；
      · `EvolutionEffectVerifier.problem_gone_from_reprobe()` —— 375 刀4 映射；
      · `baseline_probe.probe_code_repro()` —— 修复前命中数。

    ★绝不猜：`false_pass` / `not_applicable` / `verify_failed` 一律 `problem_gone=None`；
      基线取不到就不回填（保留原值），**不填 0**（0 会被八闸当"复现不出"）。
    """
    _out: dict = {
        "ok": False,
        "patch": dict(patch or {}),
        "reprobe_verdict": "",
        "problem_gone": None,
        "baseline_errors": None,
        "baseline_source": "",
        "why": "",
        "schema": SCHEMA,
    }
    if not isinstance(patch, dict) or not patch:
        _out["why"] = "patch 为空"
        return _out

    _p = dict(patch)
    # ★461 刀2：探针输入单独造一份「归一化副本」，**不改原 patch 的
    #   original_code / modified_code**（审批留痕必须保留生成器原文）。
    _probe_patch = dict(_p)
    _wrapped = False
    if bool(_cfg("ENABLE_M461_SNIPPET_NORMALIZE", True)):
        _o, _ow = normalize_snippet(_p.get("original_code"))
        _m, _mw = normalize_snippet(_p.get("modified_code"))
        _wrapped = bool(_ow or _mw)
        if _wrapped:
            _probe_patch["original_code"] = _o
            _probe_patch["modified_code"] = _m
    _out["reprobe_scope"] = "snippet" if _wrapped else "as_is"
    try:
        from nucleus.evolution.patch_active_reprobe import active_reprobe
        _r = active_reprobe(_probe_patch)
    except Exception as _e:
        silent_exc(_e, where="self_verdict::collect_evidence active_reprobe", level="debug")
        _out["why"] = f"复现探针不可用: {type(_e).__name__}: {_e}"
        return _out

    _v = str(_r.get("reprobe_verdict") or "")
    _out["reprobe_verdict"] = _v
    # ★460 首轮回归实测：无条件回填会把调用方**已有的更强证据**盖掉
    #   （探针如实给 0，把构造好的 baseline_errors=2 冲成 0 ⇒ 八闸 baseline_positive 变红）。
    #   契约：现场探针只**补缺失**，已有字段一律不动（不猜、不降级、不覆盖）。
    if "reprobe_verdict" not in _p:
        _p["reprobe_verdict"] = _v
    if "reprobe_baseline_hits" not in _p:
        _p["reprobe_baseline_hits"] = _r.get("reprobe_baseline_hits")
    if "reprobe_after_hits" not in _p:
        _p["reprobe_after_hits"] = _r.get("reprobe_after_hits")

    try:
        from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier
        _pg, _pg_why = EvolutionEffectVerifier.problem_gone_from_reprobe(
            {"reprobe_verdict": _v})
    except Exception as _e:
        silent_exc(_e, where="self_verdict::collect_evidence problem_gone", level="debug")
        _pg, _pg_why = None, f"映射不可用: {type(_e).__name__}"
    if _pg is not None and "problem_gone" not in _p:
        _p["problem_gone"] = _pg
    # ★461 刀2：有界放宽 —— **只升级、不降级**。
    #   460 的「只补缺失」契约是为防「探针如实 0 覆盖构造的 2」；但实盘 05:14
    #   生成侧显式写了 problem_gone=None / baseline_errors=None，键存在 ⇒ 补不进去。
    #   这里允许「显式真值」升级 None/False，绝不允许把已有 True 降成 False。
    if _pg is True and _p.get("problem_gone") is not True:
        _p["problem_gone"] = True
    _out["problem_gone"] = _p.get("problem_gone")

    try:
        from nucleus.evolution.baseline_probe import probe_code_repro
        _b = probe_code_repro(_probe_patch)
    except Exception as _e:
        silent_exc(_e, where="self_verdict::collect_evidence baseline", level="debug")
        _b = {}
    _be = _b.get("baseline_errors")
    _out["baseline_source"] = str(_b.get("baseline_source") or "")
    if isinstance(_be, int) and _be >= 0:
        _old_be = _p.get("baseline_errors")
        _may_upgrade = ("baseline_errors" not in _p or _old_be is None
                        or (isinstance(_old_be, (int, float)) and _be > _old_be))
        # ★只升级不降级：探针给 0 而调用方已有 2 ⇒ 不动（460 首轮顶红的教训）
        if _may_upgrade:
            _p["baseline_errors"] = _be
            if "baseline_source" not in _p:
                _p["baseline_source"] = _out["baseline_source"]
    # 裁决看**合成后的**证据（既有字段优先），不是探针原始值
    _out["baseline_errors"] = _p.get("baseline_errors")

    # ★461 刀2：修「LLM 补丁结构性不可能过闸」的契约错配。
    #   生成侧给 LLM 补丁固定 confidence="medium"（SafeEvolutionExecutor:6688），
    #   八闸 confidence_high 只认 "high" ⇒ 05:14 样本 5/8 挂在 confidence=unknown。
    #   ★不是放宽判据：只有**三证齐备**（静态复现 true_pass + problem_gone 显式真
    #     + baseline>0）才按依据提权，并写 confidence_source 供事后复核；
    #     已有 "high" 不动，证据不足绝不提权。
    _out["confidence_promoted"] = False
    if bool(_cfg("ENABLE_M461_EVIDENCE_CONFIDENCE", True)):
        _three = (_out["reprobe_verdict"] == _EVIDENCE_TRUE_PASS
                  and _out["problem_gone"] is True
                  and isinstance(_out["baseline_errors"], int)
                  and _out["baseline_errors"] > 0)
        _old_conf = str(_p.get("confidence") or "").strip().lower()
        if _three and _old_conf != "high":
            _p["confidence"] = "high"
            _p["confidence_source"] = "m461_reprobe_true_pass"
            _out["confidence_promoted"] = True

    _out["patch"] = _p
    _out["ok"] = True
    _out["why"] = str(_pg_why or "")
    return _out


# ==================== ③ 三态自裁决 ====================

def verdict(evidence: dict, patch: dict, *, gates: dict | None = None,
            retry_count: int = 0, root: str | None = None,
            now: float | None = None) -> dict:
    """三态：`apply` / `reject` / `inconclusive`。

    ★不可判定绝不默认放行，也**不丢弃**：`inconclusive` 留在队列里补证重判，
      配 `M460_MAX_REPRO_RETRY` 上限（铁律55：状态位反复入队必须配上限）。
    """
    _now = float(now if now is not None else time.time())
    _checks: dict[str, bool] = {}
    _out: dict = {"verdict": V_INCONCLUSIVE, "reason": "", "checks": _checks,
                  "dedup_key": dedup_key(patch), "patch_id": str(patch.get("id") or ""),
                  "at": _now}

    _key = _out["dedup_key"]
    if cooldown_active(_key, root=root, now=_now):
        _out["verdict"] = V_REJECT
        _out["reason"] = "同类错误冷却中"
        _checks["cooldown"] = False
        return _out
    _checks["cooldown"] = True

    _v = str((evidence or {}).get("reprobe_verdict") or "")
    _pg = (evidence or {}).get("problem_gone")
    _be = (evidence or {}).get("baseline_errors")
    _checks["reprobe_true_pass"] = (_v == _EVIDENCE_TRUE_PASS)
    _checks["problem_gone_true"] = (_pg is True)
    _checks["baseline_positive"] = (isinstance(_be, int) and _be > 0)

    if _v == _EVIDENCE_PARTIAL:
        _out["verdict"] = V_REJECT
        _out["reason"] = "复现实测：问题减少但未清零（partial_fix）"
        return _out

    _blast = len(_target_files(patch))
    _max_blast = int(_cfg("M460_MAX_BLAST_FILES", 1))
    _checks["blast_within_limit"] = (0 < _blast <= _max_blast)
    if not _checks["blast_within_limit"]:
        _out["verdict"] = V_REJECT
        _out["reason"] = f"影响面 {_blast} 文件 > 上限 {_max_blast}"
        return _out

    if gates:
        _bad = [k for k, v in gates.items()
                if isinstance(v, dict) and not v.get("ok")]
        _checks["gates_all_pass"] = not _bad
        if _bad:
            _out["verdict"] = V_REJECT
            _out["reason"] = "八闸未过: " + ", ".join(_bad)
            return _out

    if not all(_checks.get(k) for k in
               ("reprobe_true_pass", "problem_gone_true", "baseline_positive")):
        _max_retry = int(_cfg("M460_MAX_REPRO_RETRY", 3))
        if int(retry_count) >= _max_retry:
            _out["verdict"] = V_REJECT
            _out["reason"] = f"证据不足且重试已达上限 {_max_retry}（不丢弃、不再排队）"
        else:
            _out["verdict"] = V_INCONCLUSIVE
            _out["reason"] = f"证据不足（verdict={_v or '缺值'}）⇒ 待补证重判"
        return _out

    _out["verdict"] = V_APPLY
    _out["reason"] = "证据齐备 + 影响面合规 ⇒ 曈曈自裁决放行"
    return _out


# ==================== ④ 字节快照与真回退 ====================

def snapshot_before(patch: dict, *, root: str | None = None,
                    now: float | None = None) -> dict:
    """改前按**字节**快照（保留原行尾 + sha256），这是"能挽救"的物理前提。"""
    _now = float(now if now is not None else time.time())
    _out: dict = {"ok": False, "snapshot_id": "", "files": [], "why": "", "at": _now}
    if not _writable(root):
        _out["why"] = "pytest 默认生产路径 ⇒ 拒绝写快照（不放行）"
        return _out
    _files = _target_files(patch)
    if not _files:
        _out["why"] = "无目标文件"
        return _out
    _base = _root(root)
    _sid = f"{int(_now * 1000)}_{dedup_key(patch)[:8]}"
    _dir = os.path.join(snapshot_dir(root), _sid)
    _meta: list[dict] = []
    try:
        os.makedirs(_dir, exist_ok=True)
        for _i, _rel in enumerate(_files):
            _abs = _rel if os.path.isabs(_rel) else os.path.join(_base, _rel)
            _norm = os.path.normpath(_abs)
            if not _norm.startswith(os.path.normpath(_base)):
                _out["why"] = f"目标越界: {_rel}"
                return _out
            if not os.path.isfile(_norm):
                _out["why"] = f"目标不存在: {_rel}"
                return _out
            with open(_norm, "rb") as _f:
                _b = _f.read()
            _crlf = _b.count(b"\r\n")
            _lf = _b.count(b"\n") - _crlf
            with open(os.path.join(_dir, f"{_i:03d}.bin"), "wb") as _f:
                _f.write(_b)
            _meta.append({
                "index": _i,
                "rel": _rel,
                "abs": _norm,
                "bytes": len(_b),
                "sha256": hashlib.sha256(_b).hexdigest(),
                "eol": "CRLF" if _crlf > _lf else "LF",
                "crlf": _crlf,
                "bare_lf": _lf,
            })
        with open(os.path.join(_dir, "meta.json"), "w", encoding="utf-8") as _f:
            json.dump({"snapshot_id": _sid, "at": _now,
                       "patch_id": str(patch.get("id") or ""),
                       "dedup_key": dedup_key(patch), "files": _meta},
                      _f, ensure_ascii=False, indent=1)
    except Exception as _e:
        silent_exc(_e, where="self_verdict::snapshot_before", level="warning")
        _out["why"] = f"快照失败: {type(_e).__name__}: {_e}"
        return _out
    _out["ok"] = True
    _out["snapshot_id"] = _sid
    _out["files"] = _meta
    _out["why"] = f"已快照 {len(_meta)} 文件"
    return _out


def restore_snapshot(snapshot: dict, *, root: str | None = None) -> dict:
    """**真的**按字节写回并逐个校验 sha256（不是只留一条日志）。"""
    _out: dict = {"ok": False, "restored": [], "why": ""}
    _sid = str((snapshot or {}).get("snapshot_id") or "")
    _files = (snapshot or {}).get("files") or []
    if not _sid or not _files:
        _out["why"] = "无快照可回退"
        return _out
    _dir = os.path.join(snapshot_dir(root), _sid)
    try:
        for _m in _files:
            _src = os.path.join(_dir, f"{int(_m.get('index', 0)):03d}.bin")
            _dst = str(_m.get("abs") or "")
            with open(_src, "rb") as _f:
                _b = _f.read()
            if hashlib.sha256(_b).hexdigest() != _m.get("sha256"):
                _out["why"] = f"快照校验失败: {_m.get('rel')}"
                return _out
            with open(_dst, "wb") as _f:
                _f.write(_b)
            with open(_dst, "rb") as _f:
                _back = _f.read()
            if hashlib.sha256(_back).hexdigest() != _m.get("sha256"):
                _out["why"] = f"回写校验失败: {_m.get('rel')}"
                return _out
            _out["restored"].append(_m.get("rel"))
    except Exception as _e:
        silent_exc(_e, where="self_verdict::restore_snapshot", level="warning")
        _out["why"] = f"回退异常: {type(_e).__name__}: {_e}"
        return _out
    _out["ok"] = True
    _out["why"] = f"已回退 {len(_out['restored'])} 文件"
    return _out


def verify_after_apply(patch: dict, *, verify_fn: Any = None,
                       root: str | None = None) -> dict:
    """应用后验证：默认静态 `ast.parse`（不执行生产代码），可由调用方注入真单测。"""
    if callable(verify_fn):
        try:
            _r = verify_fn(patch)
            if isinstance(_r, dict):
                return _r
            return {"ok": bool(_r), "why": "外部验证函数返回"}
        except Exception as _e:
            silent_exc(_e, where="self_verdict::verify_after_apply injected", level="warning")
            return {"ok": False, "why": f"验证函数异常: {type(_e).__name__}: {_e}"}
    _base = _root(root)
    _checked = 0
    try:
        for _rel in _target_files(patch):
            _abs = _rel if os.path.isabs(_rel) else os.path.join(_base, _rel)
            if not os.path.isfile(_abs) or not _rel.endswith(".py"):
                continue
            with open(_abs, "r", encoding="utf-8") as _f:
                ast.parse(_f.read())
            _checked += 1
    except SyntaxError as _e:
        silent_exc(_e, where="self_verdict::verify_after_apply ast", level="warning")
        return {"ok": False, "why": f"改后语法错误: {_e}"}
    except Exception as _e:
        silent_exc(_e, where="self_verdict::verify_after_apply io", level="debug")
        return {"ok": False, "why": f"改后读取失败: {type(_e).__name__}: {_e}"}
    return {"ok": True, "why": f"静态语法校验通过（{_checked} 文件）"}


# ==================== ⑤ 失败经验与同类冷却 ====================

def cooldown_active(key: str, *, root: str | None = None,
                    now: float | None = None) -> float:
    """返回冷却截止时刻（未冷却 ⇒ 0.0）。"""
    _now = float(now if now is not None else time.time())
    _until = 0.0
    try:
        for _row in _read_ledger(root):
            if str(_row.get("dedup_key") or "") != key:
                continue
            if _row.get("kind") != "cooldown":
                continue
            _u = float(_row.get("cooldown_until") or 0)
            if _u > _now and _u > _until:
                _until = _u
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="self_verdict::cooldown_active", level="debug")
    return _until


def record_lesson(*, key: str, error_type: str, reason: str,
                  file_path: str = "", method: str = "",
                  root: str | None = None,
                  now: float | None = None) -> dict:
    """结构化失败经验 → 既有 `patch_failure_experience`（软保证）+ 同类冷却（硬保证）。

    `error_type` 必须是**机器可分类**的代码（如 `self_verdict:baseline_missing`），
    不写自由文本 —— 否则 `build_reminder` 按 error_type 计数会退化成 229 条
    「同一句话」那种不可分类的症状（459 批 voided_feature 的同型教训）。
    """
    _now = float(now if now is not None else time.time())
    _code = "self_verdict:" + str(error_type or "unknown").strip()
    _out: dict = {"ok": False, "error_type": _code, "same_kind_streak": 0,
                  "cooldown": False, "cooldown_until": 0.0}

    try:
        from nucleus.patch_failure_experience import record_failure
        _out["ok"] = bool(record_failure(str(reason or ""), error_type=_code,
                                         file_path=str(file_path or "")))
    except Exception as _e:
        silent_exc(_e, where="self_verdict::record_lesson record_failure", level="debug")

    _streak = 1
    try:
        _rows = [r for r in _read_ledger(root)
                 if str(r.get("dedup_key") or "") == key
                 and r.get("kind") == "lesson"]
        for _r in reversed(_rows):
            if str(_r.get("error_type") or "") == _code:
                _streak += 1
            else:
                break
    except Exception as _e:
        silent_exc(_e, where="self_verdict::record_lesson streak", level="debug")
    _out["same_kind_streak"] = _streak

    note_ledger({"kind": "lesson", "at": _now, "dedup_key": key,
                 "error_type": _code, "reason": str(reason or "")[:300],
                 "same_kind_streak": _streak, "schema": SCHEMA}, root=root)
    # ★461 刀1：两条失败路径（作废 / 自裁决）写**同一份**冷却台账，口径不再漂移。
    try:
        from nucleus.evolution import lesson_cooldown as _m461_lc
        _m461_lc.record(file=str(file_path or ""), method=str(method or ""),
                        error_type=_code, reason=str(reason or "")[:200],
                        source="self_verdict", root=root, now=_now)
    except Exception as _e:
        silent_exc(_e, where="self_verdict::record_lesson 461统一冷却", level="debug")

    _n = int(_cfg("M460_LESSON_COOLDOWN_N", 2))
    if _streak >= _n:
        _until = _now + float(_cfg("M460_LESSON_COOLDOWN_SEC", 86400.0))
        _out["cooldown"] = True
        _out["cooldown_until"] = _until
        note_ledger({"kind": "cooldown", "at": _now, "dedup_key": key,
                     "error_type": _code, "cooldown_until": _until,
                     "same_kind_streak": _streak, "schema": SCHEMA}, root=root)
    return _out


# ==================== 两段式闭环 ====================

def prepare(patch: dict, *, issue: dict | None = None, root: str | None = None,
            now: float | None = None, retry_count: int = 0,
            gates: dict | None = None) -> dict:
    """准入 → 证据 → 自裁决 → 快照。返回**副本**，绝不改原 patch。"""
    _now = float(now if now is not None else time.time())
    _out: dict = {"admitted": False, "patch": dict(patch or {}), "ticket": {},
                  "evidence": {}, "verdict": {}, "snapshot": {},
                  "why": "", "schema": SCHEMA, "at": _now}
    if not _on():
        _out["why"] = "ENABLE_M460_SELF_VERDICT=False ⇒ 逐字节回到 460 前行为"
        return _out

    _src = dict(patch or {})
    if isinstance(issue, dict) and issue:
        for _k in ("file", "method", "issue_type"):
            if not _src.get(_k) and issue.get(_k):
                _src[_k] = issue[_k]

    _t = make_ticket(_src, root=root, now=_now)
    _out["ticket"] = _t
    if not _t.get("admitted"):
        _out["why"] = str(_t.get("why") or "准入未过")
        return _out

    _e = collect_evidence(_src)
    _out["evidence"] = _e
    if not _e.get("ok"):
        _out["why"] = str(_e.get("why") or "证据生产失败")
        return _out

    _v = verdict(_e, _e["patch"], gates=gates, retry_count=retry_count,
                 root=root, now=_now)
    _out["verdict"] = _v
    _out["patch"] = _e["patch"]
    _out["admitted"] = True

    if _v.get("verdict") == V_APPLY:
        _s = snapshot_before(_e["patch"], root=root, now=_now)
        _out["snapshot"] = _s
        if not _s.get("ok"):
            _out["verdict"] = {"verdict": V_INCONCLUSIVE, "reason": _s.get("why"),
                              "checks": _v.get("checks", {}),
                              "dedup_key": _v.get("dedup_key", ""),
                              "patch_id": _v.get("patch_id", "")}
            _out["why"] = "快照失败 ⇒ 不放行"
    _out["why"] = _out["why"] or str(_v.get("reason") or "")
    note_ledger({"kind": "verdict", "at": _now, "dedup_key": _v.get("dedup_key"),
                 "patch_id": _v.get("patch_id"), "verdict": _v.get("verdict"),
                 "reason": _v.get("reason"), "reprobe_verdict": _e.get("reprobe_verdict"),
                 "problem_gone": _e.get("problem_gone"),
                 "baseline_errors": _e.get("baseline_errors"),
                 "snapshot_id": _out["snapshot"].get("snapshot_id", ""),
                 "schema": SCHEMA}, root=root)
    return _out


def finalize(prep: dict, apply_result: dict, patch_manager: Any = None, *,
             root: str | None = None, now: float | None = None,
             verify_fn: Any = None) -> dict:
    """留痕 → 应用后验证 → 失败真回退 → 失败经验/冷却。

    `apply_result` 是 `maybe_approve_scoped()` 的返回（含 `approved` / `status_written`）。
    """
    _now = float(now if now is not None else time.time())
    _p = prep if isinstance(prep, dict) else {}
    _v = _p.get("verdict") or {}
    _state = str(_v.get("verdict") or V_INCONCLUSIVE)
    _key = str(_v.get("dedup_key") or dedup_key(_p.get("patch") or {}))
    _out: dict = {"ok": False, "applied": False, "verified": False,
                  "rolled_back": False, "lesson": {}, "why": "",
                  "dedup_key": _key, "verdict": _state}
    if not _p.get("admitted"):
        _out["why"] = str(_p.get("why") or "未准入")
        return _out

    _res = apply_result if isinstance(apply_result, dict) else {}
    _applied = bool(_res.get("approved")) and bool(_res.get("status_written", True))
    _out["applied"] = _applied

    _error_type = ""
    if not _applied:
        _out["why"] = f"未应用（裁决 {_state}: {_v.get('reason', '')}）"
        # ★只有「反复补证仍不可判定」才写经验：八闸正常拒绝不是"改错了"，
        #   把它也写进经验库会灌满 452 刀6 的 prompt 提醒段（软保证被稀释）。
        #   真正"改错了"的信号是下面 post_apply_verify_failed 那条。
        if "重试已达上限" in str(_v.get("reason") or ""):
            _error_type = "retry_exhausted"
        note_ledger({"kind": "apply", "at": _now, "dedup_key": _key,
                     "applied": False, "why": _out["why"], "schema": SCHEMA}, root=root)
    else:
        _check = verify_after_apply(_p.get("patch") or {}, verify_fn=verify_fn,
                                   root=root)
        _out["verified"] = bool(_check.get("ok"))
        _out["why"] = str(_check.get("why") or "")
        if _out["verified"]:
            _out["ok"] = True
            note_ledger({"kind": "apply", "at": _now, "dedup_key": _key,
                         "applied": True, "verified": True,
                         "snapshot_id": (_p.get("snapshot") or {}).get("snapshot_id", ""),
                         "why": _out["why"], "schema": SCHEMA}, root=root)
            return _out
        _error_type = "post_apply_verify_failed"
        if bool(_cfg("ENABLE_M460_AUTO_ROLLBACK", True)):
            _rb = restore_snapshot(_p.get("snapshot") or {}, root=root)
            if not _rb.get("ok") and patch_manager is not None:
                _fn = getattr(patch_manager, "rollback_patch", None)
                if callable(_fn):
                    try:
                        _rb2 = _fn(str((_p.get("patch") or {}).get("id") or ""))
                        _rb = {"ok": bool((_rb2 or {}).get("ok")),
                               "why": str((_rb2 or {}).get("reason") or ""),
                               "restored": []}
                    except Exception as _e:
                        silent_exc(_e, where="self_verdict::finalize pm rollback",
                                   level="warning")
            _out["rolled_back"] = bool(_rb.get("ok"))
            _out["why"] += f"；回退: {_rb.get('why')}"
        note_ledger({"kind": "rollback", "at": _now, "dedup_key": _key,
                     "rolled_back": _out["rolled_back"], "why": _out["why"],
                     "schema": SCHEMA}, root=root)

    if _error_type:
        _out["lesson"] = record_lesson(
            key=_key, error_type=_error_type, reason=_out["why"],
            file_path=str((_p.get("patch") or {}).get("file") or ""),
            method=str((_p.get("patch") or {}).get("method") or ""),
            root=root, now=_now)
    note_ledger({"kind": "final", "at": _now, "dedup_key": _key,
                 "verdict": _state, "applied": _out["applied"],
                 "verified": _out["verified"], "rolled_back": _out["rolled_back"],
                 "error_type": _error_type, "why": _out["why"][:300],
                 "schema": SCHEMA}, root=root)
    # ★461 刀3：结构化结果单回传主进程（心跳有明确消费点，不是只打一行日志）。
    #   实盘 460 的子进程回来只做计数 + 日志，k1_offload 是唯一写回口且语义很窄。
    try:
        from nucleus.evolution import runtime_dispatch as _m461_rd
        _m461_rd.note_loop_result(
            patch_id=str((_p.get("patch") or {}).get("id") or ""),
            dedup_key_=_key, verdict=_state,
            applied=_out["applied"], verified=_out["verified"],
            rolled_back=_out["rolled_back"], reason=_out["why"],
            root=root, now=_now)
    except Exception as _e:
        silent_exc(_e, where="self_verdict::finalize 461结果回传", level="debug")
    return _out
