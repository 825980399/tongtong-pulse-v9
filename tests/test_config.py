# -*- coding: utf-8 -*-
"""配置测试（≥5 例）：QICA 通道权重、L3 扩缩容配置、遥测/灰度开关等。

重点验证第5/6批新增配置项存在且默认值符合任务书，避免「配置真实性」类回归。
注意：大部分开关存放于 config.FEATURE 字典（非模块级属性），故统一从 FEATURE 读取。
"""
import pytest

import config


def _feat(name, default=None):
    # 第5/6批配置项分散在「模块级属性」与「FEATURE 字典」两处，统一优先取模块级。
    if hasattr(config, name):
        return getattr(config, name)
    return config.FEATURE.get(name, default)


class TestQICAWeights:
    CHANNELS = ("keyword", "semantic", "context", "entity",
                "sentence", "expansion", "complexity", "feedback")

    def test_V1权重含全部8通道(self):
        w = _feat("QICA_CHANNEL_WEIGHTS", {})
        for ch in self.CHANNELS:
            assert ch in w, f"V1 缺通道 {ch}"

    def test_V2权重含全部8通道且和约1(self):
        w = _feat("QICA_CHANNEL_WEIGHTS_V2", {})
        for ch in self.CHANNELS:
            assert ch in w, f"V2 缺通道 {ch}"
        assert abs(sum(w.values()) - 1.0) < 1e-6

    def test_V2权重默认关闭(self):
        assert _feat("QICA_USE_TUNED_WEIGHTS", True) is False

    def test_遥测开关默认开启(self):
        assert _feat("ENABLE_QICA_CHANNEL_TELEMETRY", False) is True


class TestL3ScaleConfig:
    def test_扩容冷却为15秒(self):
        assert _feat("l3_scale_up_cooldown", 0) == 15

    def test_缩容冷却为30秒(self):
        assert _feat("l3_scale_down_cooldown", 0) == 30

    def test_提前阈值0_70_0_85_0_95(self):
        assert _feat("l3_scale_up_thresholds", []) == [0.70, 0.85, 0.95]

    def test_突发检测默认开启(self):
        assert _feat("l3_burst_detection_enabled", False) is True

    def test_突发增长阈值0_50(self):
        # ★主线第7批 P1-67：阈值由 0.30 提高到 0.50，抑制误触发
        assert _feat("l3_burst_growth_threshold", 0) == 0.50

    def test_最小worker数为3(self):
        # ★主线第7批 P1-67：不允许降到 2，避免频繁触突发
        assert _feat("l3_min_workers", 0) == 3

    def test_缩容延迟缓冲为30秒(self):
        # ★主线第7批 P1-67：队列<=0.5 后需稳定 30s 才允许缩容
        assert _feat("l3_scale_down_buffer_sec", 0) == 30

    def test_动态扩缩容开关开启(self):
        assert _feat("l3_dynamic_scaling_enabled", False) is True


class TestPhase18AndEvolutionSwitch:
    def test_PHASE18遥测总开关存在(self):
        # 第5批预埋的 PHASE18 信号层总门控（FEATURE 或模块级均可）
        present = (hasattr(config, "ENABLE_PHASE18_SIGNALS")
                   or "ENABLE_PHASE18_SIGNALS" in config.FEATURE)
        assert present

    def test_进化闭环修复开关(self):
        assert _feat("PATCH_AUTO_APPROVE_ACCEPT_RUNTIME_VERIFIED", False) is True
        assert _feat("PATCH_AUTO_APPROVE_PRUNE_PENDING", False) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
