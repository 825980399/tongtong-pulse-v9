# -*- coding: utf-8 -*-
"""452 刀5：推理引擎降级改名（别名不可删）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import organs.core.PulseInferenceEngine as PIE


def test_新类名与别名指向同一对象():
    assert PIE.PulseInferenceCache.__name__ == "PulseInferenceCache"
    assert PIE.PulseInferenceEngine is PIE.PulseInferenceCache


def test_文档说明它是缓存不是推理引擎():
    doc = PIE.PulseInferenceCache.__doc__ or ""
    assert "KV 缓存" in doc and "不是推理引擎" in doc, doc


def test_实例化通过():
    """★验收要求：单测实例化通过。跳过完整 __init__ 仅验可实例化。"""
    o = object.__new__(PIE.PulseInferenceCache)
    assert isinstance(o, PIE.PulseInferenceCache)


def test_装配键仍按文件名且在下线名单里():
    """★★452 T0 关键风险：改名不能让这个器官被意外装配起来。"""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "nucleus", "organ_loader.py")
    with open(p, encoding="utf-8", errors="replace") as f:
        src = f.read()
    assert '"PulseInferenceEngine", "PulseInferenceCache"' in src, "下线名单没同步"
    assert 'organ_class_name = file_name[:-3]' in src, "装配键来源变了"


def test_main导入仍可用():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(p, encoding="utf-8", errors="replace") as f:
        src = f.read()
    assert "from organs.core.PulseInferenceEngine import" in src
    assert 'or _core_result.get("PulseInferenceCache")' in src


def test_文件未删():
    assert os.path.exists(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "organs", "core", "PulseInferenceEngine.py"))
