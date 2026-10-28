# -*- coding: utf-8 -*-
"""★380 刀6 · 调度面去重（P2）

票面：TimeCore 已托管 32 个任务，与器官内 Timer 并存，双调度面重复轮询。

T0 结论：**没有重复**。两类件语义不同 ——
  · TimeCore 表里的项 = 周期 tick（到点就发，interval 固定）
  · 器官内 `threading.Timer` = 一次性相位（心跳对齐 / 单次延迟 / 超时熔断）
迁移会把"一次性"变成"周期性" ⇒ 行为变。故**不迁**，改为钉守卫：
任何 Timer 的回调不得发射 TimeCore 已登记的 event_type。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus._silent_except import silent_exc  # noqa: E402

_SCAN_DIRS = ("organs", "nucleus", "base", "utils")


def _py_files():
    for _d in _SCAN_DIRS:
        _base = os.path.join(ROOT, _d)
        if not os.path.isdir(_base):
            continue
        for _dp, _dn, _fn in os.walk(_base):
            _dn[:] = [x for x in _dn if x != "__pycache__" and not x.startswith(".bak")]
            for _f in _fn:
                if _f.endswith(".py"):
                    yield os.path.join(_dp, _f)


def test_m380_d6_managed_task_count_is_32():
    _cfg = getattr(config, "TIMECORE_SCHEDULE_CONFIG", {}) or {}
    assert len(_cfg) == 32, "托管任务数变了（%d），清单注释需同步" % len(_cfg)


def test_m380_d6_no_timer_reemits_managed_event_type():
    """★守卫：Timer 回调里不许再发 TimeCore 已登记的事件（那才是真重复）。"""
    _managed = {str((v or {}).get("event_type") or "")
                for v in (getattr(config, "TIMECORE_SCHEDULE_CONFIG", {}) or {}).values()}
    _managed.discard("")
    _bad = []
    for _p in _py_files():
        try:
            with io.open(_p, encoding="utf-8", errors="ignore") as _f:
                _src = _f.read()
        except OSError as _oe:
            silent_exc(_oe, where="test_m380_d6.read_src")
            continue
        if "Timer(" not in _src:
            continue
        for _m in re.finditer(r"Timer\(([^\n]{0,400})", _src):
            _blk = _m.group(1)
            for _et in _managed:
                if _et and _et in _blk:
                    _bad.append((os.path.relpath(_p, ROOT), _et))
    assert not _bad, "Timer 回调里出现托管事件（重复调度面）: %s" % _bad[:5]


def test_m380_d6_inventory_comment_present():
    with io.open(os.path.join(ROOT, "config.py"), encoding="utf-8") as _f:
        _src = _f.read()
    assert "★380刀6（T-380-调度面去重）" in _src
    assert "一次性相位" in _src
