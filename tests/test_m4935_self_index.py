# -*- coding: utf-8 -*-
"""493.5 批单测（指称链接 + 模块图 + 动作库 + 运行态埋点 + fast path 扩展）。"""
import threading

import pytest


# ── 493.5-1 模块图 ──
def test_4935_module_graph_structure():
    from nucleus.self_index import store
    d = store.SelfIndexStore().module_graph()   # 触发构建 + 落盘（供 fast path 读）
    assert isinstance(d, dict) and "nodes" in d and "edges" in d
    assert len(d["nodes"]) > 0
    assert all("kind" in e for e in d["edges"])


def test_4935_module_graph_switch_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_M4935_MODULE_GRAPH", False)
    from nucleus.self_index import store
    assert store.SelfIndexStore().module_graph() is None


# ── 493.5-2 别名 + 模糊匹配 ──
def test_4935_link_organ_exact():
    from nucleus.self_index.exporters import link_organ
    assert link_organ("管消化的").get("organ") == "胃"


def test_4935_link_organ_exact2():
    from nucleus.self_index.exporters import link_organ
    # 「看东西的」单命中眼睛（嘴别名「说话」会与主动交互「主动说话」歧义，故用无歧义 query）
    assert link_organ("看东西的").get("organ") == "眼睛"


def test_4935_link_organ_ambig():
    from nucleus.self_index.exporters import link_organ
    r = link_organ("管思考的")
    assert r is not None and "candidates" in r   # 大脑/内在世界歧义→澄清


def test_4935_link_organ_none():
    from nucleus.self_index.exporters import link_organ
    assert link_organ("今天北京天气") is None


def test_4935_export_aliases():
    from nucleus.self_index.exporters import export_organ_aliases
    d = export_organ_aliases()
    assert d["organ_count"] == 57
    assert "胃" in d["aliases"]


def test_4935_alias_switch_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_M4935_ORGAN_ALIASES", False)
    from nucleus.self_index import store
    assert store.SelfIndexStore().organ_aliases() is None


# ── 493.5-3 动作库 ──
def test_4935_export_actions():
    from nucleus.self_index.exporters import export_actions
    d = export_actions()
    assert d["action_count"] >= 30
    for a in d["actions"]:
        if a.get("kind") == "write" and a.get("status") == "blocked":
            continue
        assert a["impl"].get("file"), a["action_id"]
        assert "line" in a["impl"], a["action_id"]


# ── 493.5-4 运行态埋点 + 开关 ──
def test_4935_organs_runtime_fields():
    from nucleus.self_index.exporters import export_organs_runtime
    d = export_organs_runtime()
    assert "last_channel" in d
    o = d["organs"][0]
    for k in ("inflight", "success_rate", "avg_latency_ms"):
        assert k in o, k


def test_4935_runtime_fill_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENABLE_M4935_RUNTIME_FILL", False)
    from nucleus.self_index.exporters import export_organs_runtime
    d = export_organs_runtime()
    assert d.get("last_channel") is None


def test_4935_infofield_getters_default():
    from nucleus.field.InfoField import InfoField
    f = InfoField.__new__(InfoField)
    f._layer_organ_inflight = {}
    f._handler_stats = {}
    f._handler_stats_lock = threading.Lock()
    f._last_channel = None
    assert f.get_organ_inflight("胃") == 0
    assert f.get_organ_handler_stats("胃") == {"success_rate": None, "avg_latency_ms": None}
    assert f.get_last_channel() is None


# ── 493.5-5 fast path 扩展 ──
def test_4935_fastpath_count():
    from nucleus.self_index.fastpath import self_query_fastpath
    assert "器官" in self_query_fastpath("你有多少个器官")


def test_4935_fastpath_alias():
    from nucleus.self_index.fastpath import self_query_fastpath
    a = self_query_fastpath("管消化的器官是什么")
    assert a and "PulseStomach" in a


def test_4935_fastpath_location_fallback():
    from nucleus.self_index import store
    store.SelfIndexStore().module_graph()  # 确保模块图落盘
    from nucleus.self_index.fastpath import self_query_fastpath
    a = self_query_fastpath("你的InfoField在哪")
    assert a and "InfoField.py" in a


def test_4935_fastpath_not_found():
    from nucleus.self_index.fastpath import self_query_fastpath
    assert self_query_fastpath("今天北京天气") is None
    assert self_query_fastpath("讲个笑话") is None
