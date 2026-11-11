# -*- coding: utf-8 -*-
"""493.10 批刀1-5：模块图真实消费 + 模块名 veto + 边渲染 + 过期话术。

纪律：总闸 ENABLE_M49310_MODULE_GRAPH_CONSUME 关 → 逐字回 493.9 行为
（关系问法重新落回位置分支、link_organ 恢复模块名污染、module.imports 回全图）。
"""
import re

import pytest

import config as _cfg
from nucleus.self_index.exporters import link_organ, is_module_token, ACTION_WHITELIST
from nucleus.self_index.fastpath import (
    self_query_fastpath_explain, _answer_module_relation, _render_module_edge,
    _load_module_graph_cached, _answer_module_call)
from nucleus.self_index.module_graph import export_module_edges
from nucleus.self_index.action_executor import execute_action


@pytest.fixture(autouse=True)
def _switch_on(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49310_MODULE_GRAPH_CONSUME", True, raising=False)


REL_QS = ["pulse_inner_world_support 和 InfoField 怎么连",
          "PulseCortex 和 PulseLung 怎么连",
          "胃和肝怎么连",
          "InfoField 被谁调用"]


# ───────────── 493.10-1 关系词优先于位置分支 ─────────────
@pytest.mark.parametrize("q", REL_QS)
def test_relation_not_hijacked_by_location(q):
    _ex = self_query_fastpath_explain(q)
    assert _ex.get("answer"), f"{q} 必须本地答出"
    assert _ex.get("action_id") == "module.relation", (q, _ex.get("action_id"))
    assert "模块在代码路径" not in str(_ex["answer"]), q
    assert "在代码路径" not in str(_ex["answer"]), q


def test_relation_switch_off_reverts_to_4939(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49310_MODULE_GRAPH_CONSUME", False, raising=False)
    _ex = self_query_fastpath_explain("pulse_inner_world_support 和 InfoField 怎么连")
    assert _ex.get("action_id") == "module.location"     # 493.9 原行为：被位置分支截走
    assert "模块在代码路径" in str(_ex.get("answer"))


def test_relation_explicit_no_edge():
    # 两个互不相干的模块：如实说「未找到直接连接边」，不回位置话术、不编造
    _q = "NoSuchModuleAAA 和 NoSuchModuleBBB 怎么连"
    _ex = self_query_fastpath_explain(_q)
    assert "未找到直接连接边" in str(_ex.get("answer")), _ex


# ───────────── 493.10-2 模块名 veto ─────────────
def test_module_token_detection():
    for _t in ["PulseCortex", "InfoField", "QICA",
               "pulse_inner_world_support", "PulseStomach.py", "organs/brain/PulseCortex"]:
        assert is_module_token(_t) is True, _t
    for _t in ["胃", "肝", "肺", "心脏"]:
        assert is_module_token(_t) is False, _t


def test_link_organ_not_bound_to_module_name():
    assert link_organ("PulseCortex 和 PulseLung 怎么连") is None
    assert link_organ("PulseCortex 是器官还是模块") is None


def test_link_organ_chinese_organs_unaffected():
    assert link_organ("胃在哪")["organ"] == "胃"
    assert link_organ("管消化的")["organ"] == "胃"
    assert link_organ("看东西的")["organ"] == "眼睛"


def test_link_organ_veto_off_reverts(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49310_MODULE_GRAPH_CONSUME", False, raising=False)
    # 493.9 行为：func 层别名 cortex 把 PulseCortex 绑成「大脑皮层」
    assert link_organ("PulseCortex 和 PulseLung 怎么连")["organ"] == "大脑皮层"


# ───────────── 493.10-3 边渲染含 event/行号 + 目标行未解析 ─────────────
def test_render_event_edge_has_event_and_lines():
    _mg = _load_module_graph_cached()
    assert _mg and _mg.get("edges")
    _evs = [e for e in _mg["edges"]
            if e.get("kind") == "event" and e.get("event") == "lungs.select_model"
            and "PulseCortex.py" in str(e.get("from_file") or "")]
    assert _evs, "图中应有 Cortex→Lung 的 lungs.select_model 边"
    _s = " ".join(_render_module_edge(e) for e in _evs)
    assert "lungs.select_model" in _s
    # ★493.11-2 给 PulseCortex.py 加了 12 行 → 落盘 event 边行号随源码漂移
    #   （1939→1951）。不钉死具体数字，只断言「起点文件:数字 → 终点文件:数字」
    #   格式，避免源码增删行造成假红；终点行号也按同一口径校验。
    assert re.search(r"PulseCortex\.py:\d+", _s), _s
    assert re.search(r"PulseLung\.py:\d+", _s), _s


def test_render_inject_call_edge_marks_unresolved():
    _s = _render_module_edge({"kind": "inject", "via": "info_field",
                              "from_file": "organs/brain/pulse_inner_world_support.py",
                              "from_line": 400, "to_file": None, "to_line": None})
    assert "目标行未解析" in _s
    assert "400" in _s
    assert " :" not in _s


def test_cortex_lung_answer_real():
    _mg = _load_module_graph_cached()
    _ans = _answer_module_call(_mg, "organs.brain.PulseCortex", "organs.body.PulseLung")
    assert _ans and "lungs.select_model" in _ans
    # 493.11 同步：不钉死随源码漂移的具体行号，只断言两段真实行号都在
    assert re.search(r"PulseCortex\.py:\d+", _ans), _ans
    assert re.search(r"PulseLung\.py:\d+", _ans), _ans


def test_iw_infofield_answer_marks_unresolved():
    _ex = self_query_fastpath_explain("pulse_inner_world_support 和 InfoField 怎么连")
    assert "目标行未解析" in str(_ex.get("answer"))
    assert "inject" in str(_ex.get("answer")) or "call" in str(_ex.get("answer"))


# ───────────── 493.10-4 过期话术删除 ─────────────
def test_no_stale_wording_in_fastpath_source():
    _src = open("nucleus/self_index/fastpath.py", encoding="utf-8").read()
    assert "待 493-8 调用图补齐" not in _src
    assert "尚未索引模块级调用关系" not in _src


def test_no_stale_wording_in_exporters_source():
    _src = open("nucleus/self_index/exporters.py", encoding="utf-8").read()
    assert "待 493-8 调用图补齐" not in _src
    assert "行号待 493-8 CallGraphAnalyzer 补齐" not in _src


# ───────────── 493.10-5 module.imports 过滤 ─────────────
def test_export_module_edges_filtered():
    _r = export_module_edges("PulseLung")
    assert _r["matched"] is True
    assert _r["scope"] == "filtered"
    assert _r["edge_count"] > 0
    assert all("PulseLung" in e.get("from", "") or "PulseLung" in e.get("to", "")
               for e in _r["edges"])
    assert "node_count" not in _r and "nodes" not in _r   # 不吐 468 节点


def test_export_module_edges_summary_without_module():
    _r = export_module_edges()
    assert _r["scope"] == "summary"
    assert _r["edge_count"] > 5000
    assert _r["edges"] == []          # 摘要不吐全量边
    assert "nodes" not in _r


def test_export_module_edges_not_found_honest():
    _r = export_module_edges("NoSuchModuleXYZ")
    assert _r["matched"] is False
    assert _r["edges"] == []


def test_module_imports_action_filters():
    _r = execute_action("module.imports", {"module": "PulseLung"})
    assert _r.get("ok") is True
    _res = _r.get("result") or {}
    assert _res.get("scope") == "filtered"
    assert _res.get("edge_count", 0) > 0
    assert "nodes" not in _res       # 不再吐 468 节点全图
    assert (_r.get("verified") or {}).get("ok") is True


def test_module_imports_action_empty_honest_fail():
    _r = execute_action("module.imports", {"module": "NoSuchModuleXYZ"})
    assert _r.get("ok") is False                 # 空表诚实判失败
    assert _r.get("executed") is True            # 方法确实跑通
    assert (_r.get("verified") or {}).get("ok") is False


def test_module_imports_no_param_returns_summary():
    _r = execute_action("module.imports", {})
    assert _r.get("ok") is True
    _res = _r.get("result") or {}
    assert _res.get("scope") == "summary"
    assert _res.get("node_count", 0) > 400       # 只有计数，不吐 468 个节点对象
    assert _res.get("edge_count", 0) > 5000
    assert _res.get("edges") == []
    assert "nodes" not in _res


def test_module_imports_switch_off_reverts(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49310_MODULE_GRAPH_CONSUME", False, raising=False)
    _r = execute_action("module.imports", {"module": "PulseLung"})
    assert _r.get("ok") is True
    assert len((_r.get("result") or {}).get("nodes", [])) > 400   # 493.9：全图 468 节点


def test_descriptor_has_filter_contract():
    _acts = {a["action_id"]: a for a in ACTION_WHITELIST}
    _d = _acts["module.imports"]
    assert _d["impl"]["filter_target"] == "export_module_edges"
    assert _d["filtered_verify"]["type"] == "module_edges"
    assert _d["verify"]["type"] == "is_dict"     # 关闸仍走原 verify
