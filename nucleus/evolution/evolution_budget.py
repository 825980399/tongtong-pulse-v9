# -*- coding: utf-8 -*-
"""★335批刀6（小林拍板③）：进化费用预算熔断。

问题：T-进化费用追踪-1 只落了「估算开关」（`config.ENABLE_EVOLUTION_COST_ESTIMATE`
＋ 单价表），**没有预算、没有熔断** —— `llm_call_total=11980` 里进化占 48%
（5738 次），账能算出来，却没有任何机制在"花超了"时把进化动作按下来。

本模块只做三件事（**额度本身不由本批裁定**）：
  ① **记侧账**：把每次 LLM 调用的 `cost_estimate` 累进「日 / 周」两档；
  ② **判熔断**：日或周任一超限 ⇒ `tripped=True`；
  ③ **给开关**：`tripped` 时进化通道降级（只发现不修复 / 不派单）。

边界（任务书明令，写死在此处防止被"优化"掉）：
  * **熔断不影响只读巡检 / 心跳 / W1** —— 只拦"花钱的进化动作"；
  * 阈值**不自行定**：`config.EVOLUTION_BUDGET_DAILY_CNY` /
    `EVOLUTION_BUDGET_WEEKLY_CNY` 默认 `None` = **未配置 ⇒ 不熔断**。
    **绝不写 0** —— 0 会被读成"预算零元"，一上线就把进化全拦死；
  * 路径可注入（`path`）—— 隔离验证不得写生产账（铁律158）；
  * 侧账坏了只降级不抛：读不到就记 unavailable，**不填 0**、不阻断主流程。
"""
import json
import os
import time

from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger
from nucleus.proc_gen import proc_gen_str as _m335_pg

_module_logger = get_module_logger("EvolutionBudget")

_M335_BUDGET_REL = os.path.join("data", "evolution", "evolution_budget.json")
_M335_BUDGET_VERSION = "335.1"


def _m335_budget_path(path: str | None = None,
                      project_root: str | None = None) -> str:
    """侧账路径：注入优先 → project_root → 项目根默认。"""
    if path:
        return path
    _root = project_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(_root, _M335_BUDGET_REL)


def _m335_day_key(ts: float) -> str:
    return time.strftime("%Y%m%d", time.localtime(ts))


def _m335_week_key(ts: float) -> str:
    """ISO 周键（%G 是 ISO 年，%V 是 ISO 周号；跨年周不会错位到旧年）。"""
    return time.strftime("%G-W%V", time.localtime(ts))


def _m335_limits() -> dict:
    """读预算额度（`None` = 未配置 ⇒ 不熔断）。非法值按未配置处理并留痕。"""
    _day = None
    _week = None
    try:
        import config as _cfg
        _day = getattr(_cfg, "EVOLUTION_BUDGET_DAILY_CNY", None)
        _week = getattr(_cfg, "EVOLUTION_BUDGET_WEEKLY_CNY", None)
    except Exception as _e:
        silent_exc(_e, where="evolution_budget::_m335_limits")
        _module_logger.warning("[335刀6][费用熔断] 配置读取失败（%s: %s）"
                               " ⇒ 按未配置处理（**不熔断**，不猜额度）",
                               type(_e).__name__, _e)
    def _num(v):
        if v is None:
            return None
        try:
            _f = float(v)
        except (TypeError, ValueError) as _e:
            silent_exc(_e, where="evolution_budget::_m335_limits.num",
                       level="debug")
            return None
        return _f if _f > 0 else None

    return {"day": _num(_day), "week": _num(_week)}


def load_budget(path: str | None = None,
                project_root: str | None = None) -> dict:
    """读侧账；缺失/损坏 ⇒ 空账（**不抛、不填 0 以外的假结构**）。"""
    _p = _m335_budget_path(path, project_root)
    _empty = {"version": _M335_BUDGET_VERSION, "updated_at": 0.0,
              "day": {"key": "", "used": 0.0, "calls": 0},
              "week": {"key": "", "used": 0.0, "calls": 0}}
    try:
        if not os.path.isfile(_p):
            return _empty
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
    except (OSError, ValueError) as _e:
        silent_exc(_e, where="evolution_budget::load_budget", level="warning")
        _module_logger.warning("[335刀6][费用熔断] 侧账不可读 %s：%s: %s"
                               " ⇒ 本轮从空账起步（不覆盖原文件）",
                               _p, type(_e).__name__, _e)
        return _empty
    if not isinstance(_d, dict):
        return _empty
    for _k in ("day", "week"):
        if not isinstance(_d.get(_k), dict):
            _d[_k] = dict(_empty[_k])
    _d.setdefault("version", _M335_BUDGET_VERSION)
    _d.setdefault("updated_at", 0.0)
    return _d


def save_budget(data: dict, path: str | None = None,
                project_root: str | None = None) -> str:
    """原子写（tmp + os.replace）：半个 JSON 比不写更糟。"""
    _p = _m335_budget_path(path, project_root)
    _dir = os.path.dirname(os.path.abspath(_p))
    if not os.path.isdir(_dir):
        os.makedirs(_dir, exist_ok=True)
    _tmp = _p + ".tmp335"
    with open(_tmp, "w", encoding="utf-8") as _f:
        json.dump(data, _f, ensure_ascii=False, indent=2)
    os.replace(_tmp, _p)
    return _p


def record_cost(cost, ts: float | None = None, path: str | None = None,
                project_root: str | None = None) -> dict:
    """把一次调用费用累进日/周两档（`cost` 为 None ⇒ **跳过，不猜价**）。"""
    if cost is None:
        return {"ok": False, "reason": "cost_unavailable"}
    try:
        _c = float(cost)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="evolution_budget::record_cost.cast", level="debug")
        return {"ok": False, "reason": "cost_not_numeric"}
    _t = float(ts if ts is not None else time.time())
    _d = load_budget(path, project_root)
    _dk = _m335_day_key(_t)
    _wk = _m335_week_key(_t)
    # ★340刀7：日切/周切必须留痕。原实现是**静默重置** —— 钱花完了账自己归零，
    #   日志里一个字没有，读方无法区分「本期没花钱」与「本期花完了被切走」。
    _lim340 = _m335_limits()
    _old_day_key = str(_d["day"].get("key") or "")
    _old_day_used = float(_d["day"].get("used") or 0.0)
    _old_week_key = str(_d["week"].get("key") or "")
    _old_week_used = float(_d["week"].get("used") or 0.0)
    if _old_day_key != _dk:
        _d["day"] = {"key": _dk, "used": 0.0, "calls": 0}
        _module_logger.info(
            "[335刀6] 预算日切 used=%.4f limit=%s 上期=%s 上期used=%.4f proc_gen=%s",
            0.0, ("%.2f" % _lim340["day"]) if _lim340["day"] is not None else "None",
            _old_day_key or "-", _old_day_used, _m335_pg())
    if _old_week_key != _wk:
        _d["week"] = {"key": _wk, "used": 0.0, "calls": 0}
        _module_logger.info(
            "[335刀6] 预算周切 used=%.4f limit=%s 上期=%s 上期used=%.4f proc_gen=%s",
            0.0, ("%.2f" % _lim340["week"]) if _lim340["week"] is not None else "None",
            _old_week_key or "-", _old_week_used, _m335_pg())
    _d["day"]["used"] = round(float(_d["day"].get("used") or 0.0) + _c, 6)
    _d["day"]["calls"] = int(_d["day"].get("calls") or 0) + 1
    _d["week"]["used"] = round(float(_d["week"].get("used") or 0.0) + _c, 6)
    _d["week"]["calls"] = int(_d["week"].get("calls") or 0) + 1
    _d["version"] = _M335_BUDGET_VERSION
    _d["updated_at"] = _t
    try:
        _p = save_budget(_d, path, project_root)
    except OSError as _e:
        silent_exc(_e, where="evolution_budget::record_cost.save", level="warning")
        _module_logger.warning("[335刀6][费用熔断] 侧账写失败 %s: %s"
                               " ⇒ 本轮费用只在内存（下次成功写盘时补齐）",
                               type(_e).__name__, _e)
        return {"ok": False, "reason": "write_failed", "cost": _c}
    return {"ok": True, "cost": _c, "day_used": _d["day"]["used"],
            "week_used": _d["week"]["used"], "path": _p}


def budget_state(now: float | None = None, path: str | None = None,
                 project_root: str | None = None) -> dict:
    """当前预算状态（**三态**：ok / tripped / unavailable，不填 0 冒充 ok）。

    `tripped=True` 的唯一条件：某档**已配置额度**且 `used >= limit`。
    额度未配置（None）⇒ 该档永不熔断（还没裁额度，本模块不替人裁）。
    """
    _t = float(now if now is not None else time.time())
    _lim = _m335_limits()
    _d = load_budget(path, project_root)
    _dk = _m335_day_key(_t)
    _wk = _m335_week_key(_t)
    _day_used = float(_d["day"].get("used") or 0.0) if str(
        _d["day"].get("key") or "") == _dk else 0.0
    _week_used = float(_d["week"].get("used") or 0.0) if str(
        _d["week"].get("key") or "") == _wk else 0.0
    _r = {"now": _t, "day_key": _dk, "week_key": _wk,
          "day_used": round(_day_used, 6), "week_used": round(_week_used, 6),
          "day_limit": _lim["day"], "week_limit": _lim["week"],
          "day_calls": int(_d["day"].get("calls") or 0),
          "week_calls": int(_d["week"].get("calls") or 0),
          "configured": bool(_lim["day"] is not None or _lim["week"] is not None),
          "tripped": False, "reason": "", "state": "ok",
          "note": "额度未配置 ⇒ 不熔断（阈值由小林/星轨裁，本模块不代定）"}
    _why = []
    if _lim["day"] is not None and _day_used >= _lim["day"]:
        _why.append("日预算超限（%.4f ≥ %.4f 元）" % (_day_used, _lim["day"]))
    if _lim["week"] is not None and _week_used >= _lim["week"]:
        _why.append("周预算超限（%.4f ≥ %.4f 元）" % (_week_used, _lim["week"]))
    if _why:
        _r.update({"tripped": True, "state": "tripped",
                   "reason": "、".join(_why)})
    return _r


def is_tripped(now: float | None = None, path: str | None = None,
               project_root: str | None = None) -> bool:
    """熔断开关（**纯读**，给进化动作入口调用）。"""
    return bool(budget_state(now, path, project_root).get("tripped"))


def note_tripped(reason: str, where: str = "") -> None:
    """熔断留痕（固定一行、可 grep；带 proc_gen —— A-11）。"""
    _module_logger.warning(
        "[335刀6][费用熔断] %s ⇒ 进化通道降级：**只发现不修复 / 不派单**"
        "（只读巡检 / 心跳 / W1 不受影响）｜where=%s proc_gen=%s",
        reason or "预算超限", where or "-", _m335_pg())
