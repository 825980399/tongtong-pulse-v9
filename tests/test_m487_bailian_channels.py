"""★487-4 门控测试：百炼渠道池调整（关 5 耗尽 + 增 3 满额 + 留 2 可用）。"""
import config

_CH = config.REMOTE_API_CHANNELS["default_channels"]


def _by_name():
    return {c["name"]: c for c in _CH}


def test_disabled_channels_off(monkeypatch):
    _m = _by_name()
    _disabled = {
        "bailian-qwen38-flash",
        "bailian-deepseek-v41-flash",
        "bailian-glm53",
        "bailian-qwen37-plus",
        "bailian-deepseek-v4-pro",
    }
    for _n in _disabled:
        assert _n in _m, f"渠道缺失: {_n}"
        assert _m[_n].get("enabled") is False, f"{_n} 应关闭"


def test_new_channels_present(monkeypatch):
    _m = _by_name()
    assert _m["bailian-qwen36-flash"]["model"] == "qwen3.6-flash"
    assert _m["bailian-qwen36-flash"]["priority"] == 2
    assert _m["bailian-qwen36-flash"]["enabled"] is True

    assert _m["bailian-qwen35-flash"]["model"] == "qwen3.5-flash"
    assert _m["bailian-qwen35-flash"]["priority"] == 3
    assert _m["bailian-qwen35-flash"]["enabled"] is True

    assert _m["bailian-kimi-k31-pro"]["model"] == "kimi-k3.1-pro"
    assert _m["bailian-kimi-k31-pro"]["priority"] == 4
    assert _m["bailian-kimi-k31-pro"]["enabled"] is True


def test_kept_channels_enabled(monkeypatch):
    _m = _by_name()
    assert _m["bailian-kimi-k3"]["enabled"] is True
    assert _m["bailian-qwen38-max"]["enabled"] is True


def test_active_priority_ordering(monkeypatch):
    # 启用的百炼渠道按 priority 升序，且不含被关的 5 个
    _active = [c for c in _CH
               if c["name"].startswith("bailian") and c.get("enabled")]
    _names = [c["name"] for c in _active]
    for _n in ("bailian-qwen38-flash", "bailian-deepseek-v41-flash",
               "bailian-glm53", "bailian-qwen37-plus", "bailian-deepseek-v4-pro"):
        assert _n not in _names, f"已关渠道不应在启用列表: {_n}"
    _prio = [c["priority"] for c in _active]
    # 启用渠道之间 priority 不应重复（被关的 5 个已排除在外，不影响选择）
    assert len(_prio) == len(set(_prio)), f"启用百炼渠道 priority 重复: {_prio}"
