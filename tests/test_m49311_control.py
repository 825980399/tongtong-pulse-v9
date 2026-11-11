# -*- coding: utf-8 -*-
"""493.11 刀3-4：control 按 impl.type 三路分流 + 审计三字段复测。

纪律：不放宽 delta_receives；digest/mouth 活体有真消费者，旁路 consumed=False 属假阴性不修。
"""
import json

import pytest

import config as _cfg
import nucleus.self_index.action_executor as _ae
from nucleus.pulse.PulseCore import PulseCore


@pytest.fixture(autouse=True)
def _typed_on(monkeypatch):
    monkeypatch.setattr(_cfg, "ENABLE_M49311_CONTROL_TYPED", True, raising=False)
    monkeypatch.setattr(_cfg, "ENABLE_M49310_HONEST_RETURN", True, raising=False)


class _FakeCore:
    def __init__(self):
        self.events = []

    def emit(self, *a, **k):
        self.events.append(a[1] if len(a) > 1 else None)


# ───────────── 刀3 三路分流 ─────────────
def test_emit_controls_still_emit(monkeypatch):
    _pc = _FakeCore()
    monkeypatch.setattr(PulseCore, "_instance", _pc, raising=False)
    for _aid, _p in [("digest_submit", {"payload": "x"}),
                     ("mouth_speak", {"content": "x"})]:
        _r = _ae.execute_action(_aid, _p)
        assert _r.get("impl_type") == "emit"
        assert _r.get("emitted") is True
        assert _r.get("executed") is True
        assert _r.get("ok") == bool((_r.get("verified") or {}).get("consumed"))
    assert len(_pc.events) == 2


def test_config_no_pseudo_event(monkeypatch, tmp_path):
    """channel_rotate 绝不 emit 伪事件 channel.rotate。"""
    _pc = _FakeCore()
    monkeypatch.setattr(PulseCore, "_instance", _pc, raising=False)
    monkeypatch.setattr(_ae, "_AUDIT_PATH", str(tmp_path / "a.jsonl"))
    _r = _ae.execute_action("channel_rotate", {"to_channel": "local"})
    assert _r.get("impl_type") == "config"
    assert _r.get("emitted") is False
    assert _r.get("emitted_target") is None
    assert _pc.events == []
    assert _r.get("verified", {}).get("post_state_ok") is False  # 无头无实例，诚实
    _rows = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text(
        encoding="utf-8").strip().splitlines()]
    assert all("channel.rotate" not in json.dumps(x, ensure_ascii=False) for x in _rows)


def test_method_no_pseudo_event(monkeypatch, tmp_path):
    """organ_restart 绝不 emit 伪事件；restart_organ 不存在 → 诚实失败。"""
    _pc = _FakeCore()
    monkeypatch.setattr(PulseCore, "_instance", _pc, raising=False)
    monkeypatch.setattr(_ae, "_AUDIT_PATH", str(tmp_path / "a.jsonl"))
    _r = _ae.execute_action("organ_restart", {"organ": "胃"})
    assert _r.get("impl_type") == "method"
    assert _r.get("emitted") is False
    assert _r.get("ok") is False
    assert "方法不存在" in str(_r.get("reason"))
    assert _pc.events == []
    assert _r.get("verified", {}).get("method_exists") is False
    _rows = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text(
        encoding="utf-8").strip().splitlines()]
    assert all("PulseSystemManager.restart_organ" not in json.dumps(x, ensure_ascii=False)
               for x in _rows)


def test_typed_off_reverts_to_all_emit(monkeypatch):
    """关总闸 → 逐字回 493.10：4 个 control 全走 emit。"""
    monkeypatch.setattr(_cfg, "ENABLE_M49311_CONTROL_TYPED", False, raising=False)
    _pc = _FakeCore()
    monkeypatch.setattr(PulseCore, "_instance", _pc, raising=False)
    for _aid, _p in [("digest_submit", {"payload": "x"}),
                     ("mouth_speak", {"content": "x"}),
                     ("channel_rotate", {"to_channel": "local"}),
                     ("organ_restart", {"organ": "胃"})]:
        _r = _ae.execute_action(_aid, _p)
        assert "impl_type" not in _r, _aid
        assert _r.get("emitted") is True, _aid
    assert len(_pc.events) == 4


def test_return_contract_uniform(monkeypatch):
    """三路返回统一含 ok/executed/emitted/verified/impl_type。"""
    _pc = _FakeCore()
    monkeypatch.setattr(PulseCore, "_instance", _pc, raising=False)
    for _aid, _p in [("digest_submit", {"payload": "x"}),
                     ("channel_rotate", {"to_channel": "local"}),
                     ("organ_restart", {"organ": "胃"})]:
        _r = _ae.execute_action(_aid, _p)
        for _k in ("ok", "executed", "emitted", "verified", "impl_type"):
            assert _k in _r, (_aid, _k)


def test_emit_consumed_true_when_real_consumer(monkeypatch):
    """模拟活体真消费者（delta_receives=1）→ digest_submit ok=True/consumed=True。"""
    _pc = _FakeCore()
    monkeypatch.setattr(PulseCore, "_instance", _pc, raising=False)
    monkeypatch.setattr(_ae, "_verify_control_delta",
                        lambda b, a, t: {"consumed": True, "delta_receives": 1})
    _r = _ae.execute_action("digest_submit", {"payload": "x"})
    assert _r.get("ok") is True
    assert _r["verified"]["consumed"] is True


def test_emit_consumed_false_sidepath(monkeypatch):
    """旁路测试 delta_receives=0 → 诚实 consumed=False，不冒充生产成功。"""
    _pc = _FakeCore()
    monkeypatch.setattr(PulseCore, "_instance", _pc, raising=False)
    monkeypatch.setattr(_ae, "_verify_control_delta",
                        lambda b, a, t: {"consumed": False, "delta_receives": 0})
    _r = _ae.execute_action("digest_submit", {"payload": "x"})
    assert _r.get("ok") is False
    assert _r.get("emitted") is True          # 事件确实发了
    assert _r["verified"]["consumed"] is False


# ───────────── 刀4 审计三字段 ─────────────
def test_audit_three_fields_all_controls(monkeypatch, tmp_path):
    import os
    monkeypatch.setattr(_ae, "_AUDIT_PATH", str(tmp_path / "a.jsonl"))
    _pc = _FakeCore()
    monkeypatch.setattr(PulseCore, "_instance", _pc, raising=False)
    for _aid, _p in [("digest_submit", {"payload": "x"}),
                     ("mouth_speak", {"content": "x"}),
                     ("channel_rotate", {"to_channel": "local"}),
                     ("organ_restart", {"organ": "胃"})]:
        _ae.execute_action(_aid, _p)
    _rows = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text(
        encoding="utf-8").strip().splitlines()]
    assert len(_rows) == 4
    for _x in _rows:
        assert _x["pid"] == os.getpid()
        assert isinstance(_x["proc_gen"], (int, float)) and _x["proc_gen"] > 0
        assert isinstance(_x["assembly"], bool)
        assert _x["assembly"] is True       # PulseCore._instance 已装配
    assert {x["action_id"] for x in _rows} == {
        "digest_submit", "mouth_speak", "channel_rotate", "organ_restart"}


def test_audit_distinguishes_sidepath(monkeypatch, tmp_path):
    """assembly=False（无 PulseCore）的旁路行可与生产行区分。"""
    monkeypatch.setattr(PulseCore, "_instance", None, raising=False)
    monkeypatch.setattr(_ae, "_AUDIT_PATH", str(tmp_path / "a.jsonl"))
    _ae.execute_action("channel_rotate", {"to_channel": "local"})
    _x = json.loads((tmp_path / "a.jsonl").read_text(encoding="utf-8").strip())
    assert _x["assembly"] is False
