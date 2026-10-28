# -*- coding: utf-8 -*-
"""★378 批盲测件 · EventBus 遥测扩面（P2）

任务书 §九：EventBus 流量只有低频 patch_handoff，EventTap 喂不饱，
subscriptions/published/delivered 空转。要求扩面到至少 3 个真实发布点。

T0 实测（写进报告）：流量并不只有 patch_handoff ——
`profile_20261028_030002.json` 的 `runtime_events.by_source` = `PulseLiver: 21` /
`PulseStomach: 452`，total 473。所以真缺口不是"没接点"（全库已接 9 处），
而是**有流量的来源只有 2 个**，且 PulseCortex 那 3 处落在低频分支。
本刀加的是第 3 个**高频**来源：PulseLung 的 LLM 调用收口。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class _Stub:
    """`_m40_trace_call` 的最小宿主：只需 `_log` 与 usage 字段。"""

    _last_llm_usage = None

    def _log(self, *_a, **_kw):
        pass


def _call(stub=None):
    from organs.body.PulseLung import PulseLung
    PulseLung._m40_trace_call(
        stub or _Stub(), prompt="p", response="r", channel="ch", model="md",
        duration=0.25, status="success", origin="user_query",
        prompt_version="lung.chat.v1")


def test_m378_d8_trace_call_publishes_lung_event(monkeypatch):
    """★核心：LLM 调用收口处发布 `lung.llm_call`，来源 PulseLung、带独立开关。"""
    import nucleus.events.EventTap as et

    _seen = []

    def _fake(name, payload=None, **kw):
        _seen.append((name, payload, kw))
        return True

    monkeypatch.setattr(et, "tap_publish", _fake)
    _call()
    assert len(_seen) == 1, "一次调用应发布一条旁路事件，实际 %d" % len(_seen)
    _name, _payload, _kw = _seen[0]
    assert _name == "lung.llm_call"
    assert _kw.get("source") == "PulseLung"
    assert _kw.get("switch_attr") == "ENABLE_LUNG_EVENT_TAP"
    assert _payload["status"] == "success"
    assert _payload["duration_ms"] == 250
    assert _payload["origin"] == "user_query"


def test_m378_d8_tap_failure_never_breaks_call(monkeypatch):
    """负控：旁路发布炸了，主流程必须照跑（`_m40_trace_call` 不抛）。"""
    import nucleus.events.EventTap as et

    def _boom(*_a, **_kw):
        raise RuntimeError("tap boom")

    monkeypatch.setattr(et, "tap_publish", _boom)
    _call()          # 不抛即通过


def test_m378_d8_pulselung_was_the_missing_source():
    """反向先红：改前 PulseLung **一处 tap_publish 都没有**，本刀补的正是第 3 个来源。

    同时锁住"只加一处"（防止后续有人到处撒 tap_publish 把热路径拖慢）。
    """
    with io.open(os.path.join(ROOT, "organs", "body", "PulseLung.py"),
                 encoding="utf-8") as _f:
        _s = _f.read()
    assert _s.count('"lung.llm_call"') == 1
    assert "★378刀8" in _s
    # 已接点但零流量的器官仍保持原样（没被本刀碰）
    for _rel in ("organs/brain/PulseCortex.py", "organs/body/PulseStomach.py"):
        with io.open(os.path.join(ROOT, _rel), encoding="utf-8") as _f:
            assert "tap_publish(" in _f.read(), _rel
