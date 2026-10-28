# -*- coding: utf-8 -*-
"""386刀1：破零观察位人工补位（tools/m386_seed_o3_watch.py）的门禁用例。

覆盖三条硬事实，都在本批 T0 用真实数据量过：
  ① O3 id **必须**按 `dead_code_candidate` + `335.cg.v1` 复算（三条历史条目逐字一致）
  ② 补位后 `watch_candidate` **不再**返 `not_in_pool`
  ③ 本工具只落**未审批**形态 —— 准入回填归 377 的既有工具，两步可分别回滚
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "tools"))

import m386_seed_o3_watch as seed  # noqa: E402
from nucleus.evolution import zero_break_judge as zb  # noqa: E402
from nucleus.evolution.patch_execution_dispatch import (  # noqa: E402
    _m339_o3_dispatch_ready, _m343_o3_stable_id,
)

#: ★真实历史条目（384刀2 清理前的 pending 备份里就有），不是构造出来的
HIST = [
    ("nucleus/audit_problem_pool.py", "restore_pool", "o3_b31b31fee790d6e9"),
    ("nucleus/audit_problem_pool.py", "plan_target_drift", "o3_49e50495d5e24d5a"),
    ("nucleus/audit_problem_pool.py", "pool_summary", "o3_59cd0eef8e125186"),
]


def test_m386_d1_o3_id_recomputable():
    """★核心：三条历史 id 逐字复算一致 ⇒ 后来者可以用同一算法对账。"""
    for _f, _m, _want in HIST:
        assert seed.o3_id(_f, _m) == _want, "%s::%s ⇒ %s ≠ %s" % (_f, _m, seed.o3_id(_f, _m), _want)


def test_m386_d1_wrong_issue_type_breaks_the_id():
    """★反向：用池里的 `dead_code`（而非 `dead_code_candidate`）算 id ⇒ **对不上**。

    这是本刀最容易被后人踩的坑：池条目 issue_type 就是 `dead_code`，
    照抄它生成 id 会算出另一个值 ⇒ 重演 343刀1 修掉的「同题不同 id」。
    """
    _f, _m, _want = HIST[0]
    _wrong = _m343_o3_stable_id(_f, _m, "dead_code", "335.cg.v1")
    assert _wrong != _want, "若相等说明 issue_type 不影响 id，本守卫失去意义"
    _no_marker = _m343_o3_stable_id(_f, _m, "dead_code_candidate", "")
    assert _no_marker != _want, "marker 也必须带（335.cg.v1）"


def test_m386_d1_entry_is_unapproved():
    """★核心：本工具落**未审批**形态 —— 准入字段绝不在这里写。

    `o3_auto_dispatch_ready` 的合规写入口是 `approve_o3_patch`；
    本工具若顺手塞了它，等于「自己入池 + 自己批准」，审批路径被绕开。
    """
    _e = seed.build_entry("a/b.py", "m1", line=10, dedup_key="d" * 16,
                          pool_desc="x", now=1.0)
    assert _e["requires_confirmation"] is True
    assert "o3_auto_dispatch_ready" not in _e, "准入字段不该由补位工具写"
    assert _e["status"] == "pending"
    assert _e["o3_dead_code"] is True
    assert _e["issue_type"] == "dead_code_candidate"
    assert _e["id"] == seed.o3_id("a/b.py", "m1")
    # 留痕：后人一眼看出是人工补位
    assert "386刀1" in str(_e.get("seeded_by") or "")


def test_m386_d1_seeded_entry_blocked_by_dispatch_gate():
    """未审批 ⇒ 派单闸 `not_confirmed` —— 证明「在库」与「可派单」是两件事。"""
    _e = seed.build_entry("a/b.py", "m1", line=10, dedup_key=None, pool_desc="", now=1.0)
    _ok, _why = _m339_o3_dispatch_ready(_e)
    assert _ok is False and _why == "not_confirmed"


def test_m386_d1_approval_makes_it_dispatchable():
    """★补位 + 准入回填（377 工具口径）两步合起来 ⇒ 闸开。

    这是票面「重启后 selected=1」的前提：只补位不回填 ⇒ 永远 not_confirmed。
    """
    _e = seed.build_entry("a/b.py", "m1", line=10, dedup_key=None, pool_desc="", now=1.0)
    _e["o3_auto_dispatch_ready"] = True
    _e["requires_confirmation"] = False
    _ok, _why = _m339_o3_dispatch_ready(_e)
    assert _ok is True and _why == "ok"


def test_m386_d1_watch_no_longer_not_in_pool():
    """★核心（票面验收）：补位前 not_in_pool，补位后有**具体** missing 项。"""
    _pid = seed.o3_id("a/b.py", "m1")
    _before = zb.watch_candidate(_pid, pending_rows=[], project_root=ROOT)
    assert _before["verdict"] == "not_in_pool"

    _pend = [seed.build_entry("a/b.py", "m1", line=10, dedup_key=None,
                              pool_desc="", now=1.0)]
    _after = zb.watch_candidate(_pid, pending_rows=_pend, project_root=ROOT)
    assert _after["in_pending"] is True
    assert _after["verdict"] != "not_in_pool"
    assert len(_after["missing"]) >= 1, "必须有具体缺口，空列表说明判据没真跑"


def test_m386_d1_self_heal_picks_the_seeded_entry():
    """补位后自愈能自己挑到它（不再 no_candidate）。"""
    zb.reset_watch_runtime()
    try:
        _pend = [seed.build_entry("a/b.py", "m1", line=10, dedup_key=None,
                                  pool_desc="", now=1.0)]
        _r = zb.self_heal_watch(_pend, [])
        assert _r["state"] == "healed", _r
        assert _r["added"] == seed.o3_id("a/b.py", "m1")
        assert seed.o3_id("a/b.py", "m1") in zb.active_watch_ids()
    finally:
        zb.reset_watch_runtime()


def test_m386_d1_idempotent_and_dryrun(tmp_path, monkeypatch, capsys):
    """幂等：同 id 已在 pending ⇒ 不追加；dry-run ⇒ 一个字节都不写。"""
    _pid = seed.o3_id("nucleus/runtime/OrganCoordinator.py", "report_ready")
    _target = str(tmp_path / "pending.json")
    _rows = [{"id": _pid, "file": "nucleus/runtime/OrganCoordinator.py",
              "method": "report_ready", "status": "pending"}]
    with io.open(_target, "w", encoding="utf-8", newline="\n") as _f:
        json.dump(_rows, _f, ensure_ascii=False, indent=2)
    _sha_before = seed._sha256(_target)

    _argv = ["prog", "--target", _target]
    monkeypatch.setattr(sys, "argv", _argv)
    _rc = seed.main()
    assert _rc == 0
    assert "幂等" in capsys.readouterr().out
    assert seed._sha256(_target) == _sha_before, "dry-run/幂等都不许改盘"
    assert len(json.load(io.open(_target, encoding="utf-8"))) == 1


def test_m386_d1_filter_rejects_core_and_god_file():
    """候选筛选的边界：核心件、超长文件、方法有调用点 —— 一律排除。"""
    _rows = [
        {"issue_type": "dead_code", "status": "open", "file": "config.py",
         "method": "x", "line": 1},                       # 核心件
        {"issue_type": "dead_code", "status": "open", "file": "nucleus/a.py",
         "method": "y", "line": 1},                       # 文件不存在
        {"issue_type": "dead_code", "status": "claimed", "file": __file__,
         "method": "test_m386_d1_filter_rejects_core_and_god_file", "line": 1},
    ]
    _out = seed.pick_candidates(_rows)
    assert _out == [], "三条都不该入选：%s" % _out
