# -*- coding: utf-8 -*-
"""484-1 门控：关停 379 生命线前缀强制（先红后绿）。

★T0 偏差 D4/D5/D8：heart.alive 与 system.status.request 的运行时发射点显式传 L3，
关掉 379 只影响「未显式传 layer」的 emit 与「显式 L3 被归 L1」的降级路径；
本批不擅改 PulseHeart。D8 决定双路径保留旧代码，回滚需组合两把开关。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.const import HeartEvent, SystemEvent
from nucleus.pulse.PulseCore import PulseCore

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Switch:
    """临时改写 config 模块属性（含「属性本不存在」的情形）。"""

    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._old[k] = (hasattr(config, k), getattr(config, k, None))
            setattr(config, k, v)
        return self

    def __exit__(self, *a):
        for k, (existed, val) in self._old.items():
            if existed:
                setattr(config, k, val)
            else:
                # __enter__ 必然已 setattr，delattr 不会抛；不写 try/except
                # 以免被 cw2_t2e 静默 except 门禁判为新增静默 handler。
                delattr(config, k)
        return False


class TestM379Off(unittest.TestCase):
    def setUp(self):
        self.pc = PulseCore(max_completed_fingerprints=8)

    def test_01_default_flag_is_false(self):
        """484-1①：默认关停 379 生命线前缀强制。"""
        self.assertIs(getattr(config, "ENABLE_M379_LIFELINE_LAYER", None), False)

    def test_02_infer_beat_is_l3(self):
        """484-2 覆盖后 heart.beat 未显式声明时归 L3（改前 379 恒 L1）。"""
        self.assertEqual(self.pc._infer_layer(HeartEvent.BEAT), "L3")

    def test_03_infer_alive_is_l0(self):
        """heart.alive 回到 _DEFAULT_LAYER_MAP 的 L0（改前 379 恒 L1）。"""
        self.assertEqual(self.pc._infer_layer(HeartEvent.ALIVE), "L0")

    def test_04_explicit_l3_not_downgraded(self):
        """484-1③：显式 L3 的生命线不再被 379 归到 L1。"""
        p = self.pc.emit("acc", HeartEvent.BEAT, {}, layer="L3")
        self.assertEqual(p.get("layer"), "L3")

    def test_05_status_request_explicit_l3_kept(self):
        """D5：显式 L3 的 status request 保持 L3，不在本批改 Heart。"""
        p = self.pc.emit("acc", SystemEvent.STATUS_REQUEST, {}, layer="L3")
        self.assertEqual(p.get("layer"), "L3")

    def test_06_resolve_layer_is_noop_when_off(self):
        """_m379_resolve_layer 在开关关掉后必须原样返回。"""
        self.assertEqual(self.pc._m379_resolve_layer(HeartEvent.BEAT, "L3"), "L3")
        self.assertEqual(self.pc._m379_resolve_layer(HeartEvent.BEAT, "L2"), "L2")

    def test_07_rollback_combo_reproduces_legacy(self):
        """D8：379 开 + 注册表关 ⇒ 逐字复现改前（beat/alive 都 L1）。"""
        with _Switch(ENABLE_M379_LIFELINE_LAYER=True,
                     ENABLE_M484_EVENT_REGISTRY=False):
            self.assertEqual(self.pc._infer_layer(HeartEvent.BEAT), "L1")
            self.assertEqual(self.pc._infer_layer(HeartEvent.ALIVE), "L1")
            self.assertEqual(self.pc._infer_layer("acc.test.neutral"), "L1")

    def test_08_flag_on_alone_still_guarded_by_registry(self):
        """只把 379 开回来、注册表仍开 ⇒ 注册表是单一真源，不被前缀规则抢先。"""
        with _Switch(ENABLE_M379_LIFELINE_LAYER=True):
            self.assertEqual(self.pc._infer_layer(HeartEvent.BEAT), "L3")
            self.assertEqual(self.pc._infer_layer(HeartEvent.ALIVE), "L0")


if __name__ == "__main__":
    unittest.main()
