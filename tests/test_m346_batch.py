# -*- coding: utf-8 -*-
"""第346批施工回归（7 刀关键判据）

覆盖：
  d1 问题池消费口（三族分流 / 状态推进 / 留痕 / 幂等 / 池空 noop / 未结案豁免滚动）
  d2 回退台账记账（status 补 rolled_back / 迁移表 / 非法不写 / effect_verify 联动）
  d3 processing_type 接线（发射点全覆盖 / 7 项补表 / deriver_ 前缀 / 标注幂等 / 纯新增）
  d4 高危族扩表（4 族低→高 / 高风险不放行 / 低风险反向对照）
  d5 risk_level 读 rank（中英双写 / 未知不猜 / 下游三处单一判据）
  d6 池落盘域对齐（项目根定位 / 字面量可 grep / 隔离声明拒写且不建目录）
  d7 硬编码扫描器（版本 / 模式名规范化 / 计数自洽）

纪律：
  * 全部自包含（tmp_path / 合成数据），不依赖现场 data/（铁律179）
  * 不写生产目录（铁律158/172）；模块 logger 一律接管
  * 断言用显式布尔/档位，不从理由字符串找关键字（铁律153）
"""

import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nucleus import audit_problem_pool as pool  # noqa: E402
from nucleus import audit_problem_pool_consumer as cons  # noqa: E402
from nucleus.evolution import patch_lifecycle as life  # noqa: E402
from nucleus.processing_type import annotate_payload, processing_type_of  # noqa: E402
from nucleus.reasoning.PatchManager import PatchManager as PM  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet_loggers():
    """接管相关 logger，禁止合成指纹进生产日志（铁律172）。"""
    _names = ("pulse.module.nucleus.audit_problem_pool",
              "pulse.module.nucleus.audit_problem_pool_consumer",
              "pulse.module.PatchManager",
              "pulse.module.nucleus.evolution.patch_lifecycle")
    for _n in _names:
        _lg = logging.getLogger(_n)
        _lg.handlers = [logging.NullHandler()]
        _lg.propagate = False
    yield


def _seed(tmp_path, issues):
    """在隔离根落一份池，返回 (root, audit_dir)。"""
    root = str(tmp_path)
    _audit = os.path.join(root, pool.AUDIT_DIR_REL)
    pool.split_routes([i for i in issues if isinstance(i, dict)])
    pool.persist_pool({"issues": issues}, root=root)
    return root, _audit


# ========== d1 消费口 ==========


def test_d1_select_only_open_and_scan_all_before_truncate():
    """只取 open；且筛选必须先遍历全池再截断（铁律163）。"""
    entries = []
    for i in range(30):
        entries.append({"dedup_key": "k%02d" % i, "status": pool.STATUS_CLAIMED,
                        "first_seen_ts": 100 + i, "severity": "high"})
    entries.append({"dedup_key": "open_old", "status": pool.STATUS_OPEN,
                    "first_seen_ts": 1, "severity": "low"})
    entries.append({"dedup_key": "open_new", "status": pool.STATUS_OPEN,
                    "first_seen_ts": 999, "severity": "high"})
    picked = cons.select_consumable(entries, limit=2)
    assert [p["dedup_key"] for p in picked] == ["open_new", "open_old"]
    assert all(p["status"] == pool.STATUS_OPEN for p in picked)


def test_d1_consume_three_routes_and_status_claimed(tmp_path):
    root, audit = _seed(tmp_path, [
        {"type": "dead_code", "severity": "high", "file": "nucleus/a.py", "method": "a"},
        {"type": "reflection", "severity": "medium", "file": "nucleus/b.py", "method": "b"},
        {"type": "pollution", "severity": "low", "file": "data/n.json", "method": ""},
    ])
    r = cons.consume_pool(root=root, audit_dir_path=audit)
    assert r["state"] == "ok"
    assert r["consumed"] == 3
    assert r["routes"] == {pool.ROUTE_PATCH_POOL: 1, pool.ROUTE_OPTIMIZE_LIST: 1,
                           pool.ROUTE_CLEANUP_QUEUE: 1}
    doc = pool.load_pool(root=root)
    assert all(e["status"] == pool.STATUS_CLAIMED for e in doc["entries"])
    _notes = [h.get("note", "") for e in doc["entries"] for h in e.get("status_history") or []]
    assert sum(1 for n in _notes if n.startswith(cons.TRACE_TAG)) == 3


def test_d1_consume_is_idempotent(tmp_path):
    root, audit = _seed(tmp_path, [
        {"type": "dead_code", "severity": "high", "file": "nucleus/a.py", "method": "a"},
    ])
    assert cons.consume_pool(root=root, audit_dir_path=audit)["consumed"] == 1
    second = cons.consume_pool(root=root, audit_dir_path=audit)
    assert second["state"] == "noop_pool_empty"
    assert second["consumed"] == 0
    _cand = os.path.join(audit, "routes", cons.PATCH_CANDIDATE_FILE)
    assert len([x for x in open(_cand, encoding="utf-8") if x.strip()]) == 1


def test_d1_empty_pool_is_noop_not_zero(tmp_path):
    _empty = os.path.join(str(tmp_path), "no_such_pool")
    r = cons.consume_pool(root=str(tmp_path), audit_dir_path=_empty)
    assert r["state"] == "noop_pool_empty"
    assert r["reason"] == "no_snapshot"
    assert r["consumed"] == 0


def test_d1_unclosed_entry_survives_rolling_window():
    now = time.time()
    old = now - 10 * 86400
    kept, dropped = pool.prune([
        {"dedup_key": "open", "status": pool.STATUS_OPEN, "first_seen_ts": old},
        {"dedup_key": "fixed", "status": pool.STATUS_FIXED, "first_seen_ts": old},
        {"dedup_key": "verified", "status": pool.STATUS_VERIFIED, "first_seen_ts": old},
        {"dedup_key": "fresh", "status": pool.STATUS_OPEN, "first_seen_ts": now - 60},
    ], now, pool.POOL_RETAIN_DAYS)
    ids = {e["dedup_key"] for e in kept}
    assert ids == {"open", "fixed", "fresh"}
    assert dropped == 1


# ========== d2 回退台账 ==========


@pytest.mark.parametrize("old,new,expect", [
    ("approved", "rolled_back", True),
    ("runtime_verified", "rolled_back", True),
    ("applied", "rolled_back", True),
    ("rolled_back", "rolled_back", False),
    ("rolled_back", "applied", False),
    ("rolled_back", "pending", True),
    ("weird_state", "rolled_back", False),
    ("rejected_safety", "pending", True),
    ("rejected_safety", "applied", False),
])
def test_d2_transition_table(old, new, expect):
    assert life.legal_history_transition(old, new) is expect


def test_d2_mark_rolled_back_writes_fields():
    p = {"id": "x", "status": "approved"}
    r = life.mark_history_rolled_back(p, reason="r", now=1234.0, source="t")
    assert r["ok"] is True
    assert p["status"] == life.HISTORY_STATUS_ROLLED_BACK
    assert p["rolled_back_at"] == 1234.0
    assert p["rolled_back_reason"] == "r"
    assert p["status_history"][-1]["from"] == "approved"


def test_d2_illegal_transition_writes_nothing():
    p = {"id": "x", "status": "rolled_back"}
    r = life.mark_history_rolled_back(p, reason="again")
    assert r["ok"] is False
    assert p["status"] == "rolled_back"
    assert "status_history" not in p


# ========== d4 / d5 风险档 ==========


@pytest.mark.parametrize("family", ["command_injection", "path_traversal",
                                    "eval_usage", "pickle_load",
                                    "sql_injection", "lock_with_emit",
                                    "non_atomic_write"])
def test_d4_security_families_are_high(family):
    assert PM._m280_risk_by_issue_type(family) == "高"


@pytest.mark.parametrize("raw,expect", [
    ("低", 1), ("low", 1), ("LOW", 1), ("中", 2), ("medium", 2),
    ("高", 3), ("high", 3), ("极低", None), ("", None), (None, None), ("weird", None),
])
def test_d5_risk_rank(raw, expect):
    assert PM._m346_risk_rank(raw) is expect


def test_d5_new_family_blocks_auto_apply():
    patch = {"id": "p", "file": "nucleus/demo.py", "method": "normal_method",
             "source": "local_rule", "confidence": "high",
             "issue_type": "command_injection", "risk_level": "低",
             "learning_attempt": False}
    merged, _ = PM._m344_merge_risk("低", "command_injection")
    patch["risk_level"] = merged
    assert merged == "高"
    assert PM._m85_local_low_risk_auto_apply(patch, {"local_auto_apply_enabled": True}) is False


def test_d5_low_family_still_releasable(monkeypatch):
    """★406刀4：本用例验 m346 的低危族表（"低危族可放行"），与 406「diff 窄闸」正交。

    夹具没有 original_code/modified_code ⇒ 窄闸判 `unknown` → medium → 拦下，
    本用例就变成"因为别的原因 False"，测不到 m346 的低危族结论。
    ⇒ 先把"窄闸在位会拦"钉住，再关掉窄闸验低危族本身（收窄而非删除能力）。
    """
    import config

    patch = {"id": "p", "file": "nucleus/demo.py", "method": "normal_method",
             "source": "local_rule", "confidence": "high",
             "issue_type": "periodic_task_no_reentry", "risk_level": "低",
             "learning_attempt": False}
    # ① 窄闸在位：同一份补丁被拦（收窄是有意的）
    assert getattr(config, "PATCH_M406_NARROW_GATE", None) is True, "★窄闸必须默认在位"
    assert PM._m85_local_low_risk_auto_apply(
        patch, {"local_auto_apply_enabled": True}) is False, (
        "★406窄闸未生效：没有 diff 可判的补丁竟然被免签放行")
    # ② 关掉窄闸：低危族本身仍可放行（m346 结论不变）
    monkeypatch.setattr(config, "PATCH_M406_NARROW_GATE", False, raising=False)
    assert PM._m85_local_low_risk_auto_apply(patch, {"local_auto_apply_enabled": True}) is True


def test_d5_no_chinese_literal_comparison_left():
    _src = open(os.path.join(os.path.dirname(__file__), "..", "nucleus", "reasoning",
                             "PatchManager.py"), encoding="utf-8").read()
    assert 'risk_level", "")) != "低"' not in _src
    assert _src.count('PatchManager._m346_is_low_risk(patch.get("risk_level"))') == 3


# ========== d3 processing_type ==========


@pytest.mark.parametrize("method,expect", [
    ("branch_expand_request", "self_rule"), ("code_call_chain", "self_rule"),
    ("cognitive_compute", "self_rule"), ("composite_logic", "self_rule"),
    ("conflict_exclusive", "self_rule"), ("deep_contemplation", "self_rule"),
    ("deep_review_report", "hybrid"),
])
def test_d3_added_methods(method, expect):
    assert processing_type_of(method)[0] == expect


def test_d3_deriver_prefix():
    assert processing_type_of("deriver_deduction") == ("self_rule", "self_rule_prefix")
    assert processing_type_of("deriver_unknown_type")[0] == "self_rule"


def test_d3_annotate_is_idempotent_and_additive():
    base = {"method": "contemplation", "answer": "a"}
    p = dict(base)
    annotate_payload(p)
    first = (p["processing_type"], p["processing_type_source"])
    annotate_payload(p)
    assert (p["processing_type"], p["processing_type_source"]) == first
    assert first == ("llm_generated", "method_table")
    assert set(p) - set(base) == {"processing_type", "processing_type_source"}
    for k, v in base.items():
        assert p[k] == v


def test_d3_generator_declaration_wins():
    p = {"method": "deep_think", "processing_type": "hybrid"}
    annotate_payload(p)
    assert p["processing_type"] == "hybrid"
    assert p["processing_type_source"] == "explicit"


# ========== d6 池落盘域 ==========


def test_d6_project_root_is_repo_root():
    _repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    assert os.path.abspath(pool._project_root()) == _repo
    assert os.path.abspath(cons._project_root()) == _repo


def test_d6_literal_path_is_greppable():
    assert pool.AUDIT_DIR_LITERAL == "data/reports/audit"
    assert pool.AUDIT_DIR_REL.replace("\\", "/") == pool.AUDIT_DIR_LITERAL


def test_d6_isolated_process_cannot_write_prod_pool(tmp_path, monkeypatch):
    prod_dir = os.path.join(os.path.abspath(pool._project_root()), pool.AUDIT_DIR_REL)
    _existed = os.path.isdir(prod_dir)
    monkeypatch.setenv("PULSE_ISOLATION_ROOT", str(tmp_path))
    ok = pool._atomic_write_json(os.path.join(prod_dir, "audit_19990101.json"), {"x": 1})
    assert ok is False
    assert _existed or not os.path.isdir(prod_dir)
    assert not os.path.isfile(os.path.join(prod_dir, "audit_19990101.json"))


# ========== d7 硬编码扫描器 ==========


def test_d7_scanner_version_and_patterns():
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from tools.audit import criteria_hardcode_scan as scan
    r = scan.scan()
    assert r["hits"] == []
    assert r["pattern_counts"]
    assert sum(r["pattern_counts"].values()) == (
        len(r["hits"]) + len(r["generic_hour_windows"]) + len(r["source_internal_literals"]))
    for rec in r["generic_hour_windows"]:
        assert rec["pattern"]
        assert "|" not in rec["pattern"]  # 规范模式名不再用 `|` 拼（那是不稳定键）


def test_d7_source_of_truth_exempt():
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from tools.audit import criteria_hardcode_scan as scan
    r = scan.scan()
    assert all(rec["file"] != scan.SOURCE_OF_TRUTH for rec in r["hits"])
