# -*- coding: utf-8 -*-
"""437 刀1 单测：原生线程池上限（OpenMP/BLAS/rayon + ORT intra-op）。

判据全部来自**行为**（环境变量是否被写、是否覆盖外部值、开关是否生效），
不拿被测函数自己的输出当基准（铁律 30）。
"""
import inspect
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
import utils.native_thread_caps as ntc

_ENV_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS")


@pytest.fixture()
def clean_env(monkeypatch):
    """清空 5 个变量 + 复位模块内的 _APPLIED（避免跨用例串味）。"""
    for _k in _ENV_KEYS:
        monkeypatch.delenv(_k, raising=False)
    monkeypatch.setattr(ntc, "_APPLIED", {}, raising=True)
    monkeypatch.setattr(ntc, "_APPLIED_ONCE", False, raising=True)
    yield monkeypatch


# ---------------------------------------------------------------- ① 写入
def test_apply_writes_all_five(clean_env):
    """默认开关下必须写满 5 个变量，且取值来自 config。"""
    _r = ntc.apply_native_caps()
    assert _r["enabled"] is True
    assert set(_r["set"]) == set(_ENV_KEYS), f"未写全: {_r['set']}"
    assert os.environ["OMP_NUM_THREADS"] == str(config.M437_NATIVE_OMP_THREADS)
    assert os.environ["RAYON_NUM_THREADS"] == str(config.M437_NATIVE_RAYON_THREADS)
    # OpenMP 系四个必须同值（同一批调用方共享语义）
    _vals = {os.environ[_k] for _k in _ENV_KEYS[:4]}
    assert len(_vals) == 1, f"OpenMP 四变量不一致: {_vals}"


# ---------------------------------------------------------------- ② 不覆盖
def test_does_not_override_external(clean_env):
    """外部显式设的值优先，不能被本模块悄悄改掉。"""
    clean_env.setenv("OMP_NUM_THREADS", "12")
    _r = ntc.apply_native_caps()
    assert os.environ["OMP_NUM_THREADS"] == "12", "外部值被覆盖"
    assert _r["kept"].get("OMP_NUM_THREADS") == "12"
    assert "OMP_NUM_THREADS" not in _r["set"]
    # 其余四个仍应被写入
    assert "RAYON_NUM_THREADS" in _r["set"]


# ---------------------------------------------------------------- ③ force
def test_force_overrides_external(clean_env):
    """force=True 时才覆盖。"""
    clean_env.setenv("OMP_NUM_THREADS", "12")
    ntc.apply_native_caps(force=True)
    assert os.environ["OMP_NUM_THREADS"] == str(config.M437_NATIVE_OMP_THREADS)


# ---------------------------------------------------------------- ④ 总闸
def test_switch_off_writes_nothing(clean_env):
    """ENABLE_M437_NATIVE_THREAD_CAP=False ⇒ 一个都不写（逐字回退）。"""
    clean_env.setattr(config, "ENABLE_M437_NATIVE_THREAD_CAP", False)
    _r = ntc.apply_native_caps()
    assert _r["enabled"] is False
    for _k in _ENV_KEYS:
        assert _k not in os.environ, f"开关关闭却写了 {_k}"


# ---------------------------------------------------------------- ⑤ 取值
def test_thread_values_read_config(clean_env):
    """三个取值函数必须真读 config（不是写死）。"""
    clean_env.setattr(config, "M437_NATIVE_OMP_THREADS", 6)
    clean_env.setattr(config, "M437_NATIVE_RAYON_THREADS", 3)
    clean_env.setattr(config, "M437_ORT_INTRA_THREADS", 5)
    assert ntc.omp_threads() == 6
    assert ntc.rayon_threads() == 3
    assert ntc.ort_threads() == 5


def test_thread_values_floor_at_one(clean_env):
    """0 / 负数按 1 处理（否则 ORT / OpenBLAS 收到 0 会退回按核数自选）。"""
    clean_env.setattr(config, "M437_NATIVE_OMP_THREADS", 0)
    clean_env.setattr(config, "M437_NATIVE_RAYON_THREADS", -3)
    clean_env.setattr(config, "M437_ORT_INTRA_THREADS", 0)
    assert ntc.omp_threads() == 1
    assert ntc.rayon_threads() == 1
    assert ntc.ort_threads() == 1


# ---------------------------------------------------------------- ⑥ 幂等
def test_idempotent_second_call_is_noop(clean_env):
    """第二次调用不再重复写（set 为空、kept 里是目标值）。"""
    ntc.apply_native_caps()
    _r2 = ntc.apply_native_caps()
    assert _r2["set"] == {}, f"第二次仍写入: {_r2['set']}"
    assert set(_r2["kept"]) == set(_ENV_KEYS)


# ---------------------------------------------------------------- ⑦ 接线
def test_vector_encoder_uses_configured_ort_threads():
    """VectorEncoder 必须走配置化 ORT 线程数，且不再有写死的 `threads=2 if`。"""
    from nucleus.semantic.VectorEncoder import VectorEncoder
    _src = inspect.getsource(VectorEncoder._load_model)
    assert "_m437_ort_threads()" in _src, "未走配置化 ORT 线程数"
    assert "threads=2 if" not in _src, "仍保留 360 批写死的 threads=2 if"


def test_main_applies_caps_before_numpy():
    """main.py 的限流调用必须在**第一个** numpy 系 import 之前（源码位置判据）。"""
    with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as _f:
        _lines = _f.read().splitlines()
    _cap_line = None
    _np_line = None
    for _i, _l in enumerate(_lines, 1):
        if _cap_line is None and "_apply_native_caps(" in _l:
            _cap_line = _i
        if _np_line is None and (
                _l.strip().startswith("import numpy")
                or _l.strip().startswith("from numpy")):
            _np_line = _i
    assert _cap_line is not None, "main.py 未调用 apply_native_caps"
    assert _np_line is None or _cap_line < _np_line, \
        f"限流在第 {_cap_line} 行，numpy 在第 {_np_line} 行 —— 顺序反了"
    assert _cap_line < 20, f"限流调用在第 {_cap_line} 行，太晚（应在文件头部）"
