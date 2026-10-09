# -*- coding: utf-8 -*-
"""176批段4 A1 隔离单测：真多步推理每步补写 _last_deep_think_partial（灰度键）。

覆盖：
  1) 灰度键 ENABLE_MULTISTEP_PARTIAL_WRITE 默认 False ⇒ _m176_a1_multistep_partial_on 为 False（零回归）；
  2) 键置 True ⇒ 开关打开；且每步经 _m27_cache_partial(:15795 同构) 写入的形状正确
     （question=原问 / rounds=步数 / answer / elapsed / ts），与步骤循环内调用口径一致。

隔离纪律（铁律151）：清理 PYTHONPATH/PYTHONHOME/PYTHONSTARTUP + PYTHONNOUSERSITE；
用 __new__ 构造避开 __init__ 后台线程/重量初始化。
"""
import os

os.environ.pop("PYTHONPATH", None)
os.environ.pop("PYTHONHOME", None)
os.environ.pop("PYTHONSTARTUP", None)
os.environ["PYTHONNOUSERSITE"] = "1"

import config as _cfg_mod


def _make_iw():
    from organs.brain.PulseInnerWorld import PulseInnerWorld
    return PulseInnerWorld.__new__(PulseInnerWorld)


def test_multistep_partial_off_by_default(monkeypatch):
    monkeypatch.setattr(_cfg_mod, "ENABLE_MULTISTEP_PARTIAL_WRITE", False)
    iw = _make_iw()
    assert iw._m176_a1_multistep_partial_on() is False, \
        "灰度键默认 False ⇒ 开关关闭（零回归，不写部分结果）"


def test_multistep_partial_on_writes_shape(monkeypatch):
    monkeypatch.setattr(_cfg_mod, "ENABLE_MULTISTEP_PARTIAL_WRITE", True)
    iw = _make_iw()
    assert iw._m176_a1_multistep_partial_on() is True, "灰度键 True ⇒ 开关打开"

    # 模拟步骤循环内第 3 步（_i=2 ⇒ [None]*3）调用口径
    iw._m27_cache_partial("原始长链操作指令", [None, None, None], "步骤三的检索结论", 3.2)
    p = iw._last_deep_think_partial
    assert p is not None, "应写入 _last_deep_think_partial"
    assert p["question"] == "原始长链操作指令", "question 须用原始问题（防前30字不匹配）"
    assert p["rounds"] == 3, "rounds 须等于已完成步数 (_i+1)"
    assert p["answer"] == "步骤三的检索结论"
    assert p["elapsed"] == 3.2
    assert "ts" in p


def test_multistep_partial_empty_answer_skipped(monkeypatch):
    monkeypatch.setattr(_cfg_mod, "ENABLE_MULTISTEP_PARTIAL_WRITE", True)
    iw = _make_iw()
    # _m27_cache_partial 对空 answer 直接 return（同 :15806 语义）
    iw._m27_cache_partial("q", [None], "", 1.0)
    assert getattr(iw, "_last_deep_think_partial", "UNSET") == "UNSET", \
        "空 answer 不写入（与 :15800 一致）"
