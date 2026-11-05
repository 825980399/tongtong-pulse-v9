# -*- coding: utf-8 -*-
"""457 刀4/刀5：意图影子「必须真的跑到」+ 样本落盘。

★刀4 的实测根因（本文件把它钉成回归护栏）：
  456 把 `_m456_intent_shadow` 挂在 `_ir_dispatch_qica_method` 最前面，但真实请求
  绝大多数在 `_ir_run_detectors` 就被 `_detect_experience_route` → `_fallback_to_lung_model`
  短路掉 ⇒ **dispatch 根本走不到** ⇒ 上线至今 0 条 shadow 记录、trace 里
  `qica_intent_type/would_handler` 恒 null。本批把调用点前移到 `_m456_request_core` 首行。

★刀5 的纪律：单测不得写生产留痕（446 批教训），故路径可由 `_M457_SAMPLE_FILE` 覆盖。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from nucleus import dialogue_process_trace as TR
from nucleus.reasoning import hypothesize as H
from organs.brain.PulseInnerWorld import PulseInnerWorld as IW


class _Ctx:
    def __init__(self, question="这个函数报错了怎么修", intent_type="技术推理",
                 memory_context="", handled_by=""):
        self.question = question
        self.correlation_id = "cid-457"
        self._memory_context = memory_context
        self.payload = {"strategy_context": {
            "qica_intent_type": intent_type,
            "handled_by": handled_by,
        }}


@pytest.fixture()
def iw():
    _i = IW.__new__(IW)
    _i._log = lambda *a, **k: None
    return _i


@pytest.fixture()
def stamped(monkeypatch):
    _box = {}
    monkeypatch.setattr(TR, "stamp", lambda cid, **kw: _box.update(kw))
    return _box


@pytest.fixture()
def sample_file(tmp_path, monkeypatch):
    _p = tmp_path / "intent_samples.jsonl"
    monkeypatch.setattr(IW, "_M457_SAMPLE_FILE", str(_p), raising=False)
    monkeypatch.setattr(IW, "_M457_SAMPLE_N", -1, raising=False)
    monkeypatch.setattr(config, "ENABLE_M457_INTENT_SAMPLE", True, raising=False)
    yield _p
    IW._M457_SAMPLE_FILE = ""
    IW._M457_SAMPLE_N = -1


# ---------------------------------------------------------------- 刀4 · 调用点
def test_shadow_runs_before_any_early_return(iw, monkeypatch):
    """★核心回归：即便第一条分支就返回，影子也必须已经跑过。"""
    _seen = []
    monkeypatch.setattr(IW, "_m456_intent_shadow",
                        lambda self, ctx: _seen.append(ctx) or {})
    monkeypatch.setattr(IW, "_ir_try_explicit_search", lambda self, ctx: {"status": "x"})
    _out = iw._m456_request_core(_Ctx(), {})
    assert _out == {"status": "x"}
    assert len(_seen) == 1, "影子必须在 _m456_request_core 的首行执行"


def test_shadow_runs_once_per_request(iw, stamped, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    _ctx = _Ctx()
    _calls = []
    monkeypatch.setattr(TR, "stamp", lambda cid, **kw: _calls.append(kw) or None)
    iw._m456_intent_shadow(_ctx)
    iw._m456_intent_shadow(_ctx)      # dispatch 处的第二次调用
    assert len(_calls) == 1, "同一请求只允许预演一次"


def test_shadow_log_has_full_chain(iw, stamped, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    _logs = []
    iw._log = lambda level, msg="", *a, **k: _logs.append(str(msg))
    iw._m456_intent_shadow(_Ctx("这个函数报错了怎么修", "技术推理"))
    _hit = [x for x in _logs if "[457刀4] intent_shadow:" in x]
    assert _hit, _logs
    assert "qica_intent_type=技术推理" in _hit[0]
    assert "would_handler=code_reasoning" in _hit[0]
    assert "handled_by=-" in _hit[0]


def test_begin_request_registers_intent_and_chain(iw):
    H.end_chain()
    iw._m457_begin_request(_Ctx(intent_type="元认知报告"))
    assert IW._m457_intent() == "元认知报告"
    assert H.chain_elapsed() is not None
    H.end_chain()


def test_intent_pass_blocks_code_intent(iw):
    IW._m457_set_intent("技术推理")
    assert iw._m457_intent_pass() is False
    IW._m457_set_intent("元认知报告")
    assert iw._m457_intent_pass() is True
    IW._m457_set_intent("")


# ---------------------------------------------------------------- 刀5 · 样本
def test_sample_row_has_four_fields(iw, stamped, sample_file, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    iw._m456_intent_shadow(_Ctx("这个函数报错了怎么修", "技术推理"))
    _lines = [ln for ln in sample_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(_lines) == 1, _lines
    _row = json.loads(_lines[0])
    assert _row["query"].startswith("这个函数报错")
    assert _row["qica_intent_type"] == "技术推理"
    assert _row["would_handler"] == "code_reasoning"
    assert _row["expected_intent"] == "", "人工标注位必须留空，机器不得猜"


def test_sample_appends_not_overwrites(iw, stamped, sample_file, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    iw._m456_intent_shadow(_Ctx("这个函数报错了", "技术推理"))
    iw._m456_intent_shadow(_Ctx("你最近运行得怎么样", "元认知报告"))
    _n = len([x for x in sample_file.read_text(encoding="utf-8").splitlines() if x.strip()])
    assert _n == 2


def test_sample_switch_off_writes_nothing(iw, stamped, sample_file, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M457_INTENT_SAMPLE", False, raising=False)
    iw._m456_intent_shadow(_Ctx("这个函数报错了", "技术推理"))
    assert not sample_file.exists() or sample_file.read_text(encoding="utf-8").strip() == ""


def test_sample_counts_every_10(iw, sample_file, monkeypatch, caplog):
    monkeypatch.setattr(config, "ENABLE_M456_INTENT_SHADOW", True, raising=False)
    monkeypatch.setattr(TR, "stamp", lambda *a, **k: None)
    for _ in range(10):
        IW._m457_sample_write("q", "技术推理", "code_reasoning")
    assert IW._M457_SAMPLE_N == 10, IW._M457_SAMPLE_N
    _txt = sample_file.read_text(encoding="utf-8")
    assert len([x for x in _txt.splitlines() if x.strip()]) == 10


def test_sample_never_writes_production_path_under_pytest(iw, monkeypatch):
    """★446 批教训：pytest 经生产代码路径会灌假留痕 ⇒ 默认路径必须被护栏挡住。"""
    monkeypatch.setattr(IW, "_M457_SAMPLE_FILE", "", raising=False)
    _prod = os.path.abspath(os.path.join("data", "eval", "intent_samples.jsonl"))
    _before = os.path.getsize(_prod) if os.path.exists(_prod) else -1
    IW._m457_sample_write("这个函数报错了", "技术推理", "code_reasoning")
    _after = os.path.getsize(_prod) if os.path.exists(_prod) else -1
    assert _before == _after, "pytest 下不得写生产样本文件"
