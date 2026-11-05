# -*- coding: utf-8 -*-
"""★455 刀2/3：hypothesize —— 独立的「假设」LLM 通道（本批 shadow，只生成不进答案）。

为什么不复用 ``PulseInnerWorld._generate_branch_with_model``（烛微 455 前置 H1/H2/H3）
--------------------------------------------------------------------------------
* 那条通道是 **45s 裸 HTTP**（``timeout_by_purpose["inner_world_chat"]``），
  而 453R 的请求总预算只有 34s ⇒ 一次假设就能顶穿整轮回答；
* 它把结果写进 **1 小时 TTL 的 ``_model_cache``**（键 = 核心词 + 分支名），
  两个不同问题只要核心词重叠就会互相串味，且证据变了也不会刷新；
* 它的成功判据是 **任意 ≥15 字文本** —— 分不清「假设」还是「事实」，也没有置信度。

本模块的四条纪律
----------------
1. **自己控超时**：``timeout = min(8s, deadline - now - 10s)``；剩余 <12s 直接
   ``skipped_no_budget``，绝不占用留给最终回答的时间；
2. **不复用 ``_model_cache``**：不读不写，也不做跨请求缓存（避免串味）；
3. **严格 JSON 契约**：解析失败 ⇒ ``parse_error``，**绝不把原文当结论塞回调用方**；
4. **请求级 LLM 次数**（刀4）：由 ``nucleus.evidence_injection.reserve_llm_op()`` 发放
   「假设券」，一个请求最多一次；重试复用同一计数，不清零。

调用方（本批只有一个）
----------------------
``PulseInnerWorld._multi_step_execute_v2`` 的 shadow 分支：把结果写进
``dialogue_process_trace`` 的 ``step_results``，**不拼进最终答案**。
"""
from __future__ import annotations

import contextvars
import json
import re
import time
from collections.abc import Callable
from typing import Any

from nucleus._silent_except import silent_exc

#: 状态枚举（★``skipped_origin`` / ``no_endpoint`` 是本批新增的「没跑」细分，
#:  任务书只给了 5 个值；多出来的两个都属「未执行」，调用方按 falsy 处理即可）
STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_TIMEOUT = "timeout"
STATUS_PARSE_ERROR = "parse_error"
STATUS_NO_BUDGET = "skipped_no_budget"
STATUS_SKIPPED_ORIGIN = "skipped_origin"
STATUS_NO_ENDPOINT = "no_endpoint"

#: 默认参数（全部可被 config 覆盖，见 config.py 的 [M455-CFG] 段）
_DEF_MAX_SEC = 8.0
_DEF_RESERVE_SEC = 10.0
_DEF_MIN_START_SEC = 12.0
_DEF_TOTAL_SEC = 34.0
_DEF_MAX_TOKENS = 320
_DEF_TEMPERATURE = 0.4

#: 非用户来源一律不做假设（烛微 H5：内部/进化任务不该付这笔钱）
_NON_USER_ORIGINS = ("evolution_task", "internal", "system_internal",
                     "background_learning", "replay")


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception as _e:
        silent_exc(_e, where="hypothesize::_cfg " + name, level="debug")
        return default


def _cfg_num(name: str, default: float) -> float:
    """★450 批教训：``getattr(cfg, X, d) or d`` 会把 0 当缺省 —— 只有 None 才算。"""
    v = _cfg(name, None)
    if v is None:
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="hypothesize::_cfg_num " + name, level="debug")
        return float(default)


def enabled() -> bool:
    """shadow 开关（默认 True）：关闭时调用方整块不进入。"""
    return bool(_cfg("ENABLE_M455_HYPOTHESIZE_SHADOW", True))


def in_answer_enabled() -> bool:
    """进答案开关（默认 False）。★本批**未实现**进答案路径，只作留痕。"""
    return bool(_cfg("ENABLE_M455_HYPOTHESIZE", False))


def max_llm_ops() -> int:
    v = _cfg_num("M455_MAX_LLM_OPS", 1.0)
    return max(0, int(v))


# ---------------------------------------------------------------- 请求级 deadline
#: ★为什么单独放一个 ContextVar：刀5 的执行点在 ``_multi_step_execute_v2``，
#:  它发生在「肺」之前，拿不到 453R 的 Budget（烛微 H1）。计时起点由
#:  ``PulseInnerWorld`` 在推理上下文创建时写入（= Cortex 收到问题那一刻）。
_DEADLINE: contextvars.ContextVar = contextvars.ContextVar("m455_deadline", default=None)


def begin_request(start_ts: float | None = None, total: float | None = None) -> float:
    """登记本请求的 deadline（epoch 秒）；返回登记值。"""
    _t = _cfg_num("M455_TOTAL_BUDGET_SEC", _DEF_TOTAL_SEC) if total is None else float(total)
    _s = float(start_ts if start_ts is not None else time.time())
    _d = _s + max(1.0, _t)
    _DEADLINE.set(_d)
    return _d


def request_deadline() -> float:
    """当前请求的 deadline；未登记时按「此刻开始 + 总预算」保守取值。"""
    _d = _DEADLINE.get()
    if isinstance(_d, (int, float)) and float(_d) > 0:
        return float(_d)
    return time.time() + _cfg_num("M455_TOTAL_BUDGET_SEC", _DEF_TOTAL_SEC)


def end_request() -> None:
    _DEADLINE.set(None)


# ---------------------------------------------------------------- 刀3：JSON 契约
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fence(text: str) -> str:
    """去掉 ```json 围栏（模型最常见的「不按表格填」形态）。"""
    t = str(text or "").strip()
    if "```" not in t:
        return t
    # 取第一个围栏块的内容；没有成对围栏时退回全文去标记
    m = re.search(r"```(?:json)?\s*(.+?)```", t, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return _FENCE_RE.sub("", t).strip()


def _clamp01(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(v)))


def parse_hypotheses(text: str, allowed_ids: list[str] | None = None,
                     max_hypotheses: int = 2) -> dict:
    """把模型输出解析成契约结构。

    规则（任务书刀3）：
      * 去围栏后 ``json.loads``；失败 ⇒ ``parse_error``；
      * ``speculative`` 必须为 ``true``（否则 ``parse_error``：那不是假设）；
      * ``evidence_ids`` 只能引用**本次提供的** id，未知 id 丢弃；
      * ``confidence`` 夹到 [0,1]；**无有效 evidence_ids ⇒ 封顶 0.3**；
      * 最多 ``max_hypotheses`` 条，超出截断（不静默扩容）。

    返回 ``{"status", "hypotheses", "dropped": {...}}``。
    """
    _allow = [str(x) for x in list(allowed_ids or [])]
    _drop: dict[str, int] = {}
    _raw = _strip_fence(text)
    if not _raw:
        return {"status": STATUS_PARSE_ERROR, "hypotheses": [], "dropped": {"empty": 1}}
    try:
        _data = json.loads(_raw)
    except Exception as _e:
        silent_exc(_e, where="hypothesize::parse_hypotheses json", level="debug")
        return {"status": STATUS_PARSE_ERROR, "hypotheses": [], "dropped": {"json": 1}}
    if not isinstance(_data, dict):
        return {"status": STATUS_PARSE_ERROR, "hypotheses": [], "dropped": {"not_dict": 1}}
    if _data.get("speculative") is not True:
        # ★缺这个标记就无法区分「假设」与「事实」，按契约拒收（绝不猜）
        return {"status": STATUS_PARSE_ERROR, "hypotheses": [],
                "dropped": {"not_speculative": 1}}
    _items = _data.get("hypotheses")
    if not isinstance(_items, list):
        return {"status": STATUS_PARSE_ERROR, "hypotheses": [], "dropped": {"no_list": 1}}

    _out: list[dict] = []
    for _it in _items:
        if not isinstance(_it, dict):
            _drop["not_dict"] = _drop.get("not_dict", 0) + 1
            continue
        _claim = str(_it.get("claim") or "").strip()
        if not _claim:
            _drop["empty_claim"] = _drop.get("empty_claim", 0) + 1
            continue
        _ids = _it.get("evidence_ids")
        _ids = [str(x) for x in _ids] if isinstance(_ids, list) else []
        _known = [x for x in _ids if x in _allow]
        if len(_known) != len(_ids):
            _drop["unknown_evidence_id"] = _drop.get("unknown_evidence_id", 0) + \
                (len(_ids) - len(_known))
        _conf = _clamp01(_it.get("confidence"))
        if not _known:
            # ★没引用任何有效证据 ⇒ 只能算「低置信猜测」，封顶 0.3
            _conf = min(_conf, 0.3)
        _asm = _it.get("assumptions")
        _out.append({
            "claim": _claim[:200],
            "assumptions": [str(x)[:80] for x in (_asm if isinstance(_asm, list) else [])][:3],
            "evidence_ids": _known[:4],
            "confidence": round(_conf, 4),
            "test": str(_it.get("test") or "").strip()[:120],
        })
        if len(_out) >= max(1, int(max_hypotheses)):
            break
    if len(_items) > len(_out):
        _drop["over_limit"] = len(_items) - len(_out)
    if not _out:
        return {"status": STATUS_PARSE_ERROR, "hypotheses": [], "dropped": _drop or {"empty": 1}}
    return {"status": STATUS_OK, "hypotheses": _out, "dropped": _drop}


# ---------------------------------------------------------------- 刀2：端点 / 请求
def _fallback_endpoint() -> tuple | None:
    """**兜底**端点解析（与 ``PulseInnerWorld._m31_branch_endpoint`` 同口径）。

    ★生产调用方传入的 ``endpoint_resolver`` 就是那个静态方法本身（真复用）；
      这里的实现只为「工具/单测直接调用本模块」时不给空跑，保持同一判定顺序：
      渠道池（按 priority 升序取第一个 url+key 齐备者）→ 单端点 ``REMOTE_API_CONFIG``。
    """
    try:
        import config as _c
    except Exception as _e:
        silent_exc(_e, where="hypothesize::_fallback_endpoint config", level="debug")
        return None
    _channel_first = True
    try:
        _channel_first = bool(getattr(_c, "ENABLE_BRANCH_GEN_CHANNEL_FIRST", True))
    except Exception as _e:
        silent_exc(_e, where="hypothesize::_fallback_endpoint switch", level="debug")
        _channel_first = True
    if _channel_first:
        try:
            for _ch in (_c.get_active_channels() or []):
                _url = str(_ch.get("api_url") or "").strip()
                _key = str(_ch.get("api_key") or "").strip()
                if _url and _key:
                    return (_url, _key, str(_ch.get("model") or "").strip(),
                            "channel:" + str(_ch.get("name") or "?"))
        except Exception as _e:
            silent_exc(_e, where="hypothesize::_fallback_endpoint channels", level="debug")
    try:
        _rc = getattr(_c, "REMOTE_API_CONFIG", None) or {}
        if not _rc.get("enabled", False):
            return None
        _url = str(_rc.get("api_url") or "").strip()
        _key = str(_rc.get("api_key") or "").strip()
        if not _url or not _key:
            return None
        return (_url, _key, str(_rc.get("default_model") or "deepseek-v4-flash"),
                "remote_config")
    except Exception as _e:
        silent_exc(_e, where="hypothesize::_fallback_endpoint remote", level="debug")
        return None


def _norm_evidence(evidence: Any, limit: int = 4) -> list[dict]:
    """把调用方给的证据收敛成 ``[{id, text, source}]``（id 供模型引用）。"""
    _out: list[dict] = []
    for _e in list(evidence or []):
        if not isinstance(_e, dict):
            continue
        _t = str(_e.get("inject_text") or _e.get("text") or "").strip()
        if not _t:
            continue
        _id = str(_e.get("id") or "").strip() or f"E{len(_out) + 1}"
        _out.append({"id": _id, "text": _t[:200],
                     "source": str(_e.get("source") or "")[:40]})
        if len(_out) >= max(1, int(limit)):
            break
    return _out


_SYSTEM_PROMPT = (
    "你是严谨的假设推演器。只输出一个 JSON 对象，不要输出任何解释、前后缀或代码围栏。\n"
    "JSON 结构：{\"speculative\": true, \"hypotheses\": [{\"claim\": \"一句话假设\", "
    "\"assumptions\": [\"成立前提\"], \"evidence_ids\": [\"E1\"], "
    "\"confidence\": 0.0, \"test\": \"如何验证\"}]}\n"
    "硬性要求：speculative 必须为 true；hypotheses 最多 2 条；"
    "claim ≤40 字；assumptions 最多 2 条且每条 ≤20 字；test ≤30 字；"
    "confidence 取 0~1 的小数；evidence_ids 只能引用给定证据的 id，没有引用就给空数组。"
)


def build_prompt(question: str, evidence: list[dict]) -> str:
    """构造 user prompt：问题 + 证据清单（**只给数据，不给指令权**）。"""
    _lines = ["问题：" + str(question or "").strip()[:300], "", "可用证据："]
    if evidence:
        for _e in evidence:
            _lines.append(f"- [{_e['id']}] {_e['text']}")
    else:
        _lines.append("- （无）")
    _lines.append("")
    _lines.append("请基于上述证据做「如果/假设」推演；证据不足时把 confidence 压低并说明前提。")
    return "\n".join(_lines)


def hypothesize(question: str, evidence: Any, *, deadline: float,
                origin: str = "", max_hypotheses: int = 2,
                endpoint_resolver: Callable[[], Any] | None = None,
                http_json: Callable[..., Any] | None = None) -> dict:
    """生成假设（shadow）。

    参数:
        question: 用户问题（空串 ⇒ ``insufficient_evidence``）。
        evidence: 证据列表（``[{id,text,source}]`` 或注入层 item）；空 ⇒ 不调用 LLM。
        deadline: **epoch 秒**的请求截止时刻（见 :func:`begin_request`）。
        origin: 来源标记；非用户来源直接跳过（烛微 H5）。
        max_hypotheses: 最多几条（默认 2，硬上限 2）。
        endpoint_resolver: 端点解析器（生产传 ``PulseInnerWorld._m31_branch_endpoint``）。
        http_json: 可变注入的 HTTP 出口（单测用；默认 ``nucleus.ssrf_guard.safe_http_json``）。

    返回:
        ``{"status", "hypotheses", "elapsed_ms", "reason", "dropped", ...}``；
        ``status="ok"`` 才带非空 ``hypotheses``。
    """
    _t0 = time.time()
    _out = {"status": STATUS_INSUFFICIENT, "hypotheses": [], "elapsed_ms": 0.0,
            "reason": "", "dropped": {}, "endpoint": "", "origin": str(origin or "")}

    def _done(_st: str, _reason: str = "", **_extra) -> dict:
        _out.update({"status": _st, "reason": _reason,
                     "elapsed_ms": round((time.time() - _t0) * 1000, 1)})
        _out.update(_extra)
        return _out

    if not enabled():
        return _done(STATUS_NO_BUDGET, "shadow_off")
    if str(origin or "") in _NON_USER_ORIGINS:
        return _done(STATUS_SKIPPED_ORIGIN, "origin=" + str(origin))

    _ev = _norm_evidence(evidence)
    if not str(question or "").strip() or not _ev:
        # ★刀2 验收：无证据时**不调用 LLM**（凭空假设没有价值）
        return _done(STATUS_INSUFFICIENT, "no_evidence" if _ev == [] else "empty_question")

    # ---- 预算（刀2/刀4）----
    _now = time.time()
    _reserve = _cfg_num("M455_ANSWER_RESERVE_SEC", _DEF_RESERVE_SEC)
    _min_start = _cfg_num("M455_MIN_START_SEC", _DEF_MIN_START_SEC)
    _remain = float(deadline) - _now
    if _remain < _min_start:
        return _done(STATUS_NO_BUDGET,
                     f"remain={_remain:.1f}s<{_min_start:.1f}s")

    # ---- 刀4：请求级「假设券」----
    try:
        from nucleus import evidence_injection as _ei
        _ticket = _ei.reserve_llm_op(max_llm_ops())
    except Exception as _e:
        silent_exc(_e, where="hypothesize::reserve_llm_op", level="warning")
        _ticket = {"ok": True, "used": 0, "limit": max_llm_ops()}
    if not _ticket.get("ok"):
        return _done(STATUS_NO_BUDGET,
                     f"llm_ops={_ticket.get('used')}>={_ticket.get('limit')}")

    _timeout = min(_cfg_num("M455_HYPOTHESIZE_TIMEOUT_SEC", _DEF_MAX_SEC),
                   _remain - _reserve)
    if _timeout < 1.0:
        return _done(STATUS_NO_BUDGET, f"timeout_budget={_timeout:.1f}s")

    # ---- 端点 ----
    try:
        _ep = (endpoint_resolver or _fallback_endpoint)()
    except Exception as _e:
        silent_exc(_e, where="hypothesize::endpoint_resolver", level="warning")
        _ep = None
    if not _ep:
        return _done(STATUS_NO_ENDPOINT, "no_endpoint")
    try:
        _url, _key, _model, _src = _ep
    except Exception as _e:
        silent_exc(_e, where="hypothesize::endpoint_unpack", level="warning")
        return _done(STATUS_NO_ENDPOINT, "bad_endpoint")
    _out["endpoint"] = str(_src)
    if not _url or not _key:
        return _done(STATUS_NO_ENDPOINT, "endpoint_incomplete")

    _payload = {
        "model": _model or "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(question, _ev)},
        ],
        "temperature": _cfg_num("M455_HYPOTHESIZE_TEMPERATURE", _DEF_TEMPERATURE),
        "max_tokens": int(_cfg_num("M455_HYPOTHESIZE_MAX_TOKENS", _DEF_MAX_TOKENS)),
    }
    _data = json.dumps(_payload, ensure_ascii=False).encode("utf-8")
    _headers = {"Content-Type": "application/json; charset=utf-8",
                "Authorization": "Bearer " + str(_key)}

    # ---- 调用（超时是**自己的**，不看 inner_world_chat=45s）----
    if http_json is None:
        from nucleus.ssrf_guard import safe_http_json as http_json  # 函数内导入便于打桩
    try:
        _ok, _resp = http_json(_url, method="POST", data=_data, headers=_headers,
                               timeout=max(1, round(_timeout)))
    except TypeError as _e:
        # 有些替身只吃位置参数；不让它把整条链路带崩
        silent_exc(_e, where="hypothesize::http_json signature", level="warning")
        return _done(STATUS_TIMEOUT, "http_error:TypeError")
    except Exception as _e:
        silent_exc(_e, where="hypothesize::http_json", level="warning")
        return _done(STATUS_TIMEOUT, f"http_error:{type(_e).__name__}")

    if not _ok:
        # safe_http_json 把超时/网络失败都收敛成 (False, 错误串)
        _msg = str(_resp)
        # 超时 / 网络失败在本模块统一按 timeout 处理（区分留给调用方的 reason）
        _st = STATUS_TIMEOUT
        return _done(_st, "http_fail:" + _msg[:80])

    try:
        _content = str((((_resp or {}).get("choices") or [{}])[0]
                        .get("message") or {}).get("content") or "")
    except Exception as _e:
        silent_exc(_e, where="hypothesize::response_unpack", level="debug")
        _content = ""
    if not _content.strip():
        # ★86/87 批教训：推理模型会把预算全花在 reasoning_content 上 ⇒ 留痕备查
        _fr, _ct, _rc = "", "", 0
        try:
            _ch0 = ((_resp or {}).get("choices") or [{}])[0]
            _fr = str(_ch0.get("finish_reason") or "")
            _ct = str((( _resp or {}).get("usage") or {}).get("completion_tokens") or "")
            _rc = len(str(((_ch0.get("message") or {}).get("reasoning_content")) or ""))
        except Exception as _e:
            silent_exc(_e, where="hypothesize::empty_content_meta", level="debug")
        return _done(STATUS_PARSE_ERROR,
                     f"empty_content(finish={_fr},tokens={_ct},reasoning={_rc}字)")

    _parsed = parse_hypotheses(_content, [x["id"] for x in _ev],
                               min(2, max(1, int(max_hypotheses))))
    if _parsed["status"] != STATUS_OK:
        return _done(STATUS_PARSE_ERROR, "parse:" + str(_parsed["dropped"]),
                     dropped=_parsed.get("dropped") or {})
    _hyps = _parsed["hypotheses"]
    _avg = round(sum(float(h["confidence"]) for h in _hyps) / max(1, len(_hyps)), 4)
    return _done(STATUS_OK, "", hypotheses=_hyps, hypothesis_count=len(_hyps),
                 avg_confidence=_avg, dropped=_parsed.get("dropped") or {},
                 evidence_count=len(_ev))
