# -*- coding: utf-8 -*-
"""193批刀2隔离单测：llava 视觉响应以 system_internal 入 recorder。

覆盖：
- 视觉请求（image_b64）响应 → record_call 以 ORIGIN_SYSTEM_INTERNAL 入录；
- 非视觉请求 → record_call 不被调用（零行为变化）；
- 返回值正确透传。
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.getcwd())

from nucleus.llm.call_recorder import ORIGIN_SYSTEM_INTERNAL


def _mk_lung():
    from organs.body.PulseLung import PulseLung
    lung = PulseLung.__new__(PulseLung)
    lung.ollama_base_url = "http://127.0.0.1:11434"
    lung.ollama_timeout = 30
    lung._log = lambda *a, **k: None
    return lung


def _fake_http_ok(response_text):
    def _f(url, method=None, data=None, headers=None, timeout=None):
        return (True, {"response": response_text})
    return _f


def test_vision_response_recorded_as_system_internal():
    lung = _mk_lung()
    captured = {}

    def _fake_record(**kwargs):
        captured.update(kwargs)
        return "tid"

    with mock.patch("nucleus.ssrf_guard.safe_http_json",
                    side_effect=_fake_http_ok("图里是一只猫")), mock.patch("nucleus.llm.call_recorder.record_call",
                    side_effect=_fake_record) as rec:
        out = lung._call_ollama("llava:7b-v1.6-mistral-q3_K_M",
                                "描述这张图", image_b64="ABC==")
    assert out == "图里是一只猫"
    assert rec.call_count == 1
    assert captured.get("origin") == ORIGIN_SYSTEM_INTERNAL
    assert captured.get("response") == "图里是一只猫"
    assert captured.get("model") == "llava:7b-v1.6-mistral-q3_K_M"


def test_non_vision_request_not_recorded():
    lung = _mk_lung()
    with mock.patch("nucleus.ssrf_guard.safe_http_json",
                    side_effect=_fake_http_ok("你好")), mock.patch("nucleus.llm.call_recorder.record_call") as rec:
        out = lung._call_ollama("qwen2.5:7b", "你好")
    assert out == "你好"
    assert rec.call_count == 0


def test_vision_empty_reply_not_recorded():
    lung = _mk_lung()
    with mock.patch("nucleus.ssrf_guard.safe_http_json",
                    side_effect=_fake_http_ok("   ")), mock.patch("nucleus.llm.call_recorder.record_call") as rec:
        out = lung._call_ollama("llava:7b", "x", image_b64="IMG")
    assert out == ""
    assert rec.call_count == 0
