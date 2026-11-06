# -*- coding: utf-8 -*-
"""第463批：修「生成侧不产出补丁」的根因 · 单测

刀1 冷却判据分级（不再因「不在本地白名单」就判不可修复）
刀2 强制复检解冻（防 cooldown.json 24 条长期占满候选池）
刀3 候选池优先级 + 修复漏斗留痕

所有写盘走 `root=tmp_path`；默认生产路径在 pytest 下由护栏挡住。
"""
from __future__ import annotations

import os

import config
from nucleus.evolution import cooldown_policy as CP
from nucleus.evolution import repair_funnel as RF

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOCAL = {"silent_exception", "bare_except", "mutable_default_arg"}
HIGH = {"unsafe_eval", "sql_injection"}


def _code(t="periodic_task_no_reentry"):
    return {"file": "organs/core/PulseSystemManager.py",
            "method": "_schedule_health_check", "type": t,
            "issue_type": t}


def _log(t="ERROR"):
    return {"organ": "自检视", "type": t, "description": "某条 ERROR 日志"}


# ==================== 刀1：冷却判据分级 ====================

def test_schema_and_actions():
    assert CP.SCHEMA == "cooldown_policy/463.v1"
    assert (CP.A_SKIP, CP.A_COOL, CP.A_COOL_SHORT) == (
        "skip_cool", "cool", "cool_short")
    assert "periodic_task_no_reentry" in CP.REPRO_COVERED
    assert "non_atomic_write" in CP.REPRO_COVERED


def test_local_rule_type_never_cooled():
    r = CP.classify(_code("silent_exception"), local_fixable_types=LOCAL,
                    high_risk_types=HIGH)
    assert r["action"] == CP.A_SKIP and r["why"] == "本地规则可修"


def test_repro_covered_code_issue_never_cooled():
    """★463 核心：446 检测器能复现的代码类，不该被判「不可修复」关 6 小时。"""
    for t in ("periodic_task_no_reentry", "non_atomic_write",
              "silent_exception"):
        if t in LOCAL:
            continue
        r = CP.classify(_code(t), local_fixable_types=LOCAL,
                        high_risk_types=HIGH)
        assert r["action"] == CP.A_SKIP, (t, r)
        assert "可复现" in r["why"]


def test_high_risk_still_cooled():
    r = CP.classify(_code("unsafe_eval"), local_fixable_types=LOCAL,
                    high_risk_types=HIGH)
    assert r["action"] == CP.A_COOL and r["why"] == "高危·安全拦截"


def test_log_issue_without_location_still_cooled():
    r = CP.classify(_log("growth_need"), local_fixable_types=LOCAL,
                    high_risk_types=HIGH)
    assert r["action"] == CP.A_COOL and r["why"] == "日志类·无代码定位"
    assert r["is_code_issue"] is False


def test_other_code_issue_gets_short_cooldown():
    r = CP.classify(_code("some_new_type"), local_fixable_types=LOCAL,
                    high_risk_types=HIGH)
    assert r["action"] == CP.A_COOL_SHORT
    assert r["is_code_issue"] is True


def test_real_cooldown_keys_replayed():
    """用 cooldown.json 的真实 key 形态回放：能修的代码类必须放行。"""
    samples = [
        ("organs/body/pulseheart.py|_schedule_next_beat|periodic_task_no_reentry",
         CP.A_SKIP),
        ("organs/core/pulsesystemmanager.py|_schedule_health_check|periodic_task",
         CP.A_COOL_SHORT),
        ("organ:自检视|type:ERROR|2026-11-06 ERROR 日志", CP.A_COOL),
        ("organ:潜意识|type:growth_need|假设：如果把反思应用到知识", CP.A_COOL),
    ]
    for key, expect in samples:
        parts = key.split("|")
        if parts[0].startswith("organ:"):
            issue = {"organ": parts[0], "type": parts[1].split("type:")[-1]}
        else:
            issue = {"file": parts[0], "method": parts[1], "type": parts[2]}
        r = CP.classify(issue, local_fixable_types=LOCAL,
                        high_risk_types=HIGH)
        assert r["action"] == expect, (key, r)


# ==================== 刀2：强制复检 ====================

def test_force_recheck_threshold():
    assert CP.should_force_recheck(1, need=3) is False
    assert CP.should_force_recheck(2, need=3) is False
    assert CP.should_force_recheck(3, need=3) is True
    assert CP.should_force_recheck(99, need=3) is True


def test_force_recheck_zero_need_is_clamped_to_one():
    assert CP.should_force_recheck(1, need=0) is True   # max(1, 0) ⇒ 1
    assert CP.should_force_recheck(0, need=1) is False


def test_force_recheck_bad_input_returns_false():
    assert CP.should_force_recheck(None, need=3) is False
    assert CP.should_force_recheck("bad", need="x") is False


# ==================== 刀3：优先级与漏斗 ====================

def test_priority_orders_fixable_code_first():
    items = [_log("growth_need"), _code("some_new_type"),
             _code("non_atomic_write"), _code("bare_except")]
    ranked = sorted(items, key=lambda x: CP.priority(x, local_fixable_types=LOCAL))
    assert ranked[0]["type"] == "bare_except", ranked
    assert ranked[1]["type"] == "non_atomic_write", ranked
    assert ranked[-1]["type"] == "growth_need", ranked


def test_priority_non_dict_is_last():
    assert CP.priority("not-a-dict", local_fixable_types=LOCAL) == 99


def test_funnel_note_writes_all_fields(tmp_path):
    ok = RF.note({"candidate": 15, "cooldown_skipped": 13,
                  "forced_recheck": 1, "admitted": 3, "planned": 3,
                  "processed": 3, "submitted": 0,
                  "cool_skipped_by_policy": 2, "cooldown_registered": 1},
                 root=str(tmp_path), now=1000.0)
    assert ok is True
    rows = RF.rows(root=str(tmp_path))
    assert len(rows) == 1
    r = rows[0]
    assert r["candidate"] == 15 and r["cooldown_skipped"] == 13
    assert r["submitted"] == 0 and r["forced_recheck"] == 1
    assert r["schema"] == RF.SCHEMA


def test_funnel_rows_limit_and_bad_line(tmp_path):
    for i in range(5):
        RF.note({"candidate": i}, root=str(tmp_path), now=float(i))
    p = RF.funnel_path(str(tmp_path))
    with open(p, "a", encoding="utf-8") as f:
        f.write("BROKEN LINE\n")
    assert len(RF.rows(root=str(tmp_path), limit=3)) == 3


def test_funnel_gate_off(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M463_REPAIR_FUNNEL", False)
    assert RF.note({"candidate": 1}, root=str(tmp_path)) is False


def test_funnel_pytest_default_path_is_not_written():
    assert RF.note({"candidate": 1}, root=None) is False


# ==================== 接线（源码级，防「接了又断」） ====================

def test_wiring_all_four_points():
    with open(os.path.join(_ROOT, "nucleus", "reasoning",
                           "SafeEvolutionExecutor.py"), encoding="utf-8") as f:
        src = f.read()
    assert "_m463_cp2.classify(" in src                 # 刀1 分级
    assert "ENABLE_M463_NO_REFRESH_COOLDOWN" in src     # 刀1 禁止刷新
    assert "_m463_cp0.should_force_recheck(" in src     # 刀2 单一口径
    assert "ENABLE_M463_REPRO_PRIORITY" in src          # 刀3 优先级
    assert "_m463_rf.note({" in src                     # 刀3 漏斗
    assert "_cooldown_classify(_issue)" in src, "总闸关时必须保留旧路径"


def test_policy_gate_off_keeps_old_classifier():
    with open(os.path.join(_ROOT, "nucleus", "reasoning",
                           "SafeEvolutionExecutor.py"), encoding="utf-8") as f:
        src = f.read()
    i = src.find("ENABLE_M463_COOLDOWN_POLICY")
    assert i > 0
    assert src.find("_cooldown_classify(_issue)") > i
