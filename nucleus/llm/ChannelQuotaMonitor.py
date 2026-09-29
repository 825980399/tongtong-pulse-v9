# -*- coding: utf-8 -*-
from nucleus._silent_except import silent_exc
"""ChannelQuotaMonitor.py —— 渠道免费额度监控与自动切换

版本: v10 PulseNet
设计: 内部协作者、内部协作者
日期: 2026年9月12日

职责: 统计各 LLM 渠道的 token 用量，按「剩余额度比例」自动降优先级或暂停渠道。

机制:
  · **统计**：每次渠道调用从响应体 `usage` 取 prompt_tokens + completion_tokens 累加；
  · **持久化**：写入 `data/channel_quota_usage.json`（进程重启不丢），写盘按间隔节流；
  · **预警**：剩余 < `QUOTA_ALERT_RATIO`（默认 20%）→ WARNING 预警（★第37批 T4 新增）；
  · **降级**：剩余 < `QUOTA_DEGRADE_RATIO`（默认 10%）→ 优先级 +3（后置）；
  · **暂停**：剩余 < `QUOTA_PAUSE_RATIO`（默认 5%）→ 从渠道池剔除（quota_exhausted）；
  · **日志**：★第37批 T4（P2-228）补齐关键事件日志 —— 预警/告警用 WARNING、
    暂停/恢复**状态切换事件**用 INFO（`_event_once`，与 `_warn_once` 语义分工：
    前者是"状态真的变了"，后者是"接近阈值"）。全部经 `_module_logger` 落
    `logs/pulse.log`（此前该模块**无**任何事件级日志，运行期看不到额度状态）。
  · **恢复**：`FORCE_ENABLE_CHANNELS` 白名单可强制放行；改配置后热重载即生效；
  · **与熔断独立**：本模块只依据 token 配额，不感知 circuit_break；两者状态互不影响，
    且额度暂停的渠道**不会被重试**（熔断 300s 后仍会重试）。

灰度: `ENABLE_QUOTA_MONITOR=False` 时 `apply_to_channels` 原样返回（零副作用）。

定位: LLM 渠道治理层
"""

import datetime
import json
import os
import threading
import time
from typing import Any

from nucleus.logger import get_module_logger

_module_logger = get_module_logger("ChannelQuotaMonitor")

# 默认持久化路径（相对项目根）
_DEFAULT_USAGE_REL = os.path.join("data", "channel_quota_usage.json")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ChannelQuotaMonitor:
    """渠道免费额度监控器（进程级单例，见 `get_channel_quota_monitor`）。

    典型用法::

        _m = get_channel_quota_monitor()
        _m.record_usage("ark-ds-v4-flash", 1200, 800)      # 每次调用后登记
        _pool = _m.apply_to_channels(get_active_channels()) # 选渠道前应用策略
    """

    #: 写盘最小间隔（秒）—— 避免高频调用造成磁盘抖动
    SAVE_MIN_INTERVAL = 30.0

    def __init__(self, usage_path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._path = usage_path or os.path.join(_PROJECT_ROOT, _DEFAULT_USAGE_REL)
        # name -> {"prompt_tokens": int, "completion_tokens": int,
        #          "total_tokens_used": int, "last_updated": float}
        self._usage: dict[str, dict[str, Any]] = {}
        self._last_save = 0.0
        self._dirty = False
        # 告警去重：同一渠道同一档位只告警一次（直到状态变化）
        self._warned: dict[str, str] = {}
        # ★第37批 T4（P2-228）：状态切换事件去重（paused / recovered）
        # _m37_t4
        self._events: dict[str, str] = {}
        # ★P2-193：白名单渠道首次加入时间戳（用于FORCE_ENABLE_TTL_HOURS过期判断）
        # 内存中维护，重启后重新计算（合理：重启相当于重新开始白名单周期）
        self._force_enable_ts: dict[str, float] = {}
        # ★第97批 相关任务：协作奖励渠道每日重置日期记录（name -> "YYYY-MM-DD"）
        self._daily_reset: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------
    @staticmethod
    def _cfg():
        try:
            import config as _c
            return _c
        except Exception as e:
            silent_exc(e, "nucleus/llm/ChannelQuotaMonitor.py:86", level="warning")
            return None

    def enabled(self) -> bool:
        """额度监控总开关（`ENABLE_QUOTA_MONITOR`，默认 True）。"""
        _c = self._cfg()
        if _c is None:
            return True
        return bool(getattr(_c, "ENABLE_QUOTA_MONITOR", True))

    def degrade_ratio(self) -> float:
        """降优先级阈值（剩余比例低于此值 → 优先级 +3），默认 0.10。"""
        _c = self._cfg()
        try:
            return float(getattr(_c, "QUOTA_DEGRADE_RATIO", 0.10))
        except Exception as e:
            silent_exc(e, "nucleus/llm/ChannelQuotaMonitor.py:101", level="warning")
            return 0.10

    def pause_ratio(self) -> float:
        """暂停阈值（剩余比例低于此值 → 从池中剔除），默认 0.05。"""
        _c = self._cfg()
        try:
            return float(getattr(_c, "QUOTA_PAUSE_RATIO", 0.05))
        except Exception:
            return 0.05

    def alert_ratio(self) -> float:
        """★第37批 T4：预警阈值（剩余比例低于此值 → WARNING 预警），默认 0.20。

        早于"降级"（10%）与"暂停"（5%）触发，给运维留出处置窗口。
        """
        _c = self._cfg()
        try:
            return float(getattr(_c, "QUOTA_ALERT_RATIO", 0.20))
        except Exception:
            return 0.20

    def safety_margin(self) -> float:
        """★P2-192：额度用量安全系数（默认1.0=不调整，向后兼容）。

        本地计数可能低于真实消耗（白名单外进程/人工控制台也在调用同一接入点），
        导致剩余比例偏高、暂停时机偏晚。设置>1.0（如1.1）可提前预警/降级/暂停。
        """
        _c = self._cfg()
        try:
            return float(getattr(_c, "QUOTA_USAGE_SAFETY_MARGIN", 1.0))
        except Exception:
            return 1.0

    def _force_enabled(self) -> set:
        """`FORCE_ENABLE_CHANNELS`：即使额度不足也强制启用的渠道名集合。

        ★P2-193修复：支持 `FORCE_ENABLE_TTL_HOURS` 配置（默认0=永久有效）。
        当TTL>0时，白名单渠道在加入N小时后自动失效，避免耗尽渠道长期占用名额。
        过期时记录INFO日志（用_event_once去重，避免重复日志）。
        """
        _c = self._cfg()
        try:
            _configured = {str(x) for x in (getattr(_c, "FORCE_ENABLE_CHANNELS", []) or [])}
        except Exception:
            _configured = set()

        # 无论配置是否为空，都清理不在配置中的渠道的时间戳
        with self._lock:
            for _name in list(self._force_enable_ts.keys()):
                if _name not in _configured:
                    self._force_enable_ts.pop(_name, None)

        if not _configured:
            return set()

        # 读取TTL配置（默认0=永久有效）
        try:
            _ttl_hours = float(getattr(_c, "FORCE_ENABLE_TTL_HOURS", 0) or 0)
        except Exception:
            _ttl_hours = 0.0

        if _ttl_hours <= 0:
            return _configured  # TTL=0表示永久有效

        # TTL>0时，过滤掉超过TTL的渠道
        _now = time.time()
        _ttl_seconds = _ttl_hours * 3600.0
        _valid = set()
        _expired = []  # 收集已过期渠道，锁外调用_event_once（避免嵌套锁死锁）
        with self._lock:
            for _name in _configured:
                if _name not in self._force_enable_ts:
                    self._force_enable_ts[_name] = _now  # 首次加入，记录当前时间
                _joined_at = self._force_enable_ts[_name]
                if _now - _joined_at < _ttl_seconds:
                    _valid.add(_name)
                else:
                    _expired.append(_name)  # 已过期，锁外记录日志
        # 锁外调用_event_once（避免嵌套锁死锁：_event_once内部也用self._lock）
        for _name in _expired:
            self._event_once(_name, "force_enable_expired", 0.0)
        return _valid

    @staticmethod
    def _quota_limit_of(channel_name: str) -> int:
        """取该渠道配置的 `quota_limit`（总 tokens）。-1 / 缺失 = 不限量。"""
        _c = ChannelQuotaMonitor._cfg()
        if _c is None:
            return -1
        try:
            for _ch in (_c.REMOTE_API_CHANNELS or {}).get("default_channels") or []:
                if str(_ch.get("name")) == str(channel_name):
                    _v = _ch.get("quota_limit", -1)
                    return int(_v) if _v is not None else -1
        except Exception as _e:
            _module_logger.debug(
                f"[配额查询] _quota_limit_of 读取渠道配置失败: {type(_e).__name__}: {_e}")
        return -1

    # ------------------------------------------------------------------
    # ★第97批 相关任务：额度类型（fixed / daily_reward）与每日重置
    # ------------------------------------------------------------------
    def quota_type_of(self, channel_name: str) -> str:
        """渠道额度类型：'fixed'（默认，用完即止/降优先级）或 'daily_reward'

        （协作奖励，每日补充，不降优先级/不暂停）。配置缺失 → 'fixed'。
        """
        _c = self._cfg()
        try:
            for _ch in (_c.REMOTE_API_CHANNELS or {}).get("default_channels") or []:
                if str(_ch.get("name")) == str(channel_name):
                    return str(_ch.get("quota_type", "fixed") or "fixed")
        except Exception:
            return "fixed"
        return "fixed"

    def daily_reset_hour(self) -> int:
        """协作奖励额度每日重置时刻（本地小时，默认 11）。"""
        _c = self._cfg()
        try:
            return int(getattr(_c, "QUOTA_DAILY_RESET_HOUR", 11) or 11)
        except Exception:
            return 11

    def _ensure_daily_reset(self, channel_name: str,
                            now: "datetime.datetime | None" = None) -> None:
        """★第97批 相关任务：协作奖励（daily_reward）渠道，local QUOTA_DAILY_RESET_HOUR

        点后每日自动清零用量，避免被误判耗尽而降优先级/暂停。
        幂等：按「今日日期」去重，同一天只重置一次。固定额度（fixed）渠道不重置。
        `now` 可选，用于测试注入；生产默认用本地当前时间。
        ★线程安全：本方法独立持锁，调用方不得在持 self._lock 时调用（record_usage
        已在持锁前调用，get_remaining_ratio/apply_to_channels 调用点均不持锁）。
        """
        if self.quota_type_of(channel_name) != "daily_reward":
            return
        try:
            import datetime as _dt
            _now = now if now is not None else _dt.datetime.now()
            _hour = self.daily_reset_hour()
            _today = _now.strftime("%Y-%m-%d")
            with self._lock:
                _last = self._daily_reset.get(channel_name)
                if _now.hour >= _hour and _last != _today:
                    self._usage.pop(channel_name, None)
                    self._daily_reset[channel_name] = _today
                    self._dirty = True
                    self._last_save = 0.0
        except Exception as e:
            silent_exc(e, "nucleus/llm/ChannelQuotaMonitor.py:250", level="warning")

    def tick_daily_reset(self) -> int:
        """★第97批 相关任务：运维/调度主动触发——对所有 active 渠道执行每日重置检查。

        返回本次实际重置的渠道数（供日志/诊断）。
        """
        _n = 0
        try:
            _c = self._cfg()
            _chs = ((_c.REMOTE_API_CHANNELS or {}).get("default_channels") or [])
            for _ch in _chs:
                _name = str(_ch.get("name") or "")
                if _name and self.quota_type_of(_name) == "daily_reward":
                    _before = self._daily_reset.get(_name)
                    self._ensure_daily_reset(_name)
                    if self._daily_reset.get(_name) != _before:
                        _n += 1
        except Exception as e:
            silent_exc(e, "nucleus/llm/ChannelQuotaMonitor.py:269", level="warning")
        return _n

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def record_usage(self, channel_name: str, prompt_tokens: int = 0,
                     completion_tokens: int = 0) -> None:
        """登记一次渠道调用的 token 用量（累加 + 按间隔节流落盘）。

        参数:
            channel_name:      渠道名（对应 config 里的 `name`）。
            prompt_tokens:     输入 tokens（响应体 `usage.prompt_tokens`）。
            completion_tokens: 输出 tokens（响应体 `usage.completion_tokens`）。
        副作用:
            内存累加；距上次写盘超过 `SAVE_MIN_INTERVAL` 秒时同步落盘。
            任何异常只记 DEBUG，绝不向上抛（调用方在热路径上）。
        """
        _name = str(channel_name or "").strip()
        if not _name:
            return
        try:
            _p = max(0, int(prompt_tokens or 0))
            _c = max(0, int(completion_tokens or 0))
        except Exception as e:
            silent_exc(e, "ChannelQuotaMonitor.py:297:QuotaMonitor回收", level="warning")
            return
        if _p == 0 and _c == 0:
            return
        with self._lock:
            _e = self._usage.setdefault(_name, {
                "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens_used": 0, "last_updated": 0.0,
            })
            _e["prompt_tokens"] += _p
            _e["completion_tokens"] += _c
            _e["total_tokens_used"] += (_p + _c)
            _e["last_updated"] = time.time()
            self._dirty = True
            _should_save = (time.time() - self._last_save) >= self.SAVE_MIN_INTERVAL
        if _should_save:
            self.save()

    def get_used(self, channel_name: str) -> int:
        """该渠道累计已用 tokens。"""
        with self._lock:
            return int((self._usage.get(str(channel_name)) or {}).get("total_tokens_used", 0))

    def get_limit(self, channel_name: str) -> int:
        """该渠道额度上限（-1 = 不限量）。"""
        return self._quota_limit_of(channel_name)

    def get_remaining_ratio(self, channel_name: str) -> float | None:
        """剩余额度比例 [0,1]；不限量或未配置额度时返回 None。

        ★P2-192：用量乘以安全系数（默认1.0），补偿本地计数低于真实消耗的偏差。

        返回:
            float 剩余比例（可能 <0，表示已超用）；None = 不参与额度管控。
        """
        _limit = self.get_limit(channel_name)
        if _limit is None or _limit <= 0:
            return None
        _used = float(self.get_used(channel_name)) * self.safety_margin()
        return (float(_limit) - _used) / float(_limit)

    def is_exhausted(self, channel_name: str) -> bool:
        """该渠道是否因额度不足被暂停（且未被 `FORCE_ENABLE_CHANNELS` 放行）。"""
        # ★第97批 相关任务：协作奖励（daily_reward）渠道每日补充，永不因额度暂停
        if self.quota_type_of(str(channel_name)) == "daily_reward":
            return False
        if str(channel_name) in self._force_enabled():
            return False
        _r = self.get_remaining_ratio(channel_name)
        if _r is None:
            return False
        return _r < self.pause_ratio()

    # ------------------------------------------------------------------
    # 渠道池策略
    # ------------------------------------------------------------------
    def apply_to_channels(self, channels: list) -> list:
        """按额度用量调整渠道池：降优先级（<10%）/ 剔除（<5%）/ 白名单放行。

        参数:
            channels: 渠道 dict 列表（通常是 `config.get_active_channels()`）。
        返回:
            新的列表（元素为浅拷贝，不改动入参）；已按 priority 升序排好。
            开关关闭或异常时**原样返回**入参（零副作用）。
        """
        _c = self._cfg()
        if _c is None or not self.enabled():
            return channels
        try:
            _force = self._force_enabled()
            _alert = self.alert_ratio()
            _degrade = self.degrade_ratio()
            _pause = self.pause_ratio()
            _out = []
            for _ch in channels or []:
                if not isinstance(_ch, dict):
                    continue
                _n = str(_ch.get("name") or "")
                if not _n:
                    _out.append(dict(_ch))
                    continue
                if _n in _force:
                    _out.append(dict(_ch))
                    continue
                # ★第97批 相关任务：协作奖励（daily_reward）每日补充，不降优先级/不暂停；
                #   仍触发每日重置清空用量，保持额度账本新鲜。
                if self.quota_type_of(_n) == "daily_reward":
                    self._ensure_daily_reset(_n)
                    _out.append(dict(_ch))
                    continue
                _r = self.get_remaining_ratio(_n)
                if _r is None:
                    _out.append(dict(_ch))
                    continue
                if _r < _pause:
                    self._warn_once(_n, "pause", _r)
                    _module_logger.warning(
                        f"[渠道额度] {_n} 额度不足 {_r * 100:.1f}%，已暂停（跳过）")
                    self._event_once(_n, "paused", _r)   # ★T4：暂停事件（INFO）
                    continue
                _c2 = dict(_ch)
                if _r < _degrade:
                    try:
                        _c2["priority"] = int(_ch.get("priority", 999)) + 3
                    except Exception:
                        _c2["priority"] = 999
                    self._warn_once(_n, "degrade", _r)
                    _module_logger.warning(
                        f"[渠道额度] {_n} 额度不足 {_r * 100:.1f}%，已降低优先级"
                        f"（{_ch.get('priority')} → {_c2['priority']}）")
                    # ★T4b：记录降级态（供后续"恢复"判定；本档不另打日志）
                    self._event_once(_n, "degraded", _r)
                else:
                    self._warned.pop(_n, None)   # 额度恢复 → 允许再次告警
                    # ★T4：恢复事件（INFO）—— 此前额度恢复**无任何日志**，
                    #   运维无法从日志判断渠道何时重新可用。
                    self._event_once(_n, "recovered", _r)
                    if _r < _alert:
                        # ★T4：预警档（20%）—— 早于降级/暂停，留出处置窗口
                        self._warn_once(_n, "alert", _r)
                        _module_logger.warning(
                            f"[额度监控] 渠道{_n}剩余额度{_r * 100:.1f}%，"
                            f"低于{_alert * 100:.0f}%阈值")
                _out.append(_c2)
            _out.sort(key=lambda x: x.get("priority", 999))
            return _out
        except Exception as _e:
            _module_logger.debug(
                f"[渠道额度] 策略应用失败，使用原始池: {type(_e).__name__}: {_e}")
            return channels

    def _warn_once(self, name: str, level: str, ratio: float) -> None:
        """同一渠道同一档位只打一次告警（避免刷屏；状态变化后重置）。

        ★第37批 T4：由两档（降级/暂停）扩展为**三档**（预警/降级/暂停），
        档位按实际剩余比例推导（不再依赖调用方传参，避免传错档位）。
        """
        _key = ("pause" if ratio < self.pause_ratio()
                else ("degrade" if ratio < self.degrade_ratio() else "alert"))
        with self._lock:
            if self._warned.get(name) == _key:
                return
            self._warned[name] = _key
        _label = {"pause": "暂停", "degrade": "降级", "alert": "预警"}.get(_key, _key)
        _module_logger.warning(
            f"[渠道额度] {name} 剩余 {max(0.0, ratio) * 100:.1f}%"
            f"（阈值 {_label}）")

    def _event_once(self, name: str, kind: str, ratio: float) -> None:
        """★第37批 T4（P2-228）：状态切换**事件**日志（暂停 / 恢复）。

        与 `_warn_once` 的语义分工：
            · `_warn_once`  = 「额度告警」（接近阈值，WARNING，可按档位多次）；
            · `_event_once` = 「状态事件」（正常 ↔ 暂停 真的切换了，INFO，只一次）。
        同一渠道同一状态只打一次，避免每轮 `apply_to_channels` 刷屏。

        参数:
            name: 渠道名。
            kind: "paused" 或 "recovered"。
            ratio: 当前剩余比例（用于日志显示）。

        示例:
            >>> m._event_once("ark-ds-v4-flash", "paused", 0.03)   # doctest: +SKIP
        """
        with self._lock:
            _prev = self._events.get(name)
            if _prev == kind:
                return
            # ★语义修正：只有此前**确实**处于暂停/降级态才算"恢复"；
            #   首次（或一直正常）时额度充足**不是恢复事件**，不得打"已恢复"日志。
            if kind == "recovered" and _prev not in ("paused", "degraded"):
                self._events[name] = "normal"
                return
            self._events[name] = kind
        if kind == "paused":
            _module_logger.info(
                f"[额度监控] 渠道{name}额度耗尽，已暂停使用"
                f"（剩余 {max(0.0, ratio) * 100:.1f}%）")
        elif kind == "recovered":
            _module_logger.info(
                f"[额度监控] 渠道{name}额度已恢复，重新启用"
                f"（剩余 {max(0.0, ratio) * 100:.1f}%）")
        # kind == "degraded"：只记录状态（既有 WARNING 已告知"已降低优先级"，
        #   避免同一事实打两条日志）
# _m37_t4b

    # ------------------------------------------------------------------
    # 统计视图 / 持久化
    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        """额度统计（供自省/诊断消费）。"""
        _c = self._cfg()
        _pool = []
        try:
            _pool = [str(_x.get("name")) for _x in
                     ((_c.REMOTE_API_CHANNELS or {}).get("default_channels") or [])]
        except Exception:
            _pool = list(self._usage.keys())
        _out = {}
        for _n in _pool:
            _limit = self.get_limit(_n)
            _r = self.get_remaining_ratio(_n)
            _out[_n] = {
                "used": self.get_used(_n),
                "limit": _limit,
                "remaining_ratio": None if _r is None else round(_r, 4),
                "exhausted": self.is_exhausted(_n),
            }
        return {
            "enabled": self.enabled(),
            "alert_ratio": self.alert_ratio(),
            "degrade_ratio": self.degrade_ratio(),
            "pause_ratio": self.pause_ratio(),
            "force_enabled": sorted(self._force_enabled()),
            "usage_path": self._path,
            "channels": _out,
            # ★第37批 T4：状态事件快照（诊断用）
            "events": dict(self._events),
        }

    def _load(self) -> None:
        """从磁盘加载历史用量（失败静默，不影响启动）。

        ★P2-194修复：加载后清理不在当前配置中的渠道条目，避免长期运行
        且渠道动态增删时积累已移除渠道的历史条目，导致JSON文件无限增长。
        清理后标记_dirty=True，确保下次save落盘清理后的结果。
        """
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path, encoding="utf-8") as _f:
                _d = json.load(_f)
            _ch = (_d or {}).get("channels") or {}
            if isinstance(_ch, dict):
                self._usage = {str(k): dict(v) for k, v in _ch.items()
                               if isinstance(v, dict)}
            # ★第97批 相关任务：加载协作奖励渠道每日重置日期
            _dr = (_d or {}).get("daily_reset") or {}
            if isinstance(_dr, dict):
                self._daily_reset = {str(k): str(v) for k, v in _dr.items()
                                     if isinstance(v, str)}
            # ★P2-194：清理不在当前配置中的渠道条目
            _active = self._active_channel_names()
            if _active:
                _stale = [n for n in self._usage if n not in _active]
                if _stale:
                    for _n in _stale:
                        self._usage.pop(_n, None)
                        self._warned.pop(_n, None)
                        self._events.pop(_n, None)
                    self._dirty = True  # 确保下次save落盘清理后的结果
                    _module_logger.debug(
                        f"[渠道额度] 加载时清理{len(_stale)}个已移除渠道"
                        f"（{', '.join(_stale[:5])}{'...' if len(_stale) > 5 else ''}）")
        except Exception as _e:
            _module_logger.debug(f"[渠道额度] 用量文件加载失败: {type(_e).__name__}: {_e}")

    @staticmethod
    def _active_channel_names() -> set[str]:
        """获取当前配置中所有活跃渠道名（用于P2-194清理判断）。"""
        try:
            import config as _c
            _channels = ((_c.REMOTE_API_CHANNELS or {}).get("default_channels") or [])
            return {str(_x.get("name")) for _x in _channels
                    if isinstance(_x, dict) and _x.get("name")}
        except Exception:
            return set()

    def save(self, force: bool = False) -> bool:
        """把内存用量落盘。

        参数:
            force: True 时忽略节流间隔（测试与停机前调用）。
        返回:
            True = 已写入；False = 未到间隔或写入失败。
        """
        with self._lock:
            if not self._dirty and not force:
                return False
            if not force and (time.time() - self._last_save) < self.SAVE_MIN_INTERVAL:
                return False
            _snap = {"channels": {k: dict(v) for k, v in self._usage.items()},
                     # ★第97批 相关任务：持久化协作奖励渠道每日重置日期
                     "daily_reset": dict(self._daily_reset),
                     "updated_at": time.time()}
            _path = self._path
            self._dirty = False
            self._last_save = time.time()
        try:
            _dir = os.path.dirname(_path)
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            _tmp = _path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(_snap, _f, ensure_ascii=False, indent=2)
            os.replace(_tmp, _path)       # 原子替换，避免半写文件
            return True
        except Exception as _e:
            _module_logger.debug(f"[渠道额度] 用量落盘失败: {type(_e).__name__}: {_e}")
            return False

    def reset(self, save: bool = False) -> None:
        """清空统计（测试/运维复位用）。`save=True` 时同时清空磁盘文件。"""
        with self._lock:
            self._usage = {}
            self._warned = {}
            self._dirty = bool(save)
            self._last_save = 0.0
        if save:
            self.save(force=True)


# ======================================================================
# 进程级单例
# ======================================================================
_monitor: ChannelQuotaMonitor | None = None
_monitor_lock = threading.Lock()


def get_channel_quota_monitor() -> ChannelQuotaMonitor:
    """获取额度监控器单例。"""
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = ChannelQuotaMonitor()
    return _monitor


def reset_channel_quota_monitor() -> None:
    """复位单例（测试用；不落盘）。"""
    global _monitor
    with _monitor_lock:
        _monitor = None
