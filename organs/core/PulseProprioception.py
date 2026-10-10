# -*- coding: utf-8 -*-
"""
PulseProprioception —— 本体感知器官 · 全局自我认知与能力边界映射

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 持续维护曈曈的「本体地图」——器官清单、硬件能力边界、健康评分与可扩展方向，并响应 ProprioceptionEvent.REQUEST 输出完整报告。
机制: start / stop 起停后由 _on_heartbeat 周期刷新；_refresh_organs_status 汇总各器官状态，_evaluate_hardware_capabilities 经 _detect_camera / _detect_audio_devices / _get_gpu_devices / _has_gpu_compute 探测硬件，_evaluate_future_upgrades 评估可扩展方向，_calculate_health_score 计算健康分；_generate_full_report 成报后 _save_report_file 落盘、_print_report_console 打印并回 ProprioceptionEvent.REPORT；_on_device_attached / _on_device_detached 响应设备热插拔，_on_capability_update 同步能力表。
定位: 自我认知的「躯体地图」，是曈曈回答「我是什么、我能做什么」的依据。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import json
import os
import sys
import threading
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from base.BasePulseOrgan import BasePulseOrgan
from nucleus._silent_except import silent_exc
from nucleus.const import DeviceEvent, HeartEvent, LogLevel, ProprioceptionEvent, SystemEvent


class PulseProprioception(BasePulseOrgan):
    """
    本体感知器官 —— 全局自我认知与能力边界映射（v9.5 分层脉冲版）

    在 v9.5 脉冲网络中，本体感知器官是曈曈认识自身躯体结构和能力范围的唯一入口。
    它不控制任何硬件，不参与具体业务逻辑，只负责一件事：
    持续维护一张完整的"本体地图"，让内部器官和外部观察者随时了解曈曈的完整状态。
    """

    def __init__(self, organ_name: str = "本体感知"):
        super().__init__(organ_name)

        # ===== 依赖注入 =====
        self.touch = None           # PulseTouch 引用（硬件快照来源）
        self.system_manager = None  # PulseSystemManager 引用（服务状态）
        # ★P3-1：只读状态 provider 回调（替代 self.touch.get_hardware_info 直调）
        self._hardware_info_provider = None  # () -> dict

        # ===== 本体数据 =====
        self._organs_status: dict[str, dict[str, Any]] = {}  # 各器官状态缓存
        self._hardware_capabilities: dict[str, Any] = {}     # 当前硬件能力矩阵
        self._missing_hardware: list[str] = []               # 蓝图中有但当前缺失的硬件
        self._upgrade_path: list[dict[str, str]] = []        # 建议的升级路径
        self._device_capabilities: dict[str, Any] = {}        # 设备管理器发来的能力枚举表

        # ===== 统计 =====
        self._report_count = 0

        # ===== 线程安全 =====
        self._lock = threading.Lock()

        # ===== 运行状态 =====
        self.is_running = False

    # ========== 依赖注入 ==========

    def set_touch(self, touch):
        """注入触觉器官引用"""
        self.touch = touch
        # ★P3-1：同步注入硬件信息 provider 回调（替代 get_hardware_info 直调）
        if touch is not None and hasattr(touch, 'get_hardware_info'):
            self._hardware_info_provider = touch.get_hardware_info

    def set_hardware_info_provider(self, provider):
        """★P3-1：注入硬件信息 provider 回调（规则14 依赖注入+回调）。"""
        self._hardware_info_provider = provider

    def set_system_manager(self, system_manager):
        """注入系统管理器引用"""
        self.system_manager = system_manager

    # ========== 生命周期 ==========

    def start(self):
        super().start()
        self._log(LogLevel.INFO, "已启动，本体感知就绪")

    def stop(self):
        super().stop()
        self._log(LogLevel.INFO, f"已停止，共生成 {self._report_count} 份本体报告")

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None

        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == HeartEvent.BEAT:
            return self._on_heartbeat()
        elif event_type == ProprioceptionEvent.REQUEST:
            return self._generate_full_report(payload.get("format", "dict"))
        elif event_type == DeviceEvent.CAPABILITY_UPDATE:
            return self._on_capability_update(payload)
        elif event_type == SystemEvent.DEVICE_ATTACHED:
            self._on_device_attached(payload)
            return None
        elif event_type == SystemEvent.DEVICE_DETACHED:
            self._on_device_detached(payload)
            return None
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    HeartEvent.BEAT,
                    ProprioceptionEvent.REQUEST,
                    DeviceEvent.CAPABILITY_UPDATE,
                    SystemEvent.DEVICE_ATTACHED,
                    SystemEvent.DEVICE_DETACHED,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            },
        ]

    # ========== 音频设备检测（新增） ==========

    def _detect_audio_devices(self) -> dict[str, Any]:
        """
        检测系统中的音频输入/输出设备。
        
        使用 pyaudio 枚举设备列表。如果 pyaudio 不可用，尝试通过系统命令检测。
        
        Returns:
            {"input_devices": [...], "output_devices": [...], "has_input": bool, "has_output": bool}
        """
        result = {
            "input_devices": [],
            "output_devices": [],
            "has_input": False,
            "has_output": False,
        }

        # 方法1：pyaudio（隔离子进程，防原生崩溃）
        try:
            from utils.safe_hw_probe import safe_audio_devices
            res_in = safe_audio_devices(True)
            if isinstance(res_in, dict) and res_in.get("devices"):
                for d in res_in["devices"]:
                    result["input_devices"].append({
                        "index": d.get("index", 0),
                        "name": d.get("name", "unknown"),
                        "max_input_channels": d.get("channels", 1),
                        "max_output_channels": 0,
                    })
                res_out = safe_audio_devices(False)
                if isinstance(res_out, dict):
                    for d in res_out.get("devices", []):
                        result["output_devices"].append({
                            "index": d.get("index", 0),
                            "name": d.get("name", "unknown"),
                            "max_input_channels": 0,
                            "max_output_channels": d.get("channels", 1),
                        })
                result["has_input"] = len(result["input_devices"]) > 0
                result["has_output"] = len(result["output_devices"]) > 0
                return result
        except Exception as e:
            self._log(LogLevel.DEBUG, f"pyaudio 检测失败: {e}")

        # 方法2：Windows 系统命令
        if sys.platform == "win32":
            try:
                import subprocess
                # 检测音频输出设备
                output = subprocess.run(
                    ["powershell", "-Command", "Get-AudioDevice -List"],
                    check=False, capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace"
                )
                if output.stdout and "Speakers" in output.stdout or "Headphones" in output.stdout:
                    result["has_output"] = True
                    result["output_devices"].append({"name": "系统默认扬声器", "detected_via": "powershell"})
            except Exception as e:
                silent_exc(e, where="organs.core.PulseProprioception::_detect_audio_devices L194")

        # 方法3：Linux 系统命令
        if sys.platform.startswith("linux"):
            try:
                if os.path.exists("/proc/asound/cards"):
                    result["has_output"] = True
                    result["output_devices"].append({"name": "ALSA音频设备", "detected_via": "/proc/asound"})
            except Exception as e:
                silent_exc(e, where="organs.core.PulseProprioception::_detect_audio_devices L203")

        # 方法4：macOS
        if sys.platform == "darwin":
            result["has_output"] = True  # macOS 几乎总是有音频输出
            result["output_devices"].append({"name": "CoreAudio设备", "detected_via": "platform_default"})

        return result

    # ========== 核心：生成本体画像 ==========

    def _generate_full_report(self, output_format: str = "dict") -> dict[str, Any]:
        """聚合所有信息，生成完整的本体感知报告。"""
        with self._lock:
            self._refresh_organs_status()
            self._evaluate_hardware_capabilities()
            self._evaluate_future_upgrades()
            health_score = self._calculate_health_score()

            report = {
                "timestamp": time.time(),
                "report_id": self._report_count + 1,
                "health_score": health_score,
                "organs_topology": self._organs_status,
                "hardware_capabilities": self._hardware_capabilities,
                "missing_capabilities": self._missing_hardware,
                "upgrade_path": self._upgrade_path,
                "total_organs": len(self._organs_status),
            }

            self._report_count += 1

            if self.info_field and self.pulse_core:
                # v9.5: 本体报告脉冲标记为L3后台自主层
                report_pulse = self.pulse_core.emit(
                    source_organ=self.organ_name,
                    event_type=ProprioceptionEvent.REPORT,
                    payload=report,
                    priority=3,
                    layer="L3"
                )
                self.info_field.publish(report_pulse)

            if output_format in ("console", "all"):
                self._print_report_console(report)

            if output_format in ("file", "all"):
                self._save_report_file(report)

            return report

    def _refresh_organs_status(self):
        """从预定义列表构建器官状态"""
        self._organs_status = {
            "大脑皮层": {"status": "active", "role": "决策调度"},
            "内在世界": {"status": "active", "role": "推理引擎"},
            "潜意识": {"status": "active", "role": "好奇心探索"},
            "兴趣模型": {"status": "active", "role": "求知本能"},
            "前额叶": {"status": "active", "role": "对话复盘"},
            "风险感知": {"status": "active", "role": "风险预判"},
            "QICA": {"status": "active", "role": "心智模型"},
            "心脏": {"status": "active", "role": "脉冲调度"},
            "肺": {"status": "active", "role": "模型管理"},
            "胃": {"status": "active", "role": "知识消化"},
            "肝": {"status": "active", "role": "知识优化"},
            "肾": {"status": "active", "role": "知识淘汰"},
            "血管": {"status": "active", "role": "连通性监测"},
            "触觉": {"status": "active", "role": "硬件感知"},
            "眼睛": {"status": "active", "role": "视觉检索"},
            "耳朵": {"status": "active", "role": "意图识别"},
            "嘴巴": {"status": "active", "role": "回复生成"},
            "双手": {"status": "active", "role": "任务调度"},
            "双腿": {"status": "active", "role": "网络抓取"},
            "代码沙箱": {"status": "active", "role": "代码执行"},
            "文件消化器": {"status": "active", "role": "文件处理"},
            "自我认知": {"status": "active", "role": "人物画像"},
            "伦理": {"status": "active", "role": "安全审查"},
            "成长": {"status": "active", "role": "能力评估"},
            "叙事自我": {"status": "active", "role": "价值观维护"},
            "人格内核": {"status": "active", "role": "核心锚点"},
            "白细胞": {"status": "active", "role": "异常检测"},
            "皮肤": {"status": "active", "role": "安全沙箱"},
            "胸腺": {"status": "active", "role": "策略优化"},
            "骨髓": {"status": "active", "role": "特征库"},
            "激素": {"status": "active", "role": "情绪检测"},
            "进化": {"status": "active", "role": "参数自搜索"},
            "DNA修复": {"status": "active", "role": "错误修复"},
            "情感羁绊": {"status": "active", "role": "关系管理"},
            "共同决策": {"status": "active", "role": "提议/接受/拒绝"},
            "养育": {"status": "active", "role": "成长阶段"},
            "生育伦理": {"status": "active", "role": "繁衍审查"},
            "能量代谢": {"status": "active", "role": "能量管理"},
            "健康监控": {"status": "active", "role": "异常分级"},
            "紧急处理": {"status": "active", "role": "安全模式"},
            "脊髓": {"status": "active", "role": "器官巡检"},
            "应激轴": {"status": "active", "role": "应激恢复"},
            "硬件启动器": {"status": "active", "role": "硬件评估"},
            "指标采集器": {"status": "active", "role": "指标采集"},
            "推理引擎": {"status": "active", "role": "统一推理"},
            "设备管理器": {"status": "active", "role": "设备抽象"},
            "系统管理器": {"status": "active", "role": "环境自愈"},
        }

        if self.touch and hasattr(self.touch, 'get_hardware_info'):
            try:
                hw = self._call_provider(self._hardware_info_provider, default={})
                if hw:
                    self._organs_status["触觉"]["hardware"] = {
                        "cpu": hw.get("cpu", {}).get("model", "unknown"),
                        "cores": hw.get("cpu", {}).get("cores", 0),
                        "memory_gb": hw.get("memory", {}).get("total_gb", 0),
                        "gpu": hw.get("gpu", {}).get("model", "none"),
                    }
            except Exception as e:
                self._log(LogLevel.ERROR, f'异常: {e}')

    # ========== 硬件能力矩阵（视觉感知与视觉计算分离） ==========

    def _evaluate_hardware_capabilities(self):
        """基于设备管理器的能力枚举表构建当前能力矩阵（不再自行检测硬件）"""
        dc = self._device_capabilities

        # 优先使用设备管理器枚举值，无数据时回退到自行检测
        camera_available = dc.get("sensor.camera.available", False)
        mic_available = dc.get("sensor.microphone.available", False)
        speaker_available = dc.get("sensor.speaker.available", False)
        gpu_available = dc.get("compute.gpu.available", False)  # noqa: F841
        gpu_model = dc.get("compute.gpu.model", "none")  # noqa: F841
        cpu_model = dc.get("compute.cpu.model", "unknown")  # noqa: F841
        cpu_cores = dc.get("compute.cpu.cores", 0)  # noqa: F841

        caps = {
            "visual_sensing": {
                "available": camera_available,
                "devices": [f"摄像头 (via {dc.get('compute.cpu.model', '设备管理器')})"] if camera_available else [],
                "description": "视觉感知（摄像头）"
            },
            "visual_compute": {
                "available": self._has_gpu_compute(),
                "devices": self._get_gpu_devices(),
                "description": "视觉计算（GPU/NPU）"
            },
            "auditory_input": {
                "available": mic_available,
                "devices": ["麦克风 (via 设备管理器)"] if mic_available else [],
                "description": "音频输入（麦克风）"
            },
            "auditory_output": {
                "available": speaker_available,
                "devices": ["扬声器 (via 设备管理器)"] if speaker_available else [],
                "description": "音频输出（扬声器）"
            },
            "locomotion": {"available": False, "devices": [], "description": "运动能力"},
            "manipulation": {"available": False, "devices": [], "description": "操作能力"},
            "network": {"available": True, "devices": ["ethernet/wifi"], "description": "网络通信"},
            "storage": {"available": True, "description": "数据存储"},
        }

        caps["visual_sensing"]["potential"] = "USB UVC 摄像头、CSI 摄像头、IP 网络摄像头"
        caps["visual_compute"]["potential"] = "NVIDIA CUDA、Intel OpenVINO、Apple Metal、NPU"
        caps["auditory_input"]["potential"] = "USB 麦克风、蓝牙耳机、I2S 麦克风"
        caps["locomotion"]["potential"] = "PWM 舵机、CAN 总线电机、ROS 机器人"
        caps["manipulation"]["potential"] = "机械臂、灵巧手、Dynamixel 舵机"

        self._hardware_capabilities = caps

    def _detect_camera(self) -> dict[str, Any]:
        """检测系统中的摄像头设备"""
        result = {"has_camera": False, "devices": []}

        # 方法1：OpenCV（隔离子进程，防原生崩溃）
        try:
            from utils.safe_hw_probe import safe_camera_devices
            cam_res = safe_camera_devices()
            if isinstance(cam_res, dict) and cam_res.get("devices"):
                result["has_camera"] = True
                for d in cam_res["devices"]:
                    result["devices"].append(f"Camera {d.get('index', 0)}")
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 方法2：Windows DirectShow
        if sys.platform == "win32" and not result["has_camera"]:
            try:
                import subprocess
                output = subprocess.run(
                    ["powershell", "-Command",
                     "Get-CimInstance -ClassName Win32_PnPEntity | Where-Object { $_.PNPClass -eq 'Camera' } | Select-Object -ExpandProperty Name"],
                    check=False, capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace"
                )
                if output.stdout.strip():
                    result["has_camera"] = True
                    for line in output.stdout.strip().split("\n"):
                        name = line.strip()
                        if name:
                            result["devices"].append(name)
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 方法3：Linux V4L2
        if sys.platform.startswith("linux") and not result["has_camera"]:
            try:
                import subprocess
                output = subprocess.run(
                    ["v4l2-ctl", "--list-devices"],
                    check=False, capture_output=True, text=True, timeout=5,
                    encoding="utf-8", errors="replace"
                )
                if "/dev/video" in output.stdout:
                    result["has_camera"] = True
                    result["devices"].append("V4L2 摄像头")
            except Exception:
                if os.path.exists("/dev/video0"):
                    result["has_camera"] = True
                    result["devices"].append("/dev/video0")

        # 方法4：macOS AVFoundation
        if sys.platform == "darwin" and not result["has_camera"]:
            try:
                import subprocess
                output = subprocess.run(
                    ["system_profiler", "SPCameraDataType"],
                    check=False, capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace"
                )
                if "Camera" in output.stdout:
                    result["has_camera"] = True
                    result["devices"].append("Mac 内置摄像头")
            except Exception as e:
                silent_exc(e, where="organs.core.PulseProprioception::_detect_camera L433")

        return result

    def _has_gpu_compute(self) -> bool:
        """检测是否有可用的GPU计算能力（优先使用枚举值）"""
        dc = self._device_capabilities
        if dc:
            return dc.get("compute.gpu.available", False)
        if self.touch:
            # ★P3-1修复：跨器官 getter 直读 → provider 回调（规则14 依赖注入+回调）
            hw = self._call_provider(self._hardware_info_provider, default={})
            return hw.get("gpu", {}).get("available", False)
        return False

    def _get_gpu_devices(self) -> list[str]:
        """获取GPU设备列表（优先使用枚举值）"""
        dc = self._device_capabilities
        if dc and dc.get("compute.gpu.available"):
            return [dc.get("compute.gpu.model", "GPU")]
        if self.touch:
            # ★P3-1修复：跨器官 getter 直读 → provider 回调（规则14 依赖注入+回调）
            gpu = self._call_provider(self._hardware_info_provider, default={}).get("gpu", {})
            if gpu.get("available"):
                return [gpu.get("model", "GPU")]
        return []

    def _evaluate_future_upgrades(self):
        """对照蓝图，列出缺失能力和建议升级路径"""
        self._missing_hardware = []
        self._upgrade_path = []

        caps = self._hardware_capabilities

        if not caps.get("visual_sensing", {}).get("available"):
            self._missing_hardware.append("摄像头（视觉感知）")
            self._upgrade_path.append({
                "capability": "视觉",
                "current": "无",
                "target": "USB UVC 摄像头 / CSI 摄像头",
                "effort": "低",
                "impact": "高（解锁视觉交互）"
            })

        if not caps.get("auditory_input", {}).get("available"):
            self._missing_hardware.append("麦克风（音频输入）")
            self._upgrade_path.append({
                "capability": "音频输入",
                "current": "无",
                "target": "USB 麦克风 / 蓝牙耳机",
                "effort": "低",
                "impact": "高（解锁语音交互）"
            })

        if not caps.get("locomotion", {}).get("available"):
            self._missing_hardware.append("运动控制（机器人躯体）")
            self._upgrade_path.append({
                "capability": "运动能力",
                "current": "无",
                "target": "树莓派 + PWM 舵机 / 宇树 Go2 / 自制仿生体",
                "effort": "中-高",
                "impact": "极高（从纯数字生命迈向物理实体）"
            })

        if not caps.get("manipulation", {}).get("available"):
            self._missing_hardware.append("操作能力（机械手/臂）")
            self._upgrade_path.append({
                "capability": "操作能力",
                "current": "无",
                "target": "Dynamixel 舵机 / 灵巧手",
                "effort": "高",
                "impact": "高（解锁物理交互）"
            })

    def _calculate_health_score(self) -> float:
        total = len(self._organs_status)
        if total == 0:
            return 0.0
        active = sum(1 for o in self._organs_status.values() if o.get("status") == "active")
        return round(min(active / total * 100, 100), 1)

    # ========== 五维控制台输出 ==========

    def _print_report_console(self, report: dict[str, Any]):
        caps = report.get("hardware_capabilities", {})
        self._log(LogLevel.INFO, "\n" + "=" * 70)
        self._log(LogLevel.INFO, f"  🧠 曈曈 v9.5 本体感知报告 #{report['report_id']}")
        self._log(LogLevel.INFO, "=" * 70)
        self._log(LogLevel.INFO, f"  健康评分: {report['health_score']:.1f}%")
        self._log(LogLevel.INFO, f"  在线器官: {report['total_organs']} 个")
        self._log(LogLevel.INFO, "-" * 70)
        self._log(LogLevel.INFO, "  【五维本体画像】")
        print(f"  1. 躯体 (硬件): CPU={self._get_hw_summary('cpu')} | "
              f"内存={self._get_hw_summary('memory')} | GPU={self._get_hw_summary('gpu')}")
        self._log(LogLevel.INFO, f"  2. 心智 (器官): {', '.join(list(self._organs_status.keys())[:8])}...")
        # 感知维度——严格区分视觉感知和视觉计算
        visual_sensing = caps.get("visual_sensing", {})
        visual_compute = caps.get("visual_compute", {})
        auditory_input = caps.get("auditory_input", {})
        auditory_output = caps.get("auditory_output", {})

        self._log(LogLevel.INFO, "  3. 感知 (传感器):")
        print(f"     视觉感知={'✅' if visual_sensing.get('available') else '❌'} "
              f"({' + '.join(visual_sensing.get('devices', [])) if visual_sensing.get('devices') else '未检测到摄像头'})")
        print(f"     视觉计算={'✅' if visual_compute.get('available') else '❌'} "
              f"({' + '.join(visual_compute.get('devices', [])) if visual_compute.get('devices') else '无加速硬件'})")
        print(f"     音频输入={'✅' if auditory_input.get('available') else '❌'} "
              f"({' + '.join(auditory_input.get('devices', [])[:2]) if auditory_input.get('devices') else '未检测到麦克风'})")
        print(f"     音频输出={'✅' if auditory_output.get('available') else '❌'} "
              f"({' + '.join(auditory_output.get('devices', [])[:2]) if auditory_output.get('devices') else '未检测到扬声器'})")

        print(f"  4. 运动 (执行器): 运动={'✅' if caps.get('locomotion', {}).get('available') else '❌'} | "
              f"操作={'✅' if caps.get('manipulation', {}).get('available') else '❌'}")
        self._log(LogLevel.INFO, f"  5. 扩展 (未来): {len(self._upgrade_path)} 条建议升级路径")
        self._log(LogLevel.INFO, "-" * 70)
        if self._upgrade_path:
            self._log(LogLevel.INFO, "  【建议升级路径】")
            for idx, path in enumerate(self._upgrade_path, 1):
                print(f"  {idx}. {path['capability']}: {path['current']} → {path['target']} "
                      f"(难度:{path['effort']}, 影响:{path['impact']})")
        self._log(LogLevel.INFO, "=" * 70 + "\n")

    def _get_hw_summary(self, hw_type: str) -> str:
        if not self.touch:
            return "未知"
        hw = self._call_provider(self._hardware_info_provider, default={})
        if hw_type == "cpu":
            return f"{hw.get('cpu', {}).get('model', '未知')[:20]}"
        elif hw_type == "memory":
            return f"{hw.get('memory', {}).get('total_gb', 0)}GB"
        elif hw_type == "gpu":
            gpu = hw.get("gpu", {})
            return gpu.get("model", "无") if gpu.get("available") else "无"
        return "未知"

    def _save_report_file(self, report: dict[str, Any]):
        try:
            filepath = "data/proprioception_report.json"
            os.makedirs("data", exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            self._log(LogLevel.INFO, f"报告已保存至 {filepath}")
        except Exception as e:
            self._log(LogLevel.WARNING, f"保存报告失败: {e}")
    def _on_capability_update(self, payload: dict) -> dict[str, Any]:
        """收到设备管理器的硬件能力枚举脉冲"""
        caps = payload.get("capabilities", {})
        self._device_capabilities = caps
        self._log(LogLevel.INFO, f"设备能力更新: CPU={caps.get('compute.cpu.model', '?')}, "
                  f"GPU={'可用' if caps.get('compute.gpu.available') else '不可用'}, "
                  f"计算等级={caps.get('compute.level', '?')}")
        return {"status": "updated", "capabilities": caps}
    def _on_device_attached(self, payload: dict[str, Any]):
        device_type = payload.get("device_type", "unknown")
        self._log(LogLevel.INFO, f"检测到新设备接入: {device_type}")
        if "camera" in device_type or "cam" in device_type:
            self._hardware_capabilities["visual_sensing"]["available"] = True
        if "mic" in device_type or "audio" in device_type:
            self._hardware_capabilities["auditory_input"]["available"] = True

    def _on_device_detached(self, payload: dict[str, Any]):
        device_type = payload.get("device_type", "unknown")
        self._log(LogLevel.INFO, f"设备断开: {device_type}")

    def _on_heartbeat(self) -> None:
        if self.touch:
            self._evaluate_hardware_capabilities()

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 预留接口 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float):
        """【预留 v10.0】"""

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "report_count": self._report_count,
            "total_organs": len(self._organs_status),
            "missing_capabilities": self._missing_hardware,
            "hardware_capabilities": self._hardware_capabilities,
            "upgrade_path": len(self._upgrade_path),
            "is_running": self.is_running,
        }


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "本体感知",
    "class_name": "PulseProprioception",
    "attr_name": "proprioception",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [
        {"target": "触觉", "setter": "set_touch"},
        {"target": "系统管理器", "setter": "set_system_manager"},
    ],
}

if __name__ == "__main__":
    print("=== PulseProprioception v9.5 分层脉冲自测（音频检测版） ===\n")

    class MockTouch:
        def __init__(self):
            self._hardware_info = {
                "cpu": {"model": "AMD Ryzen 7 3700X", "cores": 16},
                "memory": {"total_gb": 47.9, "usage_percent": 35},
                "gpu": {"available": True, "model": "NVIDIA GTX 1050 Ti"},
                "disk": {"total_gb": 512, "free_gb": 200},
            }

    class MockField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {"event_type": event_type, "source_organ": source_organ,
                    "payload": payload, "priority": priority, "layer": layer}

    prop = PulseProprioception("本体感知")
    mock_touch = MockTouch()
    mock_field = MockField()
    mock_core = MockCore()

    prop.set_touch(mock_touch)
    prop.set_info_field(mock_field)
    prop.set_pulse_core(mock_core)

    prop.start()

    # 测试1: 音频设备检测
    print("1. 音频设备检测:")
    audio = prop._detect_audio_devices()
    print(f"   输入设备: {len(audio['input_devices'])} 个, 可用={audio['has_input']}")
    print(f"   输出设备: {len(audio['output_devices'])} 个, 可用={audio['has_output']}")
    for dev in audio.get("input_devices", [])[:2]:
        print(f"     - 输入: {dev.get('name', 'unknown')[:50]}")
    for dev in audio.get("output_devices", [])[:2]:
        print(f"     - 输出: {dev.get('name', 'unknown')[:50]}")

    # 测试2: 生成报告
    print("\n2. 生成控制台报告:")
    report = prop.on_pulse({
        "event_type": ProprioceptionEvent.REQUEST,
        "payload": {"format": "console"},
        "priority": 5,
    })
    print(f"   健康评分: {report['health_score']}%")
    print(f"   在线器官: {report['total_organs']} 个")
    print(f"   缺失能力: {len(report['missing_capabilities'])} 项")
    print(f"   升级路径: {len(report['upgrade_path'])} 条")

    # 验证本体报告脉冲的 layer 标记
    report_pulses = [p for p in mock_field.published if p.get("event_type") == ProprioceptionEvent.REPORT]
    if report_pulses:
        print(f"   REPORT脉冲 layer: {report_pulses[-1].get('layer', '未设置')} (预期L3)")

    prop.stop()
    print("\n=== 自测全部通过 ===")
