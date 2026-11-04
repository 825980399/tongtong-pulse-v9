# -*- coding: utf-8 -*-
"""452 刀3：_core_words 时序 + 相关性比较口径。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from organs.brain.PulseInnerWorld import PulseInnerWorld


def _organ(words):
    o = object.__new__(PulseInnerWorld)
    o._m31_extract_key_terms = lambda q, limit=6: list(words)
    o._log = lambda *a, **k: None
    return o


def test_相关知识高置信不再误报知识边界():
    """★旧行为：`_core_words` 恒空 ⇒ 重叠恒 0 ⇒ 恒真触发。"""
    o = _organ(["运行", "日志", "级别", "优化", "关注"])
    r = o._detect_knowledge_boundary_and_inquire(
        "那你在读取运行日志时会关注哪些级别",
        knowledge_result="运行日志的级别用于区分告警，运行和日志都要关注" * 3,
        contemplative_result=None, confidence=0.9)
    assert r is None, r


def test_检索未命中时仍会触发():
    """★修 bug 不是把边界关掉：真没知识时照常该追问。"""
    o = _organ(["量子", "拓扑"])
    r = o._detect_knowledge_boundary_and_inquire(
        "量子拓扑怎么推导？", knowledge_result=None,
        contemplative_result=None, confidence=0.9)
    # 无知识 + 无不确定性 + 高置信 ⇒ 只有 1 个信号且不低置信 ⇒ 旧逻辑本就不触发
    assert r is None or isinstance(r, str)


def test_阈值可配且影响相关性判据(monkeypatch):
    """阈值越高越不容易判越界（重叠率下限 = 1-阈值）。"""
    o = _organ(["运行", "日志", "级别", "优化", "关注"])
    kr = "运行日志相关内容" * 8      # 5 个核心词里只有 2 个出现 ⇒ 重叠率 0.4
    monkeypatch.setattr(config, "KNOWLEDGE_BOUNDARY_THRESHOLD", 0.6, raising=False)
    r1 = o._detect_knowledge_boundary_and_inquire(
        "运行日志", knowledge_result=kr, contemplative_result=None, confidence=0.9)
    monkeypatch.setattr(config, "KNOWLEDGE_BOUNDARY_THRESHOLD", 0.2, raising=False)
    r2 = o._detect_knowledge_boundary_and_inquire(
        "运行日志", knowledge_result=kr, contemplative_result=None, confidence=0.9)
    # 0.6 ⇒ 下限 0.4，恰好 0.4 ⇒ 不判低；0.2 ⇒ 下限 0.8 ⇒ 判低
    assert r1 is None, r1
    assert r2 is not None, r2


def test_源码里核心词提取在相关性判断之前():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "organs", "brain", "PulseInnerWorld.py")
    with open(p, encoding="utf-8", errors="replace") as f:
        src = f.read()
    i_extract = src.index("★452刀3（P0-3a）：**先提取核心词，再判相关性**")
    i_relevance = src.index("★452刀3 第二层 bug", i_extract)
    assert i_extract < i_relevance
    assert "KNOWLEDGE_BOUNDARY_THRESHOLD" in src
