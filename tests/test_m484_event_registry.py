# -*- coding: utf-8 -*-
"""484-2 门控：事件分层注册表（先红后绿）。

★T0 偏差 D6/D7/D8/D14：注册表以 _DEFAULT_LAYER_MAP 全量为基线吸收，
只按任务书覆盖 3 条；不删旧路径，开关关掉逐字复现旧行为；
新增 .py 不写裸点分事件名（event-string 门禁）。
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from nucleus.const import (
    Event,
    HeartEvent,
    InterestEvent,
    SystemEvent,
    VascularEvent,
)
from nucleus.pulse.PulseCore import _DEFAULT_LAYER_MAP, PulseCore

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REG = os.path.join(_ROOT, "nucleus", "event_layers.py")


def _read_text(path):
    """统一用上下文管理器读源码：避免 SIM115 / UP020 净增。"""
    with open(path, encoding="utf-8") as _fh:
        return _fh.read()


class _Switch:
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


class TestEventRegistry(unittest.TestCase):
    def setUp(self):
        self.pc = PulseCore(max_completed_fingerprints=8)
        if os.path.isfile(_REG):
            import nucleus.event_layers as _m
            self.reg = _m.EVENT_LAYER_MAP
        else:
            self.reg = None

    def test_01_module_exists(self):
        self.assertTrue(os.path.isfile(_REG), "nucleus/event_layers.py 缺失")
        self.assertIsInstance(self.reg, dict)
        self.assertGreaterEqual(len(self.reg), 28)

    def test_02_absorbs_full_default_map(self):
        """D6：_DEFAULT_LAYER_MAP 的 23 条一个都不能漏（漏了会被兜底 L3 误改层）。"""
        self.assertIsNotNone(self.reg)
        missing = [k for k in _DEFAULT_LAYER_MAP if k not in self.reg]
        self.assertEqual(missing, [], f"注册表漏登记: {missing}")

    def test_03_only_three_intentional_overrides(self):
        """D6/D11：只有 beat / interest.changed / 新增 status.request 与旧表不同。"""
        self.assertIsNotNone(self.reg)
        diff = {k: (v, self.reg[k]) for k, v in _DEFAULT_LAYER_MAP.items()
                if self.reg[k] != v}
        self.assertEqual(set(diff), {HeartEvent.BEAT, InterestEvent.CHANGED})

    def test_04_beat_l3(self):
        self.assertEqual(self.reg.get(HeartEvent.BEAT), "L3")

    def test_05_alive_l0(self):
        self.assertEqual(self.reg.get(HeartEvent.ALIVE), "L0")

    def test_06_interest_changed_l2(self):
        self.assertEqual(self.reg.get(InterestEvent.CHANGED), "L2")

    def test_07_status_request_l2(self):
        """D11：system.status.request 登记 L2，不是 L1。"""
        self.assertEqual(self.reg.get(SystemEvent.STATUS_REQUEST), "L2")

    def test_08_silent_organ_l0(self):
        self.assertEqual(self.reg.get(VascularEvent.SILENT_ORGAN), "L0")

    def test_09_organ_handbook_updated_l2(self):
        """D7：任务书清单漏了真实事件，本批主动补登记 L2。"""
        self.assertEqual(self.reg.get(Event.ORGAN_HANDBOOK_UPDATED), "L2")

    def test_10_unknown_event_falls_back_to_l3(self):
        self.assertEqual(self.pc._infer_layer("acc.test.neutral"), "L3")

    def test_11_unknown_event_emits_warning(self):
        with self.assertLogs("pulse.resonance", level="WARNING") as _cm:
            self.pc._infer_layer("acc.test.neutral")
        self.assertTrue(any("484" in m or "未登记" in m for m in _cm.output),
                        f"未见注册表兜底 WARNING: {_cm.output}")

    def test_12_registry_off_reproduces_legacy(self):
        """D8：开关关掉逐字回旧路径（379 也关时走 _DEFAULT_LAYER_MAP）。"""
        with _Switch(ENABLE_M484_EVENT_REGISTRY=False):
            self.assertEqual(self.pc._infer_layer(HeartEvent.BEAT), "L0")
            self.assertEqual(self.pc._infer_layer(HeartEvent.ALIVE), "L0")
            self.assertEqual(self.pc._infer_layer("acc.test.neutral"), "L1")

    def test_13_no_bare_dot_event_literals(self):
        """D14：新模块不得出现 const.py 已登记的点分事件名裸字符串。"""
        self.assertTrue(os.path.isfile(_REG))
        const = _read_text(os.path.join(_ROOT, "nucleus", "const.py"))
        ctree = ast.parse(const)
        values = set()
        for node in ast.walk(ctree):
            if isinstance(node, ast.ClassDef):
                for st in node.body:
                    if isinstance(st, ast.Assign) and isinstance(st.value, ast.Constant):
                        if isinstance(st.value.value, str) and "." in st.value.value:
                            values.add(st.value.value)
        rtree = ast.parse(_read_text(_REG))
        bad = []
        for node in ast.walk(rtree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in values:
                    bad.append(node.value)
        self.assertEqual(bad, [], f"裸点分事件名: {bad}")

    def test_14_d12_whitelist_fixed_in_config(self):
        """D12 / 483 遗留：白名单用真事件类属性，且死的裸串已移除。"""
        tree = ast.parse(_read_text(os.path.join(_ROOT, "config.py")))
        hit = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(getattr(t, "id", "") == "M483_L0_LIFELINE_EVENTS"
                       for t in node.targets):
                continue
            els = node.value.elts if isinstance(node.value, (ast.Tuple, ast.List)) else []
            hit.append((
                any(isinstance(e, ast.Attribute) and e.attr == "SILENT_ORGAN" for e in els),
                any(isinstance(e, ast.Constant) and e.value == "vascular.silent_organ"
                    for e in els),
            ))
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0], (True, False))


if __name__ == "__main__":
    unittest.main()
