#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第161批段A A3b 门控单测：对外文本公开渲染。

锁定五件事：
  1) 白名单判定 fail-closed：README/CONTRIBUTING/SECURITY/LICENSE/CHANGELOG
     与 PUBLIC_DOCS_ALLOW_DIRS 内的 docs 渲染，.py 代码面与其他路径**不渲染**；
  2) 渲染值是**非真值**（不含真实姓名/生日/路径），且不用真值表；
  3) 可撤回：ENABLE_PUBLIC_RENDER=False 时输出与渲染前完全一致；
  4) 幂等：已渲染文本再渲染不产生二次替换；
  5) 挂载点在写包/写盘**之前**（确保 A3a 包体复扫能扫到渲染后内容）。
"""
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "ep_a3b", os.path.join(ROOT, "tools", "export_public.py"))
ep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ep)

SAMPLE = "小林的女儿叫<CREATOR_DAUGHTER>（<BIRTH_DATE>出生），是<CREATOR>的孩子。"


class TestA3bWhitelist:
    def test_01_root_docs_rendered(self):
        for f in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md",
                  "LICENSE"):
            assert ep.is_public_render_target(f), f

    def test_02_code_files_not_rendered(self):
        """★.py 代码面一律不渲染（占位符保留）。"""
        for f in ("config.py", "main.py", "nucleus/const.py",
                  "tests/test_a3b_public_render_m161.py"):
            assert not ep.is_public_render_target(f), f

    def test_03_internal_docs_not_rendered(self):
        """内部协作文档不在白名单 ⇒ 不渲染（fail-closed）。"""
        for f in ("docs/路灯与星轨对话/任务书/xx.md",
                  "docs/分析报告/xx.md",
                  "docs/台账/ledger.csv"):
            assert not ep.is_public_render_target(f), f

    def test_04_fail_closed_default(self):
        """未列入白名单的路径一律不渲染（默认拒绝）。"""
        assert not ep.is_public_render_target("some_random_file.md")
        assert not ep.is_public_render_target("tools/some.md")


class TestA3bRender:
    def test_05_placeholders_replaced(self):
        out = ep.public_render(SAMPLE)
        assert "<CREATOR_DAUGHTER>" not in out
        assert "<BIRTH_DATE>" not in out
        assert "晓曈" in out

    def test_06_values_are_not_real(self):
        """★显示值必须是非真值泛化措辞，不得含 4 位年份/真实身份。"""
        for _ph, val in ep.PUBLIC_PLACEHOLDER_VALUES.items():
            assert not any(ch.isdigit() for ch in val) or "早几年" in val, \
                f"{_ph} 的显示值含数字，疑似真值：{val}"
        out = ep.public_render(SAMPLE)
        import re
        assert not re.search(r"(?:19|20)\d{2}", out), f"渲染结果含年份：{out}"

    def test_07_idempotent(self):
        once = ep.public_render(SAMPLE)
        twice = ep.public_render(once)
        assert once == twice, "渲染非幂等"

    def test_08_empty_safe(self):
        assert ep.public_render("") == ""
        assert ep.public_render("无占位符的文本") == "无占位符的文本"

    def test_09_bytes_roundtrip(self):
        raw = SAMPLE.encode("utf-8")
        out = ep.public_render_bytes("README.md", raw)
        assert b"<CREATOR_DAUGHTER>" not in out
        # .py 不渲染
        py = ep.public_render_bytes("config.py", raw)
        assert py == raw, "代码面不应被渲染"


class TestA3bSwitch:
    def test_10_switch_off_is_reversible(self):
        """★可撤回：开关关闭 ⇒ 输出与渲染前一致。"""
        import config
        saved = getattr(config, "ENABLE_PUBLIC_RENDER", None)
        try:
            config.ENABLE_PUBLIC_RENDER = False
            assert ep.public_render_enabled() is False
            assert ep.public_render(SAMPLE) == SAMPLE
            assert ep.public_render_bytes("README.md",
                                           SAMPLE.encode("utf-8")) == SAMPLE.encode("utf-8")
        finally:
            if saved is not None:
                config.ENABLE_PUBLIC_RENDER = saved

    def test_11_switch_on_by_default(self):
        import config
        assert getattr(config, "ENABLE_PUBLIC_RENDER", True) is True

    def test_12_missing_switch_defaults_on(self):
        import config
        saved = getattr(config, "ENABLE_PUBLIC_RENDER", None)
        had = hasattr(config, "ENABLE_PUBLIC_RENDER")
        try:
            if had:
                del config.ENABLE_PUBLIC_RENDER
            assert ep.public_render_enabled() is True
        finally:
            if had:
                config.ENABLE_PUBLIC_RENDER = saved


class TestA3bWiring:
    def test_13_hooked_before_write(self):
        """挂载点必须在写包/写盘之前（包体复扫才扫得到渲染后内容）。"""
        src = io.open(os.path.join(ROOT, "tools", "export_public.py"),
                      encoding="utf-8", newline=None).read()
        assert "public_render_bytes" in src
        # zip 路径：渲染调用出现在 writestr 之前
        i_zip = src.find("def export_zip")
        seg = src[i_zip:i_zip + 900]
        assert seg.index("public_render_bytes") < seg.index("writestr"), \
            "export_zip 渲染必须在 writestr 之前"
        # dir 路径：渲染调用出现在 fo.write 之前
        i_dir = src.find("def export_dir")
        seg2 = src[i_dir:i_dir + 900]
        assert seg2.index("public_render_bytes") < seg2.index("fo.write"), \
            "export_dir 渲染必须在 fo.write 之前"

    def test_14_no_placeholder_in_rendered_whitelist_files(self):
        """渲染后对外白名单文件内 <[A-Z_]{2,32}> 计数应为 0。"""
        import re
        for rel in ("README.md",):
            p = os.path.join(ROOT, rel)
            if not os.path.isfile(p):
                continue
            with open(p, "rb") as fh:
                out = ep.public_render_bytes(rel, fh.read())
            txt = out.decode("utf-8", errors="ignore")
            assert not re.search(r"<[A-Z_]{2,32}>", txt), f"{rel} 仍含占位符"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
