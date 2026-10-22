# -*- coding: utf-8 -*-
"""★345刀7：重启认知连续性 —— 从落盘事实恢复「回答上下文」。

**病根**（洞鉴第三季 #2 + 烛微345裁定：重启后回答自身状态时给出「首启」口吻）：
重启后上下文里**没有任何"我此前存在过"的事实**，模型只能按「无历史」推断，
于是给出「首启」口吻（该措辞的原句不在本文件出现，见下方"证据订正"）。

★**证据订正**（本批实测，铁律169：任务书点名结论也可被证据否决）：
烛微345 的前提是「该措辞在 `.py` 里 0 命中 ⇒ 非模板」。本批按活体树复核：
`grep -rn "该措辞" --include=*.py`（排除 `.git` / `tmp` / `.bak_*`）**≠ 0**，
至少两处**硬编码模板**会直出同款句子：
  · `nucleus/security/first_run_declaration.py` 的 `DECLARATION_TEXT`（首启声明文案）
  · `organs/identity/PulseSelfAwareness.py` 的「首次启动，没有历史主体感」分支
且 `organs/brain/PulseInnerWorld.py` 里另有一处**清洗正则**会把该片段从回答里剥掉。
⇒ 结论：**上下文缺失与模板直出两条路都成立**。本文件只按裁定补上下文注入，
   模板侧是否收口，属移交待裁项（不在本批擅动话术面）。

**处置口径**（烛微裁定：只补上下文注入、不动模板）：
  1. 收集三类事实（**只读落盘，不生成新事实**）：
       · 未结案议题 ← 345刀1 问题池快照（`status ∉ {verified, wontfix}`）
       · 愿景       ← 345刀5 成长记忆（`goal`）
       · 自我认知   ← `SelfAwarenessEngine.get_public_summary()`
  2. 渲染成一段"此前存在"事实块，前置进 prompt（与 345刀4 的时间块并列）。
  3. 留痕 `[345醒] 恢复上下文: <n>条 议题/愿景/自认` —— **条数可核**。

**不变量**（每条都可被下一个批次的 grep 复核）：
  · 不写任何 store、不改任何话术模板、不生成新事实（只读 + 渲染）；
  · 三源各自独立判定，互不掩盖：任一源不可读 ⇒ 该源记 `unavailable`，
    该段不出现（**不填 0、不编内容**，铁律156）；
  · `state` 三分（铁律166）：
      `ok`       —— 至少恢复出 1 条事实（"生效且有产出"）
      `empty`    —— 三源都可读但确实 0 条（"生效但产出 0"）
      `unavailable` —— 三源全部不可读（"没生效/读不到"，与 0 条不同罪）
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from nucleus.logger import get_module_logger

_module_logger = get_module_logger("nucleus.cognitive.restart_continuity")


#: 块 schema（下游按此判断"这段是重启恢复事实"而不是普通记忆）
SCHEMA = "restart_continuity/345.v1"

#: 单源取数上限（块要短：它是 prompt 前缀，不是日志）
MAX_ISSUES = 5
MAX_VISIONS = 3
MAX_SELF = 4

#: 单条文本截断（防一条长描述把整块撑爆）
_MAX_TEXT = 90

#: 议题的终态（与 345刀1 状态机一致；终态不算"未完成议题"）
_TERMINAL_STATUSES = ("verified", "wontfix")

#: 自我认知摘要里值得进 prompt 的键（按优先级；未列出的不进块）
_SELF_KEYS = (
    "health_level", "name", "identity", "role", "version",
    "organ_count", "capability_brief", "self_description",
)

#: 恢复事实缓存 TTL（秒）—— 只是"同一拍内不重复读盘"，不是"记忆内容会过期"
CACHE_TTL = 60.0
_CACHE: dict = {"ts": 0.0, "data": None}

# ---------------------------------------------------------------------------
# provider 注入（隔离验证用；生产不设，走默认实现）
# ---------------------------------------------------------------------------
_self_provider: Callable[[], Any] | None = None
_issue_provider: Callable[[], Any] | None = None
_vision_provider: Callable[[], Any] | None = None


def set_self_aware_provider(fn: Callable[[], Any] | None) -> None:
    """注入自我认知来源（测试用；`None` 恢复默认）。"""
    global _self_provider
    _self_provider = fn


def set_issue_provider(fn: Callable[[], Any] | None) -> None:
    """注入未结案议题来源（测试用；`None` 恢复默认）。"""
    global _issue_provider
    _issue_provider = fn


def set_vision_provider(fn: Callable[[], Any] | None) -> None:
    """注入愿景来源（测试用；`None` 恢复默认）。"""
    global _vision_provider
    _vision_provider = fn


def reset() -> None:
    """清空全部注入（每个用例前后调一次，避免互相污染）。"""
    global _self_provider, _issue_provider, _vision_provider
    _self_provider = None
    _issue_provider = None
    _vision_provider = None
    _CACHE["ts"], _CACHE["data"] = 0.0, None


def _clip(text: Any, n: int = _MAX_TEXT) -> str:
    _s = " ".join(str(text or "").split())
    return _s if len(_s) <= n else _s[:n] + "…"


def _proc_gen() -> str:
    try:
        from nucleus.proc_gen import proc_gen_str
        return proc_gen_str()
    except Exception as _e:  # noqa: BLE001 —— 取不到就如实记 unknown，绝不填 0
        _module_logger.debug("proc_gen 取不到: %s: %s", type(_e).__name__, _e)
        return "unknown"


# ---------------------------------------------------------------------------
# 三个默认取数口
# ---------------------------------------------------------------------------
def _default_issues(root: str | None = None,
                    audit_dir_path: str | None = None) -> dict:
    """未结案议题的**原始条目** ← 345刀1 问题池（不做筛选，筛选统一在 `_shape_issues`）。

    返回 `{state, items}`：`state` ∈ `ok|empty|unavailable`。

    ★为什么筛选要单独抽出去（而不是在这里就地做完）：
      若 provider 分支直接返回注入的 items、默认分支自己做过滤，
      隔离用例测的就只是"注入的值有没有被原样返回"，**生产那条过滤链一次都没跑**
      （177：判据"能跑"≠"跑对"）。所以两分支都只取原始条目，
      过滤/排序/截断只写一份，谁也别想绕过。
    """
    if _issue_provider is not None:
        try:
            _r = _issue_provider()
        except Exception as _e:  # noqa: BLE001
            _module_logger.debug("取数源不可用: %s: %s", type(_e).__name__, _e)
            return {"state": "unavailable", "items": [], "reason": type(_e).__name__}
        if isinstance(_r, dict):
            return {"state": str(_r.get("state") or "unavailable"),
                    "items": list(_r.get("items") or [])}
        return {"state": "ok" if _r else "empty", "items": list(_r or [])}
    try:
        from nucleus.audit_problem_pool import load_pool
        _res = load_pool(root=root, audit_dir_path=audit_dir_path)
    except Exception as _e:  # noqa: BLE001
        _module_logger.debug("取数源不可用: %s: %s", type(_e).__name__, _e)
        return {"state": "unavailable", "items": [], "reason": type(_e).__name__}
    if not isinstance(_res, dict) or _res.get("state") != "ok":
        return {"state": str((_res or {}).get("state") or "unavailable"),
                "items": []}
    return {"state": "ok", "items": list(_res.get("entries") or [])}


def _shape_issues(items: list) -> list[str]:
    """未结案议题的**统一加工**：去终态 → 按 last_seen_ts 倒序 → 截断 → 渲染成行。

    只此一份，生产与隔离走同一条（否则测的不是生产路径）。
    """
    _alive = [e for e in (items or [])
              if isinstance(e, dict)
              and str(e.get("status") or "") not in _TERMINAL_STATUSES]
    # 未结案议题按"最近见过"排序：刚发生的更该出现在上下文里
    _alive.sort(key=lambda e: float(e.get("last_seen_ts") or 0), reverse=True)
    _out: list[str] = []
    for _e in _alive[:MAX_ISSUES]:
        _t = str(_e.get("issue_type") or "unknown")
        _f = os.path.basename(str(_e.get("file") or "").replace("\\", "/"))
        _m = str(_e.get("method") or "")
        _s = str(_e.get("severity") or "")
        _out.append(_clip("%s %s::%s [%s]" % (_t, _f, _m, _s), 110))
    return _out


def _default_visions() -> dict:
    """愿景 ← 345刀5 成长记忆（goal 优先）。"""
    if _vision_provider is not None:
        try:
            _r = _vision_provider()
        except Exception as _e:  # noqa: BLE001
            _module_logger.debug("取数源不可用: %s: %s", type(_e).__name__, _e)
            return {"state": "unavailable", "items": [], "reason": type(_e).__name__}
        return {"state": "ok" if _r else "empty", "items": list(_r or [])}
    try:
        from nucleus.cognitive.growth_memory import read_growth_memory
        _m = read_growth_memory(limit=MAX_VISIONS)
    except Exception as _e:  # noqa: BLE001
        _module_logger.debug("取数源不可用: %s: %s", type(_e).__name__, _e)
        return {"state": "unavailable", "items": [], "reason": type(_e).__name__}
    if not isinstance(_m, dict):
        return {"state": "unavailable", "items": []}
    _state = str(_m.get("state") or "unavailable")
    if _state != "ok":
        return {"state": _state, "items": []}
    _out: list[str] = []
    for _k in ("goal", "achievement", "confusion"):
        for _rec in (_m.get(_k) or [])[:MAX_VISIONS]:
            _txt = _rec if isinstance(_rec, str) else (
                _rec.get("text") if isinstance(_rec, dict) else "")
            if str(_txt or "").strip():
                _out.append(_clip(_txt))
    return {"state": "ok", "items": _out[:MAX_VISIONS]}


def _default_self_aware() -> dict:
    """自我认知 ← SelfAwarenessEngine.get_public_summary()。"""
    if _self_provider is not None:
        try:
            _r = _self_provider()
        except Exception as _e:  # noqa: BLE001
            _module_logger.debug("取数源不可用: %s: %s", type(_e).__name__, _e)
            return {"state": "unavailable", "items": [], "reason": type(_e).__name__}
        return {"state": "ok" if _r else "empty", "items": list(_r or [])}
    try:
        from nucleus.self_awareness.SelfAwarenessEngine import (
            get_self_awareness_engine,
        )
        _sum = get_self_awareness_engine().get_public_summary()
    except Exception as _e:  # noqa: BLE001
        _module_logger.debug("取数源不可用: %s: %s", type(_e).__name__, _e)
        return {"state": "unavailable", "items": [], "reason": type(_e).__name__}
    if not isinstance(_sum, dict):
        return {"state": "unavailable", "items": []}
    _out: list[str] = []
    for _k in _SELF_KEYS:
        if _k in _sum and _sum[_k] not in (None, "", [], {}):
            _v = _sum[_k]
            if isinstance(_v, (list, tuple)):
                _v = "、".join(str(x) for x in list(_v)[:3])
            elif isinstance(_v, dict):
                continue
            _out.append("%s=%s" % (_k, _clip(_v)))
            if len(_out) >= MAX_SELF:
                break
    return {"state": "ok" if _out else "empty", "items": _out}


# ---------------------------------------------------------------------------
# 收集 + 渲染 + 注入
# ---------------------------------------------------------------------------
def collect_restart_context(root: str | None = None,
                            audit_dir_path: str | None = None,
                            now: float | None = None,
                            force: bool = False,
                            ttl: float = CACHE_TTL) -> dict:
    """收集三类恢复事实。返回结构**不含任何生成内容**，全部来自落盘读数。

    缓存（为什么必须有）：本函数挂在 `_build_memory_context`（10 处调用）上，
    每一轮对话都会走；而 `load_pool()` 要读 7 份 300KB 快照，
    `get_public_summary()` 也要算一遍 —— 不缓存等于把巡检成本搬进对话主链路。

    ★缓存的两条边界：
      · **注入了 provider 就不走缓存**（隔离验证每次都要读到自己刚造的数，
        缓存会让"改了数据但读数没变"被误判成"实现没生效"）；
      · `force=True` 强刷（重启恢复这类一次性场景用）。
    """
    _now = float(time.time() if now is None else now)
    _bypass = bool(force) or (_issue_provider is not None
                              or _vision_provider is not None
                              or _self_provider is not None)
    if not _bypass and _CACHE["data"] is not None and (_now - _CACHE["ts"]) < float(ttl):
        return dict(_CACHE["data"])

    _iss = _default_issues(root, audit_dir_path)
    _vis = _default_visions()
    _sel = _default_self_aware()

    _issue_lines: list[str] = _shape_issues(_iss["items"])

    # 愿景/自认同样在**统一处**截断（provider 分支也走这条 ⇒ 测的就是生产路径）
    _vision_lines = [_clip(x) for x in list(_vis["items"])[:MAX_VISIONS] if str(x or "").strip()]
    _self_lines = [_clip(x, 140) for x in list(_sel["items"])[:MAX_SELF] if str(x or "").strip()]

    _counts = {"issues": len(_issue_lines), "visions": len(_vision_lines),
               "self_aware": len(_self_lines)}
    _states = {"issues": _iss["state"], "visions": _vis["state"],
               "self_aware": _sel["state"]}
    _total = sum(_counts.values())
    if _total > 0:
        _state = "ok"
    elif all(v == "unavailable" for v in _states.values()):
        _state = "unavailable"
    else:
        _state = "empty"
    _out = {
        "schema": SCHEMA,
        "state": _state,
        "counts": _counts,
        "total": _total,
        "sources": _states,
        "issues": _issue_lines,
        "visions": _vision_lines,
        "self_aware": _self_lines,
        "proc_gen": _proc_gen(),
        "collected_at": _now,
    }
    if not _bypass:
        _CACHE["ts"], _CACHE["data"] = _now, dict(_out)
    return _out


def format_continuity_block(ctx: dict | None = None,
                            now_text: str = "") -> str:
    """把恢复事实渲染成 prompt 前缀块。无事实 ⇒ 空串（**不输出空壳**）。"""
    if not isinstance(ctx, dict) or int(ctx.get("total") or 0) <= 0:
        return ""
    _parts: list[str] = ["【此前存在的事实 · 重启恢复】"]
    if now_text:
        _parts.append("- 当前时间：%s" % now_text)
    if ctx.get("issues"):
        _parts.append("- 未结案议题 %d 条：%s"
                      % (len(ctx["issues"]), "；".join(ctx["issues"])))
    if ctx.get("visions"):
        _parts.append("- 最近在做的事：%s" % "；".join(ctx["visions"]))
    if ctx.get("self_aware"):
        _parts.append("- 自我认知：%s" % "；".join(ctx["self_aware"]))
    _parts.append("（以上均为本地落盘记录，描述的是我此前已经存在并做过的工作；"
                  "若被问及自身状态，应以这些记录为准，不要把它们当成推测。）")
    return "\n".join(_parts)


def note_restored(ctx: dict | None, trace: Callable[..., Any] | None = None) -> str:
    """留痕 `[345醒] 恢复上下文: <n>条 议题/愿景/自认`。返回该行文本。"""
    _c = (ctx or {}).get("counts") or {}
    _n = int((ctx or {}).get("total") or 0)
    _line = ("[345醒] 恢复上下文: %d条 议题%d/愿景%d/自认%d state=%s proc_gen=%s"
             % (_n, int(_c.get("issues") or 0), int(_c.get("visions") or 0),
                int(_c.get("self_aware") or 0), (ctx or {}).get("state"),
                (ctx or {}).get("proc_gen")))
    if callable(trace):
        try:
            trace("%s", _line)
        except Exception as _te:  # noqa: BLE001 —— 留痕失败不得影响主流程
            _module_logger.debug("留痕回调失败: %s: %s", type(_te).__name__, _te)
    return _line


def annotate_prompt(prompt: str, ctx: dict | None = None,
                    now_text: str = "",
                    trace: Callable[..., Any] | None = None) -> tuple[str, dict]:
    """把恢复块**前置**进 prompt，返回 `(新 prompt, info)`。

    `info` 字段：`state` / `total` / `injected`（是否真的改了 prompt）/
    `block_len` / `line`（留痕文本）。`injected=False` 时 prompt **原样返回**，
    绝不加空壳（"注入失败"与"注入成功但没内容"必须能区分，铁律166）。
    """
    _ctx = ctx if isinstance(ctx, dict) else collect_restart_context()
    _block = format_continuity_block(_ctx, now_text=now_text)
    _line = note_restored(_ctx, trace=trace)
    _info = {"state": _ctx.get("state"), "total": int(_ctx.get("total") or 0),
             "injected": bool(_block), "block_len": len(_block), "line": _line}
    if not _block:
        return prompt, _info
    _p = str(prompt or "")
    return (_block + "\n\n" + _p) if _p else _block, _info
