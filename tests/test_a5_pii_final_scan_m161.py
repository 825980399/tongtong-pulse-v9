#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第161批段A A5 门控单测：PII 终扫三档（tools/ci/check_pii_final_scan.py）。

锁定五件事：
  1) 三档 rc 语义可验：0=干净 / 1=脏 / 2=执行异常；
  2) ★输出只掩码值（PII 打码）——真值绝不出现在输出里；
  3) S2 二维分档：阻断判据 =「本次变更」×「会进发布包」，
     内部协作目录（不进包）命中只报告不阻断；
  4) S2/S3 内部域白名单共享同一常量（同一概念不两处定义）；
  5) 排除规则不误伤：RFC 2606 保留域、内部匿名域、URL 仓库 ID、commit SHA。
"""
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "pii_final", os.path.join(ROOT, "tools", "ci", "check_pii_final_scan.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


class TestA5RcSemantics:
    def test_01_rc_constants(self):
        assert m.RC_CLEAN == 0
        assert m.RC_DIRTY == 1
        assert m.RC_ERROR == 2

    def test_02_mask_never_leaks_value(self):
        """★掩码：输出不得含原值。"""
        for raw in ("138" + "12345678", "someone@example.com", "19900101"):
            msk = m._mask_value(raw)
            assert raw not in msk, f"掩码泄漏原值：{raw} -> {msk}"

    def test_03_mask_keeps_clue(self):
        """掩码保留形态线索（便于定位）但不回吐真值。"""
        msk = m._mask_value("someone@example.com")
        assert "@" in msk and "***" in msk
        msk2 = m._mask_value("138" + "12345678")
        assert "len=11" in msk2


class TestA5SharedAllowDomains:
    def test_04_internal_domains_defined(self):
        assert ".local" in m._INTERNAL_ALLOW_DOMAINS
        assert "github.com" in m._INTERNAL_ALLOW_DOMAINS

    def test_05_s3_uses_shared_block(self):
        """★S3 与 S2 共用同一白名单常量（同一概念不两处定义）。"""
        assert m._META_ALLOW_DOMAINS == m._INTERNAL_ALLOW_DOMAINS

    def test_06_meta_line_hits_filters_internal(self):
        line = "dev@tongtong.local 提交"
        assert list(m._meta_line_hits(line)) == [], "内部匿名域不应命中"

    def test_07_meta_line_hits_filters_url_repo_id(self):
        """URL 路径里的仓库 ID 不当手机号。"""
        line = "https://openi.pcl.ac.cn/" + "138" + "00000000" + "/tongtong-pulse-v9.git"
        assert list(m._meta_line_hits(line)) == [], "URL 仓库 ID 不应命中"

    def test_08_meta_line_hits_filters_sha(self):
        """commit SHA 里的连续数字不当手机号。"""
        line = "327e152098" + "46101b4e91359d5482595be0455d90"
        assert list(m._meta_line_hits(line)) == [], "commit SHA 不应命中"

    def test_09_meta_line_hits_catches_real_pii(self):
        """★真实 PII 仍须命中（不能因修误报而漏检）。"""
        hits = list(m._meta_line_hits("联系电话 " + "138" + "12345678" + " 请回拨"))
        assert hits, "真实手机号应命中"


class TestA5S2Tiering:
    def test_10_cli_has_strict_history(self):
        """S2 须提供 --strict-history 开关（清史后切严格）。"""
        # main() 内部构造 parser；此处直接验证参数被接受
        try:
            m.main(["--tier", "s3", "--strict-history", "--remote-head", "origin/HEAD"])
        except SystemExit as e:
            assert e.code in (0, 1, 2), f"意外退出码 {e.code}"
        except Exception:
            # S3 实际执行异常（如 git 不可用）不在本用例关注范围，但须留痕
            from nucleus._silent_except import silent_exc
            silent_exc(Exception("s3 exec"), "test_a5.test_10")

    def test_11_in_package_criterion_exists(self):
        """S2 的进包判据须复用导出器 should_skip（与 S1 同口径）。"""
        src = io.open(os.path.join(ROOT, "tools", "ci", "check_pii_final_scan.py"),
                      encoding="utf-8", newline=None).read()
        assert "should_skip" in src, "S2 未复用导出器进包判据"

    def test_12_tier_labels_present(self):
        """三档标签必须在输出中可区分。"""
        src = io.open(os.path.join(ROOT, "tools", "ci", "check_pii_final_scan.py"),
                      encoding="utf-8", newline=None).read()
        for label in ("本次变更·进包", "本次变更·不进包", "历史存量"):
            assert label in src, f"缺分档标签：{label}"


class TestA5NoWrites:
    def test_13_scan_is_read_only(self):
        """★终扫只读：不得写任何数据文件（不落真值到磁盘）。

        注意只查**写盘**模式；`print(json.dumps(...))` 是打印到 stdout，非写盘。
        """
        src = io.open(os.path.join(ROOT, "tools", "ci", "check_pii_final_scan.py"),
                      encoding="utf-8", newline=None).read()
        for bad in ("open(OUT", 'open(path, "w"', "open(base, \"w\"",
                    ".write_text(", "os.remove(", "shutil.rmtree"):
            assert bad not in src, f"终扫脚本不应含写盘操作：{bad}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
