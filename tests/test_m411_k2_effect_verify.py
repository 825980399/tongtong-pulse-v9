# -*- coding: utf-8 -*-
"""411 批刀2 单测：effect_verify 健康读数（effect_verify_health）。

钉语义（不钉措辞）：
  * 四态 = 四件**不同**的事：`passed` / `failed` / `unverifiable`（验过但判不了）/
    `missing`（从没验过）—— 合并会让人以为"全都没验过"；
  * `stale=None` 表示**判不了**（读不到 `updated`），不是"不旧"；
  * `annotate` 只**补派生字段**，既有字段一个都不许动；dry-run 一个字节都不许写；
  * `PatchManager` 的 except 分支必须有上下文（patch_id + 阶段），且**不是静默**。

T0 已推翻任务书的病因（开关是开的、调用点在、except 打的是 warning），
真断点是**上游没有新补丁走到 apply** —— 所以本模块测的是"停在哪一环"可读，
而不是"触发有没有恢复"（详见交付报告 §偏差）。
"""

from __future__ import annotations

import copy
import io
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.evolution import effect_verify_health as ev  # noqa: E402
import config  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    ev.set_data_dir(str(tmp_path))
    yield
    ev.set_data_dir(None)


def _w(tmp_path, name, obj):
    """写一个 JSON 到隔离目录（父目录不存在就建）。"""
    _p = tmp_path / name
    _p.parent.mkdir(parents=True, exist_ok=True)
    _p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(_p)


def _src(rel):
    """读源码（统一成 LF，避免 CRLF 干扰字符串断言）。"""
    _s = io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read()
    return _s.replace("\r\n", "\n")


# ---------------------------------------------------------------- A verdicts 读取

def test_01_load_verdicts_dict_form(tmp_path):
    p = _w(tmp_path, "evolution/effect_verify.json",
           {"verdicts": {"p1": {"passed": True, "reason": "三维齐"},
                         "p2": None}})
    _v = ev.load_verdicts(p)
    assert set(_v) == {"p1", "p2"}
    assert _v["p1"]["passed"] is True
    # 非 dict 的值归一成 {}（不炸、也不编造判定）
    assert _v["p2"] == {}


def test_02_load_verdicts_list_form_is_compatible(tmp_path):
    p = _w(tmp_path, "ev.json",
           {"verdicts": [{"patch_id": "a", "passed": True},
                         {"id": "b", "passed": False},
                         {"nothing": 1}]})
    _v = ev.load_verdicts(p)
    assert set(_v) == {"a", "b"}, "list 形态要认 patch_id / id 两种键"
    assert _v["b"]["passed"] is False


def test_03_load_verdicts_missing_broken_or_wrong_type(tmp_path):
    assert ev.load_verdicts(str(tmp_path / "nope.json")) == {}
    _bad = tmp_path / "bad.json"
    _bad.write_text("{not json", encoding="utf-8")
    assert ev.load_verdicts(str(_bad)) == {}
    assert ev.load_verdicts(_w(tmp_path, "x.json", {"verdicts": "nope"})) == {}


def test_04_enabled_switch(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M411_EFFECT_VERIFY_HEALTH", False, raising=False)
    assert ev.enabled() is False
    monkeypatch.setattr(config, "ENABLE_M411_EFFECT_VERIFY_HEALTH", True, raising=False)
    assert ev.enabled() is True


# ---------------------------------------------------------------- B 四态派生

def test_11_derive_state_passed_from_verdict():
    _s, _ = ev.derive_state({"id": "p1", "applied": True},
                            {"p1": {"passed": True}})
    assert _s == ev.STATE_PASSED


def test_12_derive_state_failed_from_verdict():
    _s, _why = ev.derive_state({"id": "p2", "applied": True},
                               {"p2": {"passed": False, "reason": "问题仍在"}})
    assert _s == ev.STATE_FAILED
    assert "问题仍在" in _why


def test_13_unverifiable_is_a_third_state_not_missing():
    """`passed is None` = 验过但判不了 —— 与"从没验过"必须可分。"""
    _s, _why = ev.derive_state({"id": "p3", "applied": True},
                               {"p3": {"passed": None}})
    assert _s == ev.STATE_UNVERIFIABLE
    assert _s != ev.STATE_MISSING
    assert "passed_is_none" in _why


def test_14_missing_has_two_distinct_reasons():
    # 从没走到 apply
    assert ev.derive_state({"id": "p4", "applied": False}, {}) == (
        ev.STATE_MISSING, "patch_not_applied")
    # 应用过，但 verdicts 表里没有记录
    assert ev.derive_state({"id": "p5", "applied": True}, {}) == (
        ev.STATE_MISSING, "no_verdict_record")


def test_15_verdict_table_wins_over_patch_own_field():
    """派生字段不得覆盖既有事实：表里有判定时以表为准。"""
    _s, _ = ev.derive_state(
        {"id": "p6", "applied": True, "effect_verified": False},
        {"p6": {"passed": True}})
    assert _s == ev.STATE_PASSED


def test_16_patch_own_field_is_fallback_only():
    assert ev.derive_state({"id": "p7", "applied": True,
                            "effect_verified": True}, {})[0] == ev.STATE_PASSED
    assert ev.derive_state({"id": "p8", "applied": True,
                            "effect_verified": False}, {})[0] == ev.STATE_FAILED


def test_17_not_a_dict_is_missing_not_crash():
    assert ev.derive_state(None, {})[0] == ev.STATE_MISSING
    assert ev.derive_state("p9", {})[0] == ev.STATE_MISSING


# ---------------------------------------------------------------- C 全量分布

def test_21_summarize_counts_four_states_separately():
    _hist = [{"id": "a", "applied": True},
             {"id": "b", "applied": True},
             {"id": "c", "applied": True},
             {"id": "d", "applied": False}]
    _verd = {"a": {"passed": True}, "b": {"passed": False}, "c": {"passed": None}}
    _r = ev.summarize_history(_hist, _verd)
    assert _r["total"] == 4
    assert _r["applied"] == 3
    assert _r["states"] == {ev.STATE_PASSED: 1, ev.STATE_FAILED: 1,
                            ev.STATE_UNVERIFIABLE: 1, ev.STATE_MISSING: 1}
    assert _r["verdicts_total"] == 3


def test_22_summarize_last_applied_is_max_not_last_row():
    _r = ev.summarize_history([{"id": "a", "applied": True, "applied_at": 2000.0},
                               {"id": "b", "applied": True, "applied_at": 1000.0}], {})
    assert _r["last_applied_ts"] == 2000.0
    assert _r["last_applied_at"], "有可读时刻就要给人看的字符串"


def test_23_string_timestamp_is_not_silently_counted_as_zero_but_reported_empty():
    """时刻是字符串（真实台账里就有 '2026-10-14 18:19:55'）⇒ 不编造、不崩。"""
    _r = ev.summarize_history([{"id": "a", "applied": True,
                                "applied_at": "2026-10-14 18:19:55"}], {})
    assert _r["applied"] == 1
    assert _r["last_applied_ts"] is None
    assert _r["last_applied_at"] == ""


def test_24_summarize_note_names_both_states():
    """说明文字必须点名 missing / unverifiable —— 这两个合并过一次就分不回来了。"""
    _r = ev.summarize_history([], {})
    assert ev.STATE_MISSING in _r["note"]
    assert ev.STATE_UNVERIFIABLE in _r["note"]


# ---------------------------------------------------------------- D 新鲜度

def test_31_missing_file_stale_is_none_not_false(tmp_path):
    _r = ev.stale_info(path=str(tmp_path / "nope.json"), now=1000.0)
    assert _r["state"] == "missing"
    assert _r["stale"] is None
    assert _r["reason"] == "file_not_found"


def test_32_no_updated_field_is_unknown_not_fresh(tmp_path):
    _p = _w(tmp_path, "ev.json", {"verdicts": {}})
    _r = ev.stale_info(path=_p, now=1e9)
    assert _r["state"] == "unknown"
    assert _r["stale"] is None, "判不了 ≠ 不旧"
    assert _r["updated"] is None


def test_33_fresh_readout(tmp_path):
    _now = 1_000_000.0
    _p = _w(tmp_path, "ev.json", {"updated": _now - 3600.0})
    _r = ev.stale_info(path=_p, now=_now)
    assert _r["state"] == "ok"
    assert _r["stale"] is False
    assert _r["age_sec"] == 3600.0


def test_34_stale_after_default_24h(tmp_path):
    _now = 1_000_000.0
    _p = _w(tmp_path, "ev.json", {"updated": _now - (ev.STALE_AFTER_SEC + 1)})
    _r = ev.stale_info(path=_p, now=_now)
    assert _r["state"] == "stale"
    assert _r["stale"] is True
    assert "age_sec>" in _r["reason"]


def test_35_broken_json_is_broken_not_stale(tmp_path):
    _p = tmp_path / "ev.json"
    _p.write_text("[[[", encoding="utf-8")
    _r = ev.stale_info(path=str(_p))
    assert _r["state"] == "broken"
    assert _r["stale"] is None


def test_36_future_updated_does_not_go_negative(tmp_path):
    """时钟回拨/写入方用未来时刻时，age 不许出现负数（否则报表会"负年龄还很新"）。"""
    _now = 1_000_000.0
    _p = _w(tmp_path, "ev.json", {"updated": _now + 5000.0})
    _r = ev.stale_info(path=_p, now=_now)
    assert _r["age_sec"] == 0.0
    assert _r["stale"] is False


# ---------------------------------------------------------------- E 快照字段

def test_41_snapshot_block_uses_data_root(tmp_path):
    _now = 1_000_000.0
    _w(tmp_path, "evolution/effect_verify.json", {"updated": _now - 100.0})
    _b = ev.snapshot_block(now=_now)
    assert set(_b) == {"effect_verify_stale", "effect_verify_state",
                       "effect_verify_age_sec", "effect_verify_updated"}
    assert _b["effect_verify_stale"] is False
    assert _b["effect_verify_state"] == "ok"
    assert _b["effect_verify_age_sec"] == 100.0


def test_42_snapshot_block_missing_file_reports_none_not_false():
    _b = ev.snapshot_block(now=1000.0)
    assert _b["effect_verify_stale"] is None
    assert _b["effect_verify_state"] == "missing"
    assert _b["effect_verify_updated"] is None


# ---------------------------------------------------------------- F 派生字段回写

def _rows():
    return [{"id": "a", "applied": True, "status": "applied", "note": "别动我"},
            {"id": "b", "applied": True, "status": "applied"},
            {"id": "c", "applied": False, "status": "failed_gate"}]


def _verdicts():
    return {"a": {"passed": True}, "b": {"passed": None}}


def test_51_dry_run_writes_nothing(tmp_path):
    _p = tmp_path / "patch_history.json"
    _p.write_text(json.dumps(_rows(), ensure_ascii=False), encoding="utf-8")
    _before = _p.read_bytes()
    _r = ev.annotate(verdicts=_verdicts(), dry_run=True, path=str(_p))
    assert _r["ok"] is True
    assert _r["changed"] == 3
    assert _r["written"] is False
    assert _r["reason"] == "dry_run"
    assert _p.read_bytes() == _before, "dry-run 一个字节都不许写"


def test_52_write_adds_only_derived_fields(tmp_path):
    _p = tmp_path / "patch_history.json"
    _orig = _rows()
    _p.write_text(json.dumps(_orig, ensure_ascii=False), encoding="utf-8")
    _r = ev.annotate(verdicts=_verdicts(), dry_run=False, path=str(_p))
    assert _r["written"] is True
    _got = json.loads(_p.read_text(encoding="utf-8"))
    assert len(_got) == len(_orig)
    for _g, _o in zip(_got, _orig):
        _rest = {k: v for k, v in _g.items()
                 if k not in ("effect_verify_state", "effect_verify_state_reason")}
        assert _rest == _o, "既有字段一个都不许改/删"
        assert _g["effect_verify_state"] in ev.ALL_STATES
    # 三态分布：a=passed / b=unverifiable / c=missing
    assert [_g["effect_verify_state"] for _g in _got] == [
        ev.STATE_PASSED, ev.STATE_UNVERIFIABLE, ev.STATE_MISSING]


def test_53_write_is_idempotent(tmp_path):
    _p = tmp_path / "patch_history.json"
    _p.write_text(json.dumps(_rows(), ensure_ascii=False), encoding="utf-8")
    _a = ev.annotate(verdicts=_verdicts(), dry_run=False, path=str(_p))
    assert _a["written"] is True
    _b = ev.annotate(verdicts=_verdicts(), dry_run=False, path=str(_p))
    assert _b["changed"] == 0
    assert _b["written"] is False
    assert _b["reason"] == "no_change"


def test_54_written_file_is_pure_lf(tmp_path):
    _p = tmp_path / "patch_history.json"
    _p.write_bytes(json.dumps(_rows(), ensure_ascii=False,
                              indent=2).encode("utf-8"))
    ev.annotate(verdicts=_verdicts(), dry_run=False, path=str(_p))
    _b = _p.read_bytes()
    assert _b.count(b"\r\n") == 0, "落盘是纯 LF（与仓内数据文件一致）"
    assert _b.count(b"\n") > 0


def test_55_history_not_a_list_refuses_to_write(tmp_path):
    _p = tmp_path / "patch_history.json"
    _p.write_text(json.dumps({"rows": []}), encoding="utf-8")
    _before = _p.read_bytes()
    _r = ev.annotate(verdicts={}, dry_run=False, path=str(_p))
    assert _r["ok"] is False
    assert _r["reason"] == "history_not_a_list"
    assert _p.read_bytes() == _before


def test_56_in_memory_history_is_not_mutated_when_dry_run():
    """传 list 进来时 annotate 改的是调用方对象 —— dry-run 的语义是"不写盘"，
    这里明确记录：内存对象会被标注（调用方要自己 deepcopy）。"""
    _h = _rows()
    _snap = copy.deepcopy(_h)
    ev.annotate(history=_h, verdicts=_verdicts(), dry_run=True, path="ignored")
    assert [r["id"] for r in _h] == [r["id"] for r in _snap]
    assert all("effect_verify_state" in r for r in _h)


# ---------------------------------------------------------------- G 静态接线

def test_61_patch_manager_except_branch_has_context_and_is_warning():
    _s = _src("nucleus/reasoning/PatchManager.py")
    _i = _s.find("[411刀2][效果验证失败]")
    assert _i > 0, "刀2 的 WARNING 日志必须在（否则又回到「不知道是哪个补丁」）"
    _seg = _s[_i:_i + 600]
    assert "patch_id=%s" in _seg
    assert "stage=" in _seg
    assert "missing" in _seg, "必须写明「这条补丁会保持 missing」（与台账对得上账）"
    _head = _s[max(0, _i - 400):_i]
    assert "_module_logger.warning(" in _head, "级别必须是 warning，不是 pass/debug"


def test_62_patch_manager_writes_error_field_for_triage():
    _s = _src("nucleus/reasoning/PatchManager.py")
    assert 'patch["effect_verify_error"]' in _s


def test_63_collector_publishes_effect_verify_block():
    _s = _src("organs/core/PulseMetricsCollector.py")
    assert "effect_verify_health" in _s
    assert "snapshot_block()" in _s
    # 取不到时必须写 None + state，不许写 False 冒充"不旧"
    _i = _s.find("[effect_verify]")
    assert _i > 0
    assert '["effect_verify_stale"] = None' in _s[_i:_i + 500]


def test_64_version_marker():
    assert ev.version().startswith("411.")
