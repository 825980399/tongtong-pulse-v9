# -*- coding: utf-8 -*-
"""428 批刀3 单测：perf 三维度真供给（avg / P95 / 内存）。

钉语义：
  · P95 按样本升序取 ceil(0.95*n)-1，样本不足时等价最大值（如实返回，不修正）；
  · 生产形态（original_code 是方法体片段）必须走 `revert_in_file` 才测得出 ——
    412 的 standalone 模式对片段恒 `no_sample`（87 条真实补丁实测如此）；
  · `verify()` 要把三个数值**存进 verdict**（改造前只用一次就丢）。
"""
from __future__ import annotations

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

pp = importlib.import_module("nucleus.evolution.perf_delta_probe")


def test_01_p95_definition():
    assert pp._m428_p95_ms([]) is None
    assert pp._m428_p95_ms([7.0]) == 7.0
    assert pp._m428_p95_ms([3.0, 1.0, 2.0]) == 3.0, "n=3 时 P95 等价最大值"
    _xs = [float(i) for i in range(1, 21)]       # 1..20，n=20 ⇒ idx 18
    assert pp._m428_p95_ms(_xs) == 19.0


def test_02_fragment_before_side_uses_revert_mode(tmp_path):
    """补丁落地后，before 侧必须由「整文件回退」造出来，否则 import 必失败。

    `time.sleep` 必须放在**模块级**：import 才会执行它（放函数体里 import 不调用）。
    """
    _f = tmp_path / "t.py"
    _f.write_text("X = 1\ndef f():\n    return 2\n"
                  "import time\ntime.sleep(0.06)\n", encoding="utf-8")
    _patch = {
        "id": "p1", "file": str(_f),
        # original_code 是**片段**（不能独立成模块）⇒ 只能走整文件回退
        "original_code": "    return 2\n",
        "modified_code": "    return 2\nimport time\ntime.sleep(0.06)\n",
    }
    _r = pp.perf_delta_for_patch(_patch, repeats=2)
    assert _r["state"] == "ok", _r.get("reason")
    assert _r["ab_mode"] == pp._AB_MODE_REVERT
    assert _r["delta_ms"] is not None and _r["delta_ms"] > 20.0, _r
    assert _r["p95_delta_ms"] is not None
    assert _r["mem_delta_mb"] is not None, "内存维度必须也有读数"


def test_03_verify_stores_three_perf_numbers():
    from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier
    _v = EvolutionEffectVerifier(base_dir=os.path.join(str(_ROOT), "tmp",
                                                       "m428_vf_test"))
    _vd = _v.verify({"id": "px", "file": "a.py"}, {"type": "t"},
                    problem_gone=True, function_ok=True,
                    perf_delta_ms=12.5,
                    perf_detail={"p95_delta_ms": 30.0, "mem_delta_mb": 0.5,
                                 "ab_mode": "revert_in_file"})
    assert _vd["perf_delta_ms"] == 12.5
    assert _vd["perf_p95_ms"] == 30.0
    assert _vd["perf_mem_mb"] == 0.5
    assert _vd["perf_ab_mode"] == "revert_in_file"
    assert _vd["dims"]["perf_ok"] is True
    assert _vd["passed"] is True
