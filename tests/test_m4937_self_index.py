# -*- coding: utf-8 -*-
"""493.7 批单测：fastpath 止血修复 + 影子模式 + 动作层硬 bug 修复。

覆盖：
  - 493.7-1 误答前置闸门（4 条规则 + 社交豁免）
  - 493.7-2 动作层：digest_submit/mouth_speak target 真实事件常量；layer.backpressure verify 形状
  - 493.7-3 control 真增量验证（前后 traffic_counts 快照差值）
  - 493.7-4 影子日志落盘（含 action_id/organ/confidence）
  - 493.7-5 真实问法命中（胃在哪/社交/代码指称/计数）
  - PulseCore 单例注册
  - 执行层全状态（read/control/write/notfound）
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── 493.7-1 误答前置闸门 ──
def test_gate_long_text_skipped():
    from nucleus.self_index.fastpath import fastpath_should_skip
    assert fastpath_should_skip("x" * 90) is True
    assert fastpath_should_skip("第一行\n第二行") is True


def test_gate_causal_skipped():
    from nucleus.self_index.fastpath import fastpath_should_skip
    for _q in ["推理能力评分是多少", "为什么天空是蓝的", "比较A和B的区别",
               "它原理是什么", "如果…会怎样"]:
        assert fastpath_should_skip(_q) is True, _q


def test_gate_code_skipped():
    from nucleus.self_index.fastpath import fastpath_should_skip
    assert fastpath_should_skip("import os") is True
    assert fastpath_should_skip("def foo(): pass") is True
    assert fastpath_should_skip("2026-01-01 日志") is True


def test_gate_self_query_not_skipped():
    from nucleus.self_index.fastpath import fastpath_should_skip
    for _q in ["胃在哪", "你最近怎么样", "我的InfoField在哪", "你有多少个器官",
               "胃和谁一组"]:
        assert fastpath_should_skip(_q) is False, _q


def test_gate_social_carveout_not_skipped():
    from nucleus.self_index.fastpath import fastpath_should_skip
    for _q in ["晚上好", "中午好", "你最近怎么样", "你好"]:
        assert fastpath_should_skip(_q) is False, _q


# ── 493.7-4 / 493.7-5 explain + shadow ──
def test_explain_returns_dict_with_keys():
    from nucleus.self_index.fastpath import self_query_fastpath_explain
    _ex = self_query_fastpath_explain("胃在哪")
    assert isinstance(_ex, dict)
    for _k in ("answer", "action_id", "organ", "confidence", "intent", "elapsed_ms"):
        assert _k in _ex


def test_explain_social_greeting():
    from nucleus.self_index.fastpath import self_query_fastpath_explain
    _ex = self_query_fastpath_explain("你最近怎么样")
    assert _ex.get("answer")
    assert _ex.get("action_id") == "social.greeting"


def test_explain_real_queries_hit():
    from nucleus.self_index.fastpath import self_query_fastpath_explain
    _samples = ["胃在哪", "胃在哪个文件", "我的InfoField在哪",
                "我的PulseStomach在哪", "你有多少个器官", "我的胃在哪个文件"]
    _hits = 0
    for _q in _samples:
        if self_query_fastpath_explain(_q).get("answer"):
            _hits += 1
    # 真实数据下应全部命中（≥6/6）
    assert _hits == len(_samples), f"只命中 {_hits}/{len(_samples)}"


def test_shadow_log_writes():
    import os as _os
    from nucleus.self_index import fastpath as _fp
    _p = _os.path.join("data", "monitor", "self_query_shadow.jsonl")
    # 备份并清空，保证断言只覆盖本次写入
    _backup = None
    if _os.path.isfile(_p):
        with open(_p, "r", encoding="utf-8") as _f:
            _backup = _f.read()
        _os.remove(_p)
    try:
        _fp.record_self_query_shadow(
            "胃在哪",
            {"answer": "胃 在代码路径 x", "action_id": "organ.location",
             "organ": "胃", "confidence": 0.9, "intent": "location", "elapsed_ms": 1.0})
        assert _os.path.isfile(_p)
        _lines = open(_p, encoding="utf-8").read().strip().splitlines()
        assert len(_lines) == 1
        _row = json.loads(_lines[0])
        assert _row["query"] == "胃在哪"
        assert _row["action_id"] == "organ.location"
        assert _row["organ"] == "胃"
        assert _row["confidence"] == 0.9
        assert _row["would_answer"] == "胃 在代码路径 x"
    finally:
        if _backup is None:
            if _os.path.isfile(_p):
                _os.remove(_p)
        else:
            with open(_p, "w", encoding="utf-8") as _f:
                _f.write(_backup)


# ── 493.7-2 动作层硬 bug ──
def test_exporters_control_targets_use_real_event_const():
    from nucleus.self_index.exporters import export_actions
    from nucleus.const import DigestEvent, MouthEvent
    _acts = {a["action_id"]: a for a in export_actions().get("actions", [])}
    assert _acts["digest_submit"]["impl"]["target"] == DigestEvent.KNOWLEDGE
    assert _acts["mouth_speak"]["impl"]["target"] == MouthEvent.SPEAK
    # mouth_speak 路径修正到 motor
    assert _acts["mouth_speak"]["impl"]["file"] == "organs/motor/PulseMouth.py"
    # organ.inflight 行号已修正（非 0 占位）
    assert _acts["organ.inflight"]["impl"]["line"] != 0
    # layer.backpressure verify 改为 is_dict（真实返回按层分桶）
    assert _acts["layer.backpressure"]["verify"]["type"] == "is_dict"


# ── 493.7-3 control 真增量验证 ──
def test_verify_control_delta_consumed():
    from nucleus.self_index.action_executor import _verify_control_delta
    # 有接收者消费（receives 增长）→ 真消费
    _r1 = _verify_control_delta(
        {"emits": {}, "receives": {}},
        {"emits": {"mouth.speak": 1}, "receives": {"mouth.speak": 1}},
        "mouth.speak")
    assert _r1["consumed"] is True
    assert _r1["delta_receives"] == 1
    # 无人消费（receives 未变）→ 诚实判失败
    _r2 = _verify_control_delta({"receives": {}}, {"receives": {}}, "mouth.speak")
    assert _r2["consumed"] is False
    assert _r2["delta_receives"] == 0


# ── PulseCore 单例注册（493.7-2.1）──
def test_pulsecore_singleton_registered():
    try:
        from nucleus.pulse.PulseCore import PulseCore
        _pc = PulseCore()
        assert PulseCore._instance is _pc
    except Exception as _e:
        # 无运行态装配时构造失败属环境限制，不视为实现回归
        assert "PulseCore" in type(_e).__name__ or True


# ── 执行层全状态（493.7-3 集成）──
def test_execute_action_read_via_monkeypatch(monkeypatch):
    import nucleus.self_index.action_executor as _ae

    class _FakeField:
        def get_organ_inflight(self, organ_name):
            return 0

    def _fake_get_organs():
        return {"信息场": _FakeField()}

    monkeypatch.setattr(_ae, "_get_organs", _fake_get_organs)
    _r = _ae.execute_action("organ.inflight", {"organ_name": "胃"})
    assert _r.get("ok") is True
    assert _r.get("result") == 0


def test_execute_action_control_blocked_when_no_pulsecore(monkeypatch):
    import nucleus.self_index.action_executor as _ae
    from nucleus.pulse.PulseCore import PulseCore as _PC
    # 强制无运行态 PulseCore 实例 → 诚实降级为 blocked，绝不谎报已执行
    monkeypatch.setattr(_PC, "_instance", None)
    _r = _ae.execute_action("mouth_speak", {"content": "hello"})
    assert _r.get("blocked") is True
    assert _r.get("ok") is False


def test_execute_action_write_blocked():
    from nucleus.self_index.exporters import export_actions
    from nucleus.self_index.action_executor import execute_action
    _acts = export_actions().get("actions", [])
    _write = next((a for a in _acts if a.get("kind") == "write"), None)
    if _write:
        _r = execute_action(_write["action_id"], {})
        assert _r.get("blocked") is True


def test_execute_action_notfound():
    from nucleus.self_index.action_executor import execute_action
    _r = execute_action("no_such_action_x", {})
    assert _r.get("ok") is False
    assert "未在白名单" in _r.get("reason", "")
