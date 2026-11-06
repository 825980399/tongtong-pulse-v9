# -*- coding: utf-8 -*-
"""第465批：LLM 修复通道「可观测 + 同轮熔断 + 限流换道」· 单测

刀1 `safe_http_json` 保留 HTTPError 响应体（旧行为由 URLError 宽分支吞掉）
刀2 失败日志带正文 + 限流分类纯函数 `_m465_is_rate_limited`
刀3 同轮熔断早停
刀4 渠道健康度回写接线 + 限流换道重试一次

纪律
----
* 铁律 30：断言基准一律来自**另一个入口**（真实 HTTPError / 真实配置），
  不拿被测函数的输出当基准。
* 铁律 39：用例里的字面量全部取自 465 探针实测的真实响应正文。
* 不写生产台账：`paid_api_trace.begin` 打成空串，`is_safe_http_url` 打桩。
"""
from __future__ import annotations

import importlib
import io
import os
import types
import urllib.error
import urllib.request

import pytest

import config

# ★铁律60：包里同名导出会盖掉子模块，取模块一律 importlib。
SSRF = importlib.import_module("nucleus.ssrf_guard")
SEE = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
QM = importlib.import_module("nucleus.llm.ChannelQuotaMonitor")
PAT = importlib.import_module("nucleus.llm.paid_api_trace")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

#: 465 探针实测原文（智谱 429）
BODY_429 = '{"error":{"code":"1305","message":"该模型当前访问量过大，请您稍后再试"}}'
#: 465 探针实测原文（阿里云百炼 403，免费额度耗尽）
BODY_403 = ('{"error":{"message":"Free quota exhausted.","type":"AllocationQuota.FreeTierOnly",'
            '"code":"AllocationQuota.FreeTierOnly"}}')


# ==================== 工具 ====================

def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    import email.message
    return urllib.error.HTTPError(_URL, code, "err", email.message.Message(),
                                  io.BytesIO(body.encode("utf-8")))


def _patch_urlopen(monkeypatch, exc: Exception) -> None:
    """把 ssrf_guard 里的 urllib 换成只抛异常的替身（保留 Request 与 error 命名空间）。"""
    def _raise(*_a, **_k):
        raise exc

    fake = types.SimpleNamespace(
        request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=_raise),
        error=urllib.error,
    )
    monkeypatch.setattr(SSRF, "urllib", fake)
    monkeypatch.setattr(SSRF, "is_safe_http_url", lambda _u: (True, ""))
    # 不写生产埋点文件
    monkeypatch.setattr(PAT, "begin", lambda *_a, **_k: "")


def _mk():
    """最小实例：不跑 __init__（不做任何落盘/注册）。"""
    return SEE.SafeEvolutionExecutor.__new__(SEE.SafeEvolutionExecutor)


class _QMStub:
    def apply_to_channels(self, channels):
        return list(channels)


def _as_list(exclude):
    """exclude 允许是单个名字或名单（生产端已归一，这里的桩按同一契约取值）。"""
    if isinstance(exclude, str):
        return [exclude] if exclude else []
    return list(exclude or [])


# ==================== 刀1：HTTPError 正文 ====================

def test_http_error_body_is_returned(monkeypatch):
    """旧代码：HTTPError 落到 URLError 宽分支 ⇒ 只剩 `请求失败: HTTP Error 429`。"""
    _patch_urlopen(monkeypatch, _http_error(429, BODY_429))
    ok, data = SSRF.safe_http_json(_URL, method="POST", data=b"{}", timeout=5)
    assert ok is False
    assert isinstance(data, str)
    assert data.startswith("HTTP 429: "), data
    assert "1305" in data, data
    assert "该模型当前访问量过大" in data, data


def test_http_error_403_body_kept(monkeypatch):
    _patch_urlopen(monkeypatch, _http_error(403, BODY_403))
    ok, data = SSRF.safe_http_json(_URL, method="POST", data=b"{}", timeout=5)
    assert ok is False
    assert "HTTP 403: " in data
    assert "FreeTierOnly" in data


def test_switch_off_restores_legacy_format(monkeypatch):
    """灰度关闭 ⇒ 与旧行为逐字一致（不含正文）。"""
    monkeypatch.setattr(config, "ENABLE_M465_HTTP_ERROR_BODY", False, raising=False)
    _patch_urlopen(monkeypatch, _http_error(429, BODY_429))
    ok, data = SSRF.safe_http_json(_URL, method="POST", data=b"{}", timeout=5)
    assert ok is False
    assert data.startswith("请求失败: "), data
    assert "1305" not in data


def test_urlerror_branch_unchanged(monkeypatch):
    """非 HTTPError 的连接失败仍走旧格式（本次不改既有分支）。"""
    _patch_urlopen(monkeypatch, urllib.error.URLError("timed out"))
    ok, data = SSRF.safe_http_json(_URL, method="POST", data=b"{}", timeout=5)
    assert ok is False
    assert data.startswith("请求失败: "), data


def test_http_error_with_empty_body_uses_reason(monkeypatch):
    _patch_urlopen(monkeypatch, _http_error(500, ""))
    ok, data = SSRF.safe_http_json(_URL, method="POST", data=b"{}", timeout=5)
    assert ok is False
    assert data.startswith("HTTP 500: "), data
    assert len(data) > len("HTTP 500: ")


# ==================== 刀2：限流分类纯函数 ====================

@pytest.mark.parametrize("text,expected", [
    (f"HTTP 429: {BODY_429}", "rate_limited"),
    ('HTTP 429: {"error":"rate limited"}', "rate_limited"),
    ('HTTP 400: {"error":{"code":"1305"}}', "rate_limited"),
    (f"HTTP 403: {BODY_403}", "quota"),
    ("HTTP 403: Forbidden", "quota"),
    # ★466刀1：402 余额不足与额度耗尽等价（正文来自线上 14:15:29 实测）
    ('HTTP 402: {"code":30001,"message":"Sorry, your account balance is insufficient",'
     '"data":null}', "quota"),
    ("HTTP 402: Payment Required", "quota"),
    ('HTTP 400: {"message":"账户余额不足"}', "quota"),
    ("请求失败: <urlopen error timed out>", "error"),
    ("响应解析失败: Expecting value", "error"),
    ("HTTP 500: internal", "error"),
    ("", "error"),
    (None, "error"),
])
def test_fail_kind(text, expected):
    assert SEE._m465_fail_kind(text) == expected


# ==================== 刀3：同轮熔断 ====================

def test_circuit_closed_under_threshold():
    obj = _mk()
    obj._m465_fail_streak = 2          # 默认阈值 3
    assert obj._m465_circuit_open() is False


def test_circuit_opens_at_threshold():
    obj = _mk()
    obj._m465_fail_streak = 3
    assert obj._m465_circuit_open() is True


def test_circuit_threshold_from_config(monkeypatch):
    monkeypatch.setattr(config, "M465_LLM_FAIL_STREAK_BREAK", 5, raising=False)
    obj = _mk()
    obj._m465_fail_streak = 3
    assert obj._m465_circuit_open() is False
    obj._m465_fail_streak = 5
    assert obj._m465_circuit_open() is True


def test_circuit_switch_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M465_LLM_SAME_ROUND_BREAK", False, raising=False)
    obj = _mk()
    obj._m465_fail_streak = 99
    assert obj._m465_circuit_open() is False


def test_open_circuit_short_circuits_repair(monkeypatch):
    """熔断后本轮不再发起任何 LLM 调用（判据：调用计数为 0）。"""
    obj = _mk()
    obj._m465_fail_streak = 3
    calls = []
    monkeypatch.setattr(obj, "_call_llm_with_followup",
                        lambda *a, **k: calls.append(a) or "SHOULD_NOT_BE_USED")
    out = obj._call_llm_for_repair({"type": "silent_exception", "description": "d"}, "x = 1")
    assert out is None
    assert calls == []


# ==================== 刀4：换道重试 ====================

def test_failover_retries_on_next_channel(monkeypatch):
    obj = _mk()
    obj._m44_last_channel = ""
    seen = []
    alt = {"name": "bailian-qwen-max", "api_url": "https://dashscope.aliyuncs.com/x",
           "api_key": "k2", "model": "qwen-max"}
    monkeypatch.setattr(obj, "_m96_select_channel",
                        lambda exclude="": alt if "zhipu-47-flash" in _as_list(exclude) else None)
    monkeypatch.setattr(obj, "_call_llm_with_followup",
                        lambda url, key, prompt, mf, scene, model: "PATCH-OK")
    monkeypatch.setattr(obj, "_m96_record_channel_result",
                        lambda n, s, lat=0.0: seen.append((n, s)))
    out = obj._m465_failover_retry("p", 0, None, "zhipu-47-flash")
    assert out == "PATCH-OK"
    assert seen == [("bailian-qwen-max", True)]
    assert obj._m44_last_channel == "bailian-qwen-max"


def test_failover_walks_past_dead_channel(monkeypatch):
    """p1 429 → p2 额度耗尽(403) → p3 成功：只换一次会停在 p2，仍然 0 补丁。"""
    monkeypatch.setattr(config, "M465_LLM_FAILOVER_MAX_ATTEMPTS", 3, raising=False)
    obj = _mk()
    seq = [
        {"name": "bailian-qwen-plus", "api_url": "https://b/plus",
         "api_key": "k2", "model": "qwen-plus"},
        {"name": "zhipu", "api_url": "https://z/glm", "api_key": "k3", "model": "glm-4-flash"},
    ]
    rec, asked = [], []

    def _sel(exclude=""):
        asked.append(_as_list(exclude))
        for c in seq:
            if c["name"] not in _as_list(exclude):
                return c
        return None

    def _call(url, key, prompt, mf, scene, model):
        if url.endswith("/plus"):
            obj._m465_last_fail = "quota"      # 第二个渠道：免费额度耗尽
            return None
        obj._m465_last_fail = ""
        return "PATCH-P3"

    monkeypatch.setattr(obj, "_m96_select_channel", _sel)
    monkeypatch.setattr(obj, "_call_llm_with_followup", _call)
    monkeypatch.setattr(obj, "_m96_record_channel_result",
                        lambda n, s, lat=0.0: rec.append((n, s)))
    out = obj._m465_failover_retry("p", 0, None, "zhipu-47-flash")
    assert out == "PATCH-P3"
    assert rec == [("bailian-qwen-plus", False), ("zhipu", True)]
    assert asked == [["zhipu-47-flash"], ["zhipu-47-flash", "bailian-qwen-plus"]]


def test_failover_reaches_live_channel_behind_402(monkeypatch):
    """★466刀1：线上真实序列重放 —— 429 → 403 → **402** → 活渠道。

    14:15:29 实测渠道池次序：p1 zhipu-47-flash(429 1302) →
    p2 bailian-qwen-plus(403 FreeTierOnly) → p3 siliconflow(402 balance
    insufficient) → p4 zhipu(glm-4-flash，14:15:45 成功 805 字)。
    465 只认 429/403 ⇒ 402 被判 error ⇒ 在第 3 个死渠道处停机，
    永远到不了第 4 位的活渠道。本用例把这条链整段重放。
    """
    monkeypatch.setattr(config, "M465_LLM_FAILOVER_MAX_ATTEMPTS", 4, raising=False)
    obj = _mk()
    seq = [
        {"name": "bailian-qwen-plus", "api_url": "https://d/plus",
         "api_key": "k2", "model": "qwen-plus"},
        {"name": "siliconflow-qwen2-7b", "api_url": "https://s/glm",
         "api_key": "k3", "model": "zai-org/GLM-5.3-Flash"},
        {"name": "zhipu", "api_url": "https://z/glm", "api_key": "k4", "model": "glm-4-flash"},
    ]
    rec, order = [], []

    def _sel(exclude=""):
        for c in seq:
            if c["name"] not in _as_list(exclude):
                return c
        return None

    def _call(url, key, prompt, mf, scene, model):
        order.append(model)
        if url.endswith("/plus"):
            obj._m465_last_fail = SEE._m465_fail_kind(
                'HTTP 403: {"error":{"code":"AllocationQuota.FreeTierOnly"}}')
            return None
        if url.endswith("/s/glm"):
            obj._m465_last_fail = SEE._m465_fail_kind(
                'HTTP 402: {"code":30001,"message":"Sorry, your account balance is insufficient"}')
            return None
        obj._m465_last_fail = ""
        return "PATCH-LIVE"

    monkeypatch.setattr(obj, "_m96_select_channel", _sel)
    monkeypatch.setattr(obj, "_call_llm_with_followup", _call)
    monkeypatch.setattr(obj, "_m96_record_channel_result",
                        lambda n, s, lat=0.0: rec.append((n, s)))
    out = obj._m465_failover_retry("p", 0, None, "zhipu-47-flash")
    assert out == "PATCH-LIVE", "402 若不归为 quota，会在第 3 个死渠道处停机"
    assert order == ["qwen-plus", "zai-org/GLM-5.3-Flash", "glm-4-flash"]
    assert rec == [("bailian-qwen-plus", False),
                   ("siliconflow-qwen2-7b", False),
                   ("zhipu", True)]
    assert obj._m44_last_channel == "zhipu"


def test_failover_stops_on_non_retryable(monkeypatch):
    """超时/解析失败不换道：换到另一个慢渠道只会成倍拉长本轮耗时。"""
    monkeypatch.setattr(config, "M465_LLM_FAILOVER_MAX_ATTEMPTS", 3, raising=False)
    obj = _mk()
    tries = []

    def _call(url, key, prompt, mf, scene, model):
        obj._m465_last_fail = "error"
        return None

    monkeypatch.setattr(obj, "_m96_select_channel",
                        lambda exclude="": {"name": "slow", "api_url": "https://s/1",
                                            "api_key": "k", "model": "m"})
    monkeypatch.setattr(obj, "_call_llm_with_followup", _call)
    monkeypatch.setattr(obj, "_m96_record_channel_result",
                        lambda n, s, lat=0.0: tries.append(n))
    assert obj._m465_failover_retry("p", 0, None, "zhipu-47-flash") is None
    assert tries == ["slow"], "非限流类失败应立即停止换道"


def test_failover_disabled_does_nothing(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M465_LLM_CHANNEL_FAILOVER", False, raising=False)
    obj = _mk()
    hits = []
    monkeypatch.setattr(obj, "_m96_select_channel",
                        lambda exclude="": hits.append(exclude) or {"name": "x"})
    monkeypatch.setattr(obj, "_call_llm_with_followup",
                        lambda *a, **k: hits.append("call") or "Z")
    assert obj._m465_failover_retry("p", 0, None, "zhipu-47-flash") is None
    assert hits == []


def test_failover_without_alternative(monkeypatch):
    obj = _mk()
    monkeypatch.setattr(obj, "_m96_select_channel", lambda exclude="": None)
    monkeypatch.setattr(obj, "_call_llm_with_followup",
                        lambda *a, **k: pytest.fail("无备选渠道时不应发起调用"))
    assert obj._m465_failover_retry("p", 0, None, "zhipu-47-flash") is None


def test_failover_skips_channel_without_key(monkeypatch):
    obj = _mk()
    monkeypatch.setattr(obj, "_m96_select_channel",
                        lambda exclude="": {"name": "nokey", "api_url": "https://a/b",
                                            "api_key": "", "model": "m"})
    monkeypatch.setattr(obj, "_call_llm_with_followup",
                        lambda *a, **k: pytest.fail("缺 key 时不应发起调用"))
    assert obj._m465_failover_retry("p", 0, None, "zhipu-47-flash") is None


# ==================== 刀4c：选择器 exclude ====================

def test_select_channel_exclude(monkeypatch):
    monkeypatch.setattr(SEE.SafeEvolutionExecutor, "_M96_LUNG", None)
    monkeypatch.setattr(SEE.SafeEvolutionExecutor, "_M96_HEALTH", None)
    pool = [
        {"name": "zhipu-47-flash", "api_url": "https://a/1", "model": "glm-4.7-flash", "api_key": "k1"},
        {"name": "bailian-qwen-max", "api_url": "https://b/1", "model": "qwen-max", "api_key": "k2"},
    ]
    monkeypatch.setattr(config, "get_active_channels",
                        lambda include_disabled=False: list(pool))
    monkeypatch.setattr(QM, "get_channel_quota_monitor", lambda: _QMStub())
    obj = _mk()
    assert obj._m96_select_channel()["name"] == "zhipu-47-flash"
    assert obj._m96_select_channel(exclude="zhipu-47-flash")["name"] == "bailian-qwen-max"


def test_select_channel_exclude_all(monkeypatch):
    monkeypatch.setattr(SEE.SafeEvolutionExecutor, "_M96_LUNG", None)
    monkeypatch.setattr(SEE.SafeEvolutionExecutor, "_M96_HEALTH", None)
    monkeypatch.setattr(config, "get_active_channels",
                        lambda include_disabled=False: [
                            {"name": "only", "api_url": "https://a/1", "model": "m", "api_key": "k"}])
    monkeypatch.setattr(QM, "get_channel_quota_monitor", lambda: _QMStub())
    obj = _mk()
    assert obj._m96_select_channel(exclude="only") is None


# ==================== 接线守卫（防回退到「只有定义、零调用点」） ====================

def test_health_result_is_actually_recorded():
    src = io.open(os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py"),
                  encoding="utf-8").read()
    # 465 前：`_m96_record_channel_result` 只有 def、零调用点 ⇒ 熔断永不开启
    assert "self._m96_record_channel_result(_m96_name, bool(_answer))" in src


def test_failure_log_carries_body():
    src = io.open(os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py"),
                  encoding="utf-8").read()
    assert "_m465_err = " in src
    assert "正文=%s，连续第 %d 次" in src
