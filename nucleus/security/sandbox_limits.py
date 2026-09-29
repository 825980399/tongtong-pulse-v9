# -*- coding: utf-8 -*-
"""
sandbox_limits.py —— 沙箱限制

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 沙箱执行环境的资源与权限限制
机制: 基于SandboxResourceError类实现，包含10个核心方法
定位: 安全治理层
"""

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from nucleus._silent_except import silent_exc


# ==================== 资源上限默认值 ====================
# 数值依据：内部协作者《第八批任务书》8-1 限制参数表。
DEFAULT_CPU_SECONDS = 30        # CPU 时间（非墙上时钟）
DEFAULT_MEMORY_MB = 512         # 地址空间 / 提交内存
DEFAULT_NOFILE = 64             # 文件描述符
DEFAULT_NPROC = 1               # 进程数（防 fork 炸弹）

# 内存轮询间隔（秒）。内部协作者指定 0.5s：再密是空转浪费，再疏会漏掉短命的峰值。
DEFAULT_POLL_INTERVAL = 0.5

# 子进程"因资源超限而死"的约定出口码。
# 选 91/92 的原因：避开 0/1/2（正常/未捕获异常/用法错误）与 120/125/126/127
# （shell 保留码），也避开常见信号码 128+n 区间，便于父进程无歧义识别。
LIMIT_MARKER = "__SANDBOX_LIMIT__"
EXIT_CODE_MEMORY_LIMIT = 91
EXIT_CODE_CPU_LIMIT = 92

# 用户代码可用内置函数的白名单。
# 从 PulseCodeSandbox._run_in_sandbox 原样搬来（S7 批），行为保持不变，
# 只是提到本模块以便 ScriptExecutor（8-2）复用同一份清单。
DEFAULT_ALLOWED_BUILTINS: tuple[str, ...] = (
    "print", "len", "range", "int", "float", "str", "list", "dict",
    "tuple", "set", "bool", "type", "isinstance", "abs", "min", "max",
    "sum", "sorted", "enumerate", "zip", "map", "filter", "round",
    "hex", "oct", "bin", "ord", "chr", "repr", "format", "pow",
    "divmod", "all", "any", "reversed", "slice",
)


# ==================== 异常类型 ====================
class SandboxResourceError(RuntimeError):
    """沙箱资源超限基类。

    同时继承 RuntimeError，使既有的 ``except Exception`` 兜底分支仍能接住，
    不会因本模块引入新异常类型而导致调用方漏捕。
    """

    error_code = "sandbox_resource_limit"

    def __init__(self, detail: str = ""):
        super().__init__(detail or self.__class__.__name__)
        self.detail = detail


class SandboxTimeoutError(SandboxResourceError, TimeoutError):
    """墙上时钟超时。

    刻意**同时**继承 TimeoutError（OSError 子类）：沙箱原有的
    ``except TimeoutError`` 分支（PulseCodeSandbox._execute_code:249）
    能原样接住，计数口径与错误文案都不用改。
    """

    error_code = "sandbox_timeout"


class SandboxCpuLimitError(SandboxTimeoutError):
    """CPU 时间超过 RLIMIT_CPU（仅 Linux/macOS 会触发，Windows 无此限制）。"""

    error_code = "sandbox_cpu_limit"


class SandboxMemoryError(SandboxResourceError):
    """内存超限：Linux 由 RLIMIT_AS 触发 MemoryError，Windows 由看门狗终止。"""

    error_code = "sandbox_memory_limit"


# ==================== 限制参数 ====================
@dataclass(frozen=True)
class SandboxLimits:
    """一次沙箱执行的资源上限。

    ``cpu_seconds`` 与调用方传给 subprocess 的墙上时钟超时是**两个维度**：
    前者只统计真正在 CPU 上跑的时间（``sleep(60)`` 不消耗 CPU 时间），
    后者是真实流逝时间。两者都设，互为兜底。
    """

    cpu_seconds: int = DEFAULT_CPU_SECONDS
    memory_mb: int = DEFAULT_MEMORY_MB
    nofile: int = DEFAULT_NOFILE
    nproc: int = DEFAULT_NPROC

    @property
    def memory_bytes(self) -> int:
        return int(self.memory_mb * 1024 * 1024)


DEFAULT_LIMITS = SandboxLimits()


@dataclass
class SandboxRunResult:
    """一次受限子进程执行的完整结果。"""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    limit_hit: str | None = None      # None / "wall_clock" / "cpu" / "memory"
    peak_memory_bytes: int = 0
    duration_ms: int = 0
    timed_out: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.limit_hit is None and self.returncode == 0


# ==================== 子进程侧：生成限制代码 ====================
def _resource_limits_source(limits: SandboxLimits) -> str:
    """生成"在子进程中设置资源上限"的源码（Linux/macOS 专用）。

    Windows 直接返回空串——那里没有 ``resource`` 模块，改由父进程看门狗兜底。

    三条设计说明：
    1. **每一项单独 try/except**：RLIMIT_NPROC 在部分平台（旧 macOS）不存在，
       RLIMIT_AS 在个别容器环境下设置会失败。逐项隔离，一项失败不影响其余。
    2. **软硬上限都传同一个值**（RLIMIT_CPU 硬上限软上限+1）：硬上限是
       "不可再抬高"的天花板，用户代码若试图反向抬高会被内核拒绝（EPERM）。
    3. **循环体里 import**：resource 只在非 Windows 平台可用，放在 try 内
       即使 ImportError 也不会让子进程崩掉——沙箱降级为"无内核限制"，
       而不是"根本跑不起来"。
    """
    if sys.platform == "win32":
        return ""

    entries = [
        # (常量名, 软上限, 硬上限)
        ("RLIMIT_CPU", limits.cpu_seconds, limits.cpu_seconds + 1),
        ("RLIMIT_AS", limits.memory_bytes, limits.memory_bytes),
        ("RLIMIT_NOFILE", limits.nofile, limits.nofile),
        ("RLIMIT_NPROC", limits.nproc, limits.nproc),
    ]

    lines = [
        "for _lim in " + repr(entries) + ":",
        "    try:",
        "        import resource as _res",
        # getattr 而非直接取属性：常量不存在时抛 AttributeError 被下一行接住
        "        _res.setrlimit(getattr(_res, _lim[0]), (_lim[1], _lim[2]))",
        "    except Exception:",
        "        pass",
    ]
    if entries:
        # 别把循环变量漏到用户代码的模块命名空间里
        lines.append("del _lim")
    return "\n".join(lines) + "\n"


def _cpu_signal_handler_source() -> str:
    """生成 SIGXCPU 处理器的源码，把"CPU 超限被杀"翻译成约定出口码。

    不装处理器的话，内核 SIGXCPU 默认动作是终止进程，父进程只能看到一个
    裸的 -24，无法与"代码自己崩了"区分。装了处理器就能退出成 92 + 打标记。

    整段包在 try/except 里：Windows 的 signal 模块没有 SIGXCPU，
    getattr 抛 AttributeError 被接住，静默跳过。
    """
    return (
        "try:\n"
        "    import signal as _sig\n"
        "    def _sb_on_xcpu(_n, _f):\n"
        f"        sys.stderr.write({LIMIT_MARKER!r} + ':cpu\\n')\n"
        "        sys.stderr.flush()\n"
        f"        os._exit({EXIT_CODE_CPU_LIMIT})\n"
        "    _sig.signal(_sig.SIGXCPU, _sb_on_xcpu)\n"
        "except Exception:\n"
        "    pass\n"
    )


def build_child_wrapper(
    allowed_builtins: Sequence[str] | None = None,
    limits: SandboxLimits | None = None,
    source_label: str = "<sandbox>",
    allow_full_builtins: bool = False,
) -> str:
    """生成子进程包装器源码。

    子进程以 ``python -I -c <本函数返回值> <用户代码文件路径>`` 启动，
    包装器的执行顺序刻意安排为：

        ① 设资源上限  →  ② 装 SIGXCPU 处理器  →  ③ 重建受限 builtins
        →  ④ 读用户代码文件  →  ⑤ exec 用户代码（MemoryError 单独兜底）

    ①必须在⑤之前：否则用户代码有机会在限制生效前先把内存吃掉。

    :param allowed_builtins: 允许用户代码使用的内置函数名白名单
    :param limits: 资源上限；None 表示用 DEFAULT_LIMITS
    :param source_label: 用户代码在 traceback 中显示的文件名
    :param allow_full_builtins: True 时**不重建受限 builtins**，脚本拥有完整
        内置函数与 import 能力。供 ScriptExecutor 这类「允许 import 安全模块」
        的调用方使用；其隔离强度由前置的安全校验（BLOCKING_PATTERNS 阻断
        高危操作）保证，本模块只负责资源上限这一层。
    """
    limits = limits or DEFAULT_LIMITS
    names = list(allowed_builtins if allowed_builtins is not None
                 else DEFAULT_ALLOWED_BUILTINS)

    memory_tail = (
        "except MemoryError:\n"
        f"    sys.stderr.write({LIMIT_MARKER!r} + ':memory\\n')\n"
        "    sys.stderr.flush()\n"
        f"    os._exit({EXIT_CODE_MEMORY_LIMIT})\n"
    )

    if allow_full_builtins:
        # 不重建 __builtins__，exec 使用默认全局命名空间（含完整 builtins 与 import）
        globals_setup = "_g = {'__name__': '__main__'}\n"
    else:
        globals_setup = (
            f"_allowed = {names!r}\n"
            + "_g = {'__builtins__': {n: getattr(_b, n) for n in _allowed},\n"
            + "      '__name__': '__main__'}\n"
        )

    src = (
        "import sys, os, builtins as _b\n"
        + _resource_limits_source(limits)
        + _cpu_signal_handler_source()
        + globals_setup
        + "with open(sys.argv[1], encoding='utf-8') as _f:\n"
        + "    _src = _f.read()\n"
        + "try:\n"
        + f"    exec(compile(_src, {source_label!r}, 'exec'), _g)\n"
        + memory_tail
    )
    return src


# ==================== 父进程侧：内存读取 ====================
if sys.platform == "win32":  # pragma: no cover - 仅在 Windows 上执行
    import ctypes
    from ctypes import wintypes

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        """Win32 PROCESS_MEMORY_COUNTERS 结构体（psapi.h）。"""

        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    # PROCESS_QUERY_LIMITED_INFORMATION：Vista+ 起够用于查询内存计数，
    # 且不需要管理员权限（比 PROCESS_ALL_ACCESS 安全得多）。
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def get_process_memory_bytes(pid: int) -> int | None:
        """读 Windows 进程的内存占用（字节）；读不到返回 None。

        取 **工作集（WorkingSetSize）** 与 **提交内存（PagefileUsage）** 的
        较大值：前者反映物理内存压力，后者反映已提交的虚拟内存。
        只取工作集会漏掉"分配了但还没被访问"的大块内存。
        """
        try:
            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            handle = ctypes.windll.kernel32.OpenProcess(
                _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return None
            try:
                ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                    wintypes.HANDLE(handle), ctypes.byref(counters), counters.cb)
                if not ok:
                    return None
                return max(int(counters.WorkingSetSize),
                           int(counters.PagefileUsage))
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            # 进程刚退出 / 权限不足 / API 不可用，都当作"读不到"
            return None

else:  # Linux / macOS / 其他 POSIX
    def get_process_memory_bytes(pid: int) -> int | None:
        """读进程 RSS（字节）；读不到返回 None。

        Linux 走 /proc/<pid>/status 的 VmRSS。macOS 没有 /proc，会稳定返回
        None，看门狗会自动放弃监控（见 :class:`MemoryWatchdog`）——
        此时内存防线只剩 RLIMIT_AS，这一点在文档里说明，不算静默降级。
        """
        try:
            with open(f"/proc/{int(pid)}/status", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) * 1024
        except Exception:
            return None
        return None


# ==================== 父进程侧：内存看门狗 ====================
class MemoryWatchdog:
    """轮询子进程内存占用，超限就终止它。

    定位：**Windows 上是唯一的内存防线**（没有 RLIMIT_AS）；
    Linux/macOS 上是 setrlimit 的**兜底**（RLIMIT_AS 已由内核强制执行，
    正常情况下看门狗只是安静地记录峰值）。

    线程清理（内部协作者明确要求）：
        - 线程为 daemon，不会拖住解释器退出；
        - :meth:`stop` 置事件后，线程在下一次 ``Event.wait`` 立即返回并退出，
          无需 join（join 最坏要等一个轮询间隔，会白白拖慢每次沙箱执行）；
        - 子进程先结束时，线程在自己的下一拍检测到 ``poll() is not None``
          后自行退出，不留悬挂线程。
    """

    # 连续读不到内存占用多少拍之后放弃监控。
    # 给 6 拍（默认 3 秒）是为了容忍"进程刚 fork、/proc 条目还没就绪"
    # 这类瞬时失败，同时避免在 macOS 上永久空转。
    _GIVE_UP_AFTER_TICKS = 6

    def __init__(
        self,
        proc: subprocess.Popen,
        limit_mb: int = DEFAULT_MEMORY_MB,
        interval: float = DEFAULT_POLL_INTERVAL,
    ):
        self._proc = proc
        self._limit_bytes = int(limit_mb * 1024 * 1024)
        self._interval = max(0.05, float(interval))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.exceeded = False
        self.peak_bytes = 0

    @property
    def enabled(self) -> bool:
        """limit_mb <= 0 视为"不限制内存"，看门狗不开。"""
        return self._limit_bytes > 0

    def start(self) -> "MemoryWatchdog":
        if not self.enabled or self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._run, name="sandbox-mem-watchdog", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()

    def __enter__(self) -> "MemoryWatchdog":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        return False

    def _run(self) -> None:
        unknown_ticks = 0
        while not self._stop_event.wait(self._interval):
            # 子进程已结束 → 线程使命完成，自然退出
            if self._proc.poll() is not None:
                return

            used = get_process_memory_bytes(self._proc.pid)
            if used is None:
                unknown_ticks += 1
                if unknown_ticks >= self._GIVE_UP_AFTER_TICKS:
                    # 该平台读不到内存占用（典型：macOS 无 /proc），放弃监控
                    return
                continue
            unknown_ticks = 0

            self.peak_bytes = max(self.peak_bytes, used)

            if used >= self._limit_bytes:
                self.exceeded = True
                # terminate 而非 kill：给子进程收尾的机会（Windows 上
                # TerminateProcess 是硬杀，但也没有更温和的标准手段）。
                # 主调方在 communicate 返回后还会补一次 kill，确保不留活口。
                try:
                    self._proc.terminate()
                except Exception as e:
                    silent_exc(e, "sandbox_limits:411:子进程终止异常", level="warning")
                return


# ==================== 对外主入口 ====================
def _classify_exit(returncode: int | None, stderr_text: str) -> str | None:
    """判断子进程是否死于资源超限，返回 "cpu"/"memory"/None。

    识别顺序：约定出口码 → stderr 标记 → 信号码。
    stderr 标记作为第二道保险：万一 os._exit 之前标记已写出但进程被别的
    信号带走，出口码就不是 91/92 了，标记还在。
    """
    if returncode == EXIT_CODE_MEMORY_LIMIT:
        return "memory"
    if returncode == EXIT_CODE_CPU_LIMIT:
        return "cpu"

    for line in reversed((stderr_text or "").splitlines()):
        stripped = line.strip()
        if stripped.startswith(LIMIT_MARKER + ":"):
            kind = stripped.split(":", 1)[1].strip()
            if kind in ("cpu", "memory"):
                return kind

    if returncode is not None and returncode < 0:
        # 处理器没装上、或被 SIGKILL 硬杀时，只能靠信号码反推
        if -returncode == int(getattr(signal, "SIGXCPU", -1)):
            return "cpu"
    return None


def execute_code_in_subprocess(
    code: str,
    *,
    timeout_seconds: float,
    limits: SandboxLimits | None = None,
    allowed_builtins: Sequence[str] | None = None,
    child_env: dict[str, str] | None = None,
    cwd: str | None = None,
    source_label: str = "<sandbox>",
    allow_full_builtins: bool = False,
) -> SandboxRunResult:
    """在受限子进程中执行一段 Python 代码。

    整合 8-1 的全部三道防线：
        ① 内核态资源上限（Linux/macOS，包装器内 setrlimit）
        ② 墙上时钟超时（全平台，subprocess timeout + kill）
        ③ 内存看门狗（全平台轮询，Windows 上为主防线）

    :param code: 待执行的 Python 源码
    :param timeout_seconds: 墙上时钟超时（秒）
    :param limits: 资源上限，None 表示用 DEFAULT_LIMITS
    :param allowed_builtins: 内置函数白名单，None 表示用 DEFAULT_ALLOWED_BUILTINS
    :param child_env: 子进程环境变量（建议只给白名单最小集，防凭证泄漏）
    :param cwd: 子进程工作目录，None 表示用临时目录（把写文件也关进笼子）
    :param source_label: 用户代码在 traceback 中显示的文件名
    :param allow_full_builtins: True 时子进程保留完整 builtins 与 import 能力
        （配合调用方的前置安全校验使用，本模块只负责资源上限）
    :return: :class:`SandboxRunResult`，调用方据 ``limit_hit`` 决定抛什么异常
    """
    limits = limits or DEFAULT_LIMITS
    wrapper = build_child_wrapper(
        allowed_builtins, limits, source_label,
        allow_full_builtins=allow_full_builtins,
    )

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tongtong_sandbox_") as tmp_dir:
        code_file = os.path.join(tmp_dir, "user_code.py")
        with open(code_file, "w", encoding="utf-8") as fh:
            fh.write(code)

        timed_out = False
        # -I 隔离模式：忽略 PYTHONPATH 与用户 site-packages，不把框架路径带进去
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", wrapper, code_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd or tmp_dir,
            env=child_env,
        )

        watchdog = MemoryWatchdog(proc, limit_mb=limits.memory_mb)
        with watchdog:
            try:
                out, err = proc.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                # kill 之后必须再 communicate 一次回收管道，否则子进程变僵尸
                out, err = proc.communicate()

        duration_ms = int((time.monotonic() - started) * 1000)

        if watchdog.exceeded:
            # 看门狗判定超限，但子进程可能只是被 terminate 了还没死透
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
            limit_hit: str | None = "memory"
        elif timed_out:
            limit_hit = "wall_clock"
        else:
            limit_hit = _classify_exit(proc.returncode, err or "")

        return SandboxRunResult(
            stdout=out or "",
            stderr=err or "",
            returncode=proc.returncode if proc.returncode is not None else -1,
            limit_hit=limit_hit,
            peak_memory_bytes=watchdog.peak_bytes,
            duration_ms=duration_ms,
            timed_out=timed_out,
        )
