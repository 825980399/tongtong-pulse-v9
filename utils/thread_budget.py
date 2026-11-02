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

【426 刀1 扩面】418 那版只覆盖 6 个池（合计 29），而库内还有 6 个池完全没进预算表：
  `external_executor`  ExtOp = max(8, min(32, cpu*2)) = **32** ／ ExtOpSched = 2
  `StructuredParallelScheduler`  StructParallel = **16**（硬编码）
  `parallel_scheduler`  cortex_parallel = min(64, 物理核*1.5) = **9**（自适应可翻倍到 18）
  `SelfAwarenessEngine`  SelfAwarenessPool = 2 ／ `PulseLegs`  legs_learn = 3
这 6 个池上限合计 62~65，但**大多永远用不满**（外层并发闸门只有 2）⇒ 按实测排队重定后
合计 29，全表 29+29 = 58 < 60。同时新增只读 `inventory()`，把「每个池上限多少、
实际起了几个线程、队列里排了几个」直接读出来，用于改后验收。
`ENABLE_M418_THREAD_SLIMMING=False` 时 `cap()` 原样返回，逐字回到改前行为。
"""
from __future__ import annotations

from nucleus._silent_except import silent_exc

__all__ = [
    "CAPS_ATTR",
    "ENABLE_ATTR",
    "POOL_PREFIXES",
    "budget_total",
    "cap",
    "caps",
    "inventory",
    "register",
    "slimming_on",
]

#: 开关名（config 属性）
ENABLE_ATTR = "ENABLE_M418_THREAD_SLIMMING"
#: 预算表名（config 属性）：池名 -> 上限
CAPS_ATTR = "M418_THREAD_CAPS"

#: 缺省预算（config 缺该键时用这里）。数值来源见模块头：
#: 合计 1+6+6+4+4+8 = 29，改前同口径 1+8+8+4+8+32 = 61 ⇒ 上限合计降 32。
#: 活体实测 89~90 个线程里 81 个是池 worker，全部压到上限后理论值 ≈ 89-32 = 57 < 60。
DEFAULT_CAPS = {
    # —— 418 刀3② 已接入（分层池 + 混合 IO 池）——
    "l0": 1,
    "l1": 6,
    "l2": 6,
    "l3": 4,
    "adaptive": 4,
    "hybrid_io": 8,
    # —— 426 刀1 新接入（这 6 个此前完全没有预算约束）——
    # ext_op：外部操作执行池。原 32，但外层 _SCHEDULER_EXECUTOR 只有 2 并发、
    #   `_max_concurrent` 默认也是 2 ⇒ 内层并发物理上限≈2，8 已是 4 倍余量。
    "ext_op": 8,
    # ext_sched：外部操作外层调度池。原就是 2，与 _max_concurrent 对齐，不动。
    "ext_sched": 2,
    # struct_parallel：任务组扇出池。原硬编码 16；实测单组扇出峰值 ≤6，
    #   且它下面还有 Hybrid 池兜并行，6 足够（保留 1 组满载不排队）。
    "struct_parallel": 6,
    # cortex_parallel：思维并行池。原 9（16 核 max(2,int(16*0.6))），自适应可翻倍到 18；
    #   压到 8 并**在每次建池时**过 cap()，翻倍路径也一起受约束。
    "cortex_parallel": 8,
    # self_awareness：分析器池。原 2，是「少一条分析就少一条结论」的池，不砍。
    "self_awareness": 2,
    # legs_learn：多路学习池。原 3（最多 3 路并行学习），属后台任务型，不砍。
    "legs_learn": 3,
}

#: 池名 -> 线程名前缀（用于从 `threading.enumerate()` 反查**实际**起了几个线程）。
#: ★前缀有包含关系（`ExtOp` 是 `ExtOpSched` 的前缀），匹配时按长度倒序，先具体后笼统。
POOL_PREFIXES = {
    "l0": ("L0-Life",),
    "l1": ("L1-RealTime",),
    "l2": ("L2-Cognitive",),
    "l3": ("L3-Autonomous",),
    "adaptive": ("AdaptiveTask", "Adaptive", "-Adaptive"),
    "hybrid_io": ("HybridIO",),
    "ext_op": ("ExtOp",),
    "ext_sched": ("ExtOpSched",),
    "struct_parallel": ("StructParallel",),
    "cortex_parallel": ("cortex_parallel",),
    "self_awareness": ("SelfAwarenessPool",),
    "legs_learn": ("legs_learn",),
}

#: 已登记的池执行器（弱引用，避免把池钉在内存里）。name -> [weakref, ...]
_REGISTRY: dict = {}
_REGISTRY_LOCK = None


def _lock():
    global _REGISTRY_LOCK
    if _REGISTRY_LOCK is None:
        import threading as _t
        _REGISTRY_LOCK = _t.Lock()
    return _REGISTRY_LOCK


def register(name: str, executor) -> None:
    """登记一个池执行器（只为 inventory() 读数，不影响调度）。"""
    try:
        import weakref as _wr
        _r = _wr.ref(executor)
    except Exception as _e:
        silent_exc(_e, where="utils.thread_budget::register")
        return
    try:
        with _lock():
            _REGISTRY.setdefault(str(name), []).append(_r)
    except Exception as _e:
        silent_exc(_e, where="utils.thread_budget::register/append")


def inventory() -> dict:
    """只读清点：每个池的预算上限 / 实际线程数 / 队列深度。

    ★不新建线程、不触发任何池初始化 —— 只读 `threading.enumerate()` 与
      `executor._threads` / `_work_queue.qsize()`。
    """
    import threading as _t

    _caps = caps()
    _seq = []
    for _k, _prefixes in POOL_PREFIXES.items():
        for _p in _prefixes:
            _seq.append((_p, _k))
    # 长前缀优先，避免 ExtOpSched 被算进 ext_op
    _seq.sort(key=lambda _x: -len(_x[0]))

    _live = dict.fromkeys(_caps, 0)
    _other = {}
    for _th in _t.enumerate():
        _nm = getattr(_th, "name", "") or ""
        for _p, _k in _seq:
            if _nm.startswith(_p):
                _live[_k] = _live.get(_k, 0) + 1
                break
        else:
            _other[_nm.split("(")[0].strip() or "<unnamed>"] =                 _other.get(_nm.split("(")[0].strip() or "<unnamed>", 0) + 1

    _pools = []
    for _k in sorted(_caps):
        _mw = None
        _q = None
        _regs = []
        try:
            with _lock():
                _regs = list(_REGISTRY.get(_k, ()))
        except Exception as _e:
            silent_exc(_e, where="utils.thread_budget::inventory/registry")
        for _r in _regs:
            _ex = _r()
            if _ex is None:
                continue
            try:
                _mw = (_mw or 0) + int(getattr(_ex, "_max_workers", 0) or 0)
                _q = (_q or 0) + int(_ex._work_queue.qsize())
            except Exception as _e:
                silent_exc(_e, where="utils.thread_budget::inventory/probe")
        _pools.append({
            "pool": _k,
            "cap": int(_caps[_k]),
            "threads": int(_live.get(_k, 0)),
            "max_workers": _mw,
            "queue": _q,
            # 线程数顶到预算上限 = 这个池可能已经在排队，属于「不能砍」的信号
            "at_cap": bool(_live.get(_k, 0) >= int(_caps[_k])),
        })
    return {
        "slimming": slimming_on(),
        "budget_total": budget_total(),
        "threading_active": _t.active_count(),
        "pools": _pools,
        "unbudgeted": _other,
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
