# -*- coding: utf-8 -*-
"""★383 刀3 · 写面观察埋点（P3）

票面：生产期写面探测无埋点。要求每拍记录写最频繁文件 TOP20、不拦截、可开关。

设计取舍（写进模块 docstring）：**不 wrap `builtins.open`**（热路径 + 与 safe-delete 交互），
改用 **mtime 差分采样**：每 N 秒扫一次目录，变了的计 1 拍。零侵入、可关。
能力边界：拍数 ≠ 写次数（同一窗口改 10 次也只记 1 拍）。
"""
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.metrics import write_face as wf  # noqa: E402


def setup_function(_fn):
    wf.reset()
    config.ENABLE_WRITE_FACE_OBSERVE = True
    config.WRITE_FACE_OBSERVE_EVERY_SEC = 0.0


def _mk(root, name, body="x"):
    _p = os.path.join(str(root), name)
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    with io.open(_p, "w", encoding="utf-8") as _f:
        _f.write(body)
    return _p


def test_m383_d3_first_scan_counts_all_then_zero(tmp_path):
    """首拍把已有文件记为"变化"（没有基线），第二拍未改 ⇒ 0。"""
    _mk(tmp_path, "a.txt")
    _mk(tmp_path, "b/c.txt")
    _o1 = wf.observe_once(str(tmp_path), force=True)
    assert _o1["on"] is True and _o1["changed"] == 2
    _o2 = wf.observe_once(str(tmp_path), force=True)
    assert _o2["changed"] == 0


def test_m383_d3_modified_file_counted_again(tmp_path):
    _p = _mk(tmp_path, "hot.txt", "1")
    wf.observe_once(str(tmp_path), force=True)
    time.sleep(0.01)
    with io.open(_p, "a", encoding="utf-8") as _f:
        _f.write("2")
    _o = wf.observe_once(str(tmp_path), force=True)
    assert _o["changed"] == 1
    _s = wf.stats()
    assert _s["top"][0]["path"] == "hot.txt"
    assert _s["top"][0]["beats"] == 2      # 首拍 1 + 本次 1


def test_m383_d3_stats_top_is_capped(tmp_path):
    for _i in range(25):
        _mk(tmp_path, "f%02d.txt" % _i)
    wf.observe_once(str(tmp_path), force=True)
    _w = wf.stats(top=20)
    assert _w["files"] == 25 and len(_w["top"]) == 20
    assert _w["total_beats"] == 25


def test_m383_d3_switch_off_means_no_reading(tmp_path):
    """★核心：开关关掉 ⇒ 完全不扫、零读数（票面明确要求）。"""
    config.ENABLE_WRITE_FACE_OBSERVE = False
    _mk(tmp_path, "a.txt")
    _o = wf.observe_once(str(tmp_path), force=True)
    assert _o["on"] is False and _o["skipped"] == "switch_off"
    _s = wf.stats()
    assert _s["scans"] == 0 and _s["files"] == 0 and _s["top"] == []


def test_m383_d3_throttle_default(tmp_path):
    config.WRITE_FACE_OBSERVE_EVERY_SEC = 300.0
    _mk(tmp_path, "a.txt")
    assert wf.observe_once(str(tmp_path), now_ts=1000.0)["scanned"] == 1
    _o2 = wf.observe_once(str(tmp_path), now_ts=1001.0)
    assert _o2["skipped"] == "throttled"


def test_m383_d3_never_blocks_writes(tmp_path):
    """反控：采样只读 —— 采完后文件仍可正常写、内容不被改。"""
    _p = _mk(tmp_path, "w.txt", "hello")
    wf.observe_once(str(tmp_path), force=True)
    with io.open(_p, "a", encoding="utf-8") as _f:
        _f.write(" world")
    with io.open(_p, encoding="utf-8") as _f:
        assert _f.read() == "hello world"


def test_m383_d3_hooked_into_collector():
    with io.open(os.path.join(ROOT, "organs", "core",
                              "PulseMetricsCollector.py"), encoding="utf-8") as _f:
        _src = _f.read()
    assert "nucleus.metrics.write_face import observe_once" in _src
    assert '"write_face"' in _src
