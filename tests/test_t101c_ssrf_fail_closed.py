"""T-101c（P1）：SSRF 守卫 fail-open 修复 —— 守卫异常/拦截须硬 return 不放行。

验收：
  ✅ 守卫失败硬 return（守卫抛异常 ⇒ 请求被拒绝，不落到裸 urlopen）
  ✅ 两路径失败语义一致（与 _call_remote_api 同 fail-closed）
"""
import os
import sys
import types
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from organs.body.PulseLung import PulseLung


class _FakeLung:
    def _log(self, level, msg, *a, **k):
        pass

    def _build_channel_messages(self, prompt):
        return [{"role": "user", "content": prompt}]

    def _apply_channel_field_policy(self, name, kwargs):
        return {}

    def _apply_ark_seed_complexity_routing(self, name, kwargs, prompt):
        return {}

    def _channel_http_dump_enabled(self):
        return False

    def _mask_headers(self, headers):
        return {}

    def _resolve_channel_timeout(self, channel, cfg):
        return 30

    def _m32_record_quota_usage(self, name, data):
        return None


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"ok"}}]}'


def _channel():
    return {
        "adapter": "openai_compatible",
        "name": "test_chan",
        "api_url": "http://203.0.113.5/v1/chat",
        "api_key": "k",
        "model": "m",
    }


def test_ssrf_guard_exception_fails_closed():
    """守卫自身抛异常 ⇒ 拒绝请求（fail-closed），不得放行。"""
    lung = _FakeLung()
    with mock.patch(
        "nucleus.ssrf_guard.is_safe_http_url",
        side_effect=RuntimeError("guard boom"),
    ):
        result = PulseLung._call_channel(lung, _channel(), prompt="hello")
    assert result is None, "guard exception must fail-closed (return None)"


def test_ssrf_guard_block_returns_none():
    """守卫明确拒绝（_allowed=False）⇒ 拒绝请求。"""
    lung = _FakeLung()
    with mock.patch(
        "nucleus.ssrf_guard.is_safe_http_url",
        return_value=(False, "blocked by SSRF guard"),
    ):
        result = PulseLung._call_channel(lung, _channel(), prompt="hello")
    assert result is None, "guard block must return None"


def test_ssrf_guard_allows_proceeds_to_network():
    """守卫放行（_allowed=True）⇒ 不在此处拦截，继续到真实请求（urlopen）。

    把 urlopen 替换成返回假响应，确认函数越过守卫、拿到响应（非 None），
    而非被守卫误拦成 None。
    """
    lung = _FakeLung()
    _fake_adapter = types.SimpleNamespace(
        build_request=lambda *a, **k: {"model": "m", "messages": []},
        build_headers=lambda *a, **k: {},
        parse_response=lambda d: (d or {}).get("choices", [{}])[0]
        .get("message", {}).get("content"),
        extract_usage=lambda d: None,
    )
    _fake_registry = types.SimpleNamespace(get=lambda *a, **k: _fake_adapter)
    with mock.patch("nucleus.ssrf_guard.is_safe_http_url", return_value=(True, "")), \
         mock.patch("nucleus.llm.adapter_registry.get_adapter_registry",
                    return_value=_fake_registry), \
         mock.patch("urllib.request.urlopen", return_value=_FakeResp()):
        result = PulseLung._call_channel(lung, _channel(), prompt="hello")
    assert result is not None, "guard-allows must proceed past guard (not short-circuit to None)"
