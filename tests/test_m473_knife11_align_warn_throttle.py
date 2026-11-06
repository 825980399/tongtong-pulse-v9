# -*- coding: utf-8 -*-
"""第473批 刀11 门控单测：`[470刀1]` 两类诊断日志的连击限流。

病灶
----
近两天（`logs/pulse.log` + `pulse.log.1`，2026-11-06 ~ 11-07）「安全演化执行」
WARNING 分布：

    [470刀1] modified 指向别的方法名（不对齐）        69 条
    [470刀1] 拒绝对齐(orig_mismatch / orig_fragment)  65 条   ← 合计 134 条
    [LLM修复]                                          65 条
    [469刀2]                                           59 条（刀9 已处理）
    [问题过滤]                                         28 条
    [自主迭代] / [多步规划] / [465刀3]                  10 条

这两类描述的是**同一个可预期现象**：LLM 产出的补丁不合格（改名 / original
与 body 不等），470 的判据正确拦住了，系统没有任何故障。逐条 WARNING
只会把真故障淹掉。

判据（与 451 刀4 同型）
----------------------
* 同一 `(how, 文件名)` 连续 3 次才 WARNING，前两次 DEBUG（带连击数）；
* 过阈值后每 600s 再报一次 —— 不能「只报一次」，否则真持续的产出缺陷会消失；
* 分组维度必须是**文件**而不是方法名：实测 69 条集中在少数几个文件，
  按方法名分组会让计数分散、全部停在 DEBUG，等于关掉可见性。
"""

from __future__ import annotations

import ast
import os
import uuid

import pytest

import config
import nucleus.alert_dedup as AD
import nucleus.reasoning.SafeEvolutionExecutor as SEE

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")

_HOW = "modified_other_method"
_HOW2 = "orig_mismatch"


def _uniq_file() -> str:
    """每个用例独立文件名 ⇒ 独立计数槽（不污染、不受别的用例影响）。"""
    return f"D:/fake/__m473k11_{uuid.uuid4().hex}.py"


def _patch(fpath: str) -> dict:
    return {"id": "patch_llm_fake", "file": fpath, "method": "some_method"}


@pytest.fixture(autouse=True)
def _dedup_on(monkeypatch):
    """降噪总开关必须开，否则 `should_warn_streak` 恒返回 True。"""
    monkeypatch.setattr(config, "ENABLE_M450_ALERT_DEDUP", True, raising=False)


# ==================== 连击曲线 ====================

def test_streak_curve_first_three():
    """1、2 次 DEBUG，第 3 次才 WARNING —— 单次抖动不该刷 WARNING。"""
    _f = _uniq_file()
    _out = [SEE._m473_align_should_warn(_HOW, _patch(_f)) for _ in range(4)]
    assert [x[0] for x in _out] == [False, False, True, False], _out
    assert [x[1] for x in _out] == [1, 2, 3, 4], _out


def test_warn_again_after_renotify_window():
    """过阈值后**不是**只报一次：未到 600s 不报，到点再报。"""
    _f = _uniq_file()
    _key = f"m470_align:{_HOW}:{os.path.basename(_f)}"
    # 直接驱动 alert_dedup 的时间参数（本刀只负责把 renotify_sec 传下去，
    # 窗口语义由 450/451 的既有实现提供 ⇒ 这里用它的 now 参数复现整条曲线）
    assert AD.should_warn_streak(_key, threshold=3, renotify_sec=600.0, now=1000.0)[0] is False
    assert AD.should_warn_streak(_key, threshold=3, renotify_sec=600.0, now=1001.0)[0] is False
    assert AD.should_warn_streak(_key, threshold=3, renotify_sec=600.0, now=1002.0)[0] is True
    assert AD.should_warn_streak(_key, threshold=3, renotify_sec=600.0, now=1003.0)[0] is False
    assert AD.should_warn_streak(_key, threshold=3, renotify_sec=600.0, now=1603.0)[0] is True


# ==================== 分组维度 ====================

def test_different_files_are_independent():
    """换文件 ⇒ 各自从 1 开始（否则第一个文件把全局计数吃满，其余永远静默）。"""
    _a, _b = _uniq_file(), _uniq_file()
    assert SEE._m473_align_should_warn(_HOW, _patch(_a))[0] is False
    assert SEE._m473_align_should_warn(_HOW, _patch(_a))[0] is False
    _rb = SEE._m473_align_should_warn(_HOW, _patch(_b))
    assert _rb[0] is False and _rb[1] == 1, _rb


def test_different_how_are_independent():
    _f = _uniq_file()
    assert SEE._m473_align_should_warn(_HOW, _patch(_f))[0] is False
    assert SEE._m473_align_should_warn(_HOW, _patch(_f))[0] is False
    _r2 = SEE._m473_align_should_warn(_HOW2, _patch(_f))
    assert _r2[0] is False and _r2[1] == 1, _r2


def test_basename_not_full_path():
    """同名文件在不同目录必须**共用**计数（判据按文件名，不是全路径）。"""
    _d1, _d2 = _uniq_file(), _uniq_file()
    _name = os.path.basename(_d1)
    _other = os.path.join("D:/fake/another_dir", _name)
    assert os.path.basename(_other) == _name
    assert SEE._m473_align_should_warn(_HOW, _patch(_d1))[0] is False
    assert SEE._m473_align_should_warn(_HOW, _patch(_other))[0] is False
    assert SEE._m473_align_should_warn(_HOW, _patch(_d1))[0] is True


# ==================== 健壮性 ====================

def test_missing_file_field_does_not_crash():
    """patch 缺 file / 为 None ⇒ 不崩（key 退化为空文件名）。"""
    _r1 = SEE._m473_align_should_warn(_HOW, {})
    assert isinstance(_r1, tuple) and len(_r1) == 2
    _r2 = SEE._m473_align_should_warn(_HOW, None)
    assert isinstance(_r2, tuple) and len(_r2) == 2


def test_falls_back_to_warn_when_dedup_broken(monkeypatch):
    """判据自身异常 ⇒ **回 WARNING**，不许静默丢日志。"""
    def _boom(*_a, **_k):
        raise RuntimeError("dedup down")

    monkeypatch.setattr(AD, "should_warn_streak", _boom)
    assert SEE._m473_align_should_warn(_HOW, _patch(_uniq_file())) == (True, 1)


def test_threshold_configurable(monkeypatch):
    """阈值可配：设 1 ⇒ 第一次就 WARNING（现场应急要能立刻全见）。"""
    monkeypatch.setattr(config, "M473_ALIGN_STREAK_THRESHOLD", 1, raising=False)
    _r = SEE._m473_align_should_warn(_HOW, _patch(_uniq_file()))
    assert _r[0] is True, _r


def test_renotify_sec_passed_through(monkeypatch):
    """必须把 `renotify_sec` 传给 alert_dedup（否则「只报一次」）。"""
    _seen = {}

    def _spy(key, *, threshold=3, renotify_sec=600.0, now=None):
        _seen["key"] = key
        _seen["threshold"] = threshold
        _seen["renotify"] = renotify_sec
        return True, 1

    monkeypatch.setattr(AD, "should_warn_streak", _spy)
    SEE._m473_align_should_warn(_HOW, _patch("D:/fake/window_probe.py"))
    assert _seen["key"] == f"m470_align:{_HOW}:window_probe.py", _seen
    assert _seen["threshold"] == 3
    assert _seen["renotify"] == 600.0


# ==================== 接线（AST） ====================

def _module_ast():
    with open(_MOD, "r", encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _align_fn(_tree):
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.FunctionDef) and _n.name == "_m469_align_method_patch":
            return _n
    return None


def test_helper_defined_before_align():
    _tree = _module_ast()
    _defs = {_n.name: _n.lineno for _n in _tree.body if isinstance(_n, ast.FunctionDef)}
    assert "_m473_align_should_warn" in _defs, "缺判据函数"
    assert "_m469_align_method_patch" in _defs
    assert _defs["_m473_align_should_warn"] < _defs["_m469_align_method_patch"]


def test_both_log_points_are_dynamic_level():
    """两处日志都必须是 `warning if flag else debug`，且 flag 来自判据调用。"""
    _fn = _align_fn(_module_ast())
    assert _fn is not None
    _ifexps = []
    for _n in ast.walk(_fn):
        if isinstance(_n, ast.IfExp):
            _b = _n.body.attr if isinstance(_n.body, ast.Attribute) else None
            _o = _n.orelse.attr if isinstance(_n.orelse, ast.Attribute) else None
            if (_b, _o) == ("warning", "debug"):
                _ifexps.append(_n)
    assert len(_ifexps) == 2, f"应有 2 处动态级别日志，实际 {len(_ifexps)}"

    _calls = [n for n in ast.walk(_fn) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name)
              and n.func.id == "_m473_align_should_warn"]
    assert len(_calls) == 2, f"应有 2 处判据调用，实际 {len(_calls)}"


def test_no_direct_warning_on_align_logs():
    """不许留「无条件 WARNING」的 470刀1 日志（否则限流被绕过）。"""
    with open(_MOD, "r", encoding="utf-8") as fh:
        _src = fh.read()
    for _tag in ('"[470刀1] modified 指向别的方法名（不对齐）: id=%s file=%s "',
                 '"[470刀1] 拒绝对齐(%s): id=%s file=%s method=%s orig=%d body=%d"'):
        _i = _src.find(_tag)
        assert _i > 0, _tag
        # 日志字符串前面 200 字符内必须出现动态级别选择
        _head = _src[max(0, _i - 220):_i]
        assert "_module_logger.warning if" in _head, f"该日志仍是无条件 WARNING：{_tag}"
