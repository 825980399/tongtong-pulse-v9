# -*- coding: utf-8 -*-
"""主线第74批 T1~T4 单元测试：关键 bug 修复与健壮性增强。

覆盖：
- T1：双腿模块 dict.append bug（日志文件为 dict 时静默吞 AttributeError）
- T3：胃 JSON 解析容错（尾随逗号/缺引号键/多余逗号/缺逗号/缺冒号/单引号/内部引号）
- T4：Windows 文件占用（日志轮转 / 冷存 compaction 被占用时不崩溃、指数退避）
"""
import json
import logging
import os
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.parsing.JsonRepair import parse_with_repair, classify_json_error  # noqa: E402
import nucleus.logger as logger_mod  # noqa: E402
from nucleus.logger import SafeRotatingFileHandler  # noqa: E402
import organs.motor.PulseLegs as legs_mod  # noqa: E402


# ===================== T1：双腿 dict.append =====================
def _make_legs():
    return SimpleNamespace(_log=lambda *a, **k: None)


def test_t1_dict_log_no_append_error(tmp_path):
    """日志文件为 dict 时，写入不应抛 AttributeError，且转为 list 保留数据点。"""
    fake = tmp_path / "legs_learn_log.json"
    fake.write_text('{"stale": "dict"}', encoding="utf-8")
    inst = _make_legs()
    with mock.patch.object(legs_mod.os.path, "join", return_value=str(fake)):
        legs_mod.PulseLegs._write_learn_log(inst, "方向", "来源", "预览")
    data = json.loads(fake.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert data[-1]["direction"] == "方向"


def test_t1_normal_list_log_append(tmp_path):
    """正常 list 日志继续追加，不丢历史。"""
    fake = tmp_path / "legs_learn_log.json"
    fake.write_text('[{"direction": "old"}]', encoding="utf-8")
    inst = _make_legs()
    with mock.patch.object(legs_mod.os.path, "join", return_value=str(fake)):
        legs_mod.PulseLegs._write_learn_log(inst, "新方向", "来源", "预览")
    data = json.loads(fake.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[-1]["direction"] == "新方向"


def test_t1_no_file_init_empty(tmp_path):
    """文件不存在时初始化为空 list，正常写入单条。"""
    fake = tmp_path / "legs_learn_log.json"
    inst = _make_legs()
    with mock.patch.object(legs_mod.os.path, "join", return_value=str(fake)):
        legs_mod.PulseLegs._write_learn_log(inst, "方向", "来源", "预览")
    data = json.loads(fake.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1


def test_t1_corrupt_nonjson_reset(tmp_path):
    """日志为非法内容（既非 list 也非 dict）时重置为空而非崩溃。"""
    fake = tmp_path / "legs_learn_log.json"
    fake.write_text('not json at all <<<', encoding="utf-8")
    inst = _make_legs()
    with mock.patch.object(legs_mod.os.path, "join", return_value=str(fake)):
        legs_mod.PulseLegs._write_learn_log(inst, "方向", "来源", "预览")
    data = json.loads(fake.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert data[-1]["direction"] == "方向"


# ===================== T3：胃 JSON 解析容错 =====================
T3_SAMPLES = [
    ('{"a":1,}', "尾随逗号"),
    ('{a: 1}', "缺引号键"),
    ('{"a":1,, "b":2}', "多余逗号"),
    ('{"a":1 "b":2}', "缺逗号"),
    ('{"a" 1}', "缺冒号"),
    ("{'a': 'b'}", "单引号"),
    ('{"a": "he said "hi""}', "内部未转义引号"),
    ('{"功能": "x", "关键步骤": "y",}', "尾随逗号(中文键)"),
    ('{"功能" "步骤"}', "缺冒号(中文键)"),
    ('[1,2,3,]', "数组尾随逗号"),
]


@pytest.mark.parametrize("text,label", T3_SAMPLES)
def test_t3_repair_covers_common_errors(text, label):
    obj, method = parse_with_repair(text)
    assert obj is not None, f"{label} 未修复: {text}"
    assert method == "repair"


def test_t3_classify_json_error_missing_comma():
    try:
        json.loads('{"a":1 "b":2}')
    except json.JSONDecodeError as e:
        assert classify_json_error(e) == "缺逗号"


def test_t3_classify_json_error_trailing_comma():
    try:
        json.loads('{"a":1,}')
    except json.JSONDecodeError as e:
        # 尾随逗号场景标准错误归为「属性名无引号」，均属可接受分类标签
        assert classify_json_error(e) in ("尾随逗号", "属性名无引号")


# ===================== T4：Windows 文件占用 =====================
def test_t4_log_rollover_permissionerror_no_crash(tmp_path):
    """日志轮转遇 PermissionError(WinError 32) 应优雅降级，不抛异常。"""
    logf = tmp_path / "pulse.log"
    logf.write_text("x" * 20, encoding="utf-8")
    h = SafeRotatingFileHandler(str(logf), maxBytes=10, backupCount=2)
    with mock.patch.object(logging.handlers.RotatingFileHandler, "doRollover",
                           side_effect=PermissionError("WinError 32")):
        with mock.patch.object(logger_mod, "time") as mt:
            mt.sleep = mock.MagicMock()
            try:
                h.doRollover()
            except Exception as e:
                pytest.fail(f"doRollover 抛异常(应优雅降级): {e}")
            # 至少尝试了退避重试
            assert mt.sleep.called


def test_t4_log_rollover_exponential_backoff(tmp_path):
    """日志轮转退避应为指数增长（第2次 > 第1次）。"""
    logf = tmp_path / "pulse.log"
    logf.write_text("x" * 20, encoding="utf-8")
    h = SafeRotatingFileHandler(str(logf), maxBytes=10, backupCount=2)
    sleeps = []
    with mock.patch.object(logging.handlers.RotatingFileHandler, "doRollover",
                           side_effect=PermissionError("WinError 32")):
        with mock.patch.object(logger_mod, "time") as mt:
            mt.sleep = lambda s: sleeps.append(s)
            try:
                h.doRollover()
            except Exception:
                pass
    # 至少两次退避，且呈递增（0.2, 0.4, ...）
    assert len(sleeps) >= 2
    assert sleeps[1] > sleeps[0]


def test_t4_compaction_permissionerror_no_crash(tmp_path):
    """冷存 compaction 删除旧目录遇 PermissionError 应被捕获返回 dict，不抛。"""
    import pyarrow as pa
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    pool = PulseNodePool()
    pool._cold_storage_enabled = True
    with mock.patch.object(pool, "count_cold_parquet_files", return_value=30), \
         mock.patch.object(pool, "_cold_compaction_adaptive_enabled", return_value=False), \
         mock.patch.object(pool, "_read_cold_partition_tolerant",
                           return_value=(pa.table({"node_id": ["a"], "evol_level": ["L1"]}), [])), \
         mock.patch.object(pool, "_cold_parquet_dir", return_value=str(tmp_path)), \
         mock.patch("shutil.rmtree",
                    side_effect=PermissionError("WinError 32")), \
         mock.patch("os.rename",
                    side_effect=PermissionError("WinError 32")), \
         mock.patch("nucleus.mnemosyne.PulseNodePool.time.sleep") as _sl:
        _sl.side_effect = lambda *a, **k: None
        try:
            result = pool.compact_cold_storage()
        except Exception as e:
            pytest.fail(f"compaction 抛异常(应优雅降级): {e}")
    assert isinstance(result, dict)
    assert "success" in result


def test_t4_compaction_disabled_returns_gracefully(tmp_path):
    """冷存未启用时 compaction 直接返回，不抛。"""
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    pool = PulseNodePool()
    pool._cold_storage_enabled = False
    result = pool.compact_cold_storage()
    assert isinstance(result, dict)
    assert result.get("success") is False


def test_t4_compaction_high_load_paused(tmp_path):
    """高负载时 compaction 自适应暂停，返回不抛。"""
    from nucleus.mnemosyne.PulseNodePool import PulseNodePool

    pool = PulseNodePool()
    pool._cold_storage_enabled = True
    with mock.patch.object(pool, "_cold_compaction_adaptive_enabled", return_value=True), \
         mock.patch.object(pool, "_get_system_load_level", return_value="high"):
        result = pool.compact_cold_storage()
    assert isinstance(result, dict)
    assert result.get("reason") == "high_load_paused"
