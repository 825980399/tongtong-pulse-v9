# -*- coding: utf-8 -*-
"""491-1 / 491-2 / 491-3 / 491-4 / 491-7 渠道侧单测（每刀 on/off）。"""
import datetime
import io
import urllib.error
from unittest import mock

import pytest

import config
from nucleus.llm import channel_health as CH
from nucleus.llm import channel_smart_rotate as rot
from nucleus.llm import channel_state_store as st
from nucleus.reasoning import channel_client as cc
from organs.body.PulseLung import PulseLung

_SWITCHES = (
    "ENABLE_M491_STRUCTURED_HTTP_ERROR",
    "ENABLE_M491_UNIFIED_HEALTH_TRACKER",
    "ENABLE_M491_CHANNEL_STATE_PERSIST",
    "ENABLE_M491_MONTHLY_REVIVE",
    "ENABLE_M491_SMART_CHANNEL_ROTATE",
    "ENABLE_M491_DEADLOCK_DETECT",
)


@pytest.fixture
def cfg():
    _saved = {k: getattr(config, k, None) for k in _SWITCHES}
    yield config
    for k, v in _saved.items():
        if v is None:
            if hasattr(config, k):
                delattr(config, k)
        else:
            setattr(config, k, v)
    st.reset_for_tests()
    rot.reset_for_tests()
    CH.reset_channel_health()


class _HTTPError(urllib.error.HTTPError):
    def __init__(self, code):
        super().__init__("u", code, "m", {},
                         io.BytesIO(b'{"error":"insufficient_quota"}'))


def _call(code):
    _ch = {"name": "c", "model": "m", "api_url": "https://x", "api_key": "k"}
    with mock.patch("nucleus.ssrf_guard.is_safe_http_url",
                    return_value=(True, "")), \
         mock.patch.object(cc.urllib.request, "urlopen",
                           side_effect=_HTTPError(code)):
        return cc.call_channel(_ch, "p")


# ---------------------------------------------------------------- 491-1
def test_4911_on_structured_403(cfg):
    cfg.ENABLE_M491_STRUCTURED_HTTP_ERROR = True
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = False
    _r = _call(403)
    assert _r["status_code"] == 403
    assert _r["error_type"] == "quota_exhausted"
    assert "insufficient_quota" in _r["body_head"]


def test_4911_429_not_quota_exhausted(cfg):
    """429 是临时限流，绝不能误标额度耗尽。"""
    cfg.ENABLE_M491_STRUCTURED_HTTP_ERROR = True
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = False
    assert _call(429)["error_type"] == "rate_limited"


def test_4911_off_verbatim(cfg):
    cfg.ENABLE_M491_STRUCTURED_HTTP_ERROR = False
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = False
    _r = _call(403)
    assert _r["error"] == "HTTP 403"
    assert "status_code" not in _r
    assert "error_type" not in _r
    assert "body_head" not in _r


# ---------------------------------------------------------------- 491-2
def _bare_lung():
    _l = PulseLung.__new__(PulseLung)
    _l._log = lambda *a, **k: None
    _l._channel_health = None
    return _l


def test_4912_unified_singleton(cfg):
    cfg.ENABLE_M491_UNIFIED_HEALTH_TRACKER = True
    CH.reset_channel_health()
    assert _bare_lung()._get_channel_health() is CH.get_channel_health()


def test_4912_off_separate_instance(cfg):
    cfg.ENABLE_M491_UNIFIED_HEALTH_TRACKER = False
    CH.reset_channel_health()
    assert _bare_lung()._get_channel_health() is not CH.get_channel_health()


def test_4912_threshold_unchanged(cfg):
    """统一后 3 次失败 / 300s 熔断阈值逐字不变。"""
    cfg.ENABLE_M491_UNIFIED_HEALTH_TRACKER = True
    CH.reset_channel_health()
    _h = _bare_lung()._get_channel_health()
    assert _h.circuit_break_threshold == 3
    assert _h.circuit_break_seconds == 300.0
    for _ in range(3):
        _h.record("x", False)
    assert _h.is_available("x") is False


# ---------------------------------------------------------------- 491-3
def test_4913_three_403_exhausted_and_filter(cfg):
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = True
    st.reset_for_tests()
    for _ in range(3):
        st.mark_http_error("chA", 403, "quota_exhausted")
    assert st.state_of("chA") == "exhausted"
    assert [c["name"] for c in st.filter_candidates(
        [{"name": "chA"}, {"name": "ok"}])] == ["ok"]


def test_4913_429_and_auth_separate_states(cfg):
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = True
    st.reset_for_tests()
    st.mark_http_error("r", 429, "rate_limited")
    st.mark_http_error("a", 401, "auth_fail")
    assert st.state_of("r") == "rate_limited"
    assert st.state_of("a") == "auth_failed"


def test_4913_off_verbatim(cfg):
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = False
    st.reset_for_tests()
    _rows = [{"name": "chA"}, {"name": "ok"}]
    assert st.filter_candidates(_rows) is _rows
    assert st.mark_http_error("chA", 403, "quota_exhausted") is None
    assert st.snapshot() == {}


# ---------------------------------------------------------------- 491-4
def test_4914_revive_and_idempotent(cfg):
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = True
    cfg.ENABLE_M491_MONTHLY_REVIVE = True
    st.reset_for_tests()
    for _ in range(3):
        st.mark_http_error("m", 403, "quota_exhausted")
    _chs = [{"name": "m", "quota_type": "monthly_free"}]
    _r = st.tick_monthly_revive(now=datetime.datetime(2026, 11, 15),
                                probe=lambda ch: True, channels=_chs)
    assert _r["revived"] == ["m"]
    assert st.state_of("m") == "ok"
    _r2 = st.tick_monthly_revive(now=datetime.datetime(2026, 11, 15),
                                 probe=lambda ch: True, channels=_chs)
    assert _r2["ran"] is False


def test_4914_three_months_fail_marks_dead(cfg):
    cfg.ENABLE_M491_CHANNEL_STATE_PERSIST = True
    cfg.ENABLE_M491_MONTHLY_REVIVE = True
    st.reset_for_tests()
    for _ in range(3):
        st.mark_http_error("m", 403, "quota_exhausted")
    _chs = [{"name": "m", "quota_type": "monthly_free"}]
    for _m in (1, 2, 3):
        st.tick_monthly_revive(now=datetime.datetime(2027, _m, 15),
                               probe=lambda ch: False, channels=_chs)
    assert st.state_of("m") == "dead"


def test_4914_off_verbatim(cfg):
    cfg.ENABLE_M491_MONTHLY_REVIVE = False
    assert st.tick_monthly_revive(now=datetime.datetime(2026, 11, 15),
                                  probe=lambda ch: True) is None


# ---------------------------------------------------------------- 491-7
def test_4917_on_orders_by_recent_success(cfg):
    cfg.ENABLE_M491_SMART_CHANNEL_ROTATE = True
    rot.reset_for_tests()
    for _ in range(200):
        rot.record("good", True)
    for _ in range(200):
        rot.record("bad", False)
    _rows = [{"name": "bad", "priority": 1}, {"name": "good", "priority": 9}]
    assert [c["name"] for c in rot.order(_rows)] == ["good", "bad"]


def test_4917_off_verbatim(cfg):
    cfg.ENABLE_M491_SMART_CHANNEL_ROTATE = False
    _rows = [{"name": "bad", "priority": 1}, {"name": "good", "priority": 9}]
    assert rot.order(_rows) is _rows
    assert rot.recent_success_rate("x") == 1.0
