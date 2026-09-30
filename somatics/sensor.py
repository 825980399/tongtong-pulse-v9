"""sensor —— 物理传感器抽象层（硬件抽象层 · 感知输入的物理基础）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import threading
import time
from typing import Any
from nucleus._silent_except import silent_exc


class Sensor:
    """
    物理传感器抽象层
    
    工作原理:
        1. 系统启动时，自动检测已连接的传感器设备（摄像头、麦克风等）。
        2. 为每个传感器创建设备实例，提供统一的 `read()` 接口。
        3. 定期采集所有传感器读数，聚合为标准化快照供上层使用。
        4. 监听设备热插拔事件，动态更新可用传感器列表。
    
    当前状态（v9.0）:
        - 接口完整定义，功能开关默认关闭。
        - 在桌面PC环境下，传感器列表可能为空或仅有基础设备。
        - 接入机器人平台后自动激活完整驱动。
    """
    
    # 支持的传感器类型及其标准脉冲事件
    SENSOR_TYPES = {
        "camera": "sensor.camera.frame",
        "microphone": "sensor.microphone.audio_chunk",
        "temperature": "sensor.touch.temperature",
        "pressure": "sensor.touch.pressure",
        "imu": "sensor.imu.orientation",
        "lidar": "sensor.lidar.scan",
        "gps": "sensor.gps.position",
        "bio_heart_rate": "sensor.bio.heart_rate",
    }
    
    def __init__(self):
        # 已连接的传感器: device_id → {type, driver, status, last_reading}
        self._devices: dict[str, dict[str, Any]] = {}
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 最近一次全量采集的快照
        self._latest_readings: dict[str, Any] = {}
        
        # 统计
        self._total_reads = 0
        
        # 功能开关
        self._enabled = False

    # ========== 框架控制 ==========

    def enable(self):
        """激活传感器模块"""
        self._enabled = True
        self._scan_devices()

    def disable(self):
        self._enabled = False

    def is_enabled(self) -> bool:
        return self._enabled

    # ========== 设备发现 ==========

    def _scan_devices(self):
        """扫描系统中已连接的传感器设备（当前为模拟，未来对接真实驱动）"""
        # v9.0 桌面PC环境下的基础扫描
        self._detect_camera()
        self._detect_audio()
    def _detect_camera(self):
        """检测摄像头设备（隔离子进程，防原生崩溃）"""
        try:
            from utils.safe_hw_probe import safe_camera_devices
            res = safe_camera_devices()
        except Exception as e:
            silent_exc(e, where="somatics.sensor::_detect_camera L82")
            return
        if not isinstance(res, dict):
            return
        for d in res.get("devices", []):
            i = d.get("index", 0)
            device_id = f"camera_{i}"
            self._devices[device_id] = {
                "type": "camera",
                "driver": "opencv_uvc",
                "status": "connected",
                "last_reading": None,
                "properties": {"index": i},
            }
    def _detect_audio(self):
        """检测音频输入设备（麦克风，隔离子进程，防原生崩溃）"""
        try:
            from utils.safe_hw_probe import safe_audio_devices
            res = safe_audio_devices(True)
        except Exception as e:
            silent_exc(e, where="somatics.sensor::_detect_audio L101")
            return
        if not isinstance(res, dict):
            return
        for d in res.get("devices", []):
            i = d.get("index", 0)
            self._devices[f"microphone_{i}"] = {
                "type": "microphone",
                "driver": "pyaudio",
                "status": "connected",
                "last_reading": None,
                "properties": {
                    "name": d.get("name", "unknown"),
                    "sample_rate": d.get("sample_rate", 16000),
                    "channels": d.get("channels", 1),
                },
            }
    # ========== 统一读取接口 ==========

    def read(self, device_id: str) -> dict[str, Any] | None:
        """
        从指定传感器读取一次数据。

        Args:
            device_id: 设备标识

        Returns:
            传感器读数，设备不存在或读取失败返回 None
        """
        if not self._enabled:
            return None

        device = self._devices.get(device_id)
        if not device:
            return None

        # 根据传感器类型调用对应的读取方法
        sensor_type = device["type"]
        reading = None

        if sensor_type == "camera":
            reading = self._read_camera(device)
        elif sensor_type == "microphone":
            reading = self._read_microphone(device)
        else:
            reading = self._read_generic(device)

        if reading:
            with self._lock:
                device["last_reading"] = reading
                self._latest_readings[device_id] = reading
                self._total_reads += 1

        return reading

    def _read_camera(self, device: dict) -> dict[str, Any] | None:
        """摄像头设备状态监测（不打开摄像头，避免与眼睛器官争用）。

        摄像头的打开/读取/重连完全由眼睛器官(PulseEyes)负责。
        躯体传感器只报告设备存在性，不实际访问摄像头硬件。
        """
        index = device["properties"].get("index", 0)
        return {
            "device_id": f"camera_{index}",
            "device_type": "camera",
            "status": "managed_by_eyes",  # 由眼睛器官统一管理
            "note": "摄像头读取由PulseEyes负责，躯体传感器仅做设备存在性监测",
            "timestamp": time.time(),
        }

    def _read_microphone(self, device: dict) -> dict[str, Any] | None:
        """读取麦克风音频块（预留）"""
        return {
            "device_id": device["properties"].get("name", "unknown"),
            "sample_rate": device["properties"].get("sample_rate", 16000),
            "format": "pcm16",
            "timestamp": time.time(),
            "note": "音频读取为v10.0预留功能",
        }

    def _read_generic(self, device: dict) -> dict[str, Any] | None:
        """通用传感器读取（预留）"""
        return {
            "device_id": device.get("type", "unknown"),
            "timestamp": time.time(),
            "note": f"传感器类型'{device['type']}'的驱动待实现",
        }

    # ========== 全量采集 ==========
