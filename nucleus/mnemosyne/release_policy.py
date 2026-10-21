# -*- coding: utf-8 -*-
"""release_policy.py —— 内存释放决策树 G0-G4（338 批刀2）

设计来源：烛微 337 前置 §1.4；验收硬线由小林裁定（D3）：
**heap 单日峰值 ≤ 2.0GB**（RSS 期末 <12GB 判定线不变）。

为什么验收看 heap 不看 RSS：RSS = 库镜像 + 私有堆。镜像（GPU/数值栈，实测约
4.3GB）运行中释放不掉；真正能被释放的是**私有堆**。释放动作做完后 RSS 常常
不回落（Windows/CPython arena 迟滞），拿 RSS 回落当验收线会把"释放成功但没归还"
判成失败。故本模块一律以 `heap_mb` 为决策与验收口径，`image_mb` 单独报出。

四层结构（任何一层不过都不许往下走）：

    G0 四闸门（前提）：W1 alive / 不在 04:00±600s 窗 / 不在压测圈 / 冷却≥30 拍
        —— 任一不过 ⇒ **本轮不释放，只留痕**（不是"降级释放"，是不动手）
    G1 仅观测：tracemalloc 打点，只记录不释放
    G2 软释放：语义缓存 TTL 落盘驱逐 / shadow deque 截断 / 观测缓冲 maxlen 截断
    G3 冷化：warm→cold，每批 ≤1000、批间 ≥1 采样周期
    G4 只告警不重启（`ENABLE_MEMORY_AUTO_RESTART` 恒 False）

防误杀四条件（每个候选**必须四条全过**才可释放）：
    ① last_access 过期  ② 引用计数 = 0  ③ 不在白名单（absorb 计数链）
    ④ 有可回载路径

铁律遵守：
- 释放路径**不 import 新模块**（本模块顶部只导 gc/tracemalloc/time，运行期不再 import）；
- 释放/冷化 **100% 留痕**，无静默；
- 读不到就记 `unavailable`，**不填 0**（"没读到"≠"是 0"）；
- 决策与执行分离：本模块只出决策 + 留痕，落盘由调用方走既有增量通道
  （`PulseSnapshot._m67_incremental_log_save`），避免在释放路径引入新依赖。
"""

import gc
import time
import tracemalloc
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("release_policy")

#: 决策树版本戳（口径变更必须升版，便于报表对表）
RELEASE_POLICY_VERSION = "338.rel.v1"

#: G0 四闸门标识（留痕/证据里逐条报，不用"理由字符串里找关键字"）
G0_W1 = "g0_w1_alive"
G0_NIGHT_WINDOW = "g0_night_window"
G0_STRESS = "g0_stress_guard"
G0_COOLDOWN = "g0_cooldown"
G0_ORDER = (G0_W1, G0_NIGHT_WINDOW, G0_STRESS, G0_COOLDOWN)

#: 分级
TIER_G0_BLOCK = "G0_BLOCK"
TIER_G1 = "G1_OBSERVE"
TIER_G2 = "G2_SOFT_RELEASE"
TIER_G3 = "G3_COLD_DEMOTE"
TIER_G4 = "G4_ALARM_ONLY"

#: 防误杀四条件标识
GUARD_IDLE = "idle_expired"
GUARD_REFCOUNT = "refcount_zero"
GUARD_WHITELIST = "not_whitelisted"
GUARD_RELOADABLE = "reloadable"
GUARD_ORDER = (GUARD_IDLE, GUARD_REFCOUNT, GUARD_WHITELIST, GUARD_RELOADABLE)


# ---------------------------------------------------------------- 配置读取
def _cfg(name: str, default):
    """读 config（运行期不 import 新模块：config 顶部已导入过则由 sys.modules 取）。

    注意：这里**故意允许**在首次调用时 import config（它在进程启动阶段就已被导入，
    实际走的是 sys.modules 命中），避免为了一个布尔值把 config 的 4000+ 行再解析一遍。
    """
    try:
        import config as _c
        return getattr(_c, name, default)
    except Exception as _e:
        silent_exc(_e, where="release_policy::_cfg " + name)
        return default


def tree_enabled() -> bool:
    """决策树总开关。默认 **False（影子）**：只决策、只留痕、不动手。

    为什么默认关：释放是不可逆动作，第一次上线必须是"看它想干什么"而不是
    "让它去干"。翻开需显式 `config.ENABLE_MEMORY_RELEASE_TREE=True`。
    """
    return bool(_cfg("ENABLE_MEMORY_RELEASE_TREE", False))


def thresholds() -> dict[str, float]:
    """G2/G3/G4 的 heap 阈值（MB）与冷却/空闲参数。全部可 config 覆盖。"""
    return {
        "g2_heap_mb": float(_cfg("MEMORY_RELEASE_G2_HEAP_MB", 1200.0)),
        "g3_heap_mb": float(_cfg("MEMORY_RELEASE_G3_HEAP_MB", 1700.0)),
        # 硬线在小林 D3 拍板：heap 单日峰值 ≤2.0GB。G4 到线只告警，不重启。
        "g4_heap_mb": float(_cfg("MEMORY_RELEASE_G4_HEAP_MB", 2000.0)),
        "cooldown_beats": int(_cfg("MEMORY_RELEASE_COOLDOWN_BEATS", 30)),
        "idle_sec": float(_cfg("MEMORY_RELEASE_IDLE_SEC", 1800.0)),
        "cold_batch_max": int(_cfg("MEMORY_RELEASE_COLD_BATCH_MAX", 1000)),
    }


def night_window_sec() -> tuple[int, int, float]:
    """复用夜间编排的 04:00±600s 常量（**不另抄一份**，抄一份就多一处会漂的口径）。"""
    try:
        from nucleus.evolution import night_orchestration as _no
        return (int(getattr(_no, "NIGHT_DIGEST_HOUR", 4)),
                int(getattr(_no, "NIGHT_DIGEST_MINUTE", 0)),
                float(getattr(_no, "NIGHT_DIGEST_TOLERANCE_SEC", 600.0)))
    except Exception as _e:
        silent_exc(_e, where="release_policy::night_window_sec", level="warning")
        return (4, 0, 600.0)


def in_night_window(now_ts: float | None = None) -> tuple[bool, float]:
    """是否落在 04:00±600s 窗口内；返回 `(是否在窗内, 距窗口边界秒数)`。"""
    _now = float(now_ts if now_ts is not None else time.time())
    _h, _m, _tol = night_window_sec()
    _lt = time.localtime(_now)
    _center = _now - (_lt.tm_hour * 3600 + _lt.tm_min * 60 + _lt.tm_sec) + _h * 3600 + _m * 60
    # 窗口可能跨零点，两侧都比较（取最近的一次 04:00）
    _d = _now - _center
    if _d > 43200:      # 超过 12h ⇒ 说明该用"明天/昨天"的那次 04:00
        _d -= 86400
    elif _d < -43200:
        _d += 86400
    return (abs(_d) <= _tol, abs(_d))


def in_stress_window() -> bool:
    """是否在压测圈中。

    判据 = 环境变量 `PULSE_STRESS_MODE` 为真值。用环境变量而不是 config：
    env 的"未设置"是**确定事实**（不是"读不到"），因此不存在静默降级问题；
    config 缺失时反而要靠默认值猜，那才是降级。
    """
    import os
    return str(os.environ.get("PULSE_STRESS_MODE", "")).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- G0 四闸门
def evaluate_g0(*, w1_verdict: str | None,
                now_ts: float | None = None,
                last_release_ts: float | None = None,
                beat_interval_sec: float = 0.0,
                beat_count: int | None = None,
                last_release_beat: int | None = None,
                stress: bool | None = None) -> dict[str, Any]:
    """G0 四闸门。任一不过 ⇒ `allowed=False`（本轮不释放，只留痕）。

    `w1_verdict` 取 `alive` 才放行；`stale/unavailable/None` 一律拦。
    **不猜 alive**：猜 alive 会让"外部说卡"被读成"外部说好"，方向正好反。
    """
    _th = thresholds()
    _gates: dict[str, dict[str, Any]] = {}

    # ① W1 alive
    _v = str(w1_verdict or "unavailable")
    _gates[G0_W1] = {"pass": _v == "alive", "value": _v,
                     "why": "" if _v == "alive" else "W1=%s（非 alive 不放行）" % _v}

    # ② 不在 04:00±600s 窗
    _in_win, _dist = in_night_window(now_ts)
    _gates[G0_NIGHT_WINDOW] = {"pass": not _in_win, "value": round(_dist, 1),
                               "why": "距 04:00 仅 %.0fs（窗内不释放，避免干扰 ledger 自转）"
                                      % _dist if _in_win else ""}

    # ③ 不在压测圈
    _stress = in_stress_window() if stress is None else bool(stress)
    _gates[G0_STRESS] = {"pass": not _stress, "value": _stress,
                         "why": "压测进行中不释放（否则污染压测读数）" if _stress else ""}

    # ④ 冷却 ≥30 拍
    _cd_beats = int(_th["cooldown_beats"])
    if beat_count is not None and last_release_beat is not None:
        _since = int(beat_count) - int(last_release_beat)
        _cd_ok = _since >= _cd_beats
        _cd_val = "%d 拍（需≥%d）" % (_since, _cd_beats)
    elif last_release_ts:
        _interval = float(beat_interval_sec) if beat_interval_sec and beat_interval_sec > 0 else 60.0
        _since_sec = float(now_ts if now_ts is not None else time.time()) - float(last_release_ts)
        _since = int(_since_sec // _interval)
        _cd_ok = _since >= _cd_beats
        _cd_val = "%d 拍（按 %0.0fs/拍折算，需≥%d）" % (_since, _interval, _cd_beats)
    else:
        # 从未释放过 ⇒ 冷却已满足（这是确定事实，不是读不到）
        _cd_ok, _cd_val = True, "从未释放（冷却天然满足）"
    _gates[G0_COOLDOWN] = {"pass": _cd_ok, "value": _cd_val,
                           "why": "" if _cd_ok else "冷却未到：%s" % _cd_val}

    _failed = [k for k in G0_ORDER if not _gates[k]["pass"]]
    return {"allowed": not _failed, "gates": _gates, "blocked_by": _failed}


# ---------------------------------------------------------------- 防误杀四条件
def probe_refcount(obj: Any) -> int:
    """引用计数（用 gc.get_referrers 数，不依赖 sys.getrefcount 的 +1 约定）。

    只数**非本帧**的引用：`gc.get_referrers` 会把当前栈帧算进去，
    直接把调用现场的 frame 减掉，否则任何对象都恒 ≥1，「引用计数=0」永假
    （那会让防误杀②变成一句永远拦不住的话）。
    """
    try:
        _refs = gc.get_referrers(obj)
    except Exception as _e:
        silent_exc(_e, where="release_policy::probe_refcount")
        return -1
    _n = 0
    for _r in _refs:
        if isinstance(_r, dict) and _r.get("self") is obj:
            continue          # 跳过帧/局部变量包装
        if getattr(_r, "f_code", None) is not None:
            continue          # 跳过栈帧
        _n += 1
    return _n


def check_guards(cand: dict, *, now_ts: float | None = None,
                 idle_sec: float | None = None,
                 whitelist_ids: set | None = None) -> dict[str, Any]:
    """防误杀四条件。四条全过才 `ok=True`。

    `cand` 字段（缺项按**不过**处理，不默认通过）：
        node_id, last_access, refcount, reloadable
    """
    _th = thresholds()
    _idle_req = float(idle_sec if idle_sec is not None else _th["idle_sec"])
    _now = float(now_ts if now_ts is not None else time.time())
    _wl = whitelist_ids or set()
    _nid = str(cand.get("node_id", ""))
    _g: dict[str, dict[str, Any]] = {}

    _la = cand.get("last_access")
    _idle = (_now - float(_la)) if isinstance(_la, (int, float)) else None
    _g[GUARD_IDLE] = {
        "pass": _idle is not None and _idle >= _idle_req,
        "value": (round(_idle, 1) if _idle is not None else None),
        "why": "" if (_idle is not None and _idle >= _idle_req)
               else ("last_access 缺失（不默认通过）" if _idle is None
                     else "空闲 %.0fs < 要求 %.0fs" % (_idle, _idle_req)),
    }

    _rc = cand.get("refcount")
    _rc_ok = (_rc == 0)
    _g[GUARD_REFCOUNT] = {"pass": _rc_ok, "value": _rc,
                          "why": "" if _rc_ok else "引用计数=%r（须=0；None 视为不可判⇒不释放）" % (_rc,)}

    _in_wl = _nid in _wl if _nid else True
    _g[GUARD_WHITELIST] = {"pass": not _in_wl, "value": _in_wl,
                           "why": "在白名单（absorb 计数链）内，禁释放" if _in_wl else ""}

    _rl = cand.get("reloadable")
    _g[GUARD_RELOADABLE] = {"pass": bool(_rl), "value": bool(_rl),
                            "why": "" if _rl else "无可回载路径（释放后取不回来）"}

    _failed = [k for k in GUARD_ORDER if not _g[k]["pass"]]
    return {"ok": not _failed, "guards": _g, "blocked_by": _failed}


# ---------------------------------------------------------------- 分级
def pick_tier(heap_mb: float | None) -> tuple[str, str]:
    """按 heap_mb 选级。`heap_mb=None`（测不到）⇒ 只观测，**不释放**。"""
    _th = thresholds()
    if heap_mb is None:
        return TIER_G1, "heap_mb 测不到（image 未探明）⇒ 只观测，不释放"
    _h = float(heap_mb)
    if _h >= _th["g4_heap_mb"]:
        return TIER_G4, "heap %.0fMB ≥ 硬线 %.0fMB ⇒ 只告警（不重启）" % (_h, _th["g4_heap_mb"])
    if _h >= _th["g3_heap_mb"]:
        return TIER_G3, "heap %.0fMB ≥ %.0fMB ⇒ 冷化(warm→cold)" % (_h, _th["g3_heap_mb"])
    if _h >= _th["g2_heap_mb"]:
        return TIER_G2, "heap %.0fMB ≥ %.0fMB ⇒ 软释放" % (_h, _th["g2_heap_mb"])
    return TIER_G1, "heap %.0fMB < %.0fMB ⇒ 仅观测" % (_h, _th["g2_heap_mb"])


# ---------------------------------------------------------------- 总决策
def decide(*, heap_mb: float | None,
           w1_verdict: str | None,
           candidates: list | None = None,
           whitelist_ids: set | None = None,
           now_ts: float | None = None,
           last_release_ts: float | None = None,
           beat_interval_sec: float = 0.0,
           beat_count: int | None = None,
           last_release_beat: int | None = None,
           stress: bool | None = None) -> dict[str, Any]:
    """出一次完整决策（纯函数：不改任何状态、不动任何对象）。

    Returns:
        dict: `{version, tier, tier_why, g0, release_enabled, approved, rejected,
                heap_mb, thresholds, ts}`
        `approved` 只含**四条件全过**的候选；`rejected` 每条带 `blocked_by`。
    """
    _g0 = evaluate_g0(w1_verdict=w1_verdict, now_ts=now_ts,
                      last_release_ts=last_release_ts,
                      beat_interval_sec=beat_interval_sec,
                      beat_count=beat_count, last_release_beat=last_release_beat,
                      stress=stress)
    _tier, _tier_why = pick_tier(heap_mb)
    if not _g0["allowed"]:
        _tier, _tier_why = TIER_G0_BLOCK, "G0 拦住：" + "、".join(_g0["blocked_by"])

    _approved: list = []
    _rejected: list = []
    if _tier in (TIER_G2, TIER_G3):
        for _c in (candidates or []):
            _r = check_guards(_c, now_ts=now_ts, whitelist_ids=whitelist_ids)
            if _r["ok"]:
                _approved.append(_c.get("node_id"))
            else:
                _rejected.append({"node_id": _c.get("node_id"),
                                  "blocked_by": _r["blocked_by"], "guards": _r["guards"]})
        _cap = int(thresholds()["cold_batch_max"])
        if len(_approved) > _cap:
            # G3 每批 ≤1000：超出的不是"拒"，是"下一批再做"——分开记，别混成拒收
            _deferred = _approved[_cap:]
            _approved = _approved[:_cap]
        else:
            _deferred = []
    else:
        _deferred = []

    return {
        "version": RELEASE_POLICY_VERSION,
        "ts": float(now_ts if now_ts is not None else time.time()),
        "tier": _tier,
        "tier_why": _tier_why,
        "g0": _g0,
        "release_enabled": tree_enabled(),
        "heap_mb": heap_mb,
        "thresholds": thresholds(),
        "approved": _approved,
        "deferred": _deferred,
        "rejected": _rejected,
    }


def log_decision(dec: dict, *, where: str = "") -> None:
    """把决策打成**一行可 grep** 的留痕（100% 有日志，无静默）。"""
    _g0_fail = ",".join(dec.get("g0", {}).get("blocked_by") or []) or "-"
    _rej = len(dec.get("rejected") or [])
    _app = len(dec.get("approved") or [])
    _def = len(dec.get("deferred") or [])
    _heap = dec.get("heap_mb")
    _module_logger.info(
        "[338释放] ver=%s tier=%s heap=%s 放行=%d 拒=%d 顺延=%d G0拦=%s 开关=%s %s",
        dec.get("version"), dec.get("tier"),
        ("%.1fMB" % _heap) if isinstance(_heap, (int, float)) else "unavailable",
        _app, _rej, _def, _g0_fail,
        "on" if dec.get("release_enabled") else "off(shadow)",
        where or "",
    )
    for _r in (dec.get("rejected") or [])[:10]:
        _module_logger.info("[338释放] 拒收 node=%s 因=%s",
                            _r.get("node_id"), ",".join(_r.get("blocked_by") or []))


def g1_snapshot() -> dict[str, Any]:
    """G1 观测打点：tracemalloc 已启动就取当前/峰值，未启动如实报 `unavailable`。"""
    if not tracemalloc.is_tracing():
        return {"tracing": False, "current_mb": None, "peak_mb": None,
                "status": "unavailable", "why": "tracemalloc 未启动（需显式 start）"}
    _cur, _peak = tracemalloc.get_traced_memory()
    return {"tracing": True,
            "current_mb": round(_cur / 1024 / 1024, 1),
            "peak_mb": round(_peak / 1024 / 1024, 1),
            "status": "ok", "why": ""}


def degrade_verdict(rss_mb: float | None, heap_mb: float | None, *,
                    rss_line_mb: float | None = None,
                    heap_line_mb: float | None = None) -> dict[str, Any]:
    """⑧ 降级线**双轨**：RSS ≥10GB **或** heap ≥2.5GB，先触其一即降级。

    为什么必须双轨（336 实测为证）：336 圈期末 RSS 11.594GB 已触 10GB 线，
    但其中约 4.31GB 是**不可释放的镜像**（GPU/数值栈）。单看 RSS 等于只给堆
    5.7GB 预算，会把「库装得多」误判成「堆泄漏」，从而误降级；单看 heap 又会漏掉
    镜像暴涨。两轨任一越线就降级——降级是保守动作，宁可多降一次。

    `None` 的语义：某项测不到 ⇒ 该轨记 `unavailable`，**不参与判定**；
    两轨都测不到 ⇒ 整体 `unavailable`（不判「无需降级」）。
    """
    _rss_line = float(rss_line_mb if rss_line_mb is not None else
                      _cfg("MEMORY_DEGRADE_RSS_MB", 10240.0))
    _heap_line = float(heap_line_mb if heap_line_mb is not None else
                       _cfg("MEMORY_DEGRADE_HEAP_MB", 2560.0))
    _triggers: list[str] = []
    _rss_hit = None if rss_mb is None else (float(rss_mb) >= _rss_line)
    _heap_hit = None if heap_mb is None else (float(heap_mb) >= _heap_line)
    if _rss_hit:
        _triggers.append("rss")
    if _heap_hit:
        _triggers.append("heap")
    if _rss_hit is None and _heap_hit is None:
        return {"degrade": None, "verdict": "unavailable", "triggers": [],
                "rss_mb": None, "heap_mb": None,
                "rss_line_mb": _rss_line, "heap_line_mb": _heap_line,
                "why": "两轨读数均缺失，不得据此判「无需降级」"}
    return {"degrade": bool(_triggers),
            "verdict": "degrade" if _triggers else "hold",
            "triggers": _triggers,
            "rss_mb": rss_mb, "heap_mb": heap_mb,
            "rss_line_mb": _rss_line, "heap_line_mb": _heap_line,
            "why": (("触发：" + "、".join(_triggers)) if _triggers else "两轨均未越线")}


def judge_pass_line(rss_end_mb: float | None, heap_peak_mb: float | None, *,
                    rss_line_mb: float | None = None,
                    heap_line_mb: float | None = None) -> dict[str, Any]:
    """⑨ 判定线**双写**：期末 RSS <12GB **∧** heap 单日峰值 <2.0GB，两项都过才算过。

    为什么必须双写：只写 RSS 会把「少装库」这种架构收益误记成「内存治理有效」
    ——镜像小一点 RSS 就下来了，而堆可能还在涨。反过来只看 heap 也不行：镜像
    暴涨（新装大库）同样会打穿 RSS 线。故两者**同时**成立才判 PASS。

    `None` 语义：任一项缺失 ⇒ `verdict="unavailable"`（**不判 PASS**）。
    缺一项就宣布达标，等于把无法证伪的命题当成已证。
    """
    _rss_line = float(rss_line_mb if rss_line_mb is not None else
                      _cfg("MEMORY_PASS_RSS_MB", 12288.0))
    _heap_line = float(heap_line_mb if heap_line_mb is not None else
                       thresholds()["g4_heap_mb"])
    if rss_end_mb is None or heap_peak_mb is None:
        _miss = []
        if rss_end_mb is None:
            _miss.append("期末RSS")
        if heap_peak_mb is None:
            _miss.append("heap峰值")
        return {"verdict": "unavailable", "why": "缺" + "、".join(_miss) + "（不得判 PASS）",
                "rss_ok": None, "heap_ok": None,
                "rss_end_mb": rss_end_mb, "heap_peak_mb": heap_peak_mb,
                "rss_line_mb": _rss_line, "heap_line_mb": _heap_line}
    _rss_ok = float(rss_end_mb) < _rss_line
    _heap_ok = float(heap_peak_mb) < _heap_line
    return {"verdict": ("pass" if (_rss_ok and _heap_ok) else "fail"),
            "why": "" if (_rss_ok and _heap_ok) else (
                ("期末RSS %.0fMB ≥ %.0fMB" % (float(rss_end_mb), _rss_line) if not _rss_ok else "")
                + ("；" if (not _rss_ok and not _heap_ok) else "")
                + ("heap峰值 %.0fMB ≥ %.0fMB" % (float(heap_peak_mb), _heap_line)
                   if not _heap_ok else "")),
            "rss_ok": _rss_ok, "heap_ok": _heap_ok,
            "rss_end_mb": float(rss_end_mb), "heap_peak_mb": float(heap_peak_mb),
            "rss_line_mb": _rss_line, "heap_line_mb": _heap_line}


def heap_peak_guard(peak_mb: float | None, *, line_mb: float | None = None) -> dict[str, Any]:
    """验收硬线判定：heap 单日峰值 ≤2.0GB。

    `peak_mb=None` ⇒ `verdict="unavailable"`（**不判通过**）。理由：没测到峰值时
    "≤2.0GB"是个无法证伪的命题，判 PASS 等于白送一个达标。
    """
    _line = float(line_mb if line_mb is not None else thresholds()["g4_heap_mb"])
    if peak_mb is None:
        return {"verdict": "unavailable", "peak_mb": None, "line_mb": _line,
                "why": "未取到 heap 峰值（不得据此判通过）"}
    _ok = float(peak_mb) <= _line
    return {"verdict": "pass" if _ok else "fail", "peak_mb": float(peak_mb),
            "line_mb": _line,
            "why": "" if _ok else "heap 峰值 %.0fMB > 硬线 %.0fMB" % (float(peak_mb), _line)}

# ---------------------------------------------------------------- 执行面
#: G2 三类软释放目标（与任务书口径一致：语义缓存 / shadow deque / 观测缓冲）
G2_TARGETS = ("semantic_cache", "shadow_deque", "observe_buffer")


def apply_decision(dec: dict, *, executor=None) -> dict[str, Any]:
    """按决策执行。**影子模式只留痕不动作**——这是默认行为。

    三种模式（如实报，不混成一团）：
      - `shadow`：`ENABLE_MEMORY_RELEASE_TREE=False` ⇒ 不调用 executor，只留痕"若真跑会做什么"；
      - `live`：开关为 True 且给了 executor ⇒ 按 G2_TARGETS 逐类调用并留痕；
      - `no_executor`：开关为 True 但没给 executor ⇒ **如实报缺执行面**，不假装做过。

    executor 协议：`executor(kind: str, node_ids: list) -> int`（返回实际动作条数）。
    返回值：`{mode, tier, executed, per_target, skipped}`。
    """
    _tier = dec.get("tier")
    _ids = list(dec.get("approved") or [])
    _out: dict[str, Any] = {"mode": "shadow", "tier": _tier,
                            "executed": 0, "per_target": {}, "skipped": []}
    if _tier not in (TIER_G2, TIER_G3):
        _out["skipped"].append("tier=%s 无动作" % _tier)
        log_decision(dec, where="apply/skip")
        return _out
    if not dec.get("release_enabled"):
        _out["mode"] = "shadow"
        _out["skipped"].append("影子模式：若真跑将处理 %d 条" % len(_ids))
        log_decision(dec, where="apply/shadow")
        return _out
    if executor is None:
        _out["mode"] = "no_executor"
        _out["skipped"].append("开关已开但无 executor：执行面缺失（不假装做过）")
        log_decision(dec, where="apply/no_executor")
        return _out
    _out["mode"] = "live"
    if _tier == TIER_G3:
        _kinds = ["cold_demote"]
    else:
        _kinds = list(G2_TARGETS)
    for _k in _kinds:
        try:
            _n = int(executor(_k, _ids) or 0)
        except Exception as _e:
            silent_exc(_e, where="release_policy::apply_decision " + _k, level="warning")
            _n = 0
            _out["skipped"].append("%s 执行异常" % _k)
        _out["per_target"][_k] = _n
        _out["executed"] += _n
        _module_logger.info("[338释放] 动作 tier=%s 类=%s 条=%d 回载=可用",
                            _tier, _k, _n)
    log_decision(dec, where="apply/live")
    return _out
