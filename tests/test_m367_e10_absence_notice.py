# -*- coding: utf-8 -*-
"""E10 · 缺席提示器（T-350开闸回载缺口-1）。

守的契约（这不是"降级/静默"，而是按状态变化留痕，铁律 156）：

| 场景 | 是否留痕 |
|---|---|
| 首次发现不存在 | ✅（说明机制未启动） |
| 连续多拍仍不存在 | ❌（同一事实重复，实测 2 条/拍） |
| 曾存在 → 现在不存在 | ✅（**台账被清＝事故信号**，绝不能吞） |
| 不存在 → 出现 | ✅（状态变化） |
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from nucleus import absence_notice as an


@pytest.fixture(autouse=True)
def _clean():
    an.reset()
    yield
    an.reset()


class TestAbsenceNotice:
    def test_01_first_absent_notifies_then_repeat_silent(self):
        """首次缺席要留痕；之后连续多拍同一事实不再打。"""
        p = "/tmp/e10/a.json"
        assert an.note_absent(p) is True, "首次缺席必须留痕"
        for _ in range(5):
            assert an.note_absent(p) is False, "重复缺席不得再打"
        _st = an.absence_stats()[p]["counts"]
        assert _st["absent_first"] == 1
        assert _st["absent_repeat"] == 5

    def test_02_present_then_absent_is_incident(self):
        """曾存在→现在不存在＝台账被清，**必须重新留痕**（事故信号）。"""
        p = "/tmp/e10/b.json"
        assert an.note_present(p) is True
        assert an.note_absent(p) is True, "从存在变缺席必须留痕"
        assert an.note_absent(p) is False
        _c = an.absence_stats()[p]["counts"]
        assert _c["absent_after_present"] == 1

    def test_03_absent_then_present_notifies_once(self):
        """缺席→出现：只在变化那一次留痕。"""
        p = "/tmp/e10/c.json"
        an.note_absent(p)
        assert an.note_present(p) is True
        assert an.note_present(p) is False
        assert an.absence_stats()[p]["counts"]["present_after_absent"] == 1

    def test_04_state_of_three_valued(self):
        """状态三值：None（未知）/ False / True —— 不把未知当缺席。"""
        p = "/tmp/e10/d.json"
        assert an.state_of(p) is None
        an.note_absent(p)
        assert an.state_of(p) is False
        an.note_present(p)
        assert an.state_of(p) is True

    def test_05_paths_independent(self):
        """不同路径互不影响（两份台账各记各的）。"""
        a, b = "/tmp/e10/x.json", "/tmp/e10/y.json"
        an.note_absent(a)
        assert an.note_absent(a) is False
        assert an.note_absent(b) is True, "另一路径的首次必须留痕"

    def test_06_live_gate_logs_once_for_missing_ledger(self, tmp_path, monkeypatch):
        """集成：`live_gate._load_state` 连续读不存在的台账 ⇒ 只打一条 DEBUG。"""
        from nucleus.mnemosyne import live_gate as lg

        _calls = []

        class _Rec:
            def debug(self, *a, **k):
                _calls.append(("debug", a))

            def info(self, *a, **k):
                _calls.append(("info", a))

            def warning(self, *a, **k):
                _calls.append(("warning", a))

        monkeypatch.setattr(lg, "_module_logger", _Rec())
        _p = str(tmp_path / "nope.json")
        for _ in range(4):
            st = lg._load_state(_p)
            assert st["_read"] == "absent"
        _hit = [c for c in _calls if "台账不存在" in str(c)]
        assert len(_hit) == 1, "缺席日志打了 %d 次（应只 1 次）: %s" % (len(_hit), _hit)

    def test_07_live_gate_notifies_again_after_ledger_cleared(self, tmp_path, monkeypatch):
        """集成：台账先存在、后被清掉 ⇒ 第二次缺席要再留痕（事故信号）。"""
        from nucleus.mnemosyne import live_gate as lg
        import json as _json

        _calls = []

        class _Rec:
            def debug(self, *a, **k):
                _calls.append(str(a))

            def info(self, *a, **k):
                pass

            def warning(self, *a, **k):
                pass

        monkeypatch.setattr(lg, "_module_logger", _Rec())
        _p = str(tmp_path / "s.json")
        with open(_p, "w", encoding="utf-8") as f:
            _json.dump({"stage": "shadow"}, f)
        assert lg._load_state(_p)["_read"] == "ok"
        os.remove(_p)
        lg._load_state(_p)
        lg._load_state(_p)
        _hit = [c for c in _calls if "台账不存在" in c]
        assert len(_hit) == 1, "清理后应刚好再打 1 次，实际 %d" % len(_hit)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
