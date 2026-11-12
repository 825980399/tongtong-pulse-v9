"""493.13-3/4/5 长程指代接线：get_last_organ + QICA 消费显式 context_organ。

纪律：仅验证「显式字段注入 → 指代绑定」，不放宽 history[-1] 语义；
      context_organ 必须带 user/session 隔离（不同 payload 不串味）。
"""
import time
import types

import pytest

from organs.brain.PulseInnerWorld import PulseInnerWorld
from nucleus.qica.QICA import QICA

_HAS_IW = True


@pytest.mark.skipif(not _HAS_IW, reason="PulseInnerWorld import 不可用")
def test_get_last_organ_returns_recent_organ():
    fake = types.SimpleNamespace(_conversation_memory=[
        {"user_name": "用户", "organ": "胃", "ts": time.time(),
         "organ_confidence": 0.9},
        {"user_name": "用户", "organ": None, "ts": time.time()},
    ])
    res = PulseInnerWorld.get_last_organ(fake, "用户")
    assert res["organ"] == "胃"
    assert res["confidence"] == 0.9


@pytest.mark.skipif(not _HAS_IW, reason="PulseInnerWorld import 不可用")
def test_get_last_organ_older_than_max_age_returns_none():
    old = time.time() - 90000  # > 86400
    fake = types.SimpleNamespace(_conversation_memory=[
        {"user_name": "用户", "organ": "胃", "ts": old},
    ])
    assert PulseInnerWorld.get_last_organ(fake, "用户", max_age_s=86400) is None


@pytest.mark.skipif(not _HAS_IW, reason="PulseInnerWorld import 不可用")
def test_get_last_organ_cross_user_isolated():
    fake = types.SimpleNamespace(_conversation_memory=[
        {"user_name": "A", "organ": "胃", "ts": time.time()},
    ])
    # 不同用户最近一次带 organ 记忆不存在 → None
    assert PulseInnerWorld.get_last_organ(fake, "B") is None


def test_qica_context_organ_binds_anaphora():
    # 493.13-5：context_organ=胃 + 「它呢」 → 显式绑定胃
    q = QICA()
    a = q.classify_internal({
        "content": "它呢", "user_name": "用户",
        "context_organ": "胃",
        "context_organ_age": 5,
        "context_organ_confidence": 0.9,
    })
    assert a.get("context_organ") == "胃"
    ar = a.get("anaphora_resolved")
    assert ar and ar["organ"] == "胃"
    assert ar["from"] == "context_organ_explicit"


def test_qica_no_context_organ_no_bind():
    # 无 context_organ 时「它呢」→ 不绑定（诚实说不知道指哪个）
    q = QICA()
    a = q.classify_internal({"content": "它呢", "user_name": "用户"})
    assert a.get("context_organ") is None
    assert a.get("anaphora_resolved") is None


def test_qica_context_organ_user_session_isolated():
    # 不同 payload 的 context_organ 不串味（A=胃, B=肺）
    q = QICA()
    a_a = q.classify_internal({
        "content": "它呢", "user_name": "A",
        "context_organ": "胃", "context_organ_age": 1,
    })
    a_b = q.classify_internal({
        "content": "它呢", "user_name": "B",
        "context_organ": "肺", "context_organ_age": 1,
    })
    assert a_a["anaphora_resolved"]["organ"] == "胃"
    assert a_b["anaphora_resolved"]["organ"] == "肺"


def test_qica_context_organ_switch_off_no_bind(monkeypatch):
    # 开关关 → 不注入/不绑定（逐字回 493.12）
    import config as _cfg
    monkeypatch.setattr(_cfg, "ENABLE_M49313_LONG_CONTEXT", False)
    q = QICA()
    a = q.classify_internal({
        "content": "它呢", "user_name": "用户",
        "context_organ": "胃", "context_organ_age": 1,
    })
    assert a.get("anaphora_resolved") is None
    monkeypatch.setattr(_cfg, "ENABLE_M49313_LONG_CONTEXT", True)


def test_patch_dedup_natural_key():
    # 493.13-6：去重键从 id 改为 (file, method, issue_type, 7天窗口)
    from nucleus.evolution.patch_dedup import find_pending_duplicates
    patches = [
        {"id": "k1", "file": "config.py", "method": "_watch_config_file",
         "issue_type": "dead_code", "generated_at": 100.0},
        {"id": "d1", "file": "config.py", "method": "_watch_config_file",
         "issue_type": "dead_code", "generated_at": 200.0},
        {"id": "d2", "file": "config.py", "method": "_watch_config_file",
         "issue_type": "dead_code", "generated_at": 300.0},
        {"id": "other", "file": "x.py", "method": "_m", "issue_type": "t",
         "generated_at": 150.0},
    ]
    dups = find_pending_duplicates(patches, window_days=7)
    assert dups == {"d1": "k1", "d2": "k1"}, dups
    # 不同 file/method/issue 的不算重复
    assert "other" not in dups
