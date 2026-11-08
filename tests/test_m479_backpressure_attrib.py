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


class TestLockWaitProbe:
    """★479：锁等待探针 —— 量清「等锁」是不是瓶颈。

    ★历史背景（必须记住）：`ENABLE_M436_ORGAN_SERIAL` 那把锁**不是 GIL 补偿**，
    它的原始注释写明「原模型对同器官无任何限制 ⇒ 重入竞态（器官内部状态/队列非线程安全）」，
    是为修真实 bug 加的。3.14t 去掉 GIL 后这类竞态**更容易**发生 ⇒ 锁绝不能删。
    本探针只量等待、不改语义。
    """

    def _inst(self, inflight=None):
        import threading as _th
        _o = InfoField.__new__(InfoField)
        _o._organ_concurrent_lock = _th.RLock()
        _o._m479_lw_lock = _th.Lock()
        _o._m479_lock_wait = {}
        _o._layer_organ_inflight = inflight or {}
        return _o

    def test_10_accumulates_count_sum_max(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_LOCK_WAIT_PROBE", True)
        _o = self._inst()
        _o._m479_note_lock_wait("心脏", 12.5)
        _o._m479_note_lock_wait("心脏", 8.0)
        _o._m479_note_lock_wait("胃", 3.2)
        _r = _o._m479_lock_wait
        assert _r["心脏"][0] == 2
        assert abs(_r["心脏"][1] - 20.5) < 1e-6
        assert abs(_r["心脏"][2] - 12.5) < 1e-6      # 最大，不是最后
        assert _r["胃"][0] == 1

    def test_11_switch_off_no_record(self, monkeypatch):
        """★反证：探针关 ⇒ 一条都不记（零开销路径）。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_LOCK_WAIT_PROBE", False)
        _o = self._inst()
        _o._m479_note_lock_wait("心脏", 99.0)
        assert _o._m479_lock_wait == {}

    def test_12_hint_sorted_by_sum_desc(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_LOCK_WAIT_PROBE", True)
        _o = self._inst()
        for _ms in (5.0, 5.0, 5.0):        # 心脏 15ms/3次
            _o._m479_note_lock_wait("心脏", _ms)
        _o._m479_note_lock_wait("胃", 9.0)  # 胃 9ms/1次
        _s = _o._m479_lock_wait_hint()
        assert "锁等待" in _s
        assert _s.index("心脏=15ms/3次") < _s.index("胃=9ms/1次")

    def test_13_hint_off_when_switch_off(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_LOCK_WAIT_PROBE", True)
        _o = self._inst()
        _o._m479_note_lock_wait("心脏", 20.0)
        monkeypatch.setattr(config, "ENABLE_M479_LOCK_WAIT_PROBE", False)
        assert _o._m479_lock_wait_hint() == ""

    def test_14_combined_hint_has_both_sections(self, monkeypatch):
        """在途 + 锁等待 必须同时出现在同一条告警里。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_LOCK_WAIT_PROBE", True)
        monkeypatch.setattr(config, "ENABLE_M479_BACKPRESSURE_ATTRIB", True)
        _o = self._inst({"L1": {"心脏": 7}})
        _o._m479_note_lock_wait("心脏", 33.0)
        _s = _o._m479_inflight_hint("L1")
        assert "在途归因" in _s and "锁等待" in _s

    def test_15_no_lock_wait_still_has_inflight(self, monkeypatch):
        """只有在途、没有锁等待时，告警仍要出现在途部分（不许整段变空）。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_LOCK_WAIT_PROBE", True)
        monkeypatch.setattr(config, "ENABLE_M479_BACKPRESSURE_ATTRIB", True)
        _o = self._inst({"L1": {"心脏": 7}})
        _s = _o._m479_inflight_hint("L1")
        assert "在途归因" in _s
        assert "锁等待" not in _s


class TestLockMustStay:
    """★反证（防回潮）：器官锁**必须还在**，不能被「提速」为名删掉。"""

    def test_20_organ_lock_code_present(self):
        from pathlib import Path
        _p = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) \
            / "nucleus" / "field" / "InfoField.py"
        _src = _p.read_text(encoding="utf-8", errors="replace")
        assert "_m436_organ_lock(organ_name)" in _src, "同器官锁被删了！"
        assert "_lk.acquire()" in _src and "_lk.release()" in _src, \
            "锁的 acquire/release 被删了！"
        assert "def _m436_organ_serial_on" in _src

    def test_21_serial_switch_still_default_on(self):
        """默认必须是「同器官串行」——关掉就退回重入竞态。"""
        import config
        assert bool(getattr(config, "ENABLE_M436_ORGAN_SERIAL", True)) is True

    def test_22_budget_total_still_below_60(self):
        """预算合计判据（test_m418_k3 也有，这里再守一次，防对调时漏算）。"""
        from utils.thread_budget import budget_total
        assert budget_total() < 60, budget_total()

    def test_23_l2_budget_raised_to_10(self):
        from utils.thread_budget import cap
        assert cap("l2", 8) == 8, "L2 应能从 6 提到 8（预算已调 10）"
