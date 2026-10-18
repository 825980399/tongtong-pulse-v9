# -*- coding: utf-8 -*-
"""channel_client.py —— 子代理侧渠道「单次调用」客户端（275批刀2 · 甲′路线）

★为什么存在
  执行侧宿主（代码沙箱）早已在跑，runner 也早把 model/channel/source 写进了 input，
  缺的只是 worker 伸手接过来这一步。本模块就是那只"手"。

★设计红线（与烛微274前置 / 275任务书一致）
  1. **肺不动**：不修改 PulseLung 的任何调用路径（规避 270 Q1 那类核心链路改动）。
  2. **同源不复制**：绝不重写一套渠道逻辑。路由所需的每个关键件都复用主进程既有组件：
       · 渠道池       → ``config.get_active_channels()``（与主进程同一函数、同一 priority 语义）
       · SSRF 守卫    → ``nucleus.ssrf_guard.is_safe_http_url``（fail-closed，与肺一致）
       · 适配器       → ``nucleus.llm.adapter_registry.get_adapter_registry()``（同一注册表）
       · 全局密钥兜底 → ``config.REMOTE_API_CONFIG.api_key``（与肺 :1014-1021 同款兜底）
       · 健康度       → ``nucleus.llm.channel_health``（同源上报，不另立门户）
  3. **只做单次**：本模块不实现轮询/重试/熔断。多渠道路由与失败切换仍属主进程职责；
     子代理侧的成本与失败控制由 runner 的三熔断（275批刀5）承担，职责不重叠。

★零行为变化：新增文件；worker 仅在 ``op="llm"`` 时调用，其余 op（丁步确定性任务）路径完全不变。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from nucleus._silent_except import silent_exc

_L = logging.getLogger("channel_client")

_ENV_KEY = "SUBAGENT_API_KEY"  # 刀3：密钥走进程环境变量，不落盘


def _cfg():
    import config as _c
    return _c


def resolve_api_key(channel: dict | None) -> str:
    """按优先级取密钥：环境变量 > channel 自带 > 全局 REMOTE_API_CONFIG 兜底。

    刀3（安全级）：input_file 里**不写** api_key，密钥经子进程环境变量传递，
    worker 再由此函数取回，凭证全程不落盘。
    """
    _env = os.environ.get(_ENV_KEY, "")
    if _env:
        return _env
    if isinstance(channel, dict) and channel.get("api_key"):
        return str(channel["api_key"])
    try:
        return str((_cfg().REMOTE_API_CONFIG or {}).get("api_key", "") or "")
    except Exception as exc:
        silent_exc(exc, where="channel_client::call_channel")
        _L.debug("[channel_client] 全局密钥兜底失败: %s", type(exc).__name__)
        return ""


def pick_channel(channel: dict | None = None, model: str | None = None,
                 cfg=None) -> dict | None:
    """选一个渠道：显式传入 > 按 model 匹配 > 渠道池首个可用（同源 priority 升序）。"""
    if isinstance(channel, dict) and channel.get("api_url"):
        return dict(channel)
    cfg = cfg or _cfg()
    try:
        _pool = cfg.get_active_channels()
    except Exception as exc:
        silent_exc(exc, where="channel_client::call_channel")
        _L.warning("[channel_client] 取渠道池失败: %s: %s", type(exc).__name__, exc)
        return None
    if not _pool:
        return None
    if model:
        for _ch in _pool:
            if _ch.get("model") == model:
                return _ch
    return _pool[0]


def call_channel(channel: dict, prompt: str, *, api_key: str | None = None,
                 timeout: float | None = None, max_tokens: int = 1024,
                 system: str = "") -> dict:
    """单次渠道调用（发一次 HTTP，不做轮询/重试）。

    Returns:
        {"ok": bool, "text": str|None, "usage": dict, "channel": str,
         "model": str, "error": str|None}
    """
    _out = {"ok": False, "text": None, "usage": {}, "channel": "",
            "model": "", "error": None}
    if not isinstance(channel, dict):
        _out["error"] = "channel 非 dict"
        return _out

    _name = str(channel.get("name", "?"))
    _url = str(channel.get("api_url", "") or "")
    _model = str(channel.get("model", "") or "")
    _out["channel"], _out["model"] = _name, _model
    if not _url or not _model:
        _out["error"] = "渠道配置不完整（缺 api_url/model）"
        return _out

    _key = api_key if api_key is not None else resolve_api_key(channel)
    if not _key:
        _out["error"] = "无可用密钥（环境变量/channel/全局兜底均为空）"
        return _out

    # SSRF 防护：与主进程同源、fail-closed（守卫自身异常 ⇒ 拒绝，不放行）
    try:
        from nucleus.ssrf_guard import is_safe_http_url
        _allowed, _reason = is_safe_http_url(_url)
    except Exception as exc:
        silent_exc(exc, where="channel_client::call_channel")
        _out["error"] = "SSRF守卫异常(拒绝请求): %s" % type(exc).__name__
        _L.warning("[channel_client] %s", _out["error"])
        return _out
    if not _allowed:
        _out["error"] = "被SSRF防护拦截: %s" % _reason
        _L.warning("[channel_client] 渠道 %s %s", _name, _out["error"])
        return _out

    # 适配器：与主进程同一注册表（未知类型回落 openai_compatible）
    try:
        from nucleus.llm.adapter_registry import get_adapter_registry
        _adapter = get_adapter_registry().get(
            channel.get("adapter", "openai_compatible"))
    except Exception as exc:
        silent_exc(exc, where="channel_client::call_channel")
        _out["error"] = "适配器不可用: %s" % type(exc).__name__
        _L.warning("[channel_client] %s", _out["error"])
        return _out

    _messages = []
    if system:
        _messages.append({"role": "system", "content": system})
    _messages.append({"role": "user", "content": prompt})

    try:
        _body = _adapter.build_request(_model, _messages, max_tokens=max_tokens)
    except Exception as _se:
        silent_exc(_se, where="channel_client::call_channel")
        try:
            _body = _adapter.build_request(_model, _messages)
        except Exception as exc:
            silent_exc(exc, where="channel_client::call_channel")
            _out["error"] = "build_request 失败: %s" % type(exc).__name__
            return _out
    except Exception as exc:
        silent_exc(exc, where="channel_client::call_channel")
        _out["error"] = "build_request 失败: %s" % type(exc).__name__
        return _out

    _payload = json.dumps(_body, ensure_ascii=False).encode("utf-8")
    _req = urllib.request.Request(
        _url, data=_payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % _key},
    )
    try:
        with urllib.request.urlopen(_req, timeout=timeout or 60) as _resp:
            _raw = _resp.read().decode("utf-8", errors="replace")
        _data = json.loads(_raw)
    except urllib.error.HTTPError as exc:
        silent_exc(exc, where="channel_client::call_channel")
        _out["error"] = "HTTP %s" % exc.code
        _report_health(_name, False, 0.0)
        return _out
    except Exception as exc:
        silent_exc(exc, where="channel_client::call_channel")
        _out["error"] = "%s: %s" % (type(exc).__name__, exc)
        _report_health(_name, False, 0.0)
        return _out

    try:
        _text = _adapter.parse_response(_data)
    except Exception as exc:
        silent_exc(exc, where="channel_client::call_channel")
        _out["error"] = "parse_response 失败: %s" % type(exc).__name__
        _report_health(_name, False, 0.0)
        return _out
    try:
        _usage = _adapter.extract_usage(_data) or {}
    except Exception as _se:
        silent_exc(_se, where="channel_client::call_channel")
        _usage = {}

    _report_health(_name, bool(_text), 0.0)
    if not _text:
        _out["error"] = "响应无有效内容"
        return _out
    _out.update({"ok": True, "text": _text, "usage": _usage})
    return _out


def _report_health(name: str, ok: bool, latency: float) -> None:
    """健康度上报（同源 channel_health；失败不影响主流程）。"""
    try:
        from nucleus.llm.channel_health import get_channel_health
        get_channel_health().record(name, ok, latency)
    except Exception as exc:
        silent_exc(exc, where="channel_client::call_channel")
        _L.debug("[channel_client] 健康度上报跳过: %s", type(exc).__name__)
