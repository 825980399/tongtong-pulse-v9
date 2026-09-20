# -*- coding: utf-8 -*-
"""
base_adapter.py —— 基础适配器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: LLM渠道适配器基类与接口定义
机制: 基于BaseLLMAdapter类实现，包含5个核心方法
定位: LLM抽象层
"""

from __future__ import annotations

from typing import Any



class BaseLLMAdapter:
    """LLM 渠道适配器基类。

    子类需按需覆写 build_request / parse_response / check_availability。
    默认实现按 OpenAI 兼容格式处理，便于国内多数兼容 OpenAI 的 API 直接复用。
    """

    #: 适配器类型标识，注册表按此键索引
    adapter_type: str = "base"

    def build_request(self, model: str, messages: list, **kwargs: Any) -> dict:
        """构造请求体（dict，供调用方 json.dumps 后 POST）。

        Args:
            model: 渠道实际模型名。
            messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]
            **kwargs: 可选参数（temperature / max_tokens / enable_thinking 等）。
        """
        raise NotImplementedError

    def build_headers(self, api_key: str) -> dict:
        """构造请求头。默认 Bearer 认证。"""
        return {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": "Bearer " + (api_key or ""),
        }

    def parse_response(self, response: Any) -> str | None:
        """从响应 dict 中提取回答文本；结构异常返回 None（不抛）。"""
        raise NotImplementedError

    def extract_usage(self, response: Any) -> dict | None:
        """★第94批 T-94b：从响应中提取 token 用量（**可选能力**）。  # _m94_extract_usage_marker

        设计约束（任务书 §T-94b.1）：
        * **不改** `parse_response` 签名（零回归），只**新增**本方法；
        * 基类默认返回 ``None`` —— 未覆写的适配器一律「无用量」，调用方据此
          保持既有行为（``tokens`` 沿用原值，新增 ``usage`` 字段为 ``None``）。

        Returns:
            ``{"prompt_tokens": int, "completion_tokens": int,
            "total_tokens": int}`` 或 ``None``（无 usage / 结构异常）。
        """
        return None

    def check_availability(self, api_key: str) -> bool:
        """渠道可用性预检（只做静态检查，不发网络请求）。

        默认规则：api_key 非空即视为「配置上可用」。真实连通性由调用方发起。
        """
        return bool(api_key)

    def supports(self, model: str) -> bool:
        """该适配器是否支持给定模型名。默认全部支持。"""
        return True
