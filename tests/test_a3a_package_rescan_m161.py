#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# export-scan-skip-file  （本文件 PII 均为红队演练构造的假夹具，非真实身份）
"""第161批段A A3a 门控单测：包体复扫（唯一可信口径 = 写包后包体字节）。

锁定四件事：
  1) scan_package_bytes 直接吃字节，能扫出源文件里不存在的包体内容；
  2) verify_package 对 zip 与 dir 两种包形都能遍历复扫；
  3) ★红队演练：四位数字 PII 进入包体后必被命中（rc=1 语义）；
  4) --no-scan 分支不得打印 source=post_render_package 哨兵。
"""
import io
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ep_a3a", os.path.join(ROOT, "tools", "export_public.py"))
ep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ep)


_TMP = os.path.join(ROOT, "tmp", "_a3a_pkg")


def _fresh_dir():
    import shutil
    if os.path.isdir(_TMP):
        shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(_TMP, exist_ok=True)
    return _TMP


class TestA3aScanBytes:
    def test_01_scans_plain_bytes(self):
        """直接吃字节：不含任何 PII 的文本应零命中。"""
        hits = ep.scan_package_bytes(b"hello world\nnothing here\n", "x.txt")
        assert hits == []

    def test_02_detects_four_digit_birthdate(self):
        """★红队：四位年份 + 出生上下文，包体字节必被命中。"""
        payload = "我的出生年份是 1990 年\n".encode("utf-8")
        hits = ep.scan_package_bytes(payload, "t.md")
        assert hits, "包体中的出生年份未被命中，红队演练失败"
        assert any("1990" in h[2] or "1990" in str(h) for h in hits)

    def test_03_respects_scan_skip_markers(self):
        """带扫描跳过标记的行不应命中。"""
        mk = ep.SCAN_SKIP_MARKERS[0] if ep.SCAN_SKIP_MARKERS else "SKIP"
        payload = f"{mk} 出生年份 1990\n".encode("utf-8")
        assert ep.scan_package_bytes(payload, "t.md") == []

    def test_04_handles_non_utf8_bytes(self):
        """非 UTF-8 字节不得抛异常（errors=ignore 降级）。"""
        hits = ep.scan_package_bytes(b"\xff\xfe\x00binary", "b.bin")
        assert isinstance(hits, list)


class TestA3aVerifyPackage:
    def test_05_verify_dir_package(self):
        """dir 形包体：可遍历复扫。"""
        d = _fresh_dir()
        base = os.path.join(d, "tongtong-pulse-net")
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "a.md"), "wb") as f:
            f.write(b"clean content\n")
        hits = ep.verify_package(d, False)
        assert isinstance(hits, list) and hits == []

    def test_06_verify_zip_package(self):
        """zip 形包体：可遍历复扫。"""
        d = _fresh_dir()
        zp = os.path.join(d, "pkg.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("tongtong-pulse-net/a.md", "clean content\n")
        hits = ep.verify_package(zp, True)
        assert isinstance(hits, list) and hits == []

    def test_07_zip_with_pii_is_caught(self):
        """★红队演练（zip）：包体内 PII 必被 verify_package 命中。"""
        d = _fresh_dir()
        zp = os.path.join(d, "bad.zip")
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("tongtong-pulse-net/leak.md",
                        "联系人出生年份 1988 年\n")
        hits = ep.verify_package(zp, True)
        assert hits, "zip 包体中的 PII 未被命中"
        assert any("1988" in h[3] for h in hits)


class TestA3aSentinel:
    def test_08_no_scan_branch_has_no_sentinel(self):
        """★--no-scan 是包体复扫完整旁路：其提示语中不得含哨兵字符串。"""
        src = io.open(os.path.join(ROOT, "tools", "export_public.py"),
                      encoding="utf-8", newline=None).read()
        # 取 --no-scan 的 else 分支文本
        idx = src.find('print("[WARN] 已跳过 PII 复扫')
        assert idx != -1, "未找到 --no-scan 提示分支"
        branch = src[idx:idx + 200]
        assert "source=post_render_package" not in branch, \
            "--no-scan 分支不得打印 source=post_render_package 哨兵"

    def test_09_sentinel_printed_in_scan_path(self):
        """正常复扫路径必须打印哨兵（证明扫的是包体）。"""
        src = io.open(os.path.join(ROOT, "tools", "export_public.py"),
                      encoding="utf-8", newline=None).read()
        assert "source=post_render_package" in src, "缺少包体复扫哨兵"
        assert "verify_package(out, is_zip)" in src, "main 未调用包体复扫"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
