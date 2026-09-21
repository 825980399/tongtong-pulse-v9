# -*- coding: utf-8 -*-
"""第98批 T-98a（P0）门控测试：SSRF 防护受信任主机补齐「渠道池 + 显式白名单」。

根因：原 _trusted_hosts() 只取顶层 REMOTE_API_CONFIG.api_url，漏掉渠道池
（REMOTE_API_CHANNELS.default_channels 的 api_url），导致火山方舟域名
ark.cn-beijing.volcos.com 不在受信任集合 → 生产把该域名解析到内网 IP 时被
SSRF 防护误拦（火山渠道失败率 33%）。

本测试用 monkeypatch 复现「域名解析到内网 192.168.50.86」场景，证明：
- RED（修复前）：ark 不在受信任集合 → 走 DNS → 拒绝非公网 → (False, ...)
- GREEN（修复后）：ark 在受信任集合（渠道池 + SSRF_TRUSTED_EXTRA_HOSTS）→ 直接放行 → (True, '')
- 安全底线不破：云元数据 169.254.169.254 始终硬拒绝；公网地址仍放行。
"""
import os
import sys
import socket

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nucleus.ssrf_guard as sg  # noqa: E402


def _fake_getaddrinfo(internal_ip):
    def _ga(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (internal_ip, 0))]
    return _ga


class TestSsrfWhitelistM98:
    def test_ark_trusted_after_fix(self, monkeypatch):
        # 修复后：受信任集合含渠道池 + 显式白名单，ark 直接放行
        monkeypatch.setattr(sg, "_TRUSTED_CACHE", None)
        ok, reason = sg.is_safe_http_url("https://ark.cn-beijing.volces.com/api/v1/chat")
        assert ok is True, reason
        assert reason == ""

    def test_zhipu_channel_trusted(self, monkeypatch):
        monkeypatch.setattr(sg, "_TRUSTED_CACHE", None)
        ok, reason = sg.is_safe_http_url(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions")
        assert ok is True, reason

    def test_cloud_metadata_hard_blocked(self):
        # 安全底线：云元数据始终硬拒绝，即使“受信任”也拦
        ok, reason = sg.is_safe_http_url("http://169.254.169.254/latest/meta-data/")
        assert ok is False
        assert "169.254.169.254" in reason

    def test_public_url_allowed(self, monkeypatch):
        monkeypatch.setattr(sg, "_TRUSTED_CACHE", None)
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
        ok, reason = sg.is_safe_http_url("https://example.com/path")
        assert ok is True, reason

    def test_red_before_fix_internal_ip_blocked(self, monkeypatch):
        # 复现修复前：ark 不在受信任集合，解析到内网 IP 即被拒
        monkeypatch.setattr(sg, "_TRUSTED_CACHE", {"localhost", "127.0.0.1"})
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("192.168.50.86"))
        ok, reason = sg.is_safe_http_url("https://ark.cn-beijing.volces.com/api/v1/chat")
        assert ok is False
        assert "192.168.50.86" in reason

    def test_green_after_fix_internal_ip_allowed(self, monkeypatch):
        # 修复后：ark 在受信任集合，不再走 DNS，内网解析误拦已消除
        monkeypatch.setattr(sg, "_TRUSTED_CACHE", None)
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("192.168.50.86"))
        ok, reason = sg.is_safe_http_url("https://ark.cn-beijing.volces.com/api/v1/chat")
        assert ok is True, reason
