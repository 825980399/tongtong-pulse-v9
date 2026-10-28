# -*- coding: utf-8 -*-
"""386刀2：眼睛帧流脉冲采样（organs/senses/PulseEyes.py）的门禁用例。

★本刀的核心取舍（不是"没做完"，是**刻意**）：
  票面目标「占比 <30%」按 20261029 实测需 **N=20**（稳态 21.8 秒一帧）⇒
  人脸检测事实上失效。默认取 **N=3**（稳态 3.3 秒一帧，占比 70.8%）。
"""

import io
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
from organs.senses.PulseEyes import PulseEyes  # noqa: E402

EYES_SRC = os.path.join(ROOT, "organs", "senses", "PulseEyes.py")
CORTEX_SRC = os.path.join(ROOT, "organs", "senses", "PulseVisualCortex.py")


def _mk(n: int, bootstrap: int = 30):
    """只造判定所需的最小实例（不启动摄像头/线程）。"""
    _o = PulseEyes.__new__(PulseEyes)
    _o._frame_seq = 0
    _o._m386_pulse_sample_every = types.MethodType(lambda _s: n, _o)
    _o._m386_bootstrap_frames = types.MethodType(lambda _s: bootstrap, _o)
    return _o


def _emits(o, seqs):
    """给定帧号序列，返回发脉冲的次数。"""
    _n = 0
    for _s in seqs:
        o._frame_seq = _s
        if o._m386_should_emit_pulse():
            _n += 1
    return _n


def test_m386_d2_off_means_zero_regression():
    """★零回归路径：N=1 ⇒ **每一帧都发**（与改动前逐字一致）。"""
    _o = _mk(1)
    assert _emits(_o, range(1, 501)) == 500


def test_m386_d2_bootstrap_window_is_full_speed():
    """★功能保全：启动窗口内（默认 30 帧）**不采样** —— 对齐视觉皮层的
    「前 30 帧内累计 2 帧人脸 ⇒ 立刻打招呼」。若这里也采样，快速启动确认
    会被拖成几分钟，等于废掉功能。"""
    _o = _mk(20)                                  # 采样很狠
    assert _emits(_o, range(1, 31)) == 30, "启动窗口必须全速"


def test_m386_d2_steady_state_is_one_over_n():
    """稳态：每 N 帧 1 次（含启动窗口的 30 帧）。"""
    _o = _mk(3)
    _total = _emits(_o, range(1, 201))
    assert _total == 30 + (170 // 3), _total
    # 稳态段逐帧核对：只有 seq % 3 == 0 才发
    _o2 = _mk(3)
    for _s in range(31, 61):
        _o2._frame_seq = _s
        assert _o2._m386_should_emit_pulse() == (_s % 3 == 0), _s


def test_m386_d2_publish_is_inside_the_skip_branch():
    """★核心（票面「帧流不再落 L3 主队列」）：publish 必须**只在 else 分支**执行。

    判据之所以用源码断言：`_stream_loop` 要起线程和摄像头，无法在单测里跑；
    而"publish 有没有被采样包住"恰好是**结构**问题，直接看源码最可靠。
    """
    _s = io.open(EYES_SRC, encoding="utf-8").read()
    assert "if not self._m386_should_emit_pulse():" in _s
    _i = _s.index("if not self._m386_should_emit_pulse():")
    _j = _s.index("self.info_field.publish(self.pulse_core.emit(")
    assert _i < _j, "publish 必须在采样判定之后（被 else 包住）"
    # 两者之间必须有 else，且 publish 缩进比 if 深
    _between = _s[_i:_j]
    assert "else:" in _between
    _line = _s.split("\n")[_s[:_j].count("\n")]
    assert len(_line) - len(_line.lstrip()) >= 32, "publish 应在 if/else 内层"


def test_m386_d2_skipped_frames_still_counted():
    """跳过的帧要**留痕**（`_publish_skipped`）并进统计日志 —— 否则"没发"
    与"采集不到"读数一样（铁律276）。"""
    _s = io.open(EYES_SRC, encoding="utf-8").read()
    assert "_publish_skipped = 0" in _s
    assert "_publish_skipped += 1" in _s
    assert "采样跳过={_publish_skipped}" in _s, "统计日志必须带采样跳过数"


@pytest.mark.parametrize("key,default", [
    ("EYES_STREAM_PULSE_SAMPLE_EVERY", 3),
    ("EYES_STREAM_BOOTSTRAP_FRAMES", 30),
])
def test_m386_d2_config_defaults(key, default):
    assert getattr(config, key) == default


def test_m386_d2_bootstrap_matches_visual_cortex_window():
    """★跨模块耦合守卫：这里的启动窗口必须**等于**视觉皮层 `_on_stream_frame`
    的「前 30 帧」窗口。

    两处若不同步，启动确认会静默变慢。改任一侧都必须同时改另一侧。
    """
    _c = io.open(CORTEX_SRC, encoding="utf-8").read()
    assert "self._frame_count <= 30" in _c, "视觉皮层的启动窗口不再是 30 ⇒ 同步改配置"
    assert int(getattr(config, "EYES_STREAM_BOOTSTRAP_FRAMES", 30)) == 30


def test_m386_d2_payload_not_slimmed():
    """采样**只减发射**，不瘦身 payload —— 视觉皮层拿到的字段一个都不能少
    （`raw_data` 是它的输入源，去掉等于改业务逻辑）。"""
    _s = io.open(EYES_SRC, encoding="utf-8").read()
    _i = _s.index("self.info_field.publish(self.pulse_core.emit(")
    _blk = _s[_i:_i + 900]
    for _f in ("raw_data", "frame_seq", "timestamp", "width", "height"):
        assert _f in _blk, "payload 缺字段 %s" % _f
    assert 'layer="L3"' in _blk or "layer='L3'" in _blk, "层位未改（仍 L3）"


def test_m386_d2_target_needs_n20_is_documented():
    """★取舍留证：达标 <30% 需要 N≈20（稳态约 20 秒一帧），故默认**不**达标。

    这条用例把"为什么默认 N=3 而不是 N=20"钉在代码里，防止后人为了对齐票面
    数字直接把 N 调成 20，把眼睛功能调没了还以为是在"完成任务书"。
    """
    _o = _mk(20)
    _steady = _o._m386_pulse_sample_every()
    assert _steady == 20
    # 稳态下每 20 帧才发 1 次 ⇒ 以 2fps 的采集速率计约 10~22 秒一帧
    _o._frame_seq = 100
    assert _o._m386_should_emit_pulse() is True      # 100 % 20 == 0
    _o._frame_seq = 101
    assert _o._m386_should_emit_pulse() is False
    # 默认档下功能可用：稳态 3 帧一次
    _d = _mk(int(getattr(config, "EYES_STREAM_PULSE_SAMPLE_EVERY", 3)))
    assert _d._m386_pulse_sample_every() == 3
