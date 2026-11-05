# -*- coding: utf-8 -*-
"""453R 刀8：contextmanager 防信号量泄漏 + health 采样。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus import evidence_injection as EI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PMC = os.path.join(ROOT, "organs", "core", "PulseMetricsCollector.py")


def test_初始信号量为2():
    assert EI.retry_sem_available() == 2
    assert EI.stats()["retry_sem_limit"] == 2


def test_正常路径必归还():
    before = EI.retry_sem_available()
    with EI.retry_slot() as ok:
        assert ok is True
        assert EI.retry_sem_available() == before - 1
    assert EI.retry_sem_available() == before


def test_异常路径必归还():
    before = EI.retry_sem_available()
    # ★不用裸 except（cw2 未跟踪盲区会拦）：pytest.raises 表达同一意图
    with pytest.raises(RuntimeError), EI.retry_slot() as ok:
        assert ok is True
        raise RuntimeError("模拟重试内部抛异常")
    assert EI.retry_sem_available() == before, "泄漏了！"


def test_第三次抢不到且不影响已持有的():
    held = []
    for _ in range(2):
        assert EI.try_acquire_retry() is True
        held.append(1)
    try:
        assert EI.try_acquire_retry() is False
        assert EI.retry_sem_available() == 0
    finally:
        for _ in held:
            EI.release_retry()
    assert EI.retry_sem_available() == 2


def test_生产接线用contextmanager而不是裸acquire():
    with open(os.path.join(ROOT, "organs", "body", "PulseLung.py"),
             encoding="utf-8") as _f:
        src = _f.read()
    assert "with _ei453.retry_slot() as _m453_got" in src
    assert "_ei453.try_acquire_retry()" not in src
    assert "_ei453.release_retry()" not in src


def test_health快照里有m453_retry():
    with open(PMC, encoding="utf-8") as _f:
        src = _f.read()
    assert 'snapshot["m453_retry"]' in src
    assert "_m453r_ei.retry_sem_available()" in src
    assert "silent_exc(_m453r_he" in src


def test_stats含信号量剩余():
    s = EI.stats()
    assert "retry_sem_available" in s and "retry_sem_limit" in s
    assert s["retry_sem_available"] == EI.retry_sem_available()
