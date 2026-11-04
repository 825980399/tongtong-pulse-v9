# -*- coding: utf-8 -*-
"""450 刀2：KAL 命中率观测（计数 / 按器官 / 落盘 / 生产路径护栏）。"""
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import nucleus.knowledge_access_layer as KAL


class _Node:
    def __init__(self, i):
        self.node_id = i


class _Pool:
    def __init__(self):
        self.nodes = [_Node("a"), _Node("b")]

    def get_all_nodes(self):
        return list(self.nodes)


def _reset():
    """清计数（每个用例独立，避免互相污染）。"""
    with KAL._M450_KAL_LOCK:
        KAL._m450_kal_stat.update({
            "since": KAL.time.time(), "total": 0, "hit": 0, "miss": 0,
            "by_organ": {}, "by_api": {},
            "last_flush": KAL.time.time(), "flushes": 0})
        KAL._m450_kal_last.clear()
        KAL._m450_kal_last.update({"total": 0, "hit": 0, "miss": 0,
                                   "by_organ": {}, "by_api": {}})


@pytest.fixture()
def kal():
    _reset()
    return KAL.get_kal(node_pool=_Pool(), refresh=True)


# ---------------------------------------------------------------- 命中判定
def test_is_hit_容器看长度标量看None():
    assert KAL._m450_is_hit([1]) is True
    assert KAL._m450_is_hit([]) is False
    assert KAL._m450_is_hit({"a": 1}) is True
    assert KAL._m450_is_hit(None) is False
    assert KAL._m450_is_hit(_Node("x")) is True


# ---------------------------------------------------------------- 计数
def test_get_node_命中与未命中分别计数(kal):
    assert kal.get_node("a") is not None
    assert kal.get_node("不存在") is None
    _s = KAL.kal_hit_stats()
    assert _s["total"] == 2, _s
    assert _s["hit"] == 1, _s
    assert _s["miss"] == 1, _s
    assert _s["hit_rate_pct"] == 50.0, _s
    assert _s["by_api"]["get_node"] == {"total": 2, "hit": 1, "miss": 1}


def test_批量与搜索分别记在各自api名下(kal):
    kal.get_nodes_batch(["a", "b"])
    kal.search_by_keywords(["不存在的关键词"])
    _s = KAL.kal_hit_stats()
    assert _s["by_api"]["get_nodes_batch"]["total"] == 1, _s["by_api"]
    assert _s["by_api"]["get_nodes_batch"]["hit"] == 1, _s["by_api"]
    assert _s["by_api"]["search_by_keywords"]["hit"] == 0, _s["by_api"]


def test_按器官分档_用调用栈推导(kal):
    """★器官归属不能靠调用方传参（要改 9 个签名）⇒ 从 frame 的模块名推导。

    这里造一个 `__name__='organs.body.PulseFake'` 的模块来喂调用栈。
    """
    mod = types.ModuleType("organs.body.PulseFake")
    _code = "def probe(k):\n    return k.get_node('a')\n"
    exec(compile(_code, "<probe>", "exec"), mod.__dict__)  # noqa: S102 —— 自证调用栈归属
    mod.probe(kal)
    _s = KAL.kal_hit_stats()
    assert "PulseFake" in _s["by_organ"], _s["by_organ"]
    assert _s["by_organ"]["PulseFake"]["total"] == 1, _s["by_organ"]


def test_不影响返回值(kal):
    """★观测不能改语义：包装前后返回逐字相同。"""
    assert kal.get_node("a").node_id == "a"
    assert kal.get_node("zzz") is None
    assert kal.search_by_keywords(["kw"]) == []
    assert isinstance(kal.get_nodes_batch(["a"]), list)


def test_开关关掉不计数(monkeypatch, kal):
    """★关掉只影响计数，不影响查询结果。"""
    monkeypatch.setattr(KAL, "_m450_kal_on", lambda: False)
    _before = KAL.kal_hit_stats()["total"]
    assert kal.get_node("a") is not None
    assert KAL.kal_hit_stats()["total"] == _before


# ---------------------------------------------------------------- 落盘
def test_flush写一行且窗口零查询不写(tmp_path, monkeypatch, kal):
    _p = os.path.join(str(tmp_path), "kal_hit_trace.jsonl")
    monkeypatch.setattr(config, "M450_KAL_TRACE_PATH", _p, raising=False)
    # 窗口零查询 ⇒ 不落行
    assert KAL._m450_kal_flush() == ""
    assert not os.path.exists(_p)
    kal.get_node("a")
    kal.get_node("zzz")
    _written = KAL._m450_kal_flush()
    assert _written == _p, _written
    with open(_p, encoding="utf-8") as f:
        _rows = [json.loads(x) for x in f if x.strip()]
    assert len(_rows) == 1, _rows
    _r = _rows[0]
    assert _r["window_total"] == 2, _r
    assert _r["window_hit"] == 1, _r
    assert _r["window_hit_rate_pct"] == 50.0, _r
    assert _r["cum_total"] == 2, _r
    assert _r["kind"] == "kal_hit", _r


def test_flush后窗口增量归零而累计保留(tmp_path, monkeypatch, kal):
    _p = os.path.join(str(tmp_path), "k.jsonl")
    monkeypatch.setattr(config, "M450_KAL_TRACE_PATH", _p, raising=False)
    kal.get_node("a")
    KAL._m450_kal_flush()
    kal.get_node("不存在")
    KAL._m450_kal_flush()
    with open(_p, encoding="utf-8") as f:
        _rows = [json.loads(x) for x in f if x.strip()]
    assert len(_rows) == 2, _rows
    assert _rows[0]["window_total"] == 1 and _rows[0]["cum_total"] == 1, _rows[0]
    assert _rows[1]["window_total"] == 1 and _rows[1]["cum_total"] == 2, _rows[1]


def test_测试进程不许写默认生产路径(kal):
    """★446/447 三次同型坑的护栏：pytest 里默认生产路径必须拒写。"""
    _default = os.path.join(KAL._M450_KAL_PROJECT_ROOT,
                            KAL._M450_KAL_TRACE_REL_DEFAULT)
    _before = os.path.getsize(_default) if os.path.exists(_default) else -1
    kal.get_node("a")
    assert KAL._m450_kal_flush() == ""
    _after = os.path.getsize(_default) if os.path.exists(_default) else -1
    assert _after == _before, (_before, _after)


def test_统计读数结构完整(kal):
    kal.get_node("a")
    _s = KAL.kal_hit_stats()
    for _k in ("enabled", "wrapped", "total", "hit", "miss", "hit_rate_pct",
               "by_organ", "by_api", "flushes"):
        assert _k in _s, (_k, sorted(_s))
    assert _s["wrapped"] is True, _s
