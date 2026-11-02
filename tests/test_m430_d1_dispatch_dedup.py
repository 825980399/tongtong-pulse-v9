# -*- coding: utf-8 -*-
"""第430批 刀1：派单侧「同一条不重复派」+ 待派池结构性拒收归因。

被守护的五条：
  1) 已派成功（`dispatched_ok=True`）⇒ `_m324_blocked_reason` 返回 `already_dispatched_ok`，
     **不再二次派单**（这是任务书刀1「派过的打个标记」的落点）；
  2) 存量数据只带 372刀3 的 `sandbox_ok=True` ⇒ 同样拦住（**无需数据迁移**，向后兼容）；
  3) 没有任何标记的普通补丁 ⇒ 不被拦（新闸不得变成"全拦"）；
  4) `pending_pool_diagnosis()` 按 `select_pending_candidates` 同一门槛顺序分类，
     11 条生产形态的池子能一次报出「每关挡几条」；
  5) 诊断**只读** —— 不污染生产的 `_M363_PENDING_REJ`（否则归因会被诊断自己带偏）。

★全部用注入 rows / 隔离 tmp 路径，不读生产 `data/`。
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.evolution import patch_execution_dispatch as ped


def _p(**kw):
    d = {"id": "x", "file": "organs/body/Demo.py", "method": "m",
         "status": "runtime_verified", "risk_level": "低", "trust_score": 60,
         "modified_code": "a = 1\n", "original_code": "a = 1\n"}
    d.update(kw)
    return d


class TestM430D1DispatchDedup(unittest.TestCase):
    def test_01_dispatched_ok_blocks_redispatch(self):
        """已派成功 ⇒ 拦住，不重复派。"""
        _why = ped._m324_blocked_reason(_p(dispatched_ok=True), ledger={})
        self.assertEqual(_why, "already_dispatched_ok")

    def test_02_legacy_sandbox_ok_also_blocks(self):
        """存量只带 `sandbox_ok=True`（372刀3 的成功标记）⇒ 同样拦住。"""
        _why = ped._m324_blocked_reason(_p(sandbox_ok=True), ledger={})
        self.assertEqual(_why, "already_dispatched_ok")

    def test_03_plain_patch_not_blocked(self):
        """没有任何成功标记 ⇒ 不拦（新闸不得变成全拦）。"""
        self.assertEqual(ped._m324_blocked_reason(_p(), ledger={}), "")

    def test_04_failed_patch_still_retryable(self):
        """失败件（有 fail count 但未到 cap）⇒ 仍可重试，不被新闸误伤。"""
        p = _p(runtime_verify_fail_count=1)
        self.assertEqual(ped._m324_blocked_reason(p, ledger={}), "")


class TestM430D1PoolDiagnosis(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_05_diagnosis_classifies_each_gate(self):
        """六类拒收各造一条，归因必须逐关对上。

        `sandbox` 这条走 `sandbox_incompatible=True` 显式字段（判据第一分支）——
        不用代码字面量去碰 365刀A2 的 repo_idiom 放行逻辑：那条路径会
        「预筛放行、交沙箱终判」，实测 `getattr(self,'a',0)` 属 repo_idiom
        惯用法，`is_sandbox_incompatible` 返回 False，会串到 `blast_unknown`。
        """
        rows = [
            _p(id="g_o3", source="o3_open_gate"),                      # o3
            _p(id="g_status", status="pending"),                        # status
            _p(id="g_risk", risk_level="高"),                           # risk
            _p(id="g_trust", trust_score=10),                           # trust
            _p(id="g_frag", modified_code="def m(self):\n    a = 1\n"),  # fragment
            _p(id="g_sbx", sandbox_incompatible=True),                  # sandbox
        ]
        d = ped.pending_pool_diagnosis(rows=rows)
        self.assertEqual(d["rows"], 6)
        for _k in ("o3", "status", "risk", "trust", "fragment", "sandbox"):
            self.assertEqual(d["gates"].get(_k), 1, f"门槛 {_k} 应恰好命中 1 条：{d['gates']}")

    def test_06_diagnosis_is_readonly(self):
        """诊断不得写生产拒收分布（否则读一次就把归因带偏一次）。"""
        before = ped.get_m363_pending_rej()
        ped.pending_pool_diagnosis(rows=[_p(id="r1", risk_level="高")])
        self.assertEqual(ped.get_m363_pending_rej(), before)

    def test_07_empty_pool_is_zero_not_error(self):
        """空池 ⇒ rows=0、无 error（"没有"与"读失败"必须能分开）。"""
        d = ped.pending_pool_diagnosis(rows=[])
        self.assertEqual(d["rows"], 0)
        self.assertEqual(d["dispatchable"], 0)
        self.assertEqual(d["error"], "")

    def test_08_pending_file_missing_reports_zero(self):
        """pending 文件不存在 ⇒ rows=0 且不抛（节拍里不能因缺文件炸）。"""
        _miss = os.path.join(self.tmp, "nope", "pending_patches.json")
        p = ped._m335_pending_path(self.tmp, _miss)
        self.assertFalse(os.path.isfile(p))
        d = ped.pending_pool_diagnosis(project_root=self.tmp)
        self.assertEqual(d["rows"], 0)
        self.assertEqual(d["error"], "")

    def test_09_detail_carries_per_row_gate(self):
        """逐条明细要带 gate，报告能直接引用（不靠再算一遍）。"""
        d = ped.pending_pool_diagnosis(rows=[_p(id="okrow"),
                                            _p(id="badrow", risk_level="高")])
        _by = {r["id"]: r["gate"] for r in d["detail"]}
        self.assertEqual(_by["badrow"], "risk")
        self.assertIn(_by["okrow"], ("pass", "not_pilot", "blast_unknown", "sandbox",
                                     "fragment"))


if __name__ == "__main__":
    unittest.main()
