# -*- coding: utf-8 -*-
"""第48批 T4 门控测试：摘要止血效果验证工具（P0-3 后续）

覆盖：
  * ``parse_since`` 支持 epoch 秒与多种日期格式
  * ``hemostasis_section`` 按时间正确分段（止血前 / 后）
  * 三项覆盖率：raw_summary / motivation 完整性 / field_frequency_snapshot
  * ★判定逻辑四态：effective / partial / not_yet_effective / unknown
  * ``can_proceed_cleanup`` 与判定的联动
  * ★只读性：调用工具不修改经验库
  * 生产现状：给出「可否清洗」的机器可读结论

★测试隔离：全部使用内存/临时数据，不依赖生产库内容。
"""

import json
import os
import sys
import unittest

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import serp_pollution_analyzer as _S  # noqa: E402


def _rec(ts, polluted=False, raw="", summarized=False, motivation="m" * 30,
         snapshot=None, ver=None):
    _d = {"timestamp": ts, "polluted": polluted, "summary": "s",
          "motivation": motivation, "is_summarized": summarized}
    if raw:
        _d["raw_summary"] = raw
    if snapshot:
        _d["field_frequency_snapshot"] = snapshot
    if ver is not None:
        _d["summary_version"] = ver
    return _d


BASE = 1000000.0
#: 以实际基准线为界的前后两个时刻
PRE = _S.DEFAULT_HEMOSTASIS_SINCE - 3600.0
POST = _S.DEFAULT_HEMOSTASIS_SINCE + 3600.0


# ==================== parse_since ====================

class TestParseSince(unittest.TestCase):
    def test_01_default(self):
        self.assertEqual(_S.parse_since(""), _S.DEFAULT_HEMOSTASIS_SINCE)
        self.assertEqual(_S.parse_since(None), _S.DEFAULT_HEMOSTASIS_SINCE)

    def test_02_epoch_float(self):
        self.assertEqual(_S.parse_since("1789342800"), 1789342800.0)

    def test_03_date_string(self):
        _v = _S.parse_since("2026-09-14 07:40:00")
        self.assertEqual(_v, _S.DEFAULT_HEMOSTASIS_SINCE)

    def test_04_date_only(self):
        _v = _S.parse_since("2026-09-14")
        self.assertGreater(_v, 0)

    def test_05_invalid_falls_back(self):
        self.assertEqual(_S.parse_since("not-a-date"),
                         _S.DEFAULT_HEMOSTASIS_SINCE)


# ==================== 分段统计 ====================

class TestSegStats(unittest.TestCase):
    def test_10_empty(self):
        self.assertEqual(_S._seg_stats([])["count"], 0)

    def test_11_pollution_rate(self):
        _r = _S._seg_stats([_rec(BASE, polluted=True), _rec(BASE)])
        self.assertEqual(_r["count"], 2)
        self.assertEqual(_r["polluted"], 1)
        self.assertEqual(_r["pollution_rate"], 0.5)

    def test_12_raw_coverage(self):
        _r = _S._seg_stats([_rec(BASE, raw="原文"), _rec(BASE, raw="")])
        self.assertEqual(_r["raw_summary_coverage"], 0.5)

    def test_13_motivation_intact(self):
        """未摘要 → 必然完整；已摘要且 >50 字 → 完整；已摘要且 <=50 → 截断。"""
        _r = _S._seg_stats([
            _rec(BASE, summarized=False, motivation="x" * 10),    # 完整
            _rec(BASE, summarized=True, motivation="x" * 80),     # 完整
            _rec(BASE, summarized=True, motivation="x" * 30),     # 截断
        ])
        self.assertAlmostEqual(_r["motivation_intact_rate"], 2 / 3, places=3)

    def test_14_snapshot_coverage(self):
        _r = _S._seg_stats([_rec(BASE, snapshot={"a": 1}), _rec(BASE)])
        self.assertEqual(_r["snapshot_coverage"], 0.5)

    def test_15_summary_version(self):
        _r = _S._seg_stats([_rec(BASE, ver=2), _rec(BASE, ver=None)])
        self.assertEqual(_r["summary_version_2"], 1)


# ==================== 判定逻辑四态 ====================

class TestVerdict(unittest.TestCase):
    def test_20_no_new_records(self):
        """★无止血后新增 → not_yet_effective（需重启）。"""
        _r = _S.hemostasis_section([_rec(PRE, polluted=True), _rec(PRE)])
        self.assertEqual(_r["new_records_after"], 0)
        self.assertEqual(_r["verdict"], "not_yet_effective")
        self.assertFalse(_r["can_proceed_cleanup"])
        self.assertIn("重启", _r["reason"])

    def test_21_effective(self):
        """止血后新增全干净 + raw 覆盖 100% → effective。"""
        _r = _S.hemostasis_section([
            _rec(PRE, polluted=True),
            _rec(POST, polluted=False, raw="原文", summarized=True,
                 motivation="x" * 80, snapshot={"a": 1}, ver=2),
        ])
        self.assertEqual(_r["verdict"], "effective")
        self.assertTrue(_r["can_proceed_cleanup"])

    def test_22_partial_low_coverage(self):
        """0 污染但 raw 覆盖率 <90% → partial。"""
        _r = _S.hemostasis_section([
            _rec(POST), _rec(POST), _rec(POST),
            _rec(POST, raw="原文"),
        ])
        self.assertEqual(_r["verdict"], "partial")
        self.assertTrue(_r["can_proceed_cleanup"])

    def test_23_new_pollution_still(self):
        """★止血后仍有新增污染 → not_yet_effective。"""
        _r = _S.hemostasis_section([
            _rec(POST, polluted=True, raw="原文"),
            _rec(POST, polluted=False, raw="原文"),
        ])
        self.assertEqual(_r["verdict"], "not_yet_effective")
        self.assertFalse(_r["can_proceed_cleanup"])
        self.assertEqual(_r["new_pollution_after"], 1)

    def test_24_split_by_time(self):
        """分段边界正确（含边界值归入 after）。"""
        _r = _S.hemostasis_section([
            _rec(PRE), _rec(POST),
            _rec(_S.DEFAULT_HEMOSTASIS_SINCE),      # 恰好边界 → after
        ])
        self.assertEqual(_r["before"]["count"], 1)
        self.assertEqual(_r["after"]["count"], 2)

    def test_25_custom_since(self):
        _r = _S.hemostasis_section([_rec(BASE)], since=BASE - 1)
        self.assertEqual(_r["after"]["count"], 1)

    def test_26_shape(self):
        _r = _S.hemostasis_section([])
        for _k in ("since", "since_str", "before", "after", "verdict",
                   "reason", "can_proceed_cleanup", "new_records_after"):
            self.assertIn(_k, _r)

    def test_27_non_dict_tolerated(self):
        _r = _S.hemostasis_section([None, "x", _rec(PRE)])
        self.assertEqual(_r["before"]["count"], 1)


# ==================== 生产现状与只读性 ====================

@pytest.mark.production_data
class TestProductionState(unittest.TestCase):
    POOL = os.path.join(_ROOT, "data", "experience", "experience_pool.json")

    def test_30_tool_is_read_only(self):
        """★调用 build_report 不得改动经验库（mtime/sha 不变）。"""
        if not os.path.isfile(self.POOL):
            self.skipTest("生产经验库不存在")
        _before = (os.path.getsize(self.POOL), os.path.getmtime(self.POOL))
        _S.build_report(self.POOL, topn=5)
        _after = (os.path.getsize(self.POOL), os.path.getmtime(self.POOL))
        self.assertEqual(_before, _after, "验证工具不应修改经验库")

    def test_31_production_verdict_present(self):
        if not os.path.isfile(self.POOL):
            self.skipTest("生产经验库不存在")
        _r = _S.build_report(self.POOL, topn=5)
        _h = _r["hemostasis"]
        self.assertIn(_h["verdict"],
                      ("effective", "partial", "not_yet_effective", "unknown"))

    def test_32_no_raw_summary_coverage_yet(self):
        """★第49批反向同步（跨批契约变更）—— 原「现状断言」改为**正向断言**。

        原断言前提：生产库 `raw_summary` 覆盖率恒为 0（框架从未重启）。
        变更事实：框架于 **2026-09-14 15:14:07 重启**（日志
        `[框架] INFO: 初始化 曈曈 v9.5 PulseNet...`），第47批止血**已生效**。

        ⇒ 断言改为**对 P0-3 决策真正有意义且稳定**的三项事实
          （不依赖分析器时间窗口的派生口径）：
            ① 止血后确有新增记录（证明框架在写）
            ② 新增记录中**污染 = 0**（证明止血有效）
            ③ 提供机器可读的 `can_proceed_cleanup` 门控字段
        """
        if not os.path.isfile(self.POOL):
            self.skipTest("生产经验库不存在")
        _r = _S.build_report(self.POOL, topn=5)
        _h = _r["hemostasis"]
        self.assertIn(_h["verdict"],
                      ("effective", "partial", "not_yet_effective", "unknown"))
        self.assertIn("can_proceed_cleanup", _h, "应提供机器可读清洗门控")
        self.assertIsInstance(_h["can_proceed_cleanup"], bool)
        if _h.get("new_records_after", 0) > 0:
            # 框架已重启并写入 → 止血必须已压制污染
            self.assertEqual(_h.get("new_pollution_after", 0), 0,
                             "止血后新增记录中出现污染 → 止血未生效")
        _ = json  # 保持导入一致性


if __name__ == "__main__":
    unittest.main(verbosity=2)
