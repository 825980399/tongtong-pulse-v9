# -*- coding: utf-8 -*-
"""187批刀1 隔离单测：SandboxLimits 默认上限改从 O-B1 键取（sandbox绑键刀）。

验收点：
  - 默认 OB1_SANDBOX_LEVEL="strict" → 解析为当前 DEFAULT_*（零行为变化）
  - loose / off → 放宽档
  - 显式键 OB1_SANDBOX_CPU_CAP / OB1_SANDBOX_MEM_MB 经 getattr 兜底优先
  - 缺失 OB1_SANDBOX_LEVEL → 回落 strict
"""
import sys

sys.path.insert(0, ".")

import config as _cfg
import nucleus.security.sandbox_limits as sl


def _backup(key):
    return (key, getattr(_cfg, key, None))


def _set(pairs):
    saved = []
    for k, v in pairs.items():
        saved.append(_backup(k))
        setattr(_cfg, k, v)
    return saved


def _restore(saved):
    for k, v in saved:
        if v is None:
            if hasattr(_cfg, k):
                delattr(_cfg, k)
        else:
            setattr(_cfg, k, v)


def test_default_strict_unchanged():
    """默认 strict → 当前 DEFAULT_*，与 SandboxLimits() 字面默认等价（零行为变化）。"""
    L = sl._resolve_default_sandbox_limits()
    assert (L.cpu_seconds, L.memory_mb, L.nofile, L.nproc) == (30, 512, 64, 1)
    assert L == sl.SandboxLimits()
    assert sl.DEFAULT_LIMITS == L


def test_loose_relaxed():
    s = _set({"OB1_SANDBOX_LEVEL": "loose"})
    try:
        L = sl._resolve_default_sandbox_limits()
        assert (L.cpu_seconds, L.memory_mb, L.nofile, L.nproc) == (120, 1024, 256, 4)
    finally:
        _restore(s)


def test_off_most_relaxed():
    s = _set({"OB1_SANDBOX_LEVEL": "off"})
    try:
        L = sl._resolve_default_sandbox_limits()
        assert (L.cpu_seconds, L.memory_mb, L.nofile, L.nproc) == (600, 4096, 1024, 16)
    finally:
        _restore(s)


def test_explicit_cpu_mem_override_in_strict():
    """显式键 OB1_SANDBOX_CPU_CAP / OB1_SANDBOX_MEM_MB 经 getattr 兜底优先于档位默认。"""
    s = _set({"OB1_SANDBOX_LEVEL": "strict", "OB1_SANDBOX_CPU_CAP": 99, "OB1_SANDBOX_MEM_MB": 2048})
    try:
        L = sl._resolve_default_sandbox_limits()
        assert (L.cpu_seconds, L.memory_mb, L.nofile, L.nproc) == (99, 2048, 64, 1)
    finally:
        _restore(s)


def test_missing_level_falls_back_to_strict():
    """OB1_SANDBOX_LEVEL 缺失 → getattr 兜底 strict DEFAULT_*。"""
    saved = _backup("OB1_SANDBOX_LEVEL")
    if hasattr(_cfg, "OB1_SANDBOX_LEVEL"):
        delattr(_cfg, "OB1_SANDBOX_LEVEL")
    try:
        L = sl._resolve_default_sandbox_limits()
        assert (L.cpu_seconds, L.memory_mb) == (30, 512)
    finally:
        _restore([saved])


def test_level_case_insensitive():
    s = _set({"OB1_SANDBOX_LEVEL": "LOOSE"})
    try:
        L = sl._resolve_default_sandbox_limits()
        assert (L.cpu_seconds, L.memory_mb) == (120, 1024)
    finally:
        _restore(s)


if __name__ == "__main__":
    import traceback
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _fail = 0
    for t in _tests:
        try:
            t()
            print("PASS", t.__name__)
        except Exception:
            _fail += 1
            print("FAIL", t.__name__)
            traceback.print_exc()
    print(f"\n{len(_tests) - _fail}/{len(_tests)} passed")
    sys.exit(1 if _fail else 0)
