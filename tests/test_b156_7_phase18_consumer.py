# -*- coding: utf-8 -*-
"""B156-7 遥测三缺 · 门控测试。

验证 snapshot() 消费方接入点已闭合（对应三真缺②零消费方）：
  1. ``Phase18Signals.register_snapshot_consumer`` 可注册并派发；
  2. 模块级 ``register_phase18_snapshot_consumer`` 委托进程单例；
  3. 消费方异常被静默吞掉（不污染采集器）；
  4. None 注册被忽略。

默认开关仍为 False，本测试不触发任何采集（仅验证接入点契约）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.telemetry.phase18_signals import (  # noqa: E402
    Phase18Signals,
    get_phase18_signals,
    register_phase18_snapshot_consumer,
)


class TestSnapshotConsumerRegistration:
    def test_register_and_dispatch(self):
        _sig = Phase18Signals()
        _received = []
        _sig.register_snapshot_consumer(lambda s: _received.append(s))
        assert _sig.snapshot_consumer_count == 1
        _n = _sig.dispatch_snapshot()
        assert _n == 1
        assert len(_received) == 1
        _snap = _received[0]
        assert isinstance(_snap, dict)
        assert "enabled" in _snap

    def test_none_registration_ignored(self):
        _sig = Phase18Signals()
        _sig.register_snapshot_consumer(None)
        assert _sig.snapshot_consumer_count == 0

    def test_consumer_exception_swallowed(self):
        _sig = Phase18Signals()

        def _boom(_s):
            raise RuntimeError("boom")

        _sig.register_snapshot_consumer(_boom)
        # 异常不应上抛，且派发计数为 0（失败方不计入成功派发）
        _n = _sig.dispatch_snapshot()
        assert _n == 0

    def test_module_level_delegates_to_singleton(self):
        _captured = []
        register_phase18_snapshot_consumer(lambda s: _captured.append(s))
        assert get_phase18_signals().snapshot_consumer_count >= 1
        # 清理：避免污染进程单例（其他测试模块可能复用）
        get_phase18_signals()._snapshot_consumers.clear()
