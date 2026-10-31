# -*- coding: utf-8 -*-
"""411 批刀3 单测：代码掌握率生产者 + 持久化（code_progress_store）。

钉语义（不钉措辞）：
  * **0 与"没采到"必须可分**：两个都取不到时**不写盘**（写 0/0 比不写更糟，
    它会被当成真实读数）；
  * 读回是**四态**：`ok / stale / missing / broken` —— `stale` 时数值照旧返回，
    `missing / broken` 才置 None；
  * 老格式（无 `ts`）用文件 mtime 兜底并**标记来源**，不假装是"刚写的"；
  * `set_data_dir()` **读写同改**（409 批踩过只改一半的坑）；
  * 落盘原子（无 `.tmp411` 残留）、纯 LF；
  * 取数口 `get_code_learning_stats()` 的第三路**接受 stale**，并如实标 `source`。
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.observability import code_progress_store as cps  # noqa: E402

_T0 = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    cps.set_data_dir(str(tmp_path))
    yield
    cps.set_data_dir(None)


class _FakeField:
    """信息场替身：① 路取不到数（真实环境里也是"5s TTL 的历史查询"）。"""

    def get_current(self, key):  # 替身：永远取不到数
        return {}


def _src(rel):
    _s = io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read()
    return _s.replace("\r\n", "\n")


# ---------------------------------------------------------------- A 写入

def test_01_store_then_load_roundtrip():
    _r = cps.store(120, 218, gen="PulseCodeLearner.test", now=_T0)
    assert _r["ok"] is True
    assert _r["written"]["understood"] == 120
    assert _r["written"]["total_methods"] == 218
    assert _r["written"]["mastery_pct"] == 55.0
    assert _r["written"]["source"] == "live"
    assert os.path.isfile(_r["path"])

    _l = cps.load(now=_T0)
    assert _l["state"] == cps.STATE_OK
    assert (_l["understood"], _l["total_methods"], _l["mastery_pct"]) == (120, 218, 55.0)
    assert _l["gen"] == "PulseCodeLearner.test"
    assert _l["source"] == "live"
    assert _l["age_sec"] == 0.0


def test_02_refuses_to_write_when_both_counts_missing():
    """两个都取不到 ⇒ 不落盘。写 0/0 会被下游当成"掌握率 0%"。"""
    _r = cps.store(None, None)
    assert _r["ok"] is False
    assert "no_counts" in _r["reason"]
    assert _r["written"] is None
    assert not os.path.exists(cps.progress_path()), "拒绝写时不许留下文件"


def test_03_zero_is_a_real_reading_not_missing():
    _r = cps.store(0, 0, now=_T0)
    assert _r["ok"] is True
    assert "mastery_pct" not in _r["written"], "分母 0 时不许算百分比"
    _l = cps.load(now=_T0)
    assert _l["state"] == cps.STATE_OK
    assert _l["understood"] == 0 and _l["total_methods"] == 0
    assert _l["mastery_pct"] is None


def test_04_partial_count_is_allowed_but_no_pct():
    _r = cps.store(5, None, now=_T0)
    assert _r["ok"] is True
    _l = cps.load(now=_T0)
    assert _l["understood"] == 5
    assert _l["total_methods"] is None
    assert _l["mastery_pct"] is None


def test_05_non_numeric_and_bool_are_none_not_zero():
    # 两个都认不出 ⇒ 拒绝写
    assert cps.store("abc", None)["ok"] is False
    # 布尔不算计数（True 会被当成 1，那是编造）
    _r = cps.store(True, 10, now=_T0)
    assert _r["ok"] is True
    assert _r["written"]["understood"] is None
    assert _r["written"]["total_methods"] == 10


def test_06_negative_is_none():
    _r = cps.store(-3, 10, now=_T0)
    assert _r["ok"] is True
    assert _r["written"]["understood"] is None


def test_07_switch_off_refuses_write(monkeypatch):
    import config as _cfg
    monkeypatch.setattr(_cfg, "ENABLE_M411_CODE_PROGRESS", False, raising=False)
    _r = cps.store(1, 2, now=_T0)
    assert _r["ok"] is False
    assert _r["reason"] == "disabled"
    assert not os.path.exists(cps.progress_path())


# ---------------------------------------------------------------- B 四态读回

def test_11_missing_file():
    _l = cps.load(now=_T0)
    assert _l["state"] == cps.STATE_MISSING
    assert _l["reason"] == "file_not_found"
    assert _l["understood"] is None and _l["total_methods"] is None


def test_12_broken_json():
    _p = cps.progress_path()
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    io.open(_p, "w", encoding="utf-8", newline="\n").write("{oops")
    _l = cps.load(now=_T0)
    assert _l["state"] == cps.STATE_BROKEN
    assert "read_or_parse_failed" in _l["reason"]
    assert _l["understood"] is None


def test_13_json_but_not_a_dict_is_broken():
    _p = cps.progress_path()
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    io.open(_p, "w", encoding="utf-8", newline="\n").write("[1,2,3]")
    _l = cps.load(now=_T0)
    assert _l["state"] == cps.STATE_BROKEN
    assert _l["reason"].startswith("not_a_dict")


def test_14_stale_keeps_values_but_marks_state():
    cps.store(30, 60, now=_T0)
    _l = cps.load(now=_T0 + cps.DEFAULT_MAX_AGE_SEC + 1)
    assert _l["state"] == cps.STATE_STALE
    assert (_l["understood"], _l["total_methods"]) == (30, 60)
    assert _l["mastery_pct"] == 50.0
    assert "age_sec>" in _l["reason"]
    # 自定义新鲜期能把它救回来
    assert cps.load(max_age_sec=10 ** 9, now=_T0 + cps.DEFAULT_MAX_AGE_SEC + 1)[
        "state"] == cps.STATE_OK


def test_15_old_format_without_ts_falls_back_to_mtime_and_says_so():
    _p = cps.progress_path()
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    io.open(_p, "w", encoding="utf-8", newline="\n").write(
        json.dumps({"understood": 40, "total_methods": 80}, ensure_ascii=False))
    _l = cps.load(now=time.time())
    assert _l["state"] == cps.STATE_OK
    assert _l["reason"] == "ts_from_mtime"
    assert _l["ts"] is not None and abs(_l["ts"] - os.path.getmtime(_p)) < 2
    assert (_l["understood"], _l["total_methods"]) == (40, 80)


def test_16_ts_not_a_number_also_falls_back():
    _p = cps.progress_path()
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    io.open(_p, "w", encoding="utf-8", newline="\n").write(
        json.dumps({"understood": 1, "total_methods": 2, "ts": "刚刚"}))
    _l = cps.load(now=time.time())
    assert _l["reason"] == "ts_from_mtime"
    assert _l["understood"] == 1


# ---------------------------------------------------------------- C 落盘纪律

def test_21_no_tmp_leftover():
    _r = cps.store(1, 2, now=_T0)
    _dir = os.path.dirname(_r["path"])
    assert [n for n in os.listdir(_dir) if n.endswith(".tmp411")] == []


def test_22_file_is_pure_lf():
    _r = cps.store(9, 10, now=_T0)
    _b = io.open(_r["path"], "rb").read()
    assert _b.count(b"\r\n") == 0
    assert _b.count(b"\n") > 0


def test_23_set_data_dir_changes_read_and_write_together(tmp_path):
    _a = tmp_path / "a"
    _b = tmp_path / "b"
    cps.set_data_dir(str(_a))
    cps.store(7, 14, now=_T0)
    assert os.path.isfile(str(_a / "context" / "code_progress.json"))
    assert cps.load(now=_T0)["understood"] == 7

    cps.set_data_dir(str(_b))
    assert cps.load(now=_T0)["state"] == cps.STATE_MISSING, "换根后不许还读到旧根"
    assert not os.path.exists(str(_b / "context" / "code_progress.json"))


def test_24_creates_parent_dir():
    _p = cps.progress_path()
    assert not os.path.isdir(os.path.dirname(_p))
    assert cps.store(2, 4, now=_T0)["ok"] is True
    assert os.path.isfile(_p)


def test_25_second_write_wins_not_appends():
    cps.store(1, 10, now=_T0)
    cps.store(4, 10, now=_T0 + 5)
    _l = cps.load(now=_T0 + 5)
    assert (_l["understood"], _l["total_methods"]) == (4, 10)
    _raw = json.loads(io.open(cps.progress_path(), encoding="utf-8").read())
    assert isinstance(_raw, dict), "落盘是单个快照对象，不是追加的 list"


def test_26_summary_is_compact_and_keeps_state():
    cps.store(3, 4, now=_T0)
    _s = cps.summary(now=_T0)
    assert _s["state"] == cps.STATE_OK
    assert _s["mastery_pct"] == 75.0
    assert "path" not in _s
    assert _s["source"] == "live"
    # 没有文件时 source 不许编造
    cps.set_data_dir(os.path.join(str(cps.data_root()), "_nope"))
    _s2 = cps.summary(now=_T0)
    assert _s2["state"] == cps.STATE_MISSING
    assert _s2["source"] == ""


# ---------------------------------------------------------------- D 取数口第三路

def test_31_stats_reader_uses_snapshot_when_learner_absent():
    from nucleus import code_learning_stats as cls
    cps.store(50, 100, gen="PulseCodeLearner.test", now=time.time())
    _r = cls.get_code_learning_stats(info_field=_FakeField(), organs={})
    assert _r["understood"] == 50
    assert _r["total_methods"] == 100
    assert _r["mastery_pct"] == 50.0
    assert _r["source"].startswith("code_progress.json:"), _r["source"]
    assert "ok" in _r["source"]
    assert _r["learner_found"] is False


def test_32_stats_reader_accepts_stale_snapshot():
    from nucleus import code_learning_stats as cls
    cps.store(33, 100, now=time.time() - (cps.DEFAULT_MAX_AGE_SEC + 3600))
    _r = cls.get_code_learning_stats(info_field=_FakeField(), organs={})
    assert _r["understood"] == 33
    assert "stale" in _r["source"], "旧快照要用，但必须标 stale（不许当当前值）"


def test_33_stats_reader_does_not_invent_when_no_snapshot():
    from nucleus import code_learning_stats as cls
    _r = cls.get_code_learning_stats(info_field=_FakeField(), organs={},
                                     fallback_total=0)
    assert _r["understood"] is None
    assert _r["mastery_pct"] is None
    assert _r["source"] == "unavailable"
    assert _r["gap"]


# ---------------------------------------------------------------- E 静态接线

def _func_span(path, name):
    """返回 (起始行, 结束行) —— 用 AST 取函数体范围，避免钉整文件字符串。"""
    _s = _src(path)
    _t = ast.parse(_s)
    for _n in ast.walk(_t):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _n.name == name:
            return _s, _n.lineno, max(getattr(_n, "end_lineno", _n.lineno), _n.lineno)
    raise AssertionError("找不到函数 %s" % name)


def test_41_learner_stores_progress_before_exit():
    """必须先落盘、后 `_trace_exit` —— 否则本轮末尾抛错就丢了这一轮读数。

    ★用 AST 取**真调用**的行号：方法里注释也提到了 `_trace_exit`，
      按字符串 index 比大小会被注释骗（首版就栽在这里）。
    """
    _p = "organs/brain/PulseCodeLearner.py"
    _s = _src(_p)
    _fn = None
    for _n in ast.walk(ast.parse(_s)):
        if isinstance(_n, ast.FunctionDef) and _n.name == "_learn_own_code_structure":
            _fn = _n
            break
    assert _fn is not None, "找不到 _learn_own_code_structure"
    _body = "\n".join(_s.split("\n")[_fn.lineno - 1:_fn.end_lineno])
    assert "code_progress_store" in _body, "生产者在学习方法里，不许挂到别处"

    _store = None
    _exit = None
    for _n in ast.walk(_fn):
        if not isinstance(_n, ast.Call) or not isinstance(_n.func, ast.Attribute):
            continue
        _attr = _n.func.attr
        if _attr == "store" and "cps" in ast.dump(_n.func.value):
            _store = _n.lineno if _store is None else min(_store, _n.lineno)
        elif _attr == "_trace_exit":
            _exit = _n.lineno if _exit is None else min(_exit, _n.lineno)
    assert _store is not None, "没找到 cps.store(...) 真调用"
    assert _exit is not None, "没找到 self._trace_exit(...) 真调用"
    assert _store < _exit, "落盘必须在追踪出口之前（第 %d 行 vs 第 %d 行）" % (_store, _exit)


def test_42_context_snapshot_delegates_instead_of_duplicating():
    _s, _a, _b = _func_span("nucleus/mnemosyne/ContextSnapshot.py",
                            "save_code_progress")
    _body = "\n".join(_s.split("\n")[_a - 1:_b])
    assert "code_progress_store" in _body, "唯一写入口，不许第二套写入"
    assert ".store(" in _body


def test_43_stats_reader_third_path_accepts_ok_and_stale():
    _s = _src("nucleus/code_learning_stats.py")
    assert "from nucleus.observability import code_progress_store" in _s
    _i = _s.find("code_progress_store")
    _seg = _s[_i:_i + 900]
    assert '"ok", "stale"' in _seg or "'ok', 'stale'" in _seg, \
        "第三路必须接受 stale（否则旧快照等于没有）"
    assert "code_progress.json:%s" in _seg, "source 要带状态，读数才可解释"


def test_44_version_marker():
    assert cps.version().startswith("411.")
