"""493.17 批单测：verification_learning applied / 真实规则入账 /
downstream_delta 行为闭环 / 跨重启 applied 累计。

纪律：主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False；LEARNED_ALIASES 仍 False。
单测一律 tmp 隔离（QICA 路径注入 / hub 自定义 file_path），不污染生产磁盘数据。
"""
import json
import os
import types

from nucleus.qica.QICA import QICA
from nucleus.mnemosyne.verification_learning_hub import VerificationLearningHub


class _StubNodePool:
    """生产 node_pool 的最小依赖替身，让真实 _resolve_knowledge_paths 越过注入门槛。"""

    def get_path_distribution(self):
        return {}


def _qica_with_kb(trace_path):
    """构造注入知识树/节点池 + 隔离落盘路径的 QICA（模拟生产装配，非改判定）。"""
    q = QICA()
    q.set_knowledge_tree(object())
    q.set_node_pool(_StubNodePool())
    q._m49317_absorb_trace_path = str(trace_path)
    return q


# ── 刀1：verification_learning.json 补 applied（加载→补→保存→再加载）──
def test_applied_backfill_load_save_reload(tmp_path):
    vf = tmp_path / "verification_learning.json"
    # 旧 json：无 applied 字段；构造 remediation_triggered=True/False 两种
    old = {
        "version": "v1.0", "updated_at": 1.0,
        "entries": [
            {"id": "vl_1", "remediation_triggered": True, "lesson": "已补救"},
            {"id": "vl_2", "remediation_triggered": False, "lesson": "仅记录"},
            {"id": "vl_3", "remediation_triggered": True, "lesson": "已补救2"},
        ],
    }
    vf.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    hub = VerificationLearningHub(file_path=str(vf))
    # 加载即回填 + 保存
    assert all("applied" in e for e in hub._entries)
    on_disk = json.loads(vf.read_text(encoding="utf-8"))["entries"]
    assert all("applied" in e for e in on_disk), "落盘后每条都应带 applied"
    # remediation_triggered=True → applied=True，其余 False
    assert [e["applied"] for e in on_disk] == [True, False, True]
    assert sum(1 for e in on_disk if e["applied"] is True) == 2

    # 再加载（模拟重启）字段仍在
    hub2 = VerificationLearningHub(file_path=str(vf))
    assert all(isinstance(e["applied"], bool) for e in hub2._entries)


def test_applied_backfill_save_guards_missing(tmp_path):
    # _save 兜底：即使活体写入不带 applied 的新 entry，新代码保存时也会补
    vf = tmp_path / "vl.json"
    vf.write_text(json.dumps({"entries": []}, ensure_ascii=False), encoding="utf-8")
    hub = VerificationLearningHub(file_path=str(vf))
    hub._entries.append({"id": "x", "remediation_triggered": False})
    hub._save()
    saved = json.loads(vf.read_text(encoding="utf-8"))["entries"]
    assert saved[0]["applied"] is False


def test_new_record_entry_has_applied(tmp_path):
    vf = tmp_path / "vl2.json"
    hub = VerificationLearningHub(file_path=str(vf))
    hub.record(organ="semantic", task_type="t", input_summary="q",
               local_result={}, confidence=0.9, relevance_score=0.9,
               needs_verification=False, api_better=True,
               lesson="l", remediation_triggered=True, origin="growth")
    saved = json.loads(vf.read_text(encoding="utf-8"))["entries"]
    assert saved[-1]["applied"] is True, "新条目按实际消化结果置位"


# ── 刀2/刀3：真实规则经 absorb 链路 + downstream_delta 前后对比 ──
def _real_rules():
    # concept/path 均取自真实 data/semantic_lessons.json 的真实 lesson
    return [
        {"id": "semantic_lesson:def0edf132", "type": "domain_knowledge",
         "concept": "晚上好", "path": "/情感/问候", "confidence": 0.95,
         "source": "growth_distill", "rule_id": "semantic_lesson:def0edf132"},
        {"id": "semantic_lesson:0f85394856", "type": "domain_knowledge",
         "concept": "数字化生命", "path": "/知识/数字化生命", "confidence": 0.85,
         "source": "growth_distill", "rule_id": "semantic_lesson:0f85394856"},
        {"id": "semantic_lesson:64e1b41c3a", "type": "domain_knowledge",
         "concept": "科技新闻", "path": "/技术/新闻", "confidence": 0.90,
         "source": "growth_distill", "rule_id": "semantic_lesson:64e1b41c3a"},
    ]


def test_real_rules_absorbed_with_concrete_delta(tmp_path):
    trace = tmp_path / "absorb_trace.jsonl"
    q = _qica_with_kb(trace)
    rules = _real_rules()
    n = q.absorb_growth_rules(rules)
    assert n == 3
    rows = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 3
    for r in rows:
        # 刀2：真实规则（非测试种子）、真实 source、具体 delta
        assert str(r["rule_id"]).startswith("semantic_lesson:"), r
        assert "_test" not in json.dumps(r, ensure_ascii=False)
        assert r["source"] == "growth_distill"
        assert r["result"] == "applied"
        # 刀3：不只是「更新条目」，要有吸收前/后对比 + 具体路径
        d = r["downstream_delta"]
        assert "吸收前" in d and "吸收后" in d, d
        assert "路径已变化" in d, d
        assert r["path"] in d, d
        assert r["concept"] in d, d


def test_behavior_delta_records_real_path_change(tmp_path):
    # 完整闭环：吸收前探针 → 真 absorb → 吸收后探针 → delta 记录路径变化
    trace = tmp_path / "t.jsonl"
    q = _qica_with_kb(trace)
    concept, path = "数字化生命", "/知识/数字化生命"
    before = q._m49317_probe_knowledge_paths(concept)
    assert before == ["/知识"], before
    q.absorb_growth_rules([_real_rules()[1]])
    after = q._m49317_probe_knowledge_paths(concept)
    assert before != after, "吸收后知识路径应真实变化"
    assert path in after, after
    row = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()][-1]
    assert "吸收前[/知识]" in row["downstream_delta"], row["downstream_delta"]
    assert path in row["downstream_delta"], row["downstream_delta"]


def test_behavior_delta_honest_when_no_kb(tmp_path):
    # 未注入知识树时路径不参与，delta 如实标注「未变」，不编造
    trace = tmp_path / "t2.jsonl"
    q = QICA()
    q._m49317_absorb_trace_path = str(trace)
    q.absorb_growth_rules([_real_rules()[0]])
    row = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()][-1]
    assert "吸收前" in row["downstream_delta"]
    assert "路径未变" in row["downstream_delta"], row["downstream_delta"]


# ── 刀4：跨重启从落盘复算 applied ──
def test_cross_restart_applied_recomputed(tmp_path):
    trace = tmp_path / "restart.jsonl"
    # 写 3 条真实 applied 记录（模拟上一轮进程留下的历史）
    hist = [
        {"ts": 1.0, "rule_id": f"r{i}", "type": "domain_knowledge",
         "concept": f"c{i}", "path": "/p", "source": "growth_distill",
         "result": "applied", "downstream_delta": "d"}
        for i in range(3)
    ]
    trace.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in hist) + "\n",
                     encoding="utf-8")
    # 新实例 = 模拟重启；首次 absorb_record 从落盘复算，不靠内存 dict
    q = QICA()
    q._m49317_absorb_trace_path = str(trace)
    assert q._m49317_applied_count_from_disk() == 3, "applied 从落盘文件复算=3"
    q._m49315_absorb_record(
        {"id": "r4", "type": "domain_knowledge", "concept": "c4",
         "path": "/p", "source": "growth_distill"}, "applied", "d")
    # 累计不丢历史：3 历史 + 1 新增
    assert q._m49315_absorb_applied == 4, q._m49315_absorb_applied
    assert len(q._m49315_absorb_trace) == 4
