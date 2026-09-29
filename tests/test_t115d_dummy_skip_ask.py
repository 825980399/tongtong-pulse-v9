# -*- coding: utf-8 -*-
"""相关任务 门控单测：114a Dummy 占位实例防御 + 断5 指纹级降档。

全程不构造真实 SafeEvolutionExecutor（避免重依赖），直接以 unbound 方式调用
实例方法，复现「占位/Dummy 实例被传入冷却方法」与「断5 同指纹计数」两条路径。
"""
import importlib.util
import os


def _load_see():
    _src = os.path.join(
        os.path.dirname(__file__), "..", "nucleus", "reasoning", "SafeEvolutionExecutor.py")
    _src = os.path.abspath(_src)
    _spec = importlib.util.spec_from_file_location("SEE_t115d_test", _src)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    # ★往期批次 相关任务②（R4-B22 冒烟隔离规矩）：本用例用**合成指纹**
    #   （file="a.py"、method="m"）驱动断5 硬闸，其 `[指纹咨询硬闸]` INFO 属冒烟产物。
    #   实测该行曾落进 pulse.log（2026-09-23 21:05:26），对「INFO>=1」类生产判据构成
    #   假阳性风险。改为走 smoke 日志器（独立 smoke.log + [SMOKE] 前缀 + 不冒泡）。
    try:
        from nucleus.logger import get_smoke_logger
        _mod._module_logger = get_smoke_logger("t115d_dummy_skip_ask")
    except Exception:
        pass  # 拿不到 smoke 日志器就保持原样（不因日志设施失败而让用例失败）
    return _mod.SafeEvolutionExecutor


def test_dummy_placeholder_no_attributeerror():
    """占位/Dummy 实例调用冷却落盘方法不应再抛 AttributeError。"""
    SEE = _load_see()

    class Dummy:
        pass

    d = Dummy()
    # 此前：'Dummy' object has no attribute '_m114a_cooldown_path'
    assert SEE._m114a_cooldown_path(d) == ""
    assert SEE._m114a_save_cooldown(d) is None
    assert SEE._m114a_register_verify_failure(d, "x") is None
    assert SEE._m114a_load_cooldown(d) is None


def test_skip_ask_fingerprint_level():
    """断5 指纹级降档：同指纹累计>=3 轮才跳过，且不影响其它指纹。"""
    SEE = _load_see()

    class Fake:
        _no_fix_cooldown_rounds = {}

    fake = Fake()
    fake._cooldown_key = lambda issue: SEE._cooldown_key(issue)

    iss = {"file": "a.py", "method": "m", "type": "silent_exception"}
    fp = SEE._cooldown_key(iss)

    # rounds=0 -> 不跳过（照常问询）
    assert SEE._m114a_should_skip_ask(fake, iss) is False
    # rounds=3 -> 跳过（同指纹已累计 3 轮验证失败）
    fake._no_fix_cooldown_rounds = {fp: 3}
    assert SEE._m114a_should_skip_ask(fake, iss) is True
    # 不同指纹（file|method|type 任一不同）不受影响 -> 不误拦
    other = {"file": "b.py", "method": "n", "type": "silent_exception"}
    assert SEE._m114a_should_skip_ask(fake, other) is False


def test_skip_ask_source_is_real_rounds_not_hub():
    """断5 判据源是 _no_fix_cooldown_rounds（真实计数），非空集 hub。"""
    SEE = _load_see()

    class Fake:
        _no_fix_cooldown_rounds = {"organ:x|type:silent_exception|desc": 5}

    fake = Fake()
    fake._cooldown_key = lambda issue: SEE._cooldown_key(issue)
    iss = {"organ": "x", "type": "silent_exception", "description": "desc"}
    # 即便 hub 为空，真实计数>=3 仍应跳过（死闸已被换源修复）
    assert SEE._m114a_should_skip_ask(fake, iss) is True
