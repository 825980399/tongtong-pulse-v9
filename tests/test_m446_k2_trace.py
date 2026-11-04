# -*- coding: utf-8 -*-
"""446 刀1 留痕：`detector_trace.jsonl` 真的写了吗（字段齐全 + 开关可关）。"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.evolution import baseline_probe as BP

_ORIG = '''def _schedule_next_beat(self):
    self._timer = threading.Timer(5.0, self._tick)
    self._timer.start()
'''

_MODI = '''def _schedule_next_beat(self):
    if self._busy:
        return
    self._timer = threading.Timer(5.0, self._tick)
'''


def _trace(tmp_path):
    _p = tmp_path / "detector_trace.jsonl"
    if not os.path.exists(str(_p)):
        return []
    with open(str(_p), encoding="utf-8") as _f:
        return [json.loads(_x) for _x in _f if _x.strip()]


def _wire(tmp_path, monkeypatch, on=True):
    monkeypatch.setattr(config, "M446_DETECTOR_TRACE_PATH",
                        str(tmp_path / "detector_trace.jsonl"), raising=True)
    monkeypatch.setattr(config, "ENABLE_M446_DETECTOR_TRACE", on, raising=True)


def test_note_detector_hit_writes_row(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    _ok = BP.note_detector_hit(issue_type="periodic_task_no_reentry",
                               file="organs/body/PulseHeart.py",
                               method="_schedule_next_beat",
                               rule="周期信号=Timer 无重入守卫",
                               baseline=1, after=0, verdict="true_pass",
                               patch_id="patch_x", source="unit")
    assert _ok is True
    _rows = _trace(tmp_path)
    assert len(_rows) == 1, _rows
    _r = _rows[0]
    for _k in ("issue_type", "file", "method", "rule", "baseline"):
        assert _k in _r, (_k, _r)
    assert _r["baseline"] == 1 and _r["source"] == "unit"


def test_note_detector_hit_switch_off(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch, on=False)
    assert BP.note_detector_hit(issue_type="x", file="f", method="m",
                                rule="r", baseline=1) is False
    assert _trace(tmp_path) == []


def test_probe_code_repro_writes_trace_on_hit(tmp_path, monkeypatch):
    """入队基线探针命中 ⇒ 留痕自带 baseline 计数与命中规则。"""
    _wire(tmp_path, monkeypatch)
    _r = BP.probe_code_repro({
        "id": "patch_unit_1",
        "issue_type": "periodic_task_no_reentry",
        "file": "organs/body/PulseHeart.py",
        "method": "_schedule_next_beat",
        "original_code": _ORIG,
        "modified_code": _MODI,
    })
    assert _r["baseline_source"] == BP.SOURCE_CODE_REPRO, _r
    assert _r["baseline_errors"] == 1, _r
    _rows = _trace(tmp_path)
    assert len(_rows) == 1, _rows
    assert _rows[0]["issue_type"] == "periodic_task_no_reentry"
    assert _rows[0]["baseline"] == 1
    assert "Timer" in _rows[0]["rule"], _rows[0]
    assert _rows[0]["verdict"] == "true_pass", _rows[0]


def test_probe_code_repro_no_trace_when_zero_hit(tmp_path, monkeypatch):
    """0 命中 = 复现不出，没有「命中规则」可记 ⇒ 不落行（避免留痕变流水账）。"""
    _wire(tmp_path, monkeypatch)
    _r = BP.probe_code_repro({
        "issue_type": "non_atomic_write",
        "file": "organs/x.py", "method": "_load",
        "original_code": "def _load(self):\n    return open(self.p, 'r').read()\n",
        "modified_code": "def _load(self):\n    return open(self.p, 'r').read()\n",
    })
    assert _r["baseline_errors"] == 0, _r
    assert _trace(tmp_path) == []


def test_trace_path_relative_to_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "M446_DETECTOR_TRACE_PATH",
                        "data/patches/detector_trace.jsonl", raising=True)
    _p = BP.detector_trace_path()
    assert os.path.isabs(_p), _p
    assert _p.replace("\\", "/").endswith("data/patches/detector_trace.jsonl"), _p
