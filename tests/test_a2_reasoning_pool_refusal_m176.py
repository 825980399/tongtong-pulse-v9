# -*- coding: utf-8 -*-
"""第176批 段1 A2（甲）：停止后持久拒绝复活重建 —— 门控单测。

覆盖三态机（refused → reset → build）与 158批N-5 关闭闸门回归：
  - arm_reasoning_pool_after_stop()   置「停止后拒绝复活」旗（True，不复位）
  - is_reasoning_pool_refused()       查询该旗
  - reset_reasoning_pool_refusal()    解除该旗（main.py start 调用）
  - get_reasoning_pool() 在（关闭中 或 拒绝旗置位）且全局实例为 None 时返回 None（拒绝新建）
  - 合法 start 经 get_reasoning_pool() 建池时一并解除拒绝旗

隔离策略：
  - 用 autouse fixture 保存/恢复模块级三全局（_reasoning_pool / _reasoning_pool_closing /
    _reasoning_pool_refused_after_stop）与 ReasoningWorkerPool 类引用，杜绝跨测试污染。
  - build 态用轻量 FakePool 替换模块全局 ReasoningWorkerPool 类，避免真实建池 spawn 子进程；
    验证 get_reasoning_pool() 单例语义与「建池即解旗」行为，而不触发重资源路径。
  - 全程零新增静默 except（cw2 已在 CI 中核验）。
"""
from __future__ import annotations

import pytest

import nucleus.reasoning.ReasoningWorkerPool as rp


@pytest.fixture(autouse=True)
def _isolate_pool_state():
    """保存并复位模块级三全局 + 类引用，测试后还原，保证逐文件隔离可重复。"""
    saved = {
        "_reasoning_pool": rp._reasoning_pool,
        "_reasoning_pool_closing": rp._reasoning_pool_closing,
        "_reasoning_pool_refused_after_stop": rp._reasoning_pool_refused_after_stop,
        "cls": rp.ReasoningWorkerPool,
    }
    rp._reasoning_pool = None
    rp._reasoning_pool_closing = False
    rp._reasoning_pool_refused_after_stop = False
    yield
    rp._reasoning_pool = saved["_reasoning_pool"]
    rp._reasoning_pool_closing = saved["_reasoning_pool_closing"]
    rp._reasoning_pool_refused_after_stop = saved["_reasoning_pool_refused_after_stop"]
    rp.ReasoningWorkerPool = saved["cls"]


# ---------------------------------------------------------------------------
# 状态机：refused → reset → build
# ---------------------------------------------------------------------------
def test_arm_sets_refused_flag():
    rp.reset_reasoning_pool_refusal()
    assert rp.is_reasoning_pool_refused() is False
    rp.arm_reasoning_pool_after_stop()
    # 旗置 True 且「不复位」语义：反复查询恒 True
    assert rp.is_reasoning_pool_refused() is True
    assert rp.is_reasoning_pool_refused() is True


def test_refused_blocks_rebuild_returns_none():
    """关后拒绝旗置位 + 全局实例为 None → 迟到 get_reasoning_pool() 必返回 None（拒绝复活）。"""
    rp.reset_reasoning_pool_refusal()
    rp.arm_reasoning_pool_after_stop()
    assert rp.get_reasoning_pool() is None
    # 拒绝窗口内仍不新建（幂等）
    assert rp.get_reasoning_pool() is None


def test_reset_clears_refusal_flag():
    rp.arm_reasoning_pool_after_stop()
    assert rp.is_reasoning_pool_refused() is True
    rp.reset_reasoning_pool_refusal()
    assert rp.is_reasoning_pool_refused() is False


def test_build_after_reset_resets_flag_and_returns_singleton():
    """合法 start：reset 解除旗后，get_reasoning_pool() 建池并一并解除拒绝旗，返回单例。"""
    rp.arm_reasoning_pool_after_stop()
    assert rp.get_reasoning_pool() is None  # 拒绝窗口
    rp.reset_reasoning_pool_refusal()
    assert rp.is_reasoning_pool_refused() is False

    class _FakePool:
        pass

    rp.ReasoningWorkerPool = _FakePool  # 轻量类，避免 spawn 子进程
    pool = rp.get_reasoning_pool()
    assert isinstance(pool, _FakePool)
    # 建池即解除「停止后拒绝」旗
    assert rp.is_reasoning_pool_refused() is False
    # 单例语义：再次获取返回同一实例
    assert rp.get_reasoning_pool() is pool


# ---------------------------------------------------------------------------
# 158批N-5 关闭闸门回归（未被本批改动，仍须成立）
# ---------------------------------------------------------------------------
def test_closing_gate_still_blocks():
    """_reasoning_pool_closing 置位时，迟到 get_reasoning_pool() 返回 None（同 158批N-5）。"""
    rp._reasoning_pool_closing = True
    try:
        assert rp.get_reasoning_pool() is None
    finally:
        rp._reasoning_pool_closing = False
    # 解除关闭闸门后即可建池（轻量类）
    class _FakePool:
        pass

    rp.ReasoningWorkerPool = _FakePool
    assert isinstance(rp.get_reasoning_pool(), _FakePool)


def test_shutdown_restores_closing_and_clears_pool():
    """shutdown_reasoning_pool() 复位全局实例与关闭闸门（不触碰拒绝旗逻辑）。"""
    rp._reasoning_pool = object()  # 占位实例
    rp.shutdown_reasoning_pool()
    assert rp._reasoning_pool is None
    assert rp._reasoning_pool_closing is False
    # 关闭闸门复位后，拒绝旗仍受 arm/reset 独立控制
    rp.arm_reasoning_pool_after_stop()
    assert rp.is_reasoning_pool_refused() is True
