# -*- coding: utf-8 -*-
"""第237批 隔离单测：B3 肺应答回传布线（账本+回执两钩）。

覆盖：空id不入账 / 命中回执(调indexer) / 未命中直过(不调indexer) / TTL清理 (+容量自清)。
不依赖 framework：_FakeCortex 继承 PulseCortex 但跳过真实 __init__，仅置本批所需最小属性。
"""
import threading
import time
import unittest
from unittest import mock

import nucleus.reasoning.ReasoningExperienceIndexer as _idx_mod
from organs.brain.PulseCortex import PulseCortex as PC


class _FakeCortex(PC):
    def __init__(self):
        # 跳过真实 __init__（避免依赖 framework），仅置本批所需最小属性
        self._pending_receipt_ledger: dict = {}
        self._pending_receipt_ledger_lock = threading.RLock()
        self._pending_receipt_ledger_max = 512
        self._pending_receipt_ledger_ttl = 3600.0
        self._log = lambda *a, **k: None


class TestB3ReceiptLedger(unittest.TestCase):
    # 例1：空 id 不入账
    def test_empty_id_not_recorded(self):
        c = _FakeCortex()
        c._receipt_ledger_record("", "问题X")
        self.assertEqual(c._receipt_ledger_size(), 0)

    # 例2：命中回执 → 调 indexer.record_remediation_returned（灭双记）且出账
    def test_hit_records_remediation_returned(self):
        c = _FakeCortex()
        c._receipt_ledger_record("cid1", "问题A")
        _mock_idx = mock.MagicMock()
        with mock.patch.object(
                _idx_mod, "get_reasoning_experience_indexer",
                return_value=_mock_idx):
            _out = c._maybe_append_self_state("真答123", "cid1", "用户问")
        self.assertTrue(_mock_idx.record_remediation_returned.called)
        _kw = _mock_idx.record_remediation_returned.call_args.kwargs
        self.assertEqual(_kw.get("question"), "问题A")
        self.assertEqual(_kw.get("answer"), "真答123")
        self.assertEqual(_kw.get("rule_candidate"), "lung_remediation:returned")
        self.assertEqual(c._receipt_ledger_size(), 0)  # 出账后清空
        self.assertEqual(_out, "真答123")  # self_awareness 缺省→原样返回

    # 例3：未命中直过 → 不调 indexer
    def test_miss_passes_through(self):
        c = _FakeCortex()
        _mock_idx = mock.MagicMock()
        with mock.patch.object(
                _idx_mod, "get_reasoning_experience_indexer",
                return_value=_mock_idx):
            _out = c._maybe_append_self_state("答", "cid_unknown", "问")
        self.assertFalse(_mock_idx.record_remediation_returned.called)
        self.assertEqual(_out, "答")

    # 例4：TTL 清理
    def test_ttl_cleanup(self):
        c = _FakeCortex()
        _old = time.time() - 99999.0
        _now = time.time()
        c._pending_receipt_ledger["old"] = ("q_old", _old)
        c._pending_receipt_ledger["fresh"] = ("q_fresh", _now)
        c._receipt_ledger_sweep()
        self.assertNotIn("old", c._pending_receipt_ledger)
        self.assertIn("fresh", c._pending_receipt_ledger)

    # 附加：容量上限自清（超过 max 剔除最旧）
    def test_capacity_cleanup(self):
        c = _FakeCortex()
        c._pending_receipt_ledger_max = 3
        _t0 = time.time()
        for i in range(5):
            c._pending_receipt_ledger[f"k{i}"] = (f"q{i}", _t0 + i)
        c._receipt_ledger_sweep()
        self.assertEqual(c._receipt_ledger_size(), 3)
        self.assertNotIn("k0", c._pending_receipt_ledger)  # 最旧被剔除
        self.assertIn("k4", c._pending_receipt_ledger)


if __name__ == "__main__":
    unittest.main()
