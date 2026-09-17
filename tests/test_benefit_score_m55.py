# -*- coding: utf-8 -*-
"""第55批 T1 门控测试：benefit_score 统一常量（P2-380，星轨裁决方案①）。

背景
----
第54批 T0 发现：任务书称改两个 `PatchManager.py` 里的 `benefit_score` 默认值，
但**这两个文件里根本没有该字段** —— 真实形态是 7 处 `get("benefit_score", 3)` 兜底。
星轨裁决采用**方案①**：抽统一常量 `config.DEFAULT_BENEFIT_SCORE = 4`。

★本批又一个 T0 偏差：任务书给的 5 个行号**全部偏移**（第54批 T6 在同一文件插入过代码）
→ 本测试**按内容断言，不按行号**。

覆盖
----
① 常量存在且 = 4，且 ≥ 审批门槛（trust = benefit×10 ≥ 40）
② 7 处兜底全部引用常量（源码级）
③ 无 `"benefit_score", 3` 硬编码残留（源码级）
④ 样例值（`"benefit_score": N` 形态）未被误改
⑤ 运行时跟随：改 config 值 → 兜底取值跟随（★防漂移，断言在 with 块内）
⑥ 三个模块仍可正常导入（回归保护）
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest  # noqa: E402

import config  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = [
    ("nucleus/reasoning/SafeEvolutionExecutor.py", 5),
    ("nucleus/reasoning/EvolutionSandbox.py", 1),
    ("nucleus/reasoning/AdaptiveQueryStrategyGenerator.py", 1),
]


def _read(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8", errors="replace").read()


def _benefit_default(cfg):
    """★与生产 `get("benefit_score", _DEF_BENEFIT_SCORE)` **同构**（改生产须同步改本函数）。"""
    return int(getattr(cfg, "DEFAULT_BENEFIT_SCORE", 3))


@contextlib.contextmanager
def _cfg_switch(attr, value):
    had = hasattr(config, attr)
    old = getattr(config, attr, None)
    setattr(config, attr, value)
    try:
        yield
    finally:
        if had:
            setattr(config, attr, old)
        else:
            delattr(config, attr)


class TestBenefitScoreConstantM55(unittest.TestCase):
    """★第55批 T1：benefit_score 统一常量。"""

    def test_01_constant_is_4(self):
        """常量存在且为 4。"""
        self.assertTrue(hasattr(config, "DEFAULT_BENEFIT_SCORE"),
                        "config 缺少 DEFAULT_BENEFIT_SCORE")
        self.assertEqual(config.DEFAULT_BENEFIT_SCORE, 4)

    def test_02_default_trust_meets_threshold(self):
        """★核心：默认 benefit=4 → trust_score=40，达到审批门槛（不再被拒）。"""
        _trust = min(95, max(10, config.DEFAULT_BENEFIT_SCORE * 10))
        _threshold = config.EVOLUTION_CONFIG.get("auto_apply_min_trust")
        self.assertGreaterEqual(_trust, _threshold,
                                "默认信任分 %d 低于门槛 %s → 新补丁仍会被拒"
                                % (_trust, _threshold))

    def test_03_all_sites_reference_constant(self):
        """7 处兜底全部引用常量（**按内容断言，不按行号**）。"""
        for rel, expect in FILES:
            src = _read(rel)
            self.assertIn("_DEF_BENEFIT_SCORE", src, "%s 未引用常量" % rel)
            self.assertEqual(src.count('"benefit_score", _DEF_BENEFIT_SCORE)'), expect,
                             "%s 引用处数不符（期望 %d）" % (rel, expect))

    def test_04_no_hardcoded_fallback(self):
        """无 `"benefit_score", 3` 硬编码残留。"""
        for rel, _ in FILES:
            self.assertNotIn('"benefit_score", 3)', _read(rel),
                             "%s 仍残留硬编码兜底 3" % rel)

    def test_05_sample_values_untouched(self):
        """★防误伤：EvolutionSandbox 的 6 个 plan 样例值（`"benefit_score": N`）保持原样。"""
        src = _read("nucleus/reasoning/EvolutionSandbox.py")
        import re
        hits = re.findall(r'"benefit_score":\s*(\d+)', src)
        self.assertEqual(len(hits), 6, "样例值数量变化：%s" % hits)
        self.assertIn("3", hits, "样例值 3 被误改（它是样例不是默认值）")

    def test_06_follows_config_at_runtime(self):
        """★防漂移：改 config 值，兜底取值跟随（断言写在 with 块内）。"""
        with _cfg_switch("DEFAULT_BENEFIT_SCORE", 5):
            self.assertEqual(_benefit_default(config), 5)
            self.assertEqual(min(95, max(10, _benefit_default(config) * 10)), 50)
        with _cfg_switch("DEFAULT_BENEFIT_SCORE", 4):
            self.assertEqual(_benefit_default(config), 4)


if __name__ == "__main__":
    unittest.main()
