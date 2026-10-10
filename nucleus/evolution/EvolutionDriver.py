# -*- coding: utf-8 -*-
"""
EvolutionDriver.py —— 进化驱动器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 驱动框架自主进化循环
机制: 基于EvolutionDriver类实现，包含10个核心方法
定位: 进化核心层
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.const import LogLevel
from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
from nucleus.evolution.HealthScore import HealthScore
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


class EvolutionDriver(SilentLogMixin):
    """健康度驱动的自主进化触发器。"""

    # 基线快照文件（相对 project_root/data/evolution/）
    _BASELINE_FILE = "health_baseline.json"

    def __init__(self, project_root: str, weights: dict[str, float] | None = None):
        self._project_root = project_root
        self._health = HealthScore(project_root, weights=weights)
        self._data_dir = os.path.join(project_root, "data", "evolution")
        os.makedirs(self._data_dir, exist_ok=True)
        self._baseline_path = os.path.join(self._data_dir, self._BASELINE_FILE)
        # 运行时防抖：上次评估时间戳
        self._last_evaluate_at = 0.0
        # ★v25.1: 评估计数器——每N次评估强制触发一次全面进化（不依赖健康度下降）
        self._eval_count = 0
        # ★7-1/P1-13：计数原子化（评估入口可能被外部线程触发）
        self._counter_lock = threading.Lock()

    # ========== 配置读取 ==========

    def _cfg(self) -> dict[str, Any]:
        try:
            import config
            return dict(getattr(config, "EVOLUTION_CONFIG", {}))
        except Exception:
            return {}

    # ========== 健康度评估 ==========

    def evaluate(self, runtime_metrics: dict[str, Any] | None = None,
                 system_snapshot: dict[str, Any] | None = None,
                 log_file: str | None = None,
                 force: bool = False) -> dict[str, Any]:
        """
        评估当前健康度，并返回是否应触发主动进化。

        返回:
            {
                "score": 当前健康度分,
                "grade": 等级,
                "dimensions": 各维度明细,
                "baseline": 历史基线分（无则为 None）,
                "delta": 相对基线的变化（正=回升，负=下降）,
                "should_evolve": 是否应主动触发进化,
                "trigger_reason": 触发原因（""/drop/danger/force）,
                "dimensions_weak": 低于 60 分的薄弱维度列表（供定向排查）,
            }
        """
        _cfg = self._cfg()
        _interval = float(_cfg.get("health_evaluate_interval", 1800))
        _now = time.time()

        # 防抖：非强制时，评估间隔内不重复评估
        if not force and (_now - self._last_evaluate_at) < _interval:
            return {
                "score": None, "grade": "", "dimensions": {},
                "baseline": None, "delta": 0.0,
                "should_evolve": False, "trigger_reason": "cooldown",
                "dimensions_weak": [],
            }
        self._last_evaluate_at = _now
        # ★v25.1: 评估计数（用于定期强制进化）
        # ★7-1/P1-13：原子化
        with self._counter_lock:
            self._eval_count += 1

        _result = self._health.score(
            log_file=log_file,
            runtime_metrics=runtime_metrics,
            system_snapshot=system_snapshot,
        )
        _score = float(_result["score"])
        _baseline = self._load_baseline()
        _delta = round(_score - _baseline, 1) if _baseline is not None else 0.0

        # 薄弱维度（< 60 分），供定向排查
        _weak = [
            _dim for _dim, _info in _result["dimensions"].items()
            if _info.get("score", 100.0) < 60.0
        ]

        # 触发判定
        _should = False
        _reason = ""
        _drop_threshold = float(_cfg.get("health_drop_trigger_threshold", 8.0))
        _danger_threshold = float(_cfg.get("health_danger_threshold", 55.0))
        _enabled = bool(_cfg.get("health_driven_evolution_enabled", True))
        # ★v25.1: 定期强制进化间隔（评估次数），默认4次=2小时（评估间隔1800秒）
        _periodic_interval = int(_cfg.get("periodic_evolution_interval", 4))

        if force:
            _should = True
            _reason = "force"
        elif not _enabled:
            _should = False
            _reason = "disabled"
        elif _baseline is not None and _delta <= -_drop_threshold:
            _should = True
            _reason = "drop"
        elif _score <= _danger_threshold:
            _should = True
            _reason = "danger"
        elif _periodic_interval > 0 and self._eval_count > 0 and self._eval_count % _periodic_interval == 0:
            # ★v25.1: 定期强制全面审查——框架稳定运行时也持续自我优化
            _should = True
            _reason = f"periodic(第{self._eval_count}次评估)"
        else:
            _should = False
            _reason = ""

        return {
            "score": _score,
            "grade": _result["grade"],
            "dimensions": _result["dimensions"],
            "baseline": _baseline,
            "delta": _delta,
            "should_evolve": _should,
            "trigger_reason": _reason,
            "dimensions_weak": _weak,
        }

    # ========== 基线管理 ==========

    def update_baseline(self, score: float, dimensions: dict[str, Any] | None = None,
                        source: str = "") -> None:
        """更新健康度基线（进化生效或初次评估时调用）。"""
        _data = {
            "score": round(float(score), 1),
            "dimensions": {k: v.get("score", 0.0) for k, v in (dimensions or {}).items()},
            "source": source,
            "updated_at": time.time(),
        }
        try:
            safe_write_json(self._baseline_path, _data, indent=2)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def _load_baseline(self) -> float | None:
        if not os.path.exists(self._baseline_path):
            return None
        try:
            _data = safe_read_json(self._baseline_path, default={})
            _score = _data.get("score")
            return float(_score) if _score is not None else None
        except Exception as e:
            print(f"[WARNING] EvolutionDriver.py:168: {type(e).__name__}: {e}")
            return None

    def get_baseline(self) -> dict[str, Any] | None:
        """读取完整基线数据（含各维度明细）。"""
        if not os.path.exists(self._baseline_path):
            return None
        try:
            return safe_read_json(self._baseline_path, default={})
        except Exception as e:
            print(f"[WARNING] EvolutionDriver.py:177: {type(e).__name__}: {e}")
            return None

    # ========== 进化有效性判定（目标函数闭环） ==========

    def judge_evolution(self, baseline_score: float | None = None,
                        runtime_metrics: dict[str, Any] | None = None,
                        system_snapshot: dict[str, Any] | None = None,
                        log_file: str | None = None) -> dict[str, Any]:
        """
        判定一次补丁应用后的进化是否「有效」。

        对比「应用前基线」与「应用后当前」的健康度：
        - 回升超过确认阈值 → 有效（保留补丁，更新基线）
        - 持平或小幅波动 → 中性（保留，但不更新基线，继续观察）
        - 继续下降 → 无效（应回退）

        返回:
            {
                "score": 当前健康度,
                "baseline": 基线健康度（可为 None）,
                "delta": 变化,
                "effective": True/False/None,
                "should_rollback": bool,
                "should_update_baseline": bool,
            }
        """
        _cfg = self._cfg()
        _confirm_threshold = float(_cfg.get("health_recover_confirm_threshold", 3.0))

        _result = self._health.score(
            log_file=log_file,
            runtime_metrics=runtime_metrics,
            system_snapshot=system_snapshot,
        )
        _score = float(_result["score"])

        _base = baseline_score if baseline_score is not None else self._load_baseline()
        _delta = round(_score - _base, 1) if _base is not None else 0.0

        _effective: bool | None
        _should_rollback = False
        _should_update_baseline = False

        if _base is None:
            # 无基线，无法判断；视为中性，回写基线
            _effective = None
            _should_update_baseline = True
        elif _delta >= _confirm_threshold:
            _effective = True
            _should_update_baseline = True
        elif _delta <= -_confirm_threshold:
            _effective = False
            _should_rollback = True
        else:
            _effective = None  # 中性，继续观察

        return {
            "score": _score,
            "baseline": _base,
            "delta": _delta,
            "effective": _effective,
            "should_rollback": _should_rollback,
            "should_update_baseline": _should_update_baseline,
            "dimensions": _result["dimensions"],
            "grade": _result["grade"],
        }

    def mark_evolution_applied(self, score: float, dimensions: dict[str, Any] | None = None) -> None:
        """补丁应用并重启后，记录「应用前」基线快照（供重启后对比）。"""
        self.update_baseline(score, dimensions, source="pre_apply")

    # ========== 主动应用请求标记（「验证通过即应用」闭环） ==========

    _APPLY_FLAG_FILE = "apply_now.json"

    def request_apply_now(self, approved_count: int) -> bool:
        """
        写入「立即应用补丁」请求标记，由主循环周期性检测后执行应用+重启。
        这打通了「补丁验证通过即应用」的断点——不再依赖框架退出时机。

        返回是否写入成功。
        """
        _flag_path = os.path.join(self._data_dir, self._APPLY_FLAG_FILE)
        _data = {
            "requested_at": time.time(),
            "approved_count": approved_count,
            "consumed": False,
        }
        try:
            with open(_flag_path, "w", encoding="utf-8") as _f:
                json.dump(_data, _f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.EvolutionDriver::request_apply_now L279")
            return False

    def has_apply_request(self) -> bool:
        """检查是否有未消费的「立即应用」请求。"""
        _flag_path = os.path.join(self._data_dir, self._APPLY_FLAG_FILE)
        if not os.path.exists(_flag_path):
            return False
        try:
            _data = safe_read_json(_flag_path, default={})
            return not _data.get("consumed", False)
        except Exception as e:
            print(f"[WARNING] EvolutionDriver.py:281: {type(e).__name__}: {e}")
            return False

    def consume_apply_request(self) -> dict[str, Any] | None:
        """消费「立即应用」请求（主循环调用前标记已消费，返回请求详情）。"""
        _flag_path = os.path.join(self._data_dir, self._APPLY_FLAG_FILE)
        if not os.path.exists(_flag_path):
            return None
        try:
            _data = safe_read_json(_flag_path, default={})
            _data["consumed"] = True
            _data["consumed_at"] = time.time()
            with open(_flag_path, "w", encoding="utf-8") as _f:
                json.dump(_data, _f, ensure_ascii=False, indent=2)
            return _data
        except Exception as e:
            print(f"[WARNING] EvolutionDriver.py:296: {type(e).__name__}: {e}")
            return None


# ========== 便捷函数 ==========

def get_evolution_driver(project_root: str) -> EvolutionDriver:
    """获取全局 EvolutionDriver 单例（按 project_root 缓存）。"""
    return EvolutionDriver(project_root)


if __name__ == "__main__":
    # 自测：评估当前项目健康度
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _driver = EvolutionDriver(_root)
    _r = _driver.evaluate(force=True)
    print(f"健康度: {_r['score']} 分 ({_r['grade']}), 基线={_r['baseline']}, delta={_r['delta']}")
    print(f"是否触发进化: {_r['should_evolve']} ({_r['trigger_reason']})")
    print(f"薄弱维度: {_r['dimensions_weak']}")
