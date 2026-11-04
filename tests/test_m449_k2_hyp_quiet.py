# -*- coding: utf-8 -*-
"""449 刀2：假设缺判据——首次 WARNING，之后只 DEBUG（同条目不再刷）。"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.mnemosyne.verification_learning_hub import VerificationLearningHub


def _hub(tmp_path, monkeypatch, quiet=True):
    monkeypatch.setattr(config, "ENABLE_M449_HYP_QUIET", quiet, raising=True)
    _h = VerificationLearningHub(
        file_path=str(tmp_path / "vl.json"),
        strategies_path=str(tmp_path / "ls.json"))
    _rec = []

    def _fake_log(level, message, exc_info=False):
        _rec.append((str(getattr(level, "name", level)), message))

    _h._log = _fake_log
    return _h, _rec


def _entry(**kw):
    _e = {"rule": "code_issue_lesson::silent_exception", "organ": "x", "tag": "t",
          "applied_total": 1, "first_seen": time.time(), "last_seen": time.time()}
    _e.update(kw)
    return _e


def test_first_time_warns_then_quiets(tmp_path, monkeypatch):
    """★核心：同一条老条目连扫三轮 —— 1 次 WARNING + 2 次 DEBUG。"""
    _h, _rec = _hub(tmp_path, monkeypatch)
    _e = _entry()
    _lv = []
    for _ in range(3):
        _h._m409_annotate_entry(_e, _e["rule"], False)
        _lv.append(_rec[-1][0])
    assert _lv == ["WARNING", "DEBUG", "DEBUG"], _lv
    assert _e.get("missing_criterion") is True, _e


def test_counter_records_suppressed(tmp_path, monkeypatch):
    _h, _ = _hub(tmp_path, monkeypatch)
    _e = _entry()
    for _ in range(3):
        _h._m409_annotate_entry(_e, _e["rule"], False)
    assert getattr(_h, "_m449_suppressed", 0) == 2, getattr(_h, "_m449_suppressed", None)


def test_already_marked_entry_is_debug_from_the_start(tmp_path, monkeypatch):
    """盘账里本来就带标记的（生产 40/40 条都是）⇒ 第一次调用就只 DEBUG。"""
    _h, _rec = _hub(tmp_path, monkeypatch)
    _e = _entry(missing_criterion=True)
    _h._m409_annotate_entry(_e, _e["rule"], False)
    assert _rec[-1][0] == "DEBUG", _rec[-1]


def test_new_entry_still_warns(tmp_path, monkeypatch):
    """新条目第一次发现必须 WARNING（降噪不能把新问题也压掉）。"""
    _h, _rec = _hub(tmp_path, monkeypatch)
    _e = {"rule": "brand_new", "organ": "x", "tag": "t"}
    _h._m409_annotate_entry(_e, "brand_new", True)
    assert _rec[-1][0] == "WARNING", _rec[-1]
    assert _e.get("missing_criterion") is True, _e
    # 第二轮同一新条目 ⇒ DEBUG
    _h._m409_annotate_entry(_e, "brand_new", True)
    assert _rec[-1][0] == "DEBUG", _rec[-1]


def test_complete_entry_logs_nothing(tmp_path, monkeypatch):
    """判据齐全的条目不告警、也不计数（别把正常件算进"压掉"里）。"""
    _h, _rec = _hub(tmp_path, monkeypatch)
    _e = _entry(observable_criterion={"kind": "recurrence", "name": "x"},
                observation_window="24h", due_at=time.time() + 86400)
    _h._m409_annotate_entry(_e, _e["rule"], True)
    assert _rec == [], _rec


def test_switch_off_restores_warning_every_round(tmp_path, monkeypatch):
    _h, _rec = _hub(tmp_path, monkeypatch, quiet=False)
    _e = _entry()
    for _ in range(3):
        _h._m409_annotate_entry(_e, _e["rule"], False)
        assert _rec[-1][0] == "WARNING", _rec[-1]
    assert getattr(_h, "_m449_suppressed", 0) == 0


def test_debug_message_carries_key_and_count(tmp_path, monkeypatch):
    _h, _rec = _hub(tmp_path, monkeypatch)
    _e = _entry()
    _h._m409_annotate_entry(_e, _e["rule"], False)
    _h._m409_annotate_entry(_e, _e["rule"], False)
    _msg = _rec[-1][1]
    assert "[449刀2]" in _msg, _msg
    assert "已标过" in _msg, _msg
    assert "累计压掉 1 条" in _msg, _msg
    assert _e["rule"][:20] in _msg, _msg
