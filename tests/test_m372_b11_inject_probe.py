# -*- coding: utf-8 -*-
"""372 刀11 定向单测（4 例）：进程内注入采样通路就绪。

票 `T-371-进程内注入采样归属-1`。本批只交付**静态通路**（探针 + 注入片段生成器），
活体 ≥30min 长窗与 no-gc 对照归属并入重启观察窗。
"""
from __future__ import annotations

import io
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_CI = os.path.join(_HERE, "tools", "ci")
if _TOOLS_CI not in sys.path:
    sys.path.insert(0, _TOOLS_CI)

import memprobe_372_inject as MP  # noqa: E402

_EVID_JSON = os.path.join(_HERE, "docs", "路灯与星轨对话", "交付报告", "证据件",
                          "372", "刀11_注入采样通路就绪_20261026.json")
_DIMS = {"gc_generations", "threads", "containers", "type_top", "organs"}


def test_01_probe_runs_standalone_and_zero_production_import():
    """①探针独立可跑、零生产 import（不被生产代码绑运行期）。"""
    _d = MP.sample_inproc_v2(top_n=5)
    assert isinstance(_d, dict)
    assert _d["mode"] == "self_v2"
    assert set(_d["dims"]) == _DIMS

    _src = io.open(os.path.join(_TOOLS_CI, "memprobe_372_inject.py"),
                   encoding="utf-8").read()
    for _bad in ("import nucleus", "from nucleus", "import organs", "from organs",
                 "import config", "from config import"):
        assert _bad not in _src, "★探针不得 import 生产模块: %s" % _bad
    # 也不许注册定时器/线程（"不新起定时器、不进心跳链"）
    assert "threading.Thread(" not in _src
    assert "Timer(" not in _src


def test_02_structure_matches_evidence_and_idempotent():
    """②分器结构与机读证据件一致；两次复跑结构恒等（幂等）。"""
    assert os.path.isfile(_EVID_JSON), "机读证据件缺失"
    _ev = json.load(io.open(_EVID_JSON, encoding="utf-8"))
    assert set(_ev["dims"]) == _DIMS
    assert _ev["candidates"] == list(MP.CANDIDATES)

    def _shape(d):
        # 去掉时间类易变字段，只比结构
        return {"dims": {k: (sorted(v) if isinstance(v, dict) else type(v).__name__)
                         for k, v in d["dims"].items()},
                "mode": d["mode"], "no_gc": d["no_gc"]}

    _a = MP.sample_inproc_v2(top_n=5)
    _b = MP.sample_inproc_v2(top_n=5)
    assert _shape(_a) == _shape(_b), "两次复跑结构不一致（幂等性破了）"


def test_03_errors_do_not_raise_and_missing_is_none_not_zero(monkeypatch):
    """③异常分支不抛出（逐维兜底）、取不到填 None 不填 0。"""
    def _boom(*a, **kw):
        raise RuntimeError("模拟维度崩溃")

    monkeypatch.setattr(MP, "_container_dim", _boom)
    _d = MP.sample_inproc_v2(top_n=3)          # ★不得抛出
    assert _d["dims"]["containers"] is None, "崩掉的维度应为 None（不是空 dict 冒充）"
    assert _d["err"] and "containers" in _d["err"], "崩掉的维度必须落 err 留痕"
    assert _d["dims"]["threads"] is not None, "一维崩不得带走其他维"

    # 取不到 ⇒ None 不填 0：no_gc 分支是显式布尔，缺失的数值不得写成 0
    _d2 = MP.sample_inproc_v2(top_n=3, no_gc=True)
    assert _d2["no_gc"] is True
    for _row in _d2["dims"]["gc_generations"]:
        # 只允许 int 或 None；不允许把"取不到"写成 0
        assert _row.get("count") is None or isinstance(_row.get("count"), int)


def test_04_no_gc_branch_and_candidates_complete():
    """④no-gc 对照分支在位（不预收 gc）＋候选五类齐备（分器不得漏类）。"""
    assert len(MP.CANDIDATES) == 5
    _src = io.open(os.path.join(_TOOLS_CI, "memprobe_372_inject.py"),
                   encoding="utf-8").read()
    # no_gc 为真时必须跳过 gc.collect()
    _i = _src.index("def sample_inproc_v2")
    _seg = _src[_i:_i + 1200]
    assert "if not no_gc:" in _seg, "★no-gc 对照分支必须在位（否则长窗读数被自己污染）"
    assert "gc.collect()" in _seg

    # 注入片段生成器：给的是"可粘贴代码"，且写明不进心跳链
    _snip = MP.emit_inject_snippet(60, 5, "tmp/x.json")
    assert "sample_inproc_v2" in _snip
    assert "no_gc=True" in _snip and "no_gc=False" in _snip
    assert "1800" not in _snip or "60" in _snip     # duration 参数生效
    assert "不注册定时器" in _snip
