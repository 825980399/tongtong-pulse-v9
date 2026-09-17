# -*- coding: utf-8 -*-
"""
ProtocolManager.py —— 协议管理器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 器官间通信协议管理与协商
机制: 基于VersionStatus类实现，包含10个核心方法
定位: 通信管理层
"""

import time
from enum import Enum
from typing import Any



class VersionStatus(Enum):
    """协议版本状态"""
    ACTIVE = "active"          # 当前使用中
    DEPRECATED = "deprecated"  # 已弃用但仍兼容
    RETIRED = "retired"        # 已退役，不再兼容


class ProtocolManager:
    """
    协议版本管理器
    
    工作原理:
        1. 系统启动时注册所有核心协议的版本号。
        2. 器官加载时检查其声明的协议版本是否与系统兼容。
        3. 多节点通信前进行协议握手，确保版本一致。
        4. 记录协议变更历史，支持回滚到旧版本。
    
    核心概念:
        - 协议族: 一组相关协议的集合（如"脉冲事件协议族"包含所有事件枚举）。
        - 版本号: 使用语义化版本（MAJOR.MINOR.PATCH）。
        - 兼容性: MAJOR版本变化表示不兼容，MINOR变化表示向前兼容，PATCH变化表示完全兼容。
    
    当前状态（v9.0）:
        - 注册了 v9.0 的核心协议版本。
        - 功能开关默认关闭，P3阶段激活。
    """
    
    def __init__(self):
        # 已注册的协议: protocol_name → {version, status, registered_at, description}
        self._protocols: dict[str, dict[str, Any]] = {}
        
        # 协议变更历史
        self._version_history: list[dict[str, Any]] = []
        
        # 兼容性矩阵: (protocol_name, version_a, version_b) → is_compatible
        self._compatibility_cache: dict[tuple[str, str, str], bool] = {}
        
        # 统计
        self._total_checks = 0
        self._total_conflicts = 0
        
        # 功能开关
        self._enabled = False
        
        # 初始化 v9.0 核心协议
        self._register_core_protocols()

    def _register_core_protocols(self):
        """注册 v9.0 PulseNet 的核心协议版本"""
        core_protocols = {
            "pulse_event_system": {
                "version": "9.0.0",
                "description": "脉冲事件系统（54个事件枚举类）",
                "status": VersionStatus.ACTIVE,
            },
            "pulse_data_structure": {
                "version": "9.0.0",
                "description": "脉冲数据结构（Pulse/PulseNode/ResonanceCondition）",
                "status": VersionStatus.ACTIVE,
            },
            "organ_interface": {
                "version": "9.0.0",
                "description": "器官接口规范（BasePulseOrgan/on_pulse/get_resonance_conditions）",
                "status": VersionStatus.ACTIVE,
            },
            "knowledge_evolution": {
                "version": "9.0.0",
                "description": "知识分级演化协议（L1/L2/L3）",
                "status": VersionStatus.ACTIVE,
            },
            "resonance_engine": {
                "version": "9.0.0",
                "description": "五维共振引擎协议（权重/计分公式）",
                "status": VersionStatus.ACTIVE,
            },
            "security_sandbox": {
                "version": "9.0.0",
                "description": "三级安全沙箱协议（L1/L2/L3）",
                "status": VersionStatus.ACTIVE,
            },
        }
        
        for name, info in core_protocols.items():
            self._protocols[name] = {
                **info,
                "registered_at": time.time(),
            }

    # ========== 框架控制 ==========

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    # ========== 协议注册与查询 ==========

    def register_protocol(self, 
                          name: str, 
                          version: str,
                          description: str = "",
                          status: VersionStatus = VersionStatus.ACTIVE) -> bool:
        """
        注册一个新协议或更新已有协议的版本。

        Args:
            name: 协议名称
            version: 版本号（语义化版本格式 MAJOR.MINOR.PATCH）
            description: 协议描述
            status: 版本状态

        Returns:
            是否注册成功
        """
        if not self._enabled:
            return False

        old_version = None
        if name in self._protocols:
            old_version = self._protocols[name]["version"]

        self._protocols[name] = {
            "version": version,
            "description": description,
            "status": status,
            "registered_at": time.time(),
        }

        # 记录变更历史
        self._version_history.append({
            "protocol": name,
            "old_version": old_version,
            "new_version": version,
            "changed_at": time.time(),
        })

        return True

    def get_protocol_version(self, name: str) -> str | None:
        """
        查询指定协议的当前版本。

        Args:
            name: 协议名称

        Returns:
            版本号，不存在返回 None
        """
        protocol = self._protocols.get(name)
        return protocol["version"] if protocol else None

    def get_all_protocols(self) -> dict[str, dict[str, Any]]:
        """获取所有已注册的协议"""
        return dict(self._protocols)

    # ========== 兼容性检查 ==========

    def parse_version(self, version: str) -> tuple[int, int, int]:
        """
        解析语义化版本号。

        Args:
            version: 版本字符串（如 "9.0.1"）

        Returns:
            (major, minor, patch) 元组
        """
        try:
            parts = version.split(".")
            major = int(parts[0]) if len(parts) > 0 else 0
            minor = int(parts[1]) if len(parts) > 1 else 0
            patch = int(parts[2]) if len(parts) > 2 else 0
            return (major, minor, patch)
        except (ValueError, IndexError):
            return (0, 0, 0)

    def check_compatibility(self, 
                            protocol_name: str, 
                            required_version: str) -> dict[str, Any]:
        """
        检查指定协议的版本是否与系统兼容。

        规则:
            - MAJOR 版本不同: 不兼容
            - MAJOR 相同，MINOR 不同: 向前兼容（系统版本 ≥ 要求版本）
            - MAJOR 相同，MINOR 相同，PATCH 不同: 完全兼容

        Args:
            protocol_name: 协议名称
            required_version: 要求的版本号

        Returns:
            兼容性检查结果
        """
        self._total_checks += 1

        if not self._enabled:
            return {"compatible": True, "reason": "协议管理未激活，默认兼容"}

        current = self._protocols.get(protocol_name)
        if not current:
            self._total_conflicts += 1
            return {
                "compatible": False,
                "reason": f"未知协议: {protocol_name}",
                "current_version": None,
                "required_version": required_version,
            }

        current_version = current["version"]
        cur_major, cur_minor, _cur_patch = self.parse_version(current_version)
        req_major, req_minor, _req_patch = self.parse_version(required_version)

        # MAJOR 不同 → 不兼容
        if cur_major != req_major:
            self._total_conflicts += 1
            return {
                "compatible": False,
                "reason": f"MAJOR版本不兼容: 系统={current_version}, 要求={required_version}",
                "current_version": current_version,
                "required_version": required_version,
            }

        # MINOR 不同 → 需要系统版本 ≥ 要求版本
        if cur_minor < req_minor:
            self._total_conflicts += 1
            return {
                "compatible": False,
                "reason": f"系统版本过低: 系统={current_version}, 要求={required_version}",
                "current_version": current_version,
                "required_version": required_version,
            }

        # 兼容
        return {
            "compatible": True,
            "reason": "版本兼容",
            "current_version": current_version,
            "required_version": required_version,
        }

    def check_node_compatibility(self, 
                                  remote_protocols: dict[str, str]) -> dict[str, Any]:
        """
        检查远程节点的协议版本是否与本节点兼容。
        用于 P3-2（多数字生命节点通信）的握手阶段。

        Args:
            remote_protocols: 远程节点的协议版本字典 {protocol_name: version}

        Returns:
            握手结果
        """
        conflicts = []
        for protocol_name, remote_version in remote_protocols.items():
            result = self.check_compatibility(protocol_name, remote_version)
            if not result["compatible"]:
                conflicts.append({
                    "protocol": protocol_name,
                    "reason": result["reason"],
                })

        return {
            "compatible": len(conflicts) == 0,
            "conflicts": conflicts,
            "local_protocols": {k: v["version"] for k, v in self._protocols.items()},
        }

    # ========== 版本演化管理 ==========

    def upgrade_protocol(self, name: str, new_version: str, reason: str = "") -> dict[str, Any]:
        """
        升级一个协议的版本。

        Args:
            name: 协议名称
            new_version: 新版本号
            reason: 升级原因

        Returns:
            升级结果
        """
        if name not in self._protocols:
            return {"success": False, "reason": f"未知协议: {name}"}

        old_version = self._protocols[name]["version"]
        self._protocols[name]["version"] = new_version
        self._protocols[name]["registered_at"] = time.time()

        self._version_history.append({
            "protocol": name,
            "old_version": old_version,
            "new_version": new_version,
            "changed_at": time.time(),
            "reason": reason,
        })

        return {
            "success": True,
            "protocol": name,
            "old_version": old_version,
            "new_version": new_version,
            "reason": reason,
        }

    def deprecate_protocol(self, name: str) -> bool:
        """将协议标记为弃用"""
        if name in self._protocols:
            self._protocols[name]["status"] = VersionStatus.DEPRECATED
            return True
        return False

    def retire_protocol(self, name: str) -> bool:
        """将协议标记为退役"""
        if name in self._protocols:
            self._protocols[name]["status"] = VersionStatus.RETIRED
            return True
        return False

    def get_version_history(self, protocol_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """
        查询版本变更历史。

        Args:
            protocol_name: 协议名称过滤（可选）
            limit: 返回数量

        Returns:
            变更历史列表
        """
        history = self._version_history
        if protocol_name:
            history = [h for h in history if h["protocol"] == protocol_name]
        return list(reversed(history))[-limit:]

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取协议管理统计"""
        return {
            "enabled": self._enabled,
            "total_protocols": len(self._protocols),
            "total_checks": self._total_checks,
            "total_conflicts": self._total_conflicts,
            "conflict_rate": round(self._total_conflicts / max(1, self._total_checks), 3),
            "history_size": len(self._version_history),
            "active_protocols": sum(1 for p in self._protocols.values() if p["status"] == VersionStatus.ACTIVE),
            "deprecated_protocols": sum(1 for p in self._protocols.values() if p["status"] == VersionStatus.DEPRECATED),
        }

    # ========== 未来演化预留（v10.0 振荡场） ==========

    def on_field_oscillation(self, field_data: dict[str, Any]):
        """
        【预留 v10.0】振荡场中，协议版本由场的频率调制信号自动协商。
        节点不需要显式握手，通过场的频率特征自动匹配兼容协议。
        """

    def auto_negotiate(self, remote_signature: str) -> dict[str, Any]:
        """
        【预留 v10.0】基于场签名自动协商协议版本。
        
        Args:
            remote_signature: 远程节点的场签名

        Returns:
            协商结果
        """
        return {
            "negotiated": False,
            "reason": "v9.0 经典脉冲模式不支持自动协商",
            "recommended_mode": "manual",
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== ProtocolManager 自测 ===\n")
    
    manager = ProtocolManager()
    manager.enable()
    
    # 1. 查询核心协议
    print("1. 核心协议:")
    protocols = manager.get_all_protocols()
    for name, info in protocols.items():
        print(f"   {name}: v{info['version']} - {info['description']}")
    
    # 2. 兼容性检查（同版本）
    result = manager.check_compatibility("pulse_event_system", "9.0.0")
    print(f"\n2. 同版本兼容性: {result['compatible']} - {result['reason']}")
    assert result["compatible"], "同版本应兼容"
    print("   ✅ 同版本兼容正确")
    
    # 3. 兼容性检查（MAJOR不同）
    result = manager.check_compatibility("pulse_event_system", "10.0.0")
    print(f"3. MAJOR不同兼容性: {result['compatible']} - {result['reason']}")
    assert not result["compatible"], "MAJOR不同应不兼容"
    print("   ✅ MAJOR不兼容正确")
    
    # 4. 版本解析
    major, minor, patch = manager.parse_version("9.0.1")
    print(f"\n4. 版本解析: 9.0.1 → major={major}, minor={minor}, patch={patch}")
    assert major == 9 and minor == 0 and patch == 1
    print("   ✅ 版本解析正确")
    
    # 5. 协议升级
    result = manager.upgrade_protocol("pulse_event_system", "9.1.0", "新增PulseIntent枚举")
    print(f"\n5. 协议升级: {result['protocol']} {result['old_version']} → {result['new_version']}")
    
    # 6. 多节点握手检查
    remote = {"pulse_event_system": "9.0.0", "organ_interface": "9.0.0"}
    handshake = manager.check_node_compatibility(remote)
    print(f"\n6. 节点握手: 兼容={handshake['compatible']}, 冲突={len(handshake['conflicts'])}")
    
    # 7. 统计
    stats = manager.get_stats()
    print(f"\n7. 统计: 协议{stats['total_protocols']}个, "
          f"检查{stats['total_checks']}次, 冲突{stats['total_conflicts']}次")
    
    manager.disable()
    print("\n=== 自测全部通过 ===")