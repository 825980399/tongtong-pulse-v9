# -*- coding: utf-8 -*-
"""channel_balance.py —— 353 刀1：渠道池「起点均衡」（按并发余量挑起点）

【要解决的问题】
`config.get_llm_call_target()` 恒取 priority 最小的那条（`zhipu-47-flash`，
`max_concurrent=1`）。多器官并行高峰时，所有**单点**调用都挤同一个渠道：首渠道
并发满即 429，而后面几条免费渠道空着。`PulseLung._call_via_channels` 早在第24批
就有「并发满即 `continue`」的循环，但那是**多目标轮询**才有的路径；6 处绕行点
（灵核/知识/控制器/OCR）走的是单目标口，拿不到这个循环。

【本模块只做一件事】
给定「候选行列表」，返回**起点下标**：
从前往后找第一条信号量还有空位的行；找不到就退回 0（首选）。
`reason` 三态：`default` / `concurrency_busy` / `all_busy`。

【两条硬约束】
1. **不推进到付费渠道**。扫描窗口在第一条付费渠道前截止 —— 均衡绝不能把免费调用
   变成付费调用；付费渠道仍只在「前面全失败」时由调用方按原兜底链兜到。
2. **只 peek 不 acquire**。本模块只看 `available()`，不占许可；真正占用仍由调用侧
   `acquire()` 负责。两边都占就是双重占用（铁律181 分泄漏/吊住）。

【依赖关系】
只依赖 `ChannelConcurrency`（纯 stdlib）与 `config.PAID_CHANNELS`，二者都不反向
依赖本模块 ⇒ 无循环导入。日志走 stdlib `logging`，**不 import `nucleus.logger`**
（铁律195：`import nucleus.logger` 即成日志轮转执行者）。
"""
from __future__ import annotations

import logging
from typing import Any

try:
    from nucleus._silent_except import silent_exc
except Exception:  # pragma: no cover - 兜底：独立单测时无项目内依赖
    def silent_exc(e, where="", level="debug"):
        pass

__all__ = ["BALANCE_TAG", "channel_has_room", "pick_balanced_index", "log_pick",
           "LB_TAG", "channel_headroom", "channel_load", "order_by_load",
           "log_order"]

#: 留痕前缀（任务书刀1 规定格式：`[353均衡] caller= picked= reason=`）
BALANCE_TAG = "[353均衡]"

_LOG = logging.getLogger("pulse.llm.balance")


def _manager():
    """取渠道并发管理器单例；不可用返回 None（调用方按「全放行」处理）。"""
    try:
        from nucleus.llm.ChannelConcurrency import get_channel_concurrency_manager
        return get_channel_concurrency_manager()
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::_manager")
        return None


def _paid_channels() -> frozenset:
    """付费渠道口径直接复用 `config.PAID_CHANNELS`（352 定稿），不另立一份。"""
    try:
        import config as _cfg
        _p = getattr(_cfg, "PAID_CHANNELS", frozenset())
        return frozenset(str(x) for x in _p)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::_paid_channels")
        return frozenset()


def channel_has_room(manager: Any, name: str) -> bool:
    """该渠道此刻是否还有并发余量。

    与 `ChannelConcurrencyManager.acquire()` 的「未注册即放行」语义保持一致：
    未注册 / 取不到信号量 ⇒ 视为有裕量（True），否则均衡会把所有未登记渠道
    全判成忙，退回首选 —— 那是配置问题，不该由本模块静默改写。
    """
    if manager is None or not name:
        return True
    try:
        _sem = manager.get_semaphore(name)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::channel_has_room.get_semaphore")
        return True
    if _sem is None:
        return True
    try:
        return int(_sem.available()) > 0
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::channel_has_room.available")
        return True


def pick_balanced_index(rows: list, caller: str = "",
                        paid_channels=None) -> tuple:
    """按并发余量挑起点下标。

    Args:
        rows: `config.get_llm_call_targets()` 形态的行（含 `channel` 键）。
        caller: 仅用于留痕（不参与判定）。

    Returns:
        `(index, reason)`，reason ∈ `no_candidates` / `default` /
        `concurrency_busy` / `all_busy`。
    """
    _n = len(rows or [])
    if _n <= 1:
        return 0, "no_candidates" if _n == 0 else "default"

    _mgr = _manager()
    # 灰度口径复用渠道并发开关：关着就完全保持改造前行为（一开关一语义，铁律165）
    if _mgr is None or not bool(getattr(_mgr, "enabled", True)):
        return 0, "default"

    # 付费集合可由调用方**显式给定**（测试/非标渠道名场景）。不传才回落
    #   config.PAID_CHANNELS —— 否则调用方传入的渠道名一旦不在那个常量里，
    #   均衡不越界付费这条保护会静默失效（354 自测踩到）。
    _paid = _paid_channels() if paid_channels is None else frozenset(
        str(x) for x in (paid_channels or ()))
    _end = _n
    for _i in range(_n):
        if str((rows[_i] or {}).get("channel") or "") in _paid:
            _end = _i
            break
    if _end <= 1:
        # 首选就是付费渠道、或整个池只有付费渠道 ⇒ 不做均衡
        return 0, "default"

    for _i in range(_end):
        if channel_has_room(_mgr, str((rows[_i] or {}).get("channel") or "")):
            return _i, ("concurrency_busy" if _i > 0 else "default")
    return 0, "all_busy"


def log_pick(caller: str, rows: list, index: int, reason: str) -> None:
    """按任务书格式落 `[353均衡] caller= picked= reason=` 留痕。"""
    _picked = ""
    try:
        _picked = str((rows[index] or {}).get("channel") or "")
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::log_pick")
    try:
        _LOG.info("%s caller=%s picked=%s reason=%s",
                  BALANCE_TAG, caller or "-", _picked or "-", reason)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::log_pick.emit")




# ============================================================================
# ★418刀2：多目标主路径的**负载均衡排序**
# ============================================================================
# 353刀1 只解决了单目标口（`get_llm_call_target`）的起点：peek 式 first-fit，
# 找到第一条还有余量的就返回。多目标主路径 `PulseLung._call_via_channels` 没有
# 这一步 —— 它按 priority 顺序试，只要 priority=1 那条还有余量，**所有**调用就
# 都压在它身上；6 条免费渠道的额度/成功率/余量完全不参与决策。
#
# 本段补这一层：三因子打分（并发余量 / 成功率 / 剩余额度）后**只在免费组内重排**。
# 三条硬约束：
#   1. 免费优先、付费兜底 —— 排序绝不把付费渠道提到免费之前（付费组原样垫底）；
#   2. 熔断中的渠道沉到免费组末尾（不删；调用侧 `is_available()` 仍会跳过）；
#   3. 只读现成读数，不新建计数器、不 acquire 任何许可（与 353 同口径）。

#: 留痕前缀（「同一批请求分散到不同渠道」必须在日志里看得见）
LB_TAG = "[418均衡]"

#: 三因子权重（并发余量 / 成功率 / 剩余额度），合计 1.0；改这里即改策略。
LB_W0, LB_W1, LB_W2 = 0.45, 0.35, 0.20

#: 熔断中渠道的排序键（低于任何正常分）
LB_SCORE_BROKEN = -1.0


def _lb_switch_on() -> bool:
    """`ENABLE_CHANNEL_LOAD_BALANCE`（缺省 True；取不到也按 True，与配置一致）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_CHANNEL_LOAD_BALANCE", True))
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::_lb_switch_on")
        return True


def _lb_quota():
    """额度监控单例；不可用返回 None（按「额度未知」处理，不惩罚渠道）。"""
    try:
        from nucleus.llm.ChannelQuotaMonitor import get_channel_quota_monitor
        return get_channel_quota_monitor()
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::_lb_quota")
        return None


def _lb_paid_default() -> frozenset:
    """付费渠道名集合的**默认口径**（与 `PulseLung._m360_paid_names()` 一致）。

    ★口径坑：本模块 353 段用的 `_paid_channels()` 读的是 `config.PAID_CHANNELS`
      （`{"deepseek"}`），而 `PulseLung` 排序用的是 `config.PAID_CHANNEL_NAMES`
      （`["deepseek","advanced"]`）。`advanced` 只在前者缺席 —— 若这里沿用
      `PAID_CHANNELS`，`order_by_load` 会把高级任务渠道当成免费渠道参与重排，
      「付费垫底」这条红线在**默认参数**下静默失效（生产因恒传 paid_names 而未暴露）。
    """
    try:
        import config as _cfg
        _n = getattr(_cfg, "PAID_CHANNEL_NAMES", None)
        if _n:
            return frozenset(str(x) for x in _n)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::_lb_paid_default")
    return _paid_channels()


def _lb_name(row) -> str:
    """候选行取名：`_call_via_channels` 的行用 `name`，`get_llm_call_targets` 用 `channel`。"""
    if not isinstance(row, dict):
        return ""
    return str(row.get("name") or row.get("channel") or "")


def channel_headroom(manager, name: str) -> tuple:
    """该渠道的并发余量比例（0..1）与说明。

    未注册 / 取不到信号量 ⇒ `(1.0, "unregistered")` —— 与
    `ChannelConcurrencyManager.acquire()` 的「未注册即放行」语义一致。
    """
    if manager is None or not name:
        return 1.0, "no_manager"
    try:
        _sem = manager.get_semaphore(name)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::channel_headroom.get")
        return 1.0, "sem_error"
    if _sem is None:
        return 1.0, "unregistered"
    try:
        _max = int(_sem.max_value)
        _avail = int(_sem.available())
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::channel_headroom.read")
        return 1.0, "read_error"
    if _max <= 0:
        return 0.0, "max_zero"
    return max(0.0, min(1.0, float(_avail) / float(_max))), "ok"


def channel_load(name: str, health=None, quota=None, manager=None) -> dict:
    """单渠道负载读数（三因子 + 一个可解释的 score）。

    取不到的读数一律回落**不惩罚**的 1.0，并把原因写进 `why`。
    **绝不写 0** —— 0 会被下游读成「这渠道彻底不可用」，而「未知」不等于「不可用」。
    """
    _why = []
    _hr, _hr_why = channel_headroom(manager, name)
    if _hr_why != "ok":
        _why.append("headroom:" + _hr_why)

    _succ, _broken = 1.0, False
    if health is not None and name:
        try:
            _succ = max(0.0, min(1.0, float(health.success_rate(name))))
        except Exception as e:
            silent_exc(e, where="nucleus.llm.channel_balance::channel_load.success")
            _why.append("health:error")
        try:
            _n = int(health.consecutive_fails(name) or 0)
            if _n:
                _why.append("fails:%d" % _n)
        except Exception as e:
            silent_exc(e, where="nucleus.llm.channel_balance::channel_load.fails")
        try:
            _broken = not bool(health.is_available(name))
        except Exception as e:
            silent_exc(e, where="nucleus.llm.channel_balance::channel_load.avail")

    _rem = 1.0
    if quota is not None and name:
        try:
            _r = quota.get_remaining_ratio(name)
            if _r is not None:
                _rem = max(0.0, min(1.0, float(_r)))
                if _rem < 1.0:
                    _why.append("quota:%.2f" % _rem)
        except Exception as e:
            silent_exc(e, where="nucleus.llm.channel_balance::channel_load.quota")
            _why.append("quota:error")

    _score = (LB_W0 * _hr) + (LB_W1 * _succ) + (LB_W2 * _rem)
    if _broken:
        _score = LB_SCORE_BROKEN
        _why.append("circuit_open")

    _in_use, _maxc = -1, -1
    try:
        if manager is not None and name:
            _sem = manager.get_semaphore(name)
            if _sem is not None:
                _in_use, _maxc = int(_sem.in_use), int(_sem.max_value)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::channel_load.in_use")

    return {"name": name, "headroom": round(_hr, 4), "success": round(_succ, 4),
            "remaining": round(_rem, 4), "broken": _broken, "available": _hr > 0,
            "in_use": _in_use, "max_concurrent": _maxc,
            "score": round(_score, 4), "why": ",".join(_why)}


def order_by_load(candidates: list, health=None, quota=None, manager=None,
                  caller: str = "", paid_names=None) -> tuple:
    """把**免费组**按负载重排；付费组原样垫底。

    Returns:
        `(new_list, info)`；`info = {applied, reason, moved, order, rows, paid_tail}`。
        开关关闭 / 候选 <=1 / 免费组 <=1 ⇒ 原样返回且 `applied=False`
        （`reason` 分别 `switch_off` / `too_few` / `no_free_group`）。
    """
    _rows = list(candidates or [])
    _info = {"applied": False, "reason": "", "moved": False,
             "order": [], "rows": [], "paid_tail": []}
    if not _lb_switch_on():
        _info["reason"] = "switch_off"
        return _rows, _info
    if len(_rows) <= 1:
        _info["reason"] = "too_few"
        return _rows, _info

    try:
        _mgr = manager if manager is not None else _manager()
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::order_by_load.manager")
        _mgr = None
    try:
        _q = quota if quota is not None else _lb_quota()
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::order_by_load.quota")
        _q = None
    _paid = _lb_paid_default() if paid_names is None else frozenset(
        str(x) for x in (paid_names or ()))

    _free, _paid_rows = [], []
    for _c in _rows:
        (_paid_rows if _lb_name(_c) in _paid else _free).append(_c)
    if len(_free) <= 1:
        # ★没有可比的两条免费渠道 ⇒ 不重排，但**仍要**输出「免费在前、付费在后」。
        #   这里若 `return _rows`（原始顺序），「付费兜底」的不变式就交给了调用方；
        #   生产里 360 已排好所以看不出，一旦别处调用就静默破线。
        _info["reason"] = "no_free_group"
        return _free + _paid_rows, _info

    _scored = []
    for _i, _c in enumerate(_free):
        _ld = channel_load(_lb_name(_c), health=health, quota=_q, manager=_mgr)
        # ★稳定排序：分数相同就保持原 priority 顺序（`_i` 是原下标）
        _scored.append((-_ld["score"], _i, _c, _ld))
    _scored.sort(key=lambda x: (x[0], x[1]))

    _new_free = [x[2] for x in _scored]
    _new = _new_free + _paid_rows
    _info["applied"] = True
    _info["reason"] = "ok"
    _info["moved"] = any(_new[_i] is not _free[_i] for _i in range(len(_free)))
    _info["order"] = [_lb_name(x[2]) or "?" for x in _scored]
    _info["rows"] = [x[3] for x in _scored]
    _info["paid_tail"] = [_lb_name(x) or "?" for x in _paid_rows]
    return _new, _info


def log_order(caller: str, info: dict) -> None:
    """`[418均衡] caller= order= paid_tail=` 留痕（含每档三因子读数）。"""
    try:
        _parts = []
        for _ld in (info.get("rows") or []):
            _parts.append("%s(hr=%.2f,ok=%.2f,q=%.2f,score=%.2f)"
                          % (_ld.get("name") or "?", _ld.get("headroom", 0.0),
                             _ld.get("success", 0.0), _ld.get("remaining", 0.0),
                             _ld.get("score", 0.0)))
        _LOG.info("%s caller=%s order=%s | paid_tail=%s | %s",
                  LB_TAG, caller or "-", ">".join(info.get("order") or []) or "-",
                  ">".join(info.get("paid_tail") or []) or "-", " ".join(_parts))
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::log_order")


def pick_and_acquire(rows: list, caller: str = "", paid_channels=None):
    """★354刀4：353刀1 遗留 TOCTOU 风险的**原子**补法。

    353刀1 的 `pick_balanced_index()` 是 **peek**（只看 `available()`，不占许可），
    这是刻意的：单目标口的 6 处调用方（灵核/知识/控制器/OCR）**从不 acquire**，
    若改成 acquire-then-return 却没有配套 release ⇒ 许可永久泄漏（铁律181）。
    代价是存在 TOCTOU 窗口：两个调用可能都选中同一渠道，其中一个 acquire 失败
    ⇒ 回落旧行为（切下一个/429），**不会更差**，但确实"选了又没占到"。

    本函数给**需要严格原子化**的调用方一个正确用法：先占后返，并返回 release 回调。

    Returns:
        `(target_dict, release_callable)`；拿不到许可时 `(None, None)`。
        release 只能调用一次（重复调用由信号量侧容忍，但调用方不应重复）。
    """
    try:
        _idx, _why = pick_balanced_index(rows, caller=caller,
                                        paid_channels=paid_channels)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.channel_balance::pick_and_acquire")
        return None, None
    if not rows or _idx >= len(rows):
        return None, None
    _mgr = _manager()
    if _mgr is None:
        log_pick(caller, rows, _idx, _why)
        return rows[_idx], (lambda: None)
    _name = str((rows[_idx] or {}).get("channel") or "")
    _got = _mgr.acquire(_name, blocking=False)
    if not _got:
        # 占不到 ⇒ **不返回**该目标（否则调用方以为拿到了许可）
        log_pick(caller, rows, _idx, "acquire_failed")
        return None, None
    log_pick(caller, rows, _idx, _why)

    def _release():
        try:
            _mgr.release(_name)
        except Exception as e:
            silent_exc(e, where="nucleus.llm.channel_balance::pick_and_acquire.release")

    return rows[_idx], _release
