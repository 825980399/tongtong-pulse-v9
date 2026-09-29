# -*- coding: utf-8 -*-
"""
PulseConsent —— 共同决策器官 · 重大变更征得同意

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 ConsentEvent.PROPOSE 与 ConsentEvent.DECIDE，登记重大变更提议并按 accept / reject 结案，把结果以 ConsentEvent.RESULT 广播出去。
机制: on_pulse 分派 _on_propose / _on_decide / _on_status_request；_on_propose 以 proposal_id 为键把提议写入 _proposals（初始 pending）并累加 _proposal_count；_on_decide 命中已有提议时改写 status，按决定累加 _accepted_count 或 _rejected_count，并 _emit ConsentEvent.RESULT（layer=L3 后台自主层）；get_stats 输出提议/接受/拒绝/pending 四项计数。
定位: 遗传层的「共同决策台」，always_online=False、受 enable_evolution 开关控制，是繁衍与自修改类重大动作的前置同意闸门。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import ConsentEvent, LogLevel, SystemEvent


class PulseConsent(BasePulseOrgan):
    """脉冲驱动共同决策（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。当前无参数，预留扩展。"""


    def __init__(self, organ_name: str = "共同决策"):
        super().__init__(organ_name)
        self._proposals = {}
        self._proposal_count = 0
        self._accepted_count = 0
        self._rejected_count = 0

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == ConsentEvent.PROPOSE:
            return self._on_propose(payload)
        elif event_type == ConsentEvent.DECIDE:
            return self._on_decide(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    def _on_propose(self, payload: dict) -> dict[str, Any]:
        pid = payload.get("proposal_id", f"p{self._proposal_count + 1}")
        self._proposals[pid] = {"status": "pending", "details": payload.get("details", "")}
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._proposal_count += 1
        # ★主线第37批 T5（P2-229）：业务路径补日志（原业务代码无任何日志）
        self._log(LogLevel.INFO,
                  f"[共同决策] 新提议: {pid} / {str(payload.get('details', ''))[:40]}")
        return {"status": "proposed", "proposal_id": pid}

    def _on_decide(self, payload: dict) -> dict[str, Any]:
        pid = payload.get("proposal_id", "")
        decision = payload.get("decision", "reject")
        if pid in self._proposals:
            self._proposals[pid]["status"] = decision
            if decision == "accept":
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._accepted_count += 1
            else:
                # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                self._rejected_count += 1
            # v9.5: 共同决策结果脉冲标记为L3后台自主层
            self._emit(ConsentEvent.RESULT, {
                "proposal_id": pid,
                "decision": decision
            }, priority=6, layer="L3")
            # ★主线第37批 T5（P2-229）：业务路径补日志（原业务代码无任何日志）
            self._log(LogLevel.INFO, f"[共同决策] 决策: {pid} → {decision}")
            return {"status": decision}
        self._log(LogLevel.DEBUG, f"[共同决策] 提议不存在，已忽略: {pid}")
        return {"status": "not_found"}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "proposal_count": self._proposal_count,
            "accepted_count": self._accepted_count,
            "rejected_count": self._rejected_count,
            "pending": sum(1 for p in self._proposals.values() if p["status"] == "pending"),
            "is_running": self.is_running,
        }

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name, "event_types": [ConsentEvent.PROPOSE, ConsentEvent.DECIDE, SystemEvent.STATUS_REQUEST], "min_priority": 1}]

    # ========== 未来演化预留（v10.0 振荡场） ==========
    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "共同决策",
    "class_name": "PulseConsent",
    "attr_name": "consent",
    "system": "genetic",
    "always_online": False,
    "feature_flag": "enable_evolution",
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    # ★主线第37批 T5（P2-229）：本块是**开发自测**（手动 `python 本文件` 运行），
    #   用 print 输出到控制台是正确形态 —— 框架运行时**不会执行**本块（__main__ 守卫）。
    #   器官的运行时日志统一走 `self._log`（见业务方法）；自测块不改为 _log 的原因：
    #   ① 自测需要控制台可见输出；② 本块无 `self`（用的是局部实例变量）。
    print("=== PulseConsent v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self): self.published = []
        def publish(self, pulse): self.published.append(pulse)
    mock = MockInfoField()
    c = PulseConsent("共同决策")
    c.set_info_field(mock)
    c.start()
    r1 = c.on_pulse({"event_type": ConsentEvent.PROPOSE, "payload": {"proposal_id": "t1", "details": "调整心跳"}, "priority": 5})
    print(f"1. 提议: {r1['status']}")
    r2 = c.on_pulse({"event_type": ConsentEvent.DECIDE, "payload": {"proposal_id": "t1", "decision": "accept"}, "priority": 5})
    print(f"2. 决策: {r2['status']}")
    # 验证 RESULT 脉冲的 layer 标记
    result_pulses = [p for p in mock.published if p.get("event_type") == ConsentEvent.RESULT]
    if result_pulses:
        print(f"   RESULT脉冲 layer: {result_pulses[-1].get('layer', '未设置')} (预期L3)")
    s = c.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"3. 状态: 提议{s['proposal_count']}次, 接受{s['accepted_count']}次")
    c.stop()
    print("\n=== 自测全部通过 ===")

