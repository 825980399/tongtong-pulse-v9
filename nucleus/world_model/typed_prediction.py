# -*- coding: utf-8 -*-
"""typed_prediction.py —— 第410批刀2：分类型预测（周期 / 计数 / 突发）

契约：410.2

为什么单独一个模块：
    StreamMiner 已经 700 行、且处在每次心跳的热路径上。把「切窗 / 切批 / μ±2σ 区间」
    这类**纯计算**抽出来，一是不给热路径加分支噪音，二是这部分能被单测穷举
    （含空窗、跨窗续批、长空档），不必构造整个 miner。

三种预测型（**来自 periodic_profile 的固定表，不是分类器**）：
    periodic —— 预测「下次什么时候来」：容差 = median_interval × pct（5%/10%）
    count    —— 预测「下一分钟来多少条」：区间 μ±2σ，**不设时刻容差**
    burst    —— 先按 gap>60s 切批，再预测「下一分钟几批」：同样是区间

为什么不给 count 型留时刻容差：
    眼睛帧实测 median 2.0s、p90 3.0s（三个数据源都不是任务书写的 0.5s），
    给它 60s 时刻容差 ⇒ 随便来一帧都算命中（生产实况 32/32 = 100%）。
    改成「一分钟条数落在 μ±2σ」后，只有真的来了那么多条才算命中。

第三态：
    `judge()` 在 lo/hi 为 None（历史不足、档案缺失、窗口来不及登记预测）时返回
    `no_coverage`，**不是** miss。把「查不到」判成 miss 是拿沉默当反证，
    判成 hit 是拿愿望当证据 —— 两者都会让 hit_rate 变成假数。
"""

import math

from nucleus._silent_except import silent_exc

from nucleus.world_model import periodic_profile as pp

#: 计数窗口长度（秒）—— 任务书「下一分钟」，即 60s
WINDOW_SEC = 60.0

#: 突发型切批门槛（秒）—— 任务书「gap>60s 切批」
BATCH_GAP_SEC = 60.0

#: 区间宽度：μ±2σ
SIGMA_K = 2.0

#: 冷启动（历史窗口不足）时用档案速率外推：样本数下限
MIN_HISTORY = 3

#: 每类保留多少个已关闭窗口的历史（供 μ/σ 与自适应）
HISTORY_MAX = 30

#: 单次推进最多关闭多少个空窗（防病态长空档把 CPU 吃满；
#: 超出部分直接跳到当前对齐窗，对应预测由过期扫描判 no_coverage）
MAX_CLOSE = 600

#: 判定三态
VERDICT_HIT = "hit"
VERDICT_MISS = "miss"
VERDICT_NO_COVERAGE = "no_coverage"

#: p90 对应的正态分位（用于把「median→p90 的差距」换算成 σ）
_P90_Z = 1.2816


# --------------------------------------------------------------------- 类型


def is_target(event_type: str) -> bool:
    """是否属于任务书指定的七类（其余类型保持旧 60s 行为，不扩大改动面）。"""
    return event_type in pp.TARGET_EVENTS


def classify(event_type: str) -> str:
    """取预测型。非七类返回 ``""``（调用方据此回落旧行为，不要当 periodic 用）。"""
    if not is_target(event_type):
        return ""
    return pp.predict_type_of(event_type)


def is_window_type(event_type: str) -> bool:
    """是否需要「分钟窗」记账（count / burst）。"""
    return classify(event_type) in ("count", "burst")


# --------------------------------------------------------------------- 窗口


class WindowTracker:
    """把一条事件流切成**对齐**的 W 秒窗口，逐窗给出「条数」与「批数」。

    对齐：窗口起点 = floor(ts / W) * W。这样窗口边界不随第一条事件漂移，
    不同实例、不同重启之间可比。

    批数：`ts - 上一条事件 ts > batch_gap_sec` ⇒ 新起一批。`上一条` **跨窗口保留**，
    所以跨窗口延续的老批不会在新窗口里重复计一次（任务书要的是「下一分钟几批」，
    不是「下一分钟有多少条属于某个批」）。
    """

    __slots__ = ("window_sec", "batch_gap_sec", "max_close",
                 "_start", "_count", "_batches", "_last")

    def __init__(self, window_sec: float = WINDOW_SEC,
                 batch_gap_sec: float = BATCH_GAP_SEC,
                 max_close: int = MAX_CLOSE):
        self.window_sec = float(window_sec)
        self.batch_gap_sec = float(batch_gap_sec)
        self.max_close = int(max_close)
        self._start: float | None = None
        self._count = 0
        self._batches = 0
        self._last: float | None = None

    # -- 内部 --

    def _record(self) -> dict:
        _s = float(self._start or 0.0)
        return {
            "window_start": _s,
            "window_end": _s + self.window_sec,
            "count": int(self._count),
            "batches": int(self._batches),
        }

    def _align(self, ts: float) -> float:
        return math.floor(ts / self.window_sec) * self.window_sec

    def _close_to(self, now: float) -> list[dict]:
        """关闭所有「窗口终点 <= now」的窗口（含空窗，空窗是事实不是缺失）。"""
        _out: list[dict] = []
        if self._start is None:
            return _out
        _guard = 0
        while (self._start + self.window_sec) <= now:
            _out.append(self._record())
            self._start += self.window_sec
            self._count = 0
            self._batches = 0
            _guard += 1
            if _guard >= self.max_close:
                # 空档太长（>max_close 个窗口）：放弃逐窗回放，直接跳到当前对齐窗。
                # 被跳过的窗口没有记录 ⇒ 对应预测由过期扫描判 no_coverage（不猜 0）。
                self._start = self._align(now)
                break
        return _out

    # -- 对外 --

    def feed(self, ts: float) -> list[dict]:
        """喂一个事件时间戳；返回因本次推进而关闭的窗口列表（可能为空）。"""
        _ts = float(ts)
        _closed = self._close_to(_ts)
        if self._start is None:
            self._start = self._align(_ts)
        self._count += 1
        if self._last is None or (_ts - self._last) > self.batch_gap_sec:
            self._batches += 1
        self._last = _ts
        return _closed

    def close_until(self, now: float) -> list[dict]:
        """只推进时间、不喂事件（无事件时段也要把窗口关掉，让空窗可见）。"""
        return self._close_to(float(now))

    def current(self) -> dict:
        """当前未关闭窗口的快照（供报表/排查）。"""
        if self._start is None:
            return {"window_start": None, "window_end": None, "count": 0, "batches": 0}
        return self._record()

    def last_ts(self) -> float | None:
        return self._last


# --------------------------------------------------------------------- 区间


def expected_range(samples: list[int] | list[float],
                   sigma_k: float = SIGMA_K) -> tuple[float | None, int | None, int | None]:
    """由已关闭窗口的历史值算 μ±kσ 区间。

    Returns:
        (expected, lo, hi)；历史不足 MIN_HISTORY 个样本时三者皆 None
        （**不猜**默认区间 —— 冷启动由 `cold_start_range()` 明确承担）。
    """
    _xs: list[float] = []
    for _s in samples or []:
        try:
            _xs.append(float(_s))
        except (TypeError, ValueError) as _exc:
            silent_exc(_exc, where="world_model.typed_prediction.expected_range:L183", level="debug")
            continue
    if len(_xs) < MIN_HISTORY:
        return (None, None, None)
    _n = len(_xs)
    _mu = sum(_xs) / _n
    _var = sum((_x - _mu) ** 2 for _x in _xs) / _n
    _sd = math.sqrt(_var)
    _lo = int(math.floor(_mu - sigma_k * _sd))
    _hi = int(math.ceil(_mu + sigma_k * _sd))
    if _lo < 0:
        _lo = 0
    if _hi < _lo:
        _hi = _lo
    return (round(_mu, 2), _lo, _hi)


def cold_start_range(median_interval_sec: float | None,
                     p90_interval_sec: float | None,
                     window_sec: float = WINDOW_SEC,
                     sigma_k: float = SIGMA_K) -> tuple[float | None, int | None, int | None]:
    """历史窗口不足时的冷启动区间：用档案速率外推。

    期望条数 μ ≈ window / median_interval；
    单间隔 σ ≈ (p90 - median) / 1.2816（正态分位近似），
    窗口 σ ≈ 单间隔 σ × sqrt(μ)（把 μ 条独立间隔的方差相加）。

    ★这是**近似**，不是统计保证：只用于「还没有窗口历史」的头三分钟，
    一旦攒够 MIN_HISTORY 就切到 `expected_range()` 的实测 μ±2σ。
    """
    try:
        _med = float(median_interval_sec or 0.0)
    except (TypeError, ValueError) as _exc:
        silent_exc(_exc, where="world_model.typed_prediction.cold_start_range:L215", level="debug")
        return (None, None, None)
    if _med <= 0:
        return (None, None, None)
    _mu = float(window_sec) / _med
    _sd = 0.0
    try:
        _p90 = float(p90_interval_sec or 0.0)
    except (TypeError, ValueError) as _exc:
        silent_exc(_exc, where="world_model.typed_prediction.cold_start_range:L223", level="debug")
        _p90 = 0.0
    if _p90 > _med:
        _sd = (_p90 - _med) / _P90_Z
    _sd_win = _sd * math.sqrt(max(_mu, 1.0))
    _lo = int(math.floor(_mu - sigma_k * _sd_win))
    _hi = int(math.ceil(_mu + sigma_k * _sd_win))
    if _lo < 0:
        _lo = 0
    if _hi < _lo:
        _hi = _lo
    return (round(_mu, 2), _lo, _hi)


def rate_range(stamps: list[float] | list[object],
               window_sec: float = WINDOW_SEC, sigma_k: float = SIGMA_K,
               batch: bool = False, batch_gap_sec: float = BATCH_GAP_SEC,
               min_span_sec: float | None = None) -> tuple[float | None, int | None, int | None]:
    """由**原始时间戳**估「每窗口条数/批数」区间（冷启动第二层）。

    为什么不能沿用 `cold_start_range()`：
        突发型的间隔分布是**双峰**的（批内间隔 ~2s、批间空档几百秒），
        把 p90 当正态分位换 σ 会得到「0~272 批/分钟」这种谁都命中的区间
        —— 那就退回了「怎么猜都中」。所以这里按「窗口计数 ≈ 泊松」直接由速率推：
        μ = N × window / span，σ = sqrt(μ)（泊松：计数方差 = 均值）。

    已知偏差（如实记录）：缓存里第一条事件可能处在**批中间**，把它当批首会
    多算 1 批。批数越少偏差相对越大，故本层只做冷启动，攒够窗口历史即切走。
    """
    _xs: list[float] = []
    for _s in stamps or []:
        try:
            _xs.append(float(_s))
        except (TypeError, ValueError) as _exc:
            silent_exc(_exc, where="world_model.typed_prediction.rate_range:L256", level="debug")
            continue
    _xs.sort()
    if len(_xs) < MIN_HISTORY:
        return (None, None, None)
    _span = _xs[-1] - _xs[0]
    if _span <= 0:
        return (None, None, None)
    # 跨度不足一个窗口就不许外推：3 条事件跨 4 秒外推成「每分钟 15 批」，
    # 是把小样本当分布用（实测踩过）。
    _min_span = float(window_sec if min_span_sec is None else min_span_sec)
    if _span < _min_span:
        return (None, None, None)
    _n = float(batch_count(_xs, batch_gap_sec) if batch else len(_xs))
    _mu = _n * float(window_sec) / _span
    _sd = math.sqrt(max(_mu, 0.0))
    _lo = int(math.floor(_mu - sigma_k * _sd))
    _hi = int(math.ceil(_mu + sigma_k * _sd))
    if _lo < 0:
        _lo = 0
    if _hi < _lo:
        _hi = _lo
    return (round(_mu, 2), _lo, _hi)


def point_of(mu: float | None) -> int | None:
    """把期望值取整成点预测（burst 型用）。mu 缺失 ⇒ None。"""
    if mu is None:
        return None
    try:
        return int(round(float(mu)))
    except (TypeError, ValueError) as _exc:
        silent_exc(_exc, where="world_model.typed_prediction.point_of:L287", level="debug")
        return None


def judge_point(actual: float | None, expected: float | None) -> str:
    """点判三态：实际 == 取整后的 expected ⇒ hit；expected 缺失 ⇒ no_coverage。

    ★为什么 burst 用点判而不是区间：60s 窗内批数恒 ∈ {0,1}（切批门槛也是 60s），
    区间判据必然恒中。点判至少是「这一分钟会不会起新批」的非平凡二分。
    """
    _e = point_of(expected)
    if _e is None or actual is None:
        return VERDICT_NO_COVERAGE
    try:
        _a = int(round(float(actual)))
    except (TypeError, ValueError) as _exc:
        silent_exc(_exc, where="world_model.typed_prediction.judge_point:L302", level="debug")
        return VERDICT_NO_COVERAGE
    return VERDICT_HIT if _a == _e else VERDICT_MISS


def judge(actual: float | None, lo: float | None, hi: float | None) -> str:
    """区间判定三态。lo/hi 缺失或 actual 缺失 ⇒ no_coverage（不是 miss）。"""
    if lo is None or hi is None or actual is None:
        return VERDICT_NO_COVERAGE
    try:
        _a = float(actual)
        _lo = float(lo)
        _hi = float(hi)
    except (TypeError, ValueError) as _exc:
        silent_exc(_exc, where="world_model.typed_prediction.judge:L315", level="debug")
        return VERDICT_NO_COVERAGE
    return VERDICT_HIT if _lo <= _a <= _hi else VERDICT_MISS


def window_of(ts: float, window_sec: float = WINDOW_SEC) -> float:
    """时间戳所属窗口的起点（对齐到 window_sec 整数倍）。"""
    return math.floor(float(ts) / float(window_sec)) * float(window_sec)


def next_window_start(now: float, window_sec: float = WINDOW_SEC) -> float:
    """下一个「尚未开始」的窗口起点 —— 预测只能登记在还没发生的窗口上。"""
    return window_of(now, window_sec) + float(window_sec)


def batch_count(stamps: list[float], gap_sec: float = BATCH_GAP_SEC) -> int:
    """离线切批计数（单测/工具用；在线走 WindowTracker）。"""
    _xs = sorted(float(s) for s in stamps or [])
    if not _xs:
        return 0
    _n = 1
    for _i in range(1, len(_xs)):
        if (_xs[_i] - _xs[_i - 1]) > gap_sec:
            _n += 1
    return _n


def version() -> str:
    return "410.2"
