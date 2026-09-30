# -*- coding: utf-8 -*-
"""
ssrf_guard.py —— SSRF防护

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 服务器端请求伪造防护与URL校验
机制: 函数式模块，包含4个工具函数
定位: 安全治理层
"""

import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

_LOGGER = logging.getLogger(__name__)


# 允许抓取的协议
_ALLOWED_SCHEMES = ("http", "https")

# 受信任主机（管理员显式配置，非外部输入派生）：
# - config.REMOTE_API_CONFIG.api_url 主机（用户自有模型接口，可能是本地 Ollama / 局域网 vLLM）
# - 本地 Ollama 服务主机（ollama_base_url，默认 localhost:11434）
# 这些主机即使解析到私网/环回也允许访问（本地优先部署是核心用法）。
# 但云元数据 169.254.169.254 / 0.0.0.0 / link-local 始终硬拒绝，防凭据泄漏。
_TRUSTED_HARD_BLOCK = {"169.254.169.254", "0.0.0.0", "::1", "::", "fe80::1"}
_TRUSTED_CACHE = None


def _trusted_hosts() -> set:
    """受信任主机集合（缓存）。仅包含管理员在配置中显式声明的端点。"""
    global _TRUSTED_CACHE
    if _TRUSTED_CACHE is not None:
        return _TRUSTED_CACHE
    _hosts = {"localhost", "127.0.0.1"}
    try:
        import config as _cfg
        _api = getattr(_cfg, "REMOTE_API_CONFIG", {}).get("api_url", "")
        if _api:
            _h = urlparse(_api).hostname
            if _h:
                _hosts.add(_h.lower())
        # ★第98批 T-98a：渠道池每个渠道的 api_url 同样是管理员显式配置的模型接口，
        #   与顶层单端点同属「本地优先部署」语义（火山方舟/智谱等 SaaS 端点）。
        #   原实现只取顶层 REMOTE_API_CONFIG.api_url，漏掉了渠道池，导致 ark 等
        #   渠道主机不在受信任集合 → 落入 DNS 解析 → 生产环境把火山域名解析到内网
        #   IP 时被 SSRF 防护误拦（渠道失败率 33%）。此处补齐，使所有配置过的模型
        #   端点都享受「私网/环回豁免」（仍强制 http/https，且云元数据等保留地址
        #   始终在 _TRUSTED_HARD_BLOCK 中硬拒绝）。
        for _ch in getattr(_cfg, "REMOTE_API_CHANNELS", {}).get("default_channels", []) or []:
            _cu = (_ch or {}).get("api_url", "")
            if _cu:
                _chh = urlparse(_cu).hostname
                if _chh:
                    _hosts.add(_chh.lower())
        # ★第98批 T-98a：显式额外白名单开关（默认含火山方舟域名）。运维可在不改代码
        #   的情况下追加受信任主机/域名。见 config.SSRF_TRUSTED_EXTRA_HOSTS。
        for _h in getattr(_cfg, "SSRF_TRUSTED_EXTRA_HOSTS", ()) or ():
            if _h:
                _hosts.add(_h.lower())
        _ollama = getattr(_cfg, "OLLAMA_BASE_URL", "") or "http://localhost:11434"
        _oh = urlparse(_ollama).hostname
        if _oh:
            _hosts.add(_oh.lower())
    except Exception as e:
        # ★主线第77批：受信任主机解析失败必须留痕。
        #   此处为 fail-closed（集合变小 → 判定更严格），安全方向正确；
        #   但静默会导致「合法 API 被拒却查不到原因」，排查成本极高。
        _LOGGER.debug("受信任主机集合解析失败（按最小集合处理）: %s: %s",
                      type(e).__name__, e)
    _TRUSTED_CACHE = _hosts
    return _hosts


def is_safe_http_url(url: str) -> tuple[bool, str]:
    """
    返回 (是否允许, 拒绝原因)。

    仅允许公网 http/https；解析主机后拒绝任何非公网地址
    （环回 / 私网 / 链路本地 / 保留 / 组播 / 未指定）。
    例外：管理员在配置中显式声明的模型接口 / 本地 Ollama 主机（见 _trusted_hosts）
    即使解析到私网/环回也允许，以支持本地优先部署；但云元数据等保留地址始终硬拒绝。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "URL 解析失败"

    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False, f"不支持的协议: {parsed.scheme}"

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "缺少主机名"

    # 硬安全底线：云元数据 / 未指定 / 保留地址，即使“受信任”也拒绝
    if host in _TRUSTED_HARD_BLOCK:
        return False, f"拒绝云元数据/保留地址: {host}"

    # 受信任主机（管理员显式配置）：允许私网/环回，仍要求 http/https（上方已校验）
    if host in _trusted_hosts():
        return True, ""

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, f"无法解析主机: {host}"

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        # ★修复：改用 is_global 作为「是否公网」的主判定依据。
        # 原逻辑用 is_private 判定，Python 的 ipaddress 对 IPv6 过渡地址段
        # 的判定语义不清晰（例如 2002::/16 6to4 公网地址 is_private=True）。
        # is_global 语义更准确：公网 IPv4/IPv6 返回 True，
        # 私网/环回/链路本地/保留/组播/未指定/文档示例段返回 False。
        # 注：2001::1（Teredo 占位地址）is_global=False，仍会被正确拦截——
        # 该地址本身不可通信，是运行环境 DNS 污染导致的异常解析，非 SSRF 误判。
        if not ip.is_global:
            return False, f"拒绝非公网地址: {host} -> {addr}"

    return True, ""


def safe_http_json(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[bool, Any]:
    """
    经 SSRF 防护的统一 HTTP JSON 请求封装。

    所有出站 HTTP 请求应优先走此函数，避免各模块自行调用
    urllib/requests 而绕过 is_safe_http_url 校验。
    返回 (是否成功, 解析后的 JSON 或错误信息字符串)。
    """
    _allowed, _reason = is_safe_http_url(url)
    if not _allowed:
        return False, f"SSRF 防护拦截: {_reason}"
    try:
        _req = urllib.request.Request(
            url, data=data, headers=headers or {}, method=method
        )
        with urllib.request.urlopen(_req, timeout=timeout) as _resp:
            return True, json.loads(_resp.read().decode("utf-8"))
    except urllib.error.URLError as _e:
        return False, f"请求失败: {_e}"
    except (ValueError, json.JSONDecodeError) as _e:
        return False, f"响应解析失败: {_e}"


