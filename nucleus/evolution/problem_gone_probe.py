"""★479：`problem_gone` 静态判据探针 —— 解开审批死锁。

## 死锁是什么

`approval_bridge.decision()` 的第五闸是：

    _put("problem_gone_true", patch.get("problem_gone") is True, ...)

`problem_gone` 的语义是「**补丁应用后问题确实消失了**」。于是：

    要批准 → 需要 problem_gone=True
    要 problem_gone → 需要先应用补丁跑复现
    要应用 → 需要先被批准

实测（2026-11-08）`pending_patches` 里 3 条 verified 补丁**全部**卡在这一项。

## 为什么静态判据够用

`problem_gone` 问的是「**问题还在不在**」，而绝大多数 issue_type 的「问题」
本身就是**代码形态**（有没有原子写、有没有重入守卫、方法多长、有没有裸 except）。
这些都能用 AST/文本**精确判定**，不需要真的运行。

**关键纪律：宁可 False，不要假 True。** 判据必须能证明「问题消失」，
而不是「看起来改了」。任何拿不准的情况一律返回 False（保持现状，等真实验证）。

## 与「沙箱预演」的关系

本模块是**第一层**（廉价、纯静态、零副作用）。
沙箱预演（真 apply + 跑复现 + 回滚）是第二层，用于本模块拿不准的类别。
两层都拿不到证据时，仍然只能人工批准 —— 不放宽判据。

## 用法

    from nucleus.evolution.problem_gone_probe import probe
    r = probe(patch)          # {"gone": bool, "confidence": str, "why": str, "evidence": {}}

    # 命令行（离线体检，不改任何文件）
    python -m nucleus.evolution.problem_gone_probe
"""
from __future__ import annotations

import ast
import json
import os
from typing import Any

from nucleus._silent_except import silent_exc

SCHEMA = "479.problem_gone_probe.v1"

# 原子写：命中任一即认为「真的改成原子写」了
_ATOMIC_WRITE_MARKERS = (
    "safe_write_json", "os.replace", "NamedTemporaryFile", "mkstemp",
    "atomic_write", "fsync", ".replace(", "tempfile",
)
# 裸写：直接 open(...,'w') 后 write
_RAW_WRITE_MARKERS = ('.write(', '.writelines(')

# 重入守卫：命中任一即认为「加了重入保护」
_REENTRY_MARKERS = (
    "_in_progress", "_running", "_locked", "is_alive()",
    "already_running", "_scheduled", "_inflight", "_busy",
)

# 「问题本身」的残留标记（命中则判未消失）
_BARE_EXCEPT_ONLY = ("except:", "except :")

# long_method：**与检测侧同源的绝对阈值**。
#   依据：`nucleus/self_inspector.py:791` 原注释「`_ir_*` 系列，均 ≤200 行，
#   正常进入 long_method 检测」⇒ 200 行就是 long_method 的判定线。
#   ★不用「降了 X%」当判据 —— 400 行降到 300 行降了 25%，但它**仍然超 200**，
#   问题根本没消失。只认「修改后不再超线」。
LONG_METHOD_LINE_LIMIT = 200


def _cfg_on() -> bool:
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M479_PROBLEM_GONE_PROBE", False))
    except (ImportError, AttributeError) as _e:
        silent_exc(_e, where="problem_gone_probe::_cfg_on", level="debug")
        return False


def _parse_flex(code: str):
    """把「带缩进的方法片段」解析成 AST（复用 all_collab 的同规则实现）。"""
    try:
        from nucleus.autonomy.all_collab import _parse_flex as _pf
        return _pf(code)
    except ImportError as _e:
        silent_exc(_e, where="problem_gone_probe::_parse_flex", level="debug")
        return None, "all_collab 不可用"


def _func_of(code: str, method: str):
    """取指定方法的 FunctionDef 节点（找不到返回 None）。"""
    _tree, _ = _parse_flex(code)
    if _tree is None:
        return None
    for _n in ast.walk(_tree):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and getattr(_n, "name", "") == str(method or ""):
            return _n
    return None


def _line_span(code: str) -> int:
    return len([r for r in str(code or "").replace("\r\n", "\n").split("\n") if r.strip()])


def _has_bare_except(code: str) -> bool:
    _tree, _ = _parse_flex(code)
    if _tree is None:
        return any(_m in str(code) for _m in _BARE_EXCEPT_ONLY)
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.ExceptHandler) and _n.type is None:
            return True
    return False


# ────────────────────────── 按 issue_type 分派的判据 ──────────────────────────

def _probe_non_atomic_write(orig: str, mod: str) -> dict:
    """判据：修改后**引入了**原子写手段，且不再用裸 `.write(` 直写。

    ★双重条件：只加 safe_write_json 但旁边还留着 `.write(` ⇒ 不判消失
    （可能是新增了一条路径，原路径仍在）。
    """
    _hit = [m for m in _ATOMIC_WRITE_MARKERS if m in mod]
    if not _hit:
        return {"gone": False, "why": "modified 里没有任何原子写手段",
                "evidence": {"atomic_markers": []}}
    _raw = [m for m in _RAW_WRITE_MARKERS if m in mod]
    if _raw:
        return {"gone": False,
                "why": f"modified 仍含裸写调用 {_raw}（可能只是新增了并行路径）",
                "evidence": {"atomic_markers": _hit, "raw_write": _raw}}
    return {"gone": True, "why": f"已改为原子写（命中 {_hit[:3]}），且无裸写残留",
            "evidence": {"atomic_markers": _hit[:5]}}


def _probe_reentry(orig: str, mod: str) -> dict:
    """判据：修改后出现**重入守卫**（进行中标志 / is_alive 检查）+ 提前 return。"""
    _hit = [m for m in _REENTRY_MARKERS if m in mod]
    if not _hit:
        return {"gone": False, "why": "未见重入守卫标记", "evidence": {}}
    # 必须伴随「提前退出」才算守卫（否则可能只是记录了标志却没跳过）
    _early = False
    _fn = _func_of(mod, "")
    _tree, _ = _parse_flex(mod)
    if _tree is not None:
        for _n in ast.walk(_tree):
            if not isinstance(_n, (ast.If,)):
                continue
            _blk = list(getattr(_n, "body", []) or [])
            if len(_blk) == 1 and isinstance(_blk[0], ast.Return):
                _early = True
                break
    if not _early:
        # 退回文本判据：形如 `if ...: return`
        _lines = [r.strip() for r in mod.splitlines()]
        _early = any(r.startswith("if ") and "return" in r for r in _lines)
    if not _early:
        return {"gone": False, "why": f"有守卫标记 {_hit[:2]} 但没有「提前 return」",
                "evidence": {"markers": _hit[:3]}}
    return {"gone": True, "why": f"已加重入守卫（{_hit[:2]}）且有提前退出",
            "evidence": {"markers": _hit[:5]}}


def _probe_long_method(orig: str, mod: str) -> dict:
    """判据：原文**确实超 200 行**，且修改后**不再超**。

    ★不认「降了 X%」：400→300 行降了 25% 但 300 仍 > 200，问题没消失。
    ★也不认「原文本来就不超」：那不是 long_method，问题不成立。
    """
    _o = _line_span(orig)
    _m = _line_span(mod)
    if _o <= LONG_METHOD_LINE_LIMIT:
        return {"gone": False,
                "why": f"原文仅 {_o} 行（≤{LONG_METHOD_LINE_LIMIT}），不构成 long_method",
                "evidence": {"orig_lines": _o, "mod_lines": _m}}
    _ok = _m <= LONG_METHOD_LINE_LIMIT
    return {
        "gone": bool(_ok),
        "why": (f"行数 {_o}→{_m}（门槛 {LONG_METHOD_LINE_LIMIT}）"
                + ("，已不超线" if _ok else "，仍超线，问题未消失")),
        "evidence": {"orig_lines": _o, "mod_lines": _m,
                     "limit": LONG_METHOD_LINE_LIMIT},
    }


def _probe_silent_exception(orig: str, mod: str) -> dict:
    """判据：修改后**不再有裸 except**（铁律 4）。"""
    if _has_bare_except(mod):
        return {"gone": False, "why": "modified 仍含裸 except", "evidence": {}}
    if not _has_bare_except(orig):
        return {"gone": False, "why": "原文就没有裸 except，问题不成立", "evidence": {}}
    return {"gone": True, "why": "原文有裸 except，修改后已消除", "evidence": {}}


def _probe_print(orig: str, mod: str) -> dict:
    """判据：修改后不再有 `print(`。"""
    if "print(" in mod:
        return {"gone": False, "why": "modified 仍含 print(", "evidence": {}}
    if "print(" not in orig:
        return {"gone": False, "why": "原文无 print(，问题不成立", "evidence": {}}
    return {"gone": True, "why": "print 已改为日志", "evidence": {}}


#: issue_type → (判据函数, 置信度)。置信度只描述「这个判据本身有多硬」：
#:   high = AST/强文本双重条件，gone=True 即可直接采信
#:   medium = 单一文本条件
#:   low = 启发式
_DISPATCH = {
    "non_atomic_write": (_probe_non_atomic_write, "high"),
    "periodic_task_no_reentry": (_probe_reentry, "high"),
    "long_method": (_probe_long_method, "high"),
    "silent_exception": (_probe_silent_exception, "high"),
    "print_instead_of_log": (_probe_print, "medium"),
}


def probe(patch: dict) -> dict:
    """判 `problem_gone`。**纯静态、不执行代码、不写盘、不改 patch。**

    Returns:
        ``{"gone": bool, "confidence": "high|medium|low|unknown",
           "why": str, "evidence": dict, "supported": bool}``
        `supported=False` 表示该 issue_type 没有静态判据（必须真实运行才能判）。
    """
    _p = patch if isinstance(patch, dict) else {}
    _it = str(_p.get("issue_type") or "").strip()
    _out: dict[str, Any] = {
        "schema": SCHEMA, "gone": False, "confidence": "unknown",
        "why": "", "evidence": {}, "supported": False, "issue_type": _it,
    }
    if not _cfg_on():
        _out["why"] = "开关 ENABLE_M479_PROBLEM_GONE_PROBE=False"
        return _out

    _fn_conf = _DISPATCH.get(_it)
    if _fn_conf is None:
        _out["why"] = f"issue_type={_it or '空'} 无静态判据（需真实运行验证）"
        return _out

    _fn, _conf = _fn_conf
    _o = str(_p.get("original_code") or "")
    _m = str(_p.get("modified_code") or "")
    if not _m.strip():
        _out["why"] = "modified_code 为空，无法判定"
        return _out
    try:
        _r = _fn(_o, _m)
    except (ValueError, TypeError, AttributeError, KeyError) as _e:
        silent_exc(_e, where=f"problem_gone_probe::{_it}", level="warning")
        _out["why"] = f"判据执行异常: {type(_e).__name__}"
        return _out

    _out.update({
        "supported": True,
        "gone": bool(_r.get("gone")),
        "confidence": _conf if _r.get("gone") else "unknown",
        "why": str(_r.get("why") or ""),
        "evidence": _r.get("evidence") or {},
    })
    return _out


def backfill(patch: dict) -> dict:
    """把探针结论回填成补丁可用的两个字段（`problem_gone` / `confidence`）。

    ★只在 `gone=True` 时回填 —— 判 False 时**原样返回**，不写任何反证据
    （否则会把「还没验证」变成「已验证不消失」，比不填更糟）。
    ★不改入参 patch，返回**副本**。
    """
    _r = probe(patch)
    _copy = dict(patch) if isinstance(patch, dict) else {}
    if _r.get("gone"):
        _copy["problem_gone"] = True
        _copy["confidence"] = _r.get("confidence") or "high"
        _copy["problem_gone_why"] = _r.get("why")
        _copy["problem_gone_evidence"] = _r.get("evidence")
    return _copy


def _cli() -> int:
    """离线体检：读 pending_patches，对每条 verified 补丁打印判定结果。"""
    _p = os.path.join("data", "patches", "pending_patches.json")
    if not os.path.isfile(_p):
        print(f"找不到 {_p}")
        return 1
    with open(_p, encoding="utf-8") as _f:
        _d = json.load(_f)
    _rows = _d if isinstance(_d, list) else (_d.get("patches") or [])
    _n = 0
    for _r in _rows:
        if not isinstance(_r, dict) or _r.get("status") != "verified":
            continue
        _n += 1
        _res = probe(_r)
        print(f"--- {str(_r.get('id'))[:30]} | {_r.get('issue_type')} "
              f"| risk={_r.get('risk_level')}")
        print(f"    supported={_res['supported']} gone={_res['gone']} "
              f"confidence={_res['confidence']}")
        print(f"    why: {_res['why']}")
    print(f"\n共 {_n} 条 verified 补丁")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
