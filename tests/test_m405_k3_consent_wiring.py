# -*- coding: utf-8 -*-
"""第405批 刀3 判据：consent.propose / consent.decide 真的有发射端。

背景（402 刀1 契约断裂 19 条的第 1、2 条）：
    `PulseConsent.get_resonance_conditions()` 明写承接
    `ConsentEvent.PROPOSE` / `ConsentEvent.DECIDE`，全库却零发射
    ⇒ 共同决策器官永久空转（proposal_count / accepted_count 恒 0）。
    根因：审批流程本该发它们，但 PatchManager 拿不到脉冲通道。

本刀接线口径：
    · 补丁**入队**（`save_pending_patch` 落盘成功）→ `consent.propose`
    · 补丁**审批通过**（`approve_patch` / `approve_all_patches`）→ `consent.decide`
    · 通道为**注入式**：不注入则一条不发（默认零行为变化）

判据钉语义不钉措辞：
    · 钉 "event_type 是不是那两个常量"、"proposal_id 能不能对上补丁 id"
    · 钉 "不注入时零发射"、"发射器抛异常时审批不得失败"
    · 不钉日志文本、不钉 payload 里除标识外的字段
"""
from __future__ import annotations

import unittest
from unittest import mock

from nucleus.const import ConsentEvent
from nucleus.reasoning.PatchManager import (
    PatchManager,
    get_consent_emit_fail_count,
    get_consent_emitter,
    set_consent_emitter,
)


def _mk_mgr(pending=None):
    """用 __new__ 造 PatchManager，避开 __init__ 的后台线程（否则 pytest 挂起）。"""
    _m = PatchManager.__new__(PatchManager)
    _m._pending_file = "tmp_m405_k3_pending.json"
    _m._history_file = "tmp_m405_k3_history.json"
    _m._patch_dir = "tmp_m405_k3_dir"
    _m._max_restart_count = 3
    _m._restart_counter = 0
    return _m


def _base_patch(pid="patch:m405k3:001", method="foo"):
    return {
        "file": "some_module.py",
        "method": method,
        "risk_level": "极低",
        "trust_score": 80,
        "issue_type": "formatting",
        "source": "local_rule",
        "status": "pending",
        "modified_code": "def %s():\n    return 1\n" % method,
        "aesthetic_score": {"total": 90, "grade": "A", "syntax_valid": True},
        "repair_source": "llm_multi_file",
        "id": pid,
    }


class _StubMixin:
    """把 save/approve 的重量副作用 stub 掉，只留接线逻辑被验证。"""

    def _stub(self, pending=None):
        _store = list(pending or [])

        def _load(*_a, **_k):
            return list(_store)

        def _save(*_a, **_k):
            return True

        _patches = [
            mock.patch.object(PatchManager, "_load_patch_list",
                              staticmethod(_load)),
            mock.patch.object(PatchManager, "_save_patch_list",
                              staticmethod(_save)),
            mock.patch.object(PatchManager, "_approve_via",
                              staticmethod(lambda *a, **k: True)),
            mock.patch.object(PatchManager, "_m339_is_o3",
                              staticmethod(lambda *a, **k: False)),
            mock.patch.object(PatchManager, "_m323_normalize_patch",
                              staticmethod(lambda *a, **k: None)),
            mock.patch.object(PatchManager, "_m105_try_release_low_risk",
                              lambda self, *a, **k: None),
            # ★405刀3：滞留件清扫会读写 patch 目录并可能起线程；不隔离会在
            #   测试结束后由 GC 触发 PytestUnraisableExceptionWarning（偶发顶红）。
            mock.patch.object(PatchManager, "sweep_runtime_failed",
                              lambda self, *a, **k: {"archived": 0}),
        ]
        for _p in _patches:
            _p.start()
            self.addCleanup(_p.stop)
        return _store


class TestNoEmitterMeansNoConsent(_StubMixin, unittest.TestCase):
    """默认零行为变化：没注入就不发（离线脚本/单测不受影响）。"""

    def setUp(self):
        set_consent_emitter(None)
        self.addCleanup(set_consent_emitter, None)
        self._sent = []

    def test_01_no_emitter_no_emit(self):
        # 显式确认未注入
        self.assertIsNone(get_consent_emitter())
        self._stub()
        _m = _mk_mgr()
        _m.save_pending_patch(_base_patch())
        self.assertEqual(self._sent, [], "未注入发射器却发出了脉冲")


class TestProposeOnEnqueue(_StubMixin, unittest.TestCase):
    """补丁入队 → consent.propose。"""

    def setUp(self):
        self._sent = []
        set_consent_emitter(
            lambda et, pl, prio=5: self._sent.append((et, dict(pl or {}), prio)))
        self.addCleanup(set_consent_emitter, None)

    def test_10_propose_emitted_with_patch_id(self):
        self._stub()
        _m = _mk_mgr()
        _p = _base_patch(pid="patch:m405k3:ABC")
        _m.save_pending_patch(_p)
        self.assertEqual(len(self._sent), 1,
                         "入队没有发射 consent.propose（实际 %d 条）" % len(self._sent))
        _et, _pl, _prio = self._sent[0]
        self.assertEqual(_et, ConsentEvent.PROPOSE,
                         "事件类型不是 consent.propose 而是 %s" % _et)
        self.assertEqual(_pl.get("proposal_id"), "patch:m405k3:ABC",
                         "proposal_id 与补丁 id 对不上")
        self.assertIsInstance(_prio, int)

    def test_11_payload_carries_location_not_code(self):
        """payload 只带定位字段，不带 modified_code（防脉冲日志体积按 MB 膨胀）。"""
        self._stub()
        _m = _mk_mgr()
        _m.save_pending_patch(_base_patch())
        _pl = self._sent[0][1]
        self.assertNotIn("modified_code", _pl,
                         "payload 里塞了整段代码，会把 pulse_log 撑爆")
        _d = _pl.get("details") or {}
        self.assertEqual(_d.get("file"), "some_module.py")
        self.assertEqual(_d.get("method"), "foo")

    def test_12_emit_targets_only_two_consent_events(self):
        """发射器只接受 consent.propose / consent.decide（白名单闸）。"""
        from nucleus.reasoning.PatchManager import _emit_consent
        _before = len(self._sent)
        self.assertFalse(_emit_consent("consent.result", {}),
                         "consent.result 不该由 PatchManager 发")
        self.assertFalse(_emit_consent("mouth.speak", {}))
        self.assertEqual(len(self._sent), _before, "白名单外的类型被放行了")

    def test_13_priority_is_int(self):
        """priority 必须是 int（幽灵 int/str 在本项目栽过，见 224 批 target_organ）。"""
        self._stub()
        _m = _mk_mgr()
        _m.save_pending_patch(_base_patch())
        self.assertNotIsInstance(self._sent[0][2], str)


class TestDecideOnApprove(_StubMixin, unittest.TestCase):
    """审批通过 → consent.decide(accept)。"""

    def setUp(self):
        self._sent = []
        set_consent_emitter(
            lambda et, pl, prio=5: self._sent.append((et, dict(pl or {}), prio)))
        self.addCleanup(set_consent_emitter, None)

    def test_20_single_approve_emits_decide(self):
        _p = _base_patch(pid="patch:m405k3:D1")
        self._stub(pending=[_p])
        _m = _mk_mgr()
        _r = _m.approve_patch(0, signer="human:tester")
        self.assertTrue(_r.get("ok"), "审批本身失败了，判据失去意义: %s" % _r)
        self.assertEqual(len(self._sent), 1,
                         "审批通过没有发射 consent.decide（实际 %d 条）" % len(self._sent))
        _et, _pl, _ = self._sent[0]
        self.assertEqual(_et, ConsentEvent.DECIDE)
        self.assertEqual(_pl.get("proposal_id"), "patch:m405k3:D1")
        self.assertEqual(_pl.get("decision"), "accept")
        self.assertIn("tester", str(_pl.get("signer") or ""),
                      "decide 里没有签名人，事后无法追溯谁批的")

    def test_21_approve_failure_emits_nothing(self):
        """审批失败（索引越界）不得发 decide —— 没批的事不能报成"批了"。"""
        self._stub(pending=[])
        _m = _mk_mgr()
        _r = _m.approve_patch(0, signer="human:tester")
        self.assertFalse(_r.get("ok"))
        self.assertEqual(self._sent, [], "审批失败却发了 consent.decide")

    def test_22_approve_all_emits_per_patch(self):
        _p1 = _base_patch(pid="p1", method="foo")
        _p2 = _base_patch(pid="p2", method="bar")
        self._stub(pending=[_p1, _p2])
        _m = _mk_mgr()
        _r = _m.approve_all_patches(signer="human:batch")
        self.assertTrue(_r.get("ok"))
        self.assertEqual(_r.get("approved"), 2, "两条补丁没都批下来: %s" % _r)
        self.assertEqual(len(self._sent), 2,
                         "批量审批应按条发 decide（实际 %d 条）" % len(self._sent))
        _ids = sorted(x[1].get("proposal_id") for x in self._sent)
        self.assertEqual(_ids, ["p1", "p2"])
        for _et, _pl, _ in self._sent:
            self.assertEqual(_et, ConsentEvent.DECIDE)
            self.assertEqual(_pl.get("decision"), "accept")


class TestEmitterFailureDoesNotBreakApproval(_StubMixin, unittest.TestCase):
    """发射器炸了，审批必须照常成功（脉冲是观测手段，审批是安全关键路径）。"""

    def setUp(self):
        def _boom(*_a, **_k):
            raise RuntimeError("模拟脉冲通道炸了")

        set_consent_emitter(_boom)
        self.addCleanup(set_consent_emitter, None)

    def test_30_enqueue_survives_emitter_crash(self):
        _before = get_consent_emit_fail_count()
        self._stub()
        _m = _mk_mgr()
        _p = _base_patch()
        _m.save_pending_patch(_p)          # 不得抛出
        self.assertEqual(_p.get("id"), "patch:m405k3:001")
        self.assertGreater(get_consent_emit_fail_count(), _before,
                           "发射失败没有累计计数（静默消失了）")

    def test_31_approve_survives_emitter_crash(self):
        _p = _base_patch()
        self._stub(pending=[_p])
        _m = _mk_mgr()
        _r = _m.approve_patch(0, signer="human:tester")   # 不得抛出
        self.assertTrue(_r.get("ok"),
                        "发射器异常把审批弄失败了 —— 观测手段反噬主流程")


class TestResonanceContract(unittest.TestCase):
    """订阅侧契约：PulseConsent 确实订这两个事件，且收到就会处理。

    这条是「发的人发的事件名，收的人真的认得」的对接判据 ——
    只验证两边字符串一致，不做完整总线装配（那属集成测试范围）。
    """

    def test_40_consent_organ_subscribes_both(self):
        from organs.genetic.PulseConsent import PulseConsent
        _c = PulseConsent()
        _types = []
        for _cond in _c.get_resonance_conditions():
            _types.extend(_cond.get("event_types") or [])
        self.assertIn(ConsentEvent.PROPOSE, _types,
                      "PulseConsent 没订阅 consent.propose")
        self.assertIn(ConsentEvent.DECIDE, _types,
                      "PulseConsent 没订阅 consent.decide")

    def test_41_consent_organ_handles_propose(self):
        from organs.genetic.PulseConsent import PulseConsent
        _c = PulseConsent()
        _c.on_pulse({"event_type": ConsentEvent.PROPOSE,
                     "payload": {"proposal_id": "probe405",
                                 "details": "测试提议"}, "priority": 5})
        _st = _c.get_stats()
        self.assertEqual(_st.get("proposal_count"), 1,
                         "PulseConsent 收到 propose 没有登记: %s" % _st)

    def test_42_propose_then_decide_closes_the_loop(self):
        """提议 → 决定 accept 必须闭环（_proposals 状态翻成 accepted）。"""
        from organs.genetic.PulseConsent import PulseConsent
        _c = PulseConsent()
        _c.on_pulse({"event_type": ConsentEvent.PROPOSE,
                     "payload": {"proposal_id": "probe406"}, "priority": 5})
        _c.on_pulse({"event_type": ConsentEvent.DECIDE,
                     "payload": {"proposal_id": "probe406",
                                 "decision": "accept"}, "priority": 5})
        _st = _c.get_stats()
        self.assertEqual(_st.get("accepted_count"), 1,
                         "decide=accept 没有结案: %s" % _st)


if __name__ == "__main__":
    unittest.main(verbosity=2)
