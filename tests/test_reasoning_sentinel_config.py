# -*- coding: utf-8 -*-
"""★第245批 刀4：旁路哨兵配置化注入式单测（逻辑层验证零行为守恒）。

不依赖真实 config 默认值，monkeypatch config 双键为小值制造可控窗，
直接操纵实例计数器并调用 _sentinel_check_window：
  ① gap>0 连续两窗 -> 断言 WARNING 调用（守卫可证伪预言）；
  ② 平衡注入（success==distilled） -> 断言静默（consec 归零、无 WARNING）。
无 try/except（铁律150 合规，cw2 静默 handler 门不触发）。
"""
import logging
import os

import pytest
from unittest.mock import MagicMock

from nucleus.reasoning.ReasoningExperienceIndexer import (
    _STATE_PATH,
    ReasoningExperienceIndexer,
)


@pytest.fixture(autouse=True)
def _clean_indexer_state():
    """★第165批 刀A5：测试前后清空计数持久化状态文件，避免跨测试 base 累计污染断言。"""
    if os.path.exists(_STATE_PATH):
        os.remove(_STATE_PATH)
    yield
    if os.path.exists(_STATE_PATH):
        os.remove(_STATE_PATH)


class _StubNodePool:
    def add(self, node):
        return "node-x"

    def get(self, nid):
        return None


class _StubQueue:
    def submit(self, nid, value):
        pass


def _make_indexer():
    exp = MagicMock()
    exp._experiences = []
    pool = _StubNodePool()
    queue = _StubQueue()
    idx = ReasoningExperienceIndexer(reasoning_exp=exp, node_pool=pool, encode_queue=queue)
    idx.set_enabled(True)
    return idx


def test_sentinel_gap_two_windows_triggers_warning(monkeypatch, caplog):
    import config
    monkeypatch.setattr(config, "REASONING_SENTINEL_WINDOW", 2)
    monkeypatch.setattr(config, "REASONING_SENTINEL_CONSEC", 2)
    idx = _make_indexer()
    with caplog.at_level(logging.WARNING, logger="nucleus.reasoning.ReasoningExperienceIndexer"):
        # 窗1：success=3, distilled=0 -> gap=3>0, consec=1（不告警）
        idx._sentinel_success = 3
        idx._sentinel_distilled = 0
        idx._sentinel_distilled_delegated = 0
        idx._sentinel_check_window()
        # 窗2：再喂 success=3 -> gap=3>0, consec=2 -> 告警
        idx._sentinel_success = 3
        idx._sentinel_check_window()
    _warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(_warns) == 1
    assert "旁路哨兵" in _warns[0].getMessage()


def test_sentinel_balanced_injection_silent(monkeypatch, caplog):
    import config
    monkeypatch.setattr(config, "REASONING_SENTINEL_WINDOW", 2)
    monkeypatch.setattr(config, "REASONING_SENTINEL_CONSEC", 2)
    idx = _make_indexer()
    with caplog.at_level(logging.WARNING, logger="nucleus.reasoning.ReasoningExperienceIndexer"):
        # 平衡注入：每窗 success==distilled -> gap=0 -> consec 恒为 0
        idx._sentinel_success = 3
        idx._sentinel_distilled = 3
        idx._sentinel_distilled_delegated = 0
        idx._sentinel_check_window()
        idx._sentinel_success = 3
        idx._sentinel_distilled = 3
        idx._sentinel_check_window()
    _warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(_warns) == 0
    assert idx._sentinel_consec == 0
