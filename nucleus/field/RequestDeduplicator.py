# -*- coding: utf-8 -*-
"""RequestDeduplicator —— 请求去重器（主线第26批 T1 / P2-161）。

解决的问题
----------
第25批的止血方案是「3 秒内相同 prompt 的请求**直接跳过**并返回空答案」，
存在三个缺陷：

  D1 第一个请求若最终失败（渠道全挂），第二个也被丢弃 → 用户永远等不到答案；
  D2 第一个请求卡住（渠道超时 30s）时，第二个也被跳过 → 表现为「对话无响应」；
  D3 去重键只有 prompt 文本 → 不同用户的相同提问会互相误伤。

本去重器把「跳过」换成**「等待复用 / 超时接管 / 直接复用」**：

  第一个请求处理中 → 第二个**等待**并**复用**其结果
  第一个请求超时   → 第二个**接管**（成为新 owner），旧 owner 被置为放弃
  第一个请求已完成 → 第二个**直接复用**（结果缓存窗口内）

设计定位
--------
- 位置：`nucleus/field/RequestDeduplicator.py`（★以第26批任务书为准；
  第25批设计文档 v1.0 中写的是 `nucleus/concurrency/`，实施版以任务书为准）
- 与 `PulseCortex._dialog_guard` 的关系：**分层并存，互不替代**
    · 轮次间：`_dialog_guard`（correlation_id）负责新输入排队/抢占，防话题错位
    · 轮次内：本去重器（`user_name + prompt指纹`）负责同请求合并与超时接管
- 与第25批 `PulseCortex._claim_select_model_emit` 的关系：
    · 第25批在**发射侧**保证「同一轮只发一次 SELECT_MODEL」
    · 本去重器在**执行侧**保证「同内容并发请求只真实执行一次」
    · 二者叠加后：同一轮被两次驱动 → 发射侧拦截；不同轮同内容并发 → 本去重器合并

线程安全
--------
- 只有一把 `threading.RLock`，且**持锁期间绝不执行业务**（等待走 `Condition.wait()`，
  会释放锁）→ 无死锁风险；
- 结果写入先于 `Condition.notify_all()`，`wait()` 返回后读结果满足 happens-before；
- 等待者不额外创建线程（复用 owner 的 Condition），无线程爆炸风险。

★灰度：总开关 `config.ENABLE_REQUEST_DEDUP`（**默认 False**）。
  关闭时 `_request_dedup()` 返回 None，接入点全部短路 → 与改造前行为完全一致。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc

__all__ = ["RequestDeduplicator", "get_request_deduplicator", "reset_request_deduplicator"]

# try_claim 的三种返回值
CLAIMED = "claimed"      # 本调用成为 owner（新建，或超时接管）
WAITING = "waiting"      # 已有 owner 且等待者已满，本调用未等到结果（调用方应按新请求处理或放弃）
DUPLICATE = "duplicate"  # 已有可用结果，直接复用（get_result 可取到）

# 条目状态
_PROCESSING = "processing"
_COMPLETED = "completed"
_ABANDONED = "abandoned"


class _Entry:
    """一次请求在途记录。"""

    __slots__ = ("request_id", "status", "owner_started", "result",
                 "completed_at", "waiters", "takeovers")

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.status = _PROCESSING
        self.owner_started = time.time()
        self.result: Any = None
        self.completed_at = 0.0
        self.waiters = 0
        self.takeovers = 0


class RequestDeduplicator:
    """请求去重器：同一 ``request_id`` 只真实执行一次，其余等待复用或超时接管。

    核心机制（``try_claim`` 三态）::

        "claimed"    → 本调用成为 owner，**真实执行**业务，完成后调 ``complete()``
        "duplicate"  → 已有可用结果，**不要再执行**，直接 ``get_result()``
        "waiting"    → 已有 owner 且等待者已满（或等待被中断），调用方按新请求处理

    触发「接管」的两种情形：
      1. 上一 owner 已超时（``> timeout`` 秒未完成）→ 本调用接手当 owner；
      2. 上一 owner 主动 ``cancel()``（异常退出）→ 本调用接手。

    线程安全（★本类最需要小心的地方）:
      - 全程只有一把 ``threading.RLock``；**持锁期间绝不执行业务**，
        等待统一走 ``self._cv.wait()``（自动释放锁）→ 不可能与 owner 互相死锁；
      - 结果**先写入、后** ``notify_all()``，等待者 ``wait()`` 返回后读结果
        满足 happens-before；
      - 等待者不额外创建线程（复用 owner 的 ``Condition``），无线程爆炸风险。

    使用示例::

        _d = get_request_deduplicator()      # ★开关关闭时返回 None → 调用方短路
        if _d is not None:
            _state = _d.try_claim(key)
            if _state == "duplicate":
                return _d.get_result(key)    # 复用已有结果，不重复调用大模型
            if _state == "claimed":
                try:
                    _ans = _real_work()
                    _d.complete(key, _ans)
                except Exception:
                    _d.cancel(key)           # 唤醒等待者，让其接管
                return _ans

    参数语义见 ``__init__``；统计见 ``stats()``；单条目状态见 ``get_state()``。
    """

    def __init__(self, timeout: float = 30.0, max_wait: int = 10,
                 reuse_ttl: float = 5.0) -> None:
        """
        Args:
            timeout:   单请求超时（秒）。超时后新的调用会**接管**成为 owner。
            max_wait:  同一 request_id 最多允许的等待者数量（超出直接返回 duplicate，防雪崩）。
            reuse_ttl: 结果复用窗口（秒）。完成后在该窗口内可被直接复用。
        """
        self._timeout = float(timeout)
        self._max_wait = max(1, int(max_wait))
        self._reuse_ttl = float(reuse_ttl)

        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._entries: dict[str, _Entry] = {}

        # 累计统计（诊断/监控用）
        self._stats = {
            "claimed": 0,
            "duplicate": 0,
            "waiting": 0,
            "takeover": 0,
            "completed": 0,
            "cancelled": 0,
            "expired": 0,
        }

    # ---------------- 核心 API ----------------

    def try_claim(self, request_id: str) -> str:
        """认领一次请求。

        Returns:
            ``"claimed"``   —— 本调用成为 owner，应**真实执行**业务；
            ``"duplicate"`` —— 已存在可用结果，**不要再执行**，直接 ``get_result()``；
            ``"waiting"``   —— 等待者已满或等待被中断，调用方应按新请求处理或放弃。
        """
        if not request_id:
            # 无 key 时不做去重（保守降级，绝不改变行为）
            return CLAIMED

        with self._cv:
            self._sweep()
            _e = self._entries.get(request_id)

            # ① 无条目 → 成为首个 owner
            if _e is None:
                self._entries[request_id] = _Entry(request_id)
                self._stats["claimed"] += 1
                return CLAIMED

            # ② 已完成且在复用窗口内 → 直接复用
            if _e.status == _COMPLETED and (
                    time.time() - _e.completed_at) < self._reuse_ttl:
                self._stats["duplicate"] += 1
                return DUPLICATE

            # ③ 处理中（或已过期/已放弃）→ 判定是否接管
            _expired = (time.time() - _e.owner_started) > self._timeout
            if _expired or _e.status == _ABANDONED:
                # 超时/已放弃 → 本调用接管成为新 owner
                _e.status = _PROCESSING
                _e.owner_started = time.time()
                _e.result = None
                _e.completed_at = 0.0
                _e.takeovers += 1
                if _expired:
                    self._stats["expired"] += 1
                self._stats["takeover"] += 1
                self._cv.notify_all()
                return CLAIMED

            # ④ 处理中且未超时 → 等待（等待者超限则不再等待）
            if _e.waiters >= self._max_wait:
                self._stats["waiting"] += 1
                return WAITING

            _e.waiters += 1
            try:
                # 等待完成 或 超时（wait 会释放锁，不阻塞 owner）
                _deadline = _e.owner_started + self._timeout
                _remain = max(0.0, _deadline - time.time())
                self._cv.wait(_remain)
            finally:
                _e.waiters -= 1

            # 醒来后：成功拿到结果 → 复用；否则（超时/被放弃）→ 本调用接管
            if _e.status == _COMPLETED and _e.result is not None:
                self._stats["duplicate"] += 1
                return DUPLICATE

            if (time.time() - _e.owner_started) > self._timeout or _e.status == _ABANDONED:
                _e.status = _PROCESSING
                _e.owner_started = time.time()
                _e.result = None
                _e.completed_at = 0.0
                _e.takeovers += 1
                self._stats["takeover"] += 1
                self._cv.notify_all()
                return CLAIMED

            self._stats["waiting"] += 1
            return WAITING

    def complete(self, request_id: str, result: Any) -> bool:
        """标记请求完成并缓存结果，唤醒所有等待者。

        Returns: True 表示确实完成了一个在途请求；False 表示无此在途条目。
        """
        if not request_id:
            return False
        with self._cv:
            _e = self._entries.get(request_id)
            if _e is None or _e.status != _PROCESSING:
                return False
            _e.status = _COMPLETED
            _e.result = result
            _e.completed_at = time.time()
            self._stats["completed"] += 1
            self._cv.notify_all()
            return True

    def get_result(self, request_id: str) -> Any:
        """获取已完成请求的结果；不存在/未完成返回 None。"""
        if not request_id:
            return None
        with self._lock:
            _e = self._entries.get(request_id)
            if _e is None or _e.status != _COMPLETED:
                return None
            return _e.result

    def cancel(self, request_id: str) -> bool:
        """放弃一个在途请求（owner 异常退出时调用），唤醒等待者让其接管。"""
        if not request_id:
            return False
        with self._cv:
            _e = self._entries.get(request_id)
            if _e is None or _e.status != _PROCESSING:
                return False
            _e.status = _ABANDONED
            _e.result = None
            self._stats["cancelled"] += 1
            self._cv.notify_all()
            return True

    # ---------------- 诊断接口 ----------------

    def stats(self) -> dict[str, Any]:
        """累计统计快照（供监控 / 自省消费）。

        Returns:
            dict[str, Any]: 各计数字段（claimed/duplicate/waiting/takeover/
            completed/cancelled/expired）+ ``inflight``（在途）+ ``cached``（已完成）
            + 当前生效的 ``timeout`` / ``max_wait`` 配置。
        """
        with self._lock:
            # ★第27批 T4：显式标注为 dict[str, Any] —— 此前 mypy 因 dict(self._stats)
            #   推断为 dict[str, int]，导致下面写入 float 的 timeout 报赋值类型错误。
            _out: dict[str, Any] = dict(self._stats)
            _out["inflight"] = sum(
                1 for _e in self._entries.values() if _e.status == _PROCESSING)
            _out["cached"] = sum(
                1 for _e in self._entries.values() if _e.status == _COMPLETED)
            _out["timeout"] = self._timeout
            _out["max_wait"] = self._max_wait
            return _out

    def get_state(self, request_id: str) -> dict[str, Any] | None:
        """查询单个 ``request_id`` 的状态（诊断用）。

        Args:
            request_id: 去重键。

        Returns:
            dict[str, Any] | None: 含 ``request_id``/``status``/``elapsed``/``waiters``/
            ``takeovers``/``has_result`` 的字典；条目不存在时返回 None。
        """
        with self._lock:
            _e = self._entries.get(request_id)
            if _e is None:
                return None
            return {
                "request_id": _e.request_id,
                "status": _e.status,
                "elapsed": round(time.time() - _e.owner_started, 3),
                "waiters": _e.waiters,
                "takeovers": _e.takeovers,
                "has_result": _e.result is not None,
            }

    def reset(self) -> None:
        """清空全部条目与统计（测试用）。"""
        with self._cv:
            self._entries.clear()
            for _k in self._stats:
                self._stats[_k] = 0
            self._cv.notify_all()

    def shutdown(self, wait: bool = False) -> None:
        """关停：标记全部在途为放弃并唤醒等待者（框架 stop 时调用）。"""
        with self._cv:
            for _e in self._entries.values():
                if _e.status == _PROCESSING:
                    _e.status = _ABANDONED
                    self._stats["cancelled"] += 1
            self._cv.notify_all()
        if wait:
            time.sleep(0.05)

    # ---------------- 内部 ----------------

    def _sweep(self) -> None:
        """清理过期条目（已完成且超出复用窗口 / 放弃且无等待者）。需在持锁时调用。"""
        _now = time.time()
        _dead = [
            _rid for _rid, _e in self._entries.items()
            if (_e.status == _COMPLETED
                and (_now - _e.completed_at) > self._reuse_ttl
                and _e.waiters == 0)
            or (_e.status == _ABANDONED and _e.waiters == 0)
        ]
        for _rid in _dead:
            self._entries.pop(_rid, None)


# ---------------- 进程级单例 ----------------

_DEDUP_SINGLETON: RequestDeduplicator | None = None
_DEDUP_LOCK = threading.RLock()


def get_request_deduplicator() -> RequestDeduplicator | None:
    """取进程级单例；★总开关关闭时返回 None（接入点据此完全短路）。

    开关：`config.ENABLE_REQUEST_DEDUP`（默认 False）。
    """
    global _DEDUP_SINGLETON
    try:
        import config as _cfg
        if not getattr(_cfg, "ENABLE_REQUEST_DEDUP", False):
            return None
        _timeout = float(getattr(_cfg, "REQUEST_DEDUP_TIMEOUT_SEC", 30.0) or 30.0)
        _max_wait = int(getattr(_cfg, "REQUEST_DEDUP_MAX_WAITERS", 10) or 10)
        _ttl = float(getattr(_cfg, "REQUEST_DEDUP_REUSE_TTL_SEC", 5.0) or 5.0)
    except Exception as e:
        silent_exc(e, where="nucleus.field.RequestDeduplicator::get_request_deduplicator L359")
        return None

    with _DEDUP_LOCK:
        if _DEDUP_SINGLETON is None:
            _DEDUP_SINGLETON = RequestDeduplicator(
                timeout=_timeout, max_wait=_max_wait, reuse_ttl=_ttl)
        return _DEDUP_SINGLETON


def reset_request_deduplicator() -> None:
    """丢弃单例（测试用，保证用例间互不污染）。"""
    global _DEDUP_SINGLETON
    with _DEDUP_LOCK:
        if _DEDUP_SINGLETON is not None:
            _DEDUP_SINGLETON.shutdown()
        _DEDUP_SINGLETON = None
