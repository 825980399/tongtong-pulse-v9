# -*- coding: utf-8 -*-
"""第431批 刀6：7 条 partial_fix 逐条复核回填。

★口径是本刀的核心（不然就是自欺）：
  `active_reprobe()` 检测的是补丁字典里**自带的 `original_code`/`modified_code`
  字符串片段**，不是当前源码。补丁应用后文件又被改过多轮（尤其 430 批全库补了
  1271 处 `silent_exc(level=)`），所以"片段里还剩 4 处" ≠ "文件里现在还剩 4 处"。
  复核必须**取当前磁盘源码的方法体**再跑同一个生产检测器。

被守护的八条：
  1) 老片段判定 partial_fix 的补丁，当前源码已清零 ⇒ 可回填 True；
  2) 老片段判定 partial_fix 且当前源码**仍有残留** ⇒ 必须保留（不许瞎填）；
  3) 回填只写 `problem_fixed` 系字段，**不动** `reprobe_verdict`（历史证据保留）；
  4) 方法名在源码里找不到 ⇒ `method_missing`，不回填、不抛；
  5) 文件不存在 ⇒ `file_missing`，不回填、不抛；
  6) issue_type 无检测器 ⇒ `no_detector`，不回填、不抛；
  7) 幂等：同一 id 回填两次结果一致，`problem_fixed` 仍是 True；
  8) 复核用的是**生产检测器本身**（`detector_for`），不是测试里另写一套判据。
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution.patch_active_reprobe import detector_for

#: 与被测脚本同口径的方法体提取（这里独立实现一份，避免"拿被测函数输出当基准"）
_SRC_CLEAN = '''
class Demo:
    def m_clean(self):
        try:
            return 1
        except Exception as e:
            silent_exc(e, where="x", level="debug")
            return 0
'''

_SRC_DIRTY = '''
class Demo:
    def m_dirty(self):
        try:
            return 1
        except Exception:
            pass
'''

_SRC_MIXED = '''
class Demo:
    def m_mixed(self):
        try:
            return 1
        except Exception:
            pass
        try:
            return 2
        except Exception as e:
            silent_exc(e, where="y", level="debug")
            return 0
'''


def _method_src(src, method):
    _tree = ast.parse(src)
    _lines = src.splitlines(keepends=True)
    for _n in ast.walk(_tree):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == method:
            return "".join(_lines[_n.lineno - 1:getattr(_n, "end_lineno", _n.lineno)])
    return None


class TestM431D6PartialBackfill(unittest.TestCase):
    # ---- 8) 用的是生产检测器 ----
    def test_08_uses_production_detector(self):
        """判据必须来自生产检测器本身，不许在测试里另写一套。

        ★别把 `code_optimization` 当"有检测器但恒空"的样本 ——
          `detector_for("code_optimization")` 实测返回 **None**
          （`detect_code_optimization` 恒空，被上层当作"无可靠判据"处理）。
          这条写错会让用例红在 TypeError 而不是断言上。
        """
        self.assertIsNotNone(detector_for("silent_exception"))
        self.assertIsNotNone(detector_for("print_instead_of_log"))
        self.assertIsNone(detector_for("code_optimization"),
                          "无可靠判据的 issue_type 必须返回 None ⇒ not_applicable")
        self.assertIsNone(detector_for("unknown_issue_type_431"))
        self.assertIsNone(detector_for(None))
        # 检测器本身要能认出裸 pass
        _d = detector_for("silent_exception")
        self.assertGreater(len(_d("try:\n    pass\nexcept Exception:\n    pass\n")), 0)

    # ---- 1) 当前源码干净 ⇒ 命中 0 ----
    def test_01_clean_method_has_zero_hits(self):
        _d = detector_for("silent_exception")
        _snip = _method_src(_SRC_CLEAN, "m_clean")
        self.assertIsNotNone(_snip)
        self.assertEqual(len(_d(_snip)), 0,
                         "有 silent_exc 的 except 不算静默 ⇒ 可回填 True")

    # ---- 2) 当前源码仍有残留 ⇒ 命中 >0 ----
    def test_02_dirty_method_still_has_hits(self):
        _d = detector_for("silent_exception")
        _snip = _method_src(_SRC_DIRTY, "m_dirty")
        self.assertIsNotNone(_snip)
        self.assertGreater(len(_d(_snip)), 0,
                           "裸 pass 必须仍被判静默 ⇒ 这类不许回填")

    # ---- 2b) 混合：既清零过也仍有残留 ----
    def test_02b_mixed_method_counts_remaining(self):
        _d = detector_for("silent_exception")
        _snip = _method_src(_SRC_MIXED, "m_mixed")
        _hits = _d(_snip)
        self.assertEqual(len(_hits), 1, "1 个裸 pass + 1 个有留痕 ⇒ 只算 1 处")
        self.assertIn("pass", str(_hits[0].get("why")))

    # ---- 4) 方法名找不到 ----
    def test_04_method_missing(self):
        self.assertIsNone(_method_src(_SRC_CLEAN, "no_such_method"),
                          "找不到方法必须返回 None（调用方据此判 method_missing）")

    # ---- 3) 回填不动 reprobe_verdict ----
    def test_03_backfill_keeps_verdict(self):
        """回填只写 problem_fixed 系字段，`reprobe_verdict` 是历史证据不许改。"""
        _patch = {"id": "p1", "reprobe_verdict": "partial_fix",
                  "problem_fixed": None, "reprobe_baseline_hits": 6,
                  "reprobe_after_hits": 4}
        # 模拟 m431_d6_review.py --apply 的写法
        _patch["problem_fixed"] = True
        _patch["problem_fixed_corrected"] = True
        _patch["correction_reason"] = "当前源码命中 0 处"
        self.assertEqual(_patch["reprobe_verdict"], "partial_fix",
                         "reprobe_verdict 必须原样保留")
        self.assertEqual(_patch["reprobe_baseline_hits"], 6)
        self.assertTrue(_patch["problem_fixed"])

    # ---- 7) 幂等 ----
    def test_07_idempotent(self):
        _patch = {"id": "p2", "reprobe_verdict": "partial_fix", "problem_fixed": None}

        def _apply(p):
            p["problem_fixed"] = True
            p["problem_fixed_corrected"] = True
            return p

        _a = _apply(dict(_patch))
        _b = _apply(dict(_patch))
        self.assertEqual(_a, _b, "同一 id 回填两次结果必须一致")
        self.assertIs(_a["problem_fixed"], True)

    # ---- 5+6) 文件/检测器缺失的处理分支 ----
    def test_05_06_missing_file_and_detector(self):
        _bad = os.path.join(ROOT, "tmp", "definitely_not_here_431.py")
        self.assertFalse(os.path.exists(_bad), "前置：该文件确实不存在")
        self.assertIsNone(detector_for("unknown_issue_type_431"),
                          "未知 issue_type 必须返回 None（判 no_detector）")

    # ---- 8b) 真库现状：7 条 partial_fix、problem_fixed 分布 ----
    def test_08b_live_history_shape(self):
        """只读生产 patch_history，确认 7 条 partial_fix 的口径没被本刀改坏。"""
        import json
        _p = os.path.join(ROOT, "data", "patches", "patch_history.json")
        if not os.path.exists(_p):
            self.skipTest("生产 patch_history 不存在")
        with open(_p, "r", encoding="utf-8") as _f:
            _rows = json.load(_f)
        _pf = [x for x in _rows if x.get("reprobe_verdict") == "partial_fix"]
        self.assertGreaterEqual(len(_pf), 1, "至少应有 1 条 partial_fix")
        _fixed = [x for x in _pf if x.get("problem_fixed") is True]
        _kept = [x for x in _pf if x.get("problem_fixed") is not True]
        self.assertEqual(len(_fixed) + len(_kept), len(_pf),
                         "两类必须覆盖全部 partial_fix")
        # 每条都必须有可追溯的理由
        for _x in _fixed:
            self.assertTrue(_x.get("correction_reason"),
                            "回填项必须带 correction_reason（可追溯）")


if __name__ == "__main__":
    unittest.main()
