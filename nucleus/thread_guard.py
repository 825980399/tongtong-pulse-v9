# -*- coding: utf-8 -*-
"""374刀9 · 线程引导滞留观测装置（只观测、不介入）。

【为什么需要】
CPython 的 `Thread.start()` 在**新线程引导完成前不返回**：它内部要等 `_started`
事件，而该事件由新线程的 `_bootstrap_inner` 置位。若引导期卡住（资源枯竭、
bootstrap 里取 GIL 失败、OS 线程创建被挂起……），**调用方线程会一直卡在 start() 里**，
整个装配路径就此停摆——373 批实测过 6h23m 级别的停放。

【373 丁票两个坑，本件一条都不踩】
· 坑1 **不能用 `threading.Timer` 做超时**：`Timer` 自己也是 `Thread.start()`，
  引导期一起卡死，超时永远不会到点。
· 坑2 **不能用 `is_alive()` 判引导期**：`start()` 返回前 `is_alive()` 恒为 False，
  分不清「还没轮到它」与「卡死在引导」；而且 `start()` 一旦卡住，调用方连
  `is_alive()` 都调不到（它自己就在里面）。

【本件的判据：`thread.ident is None`】
`ident` 由 OS 线程真正开始执行 `_bootstrap_inner` 时写入。于是：
  「登记时刻 + timeout 之后 `ident` 仍为 None」 ⇒ 引导期滞留。
这个判据只需要一个**旁观者**（已经跑起来的看门狗线程）去读，不依赖被观测线程
自己配合，也不需要 join 它。看门狗在启动期预建（`ensure_watchdog()`），常驻轮询。

【能力边界（必须写明，免得被当成"能治"）】
Python 无法安全地杀掉一个卡在引导期的线程，本装置**只能让"卡住"可见**：
登记表条数、滞留快照（名字/目标/超时/已等秒数）、以及每次 start() 的真实引导耗时。
PulseLiver 的 T-107a 资源采样另按「OS 线程数 − Python 活跃线程数」看**持续增量**——
两条观测互补：本件看"单次引导卡没卡"，采样看"进程里有没有攒下看不见的线程"。
"""
import logging
import threading
import time

from nucleus._silent_except import silent_exc

_LOG = logging.getLogger("pulse.thread_guard")

#: 引导期滞留判定的默认超时（秒）。**硬编码常量**——本批不新增 config 开关。
SAFE_START_TIMEOUT_SEC = 5.0
#: 看门狗轮询间隔（秒）。滞留是秒级事件，1s 分辨率足够，且不占 CPU。
WATCHDOG_POLL_SEC = 1.0
#: 滞留快照最多保留条数（只留痕，不无限增长）。
MAX_STUCK_KEPT = 32
#: 引导耗时样本最多保留条数（滚动窗口）。
MAX_BOOT_SAMPLES = 32
#: 「OS 线程数 − Python 活跃线程数」比基线**突增**多少算可疑。
THREAD_GAP_BUMP = 1
#: 突增要**连续几个采样点**才算"持续"（避免单点抖动误报）。
THREAD_GAP_PERSIST_SAMPLES = 2
#: 看门狗线程名（面板/日志按名字找得到）。
WATCHDOG_NAME = "ThreadGuardWatchdog"

_LOCK = threading.Lock()
#: id(thread) -> {name, target, t0, timeout, thread, reported}
_PENDING: dict[int, dict] = {}
#: 滞留快照（有界）
_LAST_STUCK: list[dict] = []
#: 每次 start() 的真实引导耗时（毫秒，有界滚动）
_BOOT_SAMPLES: list[float] = []
_WD_THREAD: threading.Thread | None = None
_WD_FAILED = ""


# ------------------------------------------------------------------ 观测数据
def pending_count() -> int:
    """当前仍在引导中（登记了但 start() 没返回）的线程数。"""
    with _LOCK:
        return len(_PENDING)


def stuck_snapshot() -> list[dict]:
    """最近判定为「引导期滞留」的快照副本（按发现顺序）。"""
    with _LOCK:
        return [dict(x) for x in _LAST_STUCK]


def boot_samples() -> list[float]:
    """最近若干次 `safe_start` 的引导耗时（毫秒）。"""
    with _LOCK:
        return list(_BOOT_SAMPLES)


def watchdog_state() -> dict:
    """看门狗自身状态（供面板/测试判读；不留空字符串冒充"正常"）。"""
    return {
        "started": _WD_THREAD is not None,
        "name": WATCHDOG_NAME,
        "failed": _WD_FAILED or None,
    }


def reset_state() -> None:
    """清空登记表/快照/样本（测试与重启窗复位用；生产路径不调用）。"""
    with _LOCK:
        _PENDING.clear()
        _LAST_STUCK.clear()
        _BOOT_SAMPLES.clear()


# -------------------------------------------------------------- 引导滞留判定
def scan_once(now: float | None = None) -> list[dict]:
    """扫一遍登记表，返回**本次新发现**的滞留条目（已报过的不重复返回）。

    判据只有一条：`ident is None` 且已等超过该线程登记的 timeout。
    已 start() 成功的线程 ident 必非 None，因此**正在运行但业务卡住**的线程
    不会被误判（`test_03` 用 Event 永久阻塞的线程钉住这一点）。
    """
    _now = time.time() if now is None else float(now)
    _fresh: list[dict] = []
    with _LOCK:
        _items = list(_PENDING.items())
    for _tid, _rec in _items:
        _t = _rec.get("thread")
        if _t is None or _t.ident is not None:
            continue
        _waited = _now - float(_rec.get("t0") or _now)
        if _waited <= float(_rec.get("timeout") or SAFE_START_TIMEOUT_SEC):
            continue
        if _rec.get("reported"):
            continue
        _rec["reported"] = True
        _snap = {
            "name": _rec.get("name") or "",
            "target": _rec.get("target") or "",
            "timeout_s": float(_rec.get("timeout") or 0.0),
            "waited_s": round(_waited, 3),
            "pending": len(_items),
        }
        with _LOCK:
            _LAST_STUCK.append(_snap)
            if len(_LAST_STUCK) > MAX_STUCK_KEPT:
                del _LAST_STUCK[: len(_LAST_STUCK) - MAX_STUCK_KEPT]
        _LOG.warning(
            "[374刀9] 线程引导滞留：name=%s target=%s 已等 %.1fs > 超时 %.1fs"
            "（引导期卡在 Thread.start()，无法安全终止；见登记表 %d 条）",
            _snap["name"], _snap["target"], _snap["waited_s"], _snap["timeout_s"],
            _snap["pending"])
        _fresh.append(_snap)
    return _fresh


def _watchdog_loop() -> None:
    """常驻轮询：每秒扫一次登记表。异常只留痕、绝不退出（否则观测面自己先瞎）。"""
    while True:
        time.sleep(WATCHDOG_POLL_SEC)
        try:
            scan_once()
        except Exception as _e:
            silent_exc(_e, where="thread_guard::_watchdog_loop", level="warning")


def ensure_watchdog() -> bool:
    """启动期预建看门狗（幂等）。返回是否已在跑。

    ★前提必须说明：**看门狗自己**也是 `Thread.start()` 起的，若它正好卡在引导，
    就没有任何旁观者能判定——这是"必须先有一个能跑的旁观者"的设备前提，不是漏洞。
    故本函数应在装配早期调用（main.py 启动段，LivenessWatchdog 之后），
    而不是等到要用 `safe_start` 时才懒起。
    """
    global _WD_THREAD, _WD_FAILED
    if _WD_THREAD is not None:
        return True
    try:
        _t = threading.Thread(target=_watchdog_loop, name=WATCHDOG_NAME, daemon=True)
        _t.start()
    except Exception as _e:
        _WD_FAILED = f"{type(_e).__name__}: {_e}"
        silent_exc(_e, where="thread_guard::ensure_watchdog", level="warning")
        return False
    _WD_THREAD = _t
    return True


# ------------------------------------------------------------ safe_start 入口
def safe_start(target, name="anon", timeout=SAFE_START_TIMEOUT_SEC,
               args=(), kwargs=None, daemon=True) -> threading.Thread:
    """起一条受观测的线程：先登记再 `start()`，引导耗时与滞留都由看门狗判。

    与裸起线程（`Thread(...).start()`）的差别只有观测，语义一致：
    返回同一个 Thread 对象；`start()` 抛错则清理登记并把异常原样抛出。
    """
    _t = threading.Thread(target=target, name=name, args=args,
                          kwargs=dict(kwargs or {}), daemon=daemon)
    _rec = {
        "name": name,
        "target": getattr(target, "__qualname__", None) or getattr(target, "__name__", str(target)),
        "t0": time.time(),
        "timeout": float(timeout),
        "thread": _t,
        "reported": False,
    }
    with _LOCK:
        _PENDING[id(_t)] = _rec
    _t0 = time.perf_counter()
    try:
        _t.start()
    except BaseException as _e:
        # 起不来（如 "can't start new thread"）：撤登记，免得看门狗把"根本没起"
        # 当成"卡在引导"。异常原样上抛，调用方自己决定降级。
        with _LOCK:
            _PENDING.pop(id(_t), None)
        silent_exc(_e, where="thread_guard::safe_start", level="warning")
        raise
    _boot_ms = round((time.perf_counter() - _t0) * 1000.0, 3)
    if _t.ident is None:
        # ★「start() 返回」并不等于「引导完成」——判据只认 ident。此处**不撤登记**，
        #   留给看门狗按 timeout 判滞留（撤了就等于假设了不该假设的东西）。
        return _t
    with _LOCK:
        _PENDING.pop(id(_t), None)
        _BOOT_SAMPLES.append(_boot_ms)
        if len(_BOOT_SAMPLES) > MAX_BOOT_SAMPLES:
            del _BOOT_SAMPLES[: len(_BOOT_SAMPLES) - MAX_BOOT_SAMPLES]
    return _t


# ------------------------------------------------- 线程数落差（T-107a 侧消费）
def gap_delta(os_threads, py_threads=None) -> int | None:
    """OS 线程数 − Python 活跃线程数。OS 数拿不到 ⇒ None（**不填 0**）。

    为什么两个数要分开看：Python 侧 `active_count()` 只数"已知的 Thread 对象"，
    OS 侧 `num_threads()` 数的是真实线程。差值持续为正 ⇒ 有线程在 OS 层存在
    却不在 Python 视野里（典型：引导期滞留、或 C 扩展自起的线程）。
    """
    if os_threads is None:
        return None
    _py = int(threading.active_count()) if py_threads is None else int(py_threads)
    try:
        return int(os_threads) - _py
    except (TypeError, ValueError) as _e:
        silent_exc(_e, where="thread_guard::gap_delta", level="debug")
        return None


def next_gap_state(prev: dict | None, gap: int | None) -> dict:
    """推进「突增 ≥1 且持续」状态机（纯函数，便于单测）。

    返回 `{base, persist, delta, warn}`：
      · 首次拿到 gap ⇒ 建基线，不告警（没有"参照"就谈不上突增）；
      · `gap - base >= THREAD_GAP_BUMP` ⇒ persist+1，连续达到
        `THREAD_GAP_PERSIST_SAMPLES` 才 `warn=True`（单点抖动不算）；
      · 回落（delta < BUMP）⇒ persist 归 0，基线保留（不追着下跌改参照）；
      · `gap is None` ⇒ delta=None、persist 原样带回、不告警（拿不到 ≠ 正常）。
    """
    _p = dict(prev or {})
    if gap is None:
        return {"base": _p.get("base"), "persist": int(_p.get("persist") or 0),
                "delta": None, "warn": False}
    if _p.get("base") is None:
        return {"base": int(gap), "persist": 0, "delta": 0, "warn": False}
    _delta = int(gap) - int(_p["base"])
    _persist = int(_p.get("persist") or 0) + 1 if _delta >= THREAD_GAP_BUMP else 0
    return {"base": int(_p["base"]), "persist": _persist, "delta": _delta,
            "warn": _persist >= THREAD_GAP_PERSIST_SAMPLES}
