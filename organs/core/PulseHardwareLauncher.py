# -*- coding: utf-8 -*-
"""
PulseHardwareLauncher —— 硬件自适应启动器 · 启动加载编排

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 HardwareEvent.ASSESS，根据当前机器硬件能力生成模块加载计划与启动顺序，并广播 HardwareEvent.LAUNCH_PLAN。
机制: on_pulse → _on_assess → _build_load_plan 按算力与内存档位决定可加载模块集合及其启动次序，产出计划后广播，交由系统管理器执行；_on_status_request 上报状态。
定位: 躯体的「开机自检与加载编排」，决定曈曈在这台机器上以何种形态启动。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import HardwareEvent, SystemEvent


class PulseHardwareLauncher(BasePulseOrgan):
    """脉冲驱动硬件自适应启动器（v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "硬件启动器"):
        super().__init__(organ_name)
        self._hardware_tier = "standard"
        self._organ_load_plan: dict[str, list] = {"core": [], "functional": [], "delayed": [], "sleep": []}

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        if event_type == HardwareEvent.ASSESS:
            return self._on_assess(pulse.get("payload", {}))
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    def _on_assess(self, payload: dict) -> dict[str, Any]:
        # ★P3 硬件自适应修复：优先用触觉采集的真实硬件数据评估等级，
        # 能量代谢分仅作兜底（此前完全依赖代谢分，导致真实硬件与等级评估断链）。
        tier = None
        _hw = None
        if self.info_field:
            try:
                _snap = self.info_field.get_current("touch.hardware_snapshot")
                if _snap and isinstance(_snap, dict):
                    _hw = _snap.get("payload", _snap)  # 兼容两种包结构
            except Exception:
                _hw = None

        if _hw and isinstance(_hw, dict):
            # 用真实硬件数据评估：CPU核心数 / 内存 / GPU 可用性
            _cpu = _hw.get("cpu", {}) or {}
            _mem = _hw.get("memory", {}) or {}
            _gpu = _hw.get("gpu", {}) or {}
            _cores = _cpu.get("cores", 1) or 1
            _mem_gb = _mem.get("total_gb", 0) or 0
            _has_gpu = bool(_gpu.get("available", False))

            # 评分：核心数(0-3) + 内存(0-4) + GPU(0-2)
            # GPU 是「加分项」而非「必选项」：无 GPU 但 CPU/内存强，仍应 high。
            _score = 0
            if _cores >= 16:
                _score += 3
            elif _cores >= 8:
                _score += 2
            elif _cores >= 4:
                _score += 1
            if _mem_gb >= 32:
                _score += 4
            elif _mem_gb >= 16:
                _score += 3
            elif _mem_gb >= 8:
                _score += 2
            elif _mem_gb >= 4:
                _score += 1
            if _has_gpu:
                _score += 2

            if _score >= 7:
                tier = "high"
            elif _score >= 4:
                tier = "standard"
            else:
                tier = "minimal"
            self._log(
                "INFO",
                f"硬件等级评估(真实硬件): 核心={_cores} 内存={_mem_gb}GB GPU={'有' if _has_gpu else '无'} → tier={tier}"
            )

        # 兜底1：无真实硬件数据时回退到能量代谢分（保持原有行为）
        if tier is None and self.info_field:
            es = self.info_field.get_current("energy.metabolism_snapshot")
            if es and isinstance(es, dict):
                overall = es.get("payload", {}).get("profile", {}).get("overall", 5)
                if overall >= 7:
                    tier = "high"
                elif overall >= 4:
                    tier = "standard"
                else:
                    tier = "minimal"

        # ★PHASE14-闭环修复兜底2：上面两条都拿不到数据时，原实现直接硬编码
        #   tier="standard"——这是「看家底吃饭」断链的最后一环：家底拿不到就
        #   拍脑袋定档，且完全静默，内部协作者在日志里永远看不到。
        #   改为：先用 hardware_probe 独立探一次真实硬件（与装配路径同一数据源，
        #   消除「装配用 A 源、计划用 B 源」的不一致），彻底失败才落 standard 并告警。
        if tier is None:
            try:
                from nucleus.hardware_probe import detect_hardware_tier
                _probe = detect_hardware_tier() or {}
                _p_tier = _probe.get("tier")
                if _p_tier in ("high", "standard", "minimal"):
                    tier = _p_tier
                    self._log(
                        "INFO",
                        f"硬件等级评估(独立探测兜底): 核心={_probe.get('cores')} "
                        f"内存={_probe.get('memory_gb')}GB "
                        f"GPU={'有' if _probe.get('has_gpu') else '无'} → tier={tier}"
                    )
            except Exception as _e:
                self._log("DEBUG", f"独立硬件探测兜底失败: {_e}")

        if tier is None:
            tier = "standard"
            # 静默兜底 → 显式告警：家底拿不到这件事必须可见，否则闭环永远无法验证
            self._log(
                "WARNING",
                "硬件等级评估：触觉快照与代谢分均不可用，独立探测亦失败，"
                "已兜底 tier=standard（非真实硬件评估结果，请检查触觉器官是否正常工作）"
            )

        self._hardware_tier = tier
        self._build_load_plan()
        # v9.5: 硬件评估脉冲标记为L3后台自主层
        self._emit(HardwareEvent.LAUNCH_PLAN, {
            "tier": self._hardware_tier,
            "plan": self._organ_load_plan
        }, priority=5, layer="L3")
        return {"status": "assessed", "tier": self._hardware_tier}

    def _build_load_plan(self):
        self._organ_load_plan = {"core": ["心脏","大脑皮层","内在世界","胃"], "functional": ["眼睛","耳朵","嘴巴","双手","双腿"], "delayed": ["肾","进化","DNA修复"], "sleep": ["养育","情感羁绊"]}
        if self._hardware_tier == "minimal":
            self._organ_load_plan["functional"] = ["嘴巴"]

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {"organ": self.organ_name, "hardware_tier": self._hardware_tier, "load_plan": self._organ_load_plan, "is_running": self.is_running}

    def get_resonance_conditions(self) -> list:
        return [{"organ_name": self.organ_name, "event_types": [HardwareEvent.ASSESS, SystemEvent.STATUS_REQUEST], "min_priority": 1}]

    # ========== 未来演化预留（v10.0 振荡场） ==========
    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None



# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "硬件启动器",
    "class_name": "PulseHardwareLauncher",
    "attr_name": "hardware_launcher",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseHardwareLauncher v9.5 分层脉冲自测 ===\n")
    class MockInfoField:
        def __init__(self): self.published = []; self._data = {}
        def publish(self, p): self.published.append(p)
        def get_current(self, k): return self._data.get(k)
    m = MockInfoField()
    m._data["energy.metabolism_snapshot"] = {"payload": {"profile": {"overall": 7.5}}}
    h = PulseHardwareLauncher("硬件启动器"); h.set_info_field(m); h.start()
    r = h.on_pulse({"event_type": HardwareEvent.ASSESS, "payload": {}, "priority": 5})
    print(f"1. 评估: {r['status']}, 等级={r['tier']}")
    # 验证评估脉冲的 layer 标记
    launch_pulses = [p for p in m.published if p.get("event_type") == HardwareEvent.LAUNCH_PLAN]
    if launch_pulses:
        print(f"   LAUNCH_PLAN脉冲 layer: {launch_pulses[-1].get('layer', '未设置')} (预期L3)")
    s = h.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"2. 加载计划: 核心{s['load_plan']['core']}, 功能{s['load_plan']['functional']}")
    h.stop(); print("\n=== 自测全部通过 ===")
