# -*- coding: utf-8 -*-
"""utils/thread_budget.py —— 418 刀3②：常驻线程上限的**唯一**收敛点。

【为什么要一个统一入口】
改前线程上限散在三处公式里，没人能回答「这台机器上到底允许起多少个常驻线程」：
  · `HybridParallelScheduler`: `min(64, cpu*2)`            → 16 核机器 = 32
  · `InfoField` 分层池:        L1=cpu//2, L2=cpu//2,
                              L3=cpu//4, Adaptive=cpu//2   → 16 核机器 = 8/8/4/8
  · 其余各器官零散 `Thread(...)`
实测活体 `threading.active_count()=89~90`（告警阈值 50），其中 **81 个是
`concurrent.futures` 的线程池 worker**（13:44 的 faulthandler 转储逐块统计：
44 + 37），业务常驻线程只有约 20 个 —— 所以收敛只能从**池上限**下手。

【口径】
本模块只做「把某个池的上限压到预算内」这一件事，**不新建线程、不改调度**。
`ENABLE_M418_THREAD_SLIMMING=False` 时 `cap()` 原样返回，逐字回到改前行为。
"""
from __future__ import annotations

from nucleus._silent_except import silent_exc

__all__ = ["ENABLE_ATTR", "CAPS_ATTR", "cap", "caps", "budget_total", "slimming_on"]

#: 开关名（config 属性）
ENABLE_ATTR = "ENABLE_M418_THREAD_SLIMMING"
#: 预算表名（config 属性）：池名 -> 上限
CAPS_ATTR = "M418_THREAD_CAPS"

#: 缺省预算（config 缺该键时用这里）。数值来源见模块头：
#: 合计 1+6+6+4+4+8 = 29，改前同口径 1+8+8+4+8+32 = 61 ⇒ 上限合计降 32。
#: 活体实测 89~90 个线程里 81 个是池 worker，全部压到上限后理论值 ≈ 89-32 = 57 < 60。
DEFAULT_CAPS = {
    "l0": 1,
    "l1": 6,
    "l2": 6,
    "l3": 4,
    "adaptive": 4,
    "hybrid_io": 8,
}


def _cfg():
    try:
        import config as _c
        return _c
    except Exception as _e:
        silent_exc(_e, where="utils.thread_budget::_cfg")
        return None


def slimming_on() -> bool:
    """总开关（缺省 True；config 取不到也按 True —— 与配置默认值一致）。"""
    _c = _cfg()
    if _c is None:
        return True
    try:
        return bool(getattr(_c, ENABLE_ATTR, True))
    except Exception as _e:
        silent_exc(_e, where="utils.thread_budget::slimming_on")
        return True


def caps() -> dict:
    """当前生效的预算表（config 覆盖缺省，逐键合并）。"""
    _out = dict(DEFAULT_CAPS)
    _c = _cfg()
    if _c is not None:
        try:
            _extra = getattr(_c, CAPS_ATTR, None)
            if isinstance(_extra, dict):
                for _k, _v in _extra.items():
                    try:
                        _iv = int(_v)
                    except (TypeError, ValueError) as _e:
                        silent_exc(_e, where="utils.thread_budget::caps/int")
                        continue
                    if _iv > 0:
                        _out[str(_k)] = _iv
        except Exception as _e:
            silent_exc(_e, where="utils.thread_budget::caps")
    return _out


def cap(name: str, value: int) -> int:
    """把 `value` 压到 `name` 的预算内；不在预算表里/开关关闭 ⇒ 原样返回。

    ★只压不放：预算比原值大时不会把线程数**调高**（那会变成另一种事故）。
    """
    try:
        _v = int(value)
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="utils.thread_budget::cap")
        return value
    if not slimming_on():
        return _v
    _c = caps().get(str(name))
    if _c is None:
        return _v
    return min(_v, max(1, int(_c)))


def budget_total() -> int:
    """预算表合计（用于判据：必须 < 60）。"""
    return sum(int(v) for v in caps().values())
