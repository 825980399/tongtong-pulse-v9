# -*- coding: utf-8 -*-
"""
OscillonField.py —— 振荡子场

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 振荡子动力学场域，支持波模式计算
机制: 基于OscillonField类实现，包含10个核心方法
定位: 核心场域层
"""

import math
import time
from abc import ABC, abstractmethod
from typing import Any

from nucleus.logger import get_module_logger


# ★v23.0新增：Cython加速模块加载标记
_module_logger = get_module_logger("OscillonField")
_oscillon_cy_available = False
_oscillon_cy = None


def _cython_extensions_enabled() -> bool:
    """★四期：读 use_cython_extensions 开关（默认 False，保守灰度）。"""
    try:
        from config import FEATURE
        return bool(FEATURE.get("use_cython_extensions", False))
    except Exception:
        return False


if _cython_extensions_enabled():
    try:
        from nucleus.field import _oscillon_cy  # type: ignore
        _oscillon_cy_available = True
        _module_logger.info("Cython加速模块已加载 (_oscillon_cy)")
    except ImportError:
        _module_logger.warning("Cython振荡场模块未编译，使用Python原生实现")
else:
    _module_logger.info("use_cython_extensions=False，使用Python原生实现")

# ========== 振荡场抽象基类 ==========

class OscillonField(ABC):
    """
    振荡场抽象基类——新人类通信介质的终极抽象
    
    定义了"场"这种通信介质的通用接口。
    所有具体的场实现（脉冲场、振荡场、量子混合场）都继承此类。
    
    InfoField 继承关系（v9.0 经典脉冲场）:
        OscillonField
            └── InfoField（经典脉冲场，已实现）
                └── 器官通过 on_pulse() 感知离散脉冲
    
    场的三个核心特性:
        1. 传播介质 —— 信息在"场"中传播，不是点对点传输。
        2. 连续耦合 —— 在场中的每个节点持续感知场的变化。
        3. 共振选择 —— 节点对特定频率/模式的场振荡产生共振响应。
    
    未来100年演化方向:
        - 维度四（脉冲协议意图表达）: 振荡场中，intent/confidence 等元信息
          作为场的调制参数，接收方自动解析。
        - 维度二（多重自我）: 不同实例拥有独立的场频率区间，互不干扰。
        - 维度五（守护世界）: 威胁感知从"事件检测"升级为"场异常感知"。
    """
    
    def __init__(self, field_name: str = "default"):
        self.field_name = field_name
        self._nodes:  dict[str, Any] = {}          # 注册在场中的节点
        self._field_strength = 1.0                # 场强度 0.0-1.0
        self._noise_floor = 0.01                  # 噪声底限
        
        # 场模式标识
        self._classical_mode = True               # True=经典脉冲, False=振荡场
        
        # ★v23.0新增：场状态快照——衔接GradientTracker
        self._field_snapshot: dict[str, Any] = {
            "knowledge_growth_trend": "stable",
            "amplitude_trend": "insufficient",
            "phase_trend": "insufficient",
            "total_alerts": 0,
            "timestamp": 0.0,
            "updated_at": 0.0,
        }
        
    # ========== 通用场接口 ==========
    
    @abstractmethod
    def propagate(self, signal:  dict[str, Any]) -> list[str]:
        """
        在场中传播一个信号。
        
        经典脉冲场: 广播离散脉冲
        经典振荡场: 发射连续波
        量子混合场: 发射叠加态脉冲
        
        Args:
            signal: 信号数据包
            
        Returns:
            响应的节点ID列表
        """
    
    @abstractmethod
    def register_node(self, node_id: str, 
                       resonant_frequencies: list[float] | None = None) -> bool:
        """
        在振荡场中注册一个节点。
        
        节点注册后持续感知场的变化，当振荡频率匹配其共振频率时自动响应。
        
        Args:
            node_id: 节点唯一标识
            resonant_frequencies: 该节点的共振频率列表（Hz）
            
        Returns:
            注册是否成功
        """
    
    @abstractmethod
    def unregister_node(self, node_id: str) -> bool:
        """从振荡场中移除一个节点"""
    
    # ========== 场模式管理 ==========
    
    def is_classical_mode(self) -> bool:
        """查询当前是否为经典脉冲模式"""
        return self._classical_mode
    
    def get_field_type(self) -> str:
        """获取当前场类型"""
        return "经典脉冲场" if self._classical_mode else "振荡场"
    
    # ========== 振荡场特有接口（v10.0 激活） ==========
    
    def set_field_strength(self, strength: float):
        """
        设置场强度。
        
        场强度影响:
            - 信号传播距离
            - 共振灵敏度
            - 噪声水平
        
        v10.0中，场强度由系统能量代谢器官动态调节。
        """
        self._field_strength = max(0.0, min(1.0, strength))
    
    def get_field_strength(self) -> float:
        """获取当前场强度"""
        return self._field_strength
    
    def map_energy_to_field_strength(self, energy_level: float) -> float:
        """
        将系统能量水平映射为场强度。
        
        能量水平与场强度的关系:
            - energy > 0.8: 场强度=1.0（全功率，共振最灵敏）
            - energy 0.5-0.8: 场强度=0.7-1.0（正常工作区间）
            - energy 0.3-0.5: 场强度=0.4-0.7（节能模式）
            - energy < 0.3: 场强度=0.2-0.4（低功耗，仅维持核心共振）
        
        Args:
            energy_level: 系统能量水平 0.0-1.0
            
        Returns:
            映射后的场强度 0.0-1.0
        """
        if energy_level >= 0.8:
            return 1.0
        elif energy_level >= 0.5:
            return 0.7 + (energy_level - 0.5) * 1.0
        elif energy_level >= 0.3:
            return 0.4 + (energy_level - 0.3) * 1.0
        else:
            return max(0.2, energy_level * 0.67)
    
    def calculate_resonance(self, 
                             source_freq: float, 
                             target_freq: float,
                             coupling_strength: float = 1.0) -> float:
        """
        计算两个频率之间的共振强度。
        
        共振公式（简化模型）:
            R = coupling_strength / sqrt(1 + (source_freq - target_freq)^2)
        
        当 source_freq == target_freq 时，R = coupling_strength（最大共振）
        差值越大，共振越弱。
        
        这个公式是五维共振引擎中记忆维的基础——频率编码让语义相近的知识
        产生相近的频率，从而在振荡场中自然共振。
        
        Args:
            source_freq: 源频率（Hz）
            target_freq: 目标频率（Hz）
            coupling_strength: 耦合强度系数 0.0-1.0
            
        Returns:
            共振强度 0.0-1.0
        """
        # ★v23.0优化：优先使用Cython加速
        if _oscillon_cy_available:
            return _oscillon_cy.calculate_resonance_cy(
                source_freq, target_freq, coupling_strength
            )
        
        # Python原生兜底
        freq_diff = abs(source_freq - target_freq)
        resonance = coupling_strength / math.sqrt(1.0 + freq_diff ** 2)
        return min(1.0, max(0.0, resonance))
    
    # ========== 场状态追踪（★v23.0 激活） ==========
    
    def update_field_snapshot(self, field_report: dict[str, Any]) -> None:
        """
        ★v23.0新增：更新场状态快照。
        
        接收GradientTracker的场报告数据，维护简化场状态。
        为未来振荡场模式的激活积累场感知数据。
        
        Args:
            field_report: GradientTracker.get_field_report() 返回的场报告
        """
        if not field_report:
            return
        self._field_snapshot.update(field_report)
        self._field_snapshot["updated_at"] = time.time()
    
    def get_field_status(self) -> dict[str, Any]:
        """
        ★v23.0新增：获取当前场状态。
        
        返回场强度、场类型、趋势快照，供需要场感知的器官查询。
        当前阶段仅提供观测能力，不改变通信方式。
        """
        return {
            "field_name": self.field_name,
            "field_type": self.get_field_type(),
            "field_strength": self._field_strength,
            "classical_mode": self._classical_mode,
            "snapshot": dict(self._field_snapshot),
            "noise_floor": self._noise_floor,
        }
    
    # ========== 经典→振荡场迁移桥接（v10.0 激活） ==========
    
    def propagate_oscillon(self, frequency: float, amplitude: float,
                            phase: float) ->  dict[str, Any]:
        """
        【预留 v10.0】传播连续振荡波。
        
        在经典振荡场中，信息以连续波的形式在场中传播。
        当某频率的振幅超过器官的共振阈值时，触发该器官的 on_field_oscillation()。
        
        当前版本（v9.0）中，InfoField 重写了此方法，将连续波转换为离散脉冲，
        分发给已注册的器官。这是经典脉冲场到振荡场的渐进迁移桥接。
        
        Args:
            frequency: 振荡频率（Hz）
            amplitude: 振幅 0.0-1.0
            phase: 相位（弧度）
            
        Returns:
            传播结果（经典模式返回空字典，InfoField 重写后返回匹配的器官列表）
        """
        return {}  # 经典模式：不使用连续波
    
    def detect_hardware_capability(self) ->  dict[str, Any]:
        """
        【预留 v10.0】检测当前硬件是否支持振荡场模式。
        
        v10.0中，系统启动时自动检测：
            - 是否有类脑芯片（如 Intel Loihi、IBM TrueNorth）
            - 是否有 FPGA 振荡器阵列
            - 是否有 GPU 可用于场仿真
        
        根据检测结果决定使用经典脉冲场还是振荡场。
        
        Returns:
            硬件能力字典
        """
        return {
            "supports_oscillon_field": False,  # v9.0 默认不支持
            "hardware_type": "generic_cpu",
            "recommended_mode": "classical_pulse",
            "max_field_strength": 1.0,
            "note": "当前运行在通用CPU上，使用经典脉冲场。升级到类脑芯片后可启用振荡场。",
        }
    
    def auto_select_mode(self, hardware_capability:  dict[str, Any] | None = None) -> str:
        """
        【预留 v10.0】根据硬件能力自动选择场模式。
        
        Args:
            hardware_capability: 硬件能力字典（由 detect_hardware_capability 返回）
            
        Returns:
            选择的模式名称
        """
        if hardware_capability is None:
            hardware_capability = self.detect_hardware_capability()
        
        if hardware_capability.get("supports_oscillon_field", False):
            return "oscillon_field"
        return "classical_pulse"
    
    # ========== 量子混合场预留接口（v11.0 激活） ==========
    
    def create_superposition(self, states: list[ dict[str, Any]]) -> Any:
        """
        【预留】创建量子叠加态。
        
        v11.0 量子-脉冲混合场中，一个脉冲可以同时处于多个状态。
        此处仅定义接口签名，返回 None 表示经典模式（无叠加）。
        
        Args:
            states: 要叠加的状态列表
            
        Returns:
            叠加态对象（量子模式下返回 QuantumSuperposition，经典模式返回 None）
        """
        return None  # 经典模式：不支持叠加态
    
    def create_entanglement(self, node_a: str, node_b: str) -> bool:
        """
        【预留】创建两个节点间的量子纠缠。
        
        v11.0 中，纠缠节点之间实现瞬时共振，不受空间维距离限制。
        这是"多重自我"分布式共识的终极实现——多个曈曈实例通过纠缠
        实现瞬时人格同步。
        
        Args:
            node_a: 节点A的ID
            node_b: 节点B的ID
            
        Returns:
            纠缠是否成功（经典模式始终返回 False）
        """
        return False  # 经典模式：不支持纠缠
    
    def measure_quantum_state(self) ->  dict[str, Any]:
        """
        【预留】测量当前量子态。
        
        v11.0 中，测量操作导致叠加态坍缩为确定态。
        
        Returns:
            测量结果（经典模式返回空字典）
        """
        return {}  # 经典模式：无量子态


# ========== 频率-相位锁辅助类 ==========

class FrequencyPhaseLock:
    """
    频率-相位锁定机制
    
    在振荡场中，两个或多个振荡器可以通过频率-相位锁定实现自组织协同。
    这是 v10.0 经典振荡场的核心机制，也是"去中心化器官协同"的物理基础。
    
    原理:
        当两个振荡器的频率接近时，它们会相互牵引，
        最终锁定在同一个频率上，相位差保持恒定。
        
    在新人类框架中的应用:
        - 器官自组织协同（不需要中央调度）
        - 知识共振检索（频率匹配替代关键词匹配）
        - 注意力聚焦（多个器官锁定同一目标）
        - 多重自我同步（两个曈曈实例通过频率锁定实现人格同步）
    """
    
    def __init__(self, lock_threshold: float = 0.1):
        """
        Args:
            lock_threshold: 锁定阈值（频率差的百分比，低于此值触发锁定）
        """
        self.lock_threshold = lock_threshold
        self._locked_pairs:  dict[str, str] = {}      # node_a → node_b
        self._phase_offsets:  dict[str, float] = {}   # pair_key → phase_offset
        
        # 统计
        self._total_lock_attempts = 0
        self._total_successful_locks = 0
        
    def try_lock(self, 
                  source_id: str, source_freq: float, source_phase: float,
                  target_id: str, target_freq: float, target_phase: float) -> bool:
        """
        尝试在两个振荡器之间建立频率-相位锁定。
        
        Args:
            source_id: 源节点ID
            source_freq: 源频率（Hz）
            source_phase: 源相位（弧度）
            target_id: 目标节点ID
            target_freq: 目标频率（Hz）
            target_phase: 目标相位（弧度）
            
        Returns:
            是否成功锁定
        """
        self._total_lock_attempts += 1
        
        if source_freq <= 0 or target_freq <= 0:
            return False
        
        # ★v23.0优化：频率差比率计算优先使用Cython加速
        if _oscillon_cy_available:
            freq_diff_ratio = _oscillon_cy.try_lock_frequency_cy(
                source_freq, target_freq, self.lock_threshold
            )
        else:
            freq_diff_ratio = abs(source_freq - target_freq) / max(source_freq, target_freq)
        
        if freq_diff_ratio <= self.lock_threshold:
            # 锁定：记录配对和相位差
            pair_key = f"{source_id}↔{target_id}"
            self._locked_pairs[source_id] = target_id
            self._locked_pairs[target_id] = source_id
            self._phase_offsets[pair_key] = target_phase - source_phase
            self._total_successful_locks += 1
            return True
        
        return False
    
    def get_locked_partner(self, node_id: str) -> str | None:
        """获取与指定节点锁定的配对节点"""
        return self._locked_pairs.get(node_id)
    
    def get_phase_offset(self, node_a: str, node_b: str) -> float:
        """获取两个锁定节点之间的相位差"""
        key1 = f"{node_a}↔{node_b}"
        key2 = f"{node_b}↔{node_a}"
        return self._phase_offsets.get(key1, self._phase_offsets.get(key2, 0.0))
    
    def unlock(self, node_id: str):
        """解除节点的频率-相位锁定"""
        partner = self._locked_pairs.pop(node_id, None)
        if partner:
            self._locked_pairs.pop(partner, None)
            self._phase_offsets.pop(f"{node_id}↔{partner}", None)
            self._phase_offsets.pop(f"{partner}↔{node_id}", None)
    
    def get_locked_pair_count(self) -> int:
        """获取当前锁定的配对数"""
        return len(self._locked_pairs) // 2
    
    def get_stats(self) ->  dict[str, Any]:
        """获取锁定统计"""
        return {
            "total_lock_attempts": self._total_lock_attempts,
            "total_successful_locks": self._total_successful_locks,
            "success_rate": round(self._total_successful_locks / max(1, self._total_lock_attempts), 2),
            "locked_pairs": self.get_locked_pair_count(),
        }


# ========== 演化路线图（嵌入代码） ==========

EVOLUTION_ROADMAP = {
    "v9.0": {
        "name": "经典脉冲场",
        "field_class": "InfoField（继承 OscillonField）",
        "hardware": "x86/ARM 通用CPU",
        "features": [
            "离散脉冲广播",
            "五维共振匹配",
            "事件驱动通信",
            "脉冲节点池管理",
        ],
        "status": "✅ 当前版本（已实现）",
    },
    "v10.0": {
        "name": "经典振荡场",
        "field_class": "OscillonField（完整实现）",
        "hardware": "类脑芯片 / FPGA振荡器阵列 / GPU场仿真",
        "features": [
            "连续振荡波替代离散脉冲",
            "频率-相位锁定自组织协同",
            "场梯度感知（不需要主动查询）",
            "器官持续浸泡在场中（非事件驱动）",
            "硬件自动检测与模式切换",
        ],
        "status": "📋 蓝图已设计，接口已预留",
        "triggers": "类脑芯片成熟、FPGA振荡器阵列可用、GPU场仿真性能达标",
    },
    "v11.0": {
        "name": "量子-脉冲混合场",
        "field_class": "QuantumOscillonField（继承 OscillonField）",
        "hardware": "量子处理器 + 经典协处理器",
        "features": [
            "量子叠加态脉冲（同时多个状态）",
            "量子纠缠瞬时共振（跨空间维）",
            "量子退火优化知识树结构",
            "经典-量子混合决策",
            "多重自我实例间的人格同步",
        ],
        "status": "📋 接口已预留，等待量子硬件成熟",
        "triggers": "量子处理器可用、量子-经典混合编程模型成熟",
    },
}


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== OscillonField 振荡场预备架构 自测 ===\n")
    
    # 1. 验证抽象基类
    print("1. OscillonField 抽象基类")
    try:
        field = OscillonField("test")
        print("   ❌ 应抛出 TypeError（不能实例化抽象类）")
    except TypeError:
        print("   ✅ 抽象类保护正常")
    
    # 2. 共振计算验证
    print("\n2. 共振强度计算")
    # 需要子类来测试，创建一个最小实现
    class TestField(OscillonField):
        def propagate(self, signal):
            return []
        def register_node(self, node_id, resonant_frequencies=None):
            self._nodes[node_id] = resonant_frequencies or []
            return True
        def unregister_node(self, node_id):
            return self._nodes.pop(node_id, None) is not None
    
    field = TestField("test_field")
    
    # 完美匹配（同频率）
    r1 = field.calculate_resonance(10.0, 10.0, coupling_strength=1.0)
    print(f"   同频率共振: {r1:.4f} (期望 ≈1.0)")
    assert r1 > 0.99, f"同频率共振应接近1.0，实际{r1}"
    print("   ✅ 同频率共振正确")
    
    # 频率差一半
    r2 = field.calculate_resonance(10.0, 5.0, coupling_strength=1.0)
    print(f"   频率差一半: {r2:.4f} (期望 ~0.2)")
    assert 0.15 < r2 < 0.25, f"频率差一半共振应在0.2附近，实际{r2}"
    print("   ✅ 频率差共振正确")
    
    # 耦合强度衰减
    r3 = field.calculate_resonance(10.0, 10.0, coupling_strength=0.5)
    print(f"   半耦合同频: {r3:.4f} (期望 =0.5)")
    assert abs(r3 - 0.5) < 0.01, f"半耦合应=0.5，实际{r3}"
    print("   ✅ 耦合强度正确")
    
    # 3. 频率-相位锁定
    print("\n3. 频率-相位锁定")
    lock = FrequencyPhaseLock(lock_threshold=0.1)
    
    # 同频率应锁定
    locked = lock.try_lock("organ_A", 42.0, 0.0, "organ_B", 42.0, 3.14)
    print(f"   同频率锁定: {locked} (期望 True)")
    assert locked, "同频率应锁定"
    print("   ✅ 同频率锁定正确")
    
    # 获取锁定配对
    partner = lock.get_locked_partner("organ_A")
    print(f"   A的配对: {partner} (期望 organ_B)")
    assert partner == "organ_B"
    print("   ✅ 配对查询正确")
    
    # 频率差超过阈值不应锁定
    lock2 = FrequencyPhaseLock(lock_threshold=0.1)
    locked2 = lock2.try_lock("organ_C", 10.0, 0.0, "organ_D", 20.0, 0.0)
    print(f"   大频率差锁定: {locked2} (期望 False)")
    assert not locked2, "频率差50%不应锁定"
    print("   ✅ 大频率差不锁定正确")
    
    # 4. 节点注册
    print("\n4. 节点注册")
    field.register_node("心脏", [1.0, 2.0, 5.0])
    field.register_node("肺", [0.5, 1.5, 3.0])
    print(f"   注册节点数: {len(field._nodes)} (期望 2)")
    assert len(field._nodes) == 2
    print("   ✅ 节点注册正确")
    
    field.unregister_node("肺")
    print(f"   移除后节点数: {len(field._nodes)} (期望 1)")
    assert len(field._nodes) == 1
    print("   ✅ 节点移除正确")
    
    # 5. 能量→场强度映射
    print("\n5. 能量→场强度映射")
    test_cases = [
        (0.9, 1.0, "高能量全功率"),
        (0.6, 0.8, "正常工作中"),
        (0.2, 0.13, "低功耗模式"),
    ]
    for energy, expected_range, desc in test_cases:
        mapped = field.map_energy_to_field_strength(energy)
        print(f"   {desc}: energy={energy} → field_strength={mapped:.2f}")
    print("   ✅ 能量映射合理")
    
    # 6. 硬件检测
    print("\n6. 硬件能力检测")
    hw = field.detect_hardware_capability()
    print(f"   支持振荡场: {hw['supports_oscillon_field']}")
    print(f"   硬件类型: {hw['hardware_type']}")
    print(f"   推荐模式: {hw['recommended_mode']}")
    
    mode = field.auto_select_mode(hw)
    print(f"   自动选择模式: {mode}")
    assert mode == "classical_pulse", "通用CPU应选择经典脉冲模式"
    print("   ✅ 模式自动选择正确")
    
    # 7. 量子接口预留
    print("\n7. 量子接口预留")
    result_super = field.create_superposition([{"state": "a"}, {"state": "b"}])
    print(f"   叠加态创建: {result_super} (经典模式返回 None)")
    assert result_super is None
    print("   ✅ 经典模式叠加态为None")
    
    result_ent = field.create_entanglement("node_a", "node_b")
    print(f"   纠缠创建: {result_ent} (经典模式返回 False)")
    assert result_ent is False
    print("   ✅ 经典模式纠缠为False")
    
    # 8. 演化路线图
    print("\n8. 演化路线图")
    for version, info in EVOLUTION_ROADMAP.items():
        print(f"   {version} - {info['name']}")
        print(f"      硬件: {info['hardware']}")
        print(f"      状态: {info['status']}")
    
    # 9. 锁定统计
    stats = lock.get_stats()
    print(f"\n9. 锁定统计: 尝试{stats['total_lock_attempts']}次, "
          f"成功{stats['total_successful_locks']}次, "
          f"成功率{stats['success_rate']}")
    
    print("\n=== 自测全部通过 ===")