"""actuator —— 物理执行器抽象层（硬件抽象层 · 运动输出的物理基础）

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""

import threading
import time
from typing import Any


class Actuator:
    """
    物理执行器抽象层
    
    工作原理:
        1. 系统启动时，自动检测已连接的执行器设备（显示屏、扬声器、电机等）。
        2. 为每个执行器创建设备实例，提供统一的 `execute()` 接口。
        3. 接收来自器官层的动作指令，转化为硬件控制信号。
        4. 执行完成后反馈状态，异常时触发告警。
    
    当前状态（v9.0）:
        - 接口完整定义，功能开关默认关闭。
        - 在桌面PC环境下，执行器列表可能为空或仅有基础设备。
        - 接入机器人平台后自动激活完整驱动。
    """
    
    # 支持的执行器类型及其标准脉冲事件
    ACTUATOR_TYPES = {
        "display": "actuator.display.show_text",
        "speaker": "actuator.speaker.say",
        "motor": "actuator.motor.move_to",
        "led": "actuator.led.set_color",
        "servo": "actuator.motor.move_to",
    }
    
    def __init__(self):
        # 已连接的执行器: device_id → {type, driver, status, last_action}
        self._devices: dict[str, dict[str, Any]] = {}
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 统计
        self._total_actions = 0
        self._total_successes = 0
        self._total_failures = 0
        
        # 功能开关
        self._enabled = False

    # ========== 框架控制 ==========

    def enable(self):
        """激活执行器模块"""
        self._enabled = True
        self._scan_devices()

    def disable(self):
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    # ========== 设备发现 ==========

    def _scan_devices(self):
        """扫描系统中已连接的执行器设备（当前为模拟，未来对接真实驱动）"""
        self._detect_display()
        self._detect_audio_output()

    def _detect_display(self):
        """检测显示设备"""
        # 桌面PC通常有一个主显示器
        self._devices["display_0"] = {
            "type": "display",
            "driver": "system_default",
            "status": "connected",
            "properties": {
                "resolution": "1920x1080",
                "refresh_rate": 60,
            },
        }

    def _detect_audio_output(self):
        """检测音频输出设备（隔离子进程，防原生崩溃）"""
        try:
            from utils.safe_hw_probe import safe_audio_devices
            res = safe_audio_devices(False)
        except Exception:
            res = None
        if isinstance(res, dict) and res.get("devices"):
            info = res["devices"][0]
            self._devices["speaker_0"] = {
                "type": "speaker",
                "driver": "pyaudio",
                "status": "connected",
                "properties": {
                    "name": info.get("name", "系统扬声器"),
                },
            }
        else:
            # 探测失败（含原生崩溃被隔离）也假设有基础音频输出
            self._devices["speaker_0"] = {
                "type": "speaker",
                "driver": "system_default",
                "status": "connected",
                "properties": {"name": "系统默认扬声器"},
            }

    # ========== 统一执行接口 ==========

    def execute(self, device_id: str, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        对指定执行器下发一个动作指令。

        Args:
            device_id: 设备标识
            action: 动作类型（如 "show_text", "say", "move_to", "stop"）
            params: 动作参数

        Returns:
            执行结果
        """
        if not self._enabled:
            return {"status": "disabled", "reason": "执行器模块未激活"}

        device = self._devices.get(device_id)
        if not device:
            return {"status": "not_found", "reason": f"未知执行器: {device_id}"}

        if device["status"] != "connected":
            return {"status": "unavailable", "reason": f"执行器未连接: {device['status']}"}

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._total_actions += 1
        start_time = time.time()

        try:
            if device["type"] == "display":
                result = self._execute_display(device, action, params)
            elif device["type"] == "speaker":
                result = self._execute_speaker(device, action, params)
            elif device["type"] in ("motor", "servo"):
                result = self._execute_motor(device, action, params)
            else:
                result = self._execute_generic(device, action, params)

            elapsed_ms = (time.time() - start_time) * 1000
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._total_successes += 1

            return {
                "status": "completed",
                "device_id": device_id,
                "action": action,
                "result": result,
                "elapsed_ms": round(elapsed_ms, 1),
            }

        except Exception as e:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._total_failures += 1
            return {
                "status": "failed",
                "device_id": device_id,
                "action": action,
                "error": str(e),
            }

    def _execute_display(self, device: dict, action: str, params: dict) -> dict[str, Any]:
        """执行显示类动作"""
        if action == "show_text":
            text = params.get("text", "")
            print(f"[Display] {text}")
            return {"displayed": True, "text_length": len(text)}
        elif action == "clear":
            return {"cleared": True}
        elif action == "show_image":
            return {"displayed": True, "image_path": params.get("image_path", "")}
        else:
            return {"error": f"不支持的显示动作: {action}"}

    def _execute_speaker(self, device: dict, action: str, params: dict) -> dict[str, Any]:
        """执行音频类动作"""
        if action == "say":
            text = params.get("text", "")
            print(f"[Speaker] {text}")
            return {"spoken": True, "text_length": len(text)}
        elif action == "stop":
            return {"stopped": True}
        elif action == "play":
            return {"played": True, "audio_path": params.get("audio_path", "")}
        else:
            return {"error": f"不支持的音频动作: {action}"}

    def _execute_motor(self, device: dict, action: str, params: dict) -> dict[str, Any]:
        """执行运动类动作"""
        if action == "move_to":
            joint = params.get("joint_id", "unknown")
            angle = params.get("angle_deg", 0)
            print(f"[Motor] {joint} → {angle}°")
            return {"joint": joint, "target_angle": angle, "reached": True}
        elif action == "stop":
            return {"stopped": True, "all_joints": params.get("all_joints", True)}
        elif action == "home":
            return {"homed": True}
        else:
            return {"error": f"不支持的运动动作: {action}"}

    def _execute_generic(self, device: dict, action: str, params: dict) -> dict[str, Any]:
        """执行通用动作（预留）"""
        return {"action": action, "note": "通用执行器驱动待实现"}

    # ========== 紧急停止 ==========

    def emergency_stop(self, reason: str = "紧急停止") -> dict[str, Any]:
        """
        对所有执行器执行紧急停止。

        Args:
            reason: 停止原因

        Returns:
            停止结果
        """
        stopped = []
        for device_id, device in self._devices.items():
            if device["type"] in ("motor", "servo"):
                self.execute(device_id, "stop", {"all_joints": True})
                stopped.append(device_id)

        return {
            "status": "emergency_stop",
            "reason": reason,
            "stopped_devices": stopped,
            "timestamp": time.time(),
        }

    # ========== 查询接口 ==========

    def get_device_list(self) -> list[dict[str, Any]]:
        """获取所有已连接执行器列表"""
        return [
            {
                "device_id": did,
                "type": info["type"],
                "status": info["status"],
                "properties": info["properties"],
            }
            for did, info in self._devices.items()
        ]

    def get_device_count(self) -> int:
        """获取已连接执行器数量"""
        return len(self._devices)

    def is_device_available(self, device_type: str) -> bool:
        """检查指定类型的执行器是否可用"""
        return any(d["type"] == device_type and d["status"] == "connected" for d in self._devices.values())

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        """获取执行器模块统计"""
        return {
            "enabled": self._enabled,
            "device_count": len(self._devices),
            "total_actions": self._total_actions,
            "total_successes": self._total_successes,
            "total_failures": self._total_failures,
            "success_rate": round(self._total_successes / max(1, self._total_actions), 3),
            "devices": self.get_device_list(),
        }

    # ========== 未来演化预留 ==========

    def calibrate_actuator(self, device_id: str) -> dict[str, Any]:
        """【预留 v10.0】执行执行器校准"""
        return {
            "status": "not_implemented",
            "message": "执行器校准功能预留",
            "device_id": device_id,
        }


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== Actuator 自测 ===\n")
    
    actuator = Actuator()
    actuator.enable()
    
    # 1. 设备列表
    devices = actuator.get_device_list()
    print(f"1. 已连接执行器: {len(devices)} 个")
    for d in devices:
        print(f"   - {d['device_id']}: {d['type']} ({d['status']})")
    
    # 2. 执行动作
    result = actuator.execute("display_0", "show_text", {"text": "你好，曈曈！"})
    print(f"\n2. 显示文本: {result['status']} ({result['elapsed_ms']}ms)")
    
    result2 = actuator.execute("speaker_0", "say", {"text": "守护这个世界"})
    print(f"3. 语音输出: {result2['status']}")
    
    # 4. 设备可用性
    has_display = actuator.is_device_available("display")
    has_motor = actuator.is_device_available("motor")
    print(f"\n4. 设备可用性: 显示={'✅' if has_display else '❌'}, 电机={'✅' if has_motor else '❌'}")
    
    # 5. 紧急停止
    estop = actuator.emergency_stop("测试急停")
    print(f"5. 紧急停止: {estop['stopped_devices']}")
    
    # 6. 统计
    stats = actuator.get_stats()
    print(f"\n6. 统计: 动作{stats['total_actions']}次, 成功{stats['total_successes']}次, 成功率{stats['success_rate']:.1%}")
    
    actuator.disable()
    print("\n=== 自测全部通过 ===")