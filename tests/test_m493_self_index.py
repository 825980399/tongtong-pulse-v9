# -*- coding: utf-8 -*-
"""493 批 Self-Index 身体索引底座 · 单测。

覆盖（任务书 §纪律「单测 ≥5 条」+ 治理）：
  · 导出器正常输出（493-1/2/3/4）；
  · 空数据降级（InfoField 单例不可用时不崩）；
  · 开关关闭不生成文件（493-1 总开关关 ⇒ 不读源、不落盘）；
  · 命中返回答案 / 查不到返回 None（493-5 fast path）。
"""
from __future__ import annotations


# ───────────────────────── 493-1 schema ─────────────────────────
def test_493_1_schema_keys():
    from nucleus.self_index.schema import (
        SELF_INDEX_VERSION, build_self_index, empty_self_index)
    _idx = build_self_index(
        [{"a": 1}], {"e": []}, [{"i": 1}], [{"r": 1}])
    assert set(_idx) >= {
        "version", "generated_at", "organs", "events", "intents", "body_relations"}
    assert _idx["version"] == SELF_INDEX_VERSION
    assert _idx["generated_at"]
    _e = empty_self_index()
    assert _e["organs"] == [] and _e["events"] == {} and _e["intents"] == []


def _fake_field(conditions):
    class _F:
        _conditions = conditions
    return _F()


# ───────────────────────── 493-2 事件订阅者 ─────────────────────────
def test_493_2_event_subscribers_normal(monkeypatch):
    from nucleus.self_index import exporters
    _conds = {
        "c1": {"condition": {"event_types": ["E1", "E2"], "organ_name": "胃"},
               "handler": lambda: None},
    }
    monkeypatch.setattr(
        "nucleus.field.InfoField.get_info_field", lambda: _fake_field(_conds))
    _d = exporters.export_event_subscribers()
    assert _d["event_count"] == 2
    assert _d["events"]["E1"][0]["organ"] == "胃"
    assert _d["events"]["E1"][0]["handler"] == "<lambda>"
    assert _d["events"]["E2"][0]["organ"] == "胃"


def test_493_2_empty_data(monkeypatch):
    """治理：InfoField 单例不可用（None）时 events 降级为空，不抛。

    注：fanout 取自独立的 health_snapshot（info_field.event_fanout），
    与 InfoField 单例无关，故不强制为空。
    """
    from nucleus.self_index import exporters
    monkeypatch.setattr(
        "nucleus.field.InfoField.get_info_field", lambda: None)
    _d = exporters.export_event_subscribers()
    assert _d["events"] == {}


# ───────────────────────── 493-3 器官运行态 ─────────────────────────
def test_493_3_organs_runtime(monkeypatch):
    from nucleus.self_index import exporters
    monkeypatch.setattr(
        "nucleus.cognitive.organ_inventory.export_organ_inventory",
        lambda: {"organs": [{
            "name": "胃", "file_path": "organs/body/PulseStomach.py",
            "type": "body", "is_core": False, "is_brain_organ": False,
            "health": "ok", "method_count": 3}]})
    monkeypatch.setattr(
        "nucleus.world_model.external_heartbeat.read_health_snapshot",
        lambda: {
            "memory_pressure": {"memory_by_organ": {
                "organs": [{"organ": "胃", "est_mb": 12.5}]}},
            "backpressure": {"L2": {"queue_depth": 3}}})
    _d = exporters.export_organs_runtime()
    assert _d["organ_count"] == 1
    _o = _d["organs"][0]
    assert _o["name"] == "胃"
    assert _o["file"] == "organs/body/PulseStomach.py"
    assert _o["memory_est_mb"] == 12.5
    assert _o["queue_depth"] == 3
    # 按器官的成功率/耗时数据源暂缺 ⇒ 诚实标 null
    assert _o["success_rate"] is None
    assert _o["avg_latency_ms"] is None


# ───────────────────────── 493-4 意图映射 ─────────────────────────
def test_493_4_intent_map(monkeypatch):
    from nucleus.self_index import exporters
    monkeypatch.setattr(
        "nucleus.qica.IntentChannels.INTENT_TO_METHOD",
        {"memory_query": "rule_reason",
         "cognitive_compute": "deep_think",
         "health_check": "health_check"})
    _d = exporters.export_intent_map()
    _by = {x["intent"]: x for x in _d["intents"]}
    assert _by["memory_query"]["local"] is True
    assert _by["cognitive_compute"]["local"] is False
    assert _by["health_check"]["local"] is True
    # 样本统计字段存在（真实 data/eval/intent_samples.jsonl）
    assert "sample_count_7d" in _by["memory_query"]
    assert "sample_count_all" in _by["memory_query"]


# ───────────────────────── 493-5 fast path ─────────────────────────
def _fake_organs(*names_files):
    return {"organs": [{
        "name": nf[0], "file": nf[1], "type": "x", "is_core": False,
        "is_brain_organ": False, "health": "ok", "method_count": 3}
        for nf in names_files]}


def test_493_5_fastpath_count(monkeypatch):
    """命中器官计数 ⇒ 返回含数量的答案（隔离 store，验证匹配逻辑）。"""
    from nucleus.self_index import store as S
    from nucleus.self_index.fastpath import self_query_fastpath
    monkeypatch.setattr(
        S.SelfIndexStore, "organs_runtime",
        lambda self: {"organs": [
            {"name": "胃", "file": "organs/body/PulseStomach.py", "type": "x",
             "is_core": False, "is_brain_organ": False, "health": "ok", "method_count": 3},
            {"name": "肝", "file": "organs/body/PulseLiver.py", "type": "x",
             "is_core": False, "is_brain_organ": False, "health": "ok", "method_count": 3}]})
    _a = self_query_fastpath("你有多少个器官")
    assert _a and "2 个" in _a and "器官" in _a


def test_493_5_fastpath_not_found(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from nucleus.self_index.fastpath import self_query_fastpath
    assert self_query_fastpath("今天北京天气怎么样") is None
    assert self_query_fastpath("讲个笑话") is None
    assert self_query_fastpath("") is None
    assert self_query_fastpath(None) is None


def test_493_5_fastpath_location(monkeypatch):
    """命中器官/代码指称 ⇒ 返回含代码路径的答案（不调大模型）。"""
    from nucleus.self_index import store as S
    from nucleus.self_index.fastpath import self_query_fastpath
    monkeypatch.setattr(
        S.SelfIndexStore, "organs_runtime",
        lambda self: {"organs": [
            {"name": "信息场", "file": "nucleus/field/InfoField.py", "type": "x",
             "is_core": False, "is_brain_organ": False, "health": "ok", "method_count": 3}]})
    monkeypatch.setattr(S.SelfIndexStore, "events", lambda self: {"events": {}})
    _a = self_query_fastpath("你的InfoField在哪")
    assert _a is not None and "代码路径" in _a and "InfoField.py" in _a


# ───────────────────────── 开关关闭不生成文件 ─────────────────────────
def test_493_off_no_file_generated(monkeypatch, tmp_path):
    """治理：493-1 总开关关闭 ⇒ 不读源、不落盘任何 json。"""
    from nucleus.self_index import store as S
    monkeypatch.setattr(
        "nucleus.self_index.exporters._self_index_on", lambda: False)
    _st = S.SelfIndexStore(root=str(tmp_path))
    assert _st.events() is None
    assert _st.organs_runtime() is None
    assert _st.intent_map() is None
    assert not (tmp_path / "event_subscribers.json").exists()
    assert not (tmp_path / "organs_runtime.json").exists()
    assert not (tmp_path / "intent_map.json").exists()


def test_493_store_dump_has_timestamp(monkeypatch, tmp_path):
    """治理：开关开时落盘文件含 generated_at 时间戳字段。"""
    from nucleus.self_index import store as S
    _st = S.SelfIndexStore(root=str(tmp_path))
    _d = _st.organs_runtime()
    _p = tmp_path / "organs_runtime.json"
    assert _p.exists()
    import json
    _j = json.loads(_p.read_text(encoding="utf-8"))
    assert _j["generated_at"]
    assert _j["version"]
