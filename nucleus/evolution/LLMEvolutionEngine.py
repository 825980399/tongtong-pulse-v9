# -*- coding: utf-8 -*-
"""
LLMEvolutionEngine.py —— LLM进化引擎

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 基于大模型的代码进化与优化
机制: 基于LLMEvolutionEngine类实现，包含10个核心方法
定位: 进化执行层
"""

from __future__ import annotations
from nucleus.LLMDependencyMetrics import (SCENE_EVOLUTION, record_llm_call)
from nucleus.llm.call_recorder import trace_evolution_call

import json
import os
import time
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底



class LLMEvolutionEngine(SilentLogMixin):
    """LLM 主导的进化引擎（跨文件 + 多步规划）。"""

    def __init__(self, project_root: str):
        self._project_root = project_root

    # ========== LLM 调用（复用 ssrf_guard 安全网络层） ==========

    @trace_evolution_call(prompt_pos=2, version="evolution.engine.v1")
    def _call_llm(self, system: str, prompt: str,
                  model: str | None = None,
                  max_tokens: int = 2048,
                  temperature: float = 0.2) -> str | None:
        """统一 LLM 调用（复用 SSRF 防护网络层）。
        
        优化（2026-09-10 内部协作者）：
        1. 超时重试机制：偶发网络超时自动重试1次，避免进化失败
        2. 用框架日志系统代替print，确保写入日志文件
        """
        record_llm_call(SCENE_EVOLUTION)
        try:
            import config
            _api_cfg = getattr(config, 'REMOTE_API_CONFIG', {})
            _api_url = _api_cfg.get("api_url", "")
            _api_key = _api_cfg.get("api_key", "")
            if not _api_url or not _api_key:
                self._m44_last_error = "missing_api_config: api_url/api_key 未配置"
                return None
            _model = model or _api_cfg.get("advanced_model", "deepseek-flash")
            _payload = {
                "model": _model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
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
            _timeout = _cfg["timeout_by_purpose"]["general"]
            
            # 超时重试机制：偶发网络问题自动重试1次
            _max_retries = 2
            _ok = False
            _data = None
            for _attempt in range(_max_retries):
                try:
                    with api_rate_limited(enabled=_cfg.get('enable_rate_limit', True)):
                        _ok, _data = safe_http_json(
                            _api_url, method='POST', data=_payload_bytes, headers=_headers,
                            timeout=_timeout,
                        )
                    if _ok and isinstance(_data, dict):
                        break
                except Exception as _retry_err:
                    if _attempt < _max_retries - 1:
                        self._log(LogLevel.DEBUG, 
                            f"LLM调用超时/异常，重试中（{_attempt+1}/{_max_retries}）: {type(_retry_err).__name__}")
                        time.sleep(1.0)
                        continue
                    raise
            
            if not _ok or not isinstance(_data, dict):
                self._m44_last_error = "http_failed: 请求失败或响应非 JSON 对象"
                return None
            # ★第94批 相关任务：暂存 usage 供 `trace_evolution_call` 装饰器留存
            self._last_llm_usage = _data.get("usage")  # _m94_extract_usage_marker
            _choices = _data.get("choices", [])
            if _choices:
                return _choices[0].get("message", {}).get("content", "")
            return None
        except Exception as e:
            # 修复：用框架日志系统代替print，确保写入日志文件
            self._log(LogLevel.WARNING, f"LLM调用失败: {type(e).__name__}: {e}")
            # ★第44批 T1：把失败明细留给埋点装饰器（此前只进日志，留存里恒空）
            self._m44_last_error = "%s: %s" % (type(e).__name__, e)
            return None

    # ========== 阶段B核心1：跨文件补丁包 ==========

    def generate_multi_file_patch(self, issue: dict[str, Any],
                                  original_code: str,
                                  file_path: str,
                                  method_name: str,
                                  call_chain: dict[str, Any] | None = None,
                                  self_inspector=None) -> list[dict[str, Any]]:
        """
        让 LLM 生成跨文件补丁包（改接口时同步迁移调用方）。

        Args:
            issue: 问题字典（type/description 等）
            original_code: 问题方法的原始代码
            file_path: 问题方法所在文件
            method_name: 问题方法名
            call_chain: 调用链上下文（get_call_chain 的结果，含 called_methods/called_by）
            self_inspector: SelfInspector 实例（用于读取调用方代码）

        返回:
            补丁列表（每个含 file/method/original_code/modified_code），
            可能包含多个文件（主文件 + 调用方文件）。
            失败或无 API 时返回空列表。
        """
        # 构建调用链上下文文本
        _call_ctx = self._format_call_chain(call_chain)

        # 读取调用方代码（供 LLM 同步修改）
        _callers_code = self._read_callers_code(call_chain, self_inspector)

        _prompt = (
            f"你是曈曈的架构级代码重构助手。请对下面的问题生成「跨文件补丁包」。\n\n"
            f"问题类型: {issue.get('type', '')}\n"
            f"问题描述: {issue.get('description', '')}\n"
            f"目标方法: {method_name}（位于 {file_path}）\n\n"
            f"目标方法原始代码:\n```python\n{original_code[:3000]}\n```\n\n"
            f"{_call_ctx}"
            f"{_callers_code}"
            f"要求：\n"
            f"1. 若修改会改变方法签名/接口，必须同步修改所有调用方。\n"
            f"2. ★最小化改动：只修改解决问题所必需的代码行，优先局部修改而非整体重写；"
            f"保留原有逻辑、注释和未要求改动的部分。\n"
            f"3. 输出严格的 JSON，结构为 {{\"patches\": ["
            f"{{\"file\": \"文件路径\", \"method\": \"方法名\", "
            f"\"original_code\": \"修改前完整代码\", \"modified_code\": \"修改后完整代码\"}}]}}。\n"
            f"4. 每个补丁的 original_code 必须能在对应文件中唯一定位（含足够上下文）。\n"
            f"5. 只输出 JSON，不要输出任何解释或 markdown 代码块标记。\n"
            f"\n{self._aesthetic_guidance()}"
        )
        _response = self._call_llm(
            system="你是曈曈的架构级代码重构引擎，输出严格 JSON 结构。",
            prompt=_prompt,
            model=None,  # 用 advanced_model（复杂重构需强模型）
            max_tokens=4096,
        )
        return self._parse_multi_file_patch(_response, issue)

    def _aesthetic_guidance(self) -> str:
        """获取框架审美偏好指引（生成回流），异常降级为空串（不阻断生成）。"""
        try:
            from nucleus.evolution.AestheticJudge import get_aesthetic_judge
            return get_aesthetic_judge().feedback_guidance()
        except Exception:
            return ""

    def _format_call_chain(self, call_chain: dict[str, Any] | None) -> str:
        """格式化调用链为文本。"""
        if not call_chain:
            return ""
        _parts = []
        if call_chain.get("called_methods"):
            _parts.append(f"该方法调用了: {call_chain['called_methods']}")
        if call_chain.get("called_by"):
            _parts.append(f"被以下方法调用: {call_chain['called_by']}")
        if not _parts:
            return ""
        return "调用链上下文:\n" + "\n".join(_parts) + "\n\n"

    def _read_callers_code(self, call_chain: dict[str, Any] | None,
                           self_inspector) -> str:
        """读取调用方代码，供 LLM 同步修改。"""
        if not call_chain or not self_inspector:
            return ""
        _called_by = call_chain.get("called_by", [])
        if not _called_by:
            return ""
        _snippets = []
        for _caller_key in _called_by[:3]:  # 最多读3个调用方
            if "." in _caller_key:
                _organ, _method = _caller_key.split(".", 1)
                try:
                    _detail = self_inspector.get_method_body(_organ, _method)
                    if _detail:
                        _body = _detail.get("body", "")
                        _snippets.append(f"调用方 {_caller_key} 代码:\n```python\n{_body[:1500]}\n```\n")
                except Exception as e:
                    self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        if not _snippets:
            return ""
        return "调用方代码（需同步修改）:\n" + "\n".join(_snippets) + "\n"

    def _parse_multi_file_patch(self, response: str | None,
                                issue: dict[str, Any]) -> list[dict[str, Any]]:
        """解析 LLM 输出的 JSON 补丁包。"""
        if not response:
            return []
        try:
            # 清理可能的 markdown 代码块标记
            _clean = response.strip()
            if _clean.startswith("```"):
                _clean = _clean.strip("`")
                _clean = _clean.removeprefix("json")
            _data = json.loads(_clean)
            _patches = _data.get("patches", [])
            _result = []
            for _p in _patches:
                if not _p.get("file") or not _p.get("modified_code"):
                    continue
                if not _p.get("original_code"):
                    continue
                # ★P3 最小化改动检查
                _change_ratio = self._calc_change_ratio(_p.get("original_code", ""), _p.get("modified_code", ""))
                # ★P3 零冲突检查：original_code 在文件中唯一存在
                _unique = self._check_original_unique(_p.get("file", ""), _p.get("original_code", ""))
                _patch = {
                    "id": f"patch_multi_{int(time.time())}_{hash(_p['original_code']) & 0xFFFF:04x}",
                    "file": _p["file"],
                    "method": _p.get("method", ""),
                    "issue_type": issue.get("type", "unknown"),
                    "risk_level": "中等",
                    "description": issue.get("description", ""),
                    "original_code": _p["original_code"],
                    "modified_code": _p["modified_code"],
                    "diff_summary": f"跨文件重构: {_p.get('method', '')}",
                    "trust_score": 50,  # 跨文件补丁信任分保守
                    "confidence": "medium",
                    "change_ratio": round(_change_ratio, 3),  # ★P3 改动比例（0-1，越低越精准）
                    "original_unique": _unique,  # ★P3 original_code 是否在文件中唯一
                    "repair_source": "llm_multi_file",
                    "generated_at": time.time(),
                    "status": "pending",
                    "verification": None,
                    "applied": False,
                    "applied_at": 0,
                }
                _result.append(_patch)
            return _result
        except (json.JSONDecodeError, Exception) as e:
            # 修复：用框架日志系统代替print
            self._log(LogLevel.DEBUG, f"多文件补丁JSON解析失败: {type(e).__name__}: {e}")
            return []

    # ========== 阶段B核心2：多步规划 ==========

    def plan_multi_step(self, issue: dict[str, Any],
                        call_chain: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        让 LLM 把复杂任务拆成多步计划，每步含目标+验证标准。

        返回:
            [{"step": 1, "goal": str, "verification": str, "depends_on": [0...]}, ...]
        """
        _call_ctx = self._format_call_chain(call_chain)
        _prompt = (
            f"你是曈曈的架构规划助手。请把下面的复杂重构任务拆成「多步计划」。\n\n"
            f"问题类型: {issue.get('type', '')}\n"
            f"问题描述: {issue.get('description', '')}\n"
            f"{_call_ctx}"
            f"要求：\n"
            f"1. 输出严格 JSON：{{\"steps\": ["
            f"{{\"step\": 序号, \"goal\": \"本步目标\", "
            f"\"verification\": \"本步验证标准\", \"depends_on\": [依赖的前序步骤序号]}}]}}。\n"
            f"2. 每步必须是可独立验证的最小改动。\n"
            f"3. 步骤数不超过5。\n"
            f"4. 只输出 JSON，不要输出解释。\n"
        )
        _response = self._call_llm(
            system="你是曈曈的架构规划引擎，输出严格 JSON 结构。",
            prompt=_prompt,
            model=None,
            max_tokens=2048,
        )
        return self._parse_multi_step_plan(_response)

    def _parse_multi_step_plan(self, response: str | None) -> list[dict[str, Any]]:
        """解析 LLM 输出的多步计划 JSON。"""
        if not response:
            return []
        try:
            _clean = response.strip()
            if _clean.startswith("```"):
                _clean = _clean.strip("`")
                _clean = _clean.removeprefix("json")
            _data = json.loads(_clean)
            _steps = _data.get("steps", [])
            return [s for s in _steps if s.get("goal")]
        except Exception as e:
            # 修复：用框架日志系统代替print
            self._log(LogLevel.DEBUG, f"多步计划JSON解析失败: {type(e).__name__}: {e}")
            return []


    # ========== ★P3 最小化改动 + 零冲突检查 ==========

    @staticmethod
    def _calc_change_ratio(original: str, modified: str) -> float:
        """计算改动比例（modified 中与 original 不同的行数占比）。

        返回 0-1，0 表示无改动，1 表示完全重写。
        用于约束 LLM 生成最小化改动，避免整体重写。
        """
        if not original or not modified:
            return 1.0
        _orig_lines = set(original.splitlines())
        _mod_lines = modified.splitlines()
        if not _mod_lines:
            return 1.0
        _changed = sum(1 for l in _mod_lines if l not in _orig_lines)
        return _changed / len(_mod_lines)

    def _check_original_unique(self, file_path: str, original_code: str) -> bool:
        """检查 original_code 在目标文件中是否唯一存在（零冲突基础）。

        如果 original_code 在文件中出现多次，补丁应用时可能定位错误，
        导致修改错误的代码块。
        """
        if not file_path or not original_code:
            return False
        _abs = os.path.join(self._project_root, file_path)
        if not os.path.exists(_abs):
            return False
        try:
            with open(_abs, encoding='utf-8', errors='ignore') as _f:
                _content = _f.read()
        except Exception:
            return False
        # 统计出现次数（取 original_code 的前 50 字符作为匹配键，避免完全匹配失败）
        _key = original_code.strip()[:50]
        if not _key:
            return False
        _count = _content.count(_key)
        return _count == 1


# ========== 便捷函数 ==========

def get_llm_evolution_engine(project_root: str) -> LLMEvolutionEngine:
    """获取 LLMEvolutionEngine 实例。"""
    return LLMEvolutionEngine(project_root)


if __name__ == "__main__":
    # 自测：无 API key 时优雅降级
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _engine = LLMEvolutionEngine(_root)
    _issue = {"type": "refactor", "description": "测试多步规划"}
    _result = _engine.plan_multi_step(_issue, None)
    print(f"无 API key 时多步规划返回: {_result} (应为空列表，优雅降级)")
    _patches = _engine.generate_multi_file_patch(_issue, "def x():\n    pass\n", "organs/test.py", "x", None, None)
    print(f"无 API key 时跨文件补丁返回: {_patches} (应为空列表，优雅降级)")
