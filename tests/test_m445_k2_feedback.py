# -*- coding: utf-8 -*-
"""445 刀2：284 作废反馈回灌生成侧。"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.evolution import criteria_window as cw
from nucleus.reasoning.SafeEvolutionExecutor import (
    M445_FULL_DEF_REQUIREMENT,
    _m445_append_full_def_requirement,
)


def _ledger(tmp_path, monkeypatch):
    _p = str(tmp_path / "generation_feedback.jsonl")
    monkeypatch.setattr(config, "M445_FEEDBACK_WINDOW_DAYS", 7.0, raising=True)
    monkeypatch.setattr(cw, "generation_feedback_path", lambda path=None: path or _p,
                        raising=True)
    return _p


def test_note_and_streak(tmp_path, monkeypatch):
    _p = _ledger(tmp_path, monkeypatch)
    assert cw.no_def_streak("a.py", "m", path=_p)["streak"] == 0
    cw.note_generation_feedback("a.py", "m", reason="no_def", streak=1, path=_p)
    cw.note_generation_feedback("a.py", "m", reason="no_def", streak=2, path=_p)
    _st = cw.no_def_streak("a.py", "m", path=_p)
    assert _st["records"] == 2, _st
    assert _st["streak"] == 2, "连续 no_def 次数算错"


def test_streak_resets_on_other_reason(tmp_path, monkeypatch):
    _p = _ledger(tmp_path, monkeypatch)
    cw.note_generation_feedback("b.py", "m", reason="no_def", streak=1, path=_p)
    cw.note_generation_feedback("b.py", "m", reason="mismatch", streak=0, path=_p)
    cw.note_generation_feedback("b.py", "m", reason="no_def", streak=1, path=_p)
    _st = cw.no_def_streak("b.py", "m", path=_p)
    assert _st["streak"] == 1, "遇到非 no_def 记录后应从 0 重新数"


def test_window_excludes_old_records(tmp_path, monkeypatch):
    _p = _ledger(tmp_path, monkeypatch)
    _old = time.time() - 8 * 86400
    cw.note_generation_feedback("c.py", "m", reason="no_def", streak=1,
                                path=_p, now=_old)
    _st = cw.no_def_streak("c.py", "m", path=_p)
    assert _st["streak"] == 0, "超过 7 天窗口的记录不该计"


def test_ledger_record_has_required_fields(tmp_path, monkeypatch):
    _p = _ledger(tmp_path, monkeypatch)
    cw.note_generation_feedback("d.py", "m", reason="no_def", streak=3,
                                strategy="require_full_def", path=_p,
                                patch_id="pid1")
    with open(_p, encoding="utf-8") as _f:
        _r = json.loads(_f.readline())
    for _k in ("ts", "file", "method", "reason", "streak", "strategy"):
        assert _k in _r, f"反馈台账缺字段 {_k}"
    assert _r["streak"] == 3 and _r["strategy"] == "require_full_def"


def test_prompt_tail_appended_only_when_flag():
    assert _m445_append_full_def_requirement("base", {}) == "base"
    assert _m445_append_full_def_requirement("base", None) == "base"
    _out = _m445_append_full_def_requirement("base", {"m445_require_full_def": True})
    assert _out.startswith("base")
    assert M445_FULL_DEF_REQUIREMENT in _out
    # 幂等：重复追加不重复
    assert _m445_append_full_def_requirement(_out, {"m445_require_full_def": True}) == _out


def test_generation_side_reads_feedback_source_level():
    """源码级：`_generate_llm_patch` 必须读反馈并按阈值置 flag（防接线回潮）。"""
    with open(os.path.join(_ROOT, "nucleus", "reasoning",
                           "SafeEvolutionExecutor.py"), encoding="utf-8") as _f:
        _src = _f.read()
    assert "no_def_streak(" in _src
    assert 'issue["m445_require_full_def"] = True' in _src
    assert "_m445_append_full_def_requirement(_prompt, issue)" in _src


def test_patch_manager_writes_feedback_source_level():
    with open(os.path.join(_ROOT, "nucleus", "reasoning",
                           "PatchManager.py"), encoding="utf-8") as _f:
        _src = _f.read()
    assert "note_generation_feedback(" in _src
    assert "no_def_streak(" in _src
