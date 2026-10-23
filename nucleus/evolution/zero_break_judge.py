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


# ===== ★350刀5：判死性误报率评估（97号）=====
#   ★口径纪律：判死性误报率 ≠ 存在性误报率（烛微 350 审计 4 钉死）。
#     · 存在性误报（349刀9）：判定说"这个符号不存在了"，其实还在；
#     · 判死性误报（本刀）：判定说"这段代码是死的"，其实还活着（有引用/被调用）。
#   两者**分列**，绝不相加、绝不互相折算 —— 它们是两个不同问题的错误率。

DEATH_JUDGED_DEAD = "dead"
DEATH_JUDGED_ALIVE = "alive"
DEATH_JUDGED_UNKNOWN = "unknown"

#: 真值来源（本刀只用这一种，写在结论前面，铁律196）
TRUTH_SOURCE = "textual_reference_count"

#: 判死档前缀（这些 tier 的判定**在判死**，不是豁免）
DEATH_TIERS = ("hard_strict", "hard_loose", "hard_unverified", "framework_hook")

#: 判死**字面量族**（池里的 issue_type 就是这么写的）
DEATH_ISSUE_TYPES = ("dead_code", "dead_code_candidate", "dead_code_suspect")


def _iter_py_files(project_root: str, max_files: int = 6000):
    _skip = {".git", "tmp", "__pycache__", ".bak_batch", "node_modules",
             "logs", "data", ".venv", "venv", "site-packages"}
    _n = 0
    for _dp, _dns, _fns in os.walk(project_root):
        _dns[:] = [d for d in _dns
                   if d not in _skip and not d.startswith(".bak_batch")]
        for _fn in _fns:
            if not _fn.endswith(".py"):
                continue
            _n += 1
            if _n > max_files:
                return
            yield os.path.join(_dp, _fn)


def _zbj_root(project_root: str | None = None) -> str:
    """项目根（按标记文件**有界上溯**，铁律187；不写死上溯级数）。"""
    if project_root:
        return str(project_root)
    _d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.isfile(os.path.join(_d, "main.py")) and os.path.isfile(
                os.path.join(_d, "config.py")):
            return _d
        _nd = os.path.dirname(_d)
        if _nd == _d:
            break
        _d = _nd
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def reference_truth_batch(project_root: str, items: list, *,
                           max_files: int = 6000) -> list:
    """一次遍历给**多条**样本定真值（避免 20 条样本各走一遍全仓）。

    为什么要批量：单条 `reference_truth` 每次都要读全仓 .py，
    20 条样本 = 20 次全仓扫 ⇒ 实测会把一次评估拖到分钟级。
    改成一次遍历 + 一个名字交替正则，结果与单条口径一致。
    """
    _items = list(items or [])
    _names = {}
    for _it in _items:
        _m = str(_it.get("method") or "").strip()
        if _m:
            _names.setdefault(_m, set()).add(
                os.path.abspath(os.path.join(project_root, str(_it.get("file") or ""))))
    _out = [{"dead": None, "refs": None, "why": "method 为空（不可判）",
             "truth_source": TRUTH_SOURCE} for _ in _items]
    if not _names:
        return _out
    import re as _re
    _pat = _re.compile(r"\b(?:" + "|".join(
        _re.escape(_n) for _n in sorted(_names, key=len, reverse=True)) + r")\b")
    _total = {_n: 0 for _n in _names}
    _in_def_file = {_n: 0 for _n in _names}
    _scanned = 0
    for _p in _iter_py_files(project_root, max_files=max_files):
        _scanned += 1
        try:
            _txt = open(_p, "r", encoding="utf-8", errors="replace").read()
        except Exception as _e:                                # noqa: BLE001
            silent_exc(_e, where="zero_break_judge.reference_truth_batch read",
                       level="debug")
            continue
        _hits: dict = {}
        for _mm in _pat.finditer(_txt):
            _k = _mm.group(0)
            _hits[_k] = _hits.get(_k, 0) + 1
        if not _hits:
            continue
        _ap = os.path.abspath(_p)
        for _k, _c in _hits.items():
            _total[_k] += _c
            if _ap in _names.get(_k, ()):
                _in_def_file[_k] += _c
    for _i, _it in enumerate(_items):
        _m = str(_it.get("method") or "").strip()
        if not _m:
            continue
        # 定义文件里只扣掉 1 处（`def <name>` 那一行）。
        #   多扣会把"docstring 里提到自己" 也当成定义，从而把**真死**误判成活。
        _refs = max(0, _total[_m] - (1 if _in_def_file.get(_m, 0) > 0 else 0))
        _dead = (_refs == 0)
        _out[_i] = {"dead": _dead, "refs": _refs, "scanned_files": _scanned,
                    "truth_source": TRUTH_SOURCE,
                    "boundary": ("按方法名文本引用数判活/死；有引用⇒必非死代码，"
                                 "无引用⇒**不等于**确认死代码"),
                    "why": "" if _dead else "被 %d 处引用" % _refs}
    return _out


def reference_truth(project_root: str, file: str, method: str, *,
                    max_files: int = 6000) -> dict:
    """判死**真值**：按「方法名的文本引用数」判活/死。

    ★能力边界（铁律196，必须说在结论前面）：
      本函数数的是**文本出现次数**，不是真可达性。它能证伪"被别处调用了"
      （有引用 ⇒ 一定不是死代码），但**证不了**"没引用 ⇒ 一定是死代码"
      （反射/字符串拼接/动态分派都数不到）。因此：
        · `dead=True`  只表示"扫到的文件里除定义外没有一处提到这个名字"；
        · 结论里必须同句声明这一口径，不许把它写成"确认是死代码"。

    排除项：定义文件自身的那一处（`def method` / `async def method`）。
    """
    _m = str(method or "").strip()
    if not _m:
        return {"dead": None, "refs": None, "why": "method 为空（不可判）",
                "truth_source": TRUTH_SOURCE}
    _re = None
    try:
        import re as _re_mod
        _re = _re_mod.compile(r"\b" + _re_mod.escape(_m) + r"\b")
    except Exception as _e:                                     # noqa: BLE001
        silent_exc(_e, where="zero_break_judge.reference_truth regex")
        return {"dead": None, "refs": None, "why": "正则构造失败",
                "truth_source": TRUTH_SOURCE}
    _self_abs = os.path.abspath(os.path.join(project_root, str(file or "")))
    _refs = 0
    _files_hit = 0
    _scanned = 0
    for _p in _iter_py_files(project_root, max_files=max_files):
        _scanned += 1
        try:
            _txt = open(_p, "r", encoding="utf-8", errors="replace").read()
        except Exception as _e:                                 # noqa: BLE001
            silent_exc(_e, where="zero_break_judge.reference_truth read", level="debug")
            continue
        _hits = len(_re.findall(_txt))
        if not _hits:
            continue
        if os.path.abspath(_p) == _self_abs:
            # 去掉定义那一处（可能还有 docstring 里的提及，保守地只减 1）
            _hits = max(0, _hits - 1)
        if _hits:
            _refs += _hits
            _files_hit += 1
    _dead = (_refs == 0)
    return {"dead": _dead, "refs": _refs, "files_hit": _files_hit,
            "scanned_files": _scanned, "truth_source": TRUTH_SOURCE,
            "boundary": ("按方法名文本引用数判活/死；有引用⇒必非死代码，"
                         "无引用⇒**不等于**确认死代码（反射/动态分派数不到）"),
            "why": "" if _dead else "被 %d 处引用（%d 个文件）" % (_refs, _files_hit)}


def death_false_positive_rate(samples: list, *,
                              existence_rate=None) -> dict:
    """判死性误报率（双报），并与存在性误报率**分列**。

    `samples` 每项：`{id, judged, dead_code_true, tier?}`
      · `judged`          —— 判定结论（dead/alive/unknown）
      · `dead_code_true`  —— 真值（`reference_truth` 的 `dead`），None=不可判

    两个率各按自己的分母算，**不共用一个 total**（铁律160：分母须与被测对象同域）：
      · 判死性误报率 = 判死 ∧ 真值非死 / 判死判定总数
      · 判死性漏判率 = 判活 ∧ 真值为死 / 判活判定总数
    """
    _dead_n = _dead_fp = 0
    _alive_n = _alive_miss = 0
    _unknown = 0
    _nolen = 0
    _fp_ids: list = []
    _miss_ids: list = []
    _by_tier: dict = {}
    for _s in (samples or []):
        if not isinstance(_s, dict):
            continue
        _j = str(_s.get("judged") or DEATH_JUDGED_UNKNOWN)
        _t = _s.get("dead_code_true")
        _tier = str(_s.get("tier") or "")
        if _tier:
            _b = _by_tier.setdefault(_tier, {"n": 0, "fp": 0})
        if _t is None:
            _nolen += 1
            continue
        if _j == DEATH_JUDGED_DEAD:
            _dead_n += 1
            if _tier:
                _by_tier[_tier]["n"] += 1
            if _t is False:
                _dead_fp += 1
                _fp_ids.append(str(_s.get("id") or ""))
                if _tier:
                    _by_tier[_tier]["fp"] += 1
        elif _j == DEATH_JUDGED_ALIVE:
            _alive_n += 1
            if _tier:
                _by_tier[_tier]["n"] += 1
            if _t is True:
                _alive_miss += 1
                _miss_ids.append(str(_s.get("id") or ""))
        else:
            _unknown += 1
    _fp_rate = round(_dead_fp / _dead_n, 4) if _dead_n else None
    _miss_rate = round(_alive_miss / _alive_n, 4) if _alive_n else None
    return {
        "schema": "death_false_positive/350.v1",
        "truth_source": TRUTH_SOURCE,
        "sample_size": len(samples or []),
        # ← 判死性口径（本刀）
        "death_judged_n": _dead_n,
        "death_false_positive": _dead_fp,
        "death_false_positive_rate": _fp_rate,
        "death_alive_n": _alive_n,
        "death_missed": _alive_miss,
        "death_false_negative_rate": _miss_rate,
        "undetermined_truth": _nolen,
        "judged_unknown": _unknown,
        "fp_ids": _fp_ids[:20],
        "miss_ids": _miss_ids[:20],
        "by_tier": _by_tier,
        # ← 存在性口径（349刀9，**另一条线**）
        "existence_false_positive_rate": existence_rate,
        "columns": {
            "death": "death_false_positive_rate",
            "existence": "existence_false_positive_rate",
        },
        "separation_note": ("两条线分列：判死性=说它死其实活；"
                            "存在性=说它不在其实在。不相加、不互折"),
        "boundary": ("真值=方法名文本引用数（`reference_truth`）。"
                     "无引用只表示扫不到引用，**不等于**确认死代码"),
        "rate_none_means": "无样本（不是 0）",
    }


def collect_death_samples(project_root: str | None = None,
                          pool_entries: list | None = None,
                          universe: int = 76, limit: int = 20) -> list:
    """按「76 抽 20 重放」的办法组样本（349 刀4 同一评估法，本刀扩样本用）。

    `universe` 是候选面（默认 76 —— 与烛微审计同基数），`limit` 是实际重放条数。
    抽样用**等距**而不是随机：结果可复算，换个人跑同一份池得到同一组样本。
    """
    _rows = list(pool_entries or [])
    if not _rows:
        try:
            from nucleus import audit_problem_pool as _ap
            _p = _ap.load_pool()
            if _p.get("state") == "ok":
                _rows = _p["entries"]
        except Exception as _e:                                 # noqa: BLE001
            silent_exc(_e, where="zero_break_judge.collect_death_samples pool")
            _rows = []
    _cands = []
    for _e in _rows:
        _tier = str(_e.get("evidence_tier") or _e.get("gate_verdict") or "")
        # ★350：判死候选面 = issue_type 族 ∨ gate_verdict=OPEN ∨ 判死档
        #   实测：生产池 677 条里 622 条是 `issue_type=dead_code`，
        #   而 `gate` 字段当前普遍为空（`_m349_gate_field` 读不到就不填）
        #   —— 只看 `gate_verdict` 会得到 0 条样本，那是口径错不是"没死代码"。
        _is_death = (str(_e.get("issue_type") or "") in DEATH_ISSUE_TYPES
                     or str(_e.get("gate_verdict") or "") == "OPEN"
                     or any(_tier.startswith(_d) for _d in DEATH_TIERS))
        if not _is_death:
            continue
        _cands.append({"id": str(_e.get("dedup_key") or _e.get("id") or ""),
                       "file": str(_e.get("file") or ""),
                       "method": str(_e.get("method") or ""),
                       "judged": DEATH_JUDGED_DEAD, "tier": _tier})
    _uni = _cands[:int(universe)]
    _n = min(int(limit), len(_uni))
    if _n <= 0:
        return []
    _step = max(1, len(_uni) // _n)
    _picked = [_uni[i * _step] for i in range(_n)]
    _truth = reference_truth_batch(project_root or _zbj_root(), _picked)
    _out = []
    for _s, _t in zip(_picked, _truth):
        _row = dict(_s)
        _row["dead_code_true"] = _t.get("dead")
        _row["truth_refs"] = _t.get("refs")
        _row["truth_why"] = _t.get("why")
        _row["truth_scanned_files"] = _t.get("scanned_files")
        _out.append(_row)
    return _out


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
