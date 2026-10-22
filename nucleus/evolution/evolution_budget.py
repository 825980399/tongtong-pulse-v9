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
        _day_c = getattr(_cfg, "EVOLUTION_BUDGET_DAILY_CALLS", None)
        _week_c = getattr(_cfg, "EVOLUTION_BUDGET_WEEKLY_CALLS", None)
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

    return {"day": _num(_day), "week": _num(_week),
            "day_calls": _num(_day_c), "week_calls": _num(_week_c)}


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


#: ★343刀5：三本分账（source 维度）——进化 / 对话 / 工具。
#   为什么按 origin 分：`call_recorder` 的 `origin` 是"这笔钱是谁花的"的**唯一**
#   显式字段（user_query=对话 / evolution_task=进化 / system_internal=工具类自检）。
#   改前只有日/周两档合计，回答不了"进化用了多少"。
#   ★与熔断的关系：熔断**只看"进化"这一本**（只拦花钱的进化动作，
#   不影响对话与只读巡检）——所以分账不是报表装饰，是熔断口径的前提。
_M343_BOOKS_VERSION = "343.1"
_M343_BOOKS = {"evolution": "evolution_task", "dialog": "user_query",
               "tool": "system_internal"}


def _m343_book_of(origin: str) -> str:
    """origin ⇒ 三本分账之一（未知 origin 记 `other`，不硬塞进工具本）。"""
    _o = str(origin or "")
    for _bk, _org in _M343_BOOKS.items():
        if _o == _org:
            return _bk
    if _o == "digestion":
        return "tool"      # 知识消化属后台任务（非进化、非对话），归工具本
    return "other"


def _m343_blank_coverage(day_key: str = "") -> dict:
    """coverage 空结构（**不填假数**：全 0 表示"还没统计"，由 calls 判别）。

    ★343刀3（任务书更新版）：核心字段是 `calls_unpriced`
      = `free_zero` + `unpriced`（0 单价 + 未定价，两类都"没有金额可记"）。
    `rate_paid` 与 `rate_known` 双口径：
      · `rate_paid`  = priced / calls        ← 现有熔断真正约束到的比例
      · `rate_known` = (priced+free_zero+no_tokens) / calls  ← 有单价键的比例
    两者之差就是"知道价但不计费"的那部分（免费档），**必须可见**。
    """
    return {"day_key": day_key, "calls": 0, "priced": 0, "free_zero": 0,
            "unpriced": 0, "no_model": 0, "no_tokens": 0, "table_missing": 0,
            "calls_unpriced": 0, "unpriced_models": {}}


def _m343_touch_books(d: dict, day_key: str, origin: str, amount) -> tuple:
    """把一次调用记进三本分账（`amount` 可为 0.0；**条数一律 +1**）。

    ★为什么金额允许缺、条数不许缺：熔断看金额，运营看条数。
      未定价渠道没有金额可累（不猜价），但它**确实发生了一次调用**——
      条数漏记会让"每本调用量"系统性偏低，进而让 coverage 的分母失真。
    """
    _book = _m343_book_of(origin) if origin else "other"
    _books = d.get("books")
    if not isinstance(_books, dict) or str(_books.get("day_key") or "") != day_key:
        _books = {"version": _M343_BOOKS_VERSION, "day_key": day_key,
                  "evolution": {"used": 0.0, "calls": 0},
                  "dialog": {"used": 0.0, "calls": 0},
                  "tool": {"used": 0.0, "calls": 0},
                  "other": {"used": 0.0, "calls": 0}}
    _b = _books.get(_book)
    if not isinstance(_b, dict):
        _b = {"used": 0.0, "calls": 0}
    _b["used"] = round(float(_b.get("used") or 0.0) + float(amount or 0.0), 6)
    _b["calls"] = int(_b.get("calls") or 0) + 1
    _books[_book] = _b
    _books["version"] = _M343_BOOKS_VERSION
    _books["day_key"] = day_key
    d["books"] = _books
    _module_logger.info(
        "[343分账] book=%s day_used=%.6f calls=%d origin=%s proc_gen=%s",
        _book, _b["used"], _b["calls"], origin or "-", _m335_pg())
    return _book, _b


def _m343_touch_coverage(d: dict, day_key: str, cost, basis: str, model: str,
                         path=None, project_root=None) -> dict:
    """更新侧账里的 `coverage`（只改内存 `d`，落盘由 record_cost 统一做）。

    口径（与 343刀3 同源）：`rate = (priced + free) / calls`
      —— **free 计入"有价"**：0.0 元是确定的花费，不是缺口。
    未定价渠道**不参与跳闸计算**（没有金额可累），但必须进这里，
    否则"熔断器读不到花费"这件事在账上永远看不见（缺口要可见）。
    """
    _cov = d.get("coverage")
    if not isinstance(_cov, dict) or str(_cov.get("day_key") or "") != day_key:
        _cov = _m343_blank_coverage(day_key)
    _cov["calls"] = int(_cov.get("calls") or 0) + 1
    _b = str(basis or "")
    _bump = {
        "priced": "priced", "no_tokens": "no_tokens", "free_zero": "free_zero",
        "unpriced": "unpriced", "no_model": "no_model",
        "table_missing": "table_missing",
    }.get(_b)
    if _bump is None:
        # basis 缺失（老调用方）⇒ 按 cost 是否可判补一次
        _bump = "priced" if cost is not None else "unpriced"
    _cov[_bump] = int(_cov.get(_bump) or 0) + 1
    # ★343刀3 核心口径：0 单价与未定价**都**进 calls_unpriced（计数不计费）
    if _bump in ("free_zero", "unpriced"):
        _cov["calls_unpriced"] = int(_cov.get("calls_unpriced") or 0) + 1
        _um = _cov.get("unpriced_models")
        if not isinstance(_um, dict):
            _um = {}
        _mid = str(model or "<empty>")
        _um[_mid] = int(_um.get(_mid) or 0) + 1
        _cov["unpriced_models"] = _um
        _n_now = int(_um[_mid])
        # 留痕：首次 + 每 100 次一条（避免每拍刷屏，但计数始终在涨）
        if _n_now == 1 or _n_now % 100 == 0:
            _tag = "0单价" if _bump == "free_zero" else "未定价"
            _module_logger.warning(
                "[343单价] 未定价: %s 计数=%d 档=%s（0 单价/未定价 ⇒ 不计费、"
                "不进跳闸金额；须看 calls_unpriced/全量口径） proc_gen=%s",
                _mid, _n_now, _tag, _m335_pg())
    _n = int(_cov["calls"])
    _cov["rate_paid"] = round(int(_cov["priced"]) / _n, 4) if _n else None
    _cov["rate_known"] = round(
        (int(_cov["priced"]) + int(_cov["free_zero"]) + int(_cov["no_tokens"])) / _n,
        4) if _n else None
    # 兼容旧键（342/343 早版报表读 `rate`）：语义 = 有金额覆盖率
    _cov["rate"] = _cov["rate_paid"]
    d["coverage"] = _cov
    return _cov


def record_cost(cost, ts: float | None = None, path: str | None = None,
                project_root: str | None = None, *, model: str = "",
                basis: str = "", origin: str = "") -> dict:
    """把一次调用费用累进日/周两档（`cost` 为 None ⇒ **不累金额，但记缺口**）。

    ★343刀3：`cost is None` 不再"直接返回"——那样侧账只看得见金额、看不见
    **为什么没金额**。现在无论有没有价都先记一条 coverage（按 basis 分档）：
      · `priced` / `free` ⇒ 累金额（free 的金额就是 0.0）
      · `unpriced`        ⇒ 不累 + 首次出现留痕 `[343单价] 未定价: <model>`
      · `no_model`        ⇒ 不累 + 计入 no_model（model 未透传）
      · `no_tokens`       ⇒ 不累 + 计入 no_tokens（有单价没用量）
    """
    if cost is None:
        # 缺口记账：金额累不了，但**为什么累不了**必须落盘（否则 coverage 永远空）
        _d0 = load_budget(path, project_root)
        _dk0 = _m335_day_key(float(ts if ts is not None else time.time()))
        try:
            _cov0 = _m343_touch_coverage(_d0, _dk0, None, basis, model,
                                         path, project_root)
            # ★343刀3：没钱可累也要进分账（条数不能漏，金额记 0.0）
            _m343_touch_books(_d0, _dk0, origin, 0.0)
            _d0["version"] = _M335_BUDGET_VERSION
            _d0["updated_at"] = float(ts if ts is not None else time.time())
            save_budget(_d0, path, project_root)
        except OSError as _e0:
            silent_exc(_e0, where="evolution_budget::record_cost.coverage",
                       level="warning")
            return {"ok": False, "reason": "cost_unavailable"}
        return {"ok": False, "reason": "cost_unavailable",
                "coverage": _cov0}
    try:
        _c = float(cost)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="evolution_budget::record_cost.cast", level="debug")
        return {"ok": False, "reason": "cost_not_numeric"}
    _t = float(ts if ts is not None else time.time())
    _d = load_budget(path, project_root)
    _dk = _m335_day_key(_t)
    _wk = _m335_week_key(_t)
    # ★343刀5：三本分账（source 维度）——每本独立 used/calls，互不污染。
    #   结构：{"version": "343.1", "day_key": "...", "evolution": {...},
    #          "dialog": {...}, "tool": {...}, "other": {...}}
    _m343_touch_books(_d, _dk, origin, _c)
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
    _cov = _m343_touch_coverage(_d, _dk, cost, basis, model, path, project_root)
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
          # ★343刀3：缺口可见 —— 覆盖率与未定价渠道随状态一起出，
          #   避免"熔断没跳"被读成"没花钱"（可能是压根没记上）。
          "cost_coverage": (_d.get("coverage") if isinstance(_d.get("coverage"), dict)
                            else _m343_blank_coverage()),
          "books": (_d.get("books") if isinstance(_d.get("books"), dict) else {}),
          # ★343刀3 跳闸双写：付费口径（金额）与全量口径（调用次数）各出两值。
          #   `tripped_*` 用 None 表示"该口径未配置额度 ⇒ 不可判"，
          #   绝不填 False（False 会被读成"判过了、没超"，铁律166）。
          "tripped": False, "tripped_paid": False, "tripped_all": None,
          "tripped_basis": "", "calls_paid": 0, "calls_all": 0,
          "calls_unpriced": 0,
          "reason": "", "state": "ok",
          "note": "额度未配置 ⇒ 不熔断（阈值由小林/星轨裁，本模块不代定）"}
    # ---- 量口径读数（全量：含免费/未定价调用；金额口径看不到这些）----
    _cov_d = _d.get("coverage") if isinstance(_d.get("coverage"), dict) else {}
    _cov_ok = str(_cov_d.get("day_key") or "") == _dk
    _calls_all = int(_cov_d.get("calls") or 0) if _cov_ok else 0
    _calls_unp = int(_cov_d.get("calls_unpriced") or 0) if _cov_ok else 0
    _calls_paid = int(_d["day"].get("calls") or 0) if str(
        _d["day"].get("key") or "") == _dk else 0
    _why = []
    _why_all = []
    if _lim["day"] is not None and _day_used >= _lim["day"]:
        _why.append("日预算超限（%.4f ≥ %.4f 元）" % (_day_used, _lim["day"]))
    if _lim["week"] is not None and _week_used >= _lim["week"]:
        _why.append("周预算超限（%.4f ≥ %.4f 元）" % (_week_used, _lim["week"]))
    if _lim["day_calls"] is not None and _calls_all >= _lim["day_calls"]:
        _why_all.append("日调用量超限（%d ≥ %d 次·全量口径）"
                        % (_calls_all, int(_lim["day_calls"])))
    if _lim["week_calls"] is not None and _calls_all >= _lim["week_calls"]:
        _why_all.append("周调用量超限（%d ≥ %d 次·全量口径）"
                        % (_calls_all, int(_lim["week_calls"])))
    _tripped_paid = bool(_why)
    _tripped_all = (None if (_lim["day_calls"] is None
                             and _lim["week_calls"] is None)
                    else bool(_why_all))
    _r.update({"tripped_paid": _tripped_paid, "tripped_all": _tripped_all,
               "calls_paid": _calls_paid, "calls_all": _calls_all,
               "calls_unpriced": _calls_unp})
    _basis = []
    if _tripped_paid:
        _basis.append("paid")
    if _tripped_all is True:
        _basis.append("all")
    _r["tripped_basis"] = "+".join(_basis)
    if _basis:
        _r.update({"tripped": True, "state": "tripped",
                   "reason": "、".join(_why + _why_all)})
    elif _tripped_all is None and not _tripped_paid:
        _r["note"] = ("金额口径未超限；**量口径未配置额度 ⇒ 不可判**"
                      "（免费/未定价调用 %d 次不进金额账，见 cost_coverage）"
                      % _calls_unp)
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
