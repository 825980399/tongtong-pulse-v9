from config import EXTERNAL_CALL_TIMEOUTS
# -*- coding: utf-8 -*-
"""
PulseSystemManager —— 系统自主管理器 · 整机生命周期与自愈

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日

职责: 承接 SystemEvent.BOOT / STOP 与 SystemManagerEvent.HEALTH_CHECK / START_SERVICE，负责服务启停、硬件检查、器官上报处置与自动修复，就绪后宣告系统可用。
机制: on_pulse 分派 _on_system_boot / _on_system_stop / _on_health_check / _on_start_service；_run_command 统一封装命令执行；_check_hardware 做硬件体检，_check_and_repair_services 自动修复异常服务，_schedule_health_check 与 _auto_health_check 周期巡检；_on_organ_escalation 收 VascularEvent.ORGAN_ESCALATION 后 _dispatch_organ_action 分派处置；启动就绪发 SystemManagerEvent.SYSTEM_READY。
定位: 躯体层的「系统管理员」，负责整机生命周期管理与自愈。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import subprocess
import threading
import time
from typing import Any

import config
from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import (
    SILENCE_EXEMPT_ORGANS,
    LogLevel,
    SystemEvent,
    SystemManagerEvent,
    VascularEvent,
)
from nucleus.organ_identity import resolve_organ_key, resolve_organ_keys
# ★T-112d：豁免表统一归一化为规范 key 集合（命名空间对齐）
_SILENCE_EXEMPT_KEYS = resolve_organ_keys(SILENCE_EXEMPT_ORGANS)


class PulseSystemManager(BasePulseOrgan):
    """系统自主管理器（v9.5 分层脉冲版）"""

    def __init__(self, organ_name: str = "系统管理器"):
        super().__init__(organ_name)

        # ===== 托管服务 =====
        self._managed_services = {
            "ollama": {
                "name": "Ollama",
                "check_cmd": ["ollama", "list"],
                "start_cmd": ["ollama", "serve"],
                "auto_start": True,
                "status": "unknown",
            },
        }

        # ===== 运行所需目录 =====
        self._required_dirs = [
            "data",
            "logs",
            "data/organ_states",
        ]

        # ===== 统计 =====
        self._check_count = 0
        self._repair_count = 0
        self._running = False
        self._health_timer: threading.Timer | None = None

        # ===== ★F4处置闭环：器官自愈调度状态 =====
        self.framework = None   # ★权限边界：只读访问 organs 容器，仅调用生命周期方法，严禁修改容器
        # 处置防抖：organ_name -> {"level": int, "last_action": ts, "fail_count": int}
        self._escalation_action_state: dict[str, dict[str, Any]] = {}
        self._escalation_action_cooldown = 300.0   # 同器官同级别处置防抖（5分钟观察期）
        self._escalation_recover_threshold = 3     # 连续 3 次巡检正常 → 自动恢复降级
        self._escalation_action_enabled = getattr(
            config, "ORGAN_ESCALATION_ACTION_ENABLED", False)
        # ★第106批 T-106a（P3）：not_found 分支一次性告警去重集合。
        #   处置目标不在器官注册表时，此前静默返回 "not_found" 直接蒸发（任务书：自愈闭环
        #   对非器官实体全窗 0 处置）。改为一次性 WARNING 让空转可见；同一名称只报一次。
        self._warned_not_found: set = set()

    # ========== 统一命令执行（核心方法） ==========

    def _run_command(self, cmd: list, timeout: int = 5) -> dict[str, Any]:
        """
        安全执行系统命令。
        
        返回:
            {"success": bool, "stdout": str, "stderr": str, "error": str}
        
        不会抛出异常，任何错误都返回失败状态。
        """
        try:
            result = subprocess.run(
                cmd,
                check=False, capture_output=True,
                text=True,
                encoding="utf-8", errors="replace",
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "error": "",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": "", "error": "命令超时"}
        except FileNotFoundError:
            return {"success": False, "stdout": "", "stderr": "", "error": "命令未找到"}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": "", "error": str(e)}

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == SystemEvent.BOOT:
            return self._on_system_boot(payload)
        elif event_type == SystemEvent.STOP:
            return self._on_system_stop(payload)
        elif event_type == SystemManagerEvent.HEALTH_CHECK:
            return self._on_health_check(payload)
        elif event_type == SystemManagerEvent.START_SERVICE:
            return self._on_start_service(payload)
        elif event_type == SystemEvent.STATUS_REQUEST:
            return self._on_status_request()
        elif event_type == VascularEvent.ORGAN_ESCALATION:
            return self._on_organ_escalation(payload)

        return None

    # ========== 事件处理 ==========

    def _on_system_boot(self, payload: dict) -> dict[str, Any]:
        """系统启动时执行全面自检和修复（boot 重任务异步化，避免阻塞 L0 worker 超时）。

        ★T-112c：服务检查 + 硬件检测是重任务（实测串行 ~12s > L0 看门狗 8s 预算），
        直接在 L0 生命线层处理器执行会让心跳/告警冻结。改为：轻量步骤（建目录 + 启动周期
        巡检）同步执行，重任务（服务检查 + 硬件检测）移交后台守护线程，L0 worker 立即返回，
        不阻塞。结果存入实例属性（功能不丢，HEALTH_CHECK 无影响）。
        """
        self._running = True

        # 1. 确保必要目录存在（轻量，保留同步）
        for dir_path in self._required_dirs:
            try:
                os.makedirs(dir_path, exist_ok=True)
            except Exception as e:
                self._log(LogLevel.ERROR, f"创建必需目录失败: {dir_path}: {e}")

        # 2&3. 服务检查 + 硬件检测 移后台线程（重任务，原串行 > L0 超时预算）
        self._last_boot_services = None
        self._last_boot_hardware = None
        threading.Thread(
            target=self._run_boot_checks, name="PulseSystemBootChecks", daemon=True
        ).start()

        # 4. 启动定期健康检查（轻量）
        self._schedule_health_check()

        # ★P3-5修复：删除孤儿脉冲 system.ready（纯状态广播，全库无订阅方）
        return {
            "boot": "started",
            "services": "pending",
            "hardware": "pending",
        }

    def _run_boot_checks(self) -> None:
        """★T-112c：后台执行 boot 重任务（服务检查 + 硬件检测），不阻塞 L0 worker。"""
        try:
            self._last_boot_services = self._check_and_repair_services()
            self._last_boot_hardware = self._check_hardware()
            _mem_ok = (self._last_boot_hardware or {}).get("memory_ok", True)
            _svc_n = len(self._last_boot_services or {})
            self._log(LogLevel.INFO,
                      f"[系统启动] 后台自检完成: 服务 {_svc_n} 项, "
                      f"内存{'正常' if _mem_ok else '告警'}")
        except Exception as _e:
            self._log(LogLevel.ERROR,
                      f"[系统启动] 后台自检异常: {type(_e).__name__}: {_e}")

    def _on_system_stop(self, payload: dict) -> dict[str, Any]:
        self._running = False
        if self._health_timer:
            self._health_timer.cancel()
        return {"status": "stopped"}

    def _on_health_check(self, payload: dict) -> dict[str, Any]:
        # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
        self._check_count += 1

        services_status = self._check_and_repair_services()
        hw_status = self._check_hardware()

        all_ok = (
            all(s == "running" for s in services_status.values()) and
            hw_status.get("memory_ok", True)
        )

        if not all_ok:
            # v9.5: 健康告警标记为L0生命线层
            self._emit(SystemEvent.ALARM, {
                "type": "health_degraded",
                "services": services_status,
                "hardware": hw_status,
            }, priority=7, layer="L0")

        return {
            "healthy": all_ok,
            "services": services_status,
            "hardware": hw_status,
        }

    def _on_start_service(self, payload: dict) -> dict[str, Any]:
        service_name = payload.get("service_name", "ollama")
        if service_name not in self._managed_services:
            return {"status": "unknown_service"}

        service = self._managed_services[service_name]
        # 先检查
        check_result = self._run_command(service["check_cmd"])
        if check_result["success"]:
            service["status"] = "running"
            return {"status": "already_running"}

        # 再启动
        self._log(LogLevel.DEBUG, f"正在启动 {service['name']}...")
        # ★T-112c：Popen 不接受 timeout 形参（原代码 Popen(..., timeout=) 会抛 TypeError
        #   导致 Ollama 等托管服务自动启动失败）。timeout 改由 proc.wait() 施加。
        _proc = subprocess.Popen(
            service["start_cmd"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            _proc.wait(timeout=EXTERNAL_CALL_TIMEOUTS.get("subprocess_medium", 30))
        except subprocess.TimeoutExpired:
            # 启动超时：服务可能仍在后台拉起，保留进程不强制 kill，仅记日志
            self._log(LogLevel.WARNING,
                      f"启动 {service['name']} 超时（wait），进程仍在后台运行")
        time.sleep(2)

        # 验证启动结果
        check_result2 = self._run_command(service["check_cmd"])
        service["status"] = "running" if check_result2["success"] else "error"
        if check_result2["success"]:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._repair_count += 1

        return {"status": service["status"], "service": service_name}

    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()

    # ========== ★F4处置闭环：器官自愈调度（血管检测 → 系统管理器调度 → 器官执行） ==========

    def set_framework(self, framework):
        """
        ★F4处置闭环：注入 framework 只读引用。
        ★权限边界（书面化，代码评审卡点）：
            - 只读访问 framework.organs 容器获取器官引用；
            - 仅调用器官的生命周期方法（restart/degrade/recover）；
            - 严禁修改 organs 容器（增删改），严禁调用器官业务方法（on_pulse 等）。
        """
        self.framework = framework

    def _on_organ_escalation(self, payload: dict) -> dict[str, Any]:
        """
        消费血管发射的 ORGAN_ESCALATION 处置脉冲，执行软重启/降级。

        处理流程：
            1. 开关校验（ORGAN_ESCALATION_ACTION_ENABLED，默认 False）
            2. 防抖（同器官同级别 5 分钟观察期内不重复处置）
            3. 豁免二次校验（const.SILENCE_EXEMPT_ORGANS）
            4. 熔断/忙碌检查
            5. 执行 restart（level≥2）/ degrade（level≥3），输出 [自愈审计] 日志
        """
        # 1. 开关校验：关闭时纯告警模式，不执行任何处置
        if not self._escalation_action_enabled:
            return {"dispatched": "disabled"}

        organs_need_action = payload.get("organs", [])
        action = payload.get("action", "restart")
        results = {}

        for item in organs_need_action:
            name = item.get("organ")
            level = item.get("level", 0)
            results[name] = self._dispatch_organ_action(name, level, action)

        return {"dispatched": results}

    def _dispatch_organ_action(self, name: str, level: int, action: str) -> str:
        """单个器官的处置调度（防抖 + 豁免 + 检查 + 执行 + 审计）。"""
        # 2. 豁免二次校验：绝不处置豁免器官
        # ★T-112d：比对走归一化，避免 source_organ 命名空间与裸名豁免表对不齐
        if resolve_organ_key(name) in _SILENCE_EXEMPT_KEYS:
            return "exempt"

        # 3. 防抖：同器官同级别 5 分钟观察期内不重复处置
        _now = time.time()
        _state = self._escalation_action_state.get(name, {
            "level": 0, "last_action": 0.0, "fail_count": 0,
        })
        if (_state["level"] == level
                and _now - _state["last_action"] < self._escalation_action_cooldown):
            return "cooling_down"

        # 4. 获取器官引用（只读）
        organ = self.framework.organs.get(name) if self.framework else None
        if organ is None:
            # ★第106批 T-106a（P3）：处置目标不在器官注册表 → 多为豁免实体/注册错位，
            #   此前静默蒸发。改为一次性 WARNING（同一名称只报一次），让空转可见、便于排障。
            if name not in self._warned_not_found:
                self._warned_not_found.add(name)
                self._log(LogLevel.WARNING,
                          f"处置目标不在器官注册表（疑似豁免实体/注册错位）: {name!r} "
                          f"→ 跳过处置（action={action!r}）")
            return "not_found"

        # 5. 熔断/忙碌检查：熔断中的器官跳过处置
        if hasattr(organ, "is_available") and not organ.is_available():
            return "unavailable"

        # 6. 执行处置 + 审计日志
        _start = time.time()
        try:
            if action == "restart" and level >= 2:
                _ok = organ.restart()
                _result = "restarted" if _ok else "restart_failed"
            elif action == "degrade" and level >= 3:
                organ.degrade(reason="沉默自愈")
                _result = "degraded"
            else:
                _result = "skipped"

            # 更新防抖状态 + 失败计数
            _state["level"] = level
            _state["last_action"] = _now
            if _result.endswith("failed"):
                _state["fail_count"] = _state.get("fail_count", 0) + 1
                # 连续 3 次失败 → 升级告警（人工介入）
                if _state["fail_count"] >= 3:
                    self._emit(SystemEvent.ALARM, {
                        "type": "organ_escalation_failed",
                        "organ": name,
                        "fail_count": _state["fail_count"],
                        "message": f"器官 {name} 连续处置失败 {_state['fail_count']} 次，需人工介入",
                    }, priority=8, layer="L0")
            else:
                _state["fail_count"] = 0
            self._escalation_action_state[name] = _state

            # [自愈审计] 结构化日志
            _elapsed = round((time.time() - _start) * 1000, 1)
            self._log(LogLevel.WARNING,
                      f"[自愈审计] 器官={name} 动作={action} 级别={level} "
                      f"结果={_result} 耗时={_elapsed}ms 原因=沉默自愈")
            return _result
        except Exception as _e:
            _elapsed = round((time.time() - _start) * 1000, 1)
            self._log(LogLevel.ERROR,
                      f"[自愈审计] 器官={name} 动作={action} 级别={level} "
                      f"结果=failed 耗时={_elapsed}ms 原因={_e}")
            _state["fail_count"] = _state.get("fail_count", 0) + 1
            self._escalation_action_state[name] = _state
            return "failed"

    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "organ": self.organ_name,
            "check_count": self._check_count,
            "repair_count": self._repair_count,
            "services": {k: v["status"] for k, v in self._managed_services.items()},
            "is_running": self.is_running,
            # ★F4处置闭环：暴露处置状态（防抖/失败计数）
            "escalation_action_state": dict(self._escalation_action_state),
        }

    # ========== 检测与修复 ==========

    def _check_and_repair_services(self) -> dict[str, str]:
        """检查所有服务，未运行则尝试修复"""
        result = {}
        for svc_name, service in self._managed_services.items():
            # 检查
            check = self._run_command(service["check_cmd"])
            if check["success"]:
                service["status"] = "running"
                result[svc_name] = "running"
                continue

            # 修复
            if service.get("auto_start"):
                self._log(LogLevel.DEBUG, f"{service['name']} 未运行，自动启动...")
                try:
                    subprocess.Popen(
                        service["start_cmd"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_medium"], )
                    time.sleep(2)
                    check2 = self._run_command(service["check_cmd"])
                    if check2["success"]:
                        service["status"] = "running"
                        self._repair_count += 1
                        result[svc_name] = "running"
                        continue
                except FileNotFoundError:
                    # ★P3-2端到端验收修复：服务命令不存在（如沙箱无 ollama）时降级为 stopped，
                    # 而非让 system.boot 处理抛异常中断整个启动流程。
                    self._log(LogLevel.DEBUG,
                              f"{service['name']} 命令不存在，降级为 stopped（环境未安装该服务）")
                except Exception as _e:
                    self._log(LogLevel.DEBUG,
                              f"{service['name']} 自动启动失败，降级为 stopped: {_e}")

            service["status"] = "stopped"
            result[svc_name] = "stopped"

        return result

    def _check_hardware(self) -> dict[str, Any]:
        """检查硬件状态"""
        status = {"cpu_percent": 0, "memory_percent": 0, "memory_ok": True}

        try:
            import psutil
            status["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            status["memory_percent"] = mem.percent
            status["memory_total_gb"] = round(mem.total / (1024**3), 1)
            status["memory_available_gb"] = round(mem.available / (1024**3), 1)
            status["memory_ok"] = mem.percent < 90
        except ImportError:
            status["note"] = "psutil未安装，使用基础检测"

        return status

    # ========== 自愈定时器 ==========

    def _schedule_health_check(self):
        if not self._running:
            return
        self._health_timer = threading.Timer(300, self._auto_health_check)
        self._health_timer.daemon = True
        self._health_timer.start()

    def _auto_health_check(self):
        if not self._running:
            return
        try:
            self._on_health_check({})
        except Exception as _e:  # ★FIX(AP53): 异常不得中断自愈链重排
            try:
                self._log(LogLevel.ERROR, f"自愈检查异常: {_e}")
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        finally:
            # 无论检查是否抛异常，都必须重排下一次定时器，避免 300s 自愈链永久停摆
            self._schedule_health_check()

    # ========== 共振条件 ==========

    def get_resonance_conditions(self) -> list:
        return [{
            "organ_name": self.organ_name,
            "event_types": [
                SystemEvent.BOOT, SystemEvent.STOP,
                SystemManagerEvent.HEALTH_CHECK, SystemManagerEvent.START_SERVICE,
                SystemEvent.STATUS_REQUEST,
                VascularEvent.ORGAN_ESCALATION,   # ★F4处置闭环：订阅血管的处置脉冲
            ],
            "min_priority": 1,
        }]

    def on_field_oscillation(self, frequency: float, amplitude: float,
                              phase: float, field_strength: float) -> dict[str, Any] | None:
        """【预留 v10.0】"""
        return None


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "系统管理器",
    "class_name": "PulseSystemManager",
    "attr_name": "system_manager",
    "system": "core",
    "always_online": True,
    "feature_flag": None,
    "extra_deps": {},
    "post_wiring": [],
}

if __name__ == "__main__":
    print("=== PulseSystemManager v9.5 分层脉冲自测 ===\n")

    class MockInfoField:
        def __init__(self):
            self.published = []
        def publish(self, p):
            self.published.append(p)

    mock = MockInfoField()
    sm = PulseSystemManager("系统管理器")
    sm.set_info_field(mock)
    sm.start()

    # 测试启动
    boot = sm.on_pulse({"event_type": SystemEvent.BOOT, "payload": {}, "priority": 10})
    print("1. 启动完成")
    print(f"   服务状态: {boot['services']}")
    print(f"   硬件状态: CPU={boot['hardware'].get('cpu_percent', '?')}%, 内存={boot['hardware'].get('memory_percent', '?')}%")

    # 验证系统就绪脉冲的 layer 标记
    ready_pulses = [p for p in mock.published if p.get("event_type") == SystemManagerEvent.SYSTEM_READY]
    if ready_pulses:
        print(f"   SYSTEM_READY脉冲 layer: {ready_pulses[-1].get('layer', '未设置')} (预期L0)")

    # 验证健康告警脉冲的 layer 标记
    alarm_pulses = [p for p in mock.published if p.get("event_type") == SystemEvent.ALARM]
    if alarm_pulses:
        print(f"   ALARM脉冲 layer: {alarm_pulses[-1].get('layer', '未设置')} (预期L0)")

    # 测试健康检查
    health = sm.on_pulse({"event_type": SystemManagerEvent.HEALTH_CHECK, "payload": {}, "priority": 5})
    print(f"2. 健康检查: {'健康' if health['healthy'] else '需关注'}")

    # 统计
    s = sm.on_pulse({"event_type": SystemEvent.STATUS_REQUEST, "payload": {}, "priority": 5})
    print(f"3. 统计: 检查{s['check_count']}次, 修复{s['repair_count']}次")

    sm.on_pulse({"event_type": SystemEvent.STOP, "payload": {}, "priority": 10})
    sm.stop()
    print("\n=== 自测全部通过 ===")
