# -*- coding: utf-8 -*-
"""415 刀3 单测：deferred 五项验证脚本的五条判据（存在/不存在两分支）。

被测对象：tools/m415_deferred_verify.py
重点（都由 412/413/414 的坑换来的）：
  * 字段可能落 `extra`（`row_of` 骨架外）⇒ 判据要看两处（铁律 27）
  * `pytest*.log` 里的 perf_delta 命中**不算**触发（假绿，铁律 26）
  * 空/坏行不能当 PASS（铁律 7：0 值先怀疑采集）
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import m415_deferred_verify as V  # noqa: E402

DAY = "20261102"


# ---------------------------------------------------------------- 1 叙事落盘
def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _row(headline="今天更深了一点", intent="继续观察", cur=12):
    return {"date": DAY, "version": "412.2", "headline": headline, "intent": intent,
            "delta": {"nodes": {"cur": cur, "prev": 10, "delta": cur - 10 if cur is not None else None,
                                "reason": "ok"}}}


def test_narrative_persist_missing(tmp_path):
    r = V.check_narrative_persist(str(tmp_path / "narrative"), DAY)
    assert r["ok"] is False
    assert r["value"]["files"] == 0


def test_narrative_persist_ok(tmp_path):
    d = tmp_path / "narrative"
    _write_jsonl(str(d / ("narrative_%s.jsonl" % DAY)), [_row()])
    r = V.check_narrative_persist(str(d), DAY)
    assert r["ok"] is True, r
    assert r["value"]["rows"] == 1


def test_narrative_persist_fields_in_extra(tmp_path):
    """★铁律 27：骨架外字段落 extra，判据必须能找到。"""
    d = tmp_path / "narrative"
    row = {"date": DAY, "delta": {},
           "extra": {"headline": "来自 extra", "intent": "来自 extra",
                     "delta": {"nodes": {"cur": 3}}}}
    _write_jsonl(str(d / ("narrative_%s.jsonl" % DAY)), [row])
    r = V.check_narrative_persist(str(d), DAY)
    assert r["ok"] is True, r
    assert r["value"]["headline"] == 1 and r["value"]["intent"] == 1


def test_narrative_persist_empty_headline_fails(tmp_path):
    d = tmp_path / "narrative"
    _write_jsonl(str(d / ("narrative_%s.jsonl" % DAY)), [_row(headline="")])
    r = V.check_narrative_persist(str(d), DAY)
    assert r["ok"] is False
    assert r["value"]["headline"] == 0


def test_narrative_persist_only_bad_lines(tmp_path):
    d = tmp_path / "narrative"
    os.makedirs(str(d), exist_ok=True)
    with open(str(d / ("narrative_%s.jsonl" % DAY)), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{坏行}\n")
    r = V.check_narrative_persist(str(d), DAY)
    assert r["ok"] is False and r["value"]["bad"] == 1


# ---------------------------------------------------------------- 2 经验池模板标记
def test_experience_template_missing(tmp_path):
    r = V.check_experience_template(str(tmp_path / "nope.json"))
    assert r["ok"] is False


def test_experience_template_zero(tmp_path):
    p = tmp_path / "pool.json"
    p.write_text(json.dumps({"experiences": [{"id": "a"}, {"id": "b"}]}), encoding="utf-8")
    r = V.check_experience_template(str(p))
    assert r["ok"] is False
    assert r["value"] == {"total": 2, "is_template": 0, "has_field": 0}


def test_experience_template_some(tmp_path):
    p = tmp_path / "pool.json"
    p.write_text(json.dumps({"experiences": [
        {"id": "a", "is_template": True}, {"id": "b", "is_template": False}]}), encoding="utf-8")
    r = V.check_experience_template(str(p))
    assert r["ok"] is True
    assert r["value"]["is_template"] == 1 and r["value"]["has_field"] == 2


def test_experience_template_bad_json(tmp_path):
    p = tmp_path / "pool.json"
    p.write_text("{not json", encoding="utf-8")
    r = V.check_experience_template(str(p))
    assert r["ok"] is False


# ---------------------------------------------------------------- 3 O3 进夜摘要
def test_o3_digest_missing(tmp_path):
    r = V.check_o3_in_digest(str(tmp_path / "digest_x.md"))
    assert r["ok"] is False


def test_o3_digest_with_section_and_number(tmp_path):
    p = tmp_path / "digest.md"
    p.write_text("# t\n## 七、叙事\n\n## 八、O3 删死代码 dry-run（412刀4 · 只统计不 apply）\n\n"
                 "- 若开闸将放行：**440** 条（O3 判死族 590 条，不可判 41 条）\n", encoding="utf-8")
    r = V.check_o3_in_digest(str(p))
    assert r["ok"] is True, r
    assert r["value"]["eligible"] == 440 and r["value"]["sections"] == 2


def test_o3_digest_section_without_number(tmp_path):
    p = tmp_path / "digest.md"
    p.write_text("## 八、O3 删死代码 dry-run\n- 报表失败\n", encoding="utf-8")
    r = V.check_o3_in_digest(str(p))
    assert r["ok"] is False
    assert r["value"]["has_section"] is True and r["value"]["eligible"] is None


# ---------------------------------------------------------------- 4 perf_delta
def test_perf_delta_no_logs(tmp_path):
    r = V.check_perf_delta([str(tmp_path / "none.log")])
    assert r["ok"] is False and r["value"]["hits"] == 0


def test_perf_delta_pytest_only_is_not_pass(tmp_path):
    """★pytest.log 里的命中是单测产物，拿它当 PASS 就是假绿。"""
    p = tmp_path / "pytest.log"
    p.write_text("[PerfDeltaProbe] DEBUG: [412刀3] perf_delta before=0.6ms after=1.0ms delta=0.4ms\n",
                 encoding="utf-8")
    r = V.check_perf_delta([str(p)])
    assert r["ok"] is False
    assert r["value"]["hits"] == 1 and r["value"]["prod_hits"] == 0


def test_perf_delta_prod_hit_is_pass(tmp_path):
    p = tmp_path / "pulse.log"
    p.write_text("x\n[PerfDeltaProbe] DEBUG: [412刀3] perf_delta before=1ms after=2ms delta=1ms\n",
                 encoding="utf-8")
    r = V.check_perf_delta([str(p)])
    assert r["ok"] is True and r["value"]["prod_hits"] == 1


def test_perf_delta_effect_verify_aux(tmp_path):
    ev = tmp_path / "effect_verify.json"
    ev.write_text(json.dumps({"verdicts": {
        "a": {"dims": {"perf_ok": True}}, "b": {"dims": {"perf_ok": None}}, "c": {}}}), encoding="utf-8")
    r = V.check_perf_delta([], str(ev))
    assert r["value"]["verdicts_with_perf"] == 1
    assert r["ok"] is False, "辅助证据不改判 PASS"


# ---------------------------------------------------------------- 5 叙事回放
def test_replay_empty():
    r = V.check_narrative_replay(lambda: {"state": "empty", "replayed_rows": 0})
    assert r["ok"] is False and r["value"]["state"] == "empty"


def test_replay_ok():
    r = V.check_narrative_replay(lambda: {"state": "ok", "replayed_rows": 7,
                                          "days_with_file": 3, "continuous_days": 2})
    assert r["ok"] is True and r["value"]["replayed_rows"] == 7


def test_replay_loader_raises():
    def _boom():
        raise RuntimeError("池坏了")
    r = V.check_narrative_replay(_boom)
    assert r["ok"] is False and r["value"]["error"] == "RuntimeError"


# ---------------------------------------------------------------- 编排 / CLI
def test_run_all_zero_of_five(tmp_path):
    """空根目录 ⇒ 1/2/3/4/5 全 FAIL，且不抛异常。"""
    out = V.run_all(str(tmp_path), DAY, loader=lambda: {"state": "empty", "replayed_rows": 0},
                    log_paths=[])
    assert len(out) == 5
    assert all(x["ok"] is False for x in out)


def test_run_all_five_of_five(tmp_path):
    day, root = DAY, tmp_path
    _write_jsonl(str(root / "data" / "narrative" / ("narrative_%s.jsonl" % day)), [_row()])
    (root / "data" / "experience").mkdir(parents=True, exist_ok=True)
    (root / "data" / "experience" / "experience_pool.json").write_text(
        json.dumps({"experiences": [{"is_template": True}]}), encoding="utf-8")
    (root / "data" / "reports").mkdir(parents=True, exist_ok=True)
    (root / "data" / "reports" / ("digest_%s.md" % day)).write_text(
        "## 八、O3 删死代码 dry-run\n- 若开闸将放行：**440** 条\n", encoding="utf-8")
    log = root / "logs" / "pulse.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("[412刀3] perf_delta before=1ms after=2ms delta=1ms\n", encoding="utf-8")
    out = V.run_all(str(root), day, loader=lambda: {"state": "ok", "replayed_rows": 3},
                    log_paths=[str(log)])
    assert all(x["ok"] is True for x in out), [x for x in out if not x["ok"]]


def test_main_exit_1_and_json(tmp_path, capsys):
    rc = V.main(["--root", str(tmp_path), "--day", DAY, "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["day"] == DAY and payload["passed"] == 0
    assert len(payload["results"]) == 5


def test_main_human_output(tmp_path, capsys):
    rc = V.main(["--root", str(tmp_path), "--day", DAY])
    out = capsys.readouterr().out
    assert rc == 1
    assert "0/5 PASS" in out and "[FAIL] 1 叙事落盘" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
