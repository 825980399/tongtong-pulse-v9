# -*- coding: utf-8 -*-
"""451 刀3：语义增强零重叠 WARNING → INFO（留证不打折）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_P = os.path.join(_ROOT, "organs", "motor", "PulseController.py")


@pytest.fixture(scope="module")
def src():
    with open(_P, encoding="utf-8", errors="replace") as f:
        return f.read()


def test_该条已改INFO(src):
    assert "语义增强提取与主题零重叠" in src
    # 该行前面必须是 INFO，不能还是 WARNING
    _i = src.index("语义增强提取与主题零重叠，丢弃并回退硬切: ")
    _head = src[max(0, _i - 200):_i]
    assert "LogLevel.INFO" in _head, _head[-160:]
    assert "LogLevel.WARNING,\n                              f\"语义增强" not in src


def test_留证文案一字未改(src):
    """★降级只是级别；提取词与主题仍必须打出来（否则排障时看不出丢了什么）。"""
    assert "f\"提取={semantic_kws} 主题='{str(search_topic)[:40]}'\"" in src


def test_仍保留窗口去重(src):
    """450 的 1800s 窗口保留 —— INFO 也不该一分钟刷十几条。"""
    assert "ctrl.semantic_zero_overlap" in src
    assert "M450_CTRL_SEMANTIC_RENOTIFY_SEC" in src


def test_同文件其它告警未被误降(src):
    """降级只针对这一条；控制器的超时族聚合仍是 WARNING 出口。"""
    assert "_m450_log_exc" in src
    assert "M450_CTRL_EXC_STREAK_THRESHOLD" in src
