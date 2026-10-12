# -*- coding: utf-8 -*-
"""190刀4 隔离单测：enable_vision 键段考古自验证。

验收：enable_vision 归属 FEATURE 功能总开关段且为 True；与 Lung 零接线；
硬件探针另有独立 enable_vision 默认 False（两套语义）。
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import config  # noqa: E402


def test_enable_vision_in_feature_section_and_true():
    """config.py:1937 的 enable_vision 位于 FEATURE 功能总开关段且值为 True。"""
    assert hasattr(config, "FEATURE"), "config 应有 FEATURE 段"
    assert "enable_vision" in config.FEATURE, "enable_vision 应在 FEATURE 段"
    assert config.FEATURE["enable_vision"] is True, "config.py:1937 值应为 True"
    # 非顶层模块属性（须经 FEATURE 访问）
    assert not hasattr(config, "enable_vision"), "enable_vision 不应是顶层属性"


def test_no_lung_wiring_to_enable_vision():
    """PulseLung（含 _build_channel_messages :878 多模态序列化层）不读取 enable_vision —— 与 Lung 无接线。"""
    lung = open(os.path.join(_PROJECT_ROOT, "organs/body/PulseLung.py"), encoding="utf-8").read()
    assert "enable_vision" not in lung, "PulseLung 不应读取 enable_vision（与Lung无接线）"


def test_hardware_probe_has_separate_default_false():
    """硬件探针另有独立 enable_vision 默认 False（与 FEATURE 开关两套语义，勿混淆）。"""
    hp = open(os.path.join(_PROJECT_ROOT, "nucleus/hardware_probe.py"), encoding="utf-8").read()
    assert '"enable_vision": False' in hp, "hardware_probe 应有独立 enable_vision 默认 False"


if __name__ == "__main__":
    import unittest
    unittest.main()
