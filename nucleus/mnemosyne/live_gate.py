# -*- coding: utf-8 -*-
"""live_gate.py —— shadow→live 开闸判定器 + 回滚三线 + 分档顺序锁（350 批刀2/刀3）

来源：烛微 350 交付（`5c34fe8`）93/94 号 + 任务书刀2/刀3。

**本模块只做判定，不做动作**（与 `release_policy` 同纪律：决策与执行分离）。

四条同立条件（`live_gate_ok`，任一条不立 ⇒ `ok=False`，逐条报 blocked_by）：

    C1 heap_g2_3beats_same_gen —— 同 proc_gen 下 heap ≥ G2 线**连续 ≥3 拍**
    C2 w1_alive                —— 外部心跳 W1 verdict == "alive"
    C3 recall_hit_rate_ge_95   —— 回载命中率 ≥95%（取 `PulseNodePool.reload_hit_rate()`）
    C4 cold_index_writable     —— 冷索引可写（取驱逐落索引的实证，不是"应该能写"）

为什么必须"四条件同立"而不是打分：这四条各自都能单独满足，但**任何一条缺失**
都会让 live 释放变成"放了取不回来"。凑一个总分再比阈值，等于允许用三条满分
抵一条空项 —— 那正是 337 事故的形状（判决全绿、动作全错）。故用**布尔同立**。

★口径纪律（铁律166）：
  · `heap_mb=None` / `hit_rate=None` / `cold_index=None` 一律**不立**，理由写
    `unavailable` —— "没测到"绝不等于"达标"；
  · `hit_rate=None` 是"无样本"，**不填 0**（0 会让条件恒假，看起来像"没达标"，
    掩盖了"根本还没跑起来"这个不同事实）。

分档顺序（刀3）：G2 观察 → 持续 7 天命中率 ≥95% → G3 冷化 → G4 永久告警。
`ESCALATION_ORDER` 写死并通过 `assert_escalation_order()` 断言，**跳档即红**。
"""

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus import absence_notice as _absence_notice
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("live_gate")

#: 口径版本（判据变更必须升版）
LIVE_GATE_VERSION = "350.gate.v1"

#: ★模块级 live 硬开关。**默认 False = shadow**（落地不触发）。
#:
#: 为什么要"模块级"而不只放 config：config 是运行期可改的文本文件，
#: 而开闸是**不可逆动作**。设成常量后，翻开它必须走代码变更（提交、复核、
#: 记录），不是改一个字符就能生效 —— 349 刀11 的 `PULSE_VERIFY_POOL` 同一范式。
LIVE_ENABLED = False

# ---------------------------------------------------------------- 分档（刀3）
STAGE_SHADOW = "shadow"
STAGE_G2 = "G2_SOFT_RELEASE"
STAGE_G3 = "G3_COLD_DEMOTE"
STAGE_G4 = "G4_ALARM_ONLY"

#: 开闸顺序：**写死，不可跳档**。G4 是终点（永久告警），不在链上继续升。
ESCALATION_ORDER = (STAGE_G2, STAGE_G3, STAGE_G4)
STAGE_RANK = {STAGE_SHADOW: 0, STAGE_G2: 1, STAGE_G3: 2, STAGE_G4: 3}

#: G2→G3 门槛：命中率线 + 连续观察时长
HIT_RATE_LINE = 0.95
G2_HOLD_SEC = 7 * 86400.0

#: 回滚线① 的时间窗与次数
RB_SAME_NODE_WINDOW_SEC = 1800.0
RB_SAME_NODE_MAX_RECALL = 3

# ---------------------------------------------------------------- 四条件标识
COND_HEAP = "heap_g2_3beats_same_gen"
COND_W1 = "w1_alive"
COND_RECALL = "recall_hit_rate_ge_95"
COND_COLD = "cold_index_writable"
COND_ORDER = (COND_HEAP, COND_W1, COND_RECALL, COND_COLD)

#: 回滚三线标识
RB_SAME_NODE = "same_node_release_recall_ge3"
RB_RECALL_FAIL = "recall_failed_once"
RB_EVICT_NO_INDEX = "evict_without_cold_index"

#: 回滚动作
ACT_NONE = "none"
ACT_PAUSE = "pause_category"
ACT_SHADOW = "force_shadow"


# ---------------------------------------------------------------- 配置/路径
def _cfg(name: str, default: Any) -> Any:
    try:
        import config as _c
        return getattr(_c, name, default)
    except Exception as _e:                                   # noqa: BLE001
        silent_exc(_e, where="live_gate::_cfg " + name)
        return default


def live_enabled() -> bool:
    """是否允许 live。**两道锁同时开**才算：模块常量 ∧ config 开关。"""
    return bool(LIVE_ENABLED) and bool(_cfg("ENABLE_MEMORY_RELEASE_LIVE", False))


def default_state_path() -> str:
    """台账默认路径（`data/reports/memory/live_gate_state.json`）。

    隔离验证必须显式传 `state_path`（铁律158/189：默认路径就是生产路径）。
    """
    return os.path.join("data", "reports", "memory", "live_gate_state.json")


def _load_state(state_path: str | None = None) -> dict:
    _p = state_path or default_state_path()
    try:
        with open(_p, "r", encoding="utf-8") as _f:
            _d = json.load(_f)
        if not isinstance(_d, dict):
            return _blank_state(_p, "not_dict")
        _d.setdefault("paused", {})
        _d.setdefault("history", [])
        _d.setdefault("stage", STAGE_SHADOW)
        _d["_path"] = _p
        _d["_read"] = "ok"
        return _d
    except FileNotFoundError:
        # ★E10（T-350开闸回载缺口-1）：缺席是确定性事实，**不得每拍重复**留痕；
        #   只在首次 / 曾存在又被清掉（事故信号）时打一条。
        if _absence_notice.note_absent(_p):
            _module_logger.debug("[350开闸] 台账不存在 path=%s（首次运行或刚被清理）", _p)
        return _blank_state(_p, "absent")
    except Exception as _e:                                   # noqa: BLE001
        silent_exc(_e, where="live_gate::_load_state")
        return _blank_state(_p, "unreadable")


def _blank_state(path: str, read_state: str) -> dict:
    return {"schema": LIVE_GATE_VERSION, "stage": STAGE_SHADOW,
            "paused": {}, "history": [], "g2_since_ts": None,
            "hit_ok_since_ts": None, "_path": path, "_read": read_state}


def _save_state(state: dict, state_path: str | None = None) -> bool:
    """原子落盘（铁律182：先算 data → `.tmp` → `os.replace` → 读回不在此处）。"""
    _p = state_path or state.get("_path") or default_state_path()
    _body = {k: v for k, v in state.items() if not str(k).startswith("_")}
    try:
        _dir = os.path.dirname(_p)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        _tmp = _p + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(_body, _f, ensure_ascii=False, indent=1)
        os.replace(_tmp, _p)
        return True
    except Exception as _e:                                   # noqa: BLE001
        silent_exc(_e, where="live_gate::_save_state", level="warning")
        return False


# ---------------------------------------------------------------- 类别暂停集
def pause_category(category: str, why: str = "", *,
                   now: float | None = None,
                   state_path: str | None = None) -> dict:
    """把某个释放类别**暂停**（回滚线①的处置动作）。幂等：重复暂停不重复记账。"""
    _cat = str(category or "").strip()
    if not _cat:
        return {"ok": False, "reason": "empty_category", "changed": False}
    _st = _load_state(state_path)
    _ts = float(now if now is not None else time.time())
    _old = _st["paused"].get(_cat)
    if isinstance(_old, dict):
        _old["repeat"] = int(_old.get("repeat", 0)) + 1
        _old["last_at"] = _ts
        _saved = _save_state(_st, state_path)
        _module_logger.warning(
            "[350开闸] 类别暂停(重复) category=%s 累计=%d 落盘=%s",
            _cat, _old["repeat"], "是" if _saved else "**失败**")
        return {"ok": _saved, "changed": False, "repeat": _old["repeat"],
                "persisted": _saved}
    _st["paused"][_cat] = {"since": _ts, "why": str(why or "")[:200], "repeat": 0}
    # ★366刀N2：暂停也进 history（原来只有 paused 快照，历史里查不到"何时停的"）
    _st.setdefault("history", []).append(
        {"ts": _ts, "action": "pause", "category": _cat,
         "why": str(why or "")[:200], "duration_s": None})
    _saved = _save_state(_st, state_path)
    _module_logger.warning(
        "[350开闸] 类别暂停 category=%s 因=%s 落盘=%s",
        _cat, str(why or "")[:120], "是" if _saved else "**失败**")
    return {"ok": _saved, "changed": True, "persisted": _saved}


def resume_category(category: str, *, state_path: str | None = None,
                    now: float | None = None) -> dict:
    """解除暂停（人工动作；不在自动回滚链上）。

    ★366刀N2：解除侧补**时长与成对历史** —— 过去只有一行 "类别恢复" INFO，
    查不出"停了多久、原因为何"，与暂停侧的 WARNING 配不成事件对。
    """
    _cat = str(category or "").strip()
    _st = _load_state(state_path)
    if _cat not in _st["paused"]:
        return {"ok": True, "changed": False, "reason": "not_paused"}
    _ts = float(now if now is not None else time.time())
    _old = _st["paused"].pop(_cat, None) or {}
    _since = float(_old.get("since") or _ts)
    _dur = round(max(0.0, _ts - _since), 2)
    _st.setdefault("history", []).append(
        {"ts": _ts, "action": "resume", "category": _cat,
         "why": str(_old.get("why") or "")[:200], "duration_s": _dur})
    _saved = _save_state(_st, state_path)
    _module_logger.info("[350开闸] 类别恢复 category=%s 暂停时长=%ss 原原因=%s 落盘=%s",
                        _cat, _dur, str(_old.get("why") or "-")[:80],
                        "是" if _saved else "**失败**")
    return {"ok": _saved, "changed": True, "persisted": _saved, "duration_s": _dur}


def pause_history(limit: int = 20, *, state_path: str | None = None) -> list:
    """★366刀N2：读最近的暂停/解除事件对（只读拷贝；同 schema：ts/action/category/duration_s）。"""
    _st = _load_state(state_path)
    _n = max(1, int(limit))
    return [dict(x) for x in list(_st.get("history") or [])[-_n:]]


def paused_categories(state_path: str | None = None) -> list:
    return sorted(_load_state(state_path)["paused"].keys())


def is_paused(category: str, state_path: str | None = None) -> bool:
    return str(category or "") in _load_state(state_path)["paused"]


# ---------------------------------------------------------------- 四条件同立
def _cond_heap(heap_mb: Any, heap_history: Any, proc_gen: Any) -> dict:
    """C1：同代次 heap ≥ G2 线持续 ≥3 拍。

    `heap_history` 元素形如 `{"proc_gen": "...", "heap_mb": 1234.5}`；
    **只用同一 proc_gen 的尾部样本**（跨代次的样本不能拼成"持续"——重启本身
    就会让 heap 归零，拿重启前的读数凑拍数等于自欺）。
    """
    _g2 = 1200.0
    try:
        from nucleus.mnemosyne import release_policy as _rp
        _g2 = float(_rp.thresholds()["g2_heap_mb"])
    except Exception as _e:                                   # noqa: BLE001
        silent_exc(_e, where="live_gate::_cond_heap", level="debug")
    _hist = [h for h in (heap_history or []) if isinstance(h, dict)]
    _pg = str(proc_gen or "")
    _same = [h for h in _hist if str(h.get("proc_gen") or "") == _pg] if _pg else []
    _tail = _same[-3:]
    _missing = [i for i, h in enumerate(_tail)
                if not isinstance(h.get("heap_mb"), (int, float))]
    if len(_tail) < 3:
        return {"pass": False, "value": len(_tail),
                "why": "同代次样本仅 %d 拍（需≥3；跨代次不拼）" % len(_tail)}
    if _missing:
        return {"pass": False, "value": len(_tail),
                "why": "尾部样本含测不到的 heap（不默认达标）"}
    _min = min(float(h["heap_mb"]) for h in _tail)
    _ok = _min >= _g2
    return {"pass": bool(_ok), "value": round(_min, 1),
            "why": "" if _ok else "尾部 3 拍最低 heap %.0fMB < G2 线 %.0fMB" % (_min, _g2)}


def _cond_w1(w1_verdict: Any) -> dict:
    _v = str(w1_verdict or "unavailable")
    return {"pass": _v == "alive", "value": _v,
            "why": "" if _v == "alive" else "W1=%s（非 alive 不放行）" % _v}


def _cond_recall(reload_stats: Any) -> dict:
    """C3：回载命中率 ≥95%。`None` = 无样本 ⇒ **不立**（不是 0，不是达标）。"""
    if not isinstance(reload_stats, dict):
        return {"pass": False, "value": None, "why": "回载统计不可用（不默认达标）"}
    _hr = reload_stats.get("hit_rate")
    _den = reload_stats.get("denominator")
    if _hr is None:
        return {"pass": False, "value": None,
                "why": "无样本（denominator=%r）⇒ 不判达标" % (_den,)}
    try:
        _ok = float(_hr) >= HIT_RATE_LINE
    except (TypeError, ValueError) as _hre:
        _module_logger.warning("[350开闸] 命中率非数值（不当作达标）: %r %s", _hr, _hre)
        return {"pass": False, "value": _hr, "why": "hit_rate 非数值"}
    return {"pass": bool(_ok), "value": round(float(_hr), 4),
            "why": "" if _ok else "命中率 %.2f%% < %.0f%%" % (float(_hr) * 100, HIT_RATE_LINE * 100)}


def _cond_cold(cold_index_writable: Any) -> dict:
    """C4：冷索引可写。取**实证**（真写过一条），不接受"配置允许写"。"""
    if isinstance(cold_index_writable, dict):
        _w = cold_index_writable.get("writable")
        _n = cold_index_writable.get("index_entries")
        if _w is None:
            return {"pass": False, "value": None,
                    "why": "冷索引写状态未探明（%s）" % (cold_index_writable.get("state") or "unknown")}
        return {"pass": bool(_w), "value": _n,
                "why": "" if _w else "冷索引不可写（entries=%r）" % (_n,)}
    if cold_index_writable is None:
        return {"pass": False, "value": None, "why": "冷索引写实证缺失（不默认达标）"}
    return {"pass": bool(cold_index_writable), "value": None,
            "why": "" if cold_index_writable else "冷索引不可写"}


def live_gate_ok(*, heap_mb: Any = None,
                 heap_history: list | None = None,
                 w1_verdict: Any = None,
                 reload_stats: Any = None,
                 cold_index_writable: Any = None,
                 proc_gen: str = "",
                 paused: list | None = None) -> dict:
    """四条件同立判定（**纯函数**：不读盘、不写盘，除阈值外无副作用）。"""
    _conds = {
        COND_HEAP: _cond_heap(heap_mb, heap_history, proc_gen),
        COND_W1: _cond_w1(w1_verdict),
        COND_RECALL: _cond_recall(reload_stats),
        COND_COLD: _cond_cold(cold_index_writable),
    }
    _blocked = [k for k in COND_ORDER if not _conds[k]["pass"]]
    _paused = sorted({str(x) for x in (paused or []) if str(x)})
    return {
        "schema": LIVE_GATE_VERSION,
        "ok": not _blocked,
        "conds": _conds,
        "blocked_by": _blocked,
        "paused": _paused,
        "live_enabled": live_enabled(),
        "mode": "live" if (not _blocked and not _paused and live_enabled()) else "shadow",
        "proc_gen": str(proc_gen or ""),
        "ts": time.time(),
    }


def gate_line(g: dict) -> str:
    """一行可 grep 的开闸留痕（每个心跳拍都打，无静默）。"""
    _c = g.get("conds") or {}

    def _v(k):
        _x = _c.get(k) or {}
        return "%s" % (_x.get("value"),)

    return ("[350开闸] live_gate ok=%s mode=%s heap=%s w1=%s 回载=%s 冷索引=%s "
            "拦=%s 暂停=%s live_sw=%s proc_gen=%s" % (
                "Y" if g.get("ok") else "N", g.get("mode"),
                _v(COND_HEAP), _v(COND_W1), _v(COND_RECALL), _v(COND_COLD),
                ",".join(g.get("blocked_by") or []) or "-",
                ",".join(g.get("paused") or []) or "-",
                "off" if not g.get("live_enabled") else "on",
                g.get("proc_gen") or "unknown"))


def log_gate(g: dict, *, where: str = "") -> None:
    _module_logger.info("%s %s", gate_line(g), where or "")


# ---------------------------------------------------------------- 回滚三线
def evaluate_rollback(events: list, *, now: float | None = None,
                      window_sec: float | None = None) -> dict:
    """回滚三线纯函数判定。

    `events` 元素：`{"kind": "release|recall_ok|recall_fail|evict",
                     "node_id": str, "category": str, "ts": float,
                     "cold_index_written": bool}`

    三线（任一命中即动作，**就高不就低**：force_shadow 压过 pause_category）：
      ① 同节点 30min 内「释放→回载」≥3 次 ⇒ 暂停该类别
      ② 回载失败 1 次 ⇒ 立即回 shadow 并告警（一次都不容错：取不回来就是丢了）
      ③ 驱逐未写冷索引 ⇒ 不允许 live（该次驱逐等于**真丢**）
    """
    _now = float(now if now is not None else time.time())
    _win = float(window_sec if window_sec is not None else RB_SAME_NODE_WINDOW_SEC)
    _lines: dict[str, Any] = {RB_SAME_NODE: {"hit": False, "nodes": []},
                             RB_RECALL_FAIL: {"hit": False, "nodes": []},
                             RB_EVICT_NO_INDEX: {"hit": False, "nodes": []}}
    # ② 回载失败（不设时间窗：只要发生过就成立）
    _fails = [str(e.get("node_id") or "") for e in (events or [])
              if isinstance(e, dict) and e.get("kind") == "recall_fail"]
    if _fails:
        _lines[RB_RECALL_FAIL] = {"hit": True, "nodes": _fails[:10], "count": len(_fails)}
    # ③ 驱逐未写索引
    _bad_ev = [str(e.get("node_id") or "") for e in (events or [])
               if isinstance(e, dict) and e.get("kind") == "evict"
               and e.get("cold_index_written") is not True]
    if _bad_ev:
        _lines[RB_EVICT_NO_INDEX] = {"hit": True, "nodes": _bad_ev[:10], "count": len(_bad_ev)}
    # ① 同节点窗口内「释放后回载」≥3
    _bucket: dict[str, list] = {}
    for _e in (events or []):
        if not isinstance(_e, dict):
            continue
        _ts = _e.get("ts")
        if not isinstance(_ts, (int, float)) or (_now - float(_ts)) > _win:
            continue
        _nid = str(_e.get("node_id") or "")
        if not _nid:
            continue
        _bucket.setdefault(_nid, []).append(str(_e.get("kind") or ""))
    _hot = []
    for _nid, _kinds in _bucket.items():
        if "release" in _kinds and _kinds.count("recall_ok") >= RB_SAME_NODE_MAX_RECALL:
            _hot.append(_nid)
    if _hot:
        _lines[RB_SAME_NODE] = {"hit": True, "nodes": _hot[:10]}

    if _lines[RB_RECALL_FAIL]["hit"]:
        _act, _why = ACT_SHADOW, "回滚线②：回载失败 %d 次 ⇒ 立即回 shadow" % (
            _lines[RB_RECALL_FAIL]["count"],)
    elif _lines[RB_EVICT_NO_INDEX]["hit"]:
        _act, _why = ACT_SHADOW, "回滚线③：驱逐未写冷索引 %d 次 ⇒ 不允许 live" % (
            _lines[RB_EVICT_NO_INDEX]["count"],)
    elif _lines[RB_SAME_NODE]["hit"]:
        _act, _why = ACT_PAUSE, "回滚线①：同节点 %.0fs 内释放-回载≥%d 次 ⇒ 暂停类别" % (
            _win, RB_SAME_NODE_MAX_RECALL)
    else:
        _act, _why = ACT_NONE, "三线均未命中"
    # 暂停类别取事件的 category（多类别时置空，由调用方按节点归属决定）
    _cats = sorted({str(e.get("category") or "") for e in (events or [])
                    if isinstance(e, dict) and str(e.get("node_id") or "") in
                    (_lines[RB_SAME_NODE].get("nodes") or [])})
    _cats = [c for c in _cats if c]
    return {"action": _act, "why": _why, "lines": _lines,
            "categories": _cats, "ts": _now}


def apply_rollback(res: dict, *, state_path: str | None = None,
                   now: float | None = None) -> dict:
    """把回滚判定落成动作并留痕。`force_shadow` 是**进程内**生效（不落盘）。"""
    _act = res.get("action") or ACT_NONE
    _out = {"action": _act, "applied": [], "persisted": None}
    if _act == ACT_NONE:
        return _out
    if _act == ACT_SHADOW:
        _module_logger.warning("[350开闸] 回滚 force_shadow 因=%s", res.get("why"))
        _out["applied"].append("force_shadow")
        return _out
    for _cat in (res.get("categories") or ["unknown"]):
        _r = pause_category(_cat, res.get("why") or "", now=now, state_path=state_path)
        _out["applied"].append("pause:%s" % _cat)
        _out["persisted"] = bool(_r.get("persisted"))
    return _out


# ---------------------------------------------------------------- 分档顺序锁
def assert_escalation_order(from_stage: str, to_stage: str) -> dict:
    """档位迁移合法性。**跳档即红**（`state="stage_skip_forbidden"`）。

    只允许：原地（noop）、前进 1 档、回退任意档（回退是安全动作，不拦）。
    """
    _f, _t = str(from_stage or STAGE_SHADOW), str(to_stage or STAGE_SHADOW)
    if _f not in STAGE_RANK or _t not in STAGE_RANK:
        return {"ok": False, "state": "bad_stage",
                "why": "未知档位 from=%r to=%r" % (_f, _t)}
    _df, _dt = STAGE_RANK[_f], STAGE_RANK[_t]
    if _df == _dt:
        return {"ok": True, "state": "noop", "why": ""}
    if _dt < _df:
        return {"ok": True, "state": "rollback", "why": "回退到 %s" % _t}
    if _dt - _df > 1:
        return {"ok": False, "state": "stage_skip_forbidden",
                "why": "跳档被拒：%s → %s（顺序 %s）" % (_f, _t, "→".join(ESCALATION_ORDER))}
    return {"ok": True, "state": "advance", "why": ""}


def hit_rate_ok_for(seconds: float, *, reload_stats: Any = None,
                    state: dict | None = None,
                    now: float | None = None) -> dict:
    """G2→G3 的门槛：**连续** `seconds` 秒命中率 ≥95%。

    `state["hit_ok_since_ts"]` 由本函数维护：达标且首次 ⇒ 记起点；不达标 ⇒ 清零
    （"连续"必须可中断，否则"曾经达标过一次"会被读成"连续七天达标"）。
    返回值 `persisted` 由调用方决定是否落盘。
    """
    _now = float(now if now is not None else time.time())
    _st = state if isinstance(state, dict) else {}
    _hr = (reload_stats or {}).get("hit_rate") if isinstance(reload_stats, dict) else None
    _ok = _hr is not None and float(_hr) >= HIT_RATE_LINE
    if not _ok:
        _st["hit_ok_since_ts"] = None
        return {"ok": False, "hit_rate": _hr, "since": None,
                "elapsed_sec": 0.0, "why": "当前不达标或不可判 ⇒ 连续计时清零"}
    _since = _st.get("hit_ok_since_ts")
    if not isinstance(_since, (int, float)):
        _since = _now
        _st["hit_ok_since_ts"] = _since
    _elapsed = _now - float(_since)
    return {"ok": _elapsed >= float(seconds), "hit_rate": _hr, "since": _since,
            "elapsed_sec": round(_elapsed, 1),
            "why": "" if _elapsed >= float(seconds)
                   else "连续达标仅 %.1fh（需 %.0fh）" % (_elapsed / 3600.0, float(seconds) / 3600.0)}


def advance_stage(target: str, *, state_path: str | None = None,
                  reload_stats: Any = None, now: float | None = None,
                  why: str = "") -> dict:
    """把档位推进到 `target`（含顺序校验 + G2→G3 的 7 天门槛 + 落盘 + 留痕）。"""
    _st = _load_state(state_path)
    _from = str(_st.get("stage") or STAGE_SHADOW)
    _chk = assert_escalation_order(_from, target)
    if not _chk["ok"]:
        _module_logger.error("[350开闸] 档位迁移被拒 %s", _chk["why"])
        return {"ok": False, "state": _chk["state"], "from": _from,
                "to": target, "why": _chk["why"], "persisted": False}
    if _chk["state"] == "noop":
        return {"ok": True, "state": "noop", "from": _from, "to": target,
                "why": "", "persisted": False}
    if _from == STAGE_G2 and target == STAGE_G3:
        _hold = hit_rate_ok_for(G2_HOLD_SEC, reload_stats=reload_stats,
                                state=_st, now=now)
        if not _hold["ok"]:
            _module_logger.warning(
                "[350开闸] G2→G3 被门槛拦：%s（命中率=%r）", _hold["why"], _hold["hit_rate"])
            return {"ok": False, "state": "hold_not_met", "from": _from, "to": target,
                    "why": _hold["why"], "persisted": False}
    _now = float(now if now is not None else time.time())
    _st["stage"] = target
    _hist = _st.get("history")
    if not isinstance(_hist, list):
        _hist = []
    _hist.append({"from": _from, "to": target, "at": _now,
                  "why": str(why or _chk["state"])[:200],
                  "proc_gen": _st.get("proc_gen") or ""})
    _st["history"] = _hist
    _saved = _save_state(_st, state_path)
    _module_logger.info("[350开闸] 档位 from=%s to=%s 依据=%s 落盘=%s",
                        _from, target, str(why or _chk["state"])[:120],
                        "是" if _saved else "**失败**")
    return {"ok": _saved, "state": "advanced", "from": _from, "to": target,
            "why": "", "persisted": _saved}


def stage_snapshot(state_path: str | None = None) -> dict:
    """只读快照（报告/心跳用）。读不到如实报 `read` 状态，不假装 shadow。"""
    _st = _load_state(state_path)
    return {"schema": _st.get("schema"), "stage": _st.get("stage"),
            "paused": sorted(_st.get("paused", {}).keys()),
            "g2_since_ts": _st.get("g2_since_ts"),
            "hit_ok_since_ts": _st.get("hit_ok_since_ts"),
            "transitions": len(_st.get("history") or []),
            "read": _st.get("_read"), "path": _st.get("_path"),
            "order": list(ESCALATION_ORDER), "live_enabled": live_enabled()}
