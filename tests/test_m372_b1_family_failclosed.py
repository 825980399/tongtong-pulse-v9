# -*- coding: utf-8 -*-
"""372 刀1 定向单测：族表默认档 fail-closed 兜底（票 T-371-族表默认档-1）。

背景（T0 实测，HEAD `5dff8abe`）：
  · risk 表 11 族 / trust 表 7 族；未命中分别落 "低" 与 40.0；
  · `data/patches/patch_history.json` 87 条里 81 条（93.1%）属**两表零覆盖族**
    （silent_exception 57 / Traceback 9 / print_instead_of_log 8 /
     bare_return_none_in_except 3 / code_optimization 2 / dead_code_candidate 2）；
  · 自动面只以 `_m346_is_low_risk`（rank==1）为枢轴（三处闸都不读 trust），
    另 `select_for_auto_triage` 要求 risk 低 ⇒ 未登记族落"低"＝自动档全开（fail-open）。

本刀（裁决 C-2 甲）：未登记族兜底 "低" → "中"，自动面关闭、`PatchAutoApprover`
落 need_human（转人工，不是直接拒）；已登记 11 族取值一字未动；门槛常量不动。
"""
from __future__ import annotations

import time

from nucleus.evolution.PatchAutoApprover import (
    DECISION_APPROVE,
    PatchAutoApprover,
)
from nucleus.reasoning.PatchManager import PatchManager

# 280 批立表的原始取值（**逐字抄自 372 刀1 改动前 HEAD**，用于"零放宽"对照）
EXPECT_RISK = {
    "sql_injection": "高",
    "lock_with_emit": "高",
    "non_atomic_write": "高",
    "command_injection": "高",
    "path_traversal": "高",
    "eval_usage": "高",
    "pickle_load": "高",
    "thread_no_daemon": "中",
    "resource_no_close": "中",
    "cross_module_singleton_call": "中",
    "periodic_task_no_reentry": "低",
}
EXPECT_TRUST = {
    "sql_injection": 20.0,
    "lock_with_emit": 25.0,
    "non_atomic_write": 30.0,
    "thread_no_daemon": 50.0,
    "resource_no_close": 55.0,
    "cross_module_singleton_call": 60.0,
    "periodic_task_no_reentry": 75.0,
}
UNREGISTERED = ("silent_exception", "Traceback", "print_instead_of_log",
                "bare_return_none_in_except", "code_optimization",
                "dead_code_candidate")


# ============================================================ ① 未识别族不进自动档
def test_01_unregistered_family_never_reaches_auto_tier():
    """未登记族 ⇒ 中风险 ⇒ 三处低风险闸与自动批准面全部不通过。"""
    PM = PatchManager
    for _fam in UNREGISTERED:
        # 兜底档已不是"低"
        assert PM._m280_risk_by_issue_type(_fam) == "中", _fam
        # 登记状态为 False
        assert PM._m372_family_registered(_fam) is False, _fam
        # 低风险判据（三处闸共用的唯一枢轴）不通过
        assert PM._m346_is_low_risk(PM._m280_risk_by_issue_type(_fam)) is False, _fam
        # 自动批准器把"中"归到 medium ⇒ 第 5 条 auto_approve 分支（要求 low）进不去
        assert PatchAutoApprover._risk_category({"risk_level": "中"}) == "medium", _fam

    # ★反向判别（铁律193）：改前走的就是"低"，这条必须为真，
    #   否则上面那串 False 可能是"判据恒假"而非"确实拦住"。
    assert PM._m346_is_low_risk("低") is True
    assert PatchAutoApprover._is_low_risk({"risk_level": "低"}) is True
    assert PatchAutoApprover._is_low_risk({"risk_level": "中"}) is False

    # 端到端：同一份"已验证 + 高置信 + trust 达标"的补丁，只有风险档不同
    _base = {
        "id": "p372_b1_a",
        "file": "nucleus/x.py",
        "method": "m",
        "status": "runtime_verified",
        "verified": True,
        "confidence": "high",
        "trust_score": 40,
        "created_at": time.time() - 3600,
        "saved_at": time.time() - 3600,
    }
    _ap = PatchAutoApprover.__new__(PatchAutoApprover)  # 只调纯判定，不落盘
    _dec, _reason = _ap.classify(dict(_base, risk_level="中"))
    assert _dec != DECISION_APPROVE, (_dec, _reason)


def test_02_intake_marks_unregistered_family_and_leaves_registered_clean(tmp_path):
    """入队口把「族未登记」落到条目上（布尔与说明分字段），已登记族不留噪声。"""
    _pm = PatchManager(str(tmp_path))

    def _mk(pid, fam):
        # ★modified_code 首个 def 名必须 == method（284刀5 结构校验，否则整条作废，
        #   入队口留痕就测不到了）
        return {
            "id": pid,
            "file": "nucleus/demo.py",
            "method": "some_method",
            "original_code": "def some_method():\n    return 0\n",
            "modified_code": "def some_method():\n    return 1\n",
            "aesthetic_score": {"total": 90.0, "grade": "A", "syntax_valid": True},
            "risk_level": "低",          # 上游显式声明低：未登记族也必须被升到中
            "trust_score": 40,
            "status": "pending",
            "description": "372b1 入队留痕测试",
            "issue_type": fam,
        }

    _u = _mk("p372_u", "silent_exception")
    _r = _mk("p372_r", "thread_no_daemon")   # 已登记的**中危**族（中危+显式低 ⇒ 中）
    _pm.save_pending_patch(_u)
    _pm.save_pending_patch(_r)

    # 未登记族：显式"低"被 fail-closed 升为中，且留痕齐备
    assert _u["risk_level"] == "中", _u.get("risk_level")
    assert _u["risk_family_registered"] is False
    assert "族未登记" in (_u.get("risk_family_note") or "")

    # 已登记族：判据不回归（显式低+中危族 ⇒ 中，这是 344 刀3 既有行为），不写 note
    assert _r["risk_family_registered"] is True
    assert "risk_family_note" not in _r
    assert _r["risk_level"] == "中"      # thread_no_daemon 本就是中危族


# ============================================================ ② 已登记族不回归
def test_03_registered_families_untouched():
    """11 族 risk / 7 族 trust 取值与改前逐字一致；合并判据行为不变。"""
    PM = PatchManager
    assert set(PM._M280_RISK_BY_FAMILY) == set(EXPECT_RISK)
    assert set(PM._M280_TRUST_BY_FAMILY) == set(EXPECT_TRUST)
    for _f, _v in EXPECT_RISK.items():
        assert PM._m280_risk_by_issue_type(_f) == _v, _f
    for _f, _v in EXPECT_TRUST.items():
        assert PM._m280_trust_by_issue_type(_f) == _v, _f
        assert PM._m372_family_registered(_f) is True, _f

    # 344 刀3 只升不降：高危族 + 显式低 ⇒ 高
    assert PM._m344_merge_risk("低", "sql_injection") == ("高", "escalated_by_issue_type")
    # 显式高优先（未登记族也不许被降）
    assert PM._m344_merge_risk("高", "doc_typo") == ("高", "explicit")
    # 中危族 + 显式低 ⇒ 中（344 既有断言口径）
    for _f in ("thread_no_daemon", "resource_no_close",
               "cross_module_singleton_call"):
        assert PM._m344_merge_risk("低", _f)[0] == "中", _f
    # 低危族无显式 ⇒ 仍是低（已登记族唯一"低"档，证明确有人还走低风险）
    assert PM._m344_merge_risk("", "periodic_task_no_reentry") == ("低", "issue_type")


# ============================================================ ③ 门槛未被改动
def test_04_thresholds_not_touched():
    """门槛常量与 trust 兜底值**未被本刀放宽或压低**。"""
    import config

    assert float(config.PATCH_AUTO_APPROVE_TRUST_THRESHOLD) == 40.0
    # trust 兜底保持 40.0（不压低：避免把"转人工"打成"直接拒"）
    assert PatchManager._M372_DEFAULT_FAMILY_TRUST == 40.0
    # 风险兜底只能是"中"：改"高"会把主力族打成高风险直接拒，改"低"则回到 fail-open
    assert PatchManager._M372_DEFAULT_FAMILY_RISK == "中"

    # 源码面：门槛常量在 config.py 仍只定义一次且取值为 40（防同笔偷偷改门槛）
    _src = open("config.py", "rb").read().decode("utf-8", "replace")
    assert _src.count("PATCH_AUTO_APPROVE_TRUST_THRESHOLD = 40") == 1


# ============================================================ ④ 生产面零越界
def test_05_registry_helper_is_pure_and_surface_closed():
    """登记判定纯函数：幂等、不改表、空值不猜；表未借机扩族。"""
    PM = PatchManager
    _snap_r = dict(PM._M280_RISK_BY_FAMILY)
    _snap_t = dict(PM._M280_TRUST_BY_FAMILY)

    for _ in range(3):
        assert PM._m372_family_registered("sql_injection") is True
        assert PM._m372_family_registered("silent_exception") is False
        assert PM._m372_family_registered("") is False
        assert PM._m372_family_registered(None) is False
        assert PM._m372_family_registered("   ") is False
        assert PM._m372_family_registered(123) is False   # 非字符串不猜

    assert PM._M280_RISK_BY_FAMILY == _snap_r, "判定函数不得改表"
    assert PM._M280_TRUST_BY_FAMILY == _snap_t, "判定函数不得改表"
    # 越界面：本刀不许为「未登记族」偷偷补族条目（补族=方案乙，需另裁）
    for _fam in UNREGISTERED:
        assert _fam not in PM._M280_RISK_BY_FAMILY
        assert _fam not in PM._M280_TRUST_BY_FAMILY
    # 家族数不因本刀变化（11 / 7）
    assert len(PM._M280_RISK_BY_FAMILY) == 11
    assert len(PM._M280_TRUST_BY_FAMILY) == 7
