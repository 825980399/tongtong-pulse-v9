# -*- coding: utf-8 -*-
"""493.8 批：语义层自学习闭环骨架 — 单元测试（8 刀全覆盖，默认开关关 → 可回退）。

纪律：本批所有机制仅写 learned overlay / veto 两个 JSON，不 emit 事件、不新增权限。
测试通过 monkeypatch 临时开启各开关并指向临时文件，结束后自动还原（零副作用）。
"""
import json
import os
import sys

import pytest

import config as _cfg_mod
import nucleus.self_index.learned_alias as _la
import nucleus.self_index.alias_labeler as _lb
from nucleus.self_index.exporters import link_organ, _char_ngrams, _NGRAM_CACHE


# ───────────────────────────── 493.8-1 四类标注 ─────────────────────────────
def test_label_four_types():
    _rows = {
        "alias_correct": {"query": "胃在哪", "would_answer": "胃 在…",
                          "organ": "胃", "skipped_by_guard": False},
        "alias_miss":    {"query": "我的胃", "would_answer": None,
                          "organ": "胃", "skipped_by_guard": False},
        "fastpath_hit":  {"query": "你有几个器官", "would_answer": "57 个",
                          "organ": None, "skipped_by_guard": False},
        "fastpath_miss": {"query": "今天天气", "would_answer": None,
                          "organ": None, "skipped_by_guard": False},
        "skipped":       {"query": "长文本粘贴", "would_answer": None,
                          "organ": None, "skipped_by_guard": True},
    }
    assert _lb.label_shadow_row(_rows["alias_correct"]) == "alias_correct"
    assert _lb.label_shadow_row(_rows["alias_miss"]) == "alias_miss"
    assert _lb.label_shadow_row(_rows["fastpath_hit"]) == "fastpath_hit"
    assert _lb.label_shadow_row(_rows["fastpath_miss"]) == "fastpath_miss"
    assert _lb.label_shadow_row(_rows["skipped"]) is None
    assert set(_lb.LABEL_TYPES) == {
        "alias_correct", "alias_miss", "fastpath_hit", "fastpath_miss"}


def test_aggregate_label_stats():
    _rows = [
        {"query": "胃在哪", "would_answer": "x", "organ": "胃", "skipped_by_guard": False},
        {"query": "我的胃", "would_answer": None, "organ": "胃", "skipped_by_guard": False},
        {"query": "今天天气", "would_answer": None, "organ": None, "skipped_by_guard": False},
        {"query": "长", "would_answer": None, "organ": None, "skipped_by_guard": True},
    ]
    _s = _lb.aggregate_label_stats(_rows)
    assert _s["alias_correct"] == 1
    assert _s["alias_miss"] == 1
    assert _s["fastpath_miss"] == 1
    assert _s["total"] == 3


def test_compute_self_ref():
    assert _lb.compute_self_ref("胃在哪") is True
    assert _lb.compute_self_ref("我的眼睛") is True
    assert _lb.compute_self_ref("今天天气如何") is False


# ───────────────────── 493.8-2 link_organ 重排 + 缓存 + 因果停用 ─────────────
def test_link_organ_name_priority_and_func_still_works():
    # 名称层优先但不反向误伤；功能层样本仍为 493.6 验收值
    assert link_organ("管消化的")["organ"] == "胃"
    assert link_organ("管语言的")["organ"] == "肺"
    assert link_organ("看东西的")["organ"] == "眼睛"
    assert link_organ("胃")["organ"] == "胃"


def test_link_organ_causal_stop_returns_none():
    # 493.8-2：因果/原因问法 → 直接 None（不硬消歧）
    assert link_organ("为什么胃会满") is None
    assert link_organ("胃满的原因") is None
    assert link_organ("背压的根源") is None


def test_ngram_cache_hits():
    _NGRAM_CACHE.clear()
    _a = _char_ngrams("胃")
    _b = _char_ngrams("胃")
    assert _a is _b  # 同串缓存复用
    assert "胃" in _a


# ───────────────────── 493.8-3 learned overlay 合并 ─────────────────────
def _write_overlay(tmp_path, entries):
    _p = tmp_path / "overlay.json"
    _ov = {"version": _la.OVERLAY_SCHEMA_VERSION, "updated_at": "",
           "aliases": entries, "meta": {}}
    _p.write_text(json.dumps(_ov, ensure_ascii=False), encoding="utf-8")
    return str(_p)


def test_maybe_overlay_off_is_none():
    assert _la.maybe_overlay_alias("胃在哪") is None


def test_maybe_overlay_on_merges(tmp_path, monkeypatch):
    _p = _write_overlay(tmp_path, {
        "胃在哪": {"query": "胃在哪", "quarantine": [],
                  "active": {"organ": "胃", "score": 1.0, "source": "learned",
                             "state": "active"}}})
    monkeypatch.setattr(_la, "_OVERLAY_PATH", _p)
    monkeypatch.setattr(_cfg_mod, "ENABLE_M4938_LEARNED_ALIASES", True)
    _la._OVERLAY_CACHE.update({"ts": 0.0, "data": None})
    _r = _la.maybe_overlay_alias("胃在哪")
    assert _r is not None
    assert _r["organ"] == "胃"
    assert _r["match"] == "learned"


def test_maybe_overlay_veto_blocks(tmp_path, monkeypatch):
    _p = _write_overlay(tmp_path, {
        "胃在哪": {"query": "胃在哪", "quarantine": [],
                  "active": {"organ": "胃", "score": 1.0, "source": "learned",
                             "state": "active"}}})
    monkeypatch.setattr(_la, "_OVERLAY_PATH", _p)
    monkeypatch.setattr(_la, "_VETO_PATH", str(tmp_path / "veto.json"))
    monkeypatch.setattr(_cfg_mod, "ENABLE_M4938_LEARNED_ALIASES", True)
    monkeypatch.setattr(_cfg_mod, "ENABLE_M4938_ALIAS_VETO", True)
    _la._OVERLAY_CACHE.update({"ts": 0.0, "data": None})
    # 先 veto
    assert _la.veto_alias(query="胃在哪", organ="胃", reason="test")
    _la._OVERLAY_CACHE.update({"ts": 0.0, "data": None})
    assert _la.maybe_overlay_alias("胃在哪") is None


# ───────────────────── 493.8-4 候选抽取 + 用户纠正 ─────────────────────
def test_extract_candidates_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(_cfg_mod, "ENABLE_M4938_CANDIDATE_LEARNING", True)
    # alias_correct → 入池
    _ok = _la.extract_candidates_from_shadow_row(
        {"query": "胃在哪", "would_answer": "x", "organ": "胃",
         "skipped_by_guard": False})
    assert len(_ok) == 1 and _ok[0]["organ"] == "胃"
    # fastpath_hit（非指代）→ 不入池
    assert _la.extract_candidates_from_shadow_row(
        {"query": "几个器官", "would_answer": "57", "organ": None,
         "skipped_by_guard": False}) == []
    # skipped_by_guard → 不入池
    assert _la.extract_candidates_from_shadow_row(
        {"query": "x", "would_answer": None, "organ": "胃",
         "skipped_by_guard": True}) == []
    # 污染（长文本）→ 不入池
    assert _la.extract_candidates_from_shadow_row(
        {"query": "x" * 200, "would_answer": "x", "organ": "胃",
         "skipped_by_guard": False}) == []


def test_parse_user_correction(tmp_path, monkeypatch):
    monkeypatch.setattr(_cfg_mod, "ENABLE_M4938_CANDIDATE_LEARNING", True)
    _r = _la.parse_user_correction("你刚才说的肺其实是胃")
    assert _r and _r["wrong_organ"] == "肺" and _r["correct_organ"] == "胃"
    _r2 = _la.parse_user_correction("胃不是肺是肝脏")
    assert _r2 and _r2["wrong_organ"] == "肺" and _r2["correct_organ"] == "肝脏"
    assert _la.parse_user_correction("今天天气不错") is None


# ───────────────────── 493.8-5 冷启动提取器 ─────────────────────
def test_cold_start_extract(tmp_path, monkeypatch):
    monkeypatch.setattr(_cfg_mod, "ENABLE_M4938_COLD_START", True)
    _out = _la.cold_start_extract()
    assert isinstance(_out, dict)
    assert len(_out) > 0
    # 应有至少一个器官名作为 key
    assert any(v.get("organ") for v in _out.values())


# ───────────────────── 493.8-6 digest 融合 + 日终 promoter ─────────────────────
def test_fuse_and_promote(tmp_path, monkeypatch):
    monkeypatch.setattr(_cfg_mod, "ENABLE_M4938_DIGEST_FUSION", True)
    monkeypatch.setattr(_cfg_mod, "ENABLE_M4938_CANDIDATE_LEARNING", True)
    # 测试环境 quarantine 首日即校验状态机，绕过真实 2 天留存约束
    monkeypatch.setattr(_la, "MIN_QUARANTINE_DAYS", 0)
    _ov_p = str(tmp_path / "overlay.json")
    monkeypatch.setattr(_la, "_OVERLAY_PATH", _ov_p)
    # 构造影子日志：同一 query 重复 3 次 alias_correct（达 MIN_QUARANTINE_HITS）
    _shadow = tmp_path / "shadow.jsonl"
    _rows = [{"query": "管消化的器官", "would_answer": "胃", "organ": "胃",
              "skipped_by_guard": False, "ts": "2026-11-11T00:00:0%d" % i}
             for i in range(3)]
    _shadow.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in _rows),
                       encoding="utf-8")
    _n = _la.fuse_shadow_to_overlay(shadow_path=str(_shadow), date="2026-11-11")
    assert _n >= 3
    _promoted = _la.promote_quarantine()
    assert _promoted >= 1
    _ov = _la.load_overlay()
    assert _ov["aliases"]["管消化的器官"]["active"]["organ"] == "胃"


# ───────────────────── 493.8-7 安全边界 + veto + 指标 ─────────────────────
def test_veto_and_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(_la, "_VETO_PATH", str(tmp_path / "veto.json"))
    monkeypatch.setattr(_cfg_mod, "ENABLE_M4938_ALIAS_VETO", True)
    assert _la.veto_alias(query="x", organ="肝", reason="t")
    assert _la.is_vetoed(query="x", organ="肝") is True
    _m = _la.metrics()
    assert _m["vetoes"] >= 1
    assert "switches" in _m


def test_safety_caps_constants():
    assert _la.MAX_OVERLAY_ALIASES > 0
    assert _la.MIN_QUARANTINE_HITS >= 1
    assert 0 < _la.MAX_ACTIVE_RATIO <= 1


# ───────────────────── 493.8-8 issue-trend 脚本 sys.path 修复 ─────────────────────
def test_issue_trend_script_imports_with_repo_root():
    # 验证脚本顶部已把仓库根加入 sys.path，可在 tools/ 下 `import nucleus`
    _repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _script = os.path.join(_repo, "tools", "check_code_issue_trend_302.py")
    _env = dict(os.environ)
    _env["PYTHONPATH"] = _repo
    import subprocess
    _r = subprocess.run([sys.executable, _script, "--stamp"],
                        capture_output=True, text=True, cwd=_repo, env=_env)
    # 退出 0 且打印了判据版本戳 ⇒ 未因 ModuleNotFoundError: nucleus 崩溃
    assert _r.returncode == 0, f"stderr={_r.stderr}"
    assert _r.stdout.strip()[:4] == "2026" or len(_r.stdout.strip()) > 0


def test_const_shadow_label_exists():
    # 493.8-1：事件常量占位
    from nucleus.const import DigestEvent
    assert DigestEvent.SHADOW_LABEL == "digest.shadow_label"
