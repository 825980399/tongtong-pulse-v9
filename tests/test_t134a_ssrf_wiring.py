# -*- coding: utf-8 -*-
"""第134批 T-134a：出站白名单接线验证。

覆盖四类场景：
  1) scheme 拒绝（非 http/https）
  2) 私网拒绝（非受信任私网/环回）
  3) 白名单放行（受信任主机，免 DNS）
  4) DNS 失败拒绝（不可解析主机）
并验证 RssCollector / WikiQuerier 的 _default_fetch 在守卫拒绝时 fail-closed 上抛。
"""
import config as _cfg
import nucleus.ssrf_guard as _sg

import nucleus.knowledge.RssCollector as _rss
import nucleus.knowledge.WikiQuerier as _wiki


def test_scheme_rejected():
    for url in ("file:///etc/passwd", "ftp://example.com", "gopher://x"):
        ok, reason = _sg.is_safe_http_url(url)
        assert ok is False, f"应拒绝协议 {url}: {reason}"
        assert "协议" in reason or "scheme" in reason.lower()


def test_private_rejected():
    # 10.0.0.1 是私网地址且不在受信任集合 → 拒绝（无需 DNS）
    ok, reason = _sg.is_safe_http_url("http://10.0.0.1")
    assert ok is False, f"应拒绝私网: {reason}"
    assert "非公网" in reason or "private" in reason.lower()


def test_trusted_allowed():
    # localhost 属受信任主机，免 DNS 即放行
    ok, reason = _sg.is_safe_http_url("http://localhost:11434")
    assert ok is True, f"受信任主机应放行: {reason}"


def test_dns_failure_rejected():
    ok, reason = _sg.is_safe_http_url("http://no-such-host.invalid")
    assert ok is False, f"不可解析主机应拒绝: {reason}"
    assert "解析" in reason or "gaierror" in reason.lower()


def test_rss_sources_in_trusted_extra():
    extra = _cfg.SSRF_TRUSTED_EXTRA_HOSTS
    for host in ("www.solidot.org", "www.infoq.cn", "feed.cnblogs.com",
                 "www.ruanyifeng.com", "sspai.com"):
        assert host in extra, f"RSS 源 {host} 应纳入受信任额外主机"


def test_rss_default_fetch_blocks_private():
    try:
        _rss._default_fetch("http://10.0.0.1")
        assert False, "私网请求应被 SSRF 守卫 fail-closed 拒绝"
    except ValueError as e:
        assert "[SSRF]" in str(e)


def test_wiki_default_fetch_blocks_private():
    try:
        _wiki._default_fetch("http://10.0.0.1")
        assert False, "私网请求应被 SSRF 守卫 fail-closed 拒绝"
    except ValueError as e:
        assert "[SSRF]" in str(e)
