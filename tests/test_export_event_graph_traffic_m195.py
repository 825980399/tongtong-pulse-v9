# -*- coding: utf-8 -*-
"""195刀3 隔离单测：export_event_graph 接 InfoField._event_publish_count（事件流量接线）。

验收：运行态发布计数接入时对应 edge 增加 publish_count 且 stats.event_traffic_wired=True；
未接入时边表原样（纯静态现状，零行为变化）。
"""
from tools.export_event_graph import export_event_graph


def test_default_no_publish_count():
    res = export_event_graph()
    assert "publish_count" not in res["edges"][0], "默认不应含 publish_count"
    assert res["stats"].get("event_traffic_wired") is False


def test_wired_publish_count():
    base = export_event_graph()
    et = base["edges"][0]["event_type"]
    res = export_event_graph(runtime_event_publish_count={et: 9})
    e = [x for x in res["edges"] if x["event_type"] == et][0]
    assert e["publish_count"] == 9
    assert res["stats"]["event_traffic_wired"] is True
