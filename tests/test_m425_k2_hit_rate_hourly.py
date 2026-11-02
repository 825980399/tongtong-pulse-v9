# -*- coding: utf-8 -*-
"""425 刀2 门控单测：hit_rate 小时报（新增，日报零改动）。

三条核心断言：
  ① **本小时就是本小时** —— 切片只取 `ts == 本小时起点` 的那一段，上一小时的数据不得混入；
  ② **不碰日报** —— 小时报文件名前缀不同，写了小时报之后日报内容/路径不变；
  ③ **与日报同源** —— 小时报的 `rolling_24h` 必须逐字段等于 `report()["total"]`，
     这样「小时报和日报对得上吗」是可用断言验的，不是靠人眼看。

★每个用例都用 tmp_path 重定向数据根（任务书：实例化后跑生产方法，第一行必须改路径）。
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nucleus.world_model import hit_rate as hr


@pytest.fixture()
def iso(tmp_path):
    """把数据根重定向到 tmp_path，用例结束复原（set_data_dir 是模块级全局）。"""
    hr.set_data_dir(str(tmp_path))
    yield tmp_path
    hr.set_data_dir(None)


def _state(now, extra=None):
    h = hr._hour_of(now)
    st = {
        "version": hr._M410_HITRATE_VERSION,
        "windows": 42,
        "hours": {
            "heart.alive": [[h, 8, 2, 1]],
            "eye.frame": [[h, 3, 5, 0]],
            "old.type": [[h - 3600, 100, 0, 0]],
        },
        "streak": {}, "alerts": [], "last": {},
    }
    if extra:
        st["hours"].update(extra)
    return st


def test_01_hour_slice_only_current_hour(iso):
    now = time.time()
    st = _state(now)
    rep = hr.hourly_report(now, state=st)
    t = rep["total"]
    assert t["hit"] == 11 and t["miss"] == 7 and t["covered"] == 18
    assert t["hit_rate"] == round(11 / 18, 4)
    assert t["no_coverage"] == 1
    assert "old.type" not in rep["types"], "上一小时的类型混进了本小时报"


def test_02_top_is_max_hit_then_higher_rate(iso):
    now = time.time()
    st = _state(now)
    rep = hr.hourly_report(now, state=st)
    assert rep["top"]["event_type"] == "heart.alive"
    assert rep["top"]["hit"] == 8
    assert rep["top"]["hit_rate"] == 0.8

    # 并列命中数时取命中率更高的那个
    h = hr._hour_of(now)
    st2 = {"version": "v", "windows": 1, "hours": {
        "a": [[h, 5, 5, 0]],      # hit 5, rate 0.5
        "b": [[h, 5, 0, 0]],      # hit 5, rate 1.0
    }, "streak": {}, "alerts": [], "last": {}}
    assert hr.hourly_report(now, state=st2)["top"]["event_type"] == "b"


def test_03_write_is_idempotent_within_hour(iso):
    now = time.time()
    st = _state(now)
    hr.save_state(st)
    p1 = hr.write_hourly_report(now, state=st)
    assert p1 and os.path.exists(p1)
    p2 = hr.write_hourly_report(now, state=st)
    assert p2 is None, "同一小时第二次调用必须静默返回 None（幂等靠文件名）"
    # force 才允许重写
    p3 = hr.write_hourly_report(now, state=st, force=True)
    assert p3 == p1


def test_04_does_not_touch_daily_report(iso):
    now = time.time()
    st = _state(now)
    hr.save_state(st)
    daily = hr.report_path(now)
    hr.write_report(now)
    with open(daily, encoding="utf-8") as f:
        before = f.read()

    hp = hr.write_hourly_report(now, state=st)
    assert hp and os.path.basename(hp).startswith(hr.HOURLY_PREFIX)
    assert os.path.basename(daily) not in os.path.basename(hp), "小时报不得与日报同名"
    with open(daily, encoding="utf-8") as f:
        after = f.read()
    assert before == after, "日报内容被小时报改动了"
    got = json.loads(after)
    assert got.get("kind") != "hourly", "日报被小时报覆盖（kind 字段露了）"


def test_05_rolling_matches_daily_report_total(iso):
    now = time.time()
    st = _state(now)
    rep = hr.hourly_report(now, state=st)
    daily = hr.report(now, state=st)
    for k in ("hit", "miss", "covered", "hit_rate"):
        assert rep["rolling_24h"][k] == daily["total"][k], (
            f"小时报 rolling_24h.{k}={rep['rolling_24h'][k]!r} 与日报 "
            f"total.{k}={daily['total'][k]!r} 不一致")
    # 24h 口径应把上一小时的 old.type=100 也算进去
    assert daily["total"]["hit"] == 111


def test_06_switch_off_writes_nothing(iso):
    now = time.time()
    st = _state(now)
    hr.save_state(st)
    daily = hr.report_path(now)
    hr.write_report(now)
    hourly_dir_before = sorted(os.listdir(hr.report_dir()))

    orig = hr._cfg

    def _fake(key, default=None):
        if key == "ENABLE_M425_HOURLY_HIT_RATE":
            return False
        return orig(key, default)

    hr._cfg = _fake
    try:
        assert hr.write_hourly_report(now, state=st, force=True) is None
    finally:
        hr._cfg = orig
    assert sorted(os.listdir(hr.report_dir())) == hourly_dir_before, "开关关闭仍写了文件"
    assert os.path.exists(daily)


def test_07_sample_emits_hourly(iso):
    """走 60s 采样入口：sample() 必须在跨小时时顺手写一份小时报。"""
    now = time.time()
    hr.save_state(_state(now, extra={}))

    class _Miner:
        def get_typed_prediction_stats(self):
            return {"by_event": {"heart.alive": {"hit": 20, "miss": 4, "no_coverage": 2}}}

    out = hr.sample(now, miner=_Miner())
    hp = out.get("hourly_report")
    assert hp, "sample() 没触发小时报"
    assert os.path.exists(hp)
    with open(hp, encoding="utf-8") as f:
        rep = json.load(f)
    assert rep["kind"] == "hourly"
    assert rep["hour"] == hr.hourly_key(now)
    # 第二次采样（同一小时）不得再产新报
    out2 = hr.sample(now, miner=_Miner())
    assert "hourly_report" not in out2
