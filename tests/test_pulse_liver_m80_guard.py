# -*- coding: utf-8 -*-
"""第80批 T3/P0-4 肝融合 float<dict 崩溃 —— 真实调用失败型回归（补 m79 遗留）。

m79 测试 test_pulse_liver_m79.py 把 `_fuse_l2_to_l3` 整体 MagicMock，未真实驱动
事故路径。本测试**真实调用** `_fuse_l2_to_l3`：
- 真实 PulseLiver()（验证 __init__ 把 `_adaptive_fuse_blocked_until` 初始化为 float 0.0，
  而非旧的 {}，此为 21:15 [肝]ERROR `'<' not supported between float and dict` 根因）；
- 真实 PulseNode L2 节点 + 真实分组/阈值/冷却逻辑；
- 仅桩掉下游 `_fuse_group`（融合写库动作）与 node_pool 外部协作者；
- 事故注入 `_adaptive_fuse_blocked_until = {}`，断言 :2393 类型守卫生效、方法不抛
  TypeError 且能穿过守卫到达融合点。

修复前：:2396 `now < {}` 直接 TypeError（本测试必红）；
修复后：:2394 isinstance 守卫把非数值重置为 0.0（本测试转绿）。
"""
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.body.PulseLiver import PulseLiver  # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402


class TestFuseGuardM80(unittest.TestCase):
    def setUp(self):
        self.o = PulseLiver(organ_name="肝")
        self.o.info_field = mock.MagicMock()
        self.o.pulse_core = mock.MagicMock()
        for _s in ("set_node_pool", "set_knowledge_tree", "set_code_learner",
                   "set_frequency_codec", "set_resonance_engine",
                   "set_snapshot", "set_reasoning_pool"):
            _f = getattr(self.o, _s, None)
            if callable(_f):
                _f(mock.MagicMock())
        self.addCleanup(self._safe_stop)

    def _safe_stop(self):
        try:
            self.o.stop()
        except Exception:
            pass

    def test_01_init_blocked_until_is_float(self):
        """根因：__init__ 后全局融合阻塞标记必须是 float 0.0，不能是 dict。"""
        v = self.o._adaptive_fuse_blocked_until
        self.assertEqual(v, 0.0)
        self.assertIsInstance(v, float)

    def _make_l2_nodes(self, n=7, high=4):
        """构造同组 L2 节点：数量落在自适应分支（阈值8的0.7~1之间），含 high 个 S 级。"""
        nodes = []
        for i in range(n):
            imp = "S" if i < high else "C"
            nodes.append(PulseNode(
                value=f"融合守卫真实回归测试节点内容_{i}",
                keywords=[f"kw{i}"],
                source_organ="肝测试",
                evol_level=PulseNode.EVOL_L2,
                importance=imp,
                space_path="/测试/融合守卫/",
            ))
        return nodes

    def test_02_dict_blocked_until_does_not_crash_fuse(self):
        """事故值 {} 下真实调用 _fuse_l2_to_l3 不抛 TypeError，且穿过守卫到达融合点。"""
        nodes = self._make_l2_nodes()
        # node_pool 为外部协作者：query 返回真实 L2 节点，其余方法安全桩
        self.o.node_pool = mock.MagicMock()
        self.o.node_pool.query = mock.MagicMock(return_value=nodes)
        # 固定阈值=8，使 7 节点进入自适应分支（len>=5.6 且 <8）
        with mock.patch.object(self.o, "_get_l2_threshold", return_value=8), \
             mock.patch.object(self.o, "_fuse_group",
                               return_value=["fused_l3_node"]) as fuse_mock:
            # ★事故注入：复现旧进程把全局阻塞标记污染成 dict
            self.o._adaptive_fuse_blocked_until = {}
            self.o._adaptive_fuse_blocked_paths = {}
            self.o._last_fuse_time = 0.0
            self.o._last_adaptive_fuse_time = 0.0
            self.o._stop_requested = False
            try:
                self.o._fuse_l2_to_l3()
            except TypeError as _e:
                self.fail(f"_fuse_l2_to_l3 仍在 dict 阻塞标记下崩溃(float<dict): {_e}")
            # 穿过 :2393 守卫、进入融合 → _fuse_group 必须被真实调用到
            self.assertGreaterEqual(fuse_mock.call_count, 1,
                                    "未到达融合点：守卫或前置条件阻断了自适应分支")


if __name__ == "__main__":
    unittest.main(verbosity=2)
