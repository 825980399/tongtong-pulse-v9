# -*- coding: utf-8 -*-
"""493.11 刀1-2：模块位置问法 + QICA 会话分桶（A/B 交叉指代、LRU/TTL）。

纪律：ENABLE_M49311_SESSION_ISOLATION 默认 False/影子；关 → 逐字回 493.10 全局 list。
"""
import threading
import time
from collections import OrderedDict

import pytest

import config as _cfg
import nucleus.qica.QICA as _qmod
from nucleus.qica.QICA import QICA
from nucleus.self_index.fastpath import self_query_fastpath_explain


# ───────────── 刀1：模块位置不被器官化 ─────────────
@pytest.mark.parametrize("q,expect", [
    ("PulseCortex 和 PulseLung 怎么连", "module.relation"),
    ("pulse_inner_world_support 和 InfoField 怎么连", "module.relation"),
    ("PulseCortex 在哪", "module.location"),
    ("InfoField 在哪", "module.location"),
    ("大脑皮层 和 肺 怎么连", "module.relation"),
    ("胃和肝怎么连", "module.relation"),
])
def test_six_regression_cases(q, expect):
    _ex = self_query_fastpath_explain(q)
    assert _ex.get("action_id") == expect, (q, _ex.get("action_id"))
    assert _ex.get("answer"), q
    if expect == "module.location":
        assert "模块在代码路径" in str(_ex["answer"]), q
        assert "大脑皮层" not in str(_ex["answer"]), q
    else:
        assert "大脑皮层 在代码路径" not in str(_ex["answer"]), q


def test_module_location_switch_off_reverts(monkeypatch):
    """关 493.10 总闸 → 逐字回 493.9：PulseCortex 在哪 又变回器官描述。"""
    monkeypatch.setattr(_cfg, "ENABLE_M49310_MODULE_GRAPH_CONSUME", False, raising=False)
    _ex = self_query_fastpath_explain("PulseCortex 在哪")
    assert _ex.get("action_id") == "organ.location"
    assert "大脑皮层" in str(_ex.get("answer"))


# ───────────── 刀2：会话分桶 ─────────────
def _mk_qica(session_on=True):
    monkeypatch_value = session_on
    _q = QICA.__new__(QICA)
    _q._context_lock = threading.Lock()
    _q._context_history = []          # legacy 全局桶（快照/关态）
    _q._session_histories = OrderedDict()
    _q._max_context_history = 5
    _q._m49311_max_sessions = 256
    _q._m49311_ttl = 1800
    _q._classify_count = 0
    return _q, monkeypatch_value


def _turn(q, sid, text, organ=None):
    """模拟一轮 classify_internal 的上下文段（最小 anchor）。"""
    _a = {"raw_input": text, "task_type": "自我查询", "entities": [],
          "channel": "fast", "sentence_type": "question"}
    if organ:
        _a["organ_disambiguation"] = {"organ": organ, "score": 1.0}
    _a = q._context_assist(text, _a, q._m49311_session_id(
        {"session_id": sid, "user_name": sid}))
    _a["intent_vector"] = {"intent_semantic_sig": [0, 1, 0, 0]}
    q._record_context(_a, _a.get("session_id"))
    return _a.get("anaphora_resolved")


def test_ab_cross_anaphora(monkeypatch):
    """任务书 P0：A『胃在哪』→ B『它呢』不得绑胃；A 第二个『它呢』仍绑胃；
    B『肝在哪』后 A『它呢』不得变肝。"""
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SESSION_ISOLATION", True, raising=False)
    monkeypatch.setattr(_cfg, "ENABLE_M4939_OFFLINE_LOCAL", True, raising=False)
    monkeypatch.setattr(_cfg, "ENABLE_M4939_ANAPHORA_RESOLVE", True, raising=False)
    q, _ = _mk_qica()
    _turn(q, "A", "胃在哪", "胃")
    assert _turn(q, "B", "它呢") is None, "B 不得绑到 A 的胃"
    assert _turn(q, "A", "它呢") == {"organ": "胃", "from": "prev_turn"}
    _turn(q, "B", "肝在哪", "肝")
    assert _turn(q, "A", "它呢") == {"organ": "胃", "from": "prev_turn"}, \
        "A 不得被 B 的肝污染"
    assert _turn(q, "B", "它呢") == {"organ": "肝", "from": "prev_turn"}


def test_session_buckets_separate(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SESSION_ISOLATION", True, raising=False)
    q, _ = _mk_qica()
    _turn(q, "A", "胃在哪", "胃")
    _turn(q, "B", "肝在哪", "肝")
    assert set(q._session_histories.keys()) == {"A", "B"}
    assert len(q._session_histories["A"][0]) == 1
    assert len(q._session_histories["B"][0]) == 1
    assert q._context_history == [], "legacy 全局桶在开态不得被写入"


def test_global_bucket_not_fallback(monkeypatch):
    """__global__ 桶只服务无 session 旧调用，不得被其他会话兜底取用。"""
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SESSION_ISOLATION", True, raising=False)
    monkeypatch.setattr(_cfg, "ENABLE_M4939_OFFLINE_LOCAL", True, raising=False)
    monkeypatch.setattr(_cfg, "ENABLE_M4939_ANAPHORA_RESOLVE", True, raising=False)
    q, _ = _mk_qica()
    _turn(q, "__global__", "胃在哪", "胃")
    assert _turn(q, "X", "它呢") is None
    assert _turn(q, "__global__", "它呢") == {"organ": "胃", "from": "prev_turn"}


def test_session_key_stable():
    """稳定 key：session_id > user_id > user_name > __global__。"""
    assert QICA._m49311_session_id({"session_id": "S", "user_name": "U"}) == "S"
    assert QICA._m49311_session_id({"user_id": "U2", "user_name": "U"}) == "U2"
    assert QICA._m49311_session_id({"user_name": "小林"}) == "小林"
    assert QICA._m49311_session_id({}) == "__global__"
    assert QICA._m49311_session_id({"user_name": "用户"}) == "__global__"


def test_lru_eviction(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SESSION_ISOLATION", True, raising=False)
    q, _ = _mk_qica()
    q._m49311_max_sessions = 3
    for _s in ["S1", "S2", "S3", "S4"]:
        _turn(q, _s, "胃在哪", "胃")
    assert list(q._session_histories.keys()) == ["S2", "S3", "S4"]


def test_ttl_cleanup(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SESSION_ISOLATION", True, raising=False)
    q, _ = _mk_qica()
    q._m49311_ttl = 0
    _turn(q, "OLD", "胃在哪", "胃")
    time.sleep(0.01)
    _turn(q, "NEW", "肝在哪", "肝")
    assert list(q._session_histories.keys()) == ["NEW"]


def test_single_session_maxlen(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SESSION_ISOLATION", True, raising=False)
    q, _ = _mk_qica()
    for _i in range(9):
        _turn(q, "A", f"第{_i}轮")
    assert len(q._session_histories["A"][0]) == 5


def test_session_off_reverts_to_global(monkeypatch):
    """关开关 → 逐字回 493.10：全局 list，A 的历史会串给 B。"""
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SESSION_ISOLATION", False, raising=False)
    monkeypatch.setattr(_cfg, "ENABLE_M4939_OFFLINE_LOCAL", True, raising=False)
    monkeypatch.setattr(_cfg, "ENABLE_M4939_ANAPHORA_RESOLVE", True, raising=False)
    q, _ = _mk_qica(session_on=False)
    _turn(q, "A", "胃在哪", "胃")
    assert _turn(q, "B", "它呢") == {"organ": "胃", "from": "prev_turn"}
    assert len(q._context_history) == 2
    assert len(q._session_histories) == 0


def test_snapshot_roundtrip(monkeypatch):
    """快照恢复分桶（开关开时）。"""
    monkeypatch.setattr(_cfg, "ENABLE_M49311_SESSION_ISOLATION", True, raising=False)
    q, _ = _mk_qica()
    _turn(q, "A", "胃在哪", "胃")
    # 只构造快照所需最小状态
    q._rule_hits = {}
    q._domain_knowledge_lock = threading.Lock()
    q._domain_knowledge = {}
    _snap = {"context_history": [], "session_histories": {
        "A": {"history": [dict(x) for x in q._session_histories["A"][0]],
              "ts": time.time()}}}
    q2, _ = _mk_qica()
    q2._rule_hits = {}
    q2._domain_knowledge_lock = threading.Lock()
    q2._domain_knowledge = {}
    q2.load_state_snapshot(_snap)
    assert "A" in q2._session_histories
    assert len(q2._session_histories["A"][0]) == 1


def test_switch_final_state():
    """全局纪律：会话隔离默认影子关；主笔与 4938 四开关仍关。"""
    assert _cfg.ENABLE_M49311_SESSION_ISOLATION is False
    assert _cfg.ENABLE_M493_SELF_QUERY_FASTPATH is False
    for _s in ["ENABLE_M4938_LEARNED_ALIASES",
               "ENABLE_M4938_CANDIDATE_LEARNING",
               "ENABLE_M4938_COLD_START",
               "ENABLE_M4938_DIGEST_FUSION"]:
        assert getattr(_cfg, _s) is False, _s
