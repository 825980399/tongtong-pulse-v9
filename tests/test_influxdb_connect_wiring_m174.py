# -*- coding: utf-8 -*-
"""174刀1 T-InfluxDB连接接线-1 接线验证（门禁可自动化）。

门禁前提：需要 INFLUXDB_TOKEN 环境变量 + 本地 InfluxDB 服务（localhost:8086）。
无 token / 无 influxdb_client 时整体 skip（不阻塞 CI）。
"""
import os
import time

import pytest

pytest.importorskip("influxdb_client")

if not os.environ.get("INFLUXDB_TOKEN"):
    pytest.skip("INFLUXDB_TOKEN 未注入，跳过 InfluxDB 接线验证", allow_module_level=True)

import config


def test_influxdb_auto_connect_and_process_rss(monkeypatch):
    # 灰度开关翻 True（默认 False）
    monkeypatch.setattr(config, "ENABLE_INFLUXDB_AUTO_CONNECT", True)
    monkeypatch.setattr(config, "ENABLE_INFLUXDB_TIMESERIES", True)

    from nucleus.timeseries_store import influxdb_store as mod
    from nucleus.timeseries_store.influxdb_store import get_influxdb_store

    # 重置单例，使灰度开关在首次获取时生效
    mod._INFLUX_STORE = None

    store = get_influxdb_store()
    # 验收①：连接成功路径 is_available() 返 True
    assert store.is_available() is True, "connect() 后 is_available() 应为 True"

    # 隔离环境 InfoField 可能未就绪 → 固定 RSS 采样，确保 process_rss 点含字段可写
    monkeypatch.setattr(
        store, "_sample_rss",
        lambda: {"process_rss_mb": 1234.5, "system_percent": 42.0})

    before = store.query_count("process_rss", start="-5m")
    store.record_process_rss()
    store.flush()
    # influxdb_client 异步写需短暂落盘方可查询
    time.sleep(3)
    after = store.query_count("process_rss", start="-5m")
    # 验收②：bucket 出现真实 process_rss 测量（非 0）
    assert after > before, "bucket pulse_metrics 未出现 process_rss 测量点"

    # 清理验证点（不静默，失败则测试报错）
    from datetime import datetime, timedelta, timezone
    _stop = datetime.now(timezone.utc)
    _start = _stop - timedelta(minutes=10)
    store._client.delete_api().delete(
        _start.isoformat(), _stop.isoformat(),
        '_measurement="process_rss"', store._bucket, store._org)
    # 复位单例，避免影响其他测试
    mod._INFLUX_STORE = None
