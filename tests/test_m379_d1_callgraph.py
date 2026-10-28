# -*- coding: utf-8 -*-
"""★379 刀1 · CallGraph 增脉冲语义通道（DJ4-05，P1）

票面：`_build_call_chain` 纯 AST 同文件递归，跨文件/跨器官脉冲双盲，
226 处在役发布链不可见，适配度 30/100。要求新增独立函数 `_build_pulse_call_chain`
（不改 AST 主路径），输出 pulse_call_chain.json，与 AST 链合并去重。

本件造**两条跨文件发布链**，断言新通道能抓到：
  链1 `nucleus/m379probe/pub_b.py::publish_from_b` → tap_publish("demo.b.event")
      ← 被 `nucleus/m379probe/call_a.py::call_b` 调用（跨文件）
  链2 `nucleus/m379probe/pub_c.py::publish_c` → get_event_bus().publish("demo.c.event")
      ← 被 `nucleus/m379probe/sched_c.py::sched_c` 调用（跨文件）
"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.self_inspector import SelfInspector  # noqa: E402

_PKG = "nucleus/m379probe"

_FILES = {
    "pub_b.py": (
        'def publish_from_b():\n'
        '    from nucleus.events.EventTap import tap_publish\n'
        '    tap_publish("demo.b.event", {"v": 1}, source="probe")\n'
    ),
    "call_a.py": (
        'from nucleus.m379probe.pub_b import publish_from_b\n'
        '\n'
        '\n'
        'def call_b():\n'
        '    publish_from_b()\n'
    ),
    "pub_c.py": (
        'def publish_c():\n'
        '    from nucleus.events.EventBus import get_event_bus\n'
        '    get_event_bus().publish("demo.c.event", payload={})\n'
    ),
    "sched_c.py": (
        'from nucleus.m379probe.pub_c import publish_c\n'
        '\n'
        '\n'
        'def sched_c():\n'
        '    publish_c()\n'
    ),
}


def _mkroot(tmp_path):
    _d = tmp_path / _PKG
    _d.mkdir(parents=True)
    for _n, _body in _FILES.items():
        (_d / _n).write_text(_body, encoding="utf-8")
    return str(tmp_path)


def _insp():
    return SelfInspector.__new__(SelfInspector)


def test_m379_d1_captures_two_cross_file_chains(tmp_path):
    """★核心：两条跨文件发布链都要抓到（票面要求的最小可验场景）。"""
    _root = _mkroot(tmp_path)
    _rep = _insp()._build_pulse_call_chain(root=_root)
    _by_event = {c["event"]: c for c in _rep["chains"]}
    assert "demo.b.event" in _by_event, sorted(_by_event)
    assert "demo.c.event" in _by_event, sorted(_by_event)

    _b = _by_event["demo.b.event"]
    assert _b["publisher"]["method"] == "publish_from_b"
    assert _b["publisher"]["file"].endswith("pub_b.py")
    assert _b["publisher"]["callee"] == "tap_publish"
    assert "call_b" in _b["upstream"], _b

    _c = _by_event["demo.c.event"]
    assert _c["publisher"]["method"] == "publish_c"
    assert _c["publisher"]["callee"] == "publish"
    assert "sched_c" in _c["upstream"], _c


def test_m379_d1_stats_shape(tmp_path):
    _root = _mkroot(tmp_path)
    _s = _insp()._build_pulse_call_chain(root=_root)["stats"]
    assert _s["total_sites"] >= 2
    assert _s["with_upstream"] >= 2
    assert _s["coverage_pct"] > 0
    assert _s["distinct_events"] >= 2


def test_m379_d1_writes_json_only_when_asked(tmp_path):
    """默认**不落盘**（便于隔离验证）；write=True 才写，且 schema 可核。"""
    _root = _mkroot(tmp_path)
    _i = _insp()
    _r1 = _i._build_pulse_call_chain(root=_root)
    assert "written" not in _r1
    _out = os.path.join(_root, "data", "reports", "pulse_call_chain.json")
    assert not os.path.isfile(_out)

    _r2 = _i._build_pulse_call_chain(root=_root, out_path=_out, write=True)
    assert _r2["written"] == _out and os.path.isfile(_out)
    with io.open(_out, encoding="utf-8") as _f:
        _j = json.load(_f)
    assert _j["schema"] == "pulse_call_chain/379.v1"
    assert any(c["event"] == "demo.b.event" for c in _j["chains"])


def test_m379_d1_dynamic_event_is_labelled(tmp_path):
    """事件名不是字面量时要显式标 `<dynamic>`，不许悄悄算成某个事件。"""
    _d = tmp_path / _PKG
    _d.mkdir(parents=True)
    (_d / "dyn.py").write_text(
        'def pub_dyn(name):\n'
        '    from nucleus.events.EventTap import tap_publish\n'
        '    tap_publish(name, {})\n', encoding="utf-8")
    _rep = _insp()._build_pulse_call_chain(root=str(tmp_path))
    _evs = [c["event"] for c in _rep["chains"]]
    assert "<dynamic>" in _evs
    assert _rep["stats"]["dynamic_events"] >= 1


def test_m379_d1_merge_dedups_without_touching_ast():
    """合并不改写：AST 链原样保留，重复项只记在 dup_removed 里。"""
    _ast = {"method": "root", "calls": [{"method": "publish_from_b", "calls": []}]}
    _pulse = {"chains": [{"event": "e", "publisher": {"method": "publish_from_b"}},
                         {"event": "f", "publisher": {"method": "other"}}]}
    _m = SelfInspector._m379_merge_with_ast_chain(_ast, _pulse)
    assert _m["ast"] is _ast and _m["pulse"] is _pulse
    assert _m["dup_removed"] == ["publish_from_b"]
    assert _m["merged_methods"] == ["other", "publish_from_b"]


@pytest.mark.parametrize("callee", ["tap_publish", "publish"])
def test_m379_d1_only_bus_receivers_counted(tmp_path, callee):
    """`publish` 太泛：只收 receiver 明确是 bus 的形态，别把别的对象算进来。"""
    _d = tmp_path / _PKG
    _d.mkdir(parents=True)
    (_d / "x.py").write_text(
        'def f():\n'
        '    registry.%s("not.an.event")\n' % callee, encoding="utf-8")
    _rep = _insp()._build_pulse_call_chain(root=str(tmp_path))
    assert not [c for c in _rep["chains"] if c["event"] == "not.an.event"]
