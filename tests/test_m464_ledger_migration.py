# -*- coding: utf-8 -*-
"""第464批：让 463 的判据真正作用到「已落盘」的僵尸台账 · 单测

刀1 载入期迁移（skip_cool 放行 / cool_short 收短 / cool 保留）
刀2 强制复检命中数跨重启续期（cooldown.json 第 3 个元素）
刀3 漏斗行补 cooled_buckets / cooled_top 明细

写盘一律走 tmp_path；`_m114a_cooldown_file` 在**第一行**就改到临时目录
（418 教训：实例化后跑生产方法，先改路径）。
"""
from __future__ import annotations

import importlib
import json
import os

import config
from nucleus.evolution import cooldown_policy as CP
from nucleus.evolution import repair_funnel as RF

# ★铁律60：包里同名导出会盖掉子模块，取模块一律 importlib。
SEE = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")

LOCAL = {"silent_exception", "bare_except"}
HIGH = {"unsafe_eval"}

_FP_CODE = "organs/core/PulseSystemManager.py|_schedule_health_check|periodic_task_no_reentry"
_FP_LONG = "organs/body/PulseLiver.py|_compress_group|long_method"
_FP_LOG = "organ:潜意识|type:growth_need|假设：某条成长需求"


def _mk(tmp_path):
    """最小可用实例：不跑 __init__，先钉死冷却落盘路径。"""
    obj = SEE.SafeEvolutionExecutor.__new__(SEE.SafeEvolutionExecutor)
    obj._project_root = str(tmp_path)
    obj._m114a_cooldown_file = os.path.join(str(tmp_path), "cooldown.json")
    obj._no_fix_cooldown = {}
    obj._no_fix_cooldown_rounds = {}
    obj._no_fix_cooldown_secs = {}
    obj._no_fix_cooldown_enabled = True
    return obj


def _write_ledger(obj, data):
    with open(obj._m114a_cooldown_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _read_ledger(obj):
    with open(obj._m114a_cooldown_file, "r", encoding="utf-8") as f:
        return json.load(f)


# ==================== 刀1：指纹反解 ====================

def test_fingerprint_code_shape():
    r = CP.issue_from_fingerprint(_FP_CODE)
    assert r["file"] == "organs/core/PulseSystemManager.py"
    assert r["method"] == "_schedule_health_check"
    assert r["type"] == "periodic_task_no_reentry"


def test_fingerprint_log_shape():
    r = CP.issue_from_fingerprint("organ:自检视|type:ERROR|2026-11-06 某条红告警")
    assert r["organ"] == "自检视"
    assert r["type"] == "ERROR"
    assert "红告警" in r["description"]


def test_fingerprint_roundtrip_matches_classify():
    """反解出来的 issue 交给 classify，结论必须与登记侧对同一指纹一致。"""
    code = CP.issue_from_fingerprint(_FP_CODE)
    assert CP.classify(code, local_fixable_types=LOCAL,
                       high_risk_types=HIGH)["action"] == CP.A_SKIP
    log = CP.issue_from_fingerprint(_FP_LOG)
    assert CP.classify(log, local_fixable_types=LOCAL,
                       high_risk_types=HIGH)["action"] == CP.A_COOL


# ==================== 刀1：迁移三态 ====================

def test_migrate_releases_fixable_code():
    """★464 核心：能修的代码类被判过 6 小时冷却 ⇒ 载入即放行。"""
    out, rep = CP.migrate_ledger({_FP_CODE: [21600.0, 0]},
                                 local_fixable_types=LOCAL,
                                 high_risk_types=HIGH, short_sec=300.0)
    assert out == {}
    assert rep["released"] == 1 and rep["total"] == 1


def test_migrate_shortens_unknown_code_and_keeps_tail():
    out, rep = CP.migrate_ledger({_FP_LONG: [19000.0, 2, 7]},
                                 local_fixable_types=LOCAL,
                                 high_risk_types=HIGH, short_sec=300.0)
    assert out[_FP_LONG][0] == 300.0
    assert out[_FP_LONG][1:] == [2, 7], "轮次/命中数必须原样透传"
    assert rep["shortened"] == 1


def test_migrate_never_extends_short_entry():
    """只收短不延长：本来就比 short_sec 短的条目保持原值。"""
    out, rep = CP.migrate_ledger({_FP_LONG: [60.0, 0]},
                                 local_fixable_types=LOCAL,
                                 high_risk_types=HIGH, short_sec=300.0)
    assert out[_FP_LONG][0] == 60.0
    assert rep["shortened"] == 0 and rep["kept"] == 1


def test_migrate_keeps_log_class():
    out, rep = CP.migrate_ledger({_FP_LOG: [7200.0, 0]},
                                 local_fixable_types=LOCAL,
                                 high_risk_types=HIGH, short_sec=300.0)
    assert out[_FP_LOG][0] == 7200.0
    assert rep["kept"] == 1


def test_migrate_high_risk_kept():
    fp = "organs/x/PulseX.py|run|unsafe_eval"
    out, rep = CP.migrate_ledger({fp: [80000.0, 0]},
                                 local_fixable_types=LOCAL,
                                 high_risk_types=HIGH, short_sec=300.0)
    assert out[fp][0] == 80000.0 and rep["kept"] == 1


def test_migrate_invalid_rows_counted():
    out, rep = CP.migrate_ledger({"bad": "not-a-list", _FP_LOG: []},
                                 local_fixable_types=LOCAL,
                                 high_risk_types=HIGH, short_sec=300.0)
    assert out == {}
    assert rep["invalid"] == 2 and rep["total"] == 2


def test_migrate_is_pure():
    src = {_FP_LONG: [19000.0, 2]}
    CP.migrate_ledger(src, local_fixable_types=LOCAL,
                      high_risk_types=HIGH, short_sec=300.0)
    assert src == {_FP_LONG: [19000.0, 2]}, "纯函数不得改入参"


# ==================== 刀1：载入路径接线 ====================

def test_load_migrates_and_rewrites(tmp_path):
    obj = _mk(tmp_path)
    _write_ledger(obj, {_FP_CODE: [19000.0, 0],
                        _FP_LONG: [19000.0, 1],
                        _FP_LOG: [1000.0, 0]})
    obj._m114a_load_cooldown()
    assert _FP_CODE not in obj._no_fix_cooldown, "能修的代码类必须被放行"
    assert _FP_LONG in obj._no_fix_cooldown and _FP_LOG in obj._no_fix_cooldown
    left = obj._no_fix_cooldown[_FP_LONG] - __import__("time").monotonic()
    assert 250.0 < left <= 300.0, left
    disk = _read_ledger(obj)
    assert _FP_CODE not in disk, "迁移结果必须落盘，否则下次重启又装回来"
    assert len(disk) == 2


def test_load_migration_gate_off(tmp_path, monkeypatch):
    """总闸关 ⇒ 逐字节回到 464 前（旧台账原样装回）。"""
    monkeypatch.setattr(config, "ENABLE_M464_LEDGER_MIGRATION", False)
    obj = _mk(tmp_path)
    _write_ledger(obj, {_FP_CODE: [19000.0, 0]})
    obj._m114a_load_cooldown()
    assert _FP_CODE in obj._no_fix_cooldown


# ==================== 刀2：命中数跨重启 ====================

def test_load_restores_hits(tmp_path):
    obj = _mk(tmp_path)
    _write_ledger(obj, {_FP_LOG: [600.0, 2, 5]})
    obj._m114a_load_cooldown()
    assert obj._m463_recheck_hits == {_FP_LOG: 5}


def test_save_writes_hits_and_roundtrip(tmp_path):
    obj = _mk(tmp_path)
    _write_ledger(obj, {_FP_LOG: [600.0, 2]})
    obj._m114a_load_cooldown()
    obj._m463_recheck_hits = {_FP_LOG: 4}
    obj._m114a_save_cooldown()
    assert _read_ledger(obj)[_FP_LOG][2] == 4
    obj2 = _mk(tmp_path)
    obj2._m114a_load_cooldown()
    assert obj2._m463_recheck_hits == {_FP_LOG: 4}, "重启后命中数必须还在"


def test_save_gate_off_keeps_two_elements(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M464_PERSIST_RECHECK_HITS", False)
    obj = _mk(tmp_path)
    _write_ledger(obj, {_FP_LOG: [600.0, 1]})
    obj._m114a_load_cooldown()
    obj._m463_recheck_hits = {_FP_LOG: 9}
    obj._m114a_save_cooldown()
    assert len(_read_ledger(obj)[_FP_LOG]) == 2


# ==================== 刀3：漏斗明细 ====================

def _detail():
    return [{"policy": "skip_cool", "type": "periodic_task_no_reentry",
             "file": "organs/core/PulseSystemManager.py",
             "method": "_schedule_health_check", "rem_s": 17800},
            {"policy": "cool", "type": "growth_need",
             "file": "", "method": "", "rem_s": 900}]


def test_funnel_detail_written(tmp_path):
    RF.note({"candidate": 5, "cooldown_skipped": 2, "admitted": 3,
             "cooled_detail": _detail()}, root=str(tmp_path))
    row = RF.rows(root=str(tmp_path), limit=1)[0]
    assert row["candidate"] == 5 and row["admitted"] == 3
    assert row["cooled_buckets"] == {"skip_cool": 1, "cool": 1}
    assert row["cooled_top"][0]["policy"] == "skip_cool"
    assert row["cooled_top"][0]["rem_s"] == 17800


def test_funnel_detail_gate_off(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M464_FUNNEL_DETAIL", False)
    RF.note({"candidate": 1, "cooled_detail": _detail()}, root=str(tmp_path))
    row = RF.rows(root=str(tmp_path), limit=1)[0]
    assert "cooled_top" not in row and "cooled_buckets" not in row


def test_funnel_detail_bounded(tmp_path):
    many = [{"policy": "cool", "type": "t", "file": "f", "method": "m",
             "rem_s": 1}] * 50
    RF.note({"cooled_detail": many}, root=str(tmp_path))
    row = RF.rows(root=str(tmp_path), limit=1)[0]
    assert len(row["cooled_top"]) == 5
    assert row["cooled_buckets"] == {"cool": 20}
