# -*- coding: utf-8 -*-
"""★375批刀1：K2 判据改口径 —— 纯 CPU 负载不套「降 >=30%」。

根因：K2 试点负载是纯 CPU Python（读源码 + ast.parse + 图外 probe），三件全在 GIL 下
⇒ 线程并发只换调度不换执行权。340B 刀8 live 实测串行 20.21s / 并发 19.47s，
与合成负载 0.66% 同量级 ⇒ 「降 >=30%」对 GIL 天花板无意义，会恒判 not_dropped。

本刀把判定抽成纯函数 `_k2_verdict_of`（唯一裁决口），按负载形态分流：

- 纯 CPU（kind 全在 `K2_CPU_KINDS`）→ 并发不劣化（慢的部分 <= `K2_GIL_NOISE_BAND`）
- I/O 或混合 → 仍判墙钟下降 >= `K2_DROP_TARGET`

两者都要求 productive/reviewed 完全相等；隔离泄漏优先于一切。

★关键反证在 test_02：**同一个 3.6% 读数**，I/O 型负载仍判 not_dropped
⇒ 说明放宽只针对纯 CPU 形态，不是把尺子整体锯掉。
"""
import os

import pytest

from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor as C

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SRC = os.path.join(ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")

# 340B 刀8 live 实测（票面记 3.68%，此处按两个原始读数现算，不抄结论数字）
_LIVE_SERIAL = 20.21
_LIVE_PARALLEL = 19.47
LIVE_DROP = 1.0 - _LIVE_PARALLEL / _LIVE_SERIAL


def _v(**kw):
    _base = {"leak": False, "prod_eq": True, "cpu_bound": True, "drop": LIVE_DROP}
    _base.update(kw)
    return C._k2_verdict_of(**_base)


def _read():
    with open(SRC, encoding="utf-8") as f:
        return f.read()


def _k2_src():
    """只截 K2 试点本体（`k2_parallel_pilot` 到下一个方法），不扫全文件。"""
    _t = _read()
    _i = _t.find("def k2_parallel_pilot")
    assert _i >= 0, "找不到 k2_parallel_pilot（判据无从判定，不静默跳过）"
    _j = _t.find("def run_runtime_guided_review", _i)
    assert _j > _i, "找不到 K2 段右边界"
    return _t[_i:_j]


# ------------------------------- ① 实测读数在纯 CPU 口径下的结论


def test_01_pure_cpu_live_reading_passes():
    """实测 3.6% 降幅：纯 CPU 负载判 pass（旧 30% 硬线下必判 not_dropped）。"""
    assert LIVE_DROP < C.K2_GIL_NOISE_BAND, "实测读数已不在噪声带内，用例前提变了需重算"
    _r = _v()
    assert _r["verdict"] == "cpu_ceiling_ok"
    assert _r["criteria"] == "cpu_gil_ceiling"
    assert _r["drop_target_applied"] is None, "纯 CPU 负载不该再套 30% 线"
    assert _r["sched_overhead_ok"] is True


def test_02_same_reading_still_fails_for_io_load():
    """★反证：同一个读数换成 I/O 型负载，仍判 not_dropped ⇒ 没有一刀切放宽。"""
    _r = _v(cpu_bound=False)
    assert _r["verdict"] == "wallclock_not_dropped"
    assert _r["criteria"] == "io_speedup"
    assert _r["drop_target_applied"] == C.K2_DROP_TARGET


# ------------------------------- ② 纯 CPU 口径的上下界


@pytest.mark.parametrize("drop,ok", [
    (0.0, True),          # 完全持平
    (-0.05, True),        # 正好慢 5% = 噪声带上界（闭区间）
    (-0.0501, False),     # 越过噪声带 ⇒ 调度开销不可忽略
    (-0.20, False),       # 明显劣化
])
def test_03_cpu_ceiling_bounds(drop, ok):
    _r = _v(drop=drop)
    assert _r["verdict"] == ("cpu_ceiling_ok" if ok else "cpu_ceiling_breach")
    assert _r["sched_overhead_ok"] is ok
    assert _r["sched_overhead_ratio"] == pytest.approx(max(0.0, -drop), abs=1e-4)


def test_04_cpu_drop_is_not_rewarded():
    """纯 CPU 负载降幅再大也不加分：判据仍是「不劣化」，不是「越快越好」。"""
    _r = _v(drop=0.80)
    assert _r["verdict"] == "cpu_ceiling_ok"
    assert _r["sched_overhead_ratio"] == 0.0


# ------------------------------- ③ 优先级：泄漏 > 产出 > 形态


def test_05_productivity_mismatch_beats_cpu_ceiling():
    """产出不等时，纯 CPU 形态不许放行（并行空转不是「GIL 正常」）。"""
    _r = _v(prod_eq=False, drop=0.5)
    assert _r["verdict"] == "productivity_mismatch"


def test_06_isolation_leak_beats_everything():
    _r = _v(leak=True, prod_eq=False, drop=0.9)
    assert _r["verdict"] == "isolation_leak"


# ------------------------------- ④ I/O 口径未被动过


@pytest.mark.parametrize("drop,verdict", [
    (0.35, "parallel_pilot_pass"),
    (0.30, "parallel_pilot_pass"),   # 闭区间边界
    (0.2999, "wallclock_not_dropped"),
    (0.0, "wallclock_not_dropped"),
])
def test_07_io_criteria_unchanged(drop, verdict):
    assert _v(cpu_bound=False, drop=drop)["verdict"] == verdict


# ------------------------------- ⑤ 负载形态判定：不猜


@pytest.mark.parametrize("items,want", [
    ([], False),                                            # 空集不判
    ([{"kind": "code_scan"}], True),
    ([{"kind": "code_scan"}, {"kind": "struct_assert"}], True),
    ([{"kind": "code_scan"}, {"kind": "io_wait"}], False),   # 混合 ⇒ 按 I/O 原口径
    ([{"kind": "code_scan"}, {}], False),                    # 缺 kind ⇒ unknown ⇒ 不放宽
    ([{"kind": "code_scan"}, {"kind": ""}], False),
])
def test_08_is_cpu_bound_never_guesses(items, want):
    assert C._k2_is_cpu_bound(items) is want


def test_09_load_kinds_counts_and_unknown_bucket():
    _c = C._k2_load_kinds([{"kind": "code_scan"}, {"kind": "code_scan"}, {}])
    assert _c == {"code_scan": 2, "unknown": 1}
    assert C._k2_load_kinds([]) == {}


# ------------------------------- ⑥ 不强行降速 + 口径单一源


def test_10_no_forced_slowdown_in_pilot():
    """「不强行降速」= K2 全段没有任何 sleep / 等待注入。"""
    _s = _k2_src()
    for _bad in ("sleep(", "Event().wait(", "time.wait("):
        assert _bad not in _s, f"K2 段出现等待注入 {_bad!r}，属强行降速"


def test_11_old_threshold_branch_removed():
    """旧的 30% 硬线判定分支已消失（不是两套判定并存）。"""
    _s = _k2_src()
    assert "_drop >= self.K2_DROP_TARGET and _prod_eq" not in _s
    assert "_k2_verdict_of(" in _s, "判定必须走唯一裁决口"
    assert "_cpu_bound = self._k2_is_cpu_bound(" in _s


def test_12_pilot_returns_criteria_fields():
    """返回字典带出口径字段，读方不必猜用的是哪条线。"""
    _s = _k2_src()
    for _k in ('"drop_target_applied"', '"criteria"', '"load_cpu_bound"',
               '"load_kinds"', '"sched_overhead_ratio"', '"sched_overhead_ok"',
               '"gil_noise_band"'):
        assert _k in _s, "缺返回字段 " + _k
