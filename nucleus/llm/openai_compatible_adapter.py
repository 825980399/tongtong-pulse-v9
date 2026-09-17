# -*- coding: utf-8 -*-
"""
openai_compatible_adapter.py —— OpenAI兼容适配器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: OpenAI兼容API的渠道适配器
机制: 基于OpenAICompatibleAdapter类实现，包含3个核心方法
定位: LLM适配层
"""

from __future__ import annotations

from typing import Any

from nucleus.llm.base_adapter import BaseLLMAdapter



class OpenAICompatibleAdapter(BaseLLMAdapter):
    """OpenAI /v1/chat/completions 兼容格式。"""

    adapter_type = "openai_compatible"

    #: 默认采样参数（与 PulseLung 既有行为保持一致，避免改变输出风格）
    DEFAULT_TEMPERATURE = 0.7
    DEFAULT_MAX_TOKENS = 512

    def build_request(self, model: str, messages: list, **kwargs: Any) -> dict:
        req: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.DEFAULT_TEMPERATURE),
            "max_tokens": kwargs.get("max_tokens", self.DEFAULT_MAX_TOKENS),
        }
        # 思考模式：
        #   ★主线第58批 T2：优先使用调用方显式指定的 thinking（如 ark-seed 按复杂度路由），
        #     此时 reasoning_effort 也以调用方为准（若存在）；
        #   否则沿用既有 enable_thinking 推导（DeepSeek 风格字段，向后兼容）。
        #   该改动对未预置 thinking 的调用方完全透明。
        if "thinking" in kwargs and isinstance(kwargs.get("thinking"), dict):
            req["thinking"] = kwargs["thinking"]
            if "reasoning_effort" in kwargs:
                req["reasoning_effort"] = kwargs["reasoning_effort"]
        elif "enable_thinking" in kwargs:
            _think = bool(kwargs.get("enable_thinking"))
            req["thinking"] = {"type": "enabled" if _think else "disabled"}
            req["reasoning_effort"] = "high" if _think else "low"
        # 透传额外字段（如 top_p / stop / stream）
        for _k in ("top_p", "stop", "stream", "frequency_penalty", "presence_penalty"):
            if _k in kwargs:
                req[_k] = kwargs[_k]
        return req

    def parse_response(self, response: Any) -> str | None:
        """提取 choices[0].message.content，逐层做结构防御。"""
        try:
            if not isinstance(response, dict):
                return None
            choices = response.get("choices")
            if not isinstance(choices, list) or not choices:
                return None
            first = choices[0]
            if not isinstance(first, dict):
                return None
            message = first.get("message")
            if not isinstance(message, dict):
                return None
            content = message.get("content", "")
            if not isinstance(content, str):
                return None
            _text = content.strip()
            return _text or None
        except Exception:
            return None

    def check_availability(self, api_key: str) -> bool:
        return bool(api_key)
