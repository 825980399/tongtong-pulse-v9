# -*- coding: utf-8 -*-
"""LIF 影子观测（类脑改造 P2 · 第205批 刀2）。

漏积分发放（LIF）膜电位影子观测器。**纯观测、不门控、不参与路由/熔断**。

模型（每个入脉冲）：
    V ← V · exp(-Δt/τ) + w_in
    - Δt 为距上次入脉冲的时间间隔；首次（无上次）Δt 记 0（不衰减）。
    - 阈值 θ 仅记录是否越界（crossed），**不**触发任何门控行为。

载体：
    - V 为标量（当前膜电位）；
    - 采样曲线用每实例 ``collections.deque(maxlen=...)``（append 线程安全、免新锁）。

落盘：
    - 曲线按日落盘 ``data/experiments/lif_shadow_YYYYMMDD.jsonl``（行追加式，
      与 training_pairs 同构先例）；由 flush() 显式触发或每 N 样本自动落盘。
    - flush() 内的文件 IO 失败被静默吸收（silent_exc），**绝不**向上抛入脉冲分发链。

红线（与刀3一致）：
    - 不 import 也不写 ResonanceDetector 的 weight_map（那是刀3实验件的事）；
    - 默认关闭（config.ENABLE_LIF_SHADOW=False），开启需显式灰度；
    - 本模块除 flush() 的 IO 兜底外不含任何静默 except（CI 门禁友好）。
"""
from __future__ import annotations

import json
import math
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque

from nucleus._silent_except import silent_exc

# ---- 票面定值（可被 config 覆盖） ----
DEFAULT_TAU = 60.0          # s，膜电位时间常数
DEFAULT_THETA = 5.0          # 阈值（仅观测记录，不门控）
DEFAULT_W_IN = 1.0           # 每个入脉冲注入权重
DEFAULT_MAXLEN = 10000       # 内存样本上限
DEFAULT_FLUSH_EVERY = 200    # 每 N 样本落盘一次（避免逐脉冲 IO）
DEFAULT_OUT_DIR = "data/experiments"


def lif_voltage_step(v: float | None, dt: float, tau: float, w_in: float) -> float:
    """单步 LIF 膜电位更新：``V ← V·exp(-Δt/τ) + w_in``。

    - ``v is None``（首次）从 0 起累加；
    - ``dt <= 0``（同拍/负间隔）视为不衰减；
    - ``tau <= 0`` 退化为不衰减（防御除零）。
    """
    if v is None:
        v = 0.0
    if dt < 0:
        dt = 0.0
    if tau > 0:
        decay = math.exp(-dt / tau)
    else:
        decay = 1.0
    return v * decay + w_in


class LIFShadow:
    """每器官实例一个 LIF 影子观测器。

    线程安全：deque.append / popleft 为原子操作；落盘走独立短锁，绝不阻塞脉冲分发。
    """

    def __init__(self, tau: float = DEFAULT_TAU, theta: float = DEFAULT_THETA,
                 w_in: float = DEFAULT_W_IN, maxlen: int = DEFAULT_MAXLEN,
                 flush_every: int = DEFAULT_FLUSH_EVERY,
                 out_dir: str = DEFAULT_OUT_DIR) -> None:
        self.tau = tau
        self.theta = theta
        self.w_in = w_in
        self.maxlen = maxlen
        self.flush_every = flush_every
        self.out_dir = out_dir
        self._v: float | None = None
        self._last_ts: float | None = None
        self._count = 0
        self._samples: Deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def observe(self, now_ts: float, organ_name: str, event_type: str) -> float:
        """记录一次入脉冲，返回更新后的 V。纯计算 + 追加，不抛异常。"""
        dt = 0.0 if self._last_ts is None else (now_ts - self._last_ts)
        self._v = lif_voltage_step(self._v, dt, self.tau, self.w_in)
        self._last_ts = now_ts
        self._count += 1
        crossed = self._v >= self.theta
        self._samples.append({
            "ts": now_ts,
            "iso": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
            "organ": organ_name,
            "event_type": event_type,
            "dt": round(dt, 6),
            "v": round(self._v, 6),
            "theta": self.theta,
            "crossed": crossed,
        })
        if self.flush_every and self._count % self.flush_every == 0:
            self.flush()
        return self._v

    def flush(self) -> int:
        """将内存样本落盘（行追加），返回落盘条数；IO 失败静默吸收。"""
        with self._lock:
            batch = list(self._samples)
            self._samples.clear()
        if not batch:
            return 0
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            fname = os.path.join(
                self.out_dir,
                "lif_shadow_%s.jsonl" % datetime.now().strftime("%Y%m%d"),
            )
            with open(fname, "a", encoding="utf-8") as _f:
                for _rec in batch:
                    _f.write(json.dumps(_rec, ensure_ascii=False))
                    _f.write("\n")
        except Exception as e:
            silent_exc(e)
            return 0
        return len(batch)

    def snapshot(self) -> dict[str, Any]:
        """观测器当前状态快照（调试/测试用）。"""
        return {
            "tau": self.tau, "theta": self.theta, "w_in": self.w_in,
            "maxlen": self.maxlen, "count": self._count,
            "v": self._v, "last_ts": self._last_ts,
            "buffered": len(self._samples),
        }

