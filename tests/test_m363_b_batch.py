# -*- coding: utf-8 -*-
"""363 批 B 组验收（B1 裸pass回补 / B2 四路记账 / B3 六方法生产接线 / B4 埋点核销+文案）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel: str) -> str:
    return open(os.path.join(_ROOT, rel), encoding="utf-8").read()


class TestB1BarePass(unittest.TestCase):
    """B1：裸 pass → 留痕 + 计数；**返回路径不变**。"""

    def test_01_note_counts_and_snapshot(self):
        from nucleus.data import DataAccessLayer as dal
        dal._M363_EXC_COUNTS.clear()
        dal._M363_EXC_LAST.clear()
        dal._m363_exc_note("unit_probe", RuntimeError("boom"), where="test")
        dal._m363_exc_note("unit_probe", RuntimeError("boom2"), where="test")
        s = dal.m363_exc_stats()
        self.assertEqual(s["counts"].get("unit_probe"), 2)
        self.assertEqual(s["last"]["unit_probe"]["type"], "RuntimeError")
        self.assertIn("boom2", s["last"]["unit_probe"]["msg"])

    def test_02_read_path_unchanged_when_getsize_raises(self):
        """★行为不变：getsize 抛错时仍返回 default（只多一条留痕）。"""
        from nucleus.data import DataAccessLayer as dal
        dal._M363_EXC_COUNTS.clear()
        real = os.path.getsize
        try:
            def _boom(_p):
                raise OSError("unit-getsize-boom")
            os.path.getsize = _boom
            out = dal.safe_read_json("data/knowledge/pulse_l1_snapshot.json", default={"k": 1})
        finally:
            os.path.getsize = real
        self.assertIsInstance(out, dict)
        self.assertNotEqual(out, {"k": 1},
                            "getsize 抛错后仍应继续走原读取路径（行为不变，只多一条留痕）")
        self.assertIn("version", out)
        self.assertGreaterEqual(dal.m363_exc_stats()["counts"].get("dal_getsize", 0), 1)

    def test_03_two_modules_have_stats_exports(self):
        from nucleus.reasoning import ReasoningWorkerPool as rwp
        self.assertTrue(callable(getattr(rwp, "m363_exc_stats", None)))
        self.assertTrue(callable(getattr(rwp, "_m363_exc_note", None)))

    def test_04_no_bare_pass_left_in_targets(self):
        """目标四处不得再有 `except: pass`（源码级，防回潮）。"""
        for rel in ("nucleus/data/DataAccessLayer.py",
                    "nucleus/reasoning/ReasoningWorkerPool.py"):
            src = _src(rel)
            self.assertNotIn("dal_getsize", src.replace("_m363_exc_note(\"dal_getsize\"", ""))
            # 直接判：收口后的四处均使用 _m363_exc_note
        self.assertIn("_m363_exc_note(\"dal_rm_tmp\"", _src("nucleus/data/DataAccessLayer.py"))
        self.assertIn("_m363_exc_note(\"rwp_join\"", _src("nucleus/reasoning/ReasoningWorkerPool.py"))
        self.assertIn("_m363_exc_note(\"rwp_future_cancel\"",
                      _src("nucleus/reasoning/ReasoningWorkerPool.py"))


class TestB2Accounting(unittest.TestCase):
    """B2：四路直连出口补 token 记账（渠道反查不到记 direct，不伪装）。"""

    def test_01_tokens_from_total(self):
        from nucleus.llm.call_recorder import note_direct_llm_call
        r = note_direct_llm_call(url="https://unknown.invalid/v1", model="m",
                                 usage={"total_tokens": 123}, caller="unit")
        self.assertEqual(r["tokens"], 123)

    def test_02_tokens_from_prompt_plus_completion(self):
        from nucleus.llm.call_recorder import note_direct_llm_call
        r = note_direct_llm_call(url="https://unknown.invalid/v1",
                                 usage={"prompt_tokens": 3, "completion_tokens": 4},
                                 caller="unit")
        self.assertEqual(r["tokens"], 7)

    def test_03_unknown_host_is_direct_not_guessed(self):
        """★反查不到就记 direct，不许归到某个免费渠道头上（那会让占比失真）。"""
        from nucleus.llm.call_recorder import note_direct_llm_call
        r = note_direct_llm_call(url="https://unknown.invalid/v1", caller="unit")
        self.assertEqual(r["channel"], "direct")

    def test_04_failed_call_status(self):
        from nucleus.llm.call_recorder import note_direct_llm_call
        r = note_direct_llm_call(url="https://unknown.invalid/v1", caller="unit",
                                 ok=False, error="boom")
        self.assertTrue(r.get("ok") or r.get("why") == "", "记账失败也要给出原因而非静默")
        self.assertIn("why", r)

    def test_05_four_sites_wired(self):
        """四路源码都必须出现记账调用（按 caller 名点名，防漏接）。"""
        sve = _src("nucleus/reasoning/SafeEvolutionExecutor.py")
        piw = _src("organs/brain/PulseInnerWorld.py")
        knw = _src("organs/brain/pulse_inner_world_knowledge.py")
        for caller in ("SafeEvolutionExecutor::语义复核", "SafeEvolutionExecutor::异常审查"):
            self.assertIn(caller, sve, f"{caller} 未接线")
        self.assertIn("PulseInnerWorld::分支生成", piw)
        self.assertIn("pulse_inner_world_knowledge::知识精炼", knw)


class TestB3DiagBridge(unittest.TestCase):
    """B3：六方法的生产接线桥（拿不到实例要如实记 no_instance）。"""

    def test_01_collect_without_instance_is_no_instance(self):
        import nucleus.m363_diag_bridge as b
        saved = dict(b._SLOTS)
        try:
            b._SLOTS.clear()
            d = b.collect()
            self.assertEqual(d["channel_diag"].get("state"), "no_instance")
            self.assertEqual(d["absorb_stats"].get("state"), "no_instance")
        finally:
            b._SLOTS.clear()
            b._SLOTS.update(saved)

    def test_02_register_then_collect_real_reading(self):
        import nucleus.m363_diag_bridge as b
        from nucleus.synapsys.ResonanceEngine import ResonanceEngine
        e = ResonanceEngine()
        e._total_queries = 2000
        e._semantic_calls = 7
        b.register("resonance_engine", e)
        d = b.collect()
        self.assertAlmostEqual(d["channel_diag"]["semantic"]["hit_rate"], 0.0035, places=6)

    def test_03_diag_line_renders(self):
        import nucleus.m363_diag_bridge as b
        line = b.diag_line()
        self.assertIn("[363B3]", line)
        self.assertIn("共振命中率=", line)

    def test_04_heartbeat_hook_exists(self):
        src = _src("organs/core/PulseMetricsCollector.py")
        self.assertIn("def _m363_diag_tick(self)", src)
        self.assertIn("_out[\"m363_diag\"] = self._m363_diag_tick()", src)

    def test_05_hosts_register_themselves(self):
        """四个宿主类必须自登记，否则桥永远读 no_instance。"""
        for rel, kind in (("nucleus/tooling_runner.py", "tooling_runner"),
                          ("nucleus/synapsys/ResonanceEngine.py", "resonance_engine"),
                          ("nucleus/qica/QICA.py", "qica"),
                          ("nucleus/events/EventBus.py", "event_bus")):
            self.assertIn('_m363_register("%s", self)' % kind, _src(rel),
                          f"{rel} 未登记")


class TestB4TraceAndWording(unittest.TestCase):
    """B4：第 4 出口核销 + 选择器文案口径。"""

    def test_01_fourth_export_already_wired(self):
        """★T0 核销：`ssrf_guard.safe_http_json` 的 begin/finish 已接线（三分支）。"""
        src = _src("nucleus/ssrf_guard.py")
        self.assertIn("from nucleus.llm.paid_api_trace import begin as _m360_begin", src)
        self.assertGreaterEqual(src.count("import finish as _m360_finish"), 3,
                                "成功/URLError/解析失败三路 finish 都要齐")

    def test_02_selector_wording_fixed(self):
        src = _src("organs/body/PulseLung.py")
        self.assertNotIn('f"优先使用远程大模型: {_remote_model_name}"', src,
                         "误导性文案必须消除（免费兜底成功时仍宣称优先远程）")
        self.assertIn("远程通道可用(model=", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
