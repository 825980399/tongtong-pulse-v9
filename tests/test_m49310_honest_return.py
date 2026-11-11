# -*- coding: utf-8 -*-
"""493.10 批刀6-8：返回契约诚实化 + 审计三字段 + 调用方看 verified。

纪律：总闸 ENABLE_M49310_HONEST_RETURN 关 → 逐字回 493.9 契约
（read/control 无条件 ok=True、审计无三字段）。
"""
import json
import os

import pytest

import config as _cfg
import nucleus.self_index.action_executor as _ae
import nucleus.self_index.fastpath as _fp


@pytest.fixture(autouse=True)
def _switch_on(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49310_HONEST_RETURN", True, raising=False)


# ───────────── 493.10-6 read 新契约 ─────────────
def test_read_verify_fail_forced(monkeypatch):
    """任务书：强制 _verify 返回 {"ok":False}，断言 ok=False 且 executed=True。"""
    monkeypatch.setattr(_ae, "_verify", lambda a, result: {"ok": False})
    _r = _ae.execute_action("self.count_organs", {})
    assert _r["ok"] is False
    assert _r["executed"] is True
    assert _r["blocked"] is False


def test_read_verify_ok(monkeypatch):
    monkeypatch.setattr(_ae, "_verify", lambda a, result: {"ok": True})
    _r = _ae.execute_action("self.count_organs", {})
    assert _r["ok"] is True and _r["executed"] is True


def test_read_switch_off_reverts(monkeypatch):
    """关总闸 → 493.9：验证失败仍 ok=True，且无 executed 字段。"""
    monkeypatch.setattr(_cfg, "ENABLE_M49310_HONEST_RETURN", False, raising=False)
    monkeypatch.setattr(_ae, "_verify", lambda a, result: {"ok": False})
    _r = _ae.execute_action("self.count_organs", {})
    assert _r["ok"] is True
    assert "executed" not in _r


# ───────────── 493.10-6 control 新契约 ─────────────
def test_control_not_consumed_forced(monkeypatch):
    """任务书：ok = consumed；emitted=True 只表示已发出，不准冒充已消费。"""
    from nucleus.pulse.PulseCore import PulseCore

    class _Fake:
        def emit(self, *a, **k):
            pass

    monkeypatch.setattr(PulseCore, "_instance", _Fake(), raising=False)
    monkeypatch.setattr(_ae, "_verify_control_delta",
                        lambda b, a, t: {"consumed": False, "delta_receives": 0})
    _r = _ae.execute_action("mouth_speak", {"content": "x"})
    assert _r["ok"] is False
    assert _r["emitted"] is True
    assert _r["emitted_target"] == "mouth.speak"


def test_control_consumed_ok(monkeypatch):
    from nucleus.pulse.PulseCore import PulseCore

    class _Fake:
        def emit(self, *a, **k):
            pass

    monkeypatch.setattr(PulseCore, "_instance", _Fake(), raising=False)
    monkeypatch.setattr(_ae, "_verify_control_delta",
                        lambda b, a, t: {"consumed": True, "delta_receives": 1})
    _r = _ae.execute_action("mouth_speak", {"content": "x"})
    assert _r["ok"] is True and _r["emitted"] is True


def test_control_switch_off_reverts(monkeypatch):
    from nucleus.pulse.PulseCore import PulseCore

    class _Fake:
        def emit(self, *a, **k):
            pass

    monkeypatch.setattr(_cfg, "ENABLE_M49310_HONEST_RETURN", False, raising=False)
    monkeypatch.setattr(PulseCore, "_instance", _Fake(), raising=False)
    monkeypatch.setattr(_ae, "_verify_control_delta",
                        lambda b, a, t: {"consumed": False})
    _r = _ae.execute_action("mouth_speak", {"content": "x"})
    assert _r["ok"] is True            # 493.9 旧契约
    assert "emitted_target" not in _r


# ───────────── 493.10-7 audit 三字段 ─────────────
def test_audit_three_fields(tmp_path, monkeypatch):
    _p = tmp_path / "action_audit.jsonl"
    monkeypatch.setattr(_ae, "_AUDIT_PATH", str(_p))
    _ae.execute_action("self.count_organs", {})
    _rows = [json.loads(x) for x in _p.read_text(encoding="utf-8").strip().splitlines()]
    assert _rows
    _last = _rows[-1]
    assert _last["pid"] == os.getpid()
    assert isinstance(_last["proc_gen"], (int, float)) and _last["proc_gen"] > 0
    assert isinstance(_last["assembly"], bool)


def test_audit_switch_off_no_three_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49310_HONEST_RETURN", False, raising=False)
    _p = tmp_path / "action_audit.jsonl"
    monkeypatch.setattr(_ae, "_AUDIT_PATH", str(_p))
    _ae.execute_action("self.count_organs", {})
    _last = json.loads(_p.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "pid" not in _last and "proc_gen" not in _last and "assembly" not in _last


# ───────────── 493.10-8 调用方看 verified ─────────────
def test_action_result_usable_rejects_verified_false():
    assert _fp._action_result_usable({"ok": True, "result": ["x"],
                                      "verified": {"ok": False}}) is False
    assert _fp._action_result_usable({"ok": True, "result": ["x"],
                                      "verified": {"ok": True}}) is True
    assert _fp._action_result_usable({"ok": False}) is False


def test_answer_repeat_ignores_bad_result(monkeypatch):
    """模拟验证失败但 ok=True（旧契约假通过）→ 不拿坏 result 拼答案。"""
    _bad = {"ok": True, "verified": {"ok": False},
            "result": [{"reply": "这是坏数据，不该被拼进答案"}]}

    def _fake(action_id, params=None, **kw):
        return _bad

    monkeypatch.setattr(_fp, "_action_result_usable",
                        _fp._action_result_usable)  # 用真实 helper
    import nucleus.self_index.action_executor as _mod
    monkeypatch.setattr(_mod, "execute_action", _fake)
    assert _fp._answer_repeat() is None
    assert "这是坏数据" not in str(_fp._answer_backpressure("L2 队列多深"))


def test_answer_repeat_with_good_result(monkeypatch):
    _good = {"ok": True, "verified": {"ok": True},
             "result": [{"reply": "上一句真实回复"}]}

    def _fake(action_id, params=None, **kw):
        return _good

    import nucleus.self_index.action_executor as _mod
    monkeypatch.setattr(_mod, "execute_action", _fake)
    assert "上一句真实回复" in _fp._answer_repeat()


def test_backpressure_uses_good_result(monkeypatch):
    _good = {"ok": True, "verified": {"ok": True},
             "result": {"L2": {"queue_depth": 7, "limit": 100, "ratio": 0.07}}}

    def _fake(action_id, params=None, **kw):
        return _good

    import nucleus.self_index.action_executor as _mod
    monkeypatch.setattr(_mod, "execute_action", _fake)
    assert "队列深度 7" in _fp._answer_backpressure("L2 队列多深")


def test_switch_final_state():
    """全局纪律：主笔仍 False；493.8 四个学习开关仍 False。"""
    assert _cfg.ENABLE_M493_SELF_QUERY_FASTPATH is False
    # 493.12：CANDIDATE_LEARNING 需为 True（dry-run 抽取依赖其返回 list；不写文件）
    assert _cfg.ENABLE_M4938_CANDIDATE_LEARNING is True
    for _s in ["ENABLE_M4938_LEARNED_ALIASES",
               "ENABLE_M4938_COLD_START", "ENABLE_M4938_DIGEST_FUSION"]:
        assert getattr(_cfg, _s) is False, _s
    # 493.12 四刀开关默认全开
    for _s in ["ENABLE_M49312_GUARD_LAYERED", "ENABLE_M49312_REL_EXPAND",
               "ENABLE_M49312_MODULE_EXACT", "ENABLE_M49312_DRYRUN_CANDIDATES"]:
        assert getattr(_cfg, _s) is True, _s
