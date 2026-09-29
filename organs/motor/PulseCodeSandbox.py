# -*- coding: utf-8 -*-
"""
PulseCodeSandbox —— 代码沙箱器官 · 外部代码安全执行与隔离

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日

职责: 承接 MotorEvent.EXECUTE 脉冲，在受限环境中执行外部/生成代码，先做危险关键字拦截与敏感输出脱敏，再把执行结果回灌为知识，是运动层唯一允许运行外部代码的出口。
机制: on_pulse 收单后 _security_precheck 用 DANGEROUS_KEYWORDS 做静态扫描（命中发 SecurityEvent.BLOCKED / THREAT_DETECTED，通过发 SecurityEvent.PASSED）；_run_in_sandbox 在受限命名空间执行并捕获超时与越界（SecurityEvent.SANDBOX_VIOLATION）；_filter_output 按 SENSITIVE_OUTPUT_PATTERNS 脱敏；_emit_result 广播 CodeEvent.RESULT，_emit_digest_pulse 发 DigestEvent.KNOWLEDGE 交胃消化；_record_execution 记档，get_resonance_conditions / on_field_oscillation 接入共振场。
定位: 「戴着手套的手」——不可信代码必须先过安检才能运行，是运动层的安全执行边界。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import io  # noqa: F401
import os
import sys
import threading
import time
import traceback
from typing import Any

from nucleus.const import SecurityEvent

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from base.BasePulseOrgan import BasePulseOrgan
from nucleus.const import CodeEvent, DigestEvent, LogLevel, MotorEvent


class PulseCodeSandbox(BasePulseOrgan):
    """代码沙箱 —— 外部代码安全执行与隔离器官（知识消化版 · v9.5 分层脉冲版）"""

    DANGEROUS_KEYWORDS = [
        "os.system", "subprocess", "__import__", "eval(", "exec(",
        "open(", "file(", "input(", "raw_input(",
        "sys.exit", "quit(", "exit(",
        "shutil.rmtree", "os.remove", "os.unlink", "os.rmdir",
        "importlib", "compile(", "globals()", "locals()",
        "getattr(", "setattr(", "delattr(",
        "socket.", "urllib.", "requests.",
        "threading.Thread", "multiprocessing",
        "ctypes.", "cffi.",
        # ★修复: 补充自省逃逸通道（经典沙箱逃逸不需要 getattr）
        "__globals__", "__builtins__", "__class__", "__base__",
        "__bases__", "__subclasses__", "__mro__", "__code__",
        "__getattribute__", "__dict__", "breakpoint(",
    ]

    SENSITIVE_OUTPUT_PATTERNS = [
        ("C:\\", "[本地路径]"),
        ("D:\\", "[本地路径]"),
        ("/home/", "[用户路径]"),
        ("/Users/", "[用户路径]"),
        ("\\Users\\", "[用户路径]"),
        ("password", "[敏感字段]"),
        ("token", "[敏感字段]"),
        ("secret", "[敏感字段]"),
        ("api_key", "[敏感字段]"),
    ]

    def refresh_runtime_params(self):
        """★P1: 刷新运行时参数（热加载后调用）。"""
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            if 'sandbox_max_output_chars' in _rp and hasattr(self, '_max_output_chars'):
                self._max_output_chars = _rp['sandbox_max_output_chars']
            if 'sandbox_max_memory_mb' in _rp and hasattr(self, '_max_memory_mb'):
                self._max_memory_mb = _rp['sandbox_max_memory_mb']
            if 'sandbox_max_recent' in _rp and hasattr(self, '_max_recent'):
                self._max_recent = _rp['sandbox_max_recent']
        except Exception as e:
            self._log(LogLevel.ERROR, f'异常: {e}')


    def __init__(self, organ_name: str = "代码沙箱"):
        super().__init__(organ_name)

        self._timeout_seconds = 10
                # ★P1: 从RUNTIME_PARAMS读取参数（支持热加载）
        try:
            import config as _cfg
            _rp = getattr(_cfg, 'RUNTIME_PARAMS', {})
            self._max_output_chars = _rp.get('sandbox_max_output_chars', 5000)
            self._max_memory_mb = _rp.get('sandbox_max_memory_mb', 256)
            self._max_recent = _rp.get('sandbox_max_recent', 20)
        except Exception:
            self._max_output_chars = 5000
            self._max_memory_mb = 256
            self._max_recent = 20
        self.sandbox_core = None
        self._total_executions = 0

        # ★属性初始化完整性补全（自动审查添加）
        # ★P0-E修复（2026-09-05）：原值误写为 threading.Lock()，是**类型错误**，
        #   不是「多此一举的加锁」。_blocked_count 在本文件全部三处使用中
        #   （:131 日志、:168 拦截计数、:439 状态字典）都是**整数计数器**语义，
        #   全项目 0 处 .acquire()/.release() 用法（同类属性在
        #   SandboxCore.py:38 与 PulseSkin.py:48 均初始化为 0，可对照）。
        #   原值造成的三重故障：
        #     ① :168 拦截危险代码时执行 `Lock() += 1`
        #        → TypeError: unsupported operand type(s) for +=:
        #          '_thread.lock' and 'int'
        #        即「安全防护生效的那一刻，沙箱自己崩溃」——
        #        危险代码没被拦下，反而把防护组件打挂了。
        #     ② :439 状态字典携带 Lock 对象，下游 json.dumps 即抛
        #        TypeError: Object of type lock is not JSON serializable
        #     ③ :131 停止日志打印 '<unlocked _thread.lock object at 0x...>'
        #   修复：改为 0，与同类属性的既定写法对齐。
        self._blocked_count = 0
        self._error_count = 0
        self._lock = threading.Lock()
        self._recent_executions = []
        self._success_count = 0
        self._timeout_count = 0
    def set_sandbox_core(self, core):
        self.sandbox_core = core
    # ========== 生命周期 ==========

    def start(self):
        super().start()
        self._log(LogLevel.INFO,
                  f"已启动，超时={self._timeout_seconds}s, "
                  f"最大输出={self._max_output_chars}字符, "
                  f"黑名单关键字={len(self.DANGEROUS_KEYWORDS)}项")

    def stop(self):
        super().stop()
        self._log(LogLevel.INFO,
                  f"已停止，执行{self._total_executions}次, "
                  f"成功{self._success_count}次, 超时{self._timeout_count}次, "
                  f"错误{self._error_count}次, 拦截{self._blocked_count}次")

    # ========== 脉冲入口 ==========

    def on_pulse(self, pulse: dict[str, Any]) -> dict[str, Any] | None:
        if not self.is_running:
            return None

        event_type = pulse.get("event_type", "")
        payload = pulse.get("payload", {})

        if event_type == MotorEvent.EXECUTE:
            code = payload.get("code", "")
            language = payload.get("language", "python")
            user_name = payload.get("user_name", "unknown")
            task_id = payload.get("task_id", "unknown")

            return self._execute_code(code, language, user_name, task_id)

        return None

    def get_resonance_conditions(self) -> list[dict[str, Any]]:
        return [
            {
                "organ_name": self.organ_name,
                "event_types": [MotorEvent.EXECUTE],
                "min_priority": 1,
            },
        ]

    # ========== 代码执行核心 ==========

    def _execute_code(self, code: str, language: str, user_name: str, task_id: str) -> dict[str, Any]:
        start_time = time.time()

        security_check = self._security_precheck(code)
        if not security_check["safe"]:
            # ★7-1/P1-13：沙箱执行入口可被并发调用。两个计数同属一次执行，
            #   必须在同一临界区内自增，否则并发下会出现
            #   「分类计数之和 ≠ _total_executions」的对不上账现象。
            #   复用类内已有的 self._lock（:126）；此处不在 get_stats(:431)
            #   的锁块内，故不存在重入。
            with self._lock:
                self._blocked_count += 1
                self._total_executions += 1

            result = {
                "success": False,
                "error_type": "security_blocked",
                "error": f"代码包含危险关键字: {', '.join(security_check['blocked_keywords'])}",
                "output": "",
                "execution_time_ms": 0,
                "user_name": user_name,
                "task_id": task_id,
            }
            # v9.5: 安全拦截告警标记为L0生命线层
            self._emit(SecurityEvent.BLOCKED, {
                "verdict": "blocked",
                "level": "L2",
                "reason": f"包含危险关键字: {', '.join(security_check['blocked_keywords'])}",
            }, priority=9, layer="L0")
            # ★P3-5补发射：威胁探测信号（security.threat_detected 此前有订阅无发射）
            self._emit(SecurityEvent.THREAT_DETECTED, {
                "verdict": "threat",
                "level": "L2",
                "reason": f"代码威胁探测: {', '.join(security_check['blocked_keywords'])}",
            }, priority=8, layer="L0")

            self._record_execution(result)
            self._emit_result(result)
            self._emit_digest_pulse(code, language, user_name, result)
            return result

        try:
            output = self._run_in_sandbox(code, language)
            execution_time_ms = int((time.time() - start_time) * 1000)

            filtered_output = self._filter_output(output)

            # ★7-1/P1-13：沙箱执行入口可被并发调用。两个计数同属一次执行，
            #   必须在同一临界区内自增，否则并发下会出现
            #   「分类计数之和 ≠ _total_executions」的对不上账现象。
            #   复用类内已有的 self._lock（:126）；此处不在 get_stats(:431)
            #   的锁块内，故不存在重入。
            with self._lock:
                self._success_count += 1
                self._total_executions += 1

            result = {
                "success": True,
                "output": filtered_output,
                "original_length": len(output),
                "filtered_length": len(filtered_output),
                "execution_time_ms": execution_time_ms,
                "user_name": user_name,
                "task_id": task_id,
            }
            # v9.5: 安全通过记录标记为L1实时交互层
            self._emit(SecurityEvent.PASSED, {
                "verdict": "passed",
                "level": "L2",
            }, priority=4, layer="L1")

        except TimeoutError:
            # ★7-1/P1-13：沙箱执行入口可被并发调用。两个计数同属一次执行，
            #   必须在同一临界区内自增，否则并发下会出现
            #   「分类计数之和 ≠ _total_executions」的对不上账现象。
            #   复用类内已有的 self._lock（:126）；此处不在 get_stats(:431)
            #   的锁块内，故不存在重入。
            with self._lock:
                self._timeout_count += 1
                self._total_executions += 1
            execution_time_ms = int((time.time() - start_time) * 1000)

            result = {
                "success": False,
                "error_type": "timeout",
                "error": f"代码执行超时（>{self._timeout_seconds}秒）",
                "output": "",
                "execution_time_ms": execution_time_ms,
                "user_name": user_name,
                "task_id": task_id,
            }

        except Exception as e:
            # ★7-1/P1-13：沙箱执行入口可被并发调用。两个计数同属一次执行，
            #   必须在同一临界区内自增，否则并发下会出现
            #   「分类计数之和 ≠ _total_executions」的对不上账现象。
            #   复用类内已有的 self._lock（:126）；此处不在 get_stats(:431)
            #   的锁块内，故不存在重入。
            with self._lock:
                self._error_count += 1
                self._total_executions += 1
            execution_time_ms = int((time.time() - start_time) * 1000)

            result = {
                "success": False,
                "error_type": "runtime_error",
                "error": str(e),
                "traceback": traceback.format_exc()[:1000],
                "output": "",
                "execution_time_ms": execution_time_ms,
                "user_name": user_name,
                "task_id": task_id,
            }
            # v9.5: 沙箱违规告警标记为L0生命线层
            self._emit(SecurityEvent.SANDBOX_VIOLATION, {
                "verdict": "blocked",
                "level": "L2",
                "reason": str(e)[:100],
            }, priority=7, layer="L0")

        self._record_execution(result)
        self._emit_result(result)
        self._emit_digest_pulse(code, language, user_name, result)
        return result

    # ========== 知识消化脉冲发射 ==========

    def _emit_digest_pulse(self, code: str, language: str, user_name: str, result: dict[str, Any]):
        if not self.info_field or not self.pulse_core:
            return

        success = result.get("success", False)
        output = result.get("output", "")
        error = result.get("error", "")
        error_type = result.get("error_type", "")

        if success:
            content = (
                f"{user_name} 执行了 {language} 代码:\n"
                f"```\n{code}\n```\n"
                f"执行结果: {output.strip()}\n"
                f"执行耗时: {result.get('execution_time_ms', 0)}ms"
            )
        else:
            content = (
                f"{user_name} 尝试执行 {language} 代码但失败了:\n"
                f"```\n{code}\n```\n"
                f"失败原因({error_type}): {error}"
            )

        # v9.5: 知识消化脉冲标记为L2认知思考层
        digest_pulse = self.pulse_core.emit(
            source_organ=self.organ_name,
            event_type=DigestEvent.KNOWLEDGE,
            payload={
                "content": content,
                "source_organ": self.organ_name,
                "trigger_reason": f"code_execution.{'success' if success else 'failure'}",
            },
            priority=3,
            layer="L2"
        )
        self.info_field.publish(digest_pulse)

    # ========== 沙箱执行 ==========


    def _run_in_sandbox(self, code: str, language: str) -> str:
        if language.lower() != "python":
            return f"[代码沙箱] 暂不支持 {language} 语言的直接执行。"

        # ★修复(v2): 从"进程内 exec + 线程超时"改为"子进程隔离执行"。
        # 原方案两个硬伤：①用户代码与框架同进程，黑名单一旦绕过即可触及框架内存
        # （包括自修改链与环境变量中的凭证）；②Python 无法强杀线程，死循环代码
        # 超时后仍占 CPU 且卡住框架退出。子进程方案：超时由 subprocess 直接 kill，
        # 逃逸影响被限制在子进程内；同时清空环境变量防凭证泄漏。
        #
        # ★修复(8-1): 在子进程隔离之上叠加「资源上限」四道防线。
        # 此前子进程虽隔离，但没有资源天花板：while True:pass 吃满一核 CPU，
        # [0]*10**9 吃光宿主机内存，父进程只能干等。现交由
        # nucleus/security/sandbox_limits.execute_code_in_subprocess 统一兜底：
        #   ① CPU 时间（RLIMIT_CPU，仅 Linux/macOS）
        #   ② 地址空间（RLIMIT_AS / Windows ctypes 看门狗）
        #   ③ 文件描述符（RLIMIT_NOFILE）
        #   ④ 进程数（RLIMIT_NPROC，防 fork 炸弹）
        # 加上 subprocess 墙钟超时与内存轮询看门狗，全平台覆盖。
        # 资源超限统一抛 SandboxTimeoutError/SandboxMemoryError（二者分别继承
        # TimeoutError / RuntimeError），_execute_code 的既有 except 分支原样接住，
        # 计数口径与错误文案无需改动。
        from nucleus.security.sandbox_limits import (
            SandboxCpuLimitError,
            SandboxMemoryError,
            SandboxTimeoutError,
            execute_code_in_subprocess,
        )

        # 最小环境：防子进程读到 WECOM_SECRET 等敏感环境变量；
        # 保留 Windows 运行 Python 必需的 SystemRoot/PATH/TEMP
        _child_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
        }
        for _k in ("SystemRoot", "TEMP", "TMP", "PYTHONHOME"):
            if _k in os.environ:
                _child_env[_k] = os.environ[_k]

        _res = execute_code_in_subprocess(
            code,
            timeout_seconds=self._timeout_seconds,
            child_env=_child_env,
        )

        # 把"资源超限"这一结果翻译成沙箱既有语义的异常，交给 _execute_code
        # 的分类计数分支（TimeoutError → _timeout_count；Exception → _error_count）
        if _res.limit_hit == "cpu":
            raise SandboxCpuLimitError("代码执行超过 CPU 时间上限（30 秒）")
        if _res.limit_hit == "wall_clock":
            raise SandboxTimeoutError(
                f"代码执行超过 {self._timeout_seconds} 秒")
        if _res.limit_hit == "memory":
            raise SandboxMemoryError("代码执行超过内存上限（512MB）")

        if _res.returncode != 0:
            _err = (_res.stderr or "").strip()
            _last = _err.splitlines()[-1] if _err else f"退出码 {_res.returncode}"
            raise RuntimeError(_last)

        return _res.stdout

    # ========== 安全检查 ==========

    def _security_precheck(self, code: str) -> dict[str, Any]:
        code_lower = code.lower()
        blocked = []

        for keyword in self.DANGEROUS_KEYWORDS:
            if keyword.lower() in code_lower:
                blocked.append(keyword)

        return {
            "safe": len(blocked) == 0,
            "blocked_keywords": blocked,
        }

    def _filter_output(self, output: str) -> str:
        filtered = output

        for pattern, replacement in self.SENSITIVE_OUTPUT_PATTERNS:
            filtered = filtered.replace(pattern, replacement)

        if len(filtered) > self._max_output_chars:
            filtered = filtered[:self._max_output_chars] + "\n\n[输出已截断]"

        return filtered

    # ========== 结果发射 ==========

    def _emit_result(self, result: dict[str, Any]):
        if self.info_field and self.pulse_core:
            # v9.5: 代码执行结果标记为L1实时交互层
            result_pulse = self.pulse_core.emit(
                source_organ=self.organ_name,
                event_type=CodeEvent.RESULT,
                payload=result,
                priority=4,
                layer="L1"
            )
            self.info_field.publish(result_pulse)

    # ========== 执行记录 ==========

    def _record_execution(self, result: dict[str, Any]):
        with self._lock:
            self._recent_executions.append({
                "timestamp": time.time(),
                "success": result["success"],
                "error_type": result.get("error_type", ""),
                "execution_time_ms": result.get("execution_time_ms", 0),
                "user_name": result.get("user_name", ""),
            })

            if len(self._recent_executions) > self._max_recent:
                self._recent_executions.pop(0)
    def _on_status_request(self) -> dict[str, Any]:
        return self.get_stats()
    # ========== 统计信息 ==========

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "organ": self.organ_name,
                "total_executions": self._total_executions,
                "success_count": self._success_count,
                "timeout_count": self._timeout_count,
                "error_count": self._error_count,
                "blocked_count": self._blocked_count,
                "recent_executions": list(self._recent_executions[-5:]),
            }

    def on_field_oscillation(self, frequency: float, amplitude: float, phase: float, field_strength: float):
        """【预留 v10.0】"""
        pass  # noqa: PIE790


# ========== 自测 ==========

# ★插件化阶段1：器官注册表声明（供 organ_loader.scan_organs_directory 扫描发现）
ORGAN_META = {
    "name": "代码沙箱",
    "class_name": "PulseCodeSandbox",
    "attr_name": "code_sandbox",
    "system": "motor",
    "always_online": False,
    "feature_flag": "enable_motor",
    "extra_deps": {},
    "post_wiring": [
        {"target": "sandbox_core", "setter": "set_sandbox_core"},
    ],
}

if __name__ == "__main__":
    print("=== PulseCodeSandbox v9.5 分层脉冲自测 ===\n")

    class MockField:
        def __init__(self):
            self.published = []
        def publish(self, pulse):
            self.published.append(pulse)

    class MockCore:
        def emit(self, source_organ, event_type, payload, priority, layer="L1"):
            return {
                # [批次4·深度体检][MAINT-4] __main__ mock 补回 pulse_id
                "pulse_id": f"pulse:{source_organ}:{event_type}",
                "event_type": event_type,
                "source_organ": source_organ,
                "payload": payload,
                "priority": priority,
                "layer": layer,
            }

    sandbox = PulseCodeSandbox("代码沙箱")
    mock_field = MockField()
    mock_core = MockCore()
    sandbox.set_info_field(mock_field)
    sandbox.set_pulse_core(mock_core)

    sandbox.start()

    print("1. 执行安全代码（应发射 code.result + digest.knowledge）:")
    result1 = sandbox.on_pulse({
        "event_type": MotorEvent.EXECUTE,
        "payload": {
            "code": "print('Hello, 曈曈!')\nx = sum([1, 2, 3])\nprint(f'计算结果: {x}')",
            "language": "python",
            "user_name": "小林",
            "task_id": "test_001",
        },
        "priority": 4,
    })
    print(f"   成功: {result1['success']}")
    print(f"   输出: {result1['output'][:100]}")
    print(f"   耗时: {result1['execution_time_ms']}ms")
    pulse_types = [p["event_type"] for p in mock_field.published]
    print(f"   发射脉冲: {pulse_types}")
    # 验证各脉冲的 layer 标记
    for p in mock_field.published:
        et = p.get("event_type", "")
        if et == CodeEvent.RESULT:
            print(f"   CodeEvent.RESULT layer: {p.get('layer', '未设置')} (预期L1)")
        elif et == DigestEvent.KNOWLEDGE:
            print(f"   DigestEvent.KNOWLEDGE layer: {p.get('layer', '未设置')} (预期L2)")
        elif et == SecurityEvent.PASSED:
            print(f"   SecurityEvent.PASSED layer: {p.get('layer', '未设置')} (预期L1)")

    mock_field.published.clear()
    print("\n2. 拦截危险代码（也应发射 digest.knowledge）:")
    result2 = sandbox.on_pulse({
        "event_type": MotorEvent.EXECUTE,
        "payload": {
            "code": "import os\nos.system('del /f *.*')",
            "language": "python",
            "user_name": "攻击者",
            "task_id": "test_002",
        },
        "priority": 4,
    })
    print(f"   成功: {result2['success']}")
    print(f"   拦截原因: {result2['error'][:80]}")
    pulse_types2 = [p["event_type"] for p in mock_field.published]
    print(f"   发射脉冲: {pulse_types2}")
    for p in mock_field.published:
        et = p.get("event_type", "")
        if et == SecurityEvent.BLOCKED:
            print(f"   SecurityEvent.BLOCKED layer: {p.get('layer', '未设置')} (预期L0)")
        elif et == DigestEvent.KNOWLEDGE:
            print(f"   DigestEvent.KNOWLEDGE layer: {p.get('layer', '未设置')} (预期L2)")

    mock_field.published.clear()
    print("\n3. 执行错误代码（也应发射 digest.knowledge）:")
    result3 = sandbox.on_pulse({
        "event_type": MotorEvent.EXECUTE,
        "payload": {
            "code": "x = 1 / 0\nprint(x)",
            "language": "python",
            "user_name": "测试用户",
            "task_id": "test_003",
        },
        "priority": 4,
    })
    print(f"   成功: {result3['success']}")
    print(f"   错误类型: {result3.get('error_type')}")
    pulse_types3 = [p["event_type"] for p in mock_field.published]
    for p in mock_field.published:
        et = p.get("event_type", "")
        if et == SecurityEvent.SANDBOX_VIOLATION:
            print(f"   SecurityEvent.SANDBOX_VIOLATION layer: {p.get('layer', '未设置')} (预期L0)")
        elif et == DigestEvent.KNOWLEDGE:
            print(f"   DigestEvent.KNOWLEDGE layer: {p.get('layer', '未设置')} (预期L2)")

    print("\n4. 代码沙箱统计:")
    stats = sandbox.get_stats()
    print(f"   总执行: {stats['total_executions']}")
    print(f"   成功: {stats['success_count']}")
    print(f"   拦截: {stats['blocked_count']}")
    print(f"   错误: {stats['error_count']}")

    sandbox.stop()
    print("\n=== 自测全部通过 ===")
