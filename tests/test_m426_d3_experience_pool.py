# -*- coding: utf-8 -*-
"""426 刀3：经验池近似去重（0.98）+ 总量硬上限淘汰（800）。

basetemp：`--basetemp=tmp/m426_bt_d3`（本批每文件独立 basetemp，铁律 58）。
所有用例都在 tmp 隔离根里造池，绝不碰生产 data/experience（铁律 38）。
"""

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.mnemosyne.experience_pool import ExperiencePool

_LONG = ("综合优化：3条样本指向同类改进点，代表信号「我们现在的 L2 语义缓存命中率是多少？"
         "污染率呢？按场景分类的分布是什么样的？」，建议继续累积观测，暂不做策略调整。")
# 改的是第 20 个字符：必须落在前 8 字**之外**，否则归一化分桶键不同、
# 两条根本进不了同一个桶（分桶是性能优化，但也是判定的前置条件）。
_LONG2 = _LONG[:20] + "甲" + _LONG[21:]   # 只改 1 个字符 ⇒ difflib ≈ 0.99
_LONG3 = _LONG[:20] + "乙" + _LONG[21:]   # 与 _LONG/_LONG2 各差 1 个字符
_FAR = "今天第一次把呼吸节律和心跳对齐，感觉整条链路都顺了一点，记下来备查。"


def _pool(tmp_path, rows=None):
    _d = tmp_path / "exp"
    _d.mkdir(parents=True, exist_ok=True)
    if rows is not None:
        with open(str(_d / "experience_pool.json"), "w",
                  encoding="utf-8", newline="\n") as _f:
            _f.write(json.dumps({"version": "v1.0", "experiences": rows},
                                ensure_ascii=False))
    return ExperiencePool(base_dir=str(_d))


def test_10_near_duplicate_write_is_merged(tmp_path):
    """写入侧：只差 1 个字符的两条 ⇒ 合并成 1 条，activation 累加，不新增条目。"""
    _p = _pool(tmp_path)
    assert _p.record_experience(content=_LONG, emotion_intensity=0.5) != ""
    assert len(_p._experiences) == 1
    _p.record_experience(content=_LONG2, emotion_intensity=0.5)
    assert len(_p._experiences) == 1, f"近似重复没有被合并：{len(_p._experiences)} 条"
    _e = _p._experiences[0]
    assert int(_e.get("activation_count", 0) or 0) >= 1
    assert _e.get("merged_count") == 1
    assert _e.get("merged_similarity", 0) >= 0.98

    # 反例：内容明显不同的必须写得进去（防"全都合掉"）
    _p.record_experience(content=_FAR, emotion_intensity=0.5)
    assert len(_p._experiences) == 2


def test_20_semantically_opposite_short_pair_not_merged():
    """0.9524 的 `学习到1条/0条` 不能被并（1 和 0 语义相反）—— 这是选 0.98 的理由。"""
    import difflib

    _r = difflib.SequenceMatcher(None, "学习到1条新经验", "学习到0条新经验").ratio()
    assert _r < 0.98, _r


def test_30_total_cap_evicts_oldest_keeps_newest_and_permanent(tmp_path, monkeypatch):
    """总量硬上限：超限真的删；删的是最旧的；最新的与永久保留的不动。

    本用例只测淘汰，先把「启动期近似合并」关掉 —— 否则 30 条同构文本会被合掉，
    量不到淘汰行为。
    """
    monkeypatch.setattr(config, "ENABLE_M426_EXPERIENCE_COMPACT", False, raising=False)
    _rows = []
    for _i in range(1, 31):
        _rows.append({
            "id": f"e{_i:02d}",
            "summary": f"经验条目 {_i}：{_FAR}",
            "timestamp": float(_i),
            "decay_rate": 0.05,
            "activation_count": _i,          # 越新 activation 越高（真实形态）
            "is_summarized": False,
        })
    # 最旧那条设为「高强度情绪 ⇒ 永久保留」
    _rows[0]["decay_rate"] = 0.0
    _p = _pool(tmp_path, _rows)
    assert len(_p._experiences) == 30

    _p._max_total_experiences = 10
    _evicted = _p._evict_overflow()
    assert _evicted > 0, "超上限了却一条都没删"
    assert len(_p._experiences) <= 10, f"淘汰后仍超上限：{len(_p._experiences)}"

    _ids = {e["id"] for e in _p._experiences}
    assert "e01" in _ids, "decay_rate=0 的永久条目被删了"
    assert {"e28", "e29", "e30"} <= _ids, "最新 10% 被删了（违反「不要删新的」）"
    assert "e02" not in _ids, "最旧的普通条目没被删"


def test_40_compaction_on_load_is_idempotent_and_switchable(tmp_path):
    """启动期存量合并：跑一次 3→1；再启动一次仍是 1 且不再合并；开关关掉 3 条原样。"""
    _rows = [
        {"id": "a1", "summary": _LONG, "timestamp": 1.0, "decay_rate": 0.05,
         "activation_count": 0, "is_summarized": False},
        {"id": "a2", "summary": _LONG2, "timestamp": 2.0, "decay_rate": 0.05,
         "activation_count": 0, "is_summarized": False},
        # 与 a1/a2 各差 1 个字符 ⇒ 两两都在 0.98 以上（否则是"链式"而非"重复"）
        {"id": "a3", "summary": _LONG3, "timestamp": 3.0,
         "decay_rate": 0.05, "activation_count": 0, "is_summarized": False},
    ]
    _p = _pool(tmp_path, _rows)
    assert len(_p._experiences) == 1, f"启动期没做近似合并：{len(_p._experiences)}"
    assert _p.compact_similar()["merged"] == 0, "第二次调用还能合 ⇒ 不是幂等"

    # 开关关掉：逐字回到改前（3 条原样）
    _d2 = tmp_path / "exp_off"
    _d2.mkdir(parents=True, exist_ok=True)
    with open(str(_d2 / "experience_pool.json"), "w", encoding="utf-8", newline="\n") as _f:
        _f.write(json.dumps({"version": "v1.0", "experiences": _rows}, ensure_ascii=False))
    _old = getattr(config, "ENABLE_M426_EXPERIENCE_COMPACT", True)
    config.ENABLE_M426_EXPERIENCE_COMPACT = False
    try:
        _p2 = ExperiencePool(base_dir=str(_d2))
        assert len(_p2._experiences) == 3
    finally:
        config.ENABLE_M426_EXPERIENCE_COMPACT = _old


def test_50_config_keys_present():
    """配置面：上限/阈值/两个开关都在 config 里（可运维调参，不必改代码）。"""
    assert config.EXPERIENCE_POOL_CONFIG.get("max_total_experiences") == 800
    assert config.EXPERIENCE_POOL_CONFIG.get("dedup_similarity") == pytest.approx(0.98)
    assert config.ENABLE_M426_EXPERIENCE_COMPACT is True
    assert config.ENABLE_M426_EXPERIENCE_EVICT is True
