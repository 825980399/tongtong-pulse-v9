# -*- coding: utf-8 -*-
"""492-4 / 492-5 / 492-6 / 492-7 观测侧单测（每刀 on/off）。"""
import importlib.util
import json
import os

import pytest

import config
from nucleus import m492_hypothesis_log as HL
from organs.brain.PulseCortex import _m492_on_emit
from organs.brain.PulseInnerWorld import PulseInnerWorld as IW

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ANN = os.path.join(_ROOT, "tools", "m492_annotate_suggest.py")

_SWITCHES = (
    "ENABLE_M492_INTENT_SAMPLE_EXPAND",
    "ENABLE_M492_WOULD_METHOD_LOOKUP",
    "ENABLE_M492_HYPOTHESIS_LOG",
    "M492_HYPOTHESIS_LOG_PATH",
    "ENABLE_M457_INTENT_SAMPLE",
)


@pytest.fixture
def cfg():
    _saved = {k: getattr(config, k, None) for k in _SWITCHES}
    _f, _n = IW._M457_SAMPLE_FILE, IW._M457_SAMPLE_N
    yield config
    for k, v in _saved.items():
        if v is None:
            if hasattr(config, k):
                delattr(config, k)
        else:
            setattr(config, k, v)
    IW._M457_SAMPLE_FILE, IW._M457_SAMPLE_N = _f, _n


@pytest.fixture
def sample_file(tmp_path, cfg):
    _p = tmp_path / "intent_samples.jsonl"
    cfg.ENABLE_M457_INTENT_SAMPLE = True
    IW._M457_SAMPLE_FILE = str(_p)
    IW._M457_SAMPLE_N = -1
    return _p


def _lines(p):
    if not os.path.exists(str(p)):
        return []
    with open(str(p), encoding="utf-8") as _f:
        return [json.loads(x) for x in _f if x.strip()]


# ---------------------------------------------------------------- 492-4
def test_4924_filter_synthetic(sample_file, cfg):
    """三种测试桩标记全部跳过，只留真实用户一行。"""
    cfg.ENABLE_M492_INTENT_SAMPLE_EXPAND = True
    cfg.ENABLE_M492_WOULD_METHOD_LOOKUP = False
    IW._m457_sample_write("你是谁", "身份确认", "")
    IW._m457_sample_write("acc1", "一般对话", "", {"is_synthetic": True})
    IW._m457_sample_write("acc2", "一般对话", "", {"source_organ": "acc"})
    IW._m457_sample_write("acc3", "一般对话", "", {"origin": "synthetic"})
    _rows = _lines(sample_file)
    assert len(_rows) == 1
    assert _rows[0]["origin"] == "user"


def test_4924_off_writes_synthetic_and_no_origin(sample_file, cfg):
    """关开关逐字回改前：不过滤、样本行仍 5 字段。"""
    cfg.ENABLE_M492_INTENT_SAMPLE_EXPAND = False
    IW._m457_sample_write("acc", "一般对话", "", {"source_organ": "acc"})
    _rows = _lines(sample_file)
    assert len(_rows) == 1
    assert set(_rows[0]) == {"ts", "query", "qica_intent_type",
                            "would_handler", "expected_intent"}


def test_4924_every_override_does_not_change_row_count(sample_file, cfg):
    """★T0：_every 只控日志频率，不控样本条数（任务书前提有误）。"""
    cfg.ENABLE_M492_INTENT_SAMPLE_EXPAND = True
    cfg.M492_INTENT_SAMPLE_EVERY = 3
    for _i in range(7):
        IW._m457_sample_write("q%d" % _i, "一般对话", "")
    assert len(_lines(sample_file)) == 7


def test_4924_cortex_emit_switch(cfg):
    cfg.ENABLE_M492_INTENT_SAMPLE_EXPAND = True
    assert _m492_on_emit() is True
    cfg.ENABLE_M492_INTENT_SAMPLE_EXPAND = False
    assert _m492_on_emit() is False


# ---------------------------------------------------------------- 492-5
def test_4925_would_method_on(sample_file, cfg):
    cfg.ENABLE_M492_INTENT_SAMPLE_EXPAND = True
    cfg.ENABLE_M492_WOULD_METHOD_LOOKUP = True
    IW._m457_sample_write("q", "技术推理", "")
    _row = _lines(sample_file)[0]
    assert _row["would_method"] == "cognitive_compute"
    assert _row["would_handler"] == ""          # 既有契约不动


def test_4925_off_no_field(sample_file, cfg):
    cfg.ENABLE_M492_INTENT_SAMPLE_EXPAND = True
    cfg.ENABLE_M492_WOULD_METHOD_LOOKUP = False
    IW._m457_sample_write("q", "技术推理", "")
    assert "would_method" not in _lines(sample_file)[0]


def test_4925_would_handler_contract_unchanged(cfg):
    """接表不得破坏既有 would_handler 九宫格契约。"""
    _h, _why = IW._m456_would_handler("一般对话", "随便聊聊", 0)
    assert _h == ""
    _h2, _ = IW._m456_would_handler("元认知报告", "x", 0)
    assert _h2 == "self_report"


# ---------------------------------------------------------------- 492-6
def _hyp():
    return [{"claim": "假设A", "confidence": 0.72, "evidence_ids": ["E1"],
             "assumptions": ["前提"], "test": "看读数"}]


def test_4926_record_generation_side(tmp_path, cfg):
    _p = str(tmp_path / "hypothesis_hits.jsonl")
    cfg.ENABLE_M492_HYPOTHESIS_LOG = True
    cfg.M492_HYPOTHESIS_LOG_PATH = _p
    _rows = HL.record(_hyp(), question="q", correlation_id="c1")
    assert len(_rows) == 1
    assert _rows[0]["supported"] is None        # 命中侧接口占位，不是判定
    assert "hit_rate" not in _rows[0]
    assert _rows[0]["claim"] == "假设A"
    assert os.path.exists(_p)


def test_4926_off_no_write(tmp_path, cfg):
    _p = str(tmp_path / "off.jsonl")
    cfg.ENABLE_M492_HYPOTHESIS_LOG = False
    cfg.M492_HYPOTHESIS_LOG_PATH = _p
    assert HL.record(_hyp()) == []
    assert not os.path.exists(_p)


def test_4926_push_pending_still_caps_two(cfg):
    """加台账不得影响 ContextVar 的「最多 2 条」契约。"""
    cfg.ENABLE_M492_HYPOTHESIS_LOG = False
    IW._M456_HYPS.set(None)
    IW._m456_push_pending([_hyp()[0], _hyp()[0], _hyp()[0]])
    assert len(IW._M456_HYPS.get() or []) == 2
    IW._M456_HYPS.set(None)


# ---------------------------------------------------------------- 492-7
def _ann():
    spec = importlib.util.spec_from_file_location("m492_ann_test", _ANN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_4927_suggested_intent_only(tmp_path, cfg):
    m = _ann()
    _p = tmp_path / "samples.jsonl"
    _p.write_text(json.dumps({"ts": "t", "query": "你是谁",
                              "qica_intent_type": "", "would_handler": "",
                              "expected_intent": ""}, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    _rules = m._load_rules(m._QICA_SRC)
    _valid = m._valid_intents()
    _rows = m.collect(str(_p), _rules, _valid)
    assert len(_rows) == 1
    assert _rows[0]["suggested_intent"] == "身份确认"
    assert _rows[0]["expected_intent"] == ""   # 绝不写人工真值
    assert set(_rows[0]) - {"ts", "query", "qica_intent_type",
                           "would_handler", "expected_intent"} == {"suggested_intent"}


def test_4927_never_inplace(sample_file, cfg):
    """脚本没有 --inplace 参数，且 collect 不修改输入文件。"""
    m = _ann()
    _src = open(_ANN, encoding="utf-8").read()
    assert 'add_argument("--inplace"' not in _src
    _p = sample_file
    _p.write_text(json.dumps({"query": "你是谁", "expected_intent": ""},
                             ensure_ascii=False) + "\n", encoding="utf-8")
    _before = _p.read_text(encoding="utf-8")
    m.collect(str(_p), m._load_rules(m._QICA_SRC), m._valid_intents())
    assert _p.read_text(encoding="utf-8") == _before
