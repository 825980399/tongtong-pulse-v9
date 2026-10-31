# -*- coding: utf-8 -*-
"""412 批刀2 单测：经验池治理（experience_governance 契约 412.2）。

钉语义（不钉措辞）：
  * **先查清再动手**：`content_coverage()` 必须能区分
    **design**（无独立 content 字段，summary 由入参落成 ⇒ 不必补）与
    **bug_or_partial**（字段在却大面积为空 ⇒ 要补写入）—— 这两者处置完全相反；
  * 模板治理走**标记 + 降权**，**不删条目**（模板也是"当时确实发生过"的证据）；
  * `annotate()` 默认 dry-run；写后幂等；
  * 降权必须真的改变 `top_hits` 的**排序**，而不是只加两个字段做样子。
"""

from __future__ import annotations

import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.observability import experience_governance as eg  # noqa: E402


class _Pool:
    """经验池替身（`query_experiences(limit)` + `_save()` 契约）。"""

    def __init__(self, rows, raise_on_query=False):
        self.rows = list(rows)
        self.saved = 0
        self.raise_on_query = raise_on_query

    def query_experiences(self, limit=10):
        if self.raise_on_query:
            raise RuntimeError("池子坏了（替身）")
        return list(self.rows)[-int(limit):] if limit else list(self.rows)

    def _save(self):
        self.saved += 1


def _row(i, summary, **kw):
    _d = {"id": "e%d" % i, "summary": summary}
    _d.update(kw)
    return _d


def _src(rel):
    _s = io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read()
    return _s.replace("\r\n", "\n")


# ---------------------------------------------------------------- A text_of / key

def test_01_text_of_uses_real_field_names():
    """★真字段名是 summary / raw_summary / compressed_summary，**不是** content。"""
    assert eg.text_of({"summary": "S"}) == "S"
    assert eg.text_of({"raw_summary": "R", "compressed_summary": "C"}) == "R"
    assert eg.text_of({"compressed_summary": "C"}) == "C"
    assert eg.text_of({"motivation": "M"}) == "M"
    assert eg.text_of({"content": "X"}) == "", "content 不是条目字段，不该被当成文本"
    assert eg.text_of({}) == ""


def test_02_norm_key_strips_whitespace_and_caps():
    _k = eg.norm_key({"summary": " 综 合 \n 优化 "})
    assert " " not in _k and "\n" not in _k
    assert len(eg.norm_key({"summary": "很长" * 200})) == eg.KEY_CHARS


# ---------------------------------------------------------------- B content 真相

def test_11_verdict_design_when_no_content_field_but_summary_present():
    _p = _Pool([_row(i, "摘要%d" % i) for i in range(10)])
    _c = eg.content_coverage(_p)
    assert _c["state"] == "ok"
    assert _c["has_content_field"] == 0
    assert _c["verdict"] == "design"
    assert "不必补" in _c["reason"]


def test_12_verdict_bug_when_field_exists_but_empty():
    _p = _Pool([{"id": "x%d" % i, "content": ""} for i in range(10)])
    _c = eg.content_coverage(_p)
    assert _c["has_content_field"] == 10
    assert _c["content_non_empty"] == 0
    assert _c["verdict"] == "bug_or_partial", "字段在却空 ⇒ 要补写入，不是检索侧的事"


def test_13_verdict_design_when_field_filled():
    _p = _Pool([{"id": "x%d" % i, "content": "内容%d" % i} for i in range(3)])
    assert eg.content_coverage(_p)["verdict"] == "design"


def test_14_verdict_unknown_when_neither():
    _p = _Pool([{"id": "x%d" % i} for i in range(3)])
    assert eg.content_coverage(_p)["verdict"] == "unknown"


def test_15_empty_and_broken_pool_are_distinct():
    assert eg.content_coverage(_Pool([]))["state"] == "empty"
    _b = eg.content_coverage(_Pool([], raise_on_query=True))
    assert _b["state"] == "unavailable"
    assert _b["reason"] == "query_failed"


# ---------------------------------------------------------------- C 模板标记

def _mixed_pool():
    return _Pool([_row(i, "模板A") for i in range(5)]
                 + [_row(100 + i, "各不相同%d" % i) for i in range(3)]
                 + [_row(999, "")])


def test_21_scan_finds_template_keys_and_ratio():
    _t = eg.scan_templates(_mixed_pool(), min_dup=3)
    assert _t["state"] == "ok"
    assert _t["distinct_texts"] == 5           # 模板A / 各不相同×3 / 空
    assert _t["template_keys"][0]["count"] == 5
    assert _t["template_ratio"] == pytest.approx(5 / 9, abs=1e-4)
    assert _t["already_marked"] == 0


def test_22_min_dup_is_respected():
    assert eg.scan_templates(_mixed_pool(), min_dup=5)["template_keys"][0]["count"] == 5
    assert eg.scan_templates(_mixed_pool(), min_dup=6)["template_keys"] == []


def test_23_annotate_dry_run_changes_nothing():
    _p = _mixed_pool()
    _r = eg.annotate(_p, dry_run=True)
    assert _r["written"] is False and _r["reason"] == "dry_run"
    assert _r["template_rows"] == 5
    assert all("is_template" not in x for x in _p.rows)
    assert _p.saved == 0


def test_24_annotate_write_marks_only_templates():
    _p = _mixed_pool()
    _r = eg.annotate(_p, dry_run=False)
    assert _r["written"] is True and _p.saved == 1
    _marked = [x for x in _p.rows if x.get("is_template")]
    assert len(_marked) == 5
    assert all(str(x["id"]).startswith("e1") or x["id"] in {"e0", "e2", "e3", "e4"}
               for x in _marked)
    assert all(not x.get("is_template") for x in _p.rows
               if str(x.get("id") or "").startswith("e10"))
    assert all(x.get("template_key") for x in _marked)


def test_25_annotate_is_idempotent():
    _p = _mixed_pool()
    assert eg.annotate(_p, dry_run=False)["changed"] == 5
    _again = eg.annotate(_p, dry_run=False)
    assert _again["changed"] == 0 and _again["written"] is False
    assert _again["reason"] == "no_change"


def test_26_switch_off_refuses_to_annotate(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M412_EXPERIENCE_GOVERNANCE", False, raising=False)
    _p = _mixed_pool()
    _r = eg.annotate(_p, dry_run=False)
    assert _r["ok"] is False and _r["reason"] == "disabled"
    assert _p.saved == 0


# ---------------------------------------------------------------- D 降权

def test_31_weight_template_lower_than_normal():
    assert eg.retrieval_weight({"is_template": True}) == eg.TEMPLATE_WEIGHT
    assert eg.retrieval_weight({"is_template": False}) == 1.0
    assert eg.retrieval_weight({}) == 1.0
    assert eg.TEMPLATE_WEIGHT < 1.0


def test_32_top_hits_demotes_template_to_the_back():
    """★降权必须改变排序：模板条词片更贴合，但降权后要排到正常条之后。"""
    from nucleus.observability import experience_hits as eh
    _pool = _Pool([{"id": "tpl", "summary": "预测 失败 复盘", "is_template": True,
                    "timestamp": 3000.0},
                   {"id": "norm", "summary": "预测 失败", "timestamp": 2000.0}])
    _hits = eh.top_hits("预测 失败 复盘", top_k=2, pool=_pool)
    assert [h["id"] for h in _hits] == ["norm", "tpl"], "降权不是摆设"


def test_33_hits_carry_weight_fields():
    from nucleus.observability import experience_hits as eh
    _pool = _Pool([{"id": "tpl", "summary": "预测 失败", "is_template": True}])
    _h = eh.top_hits("预测 失败", top_k=1, pool=_pool)[0]
    assert _h["is_template"] is True
    assert _h["weight"] == eg.TEMPLATE_WEIGHT
    assert _h["score_weighted"] == pytest.approx(_h["score"] * eg.TEMPLATE_WEIGHT, abs=1e-6)


def test_34_no_template_marks_means_unchanged_ordering():
    """没打标记的池 ⇒ 行为与 411 完全一致（零回归）。"""
    from nucleus.observability import experience_hits as eh
    # （两条文本必须不同 —— 411 的按文本去重会把完全相同的两条并成一条）
    _pool = _Pool([{"id": "a", "summary": "预测 失败 甲", "timestamp": 100.0},
                   {"id": "b", "summary": "预测 失败 乙", "timestamp": 900.0}])
    _hits = eh.top_hits("预测 失败", top_k=2, pool=_pool)
    assert [h["id"] for h in _hits] == ["b", "a"]      # 同分按时间新→旧
    assert all(h["weight"] == 1.0 for h in _hits)


# ---------------------------------------------------------------- E 静态接线

def test_41_experience_hits_calls_retrieval_weight():
    _s = _src("nucleus/observability/experience_hits.py")
    assert "retrieval_weight" in _s
    assert "experience_governance" in _s


def test_42_version_marker():
    assert eg.version().startswith("412.")
