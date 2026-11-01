# -*- coding: utf-8 -*-
"""E6 · SSRF 拒收分六类留痕（T-SSRF分类留痕-1）。

守的契约：
  * 分类**纯函数**可复算（reason 文本 → 六细类 → 三组）；
  * 判据本身一字未改：允许的 URL 仍允许、拒绝的仍拒绝（包装器只计数）；
  * `total=0` 是「无样本」不是「网络健康」（读数口径）；
  * 三组映射：dns_fail / private_addr / other —— 混桶会让「DNS 抖动」看起来像「安全事件」。
"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from nucleus import ssrf_guard as sg


@pytest.fixture(autouse=True)
def _clean():
    sg._m367_reset_reject_stats()
    yield
    sg._m367_reset_reject_stats()


class TestClassify:
    def test_01_six_kinds(self):
        """六类 reason 文本必须分别认出（顺序即优先级，先认更具体的）。"""
        cases = {
            "无法解析主机: evil.example": "dns_fail",
            "拒绝非公网地址: evil.example -> 10.0.0.1": "private_addr",
            "拒绝云元数据/保留地址: 169.254.169.254": "hard_block",
            "不支持的协议: ftp": "scheme",
            "缺少主机名": "no_host",
            "URL 解析失败": "url_parse",
            "某种新原因": "other",
        }
        for _reason, _want in cases.items():
            assert sg.m367_classify_reject(_reason) == _want, _reason

    def test_02_group_mapping(self):
        """三组映射：只有 dns_fail / private_addr 独立成组，其余归 other。"""
        assert sg._M367_REJECT_GROUP["dns_fail"] == "dns_fail"
        assert sg._M367_REJECT_GROUP["private_addr"] == "private_addr"
        for _k in ("hard_block", "scheme", "no_host", "url_parse", "other"):
            assert sg._M367_REJECT_GROUP[_k] == "other", _k


class TestCounting:
    def test_03_reject_increments_kind(self):
        """真实拒收（协议不支持）计入 scheme，且返回值语义不变。"""
        ok, why = sg.is_safe_http_url("ftp://example.com/x")
        assert ok is False
        assert "不支持的协议" in why
        st = sg.m367_ssrf_reject_stats()
        assert st["by_kind"].get("scheme") == 1
        assert st["by_group"].get("other") == 1
        assert st["total"] == 1

    def test_04_allow_does_not_count(self):
        """允许的 URL 不计入拒收（否则读数全错）。"""
        _trusted = sorted(sg._trusted_hosts())[0] if sg._trusted_hosts() else ""
        if not _trusted:
            pytest.skip("受信任集合为空，跳过")
        ok, _ = sg.is_safe_http_url("http://%s:11434/api/tags" % _trusted)
        assert ok is True
        assert sg.m367_ssrf_reject_stats()["total"] == 0

    def test_05_dns_fail_kind(self, monkeypatch):
        """DNS 失败归 dns_fail（这是票面最主要的一类：27 条）。"""
        def _boom(*a, **k):
            raise socket.gaierror("no such host")
        monkeypatch.setattr(socket, "getaddrinfo", _boom)
        ok, _why = sg.is_safe_http_url("http://no-such-host-e6.invalid/x")
        assert ok is False
        assert sg.m367_ssrf_reject_stats()["by_group"].get("dns_fail") == 1

    def test_06_private_addr_kind(self, monkeypatch):
        """解析到内网 ⇒ private_addr（安全事件面，必须与 dns_fail 分开）。"""
        def _fake(*a, **k):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                     ("10.1.2.3", 0))]
        monkeypatch.setattr(socket, "getaddrinfo", _fake)
        ok, why = sg.is_safe_http_url("http://intranet-probe-e6.example/x")
        assert ok is False
        assert "拒绝非公网地址" in why
        _g = sg.m367_ssrf_reject_stats()["by_group"]
        assert _g.get("private_addr") == 1
        assert _g.get("dns_fail") is None, "两类不得混桶"

    def test_07_no_sample_is_not_healthy(self):
        """`total=0` 只表示无样本；读数必须带 schema/total，不给「健康」错觉。"""
        st = sg.m367_ssrf_reject_stats()
        assert st["total"] == 0
        assert st["schema"] == "m367.ssrf_reject/v1"
        assert st["by_kind"] == {} and st["by_group"] == {}

    def test_08_stats_is_copy(self):
        """读数必须是拷贝：调用方改它不得影响内部计数。"""
        sg.is_safe_http_url("ftp://a/b")
        st = sg.m367_ssrf_reject_stats()
        st["by_kind"]["scheme"] = 999
        assert sg.m367_ssrf_reject_stats()["by_kind"]["scheme"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
