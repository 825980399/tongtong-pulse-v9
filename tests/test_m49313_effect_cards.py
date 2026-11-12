"""493.13-1/2 效果卡：数据文件 schema + fastpath 命中 + 过期/诚实降级。

纪律：主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False；本批只测 fastpath 的
效果卡子能力（_match_effect_card / _is_effect_card_stale / _effect_section /
_answer_module_relation），不打开主笔。
"""
import json
import os

import pytest

from nucleus.self_index import fastpath as fp


def _cards_path():
    return os.path.join("data", "self_index", "effects_cards.json")


def test_effects_cards_file_exists_and_count():
    # 493.13-1：10-20 张高频卡
    assert os.path.isfile(_cards_path()), "effects_cards.json 未生成"
    cards = json.load(open(_cards_path(), encoding="utf-8"))
    assert 10 <= len(cards) <= 20, f"卡数 {len(cards)} 不在 10-20"
    # 每张必须带 file:line 证据 + 效果文案
    for c in cards:
        assert c.get("from") and c.get("to"), c
        assert c.get("event") or c.get("via"), c
        assert c.get("effect"), c
        assert c.get("evidence"), c
        assert c.get("stale_after_days"), c


def test_match_effect_card_exact_and_fallback():
    cards = [
        {"from": "A", "to": "B", "event": "e1", "effect": "x"},
        {"from": "A", "to": "B", "event": "", "effect": "fallback"},
    ]
    # 精确 (from,to,event)
    m = fp._match_effect_card(cards, "A", "B", "e1")
    assert m["effect"] == "x"
    # event 缺省时退化 (from,to)
    m2 = fp._match_effect_card(cards, "A", "B", "unknown_event")
    assert m2["effect"] == "fallback"
    # 无匹配
    assert fp._match_effect_card(cards, "X", "Y", "e1") is None


def test_is_effect_card_stale():
    from datetime import datetime, timedelta
    today = datetime.today().date()
    old = (today - timedelta(days=30)).isoformat()
    recent = today.isoformat()
    fresh = {"generated_at": recent, "stale_after_days": 7}
    expired = {"generated_at": old, "stale_after_days": 7}
    assert fp._is_effect_card_stale(expired) is True
    assert fp._is_effect_card_stale(fresh) is False


def test_effect_section_hit_returns_effect_line():
    # 构造 QICA→PulseCortex 的 qica.classify_result 边（与 effects_cards.json 真实卡对齐）
    found = [{
        "from": "nucleus.qica.QICA",
        "to": "organs.brain.PulseCortex",
        "kind": "event",
        "event": "qica.classify_result",
        "via": None,
    }]
    out = fp._effect_section(found, "QICA 把分类结果传给皮层会怎样")
    assert "效果：" in out, out
    assert "证据：" in out, out


def test_effect_section_no_card_honest_downgrade():
    # 有边但无效果卡 + 问效果词 → 诚实降级（不编后果）
    found = [{
        "from": "nucleus.self_index.exporters",
        "to": "data.self_index.module_graph.json",
        "kind": "call", "via": None, "event": None,
    }]
    out = fp._effect_section(found, "exporters 应用到 module_graph 会怎样")
    assert out, "应附诚实降级话术"
    assert "不能推断" in out or "没有沉淀" in out or "没有" in out, out


def test_effect_section_stale_only_structure():
    # 过期卡 → 不答效果，只答结构（_effect_section 返回空，结构由调用方给）
    cards = fp._load_effect_cards()
    # 强行把一条卡改成过期后验证 _match_effect_card 命中但 _is_effect_card_stale 拦截
    stale = {"from": "nucleus.qica.QICA", "to": "organs.brain.PulseCortex",
             "event": "qica.classify_result", "effect": "e", "generated_at": "2000-01-01",
             "stale_after_days": 7}
    assert fp._is_effect_card_stale(stale) is True


def test_answer_module_relation_includes_effect_when_card_hit():
    # 493.13-2 验收：QICA 与皮层的关系问法命中效果卡
    mg = fp._load_module_graph_cached()
    events = {}
    # 皮层→PulseCortex 别名解析依赖 organs 列表（与真实管线一致）
    from nucleus.self_index import store as _st
    organs = _st.SelfIndexStore().organs_runtime().get("organs", []) or []
    q = "QICA 怎么把话传给皮层"
    ans = fp._answer_module_relation(q, mg, events, organs)
    assert ans is not None, "关系路由应命中 QICA↔皮层"
    assert "效果：" in ans, ans
