# -*- coding: utf-8 -*-
"""T153-6（批次1）：器官生命周期契约参数化测试 · core 4 件。

通用契约断言 5 条（每器官）：
  A1 on_pulse 未知事件不抛且返回 None/可空；已登记 SystemEvent.STATUS_REQUEST 返回非 None
  A2 get_stats 返回 dict 且两次调用键集稳定
  A3 get_resonance_conditions 返回 list；每项含 event_types/event_type
  A4 refresh_runtime_params 幂等（器官未实现时跳过的契约可选方法）
  A5 on_pulse 异常安全（正常/未知事件不向外抛未捕获异常）

批次口径：本批仅 core 4 件（PulseInferenceEngine / PulseEnergyMetabolism /
PulseHealthMonitor / PulseDeviceManager）；其余 P0/P1 器官于后续子批次补齐
（纪律：每批 <=4 件、每器官 <=1 文件）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from nucleus.const import SystemEvent


ORGAN_MODULES = {
    "PulseInferenceEngine": "organs.core.PulseInferenceEngine",
    "PulseEnergyMetabolism": "organs.core.PulseEnergyMetabolism",
    "PulseHealthMonitor": "organs.core.PulseHealthMonitor",
    "PulseDeviceManager": "organs.core.PulseDeviceManager",
}


def _load(cls_name):
    mod = __import__(ORGAN_MODULES[cls_name], fromlist=[cls_name])
    return getattr(mod, cls_name)


@pytest.fixture(params=list(ORGAN_MODULES.keys()))
def organ(request):
    cls = _load(request.param)
    yield cls()


def test_a1_on_pulse_registered_and_unknown(organ):
    # 未知事件：不抛，返回 None 或 dict
    r = organ.on_pulse({"event_type": "__t153_unknown__", "payload": {}})
    assert r is None or isinstance(r, dict), "u672a'u77e5'u4e8b'u4ef6'u5e94'u8fd4'u56de None'u6216 dict"
    # 已登记 STATUS_REQUEST：返回非 None（get_stats 契约）
    r2 = organ.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}})
    assert r2 is not None, "STATUS_REQUEST'u5e94'u8fd4'u56de'u975e None'uuff08get_stats'u5408'u7ea6'uuff09"
    assert isinstance(r2, dict)


def test_a2_get_stats_keys_stable(organ):
    s1 = organ.get_stats()
    assert isinstance(s1, dict), "get_stats'u987b'u8fd4'u56de dict"
    s2 = organ.get_stats()
    assert set(s1.keys()) == set(s2.keys()), "get_stats'u952e'u96c6'u987b'u7a33'u5b9a"


def test_a3_resonance_contains_event_type(organ):
    rc = organ.get_resonance_conditions()
    assert isinstance(rc, list), "get_resonance_conditions'u987b'u8fd4'u56de list"
    for item in rc:
        assert isinstance(item, dict), "u5171'u632f'u6761'u4ef6'u9879'u987b'u4e3a dict"
        assert ("event_types" in item) or ("event_type" in item), "u5171'u632f'u6761'u4ef6'u9879'u987b'u542b event_types/event_type"


def test_a4_refresh_runtime_params_idempotent(organ):
    if not hasattr(organ, "refresh_runtime_params"):
        pytest.skip("organ'u672a'u5b9e'u73b0 refresh_runtime_params'uuff08'u5408'u7ea6'u53ef'u9009'uuff09")
    before = set(organ.get_stats().keys())
    organ.refresh_runtime_params()
    organ.refresh_runtime_params()
    after = set(organ.get_stats().keys())
    assert before == after, "refresh_runtime_params'u987b'u5e42'u7b49'uuff08'u952e'u96c6'u4e0d'u53d8'uuff09"


def test_a5_on_pulse_exception_safe(organ):
    # 异常安全：正常事件与未知事件均不应向外抛未捕获异常
    for ev in (SystemEvent.STATUS_REQUEST, "__t153_unknown__"):
        try:
            organ.on_pulse({"event_type": ev, "payload": {}})
        except Exception as e:  # noqa: BLE001
            pytest.fail("on_pulse(%r)'u4e0d'u5e94'u629b'u672a'u6355'u83b7'u5f02'u5e38: %r" % (ev, e))
