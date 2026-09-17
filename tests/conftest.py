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

import numpy as np
import pytest

from nucleus.semantic.VectorStore import VectorStore


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


@pytest.fixture(scope="session", autouse=True)
def _isolate_project_log_files():
    """★主线第22批 T3/P2-121：会话级静音「写往项目 logs/ 目录」的 FileHandler。

    背景：pytest 运行会触发大量**测试专用**日志（VectorStore 模拟磁盘写满/
    模型变更/维度不符 50+ 条、进程池「模拟重建失败」ERROR 5 条、
    CallGraphAnalyzer 语法错误跳过 31 条），经 root FileHandler 写进**生产**
    logs/pulse.log，淹没真实故障线索。

    做法：只给项目日志 FileHandler 挂丢弃过滤器（不替换 handler 结构），
    故 caplog / assertLogs 等测试内捕获能力完全不受影响。
    任何异常都不阻塞测试。
    """
    _muted = []
    try:
        from tmp.test_log_isolation import mute_project_file_handlers
        _muted = mute_project_file_handlers()
    except Exception:
        _muted = []
    yield
    try:
        from tmp.test_log_isolation import restore_muted_handlers
        restore_muted_handlers(_muted)
    except Exception:
        pass


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
      2. 进程检测：命令行含 main.py 的 python 进程在跑。
         探测失败时**不**保守判定为运行中（避免沙箱/CI 环境误跳过全部生产测试）。

    可用环境变量 PULSE_TEST_FRAMEWORK_RUNNING 强制覆盖：
      "1" 强制视为运行中；"0" 强制视为已停止（便于本地/CI 验证两种场景）。
    """
    _env = os.environ.get("PULSE_TEST_FRAMEWORK_RUNNING")
    if _env == "1":
        return True
    if _env == "0":
        return False
    if os.path.exists(os.path.join(ROOT, "data", "runtime.lock")):
        return True
    try:
        import subprocess
        if os.name == "nt":
            _raw = subprocess.run(
                ["tasklist", "/fo", "csv"], capture_output=True, timeout=5,
            ).stdout
        else:
            _raw = subprocess.run(
                ["pgrep", "-af", "main.py"], capture_output=True, timeout=5,
            ).stdout
        _txt = _raw.decode("utf-8", errors="replace") if _raw else ""
        return "main.py" in _txt.replace('"', '')
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
