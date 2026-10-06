# -*- coding: utf-8 -*-
"""刀1·A1/N1 门控单测：InfoField 负载探针可观测化 + 内存阈值配置化。

判据（烛微 162A A1）：
  ① 负载全零时 heavy/critical 场景不吞标记（打桩 CPU=0/mem=0 + 模拟 heavy → 断言告警出现）；
  ② 阈值读 config 生效（改 config 值 → 分级边界变化）；
  ③ 无静默 except（日志可观测）—— Fix2：psutil 运行时异常被兜底捕获，不穿出到 publish 热路径。

测试用 object.__new__ 构造轻量实例，仅置 _check_high_load 所需属性，并把有副作用的
线程池/扩缩容方法替换为 no-op，避免真实后台看门狗线程启动。
"""
import types

import config as _cfg_mod
import nucleus.field.InfoField as _infomod
from nucleus.field.InfoField import InfoField


def _make_minimal_field():
    """轻量实例：仅置 _check_high_load 所需属性，副作用方法 no-op 化。"""
    import threading

    f = object.__new__(InfoField)
    f._last_load_check = 0.0
    f._load_check_interval = 0.0  # 确保 _check_high_load 不提前 return
    f._external_level_expiry = 0.0
    f._hardware_lock = threading.Lock()
    f._hardware_snapshot = {
        "cpu_usage": 0.0, "mem_usage": 0.0,
        "gpu_usage": 0.0, "gpu_memory_usage": 0.0, "updated_at": 0.0,
    }
    f._load_level = "light"
    f._high_load = False
    f._hardware_probe_ok = True
    f._load_probe_failed = False
    f._load_probe_fail_count = 0
    f._adaptive_tuning_enabled = False  # 关闭 tier offset，纯阈值验证
    f._hardware_tier = "high"
    f._load_threshold_hysteresis = 5.0
    f._high_load_since = 0.0
    f._high_load_since_level = None
    f._high_load_since_time = 0.0
    f._last_resized_level = "light"
    # 中性化副作用方法
    f._adjust_adaptive_pool = lambda: None
    f._resize_layer_pools_by_level = lambda: None
    f._auto_scale_l3_by_depth = lambda: None
    f._release_override_if_critical = lambda: None
    return f


def test_memory_threshold_config_driven():
    """判据②：内存阈值由 config 驱动（改 config 值 → 分级边界变化）。"""
    f = _make_minimal_field()
    # 默认 mem=70 > 65 → moderate
    assert f._get_load_level(cpu=0.0, mem=70.0) == "moderate"
    # 改 config.MEMORY_LOAD_MODERATE_PCT=75 → 70 不再越界 → light
    _old = getattr(_cfg_mod, "MEMORY_LOAD_MODERATE_PCT", 65.0)
    _cfg_mod.MEMORY_LOAD_MODERATE_PCT = 75.0
    try:
        assert f._get_load_level(cpu=0.0, mem=70.0) == "light"
        assert f._get_load_level(cpu=0.0, mem=80.0) == "moderate"
        # 74 仍不越界 → light；76 越界 → moderate
        assert f._get_load_level(cpu=0.0, mem=74.0) == "light"
        assert f._get_load_level(cpu=0.0, mem=76.0) == "moderate"
    finally:
        _cfg_mod.MEMORY_LOAD_MODERATE_PCT = _old


def test_load_probe_failed_marker_on_zero(monkeypatch):
    """判据①：打桩 CPU=0/mem=0 + 模拟 heavy → 断言降级标记与告警出现（不吞标记）。"""
    import psutil

    monkeypatch.setattr(psutil, "cpu_percent", lambda *a, **k: 0.0)
    monkeypatch.setattr(
        psutil, "virtual_memory",
        lambda *a, **k: types.SimpleNamespace(percent=0.0),
    )

    _warns = []
    monkeypatch.setattr(
        _infomod._module_logger, "warning",
        lambda msg, *a, **k: _warns.append(msg),
    )

    f = _make_minimal_field()
    f._load_level = "heavy"  # 模拟已处于 heavy
    f._high_load = True

    f._check_high_load()

    assert f._load_probe_failed is True
    assert f._hardware_probe_ok is False
    assert any("硬件探针无数据" in str(w) for w in _warns)


def test_psutil_runtime_error_caught(monkeypatch):
    """判据③/Fix2：psutil 运行时异常（OSError）被兜底捕获，不穿出 _check_high_load。"""
    import psutil

    def _raise(*a, **k):
        raise OSError("simulated psutil runtime failure")

    monkeypatch.setattr(psutil, "cpu_percent", _raise)
    monkeypatch.setattr(
        psutil, "virtual_memory",
        lambda *a, **k: types.SimpleNamespace(percent=0.0),
    )

    f = _make_minimal_field()
    # 不应抛出（Fix2 兜底捕获 + 标记）
    f._check_high_load()
    assert f._hardware_probe_ok is False
    assert f._load_probe_failed is True
