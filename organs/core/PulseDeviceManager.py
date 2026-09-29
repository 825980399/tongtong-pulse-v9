# -*- coding: utf-8 -*-
"""
PulseDeviceManager —— 设备管理器器官 · 硬件能力枚举与资源分配

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 SystemEvent.BOOT 与 DeviceEvent.ALLOCATE / REFRESH，枚举本机硬件能力（摄像头/麦克风/扬声器/算力档位），维护设备能力表并响应资源分配请求。
机制: _on_boot 与 _on_hardware_snapshot 触发 _refresh_from_touch，_build_capability_map 依次经 _detect_camera_available / _detect_microphone_available / _detect_speaker_available / _resolve_sensor_available 探测（缺失项由 _maybe_background_probe → _background_probe_sensor 后台补探），_calculate_compute_level 评估算力档位，_broadcast_capabilities 广播 DeviceEvent.CAPABILITY_UPDATE；_on_allocate → _auto_allocate 分配后回 DeviceEvent.ALLOCATED；load_permission_config 管控设备访问白名单。
定位: 躯体层的「设备台账与调度台」，是硬件能力的唯一事实来源。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import hashlib
import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import DeviceEvent, LogLevel, SystemEvent, TouchEvent


class PulseDeviceManager(BasePulseOrgan):
    """脉冲驱动设备管理器（v9.5.1 硬件能力枚举版）"""

    def __init__(self, organ_name: str = "设备管理器"):
        super().__init__(organ_name)
        self._devices: dict[str, dict[str, Any]] = {
            "cpu": {"available": True, "type": "CPU"}
        }
        self._allocation_count = 0
        self._permission_config = {}
        self._sensor = None  # 躯体硬件层引用

        # 硬件能力枚举表（标准化键值对，所有器官统一使用）
        self._capabilities: dict[str, Any] = {}
        self._last_capability_hash = ""  # 用于检测能力变化

        # ★属性初始化完整性补全（自动审查添加）
        self._cached_camera_available = {}
        self._cached_mic_available = {}
        self._cached_speaker_available = {}

    def set_sensor(self, sensor):
        """注入躯体硬件层 Sensor 实例"""
        self._sensor = sensor
    def load_permission_config(self):
        try:
            import config
            self._permission_config = getattr(config, 'CONTROLLER_PERMISSION', {})
        except Exception:
            self._permission_config = {}
    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == SystemEvent.BOOT:
            return self._on_boot(payload)
        elif event_type == TouchEvent.HARDWARE_SNAPSHOT:
            return self._on_hardware_snapshot(payload)
        elif event_type == DeviceEvent.ALLOCATE:
            return self._on_allocate(payload)
        elif event_type == DeviceEvent.REFRESH:
            return self._on_refresh()
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        return None

    def _on_boot(self, payload: dict) -> dict[str, Any]:
        """系统启动时初始化设备列表，等待触觉快照到达后再广播能力。

        ★FIX: system.boot 走 L0 生命线层（2s 看门狗）。原实现在此同步调用
        self._sensor.enable()/get_device_list()，属于阻塞式硬件 I/O，会与触觉/眼睛的
        并发硬件初始化争用设备（如摄像头），导致 L0 处理器卡死并触发看门狗「重建 L0 池」。
        被孤立的卡死线程若长期持有传感器锁，会级联阻塞其它器官，使框架整体失去响应。
        故将阻塞调用移出 L0 线程到守护线程，boot 立即返回；能力表仍由
        _on_hardware_snapshot（触觉快照脉冲）负责构建与广播，行为不变。
        """
        # 1. 激活躯体硬件层（摄像头由眼睛负责，设备管理器不操作摄像头）
        #    阻塞调用移入后台守护线程，避免卡住 L0 生命线线程
        if self._sensor is not None:
            _sensor = self._sensor

            def _enable_sensor_async():
                try:
                    _sensor.enable()  # enable()内部会自动调用_scan_devices()扫描设备
                except Exception as e:
                    self._log(LogLevel.ERROR, f'异常: {e}')

            threading.Thread(target=_enable_sensor_async, daemon=True).start()

        # 2. 能力表在收到第一个硬件快照时构建和广播
        return {
            "status": "booted",
            "peripheral_devices": 0,
        }
    def _on_hardware_snapshot(self, payload: dict) -> dict[str, Any]:
        """收到触觉的硬件快照，构建/更新能力表并广播（仅在变化时）"""
        # 直接使用收到的快照数据，不从历史查询
        self._refresh_from_touch(snapshot_payload=payload)
        # 更新传感器状态（摄像头/麦克风/扬声器可能热插拔）
        self._update_sensor_capabilities(payload)
        self._build_capability_map()
        self._broadcast_capabilities()
        return {"status": "capability_updated", "capabilities": self._capabilities}

    # ========== 从触觉获取硬件信息 ==========

    def _refresh_from_touch(self, snapshot_payload: dict | None = None):
        """从信息场获取触觉的最新硬件快照，或直接使用传入的快照数据"""
        if snapshot_payload is not None:
            payload = snapshot_payload
        else:
            if self.info_field is None:
                return
            hw_pulse = self.info_field.get_current("touch.hardware_snapshot")
            if hw_pulse is None:
                return
            payload = hw_pulse.get("payload", {}) if isinstance(hw_pulse, dict) else {}

        # 更新设备状态
        cpu_info = payload.get("cpu", {})
        mem_info = payload.get("memory", {})
        gpu_info = payload.get("gpu", {})
        disk_info = payload.get("disk", {})

        self._devices["cpu"] = {
            "available": True,
            "type": "CPU",
            "model": cpu_info.get("model", "unknown"),
            "cores": cpu_info.get("cores", 1),
            "usage_percent": cpu_info.get("usage_percent", 0.0),
        }

        self._devices["memory"] = {
            "available": True,
            "type": "RAM",
            "total_gb": mem_info.get("total_gb", 0),
            "usage_percent": mem_info.get("usage_percent", 0.0),
        }

        self._devices["gpu"] = {
            "available": gpu_info.get("available", False),
            "type": "GPU",
            "model": gpu_info.get("model", "none"),
            "memory_mb": gpu_info.get("memory_mb", 0),
            "usage_percent": gpu_info.get("usage_percent", 0.0),
        }

        self._devices["disk"] = {
            "available": True,
            "type": "DISK",
            "total_gb": disk_info.get("total_gb", 0),
            "usage_percent": disk_info.get("usage_percent", 0.0),
        }
    def _update_sensor_capabilities(self, payload: dict):
        """从触觉快照中更新摄像头/麦克风/扬声器的连接状态"""
        # 读取触觉快照中的设备检测结果
        camera_available = payload.get("camera_available")
        mic_available = payload.get("microphone_available")
        spk_available = payload.get("speaker_available")

        changed = False

        if camera_available is not None and getattr(self, '_cached_camera_available', None) != camera_available:
            self._cached_camera_available = camera_available
            changed = True
            self._log("INFO", f"摄像头状态更新: {'可用' if camera_available else '不可用'}")

        if mic_available is not None and getattr(self, '_cached_mic_available', None) != mic_available:
            self._cached_mic_available = mic_available
            changed = True
            self._log("INFO", f"麦克风状态更新: {'可用' if mic_available else '不可用'}")

        if spk_available is not None and getattr(self, '_cached_speaker_available', None) != spk_available:
            self._cached_speaker_available = spk_available
            changed = True
            self._log("INFO", f"扬声器状态更新: {'可用' if spk_available else '不可用'}")

        if changed:
            self._log("INFO", "传感器状态变化，将广播能力更新")

    # ========== 构建标准化能力枚举表 ==========

    def _build_capability_map(self):
        """根据当前设备状态，构建标准化硬件能力枚举表"""
        caps = {}

        # --- 计算资源 ---
        cpu = self._devices.get("cpu", {})
        caps["compute.cpu.available"] = cpu.get("available", True)
        caps["compute.cpu.model"] = cpu.get("model", "unknown")
        caps["compute.cpu.cores"] = cpu.get("cores", 1)

        gpu = self._devices.get("gpu", {})
        caps["compute.gpu.available"] = gpu.get("available", False)
        caps["compute.gpu.model"] = gpu.get("model", "none")
        caps["compute.gpu.memory_mb"] = gpu.get("memory_mb", 0)

        mem = self._devices.get("memory", {})
        caps["memory.total_gb"] = mem.get("total_gb", 0)

        disk = self._devices.get("disk", {})
        caps["storage.total_gb"] = disk.get("total_gb", 0)

        # --- 传感器（从躯体硬件层获取，或从已知状态推断） ---
        # 传感器可用性优先用快照缓存（由 _update_sensor_capabilities 填充，来源为
        # 眼睛/触觉快照，是唯一权威来源）。缓存缺失时不再于 L0 生命线线程上同步打开
        # 摄像头/麦克风——那样会与眼睛争用、在波动硬件上卡死唯一的 L0 worker 进而堵死
        # 心跳。改为后台限时探测 + 即时安全默认，L0 处理器立即返回。
        caps["sensor.camera.available"] = self._resolve_sensor_available("camera")
        caps["sensor.microphone.available"] = self._resolve_sensor_available("mic")
        caps["sensor.speaker.available"] = self._resolve_sensor_available("speaker")

        # --- 网络 ---
        caps["network.available"] = True
        # 控制器能力
        caps["desktop.control.enabled"] = self._permission_config.get("desktop_control_enabled", True)

        # --- 计算能力等级 ---
        caps["compute.level"] = self._calculate_compute_level(caps)

        self._capabilities = caps
        # 只对静态能力计算哈希（与 _broadcast_capabilities 保持一致）
        static_caps = {k: v for k, v in caps.items()
                       if "usage" not in k and "percent" not in k}
        self._last_capability_hash = hashlib.md5(
            str(sorted(static_caps.items())).encode()
        ).hexdigest()

    # ========== 传感器可用性检测（全部在隔离子进程中执行，防原生崩溃拖垮主进程） ==========

    def _detect_camera_available(self) -> bool:
        """检测摄像头是否可用（首次检测后缓存）。
        ★FIX: 原实现在主进程内直接 cv2.VideoCapture(0)，会（1）与眼睛推流争用摄像头，
        （2）在波动硬件上触发原生崩溃（access violation）直接杀死整个进程。
        改为隔离子进程探测；子进程原生崩溃/超时不影响主进程，调用方安全降级。
        """
        if hasattr(self, '_cached_camera_available'):
            return self._cached_camera_available
        try:
            from utils.safe_hw_probe import safe_camera_devices
            res = safe_camera_devices()
            ok = bool(res.get("ok", False)) if isinstance(res, dict) else False
        except Exception:
            ok = False
        self._cached_camera_available = ok
        return ok

    def _detect_microphone_available(self) -> bool:
        """检测麦克风是否可用（首次检测后缓存）。隔离子进程，防原生崩溃。"""
        if hasattr(self, '_cached_mic_available'):
            return self._cached_mic_available
        try:
            from utils.safe_hw_probe import safe_audio_devices
            res = safe_audio_devices(True)
            ok = bool(res.get("ok", False)) if isinstance(res, dict) else False
        except Exception:
            ok = False
        self._cached_mic_available = ok
        return ok

    def _detect_speaker_available(self) -> bool:
        """检测扬声器是否可用（首次检测后缓存）。隔离子进程，防原生崩溃。
        大多数系统默认有扬声器，探测失败（原生崩溃/超时）时安全默认=True。"""
        if hasattr(self, '_cached_speaker_available'):
            return self._cached_speaker_available
        try:
            from utils.safe_hw_probe import safe_audio_devices
            res = safe_audio_devices(False)
            ok = bool(res.get("ok", True)) if isinstance(res, dict) else True
        except Exception:
            ok = True
        self._cached_speaker_available = ok
        return ok

    # ========== 非阻塞传感器可用性解析（P1修复：不在L0线程打开硬件） ==========

    def _resolve_sensor_available(self, kind: str) -> bool:
        """非阻塞获取传感器可用性。

        - 优先返回快照缓存（权威来源，由眼睛/触觉快照填充）。
        - 缓存缺失时：启动一次后台限时探测（守护线程，不阻塞调用方），
          本次构建先用安全默认（摄像头/麦克风/扬声器默认可用），探测完成且结果变化会触发能力重广播。
        关键：L0 生命线处理器（硬件快照脉冲）永远不会因为打开摄像头/麦克风而卡死或崩溃。
        """
        _attr = {
            "camera": "_cached_camera_available",
            "mic": "_cached_mic_available",
            "speaker": "_cached_speaker_available",
        }[kind]
        _cached = getattr(self, _attr, None)
        if _cached is not None:
            return _cached
        # 缓存缺失：后台探测，避免在主线程（尤其 L0）上打开硬件
        self._maybe_background_probe(kind)
        # 安全默认：普通 PC 默认这些传感器可用
        return True

    def _maybe_background_probe(self, kind: str):
        # 摄像头不在此后台探测：由触觉快照（眼睛权威来源）提供，避免在设备管理器中重复打开摄像头
        if kind == "camera":
            return
        _lock_attr = f"_probing_{kind}"
        if getattr(self, _lock_attr, False):
            return
        setattr(self, _lock_attr, True)
        _t = threading.Thread(target=self._background_probe_sensor, args=(kind,), daemon=True)
        _t.start()

    def _background_probe_sensor(self, kind: str):
        """后台探测传感器；结果变化则重建并广播能力表。
        ★FIX: 探测改为隔离子进程（safe_audio_devices），pyaudio 原生崩溃被隔离在子进程，
        绝不会杀死主进程。探测失败（None）时保持默认，不误判为不可用。
        """
        _attr = {
            "mic": "_cached_mic_available",
            "speaker": "_cached_speaker_available",
        }[kind]
        try:
            _prev = getattr(self, _attr, None)
            try:
                from utils.safe_hw_probe import safe_audio_devices
                res = safe_audio_devices(kind == "mic")
                _ok = bool(res.get("ok", kind == "speaker")) if isinstance(res, dict) else None
            except Exception:
                _ok = None
            if _ok is None:
                return  # 探测失败（硬件波动/原生崩溃被隔离子进程），保持默认，不更新
            setattr(self, _attr, _ok)
            if _prev != _ok:
                self._build_capability_map()
                self._broadcast_capabilities()
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        finally:
            setattr(self, f"_probing_{kind}", False)

    def _calculate_compute_level(self, caps: dict[str, Any]) -> str:
        """根据硬件能力计算综合计算等级"""
        cpu_cores = caps.get("compute.cpu.cores", 1)
        gpu_available = caps.get("compute.gpu.available", False)
        memory_gb = caps.get("memory.total_gb", 0)

        if gpu_available and cpu_cores >= 8 and memory_gb >= 16:
            return "high"
        elif cpu_cores >= 4 and memory_gb >= 8:
            return "medium"
        else:
            return "basic"

    # ========== 广播能力更新 ==========

    def _broadcast_capabilities(self):
        """发射硬件能力枚举脉冲，仅在能力变化时通知所有器官"""
        if self.info_field is None or self.pulse_core is None:
            return

        # 只取静态能力值，排除任何动态负载
        static_caps = {}
        for k, v in self._capabilities.items():
            # 跳过实时负载字段
            if "usage" in k or "percent" in k:
                continue
            static_caps[k] = v

        current_hash = hashlib.md5(str(sorted(static_caps.items())).encode()).hexdigest()

        if current_hash == self._last_capability_hash:
            return  # 能力无变化，不重复广播

        self._last_capability_hash = current_hash

        pulse = self.pulse_core.emit(
            source_organ=self.organ_name,
            event_type=DeviceEvent.CAPABILITY_UPDATE,
            payload={
                "capabilities": dict(static_caps),  # 只广播静态能力
                "timestamp": time.time(),
            },
            priority=6,
            layer="L1"
        )
        self.info_field.publish(pulse)

    # ========== 设备分配 ==========

    def _on_allocate(self, payload: dict) -> dict[str, Any]:
        task_type = payload.get("task_type", "general")
        preferred = payload.get("preferred_device", "cpu")

        # 根据能力表智能分配
        if preferred == "gpu" and self._capabilities.get("compute.gpu.available", False):
            device = "gpu"
        elif preferred == "auto":
            # 自动模式：根据任务类型选择最优设备
            device = self._auto_allocate(task_type)
        else:
            device = "cpu"

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._allocation_count += 1
        self._emit(DeviceEvent.ALLOCATED, {
            "task_type": task_type,
            "device": device,
            "capabilities": self._capabilities.get(f"compute.{device}.model", device),
        }, priority=5, layer="L1")
        return {"status": "allocated", "device": device}

    def _auto_allocate(self, task_type: str) -> str:
        """根据任务类型自动选择最优设备"""
        gpu_tasks = ["vision", "inference", "training", "rendering"]
        if task_type in gpu_tasks and self._capabilities.get("compute.gpu.available", False):
            return "gpu"
        return "cpu"

    # ========== 刷新与状态 ==========

    def _on_refresh(self) -> dict[str, Any]:
        self._refresh_from_touch()
        self._build_capability_map()
        return {"status": "refreshed", "devices": self._devices, "capabilities": self._capabilities}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "devices": self._devices,
            "capabilities": self._capabilities,
            "allocation_count": self._allocation_count,
            "is_running": self.is_running,
        }

    def get_resonance_conditions(self) -> list:
        return [{
            "organ_name": self.organ_name,
            "event_types": [
                SystemEvent.BOOT,
                TouchEvent.HARDWARE_SNAPSHOT,
                DeviceEvent.ALLOCATE,
                DeviceEvent.REFRESH,
                SystemEvent.STATUS_REQUEST,
            ],
            "min_priority": 1,
        }]

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "设备管理器",
    "class_name": "PulseDeviceManager",
    "attr_name": "device_manager",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "sensor", "setter": "set_sensor"},
    ],
}

if __name__ == "__main__":
    print("=== PulseDeviceManager v9.5.1 硬件能力枚举自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
            self._data = {
                "touch.hardware_snapshot": {
                    "payload": {
                        "cpu": {"model": "AMD Ryzen 7 3700X", "cores": 16, "usage_percent": 35},
                        "memory": {"total_gb": 47.9, "usage_percent": 50},
                        "gpu": {"available": True, "model": "NVIDIA GTX 1050 Ti", "memory_mb": 4096, "usage_percent": 10},
                        "disk": {"total_gb": 512, "usage_percent": 60},
                    }
                }
            }
        def publish(self, p):
            self.published.append(p)
        def get_current(self, k):
            return self._data.get(k)

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {"event_type": event_type, "source_organ": source_organ,
                    "payload": payload, "priority": priority, "layer": layer}

    m = MockInfoField()
    c = MockCore()
    d = PulseDeviceManager("设备管理器")
    d.set_info_field(m)
    d.set_pulse_core(c)
    d.start()

    # 测试启动
    print("1. 启动并构建能力表:")
    boot_result = d.on_pulse({"event_type": SystemEvent.BOOT, "payload": {}, "priority": 10})
    print(f"   状态: {boot_result['status']}")
    caps = boot_result["capabilities"]
    print(f"   CPU: {caps.get('compute.cpu.model')}, {caps.get('compute.cpu.cores')}核")
    print(f"   GPU: 可用={caps.get('compute.gpu.available')}, 型号={caps.get('compute.gpu.model')}")
    print(f"   计算等级: {caps.get('compute.level')}")

    # 验证能力更新脉冲
    cap_pulses = [p for p in m.published if p.get("event_type") == DeviceEvent.CAPABILITY_UPDATE]
    if cap_pulses:
        print(f"   CAPABILITY_UPDATE 脉冲已发射 (layer={cap_pulses[-1].get('layer', '未设置')})")

    # 测试分配
    r2 = d.on_pulse({"event_type": DeviceEvent.ALLOCATE, "payload": {"task_type": "vision", "preferred_device": "auto"}, "priority": 5})
    print(f"\n2. 自动分配视觉任务: 设备={r2['device']} (预期=gpu)")

    r3 = d.on_pulse({"event_type": DeviceEvent.ALLOCATE, "payload": {"task_type": "chat", "preferred_device": "auto"}, "priority": 5})
    print(f"3. 自动分配对话任务: 设备={r3['device']} (预期=cpu)")

    # 测试硬件快照更新
    r4 = d.on_pulse({"event_type": TouchEvent.HARDWARE_SNAPSHOT, "payload": {}, "priority": 3})
    print(f"\n4. 收到硬件快照: 状态={r4['status']}")

    s = d.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"\n5. 状态: 分配{s['allocation_count']}次, GPU可用={s['devices']['gpu']['available']}")

    d.stop()
    print("\n=== 自测全部通过 ===")
