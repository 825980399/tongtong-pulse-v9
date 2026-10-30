# -*- coding: utf-8 -*-
"""399 刀1 验收：m365 台账"零漂移"是真的零漂移，不是把判据改松。

背景（★这是本刀最该被记住的一点）
--------------------------------
398 交付报告说"台账与现仓身份集脱节 69KB，该单开一刀重跑 --emit"。
本刀 T0 核实时发现：**光重跑 `--emit` 会造出一个更糟的台账** ——

  `build_ledger` 左边用 `ci_common.handler_identity`（5 元组，含 tname/kind，
  371A5 把 nth 从身份里去掉了），右边用扫描器的 `_cmp_key`（4 元组，含 nth、
  不含 tname/kind）⇒ 两个元组**永远不相等** ⇒ 差集恒等于全集。
  399 实测：`_new` 552 条，`_added` 552 条（100% 命中，一条没被"旧判据也抓到"排掉）。

真跑一遍的后果：台账从 68 行（人工带元数据分过档）涨到 552 行，
且 6 条人工结论**一条都对不上身份**（`preserve` 按 6 元组搬运）⇒ 全被抹掉。

所以本刀先修键错配，再重跑；顺带发现 `--emit` 还会**丢掉两个顶层计数**
（`manual_review_n` / `control_flow_exempt_n` 原是手写进 json 的，工具从不产出）
⇒ 改成从 rows 现场派生。

判据
----
  * 差集必须是新判据全集的**真子集**（键错配回归时这条立刻红）；
  * 工具源码里不许再出现 `handler_identity`（AST 级，结构性防回归）；
  * 台账与现仓零漂移；
  * 退役行必须写明 reason，且人工结论一条不丢；
  * `--emit` 幂等（连跑 retired 不增长）。
"""
from __future__ import annotations

import ast
import io
import importlib.util
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_TOOL = os.path.join(_ROOT, "tools", "ci", "m365_silent_ledger.py")
_LEDGER = os.path.join(_ROOT, "tools", "ci", "baselines", "m365_silent_except_ledger.json")

_REASONS = {"fixed", "func_gone", "no_longer_silent", "unknown"}


def _tool():
    _spec = importlib.util.spec_from_file_location("m399_ledger_tool", _TOOL)
    _m = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    return _m


def _src() -> str:
    return io.open(_TOOL, encoding="utf-8", errors="replace").read()


@pytest.fixture(scope="module")
def tool():
    return _tool()


# ---------------------------------------------------------------- ① 键错配防回归
def test_01_delta_is_proper_subset_of_new(tool):
    """★差集必须**严格小于**新判据全集。

    键错配那版的表现就是这个等式不成立：`_added` == 全部 `_new`。
    只要旧判据还能抓到任何东西（它当然能），真差集就必然比全集小。
    """
    _m = tool._load_scanner()
    _, old, new, _ = _m.scan("prod")
    assert old, "旧判据一条都没抓到 —— 这条判据失去判别力，先查扫描器"
    _live = tool.build_ledger(preserve=False)
    assert _live["total"] > 0, "差集为空，台账没有意义（或扫描器坏了）"
    assert _live["total"] < len(new), (
        "差集 %d == 新判据全集 %d ⇒ 两侧键函数又不一致了（399刀1 的同型回归）"
        % (_live["total"], len(new)))


def test_02_tool_no_longer_calls_handler_identity(tool):
    """★结构性护栏：工具里**不许**再出现 `handler_identity`。

    它是 5 元组（无 nth、含 tname/kind），与扫描器 `_cmp_key` 的 4 元组不可比。
    混用就是 399 这个 bug 的全部成因。走 AST 判 `ast.Call`，不认字符串
    （注释里提它是允许的 —— 说明文字本来就要写清为什么不许用）。
    """
    _calls = []
    for n in ast.walk(ast.parse(_src())):
        if isinstance(n, ast.Call):
            _f = n.func
            if isinstance(_f, ast.Attribute) and _f.attr == "handler_identity":
                _calls.append(n.lineno)
            elif isinstance(_f, ast.Name) and _f.id == "handler_identity":
                _calls.append(n.lineno)
    assert not _calls, f"工具又在调 handler_identity（行号 {_calls}）—— 差集会被算成全集"


def test_03_cmp_key_shape_is_the_shared_one(tool):
    """两侧共用的是扫描器的 `_cmp_key`（4 元组，不含 tname/kind）。"""
    _m = tool._load_scanner()
    _, old, new, _ = _m.scan("prod")
    for _x in (old[:1] + new[:1]):
        _k = _m._cmp_key(_x)
        assert len(_k) == 4, f"_cmp_key 变了形状（{len(_k)} 元组）—— 本判据要跟着改"
    # 台账自身的 identity_of 是 6 元组，两者**故意不同**：identity_of 用来认"同一行台账"，
    # _cmp_key 用来认"同一个 handler 的两副判据面孔"。混用就出事。
    _row = (tool.build_ledger(preserve=False)["rows"] or [None])[0]
    if _row:
        assert len(tool.identity_of(_row)) == 6


# ---------------------------------------------------------------- ② 零漂移
def test_04_zero_drift_now(tool):
    """重跑后台账与现仓必须零漂移（这就是本刀要的"全绿"）。"""
    import json

    _old = json.load(io.open(_LEDGER, encoding="utf-8"))
    _live = tool.build_ledger(preserve=False)
    _d = tool.diff_ledger(_old["rows"], _live["rows"])
    assert _d["added"] == [], f"新增未入册 {len(_d['added'])} 条：{_d['added'][:5]}"
    assert _d["removed"] == [], f"在册但已不在差集 {len(_d['removed'])} 条：{_d['removed'][:5]}"
    assert _d["same"] == len(_old["rows"])


# ---------------------------------------------------------------- ③ 退役存档
def test_05_retired_rows_have_reason(tool):
    """每条退役行都要写明为什么离开（fixed / func_gone / no_longer_silent）。"""
    _live = tool.build_ledger(preserve=True)
    _ret = _live.get("retired") or []
    assert _ret, "退役存档为空 —— 说明 58 条历史行没被带走（人工结论会被抹掉）"
    _bad = [r for r in _ret if str(r.get("retired_reason") or "") not in _REASONS]
    assert not _bad, f"有 {len(_bad)} 条退役行没有合法 reason：{_bad[:3]}"
    _unk = [r for r in _ret if r.get("retired_reason") == "unknown"]
    assert not _unk, f"{len(_unk)} 条退役行归因失败（文件不可读/不可解析）：{_unk[:3]}"


def test_06_retired_keeps_human_conclusions(tool):
    """★人工结论一条都不能丢：退役行里的 `manual_review` 必须原样还在。"""
    _live = tool.build_ledger(preserve=True)
    _ret = {(r.get("path"), r.get("qualname")): r for r in (_live.get("retired") or [])}
    _target = ("nucleus/llm/call_recorder.py", "_rotate")
    assert _target in _ret, "唯一那条 A 档结论（_rotate）没进退役存档 —— 被抹掉了"
    _mr = _ret[_target].get("manual_review") or {}
    assert _mr.get("verdict") == "A", f"verdict 丢了：{_mr}"
    assert _mr.get("reviewed_by") == "路灯"
    assert _mr.get("reviewed_at") == "2026-10-25"
    assert _ret[_target].get("retired_reason") == "fixed", (
        "它离开差集的原因应该是 fixed（函数体内已有 silent_exc）")


def test_07_retired_n_matches_len(tool):
    """自报计数必须与实测同源（铁律 271：分子分母同源）。"""
    _live = tool.build_ledger(preserve=True)
    assert int(_live.get("retired_n") or 0) == len(_live.get("retired") or [])
    assert int(_live.get("total") or 0) == len(_live.get("rows") or [])


# ---------------------------------------------------------------- ④ emit 幂等 / 计数不再丢
def test_08_emit_is_idempotent(tool):
    """连跑两次 `preserve=True`，退役存档不增长（否则每次 emit 都会翻倍）。"""
    _a = tool.build_ledger(preserve=True)
    _b = tool.build_ledger(preserve=True)
    assert int(_a.get("retired_n") or 0) == int(_b.get("retired_n") or 0)


def test_09_counts_are_derived_not_handwritten(tool):
    """★两个顶层计数必须能从 rows 现算出来。

    它们原来是**手写进 json** 的，`--emit` 一跑就变 None（实测）
    ⇒ test_01/test_02 当场红，而人只会看到"台账没零漂移"，根本想不到是计数丢了。
    """
    import json

    _led = json.load(io.open(_LEDGER, encoding="utf-8"))
    _rows = _led.get("rows") or []
    assert "manual_review_n" in _led, "台账缺 manual_review_n（emit 又把它丢了）"
    assert "control_flow_exempt_n" in _led, "台账缺 control_flow_exempt_n（emit 又把它丢了）"
    assert int(_led["manual_review_n"]) == sum(1 for r in _rows if r.get("manual_review"))
    assert int(_led["control_flow_exempt_n"]) == sum(1 for r in _rows if r.get("exemption"))
    _live = tool.build_ledger(preserve=False)
    # ★别写 `int(x or -1)` —— x 真为 0 时会走 or 分支变成 -1（0 值陷阱，本判据第一版就这么红的）
    assert _live.get("manual_review_n") == 0, (
        "preserve=False 时不该有人工元数据（它来自既有台账，不是从代码里读出来的）")
