# -*- coding: utf-8 -*-
"""177批刀1 隔离单测：T-对话并行双调用-1 —— 同一轮对话肺渠道委派收敛为单次。

背景（pulse.log 实测 2026-10-10 01:06:56~01:07:03）：
  同一长输入「先总结一下…然后规划…最后生成…」被切分/经不同兜底路径发射，
  两次 SELECT_MODEL 的 prompt 不同 ⇒ 旧幂等键（含 prompt_hash）无法收敛
  ⇒ 肺渠道 01:06:59 并行两调用（3.36s/732字 + 3.99s/917字）⇒ 两条控制台输出。

本单测断言「同一 correlation_id 的对话委派仅一次」，并守住三个安全边界
（补救 / 后台 / 空 cid 内在自主）不被误伤。

隔离纪律（铁律151）：清理 PYTHONPATH/PYTHONHOME/PYTHONSTARTUP + PYTHONNOUSERSITE；
用 __new__ 构造避开 __init__ 的重量初始化与后台线程。
"""
import os

os.environ.pop("PYTHONPATH", None)
os.environ.pop("PYTHONHOME", None)
os.environ.pop("PYTHONSTARTUP", None)
os.environ["PYTHONNOUSERSITE"] = "1"

import threading  # noqa: E402

import config as _cfg_mod  # noqa: E402

_CID = "ctx:1:1791565609139"


def _make_cortex():
    from organs.brain.PulseCortex import PulseCortex

    c = PulseCortex.__new__(PulseCortex)
    c._turn_emit_lock = threading.Lock()
    c._turn_emit_seen = {}
    c._turn_emit_dedup_count = 0
    # 异常兜底路径才会调用，正常路径不触及
    c._log_ignored_exception = lambda e, ctx: None
    return c


def test_same_cid_different_prompt_converges(monkeypatch):
    """核心场景：同一 cid、prompt 不同（长输入被切分）⇒ 第二次被收敛。"""
    monkeypatch.setattr(_cfg_mod, "ENABLE_DIALOG_SELECT_MODEL_DEDUP", True)
    c = _make_cortex()
    # 第一次：完整长输入
    assert c._claim_select_model_emit(_CID, "先总结一下你当前的状态，然后规划今晚的学习计划，最后生成三条行动建议") is True
    # 第二次：被切分的子问句（旧键因 prompt_hash 不同而放过，正是双响应成因）
    assert c._claim_select_model_emit(_CID, "先总结一下你当前的状态") is False, \
        "同一 cid 的第二次对话委派必须被收敛（否则双调用→双响应）"
    assert c._turn_emit_dedup_count == 1


def test_different_cid_allowed(monkeypatch):
    """不同轮（cid 不同）⇒ 各自允许一次，不误伤。"""
    monkeypatch.setattr(_cfg_mod, "ENABLE_DIALOG_SELECT_MODEL_DEDUP", True)
    c = _make_cortex()
    assert c._claim_select_model_emit("ctx:1:AAA", "问题甲") is True
    assert c._claim_select_model_emit("ctx:2:BBB", "问题甲") is True


def test_remediation_not_blocked_by_dialog_key(monkeypatch):
    """补救调用（is_remediation=True）独立 key ⇒ 不被对话 cid 键误伤。"""
    monkeypatch.setattr(_cfg_mod, "ENABLE_DIALOG_SELECT_MODEL_DEDUP", True)
    c = _make_cortex()
    assert c._claim_select_model_emit(_CID, "原始问题", is_remediation=False) is True
    assert c._claim_select_model_emit(_CID, "补救提示", is_remediation=True) is True


def test_background_not_blocked_by_dialog_key(monkeypatch):
    """后台学习（is_background=True）独立 key ⇒ 不被误伤。"""
    monkeypatch.setattr(_cfg_mod, "ENABLE_DIALOG_SELECT_MODEL_DEDUP", True)
    c = _make_cortex()
    assert c._claim_select_model_emit(_CID, "对话问题", is_background=False) is True
    assert c._claim_select_model_emit(_CID, "后台消化", is_background=True) is True


def test_empty_cid_not_converged(monkeypatch):
    """空 cid（内在自主运行）不套 cid 键 ⇒ 多条自主委派不被误收敛。"""
    monkeypatch.setattr(_cfg_mod, "ENABLE_DIALOG_SELECT_MODEL_DEDUP", True)
    c = _make_cortex()
    assert c._claim_select_model_emit("", "自主主题甲") is True
    assert c._claim_select_model_emit("", "自主主题乙") is True


def test_switch_off_keeps_legacy_behavior(monkeypatch):
    """开关关闭 ⇒ 恒放行（零回归逃逸口）。"""
    monkeypatch.setattr(_cfg_mod, "ENABLE_DIALOG_SELECT_MODEL_DEDUP", False)
    c = _make_cortex()
    assert c._claim_select_model_emit(_CID, "问题甲") is True
    assert c._claim_select_model_emit(_CID, "问题乙") is True
