# -*- coding: utf-8 -*-
"""197刀3 隔离单测：LFI 对照拍补齐半刀（vessel 单例 getter + engine:407 传运行态 organ_activity）。

验收：vessel 模块级单例 getter 可用；engine 把 {器官: sent+received} 归并 int 传入 compute_lfi；
LFI 活跃度维运行态标记（不再被剔除）。
"""
import unittest
from unittest import mock

from organs.body.PulseBloodVessel import (
    PulseBloodVessel,
    get_blood_vessel,
    set_blood_vessel_instance,
)
from tools.export_lfi import compute_lfi


class TestM197Blade3(unittest.TestCase):
    def tearDown(self):
        set_blood_vessel_instance(None)  # 避免污染模块单例

    def test_singleton_getter(self):
        set_blood_vessel_instance(None)
        _a = get_blood_vessel()
        _b = get_blood_vessel()
        self.assertIs(_a, _b)
        self.assertIsInstance(_a, PulseBloodVessel)

    def test_set_instance_registers(self):
        _v = PulseBloodVessel("血管")
        set_blood_vessel_instance(_v)
        self.assertIs(get_blood_vessel(), _v)

    def test_compute_lfi_runtime_activity(self):
        _payload = compute_lfi(runtime_activity={"心脏": 5})
        _node = next(n for n in _payload["nodes"] if n["name"] == "心脏")
        self.assertEqual(_node["dims"]["activity"]["value"], 5)
        self.assertEqual(_node["dims"]["activity"]["approx"], "运行态(pulse计数)")

    def test_engine_passes_runtime_activity(self):
        from nucleus.self_awareness.SelfAwarenessEngine import SelfAwarenessEngine

        _e = SelfAwarenessEngine()
        _e._analyzers = []  # 跳过分析器，专测 LFI 块

        class _FakeVessel:
            def get_stats(self):
                return {"organ_activity": {"心脏": {"sent": 3, "received": 2}}}

        _captured = {}

        def _fake_compute(runtime_activity=None):
            _captured["runtime_activity"] = runtime_activity
            return {
                "version": "v1", "node_count": 1,
                "nodes": [{
                    "class_name": "PulseHeart", "name": "心脏", "category": "body",
                    "dims": {
                        "connectivity": 1, "centrality": 0.5, "redundancy": 0,
                        "health": {"score": None, "status": "未装配"},
                        "activity": {"value": 5, "approx": "运行态(pulse计数)"},
                    },
                }],
                "meta": {},
            }

        with mock.patch("organs.body.PulseBloodVessel.get_blood_vessel",
                        return_value=_FakeVessel()):
            with mock.patch("tools.export_lfi.compute_lfi", side_effect=_fake_compute):
                _e.run_all_analyses()

        # 归并 {心脏: sent+received} = {心脏: 5}
        self.assertEqual(_captured.get("runtime_activity"), {"心脏": 5})
        _lfi = _e.get_profile().lfi_raw
        self.assertIsNotNone(_lfi)
        _nd = next(n for n in _lfi["nodes"] if n["name"] == "心脏")
        self.assertIn("activity", _nd["dims"], "活跃度维被错误剔除（补齐半刀失效）")


if __name__ == "__main__":
    unittest.main()
