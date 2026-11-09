# -*- coding: utf-8 -*-
"""483 批 · 刀 483-2「L0 队列上限 0→5000」门控单测（烛微设计 §2.5 矩阵）。

用桩池提供 ``_work_queue.qsize()``：不起线程、不碰活体、不写生产数据。
被测对象一律是 ``InfoField.__new__`` + **真实方法**（不复制判定逻辑）。
"""
import ast
import os
import threading
import unittest

import config
from nucleus.const import VascularEvent
from nucleus.field.InfoField import InfoField


def _read_src(path):
    with open(path, encoding="utf-8") as _f:
        return _f.read()


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IF_SRC = os.path.join(ROOT, "nucleus", "field", "InfoField.py")


class _Q:
    def __init__(self, depth):
        self.depth = depth

    def qsize(self):
        if self.depth == "raise":
            raise RuntimeError("qsize unavailable (stub)")
        return self.depth


class _StubPool:
    def __init__(self, depth):
        self._work_queue = _Q(depth)


def _mk_field(cap=5000):
    f = InfoField.__new__(InfoField)
    f._layer_queue_limits = {"L0": cap, "L1": 500, "L2": 300, "L3": 300}
    f._layer_rejected_count = {"L0": 0, "L1": 0, "L2": 0, "L3": 0}
    f._layer_overflow_allowed = {"L0": 0, "L1": 0, "L2": 0, "L3": 0}
    f._layer_lifeline_admitted = {"L0": 0, "L1": 0, "L2": 0, "L3": 0}
    f._layer_last_reject_ts = {"L0": None, "L1": None, "L2": None, "L3": None}
    f._overflow_priority_allow = 3
    f._lock = threading.Lock()
    f._layer_pools = {}
    f._organ_concurrent_lock = None
    f.pulse_core = None
    return f


def _admit(f, depth, priority=5, event_type="system.status.request"):
    return f._admit_layer_task("L0", _StubPool(depth), "organ_x",
                               priority, event_type)


class TestM483L0Cap(unittest.TestCase):
    """§2.5 七用例矩阵。上限 L=5000，硬顶 = L×4 = 20000。"""

    def test_01_under_limit_pass(self):
        f = _mk_field()
        self.assertTrue(_admit(f, 4999, 7))
        self.assertEqual(f._layer_rejected_count["L0"], 0)

    def test_02_at_limit_reject(self):
        f = _mk_field()
        self.assertFalse(_admit(f, 5000, 7))
        self.assertEqual(f._layer_rejected_count["L0"], 1)
        self.assertIsInstance(f._layer_last_reject_ts["L0"], float)

    def test_03_high_priority_at_limit_allowed(self):
        f = _mk_field()
        self.assertTrue(_admit(f, 5000, 3))
        self.assertEqual(f._layer_overflow_allowed["L0"], 1)
        self.assertEqual(f._layer_rejected_count["L0"], 0)

    def test_04_lifeline_whitelist_admitted(self):
        f = _mk_field()
        self.assertTrue(_admit(f, 5000, 7, "system.alarm"))
        self.assertEqual(f._layer_lifeline_admitted["L0"], 1)
        self.assertEqual(f._layer_rejected_count["L0"], 0)

    def test_05_hard_cap_blocks_even_whitelist(self):
        f = _mk_field()
        self.assertFalse(_admit(f, 20000, 3, "system.alarm"))
        self.assertEqual(f._layer_lifeline_admitted["L0"], 0)
        self.assertEqual(f._layer_rejected_count["L0"], 1)

    def test_06_switch_off_is_old_behavior(self):
        """开关 False ⇒ 有效上限 0，逐字回到「L0 永不拒绝」。"""
        _old = getattr(config, "ENABLE_M483_L0_QUEUE_CAP", None)
        config.ENABLE_M483_L0_QUEUE_CAP = False
        try:
            self.assertEqual(InfoField._m483_l0_cap_value(), 0)
            f = _mk_field(0)
            self.assertTrue(_admit(f, 5000, 7, "system.alarm"))
            self.assertEqual(f._layer_lifeline_admitted["L0"], 0)
        finally:
            if _old is None:
                delattr(config, "ENABLE_M483_L0_QUEUE_CAP")
            else:
                config.ENABLE_M483_L0_QUEUE_CAP = _old
        self.assertEqual(InfoField._m483_l0_cap_value(), 5000)

    def test_07_qsize_unavailable_fail_open(self):
        f = _mk_field()
        self.assertTrue(_admit(f, "raise", 7))
        self.assertEqual(f._layer_rejected_count["L0"], 0)

    # ---- 接线 / 观测 / 契约 ----

    def test_08_backpressure_three_new_keys(self):
        f = _mk_field()
        _admit(f, 5000, 7, "system.alarm")
        _admit(f, 5000, 3)
        _admit(f, 5000, 7)
        stat = f.get_backpressure_stats()["L0"]
        for k in ("queue_depth", "limit", "rejected",
                  "lifeline_admitted", "overflow_allowed", "last_reject_ts"):
            self.assertIn(k, stat)
        self.assertEqual(stat["limit"], 5000)
        self.assertEqual(stat["lifeline_admitted"], 1)
        self.assertEqual(stat["overflow_allowed"], 1)
        self.assertEqual(stat["rejected"], 1)
        self.assertIsInstance(stat["last_reject_ts"], float)

    def test_09_event_type_wired_at_unique_callsite(self):
        """唯一调用点必须把 event_type 传进来，否则白名单恒不命中。"""
        tree = ast.parse(_read_src(IF_SRC))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_admit_layer_task"]
        self.assertEqual(len(calls), 1, "唯一调用点被改多/改少了")
        self.assertEqual(len(calls[0].args), 5, "调用点必须传 5 个位置参数")
        self.assertIn("event_type", ast.dump(calls[0].args[-1]))

    def test_10_whitelist_order_after_hardcap_before_priority(self):
        """白名单必须在硬顶之后、降级放行之前（硬顶连白名单也拦）。"""
        src = _read_src(IF_SRC)
        p_life = src.find("if self._m483_is_lifeline_event(layer, event_type):")
        # 用「调用点」而不是函数定义行（定义 :186 会让 find 拿到旧位置，判据失效）
        p_hard = src.find("_hard_mult = _m474_hard_cap_multiplier()")
        p_prio = src.find("if priority <= self._overflow_priority_allow:")
        self.assertGreater(p_life, 0)
        self.assertGreater(p_hard, 0)
        self.assertGreater(p_prio, 0)
        self.assertLess(p_hard, p_life)
        self.assertLess(p_life, p_prio)

    def test_11_config_value_and_whitelist(self):
        self.assertEqual(config.PULSE_LAYER["l0_queue_max"], 5000)
        self.assertIn("system.alarm", config.M483_L0_LIFELINE_EVENTS)
        self.assertIn("system.error", config.M483_L0_LIFELINE_EVENTS)
        self.assertIn("system.stop", config.M483_L0_LIFELINE_EVENTS)
        # ★484-2 已处理 483 遗留裁决 D12：死的裸串换成真实事件常量。
        #   断言仍是「白名单必须含该事件」，期望值由虚构名改为 const.py 真值，
        #   并补一条「虚构名不得残留」，强度只增不减。
        self.assertIn(VascularEvent.SILENT_ORGAN, config.M483_L0_LIFELINE_EVENTS)
        self.assertNotIn("vascular.silent_organ", config.M483_L0_LIFELINE_EVENTS)
        self.assertIn("heart.alive", config.M483_L0_LIFELINE_EVENTS)
        # 明确不收的两类：L3 心跳 / 请求类风暴源
        self.assertNotIn("heart.beat", config.M483_L0_LIFELINE_EVENTS)
        self.assertNotIn("spinal.inspect", config.M483_L0_LIFELINE_EVENTS)
        self.assertNotIn("proprioception.request", config.M483_L0_LIFELINE_EVENTS)

    def test_12_startup_log_prints_real_number(self):
        """§2.4e：不再用 `or '无上限(生命线)'` 兜底，启动日志打印实际 L0=5000。"""
        src = _read_src(IF_SRC)
        self.assertNotIn("L0={self._layer_queue_limits[_PULSE_LAYER_L0] or '无上限(生命线)'}", src)
        self.assertIn("分层队列上限已生效", src)
        self.assertIn("L0={self._layer_queue_limits[_PULSE_LAYER_L0]}", src)

    def test_13_stale_comments_corrected(self):
        """§2.4e 六处陈旧注释：至少三组与 300/300、5000 一致，且不再写 0 无上限。"""
        src = _read_src(IF_SRC)
        self.assertIn("L2 = 300", src)
        self.assertIn("L3 = 300", src)
        self.assertNotIn("L0 上限为 0（无上限），生命线脉冲永不被拒绝", src)
        self.assertNotIn("priority，**不参与准入判定**", src)
        self.assertNotIn("L0（limit=0）在本方法开头已 return True", src)

    def test_14_rollback_switch_literal_present(self):
        src = _read_src(os.path.join(ROOT, "config.py"))
        self.assertIn("ENABLE_M483_L0_QUEUE_CAP = True", src)
        self.assertIn("l0_queue_max\": 5000", src)

    def test_15_judgement_self_check_old_code_is_red(self):
        """★判据有效性自测：旧行为（上限 0）下，用例 02 会返回 True。
        若判据在旧代码上也成立就是自欺 —— 这条把它钉死。
        """
        f_old = _mk_field(0)
        self.assertTrue(_admit(f_old, 5000, 7))
        self.assertEqual(f_old._layer_rejected_count["L0"], 0)
        # 新代码同条件必须 False
        self.assertFalse(_admit(_mk_field(), 5000, 7))


if __name__ == "__main__":
    unittest.main()
