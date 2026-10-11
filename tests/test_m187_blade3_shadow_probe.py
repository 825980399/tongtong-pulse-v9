# -*- coding: utf-8 -*-
"""187批刀3 隔离单测：影子写入意图探针（PK._record_conflict + SC 两审查拒绝点）。

验收点：
  - 信号点触发 INFO + 「入板」探针日志（纯观测/不落状态）
  - 600s 节流：同一器官 600s 内仅记一次
"""
import sys

sys.path.insert(0, ".")

from organs.identity.PulsePersonalityKernel import PulsePersonalityKernel
from organs.identity.PulseSpiritConstitution import PulseSpiritConstitution


class _MockKernel:
    def check_modification_baseline(self, proposed_content="", proposed_file=""):
        return {"safe": False, "reason": "基线校验不通过", "violations": [{"type": "test"}]}


def test_pk_record_conflict_emits_probe_and_throttles():
    k = PulsePersonalityKernel()
    captured = []
    k._log = lambda lvl, msg: captured.append((lvl, msg))
    if hasattr(k, "_shadow_intent_probe_ts"):
        delattr(k, "_shadow_intent_probe_ts")
    # 首次调用：应记探针
    k._record_conflict("baseline_violation", "test desc", "personality_baseline_check")
    probe = [m for _lvl, m in captured if "影子写入意图探针入板" in m]
    assert probe, "PK 影子写入意图探针未记录"
    assert "baseline_violation" in probe[0]
    # 600s 内二次调用：应被节流（不再记）
    captured.clear()
    k._record_conflict("another", "d2", "s2")
    probe2 = [m for _lvl, m in captured if "影子写入意图探针入板" in m]
    assert not probe2, "PK 600s 节流失效：短时间内重复记录探针"


def test_sc_on_verify_request_emits_probe():
    c = PulseSpiritConstitution("宪法守护")
    c.personality_kernel = _MockKernel()
    captured = []
    c._log = lambda lvl, msg: captured.append((lvl, msg))
    if hasattr(c, "_shadow_intent_probe_ts"):
        delattr(c, "_shadow_intent_probe_ts")
    c._on_verify_request({"proposed_content": "x", "proposed_file": "PulsePersonalityKernel.py"})
    probe = [m for _lvl, m in captured if "影子写入意图探针入板" in m]
    assert probe, "SC _on_verify_request 影子写入意图探针未记录"
    assert "宪法审查拒绝" in probe[0]


def test_sc_execute_constitution_check_emits_probe():
    c = PulseSpiritConstitution("宪法守护")
    c.personality_kernel = _MockKernel()
    captured = []
    c._log = lambda lvl, msg: captured.append((lvl, msg))
    if hasattr(c, "_shadow_intent_probe_ts"):
        delattr(c, "_shadow_intent_probe_ts")
    try:
        c._execute_constitution_check()
    except Exception as _e:
        raise AssertionError("SC._execute_constitution_check 在抵达探针点前异常: %r" % (_e,)) from _e
    probe = [m for _lvl, m in captured if "影子写入意图探针入板" in m]
    assert probe, "SC _execute_constitution_check 影子写入意图探针未记录"
    assert "宪法周期校验拒绝违规" in probe[0]


if __name__ == "__main__":
    import traceback
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _fail = 0
    for t in _tests:
        try:
            t()
            print("PASS", t.__name__)
        except Exception:
            _fail += 1
            print("FAIL", t.__name__)
            traceback.print_exc()
    print(f"\n{len(_tests) - _fail}/{len(_tests)} passed")
    sys.exit(1 if _fail else 0)
