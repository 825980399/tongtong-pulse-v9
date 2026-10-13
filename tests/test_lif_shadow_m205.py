# -*- coding: utf-8 -*-
"""刀2 验收：LIF 影子观测数学与落盘（隔离单测 · 铁律151）。

不依赖生产会话 / BasePulseOrgan 实例，仅测 nucleus.lif_shadow 的纯逻辑。
"""
import json
import math
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.lif_shadow import LIFShadow, lif_voltage_step  # noqa: E402


def test_voltage_step_first_pulse():
    # 首次（v=None）从 0 起累加 w_in
    assert lif_voltage_step(None, 0.0, 60.0, 1.0) == 1.0


def test_voltage_step_decay_and_inject():
    tau = 60.0
    # 已存在 v=1.0，dt=tau → 1.0*exp(-1)+1.0
    expected = 1.0 * math.exp(-1.0) + 1.0
    got = lif_voltage_step(1.0, tau, tau, 1.0)
    assert abs(got - expected) < 1e-9


def test_voltage_step_no_decay_when_dt_zero_or_negative():
    # 同拍（dt<=0）不衰减
    assert lif_voltage_step(2.0, 0.0, 60.0, 1.0) == 3.0
    assert lif_voltage_step(2.0, -5.0, 60.0, 1.0) == 3.0


def test_voltage_step_tau_zero_no_divide():
    # tau<=0 退化为不衰减（防御除零）
    assert lif_voltage_step(2.0, 999.0, 0.0, 1.0) == 3.0


def test_observe_sequence_matches_formula():
    sh = LIFShadow(tau=60.0, w_in=1.0, flush_every=0)  # 关闭自动落盘
    v1 = sh.observe(100.0, "o", "e1")   # dt=0 → 1.0
    v2 = sh.observe(160.0, "o", "e2")   # dt=60 → 1.0*exp(-1)+1
    v3 = sh.observe(160.0, "o", "e3")   # dt=0 → +1
    assert abs(v1 - 1.0) < 1e-9
    assert abs(v2 - (1.0 * math.exp(-1.0) + 1.0)) < 1e-9
    assert abs(v3 - (v2 + 1.0)) < 1e-9
    assert len(sh._samples) == 3
    # dt 字段正确记录
    assert sh._samples[0]["dt"] == 0.0
    assert abs(sh._samples[1]["dt"] - 60.0) < 1e-9
    assert sh._samples[2]["dt"] == 0.0


def test_observe_deque_capped():
    sh = LIFShadow(tau=60.0, w_in=1.0, maxlen=3, flush_every=0)
    for k in range(10):
        sh.observe(float(k) * 10.0, "o", "e")
    assert len(sh._samples) == 3  # maxlen 生效
    assert sh._count == 10         # 计数继续累加


def test_flush_writes_jsonl_with_fields(tmp_path):
    out = str(tmp_path)
    sh = LIFShadow(tau=60.0, w_in=1.0, flush_every=2, out_dir=out)
    sh.observe(100.0, "自我认知", "self_awareness.tick.30")
    sh.observe(160.0, "自我认知", "self_awareness.tick.60")  # 触发第 2 次自动落盘
    sh.observe(220.0, "自我认知", "self_awareness.tick.150")
    sh.flush()  # 落第 3 条
    fname = os.path.join(out, "lif_shadow_%s.jsonl" % datetime.now().strftime("%Y%m%d"))
    assert os.path.exists(fname)
    with open(fname, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    assert len(lines) == 3
    rec = json.loads(lines[0])
    for key in ("ts", "iso", "organ", "event_type", "dt", "v", "theta", "crossed"):
        assert key in rec
    assert rec["organ"] == "自我认知"
    assert rec["theta"] == 5.0

