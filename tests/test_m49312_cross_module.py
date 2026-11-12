# -*- coding: utf-8 -*-
"""493.12-1/2/3 跨模块语义问答：guard 分层 + 关系词扩展 + 模块名精确匹配。

纪律：主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False（本测试直接验 explain/guard 函数，
不依赖主笔开关）；四刀开关默认开。测试不依赖运行态装配。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.self_index import fastpath as fp  # noqa: E402


# ── 493.12-1 guard 因果词分层 ──
def test_guard_philosophy_still_skipped():
    # 纯哲学/主观判断词仍跳过
    for q in ["幸福是什么", "人生意义", "我应不应该逃跑", "比较一下肺和肝"]:
        assert fp.fastpath_should_skip(q) is True, q
        assert fp._guard_reason(q) in ("philosophical_judgment", "causal_or_judgment_word")


def test_guard_module_causal_not_skipped():
    # 模块关系因果词放行（不再一棍子 skip）
    for q in [
        "pulse_inner_world_support 应用到 InfoField 会怎样",
        "如果 InfoField 挂了会怎样",
        "Cortex 调用 Lung 会产生什么效果",
        "pulse_inner_world_support 对 InfoField 有什么影响",
    ]:
        assert fp.fastpath_should_skip(q) is False, q
        assert fp._guard_reason(q) == "causal_on_module", q


def test_guard_module_name_queries_not_skipped():
    # 提到已知模块名 / 器官别名 → 视为自我查询，不误拦
    for q in ["QICA 怎么把话传给皮层", "InfoField 和 PulseCortex 是什么关系"]:
        assert fp.fastpath_should_skip(q) is False, q


# ── 493.12-2 关系词扩展 + 诚实降级 ──
def test_eight_queries_answered_locally():
    qs = [
        "pulse_inner_world_support 应用到 InfoField 会怎样",
        "肺和胃之间怎么协作",
        "肝吸收知识后会做什么",
        "如果 InfoField 挂了会怎样",
        "QICA 怎么把话传给皮层",
        "Cortex 调用 Lung 会产生什么效果",
        "InfoField 和 PulseCortex 是什么关系",
        "pulse_inner_world_support 对 InfoField 有什么影响",
    ]
    for q in qs:
        ex = fp.self_query_fastpath_explain(q)
        assert ex.get("answer"), f"空答: {q}"
        assert ex.get("action_id") == "module.relation", q


def test_honest_degradation_on_effect_questions():
    # 493.16 刀1 后语义：效果类问法命中证据卡 → 给带 file:line 的证据化效果
    # （不再重复发「无数据」降级）；未命中卡 → 诚实降级/人话说明。
    # 两种都必须诚实：不编「幸福是什么」这类泛泛后果、不冒充确定效果。
    for q in [
        "Cortex 调用 Lung 会产生什么效果",
        "pulse_inner_world_support 应用到 InfoField 会怎样",
        "如果 InfoField 挂了会怎样",
    ]:
        ans = fp.self_query_fastpath_explain(q).get("answer") or ""
        assert ans, f"空答: {q}"
        _honest = (("证据：" in ans and "效果" in ans)  # 有卡：证据化效果
                   or "不能推断" in ans                 # 无卡：诚实降级
                   or "不编" in ans                    # 纯结构边：人话说明
                   or "没有" in ans)
        assert _honest, f"缺诚实口径: {q} -> {ans[:90]}"
        assert "幸福是什么" not in ans, f"不得落到泛泛 canned: {q}"
    # 哲学问法仍跳过，不会落到「幸福是什么」canned
    assert fp.self_query_fastpath_explain("幸福是什么").get("answer") is None


def test_liver_digest_edge_prioritized():
    # 「肝吸收知识后会做什么」单实体关系问法应展示业务边（非全是 import 噪声）
    ans = fp.self_query_fastpath_explain("肝吸收知识后会做什么").get("answer") or ""
    assert "连接" in ans
    # digest 相关边（knowledge_tree / info_field）应排在 import 之前被展示
    assert "knowledge_tree" in ans or "info_field" in ans


# ── 493.12-3 模块名精确匹配 ──
def _mg():
    return fp._load_module_graph_cached()


def test_find_module_exact_qica_not_intentchannels():
    mg = _mg()
    node = fp._find_module(mg, "qica")
    assert node is not None
    assert node.get("module") == "nucleus.qica.QICA"
    assert node.get("file").endswith("nucleus/qica/QICA.py")
    # 不得误解析成同目录 IntentChannels
    assert "IntentChannels" not in node.get("file")


@pytest.mark.parametrize("token,expected_module,expected_file", [
    ("qica", "nucleus.qica.QICA", "nucleus/qica/QICA.py"),
    ("pulsecortex", "organs.brain.PulseCortex", "organs/brain/PulseCortex.py"),
    ("pulseliver", "organs.body.PulseLiver", "organs/body/PulseLiver.py"),
    ("infofield", "nucleus.field.InfoField", "nucleus/field/InfoField.py"),
    ("pulsemouth", "organs.motor.PulseMouth", "organs/motor/PulseMouth.py"),
])
def test_find_module_exact(token, expected_module, expected_file):
    mg = _mg()
    node = fp._find_module(mg, token)
    assert node is not None
    assert node.get("module") == expected_module
    assert node.get("file").endswith(expected_file)


def test_resolve_module_terms_qica_no_intentchannels():
    mg = _mg()
    terms = fp._resolve_module_terms(mg, [], "qica")
    assert terms == {"nucleus.qica.QICA"}
    # 关键：不得把同目录 IntentChannels 也加进来
    assert not any("intentchannels" in t.lower() for t in terms)


def test_qica_to_cortex_relation_not_intentchannels():
    # 「QICA 怎么把话传给皮层」必须解析 QICA↔PulseCortex（皮层→大脑皮层→PulseCortex）
    ans = fp.self_query_fastpath_explain("QICA 怎么把话传给皮层").get("answer") or ""
    assert "PulseCortex" in ans, ans[:120]
    assert "IntentChannels" not in ans, ans[:160]
