#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第161批段B B1 门控单测：PulseLegs 安全词表纳入 config 治理（零行为变更）。

锁定三件事：
  1) LEGS_CONFIG.unsafe_keywords 与原 PulseLegs 内联 12 词**严格一致**（防未来漂移改词）；
  2) _load_legs_config 的 config 加载路径生效；
  3) config 缺 LEGS_CONFIG 时兜底不改值（零行为变更保证）。
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 原 PulseLegs 内联硬编码词表（本批不得改动其内容）
ORIGINAL_LEGS_KEYWORDS = [
    "暴力", "色情", "赌博", "毒品", "武器制造",
    "黑客攻击", "病毒制作", "诈骗", "自杀",
    "歧视", "仇恨", "恐怖",
]


def _load_legs_class():
    path = os.path.join(ROOT, "organs", "motor", "PulseLegs.py")
    spec = importlib.util.spec_from_file_location("PulseLegs_b1_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PulseLegs


class TestB1LegsConfig:
    def test_01_legs_config_exists(self):
        import config
        assert hasattr(config, "LEGS_CONFIG"), "config.py 缺 LEGS_CONFIG"
        assert "unsafe_keywords" in config.LEGS_CONFIG

    def test_02_wordlist_identical_to_original_inline(self):
        """★零行为变更核心断言：config 词表 == 原内联词表（严格顺序相等）。"""
        import config
        assert config.LEGS_CONFIG["unsafe_keywords"] == ORIGINAL_LEGS_KEYWORDS, (
            "LEGS_CONFIG 词表被改动，违反 B1 零行为变更约束"
        )

    def test_03_differs_from_ethics_on_purpose(self):
        """与 ETHICS_CONFIG 的「有意不同」必须保留（勿盲目并表）。"""
        import config
        legs = set(config.LEGS_CONFIG["unsafe_keywords"])
        ethics = set(config.ETHICS_CONFIG["forbidden_keywords"])
        assert {"病毒", "虐待"} - legs == {"病毒", "虐待"}, "LEGS 不应含 病毒/虐待"
        assert legs - ethics == set(), "LEGS 不应有 ETHICS 之外的词"
        assert ethics - legs == {"病毒", "虐待"}

    def test_04_load_method_present(self):
        cls = _load_legs_class()
        assert hasattr(cls, "_load_legs_config"), "缺 _load_legs_config 方法"

    def test_05_config_path_applies(self):
        """config 加载路径：实例值应被 LEGS_CONFIG 覆盖。"""
        import config
        cls = _load_legs_class()

        class Stub:
            pass

        s = Stub()
        s._unsafe_keywords = ["SENTINEL"]
        cls._load_legs_config(s)
        assert s._unsafe_keywords == config.LEGS_CONFIG["unsafe_keywords"]

    def test_06_fallback_keeps_value(self):
        """config 缺 LEGS_CONFIG 时兜底不改值（零行为变更保证）。"""
        cls = _load_legs_class()

        class Stub:
            pass

        s = Stub()
        s._unsafe_keywords = ["KEEP_ME"]
        import config
        saved = getattr(config, "LEGS_CONFIG", None)
        had = hasattr(config, "LEGS_CONFIG")
        try:
            if had:
                del config.LEGS_CONFIG
            cls._load_legs_config(s)
            assert s._unsafe_keywords == ["KEEP_ME"]
        finally:
            if had:
                config.LEGS_CONFIG = saved

    def test_07_is_safe_content_still_blocks(self):
        """消费点 _is_safe_content 行为不变：命中词表拦截、干净内容放行。"""
        cls = _load_legs_class()

        class Stub:
            _log = staticmethod(lambda lvl, msg: None)

        s = Stub()
        s._unsafe_keywords = list(ORIGINAL_LEGS_KEYWORDS)
        fn = cls._is_safe_content
        assert fn(s, "这是一段包含赌博内容的文本") is False
        assert fn(s, "今天天气不错，适合读书") is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
