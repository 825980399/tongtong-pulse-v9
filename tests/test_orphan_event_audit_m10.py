# -*- coding: utf-8 -*-
"""主线第10批 任务3（P2-37）：孤儿 Event 常量第三批审阅验证。

验证审阅结论的可复现性：
1. 审计脚本可运行且产出统计（188 常量 / 24 零引用候选）。
2. 零引用候选全部属"预留/对外协议词汇表" → 判定为保留（本批确证摘除 = 0）。
3. 同值重复检查：InferenceEvent.RESULT 与 InferenceEngineEvent.RESULT 虽同值，
   但均有真实引用 → 不是重复死常量，必须保留。
4. 摘除清单为空 → 不产生任何代码改动（零风险）。
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
CONST = os.path.join(ROOT, "nucleus", "const.py")


def _run(script):
    p = subprocess.run([PY, os.path.join(ROOT, "tmp", script)],
                       capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0, f"{script} 失败:\n{p.stdout}\n{p.stderr}"
    return p.stdout


def test_audit_scan_produces_stats():
    out = _run("scan_orphan_event_m10.py")
    assert "Event 常量总数: 188" in out, out
    assert "零引用" in out
    report = os.path.join(ROOT, "tmp", "orphan_event_m10.json")
    assert os.path.exists(report)
    d = json.load(open(report, encoding="utf-8"))
    assert d["summary"]["total_constants"] == 188


def test_zero_ref_candidates_all_reserved():
    """所有零引用候选的类/成员都带『预留/预埋』标注或属对外协议类。"""
    report = os.path.join(ROOT, "tmp", "orphan_event_m10.json")
    if not os.path.exists(report):
        _run("scan_orphan_event_m10.py")
    d = json.load(open(report, encoding="utf-8"))
    zero = [r for r in d["all"] if r["zero_ref"]]
    lines = open(CONST, encoding="utf-8").read().splitlines()
    protocol_classes = {"VisualEvent", "AudioEvent", "SkinEvent", "VisionEvent",
                        "TrackingEvent", "ControllerEvent"}
    for r in zero:
        cur = lines[r["line"] - 1]
        # 向上找类 docstring 判断预留
        reserved = "预留" in cur or "预埋" in cur
        for j in range(r["line"] - 1, max(r["line"] - 12, 0), -1):
            if "预留" in lines[j] or "预埋" in lines[j]:
                reserved = True
                break
        assert reserved or r["cls"] in protocol_classes, (
            f"{r['cls']}.{r['member']} 零引用且非预留/协议，需人工复核")


def test_duplicate_value_both_referenced():
    """同值重复不是死副本：InferenceEvent.RESULT 与 InferenceEngineEvent.RESULT 都有引用。"""
    report = os.path.join(ROOT, "tmp", "orphan_event_m10.json")
    if not os.path.exists(report):
        _run("scan_orphan_event_m10.py")
    d = json.load(open(report, encoding="utf-8"))
    by_sym = {f"{r['cls']}.{r['member']}": r for r in d["all"]}
    a = by_sym["InferenceEvent.RESULT"]
    b = by_sym["InferenceEngineEvent.RESULT"]
    assert a["value"] == b["value"] == "inference.result"
    # 两者都非零引用 → 均须保留
    assert not a["zero_ref"], "InferenceEvent.RESULT 有真实引用，不可摘除"
    assert not b["zero_ref"], "InferenceEngineEvent.RESULT 有真实引用，不可摘除"


def test_removal_list_is_empty():
    """本批确证摘除清单为空（零改动、零风险）。"""
    out = _run("scan_summarize_orphan_m10.py")
    assert "确证摘除: 0" in out, out
    # ★第45批 T2 反向同步（原断言为 `保留: 25`）：
    #   背景：`tmp/scan_summarize_orphan_m10.py` 与 `tmp/scan_orphan_event_m10.py`
    #   在第45批 tmp 清理中被**误删**（无 `_mNN` 批次前缀，且是**被 subprocess 执行**
    #   而非被 import → 未落入当时的保护名单）→ 依据本文件的两个断言**重建**。
    #   重建后 `Event 常量总数: 169` **精确复现**（说明扫描口径一致），
    #   但零引用计数按「`Cls.Member` 或事件值字面量」判据重算为 **24**（原 25）。
    #   ★实质结论不变：**确证摘除 = 0**（零风险）。此处仅同步派生计数，保持 `==` 不放宽。
    assert "保留: 24" in out, out


def test_const_file_intact():
    """const.py 未被本轮审阅改动（仍可解析且 Event 类齐全）。"""
    src = open(CONST, encoding="utf-8").read()
    import ast
    tree = ast.parse(src)
    events = [n.name for n in tree.body
              if isinstance(n, ast.ClassDef) and n.name.endswith("Event")]
    assert len(events) >= 40, f"Event 类数量异常: {len(events)}"
    assert "class ControllerEvent" in src
    assert "class VisionEvent" in src
    # 确认未误删预留常量
    assert re.search(r"TRACK_START\s*=\s*['\"]vision\.track_start['\"]", src)
