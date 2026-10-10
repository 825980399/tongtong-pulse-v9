# -*- coding: utf-8 -*-
"""第162批刀16 门控单测：generate_night_digest（自报 digest 去重+分级摘要）。

覆盖 nucleus.evolution.night_orchestration.generate_night_digest：
  - 读取 data/reports/alerts.jsonl 中 needs_human=True 条目；
  - 去重（report_id+anomaly_type+target）、P0/P1 分级计数、Top5、建议动作、复跑命令；
  - 只读约束：不修改 alerts.jsonl；
  - 关闭开关（NIGHT_ORCH_DIGEST_ENABLED=False）→ 返回 None 且不产文件。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nucleus.evolution.night_orchestration as orch_mod


def _make_root(records):
    """构造临时项目根，写入 data/reports/alerts.jsonl（needs_human 混合）。"""
    root = tempfile.mkdtemp(prefix="k16_digest_")
    rep_dir = os.path.join(root, "data", "reports")
    os.makedirs(rep_dir, exist_ok=True)
    path = os.path.join(rep_dir, "alerts.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return root, path


def _sample():
    return [
        {"ts": 1.0, "report_id": "r1", "anomaly_type": "A", "severity": "P0",
         "description": "d1", "target": "t1", "suggested_action": "act1", "needs_human": True},
        {"ts": 2.0, "report_id": "r1", "anomaly_type": "A", "severity": "P0",
         "description": "d1", "target": "t1", "suggested_action": "act1", "needs_human": True},  # 重复
        {"ts": 3.0, "report_id": "r2", "anomaly_type": "B", "severity": "P1",
         "description": "d2", "target": "t2", "suggested_action": "act2", "needs_human": True},
        {"ts": 4.0, "report_id": "r3", "anomaly_type": "C", "severity": "P1",
         "description": "d3", "target": "t3", "suggested_action": "act1", "needs_human": True},  # 建议动作重复
        {"ts": 5.0, "report_id": "r4", "anomaly_type": "D", "severity": "P2",
         "description": "d4", "target": "t4", "suggested_action": "act3", "needs_human": True},  # 计入去重但不计 P0/P1
    ]


def test_digest_basic_counts_and_dedup():
    root, apath = _make_root(_sample())
    try:
        out = orch_mod.generate_night_digest(root)
        assert out and os.path.isfile(out), "digest 未产出"
        # 去重后 needs_human 真条目：r1(A), r2(B), r3(C), r4(P2) = 4 条
        txt = open(out, encoding="utf-8").read()
        assert "需人工介入条目（去重后）：**4**" in txt, txt
        assert "P0：**1**" in txt  # 仅 r1
        assert "P1：**2**" in txt  # r2, r3
        # Top5 / 建议动作 / 复跑命令
        assert "Top5" in txt
        assert "建议动作" in txt
        assert "python tools/night_orch_shutdown.py --digest" in txt
        # 只读约束：alerts.jsonl 未被改写（行数不变）
        assert len(open(apath, encoding="utf-8").readlines()) == 5
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_digest_top5_order_and_actions():
    root, _ = _make_root(_sample())
    try:
        out = orch_mod.generate_night_digest(root)
        txt = open(out, encoding="utf-8").read()
        # 建议动作去重后应为 act1/act2/act3（act1 来自 r1 与 r3 只计一次）
        assert txt.count("- act1") == 1
        assert "- act2" in txt and "- act3" in txt
        # Top5 按时间倒序，最新 r3(C) 排前
        _idx_c = txt.index("C")
        _idx_a = txt.index("A")
        assert _idx_c < _idx_a, "Top5 未按时序倒序"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_digest_disabled_returns_none():
    root, _ = _make_root(_sample())
    try:
        out = orch_mod.generate_night_digest(root, enabled=False)
        assert out is None, "关闭开关应返回 None"
        # 不应生成 digest 文件
        _files = os.listdir(os.path.join(root, "data", "reports"))
        assert not any(f.startswith("digest_") for f in _files)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_digest_empty_alerts():
    root = tempfile.mkdtemp(prefix="k16_empty_")
    rep_dir = os.path.join(root, "data", "reports")
    os.makedirs(rep_dir, exist_ok=True)
    open(os.path.join(rep_dir, "alerts.jsonl"), "w", encoding="utf-8").close()
    try:
        out = orch_mod.generate_night_digest(root)
        assert out and os.path.isfile(out)
        txt = open(out, encoding="utf-8").read()
        assert "需人工介入条目（去重后）：**0**" in txt
    finally:
        shutil.rmtree(root, ignore_errors=True)
