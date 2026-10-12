# -*- coding: utf-8 -*-
"""192刀2 隔离单测：视觉能力闸旁路（请求携带 image_b64 → 本地视觉档，avoid 面一字不动）。

纪律：铁律151 隔离环境；mock _do_chat / _call_remote_api / config 远程可用态。
"""
from unittest.mock import patch

from organs.body.PulseLung import PulseLung, VISION_MODEL_NAME


def _mk_lung():
    lung = PulseLung.__new__(PulseLung)
    lung._log = lambda *a, **k: None
    lung.organ_name = "肺"
    lung._selection_count = 0
    lung._dialog_history = []
    lung._call_success_count = 0
    lung._emit = lambda *a, **k: None
    lung.append_dialog_turn = lambda *a, **k: None
    lung._is_duplicate_request = lambda *a, **k: False
    lung._request_dedup = lambda *a, **k: None
    lung._m49_mark_call_source = lambda *a, **k: None
    lung._get_local_models = lambda *a, **k: [
        "qwen2.5:7b", "llava:7b-v1.6-mistral-q3_K_M"
    ]
    lung._assess_remote_reply_quality = lambda *a, **k: {
        "passed": True, "score": 4, "level": "high", "issues": []
    }
    lung._feedback_quality_result = lambda *a, **k: None
    return lung


def _with_remote(flag: bool, key: str = "x"):
    import config as _cfg
    saved = getattr(_cfg, "REMOTE_API_CONFIG", None)
    if flag:
        _cfg.REMOTE_API_CONFIG = {
            "enabled": True, "api_key": key,
            "default_model": "deepseek-v4-flash",
        }
    return _cfg, saved


def _restore(cfg, saved):
    if saved is None:
        if hasattr(cfg, "REMOTE_API_CONFIG"):
            del cfg.REMOTE_API_CONFIG
    else:
        cfg.REMOTE_API_CONFIG = saved


@patch.object(PulseLung, "_do_chat")
def test_fallback_vision_bypass_uses_vision_model(mock_dochat):
    """有图：_fallback_to_local 直接选 VISION_MODEL_NAME，并把 image_b64 透传给 _do_chat。"""
    mock_dochat.return_value = "图描述"
    lung = _mk_lung()
    payload = {"task_type": "chat", "image_b64": "B64IMG", "correlation_id": ""}
    res = lung._fallback_to_local(
        ["qwen2.5:7b", "llava:7b-v1.6-mistral-q3_K_M"], "描述图", "小林", payload)
    args, kwargs = mock_dochat.call_args
    assert args[0] == VISION_MODEL_NAME
    assert kwargs.get("image_b64") == "B64IMG"
    assert res["model"] == VISION_MODEL_NAME


@patch.object(PulseLung, "_do_chat")
def test_fallback_no_image_uses_normal_selection(mock_dochat):
    """无图：_fallback_to_local 走原 _pick_local_model（avoid llava），image_b64=None。"""
    mock_dochat.return_value = "回复"
    lung = _mk_lung()
    payload = {"task_type": "chat", "correlation_id": ""}  # 无 image_b64
    res = lung._fallback_to_local(
        ["qwen2.5:7b", "llava:7b-v1.6-mistral-q3_K_M"], "你好", "小林", payload)
    args, kwargs = mock_dochat.call_args
    assert args[0] != VISION_MODEL_NAME
    # _pick_local_model 应避开 llava（avoid_keywords）选 qwen
    assert args[0] == "qwen2.5:7b"
    assert kwargs.get("image_b64") is None
    assert res["model"] == "qwen2.5:7b"


@patch.object(PulseLung, "_fallback_to_local")
@patch.object(PulseLung, "_call_remote_api")
def test_on_select_vision_bypasses_remote(mock_remote, mock_fallback):
    """有图 + 远程可用：_on_select_model 跳过远程优先，直接走本地视觉档。"""
    cfg, saved = _with_remote(True)
    try:
        lung = _mk_lung()
        mock_fallback.return_value = {"status": "replied", "model": VISION_MODEL_NAME}
        mock_remote.return_value = "remote-reply"
        payload = {
            "prompt": "描述图", "user_name": "小林", "is_dialogue": True,
            "image_b64": "B64IMG",
        }
        res = lung._on_select_model(payload)
        assert mock_fallback.called
        assert not mock_remote.called  # 关键：视觉档不走远程
        assert res["model"] == VISION_MODEL_NAME
    finally:
        _restore(cfg, saved)


@patch.object(PulseLung, "_fallback_to_local")
@patch.object(PulseLung, "_call_remote_api")
def test_on_select_no_image_uses_remote(mock_remote, mock_fallback):
    """无图 + 远程可用：_on_select_model 走原远程选路（avoid 面一字不动）。"""
    cfg, saved = _with_remote(True)
    try:
        lung = _mk_lung()
        mock_remote.return_value = "remote-reply"
        payload = {
            "prompt": "你好", "user_name": "小林", "is_dialogue": True,
        }
        res = lung._on_select_model(payload)
        assert mock_remote.called
        assert not mock_fallback.called  # 远程成功，不回退本地
        assert res["model"] == "deepseek-v4-flash"
    finally:
        _restore(cfg, saved)
