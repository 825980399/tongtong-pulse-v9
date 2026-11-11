# -*- coding: utf-8 -*-
"""493.9 批：断网本地应答兜底 + 真 bug 修复 — 单元测试（8 刀全覆盖）。

纪律回归：所有新行为有独立开关（ENABLE_M4939_OFFLINE_LOCAL 总闸 + 各刀分闸），
关总闸逐字回 493.8；正式 fastpath 主笔 ENABLE_M493_SELF_QUERY_FASTPATH 仍 False。
"""
import json
import os
import sys
import threading

import pytest

import config as _cfg
import nucleus.field.InfoField as _IF
import nucleus.self_index.action_executor as _ae
import nucleus.self_index.offline_local as _ol
from nucleus.self_index.exporters import ACTION_WHITELIST, link_organ, export_organs_runtime
from nucleus.self_index.fastpath import _find_organ, self_query_fastpath_explain
from nucleus.self_index.store import SelfIndexStore as _Store


@pytest.fixture(autouse=True)
def _offline_on(monkeypatch):
    """默认构造断网态（注入钩子），各测试可覆盖。"""
    monkeypatch.setattr(_cfg, "ENABLE_M4939_OFFLINE_LOCAL", True, raising=False)
    monkeypatch.setattr(_cfg, "M4939_FORCE_OFFLINE_LOCAL", True, raising=False)
    yield


# ─────────────────────── 493.9-1 指称不命中真 bug ───────────────────────
def test_organs_runtime_name_is_chinese():
    """任务书要求实测并落报告：导出 name 是中文名「胃」，非类名 PulseStomach。"""
    _o = export_organs_runtime()
    _names = [x.get("name") for x in _o.get("organs", [])]
    assert "胃" in _names
    _stomach = next(x for x in _o["organs"] if x.get("name") == "胃")
    assert "PulseStomach" in str(_stomach.get("file"))


def test_shadow_mode_five_acceptance_cases():
    """验收 5 条（影子模式 must 命中）。"""
    for _q in ["胃在哪", "我的胃在哪个文件", "InfoField在哪",
               "你有多少个器官", "肺的并发数"]:
        _ex = self_query_fastpath_explain(_q)
        assert _ex.get("answer"), f"{_q} 必须命中，实际 {_ex}"


def test_find_organ_three_way_alias():
    _organs = (_Store().organs_runtime() or {}).get("organs", [])
    for _kw, _expect in [("胃", "胃"), ("PulseStomach", "胃"),
                         ("管消化的", "胃"), ("看东西的", "眼睛")]:
        _o = _find_organ(_organs, _kw)
        assert _o and _o.get("name") == _expect, (_kw, _o)


def test_link_organ_find_organ_consistency():
    """link_organ 命中后，fastpath 必须能在 organs 列表找到同一器官。"""
    _organs = (_Store().organs_runtime() or {}).get("organs", [])
    _r = link_organ("胃在哪")
    assert _r and _r.get("organ") == "胃"
    assert _find_organ(_organs, _r["organ"]).get("name") == "胃"


def test_defensive_load_single_source_failure(monkeypatch):
    """真 bug 回归：events() 单点异常不得让整链全灭（原单一大 try）。"""
    def _boom(self):
        raise RuntimeError("simulated restart failure")
    monkeypatch.setattr(_Store, "events", _boom)
    _ex = self_query_fastpath_explain("胃在哪")
    assert _ex.get("answer"), _ex
    assert _ex.get("organ") == "胃"


def test_defensive_off_reverts(monkeypatch):
    """关刀1开关 → 逐字回 493.8（单一大 try，events 异常即全灭）。"""
    monkeypatch.setattr(_cfg, "ENABLE_M4939_FASTPATH_DEFENSIVE", False, raising=False)
    def _boom(self):
        raise RuntimeError("simulated")
    monkeypatch.setattr(_Store, "events", _boom)
    assert self_query_fastpath_explain("胃在哪").get("answer") is None


# ─────────────────────── 493.9-2 分类型 canned ───────────────────────
def test_classify_query_type():
    assert _ol.classify_query_type("你好") == "greeting"
    assert _ol.classify_query_type("晚上好") == "greeting"
    assert _ol.classify_query_type("胃在哪") == "self_body"
    assert _ol.classify_query_type("为什么L2会满") == "causal"
    assert _ol.classify_query_type("比较胃和肝") == "causal"
    assert _ol.classify_query_type("问题") == "unknown"


def test_canned_three_types_distinct_and_tagged():
    _g = _ol.canned_reply_for("你好")
    _s = _ol.canned_reply_for("胃在哪", local_answer="胃 在代码路径 X")
    _c = _ol.canned_reply_for("为什么L2会满")
    assert len({_g, _s, _c}) == 3, "三类必须回不同话术"
    assert all(_ol.TAG_LOCAL in str(x) for x in (_g, _s, _c))
    assert _ol.TAG_INDEX in _s          # 自我类接本地索引结果
    assert "外脑" in _s or "外部模型" in _c  # 因果类明确拒答
    assert "需要外部模型" in _c


def test_canned_unknown_falls_back_to_constant():
    """无法分类 → None ⇒ 调用方逐字回落 485 常量（保既有断言）。"""
    assert _ol.canned_reply_for("问题") is None
    assert _ol.canned_reply_for("帮我看看这个报错") is None


def test_canned_off_reverts(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M4939_CANNED_CLASSIFY", False, raising=False)
    assert _ol.canned_reply_for("你好") is None


def test_master_switch_off_reverts_all(monkeypatch):
    """总闸关 → 全部分类/模板/分层不介入。"""
    monkeypatch.setattr(_cfg, "ENABLE_M4939_OFFLINE_LOCAL", False, raising=False)
    assert _ol.canned_reply_for("你好") is None
    assert _ol.local_basic_template("你好")[0] is None
    assert _ol.offline_layered_answer("胃在哪").get("answer") is None


# ─────────────────────── 493.9-3 rule_reason 本地模板 ───────────────────────
def test_local_template_covers_8_of_12():
    _qs = ["你好", "晚上好", "再见", "谢谢", "你是谁", "你能做什么", "你是什么",
           "你现在感觉怎么样", "你健康吗", "你最近怎么样", "我是谁", "你有多少个器官"]
    _hit = sum(1 for _q in _qs if _ol.local_basic_template(_q)[0])
    assert _hit >= 8, f"本地模板需覆盖 ≥8 条，实际 {_hit}"


def test_local_template_identity_uses_real_data():
    _a, _ = _ol.local_basic_template("你是谁")
    _o = export_organs_runtime()
    assert str(_o.get("organ_count")) in _a          # 真实器官数，不是写死套话
    assert "数字新人类" not in _a
    assert _ol.TAG_LOCAL in _a and _ol.TAG_INDEX in _a


def test_local_template_offline_only(monkeypatch):
    """联网（非离线态）→ 模板不介入，不抢正常对话。"""
    monkeypatch.setattr(_cfg, "M4939_FORCE_OFFLINE_LOCAL", False, raising=False)
    monkeypatch.setattr(_ol, "offline_active", lambda: False)
    assert _ol.local_basic_template("你好")[0] is None


def test_who_are_you_broken_fixed():
    """「我是谁」不得再输出「你是，我们正在对话中。」"""
    from organs.brain.pulse_inner_world_support import PulseInnerWorldSupportMixin as _M
    class _Stub(_M):
        def _build_growth_retrospective(self, *a, **k):
            return "成长回溯"
        def _build_how_am_i_response(self, *a, **k):
            return "我很好"
        def _get_current_emotion(self):
            return "平静"
    # 关离线态以走原有 broken 分支
    import nucleus.self_index.offline_local as _ol2
    _orig = _ol2.offline_active
    _ol2.offline_active = lambda: False
    try:
        _r = _Stub()._rule_reason("我是谁")
        assert "你是，" not in str(_r)
        assert _r and len(str(_r)) > 4
    finally:
        _ol2.offline_active = _orig


def test_rule_reason_offline_12_questions(monkeypatch):
    """端到端：断网态下 _rule_reason 12 条 ≥8 本地可答，且全部不调大模型（纯函数）。"""
    from organs.brain.pulse_inner_world_support import PulseInnerWorldSupportMixin as _M
    class _Stub(_M):
        def _build_growth_retrospective(self, *a, **k):
            return "成长回溯"
        def _build_how_am_i_response(self, *a, **k):
            return "我很好"
        def _build_who_am_i_response(self, *a, **k):
            return "我是曈曈"
        def _get_current_emotion(self):
            return "平静"
        def _is_question_sentence(self, text):
            return bool("?" in text or "？" in text or
                        any(w in text for w in ("谁", "什么", "怎么", "吗", "几")))
    _s = _Stub()
    _qs = ["你好", "晚上好", "再见", "谢谢", "你是谁", "你能做什么", "你是什么",
           "你现在感觉怎么样", "你健康吗", "你最近怎么样", "我是谁", "你有多少个器官"]
    _n = sum(1 for _q in _qs if _s._rule_reason(_q))
    assert _n >= 8, f"断网态 ≥8 条本地可答，实际 {_n}"


# ─────────────────────── 493.9-4 organ_disambiguation 路由 ───────────────────────
def test_intent_tables_26_entries():
    from nucleus.qica.IntentChannels import (
        ALL_INTENTS, INTENT_TO_METHOD, INTENT_DEFAULT_PATHS, INTENT_TO_PATH)
    assert len(ALL_INTENTS) == 26
    assert len(INTENT_TO_METHOD) == 26
    assert len(INTENT_DEFAULT_PATHS) == 26
    assert INTENT_TO_METHOD["自我查询"] == "self_index"
    assert INTENT_DEFAULT_PATHS["自我查询"] == ["/自我"]
    assert "自我查询" in INTENT_TO_PATH


def test_qica_instance_table_synced():
    src = open("nucleus/qica/QICA.py", encoding="utf-8").read()
    assert '"自我查询": {"method": "self_index"' in src


def test_qica_classify_result_emits_organ_disambiguation():
    src = open("nucleus/qica/QICA.py", encoding="utf-8").read()
    assert '"organ_disambiguation": anchor.get("organ_disambiguation")' in src
    assert '"anaphora_resolved": anchor.get("anaphora_resolved")' in src


def test_organ_route_three_branches():
    from organs.brain.PulseCortex import PulseCortex
    _c = PulseCortex.__new__(PulseCortex)
    _fn = _ol.classify_query_type
    # 唯一命中 + 自我题 → self_index 本地答
    _r = _c._m4939_organ_route(
        {"organ_disambiguation": {"organ": "胃", "score": 1.0}}, "胃在哪", _fn)
    assert _r and _r["status"] == "self_index_local" and _ol.TAG_INDEX in _r["content"]
    # 多候选 → 澄清（不硬答）
    _r2 = _c._m4939_organ_route(
        {"organ_disambiguation": {"candidates": [{"organ": "叙事自我"},
                                                 {"organ": "自我认知"}]}}, "管记忆的", _fn)
    assert _r2 and _r2["status"] == "self_index_clarify"
    # 比较题不误路由（应走 deep）
    _r3 = _c._m4939_organ_route(
        {"organ_disambiguation": {"organ": "胃", "score": 1.0}}, "比较胃和肝", _fn)
    assert _r3 is None
    # None → 原链路
    assert _c._m4939_organ_route({}, "胃在哪", _fn) is None


# ─────────────────────── 493.9-5 read 动作生产口径 ───────────────────────
def test_descriptors_fixed():
    _acts = {a["action_id"]: a for a in ACTION_WHITELIST}
    assert _acts["self.count_organs"]["impl"]["target"] == "export_organs_runtime"
    assert _acts["event.subscribers"]["verify"]["key"] == "events"
    assert _acts["module.imports"]["verify"]["type"] == "is_dict"


def test_read_actions_real_results():
    """无运行态下可本地解的 6 条 read 全部 ok + verified.ok（真实返回）。"""
    for _a in ["self.count_organs", "organ.locate", "event.subscribers",
               "module.imports", "module.calls", "module.events"]:
        _r = _ae.execute_action(_a, {})
        assert _r.get("ok") is True and _r.get("blocked") is False, (_a, _r)
        assert (_r.get("verified") or {}).get("ok") is True, (_a, _r)
        assert _r.get("result"), (_a, "必须附真实返回")


def test_read_actions_infofield_after_assembly(monkeypatch):
    """生产装配后（get_info_field 单例就绪）两条 InfoField read 拿真实实例，不再 blocked。"""
    class _Fake:
        def get_organ_inflight(self, organ_name):
            return 3
        def get_backpressure_stats(self):
            return {"L2": {"queue_depth": 5, "limit": 100}}
    monkeypatch.setattr(_IF, "_THE_INFO_FIELD", _Fake(), raising=False)
    _r1 = _ae.execute_action("organ.inflight", {"organ_name": "胃"})
    _r2 = _ae.execute_action("layer.backpressure", {"layer": "L2"})
    assert _r1.get("ok") is True and _r1.get("result") == 3
    assert _r2.get("ok") is True and isinstance(_r2.get("result"), dict)


def test_read_fix_off_reverts(monkeypatch):
    """关刀5开关 → InfoField 兜底不生效，逐字回 493.8（诚实 blocked）。"""
    monkeypatch.setattr(_cfg, "ENABLE_M4939_READ_ACTION_FIX", False, raising=False)
    assert _ae.execute_action("organ.inflight", {"organ_name": "胃"}).get("blocked") is True


# ─────────────────────── 493.9-6 分层顺序与让位 ───────────────────────
def test_layered_L0_for_location():
    _r = _ol.offline_layered_answer("胃在哪")
    assert _r["layer"] == "L0" and _ol.TAG_INDEX in str(_r["answer"])


def test_layered_L1_for_identity():
    _r = _ol.offline_layered_answer("你的名字")
    assert _r["layer"] == "L1" and _ol.TAG_LOCAL in str(_r["answer"])


def test_layered_causal_yields_to_L3():
    """因果题全层不接 → 交 L3 明确拒答，不准 canned 假装在推理。"""
    _calls = []
    _r = _ol.offline_layered_answer("为什么L2会满", survival_fn=lambda q: _calls.append(q) or "x")
    assert _r["answer"] is None and _r["layer"] is None
    assert "causal→L3" in _r["yields"]
    assert _calls == [], "因果题不得进 L2"


def test_layered_L2_not_for_self_state():
    """L2 不处理自我/状态题（survival_fn 不得被调用）。"""
    _calls = []
    _r = _ol.offline_layered_answer("胃在哪", survival_fn=lambda q: _calls.append(q) or "x")
    # L0 已命中，L2 更不该跑
    assert _calls == []
    assert _r["layer"] in ("L0", "L1")


def test_layered_greeting_tagged():
    _r = _ol.offline_layered_answer("你好")
    assert _r["answer"] and _ol.TAG_LOCAL in str(_r["answer"])


# ─────────────────────── 493.9-7 上下文指代消解 ───────────────────────
def _mk_qica():
    from nucleus.qica.QICA import QICA
    _q = QICA.__new__(QICA)
    _q._context_lock = threading.Lock()
    _q._context_history = []
    return _q


def test_anaphora_binds_prev_organ():
    _q = _mk_qica()
    _q._context_history = [
        {"organ_disambiguation": {"organ": "胃", "score": 1.0},
         "raw_text": "胃在哪", "task_type": "自我查询"}]
    _a = _q._context_assist("它现在忙不忙", {"raw_input": "它现在忙不忙"})
    assert _a.get("anaphora_resolved") == {"organ": "胃", "from": "prev_turn"}


def test_anaphora_multi_candidate_clarify():
    _q = _mk_qica()
    _q._context_history = [
        {"organ_disambiguation": {"candidates": [{"organ": "叙事自我"},
                                                  {"organ": "自我认知"}]},
         "raw_text": "管记忆的", "task_type": "自我查询"}]
    _a = _q._context_assist("那个是什么", {"raw_input": "那个是什么"})
    assert _a.get("anaphora_resolved", {}).get("need_clarify") is True


def test_anaphora_no_guess():
    _q = _mk_qica()
    _q._context_history = [{"raw_text": "你好", "task_type": "情感问候"}]
    _a = _q._context_assist("它呢", {"raw_input": "它呢"})
    assert "anaphora_resolved" not in _a


def test_anaphora_off_reverts(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M4939_ANAPHORA_RESOLVE", False, raising=False)
    _q = _mk_qica()
    _q._context_history = [
        {"organ_disambiguation": {"organ": "胃"}, "raw_text": "胃在哪",
         "task_type": "自我查询"}]
    assert "anaphora_resolved" not in _q._context_assist(
        "它忙不忙", {"raw_input": "它忙不忙"})


# ─────────────────────── 493.9-8 开关终态 ───────────────────────
def test_switch_final_state():
    assert _cfg.ENABLE_M493_SELF_QUERY_FASTPATH is False      # 主笔仍关
    assert _cfg.ENABLE_M4938_SHADOW_LABEL is True            # 裁决打开
    assert _cfg.ENABLE_M4938_SAFETY_BOUNDARY is True
    assert _cfg.ENABLE_M4938_ALIAS_VETO is True
    for _s in ["ENABLE_M4938_LEARNED_ALIASES", "ENABLE_M4938_CANDIDATE_LEARNING",
               "ENABLE_M4938_COLD_START", "ENABLE_M4938_DIGEST_FUSION"]:
        assert getattr(_cfg, _s) is False, _s


def test_issue_trend_gate_never_exit2():
    """493.8 已修 sys.path，本批复跑退出码非 2。"""
    import subprocess
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _script = os.path.join(_root, "tools", "check_code_issue_trend_302.py")
    _env = dict(os.environ)
    _env["PYTHONPATH"] = _root
    _r = subprocess.run([sys.executable, _script, "--stamp"],
                        capture_output=True, text=True, cwd=_root, env=_env)
    assert _r.returncode != 2, _r.stderr
