# -*- coding: utf-8 -*-
"""★339批刀5：**存量回算机制**（按版本分域的消费端）。

背景（烛微338前置§4.1）
-----------------------
`data/reports/code_review/by_version/` 只有 `v-test-319` 测试夹具，330刀3
"按版本分域留样"在**生产侧从未产出真版本目录**；338刀5 已给留样加三字段
（`criteria_version` / `code_version` / `verdict_status`）⇒ 本模块把"分域"的
**消费端**（回算）落地：判据版本一变，能不能回答"按新判据是多少"。

三条纪律（A-14 同源写死）
------------------------
① 结果一律**双报**（旧口径值 / 新口径值），**禁止跨 criteria_version 单值比较**；
② **只产"计数与结论"，不得改任一侧数据**（守 SC-3 红线）——
   本模块**不写** `patch_history` / 留样 / issues，只写自己的回算报告；
③ 每次回算在口径变更登记表（337刀12 C-1~C-7）追加一行
   `旧口径/新口径/理由/受影响读数`（文档面，不在本模块里做）。

触发
----
· `criteria_version` 变更 ⇒ **全量回算**（`scope` 自动升 `full`）；
· 日常随 04:00 夜间链**增量**（`night_orchestration.maybe_run_night_digest`
  同链调用，**禁止每拍**）。
"""
from __future__ import annotations

import io
import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ReviewRecompute")

_M339_RECOMPUTE_VERSION = "339.recompute.v1"
_M339_RECOMPUTE_REL = "data/reports/recompute"
_M339_BY_VERSION_REL = "data/reports/code_review/by_version"
_M339_PATCH_HISTORY_REL = "data/patches/patch_history.json"
_M339_LEGACY_DAYS = 7.0
#: 新口径的可判性要求（与 338刀4 证据门同源：值必须带底座）
_M339_EVIDENCE_KEYS = ("risk", "trust", "blast")
#: 死代码"硬档"新口径：hard 族两档都算（旧口径只认 hard_strict）
_M339_HARD_TIERS_NEW = ("hard", "hard_strict")


def _m339_criteria_version() -> str:
    """判据版本（`_DEAD_CODE_CRITERIA_VERSION`，类属性）。取不到 ⇒ unknown。"""
    try:
        import nucleus.self_inspector as _si
        _v = str(getattr(_si, "_DEAD_CODE_CRITERIA_VERSION", "") or "")
        return _v or "unknown"
    except Exception as _e:
        silent_exc(_e, where="review_recompute::_m339_criteria_version")
        return "unknown"


def _m339_code_version() -> str:
    try:
        import nucleus.self_inspector as _si
        return str(_si._m339_code_version() or "unknown")
    except Exception as _e:
        silent_exc(_e, where="review_recompute::_m339_code_version")
        return "unknown"


def _m339_proc_gen() -> str:
    try:
        from nucleus.proc_gen import proc_gen_str as _pgs
        return str(_pgs())
    except Exception as _e:
        silent_exc(_e, where="review_recompute::_m339_proc_gen")
        return "unknown"


def _m339_load_json(path: str) -> tuple:
    """读 JSON。返回 `(obj, state)`，state ∈ ok/missing/bad。

    ★"读不到"与"读到了是空"必须分开（后者照实报 0，前者报 unavailable）。
    """
    if not os.path.isfile(path):
        return None, "missing"
    try:
        with io.open(path, encoding="utf-8") as _f:
            return json.load(_f), "ok"
    except (OSError, ValueError) as _e:
        _module_logger.warning("[339回算] JSON 不可读 %s：%s: %s",
                               path, type(_e).__name__, _e)
        return None, "bad"


def _m339_latest_sample(project_root: str | None = None,
                        sample_path: str | None = None) -> tuple:
    """取最新一份 per-item 留样（优先新分域目录 `<code>_<criteria>`）。

    返回 `(payload, path, state)`。目录不存在 ⇒ `(None, "", "missing")`
    —— **不拿测试夹具 `v-test-319` 充当生产留样**（那正是 330刀3 留的坑）。
    """
    if sample_path:
        _o, _s = _m339_load_json(sample_path)
        return _o, sample_path, _s
    _root = project_root or os.getcwd()
    _base = os.path.join(_root, _M339_BY_VERSION_REL.replace("/", os.sep))
    if not os.path.isdir(_base):
        return None, "", "missing"
    _cands: list = []
    try:
        for _dn in os.listdir(_base):
            if _dn.startswith("v-test"):
                continue          # 测试夹具不算生产留样
            _d = os.path.join(_base, _dn)
            if not os.path.isdir(_d):
                continue
            for _fn in os.listdir(_d):
                if _fn.startswith("run_") and _fn.endswith(".json"):
                    _fp = os.path.join(_d, _fn)
                    _cands.append((os.path.getmtime(_fp), _fp))
    except OSError as _e:
        _module_logger.warning("[339回算] 留样目录不可读 %s：%s: %s",
                               _base, type(_e).__name__, _e)
        return None, "", "bad"
    if not _cands:
        return None, "", "missing"
    _cands.sort()
    _p = _cands[-1][1]
    _o, _s = _m339_load_json(_p)
    return _o, _p, _s


def _m339_judgeable_old(row: dict) -> bool:
    """旧口径可判定：`runtime_verify_result.verified` 是**真布尔**。

    这是 105 批以后唯一真验证字段（顶层 `runtime_verified` 是污染字段，
    `verify_submitted_patches` 对失败补丁也无条件置 True）。
    """
    _rvr = (row or {}).get("runtime_verify_result")
    return isinstance(_rvr, dict) and isinstance(_rvr.get("verified"), bool)


def _m339_judgeable_new(row: dict) -> bool:
    """新口径可判定（339）：旧口径 **且** 证据三要素齐（`patch_evidence`）。

    为什么加证据门：338刀4 已经裁定"无据补丁不入自动派单池"。可判定性
    若仍只看 verified，"能验证"和"凭什么这么判"就还是两件事 —— 回算的意义
    就在于把这条口径变更的**差值**量化出来（而不是悄悄换尺子）。
    """
    if not _m339_judgeable_old(row):
        return False
    _ev = (row or {}).get("patch_evidence")
    if not isinstance(_ev, dict):
        return False
    return all(k in _ev for k in _M339_EVIDENCE_KEYS)


def _m339_tag_legacy(rows: list, now: float, days: float = _M339_LEGACY_DAYS) -> dict:
    """给"7 天前 applied"的存量补丁打 `legacy_criteria` 标（**只标返回副本**）。

    任务书刀5：「patch_history 84 条中 7 天前 applied 的 76 条（**不重算**，
    仅标 `legacy_criteria`）」。为什么只标不重算：这些补丁的判据版本已经不可
    追溯（当时没有 `criteria_version` 字段），拿今天的尺子去量它们，得到的
    是"今天的尺子量旧数据"这个事实，不是它们的质量。
    """
    _lo = float(now) - float(days) * 86400.0
    _total = 0
    _tagged = 0
    _items: list = []
    for _r in (rows or []):
        if not isinstance(_r, dict):
            continue
        if _r.get("applied") is not True:
            continue
        _total += 1
        _ts = _r.get("applied_at") or _r.get("applied_ts") or 0
        try:
            _ts = float(_ts or 0)
        except (TypeError, ValueError) as _te:
            _module_logger.debug("[339回算] applied_at 非数值（按 0 处理）: %s: %s",
                                 type(_te).__name__, _te)
            _ts = 0.0
        if _ts and _ts < _lo:
            _tagged += 1
            _items.append({"id": str(_r.get("id") or "")[:32],
                           "applied_at": _ts,
                           "legacy_criteria": True})
    return {"applied_total": _total, "legacy_tagged": _tagged,
            "recomputed": 0, "days": days, "items": _items[:40]}


def run_recompute(scope: str = "incremental", project_root: str | None = None,
                  out_path: str | None = None, now: float | None = None,
                  criteria_version: str | None = None,
                  code_version: str | None = None,
                  patch_history: list | None = None,
                  sample: dict | None = None,
                  state_path: str | None = None) -> dict:
    """跑一次存量回算，产出**双报**报告（只写回算报告，不碰任何业务数据面）。

    Args:
        scope: `incremental`（夜间链日常）或 `full`（判据版本变更）；
               内部再判：state 里 `last_criteria_version` 与本次不同 ⇒ 自动升 `full`。
        project_root: 项目根（**隔离验证传隔离根**，铁律158）。
        out_path: 报告落盘路径（覆盖默认 `<root>/data/reports/recompute/<cv>_run.json`）。
        patch_history / sample: 直接注入数据（隔离测试用，免得造文件）。

    Returns:
        dict: 头字段（版本戳 + proc_gen）+ `blocks` 三块 + `verdict`。
              任一块读不到 ⇒ 该块 `new/old = None` 且 `state` 说明原因（**不填 0**）。
    """
    _root = project_root or os.getcwd()
    _now = float(time.time() if now is None else now)
    _cv = criteria_version or _m339_criteria_version()
    _cdv = code_version or _m339_code_version()
    _sp = state_path or os.path.join(
        _root, _M339_RECOMPUTE_REL.replace("/", os.sep), "_state.json")
    _st, _st_state = _m339_load_json(_sp)
    _st = _st if isinstance(_st, dict) else {}
    _prev_cv = str(_st.get("last_criteria_version") or "")
    _scope = "full" if (str(scope) == "full" or (_prev_cv and _prev_cv != _cv)) else "incremental"

    # ── A. 可判性重算（unverifiable）──
    _hist = patch_history
    _hist_state = "injected"
    if _hist is None:
        _hp = os.path.join(_root, _M339_PATCH_HISTORY_REL.replace("/", os.sep))
        _obj, _hist_state = _m339_load_json(_hp)
        if isinstance(_obj, dict):
            _obj = _obj.get("patches") or _obj.get("history") or []
        _hist = _obj if isinstance(_obj, list) else []
    _rows = [x for x in (_hist or []) if isinstance(x, dict)]
    _block_a: dict[str, Any] = {
        "old_basis": "runtime_verify_result.verified ∈ {True,False}",
        "new_basis": "旧口径 ∧ patch_evidence{risk,trust,blast} 齐（338刀4 证据门同源）",
        "state": _hist_state, "total": len(_rows),
    }
    if _hist_state in ("missing", "bad"):
        _block_a.update({"old": None, "new": None,
                         "note": "patch_history 不可读 ⇒ 不可算（不填 0）"})
    else:
        _old_unv = sum(1 for _r in _rows if not _m339_judgeable_old(_r))
        _new_unv = sum(1 for _r in _rows if not _m339_judgeable_new(_r))
        _block_a.update({"old": int(_old_unv), "new": int(_new_unv),
                         "delta": int(_new_unv) - int(_old_unv),
                         "metric": "unverifiable（不可判定条数，越小越好）"})

    # ── B. dead_code / hard 类重算（判据域排除 tests/ 后）──
    _smp = sample
    _smp_path = ""
    _smp_state = "injected"
    if _smp is None:
        _smp, _smp_path, _smp_state = _m339_latest_sample(_root)
    _items = (_smp or {}).get("items") if isinstance(_smp, dict) else None
    _items = [x for x in (_items or []) if isinstance(x, dict)]
    _block_b: dict[str, Any] = {
        "old_basis": "evidence_tier == 'hard_strict'（不考虑路径域）",
        "new_basis": "evidence_tier ∈ {hard,hard_strict} ∧ 路径在判据域内（排除 tests/）",
        "state": _smp_state, "sample_path": os.path.basename(_smp_path) if _smp_path else "",
        "total": len(_items),
    }
    if _smp_state in ("missing", "bad") or not _items:
        _block_b.update({"old": None, "new": None,
                         "note": "无生产留样可读 ⇒ 不可算（不填 0；测试夹具不算）"})
    else:
        from nucleus.self_inspector import _m339_in_domain as _ind
        _old_n = sum(1 for _x in _items
                     if str(_x.get("evidence_tier") or "") == "hard_strict")
        _new_n = sum(1 for _x in _items
                     if str(_x.get("evidence_tier") or "") in _M339_HARD_TIERS_NEW
                     and _ind(_x.get("file")))
        _block_b.update({"old": int(_old_n), "new": int(_new_n),
                         "delta": int(_new_n) - int(_old_n),
                         "metric": "dead_code hard 档条数"})

    # ── C. 7 天前 applied：**不重算**，只标 legacy_criteria ──
    _block_c = _m339_tag_legacy(_rows, _now)
    _block_c["state"] = _hist_state
    _block_c["note"] = "只标 legacy_criteria，不重算（判据版本不可追溯）"

    _payload: dict[str, Any] = {
        "criteria_version": _cv,
        "code_version": _cdv,
        "proc_gen": _m339_proc_gen(),
        "recompute_version": _M339_RECOMPUTE_VERSION,
        "ts": _now,
        "ymd": time.strftime("%Y%m%d", time.localtime(_now)),
        "scope": _scope,
        "prev_criteria_version": _prev_cv or None,
        "blocks": {"A_judgeability": _block_a,
                   "B_dead_code_hard": _block_b,
                   "C_legacy_applied": _block_c},
        "verdict": "counted_only",
        # ★SC-3 红线：本函数**只读**业务数据，产物只写回算目录
        "wrote_business_data": False,
        "note": ("双报（旧口径/新口径）并**禁止跨 criteria_version 单值比较**；"
                 "未改 patch_history / 留样 / issues 任一侧数据"),
    }
    _op = out_path or os.path.join(
        _root, _M339_RECOMPUTE_REL.replace("/", os.sep), "%s_run.json" % _cv)
    try:
        _d = os.path.dirname(os.path.abspath(_op))
        if _d:
            os.makedirs(_d, exist_ok=True)
        _tmp = _op + ".tmp"
        with io.open(_tmp, "w", encoding="utf-8", newline="\n") as _f:
            json.dump(_payload, _f, ensure_ascii=False, indent=2)
        os.replace(_tmp, _op)
        _payload["out_path"] = _op
    except OSError as _e:
        silent_exc(_e, where="review_recompute::run_recompute write", level="warning")
        _module_logger.warning("[339回算] 报告落盘失败 %s：%s: %s",
                               _op, type(_e).__name__, _e)
        _payload["out_path"] = None
        _payload["write_error"] = type(_e).__name__
    # 只在**成功落盘后**推进 state：写盘失败却推进版本 ⇒ 下一轮把变更"吃掉"
    if _payload.get("out_path"):
        try:
            _st2 = {"last_criteria_version": _cv,
                    "last_code_version": _cdv,
                    "last_ts": _now,
                    "last_scope": _scope,
                    "last_out": os.path.basename(_op)}
            _tmp2 = _sp + ".tmp"
            _d2 = os.path.dirname(os.path.abspath(_sp))
            if _d2:
                os.makedirs(_d2, exist_ok=True)
            with io.open(_tmp2, "w", encoding="utf-8", newline="\n") as _f:
                json.dump(_st2, _f, ensure_ascii=False, indent=2)
            os.replace(_tmp2, _sp)
        except OSError as _e:
            silent_exc(_e, where="review_recompute::run_recompute state", level="warning")
            _module_logger.warning("[339回算] state 写入失败 %s：%s: %s",
                                   _sp, type(_e).__name__, _e)
    _module_logger.info(
        "[339回算] scope=%s criteria=%s code=%s proc_gen=%s ｜ A 可判性 旧=%s 新=%s"
        " ｜ B 硬档 旧=%s 新=%s ｜ C applied=%s legacy=%s ｜ 只产计数(未改数据)",
        _scope, _cv, _cdv, _payload["proc_gen"],
        _block_a.get("old"), _block_a.get("new"),
        _block_b.get("old"), _block_b.get("new"),
        _block_c.get("applied_total"), _block_c.get("legacy_tagged"))
    return _payload
