# -*- coding: utf-8 -*-
"""★第164批 刀A5（P1）：日志 → 告警落盘桥（ERROR/WARNING 入档）

背景（烛微 163 批夜间观测 F-2）：``data/reports/alerts.jsonl`` 实测 19h 零写入，
同期 ERROR 235 + WARNING 1671 均只打印未入档。根因：既有告警链路是
「信封/异常驱动」（仅 P0 信封异常经 ReportBus 消费者写盘，见 ``consumers.py``），
**不存在日志级别 → alerts.jsonl 的桥接**，故 ERROR/WARNING 日志流从未被捕获入档。

本模块补齐该桥：在根日志器 ``pulse`` 上挂一个 ``logging.Handler``，对达到分级门槛的
日志记录做「去重 + 限流」后追加到 ``alerts.jsonl``。

纪律（与项目既往门禁一致）：
  * 受 ``nucleus.data.write_guard`` 约束 —— pytest / 测试类环境**不写**生产 ``data/``；
  * 测试类环境直接不挂载（与 ``_log_in_test_env`` 同策略），避免污染与副作用；
  * 捕获异常一律用**具体**异常元组 + ``silent_exc``（不裸 ``except Exception``，规避 cw2）；
  * 去重 + 限流保证「无重复膨胀」，分级门槛「可观测」（模块常量）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from nucleus._silent_except import silent_exc

# ============================ 灰度 / 口径开关 ============================
#: ★模块级常量（config.py 为他方在途件，164批新开关不得改它；用模块级常量规避）
ALERT_LOG_BRIDGE_ENABLED = True
#: 分级门槛（仅 >= 该级别的记录入档）；常量可观测、可后续经 config 只读覆盖
ALERT_LOG_BRIDGE_MIN_LEVEL = "WARNING"
#: 去重窗口（秒）：同一 (logger, 级别, 消息) 在该窗口内只入档一次
ALERT_LOG_BRIDGE_DEDUP_WINDOW_SEC = 600.0
#: 单键窗口内硬上限（防极端重复膨胀）
ALERT_LOG_BRIDGE_MAX_PER_KEY_PER_WINDOW = 20

_ALERT_DIR = os.path.join("data", "reports")
_ALERT_FILE = "alerts.jsonl"

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_MIN_LEVELNO = _LEVEL_MAP.get(ALERT_LOG_BRIDGE_MIN_LEVEL, logging.WARNING)

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class AlertBridgePolicy:
    """去重 + 限流决策（纯逻辑、线程安全、可单测）。

    ``should_emit(name, levelno, message)`` 返回是否应当把这条记录入档。
    判定顺序：① 级别 < 门槛 → 否；② 同一键在窗口内未超硬上限 → 是（并计数）；
    ③ 窗口已过期 → 重置计数后入档；④ 窗口内已超上限 → 否（抑制重复膨胀）。
    """

    def __init__(self, dedup_window: float = ALERT_LOG_BRIDGE_DEDUP_WINDOW_SEC,
                 max_per_key: int = ALERT_LOG_BRIDGE_MAX_PER_KEY_PER_WINDOW,
                 min_levelno: int = _MIN_LEVELNO):
        self.dedup_window = float(dedup_window)
        self.max_per_key = int(max_per_key)
        self.min_levelno = int(min_levelno)
        self._lock = threading.Lock()
        # 键 -> {"last": 上次入档时间戳, "count": 当前窗口计数}
        self._state: Dict[str, Dict[str, float]] = {}

    @staticmethod
    def _key(name: str, levelno: int, message: str) -> str:
        _raw = "%s|%d|%s" % (name, levelno, message)
        return hashlib.md5(_raw.encode("utf-8")).hexdigest()[:16]

    def should_emit(self, name: str, levelno: int, message: str) -> bool:
        if levelno < self.min_levelno:
            return False
        _k = self._key(name, levelno, message)
        _now = time.time()
        with self._lock:
            _st = self._state.get(_k)
            if _st is None:
                self._state[_k] = {"last": _now, "count": 1}
                return True
            if _now - float(_st["last"]) >= self.dedup_window:
                _st["last"] = _now
                _st["count"] = 1
                return True
            if int(_st["count"]) >= self.max_per_key:
                return False
            _st["count"] = int(_st["count"]) + 1
            return True

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


class AlertJsonlHandler(logging.Handler):
    """把通过策略的日志记录追加到 ``data/reports/alerts.jsonl``。

    测试可注入 ``sink``（ callable(dict) ）以捕获落盘内容，避免触碰文件系统 /
    写盘守卫。生产路径走 ``_default_write``，受 ``write_guard`` 约束。
    """

    def __init__(self, policy: Optional[AlertBridgePolicy] = None,
                 sink: Optional[Callable[[dict], Any]] = None,
                 enabled: bool = ALERT_LOG_BRIDGE_ENABLED):
        super().__init__()
        self.policy = policy or AlertBridgePolicy()
        self.enabled = enabled
        self._sink = sink
        try:
            self.setLevel(self.policy.min_levelno)
        except (OSError, IOError, ValueError, TypeError) as e:
            silent_exc(e, where="alert_log_bridge::AlertJsonlHandler.__init__")
            self.setLevel(logging.WARNING)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        if not self.enabled:
            return
        try:
            _msg = self.format(record)
        except (OSError, IOError, ValueError, TypeError, AttributeError) as e:
            silent_exc(e, where="alert_log_bridge::emit.format")
            return
        if not self.policy.should_emit(record.name, record.levelno, _msg):
            return
        _doc = {
            "ts": record.created,
            "ts_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(record.created)),
            "logger": record.name,
            "level": record.levelname,
            "message": _msg,
            "path": record.pathname,
            "lineno": record.lineno,
            "source": "log_bridge",
        }
        if self._sink is not None:
            try:
                self._sink(_doc)
            except (OSError, IOError, ValueError, TypeError) as e:
                silent_exc(e, where="alert_log_bridge::emit.sink")
            return
        self._default_write(_doc)

    def _default_write(self, doc: dict) -> None:
        import nucleus.data.write_guard as _wg
        _p = os.path.join(_PROJECT_ROOT, _ALERT_DIR, _ALERT_FILE)
        if not _wg.guard_write(os.path.abspath(_p), explicit=False,
                               component="AlertLogBridge"):
            return
        try:
            os.makedirs(os.path.dirname(_p), exist_ok=True)
            with open(_p, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(doc, ensure_ascii=False) + "\n")
        except (OSError, IOError, ValueError, TypeError) as e:
            silent_exc(e, where="alert_log_bridge::_default_write")


_INSTALLED = False
_INSTALL_LOCK = threading.Lock()


def install_alert_log_bridge(logger: Optional[logging.Logger] = None,
                             policy: Optional[AlertBridgePolicy] = None) -> bool:
    """把 ``AlertJsonlHandler`` 挂到根日志器 ``pulse``（进程内仅一次）。

    返回是否**实际挂载**（已挂载 / 未启用 / 测试环境跳过 均返回 False）。
    """
    global _INSTALLED
    if not ALERT_LOG_BRIDGE_ENABLED:
        return False
    if _INSTALLED:
        return False
    with _INSTALL_LOCK:
        if _INSTALLED:
            return False
        import nucleus.data.write_guard as _wg
        if _wg.is_test_like_env():
            # 测试类环境不挂载（与 ``_log_in_test_env`` 同策略），避免污染
            _INSTALLED = True
            return False
        try:
            _lg = logger or logging.getLogger("pulse")
            for _h in _lg.handlers:
                if getattr(_h, "_alert_log_bridge", False):
                    _INSTALLED = True
                    return False
            _h = AlertJsonlHandler(policy=policy)
            _h._alert_log_bridge = True
            _lg.addHandler(_h)
            _INSTALLED = True
            return True
        except (OSError, IOError, ValueError, TypeError, AttributeError) as e:
            silent_exc(e, where="alert_log_bridge::install")
            return False


def reset_install_state() -> None:
    """清空挂载标记（测试用）。"""
    global _INSTALLED
    with _INSTALL_LOCK:
        _INSTALLED = False
