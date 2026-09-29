"""第82批 T-c：五维共振权重单一来源（Dxxx/B宪法）。

背景：
  ResonanceEngine.py:38 的类属性 WEIGHTS 与 config.py:668 的 RESONANCE_WEIGHTS
  数值完全相同（0.40/0.30/0.15/0.10/0.05），但 config 那份全仓 0 引用、是死定义，
  构成"一死一活双源"——未来改一处忘另一处即名实脱节。

收敛方向（守红线：config.py 一行不改，不动任何运行开关）：
  以 config.RESONANCE_WEIGHTS 为唯一定义源，ResonanceEngine.WEIGHTS 改为
  对它的只读引用。已确认全仓 WEIGHTS 只读（加权求和 + .copy()），无 mutate，
  引用同一 dict 不会污染 config。
"""
from __future__ import annotations

import config
from nucleus.synapsys.ResonanceEngine import ResonanceEngine


class TestSingleSource:
    def test_weights_is_same_object_as_config(self):
        # 红：改前 WEIGHTS 是类内字面 dict，与 config.RESONANCE_WEIGHTS 不是同一对象
        assert ResonanceEngine.WEIGHTS is config.RESONANCE_WEIGHTS, (
            "五维权重必须单一来源：ResonanceEngine.WEIGHTS 应直接引用 "
            "config.RESONANCE_WEIGHTS，不得在类内重复写字面数值"
        )

    def test_five_dims_present_and_sum_one(self):
        w = ResonanceEngine.WEIGHTS
        assert set(w.keys()) == {"memory", "space", "logic", "time", "state"}
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_values_unchanged(self):
        w = ResonanceEngine.WEIGHTS
        assert w["memory"] == 0.40
        assert w["space"] == 0.30
        assert w["logic"] == 0.15
        assert w["time"] == 0.10
        assert w["state"] == 0.05

    def test_config_dead_definition_is_the_source(self):
        # 反向锁定：config.RESONANCE_WEIGHTS 必须存在且值正确（它就是唯一来源）
        assert hasattr(config, "RESONANCE_WEIGHTS")
        assert config.RESONANCE_WEIGHTS["memory"] == 0.40
