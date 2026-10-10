# -*- coding: utf-8 -*-
"""181批刀3：自我认知 · 器官健康排行榜（观测级 · 身体层 v0 代理）。

定位：
  - 基于**既有** HealthScore/PulseHealthMonitor 之外的「器官实例运行态」聚合健康分；
  - 不依赖器官关联图谱（身体层 v0 代理）；
  - **不打开 INFLUENCE_DECISION 总开关**：本模块只观测、只记日志，不影响任何决策。

健康分 v0 口径（可后续替换）：
  100.0 = 运行中且无异常信号
   60.0 = 已实例化但未在运行
   40.0 = 有实例但取不到运行态
   None  = 静态清单中存在、但当前进程未装配（未加载）=> 记 "未装配"

数据来源：
  - 运行时：传入的器官实例映射（{类名或器官名: 实例}），取 get_stats() 的 is_running
  - 静态兜底：nucleus.self_inspector.SelfInspector.scan_all_organs()（保证器官全覆盖）

零行为变化：ENABLE_ORGAN_HEALTH_LEADERBOARD 默认 False -> on_heartbeat() 直接返回。
"""
from __future__ import annotations

import time
from typing import Any

from nucleus.logger import get_module_logger

_module_logger = get_module_logger("OrganHealthLeaderboard")

# 健康分常量（v0）
SCORE_RUNNING = 100.0
SCORE_IDLE = 60.0
SCORE_UNKNOWN = 40.0

_MARK_HEALTHY = 90.0


def _leaderboard_on() -> bool:
    """读取总开关（默认 False -> 零行为变化）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_ORGAN_HEALTH_LEADERBOARD", False))
    except Exception as _e:
        _module_logger.debug("[181刀3] 开关读取失败，按关闭处理: %s: %s", type(_e).__name__, _e)
        return False


def _static_organ_names() -> list[str]:
    """静态器官清单（★与刀5 共用确定性 AST 扫描，organ_name 口径，保证全覆盖）。

    ★不使用 self_inspector.scan_all_organs()：实测其 key 是**类名**（非 organ_name），
      且带 300s 自适应降频（二次调用返回 {}）→ 会导致全覆盖断言失效。
    """
    try:
        from nucleus.cognitive.organ_inventory import organ_names_all
        return organ_names_all()
    except Exception as _e:
        _module_logger.debug("[181刀3] 静态器官清单获取失败: %s: %s", type(_e).__name__, _e)
        return []


class OrganHealthLeaderboard:
    """器官健康排行榜（观测级）。"""

    def __init__(self, organ_source: Any = None) -> None:
        # organ_source: 可调用对象（返回器官映射）或 器官映射本身
        self._source = organ_source
        self._last_report: list[dict[str, Any]] = []
        self._last_ts: float = 0.0

    # ---------- 采集 ----------
    def _resolve_organs(self, organs: Any = None) -> dict[str, Any]:
        if organs is not None:
            return organs if isinstance(organs, dict) else {}
        if callable(self._source):
            try:
                _r = self._source()
                return _r if isinstance(_r, dict) else {}
            except Exception as _e:
                _module_logger.debug("[181刀3] 器官源调用失败: %s: %s", type(_e).__name__, _e)
                return {}
        if isinstance(self._source, dict):
            return self._source
        return {}

    def _score_of(self, organ: Any) -> float | None:
        """单个器官健康分（v0：基于 get_stats 的 is_running）。"""
        _stats = None
        try:
            _gs = getattr(organ, "get_stats", None)
            if callable(_gs):
                _stats = _gs()
        except Exception as _e:
            _module_logger.debug("[181刀3] get_stats 失败: %s: %s", type(_e).__name__, _e)
            return SCORE_UNKNOWN
        if not isinstance(_stats, dict):
            return SCORE_UNKNOWN
        _running = _stats.get("is_running", None)
        if _running is True:
            return SCORE_RUNNING
        if _running is False:
            return SCORE_IDLE
        return SCORE_UNKNOWN

    def collect(self, organs: Any = None) -> list[dict[str, Any]]:
        """聚合全部器官健康分（静态清单兜底 => 全覆盖）。"""
        _mapping = self._resolve_organs(organs)
        # 运行时：key 可能是类名；器官实例自带 organ_name
        _rows: list[dict[str, Any]] = []
        _seen: set[str] = set()
        for _key, _inst in _mapping.items():
            _name = str(getattr(_inst, "organ_name", "") or _key)
            _rows.append({
                "organ": _name,
                "key": str(_key),
                "score": self._score_of(_inst),
                "assembled": True,
            })
            _seen.add(_name)

        # 静态兜底：未装配的器官也进榜（score=None，标记未装配）=> 全覆盖
        for _n in _static_organ_names():
            if _n in _seen:
                continue
            _rows.append({
                "organ": _n,
                "key": _n,
                "score": None,
                "assembled": False,
            })

        _rows.sort(key=lambda r: (r["organ"],))
        self._last_report = _rows
        self._last_ts = time.time()
        return _rows

    # ---------- 排行 ----------
    def rank(self, organs: Any = None) -> list[dict[str, Any]]:
        """健康分降序排行（未装配 score=None 排末尾）。"""
        _rows = self.collect(organs) if organs is not None or not self._last_report else list(self._last_report)
        _rows = sorted(
            _rows,
            key=lambda r: ((-1.0 if r["score"] is None else -float(r["score"])), r["organ"]),
        )
        return _rows

    # ---------- 输出 ----------
    def report_text(self, top_n: int = 10) -> str:
        _ranked = self.rank()
        _total = len(_ranked)
        _assembled = sum(1 for r in _ranked if r["assembled"])
        _lines = [
            f"[器官健康排行榜] 总器官={_total} 已装配={_assembled} "
            f"未装配={_total - _assembled}"
        ]
        for _i, _r in enumerate(_ranked[:top_n], 1):
            _s = _r["score"]
            _s_txt = "未装配" if _s is None else f"{_s:.1f}"
            _lines.append(f"  {_i}. {_r['organ']}: {_s_txt}")
        return "\n".join(_lines)

    def log_report(self, top_n: int = 10) -> None:
        """日志输出（★验收：排行榜日志可 grep）。"""
        _module_logger.info("%s", self.report_text(top_n))

    # ---------- 心跳入口 ----------
    def on_heartbeat(self, organs: Any = None) -> None:
        """心跳周期入口（★开关关闭时零行为）。"""
        if not _leaderboard_on():
            return
        self.collect(organs)
        self.log_report()

    # ---------- 供自我认知引擎消费 ----------
    def as_dimension_payload(self, organs: Any = None) -> dict[str, Any]:
        """作为自我认知引擎「器官维度」数据源。"""
        _ranked = self.rank() if organs is None else self.rank(organs)
        _scores = [float(r["score"]) for r in _ranked if r["score"] is not None]
        _avg = (sum(_scores) / len(_scores)) if _scores else 0.0
        return {
            "organ_count": len(_ranked),
            "assembled_count": sum(1 for r in _ranked if r["assembled"]),
            "avg_health": round(_avg, 2),
            "top": [{"organ": r["organ"], "score": r["score"]} for r in _ranked[:10]],
            "ts": self._last_ts,
        }


def get_organ_health_leaderboard(organ_source: Any = None) -> OrganHealthLeaderboard:
    return OrganHealthLeaderboard(organ_source=organ_source)
