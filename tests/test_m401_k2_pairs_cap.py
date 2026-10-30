# -*- coding: utf-8 -*-
"""★401批刀2（P1 / L3）：pairs cap 按行数真 trim + 双列匹配率 + 孤儿按天轮转。

票面背景（烛微401 §L3）：cap 用**进程内序号** `_LAST_PAIR_SEQ` 比文件行数上限
`_PAIR_FILE_CAP`，而序号每次启动归零 ⇒ 单进程写不满 20 万笔就永不 trim
（实测 10-29 单日文件 95MB ≈ 32 万行仍在长）。本文件守四件事：

  ① cap 判据改成**文件当前行数**（首见按实测、同文件增量累加，不进热路径）；
  ② trim **前**先备份（trim = 删当天更早的数据，不可逆，必须可回溯）；
  ③ health 匹配率双列 `matched_raw` / `matched_real` + `source`（两者不可互比）；
  ④ 孤儿改"当日数组 + 按天 JSONL 环形"（数组仍是 UI 口径，2s 轮询不能砸）。

★不测"活体上真的不涨了"：那是重启后核验项（看 `data/monitor/pulse_pairs_*.jsonl` 行数）。
"""
from __future__ import annotations

import ast
import os
import sys
import threading

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config as _cfg  # noqa: E402
import utils.pulse_tracer as _pt  # noqa: E402
from organs.core.PulseMetricsCollector import PulseMetricsCollector  # noqa: E402

_IF_PATH = os.path.join(_ROOT, "nucleus", "field", "InfoField.py")

_STUB_ATTRS = (
    "_cached_organ_stats", "_cached_heartbeat", "_cached_current_user",
    "_dream_count", "_dream_last_topic", "_reflection_count",
    "_reflection_last_domain", "_liver_compress_count", "_liver_fuse_count",
    "_instinct_upgrade_count", "_headless_stats", "_cached_social_emotions",
    "_cached_top_interests", "_cached_life_state", "_cached_emotion_state",
)


@pytest.fixture(autouse=True)
def _reset_state():
    """两族滚动状态是模块级缓存 —— 用例间不清会互相污染（这是实测过的坑）。"""
    _pt._PAIR_LINE_STATE["path"] = None
    _pt._PAIR_LINE_STATE["lines"] = 0
    _pt._ORPHAN_RING_STATE["path"] = None
    _pt._ORPHAN_RING_STATE["lines"] = 0
    yield
    _pt._PAIR_LINE_STATE["path"] = None
    _pt._PAIR_LINE_STATE["lines"] = 0
    _pt._ORPHAN_RING_STATE["path"] = None
    _pt._ORPHAN_RING_STATE["lines"] = 0


def _write_lines(path, n, tag="L"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as _f:
        for _i in range(n):
            _f.write("%s%d\n" % (tag, _i))
    return str(path)


def _count(path):
    with open(str(path), encoding="utf-8") as _f:
        return sum(1 for _ in _f)


class _InfoFieldStub:
    """信息场替身：只提供快照装配真正读到的四个口（其余缺失由 silent_exc 兜住）。"""

    pulse_core = None
    node_pool = None

    def __init__(self, stats: dict):
        self._stats = stats

    def get_stats(self):
        return dict(self._stats)


def _bare_collector(info_field=None):
    _c = object.__new__(PulseMetricsCollector)
    _c._beat_count = 0
    _c.info_field = info_field
    _c._logger = __import__("logging").getLogger("m401k2")
    for _a in _STUB_ATTRS:
        setattr(_c, _a, {})
    return _c


# ---------------------------------------------------- ① cap 判据 = 文件当前行数
def test_01_line_state_measures_then_accumulates(tmp_path):
    """首见按**实测**行数，同文件按**增量**累加，换文件重新实测（跨日换名）。"""
    _p = _write_lines(os.path.join(str(tmp_path), "pulse_pairs_20261030.jsonl"), 7)
    assert _pt._pair_lines_after_append(_p, 0) == 7, "首见应实测文件行数"
    assert _pt._pair_lines_after_append(_p, 3) == 10, "同文件应增量累加（不重读全文件）"
    _p2 = _write_lines(os.path.join(str(tmp_path), "pulse_pairs_20261031.jsonl"), 2)
    assert _pt._pair_lines_after_append(_p2, 0) == 2, "换文件必须重新实测"


def test_02_trim_by_lines_really_trims(tmp_path, monkeypatch):
    """行数 > cap ⇒ 真 trim 到 cap 行，保留的是**最近** cap 行（老的先走）。"""
    monkeypatch.setitem(_cfg.RUNTIME_PARAMS, "pulse_pair_file_cap", 5)
    _p = _write_lines(os.path.join(str(tmp_path), "pairs.jsonl"), 12)

    _pt._trim_pair_file(_p)

    assert _count(_p) == 5, "没按行数 trim 到 cap"
    with open(_p, encoding="utf-8") as _f:
        _lines = [x.strip() for x in _f if x.strip()]
    assert _lines == ["L7", "L8", "L9", "L10", "L11"], "trim 应保留最近 cap 行：%s" % _lines


def test_03_trim_backs_up_before_cut(tmp_path, monkeypatch):
    """trim 前必须留备份：等于删掉当天更早的数据，一旦无备份不可逆。"""
    monkeypatch.setitem(_cfg.RUNTIME_PARAMS, "pulse_pair_file_cap", 3)
    _p = _write_lines(os.path.join(str(tmp_path), "pairs.jsonl"), 9)

    _pt._trim_pair_file(_p)

    _bak = _p + ".trim.bak"
    assert os.path.isfile(_bak), "没有 trim 前备份"
    assert _count(_bak) == 9, "备份应是 trim 前的全量（%d 行）" % _count(_bak)
    assert _count(_p) == 3


def test_04_cap_and_switch_come_from_config(monkeypatch):
    """cap 与灰度开关都读 config（单一源），且 cap 非法值要能回退而不是当 0 用。"""
    monkeypatch.setitem(_cfg.RUNTIME_PARAMS, "pulse_pair_file_cap", 123456)
    assert _pt._pair_file_cap() == 123456
    monkeypatch.setitem(_cfg.RUNTIME_PARAMS, "pulse_pair_file_cap", 0)
    assert _pt._pair_file_cap() == _pt._PAIR_FILE_CAP, "非法值(0)应回退到模块默认"

    monkeypatch.setattr(_cfg, "ENABLE_M401_PAIR_TRIM_BY_LINES", False)
    assert _pt._pair_trim_by_lines_on() is False, "灰度开关关闭必须能被读到"
    monkeypatch.setattr(_cfg, "ENABLE_M401_PAIR_TRIM_BY_LINES", True)
    assert _pt._pair_trim_by_lines_on() is True


def test_05_trigger_uses_line_count_not_process_seq():
    """AST：`_dump_pairs` 的 trim 触发点必须用 `_pair_lines_after_append`（不是 `_LAST_PAIR_SEQ`）。

    这是本刀的要害：旧判据比的是**进程内事件序号**，重启归零 ⇒ 永不触发。
    用 AST 找调用，而不是 grep 文本（旧变量名在注释里也有）。
    """
    _tree = ast.parse(open(_pt.__file__, encoding="utf-8").read())
    _dump = None
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.FunctionDef) and _n.name == "_dump_pairs":
            _dump = _n
            break
    assert _dump is not None, "找不到 _dump_pairs"
    _names = {x.func.id for x in ast.walk(_dump)
              if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)}
    assert "_pair_lines_after_append" in _names, "trim 触发点没走行数判据"


# --------------------------------------------------------- ② 孤儿按天轮转（环形）
def test_06_orphan_ring_rotates_by_day_and_caps(tmp_path, monkeypatch):
    """当日环形文件：同日追加、超 maxlen 回写最近 N 行、文件名带当天日期。"""
    monkeypatch.setattr(_pt, "_ISO_BASE_DIR", str(tmp_path))
    monkeypatch.setitem(_cfg.RUNTIME_PARAMS, "pulse_tracer_orphan_ring_max", 3)
    _today = _pt.datetime.now().strftime("%Y%m%d")
    _p = os.path.join(str(tmp_path), "data", "monitor", "pulse_orphans_%s.jsonl" % _today)
    assert _pt._orphan_ring_path() == _p, "环形文件应按天命名（跨日自动换文件）"

    _pt._ring_append_orphans([{"n": 1}, {"n": 2}])
    assert _count(_p) == 2
    _pt._ring_append_orphans([{"n": 3}, {"n": 4}, {"n": 5}])
    assert _count(_p) == 3, "超 maxlen 应回写最近 N 行（不是无限追加）"
    with open(_p, encoding="utf-8") as _f:
        _ns = [__import__("json").loads(x)["n"] for x in _f if x.strip()]
    assert _ns == [3, 4, 5], "环形应保留最新行：%s" % _ns


def test_07_today_start_ts_is_local_midnight():
    """跨天过滤的基准：今日 00:00 ≤ now < 明日 00:00。"""
    _t = _pt._today_start_ts()
    _now = _pt.datetime.now().timestamp()
    assert _t <= _now < _t + 86400


# --------------------------------------------------- ③ 匹配率双列（raw / real）
def test_08_snapshot_has_dual_match_columns(monkeypatch):
    """health 自报的匹配率必须双列：raw 含万能器官（几乎必中），real 剔除后才可比 tracer。"""
    _c = _bare_collector(_InfoFieldStub(
        {"total_published": 200, "total_matched": 199,
         "total_matched_real": 17, "active_conditions": 4}))
    monkeypatch.setattr(_c, "_collect_memory_pressure", lambda: {})

    _s = _c._build_observability_snapshot()["info_field"]

    assert _s["matched"] == 199, "旧字段名必须保留原值（history 消费方）"
    assert _s["matched_raw"] == 199
    assert _s["matched_real"] == 17
    assert _s["matched_raw"] != _s["matched_real"], "两列相同 ⇒ 没剔万能器官"
    assert set(_s["matched_source"]) == {"matched_raw", "matched_real"}, "缺口径说明"


def test_09_missing_real_is_none_not_zero(monkeypatch):
    """老版本/替身的 get_stats 没有 real 字段 ⇒ `None`，不许填 0 冒充"一次没匹配上"。"""
    _c = _bare_collector(_InfoFieldStub(
        {"total_published": 10, "total_matched": 9}))
    monkeypatch.setattr(_c, "_collect_memory_pressure", lambda: {})

    _s = _c._build_observability_snapshot()["info_field"]
    assert _s["matched_real"] is None, "缺读数填了 0"
    assert _s["matched_raw"] == 9


def test_10_infofield_counts_real_only_when_matched():
    """AST：`_total_matched_real` 只在 `if <real> > 0` 分支里 +1（万能器官命中也算命中，
    但 real 口径只数"真匹配"，判据必须能从结构上看出这条门）。"""
    _tree = ast.parse(open(_IF_PATH, encoding="utf-8").read())
    _ok = False
    for _n in ast.walk(_tree):
        if not isinstance(_n, ast.If) or not isinstance(_n.test, ast.Compare):
            continue
        if not any(isinstance(_o, ast.Gt) for _o in _n.test.ops):
            continue
        for _b in ast.walk(_n):
            if (isinstance(_b, ast.Assign) and isinstance(_b.targets[0], ast.Attribute)
                    and _b.targets[0].attr == "_total_matched_real"):
                _ok = True
    assert _ok, "`_total_matched_real` 没有挂在 `> 0` 门内（real 口径被放大）"


def test_11_thread_state_lock_is_not_reentrant_deadlock():
    """复算防回归：状态锁只包住读改写，不能把 `_pair_count_lines` 放进锁里再 IO。

    做法：并发 8 线程各自追加 20 次，最终累计值必须等于实测行数（不自锁、不丢增量）。
    """
    import tempfile
    _d = tempfile.mkdtemp(prefix="m401k2_")
    _p = _write_lines(os.path.join(_d, "pairs.jsonl"), 0)
    # 先把"首见实测"这一步做掉（生产侧 `_dump_pairs` 也是先写盘再问行数），
    # 否则第一个线程的那一次会走"实测 0 行"分支，末尾少 1 纯属测法误差。
    assert _pt._pair_lines_after_append(_p, 0) == 0

    def _work():
        for _ in range(20):
            _pt._pair_lines_after_append(_p, 1)

    _ts = [threading.Thread(target=_work) for _ in range(8)]
    for _t in _ts:
        _t.start()
    for _t in _ts:
        _t.join(timeout=10)
    assert all(not _t.is_alive() for _t in _ts), "状态锁死锁"
    assert _pt._PAIR_LINE_STATE["lines"] == 160, \
        "并发累加丢增量：%s（应为 160）" % _pt._PAIR_LINE_STATE["lines"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
