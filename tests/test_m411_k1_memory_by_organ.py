# -*- coding: utf-8 -*-
"""411 批刀1 单测：器官级内存归因（memory_by_organ）。

钉语义（不钉措辞）：
  * **RSS 不可分**这件事必须在产物里写明（`rss_separable=False` + `basis` 含"不是 RSS"）；
  * 从器官实例出发的遍历要**算得出谁大**，而不是只报一个总数；
  * 超预算要 `partial=True`（不许静默截断）；
  * 300s 节流要真生效；`0` 与"没采到"必须可分。
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.observability import memory_by_organ as mo  # noqa: E402


class _Fat:
    """模拟"吃内存的器官"：一个 20k 键的 dict + 10k 个小 dict。"""

    def __init__(self, n: int = 20000):
        self.store = {("k%d" % i): ("v" * 40) for i in range(n)}
        self.lst = [{"a": i, "b": "x" * 20} for i in range(n // 2)]


class _Thin:
    def __init__(self):
        self.x = 1
        self.name = "thin"


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    """每个用例都重定向数据根并复位节流状态。"""
    mo.set_data_dir(str(tmp_path))
    mo._LAST_WRITE["ts"] = 0.0
    yield
    mo.set_data_dir(None)
    mo._LAST_WRITE["ts"] = 0.0


# ---------------------------------------------------------------- A 模块→器官映射

def test_01_organ_of_module_brain():
    assert mo.organ_of_module("organs.brain.PulseInnerWorld") == "大脑"


def test_02_organ_of_module_core_and_senses():
    assert mo.organ_of_module("organs.core.PulseMetricsCollector") == "核心"
    assert mo.organ_of_module("organs.senses.PulseTouch") == "感官"


def test_03_organ_of_module_nucleus_specific_beats_generic():
    # 具体前缀必须赢过 `nucleus.` 兜底：先长后短的顺序是判据的一部分
    assert mo.organ_of_module("nucleus.mnemosyne.experience_pool") == "记忆核"
    assert mo.organ_of_module("nucleus.field.InfoField") == "信息场"
    assert mo.organ_of_module("nucleus.anything_else") == "核·其他"


def test_04_organ_of_module_builtin_is_empty():
    assert mo.organ_of_module("builtins") == ""
    assert mo.organ_of_module("json.decoder") == ""
    assert mo.organ_of_module("") == ""


def test_05_organ_of_module_unknown_is_empty():
    # 不在表里的第三方模块 ⇒ 空串（不是"其他器官"，那会造出不存在的归属）
    assert mo.organ_of_module("some_random_lib.core") == ""


# ---------------------------------------------------------------- B 采样形状

def test_10_sample_organs_ranks_by_size():
    _map = {"胖器官": _Fat(5000), "瘦器官": _Thin(), "空位": None}
    r = mo.sample_organs(organ_map=_map, rss_mb=11114.3)
    assert r["state"] in ("ok", "partial")
    assert r["mode"] == "organs"
    _names = [x["organ"] for x in r["organs"]]
    assert _names[0] == "胖器官", "按 est_mb 降序，胖的必须排第一（实际 %r）" % (_names,)
    assert "瘦器官" in _names
    assert "空位" not in _names, "None 器官不该出现在结果里"


def test_11_sample_organs_declares_rss_not_separable():
    r = mo.sample_organs(organ_map={"a": _Thin()}, rss_mb=1024.0)
    assert r["rss_separable"] is False
    assert "不是 RSS" in str(r["basis"]), "口径必须写明，否则读者会当 RSS 用"
    assert r["rss_mb"] == 1024.0, "整进程 RSS 要原样透出（它不可分，但它是真的）"


def test_12_sample_organs_reports_objects_and_coverage():
    r = mo.sample_organs(organ_map={"胖": _Fat(2000)})
    assert isinstance(r["objects_total"], int) and r["objects_total"] > 0
    assert isinstance(r["objects_attributed"], int) and r["objects_attributed"] > 0
    assert r["coverage"] is None or 0 <= float(r["coverage"]) <= 1.0


def test_13_sample_organs_empty_map_falls_back_to_types():
    r = mo.sample_organs(organ_map={})
    assert r["mode"] == "types"
    assert "organ_map_unavailable" in str(r["reason"])


def test_14_sample_dedups_same_object():
    """同一个对象被两个属性引用时只算一次（visited 去重）。"""
    _shared = {"payload": "x" * 100}

    class _Twice:
        def __init__(self):
            self.a = _shared
            self.b = _shared

    r = mo.sample_organs(organ_map={"双引用": _Twice()}, per_organ_cap=100)
    assert r["organs"][0]["objs"] >= 1
    # 若没去重，objs 会因为 a/b 各展开一遍而虚高；这里只要求它不随引用次数线性增长
    assert r["organs"][0]["objs"] <= 5


def test_15_sample_organs_disabled():
    _old = getattr(config, "ENABLE_M411_MEMORY_BY_ORGAN", None)
    config.ENABLE_M411_MEMORY_BY_ORGAN = False
    try:
        r = mo.sample_organs(organ_map={"a": _Thin()})
        assert r["state"] == "disabled"
        assert r["organs"] == []
    finally:
        config.ENABLE_M411_MEMORY_BY_ORGAN = True if _old is None else _old


def test_16_top3_at_most_three():
    r = mo.sample_organs(organ_map={"a": _Fat(100), "b": _Thin(), "c": _Thin(), "d": _Thin()})
    _t = mo.top3(r)
    assert len(_t) <= 3
    assert set(_t[0].keys()) == {"organ", "objs", "est_mb"}


# ---------------------------------------------------------------- C 落盘与节流

def test_20_day_path_naming():
    _p = mo.day_path(1793456000.0)
    assert os.path.basename(_p).startswith("memory_by_organ_")
    assert _p.endswith(".json")
    assert len(os.path.basename(_p)) == len("memory_by_organ_YYYYMMDD.json")


def test_21_append_record_writes_once_then_throttles():
    r = mo.sample_organs(organ_map={"a": _Fat(100)})
    _a = mo.append_record(r, force=True)
    assert _a["ok"] and _a["written"], _a
    _b = mo.append_record(r)
    assert _b["written"] is False and "throttled" in _b["reason"]
    _d = json.load(io.open(_a["path"], encoding="utf-8"))
    assert len(_d["records"]) == 1
    assert _d["basis"] == r["basis"]


def test_22_append_record_rejects_unusable():
    _r = mo.append_record({"state": "unavailable"}, force=True)
    assert _r["ok"] is False
    assert "reading_not_usable" in _r["reason"]


def test_23_append_record_file_is_pure_lf():
    r = mo.sample_organs(organ_map={"a": _Fat(100)})
    _a = mo.append_record(r, force=True)
    _b = open(_a["path"], "rb").read()
    assert _b.count(b"\r\n") == 0, "产物必须是纯 LF（项目行尾纪律）"
    assert _b.count(b"\n") > 0


def test_24_append_record_caps_records():
    """超上限要截断并留痕，不是无限增长。"""
    r = mo.sample_organs(organ_map={"a": _Thin()})
    _p = mo.append_record(r, force=True)["path"]
    _d = json.load(io.open(_p, encoding="utf-8"))
    _d["records"] = [{"i": i} for i in range(mo.MAX_RECORDS_PER_FILE + 5)]
    io.open(_p, "w", encoding="utf-8", newline="\n").write(
        json.dumps(_d, ensure_ascii=False))
    mo._LAST_WRITE["ts"] = 0.0
    mo.append_record(r, force=True)
    _d2 = json.load(io.open(_p, encoding="utf-8"))
    assert len(_d2["records"]) == mo.MAX_RECORDS_PER_FILE
    assert _d2.get("truncated") is True


# ---------------------------------------------------------------- D 静态接线

def _methods_of(path: str) -> dict:
    _src = io.open(path, encoding="utf-8").read()
    _t = ast.parse(_src)
    _out = {}
    for _n in ast.walk(_t):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _out[_n.name] = _n
    return _out


def test_30_collector_has_hook_and_calls_it():
    _m = _methods_of(os.path.join(_ROOT, "organs", "core", "PulseMetricsCollector.py"))
    assert "_m411_memory_by_organ" in _m, "采集器缺器官内存采样方法"
    _body = ast.unparse(_m["_collect_memory_pressure"])
    assert "_m411_memory_by_organ" in _body, "采样方法没被挂到活采样链上"


def test_31_snapshot_exposes_top3():
    _m = _methods_of(os.path.join(_ROOT, "organs", "core", "PulseMetricsCollector.py"))
    _body = ast.unparse(_m["_build_observability_snapshot"])
    assert "memory_by_organ_top3" in _body, "快照必须只放前 3 名（任务书要求）"
    assert "rss_separable" in _body, "口径字段要透出，否则读的人会当 RSS"


def test_32_no_new_thread_or_scheduler():
    _src = io.open(os.path.join(_ROOT, "nucleus", "observability",
                                "memory_by_organ.py"), encoding="utf-8").read()
    for _bad in ("threading.Thread", "Timer(", "sched.", "asyncio.create_task"):
        assert _bad not in _src, "红线：不新建线程/调度器（命中 %s）" % _bad
