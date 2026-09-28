# -*- coding: utf-8 -*-
"""主线第79批 T2（P0 肝异步任务崩溃修复）门控测试。

覆盖：
1. _get_background_tempo 对非数值返回值(dict/None/str/bool/异常)的防御性回退；
2. _do_optimize 在 _tempo 为 dict 时不崩溃（调用点兜底 + 75批防御双重保障）；
3. 同根因错误聚合上报：相同签名只首报 ERROR，之后按阈值聚合 WARNING，不同签名分别计数。

★mock 隔离全部外部依赖，**不写生产数据**。
"""
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from organs.body.PulseLiver import PulseLiver  # noqa: E402


class _FakeTempo:
    """模拟 runtime_tempo 单例：get_background_tempo 返回可控值。"""

    def __init__(self, val):
        self._val = val

    def get_background_tempo(self):
        return self._val


class _Base(unittest.TestCase):
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


class TestGetBackgroundTempoDefense(_Base):
    """_get_background_tempo 必须对非数值返回值回退 1.0（修复 float<dict 崩溃源头）。"""

    def _patch_tempo(self, val):
        return mock.patch("nucleus.runtime_tempo.get_runtime_tempo",
                          return_value=_FakeTempo(val))

    def test_01_dict_returns_1dot0(self):
        with self._patch_tempo({"oops": "dict"}):
            self.assertEqual(self.o._get_background_tempo(), 1.0)

    def test_02_none_returns_1dot0(self):
        with self._patch_tempo(None):
            self.assertEqual(self.o._get_background_tempo(), 1.0)

    def test_03_str_returns_1dot0(self):
        with self._patch_tempo("3.5"):
            self.assertEqual(self.o._get_background_tempo(), 1.0)

    def test_04_bool_returns_1dot0(self):
        with self._patch_tempo(True):
            self.assertEqual(self.o._get_background_tempo(), 1.0)

    def test_05_valid_float_passthrough(self):
        with self._patch_tempo(2.5):
            self.assertEqual(self.o._get_background_tempo(), 2.5)

    def test_06_valid_int_passthrough(self):
        with self._patch_tempo(3):
            self.assertEqual(self.o._get_background_tempo(), 3.0)

    def test_07_import_raises_returns_1dot0(self):
        with mock.patch("nucleus.runtime_tempo.get_runtime_tempo",
                        side_effect=RuntimeError("boom")):
            self.assertEqual(self.o._get_background_tempo(), 1.0)


class TestDoOptimizeDictTempo(_Base):
    """_do_optimize 在 _tempo 为 dict 时必须不崩溃（调用点兜底）。"""

    def test_20_dict_tempo_no_crash(self):
        # 模拟旧进程行为：_get_background_tempo 返回整字典
        self.o._get_background_tempo = lambda: {"state": "idle", "tempo": "x"}
        # 让 node_pool 返回足够多的 L2 节点以触达 L477 关联扫描分支
        _nodes = [mock.MagicMock() for _ in range(3)]
        self.o.node_pool = mock.MagicMock()
        self.o.node_pool.query = mock.MagicMock(return_value=_nodes)
        self.o.node_pool.get_stats = mock.MagicMock(
            return_value={"evol_distribution": {"L1": 0, "L2": 3, "L3": 0}})
        self.o.node_pool.get_instincts = mock.MagicMock(return_value=[])
        # 重型子方法全部 no-op，避免真实执行
        for _m in ("_compress_l1_to_l2", "_fuse_l2_to_l3", "_detect_contradictions",
                  "_check_instinct_deep_learn", "_check_instinct_upgrade",
                  "_consolidate_knowledge", "_periodic_purity_check",
                  "_semantic_association_scan"):
            setattr(self.o, _m, mock.MagicMock())
        try:
            self.o._do_optimize()
        except TypeError as _e:
            self.fail(f"_do_optimize 仍在 dict tempo 下崩溃(float<dict): {_e}")
        except Exception as _e:
            # 其它与本次修复无关的异常不应发生；若发生则说明测试桩需调整
            self.fail(f"_do_optimize 出现非预期异常: {type(_e).__name__}: {_e}")


class TestAggregatedErrorReporting(_Base):
    """同根因错误聚合上报：相同签名首报 ERROR，之后按阈值聚合 WARNING。"""

    def setUp(self):
        super().setUp()
        self._logs = []
        self.o._log = lambda level, msg: self._logs.append((level, msg))

    def _msgs_with(self, keyword):
        return [m for _lvl, m in self._logs if keyword in m]

    def test_30_same_signature_aggregated(self):
        _exc = ValueError("'<' not supported between instances of 'float' and 'dict'")
        for _ in range(25):
            self.o._report_optimize_error(_exc)
        _sig = "ValueError:'<' not supported between instances of 'float' and 'dict'"
        self.assertIn(_sig, self.o._optimize_error_counts)
        self.assertEqual(self.o._optimize_error_counts[_sig]["count"], 25)
        # 首报仅一次 ERROR，后续在阈值(20)处聚合一次 WARNING；总计数累计到 25
        self.assertEqual(len(self._msgs_with("首报")), 1)
        self.assertGreaterEqual(len(self._msgs_with("聚合")), 1)
        self.assertIn("累计", self._msgs_with("聚合")[-1])

    def test_31_distinct_signatures_separate(self):
        self.o._report_optimize_error(ValueError("A"))
        self.o._report_optimize_error(KeyError("B"))
        self.assertEqual(len(self.o._optimize_error_counts), 2)

    def test_32_first_occurrence_logs_traceback(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError as _exc:
            self.o._report_optimize_error(_exc)
        _first = self._msgs_with("首报")
        self.assertEqual(len(_first), 1)
        self.assertIn("Traceback", _first[0])


if __name__ == "__main__":
    unittest.main()
