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
    #    # ★378刀1（T-378-破零判据同源-1）：本字段**只认台账**（pending/history 的 `applied`），
    #    `watch_candidate` 不得用本拍沙箱结果覆盖它（沙箱成功 ≠ 落地，见 373刀5）。
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


# ===== ★351刀1：判死真值口径修复 —— 「类.方法」限定作用域 =====
#   350 刀5 首报 70%（**上界**）：真值按**裸方法名**全仓计数，不同类的同名方法
#   被算成引用 ⇒ "活"的判据过宽 ⇒ 误报率被抬高。本刀把每个命中拆成四类归属，
#   只有**确定指向本方法**的才算引用（铁律 162：判据按语义单元，不按字符串）。

REF_SCOPE_VERSION = "351.refscope.v1"

SCOPE_STRONG = "strong"            # 确定指向本方法（类限定 / 同类 self. / 模块级裸调用）
SCOPE_OTHER_CLASS = "other_class"  # 别的接收者上的同名方法 —— 同名碰撞，不算引用
SCOPE_BARE = "bare"                # 裸名但本方法在类内 —— 无法证明指向本类，不算引用
SCOPE_DEF = "def_line"             # 定义行本身（`def name` / `async def name`）

_RECV_RE = None


def _recv_re():
    """匹配行内「接收者.」结尾（延迟编译）。"""
    global _RECV_RE
    if _RECV_RE is None:
        import re as _re
        # 351刀1：接收者可能是 Cls. 也可能是 Cls(...).（实例化后调用）
        _RECV_RE = _re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\s*(?:\([^()]*\))?\s*\.\s*$")
    return _RECV_RE


def _def_owner(file_abs: str, method: str) -> dict:
    """解析定义文件，回答「这个方法属于哪个类」。

    返回 `{cls, span, module_level, state}`：
      · `cls=None, module_level=True`  —— 模块级函数（裸调用即引用）
      · `cls=<类名>, span=(起, 止)`     —— 类内方法（只有限定引用才算）
      · `cls="?", state=...`          —— 解析不出（**不猜**，如实报不可判）
    """
    _res = {"cls": "?", "span": None, "module_level": False, "state": "unresolved"}
    if not file_abs or not os.path.isfile(file_abs):
        _res["state"] = "no_file"
        return _res
    try:
        import ast as _ast
        with open(file_abs, "r", encoding="utf-8", errors="replace") as _f:
            _tree = _ast.parse(_f.read())
    except Exception as _e:                                     # noqa: BLE001
        silent_exc(_e, where="zero_break_judge._def_owner parse", level="debug")
        _res["state"] = "parse_failed"
        return _res
    import ast as _ast2
    _defs = (_ast2.FunctionDef, _ast2.AsyncFunctionDef)
    for _node in _tree.body:
        if isinstance(_node, _defs) and getattr(_node, "name", "") == method:
            _res = {"cls": None, "span": None, "module_level": True, "state": "module_level"}
            return _res
    for _node in _tree.body:
        if not isinstance(_node, _ast2.ClassDef):
            continue
        for _sub in _node.body:
            if isinstance(_sub, _defs) and getattr(_sub, "name", "") == method:
                return {"cls": _node.name,
                        "span": (int(getattr(_node, "lineno", 0)),
                                 int(getattr(_node, "end_lineno", 0) or 0)),
                        "module_level": False, "state": "class_method"}
    return _res


def _is_test_path(p: str) -> bool:
    """路径是否属于测试面（测试引用**不构成生产可达性**，必须分列·铁律160）。"""
    _s = str(p or "").replace("\\", "/").lower()
    if "/tests/" in _s or "/test/" in _s:
        return True
    return os.path.basename(_s).startswith("test_")


def reference_truth_scoped(project_root: str, items: list, *,
                           max_files: int = 6000) -> list:
    """★351刀1①：按「类.方法」限定作用域计引用（消解同名碰撞）。

    与 `reference_truth_batch`（裸名口径）的差别**只有一处**：命中先判归属，
    只有 `SCOPE_STRONG` 计入 `refs`；`other_class` / `bare` 分列另报，**不折算**。
    结果里同时给 `refs_prod` / `refs_test`：测试引用不算生产可达（`dead_prod`）。
    """
    import re as _re

    _items = list(items or [])
    _meta: list = []
    for _it in _items:
        _m = str(_it.get("method") or "").strip()
        _f = str(_it.get("file") or "").strip()
        _abs = os.path.abspath(os.path.join(project_root, _f)) if _f else ""
        _own = (_def_owner(_abs, _m) if (_m and _abs)
                else {"cls": "?", "span": None, "module_level": False,
                      "state": "no_file"})
        _meta.append({"method": _m, "file_abs": _abs, "owner": _own})

    _out = [{"dead": None, "refs": None, "why": "method 为空（不可判）",
             "truth_source": TRUTH_SOURCE, "ref_scope": REF_SCOPE_VERSION}
            for _ in _items]
    _by_name: dict = {}
    for _i, _md in enumerate(_meta):
        if _md["method"]:
            _by_name.setdefault(_md["method"], []).append(_i)
    if not _by_name:
        return _out

    _stat = [{"strong": 0, "prod": 0, "test": 0, "other_class": 0,
              "bare": 0, "def_line": 0} for _ in _items]
    _pat = _re.compile(r"\b(?:" + "|".join(
        _re.escape(_n) for _n in sorted(_by_name, key=len, reverse=True)) + r")\b")
    _recv = _recv_re()
    _scanned = 0
    for _p in _iter_py_files(project_root, max_files=max_files):
        _scanned += 1
        try:
            with open(_p, "r", encoding="utf-8", errors="replace") as _f:
                _txt = _f.read()
        except Exception as _e:                                 # noqa: BLE001
            silent_exc(_e, where="zero_break_judge.reference_truth_scoped read",
                       level="debug")
            continue
        if not _txt:
            continue
        _ap = os.path.abspath(_p)
        _is_test = _is_test_path(_ap)
        _lines = _txt.splitlines()
        for _mm in _pat.finditer(_txt):
            _k = _mm.group(0)
            if _k not in _by_name:
                continue
            _nl = _txt.count("\n", 0, _mm.start())
            _col = _mm.start() - (_txt.rfind("\n", 0, _mm.start()) + 1)
            _line = _lines[_nl] if 0 <= _nl < len(_lines) else ""
            _pre = _line[:max(0, _col)]
            _stripped = _pre.strip()
            _is_def_line = (_stripped.startswith("def ")
                            or _stripped.startswith("async def "))
            _m2 = _recv.search(_pre)
            for _idx in _by_name[_k]:
                _own = _meta[_idx]["owner"]
                _cls = _own.get("cls")
                _span = _own.get("span")
                _same = (_ap == _meta[_idx]["file_abs"])
                _st = _stat[_idx]
                if _is_def_line:
                    _st["def_line"] += 1
                    continue
                if _m2:
                    _r = _m2.group(1)
                    if _cls and _cls != "?" and _r == _cls:
                        _st["strong"] += 1
                        _st["test" if _is_test else "prod"] += 1
                    elif (_r in ("self", "cls") and _cls and _cls != "?"
                          and _same and _span and _span[0] <= _nl + 1 <= _span[1]):
                        _st["strong"] += 1
                        _st["test" if _is_test else "prod"] += 1
                    else:
                        _st["other_class"] += 1
                elif _own.get("module_level"):
                    _st["strong"] += 1
                    _st["test" if _is_test else "prod"] += 1
                else:
                    _st["bare"] += 1

    for _i, _it in enumerate(_items):
        _m = _meta[_i]["method"]
        if not _m:
            continue
        _st = _stat[_i]
        _own = _meta[_i]["owner"]
        _out[_i] = {
            "dead": (_st["strong"] == 0),
            "refs": _st["strong"],
            "refs_prod": _st["prod"],
            "refs_test": _st["test"],
            "refs_other_class_excluded": _st["other_class"],
            "refs_bare_excluded": _st["bare"],
            "def_lines": _st["def_line"],
            "dead_prod": (_st["prod"] == 0),
            "cls": _own.get("cls"),
            "owner_state": _own.get("state"),
            "scanned_files": _scanned,
            "truth_source": TRUTH_SOURCE,
            "ref_scope": REF_SCOPE_VERSION,
            "boundary": ("只把**确定指向本方法**的引用算作活（类限定 / 同类 self. / "
                         "模块级裸调用）；别的类同名方法(\"other_class\")与类内裸名"
                         "(\"bare\")分列不计；测试引用单列 refs_prod/refs_test。"
                         "无引用仍**不等于**确认死代码"),
            "why": ("" if _st["strong"] == 0
                    else "被 %d 处限定引用（prod %d / test %d；排除 other_class %d / bare %d）"
                         % (_st["strong"], _st["prod"], _st["test"],
                            _st["other_class"], _st["bare"])),
        }
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
                              existence_rate=None,
                              calibration: dict | None = None) -> dict:
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
        "boundary": ("真值=方法名文本引用数（scoped 口径见 `reference_truth_scoped`）。"
                     "无引用只表示扫不到引用，**不等于**确认死代码"),
        "rate_none_means": "无样本（不是 0）",
        # ★351刀1③：校准档位随结论一起返回（不给调用方"自己去比阈值"的机会）
        "calibration": calibration,
    }


# ===== ★351刀1③：判死器校准档位（点估计 → 动作）=====
#   阈值是本批**代码面**的唯一出处：要改校准线只改这里。
CALIBRATION_SCHEMA = "death_calibration/351.v1"
CALIBRATION_THRESHOLDS = {
    "candidate_only": 0.20,   # > 20%  ⇒ 判死器只产候选，不进池
    "split_by_klass": 0.10,   # 10-20% ⇒ 按 klass 分池分流
    # < 10% ⇒ 维持（照旧入池）
}
CALIBRATION_ACTIONS = ("candidate_only", "split_by_klass", "keep", "unknown")


def calibration_decision(rate, *, sample_size: int = 0,
                         source: str = "") -> dict:
    """点估计 → 校准档位。`rate=None`（无样本）⇒ `unknown` 且**不改行为**。

    ★铁律156：无样本不许静默降级成 "keep"。`unknown` 明确表示"没测出来"，
    调用方拿到 `unknown` 时行为维持现状，但**必须**把这一事实写进留痕。
    """
    if rate is None:
        return {
            "schema": CALIBRATION_SCHEMA, "rate": None, "band": "no_sample",
            "action": "unknown", "sample_size": int(sample_size or 0),
            "reason": "点估计无样本（rate=None）⇒ 不校准，行为维持现状",
            "source": source, "thresholds": dict(CALIBRATION_THRESHOLDS),
            "boundary": ("口径：rate 是**判死性误报率**（不是存在性误报率）；"
                         "上界值不得直接当点估计用"),
        }
    _r = float(rate)
    if _r > CALIBRATION_THRESHOLDS["candidate_only"]:
        _band, _act = "over_20", "candidate_only"
        _why = ("判死性误报率 %.2f%% > 20%% ⇒ 判死器**只产候选不进池**" % (_r * 100.0))
    elif _r > CALIBRATION_THRESHOLDS["split_by_klass"]:
        _band, _act = "10_20", "split_by_klass"
        _why = ("判死性误报率 %.2f%% 落在 10-20%% ⇒ 按 klass 分池分流" % (_r * 100.0))
    else:
        _band, _act = "under_10", "keep"
        _why = ("判死性误报率 %.2f%% < 10%% ⇒ 维持现行（照旧入池）" % (_r * 100.0))
    return {
        "schema": CALIBRATION_SCHEMA, "rate": round(_r, 4), "band": _band,
        "action": _act, "reason": _why, "sample_size": int(sample_size or 0),
        "source": source, "thresholds": dict(CALIBRATION_THRESHOLDS),
        "boundary": ("口径：rate 是**判死性误报率**（不是存在性误报率）；"
                     "上界值不得直接当点估计用"),
    }


def write_calibration_ledger(path: str, decision: dict, *,
                             now: float | None = None) -> dict:
    """校准动作**留痕**（jsonl，按 `rate+band` 幂等，不按墙钟）。"""
    import json as _json
    import time as _time

    _out = {"ok": False, "appended": 0, "skipped_dup": 0, "path": path,
            "persisted": False}
    if not path:
        _out["reason"] = "no_path"
        return _out
    _key = "%s|%s|%s" % (decision.get("band"), decision.get("action"),
                         decision.get("rate"))
    _row = dict(decision)
    _row["key"] = _key
    _row["written_at"] = float(now if now is not None else _time.time())
    _seen = set()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as _f:
                for _ln in _f:
                    _ln = _ln.strip()
                    if not _ln:
                        continue
                    try:
                        _seen.add(str(_json.loads(_ln).get("key") or ""))
                    except Exception:                           # noqa: BLE001
                        continue
        except OSError as _e:
            silent_exc(_e, "zero_break_judge.write_calibration_ledger read",
                       level="debug")
    if _key in _seen:
        _out["skipped_dup"] = 1
        _out["ok"] = True
        return _out
    try:
        _d = os.path.dirname(path)
        if _d:
            os.makedirs(_d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as _f:
            _f.write(_json.dumps(_row, ensure_ascii=False) + "\n")
        _out["appended"] = 1
        _out["ok"] = True
        _out["persisted"] = True
    except OSError as _e:
        silent_exc(_e, "zero_break_judge.write_calibration_ledger write",
                   level="warning")
        _out["reason"] = "%s: %s" % (type(_e).__name__, _e)
    return _out


def read_calibration(path: str) -> dict:
    """读最近一条校准结论（读不到 ⇒ `unknown`，**不当成 keep**）。"""
    import json as _json

    _res = {"state": "absent", "decision": None, "path": path}
    if not path or not os.path.isfile(path):
        return _res
    _last = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as _f:
            for _ln in _f:
                _ln = _ln.strip()
                if not _ln:
                    continue
                try:
                    _last = _json.loads(_ln)
                except Exception as _m369_e:  # noqa: BLE001
                    silent_exc(_m369_e, where="nucleus/evolution/zero_break_judge.py:read_calibration", level="debug")
                    continue
    except OSError as _e:
        silent_exc(_e, "zero_break_judge.read_calibration", level="debug")
        _res["state"] = "unreadable"
        return _res
    if _last is None:
        _res["state"] = "empty"
        return _res
    _res["state"] = "ok"
    _res["decision"] = _last
    return _res


def collect_death_samples(project_root: str | None = None,
                          pool_entries: list | None = None,
                          universe: int = 76, limit: int = 20,
                          scoped: bool = True,
                          exclude_drifted: bool = True,
                          drift_filter: dict | None = None) -> list:
    """按「76 抽 20 重放」的办法组样本（349 刀4 同一评估法，本刀扩样本用）。

    `universe` 是候选面（默认 76 —— 与烛微审计同基数），`limit` 是实际重放条数。
    抽样用**等距**而不是随机：结果可复算，换个人跑同一份池得到同一组样本。

    ★351刀1：`scoped=True`（默认）走「类.方法」限定口径；`scoped=False` 保留
    350 的裸名口径 —— 两口径**同一批样本**各跑一遍，才能看出同名碰撞消掉了多少。

    ★352刀1②：`exclude_drifted=True`（默认）——**目标已漂移的候选不进判死池**。
    道理：对一个 HEAD 上已经不存在的方法论“它是不是死代码”，结论必然是
    “没人引用”（因为它根本不存在）—— 那不是判死，是**题目错了**。把它计入
    判死分母只会把误报率要么虚高要么虚低，两头都不可信。
    `drift_filter` 传一个 dict 就把过滤明细回填（不改返回类型，不破已有调用）。
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
    # ★352刀1②：漂移候选不进判死池。**必须在抽样之前过滤**——
    #   先抽 20 再剔漂移会让分母随机变小（铁律163：“只取N条”筛选器必须
    #   先遍历全池再截断）。
    _df = {"schema": "death_drift_filter/352.v1", "applied": bool(exclude_drifted),
           "total_before": len(_cands), "excluded": 0, "kept": len(_cands),
           "by_verdict": {}, "excluded_ids": [], "boundary": ""}
    if exclude_drifted and _cands:
        try:
            from nucleus.evolution.patch_generator import (
                TARGET_DRIFTED_VERDICTS as _DV,
                precheck_batch as _pcb,
            )

            _bf = _pcb(project_root or _zbj_root(), _cands)
            _bad = {}
            for _r in _bf["rows"]:
                if _r["verdict"] in _DV and str(_r["id"] or ""):
                    _bad[str(_r["id"])] = _r
            if _bad:
                _cands = [c for c in _cands if str(c.get("id") or "") not in _bad]
                _df["excluded"] = len(_bad)
                _df["excluded_ids"] = sorted(_bad)
                for _r in _bad.values():
                    _df["by_verdict"][_r["verdict"]] = (
                        _df["by_verdict"].get(_r["verdict"], 0) + 1)
        except Exception as _e:                                # noqa: BLE001
            silent_exc(_e, where="zero_break_judge.collect_death_samples drift")
            # 预检不可用：**如实标未过滤**，不假装“没有漂移”（铁律156）
            _df["applied"] = False
            _df["boundary"] = "预检不可用 ⇒ 未过滤（非“无漂移”）"
    _df["kept"] = len(_cands)
    if isinstance(drift_filter, dict):
        drift_filter.update(_df)
    _uni = _cands[:int(universe)]
    _n = min(int(limit), len(_uni))
    if _n <= 0:
        return []
    _step = max(1, len(_uni) // _n)
    _picked = [_uni[i * _step] for i in range(_n)]
    _root = project_root or _zbj_root()
    _truth = (reference_truth_scoped(_root, _picked) if scoped
              else reference_truth_batch(_root, _picked))
    _out = []
    for _s, _t in zip(_picked, _truth):
        _row = dict(_s)
        _row["dead_code_true"] = _t.get("dead")
        _row["truth_refs"] = _t.get("refs")
        _row["truth_why"] = _t.get("why")
        _row["truth_scanned_files"] = _t.get("scanned_files")
        _row["truth_ref_scope"] = _t.get("ref_scope") or "350.bare.v1"
        _row["truth_refs_prod"] = _t.get("refs_prod")
        _row["truth_refs_test"] = _t.get("refs_test")
        _row["truth_cls"] = _t.get("cls")
        _row["truth_owner_state"] = _t.get("owner_state")
        _row["truth_other_class_excluded"] = _t.get("refs_other_class_excluded")
        _row["truth_bare_excluded"] = _t.get("refs_bare_excluded")
        _out.append(_row)
    return _out


# ===== ★351刀3：破零观察位**具名**监控 =====
#   350 刀4 撤掉 handshake 后，下一个候选是 `o3_b31b31fee790d6e9`
#   （`dead_code_candidate` / file=nucleus/audit_problem_pool.py / method=restore_pool）。
#   本刀把"它有没有被选中、四条件同不同立"写成**一行可 grep 的具名日志**——
#   不然破零那件事要么靠人翻 pending，要么等到报告才发现（349/350 各栽过一次）。
WATCH_SCHEMA = "zero_break_watch/351.v1"

#: ★351 破零观察位（具名，不扫全池；换观察对象只改这里）
#: ★385刀1：从**单值改多值** —— 384刀2 依规清零 pending 时把唯一观察位
#:   `o3_b31b31fee790d6e9` 一并删掉，破零判据随即恒返 `not_in_pool` 且**不报警**，
#:   等于「破零这件事静默失明」。多值 + 自愈后，单点消失不再导致全盲。
#: ★这里只放**真实存在过**的 ID（不编）：①351 起的原观察位（384刀2 已随 pending
#:   清零消失）；②history 里唯一的 O3 条目（已 wont_fix/approved，留作历史锚点）。
#:   **第 3 个位由自愈从 pending 真实挑选**，挑不到就由调用方如实告警。
WATCH_IDS = (
    "o3_b31b31fee790d6e9",
    "o3_1792638584_57885429",
)

#: ★385刀1：观察位**容量**上限 —— 票面「至少 3 个候选位」的落法：
#:   `WATCH_IDS` 是静态种子，自愈补进来的放 `_WATCH_RUNTIME`，两者合计不超过它。
WATCH_CAPACITY = 5

#: ★385刀1：自愈补进来的观察位（**进程内**有效，不落盘；每次启动重新挑）。
_WATCH_RUNTIME: list = []

#: ★385刀1：算作「O3 候选」的 issue_type（与 patch_execution_dispatch 同集）。
_O3_CANDIDATE_TYPES = ("dead_code", "dead_code_candidate")

#: ★385刀1：上一次自愈状态（用于 `changed` 去重 —— 派单每拍都调，
#:   无条件打 WARNING 会在一次停机里刷出成百上千条同义告警）。
_LAST_HEAL_STATE: list = [""]


def _watch_find(rows: list, patch_id: str) -> dict | None:
    for _r in (rows or []):
        if not isinstance(_r, dict):
            continue
        _pid = str(_r.get("patch_id") or _r.get("id") or "")
        if _pid == patch_id:
            return _r
    return None


def watch_candidate(patch_id: str, *,
                    pending_rows: list | None = None,
                    history_rows: list | None = None,
                    results: list | None = None,
                    project_root: str | None = None) -> dict:
    """单条候选的破零观察：`selected>0` + 四条件同立。

    ★边界（铁律196）：`selected` 只说明"本拍被选中进沙箱"，
    四条件同立只说明"链路通"，**都不说明补丁对不对**。
    """
    _pend = _watch_find(pending_rows, patch_id)
    _hist = _watch_find(history_rows, patch_id)
    _res = _watch_find(results, patch_id)
    _selected = 1 if _res is not None else 0
    _rec: dict = {}
    if isinstance(_pend, dict):
        _rec.update(_pend)
    if isinstance(_hist, dict):
        for _k in ("status", "verified", "applied", "executed"):
            if _k in _hist and _k not in _rec:
                _rec[_k] = _hist[_k]
        # ★351刀3 自证抓到的坑：pending 里的 `file` 是**目标文件**，
        #   history 里的 `file` 才是**实际落地文件**。四条件第④条问的是后者。
        #   不加这一句，隔离根产物会被目标的绝对路径掩盖成"生产改动"⇒ 假破零。
        if _hist.get("file"):
            _rec["target_file"] = _rec.get("file")
            _rec["file"] = _hist["file"]
    if isinstance(_res, dict):
        _rec["executed"] = 1
        # ★378刀1（T-378-破零判据同源-1）：`applied` **只认台账**，不许被本拍结果覆盖。
        #   原实现 `_rec["applied"] = _res.get("success") is True` 会让 `[351破零]`
        #   把「本拍沙箱执行成功」说成「补丁已落到源码」，而台账里 `applied` 仍是
        #   null（373刀5 明确"沙箱成功 ≠ 落地"）⇒ missing 从 3 项缩成 1 项，
        #   看起来"就差 ledger_verified 一步"，实际差的是同一件落地事的两面。
        #   本拍结果另存 `sandbox_ok`，好让人分辨"跑了没落地"与"压根没跑"。
        _rec["sandbox_ok"] = _res.get("success") is True
        if _res.get("verified") is not None and "verified" not in _rec:
            _rec["verified"] = _res.get("verified")
    _judge = judge_zero_break(_rec, project_root=project_root) if _rec else {
        "zero_broken": False, "verdict": "not_in_pool", "checks": {},
        "half_through": [], "boundary": "候选既不在 pending 也不在 history",
    }
    return {
        "schema": WATCH_SCHEMA,
        "patch_id": patch_id,
        "in_pending": isinstance(_pend, dict),
        "in_history": isinstance(_hist, dict),
        "selected": _selected,
        "selected_gt_0": _selected > 0,
        "sandbox_ok": (_rec.get("sandbox_ok") if _rec else None),
        "dispatch_attempts": (_rec.get("o3_dispatch_attempts") if _rec else None),
        "zero_broken": bool(_judge.get("zero_broken")),
        "verdict": _judge.get("verdict"),
        "missing": list(_judge.get("half_through") or []),
        "checks": _judge.get("checks") or {},
        "file": _rec.get("file"),
        "boundary": ("selected 只表示「本拍被选中进沙箱」；四条件同立只表示「链路通」"
                     "——**不表示补丁正确**（判据能力边界）"),
    }


def active_watch_ids() -> tuple:
    """★385刀1：当前生效的观察位 = 自愈补进来的 + 静态种子（去重保序）。"""
    _out: list = []
    for _x in list(_WATCH_RUNTIME) + list(WATCH_IDS):
        _s = str(_x or "").strip()
        if _s and _s not in _out:
            _out.append(_s)
    return tuple(_out)


def self_heal_watch(pending_rows=None, history_rows=None,
                    capacity: int | None = None) -> dict:
    """★385刀1：观察位自愈 —— 静态位**全失效**时，从 pending 挑 1 个 O3 候选补上。

    为什么需要：384刀2 依规清零 pending，把唯一观察位删掉后，`watch_candidate`
    对每个位都返 `not_in_pool`，而那是**正常返回值**（不报警）⇒ 破零这件事
    静默失明，直到某天有人翻报告才发现。

    **纯判据**：只改模块级 `_WATCH_RUNTIME`，不读盘、不写盘、**不打日志**
    （本模块自 351 起就是纯判据，日志由调用方 `_m351_watch_zero_break` 打）。

    四态（不同事实不同措辞，铁律166）：
      · `healthy`      至少一个观察位在 pending/history 里 ⇒ 不动
      · `healed`       原本全失效，已从 pending 补进 1 个 O3 候选
      · `no_candidate` 全失效、pending 有货但**没有 O3 候选** ⇒ 调用方须 WARNING
      · `no_pending`   全失效且 pending 读不到/为空 ⇒ 调用方须 WARNING
    """
    _pend = pending_rows if isinstance(pending_rows, list) else []
    _hist = history_rows if isinstance(history_rows, list) else []
    _cap = int(WATCH_CAPACITY if capacity is None else capacity)
    _active = list(active_watch_ids())
    _out = {"schema": "zero_break_watch_heal/385.v1", "state": "healthy",
            "active": _active, "added": "", "why": "",
            "capacity": _cap, "pending_rows": len(_pend)}

    # ① 只要还有一个位**在 pending**，就算健康（不折腾）。
    #   ★只看 pending、不看 history：破零观察的是「会不会被选中进沙箱」，
    #   而 history 里的已结案条目（wont_fix/approved/verified）永远不会再被派单；
    #   把它当"活着"会让自愈永不触发 ⇒ 又回到静默失明（本刀要修的就是这个）。
    for _pid in _active:
        if _watch_find(_pend, _pid) is not None:
            _out["why"] = "at_least_one_in_pending"
            _out["changed"] = (_LAST_HEAL_STATE[0] != "healthy")
            _LAST_HEAL_STATE[0] = "healthy"
            return _out

    # ② 全失效：从 pending 里找 O3 候选（真实挑，不编）
    if not _pend:
        _out["state"] = "no_pending"
        _out["why"] = "pending_empty_or_unreadable"
        _out["changed"] = (_LAST_HEAL_STATE[0] != "no_pending")
        _LAST_HEAL_STATE[0] = "no_pending"
        return _out
    if len(_active) >= _cap:
        _out["state"] = "no_candidate"
        _out["why"] = "capacity_full(no_room_to_heal)"
        _out["changed"] = (_LAST_HEAL_STATE[0] != "no_candidate")
        _LAST_HEAL_STATE[0] = "no_candidate"
        return _out

    # ★387刀4：从「只补 1 个」改成「**补满到容量上限**」。
    #   387 补位后 pending 里有 3 个 O3 候选，而旧实现只挑第一个就 break
    #   ⇒ 另外 2 个永远进不了观察位，"多候选防单点失明"形同虚设。
    _picks: list = []
    for _r in _pend:
        if not isinstance(_r, dict):
            continue
        _rid = str(_r.get("patch_id") or _r.get("id") or "").strip()
        if not _rid or _rid in _active:
            continue
        _it = str(_r.get("issue_type") or "")
        if _rid.startswith("o3_") or _it in _O3_CANDIDATE_TYPES:
            _picks.append(_rid)
            if len(_active) + len(_picks) >= _cap:
                break

    if not _picks:
        _out["state"] = "no_candidate"
        _out["why"] = "pending_has_no_o3_candidate"
        _out["changed"] = (_LAST_HEAL_STATE[0] != "no_candidate")
        _LAST_HEAL_STATE[0] = "no_candidate"
        return _out

    _WATCH_RUNTIME.extend(_picks)
    _out["state"] = "healed"
    _out["added"] = _picks[0]
    _out["added_all"] = list(_picks)
    _out["active"] = list(active_watch_ids())
    _out["why"] = "static_watch_all_missing"
    _out["changed"] = (_LAST_HEAL_STATE[0] != "healed:%s" % ",".join(_picks))
    _LAST_HEAL_STATE[0] = "healed:%s" % ",".join(_picks)
    return _out


def watch_available(pending_rows=None, history_rows=None) -> list:
    """★385刀1：诊断口 —— 每个观察位当前是否在库（供自省/报告读）。"""
    _pend = pending_rows if isinstance(pending_rows, list) else []
    _hist = history_rows if isinstance(history_rows, list) else []
    _out = []
    for _pid in active_watch_ids():
        _out.append({
            "patch_id": _pid,
            "in_pending": _watch_find(_pend, _pid) is not None,
            "in_history": _watch_find(_hist, _pid) is not None,
            "runtime": _pid in _WATCH_RUNTIME,
        })
    return _out


def reset_watch_runtime() -> None:
    """★385刀1：清空自愈补进来的观察位（测试/热重载用；不动静态种子）。"""
    _WATCH_RUNTIME.clear()
    _LAST_HEAL_STATE[0] = ""


def watch_batch(patch_ids, **kw) -> list:
    """批量（默认只观察 `WATCH_IDS`），异常不抛给调用方。"""
    _out = []
    # ★385刀1：用**动态**观察位（自愈补进来的 + 静态种子），不再只看常量。
    for _pid in (patch_ids or active_watch_ids()):
        try:
            _out.append(watch_candidate(str(_pid), **kw))
        except Exception as _e:                                 # noqa: BLE001
            silent_exc(_e, where="zero_break_judge.watch_batch", level="warning")
            _out.append({"schema": WATCH_SCHEMA, "patch_id": str(_pid),
                         "error": "%s: %s" % (type(_e).__name__, _e)})
    return _out


def watch_line(w: dict) -> str:
    """一行可 grep 的具名日志体（`[351破零]` 前缀由调用方加）。"""
    if w.get("error"):
        return "watch=%s error=%s" % (w.get("patch_id"), w["error"])
    _miss = ",".join(w.get("missing") or []) or "-"
    return ("watch=%s in_pending=%s selected=%d four_ok=%s "
            "verdict=%s missing=%s file=%s"
            % (w.get("patch_id"),
               "Y" if w.get("in_pending") else "N",
               int(w.get("selected") or 0),
               "Y" if w.get("zero_broken") else "N",
               w.get("verdict"), _miss, w.get("file")))


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
