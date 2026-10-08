# -*- coding: utf-8 -*-
"""169批 C8（T-内存告警静默失效修复-1 + T-内存告警percent口径不可达-1）门控测试。

锁定：
  ★① psutil 抛异常 → **不产生伪 heavy/critical**（采集失败不得被当成高压）；
  ★② 采集失败也**不得**被当成真实低负载而降级为 light（否则告警静默失效）；
  ③ 内存压力双口径（系统占比 + 进程 RSS），阈值可配置；
  ④ runtime_metrics 分级纳入内存维度：**高内存低 CPU → 正常升档**，
     且默认阈值下既有 CPU/队列口径**零回归**。
"""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

from nucleus.field.InfoField import InfoField
from nucleus.runtime_metrics import RuntimeMetrics


def _mk_field(level="heavy"):
    """轻量构造 InfoField（__new__ + 只挂被测所需属性）。"""
    _f = InfoField.__new__(InfoField)
    _f._hardware_snapshot = {
        "cpu_usage": 0, "mem_usage": 0,
        "gpu_usage": 0.0, "gpu_memory_usage": 0.0,
    }
    _f._hardware_lock = threading.Lock()
    _f._external_level_expiry = 0.0
    _f._last_load_check = 0.0
    _f._load_check_interval = 0
    _f._load_level = level
    _f._logs = []
    _f._log = lambda _lv, _m: _f._logs.append(str(_m))
    return _f


class TestProbeFailureNotSilent(unittest.TestCase):
    """★①② 采集失败：既不伪升档、也不伪降级。"""

    def test_10_pressure_ok_false_on_failure(self):
        """psutil 抛异常 -> 读数 ok=False、breached=False（不产生伪高压）。"""
        _f = _mk_field()
        with mock.patch.dict(sys.modules, {"psutil": None}):   # import 抛 ImportError
            _r = _f._m169_memory_pressure()
        self.assertFalse(_r["ok"], "采集失败必须标记 ok=False")
        self.assertFalse(_r["breached"], "★不得产生伪 heavy/critical")
        self.assertIn("采集失败", _r["reason"])

    def test_20_probe_failure_leaves_trace(self):
        """采集失败留痕（★不得静默）。

        注： 端到端依赖较多运行期属性，轻量夹具无法稳定
        走到回退分支；此处验证留痕方法本身，端到端缺口已在交付报告单列。
        """
        _f = _mk_field(level="heavy")
        with self.assertLogs("pulse.module.InfoField", level="WARNING") as _cm:
            _f._m169_log_probe_failure()
        self.assertTrue(any("硬件采样失败" in x for x in _cm.output),
                        "采集失败必须留痕: %s" % _cm.output)
        self.assertTrue(any("heavy" in x for x in _cm.output),
                        "留痕须带当前等级: %s" % _cm.output)

    def test_21_guard_precedes_light_downgrade(self):
        """★结构守卫：采集失败时**先**返回，不得落到「降级为 light」分支。"""
        import inspect
        _src = inspect.getsource(InfoField._check_high_load)
        _i_fail = _src.find("_m169_hw_probe_ok", _src.find("cpu_percent == 0 and mem_percent == 0", 300))
        _i_light = _src.find("视为轻负载")
        self.assertGreater(_i_fail, 0, "未找到采集失败守卫")
        self.assertGreater(_i_light, 0, "未找到轻负载降级分支")
        self.assertLess(_i_fail, _i_light,
                        "采集失败守卫必须位于降级为 light 之前")

class TestMemoryPressure(unittest.TestCase):
    """③ 双口径 + 可配置阈值。"""

    def test_30_thresholds_configurable(self):
        _f = _mk_field()
        _t = _f._m169_mem_thresholds()
        self.assertIn("system_percent", _t)
        self.assertIn("process_rss_mb", _t)
        with mock.patch.object(config, "HIGH_LOAD_PROCESS_RSS_MB", 1024.0,
                               create=True):
            self.assertEqual(_f._m169_mem_thresholds()["process_rss_mb"], 1024.0)

    def test_31_system_percent_breach(self):
        _f = _mk_field()
        _fake = mock.MagicMock()
        _fake.virtual_memory.return_value.percent = 95.0
        _fake.Process.return_value.memory_info.return_value.rss = 100 * 1048576
        with mock.patch.dict(sys.modules, {"psutil": _fake}), \
                mock.patch.object(config, "HIGH_LOAD_PROCESS_RSS_MB", 6144.0,
                                  create=True):
            _r = _f._m169_memory_pressure()
        self.assertTrue(_r["ok"])
        self.assertTrue(_r["breached"], "系统内存 95% > 85% 应判压力成立")

    def test_32_process_rss_breach(self):
        """★进程 RSS 口径：系统内存不高但进程 RSS 超阈 -> 仍判压力成立。"""
        _f = _mk_field()
        _fake = mock.MagicMock()
        _fake.virtual_memory.return_value.percent = 40.0
        _fake.Process.return_value.memory_info.return_value.rss = 7000 * 1048576
        with mock.patch.dict(sys.modules, {"psutil": _fake}), \
                mock.patch.object(config, "HIGH_LOAD_PROCESS_RSS_MB", 6144.0,
                                  create=True):
            _r = _f._m169_memory_pressure()
        self.assertTrue(_r["breached"], "进程 RSS 7000MB > 6144MB 应判压力成立")
        self.assertIn("RSS", _r["reason"])


class TestRuntimeMetricsGrading(unittest.TestCase):
    """④ runtime_metrics 内存维度参与分级。"""

    def test_40_high_memory_low_cpu_upgrades(self):
        """高内存 + 低 CPU -> 正常升档（任务书验收项）。"""
        _r = RuntimeMetrics.__new__(RuntimeMetrics)
        _r._lock = threading.RLock()
        _r._last_queue_depth = 0
        _fake = mock.MagicMock()
        _fake.cpu_percent.return_value = 5.0
        _fake.virtual_memory.return_value.percent = 92.0
        with mock.patch.dict(sys.modules, {"psutil": _fake}):
            _out = _r.get_system_load()
        self.assertGreater(_out["memory_percent"], 90.0)
        self.assertEqual(_out["load_level"], "high",
                         "高内存低 CPU 应升档到 high: %s" % _out)

    def test_41_low_memory_no_regression(self):
        """内存正常 -> 分级仍由 CPU/队列决定（零回归）。"""
        _r = RuntimeMetrics.__new__(RuntimeMetrics)
        _r._lock = threading.RLock()
        _r._last_queue_depth = 0
        _fake = mock.MagicMock()
        _fake.cpu_percent.return_value = 5.0
        _fake.virtual_memory.return_value.percent = 30.0
        with mock.patch.dict(sys.modules, {"psutil": _fake}):
            _out = _r.get_system_load()
        self.assertEqual(_out["load_level"], "low")

    def test_42_thresholds_default(self):
        _r = RuntimeMetrics.__new__(RuntimeMetrics)
        _r._lock = threading.RLock()
        _r._last_queue_depth = 0
        self.assertEqual(_r._m169_mem_level_thresholds(), (95.0, 90.0, 85.0))


if __name__ == "__main__":
    unittest.main()
