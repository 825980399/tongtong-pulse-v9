"""hypothesis_reconciler.py —— 假设对账器（第409批 刀2）。

## 为什么需要「对账」这一层

刀1 只保证「每条假设都写下了判据」。**写下判据 ≠ 判据被验过。**

`data/evolution/lesson_strategies.json` 的 30 条老账里，`applied_total` 一直在涨、
`hit_count` 也有值，但从来没人回头问过一句「当初说这个策略有用，到底验了没有」。
假设写进去就再没出来过 —— 这不是学习，是记账。

对账是让假设闭环的唯一环节：**到期就查数据、就出结论、就写回。**

## 三态（只有三个，别加中间态）

| 结论 | 含义 | 依据 |
|---|---|---|
| `confirmed` | 判据真的成立（同问题没复发 / 补丁生效 / 经验被复用） | **有**正向观测数据 |
| `refuted` | 判据被推翻（同问题如期复发 / 补丁没生效） | **有**反向观测数据 |
| `unknown` | 判不了 —— 数据不足 / 判据不可程序化 / 探针没装 | **无**观测数据 |

★ `unknown` 必须是一等公民。把「查不到」写成 `refuted`，等于用沉默当反证；
  把「查不到」写成 `confirmed`，等于用愿望当证据。任务书刀2-6「跑不动的判据标
  unknown，不硬判」说的就是这件事。

## 到期选题（`due_entries`）

- `result` 为空 且 `due_at <= now` ⇒ 到期，首次判定
- `result == unknown` 且 `resolve_attempts < MAX_ATTEMPTS` ⇒ 重判（数据可能后到）
- 其余一律不动 —— **已定论的不重复判**（重复判会让 confirmed 反复被稀释）

## 写回

`strategies[key]` 上写 `result` / `resolved_at` / `resolve_attempts` /
`resolve_detail` / `resolve_evidence`。`refuted` 另进 refuted 清单（下批处置），
`confirmed` 另进本能候选队列（见下）。

## ★ 为什么 confirmed 只是「候选」而不是本能

`organs/body/PulseLiver.py::_can_upgrade_to_instinct` 有 **7 个硬条件**，其中条件 1
是「节点存在 ≥ 30 天（`INSTINCT.cooldown_days`）」。今天刚 confirmed 的假设，
节点年龄是 0 天，**永远过不了条件 1**。所以「晋升本能」在这里只能是「进候选队列
+ 附预检 + 记下最早可复核时刻」，真升级仍归 PulseLiver 判。印一句「已晋升本能」
但没有任何一条真进得去，比排队更糟。

★ 也不再从 evolution 反向 import organs：`PulseLiver` 在 `organs/` 下，本模块在
  `nucleus/evolution/` 下，硬接会形成 evolution → organs 的反向依赖（与 405 刀3
  同一处置：用队列文件解耦）。

## 每日 03:00

`register_hypothesis_reconciler()` 把本模块挂进 `DailyScheduler.add_daily_hook`
—— 与 402 刀3（`organ_emits`）、404 刀1（`causal_strength`）**同一个 03:00 挂点**，
在 `main.py` 同一个 try 里注册（复用既有 except，不新增静默 handler）。

## 灰度

`config.ENABLE_M409_HYPOTHESIS_CRITERIA`（刀1 已登记，默认 True）。
关闭时 `reconcile()` 直接返回 `skipped="switch_off"`，**不读不写**。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from nucleus._silent_except import silent_exc
from nucleus.evolution import hypothesis_criteria as hc
from nucleus.logger import get_module_logger

_logger = get_module_logger("HypothesisReconciler")

_M409_RECONCILE_VERSION = "409.2"

#: 每日 03:00 钩子名（`DailyScheduler.has_daily_hook` 可查）
RECONCILE_HOOK = "hypothesis_reconcile_409"

LEDGER_REL = os.path.join("data", "evolution", "lesson_strategies.json")
REFUTED_REL = os.path.join("data", "evolution", "hypothesis_refuted.json")
CANDIDATE_REL = os.path.join("data", "evolution", "instinct_candidates.json")
LAST_RUN_REL = os.path.join("data", "evolution", "hypothesis_reconcile.json")

#: `unknown` 最多重判几次。每日一次 ⇒ 默认给 3 天让数据后到；到顶后 unknown 定论。
MAX_ATTEMPTS = 3

#: 对账写回的附加字段
F_ATTEMPTS = "resolve_attempts"
F_DETAIL = "resolve_detail"
F_EVIDENCE = "resolve_evidence"

#: 结构化判据的探针注册表：kind -> fn(entry, ctx) -> {"result","detail","evidence"}
#: 刀3 填（recurrence / effect / reuse）。刀2 只建表与调度，不装具体探针。
_PROBES: dict[str, Callable[[dict, dict], dict]] = {}


#: ★测试/演练用：把四个落盘路径整体重定向到这个目录（None＝回生产根）。
#:   没有这个口，单测会直接往真 `data/evolution/` 里写三个清单 —— 而 `data/`
#:   整目录被 gitignore，写脏了 git 救不回来。
_DATA_DIR: str | None = None


def set_data_dir(path: str | None) -> str | None:
    """重定向落盘目录，返回新值（传 None 复位到生产根）。"""
    global _DATA_DIR
    _DATA_DIR = os.path.abspath(path) if path else None
    return _DATA_DIR


# --------------------------------------------------------------------- 路径
def _root() -> str:
    """项目根（本文件在 `nucleus/evolution/` 下，三层 up）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _data_root() -> str:
    """落盘根：默认项目根；`set_data_dir()` 生效时用它。"""
    return _DATA_DIR or _root()


def ledger_path() -> str:
    return os.path.join(_data_root(), LEDGER_REL)


def refuted_path() -> str:
    return os.path.join(_data_root(), REFUTED_REL)


def candidate_path() -> str:
    return os.path.join(_data_root(), CANDIDATE_REL)


def last_run_path() -> str:
    return os.path.join(_data_root(), LAST_RUN_REL)


# ----------------------------------------------------------------- 探针注册
def register_probe(kind: str, fn: Callable[[dict, dict], dict] | None) -> str:
    """注册（或 `fn=None` 注销）某种判据的探针。

    Args:
        kind: 判据种类（`recurrence` / `effect` / `reuse`）。
        fn: ``fn(entry, ctx) -> {"result", "detail", "evidence"}``；None 表示注销。

    Returns:
        规整后的 kind（便于断言）。
    """
    _k = str(kind or "").strip().lower()
    if not _k:
        return ""
    if fn is None:
        _PROBES.pop(_k, None)
    else:
        _PROBES[_k] = fn
    return _k


def probes() -> dict:
    """当前已注册探针的浅拷贝（只读，便于观测与断言）。"""
    return dict(_PROBES)


# ---------------------------------------------------------------- 读写（安全）
def _detect_eol(path: str) -> str:
    """按**字节**判主导行尾。新文件默认 CRLF（`data/` 下兄弟文件都是 CRLF）。"""
    if not os.path.isfile(path):
        return "\r\n"
    try:
        with open(path, "rb") as _f:
            _b = _f.read()
    except OSError as _exc:
        silent_exc(_exc, where="hypothesis_reconciler._detect_eol", level="debug")
        return "\r\n"
    return "\r\n" if b"\r\n" in _b else "\n"


def load_json(path: str, empty: dict | None = None) -> dict:
    """容错读：缺失/损坏/非 dict ⇒ 返回 ``empty``（**不抛**，但必须留痕）。

    ★ 只用于"读坏了也不致命"的旁路清单（refuted / candidates / last_run）。
      账本本身走 `_load_ledger()`，那条路读坏了**必须中止整轮对账**。
    """
    _empty = dict(empty) if isinstance(empty, dict) else {}
    try:
        if not os.path.isfile(path):
            return _empty
        with open(path, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _exc:
        silent_exc(_exc, where="hypothesis_reconciler.load_json", level="warning")
        _logger.warning("[409刀2] 读取失败 %s：%s ⇒ 用空结构起步",
                        path, type(_exc).__name__)
        return _empty
    if not isinstance(_d, dict):
        _logger.warning("[409刀2] 结构不符 %s（%s）⇒ 用空结构起步",
                        path, type(_d).__name__)
        return _empty
    return _d


def save_json(path: str, data: dict) -> str:
    """原子写：同目录 `.tmp409` + `os.replace`（半个 JSON 比不写更糟）。

    行尾按原文件字节判定后**显式**回写 —— 不靠 Windows 文本模式的隐式转换，
    否则测试与生产会写出两种行尾。
    """
    _dir = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(_dir):
        os.makedirs(_dir, exist_ok=True)
    _eol = _detect_eol(path)
    _text = json.dumps(data, ensure_ascii=False, indent=2)
    if _eol != "\n":
        _text = _text.replace("\n", _eol)
    _tmp = path + ".tmp409"
    with open(_tmp, "wb") as _f:
        _f.write(_text.encode("utf-8"))
    os.replace(_tmp, path)
    return path


def _load_ledger(path: str) -> tuple[dict, bool]:
    """读账本，返回 ``(账本, 是否可安全回写)``。

    ★ 这个 `ok` 是**防丢数据**的闸门：`data/` 整目录被 gitignore，写坏了 git 救不
      回来。文件不存在 ⇒ 视为空账且**可写**（首次运行）；文件存在但读不出来 ⇒
      视为**不可写**，本轮直接放弃（绝不能用空账覆盖掉 30 条真账）。
    """
    if not os.path.isfile(path):
        return {"version": "335.1", "updated_at": 0.0, "strategies": {}}, True
    try:
        with open(path, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _exc:
        silent_exc(_exc, where="hypothesis_reconciler._load_ledger", level="warning")
        _logger.warning("[409刀2] 账本读取失败 %s：%s ⇒ 本轮放弃（不覆盖原文件）",
                        path, type(_exc).__name__)
        return {}, False
    if not isinstance(_d, dict) or not isinstance(_d.get("strategies"), dict):
        _logger.warning("[409刀2] 账本结构不符 %s ⇒ 本轮放弃（不覆盖原文件）", path)
        return {}, False
    return _d, True


# ------------------------------------------------------------------- 到期选题
def due_entries(ledger: dict, now: float | None = None,
                max_attempts: int | None = None) -> list[tuple[str, dict]]:
    """挑出「现在该判」的条目，按 `due_at` 升序（先到期的先判）。

    三种情况才算到期：
      1. `result` 为空 且 `due_at <= now`（首次判定）
      2. `result == unknown` 且 `resolve_attempts < max_attempts`（数据可能后到）
      3. 其余一律不动

    ★ 没有 `due_at`（或不是数值）的条目**永远不入列**：没有到期时刻就没有「该不该
      现在判」的依据，硬判等于把"没安排"当成"已到期"。
    """
    _now = float(now if now is not None else time.time())
    _cap = MAX_ATTEMPTS if max_attempts is None else int(max_attempts)
    _out: list[tuple[str, dict]] = []
    for _k, _e in (ledger.get("strategies") or {}).items():
        if not isinstance(_e, dict):
            continue
        _due = _e.get(hc.F_DUE)
        if isinstance(_due, bool) or not isinstance(_due, (int, float, str)):
            continue
        try:
            _due_f = float(_due)
        except (TypeError, ValueError) as _exc:
            # ★不是"没到期"，是"到期时刻不是数" —— 两种情况的处置一样（不入列），
            #   但必须留痕，否则一条永远判不了的假设会彻底看不见。
            #   ★异常变量**不能**叫 `_e`：循环变量就叫 `_e`，Python 3 会在 except
            #     块末尾 `del` 掉该名字，后面的 `_e.get(...)` 直接 UnboundLocalError。
            silent_exc(_exc, where="hypothesis_reconciler.due_entries:due", level="debug")
            continue
        if _due_f > _now:
            continue
        _res = str(_e.get(hc.F_RESULT) or "").strip().lower()
        if not _res:
            _out.append((_k, _e))
            continue
        if _res == hc.RESULT_UNKNOWN:
            try:
                _att = int(_e.get(F_ATTEMPTS) or 0)
            except (TypeError, ValueError) as _exc2:
                silent_exc(_exc2, where="hypothesis_reconciler.due_entries:attempts",
                           level="debug")
                _att = 0
            if _att < _cap:
                _out.append((_k, _e))
    _out.sort(key=lambda kv: float(kv[1].get(hc.F_DUE) or 0))
    return _out


# --------------------------------------------------------------------- 单条判定
def judge_one(key: str, entry: dict, now: float | None = None,
              ctx: dict | None = None) -> dict:
    """判一条假设，返回 ``{"key","kind","result","detail","evidence","reason"}``。

    ★ 凡是「判不了」都收敛到 `unknown` 并给出**机器可读的 reason**：
      `no_criterion` / `not_structured` / `no_probe` / `probe_error` /
      `probe_bad_return` / `probe_bad_result`。
      有了 reason 才能统计"到底卡在哪一环"，否则一堆 unknown 等于什么都没说。
    """
    _now = float(now if now is not None else time.time())
    _out: dict[str, Any] = {"key": key, "kind": "",
                            "result": hc.RESULT_UNKNOWN,
                            "detail": "", "evidence": {}, "reason": ""}
    _raw = entry.get(hc.F_CRITERION) if isinstance(entry, dict) else None
    _crit = hc.normalize_criterion(_raw)
    if _crit is None:
        if _raw in (None, "", {}):
            _out["detail"] = "没有 observable_criterion（老条目或入库时缺判据）"
            _out["reason"] = "no_criterion"
        else:
            _out["detail"] = f"判据不可程序化：{type(_raw).__name__}"
            _out["reason"] = "not_structured"
        return _out
    _kind = str(_crit.get("kind") or "").strip().lower()
    _out["kind"] = _kind
    _fn = _PROBES.get(_kind)
    if _fn is None:
        _out["detail"] = f"探针未注册：{_kind}（刀3 未装或灰度关闭）"
        _out["reason"] = "no_probe"
        return _out
    try:
        _r = _fn(entry, {"now": _now, "criterion": _crit,
                         "key": key, "ctx": ctx or {}})
    except Exception as _exc:
        # ★探针抛异常不等于"判据被推翻" —— 是"这次没判成"。
        silent_exc(_exc, where="hypothesis_reconciler.judge_one", level="warning")
        _out["detail"] = f"探针异常：{type(_exc).__name__}: {_exc}"
        _out["reason"] = "probe_error"
        return _out
    if not isinstance(_r, dict):
        _out["detail"] = f"探针返回非 dict：{type(_r).__name__}"
        _out["reason"] = "probe_bad_return"
        return _out
    _res = str(_r.get("result") or "").strip().lower()
    if _res not in hc.ALL_RESULTS:
        _out["detail"] = f"探针返回非法结论：{_res!r}"
        _out["reason"] = "probe_bad_result"
        return _out
    _out["result"] = _res
    _out["detail"] = str(_r.get("detail") or "")
    _ev = _r.get("evidence")
    _out["evidence"] = _ev if isinstance(_ev, dict) else {}
    _out["reason"] = str(_r.get("reason") or "")
    return _out


# ------------------------------------------------------------ confirmed → 候选
def _instinct_budget() -> tuple[int, int, int]:
    """返回 ``(当前本能数, 上限, 冷却天数)``；读不到一律给保守值。"""
    _cap, _cool = 20, 30
    try:
        import config as _c
        _cfg = getattr(_c, "INSTINCT", {}) or {}
        _cap = int(_cfg.get("max_count", 20))
        _cool = int(_cfg.get("cooldown_days", 30))
    except Exception as _exc:
        silent_exc(_exc, where="hypothesis_reconciler._instinct_budget", level="debug")
    _cur = 0
    for _rel in (os.path.join("data", "pulse_instinct_snapshot.json"),
                 os.path.join("data", "knowledge", "pulse_instinct_snapshot.json")):
        _d = load_json(os.path.join(_data_root(), _rel), {})
        try:
            _n = int(_d.get("total_nodes") or len(_d.get("nodes") or []))
        except (TypeError, ValueError) as _exc:
            silent_exc(_exc, where="hypothesis_reconciler._instinct_budget",
                       level="debug")
            _n = 0
        # ★两份快照取大者：小的那份是旧快照（knowledge 下 2026-07-19，4 节点），
        #   拿它当"当前本能数"会低估占用、多放候选进去。
        _cur = max(_cur, _n)
    return _cur, _cap, _cool


def queue_instinct_candidate(entry: dict, key: str, now: float | None = None) -> dict:
    """把一条 `confirmed` 假设排进本能候选队列，返回候选记录（不落盘）。

    ★ 预检必须**诚实**：`PulseLiver` 的 7 个条件里有 5 个依赖 PulseNode 字段
      （`activation_count` / `abstraction` / `keywords` / `source_organ` / 跨领域
      引用），账本条目里一个都没有 ⇒ 一律记进 `not_evaluable`，不假装通过。
    """
    _now = float(now if now is not None else time.time())
    _cur, _cap, _cool = _instinct_budget()
    _eligible_after = _now + _cool * 86400.0
    return {
        "queued_at": _now,
        "source": "hypothesis_confirmed",
        "rule": entry.get("rule") or "",
        "strategy": str(entry.get("strategy") or "")[:200],
        "status": "queued",
        "precheck": {
            # 唯一能从账本侧真判的一条：上限还够不够
            "cap_ok": _cur < _cap,
            "instinct_current": _cur,
            "instinct_cap": _cap,
            # 条件 1：节点年龄 0 天 < 冷却 30 天 ⇒ 现在一定过不了
            "cooldown_ok": False,
            "cooldown_days": _cool,
            "eligible_after": _eligible_after,
            # 剩下的条件账本里没有对应字段，不假装能判
            "not_evaluable": ["source_organ", "activation_count", "abstraction",
                              "cross_domains", "keyword_overlap"],
        },
        "eligible_now": False,
    }


# ------------------------------------------------------------------- 主对账
def reconcile(now: float | None = None, ledger: dict | None = None,
              path: str | None = None, dry_run: bool = False,
              write: bool = True, ctx: dict | None = None) -> dict:
    """跑一轮对账。

    Args:
        now: 注入当前时刻（测试用）。
        ledger: 直接给账本（测试用；给了就不读盘）。
        path: 账本路径（默认项目内 `data/evolution/lesson_strategies.json`）。
        dry_run: True ⇒ 只判不写（返回结论，磁盘零变化）。
        write: False ⇒ 连旁路清单都不写（纯观测）。
        ctx: 传给探针的上下文。

    Returns:
        ``{"version","at","skipped","due","confirmed","refuted","unknown",
        "rows","written","refuted_total","candidates_queued","instinct_current",
        "instinct_cap"}``
    """
    _now = float(now if now is not None else time.time())
    _rep: dict[str, Any] = {
        "version": _M409_RECONCILE_VERSION, "at": _now, "skipped": "",
        "due": 0, "confirmed": 0, "refuted": 0, "unknown": 0, "rows": [],
        "written": False, "refuted_total": 0, "candidates_queued": 0,
        "instinct_current": 0, "instinct_cap": 0,
    }
    if not hc.enabled():
        _rep["skipped"] = "switch_off"
        return _rep

    _p = path or ledger_path()
    if ledger is None:
        _d, _ok = _load_ledger(_p)
        if not _ok:
            _rep["skipped"] = "ledger_unreadable"
            return _rep
    else:
        _d = ledger
    if not isinstance(_d.get("strategies"), dict):
        _rep["skipped"] = "bad_ledger"
        return _rep

    _due = due_entries(_d, now=_now)
    _rep["due"] = len(_due)
    for _key, _e in _due:
        _j = judge_one(_key, _e, now=_now, ctx=ctx)
        _rep["rows"].append(_j)
        _rep[_j["result"]] = _rep.get(_j["result"], 0) + 1
        if dry_run:
            continue
        _e[hc.F_RESULT] = _j["result"]
        _e[hc.F_RESOLVED] = _now
        try:
            _e[F_ATTEMPTS] = int(_e.get(F_ATTEMPTS) or 0) + 1
        except (TypeError, ValueError) as _exc:
            silent_exc(_exc, where="hypothesis_reconciler.reconcile:attempts",
                       level="debug")
            _e[F_ATTEMPTS] = 1
        if _j["detail"]:
            _e[F_DETAIL] = _j["detail"]
        if _j["evidence"]:
            _e[F_EVIDENCE] = _j["evidence"]

    if dry_run or not _due:
        return _rep

    # 账本回写（dry_run 已在上面 return；这里只看 write）
    if write:
        _d["updated_at"] = _now
        _d["reconcile_version"] = _M409_RECONCILE_VERSION
        try:
            save_json(_p, _d)
            _rep["written"] = True
        except OSError as _exc:
            silent_exc(_exc, where="hypothesis_reconciler.reconcile:save_ledger",
                       level="warning")
            _logger.warning("[409刀2] 账本回写失败 %s：%s（本轮结论丢弃）",
                            _p, type(_e).__name__)
            _rep["skipped"] = "save_failed"
            return _rep

    _rep["refuted_total"] = _append_refuted(_rep["rows"], _d, _now) if write else 0
    if write:
        _rep["candidates_queued"] = _queue_candidates(_rep["rows"], _d, _now)
    _cur, _cap, _cool = _instinct_budget()
    _rep["instinct_current"], _rep["instinct_cap"] = _cur, _cap
    _rep["instinct_cooldown_days"] = _cool
    if write:
        _append_run(_rep)
    return _rep


def _append_refuted(rows: list[dict], ledger: dict, now: float) -> int:
    """把本轮 `refuted` 写进清单（下批处置），返回清单总条数。"""
    _p = refuted_path()
    _d = load_json(_p, {})
    _items = _d.get("items")
    if not isinstance(_items, dict):
        _items = {}
    for _r in rows:
        if _r.get("result") != hc.RESULT_REFUTED:
            continue
        _k = _r.get("key")
        _old = _items.get(_k) if isinstance(_items.get(_k), dict) else {}
        try:
            _times = int(_old.get("times") or 0) + 1
        except (TypeError, ValueError) as _exc:
            silent_exc(_exc, where="hypothesis_reconciler._append_refuted:times",
                       level="debug")
            _times = 1
        _e = (ledger.get("strategies") or {}).get(_k) or {}
        _items[_k] = {
            "rule": _e.get("rule") or "",
            "strategy": str(_e.get("strategy") or "")[:200],
            "kind": _r.get("kind") or "",
            "detail": _r.get("detail") or "",
            "evidence": _r.get("evidence") or {},
            "first_refuted_at": _old.get("first_refuted_at") or now,
            "last_refuted_at": now,
            "times": _times,
            "status": "pending_dispose",
        }
    _d["version"] = _M409_RECONCILE_VERSION
    _d["updated_at"] = now
    _d["items"] = _items
    _d.setdefault("note", "第409批刀2 产出：refuted 假设清单，处置留待下批")
    try:
        save_json(_p, _d)
    except OSError as _exc:
        silent_exc(_exc, where="hypothesis_reconciler._append_refuted", level="warning")
        _logger.warning("[409刀2] refuted 清单写入失败：%s", type(_e).__name__)
        return 0
    return len(_items)


def _queue_candidates(rows: list[dict], ledger: dict, now: float) -> int:
    """把本轮 `confirmed` 排进本能候选队列，返回**新增**条数。"""
    _p = candidate_path()
    _d = load_json(_p, {})
    _items = _d.get("items")
    if not isinstance(_items, dict):
        _items = {}
    _added = 0
    for _r in rows:
        if _r.get("result") != hc.RESULT_CONFIRMED:
            continue
        _k = _r.get("key")
        if _k in _items:
            # 已在队列：只更新证据，不重复排队（重复排队会虚增候选数）
            if isinstance(_items[_k], dict):
                _items[_k]["last_confirmed_at"] = now
                _items[_k]["evidence"] = _r.get("evidence") or {}
            continue
        _e = (ledger.get("strategies") or {}).get(_k) or {}
        _items[_k] = queue_instinct_candidate(_e, _k, now=now)
        _items[_k]["last_confirmed_at"] = now
        _items[_k]["evidence"] = _r.get("evidence") or {}
        _added += 1
    _cur, _cap, _cool = _instinct_budget()
    _d["version"] = _M409_RECONCILE_VERSION
    _d["updated_at"] = now
    _d["instinct_current"] = _cur
    _d["instinct_cap"] = _cap
    _d["items"] = _items
    _d.setdefault(
        "note",
        "第409批刀2 产出：confirmed 假设 → 本能候选队列。"
        "真升级仍归 PulseLiver._can_upgrade_to_instinct（7 条件，含 30 天冷却）判。")
    try:
        save_json(_p, _d)
    except OSError as _exc:
        silent_exc(_exc, where="hypothesis_reconciler._queue_candidates", level="warning")
        _logger.warning("[409刀2] 候选队列写入失败：%s", type(_e).__name__)
        return 0
    return _added


def _append_run(rep: dict) -> None:
    """落最后一次运行快照（保留最近 30 轮），供"每日 03:00 真的跑了吗"取证。"""
    _p = last_run_path()
    _d = load_json(_p, {})
    _runs = _d.get("runs")
    if not isinstance(_runs, list):
        _runs = []
    _slim = {k: v for k, v in rep.items() if k != "rows"}
    _slim["rows"] = [{k: r.get(k) for k in ("key", "kind", "result", "reason")}
                     for r in (rep.get("rows") or [])]
    _runs.append(_slim)
    _d["version"] = _M409_RECONCILE_VERSION
    _d["updated_at"] = rep.get("at") or time.time()
    _d["runs"] = _runs[-30:]
    try:
        save_json(_p, _d)
    except OSError as _exc:
        silent_exc(_exc, where="hypothesis_reconciler._append_run", level="warning")
        _logger.warning("[409刀2] 运行快照写入失败：%s", type(_exc).__name__)


# ------------------------------------------------------------------- 调度挂载
def reconcile_scheduled() -> dict:
    """每日钩子入口：**永不抛**（调度侧扩展点，失败不影响主流程）。"""
    try:
        _rep = reconcile()
        _logger.info("[409刀2] 假设对账 due=%s confirmed=%s refuted=%s unknown=%s "
                     "skip=%s written=%s",
                     _rep.get("due"), _rep.get("confirmed"), _rep.get("refuted"),
                     _rep.get("unknown"), _rep.get("skipped") or "-",
                     _rep.get("written"))
        return {k: _rep.get(k) for k in
                ("due", "confirmed", "refuted", "unknown", "written", "skipped")}
    except Exception as _exc:
        silent_exc(_exc, where="hypothesis_reconciler.reconcile_scheduled",
                   level="warning")
        _logger.warning("[409刀2] 对账失败（不影响主流程）：%s: %s",
                        type(_exc).__name__, _exc)
        return {"error": type(_exc).__name__}


def register_hypothesis_reconciler(scheduler=None):
    """把对账挂进 `DailyScheduler` 的**每日 03:00** 调度点。

    返回调度器实例；不可用 / 不支持每日钩子时返回 None（不影响启动）。
    """
    try:
        _sched = scheduler
        if _sched is None:
            from nucleus.self_awareness.DailyScheduler import get_daily_scheduler
            _sched = get_daily_scheduler()
        if _sched is None or not hasattr(_sched, "add_daily_hook"):
            return None
        _sched.add_daily_hook(RECONCILE_HOOK, reconcile_scheduled)
        return _sched
    except Exception as _exc:
        silent_exc(_exc, where="hypothesis_reconciler.register_hypothesis_reconciler",
                   level="warning")
        return None


def version() -> str:
    return _M409_RECONCILE_VERSION
