# -*- coding: utf-8 -*-
"""T-100c（D017）日志轮转自感知回归测试。

验证：
  1. doRollover 成功后写入唯一轮转标记 .rollover_marker.json；
  2. check_log_integrity 在「size 下降 + 近期轮转标记」时复判为 rollover（而非 truncated）；
  3. rollover 事件写入 log_integrity_events.log（event="rollover"，区别于外部截断）；
  4. 无标记 / 标记过期时，size 下降仍正确判为 truncated（不掩盖真实外部截断）。
"""
import io
import json
import os
import time

import nucleus.logger as _lg
from nucleus.logger import (
    SafeRotatingFileHandler,
    check_log_integrity,
    _write_rollover_marker,
    _read_rollover_marker,
    _LOG_ROLLOVER_MARKER,
    _LOG_STATE_FILE,
    _LOG_INTEGRITY_FILE,
)


def _write_file(path, content):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_doRollover_writes_unique_marker(tmp_path):
    """轮转成功后必须落唯一标记（时间窗内 check_log_integrity 可自感知）。"""
    log = str(tmp_path / "pulse.log")
    _write_file(log, "x" * 500)
    h = SafeRotatingFileHandler(log, maxBytes=200, backupCount=2, encoding="utf-8")
    h.doRollover()
    marker_path = os.path.join(tmp_path, _LOG_ROLLOVER_MARKER)
    assert os.path.isfile(marker_path), "轮转后应写入 .rollover_marker.json"
    data = _read_rollover_marker(str(tmp_path))
    assert isinstance(data, dict)
    assert "ts" in data and "archive" in data
    # 标记为近期：应触发自感知复判
    assert time.time() - float(data["ts"]) <= _lg._ROLLOVER_AWARE_WINDOW_SEC


def test_integrity_reclassifies_truncated_to_rollover(tmp_path):
    """size 下降(copy-truncate 轮转) + 近期轮转标记 → 复判 rollover。"""
    log = str(tmp_path / "pulse.log")
    _write_file(log, "small after rollover")  # 当前小
    state = os.path.join(tmp_path, _LOG_STATE_FILE)
    # 上次指纹：size 远大于当前（轮转把大文件截小了）；不写 ino 以走 truncated 分支
    with io.open(state, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "file": log, "size": 99999,
                   "mtime": time.time()}, f)
    # 写入近期轮转标记
    _write_rollover_marker(str(tmp_path), log + ".1", 99999)

    res = check_log_integrity(log_dir=str(tmp_path), log_file=log,
                              state_path=state)
    assert res["status"] == "rollover", res
    assert res.get("rollover_archive") == log + ".1"

    # 事件留痕：应为 rollover（非外部截断）
    ev_path = os.path.join(tmp_path, _LOG_INTEGRITY_FILE)
    assert os.path.isfile(ev_path)
    lines = [json.loads(x) for x in io.open(ev_path, encoding="utf-8").read().splitlines() if x.strip()]
    assert any(e.get("event") == "rollover" for e in lines), lines


def test_integrity_replaced_reclassified_with_marker(tmp_path):
    """inode 变化(rename 轮转) + 近期轮转标记 → 复判 rollover（非外部替换）。"""
    log = str(tmp_path / "pulse.log")
    _write_file(log, "small")
    state = os.path.join(tmp_path, _LOG_STATE_FILE)
    # 上次 ino 与当前不同（rename 轮转会新建文件 → inode 变化）
    with io.open(state, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "file": log, "size": 99999,
                   "ino": 12345, "mtime": time.time()}, f)
    _write_rollover_marker(str(tmp_path), log + ".1", 99999)

    res = check_log_integrity(log_dir=str(tmp_path), log_file=log, state_path=state)
    assert res["status"] == "rollover", res


def test_integrity_stale_marker_still_truncated(tmp_path):
    """标记过期（远超时间窗）→ 不掩盖真实外部截断，仍判 truncated。"""
    log = str(tmp_path / "pulse.log")
    _write_file(log, "small")
    state = os.path.join(tmp_path, _LOG_STATE_FILE)
    with io.open(state, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "file": log, "size": 99999,
                   "mtime": time.time()}, f)
    # 过期标记
    marker = os.path.join(tmp_path, _LOG_ROLLOVER_MARKER)
    with io.open(marker, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time() - 3600, "archive": log + ".1",
                   "prev_size": 99999}, f)

    res = check_log_integrity(log_dir=str(tmp_path), log_file=log, state_path=state)
    assert res["status"] == "truncated", res


def test_integrity_no_marker_still_truncated(tmp_path):
    """无轮转标记 → size 下降判为 truncated（外部截断）。"""
    log = str(tmp_path / "pulse.log")
    _write_file(log, "small")
    state = os.path.join(tmp_path, _LOG_STATE_FILE)
    with io.open(state, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "file": log, "size": 99999,
                   "mtime": time.time()}, f)
    # 不写任何标记
    res = check_log_integrity(log_dir=str(tmp_path), log_file=log, state_path=state)
    assert res["status"] == "truncated", res


def test_marker_is_not_cleaned_as_log(tmp_path):
    """轮转标记是 .json，不被 _is_log_file 当作日志清理对象。"""
    assert not _lg._is_log_file(_LOG_ROLLOVER_MARKER)
    assert not _lg._is_log_file(_LOG_STATE_FILE)
