# -*- coding: utf-8 -*-
"""
adapter_registry.py —— 适配器注册表

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: LLM渠道适配器的注册与发现
机制: 基于ExternalGatewayAdapter类实现，包含10个核心方法
定位: LLM抽象层
"""

from __future__ import annotations

from nucleus.llm.base_adapter import BaseLLMAdapter
from nucleus.llm.openai_compatible_adapter import OpenAICompatibleAdapter


class ExternalGatewayAdapter(BaseLLMAdapter):
    """外挂 LLM 聚合网关适配器（预留，待接入）。

    星轨裁决选 3 的落点：网关作为一个「可选渠道源」，通过本适配器接入。
    网关内部（New API）自行完成模型映射 / 渠道优先级 / 失败重试，
    故框架侧按 OpenAI 兼容格式收发即可——待 P1 阶段配置网关令牌后直接启用。

    当前为 none-pass 空实现：未配置网关（USE_EXTERNAL_LLM_GATEWAY=False）时，
    注册表仍可解析本类型，但 check_availability 返回 False，肺部自动跳过，
    不影响任何既有链路。
    """

    adapter_type = "external_gateway"

    def build_request(self, model: str, messages: list, **kwargs):  # type: ignore[override]
        # 待接入：网关对外即 OpenAI 兼容格式，直接复用兼容实现
        return OpenAICompatibleAdapter().build_request(model, messages, **kwargs)

    def parse_response(self, response):  # type: ignore[override]
        return OpenAICompatibleAdapter().parse_response(response)

    def extract_usage(self, response):  # type: ignore[override]  # _m94_extract_usage_marker
        """★第94批 T-94b：网关对外即 OpenAI 兼容格式，直接复用兼容实现。"""
        return OpenAICompatibleAdapter().extract_usage(response)

    def check_availability(self, api_key: str) -> bool:
        # 未配置令牌即不可用 —— 保证默认（关闭网关）时零副作用
        return bool(api_key)


class AdapterRegistry:
    """适配器注册表（进程内单例，惰性构建）。"""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseLLMAdapter] = {}
        # 内置适配器
        for _a in (OpenAICompatibleAdapter(), ExternalGatewayAdapter()):
            self.register(_a)

    def register(self, adapter: BaseLLMAdapter) -> None:
        """注册（或覆盖）一个适配器实例。"""
        self._adapters[adapter.adapter_type] = adapter

    def get(self, adapter_type: str) -> BaseLLMAdapter:
        """按类型取适配器；未知类型回落到 OpenAI 兼容（最通用格式）。"""
        _t = (adapter_type or "").strip()
        if _t in self._adapters:
            return self._adapters[_t]
        return self._fallback()

    @staticmethod
    def _fallback() -> BaseLLMAdapter:
        return OpenAICompatibleAdapter()

    def has(self, adapter_type: str) -> bool:
        return (adapter_type or "").strip() in self._adapters

    def list_types(self) -> list:
        return sorted(self._adapters.keys())


#: 进程内单例
_registry: AdapterRegistry | None = None


def get_adapter_registry() -> AdapterRegistry:
    """获取（惰性创建）适配器注册表单例。"""
    global _registry
    if _registry is None:
        _registry = AdapterRegistry()
    return _registry


def reset_adapter_registry() -> None:
    """复位单例（测试隔离用；与项目其他 shutdown_* 惯例一致）。"""
    global _registry
    _registry = None
