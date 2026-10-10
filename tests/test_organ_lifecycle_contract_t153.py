# -*- coding: utf-8 -*-
"""T153-6（批次1+2+3）：器官生命周期契约参数化测试。

通用契约断言 5 条（每器官）：
  A1 on_pulse 契约：未知事件不抛且返回 None/可空 dict；若器官在 get_resonance_conditions
     中显式声明订阅 SystemEvent.STATUS_REQUEST，则 on_pulse(STATUS_REQUEST) 必返回非 None dict
     （项目约定：声明即服务；未声明的器官不强制，避免把"未订阅"误判为违约）
  A2 get_stats 返回 dict 且两次调用键集稳定
  A3 get_resonance_conditions 返回 list；每项含 event_types/event_type
  A4 refresh_runtime_params 幂等（器官未实现时跳过的契约可选方法）
  A5 on_pulse 异常安全（正常/未知事件不向外抛未捕获异常）

批次口径：
  批次1（6070378）：core 4 件（PulseInferenceEngine / PulseEnergyMetabolism /
      PulseHealthMonitor / PulseDeviceManager），16 passed / 4 skipped
  批次2（e5e5b36）：core/genetic 4 件（PulseEmergencyHandler / PulseSpinalCord /
      PulseDNARepair / PulseReproductionEthics）
  批次3（本提交）：immune/genetic/motor/body 4 件（PulseBoneMarrow / PulseEvolution /
      PulseFileDigester / PulseBloodVessel）
  其余 P0/P1 器官于后续子批次补齐（纪律：每批 <=4 件、每器官 <=1 文件）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from nucleus.const import SystemEvent

# ── STATUS_REQUEST 订阅表（实测，决定 A1 是否施加正向断言）────────────────────
# 判定标准：器官在 get_resonance_conditions() 的 event_types/event_type 中显式列出
#   SystemEvent.STATUS_REQUEST 即视为"声明订阅"，A1 要求 on_pulse(STATUS_REQUEST) 返回非 None dict；
#   未列出者按"声明即服务"约定不强制（返回 None 合法）。
# 截至批次9（覆盖 17 件，T153-6 全部器官/引擎契约完结）：
#   声明订阅(YES, A1 施回非None, 14 件)：PulseInferenceEngine / PulseEnergyMetabolism /
#     PulseHealthMonitor / PulseDeviceManager / PulseEmergencyHandler / PulseSpinalCord /
#     PulseDNARepair / PulseReproductionEthics / PulseEvolution / PulseBoneMarrow /
#     PulseSelfAwareness / PulseHormones / PulseMotivationCycle / PulseGrowth
#   未声明(NO, A1 不强制, 3 件)：PulseFileDigester / PulseBloodVessel / PulseNeurotransmitters
#   （PulseNeurotransmitters 实测仅声明 chat.message/hormones.emotion_detected/heart.beat，未订阅 STATUS_REQUEST）
ORGAN_MODULES = {
    "PulseInferenceEngine": "organs.core.PulseInferenceEngine",
    "PulseEnergyMetabolism": "organs.core.PulseEnergyMetabolism",
    "PulseHealthMonitor": "organs.core.PulseHealthMonitor",
    "PulseDeviceManager": "organs.core.PulseDeviceManager",
    "PulseEmergencyHandler": "organs.core.PulseEmergencyHandler",
    "PulseSpinalCord": "organs.core.PulseSpinalCord",
    "PulseDNARepair": "organs.genetic.PulseDNARepair",
    "PulseReproductionEthics": "organs.genetic.PulseReproductionEthics",
    "PulseBoneMarrow": "organs.immune.PulseBoneMarrow",
    "PulseEvolution": "organs.genetic.PulseEvolution",
    "PulseFileDigester": "organs.motor.PulseFileDigester",
    "PulseBloodVessel": "organs.body.PulseBloodVessel",
    "PulseSelfAwareness": "organs.identity.PulseSelfAwareness",
    "PulseHormones": "organs.endocrine.PulseHormones",
    "PulseNeurotransmitters": "organs.endocrine.PulseNeurotransmitters",
    "PulseMotivationCycle": "organs.core.PulseMotivationCycle",
    "PulseGrowth": "organs.identity.PulseGrowth",
}


def _load(cls_name):
    mod = __import__(ORGAN_MODULES[cls_name], fromlist=[cls_name])
    return getattr(mod, cls_name)


def _declared_event_types(organ):
    declared = set()
    for item in organ.get_resonance_conditions():
        if not isinstance(item, dict):
            continue
        for key in ("event_types", "event_type"):
            val = item.get(key)
            if isinstance(val, list):
                declared.update(val)
            elif isinstance(val, str):
                declared.add(val)
    return declared


@pytest.fixture(params=list(ORGAN_MODULES.keys()))
def organ(request):
    cls = _load(request.param)
    yield cls()


def test_a1_on_pulse_contract(organ):
    declared = _declared_event_types(organ)
    # 未知事件：不抛，返回 None 或 dict（基类契约：dict | None）
    r = organ.on_pulse({"event_type": "__t153_unknown__", "payload": {}})
    assert r is None or isinstance(r, dict)
    # 声明订阅 STATUS_REQUEST 的器官必须服务它（返回非 None dict）
    if SystemEvent.STATUS_REQUEST in declared:
        r2 = organ.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}})
        assert r2 is not None and isinstance(r2, dict)


def test_a2_get_stats_keys_stable(organ):
    s1 = organ.get_stats()
    assert isinstance(s1, dict)
    s2 = organ.get_stats()
    assert set(s1.keys()) == set(s2.keys())


def test_a3_resonance_contains_event_type(organ):
    rc = organ.get_resonance_conditions()
    assert isinstance(rc, list)
    for item in rc:
        assert isinstance(item, dict)
        assert ("event_types" in item) or ("event_type" in item)


def test_a4_refresh_runtime_params_idempotent(organ):
    if not hasattr(organ, "refresh_runtime_params"):
        pytest.skip("organ 未实现 refresh_runtime_params（契约可选方法）")
    before = set(organ.get_stats().keys())
    organ.refresh_runtime_params()
    organ.refresh_runtime_params()
    after = set(organ.get_stats().keys())
    assert before == after


def test_a5_on_pulse_exception_safe(organ):
    for ev in (SystemEvent.STATUS_REQUEST, "__t153_unknown__"):
        try:
            organ.on_pulse({"event_type": ev, "payload": {}})
        except Exception as e:  # noqa: BLE001
            pytest.fail("on_pulse({!r}) 不应抛出未捕获异常: {!r}".format(ev, e))
