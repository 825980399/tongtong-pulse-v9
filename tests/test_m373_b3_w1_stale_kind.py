# -*- coding: utf-8 -*-
"""第373批 刀3 · W1 心跳 stale 成因判别（P1 · 三闸闸①）

票：`T-372-W1心跳窗加密-1`

★本刀的任务书前提经实测推翻，改法随之改写（详见证据件 §刀3）：
  任务书说「W1 alive 窗现状 30min」「心跳更新 30min→10min 级」。
  实测（logs/pulse.log 638 条心跳行 / 2 个代次）：
    * 真实心跳 **11s/拍**，日志每 10 拍抽样 ⇒ 看起来 110s；
    * `DEFAULT_MAX_AGE_SEC=90s` = 连丢 7—8 拍，原设计成立；
    * `w1=alive` 296 条 / `w1=stale` 10 条，**10 条 stale 全部落在心跳停跳时段**
      （17:24 后 beat 恒定 1371）⇒ 卡点是**心脏停跳**，不是窗太短。
  ⇒ 本刀**不动阈值**（动它=放宽门槛，属已被否的候选乙同类），改为把 `stale`
  拆成可区分的成因，让下一次排查一眼看出「心脏没跳」还是「落盘滞后」。
"""

import json
import os
import time
from pathlib import Path

import pytest

from nucleus.world_model.external_heartbeat import (
    DEFAULT_MAX_AGE_SEC,
    W1_CRITERIA_VERSION,
    external_heartbeat_verdict,
    w1_verdict_brief,
)

_SRC_O3 = Path("nucleus/self_inspector.py")


def _mk_snapshot(root: Path, *, ts_age: float, beat: int, mtime_age: float | None = None) -> Path:
    """在临时 root 下造一份 health_snapshot.json（年龄可控）。"""
    d = root / "data" / "monitor"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "health_snapshot.json"
    _now = time.time()
    p.write_text(json.dumps({"timestamp": _now - ts_age, "beat_count": beat}),
                 encoding="utf-8")
    _age = ts_age if mtime_age is None else mtime_age
    _t = _now - _age
    os.utime(p, (_t, _t))
    return p


def test_01_stale_kind_age_over_when_beat_still_advancing(tmp_path):
    """年龄超线但 beat 在涨 ⇒ age_over（落盘滞后，不是心脏停跳）。"""
    _mk_snapshot(tmp_path, ts_age=600.0, beat=100)
    r = external_heartbeat_verdict(root=str(tmp_path), prev_sample={"beat_count": 90})
    assert r["verdict"] == "stale"
    assert r["beat_delta"] == 10
    assert r["stale_kind"] == "age_over", f"实得 {r['stale_kind']}，basis={r['basis']}"
    assert any("落盘滞后" in b for b in r["basis"]), "成因未写进 basis"


def test_02_stale_kind_beat_stalled_when_snapshot_fresh(tmp_path):
    """快照新鲜但 beat 不动 ⇒ beat_stalled（心脏没跳）。"""
    _mk_snapshot(tmp_path, ts_age=5.0, beat=100)
    r = external_heartbeat_verdict(root=str(tmp_path), prev_sample={"beat_count": 100})
    assert r["verdict"] == "stale"
    assert r["beat_delta"] == 0
    assert r["stale_kind"] == "beat_stalled", f"实得 {r['stale_kind']}，basis={r['basis']}"


def test_03_stale_kind_no_delta_when_prev_missing(tmp_path):
    """超龄且没给上次采样 ⇒ age_over_no_delta（承认无法区分，不猜）。"""
    _mk_snapshot(tmp_path, ts_age=600.0, beat=100)
    r = external_heartbeat_verdict(root=str(tmp_path), prev_sample=None)
    assert r["verdict"] == "stale"
    assert r["stale_kind"] == "age_over_no_delta", f"实得 {r['stale_kind']}"


def test_04_alive_has_no_stale_kind(tmp_path):
    """alive 时成因必须为 None，不得残留上一分支的取值。"""
    _mk_snapshot(tmp_path, ts_age=5.0, beat=100)
    r = external_heartbeat_verdict(root=str(tmp_path), prev_sample={"beat_count": 90})
    assert r["verdict"] == "alive"
    assert r["beat_delta"] == 10
    assert r["stale_kind"] is None, f"alive 却带成因 {r['stale_kind']!r}"


def test_05_brief_carries_stale_kind(tmp_path):
    """brief 顶层必须带 stale_kind。

    ★337/349 两次教训：只留在 `raw` 里的字段，消费方（A6 / O3）永远读不到。
    """
    _mk_snapshot(tmp_path, ts_age=600.0, beat=100)
    b = w1_verdict_brief(prev_sample={"beat_count": 90}, root=str(tmp_path))
    assert "stale_kind" in b, f"brief 缺 stale_kind，键={sorted(b)}"
    assert b["stale_kind"] == "age_over"
    assert b["raw"]["stale_kind"] == "age_over"


def test_06_criteria_version_bumped():
    """判据面变了（新增成因维度）⇒ 版本号必须升（模块自定规则）。"""
    assert W1_CRITERIA_VERSION == "373.w1.v2", f"实得 {W1_CRITERIA_VERSION}"


def test_07_max_age_not_loosened():
    """★守「不放宽门槛」：90s 阈值一字不动。

    任务书说「加密心跳窗」，实测证明心跳 11s/拍、90s 窗成立 ⇒ 该改的是
    成因可观测性，不是阈值。若有人为了「让闸①好过」把这里调大，本用例转红。
    """
    assert DEFAULT_MAX_AGE_SEC == 90.0


def test_08_o3_reason_carries_stale_kind():
    """O3 降级理由必须带成因（否则读者只能看到 `W1=stale` 两个字）。"""
    src = _SRC_O3.read_text(encoding="utf-8")
    assert '"stale_kind": _w1b.get("stale_kind")' in src, \
        "o3_open_gate_report 的 _rep['sample']['w1'] 未带 stale_kind"
    assert '_wk = _w1b.get("stale_kind")' in src, "降级理由未取成因变量"
    assert '(f"({_wk})" if _wk else "")' in src, "降级理由未拼成因"
    # 判据本体一字未动：与门仍是三项 and
    assert "_all_ok = bool(_mode_ok and _sample_ok and _mis_ok)" in src, \
        "三取与门被改动（本刀严禁）"
    assert "_M340_JUDGE_ACTION_MIN_TICKS = 3" in src, "连续 3 tick 判据被改动（本刀严禁）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
