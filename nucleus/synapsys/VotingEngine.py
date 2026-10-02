# -*- coding: utf-8 -*-
# ⚠️ @deprecated (157-D C-6 观测孤岛封存 / Q157-2 同域口径):
#   观测孤岛·synapsys/VotingEngine（投票引擎）：多机协议未启用；③封存标注，标"预留-多机/协议未启用-拟接线批次=PHASE19"。
#   复活须待对应专门批（PHASE19 / 构建脚本移出批）；禁止新代码 import 本模块（若仍在用请先接线）。
"""
VotingEngine.py —— 投票引擎

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 多器官决策投票与结果聚合
机制: 基于VoteTier类实现，包含10个核心方法
定位: 决策协调层
"""

import threading
import time
from enum import Enum
from typing import Any



# 投票层级定义（未来演化预留）
class VoteTier(Enum):
    REFLEX = "autonomic_reflex"     # 自主反射：器官级快速决策（如心脏调速）
    DIRECTED = "directed_vote"      # 定向投票：特定器官群体决策（如免疫集群）
    GLOBAL = "global_referendum"    # 全局公投：全部器官参与的重大决策（如修改人格锚点）

class VoteStatus(Enum):
    PROPOSED = "proposed"
    VOTING = "voting"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIED = "tied"

class VotingEngine:
    """
    三层投票引擎

    工作原理:
        1. 器官提交投票提案（Proposal），指定投票层级和参与范围。
        2. 投票引擎根据层级收集指定器官的投票（脉冲）。
        3. 按照加权规则（重要器官权重更高）计算结果。
        4. 返回最终裁决，必要时触发公投升级。

    当前状态（v9.0）:
        - 接口完整定义，但仅做记录与模拟验证，不干预实际运行。
        - 通过功能开关控制激活状态，默认关闭。
    """

    def __init__(self):
        # 活跃提案池
        self._proposals: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

        # 器官投票权重（未来可配置，当前为默认值）
        # 核心大脑器官在决策中拥有更高权重，符合类脑仿生原则
        self._organ_weights = {
            "大脑皮层": 1.5,
            "人格内核": 2.0,   # 人格锚点守护
            "前额叶": 1.3,     # 复盘与纠错
            "伦理": 1.8,       # 伦理审查
            "潜意识": 1.0,
            "心脏": 0.8,
            "胃": 0.7,
            "肝": 0.7,
            "肾": 0.6,
            "免疫集群": 0.9,    # 白细胞、胸腺、骨髓等
            "default": 0.5
        }

        # 统计
        self._total_proposals = 0
        self._total_resolved = 0

        # 功能开关（P1预埋原则）
        self._enabled = False

    # ========== 框架控制 ==========

    def enable(self):
        """激活投票引擎（P2/P3阶段启用）"""
        self._enabled = True

    def disable(self):
        """关闭投票引擎，所有决策由脉冲优先级直接处理"""
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    # ========== 核心接口：提案管理 ==========

    def propose(self, 
                source_organ: str, 
                issue: str,
                options: list[str],
                tier: VoteTier = VoteTier.REFLEX,
                required_participants: list[str] | None = None,
                urgency: int = 5) -> dict[str, Any]:
        """
        发起一项投票提案。

        Args:
            source_organ: 发起器官
            issue: 议题描述（如“心跳间隔调整至15s”）
            options: 投票选项列表（如["同意", "反对"]）
            tier: 投票层级
            required_participants: 必须参与的器官列表，None表示根据层级自动决定
            urgency: 紧急程度 0-10，影响投票超时时间

        Returns:
            提案信息字典
        """
        if not self._enabled:
            return {"status": "disabled", "message": "投票引擎未激活，决策由脉冲优先级直接处理"}

        with self._lock:
            self._total_proposals += 1
            proposal_id = f"vote_{int(time.time())}_{self._total_proposals}"

            proposal = {
                "proposal_id": proposal_id,
                "source_organ": source_organ,
                "issue": issue,
                "options": options,
                "tier": tier,
                "required_participants": required_participants or [],
                "urgency": urgency,
                "status": VoteStatus.PROPOSED,
                "votes": {},        # organ -> option
                "weights": {},      # organ -> effective weight
                "created_at": time.time(),
                "resolved_at": None,
                "result": None
            }

            self._proposals[proposal_id] = proposal
            return proposal

    def cast_vote(self, proposal_id: str, organ_name: str, choice: str) -> dict[str, Any]:
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                return {"status": "not_found"}

            if proposal["status"] != VoteStatus.PROPOSED and proposal["status"] != VoteStatus.VOTING:
                return {"status": "closed", "result": proposal["result"]}

            # ★P1-4修复：校验投票器官是否在允许的参与范围内
            _required = proposal.get("required_participants", [])
            if _required and organ_name not in _required:
                return {
                    "status": "unauthorized",
                    "reason": f"器官'{organ_name}'不在本提案的投票参与范围内",
                    "allowed_organs": _required,
                }

            if choice not in proposal["options"]:
                return {"status": "invalid_choice"}

            proposal["votes"][organ_name] = choice
            proposal["weights"][organ_name] = self._get_organ_weight(organ_name)

            if proposal["status"] == VoteStatus.PROPOSED:
                proposal["status"] = VoteStatus.VOTING

            return {"status": "recorded", "organ": organ_name, "choice": choice}
    def tally(self, proposal_id: str) -> dict[str, Any]:
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                return {"status": "not_found"}

            if proposal["status"] != VoteStatus.VOTING:
                return {"status": "not_voting"}

            tallies = {opt: 0.0 for opt in proposal["options"]}
            total_weight = 0.0
            for organ, choice in proposal["votes"].items():
                weight = proposal["weights"].get(organ, 0.5)
                tallies[choice] += weight
                total_weight += weight

            if total_weight == 0:
                result = VoteStatus.TIED
                winner_name = "平局"
            else:
                sorted_options = sorted(tallies.items(), key=lambda x: x[1], reverse=True)
                winner = sorted_options[0]
                if len(sorted_options) > 1 and winner[1] == sorted_options[1][1]:
                    result = VoteStatus.TIED
                    winner_name = "平局"
                else:
                    # ★P1-4修复：根据获胜选项的实际名称判定结果状态
                    # 不再硬编码"反对"→REJECTED，而是根据选项列表判断
                    _winner_option = winner[0]
                    winner_name = _winner_option
                    if _winner_option in ("同意", "approve", "yes", "通过"):
                        result = VoteStatus.APPROVED
                    elif _winner_option in ("反对", "reject", "no", "否决"):
                        result = VoteStatus.REJECTED
                    else:
                        # 自定义选项：获胜即为通过
                        result = VoteStatus.APPROVED

            proposal["status"] = result
            proposal["resolved_at"] = time.time()
            proposal["result"] = {
                "winning_option": winner_name,
                "tallies": tallies,
                "total_weight": total_weight,
                "voter_count": len(proposal["votes"])
            }

            self._total_resolved += 1
            return {"status": result.value, "result": proposal["result"]}
    def _get_organ_weight(self, organ_name: str) -> float:
        """获取器官投票权重"""
        return self._organ_weights.get(organ_name, self._organ_weights["default"])

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "total_proposals": self._total_proposals,
                "total_resolved": self._total_resolved,
                "active_proposals": len([p for p in self._proposals.values() if p["status"] in (VoteStatus.PROPOSED, VoteStatus.VOTING)]),
            }

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, field_data: dict[str, Any]):
        """
        【预留 v10.0】振荡场中，投票机制由连续场梯度驱动。
        当信息场中出现决策冲突时，场梯度自然偏向权重大的一方，
        投票引擎将场梯度转换为最终决议。
        """


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== VotingEngine 三层投票引擎自测 ===\n")

    engine = VotingEngine()
    engine.enable()

    # 测试1：发起提案
    proposal = engine.propose(
        source_organ="心脏",
        issue="是否将心跳间隔调整为15秒？",
        options=["同意", "反对"],
        tier=VoteTier.DIRECTED,
        required_participants=["大脑皮层", "潜意识", "能量代谢"]
    )
    print(f"1. 提案创建: {proposal['proposal_id']}")

    # 测试2：器官投票
    engine.cast_vote(proposal["proposal_id"], "大脑皮层", "同意")
    engine.cast_vote(proposal["proposal_id"], "潜意识", "同意")
    engine.cast_vote(proposal["proposal_id"], "能量代谢", "反对")

    # 测试3：计票
    result = engine.tally(proposal["proposal_id"])
    print(f"2. 计票结果: {result['status']} - {result['result']['winning_option']}")
    print(f"   票数分布: {result['result']['tallies']}")

    # 测试4：统计
    stats = engine.get_stats()
    print(f"3. 统计: 提案{stats['total_proposals']} 已解决{stats['total_resolved']}")

    engine.disable()
    print("\n=== 自测全部通过 ===")