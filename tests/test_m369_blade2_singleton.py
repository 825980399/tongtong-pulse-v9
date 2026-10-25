# -*- coding: utf-8 -*-
"""369 刀2（cross_module_singleton_call 21 条核真伪）定向单测。

覆盖任务书刀2 要求的 3 例：
  ① 真伪清单 21 条条目齐备、判定字段互斥完备；
  ② 豁免元数据五字段（锚点/理由/责任人/批次）体例一致；
  ③ 真问题条目独立列清单、未混入豁免表。
外加 1 例：清单与实时检测器对照可复现（仍在报的条目不得被判为真问题却放着不管）。

★本刀**不改检测器判据**，只验证清单本身的自洽与可复现性。
清单机读副本：docs\\路灯与星轨对话\\交付报告\\证据件\\369\\刀2_singleton21条真伪与豁免清单_20261026.json
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DOC_REL = os.path.join("docs", "路灯与星轨对话", "交付报告", "证据件", "369",
                       "刀2_singleton21条真伪与豁免清单_20261026.json")
DOC_PATH = os.path.join(_ROOT, DOC_REL)

VERDICTS = {"LEGIT_INFRA", "LEGIT_CONTRACT", "LEGIT_SELF_ATTR", "REAL_RISK"}
LEGIT = {"LEGIT_INFRA", "LEGIT_CONTRACT", "LEGIT_SELF_ATTR"}


@pytest.fixture(scope="module")
def doc():
    if not os.path.exists(DOC_PATH):
        pytest.skip("刀2 清单副本不存在：%s" % DOC_REL)
    with open(DOC_PATH, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ ①

def test_verdict_list_complete_and_exclusive(doc):
    """① 21 条齐备、无重复锚点、verdict 枚举合法、exemptions + real_risks == rows。"""
    rows = doc["rows"]
    assert len(rows) == 21, "清单条目数应为 21，实际 %d" % len(rows)

    keys = [(r["file"], r["method"]) for r in rows]
    assert len(set(keys)) == len(keys), "存在重复 (file, method) 锚点"

    for r in rows:
        assert r["verdict"] in VERDICTS, "非法判定值：%s" % r["verdict"]

    ex_ids = {r["id"] for r in doc["exemptions"]}
    rk_ids = {r["id"] for r in doc["real_risks"]}
    assert ex_ids | rk_ids == {r["id"] for r in rows}, "豁免表 ∪ 真问题表 != 全集"
    assert not (ex_ids & rk_ids), "豁免表与真问题表存在交集（同一条目两头记）"

    # 判定字段互斥完备：LEGIT_* 全部进豁免，REAL_RISK 全部进真问题
    for r in rows:
        if r["verdict"] in LEGIT:
            assert r["id"] in ex_ids, "%s 判为合法却不在豁免表" % r["id"]
        else:
            assert r["id"] in rk_ids, "%s 判为真问题却不在真问题表" % r["id"]


# ------------------------------------------------------------------ ②

def test_exemption_metadata_complete(doc):
    """② 每条豁免的元数据五字段齐备且非空，责任人/批次统一。"""
    assert doc["exemptions"], "豁免表不应为空"
    for r in doc["exemptions"]:
        for field in ("file", "method", "reason", "owner", "batch"):
            assert r.get(field), "豁免条目 %s 缺字段 %s" % (r["id"], field)
        assert len(r["reason"]) >= 20, "豁免条目 %s 理由过短，不足以复核" % r["id"]
        assert r["batch"] == "369"
        assert "369" in r["owner"] or "路灯" in r["owner"]
    owners = {r["owner"] for r in doc["exemptions"]}
    batches = {r["batch"] for r in doc["exemptions"]}
    assert len(owners) == 1 and len(batches) == 1, "责任人/批次口径不统一：%s / %s" % (owners, batches)


# ------------------------------------------------------------------ ③

def test_real_risks_separated(doc):
    """③ 真问题独立列清单，未混入豁免表；豁免表里不得出现 REAL_RISK。"""
    assert "real_risks" in doc, "缺独立真问题清单字段"
    for r in doc["exemptions"]:
        assert r["verdict"] != "REAL_RISK", "豁免表混入真问题条目 %s" % r["id"]

    # 本批核出 0 条真问题：断言是"核出为空"而不是"字段缺失"
    assert doc["real_risks"] == [], "本批核出真问题 0 条；若有则须另立票并同步本断言"

    # 交叉校验：任一条目只能属于一边
    for r in doc["rows"]:
        in_ex = any(x["id"] == r["id"] for x in doc["exemptions"])
        in_rk = any(x["id"] == r["id"] for x in doc["real_risks"])
        assert in_ex != in_rk, "条目 %s 归属不唯一" % r["id"]


# ------------------------------------------------------------------ 附：与实时检测器对照

def test_still_reported_items_all_judged_legit(doc):
    """附：清单标记「实时仍报」的条目，必须全部判为 LEGIT_*（不得有真问题被放着不管）。

    ★本例同时守护「判据未被放宽」：若某天新增了仍在报且被判 REAL_RISK 的条目，
      说明本刀把真问题留在了表里却没另立票 ⇒ 直接失败。
    """
    still = [r for r in doc["rows"] if r.get("still_reported")]
    assert still, "清单里应有『实时仍报』条目（否则对照失去意义）"
    for r in still:
        assert r["verdict"] in LEGIT, \
            "条目 %s 实时仍在报却被判 %s ⇒ 真问题须另立票，不能只记不办" % (r["id"], r["verdict"])
    # 仍在报的 12 条应全部是基础设施误报
    assert {r["verdict"] for r in still} == {"LEGIT_INFRA"}, \
        "仍在报的条目判定分布异常：%s" % {r["verdict"] for r in still}


def test_detector_scan_reproduces_same_still_reported_set(doc):
    """附：实时扫描复核——仍在报的 (file, method) 与清单标记一致（清单可复现，不是拍脑袋）。"""
    from nucleus.self_inspector import SelfInspector

    si = SelfInspector()
    hits = [i for i in si.detect_code_issues(min_severity="medium")
            if i.get("type") == "cross_module_singleton_call"]
    det = set()
    for h in hits:
        fp = str(h.get("file", "")).replace("\\", "/")
        if fp.startswith("D:/"):
            fp = fp[len(_ROOT.replace("\\", "/")) + 1:]
        det.add((fp, h.get("method")))

    marked = {(r["file"], r["method"]) for r in doc["rows"] if r.get("still_reported")}
    assert marked, "清单未标记任何『实时仍报』条目"
    assert marked <= det, \
        "清单标记『仍报』但检测器已不报（清单滞后）：%s" % sorted(marked - det)
    # 反向：检测器在报的，清单里必须有条目（否则说明清单漏登记）
    assert det <= {(r["file"], r["method"]) for r in doc["rows"]}, \
        "检测器在报但清单未登记：%s" % sorted(det - {(r["file"], r["method"]) for r in doc["rows"]})
