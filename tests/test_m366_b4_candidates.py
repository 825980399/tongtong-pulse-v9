# -*- coding: utf-8 -*-
"""366 刀B4 验收：四键影子接线＋候选入料（开关 False 零行为变化）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.evolution.patch_candidates_consumer as cc  # noqa: E402
import nucleus.evolution.patch_generator as pg  # noqa: E402
from nucleus.mnemosyne.experience_retriever import ExperienceRetriever  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _row(**kw):
    _b = {"id": "k" * 16, "file": "nucleus/a.py", "method": "f", "line": 10,
          "issue_type": "dead_code", "severity": "low", "desc": "没人调用",
          "status": "claimed", "route": "patch_pool", "history": [],
          "original_code": "SECRET", "modified_code": "SECRET2"}
    _b.update(kw)
    return _b


class TestB4CandidateConsumer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "patch_candidates.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rows):
        with open(self.path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ① 白名单取字段：审计留痕/状态机/代码字段一律不进上下文
    def test_01_whitelist_only(self):
        self._write([_row()])
        r = cc.consume_patch_candidates(limit=5, candidate_file=self.path)
        self.assertEqual(r["state"], "ok", r)
        e = r["entries"][0]
        for _k in ("status", "route", "history", "original_code", "modified_code"):
            self.assertNotIn(_k, e, _k)
        for _k in cc.CONTEXT_FIELDS:
            self.assertIn(_k, e, _k)
        self.assertEqual(e["dedup_key"], "k" * 16)

    # ② 缺必填 ⇒ 拒收并计数（不猜、不补默认）
    def test_02_missing_required_rejected(self):
        self._write([_row(desc=""), _row(id="b" * 16, method="")])
        r = cc.consume_patch_candidates(limit=5, candidate_file=self.path)
        self.assertEqual(r["used"], 0)
        self.assertEqual(r["skipped_bad"], 2)

    # ③ 单次运行内按 id 去重
    def test_03_dedup_in_run(self):
        self._write([_row(), _row(desc="重复")])
        r = cc.consume_patch_candidates(limit=5, candidate_file=self.path)
        self.assertEqual(r["used"], 1)
        self.assertEqual(r["skipped_dup"], 1)

    # ④ 跨拍：已入审批流（pending 的 patch_id/dedup_key 命中）跳过
    def test_04_skip_already_pending(self):
        _p = os.path.join(self._tmp.name, "pending.json")
        with open(_p, "w", encoding="utf-8") as f:
            json.dump([{"patch_id": "k" * 16}], f)
        self._write([_row()])
        r = cc.consume_patch_candidates(limit=5, pending_path=_p, candidate_file=self.path)
        self.assertEqual(r["used"], 0)
        self.assertEqual(r["skipped_dup"], 1)

    # ⑤ 键继承：缺 id ⇒ make_dedup_key 兜底（公式单点，同源）
    def test_05_key_fallback(self):
        self._write([_row(id="")])
        r = cc.consume_patch_candidates(limit=5, candidate_file=self.path)
        from nucleus.audit_problem_pool import make_dedup_key
        self.assertEqual(r["entries"][0]["dedup_key"],
                         make_dedup_key("nucleus/a.py", "f", "dead_code"))

    # ⑥ 生成器入口：开关 False ⇒ disabled，零生成
    def test_06_generator_disabled(self):
        with mock.patch.object(pg, "generator_enabled", lambda: False):
            r = pg.generate_from_candidates(limit=3)
        self.assertEqual(r["status"], "disabled")
        self.assertEqual(r["generated"], 0)


class TestB4SwitchWiring(unittest.TestCase):
    # ⑦ 邻接展开形态分支：关 ⇒ list 归一；开 ⇒ dict 归一（两态都不炸调用方）
    def test_07_adjacency_shape_both_states(self):
        _r = ExperienceRetriever.__new__(ExperienceRetriever)
        _r.top_k = lambda: 5
        _fake = [{"id": "1"}]
        with mock.patch.object(ExperienceRetriever, "retrieve",
                               lambda *a, **k: list(_fake)), \
             mock.patch("nucleus.mnemosyne.experience_retriever._graph_adjacency_enabled",
                        lambda: False):
            _o = _r.retrieve_with_adjacency("q")
        self.assertEqual(_o["direct"], _fake)
        self.assertEqual(_o["adjacent"], [])
        self.assertFalse(_o["enabled"])
        with mock.patch.object(ExperienceRetriever, "retrieve",
                               lambda *a, **k: {"direct": list(_fake),
                                                "adjacent": [{"id": "2"}]}), \
             mock.patch("nucleus.mnemosyne.experience_retriever._graph_adjacency_enabled",
                        lambda: True):
            _o2 = _r.retrieve_with_adjacency("q")
        self.assertEqual(_o2["direct"], _fake)
        self.assertEqual(_o2["adjacent"], [{"id": "2"}])
        self.assertTrue(_o2["enabled"])

    # ⑧ 器官清单导出：开关关 ⇒ disabled；挂载点在既有 tick（不新增定时器）
    def test_08_inventory_tick_and_mount(self):
        from organs.core.PulseMetricsCollector import PulseMetricsCollector
        _c = PulseMetricsCollector.__new__(PulseMetricsCollector)
        with mock.patch("nucleus.cognitive.organ_inventory._inventory_on",
                        lambda: False):
            self.assertEqual(_c._m366_inventory_tick()["status"], "disabled")
        with open(os.path.join(_ROOT, "organs", "core", "PulseMetricsCollector.py"),
                  encoding="utf-8") as f:
            _s = f.read()
        self.assertIn("self._m366_inventory_tick()", _s)
        # 挂载必须发生在既有 tick `_collect_memory_pressure` 内
        _seg = _s.split("def _collect_memory_pressure", 1)[1]
        self.assertIn("_m366_inventory_tick()", _seg.split("def _", 1)[0])


if __name__ == "__main__":
    unittest.main()
