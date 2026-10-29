# -*- coding: utf-8 -*-
"""389刀2：眼睛帧持有卸载 —— 历史归档省略重负载。

T0 实测（13:00 槽位）
====================
`PulseEyes.py:305`（`self._camera_cap.read()`）在 tracemalloc 里是
**886.9MB / 2018 对象**（≈450KB/帧），而 `InfoField._history` 上限是
`config.PULSE["info_field_max_history"]` = **2000** —— 数量级完全吻合。

持有链：`publish` 第 4 步 `dict(pulse.get("payload", {}))` 浅拷贝，
`raw_data`（整帧 numpy/bytes）被历史原样钉住。

本测试钉住四件事：
  1. 帧**不再**进入历史（结构判据：history 里 0 个 bytes/重负载）；
  2. live 脉冲**一字不改**（视觉皮层拿的是 live，不是历史副本）；
  3. 小字段与事件元数据照旧（事件溯源语义不变）；
  4. 开关关 ⇒ 逐字退回旧行为（`dict(payload)` 浅拷贝）。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as _cfg  # noqa: E402
from nucleus.field.InfoField import InfoField  # noqa: E402

SRC = os.path.join(ROOT, "nucleus", "field", "InfoField.py")
FRAME_BYTES = 450 * 1024          # 实测 ≈450KB/帧


class _FakeNumpyArray:
    """最小 numpy 替身：只提供 `nbytes`（本刀判定只认这个属性）。"""

    def __init__(self, nbytes):
        self.nbytes = nbytes
        self.shape = (480, 640, 3)


def _pulse(payload, event_type="eye.stream_frame", pid="p389"):
    """★每个脉冲都要唯一 `pulse_id`：InfoField 有幂等去重（同 id 第二条会被吞）。"""
    return {"pulse_id": pid, "event_type": event_type, "priority": 3,
            "layer": "L3", "payload": payload}


def _no_storm(field):
    """关掉风暴检测：本测试要连发 2000 条，风暴限流会把它们聚合成一条
    （500 脉冲/秒即触发）——那是限流语义，与本刀要验的历史语义无关。"""
    field._check_pulse_storm = lambda: {"in_storm": False}


def _history_payloads(field):
    return [h.get("payload") or {} for h in list(field._history)]


def test_m389_d2_frame_not_retained_in_history():
    """★核心：450KB 的帧不再被历史持有（结构判据，不依赖 RSS 抖动）。"""
    f = InfoField(max_history=50)
    f.publish(_pulse({"raw_data": b"\x00" * FRAME_BYTES, "frame_seq": 1}))

    _hs = _history_payloads(f)
    assert len(_hs) == 1, "事件元数据必须照旧入档"
    _raw = _hs[0].get("raw_data")
    assert isinstance(_raw, dict) and _raw.get("_elided") == "heavy", _raw
    assert _raw.get("nbytes") == FRAME_BYTES
    # 结构判据：历史里**一个** bytes/bytearray 都不许剩
    for _p in _hs:
        for _v in _p.values():
            assert not isinstance(_v, (bytes, bytearray, memoryview)), \
                "历史里仍有重负载：%r" % (type(_v),)


def test_m389_d2_live_pulse_untouched():
    """★核心：live 脉冲原样（视觉皮层靠它拿帧，改链路等于改它的输入源）。"""
    f = InfoField(max_history=50)
    _frame = b"\x01" * FRAME_BYTES
    _pl = _pulse({"raw_data": _frame, "frame_seq": 7})
    f.publish(_pl)

    assert _pl["payload"]["raw_data"] is _frame, "live payload 被替换了"
    assert len(_pl["payload"]["raw_data"]) == FRAME_BYTES


def test_m389_d2_small_fields_kept():
    """小字段与元数据必须原样保留（事件溯源要能回答"谁在何时发了什么"）。"""
    f = InfoField(max_history=50)
    f.publish(_pulse({"raw_data": b"\x00" * FRAME_BYTES,
                      "frame_seq": 42, "width": 640, "height": 480,
                      "timestamp": 1.5, "note": "x" * 100}))
    _p = _history_payloads(f)[0]
    assert _p["frame_seq"] == 42 and _p["width"] == 640 and _p["height"] == 480
    assert _p["timestamp"] == 1.5 and _p["note"] == "x" * 100
    assert _p["raw_data"]["_elided"] == "heavy"


def test_m389_d2_numpy_like_elided_by_nbytes():
    """ndarray 走 `nbytes`（本刀**不 import numpy**，只用属性）。"""
    f = InfoField(max_history=50)
    _arr = _FakeNumpyArray(FRAME_BYTES)
    f.publish(_pulse({"raw_data": _arr, "frame_seq": 1}))
    _raw = _history_payloads(f)[0]["raw_data"]
    assert _raw["_elided"] == "heavy" and _raw["type"] == "_FakeNumpyArray"
    assert _raw["nbytes"] == FRAME_BYTES


def test_m389_d2_switch_off_restores_old_behaviour(monkeypatch):
    """开关关 ⇒ 逐字退回旧行为（浅拷贝、原值入档）。"""
    monkeypatch.setitem(_cfg.PULSE, "info_field_history_elide_heavy", False)
    f = InfoField(max_history=50)
    _frame = b"\x02" * FRAME_BYTES
    f.publish(_pulse({"raw_data": _frame}))
    assert _history_payloads(f)[0]["raw_data"] is _frame, "关灯后必须与改前一致"
    assert f._m389_elided_bytes == 0


def test_m389_d2_min_bytes_is_configurable(monkeypatch):
    """阈值可配：调到 1MB 后，450KB 的帧不再被省略（避免一刀切）。"""
    monkeypatch.setitem(_cfg.PULSE, "info_field_history_elide_min_bytes",
                        1024 * 1024)
    f = InfoField(max_history=50)
    f.publish(_pulse({"raw_data": b"\x03" * FRAME_BYTES}))
    assert isinstance(_history_payloads(f)[0]["raw_data"], bytes), "低于阈值不该被省略"


def test_m389_d2_non_dict_payload_keeps_only_type():
    """非 dict payload：历史只留型别（否则整个对象被浅拷贝钉住）。"""
    f = InfoField(max_history=50)
    f.publish(_pulse([1, 2, 3]))
    _p = _history_payloads(f)[0]
    assert _p.get("_elided") == "non_dict_payload"
    assert _p.get("type") == "list"


def test_m389_d2_history_length_unchanged_and_bounded():
    """历史语义不变：条数照旧记、上限照旧是 deque 上限（2000）。"""
    f = InfoField(max_history=2000)
    _no_storm(f)
    for _i in range(2100):
        f.publish(_pulse({"raw_data": b"\x00" * 1024, "frame_seq": _i},
                         event_type="eye.stream_frame", pid="p389-%d" % _i))
    assert len(f._history) == 2000, len(f._history)
    assert f._history.maxlen == 2000


def test_m389_d2_counters_and_stats():
    """计数可取证：2000 帧 × 450KB ⇒ bytes 累加 ≈900MB（改前这些字节全在堆里）。"""
    f = InfoField(max_history=2000)
    _no_storm(f)
    for _i in range(2000):
        f.publish(_pulse({"raw_data": b"\x00" * FRAME_BYTES, "frame_seq": _i},
                         pid="p389-%d" % _i))
    assert f._m389_elided_calls == 2000, f._m389_elided_calls
    assert f._m389_elided_items == 2000
    assert f._m389_elided_bytes == 2000 * FRAME_BYTES
    _st = f.get_stats()["history_elide"]
    assert _st["enabled"] is True
    assert _st["bytes"] == 2000 * FRAME_BYTES
    assert _st["min_bytes"] == 65536


def test_m389_d2_callsite_uses_helper():
    """★防回退：收集点必须走省略口，不许退回裸 `dict(payload)`。"""
    _s = open(SRC, encoding="utf-8").read()
    assert "self._m389_elide_heavy(pulse.get(\"payload\", {}))" in _s
    _i = _s.index('"payload": (self._m389_elide_heavy')
    _blk = _s[_i:_i + 400]
    assert '"_elided"' in _s, "占位描述必须可识别"
    assert "dict(pulse.get(\"payload\", {}))" in _blk, "关灯分支要在同一表达式里"


def test_m389_d2_reason_is_documented():
    """理由留证：887MB/2018 帧 与 2000 条上限的对应关系写在代码里。"""
    _s = open(SRC, encoding="utf-8").read()
    assert "★389刀2" in _s
    _i = _s.index("★389刀2（T-389-帧持有）：历史归档省略重负载")
    _blk = _s[_i:_i + 4000]
    assert "886.9MB" in _blk or "887MB" in _blk, "必须写明实测持有量"
    assert "2000" in _blk, "必须写明历史上限"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
