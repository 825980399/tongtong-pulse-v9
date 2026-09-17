# -*- coding: utf-8 -*-
"""主线第11批 T2/T3 P2-59：LLM 渠道自主管理（P0）—— 配置与肺部多渠道回归测试。

覆盖：
1. config.REMOTE_API_CHANNELS 结构完整、默认值正确
2. 兼容层 get_remote_api_config()：开关开时合并渠道、关时原样返回（向后兼容）
3. get_active_channels / _channels_enabled / get_external_gateway_config
4. ChannelHealthTracker：成功率、平均延迟、连续失败熔断、到期半开
5. PulseLung 渠道路由：_select_default_channel / _rotate_channel / _is_advanced_task
6. 灰度：REMOTE_API_CHANNELS 关闭时 _call_remote_api 不介入渠道逻辑
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bare_lung():
    """轻量实例化 PulseLung（绕过 __init__ 的器官注册副作用）。"""
    from organs.body.PulseLung import PulseLung
    lung = PulseLung.__new__(PulseLung)
    lung._log = lambda *a, **k: None
    lung._channel_health = None
    lung._model_quality = {}
    lung._call_success_count = 0
    lung._call_fail_count = 0
    return lung


# ==================== T2 配置 ====================

def test_channels_config_block_exists():
    import config
    assert hasattr(config, "REMOTE_API_CHANNELS"), "缺少 REMOTE_API_CHANNELS"
    c = config.REMOTE_API_CHANNELS
    assert c["enabled"] is True
    assert c["advanced_model"] == "deepseek-flash"
    assert isinstance(c["default_channels"], list) and c["default_channels"]
    ch = c["default_channels"][0]
    for k in ("name", "model", "api_url", "api_key", "priority", "enabled", "adapter"):
        assert k in ch, f"渠道缺少字段 {k}"
    assert ch["adapter"] == "openai_compatible"
    assert "health" in c and "circuit_break_threshold" in c["health"]
    assert c["local_fallback_enabled"] is True


def test_external_gateway_switch_default_off():
    import config
    assert config.USE_EXTERNAL_LLM_GATEWAY is False
    gw = config.get_external_gateway_config()
    assert gw["enabled"] is False, "默认关闭网关"
    assert gw["adapter"] == "external_gateway"


def test_backward_compat_remote_api_config():
    """兼容层：开关开时 default/advanced 被渠道配置覆盖，原有字段保留。"""
    import config
    merged = config.get_remote_api_config()
    # 原有字段必须保留（下游 9 个调用方仍读这些键）
    assert "api_url" in merged and "api_key" in merged
    assert merged["advanced_model"] == "deepseek-flash"
    # 渠道池首选覆盖 default_model
    assert merged["default_model"] == config.get_active_channels()[0]["model"]
    assert merged["max_concurrent"] == 8, "非渠道字段不应被改动"


def test_get_active_channels_sorted():
    import config
    chs = config.get_active_channels()
    assert chs, "应有可用渠道"
    prios = [c.get("priority", 999) for c in chs]
    assert prios == sorted(prios), "渠道应按 priority 升序"


def test_channels_enabled_false_when_disabled():
    """关闭 enabled 后 _channels_enabled 返回 False（灰度开关生效）。"""
    import config
    saved = config.REMOTE_API_CHANNELS["enabled"]
    try:
        config.REMOTE_API_CHANNELS["enabled"] = False
        assert config._channels_enabled() is False
        # 关闭时兼容层原样返回旧配置（对象同一性）
        assert config.get_remote_api_config() is config.REMOTE_API_CONFIG
    finally:
        config.REMOTE_API_CHANNELS["enabled"] = saved


# ==================== 渠道健康度 ====================

def test_channel_health_success_rate_and_latency():
    from nucleus.llm.channel_health import ChannelHealthTracker
    t = ChannelHealthTracker()
    t.record("a", True, 1.0)
    t.record("a", True, 3.0)
    t.record("a", False, 2.0)
    assert abs(t.success_rate("a") - 2 / 3) < 1e-6
    assert abs(t.avg_latency("a") - 2.0) < 1e-6


def test_channel_health_circuit_break_and_recover():
    from nucleus.llm.channel_health import ChannelHealthTracker
    t = ChannelHealthTracker(circuit_break_threshold=3, circuit_break_seconds=0.1)
    assert t.is_available("x") is True
    for _ in range(3):
        t.record("x", False)
    assert t.is_available("x") is False, "连续3次失败应熔断"
    time.sleep(0.15)
    assert t.is_available("x") is True, "熔断到期后应放行（半开）"
    t.record("x", True)
    assert t.consecutive_fails("x") == 0


# ==================== T3 肺部路由 ====================

def test_lung_select_and_rotate_channel():
    """★主线第32批 T1（P2-187）：首选渠道从 config 动态取（原硬编码 zhipu）。

    渠道池已多次调整（现首选为 ark-ds-v4-flash），断言改为「选出的就是
    priority 最高的可用渠道」，与配置解耦且验证强度不变。
    """
    import config as _cfg
    _chs = _cfg.get_active_channels()
    assert len(_chs) >= 2, "本用例需要至少 2 个可用渠道（用于验证轮询）"
    _first = _chs[0]["name"]

    lung = _bare_lung()
    sel = lung._select_default_channel()
    assert sel is not None and sel["name"] == _first, \
        f"应选出 priority 最高的可用渠道 {_first}，实际 {sel and sel.get('name')}"

    # 当前渠道失败后轮询：多渠道路由到下一个未熔断渠道
    _nxt = lung._rotate_channel({"name": _first})
    assert _nxt is not None and _nxt["name"] != _first, "轮询后应切到其他渠道"


def test_lung_advanced_task_detection():
    lung = _bare_lung()
    assert lung._is_advanced_task("deepseek-flash") is True
    assert lung._is_advanced_task("code") is True
    assert lung._is_advanced_task("deep_think") is True
    assert lung._is_advanced_task("deepseek-v4-flash") is False
    assert lung._is_advanced_task("chat") is False


def test_lung_gateway_channel_off_by_default():
    lung = _bare_lung()
    assert lung._gateway_channel() is None, "网关开关默认关闭，不应返回网关渠道"


def test_lung_call_via_channels_no_available_returns_none(monkeypatch):
    """无任何可用渠道（渠道池被 mock 成空 + 高级渠道配置不全）→ 返回 None 交上层回退。"""
    lung = _bare_lung()
    import config
    monkeypatch.setattr(config, "get_active_channels", lambda *a, **k: [])
    monkeypatch.setattr(lung, "_build_advanced_channel", lambda: None)
    assert lung._call_via_channels("hi", "chat") is None


def test_lung_call_via_channels_success(monkeypatch):
    """渠道调用成功 → 返回文本 + 健康度记为成功。"""
    lung = _bare_lung()
    import config
    monkeypatch.setattr(config, "get_active_channels",
                        lambda *a, **k: [{"name": "c1", "model": "m1",
                                          "api_url": "http://x", "api_key": "k",
                                          "priority": 1}])
    monkeypatch.setattr(lung, "_call_channel", lambda ch, p, **kw: "回答内容")
    monkeypatch.setattr(lung, "_build_advanced_channel", lambda: None)
    out = lung._call_via_channels("hi", "chat")
    assert out == "回答内容"
    assert lung._get_channel_health().success_rate("c1") == 1.0


def test_lung_call_via_channels_failover(monkeypatch):
    """首个渠道失败 → 自动轮询下一个成功（含健康度失败记录）。"""
    lung = _bare_lung()
    import config
    monkeypatch.setattr(config, "get_active_channels",
                        lambda *a, **k: [
                            {"name": "c1", "model": "m1", "api_url": "http://1",
                             "api_key": "k", "priority": 1},
                            {"name": "c2", "model": "m2", "api_url": "http://2",
                             "api_key": "k", "priority": 2},
                        ])
    monkeypatch.setattr(lung, "_build_advanced_channel", lambda: None)

    def _fake_call(ch, p, **kw):
        return None if ch["name"] == "c1" else "第二个渠道的回答"

    monkeypatch.setattr(lung, "_call_channel", _fake_call)
    out = lung._call_via_channels("hi", "chat")
    assert out == "第二个渠道的回答"
    h = lung._get_channel_health()
    assert h.success_rate("c1") == 0.0
    assert h.success_rate("c2") == 1.0


def test_lung_channels_disabled_untouched(monkeypatch):
    """灰度：REMOTE_API_CHANNELS 关闭时 _channels_config 返回 None（不介入渠道逻辑）。"""
    lung = _bare_lung()
    import config
    saved = config.REMOTE_API_CHANNELS["enabled"]
    try:
        config.REMOTE_API_CHANNELS["enabled"] = False
        assert lung._channels_config() is None
    finally:
        config.REMOTE_API_CHANNELS["enabled"] = saved
