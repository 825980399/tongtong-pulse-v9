# -*- coding: utf-8 -*-
"""第86批 T-86a 门控测试：LLM 补丁路径「零产出」根因修复（推理模型 token 预算）。

背景（已实测复现，见 tmp/m86_t0_llm5.txt）：
    REMOTE_API_CONFIG 指向的 deepseek-v4-flash 属推理模型，响应先产出
    reasoning_content 再产出 content。原 max_tokens=1500 被推理解析吃光后
    finish_reason=length、content 为空串 → ``if not answer: return None``，
    整条 LLM 补丁通道恒零产出，且生产日志中无任何线索（裸 except 静默吞异常）。

本文件断言三件事（全部离线、不触网）：
    1. LLM 修复通道被真实触发（至少进入 LLM 调用），且请求为推理模型预留了
       足够 token 预算与超时余量；
    2. 模型只回 reasoning_content、content 为空时，必须落 WARNING 留痕；
    3. 链路内部异常不再是静默 except，必须落 WARNING 后降级返回 None。
"""
from __future__ import annotations

import contextlib
import json
import unittest
from unittest import mock

import nucleus.reasoning.SafeEvolutionExecutor as _mod
from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor

_GOOD_CONTENT = (
    "```python\n"
    "def _demo_fixed(value: int = 0) -> int:\n"
    "    return value + 1\n"
    "```\n"
    "关键改动点：把可变默认参数替换为 None 哨兵后再赋值，并补充 None 分支处理，"
    "避免跨调用共享同一可变对象。这一段文字用于让回答长度超过 100 字符，"
    "从而跳过追问分支，保持测试断言聚焦在首次请求的请求体上。"
)

_ISSUE = {
    "type": "mutable_default_arg",
    "file": "nucleus/demo.py",
    "method": "demo",
    "line": 42,
    "severity": "medium",
    "message": "Traceback (most recent call last): ValueError: boom",
}
_SNIPPET = "def demo(value=[]):\n    value.append(1)\n    return value\n"


@contextlib.contextmanager
def _no_rate_limit():
    """把 api_rate_limited 替换为零开销上下文（避免跨测试的令牌桶干扰）。"""
    with mock.patch.object(_mod, "api_rate_limited",
                           lambda **kw: contextlib.nullcontext()):
        yield


def _capture_call(response, captured):
    """构造 safe_http_json 替身：记录请求体，返回固定响应。"""
    def _fake(url, method="POST", data=None, headers=None, timeout=None):
        body = json.loads(data.decode("utf-8")) if data else {}
        captured.append({"body": body, "timeout": timeout,
                         "url": url, "method": method})
        return True, response
    return _fake


class TestLLMRepairPathM86(unittest.TestCase):
    """T-86a：LLM 修复通道必须被触发且请求预算足够（推理模型）。"""

    def setUp(self):
        self._ex = SafeEvolutionExecutor()
        # ★铁律91：禁用嵌套 mock.patch.object 同一属性——外层 with 退出时会先把
        #   ``_mod.config`` 还原成真实模块，内层 patch 的 stop() 再把它还原成
        #   **外层留下的 AutoMock**，于是本测试结束后全局 ``_mod.config`` 残留
        #   MagicMock，污染后续测试（实测曾使 test_evolution_backup_filter_m60
        #   的 stdlib 判定退化为 external）。改用 patch.dict 只改配置内容。
        self._cfg_ctx = mock.patch.dict(
            _mod.config.REMOTE_API_CONFIG,
            {"api_url": "https://api.example.com/v1/chat/completions",
             "api_key": "sk-test-m86-0000000000000000000000"})
        self._cfg_ctx.start()
        self.addCleanup(self._cfg_ctx.stop)

    def test_01_llm_path_entered_with_reasoning_budget(self):
        """先红后绿核心断言：进入了 LLM 调用，且 token 预算/超时为推理模型预留余量。"""
        captured = []
        _resp = {"choices": [{"finish_reason": "stop",
                              "message": {"content": _GOOD_CONTENT}}],
                 "usage": {"completion_tokens": 900}}
        with _no_rate_limit(), \
                mock.patch("nucleus.ssrf_guard.safe_http_json",
                           _capture_call(_resp, captured)):
            _ans = self._ex._call_llm_for_repair(_ISSUE, _SNIPPET, "")

        self.assertEqual(len(captured), 1,
                         "LLM 修复通道未被触发（未进入 LLM 调用）")
        self.assertIsNotNone(_ans, "LLM 返回了完整代码块，应原样透传")
        self.assertIn("```python", _ans)
        _body = captured[0]["body"]
        _mt = int(_body.get("max_tokens") or 0)
        self.assertGreaterEqual(
            _mt, 4096,
            f"推理模型 token 预算不足（max_tokens={_mt}）："
            f"reasoning_content 会吃光预算导致 content 恒为空")
        self.assertGreaterEqual(
            int(captured[0]["timeout"] or 0), 120,
            f"修复调用超时过紧（timeout={captured[0]['timeout']}s）："
            f"推理模型单次约 27s，易被读超时打断")

    def test_02_empty_content_from_reasoning_model_is_logged(self):
        """模型只回 reasoning_content 时必须落 WARNING（原为静默 return ""）。"""
        captured = []
        _resp = {"choices": [{"finish_reason": "length",
                              "message": {"content": "",
                                          "reasoning_content": "推理解析" * 500}}],
                 "usage": {"completion_tokens": 1500}}
        with _no_rate_limit(), \
                mock.patch("nucleus.ssrf_guard.safe_http_json",
                           _capture_call(_resp, captured)):
            with self.assertLogs(_mod._module_logger, level="WARNING") as _cm:
                _ans = self._ex._call_llm_for_repair(_ISSUE, _SNIPPET, "")

        self.assertIsNone(_ans, "content 为空应降级返回 None")
        _joined = "\n".join(_cm.output)
        self.assertIn("content 为空", _joined,
                      "推理预算被耗尽导致 content 为空时必须留痕，否则再次退化为静默零产出")
        self.assertIn("finish_reason=length", _joined)

    def test_03_internal_exception_is_logged_not_swallowed(self):
        """链路内部异常必须留痕后降级（原为裸 except: return None）。"""
        with _no_rate_limit(), \
                mock.patch.object(self._ex, "_deep_root_cause_analysis",
                                  side_effect=RuntimeError("boom-m86")):
            with self.assertLogs(_mod._module_logger, level="WARNING") as _cm:
                _ans = self._ex._call_llm_for_repair(_ISSUE, _SNIPPET, "")

        self.assertIsNone(_ans)
        self.assertIn("boom-m86", "\n".join(_cm.output),
                      "内部异常被静默吞掉，日志无任何线索")


if __name__ == "__main__":
    unittest.main()
