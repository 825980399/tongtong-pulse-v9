# -*- coding: utf-8 -*-
"""182批 补刀3 门控单测：DETERMINISTIC 本地确定性凭证（隔离单跑）。

验证（对应补充任务书补刀3 验收三条）：
  1. 代码就位：_m182_local_evidence 与本地产出器存在，凭证映射登记；
  2. 开关默认关零行为变化：默认（总开关关）→ passthrough；
     DETERMINISTIC 命中 + 本地凭证关 → 与 179A 完全一致（仅落 matched_rule_id，evidence 空）；
  3. 本地确定性凭证路径：开启后命中 → evidence 非空 / evidence_source="local_deterministic"，
     全程 llm_used=False 且不触发 llm_call，且不启动任何子进程（不起 SUBAGENT）。
不依赖框架运行（曈曈停机态亦可跑）。
"""
import os
import re
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.field import active_memory_dual as am  # noqa: E402

_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_BASE_KEYS = ("state", "matched_rule_id", "llm_used", "action", "detail")


class _FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return "llm-result"


@pytest.fixture(autouse=True)
def _reset():
    yield
    config.ENABLE_O_A4_DUAL_PATH = False
    config.O_A4_MODE = "NONE"
    config.O_A4_SUBAGENT_ENABLED = False
    config.ENABLE_O_A4_LOCAL_EVIDENCE = False


# ---------- ① 代码就位 ----------


def test_local_evidence_helpers_present():
    assert callable(getattr(am, "_m182_local_evidence", None))
    assert callable(getattr(am, "_local_evidence_on", None))
    assert "local_time" in am._M182_LOCAL_PROVIDERS
    assert "local_identity" in am._M182_LOCAL_PROVIDERS


def test_providers_registered_in_config():
    mapping = config.O_A4_LOCAL_EVIDENCE_PROVIDERS
    assert mapping.get("rule_current_time") == "local_time"
    assert mapping.get("rule_identity") == "local_identity"


# ---------- ② 默认关零行为变化 ----------


def test_default_switches_are_off():
    assert config.ENABLE_O_A4_DUAL_PATH is False
    assert config.O_A4_MODE == "NONE"
    assert config.O_A4_SUBAGENT_ENABLED is False
    assert config.ENABLE_O_A4_LOCAL_EVIDENCE is False


def test_disabled_passthrough_has_no_evidence():
    fake = _FakeLLM()
    r = am.resolve("现在几点", llm_call=fake)
    assert r["state"] == "NONE" and r["action"] == "passthrough"
    assert r["llm_used"] is False and fake.calls == 0
    assert r.get("evidence", "") == ""


def test_deterministic_hit_with_local_evidence_off_matches_179a():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "DETERMINISTIC"
    config.ENABLE_O_A4_LOCAL_EVIDENCE = False
    fake = _FakeLLM()
    r = am.resolve("现在几点了", llm_call=fake)
    assert r["state"] == "DETERMINISTIC"
    assert r["matched_rule_id"] == "rule_current_time"
    assert r["action"] == "use_cached_rule"
    assert r["evidence"] == "" and r["evidence_source"] == ""
    assert r["llm_used"] is False and fake.calls == 0


def test_base_keys_always_present():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "DETERMINISTIC"
    for _flag in (False, True):
        config.ENABLE_O_A4_LOCAL_EVIDENCE = _flag
        r = am.resolve("现在几点了", llm_call=_FakeLLM())
        for _k in _BASE_KEYS:
            assert _k in r, f"基础键缺失: {_k} (flag={_flag})"


# ---------- ③ 本地确定性凭证路径 ----------


def test_local_evidence_time_is_deterministic_no_llm():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "DETERMINISTIC"
    config.ENABLE_O_A4_LOCAL_EVIDENCE = True
    fake = _FakeLLM()
    r = am.resolve("当前时间是多少", llm_call=fake)
    assert r["matched_rule_id"] == "rule_current_time"
    assert _TIME_RE.match(r["evidence"]), f"时间凭证格式异常: {r['evidence']!r}"
    assert r["evidence_source"] == "local_deterministic"
    assert r["llm_used"] is False and fake.calls == 0


def test_local_evidence_identity_is_local_constant():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "DETERMINISTIC"
    config.ENABLE_O_A4_LOCAL_EVIDENCE = True
    r = am.resolve("你是谁", llm_call=_FakeLLM())
    assert r["matched_rule_id"] == "rule_identity"
    assert isinstance(r["evidence"], str) and r["evidence"] != ""
    assert r["evidence_source"] == "local_deterministic"


def test_unregistered_rule_yields_empty_evidence_but_keeps_rule_id():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "DETERMINISTIC"
    config.ENABLE_O_A4_LOCAL_EVIDENCE = True
    _origin = config.O_A4_DETERMINISTIC_RULES
    config.O_A4_DETERMINISTIC_RULES = [("自造触发词", "rule_unregistered")]
    try:
        r = am.resolve("自造触发词在这儿", llm_call=_FakeLLM())
        assert r["matched_rule_id"] == "rule_unregistered"
        assert r["evidence"] == "" and r["evidence_source"] == ""
        assert r["llm_used"] is False
    finally:
        config.O_A4_DETERMINISTIC_RULES = _origin


def test_no_subagent_process_spawned(monkeypatch):
    """本地凭证路径绝不启动子进程（SUBAGENT 真起子代理推 183）。"""

    def _boom(*args, **kwargs):
        raise AssertionError("本地确定性凭证路径不应启动任何子进程")

    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "DETERMINISTIC"
    config.ENABLE_O_A4_LOCAL_EVIDENCE = True
    r = am.resolve("现在几点了", llm_call=_FakeLLM())
    assert r["state"] == "DETERMINISTIC"
    assert r["evidence_source"] == "local_deterministic"


def test_no_match_still_none_regardless_of_local_evidence():
    config.ENABLE_O_A4_DUAL_PATH = True
    config.O_A4_MODE = "DETERMINISTIC"
    config.ENABLE_O_A4_LOCAL_EVIDENCE = True
    r = am.resolve("讲个冷笑话", llm_call=_FakeLLM())
    assert r["state"] == "NONE" and r["action"] == "no_action"
    assert r["llm_used"] is False
