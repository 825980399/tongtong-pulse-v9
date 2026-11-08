# -*- coding: utf-8 -*-
"""477 批门控单测：以曈曈为主、LLM 为辅 —— LLM 产物先过曈曈修订。

要点：
① `draft_from_patch` 必须回**修订后的代码本体** `revision_code`（此前只有指标）；
② `_m477_tongtong_revise` 只在「曈曈真的改了 + 语法通过 + 无裸 except」时才 applied；
③ 接线必须真的在 `_m469_align_method_patch` 之前（防回潮）；
④ 开关关闭时零行为变化。
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("PULSE_FRAMEWORK", "1")

import config
from nucleus.autonomy.patch_draft import draft_from_patch
from nucleus.reasoning.SafeEvolutionExecutor import _m477_tongtong_revise


def _src(rel: str) -> str:
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8", errors="replace") as _f:
        return _f.read()


_BARE_EXCEPT_PATCH = {
    "id": "t477_bare_except",
    "file": "organs/body/PulseLiver.py",
    "method": "foo",
    "issue_type": "silent_exception",
    "original_code": (
        "def foo(self):\n"
        "    try:\n"
        "        x()\n"
        "    except Exception:\n"
        "        pass\n"
    ),
    "modified_code": (
        "def foo(self):\n"
        "    try:\n"
        "        x()\n"
        "    except Exception:\n"
        "        pass\n"
    ),
}


class TestRevisionCodeField:
    def test_draft_returns_revision_code(self):
        _rec = draft_from_patch(dict(_BARE_EXCEPT_PATCH))
        assert _rec.get("status") == "ok", _rec
        assert "revision_code" in _rec, "必须回修订后的代码本体"
        assert isinstance(_rec["revision_code"], str)


class TestTongtongRevise:
    def test_switch_off_returns_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "ENABLE_M477_TONGTONG_PRIMARY", False)
        _r = _m477_tongtong_revise(dict(_BARE_EXCEPT_PATCH))
        assert _r.get("applied") is False and _r.get("why") == "disabled"

    def test_bad_patch_no_raise(self):
        _r = _m477_tongtong_revise(None)
        assert _r.get("applied") is False

    def test_no_original_not_applied(self):
        _r = _m477_tongtong_revise({"id": "x", "modified_code": "def f():\n    pass\n"})
        assert _r.get("applied") is False

    def test_bare_except_is_revised(self):
        _r = _m477_tongtong_revise(dict(_BARE_EXCEPT_PATCH))
        assert _r.get("applied") is True, _r
        _code = _r["code"]
        assert _code != _BARE_EXCEPT_PATCH["modified_code"], "必须真的改了"
        assert "silent_exc" in _code, "裸 except 必须被补上 silent_exc"
        ast.parse(_code)  # 修订后必须语法通过
        assert "except Exception:\n        pass" not in _code


class TestWiringAntiRegression:
    def test_revise_called_before_align(self):
        _s = _src("nucleus/reasoning/SafeEvolutionExecutor.py")
        assert "def _m477_tongtong_revise" in _s
        _i_rev = _s.index("_m477_rv = _m477_tongtong_revise(_llm_patch)")
        _i_align = _s.index("_m469_al = _m469_align_method_patch(_llm_patch)")
        assert _i_rev < _i_align, "曈曈修订必须发生在粒度对齐之前"

    def test_switch_exists(self):
        assert config.ENABLE_M477_TONGTONG_PRIMARY is True
