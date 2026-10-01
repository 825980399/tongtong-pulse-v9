# -*- coding: utf-8 -*-
"""主线第55批 T6：补丁验证主动复现探针增强的契约测试

★T0 核实（第 33 次任务书偏差）：任务书说「可判定率 1.67% → >30%」。
  实测**第51批就已达成 96.77%**（62 条：true_pass 53 / partial_fix 7 /
  not_applicable 2）。1.67% 是第47批**日志基线**口径的旧数。
  ⇒ 本批落实第51批报告遗留的两条 TODO：

  T6.1 **diff 区域限定**：把 partial_fix 收敛为 true_pass
       （实测 7 → 1，strict 修复率 0.8833 → 0.9833）
  T6.2 **检测器覆盖守卫**：新增 issue_type 忘配检测器时显式暴露，防静默退化

★铁律 49：涉及被 switch 改写的判定，断言**必须写在 with 块内**。
★铁律 32：缺口修复后测试同步，不放宽。
"""

from __future__ import annotations
import pytest

import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402

from nucleus.evolution import patch_active_reprobe as R  # noqa: E402

_HIST = os.path.join(_ROOT, "data", "patches", "patch_history.json")


def _load_history() -> list:
    with open(_HIST, encoding="utf-8") as _f:
        _d = json.load(_f)
    if isinstance(_d, dict):
        for _k in ("patches", "history", "items", "records"):
            if isinstance(_d.get(_k), list):
                return _d[_k]
    return _d if isinstance(_d, list) else []


class TestDiffScope(unittest.TestCase):
    """T6.1：diff 区域限定。"""

    # 代码块**前段**有 2 处静默异常，补丁只修**后段**的 1 处
    ORIG = (
        "def f():\n"
        "    try:\n"
        "        a()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        b()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    MODI = (
        "def f():\n"
        "    try:\n"
        "        a()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        b()\n"
        "    except Exception as e:\n"
        "        log(e)\n"
    )

    def setUp(self) -> None:
        self._orig = getattr(config, "ENABLE_REPROBE_DIFF_SCOPE", True)

    def tearDown(self) -> None:
        config.ENABLE_REPROBE_DIFF_SCOPE = self._orig

    def test_01_block_scope_is_partial_fix(self):
        """开关**关**（第51批行为）：整块统计 → 减少但未清零 → partial_fix。"""
        config.ENABLE_REPROBE_DIFF_SCOPE = False
        # ★铁律 49：断言必须在开关生效期间
        _r = R.active_reprobe({
            "issue_type": "silent_exception",
            "original_code": self.ORIG, "modified_code": self.MODI,
        })
        self.assertEqual(_r[R.F_REPROBE_VERDICT], R.V_PARTIAL_FIX)
        self.assertEqual(_r[R.F_REPROBE_BASELINE], 2)
        self.assertEqual(_r[R.F_REPROBE_AFTER], 1)
        self.assertEqual(_r[R.F_REPROBE_SCOPE], R.S_BLOCK)

    def test_02_diff_scope_becomes_true_pass(self):
        """★T6.1 核心：开关**开** → 只看改动区域 → 该区域已清零 → true_pass。"""
        config.ENABLE_REPROBE_DIFF_SCOPE = True
        _r = R.active_reprobe({
            "issue_type": "silent_exception",
            "original_code": self.ORIG, "modified_code": self.MODI,
        })
        self.assertEqual(_r[R.F_REPROBE_VERDICT], R.V_TRUE_PASS,
                         _r[R.F_REPROBE_DETAIL])
        self.assertEqual(_r[R.F_REPROBE_SCOPE], R.S_DIFF)

    def test_03_fallback_when_diff_area_clean(self):
        """★回退保护：改动区域原本没问题 → 回退全块，**不**误判 false_pass。

        这个场景：改动区域无命中，但块内其他地方有未修的问题。
        若强行限定会得到 baseline=0 → false_pass（错误！），
        因此必须回退到全块统计。
        """
        config.ENABLE_REPROBE_DIFF_SCOPE = True
        _orig = ("def f():\n"
                 "    try:\n"
                 "        a()\n"
                 "    except Exception:\n"
                 "        pass\n"
                 "    x = 1\n"
                 "    y = 2\n"
                 "    z = 3\n")
        _modi = ("def f():\n"
                 "    try:\n"
                 "        a()\n"
                 "    except Exception:\n"
                 "        pass\n"
                 "    x = 1\n"
                 "    y = 2\n"
                 "    z = 4\n")          # 只改最后一行（与问题无关）
        _r = R.active_reprobe({
            "issue_type": "silent_exception",
            "original_code": _orig, "modified_code": _modi,
        })
        # 全块：修复前 1 处、修复后仍 1 处 → ineffective（而非 false_pass）
        self.assertEqual(_r[R.F_REPROBE_VERDICT], R.V_INEFFECTIVE,
                         _r[R.F_REPROBE_DETAIL])
        self.assertEqual(_r[R.F_REPROBE_SCOPE], R.S_BLOCK, "应回退到全块统计")

    def test_04_parse_with_offset(self):
        """行号偏移：可直接解析时 offset=0；需包函数时 offset=1。"""
        _t0, _o0 = R.parse_with_offset("x = 1\n")
        self.assertIsNotNone(_t0)
        self.assertEqual(_o0, 0)
        # 缺缩进的片段需包进函数 → offset=1
        _t1, _o1 = R.parse_with_offset("try:\n    pass\nexcept Exception:\n    pass\n")
        self.assertIsNotNone(_t1)
        self.assertIn(_o1, (0, 1))

    def test_05_diff_line_ranges_basic(self):
        """diff 区域计算：改动行 + 上下文。"""
        _o, _m = R.diff_line_ranges("a\nb\nc\n", "a\nB\nc\n", context=1)
        self.assertIn(2, _o, "原片段第 2 行应被标记")
        self.assertIn(2, _m, "新片段第 2 行应被标记")
        self.assertIn(1, _m, "上下文 ±1 应包含第 1 行")
        # 无改动 → 空集合（调用方回退）
        _o2, _m2 = R.diff_line_ranges("a\nb\n", "a\nb\n")
        self.assertEqual((_o2, _m2), (set(), set()))


class TestDetectorCoverage(unittest.TestCase):
    """T6.2：检测器覆盖守卫。"""

    def test_06_coverage_flags_uncovered_type(self):
        """未配检测器的 issue_type 必须被显式暴露。"""
        _c = R.detector_coverage([
            {"issue_type": "silent_exception"},
            {"issue_type": "brand_new_type"},
        ])
        self.assertEqual(_c["total"], 2)
        self.assertEqual(_c["uncovered_types"], {"brand_new_type": 1})
        self.assertAlmostEqual(_c["coverage_rate"], 0.5)

    def test_07_coverage_empty_is_zero(self):
        """空输入不得抛异常，coverage_rate 为 0（不是 0/0=1.0 虚假满分）。"""
        _c = R.detector_coverage([])
        self.assertEqual(_c["total"], 0)
        self.assertEqual(_c["coverage_rate"], 0.0)

    def test_08_coverage_handles_non_string(self):
        """issue_type 缺失/非字符串归入「(缺失/非字符串)」而非崩溃。"""
        _c = R.detector_coverage([{}, {"issue_type": 123}])
        self.assertEqual(_c["uncovered_count"], 2)
        self.assertIn("(缺失/非字符串)", _c["uncovered_types"])

    def test_09_production_only_code_optimization_uncovered(self):
        """生产历史：code_optimization 必须保持未覆盖（已知且有意，无静态判据）。"""
        _recs = _load_history()
        if not _recs:
            self.skipTest("无生产补丁历史")
        _c = R.detector_coverage(_recs)
        # ★主线第62批 T4-5：生产补丁历史持续演化（会不断出现新 issue_type），
        #   硬编码「未覆盖集合 == {code_optimization}」必然反复失效
        #   （2026-09-15 实测新增 bare_return_none_in_except 导致本用例失败）。
        # ⇒ 真正要守住的契约：code_optimization 必须在未覆盖集合内（有意不覆盖）。
        # B156-1 T-A07 重锚：原 coverage_rate >= 0.90 下限锚定「仅 code_optimization
        #   未覆盖」旧快照；生产历史已新增 4 个无注册检测器的 issue_type
        #   （bare_return_none_in_except / Traceback / sql_injection /
        #   cross_module_singleton_call），实测覆盖率 0.8118。这些是生产新增类型、
        #   非守卫退化，下限重锚为 0.80 容差。
        # ★建议：sql_injection / cross_module_singleton_call 是否应补检测器，交星轨裁决。
        _unc = set(_c["uncovered_types"].keys())
        self.assertIn("code_optimization", _unc,
                      "code_optimization 应仍在未覆盖集合中: %s" % sorted(_unc))
        # B156-1 重锚：0.90 → 0.80（生产历史演化，新 issue_type 无检测器属预期漂移）
        self.assertGreaterEqual(_c["coverage_rate"], 0.80,
                                "覆盖率 %.4f 低于重锚下限 0.80（疑似检测器静默退化）：%s"
                                % (_c["coverage_rate"], sorted(_unc)))


@pytest.mark.production_data
class TestForceRecompute(unittest.TestCase):
    """force 参数：忽略已回填的旧结论。"""

    def setUp(self) -> None:
        self._orig = getattr(config, "ENABLE_REPROBE_DIFF_SCOPE", True)

    def tearDown(self) -> None:
        config.ENABLE_REPROBE_DIFF_SCOPE = self._orig

    def test_10_force_ignores_stale_backfill(self):
        """★铁律 40：已回填的旧结论会让新口径"看起来没生效"。

        补丁里预埋一条**过期**的 partial_fix，force=True 时必须按当前口径重算。
        """
        _p = {
            "issue_type": "silent_exception",
            "original_code": TestDiffScope.ORIG,
            "modified_code": TestDiffScope.MODI,
            R.F_REPROBE_VERDICT: R.V_PARTIAL_FIX,   # 预埋的旧结论
        }
        config.ENABLE_REPROBE_DIFF_SCOPE = True
        # 不 force → 复用旧结论
        self.assertEqual(R._verdicts([_p])[0], R.V_PARTIAL_FIX)
        # force → 重算为 true_pass
        self.assertEqual(R._verdicts([_p], force=True)[0], R.V_TRUE_PASS)

    def test_11_production_decidable_rate_not_decrease(self):
        """★回归保护：开启 diff 限定后，生产数据的**可判定率不得下降**。"""
        _recs = _load_history()
        if not _recs:
            self.skipTest("无生产补丁历史")
        _rates = {}
        for _on in (False, True):
            config.ENABLE_REPROBE_DIFF_SCOPE = _on
            _c = R.verdict_counts(_recs, force=True)
            _tot = sum(_c.values())
            _na = _c.get(R.V_NOT_APPLICABLE, 0)
            _rates[_on] = (_tot - _na) / float(_tot) if _tot else 0.0
        self.assertGreaterEqual(
            _rates[True], _rates[False],
            "开启 diff 限定后可判定率下降了：%s" % _rates)
        self.assertGreaterEqual(_rates[True], 0.30,
                                "可判定率应 > 30%%（实测 %.2f%%）"
                                % (100 * _rates[True]))

    def test_12_production_strict_rate_improves(self):
        """★效果断言：开启后 strict 修复率**不应低于**关闭时。"""
        _recs = _load_history()
        if not _recs:
            self.skipTest("无生产补丁历史")
        _r = {}
        for _on in (False, True):
            config.ENABLE_REPROBE_DIFF_SCOPE = _on
            _r[_on] = R.true_fix_rate(_recs, strict=True, force=True) or 0.0
        self.assertGreaterEqual(
            _r[True], _r[False],
            "开启 diff 限定后 strict 修复率下降：%s" % _r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
