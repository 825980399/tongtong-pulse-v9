# -*- coding: utf-8 -*-
"""443 刀2：PHASE18 第 7/8 维（性能基线 / 数据一致性）。

钉住三件事：
  ① 第7维「首次只记基线、第二次才打分」，且基线取 best-known（变慢不覆盖基线）；
  ② 第8维**只读**（mark=False），拿不到样本时 `score=None` 而不是 0；
  ③ `DataQualityGuard.scan_experience_files()` 不再空转（443 修的空元组陷阱）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import nucleus.evolution.perf_delta_probe as pdp
from nucleus.self_awareness import get_self_awareness_engine
from nucleus.self_awareness.SelfAwarenessEngine import (
    _PROFILE_DICT_FIELDS,
    SelfAwarenessProfile,
)


def _engine():
    return get_self_awareness_engine()


def _stub_measure(monkeypatch, ms):
    monkeypatch.setattr(pdp, "measure_import_ms",
                        lambda *a, **k: {"state": "ok", "ms": float(ms)}, raising=True)


# ---------------------------------------------------------------- 第7维
def test_perf_baseline_first_run_only_records(tmp_path, monkeypatch):
    _stub_measure(monkeypatch, 10.0)
    _bp = str(tmp_path / "perf.json")
    _r = _engine().integrate_perf_baseline(baseline_path=_bp)
    assert _r["status"] == "baseline_created", _r
    assert _r["score"] is None, "首次观测不该给分（没有可比基线）"
    assert _r["baseline_written"] is True
    assert os.path.exists(_bp)


def test_perf_baseline_second_run_scores(tmp_path, monkeypatch):
    _stub_measure(monkeypatch, 10.0)
    _bp = str(tmp_path / "perf.json")
    _e = _engine()
    _e.integrate_perf_baseline(baseline_path=_bp)
    _r = _e.integrate_perf_baseline(baseline_path=_bp)
    assert _r["status"] == "ok", _r
    assert _r["score"] == 100.0, _r
    assert _r["regressions"] == [], _r


def test_perf_baseline_regression_is_detected(tmp_path, monkeypatch):
    _bp = str(tmp_path / "perf.json")
    _e = _engine()
    _stub_measure(monkeypatch, 10.0)
    _e.integrate_perf_baseline(baseline_path=_bp)
    _stub_measure(monkeypatch, 20.0)          # 2.0× > 阈值 1.5
    _r = _e.integrate_perf_baseline(baseline_path=_bp)
    assert len(_r["regressions"]) >= 1, _r
    assert _r["score"] < 100.0, _r
    _one = _r["regressions"][0]
    assert _one["ratio"] == 2.0, _one


def test_perf_baseline_is_best_known(tmp_path, monkeypatch):
    """变快才更新基线：先 10ms 记基线，再 5ms（更快）替换，再 10ms ⇒ 必须报退化。"""
    _bp = str(tmp_path / "perf.json")
    _e = _engine()
    _stub_measure(monkeypatch, 10.0)
    _e.integrate_perf_baseline(baseline_path=_bp)
    _stub_measure(monkeypatch, 5.0)
    _r2 = _e.integrate_perf_baseline(baseline_path=_bp)
    assert all(v["state"] == "ok" for v in _r2["modules"].values()), _r2
    _stub_measure(monkeypatch, 10.0)
    _r3 = _e.integrate_perf_baseline(baseline_path=_bp)
    _one = _r3["regressions"][0]
    assert _one["baseline_ms"] == 5.0, "变快后基线必须更新为 5ms（best-known）"
    assert _one["ratio"] == 2.0, _one


def test_perf_baseline_no_write_under_test_mode(monkeypatch):
    """测试模式下不传 baseline_path ⇒ 不写生产基线文件（防单测污染）。"""
    monkeypatch.setenv("PULSE_TEST_MODE", "1")
    _stub_measure(monkeypatch, 10.0)
    _r = _engine().integrate_perf_baseline()
    assert _r["baseline_written"] is False, _r


# ---------------------------------------------------------------- 第8维
class _FakeGuard:
    def __init__(self, rows, nodes_stats=None, groups=None):
        self._rows = rows
        self._nodes_stats = nodes_stats or {}
        self._groups = groups or []
        self.mark_args = []

    def scan_experience_files(self, rel_paths=None, mark=False):
        self.rel_paths = rel_paths
        self.mark = mark
        return list(self._rows)

    def scan_nodes(self, nodes, mark=True):
        self.mark_args.append(mark)
        return dict(self._nodes_stats)

    def find_duplicates(self, nodes, **k):
        return list(self._groups)


def _patch_guard(monkeypatch, guard):
    import nucleus.knowledge.DataQualityGuard as dq
    monkeypatch.setattr(dq, "get_data_quality_guard", lambda *a, **k: guard, raising=True)


def test_data_consistency_computes_score(monkeypatch):
    _g = _FakeGuard([{"path": "a.json", "exists": True, "total": 100,
                      "polluted": 10, "suspect": 0, "pollution_rate": 0.1}])
    _patch_guard(monkeypatch, _g)
    _r = _engine().integrate_data_consistency()
    assert _r["status"] == "ok", _r
    assert _r["score"] == 90.0, _r
    assert _r["sampled_total"] == 100, _r


def test_data_consistency_is_read_only(monkeypatch):
    """★只读红线：绝不允许 mark=True（那会把 quality_flag 写回知识库）。"""
    _g = _FakeGuard([{"path": "a.json", "exists": True, "total": 10,
                      "polluted": 0, "suspect": 0}])
    _patch_guard(monkeypatch, _g)
    _engine().integrate_data_consistency()
    assert _g.mark is False, "scan_experience_files 必须 mark=False"
    assert all(m is False for m in _g.mark_args), "scan_nodes 必须 mark=False"


def test_data_consistency_unavailable_does_not_fake_zero(monkeypatch):
    _patch_guard(monkeypatch, _FakeGuard([]))
    _r = _engine().integrate_data_consistency()
    assert _r["status"] == "unavailable", _r
    assert _r["score"] is None, "没有样本时不许填 0（0 分 ≠ 没问题）"


# ---------------------------------------------------------------- 上游修 bug
def test_scan_experience_files_no_longer_empty():
    """443 修的空元组陷阱：不传 rel_paths 时**必须**扫默认清单，而不是返回 []。"""
    from nucleus.knowledge.DataQualityGuard import get_data_quality_guard
    _rows = get_data_quality_guard().scan_experience_files(mark=False)
    assert isinstance(_rows, list) and _rows, "不传参仍返回空 ⇒ 空元组陷阱回归"
    assert any(r.get("exists") for r in _rows), _rows


# ---------------------------------------------------------------- 字段接线
def test_new_dimensions_are_profile_fields():
    assert "perf_baseline" in _PROFILE_DICT_FIELDS
    assert "data_consistency" in _PROFILE_DICT_FIELDS
    _p = SelfAwarenessProfile(perf_baseline={"status": "ok", "score": 100.0},
                              data_consistency={"status": "ok", "score": 90.0})
    _d = _p.to_dict()
    assert _d["perf_baseline"]["score"] == 100.0
    assert _d["data_consistency"]["score"] == 90.0
    _back = SelfAwarenessProfile.from_dict(_d)
    assert _back.perf_baseline["score"] == 100.0
    assert _back.data_consistency["score"] == 90.0
