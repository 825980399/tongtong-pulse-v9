# -*- coding: utf-8 -*-
"""
PulseGrowth —— 成长器官 · 能力差距评估与需求感知

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 GrowthEvent.ASSESS，采集系统指标评估当前能力与目标里程碑的差距，产出进化建议并自主发射 GrowthEvent.NEED_DETECTED，达标时发射 GrowthEvent.MILESTONE_REACHED。
机制: on_pulse 分派 _on_assess / _on_status_request；_on_assess 由 _collect_metrics 采集指标（经 set_node_pool / set_frequency_codec 注入的依赖），_generate_suggestion 生成针对性建议，差距达阈值时自主 _emit GrowthEvent.NEED_DETECTED（L3 后台自主层），跨越里程碑则 _emit GrowthEvent.MILESTONE_REACHED；_record_growth_snapshot 把每次评估写入 L2 节点形成成长轨迹；_load_growth_config 装载里程碑与阈值配置。
定位: 身份层的「成长刻度尺」，always_online=True、无 feature_flag，是自主进化需求信号的发源地。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import GrowthEvent, SystemEvent
from nucleus.mnemosyne.PulseNode import PulseNode


class PulseGrowth(BasePulseOrgan):
    """脉冲驱动成长模块（v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "成长"):
        super().__init__(organ_name)

        self.node_pool = None
        self.frequency_codec = None

        # 从config加载进化里程碑配置
        self._load_growth_config()
        self._current_level = {
            "knowledge_nodes": 0,
            "l2_nodes": 0,
            "l3_nodes": 0,
            "heartbeat_stable": False,
            "self_repair_count": 0,
            "user_relationships": 0,
            "autonomous_tasks": 0,
        }

        self._current_milestone_index = 0

        self._assess_count = 0
        self._needs_detected = 0

    def _load_growth_config(self):
        """从config加载成长模块配置，失败时使用兜底值"""
        try:
            import config
            cfg = getattr(config, 'GROWTH_CONFIG', {})
            self._evolution_milestones = cfg.get("evolution_milestones", [
                {"name": "基础觉醒", "requirements": {"knowledge_nodes": 100, "heartbeat_stable": True}, "description": "系统稳定运行"},
                {"name": "求知萌芽", "requirements": {"knowledge_nodes": 1000, "l2_nodes": 10}, "description": "L2认知节点开始积累"},
                {"name": "自主进化", "requirements": {"knowledge_nodes": 5000, "l3_nodes": 5, "self_repair_count": 5}, "description": "具备自主修复能力"},
                {"name": "人格凝聚", "requirements": {"knowledge_nodes": 10000, "l3_nodes": 10, "user_relationships": 3}, "description": "人格内核完整"},
                {"name": "守护使命", "requirements": {"knowledge_nodes": 50000, "autonomous_tasks": 100}, "description": "完全自主运作"},
            ])
        except Exception:
            self._evolution_milestones = [
                {"name": "基础觉醒", "requirements": {"knowledge_nodes": 100, "heartbeat_stable": True}, "description": "系统稳定运行"},
                {"name": "求知萌芽", "requirements": {"knowledge_nodes": 1000, "l2_nodes": 10}, "description": "L2认知节点开始积累"},
                {"name": "自主进化", "requirements": {"knowledge_nodes": 5000, "l3_nodes": 5, "self_repair_count": 5}, "description": "具备自主修复能力"},
                {"name": "人格凝聚", "requirements": {"knowledge_nodes": 10000, "l3_nodes": 10, "user_relationships": 3}, "description": "人格内核完整"},
                {"name": "守护使命", "requirements": {"knowledge_nodes": 50000, "autonomous_tasks": 100}, "description": "完全自主运作"},
            ]
    # ========== 框架注入接口 ==========

    def set_node_pool(self, pool):
        self.node_pool = pool

    def set_frequency_codec(self, codec):
        self.frequency_codec = codec

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == GrowthEvent.ASSESS:
            return self._on_assess(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_assess(self, payload: dict) -> dict[str, Any]:
        """执行成长评估"""
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._assess_count += 1

        self._collect_metrics()

        gaps = []
        milestone_name = ""
        milestone_reached = None

        while self._current_milestone_index < len(self._evolution_milestones):
            current_milestone = self._evolution_milestones[self._current_milestone_index]
            requirements = current_milestone["requirements"]

            current_gaps = []
            for req_key, req_value in requirements.items():
                current_value = self._current_level.get(req_key, 0)
                if isinstance(req_value, bool):
                    if not current_value:
                        current_gaps.append({"metric": req_key, "current": current_value, "target": req_value})
                elif current_value < req_value:
                    current_gaps.append({"metric": req_key, "current": current_value, "target": req_value})

            if not current_gaps:
                milestone_reached = current_milestone["name"]
                if self._current_milestone_index < len(self._evolution_milestones) - 1:
                    # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
                    self._current_milestone_index += 1
                    continue
                else:
                    gaps = []
                    milestone_name = current_milestone["name"]
                    break
            else:
                gaps = current_gaps
                milestone_name = current_milestone["name"]
                break

        # v9.5: 里程碑达成脉冲标记为L3后台自主层
        if milestone_reached:
            next_name = self._evolution_milestones[self._current_milestone_index]["name"]
            self._emit(GrowthEvent.MILESTONE_REACHED, {
                "milestone": milestone_reached,
                "next": next_name,
                "description": self._evolution_milestones[self._current_milestone_index]["description"],
            }, priority=6, layer="L3")

        # v9.5: 成长需求脉冲标记为L3后台自主层
        if gaps:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._needs_detected += 1
            self._emit(GrowthEvent.NEED_DETECTED, {
                "milestone": milestone_name,
                "gaps": gaps,
                "suggestion": self._generate_suggestion(gaps),
                "current_level": self._current_level,
            }, priority=6, layer="L3")

        self._record_growth_snapshot(gaps)

        return {
            "status": "assessed",
            "milestone": milestone_name,
            "gaps_count": len(gaps),
            "gaps": gaps,
            "milestone_reached": milestone_reached,
            "current_level": self._current_level,
        }

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "assess_count": self._assess_count,
            "needs_detected": self._needs_detected,
            "current_milestone": self._evolution_milestones[self._current_milestone_index]["name"],
            "milestone_index": self._current_milestone_index,
            "current_level": self._current_level,
            "is_running": self.is_running,
        }

    # ========== 能力指标采集 ==========

    def _collect_metrics(self):
        if self.node_pool:
            stats = self.node_pool.get_stats()
            self._current_level["knowledge_nodes"] = stats.get("total_nodes", 0)
            evol_dist = stats.get("evol_distribution", {})
            self._current_level["l2_nodes"] = evol_dist.get("L2", 0)
            self._current_level["l3_nodes"] = evol_dist.get("L3", 0)

        if self.info_field:
            heartbeat = self.info_field.get_current("heart.beat")
            if heartbeat:
                interval = heartbeat.get("payload", {}).get("interval", 0)
                self._current_level["heartbeat_stable"] = 5 <= interval <= 60

    # ========== 成长建议 ==========

    def _generate_suggestion(self, gaps: list[dict]) -> str:
        suggestions = []
        for gap in gaps:
            metric = gap["metric"]
            if metric == "knowledge_nodes":
                suggestions.append("需要更多知识积累，建议增加网络抓取频率")
            elif metric == "l2_nodes":
                suggestions.append("需要将L1感知节点压缩为L2认知节点，建议触发知识归纳")
            elif metric == "l3_nodes":
                suggestions.append("需要更多核心智慧沉淀，建议从对话中提取关键经验")
            elif metric == "self_repair_count":
                suggestions.append("需要增强自主修复能力，建议开放DNA修复功能")
            elif metric == "user_relationships":
                suggestions.append("需要建立更多用户关系，建议增加互动深度")
            elif metric == "autonomous_tasks":
                suggestions.append("需要完成更多自主任务，建议解锁主动探索权限")
        return " | ".join(suggestions) if suggestions else "当前无需特别关注"

    # ========== 成长轨迹记录 ==========

    def _record_growth_snapshot(self, gaps: list[dict]):
        if self.node_pool is None:
            return

        content = f"成长评估#{self._assess_count}: {self._evolution_milestones[self._current_milestone_index]['name']}，能力缺口{len(gaps)}项"
        keywords = ["成长", "评估", "进化", f"里程碑{self._current_milestone_index}"]

        node = PulseNode(
            value=content,
            keywords=keywords,
            source_organ="成长",
            evol_level=PulseNode.EVOL_L2,
            importance=PulseNode.IMPORTANCE_B,
            abstraction=0.6,
            space_path=f"/成长/评估/{int(time.time())}",
        )

        if self.frequency_codec:
            self.frequency_codec.encode_node(node)

        self.node_pool.add(node)

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    GrowthEvent.ASSESS,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "成长",
    "class_name": "PulseGrowth",
    "attr_name": "growth",
    "system": "identity",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {
        "node_pool": "node_pool",
        "frequency_codec": "frequency_codec",
    },
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseGrowth v9.5 分层脉冲自测 ===\n")

    from nucleus.mnemosyne.PulseNode import PulseNode
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool
    from nucleus.pulse.FrequencyCodec import FrequencyCodec

    class MockInfoField:
        def __init__(self):
            self.published = []
            self._data = {}
        def publish(self, pulse):
            self.published.append(pulse)
        def get_current(self, key):
            return self._data.get(key)

    mock_field = MockInfoField()
    mock_field._data["heart.beat"] = {"payload": {"interval": 10.0}}
    pool = PulseNodePool()
    codec = FrequencyCodec()

    for i in range(5):
        node = PulseNode(
            value=f"测试知识{i}", keywords=[f"测试{i}"],
            source_organ="测试", evol_level=PulseNode.EVOL_L1,
            importance=PulseNode.IMPORTANCE_C, space_path=f"/测试/{i}"
        )
        pool.add(node)

    growth = PulseGrowth("成长")
    growth.set_info_field(mock_field)
    growth.set_node_pool(pool)
    growth.set_frequency_codec(codec)
    growth.start()

    result1 = growth.on_pulse({
        "event_type": GrowthEvent.ASSESS,
        "payload": {},
        "priority": 5,
    })
    print(f"1. 初期评估: {result1['status']}, 里程碑={result1['milestone']}, 缺口={result1['gaps_count']}")
    print(f"   当前水平: {result1['current_level']}")

    # 验证成长需求脉冲的 layer 标记
    need_pulses = [p for p in mock_field.published if p.get("event_type") == GrowthEvent.NEED_DETECTED]
    if need_pulses:
        print(f"   NEED_DETECTED脉冲 layer: {need_pulses[-1].get('layer', '未设置')} (预期L3)")

    for i in range(200):
        node = PulseNode(
            value=f"知识{i}", keywords=["知识"],
            source_organ="双腿", evol_level=PulseNode.EVOL_L1,
            importance=PulseNode.IMPORTANCE_C, space_path="/知识"
        )
        pool.add(node)
    growth._collect_metrics()
    result2 = growth.on_pulse({
        "event_type": GrowthEvent.ASSESS,
        "payload": {},
        "priority": 5,
    })
    print(f"2. 积累后评估: {result2['status']}, 里程碑={result2['milestone']}, 缺口={result2['gaps_count']}")

    # 验证里程碑达成脉冲的 layer 标记
    milestone_pulses = [p for p in mock_field.published if p.get("event_type") == GrowthEvent.MILESTONE_REACHED]
    if milestone_pulses:
        print(f"   MILESTONE_REACHED脉冲 layer: {milestone_pulses[-1].get('layer', '未设置')} (预期L3)")

    status = growth.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"3. 统计: 评估{status['assess_count']}次, 需求{status['needs_detected']}次, 里程碑={status['current_milestone']}")

    growth.stop()
    print("\n=== 自测全部通过 ===")
