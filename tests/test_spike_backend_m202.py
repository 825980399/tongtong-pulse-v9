# -*- coding: utf-8 -*-
"""
test_spike_backend_m202.py —— 第202批 刀3 隔离单测

刀3：第三层执行后端层（接口隔离，现在预埋）
    新建 nucleus/synapsys/SpikeExecutionBackend.py
    SpikeExecutionBackend(ABC) + PythonSNNBackend
    config.SPIKE_BACKEND = 'python'

验收要点：
    1. 抽象基类不可实例化，三个抽象方法已声明。
    2. PythonSNNBackend 可实例化且方法为安全空实现（调用不抛、不改状态）。
    3. config 新增 SPIKE_BACKEND 且默认 'python'。
    4. 工厂 get_spike_backend 默认返回 Python 后端；未知名称安全回退。
    5. 本批未接入任何器官代码（纯接口预埋，未调用即零运行时开销）。
"""
import pytest

import config
from nucleus.synapsys.SpikeExecutionBackend import (
    BACKEND_PHOTONIC,
    BACKEND_PYTHON,
    PythonSNNBackend,
    SpikeExecutionBackend,
    get_spike_backend,
)


def test_abstract_base_cannot_instantiate():
    """抽象基类不可实例化。"""
    with pytest.raises(TypeError):
        SpikeExecutionBackend()  # type: ignore[abstract]


def test_abstract_methods_declared():
    """三个抽象方法已声明。"""
    for m in ("emit", "on_spike", "update_weight"):
        assert m in SpikeExecutionBackend.__abstractmethods__, "缺抽象方法 %s" % m


def test_python_backend_instantiable():
    """PythonSNNBackend 可实例化，实现全部抽象方法。"""
    b = PythonSNNBackend()
    assert isinstance(b, SpikeExecutionBackend)
    assert b.name == BACKEND_PYTHON
    assert b.name == "python"


def test_python_backend_methods_are_safe_noop():
    """方法为安全空实现：调用不抛异常、返回 None。"""
    b = PythonSNNBackend()
    assert b.emit({"pulse_id": "p1"}) is None
    assert b.on_spike({"pulse_id": "p1"}) is None
    assert b.update_weight("pre", "post", 0.1) is None


def test_config_spike_backend_default():
    """config 新增 SPIKE_BACKEND 且默认 'python'。"""
    assert hasattr(config, "SPIKE_BACKEND"), "config 缺 SPIKE_BACKEND"
    assert config.SPIKE_BACKEND == "python"


def test_backend_constants():
    """后端名称常量。"""
    assert BACKEND_PYTHON == "python"
    assert BACKEND_PHOTONIC == "photonic"


def test_factory_default_from_config():
    """工厂未传名时惰性读取 config.SPIKE_BACKEND。"""
    b = get_spike_backend()
    assert isinstance(b, PythonSNNBackend)


def test_factory_explicit_python():
    b = get_spike_backend(BACKEND_PYTHON)
    assert isinstance(b, PythonSNNBackend)


def test_factory_unknown_falls_back_safely():
    """未知 / 未实现（photonic）后端安全回退到 Python 后端，不抛异常。"""
    assert isinstance(get_spike_backend(BACKEND_PHOTONIC), PythonSNNBackend)
    assert isinstance(get_spike_backend("no_such_backend"), PythonSNNBackend)


def test_not_wired_into_organs():
    """本批未接入任何器官代码：全仓（器官/引擎）无 SpikeExecutionBackend 的调用点。

    仅允许本文件（测试）、后端模块自身存在引用。
    """
    import pathlib
    import re

    root = pathlib.Path(config.__file__).parent
    pat = re.compile(r"SpikeExecutionBackend|get_spike_backend")
    # 203批·刀1：唯一授权接入点（PulseCore.emit/on_spike 调用后端，受 SPIKE_BACKEND_ENABLED 门控）
    SANCTIONED = {"nucleus/pulse/PulseCore.py"}
    hits = []
    for base in ("organs", "nucleus", "functions"):
        for p in (root / base).rglob("*.py"):
            if p.name == "SpikeExecutionBackend.py":
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            if rel in SANCTIONED:
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pat.search(txt):
                hits.append(rel)
    assert hits == [], "发现未授权的后端接入点（203批仅 PulseCore.py 授权）: %s" % hits
    # 正断言：授权接入点确实存在且含接入调用
    core = root / "nucleus/pulse/PulseCore.py"
    assert core.is_file() and pat.search(core.read_text(encoding="utf-8", errors="ignore")), \
        "PulseCore.py 应含后端接入点（接入失败）"
