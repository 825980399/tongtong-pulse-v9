# -*- coding: utf-8 -*-
"""369 刀6（A5 57 条 cont/break 人工三档分档）定向单测。

覆盖任务书刀6 要求的 4 例：
  ① 57 条全集与基线对照一致、三档标签互斥完备；
  ② 每条元数据四字段（理由 / 责任人 / 批次 / 锚点）齐备；
  ③ 启发式 tier 与人工档的差异在清单中**显式标注**；
  ④ 判据 / 断言 / 指纹零改动校验（不切换 ci_common 三档判据）。
外加 2 例：已知锚点复核（call_recorder._rotate 已在册不重复进表、main._probe_loop 判 T2）。

★本刀只出静态分析清单，**不切三档判据、不改断言、不销 363 已核查指纹**。
"""
import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DOC_REL = os.path.join("docs", "路灯与星轨对话", "交付报告", "证据件", "369",
                       "刀6_A5contbreak三档分档_20261026.json")
DOC_PATH = os.path.join(_ROOT, DOC_REL)
LEDGER = os.path.join(_ROOT, "tools", "ci", "baselines",
                      "m365_silent_except_ledger.json")
A5_PROD = os.path.join(_ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件",
                       "364_A5_新旧判据对比_prod.json")

TIERS = {"T1", "T2", "T3"}


@pytest.fixture(scope="module")
def doc():
    if not os.path.exists(DOC_PATH):
        pytest.skip("刀6 分档副本不存在：%s" % DOC_REL)
    with open(DOC_PATH, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ ①

def test_fullset_matches_baseline_and_tiers_exclusive(doc):
    """① 全集 57 条 == 基线 68 − 已在册 11；三档标签互斥完备。"""
    with open(LEDGER, encoding="utf-8") as f:
        ledger = json.load(f)["rows"]
    exempt = [r for r in ledger if r.get("manual_review") or r.get("exemption")]
    rest = [r for r in ledger if not (r.get("manual_review") or r.get("exemption"))]
    assert len(exempt) == 11 and len(rest) == 57

    rows = doc["rows"]
    assert len(rows) == 57, "分档全集应为 57，实际 %d" % len(rows)

    keys = [(r["path"], r["qualname"], r["nth"], r["tname"]) for r in rows]
    assert len(set(keys)) == len(keys), "分档条目有重复锚点"
    base_keys = {(r["path"], r["qualname"], r["nth"], r["tname"]) for r in rest}
    assert set(keys) == base_keys, "分档全集与基线「非在册」集合不一致"

    for r in rows:
        assert r["tier"] in TIERS, "非法档位：%s" % r["tier"]
    # 三档互斥完备（单一 tier 字段保证），各档之和 == 全集
    assert sum(doc["counts"].values()) == 57
    assert set(doc["counts"]) <= TIERS


def test_a5_prod_is_same_universe():
    """① 附：A5 prod.json 的 added 68 条与 m365 台账 68 条为同一集合（口径同源）。"""
    if not os.path.exists(A5_PROD):
        pytest.skip("无 A5 对比产物")
    with open(A5_PROD, encoding="utf-8") as f:
        prod = json.load(f)
    with open(LEDGER, encoding="utf-8") as f:
        ledger = json.load(f)["rows"]
    a5 = {(r["path"], r["qualname"], int(r["nth"] or 0), r["tname"], r["kind"])
          for r in prod["added"]}
    lg = {(r["path"], r["qualname"], int(r["nth"] or 0), r["tname"], r["kind"])
          for r in ledger}
    assert len(a5) == 68 and a5 == lg, \
        "A5 added 与台账不是同一集合（差 %d 条）" % len(a5 ^ lg)


# ------------------------------------------------------------------ ②

def test_metadata_four_fields_complete(doc):
    """② 每条必须带理由 / 责任人 / 批次 / 锚点四字段，理由须可复核（≥20 字）。"""
    for r in doc["rows"]:
        for f in ("reason", "owner", "batch", "anchor"):
            assert r.get(f), "条目 %s 缺字段 %s" % (r["id"], f)
        assert len(r["reason"]) >= 20, \
            "条目 %s 理由过短（%d 字），不足以复核" % (r["id"], len(r["reason"]))
        assert ":" in r["anchor"], "条目 %s 锚点须为 <path>:<lineno>" % r["id"]
        assert r["batch"] == "369"
        assert "369" in r["owner"] or "路灯" in r["owner"]
    owners = {r["owner"] for r in doc["rows"]}
    batches = {r["batch"] for r in doc["rows"]}
    assert len(owners) == 1 and len(batches) == 1, "责任人/批次口径不统一"


# ------------------------------------------------------------------ ③

def test_heuristic_diff_is_explicitly_marked(doc):
    """③ 启发式 tier 与人工档的差异必须逐条显式标注（不是口头说明）。"""
    for r in doc["rows"]:
        assert "heuristic" in r, "条目 %s 缺启发式原值" % r["id"]
        assert "heuristic_differs" in r, "条目 %s 缺差异标记" % r["id"]
    # 启发式 57 条全判 C；人工判 T2 的即为漏判 ⇒ 必须被标为 differs
    assert all(r["heuristic"] == "C" for r in doc["rows"]), \
        "启发式原值不再全为 C ⇒ 分档前提变了，须重核"
    t2 = [r for r in doc["rows"] if r["tier"] == "T2"]
    assert t2, "应存在 T2（必改）条目"
    for r in t2:
        assert r["heuristic_differs"] is True, \
            "T2 条目 %s 启发式判 C ⇒ 必须标为有差异（启发式漏判点）" % r["id"]
    assert doc["heuristic_diff_count"] == sum(
        1 for r in doc["rows"] if r["heuristic_differs"])


# ------------------------------------------------------------------ ④

def test_judge_assertion_fingerprint_untouched():
    """④ 判据 / 断言 / 指纹零改动：ci_common.py、台账基线、两个指纹文件均不得改。"""
    env = dict(os.environ)
    for _k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(_k, None)
    targets = ["tools/ci/ci_common.py",
               "tools/ci/silent_except_fingerprints.json",
               "tools/ci/silent_except_legacy_whitelist.json",
               "tools/ci/baselines/m365_silent_except_ledger.json",
               "tools/scan_silent_except_dual.py"]
    r = subprocess.run(["git", "status", "--porcelain", "--"] + targets,
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    assert not (r.stdout or "").strip(), \
        "判据/断言/指纹文件出现改动（本刀不切判据、不销账）：%s" % (r.stdout or "").strip()


# ------------------------------------------------------------------ 已知锚点复核

def test_known_anchor_rotate_not_duplicated(doc):
    """附：call_recorder.py:681 _rotate 已在册 manual_review ⇒ 不得重复进 57 条分档表。"""
    keys = {(r["path"], r["qualname"]) for r in doc["rows"]}
    assert ("nucleus/llm/call_recorder.py", "_rotate") not in keys, \
        "_rotate 已在册 manual_review（A 档已认定），不得重复进分档表、不重复立票"
    with open(LEDGER, encoding="utf-8") as f:
        ledger = json.load(f)["rows"]
    rot = [r for r in ledger if r["qualname"] == "_rotate"
           and r["path"].endswith("call_recorder.py")]
    assert rot and rot[0].get("manual_review"), "_rotate 应仍在 manual_review 在册状态"


def test_known_anchor_probe_loop_is_t2(doc):
    """附：main.py:3521 _probe_loop（唯一生产面 break 主流程点）必须判 T2 并单独可查。"""
    hit = [r for r in doc["rows"] if r["path"] == "main.py"
           and r["qualname"] == "_probe_loop"]
    assert len(hit) == 1, "分档表中 _probe_loop 应恰好 1 条"
    assert hit[0]["tier"] == "T2", \
        "_probe_loop 是主流程 break（异常即终止整个探查循环），应判 T2"
    assert "主流程" in hit[0]["reason"] or "break" in hit[0]["reason"], \
        "T2 理由须点明「主流程退出」而非泛泛写「跳过坏数据」"
    assert str(hit[0]["head_lineno"]) in hit[0]["anchor"]
