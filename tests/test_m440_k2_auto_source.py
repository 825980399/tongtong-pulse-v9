# -*- coding: utf-8 -*-
"""441 刀1（补 440 欠账）：`auto:bridge` / `human:*` 的**治理口路由**判据。

440 的根因：`approve_patch()` 把 `_approve_via(..., "human", signer, ...)` 的 source
**写死成 human**，而 `_boundary_check` 规定 human 口 signer 必须以 `human:` 开头、
非 human 口不得冒充人工 ⇒ `auto:bridge` 走 human 口必被拒。
本文件守的是「路由」这一层（不测字段契约，那是 `_govern_fields_ok` / `_truth_ok` 的事）。
"""
import inspect
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from nucleus.reasoning.PatchManager import PatchManager


def _patch(**kw):
    _b = {
        "id": "p441", "file": "organs/motor/PulseController.py",
        "method": "_semantic_extract_keywords", "risk_level": "低",
        "trust_score": 75.0, "verified": True, "baseline_errors": 1,
        "problem_gone": True, "status": "pending",
    }
    _b.update(kw)
    return _b


# ---------------------------------------------------------------- ① 边界校验本身
@pytest.mark.parametrize("source,signer,expect", [
    ("human", "human:ludeng", True),      # 人工口 + 人工签名
    ("human", "auto:bridge", False),      # 人工口 + 机器签名 ⇒ 拒
    ("auto", "auto:bridge", True),        # 机器口 + 机器签名 ⇒ 过
    ("auto", "human:ludeng", False),      # 机器口冒充人工 ⇒ 拒
    ("bogus", "auto:x", False),           # 未知口 ⇒ 拒
])
def test_boundary_check_matrix(source, signer, expect):
    assert PatchManager._boundary_check(_patch(), source, signer) is expect


# ---------------------------------------------------------------- ② source 参数存在
def test_approve_patch_accepts_source_param():
    """`approve_patch` 必须有 `source` 形参且默认 human（只加不改，旧调用方不变）。"""
    _sig = inspect.signature(PatchManager.approve_patch)
    assert "source" in _sig.parameters, "approve_patch 没有 source 参数"
    assert _sig.parameters["source"].default == "human", "默认值不是 human（旧行为会被改掉）"


# ---------------------------------------------------------------- ③ 前缀路由
@pytest.mark.parametrize("signer,expect_src", [
    ("human:ludeng", "human"),
    ("auto:bridge", "auto"),
    ("machine:m440", "auto"),
])
def test_approve_by_id_routes_by_prefix(signer, expect_src):
    """`approve_patch_by_id` 必须按前缀选口，且把 source 传给 approve_patch。"""
    _src = inspect.getsource(PatchManager.approve_patch_by_id)
    assert 'startswith("human:")' in _src, "没有按 human: 前缀分流"
    assert "source=_src" in _src, "没有把推断出的 source 传下去"
    # 正向：模拟一次调用，拦截 approve_patch 看它收到什么
    _pm = object.__new__(PatchManager)
    _pm._pending_file = "x"
    _pm._load_patch_list = lambda _p: [_patch()]
    _seen = {}

    def _fake_approve(idx, signer=None, basis=None, judged_by=None, source=None):
        _seen["source"] = source
        return {"ok": True}

    _pm.approve_patch = _fake_approve
    _r = _pm.approve_patch_by_id("p441", by=signer)
    assert _r.get("ok") is True
    assert _seen["source"] == expect_src, f"{signer} 路由到 {_seen['source']}（应 {expect_src}）"


def test_approve_by_id_unknown_id_reports():
    _pm = object.__new__(PatchManager)
    _pm._pending_file = "x"
    _pm._load_patch_list = lambda _p: []
    _r = _pm.approve_patch_by_id("nope", by="auto:bridge")
    assert _r.get("ok") is False and "找不到" in _r.get("reason", "")
