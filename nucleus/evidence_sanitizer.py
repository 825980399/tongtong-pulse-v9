# -*- coding: utf-8 -*-
"""★453刀4（P0-1b 前置）：检索证据正文净化器 —— 拼进 prompt 之前的唯一入口。

为什么必须单独一层
------------------
452 的 dry-run 只是「记下来」，453 要把正文**拼进用户可见的 prompt**。经验池是
**用户可写的长期存储**（125 条 Q&A 记忆里就存着用户原始提问），所以这是典型的
*存储型 prompt injection* 面：一次恶意提问被写进经验池，下一轮检索命中后就会
被塞进别人的 system/user 上下文。烛微《453批前置风险深挖》§二 列了 8 类攻击面，
本模块逐条落地。

八类污染（与任务书刀4 一一对应）
--------------------------------
1. 注入攻击   「忽略以上指令 / ignore previous / system prompt / 请扮演 / 执行以下」
2. 乱码       非常用字符（控制符/零宽符/私用区）占比 > 30%
3. PII        手机号 / 邮箱 / API key / 身份证 → 打码；单条 ≥2 处直接丢弃
4. 标签逃逸   ``</evidence>``、``<``、``>``、``&`` → HTML 实体转义
5. 角色前缀   ``system:`` / ``助手：`` / ``用户:`` … → 剥离
6. 指令句     句首「请 / 你应该 / 务必 / 现在执行」等祈使句 → 整条丢弃
7. 重复       与已选证据 ``SequenceMatcher`` 相似度 > 0.8 → 丢弃
8. 过期       经验自身 ``timestamp`` 距今 > 30 天 → 丢弃

设计纪律
--------
* **纯函数**：不读 config、不写盘、不发日志、不 import 框架器官；
  所有策略参数由调用方显式传入（默认取自 config 的可调用层）。
* **绝不抛异常**：任何脏输入都返回 ``""`` 或空结果，绝不把异常冒泡到回答链路。
* **宁可少注入，不可错注入**：被过滤的条数由 ``filter_evidence`` 汇总返回，
  调用方记 debug 日志（本模块不碰日志器）。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# ---------------------------------------------------------------- 1 注入攻击
_INJECTION_RE = re.compile(
    r"(?:忽略(?:以上|前面|之前|所有|上述)?(?:的)?(?:指令|提示|要求|内容)"
    r"|ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?)"
    r"|system\s*prompt|you\s+are\s+now|请扮演|扮演(?:成|为)|"
    r"执行以下(?:指令|命令|操作)|不要回答|不得回答|绕过(?:以上|上述)?(?:限制|规则)|"
    # ★453R刀9：烛微/星轨点名的绕过句式
    r"disregard(?:\s+(?:all|any|the|previous))?|forget\s+(?:everything|all|prior)|"
    r"from\s+now\s+on|从现在起|作为(?:一名)?开发者|请直接输出|"
    r"override(?:\s+the)?\s+(?:system|instructions))",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- 5 角色前缀
_ROLE_PREFIX_RE = re.compile(
    r"^\s*(?:system|assistant|user|developer|tool|function"
    r"|系统|助手|用户|开发者|工具|函数)\s*[:：]\s*",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- 6 指令句
_IMPERATIVE_RE = re.compile(
    r"^\s*(?:请(?:你)?(?!问)|你应该|你必须|务必|现在(?:请)?执行|请立即|"
    r"please\s+(?:do|run|execute|ignore)|do\s+not\s+answer)",
    re.IGNORECASE,
)

# ★453R刀9：祈使句从「只判句首」扩到「句首或分句首」。
#   只收**强指令**词（普通「请关注」放行），避免把正常经验正文误杀。
_CLAUSE_IMPERATIVE_RE = re.compile(
    r"(?:^|[。；;!?！？\n]\s*)(?:你必须|请直接输出|请立即|务必|现在执行|"
    r"你应该|do\s+not\s+answer|please\s+(?:run|execute|ignore))",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- 3 PII
_PII_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "<已脱敏手机号>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<已脱敏邮箱>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "<已脱敏密钥>"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "<已脱敏证件号>"),
)

# ---------------------------------------------------------------- 2 乱码
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 允许：CJK / 拉丁字母 / 数字 / 常见中英标点 / 空白；其余算噪声字符
_ALLOWED_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbfA-Za-z0-9\s"
    r"\u3002\uff0c\uff01\uff1f\u3001\uff1b\uff1a\u201c\u201d\u2018\u2019"
    r"\uff08\uff09\u300a\u300b\u3008\u3009\u3010\u3011\[\](){}<>/\\\-_=+*&^%$#@~|"
    r".,!?;:'\"`\u2026\u2014\u00b7\u00d7\u2192\u2190\u2191\u2193]"
)

_TAG_ESCAPE_RE = re.compile(r"</?\s*evidence\b[^>]*>", re.IGNORECASE)

_DEFAULT_MAX_CHARS = 100
_DEFAULT_MAX_AGE_DAYS = 30
_DEFAULT_SIMILARITY = 0.8


def _garble_ratio(text: str) -> float:
    """非常用字符占比（空串视为 0，不判乱码）。"""
    if not text:
        return 0.0
    bad = sum(1 for ch in text if not _ALLOWED_RE.match(ch))
    return bad / len(text)


def _pii_redact(text: str) -> tuple[str, int]:
    n = 0
    for pat, mask in _PII_PATTERNS:
        text, k = pat.subn(mask, text)
        n += k
    return text, n


def _escape_tags(text: str) -> str:
    """先替 ``</evidence>`` 闭合标签，再对全部 ``& < >`` 做实体转义。

    裸 ``<``/``>`` 也要转义（防 ``<system>`` / ``<assistant>`` 之类伪标签
    被模型当成结构标记）；闭合标签本身保留为可读的 ``&lt;/evidence&gt;``。
    """
    text = _TAG_ESCAPE_RE.sub(lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), text)
    text = (text.replace("&", "&amp;")
                .replace("&amp;lt;", "&lt;")
                .replace("&amp;gt;", "&gt;"))
    return text.replace("<", "&lt;").replace(">", "&gt;")


def sanitize_evidence(text: Any, *, max_chars: int = _DEFAULT_MAX_CHARS) -> dict:
    """净化**单条**证据正文，返回 ``{"text", "ok", "reason", "redactions"}``。

    ``ok=False`` 时 ``text`` 恒为 ``""`` —— 调用方只需判 ``ok`` 即可，
    不会把「过滤后的空串」误当成「正常但内容短」。
    """
    try:
        raw = "" if text is None else str(text)
    except Exception as _e:
        # ★每个 except 都要留痕（cw2 门禁）：绝不把异常静默吞进回答链路。
        from nucleus._silent_except import silent_exc
        silent_exc(_e, where="evidence_sanitizer::sanitize_evidence 不可字符串化", level="debug")
        return {"text": "", "ok": False, "reason": "unstringifiable", "redactions": 0}
    if not raw.strip():
        return {"text": "", "ok": False, "reason": "empty", "redactions": 0}

    # 2 零宽/控制字符先剥（否则会被算进乱码比例，造成误杀）
    t = _ZERO_WIDTH_RE.sub("", raw)
    t = _CONTROL_RE.sub("", t)
    if not t.strip():
        return {"text": "", "ok": False, "reason": "control_only", "redactions": 0}
    if _garble_ratio(t) > 0.30:
        return {"text": "", "ok": False, "reason": "garble", "redactions": 0}

    # 1 注入攻击：整条丢弃（不是打码 —— 打码后残留的语义仍可能诱导模型）
    if _INJECTION_RE.search(t):
        return {"text": "", "ok": False, "reason": "injection", "redactions": 0}

    # 5 角色前缀：只剥句首一次（正文中间出现属正常叙述，不误杀）
    t = _ROLE_PREFIX_RE.sub("", t, count=1)

    # 6 指令句：剥完角色前缀再判，防「助手：请执行…」绕过
    if _IMPERATIVE_RE.search(t) or _CLAUSE_IMPERATIVE_RE.search(t):
        return {"text": "", "ok": False, "reason": "imperative", "redactions": 0}

    # 3 PII 打码；单条命中 ≥2 处 ⇒ 信息密度太低且有跨用户泄漏风险 ⇒ 丢弃
    t, redactions = _pii_redact(t)
    if redactions >= 2:
        return {"text": "", "ok": False, "reason": "pii_dense", "redactions": redactions}

    # 4 标签逃逸（含裸 < > &）
    t = _escape_tags(t)

    # 单行化 + 硬截（★截断放在转义之后，保证截出来的串仍是合法实体文本）
    t = " ".join(t.split())
    limit = max(1, int(max_chars))
    t = t[:limit]
    if not t:
        return {"text": "", "ok": False, "reason": "empty_after_clean", "redactions": redactions}
    return {"text": t, "ok": True, "reason": "kept", "redactions": redactions}


def sanitize_text(text: Any, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """便捷口：只要净化后的正文，被过滤返回 ``""``。"""
    return sanitize_evidence(text, max_chars=max_chars)["text"]


def _expired(item: dict, now: float, max_age_days: float) -> bool:
    ts = item.get("timestamp", item.get("ts"))
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False                      # 无时间戳不臆造「过期」
    return float(ts) < float(now) - float(max_age_days) * 86400.0


def _similar(a: str, b: str, threshold: float) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() > float(threshold)


def filter_evidence(items: list[dict] | None, *, now: float | None = None,
                    max_age_days: float = _DEFAULT_MAX_AGE_DAYS,
                    similarity: float = _DEFAULT_SIMILARITY,
                    max_chars: int = _DEFAULT_MAX_CHARS,
                    max_items: int = 2) -> dict:
    """净化 + 去重 + 时效过滤一批证据，返回 ``{"kept", "dropped", "total"}``。

    ``dropped`` 是 ``{原因: 条数}``，供调用方一行 DEBUG 汇总（本模块不写日志）。
    输入顺序保持（不重排）—— 排序是 ``evidence_trace`` / 注入层的职责。
    """
    import time

    now = float(time.time()) if now is None else float(now)
    out: list[dict] = []
    dropped: dict[str, int] = {}
    for raw in list(items or []):
        if not isinstance(raw, dict):
            dropped["not_dict"] = dropped.get("not_dict", 0) + 1
            continue
        if _expired(raw, now, max_age_days):
            dropped["expired"] = dropped.get("expired", 0) + 1
            continue
        r = sanitize_evidence(raw.get("text"), max_chars=max_chars)
        if not r["ok"]:
            key = r["reason"]
            dropped[key] = dropped.get(key, 0) + 1
            continue
        clean = r["text"]
        if any(_similar(clean, str(k.get("text") or ""), similarity) for k in out):
            dropped["duplicate"] = dropped.get("duplicate", 0) + 1
            continue
        rec = dict(raw)
        rec["text"] = clean
        rec["redactions"] = r["redactions"]
        out.append(rec)
        if len(out) >= max(1, int(max_items)):
            break
    return {"kept": out, "dropped": dropped, "total": len(list(items or []))}
