# -*- coding: utf-8 -*-
"""439 刀2 单测：基线主源换 code_repro（静态复现，不依赖日志）。

★核心判据：**基线不再结构性恒 0** —— 用一段真含静默 except 的 original_code，
探针必须数出 ≥1；而同样的补丁在日志口径下永远是 0（438 实测）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
import nucleus.evolution.baseline_probe as bp

_BAD = """def refresh_runtime_params(self):
    try:
        import config as _cfg
        self._v = _cfg.X
    except Exception:
        pass
"""

_GOOD = """def refresh_runtime_params(self):
    try:
        import config as _cfg
        self._v = _cfg.X
    except Exception as e:
        _log.debug("refresh 失败: %s", e)
"""


def _patch(**kw):
    _b = {
        "id": "p2",
        "file": "organs/motor/PulseController.py",
        "method": "refresh_runtime_params",
        "issue_type": "silent_exception",
        "original_code": _BAD,
        "modified_code": _GOOD,
    }
    _b.update(kw)
    return _b


# ---------------------------------------------------------------- ① 取源选择
def test_default_source_is_code_repro():
    assert bp.baseline_source_pref() == bp.SOURCE_CODE_REPRO


def test_source_switch_back_to_log(monkeypatch):
    monkeypatch.setattr(config, "M438_BASELINE_SOURCE", "log", raising=True)
    assert bp.baseline_source_pref() == "log"


def test_illegal_source_falls_back_to_code_repro(monkeypatch):
    monkeypatch.setattr(config, "M438_BASELINE_SOURCE", "nonsense", raising=True)
    assert bp.baseline_source_pref() == bp.SOURCE_CODE_REPRO


# ---------------------------------------------------------------- ② 真复现出基线
def test_code_repro_gives_positive_baseline():
    """★本批核心：含静默 except 的 original_code 必须复现出 ≥1 处（不是 0）。"""
    _r = bp.probe_code_repro(_patch())
    assert _r["baseline_source"] == bp.SOURCE_CODE_REPRO, _r
    assert isinstance(_r["baseline_errors"], int)
    assert _r["baseline_errors"] >= 1, f"仍数到 0：{_r}"
    assert _r["baseline_verdict"] in ("true_pass", "partial_fix"), _r["baseline_verdict"]


def test_code_repro_verdict_reflects_after_hits():
    """modified_code 已修好 ⇒ after=0；判 true_pass（不是 false_pass）。"""
    _r = bp.probe_code_repro(_patch())
    assert _r["baseline_verdict"] != "false_pass", _r


def test_clean_original_gives_zero_but_known_source():
    """原始代码本来就没问题 ⇒ 基线 0，但 source 仍是 code_repro（如实的 0，不是"取不到"）。"""
    _r = bp.probe_code_repro(_patch(original_code=_GOOD))
    assert _r["baseline_source"] == bp.SOURCE_CODE_REPRO
    assert _r["baseline_errors"] == 0


# ---------------------------------------------------------------- ③ 不可判定
def test_unknown_issue_type_is_unavailable():
    _r = bp.probe_code_repro(_patch(issue_type="totally_unknown"))
    assert _r["baseline_source"] == bp.SOURCE_UNAVAILABLE
    assert _r["baseline_errors"] is None


def test_unparsable_code_is_unavailable():
    _r = bp.probe_code_repro(_patch(original_code="def f(:\n  pass"))
    assert _r["baseline_source"] == bp.SOURCE_UNAVAILABLE
    assert _r["baseline_errors"] is None


# ---------------------------------------------------------------- ④ 入队写入
def test_apply_writes_code_repro(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M438_BASELINE_PROBE", True, raising=True)
    monkeypatch.setattr(config, "M438_BASELINE_SOURCE", "code_repro", raising=True)
    _p = _patch()
    _r = bp.apply_to_patch(_p, path=str(tmp_path / "nope.log"))
    assert _r["applied"] is True
    assert _p["baseline_source"] == bp.SOURCE_CODE_REPRO
    assert _p["baseline_errors"] >= 1
    assert _p.get("baseline_verdict")


def test_log_source_still_available_as_backup(tmp_path, monkeypatch):
    """切回 log 口径时行为回到 438（仍不填假 0）。"""
    monkeypatch.setattr(config, "ENABLE_M438_BASELINE_PROBE", True, raising=True)
    monkeypatch.setattr(config, "M438_BASELINE_SOURCE", "log", raising=True)
    _p = _patch()
    bp.apply_to_patch(_p, path=str(tmp_path / "nope.log"))
    assert _p["baseline_source"] == bp.SOURCE_UNAVAILABLE
    assert _p["baseline_errors"] is None
