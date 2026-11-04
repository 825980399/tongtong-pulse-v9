# -*- coding: utf-8 -*-
"""445 刀1：片段→完整方法 包裹器（影子优先 + 全量留痕）。"""
import ast
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.reasoning.PatchManager import PatchManager as PM

_SRC = '''class A:
    def target(self, x: int, y: str = "a") -> bool:
        if not x:
            return False
        self.log("x", x, y)
        return True

    async def atarget(self):
        return 1
'''


def _mk(tmp_path, monkeypatch, mode="shadow"):
    _p = tmp_path / "mod.py"
    _p.write_text(_SRC, encoding="utf-8")
    monkeypatch.setattr(config, "M445_WRAP_FRAGMENT_MODE", mode, raising=True)
    monkeypatch.setattr(config, "M445_WRAP_TRACE_PATH",
                        str(tmp_path / "wrap_trace.jsonl"), raising=True)
    return str(_p)


def _trace(tmp_path):
    _p = tmp_path / "wrap_trace.jsonl"
    if not os.path.exists(_p):
        return []
    with open(_p, encoding="utf-8") as _f:
        return [json.loads(x) for x in _f if x.strip()]


def test_signature_from_ast(tmp_path, monkeypatch):
    _mod = _mk(tmp_path, monkeypatch)
    _sig = PM._m445_find_signature(_mod, "target")
    assert _sig["ok"] is True, _sig
    assert _sig["args"].startswith("self, x: int"), _sig
    assert _sig["indent"] == 4, _sig
    assert _sig["prefix"] == "def", _sig


def test_async_signature_kept(tmp_path, monkeypatch):
    _mod = _mk(tmp_path, monkeypatch)
    _sig = PM._m445_find_signature(_mod, "atarget")
    assert _sig["ok"] is True and _sig["prefix"] == "async def", _sig


def test_wrap_body_only_fragment(tmp_path, monkeypatch):
    _mod = _mk(tmp_path, monkeypatch)
    _patch = {"id": "p1", "file": _mod, "method": "target",
              "modified_code": "        if not x:\n            return False\n"}
    _r = PM._m445_wrap_fragment(_patch)
    assert _r["status"] == "wrapped", _r
    assert _r["parse_ok"] is True, _r
    _after = _r["wrapped"]
    assert _after.lstrip().startswith("def target(self, x: int"), _after[:80]
    # 套类壳后必须能 parse
    ast.parse("class _Shim:\n" + _after)


def test_shadow_mode_does_not_modify(tmp_path, monkeypatch):
    _mod = _mk(tmp_path, monkeypatch, mode="shadow")
    _body = "        return 1\n"
    _patch = {"id": "p2", "file": _mod, "method": "target", "modified_code": _body}
    PM._m445_wrap_fragment(_patch)
    assert _patch["modified_code"] == _body, "shadow 模式竟然改了 modified_code"


def test_apply_mode_writes_back(tmp_path, monkeypatch):
    _mod = _mk(tmp_path, monkeypatch, mode="apply")
    _patch = {"id": "p3", "file": _mod, "method": "target",
              "modified_code": "        return 1\n"}
    _r = PM._m445_wrap_fragment(_patch)
    assert _r["status"] == "wrapped" and _r["applied"] is True, _r
    assert "def target(self, x: int" in _patch["modified_code"]
    # 284 的 def 判据必须认得出来
    assert PM._m280_extract_def_name(_patch["modified_code"]) == "target"


def test_already_full_is_skipped(tmp_path, monkeypatch):
    _mod = _mk(tmp_path, monkeypatch, mode="apply")
    _code = "def target(self, x):\n    return x\n"
    _patch = {"id": "p4", "file": _mod, "method": "target", "modified_code": _code}
    _r = PM._m445_wrap_fragment(_patch)
    assert _r["status"] == "already_full", _r
    assert _patch["modified_code"] == _code


def test_missing_signature_is_reported(tmp_path, monkeypatch):
    _mod = _mk(tmp_path, monkeypatch)
    _patch = {"id": "p5", "file": _mod, "method": "no_such_method",
              "modified_code": "        return 1\n"}
    _r = PM._m445_wrap_fragment(_patch)
    assert _r["status"] == "no_signature", _r
    assert _r["parse_ok"] is False


def test_trace_has_full_before_and_after(tmp_path, monkeypatch):
    _mod = _mk(tmp_path, monkeypatch)
    _body = "        if not x:\n            return False\n"
    _patch = {"id": "p6", "file": _mod, "method": "target", "modified_code": _body}
    PM._m445_wrap_fragment(_patch)
    _rows = _trace(tmp_path)
    assert len(_rows) == 1, _rows
    _r = _rows[0]
    for _k in ("patch_id", "file", "method", "mode", "status", "parse_ok",
               "elapsed_ms", "signature", "modified_code_before", "modified_code_after"):
        assert _k in _r, f"留痕缺字段 {_k}"
    assert _r["modified_code_before"] == _body, "修改前内容必须完整留痕"
    assert "def target(" in _r["modified_code_after"], "修改后内容必须完整留痕"


def test_off_mode_does_nothing(tmp_path, monkeypatch):
    _mod = _mk(tmp_path, monkeypatch, mode="off")
    _patch = {"id": "p7", "file": _mod, "method": "target",
              "modified_code": "        return 1\n"}
    _r = PM._m445_wrap_fragment(_patch)
    assert _r["status"] == "skipped", _r
    assert _trace(tmp_path) == [], "off 模式不该留痕"
