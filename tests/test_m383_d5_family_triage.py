# -*- coding: utf-8 -*-
"""383 刀5：族分级三级 SAFE / REVIEW / BLOCKED。

## 设计要点（读代码前先看这段）

1. **档次派生自 `_M280_RISK_BY_FAMILY`**，不另立第二张族表。
   两张表一旦分叉，就会出现「风险高却被判 SAFE」这种自相矛盾。
2. **只收紧不放松**：调用点是
   `_whitelist_ok = 旧判定 and (档次 == "safe")` —— 是 `and`，不是 `or`。
   旧判定拒绝的补丁，三级绝不会把它放行。
3. **REVIEW 不新建人工入口**：它就是「不自动」的默认路径，由既有 O3 确认入口处理
   （票面第 3 条「REVIEW 复用现有 O3 确认入口」的正确实现就是**不写新入口**）。
4. **开关默认关**，关 ⇒ `_m383_family_triage` 一律返 "off"，调用点整段不执行。

## SAFE 只有 1 个族 —— 这是事实，不是遗漏

`_M280_RISK_BY_FAMILY` 里只有 1 个「低」族（`periodic_task_no_reentry`）。
票面要求「三级各至少 2 个族」，SAFE 满足不了。要凑出第 2 个，必须把某个「中」族
（如 `silent_exception`）降到「低」—— 那是 **372刀1 fail-closed 裁决的反向修改**，
属安全面放宽，不在本刀范围。有用例钉住「SAFE 里不许出现中/高风险族」。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.reasoning.PatchManager import PatchManager as P  # noqa: E402


def _triage(issue_type, on=True, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(config, "ENABLE_M383_FAMILY_TRIAGE", on, raising=False)
    return P._m383_family_triage({"issue_type": issue_type})


# ------------------------------------------------------- ① 开关默认关（零回归）
def test_m383_d5_switch_defaults_off():
    """★零回归的根：开关默认必须是 False，否则等于擅自改生产行为。"""
    assert config.ENABLE_M383_FAMILY_TRIAGE is False


def test_m383_d5_off_returns_off_for_every_family(monkeypatch):
    """关 ⇒ 任何族都返 "off"（调用点据此整段跳过）。"""
    monkeypatch.setattr(config, "ENABLE_M383_FAMILY_TRIAGE", False, raising=False)
    for it in ("periodic_task_no_reentry", "sql_injection", "silent_exception", ""):
        assert P._m383_family_triage({"issue_type": it}) == "off"
    assert P._m383_family_triage(None) == "off"


# ------------------------------------------------------------- ② 三级族清单
def test_m383_d5_table_lists_real_families():
    """★票面「每级列具体族名」：BLOCKED / REVIEW 各 ≥2 个真实在册族。"""
    t = P._m383_family_triage_table()
    assert len(t["BLOCKED"]) >= 2, t["BLOCKED"]
    assert len(t["REVIEW"]) >= 2, t["REVIEW"]
    assert len(t["SAFE"]) >= 1, "SAFE 至少要有 1 个族（实际风险表只有 1 个「低」族）"
    assert t["SOURCE"] == "_M280_RISK_BY_FAMILY"


def test_m383_d5_safe_has_no_medium_or_high_family():
    """★防凑数守卫：SAFE 里**不许**出现中/高风险族。

    谁要是为了满足「每级≥2」把 `silent_exception` 塞进 SAFE，这条会立刻红
    —— 那是把 372刀1 的 fail-closed 兜底反向放宽。
    """
    t = P._m383_family_triage_table()
    for fam in t["SAFE"]:
        _risk = P._m280_risk_by_issue_type(fam)
        assert P._m346_risk_rank(_risk) == 1, (
            "SAFE 里出现非低风险族 %s（risk=%s）⇒ 这是安全面放宽，不允许" % (fam, _risk))


def test_m383_d5_unregistered_goes_review_not_safe():
    """未登记族按 372刀1 兜底「中」⇒ REVIEW（历史主力族全在这里，别误以为它们能自动）。"""
    t = P._m383_family_triage_table()
    assert t["UNREGISTERED"] == "REVIEW"
    assert t["DEFAULT_FAMILY_RISK"] == "中"


# --------------------------------------------------------------- ③ 三档判定
@pytest.mark.parametrize("issue_type,want", [
    ("periodic_task_no_reentry", "safe"),
    ("thread_no_daemon", "review"),
    ("resource_no_close", "review"),
    ("cross_module_singleton_call", "review"),
    ("sql_injection", "blocked"),
    ("eval_usage", "blocked"),
    ("command_injection", "blocked"),
])
def test_m383_d5_each_level_judges_correctly(issue_type, want, monkeypatch):
    """★核心：三个档位都至少覆盖 2 个族（SAFE 只有 1 个族，见模块 docstring）。"""
    assert _triage(issue_type, monkeypatch=monkeypatch) == want


@pytest.mark.parametrize("issue_type", ["silent_exception", "dead_code_candidate",
                                        "cross_file_refactor", "", "Traceback"])
def test_m383_d5_unregistered_families_are_review(issue_type, monkeypatch):
    """未登记族 / 空 issue_type 一律 REVIEW（转人工），绝不放行。"""
    assert _triage(issue_type, monkeypatch=monkeypatch) == "review"


# ------------------------------------------------ ④ 只收紧不放松（结构性守卫）
def test_m383_d5_call_site_uses_and_not_or():
    """★最重要的一条：收紧表达式必须是 `旧 and (档次=="safe")`。

    写成 `or` 就成了「旧判定拒绝 + 三级放行 = 放行」，即安全面被打开。
    这条用源码断言钉死，比跑一万个用例都直接。
    """
    import io as _io
    src = _io.open(os.path.join(ROOT, "nucleus/reasoning/PatchManager.py"),
                   encoding="utf-8").read()
    assert '_whitelist_ok_a1 = _whitelist_ok_a1 and (_triage_a1 == "safe")' in src, \
        "收紧表达式必须是 and；若为 or 则三级会放宽旧判定"


def test_m383_d5_review_reuses_existing_entry_no_new_gate():
    """★票面第 3 条：REVIEW 复用现有 O3 确认入口 ⇒ 本刀**不许**新增人工批入口。"""
    import io as _io
    src = _io.open(os.path.join(ROOT, "nucleus/reasoning/PatchManager.py"),
                   encoding="utf-8").read()
    _start = src.index("def _m383_family_triage_enabled")
    _end = src.index("def _m383_family_triage_table")
    _seg = src[_start:_end]
    for _bad in ("approve_o3_patch", "requires_confirmation = ", "auto_approved = "):
        assert _bad not in _seg, "分级函数里出现 %r ⇒ 说明在自建入口/自改状态" % _bad


# ------------------------------------------------- ⑤ 未知族与空输入不抛
def test_m383_d5_never_raises_on_weird_input(monkeypatch):
    """issue_type 是奇怪类型也不许抛（走 str() 兜底）。"""
    monkeypatch.setattr(config, "ENABLE_M383_FAMILY_TRIAGE", True, raising=False)
    for _p in ({"issue_type": None}, {"issue_type": 123}, {}, None):
        assert P._m383_family_triage(_p) in ("safe", "review", "blocked", "off")


def test_m383_d5_triage_table_is_pure_read():
    """清单函数只读：连续两次调用结果一致（不缓存、不副作用）。"""
    assert P._m383_family_triage_table() == P._m383_family_triage_table()
