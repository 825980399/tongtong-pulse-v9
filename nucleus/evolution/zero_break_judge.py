# -*- coding: utf-8 -*-
"""★349刀4（P1 · 烛微349审计4）：O3「破零」判定器 + 撤单后派单链监控。

## 为什么需要一个**专门**的判定器

348 刀10 撤掉了唯一的 O3 候选（`CompanionBridge.handshake` 是协议接口，误判），
于是"历史首次 applied"这件事重新归零。归零之后最容易犯的错是**把半通当破零**：

    executed=1 但 applied=False        —— 跑了，没落地（验证拒绝/回滚）
    applied=True 但台账 verified=False —— 文件改了，台账没认（有键无色·铁律156）
    applied=True 且 verified=True，但文件在 `tmp/` 隔离根 —— 那是自证产物，不是生产

审计4 的四条件就是针对这三种半通写的。本模块把四条**写死在同一个函数里**，
返回 `half_through` 逐条指认缺哪一条 —— 不允许任何调用方"看个 bool 就走"。

## 判据的能力边界（写在结论前面·铁律196）

本判定器只回答"**链路是否四条件同立**"，它**不回答**"这次改动是否正确"、
也不回答"误判率是多少"（那是抽验线的活）。所以
`zero_broken=True` 的正确读法是"链路通了"，**不是**"补丁对了"。
"""
from __future__ import annotations

import os
from typing import Any

from nucleus._silent_except import silent_exc

#: 台账侧"已验证"的状态集合（与 `patch_lifecycle` 同源；加新状态时这里必须同步）
VERIFIED_STATUSES = ("runtime_verified", "verified", "applied_verified")

#: 隔离根路径标记（`tmp/` 下的 `*_root`/`*_iso` 一律不算生产）
ISOLATION_MARKERS = ("/tmp/", "\\tmp\\", "/tmp\\", "\\tmp/")


def is_isolation_path(path: Any) -> bool:
    """路径是否指向隔离根（判据用**路径结构**，不看文件名关键字·铁律153）。"""
    _p = str(path or "").replace("\\", "/")
    if not _p:
        return True  # 没路径 ⇒ 无法证明是生产 ⇒ 当隔离处理（宁可少报）
    if "/tmp/" in _p or _p.startswith("tmp/"):
        return True
    _low = _p.lower()
    for _m in ("_root/", "_iso/", "p34", "p35", "worktree"):
        if _m in _low:
            return True
    return False


def judge_zero_break(record: dict,
                     project_root: str | None = None) -> dict:
    """★349刀4①：四条件同立才算"破零"。

    Args:
        record: 一条补丁台账记录（或执行摘要），至少含
            `executed`/`applied`/`status`/`verified`/`file`。
        project_root: 项目根（用于把相对路径判成隔离/生产）。

    返回：
        `zero_broken` —— 四条件**全部**成立
        `checks`      —— 逐条 `{ok, got, expect}`
        `half_through`—— 缺的那几条（空 = 无缺口）
        `verdict`     —— `broken` / `half_through` / `not_started`
    """
    _checks: dict = {}

    # ① executed ≥ 1
    try:
        _ex = int(record.get("executed") or 0)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="zero_break_judge.executed")
        _ex = 0
    _checks["executed_ge_1"] = {"ok": _ex >= 1, "got": _ex, "expect": ">=1"}

    # ② applied is True（**必须是 True，不是"真值"**：字符串 "false" 也是真值）
    _ap = record.get("applied")
    _checks["applied_true"] = {"ok": _ap is True, "got": _ap, "expect": "is True"}

    # ③ 台账 verified（status ∈ VERIFIED_STATUSES 或 verified is True）
    _st = str(record.get("status") or "").strip().lower()
    _vf = record.get("verified")
    _ok3 = (_st in VERIFIED_STATUSES) or (_vf is True)
    _checks["ledger_verified"] = {
        "ok": _ok3, "got": {"status": _st, "verified": _vf},
        "expect": "status∈%s 或 verified=True" % (VERIFIED_STATUSES,),
    }

    # ④ 文件非隔离根
    _f = record.get("file")
    _iso = is_isolation_path(_f)
    _checks["not_isolation_path"] = {
        "ok": not _iso, "got": {"file": _f, "isolation": _iso},
        "expect": "生产路径（非 tmp/*_root/*_iso）",
    }

    _missing = [k for k, v in _checks.items() if not v["ok"]]
    _broken = not _missing
    if _broken:
        _verdict = "broken"
    elif _checks["executed_ge_1"]["ok"]:
        _verdict = "half_through"
    else:
        _verdict = "not_started"
    return {
        "zero_broken": _broken,
        "verdict": _verdict,
        "checks": _checks,
        "half_through": _missing,
        "boundary": ("只判链路四条件是否同立；**不判**补丁是否正确、不判误判率"),
        "project_root": project_root,
    }


def scan_zero_break(patch_history: list,
                    project_root: str | None = None) -> dict:
    """扫整本台账，找"破零"位（`applied=True` 的条目逐条判四条件）。

    返回 `{any_broken, broken_ids, half_ids, candidates}`。
    `half_ids` 是"跑过但没四条件同立"的 id —— 这些是**最需要人看**的，
    因为它们在别处可能已经被当成"成功"写进报告了。
    """
    _broken: list = []
    _half: list = []
    _cand = 0
    for _p in patch_history or []:
        if not isinstance(_p, dict):
            continue
        if _p.get("applied") is not True and not int(_p.get("executed") or 0):
            continue
        _cand += 1
        _r = judge_zero_break(_p,
                             project_root=project_root)
        if _r["zero_broken"]:
            _broken.append(str(_p.get("id") or ""))
        elif _r["half_through"]:
            _half.append({"id": str(_p.get("id") or ""),
                          "missing": _r["half_through"]})
    return {
        "candidates_scanned": _cand,
        "any_broken": bool(_broken),
        "broken_ids": _broken,
        "half_ids": _half,
    }


def monitor_dispatch_chain(patch_history: list,
                           next_top: int = 5) -> dict:
    """★349刀4②：撤单后下个候选的派单链监控。

    回答三个问题（不是"看一眼有没有候选"）：
      1. 撤单的条目**是否真的**离开了候选池（`wont_fix` + `o3_auto_dispatch_ready=False`）；
      2. 下一个候选是否**自然**进链（不是被手工塞进去的）；
      3. 是否被当日入池额度卡住（75号：重置/恢复**不占**额度）。
    """
    _active: list = []
    _wont: list = []
    for _p in patch_history or []:
        if not isinstance(_p, dict):
            continue
        _st = str(_p.get("status") or "").strip().lower()
        if _st in ("wont_fix", "wontfix"):
            _wont.append({"id": _p.get("id"), "ready": _p.get("o3_auto_dispatch_ready")})
            continue
        if _p.get("o3_auto_dispatch_ready") is True and _st in ("approved", "pending"):
            _active.append({"id": _p.get("id"), "status": _st,
                            "method": _p.get("method"),
                            "risk": _p.get("risk_level")})
    _quota = None
    try:
        from nucleus.self_inspector import _m333_intake_rate

        _q = _m333_intake_rate()
        _quota = {"count": _q.get("count"), "limit": _q.get("limit"),
                  "rate_ok": _q.get("rate_ok"),
                  "quota_exempt": _q.get("quota_exempt")}
    except Exception as _e:
        silent_exc(_e, where="zero_break_judge.monitor_dispatch_chain quota")
    return {
        "wont_fix_removed": _wont,
        "wont_fix_all_unready": all(w.get("ready") is not True for w in _wont),
        "active_candidates": _active[:max(1, int(next_top))],
        "active_count": len(_active),
        "quota": _quota,
        "note": ("撤单条目必须 ready=False 才算真离开；额度口径按 75号："
                 "重置/恢复不计入当日入池额度"),
    }


#: ★豁免**种类**白名单。只有这几种才叫"被放行"；
#  `tier:hard_*` 是**判死档**，不是豁免 —— 把它们混进来等于用判死的正确率
#  去污染豁免的召回率（本批首轮实测就踩了这个坑：14 条"泄漏"全是判死档）。
EXEMPTION_KINDS = ("protocol_iface", "test_referenced", "test_ref",
                   "env_defect", "environment_defect")


def recall_audit_sample(samples: list,
                        exemption_kinds: tuple = EXEMPTION_KINDS) -> dict:
    """★349刀4③：豁免召回率评估（76抽20 重放）。

    `samples` 每项：
        `{id, exempted_by, dead_code_true}` —— `dead_code_true` 是**重放得到的真值**
        （不是原判定）。

    核心指标只有一个：**豁免放行真死代码的条数必须为 0**。
    `recall`（豁免召回率）= 被豁免且**确实不是**死代码 / 被豁免总数
      —— 这一项低不等于错，它只说明"豁免面里有大量其实该留下的人"。
    """
    _all = list(samples or [])
    _bad: list = []
    _good = 0
    _by: dict = {}
    _non_exempt: dict = {}
    _n = 0
    for _s in _all:
        _k = str(_s.get("exempted_by") or "")
        if _k not in exemption_kinds:
            # ★不进分母：没被豁免的条目，"它是不是死代码"与豁免召回率无关
            _nk = _k or "not_exempted"
            _non_exempt[_nk] = _non_exempt.get(_nk, 0) + 1
            continue
        _n += 1
        _by[_k] = _by.get(_k, 0) + 1
        if _s.get("dead_code_true") is True:
            _bad.append(str(_s.get("id") or ""))
        else:
            _good += 1
    return {
        "sample_size": _n,
        "all_rows": len(_all),
        "non_exempt_count": len(_all) - _n,
        "non_exempt_by": _non_exempt,
        "exempted_true_dead_code": len(_bad),
        "leaked_ids": _bad,
        "recall": round(_good / _n, 4) if _n else None,
        "by_exemption": _by,
        "exemption_kinds": list(exemption_kinds),
        "gate": "豁免放行真死代码必须为 0",
        "gate_ok": len(_bad) == 0,
        "boundary": ("分母只含**豁免**条目；判死档(`tier:hard_*`)不进来 —— "
                     "它们判对了是本职，判错了归误判率那条线管"),
    }


def production_patch_history(project_root: str | None = None) -> list:
    """读生产台账（只读；读不到返回空 list，**不当成"没有补丁"**以外的结论）。"""
    _root = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    _p = os.path.join(str(_root), "data", "patches", "patch_history.json")
    if not os.path.isfile(_p):
        return []
    try:
        import json

        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
        return _d if isinstance(_d, list) else []
    except Exception as _e:
        silent_exc(_e, where="zero_break_judge.production_patch_history")
        return []
