# -*- coding: utf-8 -*-
"""代码学习掌握率取数（★第314批 刀1，P0）—— **唯一口径**。

问题（烛微314批前置分析）
--------------------------
掌握率的真实断点不是"分母难算"，而是 **`code_learner.stats` 没有生产者**：
全库 2 个读取方（`daily_reader.append_mastery_section` /
`PulseNarrativeSelf._generate_weekly_report`），**0 个发射方**。

两处都走 `InfoField.get_current("code_learner.stats")`，而 `get_current` 是
**脉冲历史查询**（`event_type == key` 且脉冲自带 TTL，缺省 5 秒 —— 见
`nucleus/field/InfoField.py::get_current` 的 docstring），不是"读当前值"。
两个后果叠在一起，读数就"哑"了却不会报错：

  ① 就算哪天有人 emit，5 秒后照样读不到；
  ② **返回的是脉冲记录 dict**（`event_type` / `payload` / `timestamp_ns`），
     不是 stats 本体 ⇒ `pulse.get("understood")` 恒为 `None`
     ⇒ `source=unavailable` 与"读数真的是 0"长得一模一样。

解法
----
本模块给出**唯一取数口** :func:`get_code_learning_stats`：

  ① 先试 `InfoField.get_current`（**保留兼容**；两种写法都认：
     「脉冲里嵌 payload」与「直接就是 stats 本体」）；
  ② 取不到就**直调真实生产者** `PulseCodeLearner.get_stats()`
     ——器官活体从 `SelfAwarenessEngine.set_awareness_organs()` 注册的器官表取
     （308 批接线，中文名/属性名/类名三套键都认）；
  ③ 两路都取不到 ⇒ `source="unavailable"` 且 `gap` 明确写成**结构性缺口**
     （`no_producer(...)`），**绝不返回 0 冒充读数**。

语义边界：纯只读。本模块不 emit、不写缓存、不改器官内部口径；
分母若两路都没有，由调用方传 `fallback_total`（`daily_reader` 用
`self_inspector` 的 AST `method_count` 求和）。
"""

from __future__ import annotations

from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_module_logger = get_module_logger("CodeLearningStats")

#: 代码学习器官在器官表里的三套键（中文名 / 属性名 / 类名）
LEARNER_ORGAN_KEYS: tuple[str, ...] = ("代码学习", "code_learner", "PulseCodeLearner")

#: 信息场里的统计事件名（历史口径，全库 0 个发射方，仅作兼容尝试）
STATS_EVENT_KEY = "code_learner.stats"


def _as_count(value: Any) -> int | None:
    """把计数归一为非负 int；认不出（None/布尔/非数字串）⇒ None（**不折算成 0**）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        _s = value.strip()
        if _s.isdigit():
            return int(_s)
    return None


def _stats_from_mapping(raw: Any) -> tuple[int | None, int | None]:
    """从「脉冲记录」或「stats 本体」里取 `(understood, total_methods)`。

    `get_current` 返回的是脉冲记录，真正的统计在它的 `payload` 里（若调用方
    直接塞了 stats 本体也照样认）——两种写法都在这里一次收口。
    """
    if not isinstance(raw, dict):
        return None, None
    _payload = raw.get("payload")
    _stats = _payload if isinstance(_payload, dict) else raw
    return (_as_count(_stats.get("understood")),
            _as_count(_stats.get("total_methods")))


def _find_learner(organs: Any = None) -> Any:
    """从器官表取**活体**代码学习器官；取不到返回 None。"""
    if organs is None:
        try:
            from nucleus.self_awareness.SelfAwarenessEngine import _AWARENESS_ORGANS
            organs = _AWARENESS_ORGANS
        except Exception as e:
            silent_exc(e, where="nucleus.code_learning_stats::_find_learner:import")
            return None
    if organs is None:
        return None
    for _k in LEARNER_ORGAN_KEYS:
        try:
            _o = organs.get(_k) if isinstance(organs, dict) else getattr(organs, _k, None)
        except Exception as e:
            silent_exc(e, where="nucleus.code_learning_stats::_find_learner:get")
            continue
        if _o is not None:
            return _o
    return None


def get_code_learning_stats(info_field: Any = None, organs: Any = None,
                            fallback_total: int = 0) -> dict[str, Any]:
    """取代码学习掌握率分子/分母（唯一口径，两处读者共用）。

    Args:
        info_field: 信息场实例；None 时取 `get_info_field()` 单例。
        organs: 器官表；None 时取 `SelfAwarenessEngine` 的模块级注册表。
        fallback_total: 两路都取不到分母时的兜底（通常是 AST `method_count` 求和）。

    Returns:
        ``{"understood": int|None, "total_methods": int|None, "mastery_pct": float|None,
           "source": str, "gap": str|None, "learner_found": bool}``

        - ``source``：``info_field.code_learner.stats`` / ``code_learner.get_stats`` /
          ``unavailable``（**不虚构**）；
        - ``understood is None`` ⇒ 结构性缺口（见 ``gap``），调用方应打印缺口而不是 0%。
    """
    _understood: int | None = None
    _total: int | None = None
    _src = "unavailable"
    _gap: str | None = None
    _learner_found = False

    # ① 兼容口径：信息场脉冲历史（get_current 实为 5s TTL 的历史查询）
    try:
        _if = info_field
        if _if is None:
            from nucleus.field.InfoField import get_info_field
            _if = get_info_field()
        if _if is not None and hasattr(_if, "get_current"):
            _u, _t = _stats_from_mapping(_if.get_current(STATS_EVENT_KEY))
            if _u is not None:
                _understood, _total = _u, _t
                _src = "info_field.code_learner.stats"
    except Exception as e:
        silent_exc(e, where="nucleus.code_learning_stats::get_code_learning_stats:info_field",
                   level="warning")

    # ② 直调真实生产者（PulseCodeLearner.get_stats 是仓内唯一理解度计数出口）
    if _understood is None:
        try:
            _organ = _find_learner(organs)
            _learner_found = _organ is not None
            if _learner_found and hasattr(_organ, "get_stats"):
                _u, _t = _stats_from_mapping(_organ.get_stats())
                if _u is not None:
                    _understood, _total = _u, _t
                    _src = "code_learner.get_stats"
        except Exception as e:
            silent_exc(e, where="nucleus.code_learning_stats::get_code_learning_stats:learner",
                       level="warning")

    # ③ 分母兜底（只补分母，绝不补分子）
    if (_total is None or _total <= 0) and fallback_total:
        _t_fb = _as_count(fallback_total)
        if _t_fb:
            _total = _t_fb

    if _understood is None:
        _gap = ("no_producer(code_learner.stats 无生产者：器官未注册或未启动，"
                "且信息场无历史脉冲)")
    elif not _total:
        _gap = "total_unavailable(分母两路均取不到)"

    _pct: float | None = None
    if _understood is not None and _total:
        _pct = round(min(100.0, _understood / _total * 100), 1)
    if _understood is None:
        _module_logger.warning("[掌握率] 分子取不到（结构性缺口）: %s", _gap)
    return {
        "understood": _understood,
        "total_methods": _total,
        "mastery_pct": _pct,
        "source": _src,
        "gap": _gap,
        "learner_found": _learner_found,
    }
