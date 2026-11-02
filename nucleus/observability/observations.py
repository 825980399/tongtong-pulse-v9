# -*- coding: utf-8 -*-
"""observations —— ★431 刀3：三条「动作细节」观测的 append-only 落盘。

为什么要有它
------------
现在只知道"曈曈做了什么动作"（`pulse_pairs` 里有 emit/receive，日志里有
`[411刀4] 经验命中 N 条`、`[353均衡] caller= picked= reason=`），但不知道
**动作的细节**：

  · 意图怎么来的 —— 三路信号（价值偏好 / 体验动机 / 状态）合成一句话，
    只在 `IntentGenerator._intent_history` 内存里留最近 100 条，重启即失；
  · 经验命中了哪几条、每条多少分 —— 只打成一行文本日志，`score` 靠肉眼抠；
  · 这次调用为什么选这条渠道 —— `[353均衡]` 那行有 picked/reason，但没有
    caller 侧的候选全貌，事后无法回答"当时其它渠道是什么状态"。

三条 jsonl，按天，append-only
----------------------------
```
data/observations/intent_evolution_YYYYMMDD.jsonl
data/observations/experience_hits_YYYYMMDD.jsonl
data/observations/channel_choices_YYYYMMDD.jsonl
```

三个硬约束（与 `dialogue_process_trace` 同口径）
------------------------------------------------
1. **永不抛异常**：每条记录都包在 `try` 里，失败只少一行，绝不影响主流程；
2. **有界**：文本字段截断到 `_MAX_TEXT`，列表截到 `_MAX_LIST`，
   防止一次大 prompt 就把当天文件撑成几十 MB；
3. **可关**：`config.ENABLE_M431_OBSERVATIONS=False` ⇒ 完全静默、不建目录。

为什么不做成 import 时初始化：生产启动期 `data/` 可能还在别的模块手里，
  按需建目录（首次写入时 `os.makedirs(exist_ok=True)`）最不容易打架。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("Observations")

#: 项目根（本文件在 nucleus/observability/ 下，上溯两级）
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 单字段文本上限（存判据，不存素材）
_MAX_TEXT = 500
#: 列表字段上限
_MAX_LIST = 12
#: 列表元素文本上限
_MAX_ITEM = 120

#: 三条观测的 kind → 文件名前缀
KINDS = {
    "intent_evolution": "intent_evolution",
    "experience_hits": "experience_hits",
    "channel_choices": "channel_choices",
}


def enabled() -> bool:
    """总开关 `config.ENABLE_M431_OBSERVATIONS`（缺省 True）。"""
    try:
        import config
        return bool(getattr(config, "ENABLE_M431_OBSERVATIONS", True))
    except Exception as _e:
        silent_exc(_e, where="observations::enabled", level="debug")
        return False


def _root() -> str:
    """数据根。测试用 `M431_OBS_ROOT` 显式注入（铁律 38：先改路径）。"""
    _env = os.environ.get("M431_OBS_ROOT")
    if _env:
        return os.path.abspath(os.path.normpath(_env))
    return _ROOT


def out_dir() -> str:
    return os.path.join(_root(), "data", "observations")


def out_path(kind: str, now: float | None = None) -> str:
    """当天某类观测的 jsonl 路径（按本地日期切分）。"""
    _p = KINDS.get(str(kind))
    if not _p:
        raise KeyError(f"unknown observation kind: {kind!r}")
    _t = time.localtime(float(now if now is not None else time.time()))
    return os.path.join(out_dir(), "{}_{}.jsonl".format(_p, time.strftime("%Y%m%d", _t)))


def _clip(value: Any, limit: int = _MAX_TEXT) -> str:
    """None → 空串（不写 "None"）；其余转 str 并截断。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception as _e:
            silent_exc(_e, where="observations::_clip str", level="debug")
            return ""
    return value if len(value) <= limit else value[:limit] + "…"


def _clip_list(items: Any, limit: int = _MAX_LIST) -> list:
    if not isinstance(items, (list, tuple)):
        return []
    _out = []
    for _x in list(items)[:limit]:
        if isinstance(_x, dict):
            # ★只截 str，数值/布尔/None 原样保留 —— 转成字符串会丢类型，
            #   下游做聚合要再 parse 一遍（431 实测：strength 落成 "0.82"）。
            _d = {}
            for _k2, _v2 in list(_x.items())[:8]:
                _d[str(_k2)] = (_clip(_v2, _MAX_ITEM) if isinstance(_v2, str)
                                else (_v2 if isinstance(_v2, (int, float, bool)) or _v2 is None
                                      else _clip(_v2, _MAX_ITEM)))
            _out.append(_d)
        elif isinstance(_x, (int, float, bool)) or _x is None:
            _out.append(_x)
        else:
            _out.append(_clip(_x, _MAX_ITEM))
    return _out


def _append(kind: str, payload: dict, now: float | None = None) -> str:
    """落一行。返回写入路径；未写（关闭/失败）返回空串。**永不抛异常。**"""
    if not enabled():
        return ""
    try:
        _t = float(now if now is not None else time.time())
        _path = out_path(kind, _t)
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        _rec = {"ts": round(_t, 3), "kind": kind}
        _rec.update(payload)
        with open(_path, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_rec, ensure_ascii=False, default=str))
            _f.write("\n")
        return _path
    except Exception as _e:
        silent_exc(_e, where=f"observations::_append:{kind}", level="warning")
        return ""


# ---------------------------------------------------------------- ① 意图进化

def record_intent_evolution(intent: Any, signals: Any = None,
                            now: float | None = None) -> str:
    """记一次意图生成：**输入三路信号 → 输出意图**。

    ★任务书要的是「prompt 和 response」。实测 `IntentGenerator` 这一层
      **没有 LLM 调用**（`_generate_intent_text` 是确定性模板 + 强度修饰），
      所以"prompt"的真实对应物是**三路输入信号**（价值偏好 / 体验动机 / 状态），
      "response"是合成出的 `intent_text` + `confidence`。照实记这个，不编 prompt。
    """
    try:
        _i = intent if isinstance(intent, dict) else {}
        _sig = signals if isinstance(signals, dict) else {}
        return _append("intent_evolution", {
            "intent_id": _clip(_i.get("intent_id"), 64),
            "intent_text": _clip(_i.get("intent_text")),
            "confidence": _i.get("confidence"),
            "is_autonomous": bool(_i.get("is_autonomous")),
            "source": _clip(_i.get("source")),
            # 三路输入信号（"prompt"）
            "signal_value": _clip_list([_sig.get("value")] if _sig.get("value") else []),
            "signal_experience": _clip_list([_sig.get("experience")] if _sig.get("experience") else []),
            "signal_state": _clip_list([_sig.get("state")] if _sig.get("state") else []),
        }, now=now)
    except Exception as _e:
        silent_exc(_e, where="observations::record_intent_evolution", level="warning")
        return ""


# ---------------------------------------------------------------- ② 经验命中

def record_experience_hits(question: str, hits: Any, candidates: int = 0,
                           extra: Any = None, now: float | None = None) -> str:
    """记一次经验检索：命中了哪几条、每条相似度多少。

    与 `experience_hits.observe()` 的分工：observe 负责"算 + 打一行日志"，
    本函数只负责"把明细落盘"。两者都在旁路，绝不参与命中判定。
    """
    try:
        _rows = []
        for _h in (hits or []) if isinstance(hits, (list, tuple)) else []:
            if not isinstance(_h, dict):
                continue
            _rows.append({
                "exp_id": _clip(_h.get("id") or _h.get("exp_id"), 48),
                "score": _h.get("score"),
                "is_template": bool(_h.get("is_template")),
                "text": _clip(_h.get("text"), _MAX_ITEM),
            })
        _p = {"question": _clip(question), "n": len(_rows),
              "candidates": int(candidates or 0), "hits": _rows[:_MAX_LIST]}
        if isinstance(extra, dict):
            for _k, _v in list(extra.items())[:6]:
                _p[str(_k)] = _clip(_v, _MAX_ITEM)
        return _append("experience_hits", _p, now=now)
    except Exception as _e:
        silent_exc(_e, where="observations::record_experience_hits", level="warning")
        return ""


# ---------------------------------------------------------------- ③ 渠道选择

def record_channel_choice(caller: str, picked: str, reason: str,
                          rows: Any = None, now: float | None = None) -> str:
    """记一次渠道选择：选了谁、为什么、当时其它候选是什么状态。

    `rows` 是 `channel_balance` 里的候选行（含 channel / priority / 额度 / 成功率）；
    只取前 `_MAX_LIST` 条、每行前 8 个字段，避免把渠道全量配置抄进日志。
    """
    try:
        _cand = []
        for _r in (rows or []) if isinstance(rows, (list, tuple)) else []:
            if isinstance(_r, dict):
                _cand.append({
                    "channel": _clip(_r.get("channel") or _r.get("name"), 64),
                    "priority": _r.get("priority"),
                    "available": _r.get("is_available") if "is_available" in _r
                    else _r.get("available"),
                    "remaining": _r.get("remaining") or _r.get("quota_remaining"),
                    "success_rate": _r.get("success_rate"),
                })
        return _append("channel_choices", {
            "caller": _clip(caller, 64),
            "picked": _clip(picked, 64),
            "reason": _clip(reason, _MAX_ITEM),
            "candidates_n": len(_cand),
            "candidates": _cand[:_MAX_LIST],
        }, now=now)
    except Exception as _e:
        silent_exc(_e, where="observations::record_channel_choice", level="warning")
        return ""


# ---------------------------------------------------------------- 自检

def stats(now: float | None = None) -> dict:
    """只读自检：三条观测各自的路径与行数（供运维/测试）。"""
    _out = {"enabled": enabled(), "dir": out_dir(), "kinds": {}}
    for _k in KINDS:
        _p = out_path(_k, now)
        _n = 0
        try:
            if os.path.exists(_p):
                with open(_p, "r", encoding="utf-8", errors="ignore") as _f:
                    for _ln in _f:
                        if _ln.strip():
                            _n += 1
        except Exception as _e:
            silent_exc(_e, where=f"observations::stats:{_k}", level="debug")
        _out["kinds"][_k] = {"path": _p, "lines": _n}
    return _out


def version() -> str:
    return "431.1"
