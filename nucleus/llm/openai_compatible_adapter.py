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

    def extract_usage(self, response: Any) -> dict | None:
        """★第94批 T-94b：解析 OpenAI 响应体的 ``usage`` 字段。  # _m94_extract_usage_marker

        ★实测根因（本批 T0）：``data/llm_traces`` 8 天 5759 条记录中
        ``origin=evolution_task`` **2423 条 100% tokens=0** —— 进化引擎走
        ``trace_evolution_call`` 装饰器留存，而装饰器**从未取用 usage**；肺通道
        虽有 ``_m40_last_usage`` 私有旁路（第40批 T2），但全项目**无统一入口**。

        语义：
        * 缺 ``total_tokens`` 时用 prompt+completion 补齐；
        * 三值全为 0 / 非数值 / 无 ``usage`` → 返回 ``None``（**不写假数据**）。
        """
        try:
            if not isinstance(response, dict):
                return None
            _u = response.get("usage")
            if not isinstance(_u, dict):
                return None

            def _num(_v: Any) -> int | None:
                if isinstance(_v, bool) or not isinstance(_v, (int, float)):
                    return None
                _i = int(_v)
                return _i if _i >= 0 else None

            _pi = _num(_u.get("prompt_tokens"))
            _ci = _num(_u.get("completion_tokens"))
            _ti = _num(_u.get("total_tokens"))
            if _ti is None:
                if _pi is None and _ci is None:
                    return None
                _ti = (_pi or 0) + (_ci or 0)
            if _ti <= 0 and not (_pi or 0) and not (_ci or 0):
                return None
            return {"prompt_tokens": _pi or 0, "completion_tokens": _ci or 0,
                    "total_tokens": _ti}
        except Exception:
            return None

    def check_availability(self, api_key: str) -> bool:
        return bool(api_key)
