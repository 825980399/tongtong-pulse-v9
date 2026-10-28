# -*- coding: utf-8 -*-
"""★382 刀10 · SSRF 拦截分类留痕（P2）

票面：`ssrf_guard.py:121` 三类混同（DNS_FAIL 27 + PRIVATE_ADDR 2 + 其他 4）。
要求：拆成三类分别计数、日志写清哪类、三类各一条用例。

改动只加计数，**不改任何判定**（返回值与语义逐字不变）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus import ssrf_guard as sg  # noqa: E402


def setup_function(_fn):
    sg.reset_ssrf_block_stats()


def test_m382_d10_dns_fail_counted_separately():
    """★DNS_FAIL：解析不了的主机要单独记一类（不再混进"其他"）。"""
    _ok, _why = sg.is_safe_http_url("http://no-such-host-m382.invalid/x")
    assert _ok is False
    _s = sg.ssrf_block_stats()
    assert _s["DNS_FAIL"] == 1, _s
    assert _s["PRIVATE_ADDR"] == 0 and _s["OTHER"] == 0


def test_m382_d10_private_addr_counted_separately():
    """★PRIVATE_ADDR：云元数据/保留地址走硬底线分支。"""
    _ok, _why = sg.is_safe_http_url("http://169.254.169.254/latest/meta-data/")
    assert _ok is False
    _s = sg.ssrf_block_stats()
    assert _s["PRIVATE_ADDR"] == 1, _s
    assert _s["DNS_FAIL"] == 0


def test_m382_d10_other_counted_separately():
    """★OTHER：协议不支持这类走通用拦截分支。"""
    _ok, _why = sg.is_safe_http_url("ftp://example.com/x")
    assert _ok is False
    _s = sg.ssrf_block_stats()
    assert _s["OTHER"] == 1, _s
    assert _s["total"] == 1


def test_m382_d10_three_kinds_do_not_mix():
    """三类各打一次 ⇒ 各自 1，互不串味；total = 3。"""
    sg.is_safe_http_url("http://no-such-host-m382.invalid/x")   # DNS_FAIL
    sg.is_safe_http_url("http://169.254.169.254/x")             # PRIVATE_ADDR
    sg.is_safe_http_url("ftp://example.com/x")                  # OTHER
    _s = sg.ssrf_block_stats()
    assert (_s["DNS_FAIL"], _s["PRIVATE_ADDR"], _s["OTHER"]) == (1, 1, 1), _s
    assert _s["total"] == 3


def test_m382_d10_allowed_url_not_counted():
    """反控：通过判定的 URL 不许计成拦截。"""
    sg.is_safe_http_url("https://example.com/ok")
    assert sg.ssrf_block_stats()["total"] == 0
