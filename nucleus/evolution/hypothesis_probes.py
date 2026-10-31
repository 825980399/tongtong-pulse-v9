"""hypothesis_probes.py —— 三个可执行判据探针（第409批 刀3）。

刀2 建好了对账机制，但 `_PROBES` 一直是空的 ⇒ 所有判据都会落 `no_probe` 的
`unknown`。本模块把任务书刀3 的三条判据真正接上数据源：

| 判据 | 问的问题 | 查哪份数据 |
|---|---|---|
| `recurrence` 复发 | 修完之后，同一个问题在观察窗内**又出现了**吗 | `data/reports/audit/audit_*.json` |
| `effect` 效果 | 补丁落盘后，效果校验**通过了吗** | `data/evolution/effect_verify.json` |
| `reuse` 复用 | 经验命中后 7 天内，同一个问题**又被问了一遍**吗 | `data/llm_traces/calls_*.jsonl` |

## ★ 三条判据都有「没有数据」这个第三态

最容易犯的错是把「查不到」当成「没发生」：
审核池里没有这条签名，可能是**真的修好了**，也可能是**那天根本没跑审计**；
追踪里没有匹配调用，可能是**真的没再问**，也可能是**那几天的 trace 没留下来**。
所以三条探针都先判 **coverage**（这段窗口有没有人观测过），没有就 `unknown`。

## ★ 判据3 只能做文本级近似匹配

`data/llm_traces/calls_*.jsonl` 的字段是
`trace_id / ts / origin / model / prompt / response / prompt_version / channel /
cost_estimate / duration / status / tokens / usage / error / feedback`
—— **没有 `issue_signature`**（任务书假设它有）。所以复用判据只能在 `prompt`
正文里找方法名/文件名，这是**文本级近似**，不是结构化匹配：
- 方法名为空（只有文件名）⇒ 直接 `unknown(coarse_signature)`，不硬判；
- 命中只说明"有调用的提示词里提到了这个方法"，不保证是同一个问题的再求解；
- 每份 24h 的 trace 里 `origin` 混杂 `system_internal / evolution_task /
  user_query`，命中会按 origin 拆开写进证据，便于人工复核。
"""

from __future__ import annotations

import glob
import json
import os
from collections import Counter

from nucleus._silent_except import silent_exc
from nucleus.evolution import hypothesis_criteria as hc
from nucleus.evolution import hypothesis_reconciler as hr
from nucleus.logger import get_module_logger

_logger = get_module_logger("HypothesisProbes")

_M409_PROBE_VERSION = "409.3"

AUDIT_REL = os.path.join("data", "reports", "audit", "audit_*.json")
EFFECT_REL = os.path.join("data", "evolution", "effect_verify.json")
TRACE_REL = os.path.join("data", "llm_traces", "calls_*.jsonl")

#: 不算复发的状态：`wontfix` 是"主动不修"，它的存在不能拿来说"补丁失效"。
#: ★`fixed` 要算 —— 修过的问题又出现在池子里，那正是复发。
_STATUS_EXCLUDED = ("wontfix",)

#: 复用判据的默认观察窗（任务书：7 天）
DEFAULT_REUSE_WINDOW_SEC = 7 * 86400.0
#: 复发判据的默认观察窗（任务书：24h）
DEFAULT_RECURRENCE_WINDOW_SEC = 86400.0

#: ★覆盖宽限：到期后多久之内跑过的审计，算"观测到了这个窗口"。
#:   为什么要它：`due_at` 是"该看了"的时刻，而审计是每日 03:00 批跑，两者几乎
#:   不会重合。若要求快照严格落在 [start, end] 内，最正常的场景（到期后几小时
#:   跑了一次审计）反而会被判成"没覆盖"。
#:   为什么必须有上界：不限上界的话，40 天前那个窗口会被"昨天刚跑的快照"判成
#:   "已覆盖、零命中 ⇒ confirmed"——用今天的观测给上个月的窗口背书（409 实测即此）。
_COVER_GRACE_SEC = 6 * 3600.0


# ------------------------------------------------------------------ 小工具
def _out(result, detail, evidence=None, reason=""):
    return {"result": result, "detail": detail,
            "evidence": evidence or {}, "reason": reason}


def _num(v):
    """把任意值收成 float；收不了返回 None（布尔不算数）。"""
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError) as _exc:
        silent_exc(_exc, where="hypothesis_probes._num", level="debug")
        return None


def window_sec(criterion: dict, default: float) -> float:
    """观察窗：判据自带 `window_sec` 优先，其次条目字段，最后默认值。"""
    _w = _num((criterion or {}).get("window_sec"))
    if _w and _w > 0:
        return _w
    return float(default)


def _end_of_window(entry: dict, ctx: dict) -> float:
    """窗口右端 = 到期时刻（没有就取 now）。"""
    _due = _num(entry.get(hc.F_DUE))
    if _due is not None:
        return _due
    return float(ctx.get("now") or 0.0)


def split_signature(sig: str) -> tuple[str, str, str]:
    """`file::type::method` → 三元组；method 里可能还有 `::`，不切碎。"""
    _p = str(sig or "").split("::", 2)
    while len(_p) < 3:
        _p.append("")
    return _p[0], _p[1], _p[2]


def signature_of(entry: dict, criterion: dict) -> str:
    """优先取判据里的 signature，退一步用条目的 rule 反推。"""
    _s = str((criterion or {}).get("signature") or "").strip()
    if _s:
        return _s
    _r = str(entry.get("rule") or "").strip()
    # rule 形如 `type::issue_type`，凑不出 file，只能给出两段
    if "::" in _r:
        return f"::{_r}"
    return ""


# ------------------------------------------------------------- 数据源（读）
def _audit_snapshots() -> list[tuple[float, list]]:
    """返回 ``[(generated_at, entries), ...]``，按时间升序。"""
    _o = []
    for _p in sorted(glob.glob(os.path.join(hr.data_root(), AUDIT_REL))):
        try:
            with open(_p, encoding="utf-8") as _f:
                _d = json.load(_f)
        except (OSError, ValueError) as _exc:
            silent_exc(_exc, where="hypothesis_probes._audit_snapshots", level="debug")
            continue
        if not isinstance(_d, dict):
            continue
        _ts = _num(_d.get("generated_at"))
        if _ts is None:
            continue
        _o.append((_ts, _d.get("entries") or []))
    _o.sort(key=lambda kv: kv[0])
    return _o


def _audit_sig(entry: dict) -> str:
    """按 `EvolutionEffectVerifier.issue_signature` 同一口径生成签名。

    ★ 审核池里的字段叫 `issue_type`，而 `issue_signature()` 读的是 `type` ——
      直接喂进去会得到 `file::::method`（类型段空），和假设里的签名永远对不上。
    """
    _f = str(entry.get("file") or "")
    _t = str(entry.get("issue_type") or entry.get("type") or "")
    _m = str(entry.get("method") or entry.get("desc") or entry.get("description") or "")[:24]
    return f"{_f}::{_t}::{_m}"


def _load_effect() -> dict:
    _p = os.path.join(hr.data_root(), EFFECT_REL)
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _exc:
        silent_exc(_exc, where="hypothesis_probes._load_effect", level="warning")
        return {}
    return _d if isinstance(_d, dict) else {}


def _trace_files() -> list[str]:
    return sorted(glob.glob(os.path.join(hr.data_root(), TRACE_REL)))


def _trace_rows(start: float, end: float) -> tuple[list[dict], int]:
    """读窗口内的调用记录。返回 ``(rows, 覆盖到的文件数)``。

    ★ 文件名只到"日"粒度，而窗口可能落在一天中间 ⇒ 不做文件名粗筛，直接按 `ts`
      过滤。47 份文件、日频跑一次，代价可接受；粗筛反而会漏掉跨日窗口。
    """
    _rows: list[dict] = []
    for _p in _trace_files():
        _rows.extend(_read_jsonl_window(_p, start, end, os.path.basename(_p)))
    return _rows, len({r.get("_file") for r in _rows})


def _read_jsonl_window(path: str, start: float, end: float, name: str) -> list[dict]:
    _o: list[dict] = []
    try:
        with open(path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _d = json.loads(_line)
                except ValueError as _exc:
                    silent_exc(_exc, where="hypothesis_probes._read_jsonl_window",
                               level="debug")
                    continue
                if not isinstance(_d, dict):
                    continue
                _ts = _num(_d.get("ts"))
                if _ts is None or _ts < start or _ts > end:
                    continue
                _d["_file"] = name
                _o.append(_d)
    except OSError as _exc:
        silent_exc(_exc, where="hypothesis_probes._read_jsonl_window", level="debug")
    return _o


# ============================================================ 判据1 复发
def probe_recurrence(entry: dict, ctx: dict) -> dict:
    """修完之后，同一个 `issue_signature` 在观察窗内又出现了吗？

    数据：`data/reports/audit/audit_*.json`（滚动保留最近 8 份）
    判定：
      - 窗口内**没有任何审计快照** ⇒ `unknown(no_coverage)`（没人看过，不算没发生）
      - 窗口内出现同签名且未被 `wontfix` 排除的条目 ⇒ `refuted`
      - 有覆盖、零命中 ⇒ `confirmed`
    """
    _c = ctx.get("criterion") or {}
    _sig = signature_of(entry, _c)
    if not _sig or _sig.startswith("::"):
        return _out(hc.RESULT_UNKNOWN,
                    "判据里没有可用的 signature（rule 反推不出文件名）",
                    reason="no_signature")
    _w = window_sec(_c, DEFAULT_RECURRENCE_WINDOW_SEC)
    _end = _end_of_window(entry, ctx)
    _start = _end - _w
    _snaps = _audit_snapshots()
    if not _snaps:
        return _out(hc.RESULT_UNKNOWN, "审核池一份快照都没有",
                    {"window": [_start, _end]}, reason="no_audit_data")
    _covered = [t for t, _e in _snaps if _start <= t <= _end + _COVER_GRACE_SEC]
    if not _covered:
        return _out(hc.RESULT_UNKNOWN,
                    f"窗口内没有审计快照（窗口 [{_start:.0f}, {_end:.0f}]，"
                    f"宽限 {_COVER_GRACE_SEC:.0f}s；最新快照 {_snaps[-1][0]:.0f}）",
                    {"window": [_start, _end], "snapshots": len(_snaps),
                     "coverage_grace_sec": _COVER_GRACE_SEC},
                    reason="no_coverage")
    _hits, _excluded = [], 0
    for _ts, _entries in _snaps:
        # ★上界同样要卡：窗口之后 10 天才冒出来的同签名，不属于这次观察。
        if _ts < _start or _ts > _end + _COVER_GRACE_SEC:
            continue
        for _e in _entries:
            if not isinstance(_e, dict):
                continue
            if _audit_sig(_e) != _sig:
                continue
            if str(_e.get("status") or "") in _STATUS_EXCLUDED:
                _excluded += 1
                continue
            _seen = max(_num(_e.get("first_seen_ts")) or 0.0,
                        _num(_e.get("last_seen_ts")) or 0.0)
            if _seen < _start or _seen > _end + _COVER_GRACE_SEC:
                continue
            _hits.append({"id": _e.get("id"), "status": _e.get("status"),
                          "last_seen_ts": _seen, "snapshot_at": _ts})
    _ev = {"window": [_start, _end], "window_sec": _w, "signature": _sig,
           "coverage_grace_sec": _COVER_GRACE_SEC,
           "snapshots_in_window": len(_covered), "hits": len(_hits),
           "excluded_wontfix": _excluded}
    if _hits:
        _ev["first_hit"] = _hits[0]
        _ev["last_hit"] = _hits[-1]
        return _out(hc.RESULT_REFUTED,
                    f"窗口内同签名复现 {len(_hits)} 次（总快照 {len(_covered)} 份）",
                    _ev)
    return _out(hc.RESULT_CONFIRMED,
                f"窗口内 {len(_covered)} 份快照均未见同签名（wontfix 排除 {_excluded} 条）",
                _ev)


# ============================================================ 判据2 效果
def probe_effect(entry: dict, ctx: dict) -> dict:
    """补丁落盘后，`effect_verify.verdict.passed` 到底是 True / False / None？

    数据：`data/evolution/effect_verify.json` → `verdicts[patch_id].passed`
    判定：True→confirmed / False→refuted / None 或缺记录→unknown。

    ★「补齐三态」在这里的含义：`passed is None` 过去常被下游当成 falsy 处理，
      于是"没判过"被算成"没通过"。本探针显式保留 None 为 unknown，并把
      `dims.problem_gone / function_ok / perf_ok` 一并写进证据，让"为什么没判出来"
      肉眼可查。
    """
    _c = ctx.get("criterion") or {}
    _pid = str(_c.get("patch_id") or entry.get("patch_id") or "").strip()
    if not _pid:
        return _out(hc.RESULT_UNKNOWN, "判据里没有 patch_id",
                    reason="no_patch_id")
    _d = _load_effect()
    if not _d:
        return _out(hc.RESULT_UNKNOWN, "effect_verify.json 读不出来",
                    {"patch_id": _pid}, reason="no_effect_data")
    _v = (_d.get("verdicts") or {}).get(_pid)
    if not isinstance(_v, dict):
        return _out(hc.RESULT_UNKNOWN, f"verdicts 里没有 {_pid}",
                    {"patch_id": _pid, "verdicts": len(_d.get("verdicts") or {})},
                    reason="no_verdict")
    _passed = _v.get("passed")
    if isinstance(_passed, str):
        # 容错：老记录可能把布尔写成字符串
        _low = _passed.strip().lower()
        _passed = True if _low in ("true", "1", "yes") else (
            False if _low in ("false", "0", "no") else None)
    _ev = {"patch_id": _pid, "passed": _passed,
           "verified_at": _v.get("verified_at"),
           "reason": _v.get("reason"), "dims": _v.get("dims")}
    if _passed is True:
        return _out(hc.RESULT_CONFIRMED, f"效果校验通过：{_v.get('reason') or ''}"[:200], _ev)
    if _passed is False:
        return _out(hc.RESULT_REFUTED, f"效果校验未通过：{_v.get('reason') or ''}"[:200], _ev)
    return _out(hc.RESULT_UNKNOWN,
                f"效果校验三态为 None（没判过，不等于没通过）：{_v.get('reason') or ''}"[:200],
                _ev, reason="verdict_none")


# ============================================================ 判据3 复用
def probe_reuse(entry: dict, ctx: dict) -> dict:
    """经验命中之后，7 天内同一个问题又产生过 LLM 调用吗？

    数据：`data/llm_traces/calls_*.jsonl`（`ts` 为 epoch 秒）
    ★ 该文件**没有 `issue_signature` 字段** ⇒ 只能拿签名里的方法名去 `prompt`
      正文里做文本匹配。这是近似，不是结构匹配：
      - 方法名为空（只剩文件名）⇒ `unknown(coarse_signature)`，不硬判
      - 窗口内没有任何 trace 文件 ⇒ `unknown(no_coverage)`
      - 方法名命中 ⇒ `refuted`；零命中 ⇒ `confirmed`
    """
    _c = ctx.get("criterion") or {}
    _sig = signature_of(entry, _c)
    _file, _type, _method = split_signature(_sig)
    _method = _method.strip()
    if not _method:
        return _out(hc.RESULT_UNKNOWN,
                    "签名里没有方法名，只剩文件名无法定位具体问题（文本匹配会大面积误中）",
                    {"signature": _sig}, reason="coarse_signature")
    _w = window_sec(_c, DEFAULT_REUSE_WINDOW_SEC)
    _end = _end_of_window(entry, ctx)
    _start = _end - _w
    _rows, _files = _trace_rows(_start, _end)
    if not _rows:
        return _out(hc.RESULT_UNKNOWN,
                    f"窗口内没有任何调用记录（{_files} 份文件命中，可能是没跑也没留 trace）",
                    {"window": [_start, _end], "signature": _sig},
                    reason="no_coverage")
    _stem = os.path.splitext(os.path.basename(_file.replace("\\", "/")))[0]
    _hits, _strong, _origins = 0, 0, Counter()
    for _r in _rows:
        _text = f"{_r.get('prompt') or ''}\n{_r.get('response') or ''}"
        if _method not in _text:
            continue
        _hits += 1
        _origins[str(_r.get("origin") or "?")] += 1
        if _stem and _stem in _text:
            _strong += 1
    _ev = {"window": [_start, _end], "window_sec": _w, "signature": _sig,
           "match": "text", "matched_on": _method, "file_stem": _stem,
           "rows_in_window": len(_rows), "hits": _hits,
           "strong_hits": _strong, "by_origin": dict(_origins),
           "note": "calls_*.jsonl 无 issue_signature 字段 ⇒ 文本级近似匹配"}
    if _hits:
        return _out(hc.RESULT_REFUTED,
                    f"窗口内 {_hits} 次调用提到「{_method}」"
                    f"（其中 {_strong} 次同时出现文件名）",
                    _ev)
    return _out(hc.RESULT_CONFIRMED,
                f"窗口内 {len(_rows)} 次调用均未提到「{_method}」", _ev)


# ================================================================ 安装
_PROBE_FNS = {
    hc.KIND_RECURRENCE: probe_recurrence,
    hc.KIND_EFFECT: probe_effect,
    hc.KIND_REUSE: probe_reuse,
}


def install() -> dict:
    """把三个探针注册进对账器，返回 ``{kind: 是否装上}``。"""
    _o = {}
    for _k, _fn in _PROBE_FNS.items():
        try:
            hr.register_probe(_k, _fn)
            _o[_k] = _k in hr.probes()
        except Exception as _exc:
            silent_exc(_exc, where=f"hypothesis_probes.install:{_k}", level="warning")
            _o[_k] = False
    return _o


def version() -> str:
    return _M409_PROBE_VERSION


def selfcheck() -> dict:
    """三份数据源的可用性快照（供命令行 `--probe` 打印）。"""
    _snaps = _audit_snapshots()
    _eff = _load_effect()
    _traces = _trace_files()
    return {
        "version": _M409_PROBE_VERSION,
        "audit": {"files": len(_snaps),
                  "first": _snaps[0][0] if _snaps else None,
                  "last": _snaps[-1][0] if _snaps else None,
                  "entries": sum(len(e) for _t, e in _snaps)},
        "effect": {"verdicts": len(_eff.get("verdicts") or {})},
        "trace": {"files": len(_traces)},
    }
