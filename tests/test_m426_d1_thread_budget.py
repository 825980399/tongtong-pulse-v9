# -*- coding: utf-8 -*-
"""426 刀1：线程池预算扩面（6 个未接入池）+ inventory 只读清点。

basetemp：`--basetemp=tmp/m426_bt_d1`（本批每文件独立 basetemp，铁律 58）。
"""

import concurrent.futures
import threading
import time
from pathlib import Path

import pytest

#: 426 新接入的 6 个池（池名 -> 改前上限）。改前上限是「生产代码里的原值」，
#: 不是从被测函数里读出来的 —— 否则就是拿被测函数的输出当基准（铁律 30）。
NEW_POOLS_BEFORE = {
    "ext_op": 32,
    "ext_sched": 2,
    "struct_parallel": 16,
    "cortex_parallel": 9,
    "self_awareness": 2,
    "legs_learn": 3,
}


def test_10_budget_covers_all_twelve_pools_and_stays_below_60():
    """12 个池全部进预算表；全表合计 58 < 60（418 的验收线不能被扩面顶破）。"""
    from utils.thread_budget import DEFAULT_CAPS, budget_total, caps

    _c = caps()
    for _k in NEW_POOLS_BEFORE:
        assert _k in _c, f"池 {_k} 未进预算表"
    for _k in ("l0", "l1", "l2", "l3", "adaptive", "hybrid_io"):
        assert _k in _c, f"418 既有池 {_k} 丢了"

    assert set(NEW_POOLS_BEFORE).issubset(set(DEFAULT_CAPS))
    assert budget_total() == 58, (_c, budget_total())
    assert budget_total() < 60


def test_20_new_pools_only_squeezed_never_raised():
    """只压不放：超预算的压到预算，低于预算的原样通过。"""
    from utils.thread_budget import cap

    # 三个真有冗余的池：压
    assert cap("ext_op", NEW_POOLS_BEFORE["ext_op"]) == 8
    assert cap("struct_parallel", NEW_POOLS_BEFORE["struct_parallel"]) == 6
    assert cap("cortex_parallel", NEW_POOLS_BEFORE["cortex_parallel"]) == 8
    # 自适应翻倍后（9*2=18）仍然只到 8
    assert cap("cortex_parallel", 18) == 8
    # 三个不该砍的池：与现值持平，且低于预算时不许调高
    assert cap("ext_sched", 2) == 2
    assert cap("self_awareness", 2) == 2
    assert cap("legs_learn", 3) == 3
    assert cap("ext_op", 4) == 4, "预算比原值大时不许调高"
    assert cap("legs_learn", 1) == 1


def test_30_inventory_counts_are_not_double_counted():
    """`ExtOpSched` 不能被算进 `ext_op`（前缀包含关系），且跑着的线程要数得到。"""
    from utils.thread_budget import inventory, register

    _stop = threading.Event()

    def _hold():
        _stop.wait(5)

    _a = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="ExtOp")
    _b = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="ExtOpSched")
    register("ext_op", _a)
    register("ext_sched", _b)
    try:
        for _ in range(2):
            _a.submit(_hold)
        _b.submit(_hold)
        # 等待线程真正起来（最多 2s）
        _deadline = time.time() + 2
        while time.time() < _deadline:
            _pools = {p["pool"]: p for p in inventory()["pools"]}
            if _pools["ext_op"]["threads"] >= 2 and _pools["ext_sched"]["threads"] >= 1:
                break
            time.sleep(0.02)
        _pools = {p["pool"]: p for p in inventory()["pools"]}
        assert _pools["ext_op"]["threads"] == 2, _pools["ext_op"]
        assert _pools["ext_sched"]["threads"] == 1, _pools["ext_sched"]
        assert _pools["ext_op"]["cap"] == 8
        assert _pools["ext_sched"]["max_workers"] == 1  # 本次测试池，不是生产 2
    finally:
        _stop.set()
        _a.shutdown(wait=True)
        _b.shutdown(wait=True)


def test_40_six_call_sites_wired_to_budget():
    """6 个池的建池点都必须真的调 cap()（源码级判据，防"只改预算表没接线"）。"""
    _expect = {
        # 注意：第二个接线点用了别名 `_m426_cap2`，所以只钉引号内字面量
        "nucleus/external_executor.py": ('cap("ext_op"', '"ext_sched", 2'),
        "nucleus/StructuredParallelScheduler.py": ('cap("struct_parallel"',),
        "nucleus/parallel_scheduler.py": ('cap("cortex_parallel"',),
        "nucleus/self_awareness/SelfAwarenessEngine.py": ('cap("self_awareness"',),
        "organs/motor/PulseLegs.py": ('cap("legs_learn"',),
    }
    for _rel, _needles in _expect.items():
        _src = Path(_rel).read_text(encoding="utf-8")
        for _n in _needles:
            assert _n in _src, f"{_rel} 未接预算: {_n}"


def test_50_switch_off_returns_original_numbers(monkeypatch):
    """开关关闭 ⇒ 逐字回到改前：cap() 原样返回（含 32/16/18 这些大值）。"""
    import config
    from utils.thread_budget import cap

    monkeypatch.setattr(config, "ENABLE_M418_THREAD_SLIMMING", False, raising=False)
    for _k, _v in NEW_POOLS_BEFORE.items():
        assert cap(_k, _v) == _v, _k
    assert cap("cortex_parallel", 18) == 18


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
