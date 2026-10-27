# -*- coding: utf-8 -*-
"""第375批 刀11 定向单测：token 四维判读补第四维（429/健康度）＋ 挂进日报。

口径说明（为什么这么测）：
  · 354 的「四维」④ 那一维**是空的** —— 注释写「由渠道健康面给出（此处只读不判）」，
    而 `ChannelHealthTracker` 是进程内内存态、重启即清零，给不出「当日累计」。
    本件钉的是「④ 真的算了」，不是「函数返回值非空」。
  · 「没采到」与「采到且全健康」必须分开（铁律166）：旧格式落盘没有 `failed`
    字段，读成 100% 健康就是假安全 ⇒ 必须有 `error_dim_state` 这个显式状态位。
  · 阈值只有一处真源：`CHANNEL_RATE_LIMIT_ALERT` 与刀10 的入池判据同值（2），
    一处改两处跟 —— 本件用「同源」断言把它钉住。
  · 任何断言都不读生产 `data/`：日汇总走 `base_dir` 注入；
    日报节走 `read_daily_tokens_report` 的 monkeypatch。
"""
import json
import os
import re

import pytest

import nucleus.llm.call_recorder as CR
from nucleus.self_awareness import daily_reader as DR

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DS = os.path.join(_ROOT, "nucleus", "self_awareness", "DailyScheduler.py")


def _write_day(tmp_path, cells, date="2026-10-27"):
    """写一份日汇总到 tmp（结构与生产一致），返回目录。"""
    _d = str(tmp_path)
    _payload = {"schema": "daily_tokens/354.v1", "date": date,
                "calls": sum(int((v or {}).get("calls") or 0) for v in cells.values()),
                "total_tokens": sum(int((v or {}).get("tokens") or 0) for v in cells.values()),
                "paid_tokens": 0, "by_channel": cells, "by_model": {},
                "alerted": False, "updated_at": "2026-10-27T21:00:00"}
    with open(os.path.join(_d, "daily_tokens.json"), "w", encoding="utf-8") as f:
        json.dump(_payload, f, ensure_ascii=False)
    return _d


def _cell(calls, failed=None, rate_limit=None, tokens=0):
    _c = {"calls": calls, "tokens": tokens}
    if failed is not None:
        _c["failed"] = failed
    if rate_limit is not None:
        _c["rate_limit"] = rate_limit
    return _c


# ============================ ① schema 与阈值同源
def test_01_schema_bumped_to_375():
    """键集变了就升版本 —— 消费方按旧版读新体必然错位（多出/缺失都算）。"""
    assert CR.CHANNEL_READING_SCHEMA == "channel_reading/375.v1"


def test_02_rate_limit_threshold_same_source_as_d10():
    """429 告警线必须与刀10 入池判据同值 —— 两处各写一个数就是两个口径。"""
    assert CR.CHANNEL_RATE_LIMIT_ALERT == 2
    _text = CR.POOL_EXPANSION_CRITERIA["TokenHub"]
    _m = re.search(r"429≥(\d+)", _text)
    assert _m, f"入池判据文案里没有 429 阈值：{_text!r}"
    assert int(_m.group(1)) == CR.CHANNEL_RATE_LIMIT_ALERT, (
        f"入池判据的 429 阈值与 CHANNEL_RATE_LIMIT_ALERT 不同源："
        f"{_m.group(1)} vs {CR.CHANNEL_RATE_LIMIT_ALERT}")


def test_03_health_alert_line_is_not_reachable_by_accident():
    """健康度告警线 0.6 == 刀10 判据的「健康度<0.6」（同一语义两个消费口）。"""
    assert CR.CHANNEL_HEALTH_ALERT == 0.6
    assert "健康度<0.6" in CR.POOL_EXPANSION_CRITERIA["TokenHub"]


# ============================ ② 第四维真的算
def test_04_health_computed_per_channel(tmp_path):
    _d = _write_day(tmp_path, {
        "zhipu-47-flash": _cell(10, failed=3, rate_limit=0, tokens=100),
        "bailian-qwen-plus": _cell(10, failed=0, rate_limit=0, tokens=50)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    _h = _r["by_channel_health"]
    assert _h["zhipu-47-flash"]["health"] == pytest.approx(0.7)
    assert _h["bailian-qwen-plus"]["health"] == pytest.approx(1.0)
    assert _h["zhipu-47-flash"]["error_dims"] is True
    assert _r["error_dim_state"] == "ok"


def test_05_unhealthy_alert_fires_below_line(tmp_path):
    _d = _write_day(tmp_path, {"zhipu-47-flash": _cell(10, failed=5, rate_limit=0)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    _codes = [(a["code"], a["channel"]) for a in _r["alerts"]]
    assert ("unhealthy", "zhipu-47-flash") in _codes, _r["alerts"]


def test_06_unhealthy_alert_does_not_fire_at_boundary(tmp_path):
    """健康度**恰好** 0.6 不算不健康（判据是 `<`，两侧都要测）。"""
    _d = _write_day(tmp_path, {"c1": _cell(10, failed=4, rate_limit=0)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    assert [a for a in _r["alerts"] if a["code"] == "unhealthy"] == []


def test_07_rate_limited_alert_fires(tmp_path):
    _d = _write_day(tmp_path, {"siliconflow-qwen2-7b": _cell(10, failed=0, rate_limit=2)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    _codes = [(a["code"], a["channel"], a["value"]) for a in _r["alerts"]]
    assert ("rate_limited", "siliconflow-qwen2-7b", 2) in _codes, _r["alerts"]


def test_08_rate_limited_alert_does_not_fire_at_one(tmp_path):
    """1 次限流不告警（阈值 ≥2）；也不许因为限流而把 100% 健康的渠道判成 unhealthy。"""
    _d = _write_day(tmp_path, {"c1": _cell(10, failed=0, rate_limit=1)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    assert [a for a in _r["alerts"] if a["code"] in ("rate_limited", "unhealthy")] == []


def test_09_rate_limit_is_not_counted_as_failure(tmp_path):
    """限流是容量信号、失败是质量信号 —— 5 次 429 但零失败 ⇒ 只告警限流。"""
    _d = _write_day(tmp_path, {"c1": _cell(10, failed=0, rate_limit=5)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    _codes = [a["code"] for a in _r["alerts"]]
    assert "rate_limited" in _codes
    assert "unhealthy" not in _codes, _r["alerts"]
    assert _r["by_channel_health"]["c1"]["health"] == pytest.approx(1.0)


# ============================ ③ 没采到 ≠ 全健康
def test_10_legacy_schema_is_unavailable_not_healthy(tmp_path):
    """375刀10 之前落盘的 cell 没有 failed/rate_limit 键 ⇒ **不许**判成健康。"""
    _d = _write_day(tmp_path, {"zhipu-47-flash": _cell(33, tokens=1000)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    assert _r["error_dim_state"] == "unavailable"
    assert _r["by_channel_health"]["zhipu-47-flash"]["error_dims"] is False
    assert _r["by_channel_health"]["zhipu-47-flash"]["health"] is None, (
        "旧格式没有失败计数，算不出健康度 ⇒ 必须是 None，不能给 1.0")
    assert [a for a in _r["alerts"] if a["code"] in ("unhealthy", "rate_limited")] == []


def test_11_mixed_schema_state_is_ok_and_per_channel_flagged(tmp_path):
    """一个渠道采到了、一个没采到 ⇒ 状态是 ok，但没采到那个仍单列 False。"""
    _d = _write_day(tmp_path, {"new": _cell(5, failed=1, rate_limit=0),
                               "old": _cell(5)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    assert _r["error_dim_state"] == "ok"
    assert _r["by_channel_health"]["new"]["error_dims"] is True
    assert _r["by_channel_health"]["old"]["error_dims"] is False


def test_12_zero_calls_channel_has_no_health(tmp_path):
    """0 次调用算不出占比 ⇒ health=None（不是 0，也不是 1）。"""
    _d = _write_day(tmp_path, {"c0": _cell(0, failed=0, rate_limit=0)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    assert _r["by_channel_health"]["c0"]["health"] is None


def test_13_four_dimensions_all_present(tmp_path):
    """四维（paid / HHI / 燃烧率 / 健康度）必须都在返回体里，缺一维就是没做完。

    ★不光看键在不在，还要看**有没有内容** —— 「键在但空 dict」与「没做」对
    消费方是同一件事，只断言 `in` 会把空壳判成合格。
    """
    _d = _write_day(tmp_path, {"c1": _cell(10, failed=0, rate_limit=0)})
    _r = CR.read_daily_tokens_report(base_dir=_d)
    for _k in ("paid_tokens", "hhi", "by_channel_burn", "by_channel_health"):
        assert _k in _r, f"四维缺 {_k}"
    assert _r["by_channel_health"].get("c1"), "第四维是空壳（键在但没内容）"
    assert _r["by_channel_health"]["c1"]["calls"] == 10


# ============================ ④ 日报节
def _fake_report():
    return {"schema": "channel_reading/375.v1", "date": "2026-10-27", "calls": 20,
            "total_tokens": 1000, "paid_tokens": 0, "hhi": 0.52,
            "alerts": [{"code": "rate_limited", "level": "warn",
                        "channel": "c1", "value": 3, "why": "-"}],
            "by_channel_burn": {},
            "by_channel_health": {"c1": {"calls": 20, "failed": 0, "rate_limit": 3,
                                         "health": 1.0, "error_dims": True},
                                 "c2": {"calls": 0, "failed": 0, "rate_limit": 0,
                                        "health": None, "error_dims": True}},
            "error_dim_state": "ok", "threshold_deprecated": "全局阈值口径错误"}


def test_14_reader_appends_section(tmp_path, monkeypatch):
    _p = os.path.join(str(tmp_path), "r.txt")
    with open(_p, "w", encoding="utf-8") as f:
        f.write("日报正文\n")
    monkeypatch.setattr(CR, "read_daily_tokens_report", lambda *a, **k: _fake_report())
    _got = DR.append_channel_reading_section(_p)
    assert _got["date"] == "2026-10-27"
    with open(_p, encoding="utf-8") as f:
        _txt = f.read()
    assert "【渠道四维判读】" in _txt, _txt
    assert "HHI=0.520" in _txt
    assert "rate_limited@c1=3" in _txt
    assert "error_dim=ok" in _txt
    assert "N/A" in _txt, "health=None 的渠道必须显示 N/A，不能显示 100%"


def test_15_reader_never_raises(tmp_path, monkeypatch):
    """取数炸了只许返回 {}，不许把调度主流程带下去；日报原内容不许被动。"""
    _p = os.path.join(str(tmp_path), "r.txt")
    with open(_p, "w", encoding="utf-8") as f:
        f.write("原样\n")

    def _boom(*a, **k):
        raise RuntimeError("取数炸了")

    monkeypatch.setattr(CR, "read_daily_tokens_report", _boom)
    assert DR.append_channel_reading_section(_p) == {}
    with open(_p, encoding="utf-8") as f:
        assert f.read() == "原样\n"


# ============================ ⑤ 挂载（接线才叫做了）
def _ds_src():
    with open(_DS, encoding="utf-8") as f:
        return f.read()


def test_16_scheduler_mounts_reader():
    _s = _ds_src()
    assert "append_channel_reading_section" in _s, "日报节没有被任何生产代码调用（有键无色）"
    assert '_res["channel_reading"]' in _s, "挂载没有落进 run_once 的结果字典"


def test_17_mount_same_guard_as_paid_usage():
    """与 paid_usage 同一守卫（非测试环境 + 报告已生成）—— 多一个判据就是多一处口径。"""
    _s = _ds_src()
    _guards = re.findall(r"if not self\._is_test_env\(\) and _rp:\n((?:[ \t]+.*\n)+)", _s)
    assert len(_guards) >= 2, f"挂载守卫与 paid_usage 不是同一写法：{len(_guards)} 处"
    assert any("append_channel_reading_section" in _g for _g in _guards), (
        "渠道判读节没有被同一守卫保护")


def test_18_mount_is_exception_isolated():
    """挂载必须 try/except + silent_exc 留痕（日报附加节不许拖垮调度）。"""
    _s = _ds_src()
    _i = _s.find("append_channel_reading_section")
    assert _i > 0
    _win = _s[max(0, _i - 700):_i + 300]
    assert "try:" in _win and "except Exception" in _win, _win
    assert "silent_exc" in _win, "异常被吞但没有留痕"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
