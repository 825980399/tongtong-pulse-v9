# -*- coding: utf-8 -*-
"""480 刀7-10：S 系列四项（台账 S1 / S2 / S5 / S13）。

## 判据来源

台账《全量问题汇总台账与修复路线图_20261106》横切面一：
  S1  `POST /send` 的 `file_paths` 无路径沙箱（只判 exists）⇒ 任意本地文件被 OCR；
  S2  `GET /replies` 无 `_check_origin` ⇒ DNS rebinding 可读走全部回复；
  S5  `ssrf_guard` 裸 `urlopen` 默认跟随 30x ⇒ 302 绕过首跳校验；
  S13 `/upload` 读 body 无上限 ⇒ 内存放大 DoS。

## 这些测试**不**证明什么

不证明 HTTP 端点真的挡住了攻击 —— 那要起服务打请求。
这里只证明「判据函数本身正确」+「接线点确实调了它」（源码结构断言）。
"""
import os
import sys
import urllib.error

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

import config
import functions.web_chat as WC
from nucleus import ssrf_guard as SG

# ============ S1：file_paths 路径沙箱 ============

def test_m480_path_under_uploads_allows_upload_dir(monkeypatch, tmp_path):
    _up = tmp_path / "uploads"
    _up.mkdir()
    monkeypatch.setattr(WC, "_UPLOAD_DIR", str(_up))
    _f = _up / "a.jpg"
    _f.write_bytes(b"x")
    _ok, _why = WC._m480_path_under_uploads(str(_f))
    assert _ok is True, _why


def test_m480_path_under_uploads_rejects_outside(monkeypatch, tmp_path):
    _up = tmp_path / "uploads"
    _up.mkdir()
    monkeypatch.setattr(WC, "_UPLOAD_DIR", str(_up))
    _outside = tmp_path / "secret.jpg"
    _outside.write_bytes(b"x")
    _ok, _why = WC._m480_path_under_uploads(str(_outside))
    assert _ok is False
    assert "uploads" in _why


def test_m480_path_under_uploads_rejects_prefix_trap(monkeypatch, tmp_path):
    """核心：`uploads_evil/` 与 `uploads/` 共享前缀，字符串 startswith 会放行。"""
    _up = tmp_path / "uploads"
    _up.mkdir()
    _evil = tmp_path / "uploads_evil"
    _evil.mkdir()
    _f = _evil / "a.jpg"
    _f.write_bytes(b"x")
    monkeypatch.setattr(WC, "_UPLOAD_DIR", str(_up))
    _ok, _ = WC._m480_path_under_uploads(str(_f))
    assert _ok is False, "按字符串前缀判会误放行，必须按路径语义判"


def test_m480_path_under_uploads_rejects_traversal(monkeypatch, tmp_path):
    _up = tmp_path / "uploads"
    _up.mkdir()
    monkeypatch.setattr(WC, "_UPLOAD_DIR", str(_up))
    _ok, _ = WC._m480_path_under_uploads(str(_up / ".." / "secret.jpg"))
    assert _ok is False


def test_m480_s1_wired_in_send_path():
    """接线断言：`/send` 处理 file_paths 时必须先过沙箱。"""
    with open(os.path.join(_ROOT, "functions", "web_chat.py"),
              encoding="utf-8") as _f:
        _src = _f.read()
    assert "_m480_path_under_uploads(fp)" in _src, "沙箱未接线到 file_paths 循环"


# ============ S2：/replies 同源校验 ============

def test_m480_guard_replies_default_on():
    assert WC._m480_guard_replies_on() is True


def test_m480_guard_replies_switch_off(monkeypatch):
    monkeypatch.setattr(config, "WEB_CHAT_GUARD_REPLIES", False, raising=False)
    assert WC._m480_guard_replies_on() is False


def test_m480_s2_wired_in_do_get():
    """接线断言：`do_GET` 的 /replies 分支必须调 `_check_origin`。"""
    with open(os.path.join(_ROOT, "functions", "web_chat.py"),
              encoding="utf-8") as _f:
        _src = _f.read()
    _i = _src.find("def do_GET")
    _j = _src.find("def do_POST")
    _block = _src[_i:_j]
    assert "_serve_replies" in _block
    assert "_check_origin" in _block, "do_GET 里没有同源校验 ⇒ S2 未修"


# ============ S13：上传大小上限 ============

def test_m480_max_upload_default_32m():
    assert WC._m480_max_upload_bytes() == 32 * 1024 * 1024


def test_m480_max_upload_zero_means_unlimited(monkeypatch):
    monkeypatch.setattr(config, "WEB_CHAT_MAX_UPLOAD_BYTES", 0, raising=False)
    assert WC._m480_max_upload_bytes() == 0


def test_m480_s13_wired_before_read():
    """接线断言：大小判定必须在 `rfile.read` **之前**（读进去就晚了）。"""
    with open(os.path.join(_ROOT, "functions", "web_chat.py"),
              encoding="utf-8") as _f:
        _src = _f.read()
    _chk = _src.find("_m480_max_upload_bytes()")
    _rd = _src.find("body = self.rfile.read(content_length)")
    assert _chk > 0 and _rd > 0
    assert _chk < _rd, "上限判定在 read 之后 ⇒ 拦不住内存放大"


# ============ S5：SSRF 禁跟随重定向 ============

def test_m480_ssrf_redirect_off_by_default():
    assert SG._m480_allow_redirect() is False
    assert SG._m480_get_opener() is not None


def test_m480_ssrf_redirect_switch_on(monkeypatch):
    monkeypatch.setattr(config, "SSRF_ALLOW_REDIRECT", True, raising=False)
    monkeypatch.setattr(SG, "_M480_OPENER", None)
    assert SG._m480_allow_redirect() is True
    assert SG._m480_get_opener() is None, "开开关应退回裸 urlopen"


def test_m480_no_redirect_raises():
    """核心：30x 必须直接抛 HTTPError，绝不再发第二次请求。"""
    _h = SG._M480NoRedirect()
    _req = type("R", (), {"full_url": "http://evil.example/x"})()
    with pytest.raises(urllib.error.HTTPError) as _ei:
        _h.redirect_request(_req, None, 302, "Found", {},
                            "http://169.254.169.254/latest/meta-data/")
    assert "拒绝跟随重定向" in str(_ei.value)
    assert "169.254.169.254" in str(_ei.value)


def test_m480_s5_wired_in_safe_http_json():
    with open(os.path.join(_ROOT, "nucleus", "ssrf_guard.py"),
              encoding="utf-8") as _f:
        _src = _f.read()
    assert "_m480_get_opener()" in _src, "safe_http_json 未接禁跟随 opener"
