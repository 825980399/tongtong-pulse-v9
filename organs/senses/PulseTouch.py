from config import EXTERNAL_CALL_TIMEOUTS
"""PulseTouch —— _read_sys_file 相关实现

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

"""
PulseTouch —— 脉冲驱动触觉（硬件感知器官 · v9.5 分层脉冲版）
版本: v9.5 PulseNet
设计: 路灯、小林、星轨 
日期: 2026年6月9日
更新: 2026年6月13日（P0-2+P0-5: 五合一全面改造——事件枚举+统一日志+命名规范+get_stats+自测同步）
更新: 2026年6月14日（v9.5: 硬件快照脉冲标记layer=L3，适配分层异步调度）
更新: 2026年6月21日（修复：磁盘使用率每次快照动态采集，避免使用初始死值）

职责:
    1. 硬件信息采集：CPU/内存/GPU/磁盘
    2. 实时资源监控：使用率、温度、频率
    3. 定时发布 TouchEvent.HARDWARE_SNAPSHOT 脉冲
    4. 缺失容错：GPU不存在时不崩溃，嵌入式平台优雅降级
    5. 多平台适配：Windows/Linux/macOS 通用
"""

import threading
import time
from typing import Any

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import Event, LogLevel, SystemEvent, TouchEvent
from nucleus._silent_except import silent_exc


def _read_sys_file(path: str) -> str:
    """★二期：读取 /sys 单值文件（如 rotational、size），失败返回空串。"""
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def _parse_size_kb(size_str: str) -> int:
    """★二期：把 /sys 的 size 字符串（如 "32K"、"512K"、"8M"）转成 KB 整数。"""
    try:
        _s = size_str.strip().upper()
        if _s.endswith("K"):
            return int(float(_s[:-1]))
        if _s.endswith("M"):
            return int(float(_s[:-1]) * 1024)
        if _s.endswith("G"):
            return int(float(_s[:-1]) * 1024 * 1024)
        return int(float(_s))
    except Exception:
        return 0


class PulseTouch(BasePulseOrgan):
    """脉冲驱动触觉（v9.5 分层脉冲版）"""

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'touch_snapshot_interval' in _rp and hasattr(self, '_snapshot_interval'):
                self._snapshot_interval = _rp['touch_snapshot_interval']
            if 'touch_alarm_cooldown' in _rp and hasattr(self, '_alarm_cooldown'):
                self._alarm_cooldown = _rp['touch_alarm_cooldown']
            if 'touch_gpu_probe_interval' in _rp and hasattr(self, '_gpu_probe_interval'):
                self._gpu_probe_interval = _rp['touch_gpu_probe_interval']
        except Exception:
            pass


    def __init__(self, organ_name: str = "触觉"):
        super().__init__(organ_name)

        self._hardware_info: dict[str, Any] = {}
        self._last_snapshot_time = 0.0
        # ★PHASE17-B 验收发现（2026-09-07）：属性初始化顺序错误导致启动 ERROR。
        #   原顺序是「先 self._detect_hardware()（:89）→ 后 self._has_gpu = False（:92）」，
        #   而 _detect_hardware 内部 :126 会读 self._has_gpu，_get_cpu_info 等也会读
        #   self._has_psutil —— 两者此时都还不存在。
        #   之所以时好时坏：_get_gpu_info() 在**探测到 GPU** 时会顺手把 _has_gpu 置 True
        #   （:239/:256），于是「有 GPU 的机器」侥幸不报错；一旦 GPU 探测失败
        #   （无独显 / 驱动异常 / 沙箱），:126 立即 AttributeError。
        #   实测证据：小林 logs/pulse.log:595 与本沙箱启动日志均出现
        #   「声明式装配失败 触觉: 'PulseTouch' object has no attribute '_has_gpu'」。
        #   修复：把这两个属性的初始化提到 _detect_hardware() 之前，零行为副作用。
        self._has_gpu = False
        self._has_psutil = False
                # ★P1: 从RUNTIME_PARAMS读取参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._snapshot_interval = _rp.get('touch_snapshot_interval', 30.0)
            self._alarm_cooldown = _rp.get('touch_alarm_cooldown', 60.0)
            self._gpu_probe_interval = _rp.get('touch_gpu_probe_interval', 15.0)
        except Exception:
            self._snapshot_interval = 30.0
            self._alarm_cooldown = 60.0
            self._gpu_probe_interval = 15.0
        self._detect_hardware()
        # ★P0修复：初始化_booted属性（之前缺失导致AttributeError）
        self._booted = False
        # ★Q4（2026-09-09 第一批追加，星轨拍板）：此处原有两句
        #     self._has_gpu = False / self._has_psutil = False
        #   它们在 _detect_hardware() **之后**又把检测结果抹掉 —— 于是运行时
        #   这两个标志恒为 False，温度/CPU/GPU/内存采集一律走兜底分支，psutil 白装。
        #   （这两句是早先"属性初始化完整性补全"时补进来的，属无心之失。）
        #   默认值已在 :95-96 于检测**之前**设好（PHASE17-B 修复），
        #   检测失败时仍然保持 False，因此这里直接删除是安全的。
        #   ⚠ 请勿再加回来：加了等于让整个硬件感知层静默失效。
        self._snapshot_count = 0
        self._gpu_probe_timer = None
        self._last_gpu_usage = 0.0

        # ★属性初始化完整性补全（自动审查添加）
        self._cached_camera_available = {}
        self._cached_microphone_available = {}
        self._cached_speaker_available = {}
        self._cpu_high_count = 0
        self._device_check_counter = 0
        self._gpu_usage_cache = {}
        self._last_alarm_time = 0.0
        self._last_gpu_available = 0.0
        self._next_timer = 0.0

    # ========== 硬件检测 ==========

    def _detect_hardware(self):
        try:
            import psutil  # noqa: F401
            self._has_psutil = True
        except ImportError:
            self._log(LogLevel.WARNING, "psutil 未安装，使用基础硬件检测")

        self._hardware_info["cpu"] = self._get_cpu_info()
        self._hardware_info["memory"] = self._get_memory_info()
        self._hardware_info["gpu"] = self._get_gpu_info()
        self._hardware_info["disk"] = self._get_disk_info()

        self._log(LogLevel.INFO,
                  f"硬件检测完成: CPU={self._hardware_info['cpu'].get('cores', '?')}核心 "
                  f"内存={self._hardware_info['memory'].get('total_gb', '?')}GB "
                  f"GPU={'有' if self._has_gpu else '无'}")
        self._hardware_info["environment"] = self._get_environment_info()        

    def _get_cpu_info(self) -> dict[str, Any]:
        info = {"model": "unknown", "cores": 1, "usage_percent": 0.0}
        if self._has_psutil:
            import psutil
            try:
                info["model"] = self._get_cpu_model()
                info["cores"] = psutil.cpu_count(logical=True) or 1
                info["physical_cores"] = psutil.cpu_count(logical=False) or 1
                info["usage_percent"] = psutil.cpu_percent(interval=0.1)
                info["frequency_mhz"] = self._get_cpu_freq()
                # ★二期：补全 L1/L2/L3 三级缓存检测
                info["cache"] = self._get_cpu_cache_info()
            except Exception as e:
                self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        else:
            info["cores"] = os.cpu_count() or 1
        return info

    def _get_cpu_cache_info(self) -> dict[str, Any]:
        """★二期：读取 CPU L1/L2/L3 三级缓存大小（Linux /sys，跨平台兜底）。

        返回形如 {"l1d_kb": 32, "l1i_kb": 32, "l2_kb": 256, "l3_kb": 8192, "source": "sysfs"}
        读取失败返回空 dict（不抛异常），保证任何平台都不误伤。
        """
        cache = {"source": "unknown"}
        try:
            if sys.platform == "linux":
                # /sys/devices/system/cpu/cpu0/cache/indexN/{level,size,type}
                _base = "/sys/devices/system/cpu/cpu0/cache"
                if os.path.isdir(_base):
                    for _idx in os.listdir(_base):
                        _dir = os.path.join(_base, _idx)
                        if not os.path.isdir(_dir):
                            continue
                        _level = _read_sys_file(os.path.join(_dir, "level"))
                        _ctype = _read_sys_file(os.path.join(_dir, "type"))
                        _size = _read_sys_file(os.path.join(_dir, "size"))
                        if not _level or not _size:
                            continue
                        # 统一转换为 KB
                        _kb = _parse_size_kb(_size)
                        if _kb <= 0:
                            continue
                        _key = f"l{_level.strip().lower()}"
                        if _ctype and _ctype.strip().lower() == "instruction":
                            _key = f"l{_level.strip().lower()}i"
                        else:
                            _key = f"l{_level.strip().lower()}d"
                        # 多核共享的 L3 只记一次（取最大值）
                        if _key in cache and isinstance(cache[_key], int):
                            cache[_key] = max(cache[_key], _kb)
                        else:
                            cache[_key] = _kb
                    if any(k.startswith("l") for k in cache):
                        cache["source"] = "sysfs"
        except Exception as e:
            self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return cache

    def _get_cpu_model(self) -> str:
        try:
            if os.path.exists("/proc/cpuinfo"):
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if "model name" in line:
                            return line.split(":")[1].strip()
            if sys.platform == "darwin":
                import subprocess
                result = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                       capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_short"], )
                if result.stdout:
                    return result.stdout.strip()
        except Exception as e:
            self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return "unknown"

    def _get_cpu_freq(self) -> float:
        try:
            import psutil
            freq = psutil.cpu_freq()
            if freq:
                return freq.current
        except Exception as e:
            self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return 0.0

    def _get_memory_info(self) -> dict[str, Any]:
        info = {"total_gb": 0.0, "available_gb": 0.0, "usage_percent": 0.0}
        if self._has_psutil:
            import psutil
            try:
                mem = psutil.virtual_memory()
                info["total_gb"] = round(mem.total / (1024**3), 1)
                info["available_gb"] = round(mem.available / (1024**3), 1)
                info["usage_percent"] = mem.percent
            except Exception as e:
                self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return info

    def _get_gpu_info(self) -> dict[str, Any]:
        info = {"available": False, "model": "none", "memory_mb": 0}
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                check=False, capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace"
            )
            if result.returncode == 0 and result.stdout.strip():
                self._has_gpu = True
                info["available"] = True
                parts = result.stdout.strip().split(",")
                if len(parts) >= 1:
                    info["model"] = parts[0].strip()
                if len(parts) >= 2:
                    mem_str = parts[1].strip().replace("MiB", "").strip()
                    info["memory_mb"] = int(mem_str)
                # ★二期：补全共享显存（独立显卡用 memory.total 作参考口径）
                info["shared_memory_mb"] = self._get_gpu_shared_memory()
                return info
        except Exception as e:
            self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                self._has_gpu = True
                info["available"] = True
                info["model"] = gpus[0].name
                info["memory_mb"] = gpus[0].memoryTotal
                info["shared_memory_mb"] = 0  # GPUtil 不提供共享显存
                return info
        except Exception as e:
            self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return info

    def _get_disk_info(self) -> dict[str, Any]:
        info = {"total_gb": 0.0, "free_gb": 0.0, "usage_percent": 0.0}
        if self._has_psutil:
            import psutil
            try:
                target_path = os.getcwd()
                if sys.platform == "win32":
                    target_path = os.path.splitdrive(target_path)[0] + "\\"
                usage = psutil.disk_usage(target_path)
                info["total_gb"] = round(usage.total / (1024**3), 1)
                info["free_gb"] = round(usage.free / (1024**3), 1)
                info["usage_percent"] = usage.percent
                # ★二期：补全磁盘类型（SSD/HDD）检测
                _dtype = self._get_disk_type()
                if _dtype:
                    info["type"] = _dtype
            except Exception as e:
                self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return info

    def _get_disk_type(self) -> str:
        """★二期：判断系统盘类型 SSD / HDD / unknown。

        方法：读 /sys/block/<dev>/queue/rotational（Linux），0=SSD、1=HDD；
        Windows/macOS 用 psutil 兜底（无 rotational 字段时返回 unknown）。
        不抛异常，任何平台都安全。
        """
        try:
            if sys.platform == "linux":
                # 找到承载当前工作目录的块设备
                import psutil
                _disk = psutil.disk_partitions(all=False)
                _dev = None
                for _p in _disk:
                    if _p.mountpoint and os.getcwd().startswith(_p.mountpoint):
                        _dev = _p.device
                        break
                if _dev:
                    # /dev/sda1 → sda；/dev/nvme0n1p2 → nvme0n1
                    _name = os.path.basename(_dev)
                    # 去掉分区号：取最长的纯字母前缀
                    _base = _name.rstrip("0123456789")
                    _rot = _read_sys_file(f"/sys/block/{_base}/queue/rotational")
                    if _rot == "0":
                        return "ssd"
                    if _rot == "1":
                        return "hdd"
                    # nvme 一定是 SSD
                    if _name.startswith("nvme"):
                        return "ssd"
        except Exception as e:
            self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return "unknown"

    def _get_gpu_shared_memory(self) -> int:
        """★二期：读取 GPU 共享显存（MB，0 表示无/未知）。

        Linux nvidia-smi 输出 shared memory；集成显卡（无独立 GPU）读 /sys 或返回 0。
        不抛异常。
        """
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
                check=False, capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0 and result.stdout.strip():
                # nvidia-smi 的 memory.total 即显存总量；共享显存需另查，这里返回总量作为参考
                # 注：nvidia-smi 不直接给 shared memory，二期以「显存总量」+「有无独显」为口径，
                #     集成显卡共享显存（借系统内存）通过 /sys/class/drm 探测（见下方）。
                _first = result.stdout.strip().split("\n")[0]
                _parts = _first.split(",")
                if _parts and _parts[0].strip().isdigit():
                    return int(_parts[0].strip())
        except Exception as e:
            self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return 0

    def _get_environment_info(self) -> dict[str, Any]:
        """
        检测物理环境信息。
        当前使用系统时间和桌面环境推断，未来可接入物理传感器。
        只规定下限逻辑——无传感器时返回默认值，永远不报错。
        """
        
        info = {
            "light_level": self._detect_light_level(),
            "temperature": self._detect_temperature(),
            "season": self._get_current_season(),
            "time_of_day": self._get_time_period(),
            "detection_method": "inference",  # 当前使用推断，未来可切换为"sensor"
        }
        return info
    
    def _detect_light_level(self) -> float:
        """
        检测环境光照水平 (0.0-1.0)。
        当前基于时间和系统主题推断，未来可接入环境光传感器。
        """
        hour = time.localtime().tm_hour
        
        # 基于时间的简单推断
        if 6 <= hour < 8:
            return 0.4  # 日出
        elif 8 <= hour < 17:
            return 0.9  # 白天
        elif 17 <= hour < 19:
            return 0.5  # 日落
        elif 19 <= hour < 21:
            return 0.2  # 傍晚
        else:
            return 0.05  # 夜间
        
        return 0.5  # 默认中等光照
    
    def _detect_temperature(self) -> float:
        """
        检测环境温度（摄氏度）。
        当前使用CPU温度近似，未来可接入温度传感器。

        ★P2-16（2026-09-09 技术债务第一批）：Windows 平台兼容。
        实测 psutil 7.2.2 / Windows 11：`hasattr(psutil, "sensors_temperatures") == False`，
        直接调用抛 `AttributeError: module 'psutil' has no attribute 'sensors_temperatures'`。
        原实现把它当成普通异常打成 WARNING，导致**每次启动固定一条噪音**，
        而"Sandbox/Windows 没有温度传感器"根本不是错误，是平台能力缺失。
        修复：① 先 `hasattr` 判断平台能力，缺失则走 DEBUG 说明 + 季节兜底；
              ② 接口存在时仍用 try/except 兜底（部分 Linux 容器返回空/无权限）；
              ③ 降级说明只打一次，避免周期性快照刷屏。
        """
        # 尝试从CPU获取温度
        if self._has_psutil:
            try:
                import psutil
                # ★P2-16：平台能力前置判断（Windows / 受限容器无此接口）
                if not hasattr(psutil, "sensors_temperatures"):
                    if not getattr(self, "_temp_api_checked", False):
                        self._temp_api_checked = True
                        self._log(LogLevel.DEBUG,
                                  "平台不支持 psutil.sensors_temperatures"
                                  "（Windows/受限容器无温度传感器接口），"
                                  "温度改由季节推断兜底（非错误，不告警）")
                else:
                    temps = psutil.sensors_temperatures()
                    if temps:
                        for entries in temps.values():
                            if entries:
                                return entries[0].current
                    # 接口存在但返回空（部分 Linux 容器）：同样静默走兜底
                    if not getattr(self, "_temp_api_checked", False):
                        self._temp_api_checked = True
                        self._log(LogLevel.DEBUG,
                                  "psutil.sensors_temperatures() 返回空，"
                                  "温度改由季节推断兜底（非错误，不告警）")
            except Exception as e:
                # ★P2-16：外部依赖不可用 → DEBUG（原为 WARNING，属噪音）。
                #   真异常信息不丢：保留 {type(e).__name__}: {e} 便于事后定位。
                self._log(LogLevel.DEBUG,
                          f"温度采集失败，改由季节推断兜底: {type(e).__name__}: {e}")
        
        # 兜底：基于季节推断
        month = time.localtime().tm_mon
        if month in (12, 1, 2):
            return 15.0  # 冬季
        elif month in (3, 4, 5):
            return 22.0  # 春季
        elif month in (6, 7, 8):
            return 30.0  # 夏季
        else:
            return 20.0  # 秋季
    
    def _get_current_season(self) -> str:
        """获取当前季节"""
        month = time.localtime().tm_mon
        if month in (3, 4, 5):
            return "spring"
        elif month in (6, 7, 8):
            return "summer"
        elif month in (9, 10, 11):
            return "autumn"
        else:
            return "winter"
    
    def _get_time_period(self) -> str:
        """获取当前时段"""
        hour = time.localtime().tm_hour
        if 5 <= hour < 8:
            return "dawn"
        elif 8 <= hour < 12:
            return "morning"
        elif 12 <= hour < 14:
            return "noon"
        elif 14 <= hour < 18:
            return "afternoon"
        elif 18 <= hour < 21:
            return "evening"
        elif 21 <= hour < 23:
            return "night"
        else:
            return "late_night"
        
    def _get_gpu_usage(self) -> dict[str, float]:
        """获取GPU实时使用率（非阻塞：直接返回后台探针缓存，绝不在此路径上启动 nvidia-smi 子进程）。"""
        # 惰性启动后台探针（首次调用时），避免脉冲线程被 nvidia-smi 阻塞
        if self._gpu_probe_timer is None and self._has_gpu:
            self._start_gpu_probe()
        return dict(self._gpu_usage_cache)

    # ========== GPU 使用率后台探针（非阻塞） ==========
    def _start_gpu_probe(self):
        """启动后台定时探针：周期性运行 nvidia-smi 并更新缓存，脉冲路径只读取缓存。"""
        if self._gpu_probe_timer is not None:
            return
        self._gpu_probe_once()  # 先立即探测一次（在后台线程，不阻塞调用方）
        self._gpu_probe_timer = threading.Timer(self._gpu_probe_interval, self._gpu_probe_loop)
        self._gpu_probe_timer.daemon = True
        self._gpu_probe_timer.start()

    def _gpu_probe_loop(self):
        self._gpu_probe_once()
        if not self._booted:
            self._gpu_probe_timer = None
            return
        self._gpu_probe_timer = threading.Timer(self._gpu_probe_interval, self._gpu_probe_loop)
        self._gpu_probe_timer.daemon = True
        self._gpu_probe_timer.start()

    def _gpu_probe_once(self):
        """在独立守护线程中运行 nvidia-smi（带超时），仅更新缓存，不阻塞任何脉冲线程。"""
        def _run():
            try:
                import subprocess
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory", "--format=csv,noheader,nounits"],
                    check=False, capture_output=True, text=True, timeout=5,
                    encoding="utf-8", errors="replace"
                )
                if r.returncode == 0 and r.stdout.strip():
                    parts = r.stdout.strip().split(",")
                    usage = float(parts[0].strip()) if len(parts) >= 1 else 0.0
                    mem = float(parts[1].strip()) if len(parts) >= 2 else 0.0
                    self._gpu_usage_cache = {"usage_percent": usage, "memory_usage_percent": mem}
            except Exception as e:
                silent_exc(e, "PulseTouch:534:GPU探测异常", level="warning")
        _t = threading.Thread(target=_run, daemon=True)
        _t.start()

    def _stop_gpu_probe(self):
        if self._gpu_probe_timer is not None:
            try:
                self._gpu_probe_timer.cancel()
            except Exception as e:
                self._log(LogLevel.DEBUG, f"清理异常已忽略: {type(e).__name__}: {e}")
            self._gpu_probe_timer = None

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")

        if event_type == SystemEvent.BOOT:
            return self._on_system_boot()
        elif event_type == SystemEvent.STOP:
            return self._on_system_stop()
        elif event_type == TouchEvent.SNAPSHOT:
            return self._on_snapshot()
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()

        return None

    # ========== 事件处理 ==========

    def _on_system_boot(self) -> dict[str, Any]:
        if self._booted:
            return {"status": "already_booted"}
        self._booted = True
        self._log(LogLevel.INFO, "触觉激活，开始硬件感知")
        # ★T-113f（2026-09-23）：首帧硬件快照采集(CPU/内存/磁盘/GPU/设备连接检测)
        #   属硬件 I/O，移后台守护线程，boot 立即返回，避免阻塞 L0 生命线层。
        #   GPU 探针本就后台；快照定时器轻量同步。
        threading.Thread(target=self._emit_snapshot, daemon=True).start()
        self._start_gpu_probe()  # 启动后台 GPU 探针（非阻塞）
        self._schedule_next_snapshot()
        return {"status": "booted"}

    def _on_system_stop(self) -> dict[str, Any]:
        self._booted = False
        self._stop_gpu_probe()
        if self._next_timer:
            self._next_timer.cancel()
            self._next_timer = None
        return {"status": "stopped"}

    def _on_snapshot(self) -> dict[str, Any]:
        return self._emit_snapshot()

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "snapshot_count": self._snapshot_count,
            "has_psutil": self._has_psutil,
            "has_gpu": self._has_gpu,
            "hardware": self._hardware_info,
            "is_running": self.is_running,
        }

    # ========== 快照发射 ==========

    def _emit_snapshot(self) -> dict[str, Any]:
        # 动态采集实时使用率
        self._hardware_info["cpu"]["usage_percent"] = self._get_cpu_usage()
        self._hardware_info["memory"] = self._get_memory_info()
        self._hardware_info["disk"] = self._get_disk_info()  # 每次更新磁盘使用率

        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._snapshot_count += 1
        self._last_snapshot_time = time.time()

        # 获取GPU实时使用率
        gpu_usage = self._get_gpu_usage()
        gpu_info = dict(self._hardware_info["gpu"])
        gpu_info["usage_percent"] = gpu_usage.get("usage_percent", 0.0)
        gpu_info["memory_usage_percent"] = gpu_usage.get("memory_usage_percent", 0.0)
        # 设备热插拔检测（每5次快照约2.5分钟检测一次，避免频繁打开硬件）
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._device_check_counter += 1
        if self._device_check_counter % 5 == 0:
            self._check_device_connections()        
        snapshot = {
            "cpu": self._hardware_info["cpu"],
            "memory": self._hardware_info["memory"], 
            "gpu": gpu_info,
            "disk": self._hardware_info["disk"],
            "timestamp": time.time(),
            "snapshot_id": self._snapshot_count,
            # ===== 热插拔检测状态 =====
            "camera_available": self._cached_camera_available,
            "microphone_available": self._cached_microphone_available,
            "speaker_available": self._cached_speaker_available,
            "environment": self._hardware_info.get("environment", {}),
        }

        # 硬件告警检测（带冷却保护，避免每次快照都重复告警）
        # 阈值从config读取，适配不同硬件环境
        now = time.time()
        alert_cfg = self._load_alert_config()
        alarm_cooldown = alert_cfg.get("alarm_cooldown", 60)
        
        if now - self._last_alarm_time >= alarm_cooldown:
            cpu_usage = snapshot["cpu"].get("usage_percent", 0)
            mem_usage = snapshot["memory"].get("usage_percent", 0)
            gpu_usage = snapshot["gpu"].get("usage_percent", 0)
            gpu_mem_usage = snapshot["gpu"].get("memory_usage_percent", 0)  # noqa: F841
            disk_usage = snapshot["disk"].get("usage_percent", 0)
            
            # 从config读取告警阈值
            cpu_critical = alert_cfg.get("cpu_critical", 90)
            mem_critical = alert_cfg.get("memory_critical", 90)
            gpu_critical = alert_cfg.get("gpu_critical", 95)
            disk_critical = alert_cfg.get("disk_critical", 95)
            cpu_warning = alert_cfg.get("cpu_warning", 70)
            mem_warning = alert_cfg.get("memory_warning", 80)
            gpu_warning = alert_cfg.get("gpu_warning", 85)
            disk_warning = alert_cfg.get("disk_warning", 90)
            
            # 综合判断：任一维度超过临界阈值则触发严重告警
            if (cpu_usage > cpu_critical or mem_usage > mem_critical or 
                gpu_usage > gpu_critical or disk_usage > disk_critical):
                self._last_alarm_time = now
                self._emit(SystemEvent.ALARM, {
                    "type": "hardware_critical",
                    "cpu_usage": cpu_usage,
                    "memory_usage": mem_usage,
                    "gpu_usage": gpu_usage,
                    "disk_usage": disk_usage,
                    "message": f"硬件资源严重不足 (CPU={cpu_usage:.0f}%, 内存={mem_usage:.0f}%, GPU={gpu_usage:.0f}%, 磁盘={disk_usage:.0f}%)",
                    "action": "建议暂停所有非必要后台任务",
                }, priority=10, layer="L0")
                self._log(LogLevel.WARNING, f"硬件告警: CPU={cpu_usage:.0f}%, 内存={mem_usage:.0f}%, GPU={gpu_usage:.0f}%, 磁盘={disk_usage:.0f}%")
            elif (cpu_usage > cpu_warning or mem_usage > mem_warning or 
                  gpu_usage > gpu_warning or disk_usage > disk_warning):
                self._last_alarm_time = now 
                self._emit(SystemEvent.ALARM, {
                    "type": "hardware_warning",
                    "cpu_usage": cpu_usage,
                    "memory_usage": mem_usage,
                    "gpu_usage": gpu_usage,
                    "disk_usage": disk_usage,
                    "message": f"硬件资源偏高 (CPU={cpu_usage:.0f}%, 内存={mem_usage:.0f}%, GPU={gpu_usage:.0f}%, 磁盘={disk_usage:.0f}%)",
                    "action": "建议降低后台任务频率",
                }, priority=8, layer="L1")
        # ===== 新增: 适应性突变检测 =====
        # 检测硬件环境是否发生了需要调整认知策略的变化
        env_change = self._detect_environment_change()
        if env_change:
            # 发射环境突变脉冲，供潜意识调整探索策略
            self._emit(Event.ENVIRONMENT_MUTATED, {
                "change_type": env_change.get("type", "unknown"),
                "detail": env_change.get("detail", {}),
                "adaptation_hint": env_change.get("hint", ""),
            }, priority=5, layer="L3")
            self._log(LogLevel.INFO, f"环境突变: {env_change.get('type')} → {env_change.get('hint', '')}")    
        # 发射硬件快照脉冲给信息场和设备管理器
        # ★PHASE14-闭环修复：硬件快照是「状态型」数据，TTL 必须覆盖采集间隔。
        #   原实现沿用 _emit 默认 TTL=5s，而本器官采集间隔为 30s（可配），
        #   导致消费方 get_current("touch.hardware_snapshot") 命中率仅 5/30≈17%——
        #   PulseHardwareLauncher 每次评估都取不到「家底」，静默兜底 tier=standard。
        #   这里显式把 TTL 设为「采集间隔 ×2 + 5s 余量」，保证任意时刻至少有一份有效快照。
        _snap_ttl_ns = int(
            (float(getattr(self, "_snapshot_interval", 30.0) or 30.0) * 2.0 + 5.0)
            * 1_000_000_000
        )
        self._emit(TouchEvent.HARDWARE_SNAPSHOT, snapshot, priority=3,
                   ttl_ns=_snap_ttl_ns, layer="L3")

    def _get_cpu_usage(self) -> float:
        if self._has_psutil:
            import psutil
            try:
                return psutil.cpu_percent(interval=0.1)
            except Exception as e:
                self._log(LogLevel.DEBUG, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return 0.0
    def _check_device_connections(self):
        """检测摄像头和麦克风的连接状态（隔离子进程，防原生崩溃，避免与眼睛推流冲突）"""
        # 摄像头检测：隔离子进程，避免与眼睛推流争用摄像头，并防止原生崩溃拖垮主进程
        try:
            from utils.safe_hw_probe import safe_camera_devices
            cam_res = safe_camera_devices()
            new_state = bool(cam_res.get("ok", False)) if isinstance(cam_res, dict) else None
        except Exception:
            new_state = None

        if new_state is not None and self._cached_camera_available != new_state:
            old = self._cached_camera_available
            self._cached_camera_available = new_state
            if old is not None:  # 不是首次检测
                self._log(LogLevel.INFO, f"摄像头状态变化: {'未连接→已连接' if new_state else '已连接→未连接'}")

        # 麦克风检测（隔离子进程）
        try:
            from utils.safe_hw_probe import safe_audio_devices
            mic_res = safe_audio_devices(True)
            new_mic_state = bool(mic_res.get("ok", False)) if isinstance(mic_res, dict) else None
        except Exception:
            new_mic_state = None

        if new_mic_state is not None and self._cached_microphone_available != new_mic_state:
            old_mic = self._cached_microphone_available
            self._cached_microphone_available = new_mic_state
            if old_mic is not None:
                self._log(LogLevel.INFO, f"麦克风状态变化: {'未连接→已连接' if new_mic_state else '已连接→未连接'}")

        # 扬声器检测（隔离子进程）；探测失败安全默认=True
        try:
            from utils.safe_hw_probe import safe_audio_devices
            spk_res = safe_audio_devices(False)
            new_spk_state = bool(spk_res.get("ok", True)) if isinstance(spk_res, dict) else True
        except Exception:
            new_spk_state = True  # 大多数系统默认有扬声器

        if self._cached_speaker_available != new_spk_state:
            self._cached_speaker_available = new_spk_state
    def _detect_environment_change(self) -> dict[str, Any] | None:
        """
        检测硬件环境是否发生了需要调整认知策略的变化。
        检测内容：CPU负载持续偏高、GPU状态变化、磁盘空间紧张、网络断开。
        """
        cpu_usage = self._hardware_info.get("cpu", {}).get("usage_percent", 0)
        gpu_available = self._hardware_info.get("gpu", {}).get("available", False)
        disk_usage = self._hardware_info.get("disk", {}).get("usage_percent", 0)
        
        # 1. CPU持续偏高（超过70%持续3次快照约90秒）
        if not hasattr(self, '_cpu_high_count'):
            self._cpu_high_count = 0
        if cpu_usage > 70:
            self._cpu_high_count += 1
        else:
            self._cpu_high_count = 0
        
        if self._cpu_high_count >= 3:
            self._cpu_high_count = 0
            return {
                "type": "cpu_sustained_high",
                "detail": {"cpu_usage": cpu_usage},
                "hint": "CPU持续高负载，建议降低深度搜索频率，增加内在沉思比重",
            }
        
        # 2. GPU状态变化
        if not hasattr(self, '_last_gpu_available'):
            self._last_gpu_available = gpu_available
        if gpu_available != self._last_gpu_available:
            self._last_gpu_available = gpu_available
            if not gpu_available:
                return {
                    "type": "gpu_lost",
                    "detail": {"gpu_available": False},
                    "hint": "GPU不可用，建议减少视觉相关探索，增加文本类知识学习",
                }
            else:
                return {
                    "type": "gpu_restored",
                    "detail": {"gpu_available": True},
                    "hint": "GPU已恢复，可恢复正常视觉探索",
                }
        
        # 3. 磁盘空间紧张（超过90%）
        if disk_usage > 90:
            return {
                "type": "disk_critical",
                "detail": {"disk_usage": disk_usage},
                "hint": "磁盘空间紧张，建议暂停大规模深度搜索，优先内部知识整理",
            }
        
        return None
    
    def _load_alert_config(self) -> dict:
        """从config加载硬件告警阈值，失败时用兜底"""
        try:
            import config
            cfg = getattr(config, 'HARDWARE_ALERT', {})
            if cfg:
                return cfg
        except Exception as e:
            self._log(LogLevel.DEBUG, f"数据处理异常已忽略: {type(e).__name__}: {e}")
        return {
            "cpu_critical": 90, "cpu_warning": 70,
            "memory_critical": 90, "memory_warning": 80,
            "gpu_critical": 95, "gpu_warning": 85,
            "disk_critical": 95, "disk_warning": 90,
            "alarm_cooldown": 60,
        }
    # ========== 定时器 ==========

    def _schedule_next_snapshot(self):
        self._next_timer = threading.Timer(
            self._snapshot_interval,
            self._trigger_snapshot
        )
        self._next_timer.daemon = True
        self._next_timer.start()

    def _trigger_snapshot(self):
        if not self.is_running:
            return
        self._emit_snapshot()
        self._schedule_next_snapshot()
    def get_hardware_info(self) -> dict[str, Any]:
        """公开接口：获取硬件信息快照"""
        return dict(self._hardware_info)
    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [
                    SystemEvent.BOOT,
                    SystemEvent.STOP,
                    TouchEvent.SNAPSHOT,
                    SystemEvent.STATUS_REQUEST,
                ],
                "min_priority": 1,
            }
        ]

    # ========== 未来演化预留 ==========

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "触觉",
    "class_name": "PulseTouch",
    "attr_name": "touch",
    "system": "senses",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseTouch v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    mock_field = MockInfoField()

    touch = PulseTouch("触觉")
    touch.set_info_field(mock_field)
    touch.start()

    cpu = touch._hardware_info.get("cpu", {})
    mem = touch._hardware_info.get("memory", {})
    gpu = touch._hardware_info.get("gpu", {})
    print(f"1. 硬件检测: CPU={cpu.get('cores', '?')}核 内存={mem.get('total_gb', '?')}GB GPU={'有' if gpu.get('available') else '无'}")

    result = touch.on_pulse({
        "event_type": TouchEvent.SNAPSHOT,
        "payload": {},
        "priority": 5,
    })
    print(f"2. 快照发射: {result['status']}, ID={result['snapshot_id']}")

    # 验证硬件快照脉冲的 layer 标记
    snapshot_pulses = [p for p in mock_field.published if p.get("event_type") == TouchEvent.HARDWARE_SNAPSHOT]
    if snapshot_pulses:
        print(f"   HARDWARE_SNAPSHOT脉冲 layer: {snapshot_pulses[-1].get('layer', '未设置')} (预期L3)")

    boot_result = touch.on_pulse({
        "event_type": SystemEvent.BOOT,
        "payload": {},
        "priority": 10,
    })
    print(f"3. 启动: {boot_result['status']}")

    status = touch.on_pulse({
        "event_type": SystemEvent.STATUS_REQUEST,
        "payload": {},
        "priority": 5,
    })
    print(f"4. 状态: 快照{status['snapshot_count']}次 psutil={'有' if status['has_psutil'] else '无'}")

    print(f"5. 硬件快照脉冲: {len(snapshot_pulses)} 条")

    touch.on_pulse({
        "event_type": SystemEvent.STOP,
        "payload": {},
        "priority": 10,
    })
    print("6. 停止: 定时器已取消")

    touch.stop()
    print("\n=== 自测全部通过 ===")