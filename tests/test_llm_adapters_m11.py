# -*- coding: utf-8 -*-
"""主线第11批 T4 P2-59：LLM 适配器架构 —— 回归测试。

覆盖：
1. BaseLLMAdapter 抽象接口约定
2. OpenAICompatibleAdapter：请求构造（含思考模式/透传）、响应解析多层防御
3. AdapterRegistry：内置注册、按类型取用、未知类型回落、external_gateway 预留
4. 单例 get/reset
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_base_adapter_interface():
    from nucleus.llm.base_adapter import BaseLLMAdapter
    a = BaseLLMAdapter()
    assert a.adapter_type == "base"
    assert a.check_availability("k") is True
    assert a.check_availability("") is False
    assert a.supports("any") is True
    # 抽象方法未实现 → NotImplementedError
    for call in (lambda: a.build_request("m", []),
                 lambda: a.parse_response({})):
        try:
            call()
            raise AssertionError("应抛 NotImplementedError")
        except NotImplementedError:
            pass


def test_openai_adapter_build_request():
    from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter
    a = OpenAICompatibleAdapter()
    assert a.adapter_type == "openai_compatible"
    req = a.build_request("deepseek-flash", [{"role": "user", "content": "hi"}])
    assert req["model"] == "deepseek-flash"
    assert req["messages"][0]["content"] == "hi"
    assert req["temperature"] == 0.7 and req["max_tokens"] == 512
    assert "thinking" not in req, "未传 enable_thinking 不应附带思考字段"

    req2 = a.build_request("m", [], enable_thinking=True, top_p=0.9)
    assert req2["thinking"] == {"type": "enabled"}
    assert req2["reasoning_effort"] == "high"
    assert req2["top_p"] == 0.9
    req3 = a.build_request("m", [], enable_thinking=False)
    assert req3["thinking"] == {"type": "disabled"}
    assert req3["reasoning_effort"] == "low"

    h = a.build_headers("secret")
    assert h["Authorization"] == "Bearer secret"


def test_openai_adapter_parse_response_defensive():
    from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter
    a = OpenAICompatibleAdapter()
    assert a.parse_response({"choices": [{"message": {"content": "  hi  "}}]}) == "hi"
    # 各类异常结构一律返回 None，不抛
    for bad in (None, {}, {"choices": []}, {"choices": "x"},
                {"choices": [None]}, {"choices": [{}]},
                {"choices": [{"message": None}]},
                {"choices": [{"message": {"content": 123}}]},
                {"choices": [{"message": {"content": "   "}}]}):
        assert a.parse_response(bad) is None, f"应返回 None: {bad!r}"


def test_registry_builtin_and_fallback():
    from nucleus.llm.adapter_registry import (get_adapter_registry,
                                              reset_adapter_registry)
    reset_adapter_registry()
    r = get_adapter_registry()
    assert "openai_compatible" in r.list_types()
    assert "external_gateway" in r.list_types(), "裁决要求预留网关适配器"
    assert r.has("openai_compatible") is True
    assert r.has("nope") is False
    assert r.get("openai_compatible").adapter_type == "openai_compatible"
    # 未知类型回落 OpenAI 兼容
    assert r.get("unknown_xyz").adapter_type == "openai_compatible"
    assert r.get(None).adapter_type == "openai_compatible"
    reset_adapter_registry()


def test_registry_external_gateway_reserved():
    """网关适配器为预留空实现：未配令牌即不可用（默认关闭时零副作用）。"""
    from nucleus.llm.adapter_registry import get_adapter_registry
    r = get_adapter_registry()
    g = r.get("external_gateway")
    assert g.adapter_type == "external_gateway"
    assert g.check_availability("") is False
    assert g.check_availability("token") is True
    # 待接入：请求/解析复用 OpenAI 兼容格式（可正常往返）
    req = g.build_request("deepseek-flash", [{"role": "user", "content": "hi"}])
    assert req["model"] == "deepseek-flash"
    assert g.parse_response({"choices": [{"message": {"content": "ok"}}]}) == "ok"


def test_registry_register_custom():
    """注册自定义适配器可覆盖/扩展。"""
    from nucleus.llm.adapter_registry import (AdapterRegistry,
                                              reset_adapter_registry)
    from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter

    class MyAdapter(OpenAICompatibleAdapter):
        adapter_type = "my_fmt"

    reset_adapter_registry()
    r = AdapterRegistry()
    r.register(MyAdapter())
    assert r.has("my_fmt")
    assert r.get("my_fmt").adapter_type == "my_fmt"
    reset_adapter_registry()
