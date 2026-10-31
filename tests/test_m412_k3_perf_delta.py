# -*- coding: utf-8 -*-
"""412 批刀3 单测：perf_delta_ms 供给补全（perf_delta_probe 契约 412.3）。

钉语义（不钉措辞）：
  * 第三维必须有**真数值**：`perf_delta_ms` = 改后导入耗时 − 改前导入耗时（正 = 变慢）；
  * 测不出来就给 **None**，**绝不给 0**（0 会被 `verify()` 判成 perf_ok ⇒ 编造"没有变化"）；
  * A/B 两侧必须写**不同文件名**（同名会互相覆盖 ⇒ A/A ⇒ delta 恒 ≈0，首版实测踩到）；
  * 采样取**最小值**并列全样本（不声称精确到毫秒）；
  * 三维判据本身**一个字不改**：三维全知且都过 ⇒ passed；缺一维 ⇒ None（覆盖不足不是通过）。
"""

from __future__ import annotations

import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.evolution import perf_delta_probe as pp  # noqa: E402


class _Res:
    def __init__(self, stdout, returncode=0, limit_hit=None):
        self.stdout = stdout
        self.returncode = returncode
        self.limit_hit = limit_hit
        self.stderr = ""
        self.duration_ms = 0.0
        self.peak_memory_bytes = 0


def _fake_sandbox(samples, returncode=0, limit_hit=None):
    """替换 `execute_code_in_subprocess`：按调用次序吐出给定样本。"""
    _it = iter(samples)

    def _run(code, **kwargs):
        _v = next(_it, None)
        if _v is None:
            return _Res("", returncode=1)
        return _Res("M412_MS %s\n" % _v, returncode=returncode, limit_hit=limit_hit)
    return _run


@pytest.fixture
def fake_sandbox(monkeypatch):
    def _install(samples, **kw):
        import nucleus.security.sandbox_limits as _sl
        monkeypatch.setattr(_sl, "execute_code_in_subprocess",
                            _fake_sandbox(samples, **kw), raising=True)
    return _install


def _src(rel):
    _s = io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read()
    return _s.replace("\r\n", "\n")


# ---------------------------------------------------------------- A 单次采样

def test_01_measure_import_ms_real_file(tmp_path):
    _f = tmp_path / "m.py"
    _f.write_text("X = 1\n", encoding="utf-8")
    _r = pp.measure_import_ms(str(_f), repeats=1)
    assert _r["state"] == "ok"
    assert _r["ms"] is not None and _r["ms"] >= 0.0
    assert len(_r["samples"]) == 1


def test_02_measure_missing_file_is_error_not_zero(tmp_path):
    _r = pp.measure_import_ms(str(tmp_path / "nope.py"))
    assert _r["state"] == "error"
    assert _r["ms"] is None, "测不出来给 None，不给 0"
    assert _r["reason"] == "file_missing"


def test_03_samples_take_minimum(fake_sandbox, tmp_path):
    """取最小值压掉冷启动抖动，同时把全样本留下（读数可解释）。"""
    _f = tmp_path / "m.py"
    _f.write_text("X = 1\n", encoding="utf-8")
    fake_sandbox([5.0, 2.0, 3.0])
    _r = pp.measure_import_ms(str(_f), repeats=3)
    assert _r["state"] == "ok"
    assert _r["ms"] == 2.0
    assert _r["samples"] == [5.0, 2.0, 3.0]


def test_04_bad_runs_are_not_counted(fake_sandbox, tmp_path):
    _f = tmp_path / "m.py"
    _f.write_text("X = 1\n", encoding="utf-8")
    fake_sandbox([1.0], returncode=1)
    assert pp.measure_import_ms(str(_f), repeats=2)["state"] == "error"

    fake_sandbox([1.0], limit_hit="wall_clock")
    assert pp.measure_import_ms(str(_f), repeats=2)["state"] == "error"


def test_05_switch_off_means_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENABLE_M412_PERF_DELTA", False, raising=False)
    _f = tmp_path / "m.py"
    _f.write_text("X = 1\n", encoding="utf-8")
    _r = pp.perf_delta_for_patch({"file": str(_f), "original_code": "X = 1\n"})
    assert _r["state"] == "missing" and _r["reason"] == "disabled"
    assert _r["delta_ms"] is None


# ---------------------------------------------------------------- B A/B 差值

def _patch_pair(tmp_path, after_text, before_text="X = 1\n"):
    _f = tmp_path / "target.py"
    _f.write_text(after_text, encoding="utf-8")
    return {"id": "p1", "file": str(_f), "original_code": before_text}


def test_11_slower_change_gives_positive_delta(tmp_path):
    """真沙箱：改后 sleep 80ms ⇒ delta 明显为正（正 = 变慢，与 verify 同语义）。"""
    _r = pp.perf_delta_for_patch(
        _patch_pair(tmp_path, "import time\ntime.sleep(0.08)\nY = 2\n"), repeats=2)
    assert _r["state"] == "ok", _r.get("reason")
    assert _r["delta_ms"] > 50.0, _r
    assert _r["after_ms"] > _r["before_ms"]


def test_12_faster_change_gives_negative_delta(tmp_path):
    _r = pp.perf_delta_for_patch(
        _patch_pair(tmp_path, "X = 1\n", before_text="import time\ntime.sleep(0.08)\n"),
        repeats=2)
    assert _r["state"] == "ok"
    assert _r["delta_ms"] < -50.0, _r


def test_13_no_original_code_is_missing_not_zero(tmp_path):
    _f = tmp_path / "t.py"
    _f.write_text("X = 1\n", encoding="utf-8")
    _r = pp.perf_delta_for_patch({"file": str(_f)})
    assert _r["state"] == "missing"
    assert _r["delta_ms"] is None
    assert "no_original_code" in _r["reason"]


def test_14_file_not_on_disk_is_missing(tmp_path):
    _r = pp.perf_delta_for_patch({"file": str(tmp_path / "nope.py"),
                                  "original_code": "X = 1\n"})
    assert _r["state"] == "missing" and _r["delta_ms"] is None


def test_15_ab_sides_must_not_share_one_filename(tmp_path):
    """★首版 bug：两侧同名 ⇒ 后写覆盖前写 ⇒ A/A ⇒ delta 恒 ≈0。"""
    _r = pp.perf_delta_for_patch(
        _patch_pair(tmp_path, "import time\ntime.sleep(0.08)\n",
                    before_text="X = 1\n"), repeats=1)
    assert abs(_r["delta_ms"]) > 10.0, "两侧若共用文件名，这个差会是 ~0"


# ---------------------------------------------------------------- C 三维判定（真 verify）

def _verifier():
    from nucleus.evolution.EvolutionEffectVerifier import EvolutionEffectVerifier
    return EvolutionEffectVerifier()


def test_21_three_dims_known_and_all_pass_gives_passed():
    _v = _verifier().verify({"id": "p3d", "file": "a.py"}, {"type": "x"},
                            problem_gone=True, function_ok=True, perf_delta_ms=1.0)
    assert _v["passed"] is True, _v["reason"]
    assert _v["dims"]["perf_ok"] is True
    assert _v["trust_delta"] == 10


def test_22_perf_regression_beyond_threshold_gives_failed():
    """阈值用**实测**的 `DEFAULT_MAX_REGRESSION_MS`（不是任务书里的 0.5ms）。"""
    _max = _verifier().DEFAULT_MAX_REGRESSION_MS
    _v = _verifier().verify({"id": "pperf", "file": "a.py"}, {"type": "x"},
                            problem_gone=True, function_ok=True,
                            perf_delta_ms=_max + 1.0)
    assert _v["passed"] is False
    assert _v["dims"]["perf_ok"] is False


def test_23_missing_perf_dim_stays_unverifiable():
    """★这就是 411 查出的问题：少一维 ⇒ 恒 None（覆盖不足不是通过）。"""
    _v = _verifier().verify({"id": "pno", "file": "a.py"}, {"type": "x"},
                            problem_gone=True, function_ok=True, perf_delta_ms=None)
    assert _v["passed"] is None
    assert _v["dims"]["perf_ok"] is None
    assert "覆盖不足" in _v["reason"]


def test_24_supplied_perf_turns_unverifiable_into_verdict(tmp_path):
    """★本刀的验收：补上第三维之后，**同一条补丁**从 None 变成 True。"""
    _patch = {"id": "pab", "file": "a.py"}
    _before = _verifier().verify(_patch, {"type": "x"}, problem_gone=True,
                                 function_ok=True, perf_delta_ms=None)
    assert _before["passed"] is None
    _delta = pp.perf_delta_for_patch(_patch_pair(tmp_path, "X = 1\n"), repeats=1)
    assert isinstance(_delta["delta_ms"], float)
    _after = _verifier().verify(_patch, {"type": "x"}, problem_gone=True,
                                function_ok=True, perf_delta_ms=_delta["delta_ms"])
    assert _after["passed"] is True, _after["reason"]


# ---------------------------------------------------------------- D 接线

def test_31_patch_manager_helper_returns_number(tmp_path, monkeypatch):
    import nucleus.reasoning.PatchManager as pm
    monkeypatch.setattr(config, "ENABLE_M412_PERF_DELTA", True, raising=False)
    _f = tmp_path / "t.py"
    _f.write_text("import time\ntime.sleep(0.08)\n", encoding="utf-8")
    _d = pm._m412_perf_delta_ms({"file": str(_f), "original_code": "X = 1\n"},
                                project_root=_ROOT)
    assert isinstance(_d, float) and _d > 10.0


def test_32_helper_returns_none_when_switch_off(tmp_path, monkeypatch):
    import nucleus.reasoning.PatchManager as pm
    monkeypatch.setattr(config, "ENABLE_M412_PERF_DELTA", False, raising=False)
    _f = tmp_path / "t.py"
    _f.write_text("X = 1\n", encoding="utf-8")
    assert pm._m412_perf_delta_ms({"file": str(_f), "original_code": "X = 1\n"}) is None


def test_33_helper_returns_none_when_not_measurable(monkeypatch):
    import nucleus.reasoning.PatchManager as pm
    monkeypatch.setattr(config, "ENABLE_M412_PERF_DELTA", True, raising=False)
    # 没有 original_code ⇒ 测不出来 ⇒ None（不拿 0 冒充）
    assert pm._m412_perf_delta_ms({"file": "不存在.py"}) is None


def test_34_call_site_no_longer_hardcodes_none():
    _s = _src("nucleus/reasoning/PatchManager.py")
    assert "perf_delta_ms=_m412_perf_delta_ms(patch, self._project_root)" in _s
    assert "perf_delta_ms=None,  # 无实测耗时源" not in _s


def test_35_version_marker():
    assert pp.version().startswith("412.")
