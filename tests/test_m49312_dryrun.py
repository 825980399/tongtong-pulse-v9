# -*- coding: utf-8 -*-
"""493.12-4/5 dry-run 独立候选管线 + 观测指标 + date 过滤修复 + veto 拦截。

纪律：dry-run 只写候选文件，不写 overlay、不 promote、不改 link_organ/线上回答。
测试隔离真实数据文件（备份+还原）。
"""
import json
import os
import shutil

import pytest

from nucleus.self_index import learned_alias as la

_SHADOW = os.path.join("data", "monitor", "self_query_shadow.jsonl")
_DRYRUN = os.path.join("data", "self_index", "alias_candidates_dryrun.jsonl")
_VETO = os.path.join("data", "self_index", "alias_veto.json")
_OVERLAY = os.path.join("data", "self_index", "alias_overlay.json")


@pytest.fixture
def _isolate():
    backups = {}
    for p in (_SHADOW, _DRYRUN, _VETO, _OVERLAY):
        if os.path.isfile(p):
            backups[p] = p + ".bak49312"
            shutil.copyfile(p, backups[p])
            if p in (_DRYRUN, _OVERLAY):
                os.remove(p)  # 让候选/overlay 测试从干净开始
    yield
    for p, b in backups.items():
        shutil.copyfile(b, p)
        os.remove(b)
    # 复位计数器，避免跨测试污染
    la._DRYRUN_COUNTER = 0
    la._SEED_VETO_DONE = False


def _write_shadow(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_extract_appends_dryrun_not_overlay(_isolate, tmp_path):
    sp = str(tmp_path / "shadow.jsonl")
    _write_shadow(sp, [
        {"query": "胃在哪", "would_answer": "胃 在 organs.body.PulseStomach.py",
         "action_id": "organ.location", "organ": "胃", "confidence": 0.95,
         "skipped_by_guard": False},
        # 493.14-2：关系类 query 不进 alias 候选池（「肝怎么连」被过滤）
        {"query": "肝怎么连", "would_answer": None, "action_id": None,
         "organ": "肝", "confidence": None, "skipped_by_guard": False},
    ])
    n = la.extract_and_append_dryrun(sp)
    assert n == 1, n  # 仅「胃在哪」入池，「肝怎么连」被关系类过滤
    assert os.path.isfile(_DRYRUN)
    lines = [json.loads(x) for x in open(_DRYRUN, encoding="utf-8").read().splitlines() if x.strip()]
    assert len(lines) == 1
    assert {x["organ"] for x in lines} == {"胃"}
    # overlay 不得被写（active=0）
    ov = la.load_overlay()
    assert all(not e.get("active") for e in ov.get("aliases", {}).values())


def test_veto_blocks_candidate(_isolate, tmp_path):
    # 种子负例：「肺其实是肝」→ organ=肝 应被 veto 拦下，不进 dryrun
    sp = str(tmp_path / "shadow.jsonl")
    _write_shadow(sp, [
        {"query": "胃在哪", "would_answer": "胃 在 x", "action_id": "organ.location",
         "organ": "胃", "confidence": 0.9, "skipped_by_guard": False},
        {"query": "肺其实是肝", "would_answer": None, "organ": "肝",
         "skipped_by_guard": False},
    ])
    n = la.extract_and_append_dryrun(sp)
    assert n == 1, n  # 只有胃候选进文件，肺→肝被 veto 拦
    lines = [json.loads(x) for x in open(_DRYRUN, encoding="utf-8").read().splitlines() if x.strip()]
    assert {x["organ"] for x in lines} == {"胃"}


def test_metrics_online_delta_zero(_isolate, tmp_path):
    sp = str(tmp_path / "shadow.jsonl")
    _write_shadow(sp, [
        {"query": "胃在哪", "would_answer": "胃 在 x", "action_id": "organ.location",
         "organ": "胃", "confidence": 0.9, "skipped_by_guard": False},
    ])
    la.extract_and_append_dryrun(sp)
    m = la.metrics_dryrun()
    # 硬约束：dry-run 不改线上回答
    assert m["online_answer_delta"] == 0
    # 关键开关态
    assert m["switches"]["learned_aliases"] is False
    assert m["switches"]["dryrun_candidates"] is True


def test_link_organ_untouched_by_dryrun(_isolate, tmp_path):
    from nucleus.self_index.exporters import link_organ
    before = link_organ("胃在哪")
    sp = str(tmp_path / "shadow.jsonl")
    _write_shadow(sp, [
        {"query": "胃在哪", "would_answer": "胃 在 x", "action_id": "organ.location",
         "organ": "胃", "confidence": 0.9, "skipped_by_guard": False},
    ])
    la.extract_and_append_dryrun(sp)
    after = link_organ("胃在哪")
    # dry-run 不合并、不改 link_organ 输出
    assert before == after


def test_date_filter_fix_float_ts():
    # 493.12-4：真实影子 ts 是 float，_row_date_str 必须按日期归一化
    # （修复前 str(ts).find(date) 对 float 永远失败 → date 过滤完全失效）
    assert la._row_date_str(1794352372.817) == "2026-11-11"
    assert la._row_date_str(1794200000.0) == "2026-11-09"  # 早两天
    assert la._row_date_str("not-a-number") == ""
    # fuse_shadow_to_overlay 用归一化后的日期做比较（DIGEST_FUSION 关 → 直接返回 0，
    # 但 date 比较分支逻辑已抽成 _row_date_str 单测覆盖）
    assert la.fuse_shadow_to_overlay(date="2026-11-11") == 0


def test_maybe_trigger_every_100(_isolate, tmp_path):
    sp = str(tmp_path / "shadow.jsonl")
    _write_shadow(sp, [
        {"query": "胃在哪", "would_answer": "胃 在 x", "action_id": "organ.location",
         "organ": "胃", "confidence": 0.9, "skipped_by_guard": False},
    ])
    la._DRYRUN_COUNTER = 0
    triggered = 0
    for _ in range(99):
        triggered += la._maybe_trigger_dryrun()
    assert triggered == 0, "99 次不应触发"
    # 第 100 次触发（抽取默认读真实影子，这里只验证计数回零+触发信号）
    la._DRYRUN_COUNTER = 99
    r = la._maybe_trigger_dryrun()
    assert la._DRYRUN_COUNTER == 0
    # 触发时若真实影子为空（本测试未写真实影子），返回 0 行也被接受，但不应抛错
    assert r >= 0
