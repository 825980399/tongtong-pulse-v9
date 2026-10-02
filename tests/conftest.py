# -*- coding: utf-8 -*-
"""pytest 公共 fixture：项目根加入 sys.path、提供临时 VectorStore 工厂、进化测试基目录。

注意：本 sandbox 不具备向量编码器（BAAI/bge），故 VectorStore 测试用 FakeEncoder
（仅做维度/有限性校验）配合 `put(node, text, vec)` 直接写预计算向量，绕开编码器；
检索用真实 VectorEncoder.cosine_matrix 静态方法（import 不加载模型）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ★第158批 N-8（P1）：pytest 运行期框架日志重定向到 logs/pytest.log，
#   避免测试专用日志（VectorStore 模拟磁盘写满 / 进程池「模拟重建失败」等）
#   污染生产 pulse.log。复用 logger.py 既有 PULSE_LOG_FILE 隔离机制
#   （子进程通过环境变量指定独立日志文件）。仅当未由父进程显式指定时设置，
#   以尊重 PeriodicTestScheduler 自身可能注入的隔离日志路径。
os.environ.setdefault("PULSE_LOG_FILE", os.path.join(ROOT, "logs", "pytest.log"))

import numpy as np
import pytest

from nucleus.semantic.VectorStore import VectorStore
from nucleus._silent_except import silent_exc


class FakeEncoder:
    """替代向量编码器：仅校验维度与有限性，不加载模型。"""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def verify_vector(self, vec) -> tuple[bool, str]:
        arr = np.asarray(vec, dtype=np.float32).ravel()
        if arr.ndim != 1 or arr.shape[0] != self.dim:
            return False, f"dim mismatch: expect {self.dim} got {arr.shape[0]}"
        if not np.all(np.isfinite(arr)):
            return False, "non-finite values"
        return True, "ok"


def build_store(base_dir, dim: int = 4, model: str = "test-model",
                preloaded: bool = True) -> VectorStore:
    """构造一个指向临时目录、使用 FakeEncoder 的 VectorStore 实例。

    preloaded=True：跳过磁盘加载（用于空库写入/检索测试）。
    preloaded=False：从磁盘加载（用于持久化往返测试）。
    """
    s = VectorStore()
    s._vec_path = os.path.join(str(base_dir), "vectors.npz")
    s._meta_path = os.path.join(str(base_dir), "vectors_meta.json")
    s._dim = dim
    s._model = model
    s._encoder = FakeEncoder(dim)
    s._matrix = np.zeros((0, dim), dtype=np.float32)
    s._ids = []
    s._row_of = {}
    s._hash_of = {}
    s._updated = {}
    s._dirty = 0
    s._quality_flag_provider = None  # 关闭 quality_flag 过滤，便于断言原始检索结果
    s._loaded = bool(preloaded)
    if not preloaded:
        s._ensure_loaded()
    return s


@pytest.fixture
def tmp_store(tmp_path):
    """一个已就绪的空 VectorStore（临时目录）。"""
    return build_store(tmp_path, preloaded=True)


@pytest.fixture
def store_builder():
    """返回 build_store 工厂，便于在测试中构造第二个实例（如持久化往返）。"""
    return build_store


def _mute_project_logs_handlers():
    """★B156-6 + 第158批 N-8：给指向项目 logs/ 的 FileHandler 挂丢弃过滤器，返回 [(handler, filter)]。

    ★N-8 修正：原实现对所有 logs/ 下的 FileHandler 一律丢弃（保 pulse.log 干净但
    一并丢失测试日志）。现改为「允许 logs/pytest.log 写出」——因为 pytest 运行期
    框架根日志器（经 PULSE_LOG_FILE 环境变量）即落 logs/pytest.log，
    该文件本就是 N-8 规定的测试面日志归集点，不应被丢弃。
    """
    import logging

    _muted = []
    try:
        _logs_root = os.path.abspath(os.path.join(ROOT, "logs")).replace(
            "\\", "/").lower()
        _pytest_log = os.path.abspath(os.path.join(ROOT, "logs", "pytest.log")).replace(
            "\\", "/").lower()

        def _is_logs_handler(h):
            _bf = getattr(h, "baseFilename", None)
            if not _bf:
                return False
            _abf = os.path.abspath(_bf).replace("\\", "/").lower()
            if _abf == _pytest_log:
                return False  # ★N-8：pytest.log 本身允许写入（不再丢弃）
            return _abf.startswith(_logs_root + "/")

        def _drop(_record):
            return False

        _loggers = [logging.getLogger()]
        try:
            _loggers += [logging.getLogger(_n)
                         for _n in logging.root.manager.loggerDict]
        except Exception as _e:
            silent_exc(_e, where="tests.conftest._mute_project_logs_handlers:loggerDict")
        for _lg in _loggers:
            for _h in list(getattr(_lg, "handlers", []) or []):
                if _is_logs_handler(_h):
                    _h.addFilter(_drop)
                    _muted.append((_h, _drop))
    except Exception as _e:
        silent_exc(_e, where="tests.conftest._mute_project_logs_handlers")
        _muted = []
    return _muted


def _restore_muted_logs_handlers(muted):
    """★B156-6：搜肉隔离期挂上的丢弃过滤器。"""
    for _h, _f in (muted or []):
        try:
            _h.removeFilter(_f)
        except Exception as _e:
            silent_exc(_e, where="tests.conftest._restore_muted_logs_handlers")


@pytest.fixture(scope="session", autouse=True)
def _isolate_project_log_files():
    """★主线第22批 T3/P2-121 + 第158批 N-8：会话级隔离「写往项目 logs/ 目录」的 FileHandler。

    背景：pytest 运行会触发大量**测试专用**日志（VectorStore 模拟磁盘写满/
    模型变更/维度不符 50+ 条、进程池「模拟重建失败」ERROR 5 条、
    CallGraphAnalyzer 语法错误跳过 31 条），经 root FileHandler 写进**生产**
    logs/pulse.log，淹没真实故障线索。

    做法（第158批 N-8 演进）：
      - pytest 启动期由本 conftest 顶部设置 ``PULSE_LOG_FILE=logs/pytest.log``，
        框架根日志器（含 VectorStore / 进程池）在 pytest 运行期即直接落
        ``logs/pytest.log``，``pulse.log`` 只收真实运行事件；
      - 仅对指向生产 ``pulse.log`` 的 FileHandler 挂丢弃过滤器（不替换 handler 结构），
        ``logs/pytest.log`` 目标被显式放行（见 ``_mute_project_logs_handlers``），
        故 caplog / assertLogs 等测试内捕获能力完全不受影响。
    任何异常都不阻塞测试。
    """
    # ★B156-6 修正：原实现依赖 gitignored 的 tmp.test_log_isolation
    #   （未入库 → 静默降级为 no-op，logs/ 隔离从未真正生效）。
    #   改为内联实现，去除对 gitignored 模块的依赖
    #   （符合前置分析 T-A03「改为测试自建 fixture」结论）。
    _muted = _mute_project_logs_handlers()
    yield
    _restore_muted_logs_handlers(_muted)


# ============================================================ 框架运行期守卫（主线第57批 T1 / P2-391）
def pytest_configure(config):
    """注册 production_data 标记，避免未知标记告警。"""
    config.addinivalue_line(
        "markers",
        "production_data: 标记读取生产 data/ 目录的现状断言型测试；"
        "框架运行时由本 conftest 自动跳过（P2-391）",
    )


def is_framework_running():
    """★第57批 T1 / P2-391：检测生产框架是否在运行（实时改写 data/ 的进程）。

    框架运行时，读取生产 data/ 的现状断言型测试应跳过，避免被实时改写导致
    假失败并污染生产数据。

    判定（任一为真即视为运行中）：
      1. data/runtime.lock 存在；
      2. 进程检测：命令行**指向** main.py 的进程在跑
         （★第89批 T-89c：改用 psutil 读命令行；原 Windows `tasklist /fo csv`
          输出不含命令行，"main.py" 恒不命中 → 该分支恒假，详见 _detect_framework_process）。
         探测失败时**不**保守判定为运行中（避免沙箱/CI 环境误跳过全部生产测试）。

    可用环境变量 PULSE_TEST_FRAMEWORK_RUNNING 强制覆盖：
      "1" 强制视为运行中；"0" 强制视为已停止（便于本地/CI 验证两种场景）。
    """
    _env = os.environ.get("PULSE_TEST_FRAMEWORK_RUNNING")
    if _env == "1":
        return True
    if _env == "0":
        return False
    if _framework_lock_present():
        return True
    return _detect_framework_process()


def _framework_lock_present() -> bool:
    """★第57批 T1：`data/runtime.lock` 是否存在（独立成函数，便于单测注入）。"""
    return os.path.exists(os.path.join(ROOT, "data", "runtime.lock"))


def _cmdline_is_framework_main(cmdline) -> bool:
    """★第89批 T-89c：命令行是否**指向框架入口** main.py。

    精确匹配「某个参数的文件名恰为 main.py」，而不是子串包含 ——
    子串匹配会误命中「命令行正文里提到 main.py」的进程
    （如 `python -c "import ast; ast.parse(open('main.py').read())"`）。
    """
    for _a in cmdline or []:
        try:
            if os.path.basename(str(_a)).lower() == "main.py":
                return True
        except Exception:
            continue
    return False


def _iter_main_py_cmdlines():
    """★第89批 T-89c：用 psutil 枚举「命令行指向 main.py」的进程（排除本进程）。

    返回命令行列表；psutil 不可用时返回 ``None`` —— 以便调用方区分
    「探测可用但没找到」([]) 与「无法探测」(None) 两种情形。
    """
    try:
        import psutil
    except Exception:
        return None
    _self_pid = os.getpid()
    _out = []
    for _p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if _p.info.get("pid") == _self_pid:
                continue
            _cl = _p.info.get("cmdline") or []
            if _cmdline_is_framework_main(_cl):
                _out.append(" ".join(str(_a) for _a in _cl))
        except Exception:
            continue
    return _out


def _detect_framework_process() -> bool:
    """★第89批 T-89c：进程探测 —— 改用可读「命令行」的方式。

    ★根因（2026-09-20 实测）：原实现 Windows 走 ``tasklist /fo csv``，而该输出
    **不含命令行参数**（实测 15290 字节输出里 "main.py" 出现 **0** 次），
    故 ``"main.py" in _raw`` 恒假 —— 框架明明在跑（PID 26392 ``python.exe main.py``，
    `logs/pulse.log` mtime 秒级刷新）却判为"未运行"，
    ``production_data`` 测试不被跳过 → 必然假失败（第88批 m47 即此）。

    分层探测（psutil 优先）：
      1. **psutil**（`requirements.txt` 既有依赖：``psutil>=5.9.0``，项目内 17 个模块在用）
         —— 枚举命令行并精确匹配参数文件名 == main.py；
      2. POSIX 回退 ``pgrep -af main.py``；
      3. Windows 无 psutil 时回退 ``wmic process get commandline``
         （部分受限环境被安全策略拒绝，故包在 try 内）；
      4. 最后才回退 ``tasklist /fo csv``（★已知不含命令行，仅"尽力而为"，
         其结果不足以判定"未运行"）。
    探测失败一律返回 False（沿用原语义：不保守判定为运行中，
    避免沙箱/CI 环境误跳过全部生产数据测试）。
    """
    _cl = _iter_main_py_cmdlines()
    if _cl is not None:
        return bool(_cl)
    try:
        import subprocess
        if os.name != "nt":
            _raw = subprocess.run(
                ["pgrep", "-af", "main.py"], capture_output=True, timeout=5,
            ).stdout
            return "main.py" in (_raw.decode("utf-8", errors="replace") or "")
        try:
            _raw = subprocess.run(
                ["wmic", "process", "get", "commandline"],
                capture_output=True, timeout=8,
            ).stdout
            _txt = (_raw.decode("utf-8", errors="replace") or "").replace('"', "")
            if "main.py" in _txt:
                return True
        except Exception:
            pass
        _raw = subprocess.run(
            ["tasklist", "/fo", "csv"], capture_output=True, timeout=5,
        ).stdout
        _txt = (_raw.decode("utf-8", errors="replace") or "").replace('"', "")
        return "main.py" in _txt
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    """★第57批 T1：框架运行时，自动跳过标记 production_data 的测试。"""
    if not is_framework_running():
        return
    _reason = "框架运行中，跳过生产数据读取测试"
    for _item in items:
        if "production_data" in _item.keywords:
            _item.add_marker(pytest.mark.skip(reason=_reason))


def _framework_running_skip_if_needed():
    """★第57批 T1：框架运行时抛出 pytest.skip（统一守卫核心逻辑，可单测）。"""
    if is_framework_running():
        pytest.skip("框架运行中，跳过生产数据读取测试")


@pytest.fixture
def guard_production_data():
    """内联守卫：框架运行时跳过当前测试（供未使用标记的生产数据读取测试调用）。"""
    _framework_running_skip_if_needed()
    yield
