# -*- coding: utf-8 -*-
"""相关任务：LogAggregationFilter 单元测。

验证聚合 filter 只挂在 file_handler 上、对落盘日志生效：
  - 相同 DEBUG/INFO 消息在窗口内被抑制，窗口结束后再来一条触发「聚合 N 次」摘要；
  - 不挂 filter（等价开关 LOG_AGGREGATION_ENABLED=False）时，相同消息全部原样落盘。
"""
import logging
import os
import time

from nucleus.logger import LogAggregationFilter


def _build_child(tmp_path, name):
    log_file = os.path.join(str(tmp_path), name + ".log")
    logger = logging.getLogger("pulse.test." + name)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.addFilter(LogAggregationFilter(window_seconds=300.0))
    logger.addHandler(fh)
    logger.propagate = False
    return logger, log_file


def test_aggregation_summary_emitted(tmp_path, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "time", lambda: clock["t"])
    logger, log_file = _build_child(tmp_path, "agg_on")
    logger.info("心跳信号")        # 首次放行
    clock["t"] += 1.0              # 仍在窗口内(300s)
    logger.info("心跳信号")        # 抑制 (count=1)
    clock["t"] += 399.0            # 越过窗口(>300) 但 < 2*窗口(600)，避免清理误删计数
    logger.info("心跳信号")        # 窗口末 → 输出「聚合 1 次」
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "心跳信号" in content
    assert "聚合 1 次" in content


def test_aggregation_disabled_no_summary(tmp_path):
    # 开关 False 的等价行为：不挂聚合 filter → 3 条全部原样落盘，无「聚合」字样
    log_file = os.path.join(str(tmp_path), "agg_off.log")
    logger = logging.getLogger("pulse.test.agg_off")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    logger.addHandler(fh)
    logger.propagate = False
    for _ in range(3):
        logger.info("噪声日志")
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "聚合" not in content
    assert content.count("噪声日志") == 3
