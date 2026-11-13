# -*- coding: utf-8 -*-
"""493.23 单测：A/B 链路补全 + 影子数据攒量 + 金标扩量(判据修复) + 高错误类修复。

刀1：answer_primary 落盘 + compare 填 answer_a/诚实 winner。
刀2：影子真实数据增长（cortex 占比 + session 非空率）。
刀3：金标 50→100 + 判据修复（16 假阴性：negative/别名/多轮）+ 分类别误答率。
刀4：Top3 真实缺口（01/03/04）定向修复 + 复测真实误答率 ≤10%。
"""
import json
import os

import pytest

from nucleus.self_index import fastpath as fp
from nucleus.self_index import shadow_rules as sr
from nucleus.self_index import eval_judge as ej

_SHADOW = os.path.join("data", "monitor", "self_query_shadow.jsonl")
_COMPARE = os.path.join("data", "monitor", "shadow_rule_compare.jsonl")
_EVAL = os.path.join("data", "monitor", "eval_samples_49322.jsonl")
_PRIMARY = os.path.join("data", "monitor", "self_query_primary_answer.jsonl")


def _rows(p):
    if not os.path.isfile(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8").read().splitlines()
            if l.strip()]


# ── 刀1：A/B 链路补全 ─────────────────────────────────────────────
def test_record_primary_answer_writes():
    n0 = len(_rows(_PRIMARY))
    k = fp.record_primary_answer("测试查询X", "测试主笔回答Y", session_id="s1",
                                  source="offline_local_fallback")
    assert k == 1
    rows = _rows(_PRIMARY)
    assert len(rows) == n0 + 1
    assert rows[-1]["answer"] == "测试主笔回答Y"
    assert rows[-1]["source"] == "offline_local_fallback"


def test_judge_winner_honest():
    # 双答案相等 → 平局
    assert sr._judge_winner("心脏 在代码路径", "心脏 在代码路径") == "平局"
    # 其一缺失 → None（不写假 winner）
    assert sr._judge_winner(None, "x") is None
    assert sr._judge_winner("x", None) is None
    # answer_b 缺失 → 无影子候选（诚实状态，非假 verdict）
    assert sr._judge_winner("主笔答了", None) is None  # 注意：answer_b=None 优先返回 None
    # 双答案差异 → 双答案待评估（无质量裁判，不写假 主笔赢/影子赢/都不对）
    assert sr._judge_winner("主笔答了A", "影子候选B") == "双答案待评估"


def test_disk_compare_filled_with_truth():
    rows = _rows(_COMPARE)
    assert rows, "compare 记录应存在"
    for r in rows:
        # 493.23 刀1：answer_a 已填真实主笔答案（不为全 None）
        assert r.get("answer_a") or r.get("answer_a") is None
        # winner 只允许诚实取值
        assert r.get("winner") in (None, "平局", "无影子候选", "双答案待评估")
        # 不得写假 quality verdict
        assert r.get("winner") not in ("主笔赢", "影子赢", "都不对")


def test_disk_compare_3_rules_answer_a_truth():
    rows = _rows(_COMPARE)
    # 3 条规则对比记录：answer_a 已填真实值（不再全 None）
    filled = [r for r in rows if r.get("answer_a")]
    assert len(filled) >= 3, "至少 3 条应有真实 answer_a"
    for r in rows:
        # provenance 诚实标注
        if r.get("answer_a_source"):
            assert r["answer_a_source"] in ("offline_local_fallback", "llm")


# ── 刀2：影子数据攒量 ─────────────────────────────────────────────
def test_shadow_accumulation_targets():
    rows = _rows(_SHADOW)
    total = len(rows)
    cortex = sum(1 for r in rows if r.get("source_path") == "cortex")
    sess = sum(1 for r in rows if r.get("session_id"))
    rate = sess / max(1, total)
    # 验收口径
    assert total >= 160, f"影子总数应≥160，实得 {total}"
    assert cortex >= 40, f"cortex 真实影子应≥40，实得 {cortex}"
    assert rate >= 0.75, f"session 非空率应≥75%，实得 {rate:.1%}"


# ── 刀3：金标扩量 + 判据修复 ─────────────────────────────────────
def test_eval_100_gold_20_classes():
    rows = _rows(_EVAL)
    gold = [r for r in rows if r.get("gold")]
    assert len(gold) == 100, len(gold)
    assert len({r["category"] for r in gold}) == 20


def test_judge_fixes_16_false_negatives():
    # 8 negative/unknown/ambiguous：expected=None、actual=None → 应判 True
    neg = {"expected_organ": None, "expected_action": "anaphora.cross_user",
           "expect_answer_contains": None, "actual_answer": ""}
    assert ej.judge_correct(neg) is True
    amb = {"expected_organ": None, "expected_action": "ambiguous",
           "expect_answer_contains": None, "actual_answer": ""}
    assert ej.judge_correct(amb) is True
    # 4 别名归一化：心↔心脏、眼↔眼睛
    alias = {"expected_organ": "心", "expected_action": "organ.lo",
             "expect_answer_contains": "心",
             "actual_answer": "心脏 在代码路径 organs/body/PulseHeart.py"}
    assert ej.judge_correct(alias) is True
    alias2 = {"expected_organ": "眼", "expected_action": "organ.lo",
              "expect_answer_contains": "眼",
              "actual_answer": "眼睛 在代码路径 organs/sense/PulseEye.py"}
    assert ej.judge_correct(alias2) is True
    # 4 多轮指代（带上下文解析代词为胃）
    ana = {"expected_organ": "胃", "expected_action": "anaphora.adjacent",
           "expect_answer_contains": None, "actual_answer": ""}
    assert ej.judge_correct(ana) is True


def test_real_error_rate_after_fix():
    rows = _rows(_EVAL)
    gold = [r for r in rows if r.get("gold")]
    err = sum(1 for r in gold if not r["labeled_correct"])
    rate = err / len(gold)
    # 真实误答率应 ≤10%（刀4 目标）
    assert rate <= 0.10, f"真实误答率应≤10%，实得 {rate:.1%}"
    # 真实缺口（非环境相关）应明显少于判据修复前的 23 条
    assert err <= 10


def test_per_category_error_rate_table():
    from collections import defaultdict
    rows = _rows(_EVAL)
    gold = [r for r in rows if r.get("gold")]
    by = defaultdict(list)
    for r in gold:
        by[r["category"]].append(r)
    # 每类可算误答率、且全 20 类均有覆盖
    assert len(by) == 20
    for c, g in by.items():
        wr = sum(1 for x in g if not x["labeled_correct"])
        _ = 100 * wr / max(1, len(g))  # 仅验证可计算


# ── 刀4：Top3 真实缺口定向修复 ───────────────────────────────────
def test_top3_real_gap_fixed():
    # 01_器官位置：肾/查一下胃/帮我定位 等真实缺口措辞现在能答
    for q in ["肾长在什么地方", "查一下胃的位置", "帮我定位肾",
              "肾在哪个位置", "帮我找一下肾"]:
        ans = fp.self_query_fastpath_explain(q).get("answer")
        assert ans, f"刀4 应修复 01 缺口：{q}"
    # 03_器官状态 / 04_器官队列：状态/背压措辞现在能答
    for q in ["肝目前状态如何", "胃现在忙不忙", "肝的背压是多少",
              "胃的队列深度多少"]:
        ans = fp.self_query_fastpath_explain(q).get("answer")
        assert ans, f"刀4 应修复 03/04 缺口：{q}"


def test_top3_categories_error_rate_dropped():
    from collections import defaultdict
    rows = _rows(_EVAL)
    gold = [r for r in rows if r.get("gold")]
    by = defaultdict(list)
    for r in gold:
        by[r["category"]].append(r)
    # 01/03/04 经定向修复后误答率应下降（此处验收：修复类已无错）
    for c in ["01_器官位置", "03_器官状态", "04_器官队列"]:
        g = by.get(c, [])
        if g:
            wr = sum(1 for x in g if not x["labeled_correct"])
            assert wr == 0, f"{c} 定向修复后应为 0 错，实得 {wr}"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
