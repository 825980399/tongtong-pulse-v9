# -*- coding: utf-8 -*-
"""主线第55批 T7：SERP 经验库质量评估工具 tools/serp_quality_report.py 的契约测试

★铁律 35/40：用 tempfile 沙箱构造经验池，绝不读/写生产数据。
★本任务只评估不修改 —— 必须断言**评估前后文件字节不变**。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_SPEC = importlib.util.spec_from_file_location(
    "serp_quality_report_m55",
    os.path.join(_ROOT, "tools", "serp_quality_report.py"))
SQR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(SQR)

from nucleus.data import experience_cleanup as EC  # noqa: E402


def _rmtree(path: str) -> None:
    for _dp, _dns, _fns in os.walk(path, topdown=False):
        for _f in _fns:
            try:
                os.remove(os.path.join(_dp, _f))
            except OSError:
                pass
        for _d in _dns:
            try:
                os.rmdir(os.path.join(_dp, _d))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


class TestSerpQualityReport(unittest.TestCase):
    """评估工具的统计与漏网判据。"""

    def setUp(self) -> None:
        self.sb = tempfile.mkdtemp(prefix="m55_serp_")
        self.pool = os.path.join(self.sb, "pool.json")

    def tearDown(self) -> None:
        _rmtree(self.sb)

    def _w(self, recs: list) -> None:
        with io.open(self.pool, "w", encoding="utf-8", newline="") as _f:
            _f.write(json.dumps({"experiences": recs}, ensure_ascii=False))

    # ------------------------------------------------------------------
    def test_01_load_pool_variants(self):
        """能加载 list 与 dict(experiences=) 两种形态。"""
        with io.open(self.pool, "w", encoding="utf-8") as _f:
            _f.write(json.dumps([{"id": "a"}]))
        self.assertEqual(len(SQR.load_pool(self.pool)), 1)
        self._w([{"id": "a"}, {"id": "b"}])
        self.assertEqual(len(SQR.load_pool(self.pool)), 2)

    def test_02_marked_count_distinguishes_false(self):
        """★铁律 59：``is_cleaned=False`` 是**合法标记值**（表示已判污染），
        不能因为 falsy 就被漏统计。"""
        self._w([
            {"id": "1", "is_cleaned": False, "polluted": True},   # 已标记
            {"id": "2"},                                          # 无字段
            {"id": "3", "is_cleaned": True},                      # 干净
        ])
        _a = SQR.assess(self.pool)
        self.assertEqual(_a["total"], 3)
        self.assertEqual(_a["marked"], 1, "is_cleaned=False 必须计入已标记")
        self.assertEqual(_a["usable"], 2)

    def test_03_usable_equals_is_retrievable(self):
        """可用数应与检索闸门 ``is_retrievable`` 一致。"""
        _recs = [
            {"id": "1", "is_cleaned": False},
            {"id": "2"},
            {"id": "3", "polluted": True},
        ]
        self._w(_recs)
        _a = SQR.assess(self.pool)
        self.assertEqual(
            _a["usable"], sum(1 for r in _recs if EC.is_retrievable(r)))

    def test_04_missed_detection(self):
        """★核心：符合**明确污染特征**却未被标记 → 应被检出为漏网。"""
        _tpl = "我曾因不确定如何回答「某某问题」而行动，获得了cognitive奖赏"
        self._w([
            {"id": "tpl_unmarked", "summary": _tpl},          # 模板特征、未标记
            {"id": "tpl_marked", "summary": _tpl, "is_cleaned": False},
            {"id": "clean", "summary": "今天讨论了某个技术方案的取舍"},
        ])
        _a = SQR.assess(self.pool)
        _ids = {m["id"] for m in _a["missed"]}
        self.assertIn("tpl_unmarked", _ids, "未标记的模板摘要应被检出")
        self.assertNotIn("tpl_marked", _ids, "已标记的不应重复计入漏网")
        self.assertNotIn("clean", _ids, "正常摘要不应被判污染")

    def test_05_other_class_not_treated_as_pollution(self):
        """``other_polluted`` 是**兜底类**（无明确特征）→ 不得据此判漏网。"""
        self._w([{"id": "x", "summary": "一段完全正常但不含任何特征的经验"}])
        _a = SQR.assess(self.pool)
        self.assertEqual(_a["missed_count"], 0,
                         "无明确特征的记录不得被判为漏网")

    def test_06_quality_stats(self):
        """质量统计：空摘要 / 短摘要 / 平均长度。"""
        self._w([
            {"id": "1", "summary": "a" * 10},
            {"id": "2", "summary": "b" * 100},
            {"id": "3", "summary": ""},
        ])
        _a = SQR.assess(self.pool)
        self.assertEqual(_a["quality"]["empty_summary"], 1)
        self.assertEqual(_a["quality"]["short_lt20"], 1)
        self.assertGreater(_a["quality"]["avg_len"], 0)

    # ------------------------------------------------------------------
    def test_07_assess_does_not_modify_pool(self):
        """★核心纪律：只评估不修改 —— 评估前后文件**字节不变**。"""
        self._w([{"id": "1", "summary": "x" * 30}])
        _before = io.open(self.pool, "rb").read()
        SQR.assess(self.pool)
        SQR.render_md(SQR.assess(self.pool))
        _after = io.open(self.pool, "rb").read()
        self.assertEqual(_before, _after, "评估不得修改经验池")

    def test_08_render_contains_sections(self):
        """报告含四节 + 「不修改」纪律声明。"""
        self._w([{"id": "1", "summary": "x" * 30}])
        _md = SQR.render_md(SQR.assess(self.pool))
        for _k in ("## 一、总体", "## 二、污染标记分布",
                   "## 三、", "## 四、可用池内容质量", "只评估，不修改"):
            self.assertIn(_k, _md)

    def test_09_empty_pool_no_crash(self):
        """空池不崩溃，且不出现 0/0 型虚假满分（铁律 25）。"""
        self._w([])
        _a = SQR.assess(self.pool)
        self.assertEqual(_a["total"], 0)
        self.assertEqual(_a["marked_rate"], 0.0)
        self.assertEqual(_a["usable_rate"], 0.0)
        self.assertEqual(_a["missed_rate"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
