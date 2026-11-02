# -*- coding: utf-8 -*-
"""424 刀2 门控单测：知识库 L1 计数多口径对账。

三条核心断言：
  ① **口径差异是「谓词」造成的，不是 bug** —— ephemeral 计入与否，差值必须精确等于 ephemeral 数；
  ② 真不一致（文件自洽性 / 应写数 vs 实写数 / 冷热重叠）必须 status=mismatch **且打 WARNING**；
  ③ 开关关闭时完全不做事（status=disabled，零 WARNING）。

★每个用例都用 tmp_path 当 root（任务书：实例化后跑生产方法，第一行必须改路径）。
"""
from __future__ import annotations

import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nucleus.mnemosyne import knowledge_count_reconcile as kc


# ---------------------------------------------------------------- 替身
class _Node:
    def __init__(self, nid, level="L1", ephemeral=False):
        self.node_id = nid
        self.evol_level = level
        self.ephemeral = ephemeral


class _Pool:
    """复刻真池语义：get_stats 恒统计 hot+warm+cold（**含** ephemeral）。"""

    def __init__(self, nodes):
        self._nodes = list(nodes)

    def get_stats(self):
        dist = {}
        for n in self._nodes:
            dist[n.evol_level] = dist.get(n.evol_level, 0) + 1
        return {"evol_distribution": dist, "total_nodes": len(self._nodes)}

    def get_all_including_evicted(self):
        return list(self._nodes)


def _write_l1_file(root, nodes, declared=None):
    d = os.path.join(root, "data", "knowledge")
    os.makedirs(d, exist_ok=True)
    doc = {
        "version": "v9.5",
        "created_at": "2026-11-02T13:00:00",
        "l1_count": len(nodes) if declared is None else declared,
        "nodes": [{"node_id": n} for n in nodes],
    }
    p = os.path.join(d, "pulse_l1_snapshot.json")
    with open(p, "w", encoding="utf-8") as _f:
        json.dump(doc, _f, ensure_ascii=False)
    return p


# ---------------------------------------------------------------- 1 口径差 = ephemeral
def test_01_predicate_gap_equals_ephemeral(tmp_path):
    """`get_stats()` 含 ephemeral、落盘谓词排除 ⇒ 两口径差值必须**精确等于** ephemeral 数。

    ★这就是生产 578 vs 562 的机制（差 16 条瞬时节点），不是丢数据。
    """
    nodes = ([_Node(f"n{i}") for i in range(20)]
             + [_Node(f"e{i}", ephemeral=True) for i in range(7)]
             + [_Node("l2a", level="L2")])
    pool = _Pool(nodes)
    got = kc.count_pool_l1(pool)

    assert got["pool_l1_all"] == 27, "含 ephemeral 的 L1 应为 20+7"
    assert got["pool_l1_persistable"] == 20, "可落盘的 L1 只算非 ephemeral"
    assert got["ephemeral_l1"] == 7
    assert got["pool_l1_all"] - got["pool_l1_persistable"] == got["ephemeral_l1"]


# ---------------------------------------------------------------- 2 文件自洽 + WARNING
def test_02_file_self_inconsistent_raises_warning(tmp_path, caplog):
    """`l1_count` 字段与 `len(nodes)` 不符 ⇒ status=mismatch + WARNING。"""
    _write_l1_file(str(tmp_path), ["a", "b", "c"], declared=99)
    with caplog.at_level(logging.WARNING, logger="pulse"):
        res = kc.reconcile(str(tmp_path))
    assert res["status"] == "mismatch"
    codes = [d["code"] for d in res["diffs"]]
    assert "l1_file_self_inconsistent" in codes
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and "424刀2" in r.getMessage()]
    assert warns, "不一致必须打 WARNING，不许静默"
    assert "l1_file_self_inconsistent" in warns[0].getMessage()


# ---------------------------------------------------------------- 3 应写数 vs 实写数
def test_03_wrote_count_not_persisted(tmp_path):
    """本应写 10 条、文件里只有 3 条 ⇒ wrote_not_persisted（写漏检测）。"""
    _write_l1_file(str(tmp_path), ["a", "b", "c"])
    res = kc.reconcile(str(tmp_path), wrote_count=10)
    assert res["status"] == "mismatch"
    assert [d["code"] for d in res["diffs"]] == ["wrote_not_persisted"]

    ok = kc.reconcile(str(tmp_path), wrote_count=3)
    assert ok["status"] == "ok", "应写数 == 实写数时必须判 ok"


# ---------------------------------------------------------------- 4 冷热重叠
def test_04_cold_hot_overlap_detected(tmp_path):
    """同一 node_id 既在热 L1 又在冷索引 ⇒ cold_hot_overlap（真重复存储）。"""
    _write_l1_file(str(tmp_path), ["dup", "hot_only"])
    d = tmp_path / "data" / "knowledge"
    (d / "cold.index.json").write_text(
        json.dumps({"dup": {"level": "L1"}, "cold_only": {"level": "L1"}}), encoding="utf-8")
    res = kc.reconcile(str(tmp_path), full=True)
    assert res["status"] == "mismatch"
    codes = [x["code"] for x in res["diffs"]]
    assert "cold_hot_overlap" in codes
    assert res["counts"]["cold_hot_overlap"] == 1


def test_05_cold_index_parquet_mismatch(tmp_path):
    """索引条数与 Parquet 条数不等 ⇒ cold_index_parquet_mismatch（S4 关系式在模块内也守）。"""
    _write_l1_file(str(tmp_path), ["a"])
    d = tmp_path / "data" / "knowledge"
    (d / "cold.index.json").write_text(
        json.dumps({"x": {"level": "L1"}, "y": {"level": "L1"}}), encoding="utf-8")

    orig = kc.count_cold

    def _fake(root, with_parquet=True):
        out = orig(root, with_parquet=False)
        out["parquet"] = 1          # 伪造：Parquet 只剩 1 条
        out["parquet_levels"] = {"L1": 1}
        out["parquet_ids"] = {"x"}
        return out

    kc.count_cold = _fake
    try:
        res = kc.reconcile(str(tmp_path), full=True)
    finally:
        kc.count_cold = orig
    assert [x["code"] for x in res["diffs"]] == ["cold_index_parquet_mismatch"]


# ---------------------------------------------------------------- 5 开关
def test_06_switch_off_does_nothing(tmp_path, caplog, monkeypatch):
    """开关关 ⇒ status=disabled、零 WARNING、不读文件。"""
    _write_l1_file(str(tmp_path), ["a"], declared=42)   # 故意造不一致
    monkeypatch.setattr(kc, "enabled", lambda: False)
    with caplog.at_level(logging.WARNING, logger="pulse"):
        res = kc.reconcile(str(tmp_path))
    assert res["status"] == "disabled"
    assert res["diffs"] == []
    assert not [r for r in caplog.records if "424刀2" in r.getMessage()]


# ---------------------------------------------------------------- 6 接线
def test_07_save_l1_hook_wired():
    """`PulseSnapshot._save_l1_locked` 必须真的在保存成功后调对账（不是只写了模块）。"""
    p = os.path.join(ROOT, "nucleus", "mnemosyne", "PulseSnapshot.py")
    with open(p, encoding="utf-8-sig", errors="replace") as _f:
        src = _f.read()
    i = src.index("def _save_l1_locked")
    j = src.index("def load_l1", i)
    body = src[i:j]
    assert "knowledge_count_reconcile" in body, "落盘钩子没接上"
    assert "at_write=True" in body, "钩子必须走便宜模式（不读 parquet）"
    # 钩子必须在 return True 之前，且异常不能影响保存结果
    assert body.index("reconcile") < body.rindex("return True")
    assert "silent_exc" in body, "对账自身异常必须静默留痕，不能吃掉保存结果"


def test_08_never_raises_on_garbage_root():
    """root 指向不存在的目录也必须优雅返回，不许抛。"""
    res = kc.reconcile("/nonexistent/root/xyz_424")
    assert res["status"] in ("ok", "no_data")
    assert res["diffs"] == []
