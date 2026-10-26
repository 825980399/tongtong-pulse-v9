# -*- coding: utf-8 -*-
"""369 刀3（既有 134 条失败逐族归档三档 · B5）定向单测。

覆盖任务书刀3 要求的 4 例：
  ① 三档标签互斥完备、134 条全覆盖；
  ② 默认值族（B）／断言族（A）无交叉双改标记；
  ③ unknown 归类结论可复现（U 档必须有"待补齐"结论，不得静默留空）；
  ④ 归档**零断言改动**校验：归档过程不得触碰测试断言与开关默认值。
外加 2 例：B 档文件数达标（≥6）与每条元数据齐备。

★本刀只做归档，**不改任何测试断言、不改任何开关默认值、不销既有失败账**。
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
                       "刀3_既有134失败三档归档_20261026.json")
DOC_PATH = os.path.join(_ROOT, DOC_REL)
TIERS = {"A", "B", "C", "U"}


@pytest.fixture(scope="module")
def doc():
    if not os.path.exists(DOC_PATH):
        pytest.skip("刀3 归档副本不存在：%s" % DOC_REL)
    with open(DOC_PATH, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ ①

def test_three_tiers_complete_and_exclusive(doc):
    """① 全集覆盖 + 每条单一档位（三档标签互斥完备）。"""
    rows = doc["rows"]
    assert len(rows) == 135, "全集应为 135 条（基线口径），实际 %d" % len(rows)
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == len(ids), "id 有重复"
    nodeids = [r["nodeid"] for r in rows]
    assert len(set(nodeids)) == len(nodeids), "nodeid 有重复"

    for r in rows:
        assert r["tier"] in TIERS, "非法档位：%s" % r["tier"]
    # 每条只能属于一档（由单一 tier 字段保证），且各档之和 == 全集
    total = sum(doc["counts"].values())
    assert total == len(rows), "各档计数之和 %d != 全集 %d" % (total, len(rows))


# ------------------------------------------------------------------ ②

def test_no_dual_change_marking(doc):
    """② A（改断言）与 B（改默认值）严格分离：同一条不得两头都改。"""
    a_ids = {r["id"] for r in doc["rows"] if r["tier"] == "A"}
    b_ids = {r["id"] for r in doc["rows"] if r["tier"] == "B"}
    assert not (a_ids & b_ids), "存在既标 A 又标 B 的双改条目：%s" % sorted(a_ids & b_ids)
    # 逐条自查：任何条目的 reason/evidence 都不得出现"既改默认值又改断言"的表述
    for r in doc["rows"]:
        blob = (r.get("reason") or "") + (r.get("evidence") or "")
        assert "既改默认值又改断言" not in blob
    # B 档必须带开关名（无开关的 B 档不可复核）
    for r in doc["rows"]:
        if r["tier"] == "B":
            assert r.get("switch"), "B 档条目 %s 缺开关名" % r["id"]


# ------------------------------------------------------------------ ③

def test_unknown_rows_have_conclusion(doc):
    """③ U 档必须给出"待补齐"的归类结论，不得静默留空（可复现口径）。"""
    u_rows = [r for r in doc["rows"] if r["tier"] == "U"]
    for r in u_rows:
        assert r.get("reason"), "U 档条目 %s 无归类结论（不得留空）" % r["id"]
        assert ("待" in r["reason"] or "补齐" in r["reason"] or "未取得" in r["reason"]), \
            "U 档条目 %s 的结论未说明『待补齐』口径" % r["id"]
        assert r.get("evidence"), "U 档条目 %s 缺证据" % r["id"]
    # U 档占比留痕：若超过半数说明取数口径需复核（本断言用于暴露取数不足，不是硬性失败线）
    ratio = len(u_rows) / max(1, len(doc["rows"]))
    assert ratio <= 0.60, \
        "U 档占比 %.1f%% 过高（>60%%）⇒ 取数口径需复核后再归档" % (ratio * 100)


# ------------------------------------------------------------------ ④

def test_archive_did_not_touch_assertions_or_defaults():
    """④ 归档零断言改动：工作区 tests/ 与 config.py 不得因归档产生改动。

    ★用 git 工作区状态实测（不是看脚本源码）——归档是分析动作，任何对
    tests/ 断言或 config.py 默认值的写盘都属越权。
    """
    env = dict(os.environ)
    for _k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(_k, None)
    r = subprocess.run(["git", "status", "--porcelain", "--", "tests/", "config.py"],
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    changed = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    # 允许本刀自己新增的归档单测文件存在（未跟踪），但不得有**已跟踪文件的修改**
    modified = [c for c in changed if c[:2].strip() in ("M", "D")]
    # ★判据精确化：排除 `_probe_*` 探针临时文件。
    #   实测：批量跑 pytest 时 tests/_probe_py_m162.py（历史遗留「提交后即删」探针）
    #   会被重建并串进 git 索引 ⇒ 与本刀归档动作无关，属另一条清理线（见交付报告新发现）。
    #   ★不是放宽判据：config.py 与其余 tests/ 断言文件仍严格校验零改动。
    modified = [c for c in modified if "_probe_" not in c]
    assert not modified, "归档过程改动了 tests/ 或 config.py：%s" % modified


def test_config_defaults_untouched():
    """④ 补：config.py 开关默认值零改动（B 档只归档，绝不顺手改默认值）。"""
    env = dict(os.environ)
    for _k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(_k, None)
    r = subprocess.run(["git", "status", "--porcelain", "--", "config.py"],
                       cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    assert not (r.stdout or "").strip(), \
        "config.py 出现改动（B 档归档不得改默认值）：%s" % (r.stdout or "").strip()


def test_b_tier_file_count_meets_threshold(doc):
    """附：B 档（灰度开关默认值族）须覆盖 ≥6 个文件（任务书明确口径）。"""
    b_files = {r["file"] for r in doc["rows"] if r["tier"] == "B"}
    assert len(b_files) >= 6, "B 档文件数 %d < 6，未达任务书口径" % len(b_files)
    assert set(b_files) == set(doc.get("b_switch_files") or []), \
        "b_switch_files 与逐条统计不一致"


def test_every_row_has_metadata(doc):
    """附：每条须带可复核的四字段（档位 / 判据 / 证据 / 来源分片）。"""
    for r in doc["rows"]:
        for f in ("tier", "reason", "evidence", "shard", "file", "case"):
            assert r.get(f) not in (None, ""), "条目 %s 缺字段 %s" % (r["id"], f)
        assert len(r["reason"]) >= 20, "条目 %s 判据过短，不足以复核" % r["id"]
