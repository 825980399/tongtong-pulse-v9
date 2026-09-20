# -*- coding: utf-8 -*-
"""
SelfReflectionEngine.py —— 自我反思引擎

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 框架自我反思与经验总结
机制: 基于SelfReflectionEngine类实现，包含6个核心方法
定位: 进化认知层
"""

from __future__ import annotations
from nucleus.LLMDependencyMetrics import (SCENE_EVOLUTION, record_llm_call)
from nucleus.llm.call_recorder import trace_evolution_call

import json
import os
import time
from typing import Any



class SelfReflectionEngine:
    """失败后自我反思引擎。"""

    def __init__(self, project_root: str):
        self._project_root = project_root

    # ========== LLM 调用 ==========

    @trace_evolution_call(prompt_pos=2, version="evolution.reflect.v1")
    def _call_llm(self, system: str, prompt: str,
                  max_tokens: int = 2048) -> str | None:
        """统一 LLM 调用（复用 SSRF 防护网络层）。"""
        record_llm_call(SCENE_EVOLUTION)
        try:
            import config
            _api_cfg = getattr(config, 'REMOTE_API_CONFIG', {})
            _api_url = _api_cfg.get("api_url", "")
            _api_key = _api_cfg.get("api_key", "")
            if not _api_url or not _api_key:
                self._m44_last_error = "missing_api_config: api_url/api_key 未配置"
                return None
            _model = _api_cfg.get("advanced_model", "deepseek-flash")
            _payload = {
                "model": _model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": max_tokens,
            }
            _payload_bytes = json.dumps(_payload, ensure_ascii=False).encode('utf-8')
            _headers = {
                'Content-Type': 'application/json; charset=utf-8',
                'Authorization': 'Bearer ' + _api_key,
            }
            from nucleus.ssrf_guard import safe_http_json
            from nucleus.api_rate_limiter import get_llm_call_config, api_rate_limited
            _cfg = get_llm_call_config()
            with api_rate_limited(enabled=_cfg.get('enable_rate_limit', True)):
                _ok, _data = safe_http_json(
                    _api_url, method='POST', data=_payload_bytes, headers=_headers,
                    timeout=_cfg["timeout_by_purpose"]["general"],
                )
            if not _ok or not isinstance(_data, dict):
                self._m44_last_error = "http_failed: 请求失败或响应非 JSON 对象"
                return None
            # ★第94批 T-94b：暂存 usage 供 `trace_evolution_call` 装饰器留存
            self._m44_last_usage = _data.get("usage")  # _m94_extract_usage_marker
            _choices = _data.get("choices", [])
            if _choices:
                return _choices[0].get("message", {}).get("content", "")
            return None
        except Exception as e:
            print(f"[WARNING] SelfReflectionEngine.py:65: {type(e).__name__}: {e}")
            # ★第44批 T1：把失败明细留给埋点装饰器
            self._m44_last_error = "%s: %s" % (type(e).__name__, e)
            return None

    # ========== 阶段C核心：反思 + 改进 ==========

    def reflect_and_improve(self, failed_patch: dict[str, Any],
                            verification_detail: dict[str, Any] | None = None,
                            health_before: float | None = None,
                            health_after: float | None = None) -> dict[str, Any] | None:
        """
        验证失败后，把失败现场喂给 LLM，分析根因并生成改进版补丁。

        Args:
            failed_patch: 失败的补丁（含 original_code/modified_code/diff_summary）
            verification_detail: 三关验证详细结果（run_verification_detailed 的返回）
            health_before: 应用前健康度
            health_after: 应用后健康度

        返回:
            改进版补丁（与原补丁同结构），或 None（LLM 不可用或无法改进）。
        """
        # 构建失败现场文本
        _fail_ctx = self._format_failure_context(
            failed_patch, verification_detail, health_before, health_after
        )

        _prompt = (
            f"你是曈曈的自我反思引擎。下面这个补丁应用后验证失败，请分析根因并生成改进版补丁。\n\n"
            f"{_fail_ctx}"
            f"要求：\n"
            f"1. 分析最可能的失败根因。\n"
            f"2. 生成改进版的修改后代码（保持 original_code 不变，只改 modified_code）。\n"
            f"3. 输出严格 JSON：{{\"root_cause\": \"根因分析\", "
            f"\"modified_code\": \"改进后的完整代码\"}}。\n"
            f"4. 只输出 JSON，不要输出解释或 markdown 标记。\n"
        )
        _response = self._call_llm(
            system="你是曈曈的自我反思引擎，分析补丁失败根因并生成改进。",
            prompt=_prompt,
            max_tokens=2048,
        )
        return self._parse_reflection(_response, failed_patch)

    def _format_failure_context(self, failed_patch: dict[str, Any],
                                verification_detail: dict[str, Any] | None,
                                health_before: float | None,
                                health_after: float | None) -> str:
        """格式化失败现场为文本。"""
        _parts = []

        _parts.append(
            f"失败补丁文件: {failed_patch.get('file', '')}\n"
            f"失败补丁方法: {failed_patch.get('method', '')}\n"
            f"变更摘要: {failed_patch.get('diff_summary', '')}\n"
            f"修改前代码:\n```python\n{failed_patch.get('original_code', '')[:2000]}\n```\n"
            f"修改后代码:\n```python\n{failed_patch.get('modified_code', '')[:2000]}\n```\n"
        )

        if verification_detail:
            _checks = verification_detail.get("checks", {})
            _detail = verification_detail.get("detail", {})
            _failed_checks = [k for k, v in _checks.items() if not v]
            _parts.append(
                f"验证失败关卡: {', '.join(_failed_checks) if _failed_checks else '未知'}\n"
                f"验证详情: {json.dumps(_detail, ensure_ascii=False)}\n"
            )

        if health_before is not None and health_after is not None:
            _delta = round(health_after - health_before, 1)
            _parts.append(
                f"健康度变化: {health_before} → {health_after} ({_delta:+})\n"
            )

        return "\n".join(_parts) + "\n"

    def _parse_reflection(self, response: str | None,
                          failed_patch: dict[str, Any]) -> dict[str, Any] | None:
        """解析 LLM 输出的反思 JSON，构造改进版补丁。"""
        if not response:
            return None
        try:
            _clean = response.strip()
            if _clean.startswith("```"):
                _clean = _clean.strip("`")
                _clean = _clean.removeprefix("json")
            _data = json.loads(_clean)
            _modified = _data.get("modified_code", "")
            if not _modified or _modified.strip() == failed_patch.get("original_code", "").strip():
                return None
            # 构造改进版补丁（复用原补丁的 file/method/original_code）
            return {
                "id": f"patch_reflect_{int(time.time())}_{hash(_modified) & 0xFFFF:04x}",
                "file": failed_patch.get("file", ""),
                "method": failed_patch.get("method", ""),
                "issue_type": failed_patch.get("issue_type", "unknown"),
                "risk_level": failed_patch.get("risk_level", "中等"),
                "description": f"反思改进: {_data.get('root_cause', '')[:200]}",
                "original_code": failed_patch.get("original_code", ""),
                "modified_code": _modified,
                "diff_summary": f"反思改进: {_data.get('root_cause', '')[:100]}",
                "trust_score": 40,  # 反思产物信任分更低（风险更高）
                "confidence": "low",  # ★反思产物最低置信度
                "repair_source": "llm_reflection",  # ★标记反思来源
                "generated_at": time.time(),
                "status": "pending",
                "verification": None,
                "applied": False,
                "applied_at": 0,
            }
        except Exception as e:
            print(f"[WARNING] SelfReflectionEngine.py:174: {type(e).__name__}: {e}")
            return None


# ========== 便捷函数 ==========

def get_self_reflection_engine(project_root: str) -> SelfReflectionEngine:
    """获取 SelfReflectionEngine 实例。"""
    return SelfReflectionEngine(project_root)


if __name__ == "__main__":
    # 自测：无 API key 时优雅降级
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _engine = SelfReflectionEngine(_root)
    _failed = {
        "file": "organs/test.py", "method": "foo",
        "original_code": "def foo():\n    return 1\n",
        "modified_code": "def foo():\n    return None\n",
        "diff_summary": "改坏了",
    }
    _verif = {"passed": False, "checks": {"basic_reasoning": False}, "detail": {"basic_reasoning": "无答案"}}
    _result = _engine.reflect_and_improve(_failed, _verif, 80.0, 60.0)
    print(f"无 API key 时反思返回: {_result} (应为 None，优雅降级)")

    # 测试解析逻辑
    _fake = '{"root_cause": "返回了None导致推理失败", "modified_code": "def foo():\\n    return \\"你好\\""}'
    _parsed = _engine._parse_reflection(_fake, _failed)
    if _parsed:
        print(f"解析成功: {_parsed['repair_source']} 信任={_parsed['trust_score']} 描述={_parsed['description']}")
