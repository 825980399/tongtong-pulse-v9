# -*- coding: utf-8 -*-
"""
SurvivalOrchestrator.py —— 生存编排器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架生存策略编排与资源优先级调度
机制: 基于SurvivalSnapshot类实现，包含10个核心方法
定位: 生存管理层
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import (  # ★P0-1: _log签名兼容兜底

    SilentLogMixin,
    coerce_log_level,
)

# ★第九批 B-3：置信度证据化——由「硬编码常数」改为
#   0.9 × 该类型历史成功率系数 × 证据强度系数（开关关闭时原值返回）
from nucleus.reasoning.SelfCalibrator import evidence_confidence as _evidence_conf

# ===== 阶段一预埋的数据结构与钩子（保持不变，向后兼容） =====


@dataclass
class SurvivalSnapshot:
    """存续状态快照（统一封装 get_existential_state 输出）。"""

    index: int = 50
    level: str = "medium"
    mode_switched: bool = False
    indicators: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    stable_duration: float = 0.0

    @classmethod
    def from_existential_state(cls, state: dict[str, Any]) -> SurvivalSnapshot:
        if not state or not isinstance(state, dict):
            return cls()
        return cls(
            index=int(state.get("index", 50)),
            level=str(state.get("level", "medium")),
            mode_switched=bool(state.get("mode_switched", False)),
            indicators=dict(state.get("indicators", {}) or {}),
            scores=dict(state.get("scores", {}) or {}),
            timestamp=float(state.get("timestamp", 0.0) or 0.0),
            stable_duration=float(state.get("stable_duration", 0.0) or 0.0),
        )

    @property
    def is_low(self) -> bool:
        return self.level == "low" or self.index < 40

    @property
    def is_high(self) -> bool:
        return self.level == "high" and self.index >= 80

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "level": self.level,
            "mode_switched": self.mode_switched,
            "indicators": self.indicators,
            "scores": self.scores,
            "timestamp": self.timestamp,
            "stable_duration": self.stable_duration,
        }


class SurvivalHookMixin:
    # TODO: 预留接口，待未来功能使用（五层次生存动作钩子，当前未接入）
    """五层次代表器官的统一定稿钩子接口。

    阶段二填充逻辑时，只改方法体，不改函数签名、不改协议。
    """

    def on_survival_low(self, snapshot: SurvivalSnapshot) -> dict[str, Any]:
        """存续低位动作。返回 dict 用于反馈回写（第二批启用），默认返回空。"""
        return {}

    def on_survival_high(self, snapshot: SurvivalSnapshot) -> dict[str, Any]:
        """存续高位动作。返回 dict 用于反馈回写（第二批启用），默认返回空。"""
        return {}


# ===== 全局开关与常量 =====
# ★R4阶段二治理：总开关已收编到 config.FEATURE["survival_orchestrator_enabled"]（唯一真源）。
# 此处保留模块级常量仅作「向后兼容的缓存引用」，实际判定统一走 _orchestrator_enabled() 动态读取，
# 从而支持热重载（修改 data/config_override.json 无需重启即可切换）。
SURVIVAL_ORCHESTRATOR_ENABLED = False


def _orchestrator_enabled() -> bool:
    """动态读取存续编排器总开关（唯一真源 = config.FEATURE）。

    支持热重载：config 热重载机制会原子替换 FEATURE 字典，此处每次心跳动态读取，
    因此修改 data/config_override.json 中的 survival_orchestrator_enabled 无需重启即可生效。

    向后兼容：若 config 读取失败（config 未导入/键缺失），回退到模块级常量。
    """
    try:
        import config as _cfg
        return bool(_cfg.FEATURE.get("survival_orchestrator_enabled", False))
    except Exception:
        return SURVIVAL_ORCHESTRATOR_ENABLED

LOG_TAG = "[SurvivalOrchestrator]"

# 双阻尼参数（定稿）
INDEX_THRESHOLD = 8          # 阈值抖动过滤：±8 分
COOLDOWN_SECONDS = 1200      # 时间冷却：20 分钟 = 1200 秒
HIGH_UNLOCK_CONSECUTIVE = 2  # 创造层解锁需连续 2 次 high（第二批用）
DETECT_EVERY_BEATS = 5       # 每 5 拍检测一次

# 高位保护性限流（定稿约束4）
HIGH_ACTION_COOLDOWN = 60    # 高位动作最小间隔（秒）


class SurvivalOrchestrator(SilentLogMixin):
    """存续编排器（完整实现）。

    职责：统一感知存续状态 → 强序串行分发五层次动作 → 收集反馈（第二批）→ 再感知。
    原则：只做「读 + 分发 + 收集」，不替代各器官原有逻辑；异常自动降级。
    """

    # 强序串行顺序（定稿：生命→协调→智慧→创造→精神）
    _LAYER_ORDER = (
        "life",           # 生命层：PulseHeart + R2自我保存
        "coordination",   # 协调层：控制器/调度/节流
        "wisdom",         # 智慧层：双腿/内在世界/潜意识
        "creation",       # 创造层：SelfInspector/代码学习/知识重构
        "spirit",         # 精神层：精神宪法/自我叙事
    )

    def __init__(self, get_existential_state: Callable[[], dict[str, Any]] | None = None):
        self._get_existential_state = get_existential_state
        self._layer_organs: dict[str, Any] = {}

        # 双阻尼状态
        self._last_index: int = 50
        self._last_action_time: float = 0.0

        # 状态锁死机制（定稿约束1）
        self._state_locked: bool = False
        self._lock_until: float = 0.0
        self._locked_level: str = ""

        # 高位限流（定稿约束4）
        self._last_high_action_time: float = 0.0

        # 心跳计数
        self._beat_count: int = 0

        # 连续高位计数（决策3：解锁用，第二批）
        self._consecutive_high: int = 0

        # 降级状态
        self._degraded: bool = False
        self._consecutive_errors: int = 0
        self._max_consecutive_errors = 3

        self._lock = threading.Lock()

    # ========== 注册五层次代表器官 ==========

    def register_layer(self, layer: str, organ: Any) -> None:
        """注册某个层次的代表器官。organ 需实现 on_survival_low/high。"""
        if layer in self._LAYER_ORDER:
            self._layer_organs[layer] = organ

    def set_existential_provider(self, provider: Callable[[], dict[str, Any]]) -> None:
        """注入存续状态提供函数（通常是 self_awareness.get_existential_state）。"""
        self._get_existential_state = provider

    # ========== 心跳驱动入口 ==========

    def on_heartbeat(self, beat_count: int) -> None:
        """心跳驱动：每 DETECT_EVERY_BEATS 拍执行一次状态检测。

        由 PulseHeart 心跳回调调用（或 main.py 装配处转发）。
        """
        if not _orchestrator_enabled():
            return
        if self._degraded:
            return  # 已降级，不再执行
        self._beat_count = beat_count
        if beat_count % DETECT_EVERY_BEATS != 0:
            return  # 未到检测拍，跳过（轻量化）

        self.tick()

    # ========== 主循环 ==========

    def tick(self) -> None:
        """执行一轮「感知 → 行动 → 反馈」闭环。异常自动降级。"""
        try:
            snap = self._sense()
            if snap is None:
                return
            # 状态锁死：冷却期内只更新快照，不执行业务逻辑
            if self._state_locked:
                self._log(f"状态锁死中，跳过动作（{self._locked_level}，剩余{int(self._lock_until - time.time())}s）")
                return

            # 判定是否触发动作
            if snap.is_low:
                if self._should_act(snap):
                    self._act_low(snap)
            elif snap.is_high and self._should_act(snap):
                self._act_high(snap)

        except Exception as e:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._consecutive_errors += 1
            self._log_error(f"tick 异常（第{self._consecutive_errors}次）: {e}")
            if self._consecutive_errors >= self._max_consecutive_errors:
                self._degrade(f"连续 {self._max_consecutive_errors} 次异常，自动降级")

    # ========== 感知回路 ==========

    def _sense(self) -> SurvivalSnapshot | None:
        """读取存续状态，产出标准快照（含双阻尼过滤）。"""
        if self._get_existential_state is None:
            return None
        raw = self._get_existential_state()
        snap = SurvivalSnapshot.from_existential_state(raw)

        # 双阻尼①：阈值抖动过滤（±8 分内波动视为噪音）
        if abs(snap.index - self._last_index) < INDEX_THRESHOLD and not snap.mode_switched:
            # 不视为实质变化，但仍更新 last_index 以追踪趋势
            self._last_index = snap.index
            return snap  # 返回快照但 is_low/is_high 判定由调用方结合 mode_switched 处理
        self._last_index = snap.index
        return snap

    def _should_act(self, snap: SurvivalSnapshot) -> bool:
        """双阻尼②：时间冷却（20 分钟内不重复触发）。"""
        return not (time.time() - self._last_action_time < COOLDOWN_SECONDS)

    # ========== 行动回路（强序串行） ==========

    def _act_low(self, snap: SurvivalSnapshot) -> None:
        """存续低位：按强序串行执行五层次 on_survival_low。"""
        self._log(f"触发存续低位动作（index={snap.index}, level={snap.level}）")
        self._last_action_time = time.time()
        self._lock_state("low")

        # 可观测埋点：状态切换洞察
        self._post_insight(
            "existential_state",
            f"存续低位动作触发：指数={snap.index}，等级={snap.level}",
            "survival_orchestrator_low",
            confidence=_evidence_conf(0.9, "survival", [snap.index, snap.level]),
        )

        _results: list[dict] = []
        for layer in self._LAYER_ORDER:
            organ = self._layer_organs.get(layer)
            if organ is None:
                continue
            try:
                _r = organ.on_survival_low(snap)
                _results.append({"layer": layer, "state": "low", "result": str(_r)[:120]})
                # 可观测埋点：分层动作洞察
                self._post_insight(
                    "existential_state",
                    "生命层动作完成" if layer == "life" else f"层次[{layer}]动作完成",
                    f"survival_layer_{layer}",
                    confidence=0.8,
                )
            except Exception as e:
                _results.append({"layer": layer, "state": "low", "result": f"error:{e}"})
                self._log_error(f"层次[{layer}]低位动作异常（隔离，不影响其他层）: {e}")
        # ★生命层补强: 动作→反馈 消费闭环（R2 确定性结果回写）
        try:
            self._feedback({"level": "low", "index": snap.index, "layers": _results})
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def _act_high(self, snap: SurvivalSnapshot) -> None:
        """存续高位：按强序串行执行五层次 on_survival_high（含限流）。"""
        # 高位限流（定稿约束4）
        if time.time() - self._last_high_action_time < HIGH_ACTION_COOLDOWN:
            return
        self._last_high_action_time = time.time()

        self._log(f"触发存续高位动作（index={snap.index}, level={snap.level}）")
        self._last_action_time = time.time()
        self._lock_state("high")

        self._post_insight(
            "existential_state",
            f"存续高位动作触发：指数={snap.index}",
            "survival_orchestrator_high",
            confidence=_evidence_conf(0.9, "survival", [snap.index, snap.level]),
        )

        _results: list[dict] = []
        for layer in self._LAYER_ORDER:
            organ = self._layer_organs.get(layer)
            if organ is None:
                continue
            try:
                _r = organ.on_survival_high(snap)
                _results.append({"layer": layer, "state": "high", "result": str(_r)[:120]})
                self._post_insight(
                    "existential_state",
                    f"层次[{layer}]高位动作完成",
                    f"survival_layer_{layer}",
                    confidence=0.8,
                )
            except Exception as e:
                _results.append({"layer": layer, "state": "high", "result": f"error:{e}"})
                self._log_error(f"层次[{layer}]高位动作异常（隔离）: {e}")
        # ★生命层补强: 动作→反馈 消费闭环
        try:
            self._feedback({"level": "high", "index": snap.index, "layers": _results})
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ========== 状态锁死机制（定稿约束1） ==========

    def _lock_state(self, level: str) -> None:
        """动作触发后锁死状态，冷却周期内不二次切换。"""
        self._state_locked = True
        self._locked_level = level
        self._lock_until = time.time() + COOLDOWN_SECONDS

    def _maybe_unlock(self) -> None:
        """冷却期结束后解锁。"""
        if self._state_locked and time.time() >= self._lock_until:
            self._state_locked = False
            self._locked_level = ""

    # ========== 反馈回路（第二批启用，本批预留） ==========

    def _feedback(self, action_results: dict[str, Any]) -> None:
        """确定性结果回写（生命层补强：动作→反馈→自我观察 消费闭环）。

        把 _act_low/_act_high 的分层动作结果回写到 InsightBoard（life_posture_feedback），
        供叙事/对话层感知「自我保存动作是否真正生效」，并记录最近反馈供后续 _sense 参考。
        失败不阻塞（编排器主流程不受影响）。
        """
        try:
            _level = action_results.get("level", "?")
            _idx = action_results.get("index", "?")
            _layers = action_results.get("layers", [])
            _ok_cnt = sum(1 for r in _layers if "error" not in str(r.get("result", "")))
            from nucleus.InsightBoard import get_insight_board
            get_insight_board().post(
                insight_type="life_posture_feedback",
                content=f"存续{_level}动作反馈：{_ok_cnt}/{len(_layers)}层次生效，指数={_idx}",
                source_loop="生命姿态反馈",
                related_dimension="自我存续",
                confidence=0.85,
                keywords=["生命姿态反馈", f"level_{_level}", "动作闭环"],
            )
            self._last_feedback = {
                "level": _level, "index": _idx, "ok": _ok_cnt,
                "total": len(_layers), "at": time.time(),
            }
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    # ========== 降级机制 ==========

    def _degrade(self, reason: str) -> None:
        """异常自动降级：关闭编排器，回退各自读。

        ★R4阶段二治理：降级是「运行时一次性动作」，只设置实例级 _degraded 标志
        （永久生效，on_heartbeat 开头据此直接 return），不写回 config.FEATURE——
        避免污染用户配置，也避免与热重载机制冲突。用户配置保持用户意图，降级是实例内部状态。
        """
        self._degraded = True
        self._log_error(f"编排器降级：{reason}")
        # 可观测埋点：降级触发洞察
        self._post_insight(
            "existential_state",
            f"编排器自动降级：{reason}",
            "survival_orchestrator_degrade",
            confidence=1.0,
        )

    # ========== 可观测埋点 ==========

    def _post_insight(self, insight_type: str, content: str, source: str,
                      confidence: float = 0.5) -> None:
        """写入 InsightBoard（复用现有基础设施，失败不阻塞）。"""
        try:
            from nucleus.InsightBoard import get_insight_board
            get_insight_board().post(
                insight_type=insight_type,
                content=content,
                source_loop=source,
                confidence=confidence,
            )
        except Exception:
            pass  # 埋点失败不影响编排主流程

    # ========== 日志 ==========

    # ★P0-1修复（第十二批）：原签名只接受单参 msg，但本类有 5 处双参调用
    #   self._log("DEBUG", "异常已忽略") → TypeError: takes 2 positional arguments but 3 were given
    #   而该调用恰恰位于 except 块内，于是「忽略异常」的代码自己抛异常，
    #   把原始异常彻底掩盖。现改为兼容「单参 / 双参 / 带关键字」三种形态。

    def _log(self, level, message=None, **kwargs) -> None:
        try:
            if message is None:          # 单参形态 self._log("消息")
                level, message = "INFO", level
            import logging
            logging.getLogger("pulse").log(
                coerce_log_level(level), f"{LOG_TAG} {message}")
        except Exception:
            pass                          # 日志失败绝不能影响主流程

    def _log_error(self, level, message=None, **kwargs) -> None:
        try:
            if message is None:
                level, message = "INFO", level
            import logging
            logging.getLogger("pulse").error(f"{LOG_TAG} {message}")
        except Exception:
            pass


# ===== 模块级单例 =====
_orchestrator: SurvivalOrchestrator | None = None
_orchestrator_lock = threading.Lock()


def get_survival_orchestrator() -> SurvivalOrchestrator:
    """获取存续编排器单例。"""
    global _orchestrator
    if _orchestrator is None:
        with _orchestrator_lock:
            if _orchestrator is None:
                _orchestrator = SurvivalOrchestrator()
    return _orchestrator


def reset_survival_orchestrator() -> None:
    """复位单例（测试用）。"""
    global _orchestrator
    _orchestrator = None
