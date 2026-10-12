# -*- coding: utf-8 -*-
"""192刀1 隔离单测：_call_ollama 视觉扩展（image_b64 → 顶层 images 裸 base64，无图零行为）。

纪律：铁律151 隔离环境运行；mock safe_http_json 取 payload；不依赖真实 Ollama 服务。
"""
import json
from unittest.mock import patch

from organs.body.PulseLung import PulseLung


def _mk_lung():
    lung = PulseLung.__new__(PulseLung)
    lung.ollama_base_url = "http://127.0.0.1:11434"
    lung.ollama_timeout = 5
    lung._log = lambda *a, **k: None
    return lung


@patch("nucleus.ssrf_guard.safe_http_json")
def test_image_b64_injected_as_bare_base64(mock_http):
    """有图：payload 顶层注入 images 键，裸 base64 无 data-URL 前缀。"""
    captured = {}

    def _fake(url, method=None, data=None, headers=None, timeout=None):
        captured["data"] = data
        return (True, {"response": "这是一张猫的图片"})

    mock_http.side_effect = _fake
    lung = _mk_lung()
    reply = lung._call_ollama("llava:7b-v1.6-mistral-q3_K_M", "描述这张图", image_b64="BASE64RAW==")
    assert reply == "这是一张猫的图片"
    payload = json.loads(captured["data"].decode("utf-8"))
    assert payload["images"] == ["BASE64RAW=="]
    # 裸 base64，不带 data-URL 前缀（Ollama 视觉约定）
    assert not payload["images"][0].startswith("data:image")


@patch("nucleus.ssrf_guard.safe_http_json")
def test_no_image_zero_behavior(mock_http):
    """无图（不传 image_b64）：payload 不含 images 键，零行为变化。"""
    captured = {}

    def _fake(url, method=None, data=None, headers=None, timeout=None):
        captured["data"] = data
        return (True, {"response": "你好"})

    mock_http.side_effect = _fake
    lung = _mk_lung()
    reply = lung._call_ollama("qwen2.5:7b", "你好")
    assert reply == "你好"
    payload = json.loads(captured["data"].decode("utf-8"))
    assert "images" not in payload


@patch("nucleus.ssrf_guard.safe_http_json")
def test_empty_image_b64_treated_as_no_image(mock_http):
    """image_b64 空串：回落无图分支，不注入 images 键。"""
    captured = {}

    def _fake(url, method=None, data=None, headers=None, timeout=None):
        captured["data"] = data
        return (True, {"response": "你好"})

    mock_http.side_effect = _fake
    lung = _mk_lung()
    lung._call_ollama("qwen2.5:7b", "你好", image_b64="")
    payload = json.loads(captured["data"].decode("utf-8"))
    assert "images" not in payload
