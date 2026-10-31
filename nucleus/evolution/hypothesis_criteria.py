"""hypothesis_criteria.py —— 假设的可观测判据层（第409批 刀1/刀3）。

## 为什么需要它

`data/evolution/lesson_strategies.json` 里 30 条策略此前只有 `strategy` 一段文字描述，
**没有"怎么算对了""怎么算错了""什么时候出结果"** —— 等于记了一堆教训却从不回头验证，
是真教训还是误判永远不知道。本模块补的就是这三个字段，以及配套的判据探针。

## 字段契约（写进 `strategies[key]`）

| 字段 | 含义 | 形态 |
|---|---|---|
| `observable_criterion` | 可观测判据 | **dict**（可程序化）或 **str**（人读描述） |
| `observation_window` | 观察窗 | `"24h"` / `"7d"` / 秒数 |
| `due_at` | 到期时刻 | epoch 秒 = 入库时间 + 观察窗 |
| `missing_criterion` | 老条目标记 | `True` 表示"入库时没有判据，不强制补" |
| `result` | 对账三态 | `confirmed` / `refuted` / `unknown` |
| `resolved_at` | 对账时刻 | epoch 秒 |

## ★ 为什么 `observable_criterion` 允许两种形态

任务书要求"按 `observable_criterion` 去查实际数据"。但判据写成自由文本时**程序无从执行**
—— 唯一诚实的做法是：能结构化的给 dict（对账时真跑），不能的给 str（对账时判 `unknown`）。
**印一句"已验证"却没有任何数据支撑，比标 unknown 更糟。**

## 结构化判据的三种 kind（= 任务书刀3 的三判据）

```python
{"kind": "recurrence", "signature": "a.py::t::m", "window_sec": 86400}
{"kind": "effect",     "patch_id": "patch_xxx"}
{"kind": "reuse",      "signature": "a.py::t::m", "window_sec": 604800}
```

## 灰度

`config.ENABLE_M409_HYPOTHESIS_CRITERIA`（默认 True）。关闭时 `annotate_new_entry()`
原样返回、**一个字段都不写**，行为回到第 408 批。
"""

from __future__ import annotations

import re
import time
from typing import Any

from nucleus._silent_except import silent_exc

_M409_CRITERIA_VERSION = "409.1"

#: 三个必备字段
F_CRITERION = "observable_criterion"
F_WINDOW = "observation_window"
F_DUE = "due_at"
#: 老条目标记（不强制补判据）
F_MISSING = "missing_criterion"
#: 对账三态（刀2 写）
F_RESULT = "result"
F_RESOLVED = "resolved_at"

REQUIRED_FIELDS = (F_CRITERION, F_WINDOW, F_DUE)

#: 对账三态取值（**只有这三个**，别加"部分确认"这类中间态 —— 三态才可统计）
RESULT_CONFIRMED = "confirmed"
RESULT_REFUTED = "refuted"
RESULT_UNKNOWN = "unknown"
ALL_RESULTS = (RESULT_CONFIRMED, RESULT_REFUTED, RESULT_UNKNOWN)

#: 判据种类（与任务书刀3 一一对应）
KIND_RECURRENCE = "recurrence"
KIND_EFFECT = "effect"
KIND_REUSE = "reuse"
ALL_KINDS = (KIND_RECURRENCE, KIND_EFFECT, KIND_REUSE)

#: ★判据2 的效果判据不需要观察窗（看的是 effect_verify 的即时结论），
#:   但窗口字段仍要求填 —— 它是"多久之内该看到结论"的承诺，不是判据本身。
#: ★单位必须**长短优先**交替（`分钟|小时` 在前）：单字符类 `[分秒]` 匹配不到
#:   `"2分钟"` 这种两字单位，会让合法的窗口被判成"无法解析"（409 首跑即此）。
_WINDOW_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(分钟|小时|天|分|秒|s|m|h|d)?\s*$", re.I)
_UNIT_SECONDS = {
    "s": 1.0, "秒": 1.0,
    "m": 60.0, "分": 60.0, "分钟": 60.0,
    "h": 3600.0, "时": 3600.0, "小时": 3600.0,
    "d": 86400.0, "天": 86400.0,
}


def enabled() -> bool:
    """灰度总开关。读取失败一律按「开」处理（判据层是增强，不是依赖）。"""
    try:
        import config as _c
        return bool(getattr(_c, "ENABLE_M409_HYPOTHESIS_CRITERIA", True))
    except Exception as _e:
        silent_exc(_e, where="hypothesis_criteria.enabled", level="debug")
        return True


def parse_window(value: Any) -> float | None:
    """把 `"24h"` / `"7d"` / `"7天"` / `86400` 统一成秒。

    Returns:
        秒数（float）；无法解析返回 ``None``（**不猜默认值** —— 猜出来的窗口会让
        `due_at` 变成一个看似合理实则无依据的时刻）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    _m = _WINDOW_RE.match(value)
    if not _m:
        return None
    try:
        _num = float(_m.group(1))
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="hypothesis_criteria.parse_window", level="debug")
        return None
    _unit = (_m.group(2) or "s").lower()
    _mul = _UNIT_SECONDS.get(_unit)
    if _mul is None:
        return None
    _sec = _num * _mul
    return _sec if _sec > 0 else None


def build_criteria(criterion: Any, window: Any, now: float | None = None) -> dict:
    """由判据 + 观察窗构造三字段（不校验语义，只做形态归一）。

    Returns:
        ``{"observable_criterion": ..., "observation_window": ..., "due_at": ...}``；
        `due_at` 在窗口无法解析时**不写**（宁可缺字段让人看见，也不填假时刻）。
    """
    _now = float(now if now is not None else time.time())
    _out: dict[str, Any] = {F_CRITERION: criterion, F_WINDOW: window}
    _sec = parse_window(window)
    if _sec is not None:
        _out[F_DUE] = _now + _sec
    return _out


def normalize_criterion(criterion: Any) -> dict | None:
    """把判据归一成结构化 dict；不可结构化的返回 ``None``。

    这是刀2/刀3 对账的**入口判据**：拿不到 dict 就不硬判（标 unknown）。
    """
    if not isinstance(criterion, dict):
        return None
    _kind = str(criterion.get("kind") or "").strip().lower()
    if _kind not in ALL_KINDS:
        return None
    return criterion


def validate(entry: Any, now: float | None = None) -> dict:
    """检查一个策略条目是否三字段齐备。

    Returns:
        ``{"ok": bool, "missing": [...], "warnings": [...], "window_sec": float|None}``
    """
    _warnings: list[str] = []
    _missing: list[str] = []
    if not isinstance(entry, dict):
        return {"ok": False, "missing": list(REQUIRED_FIELDS),
                "warnings": ["条目不是 dict"], "window_sec": None}
    for _f in REQUIRED_FIELDS:
        if entry.get(_f) in (None, "", {}):
            _missing.append(_f)
    _sec = parse_window(entry.get(F_WINDOW))
    if entry.get(F_WINDOW) not in (None, "") and _sec is None:
        _warnings.append(f"observation_window 无法解析: {entry.get(F_WINDOW)!r}")
    # `due_at` 必须是可比的数值（字符串/None 都会让对账静默漏掉）
    _due = entry.get(F_DUE)
    if _due is not None:
        try:
            float(_due)
        except (TypeError, ValueError):
            _warnings.append(f"due_at 不是数值: {_due!r}")
    # 判据能结构化就顺手验一下 kind 是否在册
    _crit = entry.get(F_CRITERION)
    if _crit not in (None, "") and normalize_criterion(_crit) is None \
            and isinstance(_crit, dict):
        _warnings.append(
            f"observable_criterion.kind 不在册: {_crit.get('kind')!r}")
    _now_check = float(now if now is not None else time.time())
    if isinstance(_due, (int, float)) and not isinstance(_due, bool):
        if float(_due) <= 0:
            _warnings.append("due_at <= 0（大概率是占位值）")
        elif float(_due) > _now_check + 366 * 86400:
            _warnings.append("due_at 超过一年（大概率单位写错）")
    return {"ok": not _missing, "missing": _missing,
            "warnings": _warnings, "window_sec": _sec}


def annotate_new_entry(entry: dict, now: float | None = None,
                       treat_as_new: bool = True) -> dict:
    """给**新入库**的假设补 `due_at`、缺字段则打 `missing_criterion`（不阻断）。

    Args:
        entry: 策略条目（原地修改）。
        now: 入库时刻（测试可注入）。
        treat_as_new: True 表示这是新假设 —— 缺判据要打 WARNING 级的标记。

    Returns:
        ``{"ok", "missing", "warnings", "applied"[], "skipped"}`；
        `applied` 是本函数**实际写进 entry 的字段名**（便于断言"关了开关零写入"）。
    """
    _now = float(now if now is not None else time.time())
    _rep = validate(entry, now=_now)
    _rep["applied"] = []
    _rep["skipped"] = ""
    if not enabled():
        _rep["skipped"] = "switch_off"
        return _rep
    if _rep["ok"]:
        return _rep
    # 缺字段：只补能补的（due_at 由 window 推），其余留空由 missing 标记说明
    _sec = parse_window(entry.get(F_WINDOW))
    if _sec is not None and entry.get(F_DUE) in (None, ""):
        entry[F_DUE] = _now + _sec
        _rep["applied"].append(F_DUE)
    # ★补完 due_at 要**重新判一次**：判据+窗口齐、只差到期时刻的条目，
    #   补完就是合格的，不该再被打上「没有判据」的标记（否则标记会通货膨胀，
    #   而 missing_criterion 一旦通胀就再也读不出真实缺口）。
    _mid = validate(entry, now=_now)
    if not _mid["ok"] and treat_as_new and not entry.get(F_MISSING):
        entry[F_MISSING] = True
        _rep["applied"].append(F_MISSING)
    # ★`missing` 反映**补完之后**仍缺什么 —— 调用方读它才能直接决定要不要告警。
    _final = validate(entry, now=_now)
    _rep["ok"] = _final["ok"]
    _rep["missing"] = _final["missing"]
    _rep["warnings"] = _final["warnings"]
    return _rep


def mark_legacy_entries(strategies: dict) -> dict:
    """给**老条目**打 `missing_criterion=True`（不强制补三字段）。

    ★ 只标记、不改写、不删除：老 30 条是历史账，补判据需要人知道当初想验什么，
      程序猜不出来。下批再议。

    Returns:
        ``{"marked": n, "already": n, "has_criteria": n, "total": n}``
    """
    # ★开关必须在**动手之前**判：先改后判等于"关掉也照写"，灰度就成了摆设。
    #   （409 首跑：marked 报 0、字段却真被写进去了，计数与副作用脱钩。）
    if not enabled():
        return {"marked": 0, "already": 0, "has_criteria": 0, "total": 0,
                "skipped": "switch_off"}
    _marked = _already = _has = 0
    for _v in (strategies or {}).values():
        if not isinstance(_v, dict):
            continue
        if all(_v.get(_f) not in (None, "", {}) for _f in REQUIRED_FIELDS):
            _has += 1
            continue
        if _v.get(F_MISSING):
            _already += 1
            continue
        _v[F_MISSING] = True
        _marked += 1
    return {"marked": _marked, "already": _already, "has_criteria": _has,
            "total": _marked + _already + _has}


def summarize(strategies: dict) -> dict:
    """盘账快照：多少条有判据、多少条标了 missing、多少条已有对账结果。"""
    _s = {"total": 0, "has_criteria": 0, "missing_criterion": 0,
          "no_criteria_and_no_flag": 0, "settled": 0,
          "by_result": {RESULT_CONFIRMED: 0, RESULT_REFUTED: 0,
                        RESULT_UNKNOWN: 0},
          "by_kind": {_k: 0 for _k in ALL_KINDS}}
    for _v in (strategies or {}).values():
        if not isinstance(_v, dict):
            continue
        _s["total"] += 1
        _complete = all(_v.get(_f) not in (None, "", {}) for _f in REQUIRED_FIELDS)
        if _complete:
            _s["has_criteria"] += 1
        elif _v.get(F_MISSING):
            _s["missing_criterion"] += 1
        else:
            # ★这个计数必须单独存在：它表示"既没判据也没标记"的漏网条目，
            #   是刀1 是否真正覆盖全部写入点的唯一可观测证据。
            _s["no_criteria_and_no_flag"] += 1
        _r = str(_v.get(F_RESULT) or "")
        if _r in _s["by_result"]:
            _s["by_result"][_r] += 1
            _s["settled"] += 1
        _c = normalize_criterion(_v.get(F_CRITERION))
        if _c is not None:
            _s["by_kind"][str(_c.get("kind"))] += 1
    return _s


def version() -> str:
    """本模块契约版本（写进盘账顶层，便于跨批比对）。"""
    return _M409_CRITERIA_VERSION
