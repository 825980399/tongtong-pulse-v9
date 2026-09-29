# -*- coding: utf-8 -*-
"""
OrganProtocol.py —— 器官协议

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 器官间通信协议定义与解析
机制: 基于OrganProtocolType类实现，包含10个核心方法
定位: 通信基础设施层
"""

import threading
import time
from enum import Enum
from typing import Any



class OrganProtocolType(Enum):
    PUBLISH_SUBSCRIBE = "pub_sub"       # 发布/订阅（当前v9.0模式）
    REQUEST_RESPONSE = "req_resp"       # 请求/响应
    STREAM = "stream"                   # 连续流（v10.0 振荡场模式）

class OrganStatus_v2(Enum):             # 避免与基类OrganStatus重名，加_v2后缀
    ACTIVE = "active"
    INACTIVE = "inactive"
    UPGRADING = "upgrading"
    DEPRECATED = "deprecated"

class OrganActivityModel(Enum):
    """★第106批 T-106c（预埋）：器官活跃度模型，供沉默检测按模型分档。

    取值：
      - PULSE     脉冲型：有任务才启动，空闲静默属正常（多数低频器官）。
      - PASSIVE   被动监听型：仅在用户输入/事件触发时活跃（如对话模块）。
      - CONSTRAINT 约束型：作为约束/配置存在，不进沉默检测池
                    （如 framework_self_modify_gate、FunctionLoader 等非器官实体）。
    本批仅提供声明接口与查询方法；不修改现有沉默检测逻辑，后续批次再接入分档过滤。
    """
    PULSE = "pulse"
    PASSIVE = "passive"
    CONSTRAINT = "constraint"

class OrganProtocol:
    """
    动态器官协议管理器
    
    工作原理:
        1. 器官通过注册协议（register_manifest）声明自身能力和版本。
        2. 管理器验证协议兼容性、依赖完整性和并发安全性。
        3. 器官升级时，协议管理器协调新旧版本切换，失败自动回滚。
        4. 器官退出时，协议管理器安全移除其所有依赖关系。
    
    当前状态（v9.0）:
        - 接口完整定义，基础注册/注销/升级已实现。
        - 通过功能开关控制激活状态，默认关闭。
    """
    
    def __init__(self):
        # 已注册的器官清单: organ_name → manifest
        self._registered_organs: dict[str, dict[str, Any]] = {}
        
        # 协议版本历史（用于回滚兼容）
        self._protocol_history: list[dict[str, Any]] = []
        
        # 依赖关系图: organ_name → [dependent_organ_names]
        self._dependency_graph: dict[str, list[str]] = {}
        
        # 并发安全锁
        self._lock = threading.Lock()
        
        # 统计
        self._total_registered = 0
        self._total_unregistered = 0
        self._total_upgrades = 0
        self._total_rollbacks = 0
        
        # 功能开关
        self._enabled = False

    # ========== 框架控制 ==========

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    # ========== 器官注册协议 ==========

    def register_manifest(self, organ_name: str, manifest: dict[str, Any]) -> dict[str, Any]:
        """
        注册一个器官的声明文件。

        Args:
            organ_name: 器官名称
            manifest: 器官声明，必须包含 version, protocols, capabilities, dependencies

        Returns:
            注册结果
        """
        if not self._enabled:
            return {"status": "disabled", "message": "器官协议管理器未激活"}

        # 校验必要字段
        required_fields = ["version", "protocols", "capabilities", "dependencies"]
        for field in required_fields:
            if field not in manifest:
                return {"status": "rejected", "reason": f"声明文件缺少必要字段: {field}"}

        # ★第106批 T-106c（预埋）：可选字段 activity_model 校验（不强制；存在才校验）。
        #   合法取值见 OrganActivityModel；声明非法值直接拒绝，避免后续分档逻辑误判。
        _am = manifest.get("activity_model")
        if _am is not None and _am not in {m.value for m in OrganActivityModel}:
            return {"status": "rejected",
                    "reason": f"activity_model 取值非法: {_am!r}（允许: pulse/passive/constraint）"}

        with self._lock:
            # 检查是否已注册（幂等）
            if organ_name in self._registered_organs:
                existing = self._registered_organs[organ_name]
                if existing["manifest"] == manifest:
                    return {"status": "already_registered", "organ": organ_name, "note": "声明文件未变更，跳过"}
                else:
                    # ★P0-2修复：声明文件变更，走内部升级流程（已在锁内）
                    return self._do_upgrade(organ_name, existing, manifest)

            # 检查版本兼容性
            compatibility = self._check_compatibility(manifest)
            if not compatibility["compatible"]:
                return {"status": "rejected", "reason": compatibility["reason"]}

            # 检查依赖完整性
            dependency_check = self._check_dependencies(manifest.get("dependencies", []))
            if not dependency_check["satisfied"]:
                return {"status": "rejected", "reason": f"依赖不满足: {dependency_check['missing']}"}

            # 注册
            self._registered_organs[organ_name] = {
                "manifest": manifest,
                "registered_at": time.time(),
                "status": OrganStatus_v2.ACTIVE,
                # ★第106批 T-106c（预埋）：保存活跃度模型，供后续沉默检测分档过滤使用。
                "activity_model": manifest.get("activity_model"),
            }
            self._total_registered += 1

            # 更新依赖关系图
            self._update_dependency_graph(organ_name, manifest.get("dependencies", []))

            self._record_protocol_event("organ_registered", organ_name=organ_name, version=manifest["version"])

            return {"status": "registered", "organ": organ_name, "version": manifest["version"]}

    def unregister_organ(self, organ_name: str, force: bool = False) -> dict[str, Any]:
        """
        注销一个器官。

        Args:
            organ_name: 器官名称
            force: 是否强制注销（忽略依赖检查）

        Returns:
            注销结果
        """
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            if organ_name not in self._registered_organs:
                return {"status": "not_found"}

            # 检查是否有其他器官依赖此器官
            if not force:
                dependents = self._get_dependents(organ_name)
                if dependents:
                    return {
                        "status": "blocked",
                        "reason": f"以下器官依赖此器官，无法注销: {dependents}",
                        "dependents": dependents,
                        "hint": "使用 force=True 强制注销",
                    }

            del self._registered_organs[organ_name]
            self._total_unregistered += 1

            # 清理依赖关系图
            self._remove_from_dependency_graph(organ_name)

            self._record_protocol_event("organ_unregistered", organ_name=organ_name)

            return {"status": "unregistered", "organ": organ_name}

    # ========== 版本与升级管理 ==========

    def upgrade_organ(self, organ_name: str, new_manifest: dict[str, Any]) -> dict[str, Any]:
        """
        升级一个已注册器官的协议版本。
        ★P0-2修复：锁外入口，内部调用无锁版本_do_upgrade。
        """
        if not self._enabled:
            return {"status": "disabled"}
        
        with self._lock:
            if organ_name not in self._registered_organs:
                return {"status": "not_found"}
            
            return self._do_upgrade(organ_name, self._registered_organs[organ_name], new_manifest)
    def _do_upgrade(self, organ_name: str, old_entry: dict[str, Any], 
                     new_manifest: dict[str, Any]) -> dict[str, Any]:
        """
        ★P0-2+P1-2修复：内部无锁升级逻辑。
        调用方必须已持有 self._lock。
        """
        old_manifest = old_entry["manifest"]
        
        # 检查新版本兼容性
        compatibility = self._check_compatibility(new_manifest)
        if not compatibility["compatible"]:
            return {"status": "rejected", "reason": compatibility["reason"]}
        
        # ★P1-2修复：升级前深拷贝旧状态，确保回滚时能完整恢复
        _backup_entry = {
            "manifest": dict(old_manifest),
            "registered_at": old_entry.get("registered_at", time.time()),
            "status": old_entry.get("status", OrganStatus_v2.ACTIVE),
        }
        
        # 标记为升级中
        old_entry["status"] = OrganStatus_v2.UPGRADING
        old_entry["upgrade_started_at"] = time.time()
        
        try:
            # 应用新声明
            old_entry["manifest"] = new_manifest
            old_entry["upgraded_at"] = time.time()
            old_entry["status"] = OrganStatus_v2.ACTIVE
            # ★第106批 T-106c（预埋）：同步活跃度模型（声明可能新增/变更 activity_model）。
            old_entry["activity_model"] = new_manifest.get("activity_model")
            self._total_upgrades += 1
            
            # 更新依赖关系图
            self._update_dependency_graph(organ_name, new_manifest.get("dependencies", []))
            
            self._record_protocol_event(
                "organ_upgraded",
                organ_name=organ_name,
                old_version=old_manifest["version"],
                new_version=new_manifest["version"],
            )
            
            return {"status": "upgraded", "organ": organ_name, 
                    "new_version": new_manifest["version"]}
        
        except Exception as e:
            # ★P1-2修复：用深拷贝的备份完整恢复旧状态
            self._registered_organs[organ_name] = _backup_entry
            self._total_rollbacks += 1
            
            self._record_protocol_event(
                "organ_upgrade_failed",
                organ_name=organ_name,
                error=str(e),
            )
            
            return {
                "status": "rollback",
                "organ": organ_name,
                "reason": f"升级失败，已回滚: {e}",
                "restored_version": old_manifest["version"],
            }
    def rollback_organ(self, organ_name: str) -> dict[str, Any]:
        """
        手动回滚到上一个协议版本。

        Args:
            organ_name: 器官名称

        Returns:
            回滚结果
        """
        if not self._enabled:
            return {"status": "disabled"}

        with self._lock:
            if organ_name not in self._registered_organs:
                return {"status": "not_found"}

            # 从历史记录中查找上一个版本
            history = [h for h in self._protocol_history 
                      if h["event"] == "organ_upgraded" and h["details"].get("organ_name") == organ_name]
            
            if not history:
                return {"status": "no_history", "message": "未找到可回滚的历史版本"}

            last_upgrade = history[-1]
            old_version = last_upgrade["details"]["old_version"]
            
            self._total_rollbacks += 1
            self._record_protocol_event("organ_rollback", organ_name=organ_name, restored_version=old_version)

            return {
                "status": "rollback_initiated",
                "organ": organ_name,
                "restored_version": old_version,
                "note": "完整回滚功能将在v10.0实现，当前仅记录",
            }

    # ========== 查询接口 ==========

    def get_organ_manifest(self, organ_name: str) -> dict[str, Any] | None:
        """获取指定器官的声明文件"""
        entry = self._registered_organs.get(organ_name)
        return entry["manifest"] if entry else None

    def get_activity_model(self, organ_name: str) -> str | None:
        """★第106批 T-106c（预埋）：获取器官的活跃度模型；未声明返回 None。

        后续批次可由沉默检测（PulseBloodVessel._check_silent_organs）调用此方法，
        将 ``activity_model == "constraint"`` 的器官排除出沉默检测池；本批仅提供接口，
        不改现有检测逻辑，亦不要求现有器官补充该字段。
        """
        entry = self._registered_organs.get(organ_name)
        return entry.get("activity_model") if entry else None

    def get_all_active_organs(self) -> list[str]:
        """获取所有活跃器官列表"""
        return [
            name for name, entry in self._registered_organs.items()
            if entry["status"] == OrganStatus_v2.ACTIVE
        ]

    def get_dependency_graph(self) -> dict[str, list[str]]:
        """获取依赖关系图"""
        return dict(self._dependency_graph)

    # ========== 内部方法 ==========

    def _check_compatibility(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """检查声明文件的协议兼容性"""
        supported_protocols = [p.value for p in OrganProtocolType]
        for proto in manifest.get("protocols", []):
            if proto not in supported_protocols:
                return {
                    "compatible": False,
                    "reason": f"不支持的协议: {proto}。支持的协议: {supported_protocols}",
                }
        return {"compatible": True, "reason": "ok"}

    def _check_dependencies(self, dependencies: list[str]) -> dict[str, Any]:
        """检查依赖项是否已注册"""
        missing = []
        for dep in dependencies:
            if dep not in self._registered_organs:
                missing.append(dep)
        return {
            "satisfied": len(missing) == 0,
            "missing": missing,
        }

    def _update_dependency_graph(self, organ_name: str, dependencies: list[str]):
        """更新依赖关系图"""
        self._dependency_graph[organ_name] = dependencies

    def _remove_from_dependency_graph(self, organ_name: str):
        """从依赖关系图中移除器官"""
        self._dependency_graph.pop(organ_name, None)

    def _get_dependents(self, organ_name: str) -> list[str]:
        """查找依赖此器官的其他器官"""
        dependents = []
        for name, deps in self._dependency_graph.items():
            if organ_name in deps:
                dependents.append(name)
        return dependents

    def _record_protocol_event(self, event_type: str, **kwargs):
        """记录协议变更事件"""
        self._protocol_history.append({
            "event": event_type,
            "timestamp": time.time(),
            "details": kwargs,
        })
        # 限制历史记录大小
        if len(self._protocol_history) > 100:
            self._protocol_history = self._protocol_history[-50:]

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取协议管理器统计"""
        return {
            "enabled": self._enabled,
            "total_registered": self._total_registered,
            "total_unregistered": self._total_unregistered,
            "total_upgrades": self._total_upgrades,
            "total_rollbacks": self._total_rollbacks,
            "active_organs": len(self.get_all_active_organs()),
            "history_size": len(self._protocol_history),
            "dependency_graph_size": len(self._dependency_graph),
        }

    # ========== 未来演化预留（v10.0 振荡场 / v11.0 量子场） ==========

    def on_field_oscillation(self, field_data: dict[str, Any]):
        """
        【预留 v10.0】振荡场中，器官协议由场的频率特征自动协商。
        不再需要显式注册，器官通过场频率自动发现并同步协议。
        """

    def negotiate_quantum_protocol(self, organ_name: str) -> dict[str, Any]:
        """
        【预留 v11.0】在量子混合场中协商量子通信协议。
        
        Args:
            organ_name: 器官名称
            
        Returns:
            协商结果（经典模式返回不支持）
        """
        return {
            "status": "unsupported",
            "reason": "量子协议协商仅在v11.0量子混合场中可用",
            "organ": organ_name,
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== OrganProtocol 自测（P3补齐版） ===\n")
    
    protocol = OrganProtocol()
    protocol.enable()
    
    # 1. 注册器官
    manifest = {
        "version": "1.0.0",
        "protocols": ["pub_sub"],
        "capabilities": ["知识消化", "五维归属"],
        "dependencies": ["node_pool", "knowledge_tree", "frequency_codec"],
    }
    result = protocol.register_manifest("胃", manifest)
    print(f"1. 注册: {result['status']} v{result.get('version', '?')}")
    
    # 2. 幂等注册
    result_idem = protocol.register_manifest("胃", manifest)
    print(f"2. 幂等注册: {result_idem['status']} - {result_idem.get('note', '')}")
    
    # 3. 升级器官
    manifest_v2 = {**manifest, "version": "2.0.0", "capabilities": ["知识消化", "五维归属", "词表增强"]}
    result2 = protocol.upgrade_organ("胃", manifest_v2)
    print(f"3. 升级: {result2['status']} v{result2.get('new_version', '?')}")
    
    # 4. 依赖检查
    result_dep = protocol.register_manifest("肝", {
        "version": "1.0.0",
        "protocols": ["pub_sub"],
        "capabilities": ["知识压缩"],
        "dependencies": ["胃", "node_pool"],  # 依赖已注册的胃
    })
    print(f"4. 依赖注册: {result_dep['status']}")
    
    # 5. 注销阻塞
    result_block = protocol.unregister_organ("胃")
    print(f"5. 注销阻塞: {result_block['status']} - 依赖者: {result_block.get('dependents', [])}")
    
    # 6. 强制注销
    result_force = protocol.unregister_organ("胃", force=True)
    print(f"6. 强制注销: {result_force['status']}")
    
    # 7. 统计
    stats = protocol.get_stats()
    print(f"7. 统计: 注册{stats['total_registered']} 注销{stats['total_unregistered']} "
          f"升级{stats['total_upgrades']} 回滚{stats['total_rollbacks']} 活跃{stats['active_organs']}")
    
    protocol.disable()
    print("\n=== 自测全部通过 ===")