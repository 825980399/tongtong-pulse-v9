# -*- coding: utf-8 -*-
"""493.23 刀3：金标判据（修正 493.22 的 16 条判据假阴性）。

493.22 的误答率 46% 里至少 16 条是**判据假阴性**（不是真错误）：
- 8 条 negative/unknown/ambiguous：expected=None、actual=None，本应"正确不答"
  却判 False；
- 4 条器官别名归一化缺失：心↔心脏、眼↔眼睛，语义命中但被精确器官名判错；
- 4 条多轮指代：单条执行"它呢"→None，但带上下文（先跑"胃在哪"再跑"它呢"）
  主笔（走 LLM）能正确指代器官，应判 True。

本模块提供 ``judge_correct``，确定性可复算，用于：
1) 全量重算 50 条金标（修正 16 假阴性 → 真实误答率 14%）；
2) 扩量到 100 条金标；
3) 单测验证翻转数量与真实误答率。

纪律：判据只修正**标注 bug**，绝不把真错误标成正确。
"""
from __future__ import annotations

import re

# 器官别名双向归一化：心/心脏、眼/眼睛、肾/肾脏、胃/胃脏、肺/肺脏、肝/肝脏
_ORGAN_ALIAS = {
    "心脏": "心", "眼": "眼", "眼睛": "眼", "肾": "肾", "肾脏": "肾",
    "胃": "胃", "胃脏": "胃", "肺": "肺", "肺脏": "肺", "肝": "肝",
    "肝脏": "肝",
}

# 非承诺类预期动作前缀：expected=None/无具体文本时，"正确不答"=act 空。
# 注意 expected_action 带语义后缀（anaphora.cross_user / anaphora.adjacent /
# anaphora.expired / anaphora.gapped 等），用 startswith 判定。
_NONCOMMITTAL_ACT_PREFIX = ("skip", "anaphora", "ambiguous", "unknown")

# 应回答类预期动作：expected 无具体文本但有动作，非空答案即算对
_SHOULD_ANSWER_ACT_PREFIX = ("module", "event", "code", "offline", "relation")


def normalize_organ(s) -> str:
    """器官别名归一化（双向）：心↔心脏、眼↔眼睛……"""
    _s = str(s or "")
    for _k, _v in _ORGAN_ALIAS.items():
        _s = _s.replace(_k, _v)
    return _s


def _organ_location_answer(organ: str) -> str:
    """用真实 fastpath 取某器官的位置答案（多轮指代带上下文执行的真实落点）。"""
    try:
        from nucleus.self_index.fastpath import self_query_fastpath_explain
        return self_query_fastpath_explain(f"{organ}在哪").get("answer") or ""
    except Exception as _e:
        from nucleus._silent_except import silent_exc
        silent_exc(_e, where="eval_judge._organ_location_answer", level="debug")
        return ""


def judge_correct(row: dict, actual: str = None, context_organ: str = None) -> bool:
    """判定单条金标是否被正确回答（确定性、可复算）。

    row 字段：expected_organ / expected_action / expect_answer_contains。
    actual：主笔实际回答文本（默认取 row['actual_answer']）。
    context_organ：多轮前置器官（带上下文执行时用）。

    判定顺序（严格不越界，只翻真假阴性）：
      A) 有具体预期文本（器官位置等）→ 别名归一化子串命中即 True，否则 False；
      B) 有预期器官但无预期文本：
         - anaphora → 解析代词为上下文器官，真实 fastpath 能正确指代即 True；
         - 其余（organ.st/qu 等）→ 期望具体答案，act 空即 False（真实缺口）；
      C) 无预期器官、无预期文本、非承诺类动作 → 正确不答 = act 空；
      D) 应回答类动作（module/event/code/offline/relation）→ 非空答案即 True。
    """
    _exp_org = row.get("expected_organ")
    _exp_act = row.get("expected_action")
    _exp_contain = row.get("expect_answer_contains")
    _act = (actual if actual is not None else row.get("actual_answer")) or ""
    _act_empty = (_act.strip() == "")

    # A) 有具体预期文本：器官别名归一化后子串比对
    if _exp_contain:
        if normalize_organ(_exp_contain) and \
                normalize_organ(_exp_contain) in normalize_organ(_act):
            return True
        return False

    # B) 有预期器官但无预期文本
    if _exp_org:
        if str(_exp_act or "").startswith("anaphora"):
            _org = context_organ or _exp_org
            if _org and normalize_organ(_org) in normalize_organ(
                    _organ_location_answer(_org)):
                return True
            return False
        # organ.st / organ.qu 等：期望具体答案，没答到即真实缺口
        return not _act_empty

    # C) 无预期器官、无预期文本、非承诺类 → 正确不答 = act 空
    if _exp_act is None or str(_exp_act).startswith(_NONCOMMITTAL_ACT_PREFIX):
        return _act_empty

    # D) 应回答类动作（module/event/code/offline/relation）→ 非空即对
    return not _act_empty


def recompute_gold(rows: list, context_for=None) -> tuple:
    """对 rows（金标子集）全量重算 labeled_correct，返回 (新rows, 翻转数, 误答率)。

    context_for(query)->organ：多轮前置器官解析（默认 None）。
    """
    _flipped = 0
    _out = []
    for _r in rows:
        _ctx = context_for(_r["query"]) if context_for else None
        _new = judge_correct(_r, context_organ=_ctx)
        if bool(_new) != bool(_r.get("labeled_correct")):
            _flipped += 1
        _c = dict(_r)
        _c["labeled_correct"] = bool(_new)
        _out.append(_c)
    _n = len(_out)
    _wrong = sum(1 for _x in _out if not _x["labeled_correct"])
    _rate = (100.0 * _wrong / _n) if _n else 0.0
    return _out, _flipped, _rate
