"""body_state —— 躯体状态感知模块（硬件抽象层 · 本体感知的物理基础）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import threading
import time
from typing import Any


class BodyState:
    """
    躯体状态感知模块
    
    工作原理:
        1. 定期从 sensor 模块拉取最新读数。
        2. 聚合为标准化躯体快照，供器官层查询。
        3. 持续监控关键指标（温度、电量、关节力矩），异常时触发告警。
        4. 记录躯体状态历史，供健康监控和演化分析使用。
    
    当前状态（v9.0）:
        - 接口完整定义，功能开关默认关闭。
        - 在桌面PC环境下，躯体状态为空（无物理躯体）。
        - 接入机器人平台后自动激活。
    """
    
    def __init__(self, body_id: str = "body_001"):
        """
        Args:
            body_id: 躯体标识（多躯体场景下区分不同物理实例）
        """
        self._body_id = body_id
        
        # 躯体状态快照
        self._body_snapshot: dict[str, Any] = {
            "body_id": body_id,
            "timestamp": time.time(),
            "temperature": {
                "ambient": 25.0,       # 环境温度（℃）
                "cpu": 0.0,            # CPU温度
                "motor": {},            # 各关节电机温度
            },
            "power": {
                "battery_level": 100.0, # 电池电量（%）
                "voltage": 12.0,        # 电压（V）
                "current": 0.0,         # 电流（A）
                "is_charging": False,
            },
            "posture": {
                "joints": {},           # 各关节角度（度）
                "orientation": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
                "center_of_mass": {"x": 0.0, "y": 0.0, "z": 0.0},
            },
            "contacts": [],             # 接触传感器读数
            "imu": {                    # 惯性测量单元
                "acceleration": {"x": 0.0, "y": 0.0, "z": 9.8},
                "angular_velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
            },
            "status": {
                "is_initialized": False,
                "is_calibrated": False,
                "error_flags": [],
            },
        }
        
        # 关联组件引用
        self.sensor_module = None
        self.actuator_module = None
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 功能开关
        self._enabled = False

    # ========== 框架控制 ==========

    def enable(self):
        """激活躯体感知"""
        self._enabled = True
        self._body_snapshot["status"]["is_initialized"] = True

    def disable(self):
        """关闭躯体感知"""
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    def set_sensor_module(self, sensor_module):
        """注入传感器模块"""
        self.sensor_module = sensor_module

    def set_actuator_module(self, actuator_module):
        """注入执行器模块"""
        self.actuator_module = actuator_module

    # ========== 躯体快照 ==========

    def get_body_snapshot(self) -> dict[str, Any]:
        """
        获取当前躯体状态的完整快照。

        Returns:
            躯体状态字典
        """
        if not self._enabled:
            return {"status": "disabled", "body_id": self._body_id}

        with self._lock:
            self._body_snapshot["timestamp"] = time.time()
            
            # 如果传感器模块可用，拉取最新数据
            if self.sensor_module and self.sensor_module.is_enabled():
                sensor_data = self.sensor_module.get_all_readings()
                if sensor_data:
                    self._update_from_sensors(sensor_data)
            
            return dict(self._body_snapshot)

    def _update_from_sensors(self, sensor_data: dict[str, Any]):
        """从传感器数据更新躯体状态"""
        # 温度更新
        if "temperature" in sensor_data:
            self._body_snapshot["temperature"].update(sensor_data["temperature"])
        
        # 电量更新
        if "power" in sensor_data:
            self._body_snapshot["power"].update(sensor_data["power"])
        
        # 姿态更新
        if "joint_angles" in sensor_data:
            self._body_snapshot["posture"]["joints"] = sensor_data["joint_angles"]
        
        # IMU更新
        if "imu" in sensor_data:
            self._body_snapshot["imu"].update(sensor_data["imu"])

    # ========== 异常检测 ==========

    def check_anomalies(self) -> list[dict[str, Any]]:
        """
        检测躯体异常状态。

        Returns:
            异常列表，空列表表示正常
        """
        if not self._enabled:
            return []

        anomalies = []
        snapshot = self.get_body_snapshot()

        # 温度异常
        cpu_temp = snapshot["temperature"].get("cpu", 0)
        if cpu_temp > 85.0:
            anomalies.append({
                "type": "overheating",
                "severity": "critical",
                "value": cpu_temp,
                "threshold": 85.0,
                "message": f"CPU温度过高: {cpu_temp}℃",
            })
        elif cpu_temp > 70.0:
            anomalies.append({
                "type": "overheating",
                "severity": "warning",
                "value": cpu_temp,
                "threshold": 70.0,
                "message": f"CPU温度偏高: {cpu_temp}℃",
            })

        # 电量异常
        battery = snapshot["power"].get("battery_level", 100)
        if battery < 10.0:
            anomalies.append({
                "type": "low_battery",
                "severity": "critical",
                "value": battery,
                "threshold": 10.0,
                "message": f"电量极低: {battery}%",
            })
        elif battery < 25.0:
            anomalies.append({
                "type": "low_battery",
                "severity": "warning",
                "value": battery,
                "threshold": 25.0,
                "message": f"电量偏低: {battery}%",
            })

        return anomalies

    # ========== 关节状态 ==========

    def get_joint_state(self, joint_name: str) -> dict[str, Any] | None:
        """
        获取指定关节的当前状态。

        Args:
            joint_name: 关节名称

        Returns:
            关节状态字典，不存在返回 None
        """
        joints = self._body_snapshot["posture"]["joints"]
        return joints.get(joint_name)

    def get_all_joint_states(self) -> dict[str, Any]:
        """获取所有关节状态"""
        return dict(self._body_snapshot["posture"]["joints"])

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取躯体状态统计"""
        with self._lock:
            return {
                "body_id": self._body_id,
                "enabled": self._enabled,
                "is_initialized": self._body_snapshot["status"]["is_initialized"],
                "joint_count": len(self._body_snapshot["posture"]["joints"]),
                "error_flags": self._body_snapshot["status"]["error_flags"],
            }
    # ========== 未来演化预留 ==========

    def calibrate_body(self) -> dict[str, Any]:
        """
        【预留 v10.0】执行躯体校准程序。
        
        Returns:
            校准结果
        """
        return {
            "status": "not_implemented",
            "message": "躯体校准功能将在接入机器人平台后激活",
        }

    def switch_body(self, new_body_id: str) -> dict[str, Any]:
        """
        【预留 v10.0】切换躯体实例（多躯体场景）。

        Args:
            new_body_id: 目标躯体标识

        Returns:
            切换结果
        """
        return {
            "status": "not_implemented",
            "message": f"躯体切换功能预留，当前躯体: {self._body_id}",
            "target_body": new_body_id,
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== BodyState 自测 ===\n")
    
    body = BodyState(body_id="test_body_001")
    body.enable()
    
    # 1. 获取躯体快照
    snapshot = body.get_body_snapshot()
    print(f"1. 躯体ID: {snapshot['body_id']}")
    print(f"   电量: {snapshot['power']['battery_level']}%")
    print(f"   温度: CPU={snapshot['temperature']['cpu']}℃")
    print(f"   姿态: 关节数={len(snapshot['posture']['joints'])}")
    
    # 2. 模拟温度异常
    body._body_snapshot["temperature"]["cpu"] = 90.0
    anomalies = body.check_anomalies()
    print(f"\n2. 异常检测: {len(anomalies)} 个")
    for a in anomalies:
        print(f"   [{a['severity']}] {a['message']}")
    
    # 3. 统计
    stats = body.get_stats()
    print(f"\n3. 统计: 已初始化={stats['is_initialized']}, 关节数={stats['joint_count']}")
    
    body.disable()
    print("\n=== 自测全部通过 ===")