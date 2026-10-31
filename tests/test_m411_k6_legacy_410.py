# -*- coding: utf-8 -*-
"""411 批刀6 单测：410 遗留四项收口。

覆盖：
  ① 周期档案自动重建（`night_orchestration` 04:00 摘要段，**排在命中率报表之前**）；
  ② 容差表按 σ 重算（目标每类 tol≈2σ，count/burst 不给时刻容差）；
  ③ hit_rate 日报表加 `coverage`（低覆盖下的高命中率不算健康）；
  ④ `_periodicity_cache` 样本 50→100。

钉语义（不钉措辞）：
  * `tol = SIGMA_K × σ`，`sigma_ratio == 2.0`（**不钉具体秒数** —— 它会随档案重算漂移）；
  * `count` / `burst` 型**没有**"下一次何时来"，所以 `tolerance_sec is None`；
  * 覆盖率分母 = `covered + no_coverage`，分母为 0 时给 `None` 而不是 0.0；
  * 档案重建必须在报表**之前**（档案过期 ⇒ 七类退成 no_coverage ⇒ 报表会变假）。
"""

from __future__ import annotations

import ast
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus.world_model import hit_rate as hr  # noqa: E402
from nucleus.world_model import periodic_profile as pp  # noqa: E402

_PERIODIC = [pp.EV_TOUCH_HARDWARE_SNAPSHOT, pp.EV_ENERGY_METABOLISM_SNAPSHOT,
             pp.EV_SPINAL_INSPECTION_REPORT, pp.EV_HEART_ALIVE, pp.EV_HEART_BEAT]


def _stamps(intervals, start=1000.0):
    _out = [start]
    for _d in intervals:
        _out.append(_out[-1] + _d)
    return _out


def _patterns():
    """七类齐全的 patterns（periodic 五类用同一组间隔：median=10 / p90=20）。"""
    _per = {}
    for _et in _PERIODIC:
        _per[_et] = _stamps([10, 10, 10, 10, 20])
    # count 型：间隔全等 ⇒ p90 == median ⇒ σ 估不出来
    _per[pp.EV_EYES_STREAM_FRAME] = _stamps([1, 1, 1, 1, 1])
    # burst 型：长尾（σ 会很大，但不该给时刻容差）
    _per[pp.EV_DIGEST_KNOWLEDGE] = _stamps([1, 1, 3, 5, 290])
    return {"periodicity": _per}


def _src(rel):
    _s = io.open(os.path.join(_ROOT, rel), encoding="utf-8", newline="").read()
    return _s.replace("\r\n", "\n")


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    pp.set_data_dir(str(tmp_path))
    yield
    pp.set_data_dir(None)


# ---------------------------------------------------------------- A σ 估计

def test_01_sigma_from_p90_exact():
    # p90 = median + 1.2816 × σ ⇒ σ = 1.0
    assert pp.sigma_from_p90(13.0, 13.0 + pp.P90_Z) == pytest.approx(1.0, abs=1e-6)


def test_02_sigma_from_p90_degenerate_returns_none():
    assert pp.sigma_from_p90(None, 20.0) is None
    assert pp.sigma_from_p90(10.0, None) is None
    assert pp.sigma_from_p90(10.0, 10.0) is None, "p90==median ⇒ 分布退化，不许硬算 0"
    assert pp.sigma_from_p90(20.0, 10.0) is None, "p90<median ⇒ 不许出负数 σ"


def test_03_sigma_from_p90_bad_input_returns_none():
    assert pp.sigma_from_p90("十", 20.0) is None


def test_04_sigma_k_reads_config(monkeypatch):
    import config as _cfg
    monkeypatch.setattr(_cfg, "M411_SIGMA_K", 3.0, raising=False)
    assert pp.sigma_k() == 3.0
    _e = pp._one_entry(pp.EV_HEART_BEAT, _stamps([10, 10, 10, 10, 20]))
    assert _e["tolerance_basis"] == "sigma_k3"
    assert _e["sigma_ratio"] == 3.0, "容差口径改了，档案里必须看得出来"


def test_05_sigma_k_bad_config_falls_back(monkeypatch):
    import config as _cfg
    monkeypatch.setattr(_cfg, "M411_SIGMA_K", "两倍", raising=False)
    assert pp.sigma_k() == pp.SIGMA_K


# ---------------------------------------------------------------- B 容差口径

def test_11_periodic_tolerance_is_two_sigma():
    _e = pp._one_entry(pp.EV_HEART_BEAT, _stamps([10, 10, 10, 10, 20]))
    assert _e["median_interval_sec"] == 10.0
    assert _e["p90_interval_sec"] == 20.0
    assert _e["sigma_sec"] == pytest.approx(10.0 / pp.P90_Z, abs=1e-4)
    assert _e["tolerance_basis"] == "sigma_k%g" % pp.SIGMA_K
    assert _e["sigma_ratio"] == 2.0
    assert _e["tolerance_sec"] == pytest.approx(
        _e["sigma_sec"] * pp.SIGMA_K, abs=0.01)
    # 百分比口径仍然留档（样本不足时回退用），但不再决定容差
    assert _e["tolerance_pct_sec"] == pytest.approx(0.5, abs=1e-6)


def test_12_count_type_has_no_time_tolerance():
    _e = pp._one_entry(pp.EV_EYES_STREAM_FRAME, _stamps([1, 1, 1, 1, 1]))
    assert _e["tolerance_pct"] is None
    assert _e["tolerance_sec"] is None
    assert _e["tolerance_basis"] == "none"
    assert _e["sigma_ratio"] is None


def test_13_burst_type_sigma_may_be_huge_but_no_tolerance():
    """反例守卫：曾算出 `digest.knowledge tol=186s` —— count/burst 不许给时刻容差。"""
    _e = pp._one_entry(pp.EV_DIGEST_KNOWLEDGE, _stamps([1, 1, 3, 5, 290]))
    assert _e["sigma_sec"] and _e["sigma_sec"] > 50, "长尾事件的 σ 本来就大"
    assert _e["tolerance_sec"] is None
    assert _e["tolerance_basis"] == "none"
    assert _e["sigma_ratio"] is None


def test_14_insufficient_samples_do_not_fabricate_tolerance():
    _e = pp._one_entry(pp.EV_HEART_BEAT, [1000.0, 1000.0])
    assert _e["ok"] is False
    assert _e["tolerance_sec"] is None
    assert "样本不足" in _e["note"]


# ---------------------------------------------------------------- C 档案构建

def test_21_build_profile_covers_all_seven():
    _p = pp.build_profile(_patterns(), now=1_700_000_000.0)
    assert _p["complete"] is True
    assert _p["missing"] == [] and _p["insufficient"] == []
    assert len(_p["events"]) == 7
    assert all(_p["events"][_et]["tolerance_basis"] == "sigma_k%g" % pp.SIGMA_K
               for _et in _PERIODIC)


def test_22_summarize_reports_basis_and_ratio():
    _s = pp.summarize(pp.build_profile(_patterns(), now=1_700_000_000.0))
    assert _s["total"] == 7 and _s["ok"] == 7
    assert _s["by_predict_type"] == {"periodic": 5, "count": 1, "burst": 1}
    assert _s["tolerance_basis"]["sigma_k%g" % pp.SIGMA_K] == 5
    assert _s["tolerance_basis"]["none"] == 2
    assert _s["sigma_ratio_range"].startswith("2.00~2.00"), "五类都该收敛到 2σ"


def test_23_tolerance_for_only_periodic():
    _p = pp.build_profile(_patterns(), now=1_700_000_000.0)
    assert pp.tolerance_for(pp.EV_HEART_BEAT, _p) == pytest.approx(2 * 10.0 / pp.P90_Z,
                                                                   abs=0.01)
    assert pp.tolerance_for(pp.EV_DIGEST_KNOWLEDGE, _p) is None
    assert pp.tolerance_for("不存在的类型", _p) is None


def test_24_build_writes_and_loads_back():
    _r = pp.build(_patterns(), now=1_700_000_000.0)
    assert _r["written"] is True, _r.get("skipped")
    assert os.path.isfile(_r["path"])
    _back = pp.load_profile(max_age_sec=10 ** 6, now=1_700_000_000.0 + 60)
    assert _back and _back["complete"] is True


def test_25_profile_file_is_pure_lf():
    _r = pp.build(_patterns(), now=1_700_000_000.0)
    _b = io.open(_r["path"], "rb").read()
    assert _b.count(b"\r\n") == 0
    assert _b.count(b"\n") > 0


def test_26_missing_event_is_reported_not_guessed():
    _pat = _patterns()
    _pat["periodicity"].pop(pp.EV_HEART_BEAT)
    _p = pp.build_profile(_pat, now=1_700_000_000.0)
    assert _p["complete"] is False
    assert pp.EV_HEART_BEAT in _p["missing"]
    assert _p["events"][pp.EV_HEART_BEAT]["median_interval_sec"] is None


# ---------------------------------------------------------------- D 覆盖率

def test_31_coverage_denominator_is_covered_plus_no_coverage():
    _agg = {"total": {"covered": 60, "no_coverage": 8, "hit": 55, "miss": 5},
            "types": {"heart.alive": {"covered": 2, "no_coverage": 0}}}
    _c = hr._coverage_block(_agg)
    assert _c["total"] == {"covered": 60, "no_coverage": 8, "coverage": 0.8824}
    assert _c["types"]["heart.alive"]["coverage"] == 1.0


def test_32_zero_denominator_is_none_not_zero():
    _c = hr._coverage_block({"total": {"covered": 0, "no_coverage": 0}, "types": {}})
    assert _c["total"]["coverage"] is None, "没有分母时给 None（0.0 会被读成「一条都没命中」）"


def test_33_missing_keys_do_not_crash():
    _c = hr._coverage_block({})
    assert _c["total"] == {"covered": 0, "no_coverage": 0, "coverage": None}
    assert _c["types"] == {}
    assert hr._coverage_block(None)["total"]["coverage"] is None


def test_34_report_carries_coverage_and_its_denominator():
    _r = hr.report(now=1_700_000_000.0, state={})
    assert "coverage" in _r
    assert set(_r["coverage"]) == {"total", "types"}
    assert "coverage_denominator" in _r["criteria"]
    # 命中率分母与覆盖率分母是**两件事**，都要留下口径
    assert "hit_rate_denominator" in _r["criteria"]


# ---------------------------------------------------------------- E 夜间重建接线

def _night_body():
    _s = _src("nucleus/evolution/night_orchestration.py")
    _fn = None
    for _n in ast.walk(ast.parse(_s)):
        if isinstance(_n, ast.FunctionDef) and _n.name == "generate_night_digest":
            _fn = _n
            break
    assert _fn is not None, "找不到 generate_night_digest"
    return _s, "\n".join(_s.split("\n")[_fn.lineno - 1:_fn.end_lineno])


def test_41_profile_rebuild_happens_before_hit_rate_report():
    _s, _body = _night_body()
    _i_prof = _body.find("五点五、周期档案重建")
    _i_rate = _body.find("六、预测命中率")
    assert _i_prof > 0 and _i_rate > 0
    assert _i_prof < _i_rate, "档案过期会让七类退成 no_coverage ⇒ 报表会变假"


def test_42_rebuild_calls_build_save_summarize():
    _s, _body = _night_body()
    _seg = _body[_body.find("五点五、周期档案重建") - 1200:
                 _body.find("六、预测命中率")]
    for _call in ("build(", "save_profile(", "summarize("):
        assert _call in _seg, "缺 %s ⇒ 重建/落盘/读数少一环" % _call


def test_43_rebuild_failure_does_not_block_digest():
    _s, _body = _night_body()
    _seg = _body[_body.find("五点五、周期档案重建") - 1200:
                 _body.find("六、预测命中率")]
    assert "except Exception" in _seg, "重建失败必须被兜住，不能拖死整篇摘要"


def test_44_no_new_scheduler_in_rebuild_block():
    _s, _body = _night_body()
    _seg = _body[_body.find("五点五、周期档案重建") - 1200:
                 _body.find("六、预测命中率")]
    for _bad in ("threading.Thread", "Timer(", "sched.", "asyncio.create_task"):
        assert _bad not in _seg, "410 红线：不新建调度器"


def test_45_digest_reads_keys_that_summarize_really_returns():
    """★首版把 `by_predict_type` 写成 `by_type` ⇒ 夜报表里那个分布**恒为空**。

    这类"读错键"不会报错、只会静默空 —— 所以要在单测里把两个名字对上。
    """
    _real = set(pp.summarize(pp.build_profile(_patterns(), now=1_700_000_000.0)))
    assert "by_predict_type" in _real
    _s, _body = _night_body()
    _seg = _body[_body.find("五点五、周期档案重建") - 1200:
                 _body.find("六、预测命中率")]
    assert "by_predict_type" in _seg
    assert "by_type" not in _seg.replace("by_predict_type", ""), \
        "除 by_predict_type 外不许再出现 by_type（那个键不存在）"


# ---------------------------------------------------------------- F 样本 50→100

def test_51_periodicity_cache_keeps_last_100():
    from nucleus.genesis.StreamMiner import StreamMiner
    _m = StreamMiner(window_size=10, baseline_window=20)
    assert _m._M411_PERIODICITY_KEEP == 100
    _m._enabled = True
    for _i in range(150):
        _m.feed_event({"event_type": "heart.beat", "timestamp": 1000.0 + _i})
    _c = _m._periodicity_cache["heart.beat"]
    assert len(_c) == 100
    assert _c[0] == 1050.0, "保留的是**最近** 100 条（裁掉老的，不是裁掉新的）"
    assert _c[-1] == 1149.0


def test_52_samples_do_not_mix_event_types():
    from nucleus.genesis.StreamMiner import StreamMiner
    _m = StreamMiner(window_size=10, baseline_window=20)
    _m._enabled = True
    for _i in range(120):
        _m.feed_event({"event_type": "heart.beat", "timestamp": 1000.0 + _i})
        _m.feed_event({"event_type": "heart.alive", "timestamp": 1000.0 + _i})
    assert len(_m._periodicity_cache["heart.beat"]) == 100
    assert len(_m._periodicity_cache["heart.alive"]) == 100


def test_53_keep_falls_back_on_bad_config(monkeypatch):
    """缓存上限被写成 0/负数/非整数 ⇒ 回落 100（写 0 会把周期检测彻底饿死）。"""
    import config as _cfg
    from nucleus.genesis.StreamMiner import StreamMiner
    for _bad in (0, -5, "很多"):
        monkeypatch.setattr(_cfg, "M411_PERIODICITY_KEEP", _bad, raising=False)
        assert StreamMiner(window_size=5,
                           baseline_window=10)._M411_PERIODICITY_KEEP == 100
