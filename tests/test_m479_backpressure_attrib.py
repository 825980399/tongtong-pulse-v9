"""★479收尾：背压归因探针的门控单测。

背景：实测 L1 队列 1788/500、L2 达硬顶 1200/300、累计拒绝 2400 次，
但日志只说「队列满了」，说不出「谁堵的」。而 `_layer_organ_inflight`
（P2-87 按层·按器官在途计数）一直在采集却从未输出。
本模块把它接进告警，本测试守住三件事：
  ① 有数据时格式正确、按在途数降序；
  ② 没数据 / 开关关闭 / 缺锁 时**必须返回空串**（逐字回滚，不改告警内容）；
  ③ L1 线程数已从 4 提到预算上限 6（配置契约防回潮）。
"""
from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.field.InfoField import InfoField


def _inst(inflight: dict, lock=...):
    _o = InfoField.__new__(InfoField)
    _o._organ_concurrent_lock = threading.RLock() if lock is ... else lock
    _o._layer_organ_inflight = inflight
    return _o


class TestInflightHint:
    def test_01_formats_top3_desc(self):
        _o = _inst({"L1": {"心脏": 12, "动机循环": 8, "胃": 3, "双腿": 1}})
        _s = _o._m479_inflight_hint("L1")
        assert "在途归因" in _s
        assert "合计=24" in _s
        assert "心脏=12" in _s
        # 降序：心脏 12 > 动机循环 8 > 胃 3；第四名被截断
        assert _s.index("心脏=12") < _s.index("动机循环=8") < _s.index("胃=3")
        assert "双腿=1" not in _s

    def test_02_empty_layer_returns_blank(self):
        _o = _inst({"L1": {"心脏": 1}})
        assert _o._m479_inflight_hint("L2") == ""

    def test_03_empty_all_returns_blank(self):
        _o = _inst({})
        assert _o._m479_inflight_hint("L1") == ""

    def test_04_none_map_returns_blank(self):
        _o = _inst(None)
        assert _o._m479_inflight_hint("L1") == ""

    def test_05_switch_off_returns_blank(self, monkeypatch):
        """★反证：开关关闭 ⇒ 空串（告警逐字回到改前）。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_BACKPRESSURE_ATTRIB", False)
        _o = _inst({"L1": {"心脏": 5}})
        assert _o._m479_inflight_hint("L1") == ""

    def test_06_missing_lock_returns_blank(self):
        """容错：没注入锁时不得抛异常，返回空串。"""
        _o = _inst({"L1": {"心脏": 5}}, lock=None)
        assert _o._m479_inflight_hint("L1") == ""

    def test_07_top_param_respected(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_BACKPRESSURE_ATTRIB", True)
        _o = _inst({"L1": {"a": 5, "b": 4, "c": 3, "d": 2, "e": 1}})
        _s = _o._m479_inflight_hint("L1", top=2)
        assert "a=5" in _s and "b=4" in _s
        assert "c=3" not in _s
        assert "top2" in _s

    def test_08_switch_on_has_data(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_BACKPRESSURE_ATTRIB", True)
        _o = _inst({"L1": {"心脏": 5}})
        assert "心脏=5" in _o._m479_inflight_hint("L1")


class TestWiring:
    """接线防回潮：两处告警必须真的调用它。"""

    def test_10_both_warn_paths_call_hint(self):
        from pathlib import Path
        _p = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) \
            / "nucleus" / "field" / "InfoField.py"
        _src = _p.read_text(encoding="utf-8", errors="replace")
        assert _src.count("def _m479_inflight_hint") == 1
        # ① 超限放行 ② 拒绝，各一处
        assert _src.count("self._m479_inflight_hint(layer)") == 2

    def test_11_config_l1_threads_raised(self):
        """L1 线程数：4 → 6（用满 thread_budget 的 l1 上限）。"""
        import config
        assert int(config.PULSE_LAYER.get("l1_threads", 0)) == 6

    def test_12_thread_budget_cap_allows_6(self):
        """预算表必须容得下 6，否则 config 改了也会被收敛回去（假修复）。"""
        from utils.thread_budget import cap
        assert int(cap("l1", 6)) >= 6


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
