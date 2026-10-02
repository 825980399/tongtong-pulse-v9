# ⚠️ @deprecated (157-D C-4 躯体/多机封存 / Q157-2 已裁):
#   躯体半区·reflex：反射弧缺 sensor/actuator 两端闭环，无触发路径；③封存待 PHASE19 具身化再启。
#   复活须待 PHASE19（具身化/多机）专门批；禁止新代码 import 本模块（若仍在用请先接线）。
"""reflex —— 本能反射层（硬件抽象层 · 物理自我保护的第一道防线）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import threading
import time
from typing import Any
from nucleus._silent_except import silent_exc


class ReflexRule:
    """
    反射规则定义
    
    一条反射规则包含：
        - 触发条件：传感器类型、阈值、比较方式
        - 执行动作：目标执行器、动作指令、参数
        - 优先级：同时触发多条规则时的执行顺序
        - 冷却时间：防止同一条件反复触发
    """
    
    def __init__(self, 
                 rule_id: str,
                 description: str,
                 sensor_type: str,
                 sensor_field: str,
                 threshold: float,
                 comparison: str = "greater_than",
                 target_actuator: str = "",
                 action: str = "stop",
                 action_params: dict[str, Any] | None = None,
                 priority: int = 5,
                 cooldown_seconds: float = 1.0,
                 enabled: bool = True):
        """
        Args:
            rule_id: 规则唯一标识
            description: 规则描述
            sensor_type: 触发传感器类型
            sensor_field: 传感器数据中的字段名
            threshold: 触发阈值
            comparison: 比较方式（greater_than/less_than/equals）
            target_actuator: 目标执行器标识
            action: 动作名称
            action_params: 动作参数
            priority: 优先级（0-10，10最高）
            cooldown_seconds: 冷却时间（秒）
            enabled: 是否启用
        """
        self.rule_id = rule_id
        self.description = description
        self.sensor_type = sensor_type
        self.sensor_field = sensor_field
        self.threshold = threshold
        self.comparison = comparison
        self.target_actuator = target_actuator
        self.action = action
        self.action_params = action_params or {}
        self.priority = priority
        self.cooldown_seconds = cooldown_seconds
        self.enabled = enabled
        self.last_triggered = 0.0
        self.trigger_count = 0


class Reflex:
    """
    本能反射层
    
    工作原理:
        1. 持续监听传感器读数更新。
        2. 遍历反射规则库，检查是否有条件被触发。
        3. 条件满足时，立即向执行器发送动作指令。
        4. 同时发射脉冲通知器官层（不等待响应）。
        5. 冷却期内不重复触发同一规则。
    
    当前状态（v9.0）:
        - 接口完整定义，预置基本保护规则。
        - 功能开关默认关闭。
        - 在桌面PC环境下，物理传感器和执行器有限。
    """
    
    def __init__(self):
        # 反射规则库
        self._rules: dict[str, ReflexRule] = {}
        
        # 关联组件引用
        self.sensor_module = None
        self.actuator_module = None
        self.info_field = None
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 统计
        self._total_triggers = 0
        
        # 功能开关
        self._enabled = False
        
        # 预置基本保护规则
        self._init_default_rules()

    def _init_default_rules(self):
        """预置基本保护规则"""
        # 碰撞检测→紧急停止
        self.add_rule(ReflexRule(
            rule_id="collision_stop",
            description="检测到碰撞时立即停止所有电机",
            sensor_type="imu",
            sensor_field="acceleration.z",
            threshold=2.0,
            comparison="greater_than",
            target_actuator="motor",
            action="emergency_stop",
            priority=10,
            cooldown_seconds=2.0,
        ))
        # 电量过低→进入低功耗
        self.add_rule(ReflexRule(
            rule_id="low_battery_protection",
            description="电量低于5%时停止所有非必要动作",
            sensor_type="power",
            sensor_field="battery_level",
            threshold=5.0,
            comparison="less_than",
            target_actuator="motor",
            action="power_save_mode",
            priority=9,
            cooldown_seconds=30.0,
        ))
        # CPU过热→降速保护
        self.add_rule(ReflexRule(
            rule_id="cpu_thermal_throttle",
            description="CPU温度超过90℃时降低运动速度",
            sensor_type="temperature",
            sensor_field="cpu",
            threshold=90.0,
            comparison="greater_than",
            target_actuator="motor",
            action="reduce_speed",
            action_params={"speed_factor": 0.3},
            priority=8,
            cooldown_seconds=10.0,
        ))

    # ========== 框架控制 ==========

    def enable(self):
        """激活反射层"""
        self._enabled = True

    def disable(self):
        """关闭反射层"""
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def set_sensor_module(self, sensor_module):
        self.sensor_module = sensor_module

    def set_actuator_module(self, actuator_module):
        self.actuator_module = actuator_module

    def set_info_field(self, info_field):
        """注入信息场，用于反射触发后通知器官层"""
        self.info_field = info_field

    # ========== 规则管理 ==========

    def add_rule(self, rule: ReflexRule) -> str:
        """添加一条反射规则"""
        with self._lock:
            self._rules[rule.rule_id] = rule
        return rule.rule_id

    def remove_rule(self, rule_id: str) -> bool:
        """移除一条反射规则"""
        with self._lock:
            if rule_id in self._rules:
                del self._rules[rule_id]
                return True
        return False

    def enable_rule(self, rule_id: str) -> bool:
        """启用指定规则"""
        rule = self._rules.get(rule_id)
        if rule:
            rule.enabled = True
            return True
        return False

    def disable_rule(self, rule_id: str) -> bool:
        """禁用指定规则"""
        rule = self._rules.get(rule_id)
        if rule:
            rule.enabled = False
            return True
        return False

    def get_rules(self) -> list[dict[str, Any]]:
        """获取所有反射规则"""
        return [
            {
                "rule_id": r.rule_id,
                "description": r.description,
                "sensor_type": r.sensor_type,
                "threshold": r.threshold,
                "action": r.action,
                "priority": r.priority,
                "enabled": r.enabled,
                "trigger_count": r.trigger_count,
                "in_cooldown": time.time() - r.last_triggered < r.cooldown_seconds,
            }
            for r in self._rules.values()
        ]

    # ========== 核心：反射检测与执行 ==========

    def scan_and_execute(self) -> list[dict[str, Any]]:
        """
        扫描所有传感器读数，检查反射条件，执行触发的动作。
        
        按优先级从高到低排序规则，同一轮扫描中每条规则最多触发一次。

        Returns:
            本轮触发的反射动作列表
        """
        if not self._enabled:
            return []

        # 获取最新传感器读数
        sensor_readings = {}
        if self.sensor_module and self.sensor_module.is_enabled():
            sensor_readings = self.sensor_module.get_all_readings()

        if not sensor_readings:
            return []

        triggered_actions = []
        now = time.time()

        with self._lock:
            # 按优先级排序（高优先级先执行）
            sorted_rules = sorted(
                self._rules.values(),
                key=lambda r: r.priority,
                reverse=True,
            )

            for rule in sorted_rules:
                if not rule.enabled:
                    continue

                # 冷却检查
                if now - rule.last_triggered < rule.cooldown_seconds:
                    continue

                # 检查触发条件
                if self._check_condition(rule, sensor_readings):
                    # 执行动作
                    result = self._execute_action(rule)
                    
                    rule.last_triggered = now
                    rule.trigger_count += 1
                    self._total_triggers += 1

                    triggered_actions.append({
                        "rule_id": rule.rule_id,
                        "description": rule.description,
                        "action": rule.action,
                        "result": result,
                        "timestamp": now,
                    })

                    # 通知器官层
                    self._notify_organs(rule, sensor_readings)

        return triggered_actions

    def _check_condition(self, rule: ReflexRule, sensor_readings: dict[str, Any]) -> bool:
        """
        检查反射规则的条件是否满足。

        Args:
            rule: 反射规则
            sensor_readings: 传感器读数字典

        Returns:
            条件是否满足
        """
        # 从传感器读数中查找匹配的数据
        value = None
        for device_id, reading in sensor_readings.items():
            if not isinstance(reading, dict):
                continue
            # 检查读数中是否包含目标字段
            if rule.sensor_field in reading:
                value = reading[rule.sensor_field]
                break
            # 检查嵌套字段
            for key in reading:
                if isinstance(reading[key], dict) and rule.sensor_field in reading[key]:
                    value = reading[key][rule.sensor_field]
                    break

        if value is None:
            return False

        try:
            value = float(value)
        except (ValueError, TypeError) as e:
            silent_exc(e, where="somatics.reflex::_check_condition L315")
            return False

        # 比较判断
        if rule.comparison == "greater_than":
            return value > rule.threshold
        elif rule.comparison == "less_than":
            return value < rule.threshold
        elif rule.comparison == "equals":
            return abs(value - rule.threshold) < 0.001
        else:
            return False

    def _execute_action(self, rule: ReflexRule) -> dict[str, Any]:
        """
        执行反射规则定义的动作。

        Args:
            rule: 反射规则

        Returns:
            执行结果
        """
        if not self.actuator_module:
            return {"status": "no_actuator", "action": rule.action}

        # 根据动作类型分发
        if rule.action == "emergency_stop":
            return self.actuator_module.emergency_stop()
        elif rule.action == "power_save_mode":
            return self.actuator_module.send_command(
                rule.target_actuator,
                "set_mode",
                {"mode": "power_save"},
            )
        elif rule.action == "reduce_speed":
            speed_factor = rule.action_params.get("speed_factor", 0.5)
            return self.actuator_module.send_command(
                rule.target_actuator,
                "set_speed_factor",
                {"factor": speed_factor},
            )
        else:
            return self.actuator_module.send_command(
                rule.target_actuator,
                rule.action,
                rule.action_params,
            )

    def _notify_organs(self, rule: ReflexRule, sensor_readings: dict[str, Any]):
        """
        反射触发后通知器官层（非阻塞，仅做记录）。

        Args:
            rule: 被触发的反射规则
            sensor_readings: 触发时的传感器读数
        """
        if self.info_field:
            # 这里可以发射脉冲通知大脑皮层或健康监控
            pass

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取反射层统计"""
        return {
            "enabled": self._enabled,
            "total_rules": len(self._rules),
            "active_rules": sum(1 for r in self._rules.values() if r.enabled),
            "total_triggers": self._total_triggers,
            "rules": self.get_rules(),
        }

    # ========== 未来演化预留 ==========

    def learn_reflex(self, sensor_type: str, action: str, success_rate: float) -> bool:
        """
        【预留 v10.0】基于经验自动学习新的反射规则。
        
        Args:
            sensor_type: 传感器类型
            action: 有效动作
            success_rate: 该动作的成功率

        Returns:
            是否成功固化为新规则
        """
        if success_rate > 0.9:
            new_rule = ReflexRule(
                rule_id=f"learned_{sensor_type}_{action}_{int(time.time())}",
                description=f"自动学习的反射: {sensor_type}→{action}",
                sensor_type=sensor_type,
                sensor_field="auto_detected",
                threshold=0.5,
                target_actuator="motor",
                action=action,
                priority=3,
                cooldown_seconds=5.0,
            )
            self.add_rule(new_rule)
            return True
        return False


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== Reflex 自测 ===\n")
    
    reflex = Reflex()
    reflex.enable()
    
    # 模拟传感器模块
    class MockSensor:
        def __init__(self):
            self._readings = {}
        def is_enabled(self):
            return True
        def get_all_readings(self):
            return self._readings
        def set_reading(self, sensor_type, field, value):
            self._readings = {f"{sensor_type}_0": {field: value, "timestamp": time.time()}}
    
    # 模拟执行器模块
    class MockActuator:
        def emergency_stop(self):
            return {"status": "stopped", "message": "紧急停止"}
        def send_command(self, target, action, params=None):
            return {"status": "executed", "target": target, "action": action, "params": params}
    
    mock_sensor = MockSensor()
    mock_actuator = MockActuator()
    reflex.set_sensor_module(mock_sensor)
    reflex.set_actuator_module(mock_actuator)
    
    # 1. 查看预置规则
    rules = reflex.get_rules()
    print(f"1. 预置规则: {len(rules)} 条")
    for r in rules:
        print(f"   [{r['priority']}] {r['rule_id']}: {r['description']} (启用={r['enabled']})")
    
    # 2. 模拟碰撞触发
    print("\n2. 模拟碰撞检测:")
    mock_sensor.set_reading("imu", "acceleration.z", 15.0)
    actions = reflex.scan_and_execute()
    if actions:
        for a in actions:
            print(f"   触发: {a['rule_id']} → {a['action']} → {a['result'].get('message', a['result'])}")
    
    # 3. 冷却期内不重复触发
    print("\n3. 冷却测试（立即再次扫描）:")
    actions2 = reflex.scan_and_execute()
    print(f"   触发数: {len(actions2)} (应为0，冷却中)")
    assert len(actions2) == 0, "冷却期内不应重复触发"
    print("   ✅ 冷却机制正确")
    
    # 4. 统计
    stats = reflex.get_stats()
    print(f"\n4. 统计: 规则{stats['total_rules']}条, 活跃{stats['active_rules']}, 触发{stats['total_triggers']}次")
    
    reflex.disable()
    print("\n=== 自测全部通过 ===")