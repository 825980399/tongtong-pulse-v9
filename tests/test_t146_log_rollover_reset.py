# -*- coding: utf-8 -*-
"""★第146批 T146-8：D017 日志轮转误报结案回归测试。

原缺陷：框架自己的 `SafeRotatingFileHandler.doRollover()` 转完后，
下一次 `check_log_integrity` 仍拿「轮转前的大 size」当 prev，算成 size 下降 →
报 truncated（外部截断）。只有恰好落在 120s marker 时间窗内才被复判为 rollover，
一旦 marker 过期/被清，正常轮转就被误报成本该告警的事件。

两条修复互为兜底：
  A. 写 marker 时**同步复位** .log_state.json 的 size/ino → 下一次直接判 ok；
  B. 补充判据：prev.size >= LOG_MAX_BYTES 且 <log>.1 归档存在 → 判 rollover。
"""
import io
import json
import os
import time

import nucleus.logger as _lg
from nucleus.logger import (
    _LOG_STATE_FILE,
    SafeRotatingFileHandler,
    _write_rollover_marker,
    check_log_integrity,
)


def _write_file(path, content):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _read_state(tmp_path):
    with io.open(str(tmp_path / _LOG_STATE_FILE), encoding="utf-8") as f:
        return json.load(f)


def test_doRollover_resets_state_so_next_check_is_ok(tmp_path):
    """判据 A：真实轮转后，下一次完整性检查应是 ok，不得误报 truncated。"""
    log = str(tmp_path / "pulse.log")
    _write_file(log, "x" * 500)
    # 先跑一次，落 baseline 状态（size=500）
    before = check_log_integrity(log_dir=str(tmp_path), log_file=log,
                                 state_path=str(tmp_path / _LOG_STATE_FILE))
    assert before["status"] == "first_run", before

    h = SafeRotatingFileHandler(log, maxBytes=200, backupCount=2, encoding="utf-8")
    h.doRollover()

    after = check_log_integrity(log_dir=str(tmp_path), log_file=log,
                                state_path=str(tmp_path / _LOG_STATE_FILE))
    assert after["status"] == "ok", (
        "轮转后仍被误判为 {} —— 状态未同步复位".format(after["status"]))


def test_state_fingerprint_reset_to_post_rollover_value(tmp_path):
    """判据 A 直接验证：状态文件里的 size 必须是轮转后的值，不是轮转前的旧值。"""
    log = str(tmp_path / "pulse.log")
    _write_file(log, "y" * 400)
    old_state = str(tmp_path / _LOG_STATE_FILE)
    with io.open(old_state, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "file": log, "size": 400,
                   "ino": 1, "mtime": time.time()}, f)

    # 轮转已经发生：当前文件变小，随后写 marker（与 doRollover 的时序一致）
    _write_file(log, "small")
    _write_rollover_marker(str(tmp_path), log + ".1", 400, log)

    st = _read_state(tmp_path)
    assert st.get("size") == len("small"), \
        "状态未复位到轮转后的实际 size：期望 %d，实际 %r" % (len("small"), st.get("size"))


def test_criterion_b_maxsize_plus_archive_means_rollover(tmp_path, monkeypatch):
    """判据 B：无 marker，但 prev.size 达上限且 .1 存在 → 复判 rollover。"""
    import config as _cfg
    monkeypatch.setattr(_cfg, "LOG_MAX_BYTES", 200, raising=False)

    log = str(tmp_path / "pulse.log")
    _write_file(log, "small after rollover")
    state = str(tmp_path / _LOG_STATE_FILE)
    with io.open(state, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "file": log, "size": 999,
                   "mtime": time.time()}, f)
    # 轮转归档确实存在
    _write_file(log + ".1", "archived content")

    res = check_log_integrity(log_dir=str(tmp_path), log_file=log, state_path=state)
    assert res["status"] == "rollover", res
    assert res.get("rollover_archive") == log + ".1", res


def test_criterion_b_does_not_mask_real_truncation(tmp_path, monkeypatch):
    """判据 B 不得掩盖真实外部截断：prev.size 未达上限时仍报 truncated。"""
    import config as _cfg
    monkeypatch.setattr(_cfg, "LOG_MAX_BYTES", 10 * 1024 * 1024, raising=False)

    log = str(tmp_path / "pulse.log")
    _write_file(log, "small")
    state = str(tmp_path / _LOG_STATE_FILE)
    with io.open(state, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "file": log, "size": 999,
                   "mtime": time.time()}, f)
    # 故意也放一个 .1 —— 但 size 没到上限，不该被判成轮转
    _write_file(log + ".1", "archived")

    res = check_log_integrity(log_dir=str(tmp_path), log_file=log, state_path=state)
    assert res["status"] == "truncated", res


def test_missing_file_still_missing(tmp_path):
    """既有语义保持：文件不存在仍判 missing。"""
    log = str(tmp_path / "pulse.log")
    _write_file(log, "content")
    state = str(tmp_path / _LOG_STATE_FILE)
    check_log_integrity(log_dir=str(tmp_path), log_file=log, state_path=state)
    os.remove(log)
    res = check_log_integrity(log_dir=str(tmp_path), log_file=log, state_path=state)
    assert res["status"] == "missing", res


def test_max_bytes_helper_defaults_to_config(monkeypatch):
    """`_log_max_bytes` 应读 config，值非法时返回 0（表示未知，不臆断）。"""
    import config as _cfg
    monkeypatch.setattr(_cfg, "LOG_MAX_BYTES", 1234, raising=False)
    assert _lg._log_max_bytes() == 1234
    monkeypatch.setattr(_cfg, "LOG_MAX_BYTES", None, raising=False)
    assert _lg._log_max_bytes() == 0
