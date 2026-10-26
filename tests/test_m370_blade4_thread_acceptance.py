# -*- coding: utf-8 -*-
"""370 刀4 定向单测：线程下降验收**夹具**＋对照基线归档（NOT_DUE 挂起）。

★本刀零生产代码改动；验收判据显式挂起，不宣称线程已下降。
★夹具：`tests/thread_snapshot_probe.py`（测试/辅助面，不绑生产、不连生产心跳）。
"""
import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EVID_DIR = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "370")
EVID_JSON = os.path.join(EVID_DIR, "线程验收对照基线_20261026.json")
EVID_MD = os.path.join(EVID_DIR, "线程验收对照基线_20261026.md")
REPORT_342 = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告",
                          "路灯_342批交付报告_20261022.md")

# 342 报告与 E-7 票面数字（只读常量，用于交叉核对证据件）
_EXPECT_THREADS = [184, 187, 182]
_EXPECT_PID = 43864


# ------------------------------------------------------------------ 用例
def test_01_probe_collects_at_least_three_ticks(tmp_path):
    """① 夹具可采 ≥3 拍并落表：字段齐备、间隔/拍数可控、JSON 可回读。"""
    from tests.thread_snapshot_probe import SAMPLE_FIELDS, collect

    out = tmp_path / "probe.json"
    rep = collect(pid=None, ticks=3, interval_s=0.01, out_json=str(out),
                  note="单测自采（当前进程）")

    assert rep["ticks"] == 3 and len(rep["rows"]) == 3, "拍数不足"
    assert rep["interval_s"] == 0.01, "快照间隔未记录"
    for r in rep["rows"]:
        for k in SAMPLE_FIELDS:
            assert k in r, "落表缺字段 %s" % k
        assert isinstance(r["threads"], int) and r["threads"] > 0
        assert isinstance(r["rss_mb"], float) and r["rss_mb"] > 0
        assert re.match(r"^\d{2}:\d{2}:\d{2}$", r["sampled_at"]), "时刻格式不对"
    assert [r["tick"] for r in rep["rows"]] == [1, 2, 3]
    assert rep["thread_values"] == [r["threads"] for r in rep["rows"]]

    back = json.loads(out.read_text(encoding="utf-8"))
    assert back["thread_values"] == rep["thread_values"], "落盘与返回值不一致"

    # ★E-7 口径守卫：拍数 <3 必须显式报错，不得悄悄产出「看起来够了」的报告
    with pytest.raises(ValueError):
        collect(ticks=2, interval_s=0.01)
    with pytest.raises(ValueError):
        collect(ticks=3, interval_s=0)


def test_02_archive_matches_342_report_and_ticket():
    """② 基线归档件读数与 342 报告 §八/E-7 票面、以及原始采集件三方一致。"""
    assert os.path.isfile(EVID_JSON), "归档 JSON 未落盘"
    with open(EVID_JSON, encoding="utf-8") as f:
        d = json.load(f)

    assert d["thread_values"] == _EXPECT_THREADS, \
        "三拍读数与基线不符：%r" % d["thread_values"]
    assert d["pid"] == _EXPECT_PID
    assert d["verdict"] == "NOT_DUE", "判据挂起状态须显式标注"
    assert len(d["rows"]) == 3
    for r, n in zip(d["rows"], _EXPECT_THREADS):
        assert r["threads"] == n

    # 342 报告原文交叉核对（报告可得 ⇒ 必须核对，不得只信票面）
    txt = open(REPORT_342, encoding="utf-8").read()
    assert "184/187/182" in txt, "342 报告§八未见基线三拍数字"
    assert "43864" in txt, "342 报告未见 pid43864"
    assert "NOT_DUE" in txt, "342 报告未见 NOT_DUE 判据"

    # 证据件 MD 须含同一组数字（人读面与机读面一致）
    md = open(EVID_MD, encoding="utf-8").read()
    for n in _EXPECT_THREADS:
        assert str(n) in md, "证据件 MD 缺读数 %s" % n
    assert "43864" in md and "NOT_DUE" in md

    # 原始采集件若仍在（tmp/ 下，gitignore），则逐字段复核归档无失真
    raw = os.path.join(ROOT, "tmp", "p342_root", "d5_threads_live.json")
    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as f:
            r0 = json.load(f)
        assert [x["threads"] for x in r0["rows"]] == d["thread_values"]
        assert r0["proc_gen"] == d["proc_gen"], "proc_gen 归档失真"
        assert [x["rss_mb"] for x in r0["rows"]] == [x["rss_mb"] for x in d["rows"]]
        assert [x["sampled_at"] for x in r0["rows"]] == [x["sampled_at"] for x in d["rows"]]


def test_03_zero_production_diff():
    """③ ★红线：本刀零生产代码改动（git 实测，只看 HEAD 与工作区的生产面差异）。"""
    prod_roots = ("nucleus", "organs", "base", "main.py", "config.py", "functions")
    out = subprocess.run(["git", "status", "--short", "--"] + list(prod_roots),
                         cwd=ROOT, capture_output=True, text=True)
    changed = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
    # 在工件（历史探针）不算本刀改动，但**生产面任何 M/D 都不允许**
    mine = [c for c in changed
            if "_probe_py_m162" not in c and "_probe_docs_m162" not in c]
    assert not mine, "本刀改动了生产面：%s" % mine

    # 夹具不得进入生产 import 链
    for rel in ("nucleus", "organs", "base"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, rel)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".py"):
                    src = open(os.path.join(dirpath, fn), "rb").read().decode(
                        "utf-8", "replace")
                    assert "thread_snapshot_probe" not in src, \
                        "夹具被生产代码引用：%s" % fn


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
