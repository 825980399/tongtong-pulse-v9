# -*- coding: utf-8 -*-
"""★378 批盲测件 · 两份台账「首次不存在」留痕级别（P2）

任务书 §八：`reload_stats.json` / `live_gate_state.json` 首次不存在时**一次 INFO 初始化**，
后续不再每拍 DEBUG。

T0 实测（写进报告）：**每拍重复已经修过了** —— 367 E10 的 `nucleus/absence_notice.py`
已实现「首次/状态变化才留痕」且两处都已接线，现网 `logs/pulse.log` 里两条各只 1 行。
真缺口只剩**级别**：任务书要 INFO，现状是 DEBUG。本件锁住改后行为。
"""
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus import absence_notice  # noqa: E402
from nucleus.mnemosyne import live_gate as lg  # noqa: E402


def _hits(caplog, needle="台账不存在"):
    return [r for r in caplog.records if needle in r.getMessage()]


def test_m378_d7_absent_state_logs_info_once(caplog, tmp_path):
    """★核心：首次 INFO 一条，第二次**一条都不打**（同一事实不重复）。"""
    absence_notice.reset()
    try:
        _p = str(tmp_path / "no_such_state.json")
        with caplog.at_level(logging.INFO, logger=lg._module_logger.name):
            lg._load_state(_p)
            _first = _hits(caplog)
            caplog.clear()
            lg._load_state(_p)
            _second = _hits(caplog)
        assert len(_first) == 1, "首次应留痕一条，实际 %d" % len(_first)
        assert _first[0].levelno == logging.INFO, "级别必须是 INFO（任务书要的是初始化语义）"
        assert len(_second) == 0, "同一事实重复不得再打，实际 %d" % len(_second)
    finally:
        absence_notice.reset()


def test_m378_d7_present_then_absent_still_logs(caplog, tmp_path):
    """反控：**曾存在又被清掉**必须留痕（这是事故信号，不能一起静掉）。"""
    absence_notice.reset()
    try:
        _p = tmp_path / "state.json"
        _p.write_text("{}", encoding="utf-8")
        with caplog.at_level(logging.INFO, logger=lg._module_logger.name):
            lg._load_state(str(_p))          # 存在 ⇒ note_present
            os.remove(str(_p))
            caplog.clear()
            lg._load_state(str(_p))          # 消失 ⇒ note_absent 返回 True
            _after = _hits(caplog)
        assert len(_after) == 1, "台账消失必须留痕，实际 %d" % len(_after)
    finally:
        absence_notice.reset()


def test_m378_d7_no_debug_left_at_both_sites():
    """反向先红：两处首条留痕都不许回落到 DEBUG（否则 INFO 初始化语义丢了）。"""
    import io
    for _rel, _needle in (
            ("nucleus/mnemosyne/live_gate.py",
             '_module_logger.debug("[350开闸] 台账不存在'),
            ("organs/core/PulseMetricsCollector.py",
             ('self._log(LogLevel.DEBUG,\n'
              '                              "[350开闸] 回载统计尚不存在'))):
        with io.open(os.path.join(ROOT, _rel), encoding="utf-8") as _f:
            _s = _f.read()
        assert _needle not in _s, "%s 仍有 DEBUG 首条留痕" % _rel
        assert "★378刀7" in _s, "%s 缺 378刀7 标记" % _rel
