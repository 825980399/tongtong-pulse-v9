# -*- coding: utf-8 -*-
"""374 刀9 单测：线程引导滞留观测装置（烛微甲档装置）。

票 `T-373-线程引导滞留观测-1`（P2 · 374 批 D 组观测面）。

【票面两条硬约束，本件逐条钉住】
· 不能用 `threading.Timer` 做超时（Timer 自己也要 Thread.start()，引导期一起卡死）；
· 不能用 `is_alive()` 断言引导期（`start()` 返回前恒 False，分不清"没轮到"与"卡住"）。
判据只用 `thread.ident is None`：ident 由 OS 线程真正开始执行 bootstrap 时写入。

验七件事：① 落差取值（拿不到 OS 线程数 ⇒ None，不填 0）；② 「突增≥1 且持续」
状态机（首次建基线不告警／单点不告警／连续两点告警／回落清零保基线／None 不推进）；
③ 未引导线程超时即判滞留；④ **运行中卡住的线程不误判**（关键反控）；
⑤ 报过不刷屏、快照有界；⑥ 模块内无 Timer / 无 is_alive（硬约束机器化）；
⑦ main.py 启动期真预建（防"写了没人调"）。
"""
import os
import threading
import time
import tokenize

import pytest

import nucleus.thread_guard as tg

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GUARD = os.path.join(_ROOT, "nucleus", "thread_guard.py")


@pytest.fixture(autouse=True)
def _clean():
    tg.reset_state()
    yield
    tg.reset_state()


def _code_tokens(path):
    """只看**代码** token（注释/字符串一律排除，免得文档串里的词被当成用到了）。"""
    _out = []
    with open(path, "rb") as _f:
        for _tk in tokenize.tokenize(_f.readline):
            if _tk.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            _out.append(_tk.string)
    return _out


# ------------------------------------------------- ① 落差取值（拿不到 ≠ 0）
def test_01_gap_delta_none_when_os_unknown():
    assert tg.gap_delta(None) is None
    assert tg.gap_delta(None, 5) is None


def test_02_gap_delta_subtracts_python_side():
    assert tg.gap_delta(10, 7) == 3
    assert tg.gap_delta(4, 9) == -5
    # 不传 py 侧 ⇒ 用 threading.active_count()
    assert tg.gap_delta(threading.active_count() + 2) == 2


# --------------------------------------------- ② 「突增≥1 且持续」状态机
def test_03_first_sample_establishes_baseline():
    _s = tg.next_gap_state(None, 3)
    assert (_s["base"], _s["persist"], _s["delta"], _s["warn"]) == (3, 0, 0, False)


def test_04_single_bump_does_not_warn():
    _s = tg.next_gap_state({"base": 3, "persist": 0}, 4)
    assert _s["persist"] == 1
    assert _s["warn"] is False, "单点抖动就告警 ⇒ 误报会淹掉真滞留"


def test_05_persistent_bump_warns():
    _s = tg.next_gap_state({"base": 3, "persist": 1}, 4)
    assert (_s["persist"], _s["delta"], _s["warn"]) == (2, 1, True)


def test_06_drop_resets_persist_but_keeps_base():
    _s = tg.next_gap_state({"base": 3, "persist": 2}, 2)
    assert (_s["base"], _s["persist"], _s["warn"]) == (3, 0, False), "回落不该追着改参照"


def test_07_none_keeps_state_and_does_not_warn():
    _s = tg.next_gap_state({"base": 3, "persist": 2}, None)
    assert _s["delta"] is None, "拿不到 OS 线程数不许填 0"
    assert _s["persist"] == 2, "读不到样本不等于落差消失"
    assert _s["warn"] is False


# --------------------------- ③ 未引导线程超时即判滞留（start 返回 ≠ 引导完成）
def test_08_start_returned_but_unguided_is_stuck(monkeypatch):
    """把 Thread.start 换成 no-op ⇒ 登记在、ident 仍 None ⇒ 超时后必须判滞留。"""
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    _t = tg.safe_start(lambda: None, name="幽灵线程", timeout=0.01)
    assert _t.ident is None
    assert tg.pending_count() == 1, "start() 返回就撤登记 ⇒ 假设了不该假设的东西"
    time.sleep(0.03)
    _stuck = tg.scan_once()
    assert len(_stuck) == 1, _stuck
    assert _stuck[0]["name"] == "幽灵线程"
    assert _stuck[0]["waited_s"] >= 0.01


# ------------------------------- ④ 运行中卡住的线程不许误判（关键反控）
def _register(thread, name, timeout, t0=None):
    """白盒登记：模拟「已在登记表里、但线程其实早就在跑」的窗口。

    这是 `ident` 判据唯一有意义的场景：`safe_start` 在 `start()` 返回后立刻撤登记，
    正常路径上留不下"已运行却仍登记"的条目；但**窗口是真实存在的**（线程已 ident
    非空、调用方还没走到撤登记那一步），此时用 `is_alive()` 或"登记即滞留"都会误报。
    """
    tg._PENDING[id(thread)] = {
        "name": name, "target": "白盒", "t0": time.time() if t0 is None else t0,
        "timeout": timeout, "thread": thread, "reported": False,
    }


def test_09_running_but_blocked_thread_is_not_stuck():
    _ev = threading.Event()
    _t = threading.Thread(target=_ev.wait, name="业务卡住", daemon=True)
    _t.start()
    try:
        assert _t.ident is not None
        _register(_t, "业务卡住", timeout=0.01, t0=time.time() - 5.0)
        assert tg.pending_count() == 1
        assert tg.scan_once() == [], "运行中卡住 ≠ 引导期滞留；判据只认 ident"
        assert tg.stuck_snapshot() == []
    finally:
        _ev.set()
        _t.join(2.0)


def test_09b_safe_start_clears_registration_after_start():
    """正常路径：start() 返回且 ident 非空 ⇒ 登记表必须清空（否则看门狗误报）。"""
    _ev = threading.Event()
    _t = tg.safe_start(_ev.wait, name="正常", timeout=0.01)
    try:
        assert _t.ident is not None
        assert tg.pending_count() == 0
        assert tg.scan_once() == []
    finally:
        _ev.set()
        _t.join(2.0)


# ------------------------------------------- ⑤ 报过不刷屏 + 快照有界
def test_10_reported_once_no_spam(monkeypatch):
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    tg.safe_start(lambda: None, name="只报一次", timeout=0.01)
    time.sleep(0.02)
    assert len(tg.scan_once()) == 1
    assert tg.scan_once() == [], "同一个滞留每拍重报 ⇒ 日志刷屏"
    assert len(tg.stuck_snapshot()) == 1


def test_11_snapshot_is_bounded(monkeypatch):
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    for _i in range(tg.MAX_STUCK_KEPT + 8):
        tg.safe_start(lambda: None, name=f"滞留{_i}", timeout=0.001)
    time.sleep(0.02)
    _fresh = tg.scan_once()
    assert len(_fresh) == tg.MAX_STUCK_KEPT + 8
    assert len(tg.stuck_snapshot()) == tg.MAX_STUCK_KEPT, "快照必须有界，否则滞留久了就是内存坑"


# --------------------------------- ⑥ 硬约束机器化：无 Timer / 无 is_alive
def test_12_module_uses_neither_timer_nor_is_alive():
    _toks = _code_tokens(_GUARD)
    assert "Timer" not in _toks, "Timer 自己也要 Thread.start()，引导期一起卡死"
    assert "is_alive" not in _toks, "is_alive 判不了引导期"
    assert "ident" in _toks, "判据必须是 ident"


def test_13_safe_start_returns_started_thread():
    _ev = threading.Event()
    _t = tg.safe_start(_ev.set, name="正常线程")
    _ev.wait(3.0)
    _t.join(3.0)
    assert _t.ident is not None
    assert tg.pending_count() == 0
    _s = tg.boot_samples()
    assert len(_s) == 1 and _s[0] >= 0.0


# ------------------------------------------- ⑦ 启动期真预建（防死代码）
def test_14_main_prebuilds_watchdog():
    with open(os.path.join(_ROOT, "main.py"), encoding="utf-8") as _f:
        _src = _f.read()
    _i = _src.find("from nucleus.thread_guard import ensure_watchdog")
    assert _i >= 0, "启动期没预建看门狗 ⇒ safe_start 的登记没人判"
    assert _src.find("_tg_ensure()", _i) > _i


def test_15_ensure_watchdog_is_idempotent():
    assert tg.ensure_watchdog() is True
    _st = tg.watchdog_state()
    assert _st["started"] is True and _st["failed"] is None
    assert tg.ensure_watchdog() is True
    _names = [t.name for t in threading.enumerate()]
    assert _names.count(tg.WATCHDOG_NAME) == 1, "看门狗只许一条"
